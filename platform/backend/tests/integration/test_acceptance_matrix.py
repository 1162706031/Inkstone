"""T06 acceptance-matrix cases not covered by the T02–T05 suites.

Each case runs against the isolated PostgreSQL test database, the real test Redis when
TEST_REDIS_URL is set, and loopback suppliers that count every upstream request.
"""

import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from secrets import token_bytes
from uuid import uuid4

import psycopg
import pyotp
import pytest
from redis import Redis

from ai_platform.services import chat_execution as chat
from ai_platform.services.chat_context import SUMMARY_PROMPT
from ai_platform.db import connection
from ai_platform.services import identity as service
from ai_platform.services import connection_tests as execution, metering
from ai_platform.settings import settings
from test_chat import send, setup_chat, write_fragment
from test_identity import ADMIN_PASSWORD, post, sign_in
from test_models_metering import accept, enroll_admin, fake_supplier

SECRET_KEY = "sk-matrix-secret-0123456789"
OK_STREAM = [
    b'data: {"choices":[{"delta":{"content":"OK"}}]}\n\n',
    b'data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":4}}\n\n',
    b"data: [DONE]\n\n",
]


@contextmanager
def scripted_supplier(script):
    """Loopback supplier whose status, headers and body come from ``script(payload)``."""
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.headers.get("Authorization"), payload))
            status, headers, chunks = script(payload)
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            for chunk in chunks:
                self.wfile.write(chunk)
                self.wfile.flush()

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/v1", requests
        finally:
            server.shutdown()
            thread.join()


def create_model(admin, name, url, key="fake"):
    link = post(admin, "/admin/model-connections", {"name": name, "base_url": url, "api_key": key})
    assert link.status_code == 201, link.text
    model = post(admin, f"/admin/model-connections/{link.json()['id']}/models", {
        "upstream_model": "okay", "display_name": name, "context_limit_tokens": 2048,
        "max_output_tokens": 128, "supports_temperature": False, "usage_support": "unknown",
    })
    assert model.status_code == 201, model.text
    return link.json()["id"], model.json()["id"]


def attempts_for(run):
    with connection() as conn:
        return conn.execute(
            """SELECT c.purpose,a.state,a.error_code FROM app.model_calls c
            JOIN app.model_call_attempts a ON a.model_call_id=c.id WHERE c.run_id=%s ORDER BY a.created_at""",
            (run,),
        ).fetchall()


@pytest.mark.parametrize("identity", ["0008_agent_releases"], indirect=True)
def test_revision_outbound_policy_and_log_redaction(graph_db, monkeypatch, caplog, capfd):
    caplog.set_level(logging.DEBUG)
    redirected = []

    def bounce(payload):
        return 302, {"Location": "http://127.0.0.1:9/v1/chat/completions"}, []

    with (
        graph_db() as admin,
        graph_db() as member,
        fake_supplier() as (endpoint, requests),
        scripted_supplier(bounce) as (bouncer, bounced),
        scripted_supplier(lambda payload: redirected.append(payload) or (200, {}, [])) as (target, _),
    ):
        _, model = setup_chat(admin, member, endpoint, monkeypatch)
        # B: a stale draft revision is rejected and does not change the saved draft.
        agent = post(member, "/agents", {"name": "draft", "model_definition_id": model}).json()
        edited = post(member, f"/agents/{agent['id']}", {"revision": 1, "name": "renamed"}, method="PATCH")
        assert edited.status_code == 200 and edited.json()["revision"] == 2
        stale = post(member, f"/agents/{agent['id']}", {"revision": 1, "name": "lost"}, method="PATCH")
        assert stale.status_code == 409
        assert member.get(f"/api/v1/agents/{agent['id']}").json()["name"] == "renamed"

        # C: HTTP endpoints must be explicitly allowlisted; private and metadata addresses are refused.
        refused = post(admin, "/admin/model-connections",
                       {"name": "plain", "base_url": "http://10.1.2.3/v1", "api_key": SECRET_KEY})
        assert refused.status_code == 422
        monkeypatch.setenv("PRIVATE_MODEL_ALLOWLIST", f"{endpoint},{bouncer}")
        settings.cache_clear()
        outcomes = {}
        for name, url in {
            "private": "https://10.255.255.1/v1",
            "metadata": "https://169.254.169.254/v1",
            "redirect": bouncer,
        }.items():
            link, definition = create_model(admin, name, url, SECRET_KEY)
            run = accept(admin, link, definition).json()["id"]
            assert execution.run_once("matrix")
            outcomes[name] = admin.get(f"/api/v1/runs/{run}").json()["error"], len(attempts_for(run))
        assert outcomes == {
            "private": ("ENDPOINT_FORBIDDEN", 0),
            "metadata": ("ENDPOINT_FORBIDDEN", 0),
            "redirect": ("UPSTREAM_REJECTED", 1),
        }
        assert len(bounced) == 1 and redirected == [] and bounced[0][0] == f"Bearer {SECRET_KEY}"
        assert len(requests) == 1  # Only the setup connection test reached the normal supplier.

    # G: secrets never reach application logs or process output.
    output = caplog.text + "".join(capfd.readouterr())
    database_password = psycopg.conninfo.conninfo_to_dict(os.environ["TEST_DATABASE_URL"]).get("password")
    for secret in filter(None, (SECRET_KEY, ADMIN_PASSWORD, database_password)):
        assert secret not in output
    print("revision_conflict=409 http_unlisted=422 private=forbidden metadata=forbidden "
          "redirect=rejected redirect_target_requests=0 log_secrets=0")


