"""Agents, publishing, conversations and the request side of chat runs."""

import hashlib
from uuid import UUID, uuid4

from ai_platform.repositories import agents as agents_repo
from ai_platform.repositories import conversations as repo
from ai_platform.repositories import models as models_repo
from ai_platform.repositories import runs
from ai_platform.repositories.runs import ACTIVE
from ai_platform.schemas import chat as s
from ai_platform.security import digest
from ai_platform.services import connection_tests
from ai_platform.services.chat_execution import terminal
from ai_platform.services.clock import now
from ai_platform.services.common import require, workspace
from ai_platform.services.dispatch import event
from ai_platform.services.errors import Conflict, Forbidden, Gone, InvalidInput, NotFound
from ai_platform.services.pagination import parse_cursor, to_page

FIELDS = tuple(s.AgentConfig.model_fields)
SNAPSHOT_FIELDS = ("upstream_model", "context_limit_tokens", "max_output_tokens", "supports_temperature",
                   "request_config_generation")


def viewer(user):
    return user["id"], user["role"] == "admin"


def agent_row(conn, user, agent, *, write=False, share=False):
    if write or share:
        # Read releases in a fresh statement after concurrent editors/publishers release the lock.
        require(agents_repo.get_agent(conn, viewer(user), agent, "update" if write else "share"))
    return require(agents_repo.get_agent(conn, viewer(user), agent))


def conversation_row(conn, owner, conversation, lock=False):
    return require(repo.get_conversation(conn, owner, conversation, lock))


def visible_run(conn, owner, run):
    return require(repo.visible_run(conn, owner, run))


def config_model(conn, config):
    require(models_repo.share_model(conn, config.model_definition_id), "Model unavailable")
    model = require(models_repo.get_verified_model(conn, config.model_definition_id), "Model unavailable")
    if config.temperature is not None and not model["supports_temperature"]:
        raise InvalidInput("Model does not support temperature")
    return model


def saved_config(row):
    return s.AgentConfig(**{k: row[k] for k in FIELDS})


# --- agents ------------------------------------------------------------------

def list_agents(conn, user, cursor, limit):
    return to_page(agents_repo.list_agents(conn, viewer(user), parse_cursor(cursor), limit), limit, s.AgentView)


def create_agent(conn, user, data):
    owner = workspace(conn, user["id"])
    config_model(conn, data)
    created = agents_repo.insert_agent(conn, owner, data.visibility, FIELDS, [getattr(data, key) for key in FIELDS])
    return agent_row(conn, user, created["id"])


def edit_agent(conn, user, agent_id, data):
    row = agent_row(conn, user, agent_id, write=True)
    if row["status"] != "active" or row["revision"] != data.revision:
        raise Conflict("Agent revision conflict")
    changes = data.model_dump(exclude_unset=True, exclude={"revision"})
    if not changes or any(v is None and k != "temperature" for k, v in changes.items()):
        raise InvalidInput("Invalid change")
    if changes.get("visibility", row["visibility"]) != row["visibility"] and not row["can_manage"]:
        raise Forbidden("仅创建者和管理员可以更改可见性")
    changes = {k: v for k, v in changes.items() if row[k] != v}
    if not changes:
        return row
    config_model(conn, saved_config({**row, **changes}))
    agents_repo.update_agent(conn, agent_id, changes)
    return agent_row(conn, user, agent_id)


def validate_agent(conn, user, agent_id):
    row = agent_row(conn, user, agent_id)
    try:
        config_model(conn, saved_config(row))
    except (NotFound, InvalidInput) as exc:
        return {"valid": False,
                "errors": [{"field": "model_definition_id", "code": "MODEL_INVALID", "message": exc.detail}]}
    return {"valid": True, "errors": []}


def archive_agent(conn, user, agent_id):
    row = agent_row(conn, user, agent_id, write=True)
    if not row["can_manage"]:
        raise Forbidden("仅创建者和管理员可以归档")
    agents_repo.archive_agent(conn, agent_id)


