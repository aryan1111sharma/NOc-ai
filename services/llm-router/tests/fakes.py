"""Test doubles for a scripted OpenAI-compatible upstream provider.

Built on httpx.MockTransport rather than a real HTTP server: each Connection
in a test gets its own scripted behaviour, dispatched by base_url, and errors
(timeout / connection failure / mid-stream drop) are simulated by raising the
same exception types httpx itself raises for those conditions -- so
llm_router.proxy is exercised through the exact same exception classes it
will see in production, with no real socket involved.
"""

from __future__ import annotations

import httpx


class ScriptedStream(httpx.AsyncByteStream):
    """An SSE-style byte stream that can fail partway through."""

    def __init__(self, chunks: list[bytes], fail_after: int | None = None):
        self._chunks = chunks
        self._fail_after = fail_after

    async def __aiter__(self):
        for i, chunk in enumerate(self._chunks):
            if self._fail_after is not None and i == self._fail_after:
                raise httpx.ReadError("simulated mid-stream drop")
            yield chunk

    async def aclose(self) -> None:
        return None


def json_ok(body: dict) -> httpx.Response:
    return httpx.Response(200, json=body)


def status(code: int, body: dict | None = None) -> httpx.Response:
    return httpx.Response(code, json=body or {"error": {"message": f"status {code}"}})


def stream_ok(chunks: list[bytes], fail_after: int | None = None) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        stream=ScriptedStream(chunks, fail_after=fail_after),
    )


def stream_error(code: int, body: dict | None = None) -> httpx.Response:
    """A provider that rejects the request before any SSE bytes are sent --
    this is a status-code failure, not a stream failure, even though the
    caller asked for stream=true."""
    return status(code, body)


class FakeProviders:
    """Maps a Connection's base_url to a queue of canned responses/behaviours.

    Each entry in the queue is either an httpx.Response or an Exception
    instance/class to raise. Recording which base_urls were actually called,
    and in what order, is what the fallback-order assertions check.
    """

    def __init__(self):
        self._queues: dict[str, list] = {}
        self.calls: list[str] = []
        self.request_bodies: dict[str, list[dict]] = {}

    def script(self, base_url: str, *behaviours) -> None:
        self._queues.setdefault(base_url, []).extend(behaviours)

    def handler(self, request: httpx.Request):
        base = f"{request.url.scheme}://{request.url.netloc.decode()}"
        self.calls.append(base)
        self.request_bodies.setdefault(base, []).append(_read_json(request))
        queue = self._queues.get(base)
        if not queue:
            raise AssertionError(f"no scripted behaviour left for {base}")
        behaviour = queue.pop(0)
        if isinstance(behaviour, Exception):
            raise behaviour
        if isinstance(behaviour, type) and issubclass(behaviour, Exception):
            raise behaviour("scripted failure", request=request)
        return behaviour

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def _read_json(request: httpx.Request) -> dict:
    import json

    if not request.content:
        return {}
    return json.loads(request.content)
