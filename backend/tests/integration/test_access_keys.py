"""External access keys: issuing, allowlist scope, inbound auth and cross-key isolation."""

from uuid import uuid4

import pytest

from ai_platform.db import connection
from ai_platform.services import chat_execution as chat
from test_chat import setup_chat
from test_identity import PASSWORD, post, sign_in
from test_models_metering import fake_supplier

BASE = "/openapi/v1"


def call(client, path, token, data=None, method="POST", key=None):
    """Issue an external API request; no cookie or CSRF token is involved."""
    headers = {"Authorization": f"Bearer {token}"}
    if method == "POST":
        headers["Idempotency-Key"] = key or str(uuid4())
    return client.request(method, f"{BASE}{path}", headers=headers, json=data)


def publish_agent(member, model, name="assistant"):
    agent = post(member, "/agents", {"name": name, "model_definition_id": model}).json()["id"]
    assert post(member, f"/agents/{agent}/publish", {"revision": 1}).is_success
    return agent


@pytest.mark.parametrize("identity", ["0009_access_keys"], indirect=True)
def test_external_chat_and_isolation(graph_db, monkeypatch):
    with (
        graph_db() as admin,
        graph_db() as member,
        graph_db() as outsider,
        fake_supplier() as (endpoint, requests),
    ):
        _, model = setup_chat(admin, member, endpoint, monkeypatch)
        agent = publish_agent(member, model)
        private = publish_agent(member, model, "not-shared")

        issued = post(member, "/access-keys", {"name": "website", "agent_ids": [str(agent)]}).json()
        token = issued["token"]
        assert token.startswith("aip_")
        assert issued["key"]["key_prefix"] == token[:12]
        assert issued["key"]["agent_ids"] == [str(agent)]

        # The plaintext is never readable again.
        listed = post(member, "/access-keys", method="GET").json()["items"]
        assert [k["id"] for k in listed] == [issued["key"]["id"]]
        assert "token" not in listed[0]
        assert post(member, f"/access-keys/{issued['key']['id']}", method="GET").json()["key_prefix"] == token[:12]

        # An agent outside the allowlist is reported as missing, not as forbidden.
        assert call(outsider, f"/agents/{private}/conversations", token,
                    {"agent_revision": 1, "external_user_id": "visitor-1"}).status_code == 404

        created = call(outsider, f"/agents/{agent}/conversations", token,
                       {"agent_revision": 1, "external_user_id": "visitor-1"})
        assert created.status_code == 201, created.text
        conv = created.json()["id"]

        before = len(requests)
        run = call(outsider, f"/agents/{agent}/runs", token,
                   {"conversation_id": conv, "external_user_id": "visitor-1",
                    "input": {"content": [{"type": "text", "text": "hello from the website"}]}})
        assert run.status_code == 202, run.text
        run_id = run.json()["id"]
        assert chat.run_once("external-test")
        assert len(requests) == before + 1, "the external run must reach the supplier exactly once"

        detail = call(outsider, f"/runs/{run_id}", token, method="GET").json()
        assert detail["status"] == "completed", detail
        assert detail["output_message"]["content"][0]["text"] == "OK"

        # The conversation and its usage belong to the key owner's workspace, visible in the workbench.
        with connection() as conn:
            row = conn.execute(
                """SELECT c.access_key_id, c.external_user_id, c.owner_user_id, r.access_key_id AS run_key,
                   w.owner_user_id AS workspace_owner FROM app.conversations c
                   JOIN app.runs r ON r.conversation_id=c.id
                   JOIN app.workspaces w ON w.id=c.workspace_id WHERE c.id=%s""", (conv,)).fetchone()
            assert str(row["access_key_id"]) == issued["key"]["id"]
            assert row["external_user_id"] == "visitor-1"
            assert row["owner_user_id"] == row["workspace_owner"], "usage is attributed to the key's creator"
            assert str(row["run_key"]) == issued["key"]["id"]
            usage = conn.execute(
                """SELECT count(*) AS calls FROM app.model_calls mc JOIN app.runs r ON r.id=mc.run_id
                   WHERE r.id=%s""", (run_id,)).fetchone()
            assert usage["calls"] == 1

        history = call(outsider, f"/conversations/{conv}/messages?external_user_id=visitor-1", token,
                       method="GET").json()
        assert [m["role"] for m in history["items"]] == ["user", "assistant"]

        # A second key cannot reach the first key's conversation or run.
        other = post(member, "/access-keys", {"name": "erp", "agent_ids": [str(agent)]}).json()["token"]
        assert call(outsider, f"/runs/{run_id}", other, method="GET").status_code == 404
        assert call(outsider, f"/conversations/{conv}/messages?external_user_id=visitor-1", other,
                    method="GET").status_code == 404
        assert call(outsider, f"/agents/{agent}/runs", other,
                    {"conversation_id": conv, "external_user_id": "visitor-1",
                     "input": {"content": [{"type": "text", "text": "borrowed"}]}}).status_code == 404

        # Another external user of the same key cannot read the first visitor's conversation.
        assert call(outsider, f"/conversations/{conv}/messages?external_user_id=visitor-2", token,
                    method="GET").status_code == 404

        # The same idempotency key means different things for two different access keys.
        shared = "shared-client-key"
        second = call(outsider, f"/agents/{agent}/conversations", other,
                      {"agent_revision": 1, "external_user_id": "visitor-1"}).json()["id"]
        first_run = call(outsider, f"/agents/{agent}/runs", token,
                         {"conversation_id": conv, "external_user_id": "visitor-1",
                          "input": {"content": [{"type": "text", "text": "again"}]}}, key=shared)
        other_run = call(outsider, f"/agents/{agent}/runs", other,
                         {"conversation_id": second, "external_user_id": "visitor-1",
                          "input": {"content": [{"type": "text", "text": "again"}]}}, key=shared)
        assert first_run.status_code == 202 and other_run.status_code == 202
        assert first_run.json()["id"] != other_run.json()["id"], "idempotency must be scoped per key"


