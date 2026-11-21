import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ai_platform.settings import settings
from test_identity import identity, post  # noqa: F401 - pytest fixture
from test_models_metering import enroll_admin


@contextmanager
def model_listing(status=200):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            seen.append((self.path, self.headers["Authorization"]))
            body = json.dumps({"object": "list", "data": [
                {"id": "gpt-small", "object": "model"},
                {"id": "long-ctx", "object": "model", "context_length": 131072},
                {"id": "gpt-small", "object": "model"},
                {"object": "model"},
            ]}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/v1", seen
        finally:
            server.shutdown()
            thread.join()


@pytest.mark.parametrize("identity", ["0004_metering"], indirect=True)
def test_discover_and_import_models(identity, monkeypatch):
    with identity() as admin, model_listing() as (url, seen), model_listing(401) as (denied, _):
        monkeypatch.setenv("PRIVATE_MODEL_ALLOWLIST", f"{url},{denied}")
        settings.cache_clear()
        enroll_admin(admin)
        conn_id = post(admin, "/admin/model-connections", {"name": "p", "base_url": url, "api_key": "sk-1"}).json()["id"]

        found = admin.get(f"/api/v1/admin/model-connections/{conn_id}/discover")
        assert found.status_code == 200, found.text
        assert found.json()["items"] == [
            {"upstream_model": "gpt-small", "context_limit_tokens": None, "registered": False},
            {"upstream_model": "long-ctx", "context_limit_tokens": 131072, "registered": False},
        ]
        assert seen == [("/v1/models", "Bearer sk-1")]

        result = post(admin, f"/admin/model-connections/{conn_id}/models/import", {"models": [
            {"upstream_model": "gpt-small"}, {"upstream_model": "long-ctx", "context_limit_tokens": 131072},
        ]})
        assert result.json() == {"created": 2, "skipped": 0}
        again = post(admin, f"/admin/model-connections/{conn_id}/models/import", {"models": [{"upstream_model": "gpt-small"}]})
        assert again.json() == {"created": 0, "skipped": 1}

        models = {m["upstream_model"]: m for m in admin.get(f"/api/v1/admin/model-connections/{conn_id}/models").json()["items"]}
        assert models["long-ctx"]["context_limit_tokens"] == 131072
        assert models["long-ctx"]["max_output_tokens"] == 4096
        assert models["gpt-small"]["context_limit_tokens"] == 8192
        assert all(m["last_test_status"] == "untested" for m in models.values())
        assert all(m["registered"] for m in admin.get(f"/api/v1/admin/model-connections/{conn_id}/discover").json()["items"])

        bad = post(admin, "/admin/model-connections", {"name": "q", "base_url": denied, "api_key": "sk-2"}).json()["id"]
        failed = admin.get(f"/api/v1/admin/model-connections/{bad}/discover")
        assert failed.status_code == 502
        assert failed.json()["error"]["message"] == "Provider rejected the API key"
