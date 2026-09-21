"""ASGI app: the OpenAI-compatible surface (/v1/*) plus the admin surface
(/admin/*) that the LibreChat custom-image facade (apps/ai-platform/librechat/
custom-image/files/api/llmRouting.js) proxies to.

/admin/* carries no authentication of its own -- this mirrors
kb-admin-mcp-server (apps/ai-platform/librechat/docker-compose.yml), which is
likewise unauthenticated at its own layer and relies entirely on not being
reachable from outside the Docker network (no published port). The real
authorization boundary for admin operations is the facade's
requireJwtAuth + requireCapability chain, one hop up.

/admin/catalog is read-only by design: the set of (endpoint, model) pairs an
admin can pick from comes entirely from librechat.yaml, and this service
never writes to it -- there is no create/update/delete for a catalog entry
here, only for a Combo that references one.
"""

from __future__ import annotations

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route as ASGIRoute

from .catalog import Catalog
from .proxy import (
    AllTargetsFailedError,
    ComboDisabledError,
    ComboNotFoundError,
    NoEnabledTargetsError,
    Router,
)
from .store import Registry, RegistryError

_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "trailers",
        "transfer-encoding",
        "content-length",
    }
)


def _forwardable_headers(headers: httpx.Headers) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}


async def _relay(response: httpx.Response):
    """Yield exactly the bytes the provider sent, in order, then close the
    upstream connection. No buffering, no re-chunking -- see
    services/tool-filter-proxy/tool_filter_proxy/app.py for the same rule
    applied to the tool-filtering proxy."""
    try:
        async for chunk in response.aiter_raw():
            yield chunk
    finally:
        await response.aclose()


def _error(message: str, *, status_code: int, error_type: str = "invalid_request_error"):
    return JSONResponse(
        {"error": {"message": message, "type": error_type}}, status_code=status_code
    )


def create_app(
    registry: Registry,
    catalog: Catalog,
    *,
    http_client: httpx.AsyncClient | None = None,
    request_timeout_seconds: float = 30.0,
) -> Starlette:
    router = Router(registry, catalog, http_client=http_client, timeout=request_timeout_seconds)

    # -- OpenAI-compatible surface ---------------------------------------

    async def list_models(request: Request):
        """The agent ids with an enabled Combo. LibreChat's `NOC Router`
        custom endpoint has `models.fetch: true`, so LibreChat calls this on
        every session to populate the list `validateAgentModel` checks an
        agent's `model` against -- this is what lets an agent's own id be a
        valid `model` string with zero LibreChat-side changes."""
        combos = [c for c in registry.list_combos() if c.enabled]
        return JSONResponse(
            {
                "object": "list",
                "data": [
                    {"id": c.agent_id, "object": "model", "owned_by": "noc-llm-router"}
                    for c in combos
                ],
            }
        )

    async def chat_completions(request: Request):
        try:
            body = await request.json()
        except Exception:
            return _error("request body must be valid JSON", status_code=400)

        if not isinstance(body.get("model"), str):
            return _error("`model` (the agent id) is required", status_code=400)

        try:
            result = await router.dispatch(body)
        except ComboNotFoundError as exc:
            return _error(str(exc), status_code=404)
        except ComboDisabledError as exc:
            return _error(str(exc), status_code=503)
        except NoEnabledTargetsError as exc:
            return _error(str(exc), status_code=503)
        except AllTargetsFailedError as exc:
            return _error(str(exc), status_code=502, error_type="upstream_error")

        if result.stream_response is not None:
            return StreamingResponse(
                _relay(result.stream_response),
                status_code=result.status_code,
                headers=_forwardable_headers(result.headers),
            )
        return JSONResponse(
            result.body,
            status_code=result.status_code,
            headers=_forwardable_headers(result.headers),
        )

    # -- admin: catalog (read-only) ---------------------------------------

    async def get_catalog(request: Request):
        return JSONResponse({"targets": [t.masked() for t in await catalog.list_targets()]})

    # -- admin: combos ------------------------------------------------------

    async def list_combos(request: Request):
        return JSONResponse({"combos": [c.to_json() for c in registry.list_combos()]})

    async def get_combo(request: Request):
        agent_id = request.path_params["agent_id"]
        combo = registry.get_combo(agent_id)
        if combo is None:
            return _error(f"no such combo: {agent_id!r}", status_code=404)
        return JSONResponse({"combo": combo.to_json()})

    async def _validate_targets_in_catalog(targets: list[dict]) -> str | None:
        """None if every target names a real catalog entry; otherwise an
        error message naming the first one that doesn't. A Combo must only
        ever be built from what's already registered."""
        known = {(t.endpoint, t.model) for t in await catalog.list_targets()}
        for t in targets:
            pair = (t.get("endpoint"), t.get("model"))
            if pair not in known:
                return f"not a registered credential/model: {pair[0]!r} / {pair[1]!r}"
        return None

    async def upsert_combo(request: Request):
        agent_id = request.path_params["agent_id"]
        body = await request.json()
        targets = body.get("targets", [])
        error = await _validate_targets_in_catalog(targets)
        if error:
            return _error(error, status_code=400)
        existing = registry.get_combo(agent_id)
        try:
            combo = registry.upsert_combo(
                agent_id=agent_id,
                targets=targets,
                enabled=body.get("enabled", existing.enabled if existing else True),
                original_provider=body.get("original_provider"),
                original_model=body.get("original_model"),
            )
        except RegistryError as exc:
            return _error(str(exc), status_code=400)
        return JSONResponse({"combo": combo.to_json()}, status_code=200 if existing else 201)

    async def set_combo_enabled(request: Request):
        agent_id = request.path_params["agent_id"]
        body = await request.json()
        if not isinstance(body.get("enabled"), bool):
            return _error("`enabled` (boolean) is required", status_code=400)
        try:
            combo = registry.set_combo_enabled(agent_id, body["enabled"])
        except RegistryError as exc:
            return _error(str(exc), status_code=404)
        return JSONResponse({"combo": combo.to_json()})

    async def delete_combo(request: Request):
        agent_id = request.path_params["agent_id"]
        try:
            registry.delete_combo(agent_id)
        except RegistryError as exc:
            return _error(str(exc), status_code=404)
        return JSONResponse({"deleted": agent_id})

    return Starlette(
        routes=[
            ASGIRoute("/v1/models", list_models, methods=["GET"]),
            ASGIRoute("/v1/chat/completions", chat_completions, methods=["POST"]),
            ASGIRoute("/admin/catalog", get_catalog, methods=["GET"]),
            ASGIRoute("/admin/combos", list_combos, methods=["GET"]),
            ASGIRoute("/admin/combos/{agent_id}", get_combo, methods=["GET"]),
            ASGIRoute("/admin/combos/{agent_id}", upsert_combo, methods=["PUT"]),
            ASGIRoute("/admin/combos/{agent_id}/enabled", set_combo_enabled, methods=["PUT"]),
            ASGIRoute("/admin/combos/{agent_id}", delete_combo, methods=["DELETE"]),
        ]
    )
