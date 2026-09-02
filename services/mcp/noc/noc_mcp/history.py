"""Per-charger history and telemetry composites.

These read the NOC backend's TimescaleDB-backed endpoints rather than the device, so unlike the
live ``charger_*`` tools they answer *after* a charger drops offline. That is the point: the
moment a charger goes dark is exactly when a NOC engineer needs its recent history.

``noc_charger_ocpp_config`` is the one tool here that prefers the live device, falling back to
the backend's cached copy and labelling the result.

Identifier safety
-----------------
Every fetcher here splices a caller-supplied ``charger_id`` (and ``_fetch_session_events`` a
``session_id``) into a NOC backend path. These are plain backend routes, not the hardened
charger proxy, so they do NOT go through ``_validate_proxy_url``. Two controls cover them, and
both are required:

* each fetcher rejects an illegal identifier up front via ``_rejected_identifiers`` -- before
  any sub-call and, for ``_fetch_charger_ocpp_config``, before ``live_or_history`` can run --
  so a hostile id costs zero outbound requests, matching ``_proxy_get``'s fail-closed contract
  (see ``control_center._rejected_charger_id`` for the same reasoning); and
* every request goes out through ``noc_mcp.http.get``, which re-validates the identifiers it
  is given, re-validates the assembled URL, and always sends ``params``.

The second control is what makes the failure structural rather than a habit: a fetcher that
forgot the first check still could not emit an injected URL.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastmcp import FastMCP

from .control_center import _proxy_get
from .freshness import HISTORY, extract_as_of, is_error, live_or_history, stamp
from .http import get
from .identifiers import validate_charger_id, validate_session_id

_CHARGER = "/api/v1/chargers/{charger_id}"
_SESSION_EVENTS = "/api/v1/chargers/{charger_id}/sessions/{session_id}/events"
_OCPP_CACHE = "/api/v1/chargers/{charger_id}/ocpp-config/cache"

# Distinguishes "no session_id to check" from a caller who really passed ``None``, which must
# be rejected rather than skipped.
_UNSET = object()


def _p(**kwargs) -> dict:
    return {k: v for k, v in kwargs.items() if v is not None}


def _rejected_identifiers(charger_id: Any, session_id: Any = _UNSET) -> Optional[dict]:
    """``None`` if the identifiers are safe to use, else the structured error for the first bad one.

    Fail-closed and evaluated *before* any outbound request, exactly like
    ``control_center._rejected_charger_id``: a rejected id must cost zero requests, not one
    request that happens to fail. Placement matters as much as the check -- in
    ``_fetch_charger_ocpp_config`` the live leg's own rejection is an ``is_error``, which
    ``live_or_history`` reads as "device unreachable" and answers by calling the *cached* leg.
    Validating only inside the closures would therefore let the fallback fire the hostile URL.

    Reuses ``identifiers``' validators rather than restating their rules; there is exactly one
    definition of a legal charger id and one of a legal session id in this service.
    """
    reason = validate_charger_id(charger_id)
    if reason is None and session_id is not _UNSET:
        reason = validate_session_id(session_id)
    return {"error": reason} if reason is not None else None


def _oldest(*payloads) -> Optional[datetime]:
    """Oldest as_of across merged sub-payloads.

    A composite is only as fresh as its stalest part; reporting the freshest would let an
    older sub-payload hide behind a newer one's timestamp.
    """
    stamps = [ts for ts in (extract_as_of(p) for p in payloads) if ts is not None]
    return min(stamps) if stamps else None


async def _fetch_charger_history(client, charger_id: str, *, limit: Optional[int]) -> dict:
    rejected = _rejected_identifiers(charger_id)
    if rejected is not None:
        return rejected
    params = _p(limit=limit)
    sessions = await get(client, f"{_CHARGER}/history/sessions", params, charger_id=charger_id)
    alarms = await get(client, f"{_CHARGER}/history/alarms", params, charger_id=charger_id)
    merged = {"sessions": sessions, "alarms": alarms}
    return stamp(merged, source=HISTORY, as_of=_oldest(sessions, alarms),
                 reason="NOC backend history (independent of charger state)")


async def _fetch_charger_telemetry(client, charger_id: str, *, resolution: Optional[str],
                                   hours: Optional[int], from_ts: Optional[str],
                                   to_ts: Optional[str], limit: Optional[int]) -> dict:
    rejected = _rejected_identifiers(charger_id)
    if rejected is not None:
        return rejected
    base = f"{_CHARGER}/telemetry"
    latest = await get(client, f"{base}/latest", {}, charger_id=charger_id)
    aggregated = await get(client, f"{base}/aggregated",
                           _p(resolution=resolution, hours=hours), charger_id=charger_id)
    history = await get(client, f"{base}/history",
                        _p(limit=limit, from_ts=from_ts, to_ts=to_ts), charger_id=charger_id)
    merged = {"latest": latest, "aggregated": aggregated, "history": history}
    return stamp(merged, source=HISTORY, as_of=_oldest(latest, aggregated, history),
                 reason="NOC backend telemetry (independent of charger state)")


async def _fetch_session_events(client, charger_id: str, session_id: str) -> dict:
    rejected = _rejected_identifiers(charger_id, session_id)
    if rejected is not None:
        return rejected
    out = await get(client, _SESSION_EVENTS, {},
                    charger_id=charger_id, session_id=session_id)
    return out if is_error(out) else stamp(out, source=HISTORY, as_of=extract_as_of(out))


async def _fetch_charger_stability(client, charger_id: str) -> dict:
    rejected = _rejected_identifiers(charger_id)
    if rejected is not None:
        return rejected
    stability = await get(client, f"{_CHARGER}/stability", {}, charger_id=charger_id)
    commands = await get(client, f"{_CHARGER}/commands", {}, charger_id=charger_id)
    ssh = await get(client, f"{_CHARGER}/ssh-status", {}, charger_id=charger_id)
    merged = {"stability": stability, "commands": commands, "ssh": ssh}
    return stamp(merged, source=HISTORY, as_of=extract_as_of(stability),
                 reason="NOC backend connection history")


async def _fetch_cached_ocpp_config(client, charger_id: str) -> dict:
    """GET the backend's cached OCPP config, distinguishing a real failure from "never cached".

    A 404 body carrying ``code=NO_CACHE`` means the backend has simply never cached this
    charger's config -- that must be reported distinctly from a transport/server failure, so the
    response body is inspected rather than collapsed into a generic HTTP-error string.
    ``noc_mcp.http.get`` surfaces ``status`` and the decoded ``body`` on a non-2xx precisely so
    this stays possible without a hand-rolled ``client.get``. The hand-rolled call this replaced
    passed no ``params=``, which is what let a query string smuggled inside ``charger_id``
    survive as a real ``?method=`` verb override against live charger hardware.
    """
    out = await get(client, _OCPP_CACHE, {}, charger_id=charger_id)
    if is_error(out) and out.get("status") == 404:
        body = out.get("body")
        if isinstance(body, dict) and body.get("code") == "NO_CACHE":
            return {"error": "NO_CACHE"}
    return out


async def _fetch_charger_ocpp_config(client, charger_id: str) -> dict:
    """Live OCPP config, falling back to the backend's cached copy when the charger is offline.

    A 404 with code=NO_CACHE means the backend has never cached this charger's config. That is a
    normal empty result, not a failure, so it is reported as such rather than as an error.

    The id is rejected here, before ``live_or_history`` -- see ``_rejected_identifiers`` for why
    checking inside the closures would be worse than useless.
    """
    rejected = _rejected_identifiers(charger_id)
    if rejected is not None:
        return rejected

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