@pytest.mark.parametrize("identity", ["0008_agent_releases"], indirect=True)
def test_admission_survives_database_failure(graph_db, monkeypatch):
    with graph_db() as admin, graph_db() as member, fake_supplier() as (endpoint, requests):
        _, model = setup_chat(admin, member, endpoint, monkeypatch)
        agent = post(member, "/agents", {"name": "db", "model_definition_id": model}).json()["id"]
        assert post(member, f"/agents/{agent}/publish", {"revision": 1}).is_success
        cid = post(member, f"/agents/{agent}/conversations", {"agent_revision": 1}).json()["id"]
        run = send(member, agent, cid, "hello").json()["id"]
        workspace = member.get("/api/v1/auth/me").json()["workspace_id"]
        original = metering.reserve

        def broken(conn, *args):
            raise psycopg.OperationalError("injected reservation failure")

        before = len(requests)
        monkeypatch.setattr(metering, "reserve", broken)
        # E: Redis admission succeeded, then the reservation transaction failed.
        with pytest.raises(psycopg.OperationalError):
            chat.run_once("db-failure")
        assert len(requests) == before and attempts_for(run) == []
        with connection() as conn:
            assert conn.execute(
                "SELECT count(*) AS n FROM app.quota_reservations WHERE workspace_id=%s", (workspace,)
            ).fetchone()["n"] == 0
        if redis_url := settings().redis_url:
            with Redis.from_url(redis_url) as redis:
                assert redis.get(f"{settings().redis_prefix}:model:active:{workspace}") == b"0"
        monkeypatch.setattr(metering, "reserve", original)
        with connection() as conn:
            conn.execute("UPDATE app.execution_jobs SET lease_until=now() WHERE run_id=%s", (run,))
        assert chat.run_once("successor")
        assert member.get(f"/api/v1/runs/{run}").json()["status"] == "completed"
        assert len(requests) == before + 1
        rows = attempts_for(run)
        assert [(row["purpose"], row["state"]) for row in rows] == [("chat", "completed")]
        with connection() as conn:
            ledger = conn.execute(
                """SELECT operation FROM app.quota_ledger l JOIN app.model_call_attempts a ON a.id=l.attempt_id
                JOIN app.model_calls c ON c.id=a.model_call_id WHERE c.run_id=%s ORDER BY l.created_at""",
                (run,),
            ).fetchall()
        assert [row["operation"] for row in ledger] == ["reserve", "settle"]
        print(f"db_failure_dispatches=0 retry_dispatches=1 ledger=reserve,settle requests={len(requests)}")


