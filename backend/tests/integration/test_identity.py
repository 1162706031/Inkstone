import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from secrets import token_bytes
from uuid import uuid4

import psycopg
import pyotp
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict

from ai_platform.api.main import app
from ai_platform.db import connection
from ai_platform.services import identity as service
from ai_platform.settings import settings

ORIGIN = "https://platform.test"
PASSWORD = "member-password-1"
ADMIN_PASSWORD = "administrator-password-2"


@pytest.fixture
def identity(request, monkeypatch, tmp_path):
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is required")
    assert conninfo_to_dict(dsn).get("dbname") == "ai_platform_test"
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT current_database()").fetchone()[0] == "ai_platform_test"
        exists = conn.execute("SELECT to_regnamespace('app') IS NOT NULL").fetchone()[0]
        assert not exists, "Use a fresh isolated ai_platform_test database"

    key = tmp_path / "credential.key"
    key.write_bytes(token_bytes(32))
    for name, value in {
        "DATABASE_URL": dsn, "CHECKPOINT_DATABASE_URL": dsn, "APP_ORIGIN": ORIGIN, "ENVIRONMENT": "test",
        "REDIS_URL": os.getenv("TEST_REDIS_URL", ""),
        "DELETION_ARCHIVE_PATH": str(tmp_path / "deletions.sqlite3"),
        "CREDENTIAL_KEY_FILE": str(key), "CREDENTIAL_KEY_VERSION": "test",
    }.items():
        monkeypatch.setenv(name, value)
    settings.cache_clear()
    # Each test starts with the business tables for its requested schema version.
    script = Path(__file__).resolve().parents[3] / "infra/sql/init.sql"
    requested_version = getattr(request, "param", "0001_identity")
    sections = re.findall(
        r"^-- ============ (\d{4}_\w+) ============\n(.*?)(?=^-- ============ |\Z)",
        script.read_text(encoding="utf-8"), flags=re.MULTILINE | re.DOTALL,
    )
    with psycopg.connect(dsn) as conn:
        for version, statements in sections:
            conn.execute(statements)
            if version == requested_version:
                break
        else:
            pytest.fail(f"Missing SQL for schema version {requested_version}")
    if redis_url := os.getenv("TEST_REDIS_URL"):
        monkeypatch.setenv("REDIS_URL", redis_url)
        monkeypatch.setenv("REDIS_PREFIX", f"ai-test-{uuid4().hex}")
        settings.cache_clear()
    else:
        # Exercise real session and database logic independently of Redis availability.
        monkeypatch.setattr(service, "rate_attempt", lambda *_: lambda: None)

    try:
        yield lambda: TestClient(app, base_url=ORIGIN)
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute("DROP SCHEMA app CASCADE")
        settings.cache_clear()


def post(client, path, data=None, method="POST"):
    csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
    return client.request(method, f"/api/v1{path}", headers={"origin": ORIGIN, "x-csrf-token": csrf}, json=data)


def sign_in(client, username, password):
    return post(client, "/auth/login", {"username": username, "password": password})


