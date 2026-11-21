import asyncio
import ipaddress
import json
import re
import socket

import httpx

from ai_platform.settings import settings


class ProviderFailure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def normalize_endpoint(value):
    try:
        url = httpx.URL(value)
        host = url.raw_host.decode("ascii")
    except (ValueError, httpx.InvalidURL) as exc:
        raise ProviderFailure("INVALID_ENDPOINT") from exc
    if (
        url.scheme not in ("https", "http") or not host or url.userinfo
        or any(character in value for character in ("?", "#", "\\", "%", "@"))
        or not re.fullmatch(r"[A-Za-z0-9._~/-]*", url.path)
        or any(segment in (".", "..") for segment in value.split("/"))
        or any(character.isspace() or ord(character) < 32 for character in value)
    ):
        raise ProviderFailure("INVALID_ENDPOINT")
    if url.port is not None and not 1 <= url.port <= 65535:
        raise ProviderFailure("INVALID_ENDPOINT")
    normalized = str(url).rstrip("/")
    if url.scheme == "http" and not explicitly_allowed(normalized):
        raise ProviderFailure("INVALID_ENDPOINT")
    return normalized


def explicitly_allowed(endpoint):
    config = settings()
    return endpoint in {entry.strip().rstrip("/") for entry in config.private_model_allowlist.split(",") if entry.strip()}


async def resolve(endpoint):
    parsed = httpx.URL(normalize_endpoint(endpoint))
    host = parsed.raw_host.decode("ascii")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    records = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(ipaddress.ip_address(record[4][0]) for record in records))
    allowed = explicitly_allowed(endpoint)
    if not addresses or any(
        ip.is_multicast or ip.is_unspecified or ip.is_link_local or not ip.is_global and not allowed
        for ip in addresses
    ):
        raise ProviderFailure("ENDPOINT_FORBIDDEN")
    if settings().environment == "production" and parsed.scheme == "http" and any(
        ip.is_loopback for ip in addresses
    ):
        raise ProviderFailure("ENDPOINT_FORBIDDEN")
    return parsed, host, addresses[0]


async def preflight(endpoint):
    try:
        async with asyncio.timeout(10):
            return await resolve(endpoint)
    except (TimeoutError, OSError) as exc:
        raise ProviderFailure("ENDPOINT_UNAVAILABLE") from exc


def usage_pair(usage):
    if not isinstance(usage, dict):
        return None
    first, second = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if all(type(value) is int and 0 <= value <= 10**12 for value in (first, second)):
        return first, second
    return None


def _context_length(item):
    for field in ("context_length", "context_window", "max_model_len", "max_context_length"):
        value = item.get(field)
        if type(value) is int and 1 < value <= 10**9:
            return value
    return None


async def list_models(target, api_key, *, timeout=15):
    """Read the OpenAI-compatible ``GET /models`` listing from a pinned, preflighted target."""
    parsed, host, address = target
    endpoint = parsed.copy_with(host=str(address), path=parsed.path.rstrip("/") + "/models")
    headers = {"Host": parsed.netloc.decode("ascii"), "Authorization": f"Bearer {api_key}",
               "Accept": "application/json", "Accept-Encoding": "identity"}
    try:
        async with asyncio.timeout(timeout), httpx.AsyncClient(
            trust_env=False, follow_redirects=False, timeout=httpx.Timeout(timeout, connect=10),
            transport=httpx.AsyncHTTPTransport(retries=0, trust_env=False),
        ) as client, client.stream("GET", endpoint, headers=headers, extensions={"sni_hostname": host}) as response:
            if response.status_code in (401, 403):
                raise ProviderFailure("UPSTREAM_UNAUTHORIZED")
            if response.status_code != 200:
                raise ProviderFailure("UPSTREAM_REJECTED")
            body = bytearray()
            async for chunk in response.aiter_raw():
                body.extend(chunk)
                if len(body) > 4 * 1024 * 1024:
                    raise ProviderFailure("UPSTREAM_RESPONSE_LIMIT")
        payload = json.loads(body)
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise ProviderFailure("UPSTREAM_TIMEOUT") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise ProviderFailure("UPSTREAM_PROTOCOL") from exc
    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ProviderFailure("UPSTREAM_PROTOCOL")
    found = {}
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("id"), str) and 0 < len(item["id"]) <= 200:
            found.setdefault(item["id"], _context_length(item))
    return [{"id": key, "context_length": found[key]} for key in sorted(found)]