@pytest.mark.parametrize("identity", ["0004_metering"], indirect=True)
def test_month_boundary_freezes_admission_bucket(identity, monkeypatch):
    with identity() as admin:
        enroll_admin(admin)
        monkeypatch.setenv("PRIVATE_MODEL_ALLOWLIST", "http://127.0.0.1:9/v1")
        settings.cache_clear()
        _, model = create_model(admin, "month", "http://127.0.0.1:9/v1")
        me = admin.get("/api/v1/auth/me").json()
        workspace, user = me["workspace_id"], me["user"]["id"]
        october = (datetime(2026, 9, 30, 16, tzinfo=UTC), datetime(2026, 10, 31, 16, tzinfo=UTC))
        november = (october[1], datetime(2026, 11, 30, 16, tzinfo=UTC))

        def attempt(conn):
            attempt_id = uuid4()
            run = conn.execute("""INSERT INTO app.runs(workspace_id,actor_user_id,kind,status)
                VALUES (%s,%s,'connection_test','running') RETURNING id""", (workspace, user)).fetchone()["id"]
            call = conn.execute("""INSERT INTO app.model_calls(workspace_id,run_id,model_definition_id,
                logical_call_key,purpose,status) VALUES (%s,%s,%s,'test','connection_test','dispatching')
                RETURNING id""", (workspace, run, model)).fetchone()["id"]
            conn.execute("""INSERT INTO app.model_call_attempts(id,model_call_id,workspace_id,attempt_number,
                state,credential_version,deadline_at) VALUES (%s,%s,%s,1,'dispatching',1,now()+interval '2 minutes')""",
                         (attempt_id, call, workspace))
            return attempt_id

        deadline = service.now() + timedelta(minutes=2)
        monkeypatch.setattr(metering, "month_bounds", lambda month=None: october)
        with connection() as conn:
            late = attempt(conn)
            assert metering.reserve(conn, workspace, late, 50, deadline, False) is None
        monkeypatch.setattr(metering, "month_bounds", lambda month=None: november)
        with connection() as conn:
            assert metering.settle(conn, late, (10, 5))
            fresh = attempt(conn)
            assert metering.reserve(conn, workspace, fresh, 40, deadline, False) is None
            buckets = conn.execute("""SELECT period_start,settled_tokens,reserved_tokens FROM app.quota_buckets
                WHERE workspace_id=%s ORDER BY period_start""", (workspace,)).fetchall()
        # E: settlement after the boundary stays in the admission month; the new month starts clean.
        assert [(row["period_start"], row["settled_tokens"], row["reserved_tokens"]) for row in buckets] == [
            (october[0], 15, 0), (november[0], 0, 40),
        ]
        print("month_boundary: settled_into=admission_month(15) new_month_reserved=40")


@pytest.mark.parametrize("identity", ["0008_agent_releases"], indirect=True)
def test_failed_batch_write_is_not_pushed(graph_db, monkeypatch):
    def stream(file, payload):
        if payload["messages"][-1]["content"] != "batch":
            write_fragment(file, "OK")
            return
        write_fragment(file, "first")
        time.sleep(1.3)
        write_fragment(file, "-second")
        time.sleep(1.3)

    with graph_db() as admin, graph_db() as member, fake_supplier(stream) as (endpoint, requests):
        _, model = setup_chat(admin, member, endpoint, monkeypatch)
        agent = post(member, "/agents", {"name": "batch", "model_definition_id": model}).json()["id"]
        assert post(member, f"/agents/{agent}/publish", {"revision": 1}).is_success
        cid = post(member, f"/agents/{agent}/conversations", {"agent_revision": 1}).json()["id"]
        run = send(member, agent, cid, "batch").json()["id"]
        original, calls, failures = chat.emit_text, [], []

        def flaky(conn, job, text):
            calls.append(text)
            if len(calls) == 2:
                with connection() as probe:
                    failures.append(probe.execute(
                        "SELECT count(*) AS n FROM app.run_events WHERE run_id=%s AND type='message.delta'", (run,)
                    ).fetchone()["n"])
                raise psycopg.OperationalError("injected batch failure")
            return original(conn, job, text)

        monkeypatch.setattr(chat, "emit_text", flaky)
        assert chat.run_once("flaky")
        snapshot = member.get(f"/api/v1/runs/{run}").json()
        assert snapshot["status"] == "completed"
        final = snapshot["output_message"]["content"][0]["text"]
        stream_text = member.get(f"/api/v1/runs/{run}/events").text
        deltas = [
            json.loads(line[6:])["payload"]["text"] for block in stream_text.split("\n\n") if "event: message.delta" in block
            for line in block.splitlines() if line.startswith("data: ")
        ]
        # F: the failed batch left no event; the retry pushed it exactly once.
        assert final == "first-second" and "".join(deltas) == final
        assert failures == [1] and calls.count("first-second") >= 2
        assert len(requests) == 2
        print(f"failed_batch_events=0 retried_deltas={len(deltas)} final={final} requests={len(requests)}")


