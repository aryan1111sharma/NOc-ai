"""ASGI app: the ingest surface (/ingest/turn) that llm-router's
session_client fire-and-forgets telemetry to, plus the admin surface
(/admin/sessions*) that the LibreChat custom-image facade
(apps/ai-platform/librechat/custom-image/files/api/sessions.js) proxies to.

/admin/* carries no authentication of its own -- this mirrors llm-router's
own /admin/* (see llm_router/app.py's module docstring): the real
authorization boundary is the facade's requireJwtAuth + requireCapability
chain, one hop up, and this service is never published outside the Docker
network.

/ingest/turn is intentionally permissive: malformed input returns 400 (so a
buggy caller notices in its own logs), but nothing in this module can ever
raise into, block, or slow down an actual AI request -- the caller
(llm_router.session_client) always emits fire-and-forget with a short
timeout and swallows every error on its side. See that module's docstring
for the other half of that guarantee.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route as ASGIRoute

from . import aggregate
from .config import Settings
from .config import settings as default_settings
from .store import EventStore, TurnEvent


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _to_json(instance: Any) -> Any:
    return _json_safe(asdict(instance))


def _error(message: str, *, status_code: int):
    return JSONResponse({"error": {"message": message}}, status_code=status_code)


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def create_app(
    event_store: EventStore,
    db: Any,
    *,
    settings: Settings | None = None,
) -> Starlette:
    settings = settings if settings is not None else default_settings

    # -- ingest (fed by llm_router.session_client) -------------------------

    async def ingest_turn(request: Request):
        try:
            body = await request.json()
        except Exception:
            return _error("request body must be valid JSON", status_code=400)

        if not isinstance(body, dict) or not isinstance(body.get("agent_id"), str):
            return _error("`agent_id` (string) is required", status_code=400)
        if not isinstance(body.get("ok"), bool):
            return _error("`ok` (boolean) is required", status_code=400)

        event = TurnEvent(
            agent_id=body["agent_id"],
            target=body.get("target"),
            attempted=tuple(body.get("attempted", [])),
            status_code=body.get("status_code"),
            ok=body["ok"],
            tokens=body.get("tokens"),
            latency_ms=body.get("latency_ms"),
            tool_filter=body.get("tool_filter"),
            error=body.get("error"),
        )
        event_store.record(event)
        return JSONResponse({"recorded": True}, status_code=202)

    # -- admin: sessions -----------------------------------------------------

    async def list_sessions(request: Request):
        import asyncio

        params = request.query_params
        try:
            limit = min(int(params.get("limit", settings.default_list_limit)), settings.max_list_limit)
            skip = max(int(params.get("skip", 0)), 0)
        except ValueError:
            return _error("`limit`/`skip` must be integers", status_code=400)

        entries = await asyncio.to_thread(
            aggregate.build_session_list,
            db,
            event_store,
            agent_id=params.get("agent"),
            date_from=_parse_date(params.get("date_from")),
            date_to=_parse_date(params.get("date_to")),
            limit=limit,
            skip=skip,
            correlation_window_seconds=settings.correlation_window_seconds,
        )

        model = params.get("model")
        if model:
            entries = [e for e in entries if model in e.models]

        return JSONResponse({"sessions": [_to_json(e) for e in entries]})

    async def get_session_detail(request: Request):
        import asyncio

        conversation_id = request.path_params["conversation_id"]
        view = await asyncio.to_thread(
            aggregate.build_session_detail,
            db,
            event_store,
            conversation_id,
            correlation_window_seconds=settings.correlation_window_seconds,
        )
        if view is None:
            return _error(f"no such session: {conversation_id!r}", status_code=404)
        return JSONResponse({"session": _to_json(view)})

    async def healthz(request: Request):
        return JSONResponse({"status": "ok"})

    return Starlette(
        routes=[
            ASGIRoute("/ingest/turn", ingest_turn, methods=["POST"]),
            ASGIRoute("/admin/sessions", list_sessions, methods=["GET"]),
            ASGIRoute("/admin/sessions/{conversation_id}", get_session_detail, methods=["GET"]),
            ASGIRoute("/healthz", healthz, methods=["GET"]),
        ]
    )
