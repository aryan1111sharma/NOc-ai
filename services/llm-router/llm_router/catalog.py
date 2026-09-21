"""Read-only view of the provider credentials/models an admin has registered
through LibreChat's OWN existing configuration system -- librechat.yaml's
base `endpoints.custom` list, merged with whatever an admin has since added
or changed through LibreChat's own Admin Panel (e.g. a new OpenRouter entry)
-- the EXACT resolution LibreChat's own backend already performs for direct
model execution (see `loadCustomConfig.js` / `getAppConfig` /
`getCustomEndpointConfig` in the LibreChat source, and
`packages/data-schemas/src/app/resolution.ts` for the admin-override merge).

This service never re-implements that resolution and never keeps its own
copy of a provider credential. It asks LibreChat's own backend, over the
internal Docker network, through a small internal-only surface added to the
existing LLM Routing admin facade (apps/ai-platform/librechat/custom-image/
files/api/llmRouting.js):

    GET  {librechat_api_url}/api/llm-routing/internal/catalog
         -> every currently resolvable {endpoint, model} pair, masked (no key)
    POST {librechat_api_url}/api/llm-routing/internal/resolve
         body {endpoint, model} -> {baseURL, apiKey} for that ONE pair, or 404

Both are gated by a shared secret (LLM_ROUTER_INTERNAL_SECRET) known only to
this service and that facade -- never a provider API key, and deliberately
outside the facade's browser-facing `requireJwtAuth` gate, since this caller
has no browser session. A resolved api_key is used in-memory for one
outbound call and is never written to this service's own registry, logged,
or echoed back to an admin UI.

For tests, `endpoints=[...]` still injects a fixed, in-memory list (with a
literal apiKey or a "${VAR}"-style placeholder resolved against this
process's own environment) instead of making a network call -- nothing about
the admin CRUD/fallback logic downstream of this class needs to know the
difference.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import httpx

_ENV_VAR = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


@dataclass(frozen=True)
class CatalogTarget:
    """One selectable (credential, model) pair, exactly as an admin should
    see it: an endpoint name and a model. Never a key."""

    endpoint: str
    model: str

    def masked(self) -> dict:
        return {"endpoint": self.endpoint, "model": self.model}


@dataclass(frozen=True)
class ResolvedTarget:
    """The real values needed to call a provider. Only ever handed to
    llm_router.proxy -- never serialized into an HTTP response."""

    base_url: str
    api_key: str


def _resolve_env(value: str) -> str:
    """`"${GLM_API_KEY}"` -> the real environment value. A literal string
    (librechat.yaml's schema allows one) passes through unchanged. Used only
    for the static, test-only `endpoints=` path -- the real network path
    gets an already-resolved apiKey back from LibreChat itself."""
    match = _ENV_VAR.match(value or "")
    if not match:
        return value or ""
    return os.environ.get(match.group(1), "")


class Catalog:
    """`endpoints`, when given, overrides the network call entirely -- used
    by tests to supply a fixed list without a real LibreChat backend. This
    mirrors the previous file-parsing Catalog's own test seam, so nothing
    below this layer (Registry/Router/admin CRUD) needed to change.
    """

    def __init__(
        self,
        librechat_api_url: str | None = None,
        *,
        internal_secret: str | None = None,
        self_endpoint_name: str = "NOC Router",
        endpoints: list[dict] | None = None,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
    ):
        self._api_url = librechat_api_url.rstrip("/") if librechat_api_url else None
        self._secret = internal_secret
        self._self_name = self_endpoint_name
        self._static_endpoints = endpoints
        self._client = http_client
        self._timeout = timeout

    def _headers(self) -> dict:
        return {"X-Internal-Secret": self._secret} if self._secret else {}

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        if self._client is not None:
            return await self._client.request(method, f"{self._api_url}{path}", **kwargs)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await client.request(method, f"{self._api_url}{path}", **kwargs)

    async def _fetch_endpoints(self) -> list[dict]:
        if not self._api_url:
            return []
        resp = await self._request(
            "GET", "/api/llm-routing/internal/catalog", headers=self._headers()
        )
        resp.raise_for_status()
        return resp.json().get("endpoints", [])

    async def list_targets(self) -> list[CatalogTarget]:
        if self._static_endpoints is not None:
            source = self._static_endpoints
        else:
            source = await self._fetch_endpoints()
        # Exclude the router's own custom-endpoint entry: routing to itself
        # would be an infinite loop, not a fallback target.
        targets: list[CatalogTarget] = []
        for endpoint in source:
            if endpoint.get("name") == self._self_name:
                continue
            name = endpoint.get("name", "")
            models = (endpoint.get("models") or {}).get("default") or []
            for model in models:
                targets.append(CatalogTarget(endpoint=name, model=model))
        return targets

    async def resolve(self, endpoint: str, model: str) -> ResolvedTarget | None:
        """The real base_url/api_key for one (endpoint, model) pair, or
        None if it is no longer registered -- the endpoint was removed, or
        that model dropped from its `models.default` list. Treated as
        config drift by the caller, not an error."""
        if endpoint == self._self_name:
            return None
        if self._static_endpoints is not None:
            for endpoint_cfg in self._static_endpoints:
                if endpoint_cfg.get("name") != endpoint:
                    continue
                models = (endpoint_cfg.get("models") or {}).get("default") or []
                if model not in models:
                    return None
                return ResolvedTarget(
                    base_url=endpoint_cfg.get("baseURL", ""),
                    api_key=_resolve_env(endpoint_cfg.get("apiKey", "")),
                )
            return None

        if not self._api_url:
            return None
        resp = await self._request(
            "POST",
            "/api/llm-routing/internal/resolve",
            json={"endpoint": endpoint, "model": model},
            headers=self._headers(),
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        return ResolvedTarget(base_url=data.get("baseURL", ""), api_key=data.get("apiKey") or "")
