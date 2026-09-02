"""Per-charger history and telemetry composites.

These read the NOC backend's TimescaleDB-backed endpoints rather than the device, so unlike the
live ``charger_*`` tools they answer *after* a charger drops offline. That is the point: the
moment a charger goes dark is exactly when a NOC engineer needs its recent history.

``noc_charger_ocpp_config`` is the one tool here that prefers the live device, falling back to
the backend's cached copy and labelling the result.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastmcp import FastMCP

from .control_center import _proxy_get
from .freshness import HISTORY, extract_as_of, is_error, live_or_history, stamp
from .reports import _get


def _p(**kwargs) -> dict:
    return {k: v for k, v in kwargs.items() if v is not None}


def _oldest(*payloads) -> Optional[datetime]:
    """Oldest as_of across merged sub-payloads.

    A composite is only as fresh as its stalest part; reporting the freshest would let an
    older sub-payload hide behind a newer one's timestamp.
    """
    stamps = [ts for ts in (extract_as_of(p) for p in payloads) if ts is not None]
    return min(stamps) if stamps else None


async def _fetch_charger_history(client, charger_id: str, *, limit: Optional[int]) -> dict:
    base = f"/api/v1/chargers/{charger_id}"
    params = _p(limit=limit)
    sessions = await _get(client, f"{base}/history/sessions", params)
    alarms = await _get(client, f"{base}/history/alarms", params)
    merged = {"sessions": sessions, "alarms": alarms}
    return stamp(merged, source=HISTORY, as_of=_oldest(sessions, alarms),
                 reason="NOC backend history (independent of charger state)")


async def _fetch_charger_telemetry(client, charger_id: str, *, resolution: Optional[str],
                                   hours: Optional[int], from_ts: Optional[str],
                                   to_ts: Optional[str], limit: Optional[int]) -> dict:
    base = f"/api/v1/chargers/{charger_id}/telemetry"
    latest = await _get(client, f"{base}/latest", {})
    aggregated = await _get(client, f"{base}/aggregated", _p(resolution=resolution, hours=hours))
    history = await _get(client, f"{base}/history", _p(limit=limit, from_ts=from_ts, to_ts=to_ts))
    merged = {"latest": latest, "aggregated": aggregated, "history": history}
    return stamp(merged, source=HISTORY, as_of=_oldest(latest, aggregated, history),
                 reason="NOC backend telemetry (independent of charger state)")


async def _fetch_session_events(client, charger_id: str, session_id: str) -> dict:
    out = await _get(client, f"/api/v1/chargers/{charger_id}/sessions/{session_id}/events", {})
    return out if is_error(out) else stamp(out, source=HISTORY, as_of=extract_as_of(out))


async def _fetch_charger_stability(client, charger_id: str) -> dict:
    base = f"/api/v1/chargers/{charger_id}"
    stability = await _get(client, f"{base}/stability", {})
    commands = await _get(client, f"{base}/commands", {})
    ssh = await _get(client, f"{base}/ssh-status", {})
    merged = {"stability": stability, "commands": commands, "ssh": ssh}
    return stamp(merged, source=HISTORY, as_of=extract_as_of(stability),
                 reason="NOC backend connection history")


async def _fetch_cached_ocpp_config(client, charger_id: str) -> dict:
    """GET the backend's cached OCPP config, distinguishing a real failure from "never cached".

    A 404 body carrying ``code=NO_CACHE`` means the backend has simply never cached this
    charger's config -- that must be reported distinctly from a transport/server failure, so the
    body is inspected before ``raise_for_status`` would otherwise collapse it into a generic
    HTTP-error string.
    """
    url = f"/api/v1/chargers/{charger_id}/ocpp-config/cache"
    try:
        resp = await client.get(url)
        if resp.status_code == 404:
            body = resp.json()
            if isinstance(body, dict) and body.get("code") == "NO_CACHE":
                return {"error": "NO_CACHE"}
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {"data": data}
    except Exception as e:  # noqa: BLE001 - surface any failure as a clean error payload
        return {"error": f"{type(e).__name__}: {e}", "url": url}


async def _fetch_charger_ocpp_config(client, charger_id: str) -> dict:
    """Live OCPP config, falling back to the backend's cached copy when the charger is offline.

    A 404 with code=NO_CACHE means the backend has never cached this charger's config. That is a
    normal empty result, not a failure, so it is reported as such rather than as an error.
    """
    async def live():
        return await _proxy_get(client, charger_id, 8003, "api/v1/config/ocpp")

    async def cached():
        return await _fetch_cached_ocpp_config(client, charger_id)

    out = await live_or_history(live, cached, reason="charger offline; served from NOC cache")
    if is_error(out) and out.get("history_error") == "NO_CACHE":
        return stamp({}, source=HISTORY,
                     reason="charger offline and no cached OCPP config exists for it yet")
    return out


def _register_history_tools(mcp: FastMCP, client) -> None:
    """Register the per-charger history composites onto ``mcp``."""

    @mcp.tool
    async def noc_charger_history(charger_id: str, limit: Optional[int] = None) -> dict:
        """Recent charging sessions and alarm history for a charger, from the NOC database.

        Unlike the live charger_get_* tools this works when the charger is OFFLINE, which is when
        you usually need it. Use this to reconstruct what happened before a charger went dark."""
        return await _fetch_charger_history(client, charger_id, limit=limit)

    @mcp.tool
    async def noc_charger_telemetry(charger_id: str, resolution: Optional[str] = None,
                                    hours: Optional[int] = None, from_ts: Optional[str] = None,
                                    to_ts: Optional[str] = None,
                                    limit: Optional[int] = None) -> dict:
        """Telemetry for a charger: latest snapshot, time-bucketed aggregates, and raw history.

        Reads TimescaleDB, so it works when the charger is OFFLINE. Use `resolution` and `hours`
        for trends; `from_ts`/`to_ts` (ISO-8601) to bound the raw history."""
        return await _fetch_charger_telemetry(client, charger_id, resolution=resolution,
                                              hours=hours, from_ts=from_ts, to_ts=to_ts,
                                              limit=limit)

    @mcp.tool
    async def noc_session_events(charger_id: str, session_id: str) -> dict:
        """Lifecycle events for one charging session: SESSION_START, START_CHARGING,
        STOP_CHARGING, SESSION_STOP.

        Use this to explain why a session failed. Works when the charger is OFFLINE."""
        return await _fetch_session_events(client, charger_id, session_id)

    @mcp.tool
    async def noc_charger_stability(charger_id: str) -> dict:
        """Connection stability for a charger: reconnect frequency, recent command history, and
        SSH connection status.

        Start here for "this charger keeps dropping" complaints. Works when the charger is
        OFFLINE."""
        return await _fetch_charger_stability(client, charger_id)

    @mcp.tool
    async def noc_charger_ocpp_config(charger_id: str) -> dict:
        """The charger's OCPP configuration keys, read live from the device where possible.

        If the charger is offline this falls back to the NOC backend's cached copy and marks the
        result source="history" with its age -- so a stale value is never mistaken for current."""
        return await _fetch_charger_ocpp_config(client, charger_id)