@pytest.mark.parametrize("identity", ["0009_access_keys"], indirect=True)
def test_key_lifecycle_and_revocation(graph_db, monkeypatch):
    with graph_db() as admin, graph_db() as member, graph_db() as outsider, fake_supplier() as (endpoint, _):
        _, model = setup_chat(admin, member, endpoint, monkeypatch)
        agent = publish_agent(member, model)
        issued = post(member, "/access-keys", {"name": "website", "agent_ids": [str(agent)]}).json()
        key_id, token = issued["key"]["id"], issued["token"]

        def start(active_token, expect):
            response = call(outsider, f"/agents/{agent}/conversations", active_token,
                            {"agent_revision": 1, "external_user_id": "visitor"})
            assert response.status_code == expect, response.text
            return response

        start(token, 201)
        assert call(outsider, f"/agents/{agent}/conversations", "aip_not-a-real-token",
                    {"agent_revision": 1, "external_user_id": "visitor"}).status_code == 401
        assert outsider.post(f"{BASE}/agents/{agent}/conversations",
                             headers={"Idempotency-Key": str(uuid4())},
                             json={"agent_revision": 1, "external_user_id": "v"}).status_code == 401

        # Rotation invalidates the previous token immediately.
        rotated = post(member, f"/access-keys/{key_id}/rotate", {"revision": 1}).json()
        assert rotated["token"] != token
        start(token, 401)
        start(rotated["token"], 201)

        # Narrowing the allowlist takes effect on the next call.
        spare = publish_agent(member, model, "spare")
        edited = post(member, f"/access-keys/{key_id}",
                      {"revision": rotated["key"]["revision"], "agent_ids": [str(spare)]}, method="PATCH")
        assert edited.is_success, edited.text
        start(rotated["token"], 404)

        # A stale revision is rejected rather than silently overwriting.
        assert post(member, f"/access-keys/{key_id}", {"revision": 1, "name": "stale"},
                    method="PATCH").status_code == 409

        # Revoking blocks every further call.
        current = post(member, f"/access-keys/{key_id}", method="GET").json()["revision"]
        assert post(member, f"/access-keys/{key_id}",
                    {"revision": current, "status": "revoked", "agent_ids": [str(agent)]},
                    method="PATCH").is_success
        start(rotated["token"], 401)
        assert post(member, f"/access-keys/{key_id}/rotate",
                    {"revision": current + 1}).status_code == 409


@pytest.mark.parametrize("identity", ["0009_access_keys"], indirect=True)
def test_allowlist_cannot_exceed_creator_scope(graph_db, monkeypatch):
    with (
        graph_db() as admin,
        graph_db() as member,
        graph_db() as peer,
        graph_db() as outsider,
        fake_supplier() as (endpoint, _),
    ):
        _, model = setup_chat(admin, member, endpoint, monkeypatch)
        invitation = post(admin, "/admin/users", {"username": "peer", "display_name": "Peer"}).json()
        assert post(peer, "/auth/activate",
                    {"token": invitation["activation_token"], "password": PASSWORD}).is_success
        assert sign_in(peer, "peer", PASSWORD).is_success

        # A private agent owned by another member is not a valid allowlist entry.
        hidden = publish_agent(peer, model, "peer-private")
        rejected = post(member, "/access-keys", {"name": "bad", "agent_ids": [str(hidden)]})
        assert rejected.status_code == 422, rejected.text

        # An unpublished agent cannot be bound either: external callers only run released versions.
        draft = post(member, "/agents", {"name": "draft", "model_definition_id": model}).json()["id"]
        assert post(member, "/access-keys", {"name": "draft", "agent_ids": [str(draft)]}).status_code == 422

        # A public agent is reachable, so it may be bound; making it private later stops the key.
        shared = post(peer, "/agents", {"name": "peer-public", "model_definition_id": model,
                                        "visibility": "public"}).json()["id"]
        assert post(peer, f"/agents/{shared}/publish", {"revision": 1}).is_success
        token = post(member, "/access-keys", {"name": "ok", "agent_ids": [str(shared)]}).json()["token"]
        assert call(outsider, f"/agents/{shared}/conversations", token,
                    {"agent_revision": 1, "external_user_id": "visitor"}).status_code == 201
        assert post(peer, f"/agents/{shared}", {"revision": 1, "visibility": "private"},
                    method="PATCH").is_success
        denied = call(outsider, f"/agents/{shared}/conversations", token,
                      {"agent_revision": 1, "external_user_id": "visitor"})
        assert denied.status_code == 404, "losing access must disable the deployed key"
