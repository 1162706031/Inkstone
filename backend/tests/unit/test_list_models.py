import asyncio
import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ai_platform.clients import provider
from ai_platform.settings import settings


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


def discover(url, key="sk-1"):
    async def run():
        return await provider.list_models(await provider.preflight(url), key)
    return asyncio.run(run())


def test_lists_unique_models_with_context(monkeypatch):
    with model_listing() as (url, seen):
        monkeypatch.setenv("PRIVATE_MODEL_ALLOWLIST", url)
        settings.cache_clear()
        assert discover(url) == [
            {"id": "gpt-small", "context_length": None},
            {"id": "long-ctx", "context_length": 131072},
        ]
        assert seen == [("/v1/models", "Bearer sk-1")]


def test_rejected_key_is_reported(monkeypatch):
    with model_listing(401) as (url, _):
        monkeypatch.setenv("PRIVATE_MODEL_ALLOWLIST", url)
        settings.cache_clear()
        with pytest.raises(provider.ProviderFailure) as failure:
            discover(url)
        assert failure.value.code == "UPSTREAM_UNAUTHORIZED"


def test_private_endpoint_needs_allowlist(monkeypatch):
    with model_listing() as (url, seen):
        monkeypatch.setenv("PRIVATE_MODEL_ALLOWLIST", "")
        settings.cache_clear()
        with pytest.raises(provider.ProviderFailure):
            discover(url)
        assert seen == []


@contextmanager
def slow_stream(first_delta):
    """Sends one delta, then stalls far longer than a probe should wait."""
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunk = {"choices": [{"delta": first_delta}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.flush()
            release.wait(10)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/v1"
        finally:
            release.set()
            server.shutdown()


@pytest.mark.parametrize("delta", [{"content": "OK"}, {"reasoning_content": "思考"}])
def test_probe_returns_on_first_token(monkeypatch, delta):
    with slow_stream(delta) as url:
        monkeypatch.setenv("PRIVATE_MODEL_ALLOWLIST", url)
        settings.cache_clear()

        async def run():
            target = await provider.preflight(url)
            return await provider.request_text(target, "k", "m", [{"role": "user", "content": "hi"}], 8,
                                               timeout=5, probe=True)
        started = time.monotonic()
        ok, _, error = asyncio.run(run())
        assert (ok, error) == (True, None)
        assert time.monotonic() - started < 3
