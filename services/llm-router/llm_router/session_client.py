"""Best-effort telemetry emission to the NOC Session Intelligence service
(see services/session/). This module owns the ONE hard guarantee the whole
feature depends on: nothing in here may ever raise into, block, or slow down
an actual AI request beyond the small, fixed
Settings.session_service_timeout_seconds budget.

Every public method:
  - is a no-op when `settings.session_service_url` is unset (the default),
    so an existing deployment is entirely unaffected until an operator
    opts in;
  - catches and swallows every exception (network error, timeout, non-2xx
    response) rather than propagating it;
  - never awaits inline in the request path -- app.py fires these via
    `asyncio.create_task`, so even the timeout budget above is paid by a
    background task, not the response the caller is waiting on.

This module never reads or forwards a provider API key: the payload it
builds is limited to the agent id, target/model name, fallback chain,
token/latency numbers, and the tool-filter decision header -- exactly the
fields session/store.py:TurnEvent models.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from .config import Settings
from .config import settings as default_settings

logger = logging.getLogger(__name__)


def extract_tool_filter_header(raw: str | None) -> dict | None:
    """Parses the X-Tool-Filter-Decision request header tool-filter-proxy
    attaches (see tool_filter_proxy/app.py:Proxy.handle). Malformed or
    missing input yields None -- this is enrichment, never required."""
    if not raw:
        return None
    import json

    try:
        decision = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return decision if isinstance(decision, dict) else None


class SessionClient:
    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        settings: Settings | None = None,
    ):
        self._settings = settings if settings is not None else default_settings
        self._http_client = http_client

    @property
    def enabled(self) -> bool:
        return bool(self._settings.session_service_url)

    async def _post(self, payload: dict) -> None:
        if not self.enabled:
            return
        client = self._http_client
        owns_client = client is None
        if owns_client:
            client = httpx.AsyncClient()
        try:
            await client.post(
                f"{self._settings.session_service_url}/ingest/turn",
                json=payload,
                timeout=self._settings.session_service_timeout_seconds,
            )
        except Exception:
            logger.debug("session telemetry emit failed (non-fatal)", exc_info=True)
        finally:
            if owns_client:
                await client.aclose()

    def emit_success(
        self,
        *,
        agent_id: str,
        target: str,
        attempted: list[str],
        status_code: int,
        tokens: dict | None,
        latency_ms: float,
        tool_filter: dict | None,
    ) -> None:
        if not self.enabled:
            return
        payload = {
            "agent_id": agent_id,
            "target": target,
            "attempted": attempted,
            "status_code": status_code,
            "ok": True,
            "tokens": tokens,
            "latency_ms": latency_ms,
            "tool_filter": tool_filter,
            "error": None,
        }
        asyncio.create_task(self._post(payload))

    def emit_failure(
        self,
        *,
        agent_id: str,
        attempted: list[str],
        status_code: int | None,
        latency_ms: float,
        tool_filter: dict | None,
        error: str,
    ) -> None:
        if not self.enabled:
            return
        payload = {
            "agent_id": agent_id,
            "target": None,
            "attempted": attempted,
            "status_code": status_code,
            "ok": False,
            "tokens": None,
            "latency_ms": latency_ms,
            "tool_filter": tool_filter,
            "error": error,
        }
        asyncio.create_task(self._post(payload))
