"""Member-level external access keys: issuing, allowlists and inbound authentication.

A key carries its creator's identity. Its agent allowlist is a snapshot of what the creator could reach
when the key was issued, so later changes to platform-wide visibility never widen a deployed key.
"""

import secrets

from ai_platform.repositories import access_keys as repo
from ai_platform.repositories import agents as agents_repo
from ai_platform.schemas import access_keys as s
from ai_platform.security import digest
from ai_platform.services.clock import now
from ai_platform.services.common import require, workspace
from ai_platform.services.errors import Conflict, Forbidden, InvalidInput, NotFound, Unauthenticated
from ai_platform.services.pagination import parse_cursor, to_page

PREFIX = "aip"
MUTABLE = ("name", "expires_at", "status")


def issue_token():
    """Return ``(token, prefix, digest)``. The plaintext is shown once and never stored."""
    token = f"{PREFIX}_{secrets.token_urlsafe(32)}"
    return token, token[:12], digest(token)


def key_view(row):
    return s.KeyView.model_validate(row)


def key_row(conn, user, key_id, lock=False):
    return require(repo.get_key(conn, workspace(conn, user["id"]), key_id, lock), "Access key unavailable")


def allowed_agents(conn, user, agent_ids):
    """Reject any agent the creator cannot currently reach; the surviving ids become the snapshot."""
    unique = list(dict.fromkeys(agent_ids))
    viewer = (user["id"], user["role"] == "admin")
    for agent_id in unique:
        agent = agents_repo.get_agent(conn, viewer, agent_id)
        if not agent or agent["status"] != "active":
            raise InvalidInput(f"Agent unavailable: {agent_id}")
        if not agent["published_version"]:
            raise InvalidInput(f"Agent is not published: {agent_id}")
    return unique


def create_key(conn, user, data):
    if data.expires_at is not None and data.expires_at <= now():
        raise InvalidInput("expires_at must be in the future")
    agent_ids = allowed_agents(conn, user, data.agent_ids)
    token, prefix, token_hash = issue_token()
    key_id = repo.insert_key(conn, workspace(conn, user["id"]), user["id"], data.name, prefix, token_hash,
                             data.expires_at)
    repo.replace_allowlist(conn, key_id, agent_ids)
    return s.IssuedKey(key=key_view(key_row(conn, user, key_id)), token=token)


def list_keys(conn, user, cursor, limit):
    rows = repo.list_keys(conn, workspace(conn, user["id"]), parse_cursor(cursor), limit)
    return to_page(rows, limit, s.KeyView)


def get_key(conn, user, key_id):
    return key_view(key_row(conn, user, key_id))


def update_key(conn, user, key_id, data):
    row = key_row(conn, user, key_id, lock=True)
    if data.revision != row["revision"]:
        raise Conflict("Access key was updated elsewhere; reload and retry")
    if data.expires_at is not None and data.expires_at <= now():
        raise InvalidInput("expires_at must be in the future")
    changes = {field: getattr(data, field) for field in MUTABLE if getattr(data, field) is not None}
    if data.agent_ids is not None:
        repo.replace_allowlist(conn, key_id, allowed_agents(conn, user, data.agent_ids))
    elif not changes:
        raise InvalidInput("No changes supplied")
    if changes:
        repo.update_key(conn, key_id, changes)
    return key_view(key_row(conn, user, key_id))


def rotate_key(conn, user, key_id, data):
    row = key_row(conn, user, key_id, lock=True)
    if data.revision != row["revision"]:
        raise Conflict("Access key was updated elsewhere; reload and retry")
    if row["status"] != "active":
        raise Conflict("Access key is revoked")
    token, prefix, token_hash = issue_token()
    repo.rotate_secret(conn, key_id, prefix, token_hash)
    return s.IssuedKey(key=key_view(key_row(conn, user, key_id)), token=token)


# --- inbound authentication --------------------------------------------------

def authenticate(conn, token, agent_id):
    """Resolve a bearer token to ``(key, creator)``.

    Every call re-checks the live state behind the key: an unexpired token is not on its own a reason to
    admit a request. An agent outside the allowlist reports 404 rather than 403, so a caller cannot probe
    which agents exist.
    """
    if not token:
        raise Unauthenticated("Access key required")
    key = repo.resolve(conn, digest(token))
    if not key or key["status"] != "active":
        raise Unauthenticated("Access key is invalid or revoked")
    if key["expires_at"] and key["expires_at"] <= now():
        raise Unauthenticated("Access key has expired")
    if key["creator_status"] != "active":
        raise Forbidden("Key owner is no longer active")
    if agent_id is not None and not repo.allows_agent(conn, key["id"], agent_id):
        raise NotFound("Agent unavailable")
    repo.touch(conn, key["id"])
    return key, key["creator"]