async def request_text(target, api_key, model, messages, output_limit, *, timeout=120, temperature=None, on_text=None,
                       probe=False):
    """Stream a chat completion. ``probe`` succeeds on the first content or reasoning token."""
    parsed, host, address = target
    authority = parsed.netloc.decode("ascii")
    endpoint = parsed.copy_with(host=str(address), path=parsed.path.rstrip("/") + "/chat/completions")
    body = {
        "model": model, "messages": messages, "max_tokens": output_limit,
        "stream": True, "stream_options": {"include_usage": True},
    }
    if temperature is not None:
        body["temperature"] = temperature
    headers = {"Host": authority, "Authorization": f"Bearer {api_key}", "Accept": "text/event-stream",
               "Accept-Encoding": "identity"}
    seen_text = False
    usage = None
    terminal = False
    try:
        start = asyncio.get_running_loop().time()
        deadline = start + timeout
        async with asyncio.timeout_at(min(deadline, start+30)) as timer:
            async with httpx.AsyncClient(
                trust_env=False, follow_redirects=False,
                timeout=httpx.Timeout(30, connect=10),
                transport=httpx.AsyncHTTPTransport(retries=0, trust_env=False),
            ) as client, client.stream(
                "POST", endpoint, headers=headers, json=body, extensions={"sni_hostname": host},
            ) as response:
                if response.status_code != 200:
                    return False, None, "UPSTREAM_REJECTED"
                if response.headers.get("content-type", "").split(";")[0].strip() != "text/event-stream":
                    return False, None, "UPSTREAM_PROTOCOL"
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    return False, None, "UPSTREAM_PROTOCOL"
                buffer = bytearray()
                event = []
                size = 0
                async for chunk in response.aiter_raw():
                    size += len(chunk)
                    if size > 512 * 1024:
                        return False, usage, "UPSTREAM_RESPONSE_LIMIT"
                    buffer.extend(chunk)
                    while b"\n" in buffer:
                        raw, _, remaining = buffer.partition(b"\n")
                        buffer = bytearray(remaining)
                        if raw.startswith(b"data:"):
                            event.append(raw[5:].lstrip())
                        elif not raw.strip() and event:
                            data = b"\n".join(event).strip()
                            event.clear()
                            if data == b"[DONE]":
                                terminal = True
                                break
                            payload = json.loads(data)
                            if not isinstance(payload, dict) or "error" in payload:
                                return False, usage, "UPSTREAM_PROTOCOL"
                            usage = usage_pair(payload.get("usage")) or usage
                            for choice in payload.get("choices") or ():
                                delta = choice.get("delta") or {}
                                text = delta.get("content")
                                if probe and any(isinstance(delta.get(k), str) and delta[k]
                                                 for k in ("content", "reasoning_content", "reasoning")):
                                    # A probe only needs proof of a live answer; stop at the first token.
                                    seen_text = terminal = True
                                    break
                                if not seen_text and isinstance(text, str) and text:
                                    seen_text = True
                                    timer.reschedule(deadline)
                                if isinstance(text, str) and text and on_text:
                                    await on_text(text)
                            if terminal:
                                break
                    if terminal:
                        break
    except asyncio.CancelledError:
        return False, usage, "CANCELLED"
    except (TimeoutError, httpx.TimeoutException):
        return False, usage, "UPSTREAM_TIMEOUT"
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return False, usage, "UPSTREAM_PROTOCOL"
    return (True, usage, None) if terminal and seen_text else (False, usage, "UPSTREAM_INTERRUPTED")