def list_versions(conn, user, agent_id, cursor, limit):
    row = agent_row(conn, user, agent_id, share=True)
    rows = agents_repo.list_versions(conn, row["workspace_id"], agent_id, parse_cursor(cursor), limit)
    return to_page(rows, limit, s.Version)


def publish(conn, user, agent_id, revision):
    row = agent_row(conn, user, agent_id, write=True)
    if row["status"] != "active" or row["revision"] != revision:
        raise Conflict("Agent revision conflict")
    if row["published_revision"] == row["revision"]:
        return row
    config = saved_config(row)
    model = config_model(conn, config)
    link = require(models_repo.connection_link(conn, model["connection_id"]))
    snapshot = {k: model[k] for k in SNAPSHOT_FIELDS}
    snapshot["base_url"] = link["base_url"]
    key = (model["connection_id"], model["id"], link["revision"], model["revision"])
    version = require(agents_repo.ensure_model_config_version(conn, key, snapshot))["id"]
    bound = require(agents_repo.ensure_agent_version(
        conn, row["workspace_id"], agent_id, row["revision"], version, config.model_dump(mode="json"),
        hashlib.sha256(config.model_dump_json().encode()).hexdigest(),
    ))["id"]
    agents_repo.insert_release(conn, agent_id, (row["published_version"] or 0) + 1, bound)
    agents_repo.touch_agent(conn, agent_id)
    return agent_row(conn, user, agent_id)


# --- conversations -----------------------------------------------------------

def create_conversation(conn, user, agent_id, data):
    owner = workspace(conn, user["id"])
    row = agent_row(conn, user, agent_id, share=True)
    if row["status"] != "active" or not row["published_version"]:
        raise Conflict("此智能体尚未发布或已归档")
    if data.agent_revision != row["published_revision"]:
        raise Conflict("发布版本已更新，请刷新后重试")
    released = require(agents_repo.released_version(conn, agent_id, row["published_version"]))
    config_model(conn, s.AgentConfig(**released["config"]))
    conversation = repo.insert_conversation(conn, owner, user["id"], agent_id, released["id"], data.title or "",
                                            uuid4())
    return conversation_row(conn, owner, conversation)


def list_conversations(conn, user, agent_id, cursor, limit):
    owner = workspace(conn, user["id"])
    return to_page(repo.list_conversations(conn, owner, agent_id, parse_cursor(cursor), limit), limit,
                   s.Conversation)


def get_conversation(conn, user, conversation_id):
    return conversation_row(conn, workspace(conn, user["id"]), conversation_id)


def rename_conversation(conn, user, conversation_id, title):
    owner = workspace(conn, user["id"])
    conversation_row(conn, owner, conversation_id, True)
    repo.rename(conn, conversation_id, title)
    return conversation_row(conn, owner, conversation_id)


def history(conn, user, conversation_id, cursor, limit):
    owner = workspace(conn, user["id"])
    rows = repo.history_page(conn, owner, conversation_id, cursor, limit)
    if not rows:
        raise NotFound("Conversation unavailable")
    items = [r for r in rows[:limit] if r["id"]]
    return s.HistoryPage(
        items=items,
        run_cursors={r["run_id"]: r["last_event_seq"] for r in items},
        compression={r["run_id"]: r["context_compression"] or "none" for r in items},
        next_cursor=str(items[-1]["sequence"]) if len(rows) > limit else None,
    )


# --- runs --------------------------------------------------------------------

