"""Conversation deletion backed by an independent SQLite archive that survives database restores."""

import sqlite3
from contextlib import closing

from ai_platform.db import connection
from ai_platform.repositories import conversations as repo
from ai_platform.services.chat_context import cache
from ai_platform.services.common import workspace
from ai_platform.services.errors import Conflict, NotFound, Unavailable
from ai_platform.settings import settings

FIELDS = ("event_id", "workspace_id", "conversation_id", "deleted_at")


def archive_path():
    path = settings().deletion_archive_path
    if path is None or not path.is_absolute():
        raise OSError("Independent archive path required")
    return path


def create_archive(store):
    store.execute("""CREATE TABLE IF NOT EXISTS deletions (
        event_id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,conversation_id TEXT NOT NULL,deleted_at TEXT NOT NULL,
        UNIQUE(workspace_id,conversation_id))""")


def init_archive():
    with closing(sqlite3.connect(archive_path(), timeout=2)) as store, store:
        store.execute("PRAGMA synchronous=FULL")
        create_archive(store)


def archive(marker):
    record = tuple(str(marker[key]) for key in FIELDS)
    # mode=rw never creates a file: a missing or swapped archive must fail instead of
    # confirming deletions into a new, empty store.
    uri = archive_path().as_uri() + "?mode=rw"
    with closing(sqlite3.connect(uri, uri=True, timeout=2)) as store, store:
        store.execute("PRAGMA synchronous=FULL")
        store.execute("INSERT INTO deletions VALUES (?,?,?,?) ON CONFLICT(event_id) DO NOTHING", record)
        if store.execute("SELECT * FROM deletions WHERE event_id=?", (record[0],)).fetchone() != record:
            raise OSError("Archive record mismatch")


def confirm(marker):
    if marker["archived_at"]:
        return True
    try:
        archive(marker)
    except (sqlite3.Error, OSError):
        return False
    with connection() as conn:
        repo.confirm_marker(conn, marker["event_id"])
    return True


def request_deletion(conn, user, conversation_id):
    """Hide the conversation and record a deletion marker; returns ``(workspace, marker)``."""
    owner = workspace(conn, user["id"])
    row = repo.lock_any_conversation(conn, owner, conversation_id)
    marker = repo.deletion_marker(conn, owner, conversation_id)
    if not marker:
        if not row:
            raise NotFound("Conversation unavailable")
        if repo.has_active_run(conn, conversation_id):
            raise Conflict("CONVERSATION_BUSY")
        repo.mark_deleting(conn, conversation_id)
        marker = repo.insert_deletion_marker(conn, owner, conversation_id)
    return owner, marker


def finish_deletion(owner, conversation_id, marker):
    """Runs after the request transaction commits."""
    cache.discard((owner, conversation_id))
    if not confirm(marker):
        raise Unavailable("DELETION_ARCHIVE_PENDING")


def purge(conn, marker):
    cid = marker["conversation_id"]
    if repo.lock_deleting(conn, cid):
        repo.purge_conversation(conn, cid)


def replay_archive():
    """Re-apply every archived deletion to a restored database before the workbench opens.

    Fails with OSError when the independent archive is missing, so a restored instance stays isolated.
    Returns (archived records, markers applied in this database, conversations purged).
    """
    path = archive_path()
    if not path.is_file():
        raise OSError("Independent archive unavailable")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)) as store:
        records = store.execute(f"SELECT {','.join(FIELDS)} FROM deletions").fetchall()
    applied = 0
    with connection() as conn:
        for event_id, workspace_id, conversation_id, deleted_at in records:
            if not repo.workspace_exists(conn, workspace_id):
                continue
            repo.restore_marker(conn, event_id, workspace_id, conversation_id, deleted_at)
            # Runs restored mid-flight from the backup must not continue on a deleted conversation.
            repo.cancel_deleted_runs(conn, conversation_id)
            applied += bool(repo.hide_conversation(conn, workspace_id, conversation_id, deleted_at))
    with connection() as conn:
        before = repo.count_deleting(conn)
    retry_archives()
    with connection() as conn:
        after = repo.count_deleting(conn)
    return len(records), applied, before - after


def retry_archives():
    with connection() as conn:
        markers = repo.pending_markers(conn)
    for marker in markers:
        if confirm(marker):
            with connection() as conn:
                purge(conn, marker)