@pytest.mark.parametrize("identity", ["0006_member_deletion"], indirect=True)
def test_identity_lifecycle(identity):
    with connection() as conn:
        assert service.bootstrap_admin(conn)
        assert not service.bootstrap_admin(conn)

    with identity() as admin, identity() as first, identity() as second, identity() as another:
        assert first.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"}).status_code == 403
        login = sign_in(admin, "ADMIN", "admin")
        assert login.json() == {"next": "initial_password_change"}
        cookie = login.headers["set-cookie"]
        assert all(flag in cookie for flag in ("__Host-ai_session=", "HttpOnly", "Secure", "SameSite=lax", "Path=/"))
        assert admin.get("/api/v1/admin/users").status_code == 403
        assert post(admin, "/auth/change-initial-password", {"new_password": ADMIN_PASSWORD}).status_code == 204
        with connection() as conn:
            assert not service.bootstrap_admin(conn)
        assert sign_in(admin, "admin", "admin").status_code == 401
        assert sign_in(admin, "admin", ADMIN_PASSWORD).json()["next"] == "mfa_setup"
        assert admin.get("/api/v1/admin/users").status_code == 403
        setup = post(admin, "/auth/mfa/setup")
        totp = pyotp.TOTP(setup.json()["secret"])
        code = totp.now()
        confirm = post(admin, "/auth/mfa/confirm", {"code": code})
        assert confirm.status_code == 200
        assert len(confirm.json()["recovery_codes"]) == 10

        assert sign_in(another, "admin", ADMIN_PASSWORD).json()["next"] == "mfa_verify"
        assert post(another, "/auth/mfa/setup").status_code == 403
        assert post(another, "/auth/mfa/verify", {"code": code}).status_code == 401
        recovery = confirm.json()["recovery_codes"][0]
        assert post(another, "/auth/mfa/verify", {"recovery_code": recovery}).status_code == 200
        assert post(another, "/auth/logout").status_code == 204
        assert sign_in(another, "admin", ADMIN_PASSWORD).json()["next"] == "mfa_verify"
        assert post(another, "/auth/mfa/verify", {"recovery_code": recovery}).status_code == 401
        fresh = totp.at(datetime.now(timezone.utc) + timedelta(seconds=30))
        assert post(another, "/auth/mfa/verify", {"code": fresh}).status_code == 200
        assert post(another, "/auth/logout").status_code == 204

        admin_user = admin.get("/api/v1/auth/me").json()["user"]
        listing = admin.get("/api/v1/admin/users").json()
        assert post(admin, f"/admin/users/{admin_user['id']}",
                    {"status": "disabled", "revision": listing["items"][0]["revision"]}, method="PATCH").status_code == 403
        assert post(admin, f"/admin/users/{admin_user['id']}?revision=1", method="DELETE").status_code == 403

        users = []
        for client, name in [(first, "first"), (second, "second")]:
            result = post(admin, "/admin/users", {"username": name, "display_name": name.title()})
            assert result.status_code == 201
            created = result.json()
            user_id = created["user"]["id"]
            path = f"/admin/users/{user_id}"
            disabled = post(admin, path, {"status": "disabled", "revision": created["user"]["revision"]}, method="PATCH")
            assert disabled.status_code == 200
            enabled = post(admin, path, {"status": "active", "revision": disabled.json()["revision"]}, method="PATCH")
            assert enabled.json()["status"] == "pending"
            assert post(client, "/auth/activate", {"token": created["activation_token"], "password": PASSWORD}).status_code == 422
            reissued = post(admin, f"/admin/users/{user_id}/activation-token").json()["activation_token"]
            assert post(client, "/auth/activate", {"token": created["activation_token"], "password": PASSWORD}).status_code == 422
            assert post(client, "/auth/activate", {"token": reissued, "password": PASSWORD}).status_code == 200
            assert post(client, "/auth/activate", {"token": reissued, "password": PASSWORD}).status_code == 422
            assert sign_in(client, name, PASSWORD).json()["next"] == "ready"
            assert client.get("/api/v1/admin/users").status_code == 403
            users.append(client.get("/api/v1/auth/me").json())

        assert users[0]["workspace_id"] != users[1]["workspace_id"]
        cross_csrf = users[1]["csrf_token"]
        assert first.post("/api/v1/auth/logout", headers={"origin": ORIGIN, "x-csrf-token": cross_csrf}).status_code == 403
        assert first.get("/api/v1/auth/me").json()["user"]["id"] == users[0]["user"]["id"]
        assert sign_in(another, "first", PASSWORD).status_code == 200
        assert post(first, "/auth/revoke-other-sessions", {"password": PASSWORD}).status_code == 204
        assert another.get("/api/v1/auth/me").status_code == 401

        reset = post(admin, f"/admin/users/{users[0]['user']['id']}/password-reset").json()["reset_token"]
        assert first.get("/api/v1/auth/me").status_code == 401
        assert post(another, "/auth/reset-password", {"token": reset, "new_password": "changed-password-3"}).status_code == 200
        assert sign_in(another, "first", PASSWORD).status_code == 401
        assert sign_in(another, "first", "changed-password-3").status_code == 200
        member_list = admin.get("/api/v1/admin/users").json()["items"]
        second_user = next(user for user in member_list if user["username"] == "second")
        path = f"/admin/users/{second_user['id']}"
        disabled = post(admin, path, {"status": "disabled", "revision": second_user["revision"]}, method="PATCH")
        assert disabled.status_code == 200
        assert second.get("/api/v1/auth/me").status_code == 401
        enabled = post(admin, path, {"status": "active", "revision": disabled.json()["revision"]}, method="PATCH")
        assert enabled.json()["status"] == "active"
        assert sign_in(second, "second", PASSWORD).json()["next"] == "ready"
        assert post(second, f"{path}?revision={enabled.json()['revision']}", method="DELETE").status_code == 403
        assert post(admin, f"{path}?revision={second_user['revision']}", method="DELETE").status_code == 409
        assert post(admin, f"{path}?revision={enabled.json()['revision']}", method="DELETE").status_code == 204
        assert second.get("/api/v1/auth/me").status_code == 401
        assert sign_in(second, "second", PASSWORD).status_code == 401
        assert all(row["id"] != second_user["id"] for row in admin.get("/api/v1/admin/users").json()["items"])
        assert post(admin, path, {"status": "active", "revision": enabled.json()["revision"]+1}, method="PATCH").status_code == 404
        replacement = post(admin, "/admin/users", {"username": "second", "display_name": "New member"})
        assert replacement.status_code == 201 and replacement.json()["user"]["id"] != second_user["id"]
        assert post(second, "/auth/activate", {"token": replacement.json()["activation_token"], "password": PASSWORD}).status_code == 200
        assert sign_in(second, "second", PASSWORD).json()["next"] == "ready"
        assert second.get("/api/v1/auth/me").json()["workspace_id"] != users[1]["workspace_id"]
        assert another.get("/api/v1/auth/me").status_code == 200

        with connection() as conn:
            assert service.reset_admin_mfa(conn, "admin")
        assert admin.get("/api/v1/admin/users").status_code == 401
        assert sign_in(admin, "admin", ADMIN_PASSWORD).json()["next"] == "mfa_setup"


@pytest.mark.skipif(not os.getenv("TEST_REDIS_URL"), reason="TEST_REDIS_URL is required for real Redis rate limiting")
def test_login_rate_limit(identity):
    with identity() as client:
        for _ in range(5):
            assert sign_in(client, "absent", "invalid").status_code == 401
        assert sign_in(client, "absent", "invalid").status_code == 429