@pytest.mark.parametrize("identity", ["0008_agent_releases"], indirect=True)
def test_count_cap_summary_failure_truncates(graph_db, monkeypatch):
    def script(payload):
        if payload["messages"][0]["content"] == SUMMARY_PROMPT:
            return 429, {}, []
        return 200, {"Content-Type": "text/event-stream"}, OK_STREAM

    with graph_db() as admin, graph_db() as member, scripted_supplier(script) as (endpoint, requests):
        _, model = setup_chat(admin, member, endpoint, monkeypatch)
        agent = post(member, "/agents", {
            "name": "cap", "model_definition_id": model, "history_max_messages": 2,
        }).json()["id"]
        assert post(member, f"/agents/{agent}/publish", {"revision": 1}).is_success
        cid = post(member, f"/agents/{agent}/conversations", {"agent_revision": 1}).json()["id"]
        for text in ("one", "two"):
            assert send(member, agent, cid, text).is_success and chat.run_once("worker")
        run = send(member, agent, cid, "three").json()["id"]
        before = len(requests)
        assert chat.run_once("worker")
        # H: 4 raw messages exceed the cap of 2 while tokens stay far below the 80% threshold.
        result = member.get(f"/api/v1/runs/{run}").json()
        assert result["status"] == "completed" and result["context_compression"] == "truncated"
        summary, reply = (payload for _, payload in requests[before:])
        assert summary["messages"][0]["content"] == SUMMARY_PROMPT
        assert reply["messages"][1:] == [
            {"role": "user", "content": "two"}, {"role": "assistant", "content": "OK"},
            {"role": "user", "content": "three"},
        ]
        rows = attempts_for(run)
        assert [(row["purpose"], row["state"], row["error_code"]) for row in rows] == [
            ("context_summary", "failed", "UPSTREAM_REJECTED"), ("chat", "completed", None),
        ]
        usage = [row["purpose"] for row in member.get("/api/v1/usage/attempts").json()["items"]]
        assert usage.count("context_summary") == 1
        history = member.get(f"/api/v1/conversations/{cid}/messages").json()["items"]
        assert [item["content"][0]["text"] for item in history] == ["one", "OK", "two", "OK", "three", "OK"]
        print(f"count_cap_summary=failed(429) reply=truncated summary_requests=1 requests={len(requests)}")


@pytest.mark.parametrize("identity", ["0004_metering"], indirect=True)
def test_credential_key_recovery(identity, monkeypatch, tmp_path):
    with identity() as admin, identity() as other, fake_supplier() as (endpoint, requests):
        monkeypatch.setenv("PRIVATE_MODEL_ALLOWLIST", endpoint)
        settings.cache_clear()
        if not settings().redis_url:
            monkeypatch.setattr(metering, "redis_admission", lambda *_: (True, ""))
        with connection() as conn:
            assert service.bootstrap_admin(conn)
        assert sign_in(admin, "admin", "admin").is_success
        assert post(admin, "/auth/change-initial-password", {"new_password": ADMIN_PASSWORD}).status_code == 204
        assert sign_in(admin, "admin", ADMIN_PASSWORD).is_success
        totp = pyotp.TOTP(post(admin, "/auth/mfa/setup").json()["secret"])
        assert post(admin, "/auth/mfa/confirm", {"code": totp.now()}).status_code == 200
        link, model = create_model(admin, "key", endpoint, SECRET_KEY)
        correct = os.environ["CREDENTIAL_KEY_FILE"]
        wrong = tmp_path / "wrong.key"
        wrong.write_bytes(token_bytes(32))

        def use_key(path):
            monkeypatch.setenv("CREDENTIAL_KEY_FILE", str(path))
            settings.cache_clear()

        # G: a restored database with the wrong key material fails closed, without upstream calls.
        use_key(wrong)
        run = accept(admin, link, model).json()["id"]
        assert execution.run_once("wrong-key")
        assert admin.get(f"/api/v1/runs/{run}").json()["error"] == "DEPENDENCY_UNAVAILABLE"
        assert sign_in(other, "admin", ADMIN_PASSWORD).json()["next"] == "mfa_verify"
        code = totp.at(datetime.now(UTC) + timedelta(seconds=30))
        assert post(other, "/auth/mfa/verify", {"code": code}).status_code == 503
        assert requests == []
        use_key(correct)
        assert post(other, "/auth/mfa/verify", {"code": code}).status_code == 200
        run = accept(admin, link, model).json()["id"]
        assert execution.run_once("restored-key")
        assert admin.get(f"/api/v1/runs/{run}").json()["status"] == "completed"
        assert [key for _, key, _ in requests] == [f"Bearer {SECRET_KEY}"]
        print("wrong_key: connection_test=DEPENDENCY_UNAVAILABLE mfa=503 upstream=0; restored_key: mfa=200 upstream=1")


