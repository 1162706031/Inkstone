import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pyotp
import pytest
from redis import Redis

from ai_platform.db import connection
from ai_platform.services import identity as service
from ai_platform.services import connection_tests as execution, metering
from ai_platform.settings import settings
from test_identity import ADMIN_PASSWORD, PASSWORD, identity, post, sign_in


@contextmanager
def fake_supplier(stream=None):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, self.headers["Authorization"], payload))
            if payload["model"] == "bad":
                self.send_response(429)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            if stream:
                stream(self.wfile, payload)
            else:
                self.wfile.write(b'data: {"choices":[{"delta":{"content":"OK"}}]}\n\n')
            try:
                if payload["model"] != "unknown":
                    self.wfile.write(b'data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":4}}\n\n')
                self.wfile.write(b"data: [DONE]\n\n")
            except ConnectionError:
                return  # Cancellation closes the HTTP stream before the supplier finishes.

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/v1", requests
        finally:
            server.shutdown()
            thread.join()


def enroll_admin(admin):
    with connection() as conn:
        assert service.bootstrap_admin(conn)
    assert sign_in(admin, "admin", "admin").json()["next"] == "initial_password_change"
    assert post(admin, "/auth/change-initial-password", {"new_password": ADMIN_PASSWORD}).status_code == 204
    assert sign_in(admin, "admin", ADMIN_PASSWORD).json()["next"] == "mfa_setup"
    secret = post(admin, "/auth/mfa/setup").json()["secret"]
    assert post(admin, "/auth/mfa/confirm", {"code": pyotp.TOTP(secret).now()}).status_code == 200


def accept(client, provider_id, model, key=None):
    csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
    return client.post(f"/api/v1/admin/model-connections/{provider_id}/test",
                       json={"model_definition_id": model},
                       headers={"origin": "https://platform.test", "x-csrf-token": csrf,
                                "Idempotency-Key": key or str(uuid4())})


