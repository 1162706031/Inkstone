"""Real supplier check for T06. Skipped unless explicitly enabled with authorized test credentials.

    RUN_PROVIDER_TESTS=1 PROVIDER_TEST_BASE_URL=https://.../v1 PROVIDER_TEST_MODEL=... \
    PROVIDER_TEST_API_KEY=... uv run --locked pytest -q -s tests/provider

Uses the platform's controlled request path (endpoint policy, pinned address, no retries,
no redirects). Prints supplier host, model, protocol, duration and result; never the key.
"""

import asyncio
import os
import time

import httpx
import pytest

from ai_platform.clients import provider

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_PROVIDER_TESTS") != "1"
    or not all(os.getenv(name) for name in ("PROVIDER_TEST_BASE_URL", "PROVIDER_TEST_API_KEY", "PROVIDER_TEST_MODEL")),
    reason="Set RUN_PROVIDER_TESTS=1 and PROVIDER_TEST_BASE_URL/API_KEY/MODEL with authorized test credentials",
)

PROMPT = [{"role": "user", "content": "Count from 1 to 40, one number per line."}]


def config():
    return os.environ["PROVIDER_TEST_BASE_URL"], os.environ["PROVIDER_TEST_API_KEY"], os.environ["PROVIDER_TEST_MODEL"]


def report(case, started, outcome):
    host = httpx.URL(os.environ["PROVIDER_TEST_BASE_URL"]).host
    print(f"provider={host} model={os.environ['PROVIDER_TEST_MODEL']} protocol=chat_completions "
          f"case={case} seconds={time.monotonic() - started:.2f} {outcome}")


def test_stream_and_usage():
    url, key, model = config()
    fragments = []

    async def collect(text):
        fragments.append(text)

    async def run():
        target = await provider.preflight(url)
        return await provider.request_text(target, key, model, PROMPT, 64, timeout=60, on_text=collect)

    started = time.monotonic()
    ok, usage, error = asyncio.run(run())
    report("stream", started, f"ok={ok} error={error} fragments={len(fragments)} usage={usage}")
    assert ok and error is None and len(fragments) > 1
    assert usage is not None and usage[0] > 0 and usage[1] > 0


def test_cancel_after_first_fragment():
    url, key, model = config()
    first = asyncio.Event()

    async def mark(text):
        first.set()

    async def run():
        target = await provider.preflight(url)
        request = asyncio.create_task(provider.request_text(target, key, model, PROMPT, 256, timeout=60, on_text=mark))
        await asyncio.wait_for(first.wait(), 30)
        request.cancel()
        return await request

    started = time.monotonic()
    ok, usage, error = asyncio.run(run())
    report("cancel", started, f"ok={ok} error={error} usage_after_cancel={usage}")
    assert not ok and error == "CANCELLED"