@pytest.mark.parametrize("identity", ["0008_agent_releases"], indirect=True)
def test_archive_replay_after_restore(graph_db, monkeypatch, tmp_path):
    from ai_platform.services.deletion import replay_archive

    with graph_db() as admin, graph_db() as member, fake_supplier() as (endpoint, requests):
        _, model = setup_chat(admin, member, endpoint, monkeypatch)
        agent = post(member, "/agents", {"name": "restore", "model_definition_id": model}).json()["id"]
        assert post(member, f"/agents/{agent}/publish", {"revision": 1}).is_success
        archived, pending = (post(member, f"/agents/{agent}/conversations", {"agent_revision": 1}).json()["id"]
                             for _ in range(2))
        for cid in (archived, pending):
            assert send(member, agent, cid, "hello").is_success and chat.run_once("worker")
        archive = settings().deletion_archive_path
        # A swapped-out archive file (directory still mounted) must not be recreated empty.
        held = archive.with_suffix(".held")
        archive.rename(held)
        assert post(member, f"/conversations/{archived}", method="DELETE").status_code == 503
        assert not archive.exists()
        held.rename(archive)
        assert post(member, f"/conversations/{archived}", method="DELETE").status_code == 202
        monkeypatch.setenv("DELETION_ARCHIVE_PATH", str(tmp_path / "offline" / "deletions.sqlite3"))
        settings.cache_clear()
        assert post(member, f"/conversations/{pending}", method="DELETE").status_code == 503

        # Restoring the pre-deletion backup brings back both conversations and drops every marker.
        # The worker has not purged anything yet, so resetting these rows reproduces that backup.
        with connection() as conn:
            conn.execute("DELETE FROM app.conversation_deletion_markers")
            conn.execute("UPDATE app.conversations SET status='active',deletion_requested_at=NULL")
        stale = send(member, agent, archived, "restored in flight").json()["id"]
        assert member.get(f"/api/v1/conversations/{archived}").status_code == 200

        with pytest.raises(OSError):
            replay_archive()  # The archive is unreachable, so the restored instance stays isolated.
        monkeypatch.setenv("DELETION_ARCHIVE_PATH", str(archive))
        settings.cache_clear()
        assert replay_archive() == (1, 1, 1)
        assert member.get(f"/api/v1/conversations/{archived}").status_code == 404
        assert {item["id"] for item in member.get("/api/v1/conversations").json()["items"]} == {pending}
        with connection() as conn:
            assert conn.execute("SELECT count(*) AS n FROM app.messages WHERE conversation_id=%s",
                                (archived,)).fetchone()["n"] == 0
            assert conn.execute("SELECT status,error_code FROM app.runs WHERE id=%s", (stale,)).fetchone() == {
                "status": "cancelled", "error_code": "CONVERSATION_DELETED"}
        assert not chat.run_once("after-replay")
        assert replay_archive() == (1, 0, 0)
        # The deletion that never reached the archive reappears and can be requested again.
        assert post(member, f"/conversations/{pending}", method="DELETE").status_code == 202
        assert len(requests) == 3
        print("restore_replay: archived=hidden,purged in_flight=cancelled unarchived=visible,redelete=202 "
              "archive_offline=isolated upstream=3")