def accept_chat(conn, user, agent_id, data, idempotency_key):
    owner = workspace(conn, user["id"])
    agent = agent_row(conn, user, agent_id, share=True)
    conv = conversation_row(conn, owner, data.conversation_id, True)
    if conv["agent_id"] != agent_id:
        raise NotFound("Conversation unavailable")
    key = (owner, user["id"], agent_id, digest(idempotency_key))
    fingerprint = digest(data.model_dump_json())
    prior = runs.find_idempotency(conn, "chat", *key)
    if prior and prior["expires_at"] > now():
        if prior["request_hash"] != fingerprint:
            raise Conflict("IDEMPOTENCY_CONFLICT")
        return visible_run(conn, owner, prior["run_id"])
    if prior:
        runs.delete_idempotency(conn, prior["id"])
    if agent["status"] != "active":
        raise Conflict("Agent archived")
    if not agent["published_version"]:
        raise Conflict("请先发布智能体")
    if conv["active_run_id"]:
        raise Conflict("CONVERSATION_BUSY")
    run, input_id, output_id = uuid4(), uuid4(), uuid4()
    repo.insert_chat_run(conn, run, owner, user["id"], conv["id"], conv["agent_version_id"], input_id, output_id,
                         conv["thread_id"])
    text = data.input.content[0].text
    for offset, (mid, role, value) in enumerate(((input_id, "user", text), (output_id, "assistant", ""))):
        content = [{"type": "text", "block_id": str(uuid4()), "text": value}]
        repo.insert_message(conn, mid, owner, conv["id"], run, conv["next_message_seq"] + offset, role, content)
    repo.advance_messages(conn, conv["id"], text[:30])
    runs.insert_job(conn, run)
    runs.insert_idempotency(conn, "chat", *key, fingerprint, run)
    event(conn, run, "run.queued", {"status": "queued"})
    return visible_run(conn, owner, run)


def get_run(conn, user, run_id):
    owner = workspace(conn, user["id"])
    row = visible_run(conn, owner, run_id)
    if row["output_message_id"]:
        row.update(require(repo.output_snapshot(conn, row["output_message_id"], owner)))
    else:
        row["output_message"] = None
    return row


def cancel_run(conn, user, run_id):
    """Returns ``(run, accepted)``; ``accepted`` is false when the run had already finished."""
    owner = workspace(conn, user["id"])
    visible_run(conn, owner, run_id)
    runs.lock_job(conn, run_id)
    row = visible_run(conn, owner, run_id)
    if row["status"] not in ACTIVE:
        return visible_run(conn, owner, run_id), False
    if row["status"] == "queued":
        if row["kind"] == "chat":
            terminal(conn, require(runs.get_run_as_job(conn, run_id)), "cancelled")
        else:
            connection_tests.finish(conn, run_id, None, cancelled=True)
    elif row["status"] == "running":
        runs.request_cancel(conn, run_id)
        event(conn, run_id, "run.cancel_requested", {"status": "cancel_requested"})
    return visible_run(conn, owner, run_id), True


def parse_event_cursor(cursor, run_id):
    if cursor is None:
        return 0
    try:
        root_id, raw = cursor.split(":")
        sequence = int(raw)
        if UUID(root_id) != run_id or sequence < 0:
            raise ValueError
    except ValueError as exc:
        raise InvalidInput("Invalid run cursor") from exc
    return sequence


def read_events(conn, user, run_id, sequence):
    """Returns ``(rows, done, last_sequence)`` for events after ``sequence``."""
    run = visible_run(conn, workspace(conn, user["id"]), run_id)
    if sequence > run["last_event_sequence"]:
        raise InvalidInput("Invalid event sequence")
    rows = repo.run_events(conn, run_id, sequence)
    if sequence < run["last_event_sequence"] and (not rows or rows[0]["sequence"] != sequence + 1):
        raise Gone("EVENT_REPLAY_EXPIRED")
    return rows, run["status"] not in ACTIVE, run["last_event_sequence"]


def envelope(run_id, row):
    payload = dict(row["payload"])
    body = {"schema_version": 1, "root_run_id": str(run_id), "run_id": str(run_id),
            "sequence": row["sequence"], "type": row["type"], "payload": payload}
    for field in ("block_id", "message_id"):
        if field in payload:
            body[field] = payload.pop(field)
    return body
