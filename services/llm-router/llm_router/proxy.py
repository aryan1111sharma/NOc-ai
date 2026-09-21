"""Routing/fallback engine: resolves an agent_id to its Combo, resolves each
enabled target against the Catalog, and calls them in priority order,
falling back to the next one only when the failure happened before any
response bytes reached the caller.

Fallback triggers on exactly four conditions, matching the spec this service
was built to: HTTP 429, any 5xx, a request timeout, or a connection error.
Anything else -- a 400, a 401, a 404 -- is the caller's problem, not the
provider's, and is returned to the client from whichever target produced
it, with no further attempts.

Streaming is the one place "before any response bytes" needs a precise
definition: HTTP delivers status and headers before the body, so a
streaming request is committed to its target the moment status/headers
come back non-retryable. A drop after that point ends the stream; it never
starts a second target (`no mid-stream fallback`).
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .catalog import Catalog, ResolvedTarget
from .store import Combo, ComboTarget, Registry

#: The exact failure modes this router is required to fall back on. Anything
#: else propagates to the caller from the target that produced it.
_RETRYABLE_EXCEPTIONS = (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError)


def _is_retryable_status(status_code: int) -> bool:
    return status_code == 429 or 500 <= status_code < 600


class ComboNotFoundError(Exception):
    def __init__(self, agent_id: str):
        super().__init__(f"no such combo: {agent_id!r}")
        self.agent_id = agent_id


class ComboDisabledError(Exception):
    def __init__(self, agent_id: str):
        super().__init__(f"combo {agent_id!r} is disabled")
        self.agent_id = agent_id


class NoEnabledTargetsError(Exception):
    def __init__(self, agent_id: str):
        super().__init__(f"combo {agent_id!r} has no enabled targets registered in the catalog")
        self.agent_id = agent_id


class AllTargetsFailedError(Exception):
    def __init__(self, agent_id: str, attempted: list[str], last_error: str):
        super().__init__(
            f"combo {agent_id!r}: all targets failed "
            f"(attempted {attempted}); last error: {last_error}"
        )
        self.agent_id = agent_id
        self.attempted = attempted
        self.last_error = last_error


@dataclass
class DispatchResult:
    target: str
    attempted: list[str]
    status_code: int
    headers: httpx.Headers
    #: Set for a non-streaming response.
    body: dict | None = None
    #: Set for a streaming response; caller relays via .aiter_raw() and must
    #: eventually call .aclose() on it.
    stream_response: httpx.Response | None = None


def _target_label(target: ComboTarget) -> str:
    return f"{target.endpoint}/{target.model}"


async def _resolve_candidates(
    catalog: Catalog, combo: Combo
) -> list[tuple[ComboTarget, ResolvedTarget]]:
    """Enabled targets, in priority order, with their real base_url/api_key
    resolved from the catalog. A target whose endpoint/model is no longer
    registered is silently skipped -- config drift, not an error (see
    Catalog.resolve)."""
    resolved: list[tuple[ComboTarget, ResolvedTarget]] = []
    for target in combo.targets:
        if not target.enabled:
            continue
        real = await catalog.resolve(target.endpoint, target.model)
        if real is not None:
            resolved.append((target, real))
    return resolved


def _upstream_request(target: ComboTarget, real: ResolvedTarget, body: dict) -> tuple[str, dict, dict]:
    url = real.base_url.rstrip("/") + "/chat/completions"
    headers = {"authorization": f"Bearer {real.api_key}"} if real.api_key else {}
    upstream_body = {**body, "model": target.model}
    return url, headers, upstream_body


class Router:
    """Stateless apart from the Registry/Catalog it reads and the HTTP
    client it reuses across calls -- all handed in, so tests inject a
    scripted transport and a static catalog instead of hitting real
    providers or a real librechat.yaml."""

    def __init__(
        self,
        registry: Registry,
        catalog: Catalog,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
    ):
        self._registry = registry
        self._catalog = catalog
        self._client = http_client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = http_client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def dispatch(self, body: dict) -> DispatchResult:
        agent_id = body.get("model")
        combo = self._registry.get_combo(agent_id)
        if combo is None:
            raise ComboNotFoundError(agent_id)
        if not combo.enabled:
            raise ComboDisabledError(agent_id)

        candidates = await _resolve_candidates(self._catalog, combo)
        if not candidates:
            raise NoEnabledTargetsError(agent_id)

        is_stream = bool(body.get("stream"))
        attempted: list[str] = []
        last_error = "unknown error"

        for target, real in candidates:
            label = _target_label(target)
            attempted.append(label)
            url, headers, upstream_body = _upstream_request(target, real, body)
            try:
                if is_stream:
                    result = await self._try_stream(label, url, headers, upstream_body, attempted)
                else:
                    result = await self._try_json(label, url, headers, upstream_body, attempted)
            except _RETRYABLE_EXCEPTIONS as exc:
                last_error = f"{label}: {type(exc).__name__}: {exc}"
                continue
            if result is None:
                # Retryable HTTP status; already logged below by the helper.
                last_error = f"{label}: retryable HTTP status"
                continue
            return result

        raise AllTargetsFailedError(agent_id, attempted, last_error)

    async def _try_json(self, label, url, headers, upstream_body, attempted) -> DispatchResult | None:
        response = await self._client.post(url, json=upstream_body, headers=headers)
        if _is_retryable_status(response.status_code):
            return None
        return DispatchResult(
            target=label,
            attempted=list(attempted),
            status_code=response.status_code,
            headers=response.headers,
            body=response.json(),
        )

    async def _try_stream(self, label, url, headers, upstream_body, attempted) -> DispatchResult | None:
        request = self._client.build_request("POST", url, json=upstream_body, headers=headers)
        response = await self._client.send(request, stream=True)
        if _is_retryable_status(response.status_code):
            await response.aclose()
            return None
        return DispatchResult(
            target=label,
            attempted=list(attempted),
            status_code=response.status_code,
            headers=response.headers,
            stream_response=response,
        )