def test_model_admission_environment_limits(monkeypatch):
    redis_url = os.getenv("TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("TEST_REDIS_URL is required")
    monkeypatch.setenv("REDIS_URL", redis_url)
    monkeypatch.setenv("REDIS_PREFIX", f"ai-test-{uuid4().hex}")
    monkeypatch.setenv("MODEL_WORKSPACE_REQUESTS_PER_MINUTE", "3")
    monkeypatch.setenv("MODEL_WORKSPACE_MAX_CONCURRENT", "4")
    monkeypatch.setenv("MODEL_GLOBAL_MAX_CONCURRENT", "6")
    settings.cache_clear()
    try:
        for recent, local, global_count, expected in [
            (2, 3, 5, True), (3, 0, 0, False), (0, 4, 4, False), (0, 0, 6, False),
        ]:
            counts = {"recent": recent, "local": local, "global": global_count}
            assert metering.redis_admission("example", counts)[0] is expected
    finally:
        settings.cache_clear()


@pytest.mark.parametrize("identity", ["0010_member_rate_limits"], indirect=True)
def test_provider_routing_and_accounting(identity, monkeypatch):
    monkeypatch.setenv("MODEL_WORKSPACE_REQUESTS_PER_MINUTE", "20")
    monkeypatch.setenv("MODEL_WORKSPACE_MAX_CONCURRENT", "2")
    monkeypatch.setenv("MODEL_GLOBAL_MAX_CONCURRENT", "4")
    settings.cache_clear()
    snapshots = []
    if not os.getenv("TEST_REDIS_URL"):
        def admit(workspace, counts, overrides):
            snapshots.append(counts)
            return True, ""
        monkeypatch.setattr(metering, "redis_admission", admit)

    # Connection probes stop at the first token. Supply its usage in the same SSE frame.
    def token_with_usage(stream, _payload):
        stream.write(b'data: {"choices":[{"delta":{"content":"OK"}}],'
                     b'"usage":{"prompt_tokens":3,"completion_tokens":4}}\n\n')

    with identity() as admin, identity() as member, fake_supplier(token_with_usage) as first, fake_supplier() as second:
        monkeypatch.setenv("PRIVATE_MODEL_ALLOWLIST", f"{first[0]},{second[0]}")
        settings.cache_clear()
        enroll_admin(admin)
        invited = post(admin, "/admin/users", {"username": "member", "display_name": "Member"}).json()
        member_id = invited["user"]["id"]
        assert post(member, "/auth/activate", {"token": invited["activation_token"], "password": PASSWORD}).status_code == 200
        assert sign_in(member, "member", PASSWORD).status_code == 200
        assert member.get("/api/v1/admin/model-connections").status_code == 403
        assert member.get(f"/api/v1/admin/users/{member_id}/quota").status_code == 403
        assert post(member, f"/admin/users/{member_id}/quota",
                    {"revision": 1, "mode": "strict", "monthly_token_limit": 2}, method="PATCH").status_code == 403
        assert post(admin, "/admin/model-connections", {
            "name": "Invalid", "base_url": first[0] + "?secret=discard", "api_key": "discard",
        }).status_code == 422

        def create_connection(number, url, key):
            response = post(admin, "/admin/model-connections", {"name": number, "base_url": url, "api_key": key})
            assert response.status_code == 201, response.text
            assert "api_key" not in response.json() and key not in response.text
            return response.json()

        a = create_connection("first", first[0], "key-first")
        b = create_connection("second", second[0], "key-second")

        def create_model(conn_id, upstream):
            response = post(admin, f"/admin/model-connections/{conn_id}/models", {
                "upstream_model": upstream, "display_name": upstream, "context_limit_tokens": 2048,
                "max_output_tokens": 128, "supports_temperature": False, "usage_support": "unknown",
            })
            assert response.status_code == 201, response.text
            return response.json()

        okay = create_model(a["id"], "okay")
        bad = create_model(a["id"], "bad")
        unknown = create_model(b["id"], "unknown")
        assert member.get("/api/v1/models").json()["items"] == []
        assert accept(member, a["id"], okay["id"]).status_code == 403
        accepted = accept(admin, a["id"], okay["id"], "same")
        assert accepted.status_code == 202, accepted.text
        run = accepted.json()
        assert accept(admin, a["id"], okay["id"], "same").json()["id"] == run["id"]
        assert accept(admin, a["id"], bad["id"], "same").status_code == 409
        assert member.get(f"/api/v1/runs/{run['id']}").status_code == 404
        assert accept(admin, b["id"], unknown["id"]).status_code == 202
        assert accept(admin, a["id"], bad["id"]).status_code == 202
        for _ in range(3):
            assert execution.run_once("fixture-worker")
        assert not execution.run_once("fixture-worker")
        assert [(path, key, payload["model"]) for path, key, payload in first[1]] == [
            ("/v1/chat/completions", "Bearer key-first", "okay"),
            ("/v1/chat/completions", "Bearer key-first", "bad"),
        ]
        assert [(path, key, payload["model"]) for path, key, payload in second[1]] == [
            ("/v1/chat/completions", "Bearer key-second", "unknown"),
        ]
        assert all(item[2]["max_tokens"] == 32 for item in [*first[1], *second[1]])
        assert admin.get(f"/api/v1/runs/{run['id']}").json()["status"] == "completed"
        catalog = member.get("/api/v1/models").json()["items"]
        assert {item["id"] for item in catalog} == {okay["id"], unknown["id"]}
        assert {field for item in catalog for field in item} == {
            "id", "display_name", "context_limit_tokens", "max_output_tokens", "supports_temperature",
        }
        usage = admin.get("/api/v1/usage/summary").json()
        assert (usage["confirmed_tokens"], usage["unknown_attempts"], usage["unpriced_attempts"]) == (7, 2, 3)
        assert usage["estimated_costs"] == [] and usage["uncertain_reserved_tokens"] > 0
        assert member.get("/api/v1/usage/summary").json()["confirmed_tokens"] == 0
        usage_rows = admin.get("/api/v1/usage/attempts").json()["items"]
        assert len(usage_rows) == 3 and all(row["cost"] is None for row in usage_rows)
        assert all(row["input_tokens"] is None for row in usage_rows if row["usage_source"] == "unknown")
        assert member.get("/api/v1/usage/attempts").json()["items"] == []
        assert post(admin, f"/admin/model-connections/{a['id']}", {
            "revision": a["revision"], "base_url": second[0],
        }, method="PATCH").status_code == 422
        old_config = accept(admin, a["id"], okay["id"]).json()
        rotated = post(admin, f"/admin/model-connections/{a['id']}", {
            "revision": a["revision"], "api_key": "key-rotated",
        }, method="PATCH")
        assert rotated.status_code == 200 and rotated.json()["base_url"] == first[0]
        assert execution.run_once("fixture-worker")
        assert admin.get(f"/api/v1/runs/{old_config['id']}").json()["error"] == "MODEL_CONFIGURATION_CHANGED"
        assert len(first[1]) == 2
        assert {row["id"] for row in member.get("/api/v1/models").json()["items"]} == {unknown["id"]}
        assert accept(admin, a["id"], okay["id"]).status_code == 202
        assert execution.run_once("fixture-worker")
        assert first[1][-1][1] == "Bearer key-rotated" and len(second[1]) == 1
        edit = post(admin, f"/admin/models/{okay['id']}", {
            "revision": okay["revision"], "upstream_model": "changed",
        }, method="PATCH")
        assert edit.status_code == 200, edit.text
        assert edit.json()["last_test_status"] == "untested"
        assert {row["id"] for row in member.get("/api/v1/models").json()["items"]} == {unknown["id"]}
        if snapshots:
            assert [snapshot["recent"] for snapshot in snapshots] == [0, 1, 2, 3]
        print(f"provider_requests={len(first[1])},{len(second[1])} reported_tokens=7 unknown_attempts=2 unpriced=3")

        member_workspace = member.get("/api/v1/auth/me").json()["workspace_id"]
        updated = post(admin, f"/admin/users/{member_id}/quota",
                       {"revision": 1, "mode": "soft", "monthly_token_limit": 100}, method="PATCH")
        assert updated.status_code == 200 and updated.json()["workspace_id"] == member_workspace
        path = f"/admin/users/{member_id}/quota"
        quota = {"mode": "soft", "monthly_token_limit": 100}
        custom = post(admin, path, {**quota, "revision": 2, "requests_per_minute": 1,
                                    "max_concurrent": 3}, method="PATCH")
        assert custom.status_code == 200 and admin.get("/api/v1" + path).json()["requests_per_minute"] == 1
        assert post(admin, path, {**quota, "revision": 2, "max_concurrent": 5}, method="PATCH").status_code == 409
        assert post(admin, path, {**quota, "revision": 3, "max_concurrent": 0}, method="PATCH").status_code == 422
        preserved = post(admin, path, {**quota, "revision": 3}, method="PATCH").json()
        assert (preserved["requests_per_minute"], preserved["max_concurrent"]) == (1, 3)
        attempts = []
        with connection() as conn:
            for _ in range(2):
                attempt = uuid4()
                run_id = conn.execute("""
                    INSERT INTO app.runs(workspace_id,actor_user_id,kind,status)
                    VALUES (%s,%s,'connection_test','running') RETURNING id
                """, (member_workspace, member_id)).fetchone()["id"]
                call_id = conn.execute("""
                    INSERT INTO app.model_calls(workspace_id,run_id,model_definition_id,logical_call_key,purpose,status)
                    VALUES (%s,%s,%s,'test','connection_test','dispatching') RETURNING id
                """, (member_workspace, run_id, unknown["id"])).fetchone()["id"]
                conn.execute("""
                    INSERT INTO app.model_call_attempts(id,model_call_id,workspace_id,
                        attempt_number,state,credential_version,deadline_at)
                    VALUES (%s,%s,%s,1,'dispatching',1,now()+interval '120 seconds')
                """, (attempt, call_id, member_workspace))
                attempts.append(attempt)

        barrier = threading.Barrier(2, timeout=5)

        def reserve(attempt):
            with connection() as conn:
                barrier.wait()
                result = metering.reserve(conn, member_workspace, attempt, 60,
                                          service.now()+timedelta(seconds=120), False)
                return attempt, result

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = dict(pool.map(reserve, attempts))
        assert set(results.values()) == {None, "MONTHLY_QUOTA_EXCEEDED"}
        winner = next(attempt for attempt in results if results[attempt] is None)

        def settle(_):
            with connection() as conn:
                return metering.settle(conn, winner, (3, 4))

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(settle, range(2))) == [False, True]
        member_usage = member.get("/api/v1/usage/summary").json()
        assert (member_usage["confirmed_tokens"], member_usage["reserved_tokens"]) == (7, 0)
        with connection() as conn:
            assert conn.execute("SELECT count(*) AS n FROM app.quota_ledger WHERE attempt_id=%s",
                                (winner,)).fetchone()["n"] == 2
        if redis_url := os.getenv("TEST_REDIS_URL"):
            prefix = settings().redis_prefix
            keys = [f"{prefix}:model:rate:{member_workspace}", f"{prefix}:model:active:{member_workspace}",
                    f"{prefix}:model:active:global"]
            with Redis.from_url(redis_url) as redis, connection() as conn:
                # Simulate empty Redis state without touching unrelated test keys.
                redis.delete(*keys)
                # Two active calls fit the member override of three and exceed the default of two.
                assert metering.admission(conn, member_workspace)[0]
                assert redis.get(keys[1]) == b"2"
                reset = post(admin, path, {**quota, "revision": 4, "max_concurrent": None}, method="PATCH").json()
                assert reset["max_concurrent"] is None and reset["requests_per_minute"] == 1
                assert reset["default_max_concurrent"] == 2 and not metering.admission(conn, member_workspace)[0]
                conn.execute("""
                    UPDATE app.model_call_attempts SET state='completed' WHERE workspace_id=%s
                """, (member_workspace,))
                redis.delete(*keys)
                assert metering.admission(conn, member_workspace)[0]
                assert redis.get(keys[1]) == b"0"
                conn.execute("UPDATE app.model_call_attempts SET dispatched_at=now() WHERE id=%s", (winner,))
                assert not metering.admission(conn, member_workspace)[0]
                post(admin, path, {**quota, "revision": 5, "requests_per_minute": None}, method="PATCH").raise_for_status()
                assert metering.admission(conn, member_workspace)[0]
            print("redis_recovery=in_flight_capacity_restored")
        admin_id = admin.get("/api/v1/auth/me").json()["user"]["id"]
        strict = post(admin, f"/admin/users/{admin_id}/quota",
                      {"revision": 1, "mode": "strict", "monthly_token_limit": 1000}, method="PATCH")
        assert strict.status_code == 200
        strict_run = accept(admin, b["id"], unknown["id"]).json()
        assert execution.run_once("fixture-worker")
        assert admin.get(f"/api/v1/runs/{strict_run['id']}").json()["error"] == "STRICT_BUDGET_UNSUPPORTED"
        assert len(second[1]) == 1
        print("quota_competition=1_admitted settlement_writes=1")
