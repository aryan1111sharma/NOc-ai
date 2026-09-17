"""ASGI reverse proxy: filters `tools[]` on chat-completion requests, forwards
everything else verbatim.

Runtime path is exactly one extra hop:

    LibreChat -> this proxy -> provider (GLM / Kimi)

Transparency rules that keep provider behaviour identical:

  * Streaming uses `aiter_raw()`, so SSE chunks are relayed as received - no buffering,
    no re-encoding, no re-chunking. First-token latency is provider latency plus one
    in-network hop.
  * Response status and body are relayed untouched, so provider errors surface to
    LibreChat exactly as the provider sent them. The proxy never retries; retry policy
    stays with LibreChat.
  * Request headers pass through (so `Authorization` reaches the provider and the proxy
    holds no credential of its own). Hop-by-hop headers and `content-length` are dropped:
    the body changes length when tools are removed, so a stale length would corrupt it.
  * One shared AsyncClient keeps connections to the provider warm, which is what stops
    the extra hop from costing a fresh TLS handshake per request.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
from typing import Iterable

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from .adapter import filter_body
from .config import Settings, settings as default_settings
from .metadata import RegistryMetadata

log = logging.getLogger("tool_filter_proxy")

# Headers that describe a single transport hop and must not be forwarded, plus
# content-length (recomputed) and host (rewritten to the upstream).
_HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "trailers", "transfer-encoding", "upgrade",
})
_DROP_REQUEST_HEADERS = _HOP_BY_HOP | {"host", "content-length"}
_DROP_RESPONSE_HEADERS = _HOP_BY_HOP | {"content-length"}


def _forwardable(headers: Iterable[tuple[str, str]], drop: frozenset) -> dict[str, str]:
    return {k: v for k, v in headers if k.lower() not in drop}


def _is_chat_completions(path: str) -> bool:
    return path.rstrip("/").endswith("/chat/completions")


class Proxy:
    def __init__(self, settings: Settings, metadata: RegistryMetadata,
                 client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._metadata = metadata
        self._client = client

    def resolve_upstream(self, path: str) -> tuple[str, str] | None:
        """`/kimi/chat/completions` -> ("kimi", "https://api.kimi.com/coding/v1/chat/completions")."""
        parts = path.lstrip("/").split("/", 1)
        if not parts or parts[0] not in self._settings.upstreams:
            return None
        key = parts[0]
        remainder = parts[1] if len(parts) > 1 else ""
        base = self._settings.upstreams[key].rstrip("/")
        return key, f"{base}/{remainder}" if remainder else base

    async def handle(self, request: Request) -> StreamingResponse | JSONResponse:
        started = time.perf_counter()
        resolved = self.resolve_upstream(request.url.path)
        if resolved is None:
            return JSONResponse(
                {"error": {"message": f"no upstream configured for path {request.url.path}",
                            "type": "invalid_request_error"}},
                status_code=404,
            )
        upstream_key, url = resolved
        raw = await request.body()

        body_out, decision = raw, None
        if raw and _is_chat_completions(request.url.path) and request.method == "POST":
            body_out, decision = self._maybe_filter(raw)

        headers = _forwardable(request.headers.items(), _DROP_REQUEST_HEADERS)
        params = dict(request.query_params)

        try:
            upstream_req = self._client.build_request(
                request.method, url, content=body_out, headers=headers, params=params,
            )
            upstream_resp = await self._client.send(upstream_req, stream=True)
        except httpx.TimeoutException:
            self._log(upstream_key, request.url.path, decision, started, 504)
            return JSONResponse(
                {"error": {"message": "upstream provider timed out", "type": "timeout"}},
                status_code=504,
            )
        except httpx.HTTPError as exc:
            self._log(upstream_key, request.url.path, decision, started, 502)
            return JSONResponse(
                {"error": {"message": f"upstream provider unreachable: {type(exc).__name__}",
                            "type": "bad_gateway"}},
                status_code=502,
            )

        self._log(upstream_key, request.url.path, decision, started, upstream_resp.status_code)

        async def stream():
            try:
                async for chunk in upstream_resp.aiter_raw():
                    yield chunk
            finally:
                # Runs on normal completion AND on client disconnect mid-stream, so a
                # cancelled stream never leaks the upstream connection.
                await upstream_resp.aclose()

        return StreamingResponse(
            stream(),
            status_code=upstream_resp.status_code,
            headers=_forwardable(upstream_resp.headers.items(), _DROP_RESPONSE_HEADERS),
        )

    def _maybe_filter(self, raw: bytes) -> tuple[bytes, object | None]:
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return raw, None  # not JSON we understand - forward verbatim
        if not isinstance(body, dict):
            return raw, None

        new_body, decision = filter_body(
            body, metadata_provider=self._metadata,
            top_k=self._settings.filter_top_k,
            enabled=not self._settings.passthrough_only,
        )
        if not decision.applied:
            return raw, decision  # byte-identical original, not a re-serialized copy
        return json.dumps(new_body).encode("utf-8"), decision

    def _log(self, upstream: str, path: str, decision, started: float, status: int) -> None:
        elapsed = (time.perf_counter() - started) * 1000
        if decision is None:
            log.info("%s %s -> %d (%.1fms, no filtering)", upstream, path, status, elapsed)
            return
        log.info(
            "%s %s -> %d (%.1fms) tools %d->%d pinned=%d reason=%s tier=%s filter=%sms",
            upstream, path, status, elapsed, decision.tools_before, decision.tools_after,
            decision.pinned, decision.reason, decision.fallback_tier, decision.filter_ms,
        )


def build_app(settings: Settings | None = None, metadata: RegistryMetadata | None = None,
              client: httpx.AsyncClient | None = None) -> Starlette:
    """`metadata` and `client` are injection points for tests (a prebuilt index, and an
    httpx MockTransport client); real wiring loads/creates them once at startup.

    The registry index and the upstream client are built in the lifespan handler rather
    than at import time so a registry problem surfaces as a logged warning on boot, and
    so the connection pool belongs to the running event loop.
    """
    settings = settings or default_settings
    owns_client = client is None

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        nonlocal client
        app.state.metadata = metadata or RegistryMetadata.load()
        if client is None:
            client = httpx.AsyncClient(
                timeout=httpx.Timeout(settings.read_timeout, connect=settings.connect_timeout),
                follow_redirects=False,
            )
        app.state.proxy = Proxy(settings, app.state.metadata, client)
        log.info("proxy ready on %s:%d upstreams=%s top_k=%d passthrough_only=%s",
                 settings.host, settings.port, sorted(settings.upstreams),
                 settings.filter_top_k, settings.passthrough_only)
        try:
            yield
        finally:
            if owns_client and client is not None:
                await client.aclose()

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({
            "status": "ok",
            "upstreams": sorted(settings.upstreams),
            "registry_tools_indexed": request.app.state.metadata.size,
            "filtering_enabled": not settings.passthrough_only,
        })

    async def dispatch(request: Request):
        return await request.app.state.proxy.handle(request)

    return Starlette(
        routes=[
            Route("/healthz", health, methods=["GET"]),
            Route("/{path:path}", dispatch,
                  methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]),
        ],
        lifespan=lifespan,
    )
