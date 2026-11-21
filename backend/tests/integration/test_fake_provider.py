import asyncio
import json
import threading
from contextlib import aclosing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import openai
import pytest
from langchain_openai import ChatOpenAI


def event(*, text=None, usage=None):
    choices = [] if usage else [{"index": 0, "delta": {"content": text}}]
    payload = {"id": "probe", "object": "chat.completion.chunk", "created": 1,
               "model": "probe", "choices": choices}
    if usage:
        payload["usage"] = usage
    return f"data: {json.dumps(payload)}\n\n".encode()


@pytest.fixture
def provider():
    requests = []
    release = threading.Event()
    disconnected = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, body))
            mode = body["messages"][-1]["content"]
            if mode == "rate_limit":
                self.send_response(429)
                self.end_headers()
                return
            if mode == "timeout":
                release.wait(5)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()

            def send(data):
                self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                self.wfile.flush()

            send(event(text="你好"))
            if mode == "disconnect":
                return  # Close without the terminal HTTP chunk.
            if mode == "cancel":
                while not release.wait(0.05):
                    try:
                        send(b": keepalive\n\n")
                    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                        disconnected.set()
                        return
                return
            send(event(text="世界"))
            if mode == "duplicate":
                send(event(text="世界"))
            if mode != "no_usage":
                send(event(usage={"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}))
            send(b"data: [DONE]\n\n")
            self.wfile.write(b"0\r\n\r\n")

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/v1", requests, disconnected
        finally:
            release.set()
            server.shutdown()
            thread.join()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode, expected, error",
    [
        ("normal", "你好世界", None),
        ("no_usage", "你好世界", None),
        ("duplicate", "你好世界世界", None),
        ("rate_limit", "", openai.RateLimitError),
        ("timeout", "", openai.APITimeoutError),
        ("disconnect", "你好", openai.APIConnectionError),
        ("cancel", "你好", asyncio.CancelledError),
    ],
)
async def test_chat_completions_stream(provider, mode, expected, error):
    url, requests, disconnected = provider
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
        model = ChatOpenAI(
            model="probe", api_key="fake", base_url=url, max_retries=0,
            timeout=0.25 if mode == "timeout" else 3,
            stream_usage=True, use_responses_api=False, http_async_client=client,
        )
        texts = []
        usage = []
        first = asyncio.Event()

        async def consume():
            async with aclosing(model.astream(mode)) as stream:
                async for chunk in stream:
                    texts.append(chunk.content)
                    if chunk.usage_metadata:
                        usage.append(chunk.usage_metadata)
                    first.set()

        if mode == "cancel":
            task = asyncio.create_task(consume())
            try:
                await asyncio.wait_for(first.wait(), timeout=3)
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            assert await asyncio.to_thread(disconnected.wait, 3)
        elif error:
            with pytest.raises(error) as raised:
                await consume()
            if mode == "disconnect":
                assert isinstance(raised.value.__cause__.__cause__, httpx.RemoteProtocolError)
        else:
            await consume()

    assert "".join(texts) == expected
    if mode in {"normal", "duplicate"}:
        assert len(usage) == 1 and usage[0]["total_tokens"] == 7
        assert usage[0]["input_tokens"] == 3 and usage[0]["output_tokens"] == 4
    else:
        assert usage == []
    assert len(requests) == 1
    path, body = requests[0]
    assert path == "/v1/chat/completions"
    assert body["messages"] == [{"content": mode, "role": "user"}]
    assert body["stream"] is True and body["stream_options"] == {"include_usage": True}
    assert "tools" not in body
    print(f"{mode}: upstream_requests={len(requests)}, usage_records={len(usage)}")
