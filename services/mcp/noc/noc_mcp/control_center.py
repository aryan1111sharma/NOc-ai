"""Curated read-only per-charger 'control-center' tools.

Thin wrappers over the NOC path-based charger proxy
(``GET /api/v1/chargers/{charger_id}/proxy/{port}/{path}``) for the highest-value on-charger
triage reads. The device APIs already return lean JSON, so responses are passed through;
composites merge a few related endpoints under fixed keys (best-effort). All calls are GET —
read-only. The long tail of ~110 endpoints stays reachable via ``get_control_center_api_catalog``
plus the generic proxy.
"""

from __future__ import annotations

import re
from typing import Any, Optional, Tuple
from urllib.parse import quote

from .freshness import is_error, live_or_history
from .http import get
from .identifiers import validate_charger_id

# Conservative allowlist for the device path: letters, digits, '-', '_', '.', '~' and the '/'
# separators, plus ':', '@' and '+' because an on-charger log source name (charger_get_logs)
# is a device-supplied identifier we cannot enumerate from this repo -- the catalog documents
# it only as "<str>" -- and service/unit-style names commonly carry those. Widening here is
# safe: the receiver percent-decodes before routing, so the only characters that can change
# how a path is parsed or forwarded are '/', '?', '#', '\', '%' and the '..' segment, and
# every one of those stays rejected below.
_PATH_ALLOWED = re.compile(r"^[A-Za-z0-9._~:@+/-]+$")

_MIN_PORT, _MAX_PORT = 1, 65535


def _validate_proxy_url(charger_id: Any, port: Any, path: Any) -> Tuple[Optional[str], Optional[str]]:
    """Build the charger-proxy URL, or explain why the inputs are unsafe.

    Returns ``(url, None)`` on success or ``(None, reason)`` on rejection. The caller turns a
    reason into the module's structured error payload rather than raising, so composites keep
    degrading to partial results (never-raise contract).

    Why this exists: the NOC charger-proxy route honours a ``?method=`` query parameter and
    forwards that verb to live charger hardware. Any model-controlled string that can inject a
    ``?`` — or climb out of its path segment with ``..`` — turns a read-only tool into a write
    to high-voltage equipment. httpx does not encode ``?`` in a relative-URL string and it
    normalises dot segments, so f-string interpolation alone is not safe.

    charger_id: validated (``identifiers.validate_charger_id``), then encoded. Encoding alone
    is NOT a defence, because the receiving server percent-decodes the path before it routes:
    a ``%2F``-laden id re-splits the route and hands the caller a charger port and on-charger
    path of their choosing. Validation is what actually closes that; see identifiers.py.

    path: validated *and* encoded, deliberately both.
      * Validation (the allowlist + the dot-segment / ``?#\\`` rejections) is the primary
        control: it is fail-closed and it fails *loudly*, so a hostile or malformed path
        surfaces as an error instead of being silently rewritten into a weird-but-harmless URL
        that the caller then has to reason about.
      * Per-segment ``quote(seg, safe="")`` is the structural backstop. ``path`` legitimately
        contains ``/`` separators, so it cannot be quoted wholesale; quoting each segment keeps
        the template's structure while guaranteeing that no segment can ever contribute URL
        syntax. Under the current allowlist this encoding is a no-op (every allowed character
        is RFC 3986 unreserved) — which is exactly the point: it costs nothing today and it
        means a future widening of the allowlist cannot silently reintroduce injection.
    """
    reason = validate_charger_id(charger_id)
    if reason is not None:
        return None, reason
    # Backstop only -- after validation this is a no-op, because every allowed character is
    # RFC 3986 unreserved. Encoding ALONE would not be a defence: the receiver decodes before
    # routing, so %2F becomes '/' again and re-splits the route (see identifiers.py).
    charger_seg = quote(charger_id, safe="")

    if isinstance(port, bool):  # bool is an int subclass; never a real port
        return None, f"ValueError: port must be an integer (got {port!r})"
    try:
        port_num = int(port)
    except (TypeError, ValueError):
        return None, f"ValueError: port must be an integer (got {port!r})"
    if not _MIN_PORT <= port_num <= _MAX_PORT:
        return None, f"ValueError: port out of range 1-65535 (got {port!r})"

    if not isinstance(path, str) or not path.strip():
        return None, "ValueError: path must be a non-empty string"
    for bad in ("?", "#", "\\"):
        if bad in path:
            return None, f"ValueError: path must not contain {bad!r}"
    if not _PATH_ALLOWED.match(path):
        return None, "ValueError: path contains characters outside [A-Za-z0-9._~/-]"
    segments = path.split("/")
    for seg in segments:
        if seg in ("", ".", ".."):
            # '' also rejects a leading/trailing slash: callers pass paths without either.
            return None, f"ValueError: path segment {seg!r} is not allowed"

    device_path = "/".join(quote(seg, safe="") for seg in segments)
    return f"/api/v1/chargers/{charger_seg}/proxy/{port_num}/{device_path}", None


async def _proxy_get(client, charger_id: str, port: int, path: str) -> dict:
    """GET one on-charger endpoint through the NOC proxy; return device JSON.

    ``charger_id``, ``port`` and ``path`` are validated and encoded here — this is the single
    choke point through which every curated tool reaches a charger (see ``_validate_proxy_url``).

    On any failure (rejected input, offline charger, non-2xx, transport error, non-JSON) returns
    a structured ``{"error", "port", "path"}`` instead of raising, so composites degrade to
    partial results. Non-dict JSON (e.g. a top-level list) is wrapped as ``{"data": <value>}``.
    """
    url, reason = _validate_proxy_url(charger_id, port, path)
    if url is None:
        return {"error": reason, "port": port, "path": path}
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {"data": data}
    except Exception as e:  # noqa: BLE001 - surface any failure as a clean error payload
        return {"error": f"{type(e).__name__}: {e}", "port": port, "path": path}


def _rejected_charger_id(charger_id: Any) -> Optional[dict]:
    """``None`` if ``charger_id`` is safe to use, else the structured error for it.

    The history-fallback endpoints below are plain backend routes (not the hardened
    charger-proxy), so they do not go through ``_proxy_get``/``_validate_proxy_url`` and
    must not splice an unvalidated ``charger_id`` into a URL themselves. Rejecting up front
    -- before ``live_or_history`` ever calls the ``history()`` closure -- guarantees a
    malicious id triggers zero outbound requests, matching ``_proxy_get``'s fail-closed
    contract instead of silently forwarding the id once the live call fails.
    """
    reason = validate_charger_id(charger_id)
    return {"error": reason} if reason is not None else None


async def _get_ocpp_status_with_fallback(client, charger_id: str) -> dict:
    """Live OCPP status + config; on failure, the backend's cached config."""
    rejected = _rejected_charger_id(charger_id)
    if rejected is not None:
        return rejected

    async def live():
        status = await _proxy_get(client, charger_id, 8003, "api/v1/ocpp/status")
        if is_error(status):
            return status
        config = await _proxy_get(client, charger_id, 8003, "api/v1/config/ocpp")
        return {"status": status, "config": config}

    async def cached():
        return await get(client, "/api/v1/chargers/{charger_id}/ocpp-config/cache",
                         charger_id=charger_id)

    return await live_or_history(live, cached,
                                 reason="live read failed; OCPP config served from NOC cache")


async def _get_alarms_with_fallback(client, charger_id: str, gun_id: Optional[int]) -> dict:
    """Live alarms; on failure, the backend's alarm history.

    ``/history/alarms`` has no gun filter server-side, so a gun-scoped request that falls back
    widens to charger-wide history -- that widening is called out in ``reason`` rather than
    left silent, since attributing another gun's fault to the requested one is a real
    misdiagnosis risk on a multi-gun charger.
    """
    rejected = _rejected_charger_id(charger_id)
    if rejected is not None:
        return rejected

    async def live():
        if gun_id is not None:
            errors = await _proxy_get(client, charger_id, 8006,
                                      f"error-generator/errors/by-gun/{gun_id}")
            return errors if is_error(errors) else {"errors_by_gun": errors}
        latched = await _proxy_get(client, charger_id, 8006, "error-generator/alarms/latched")
        if is_error(latched):
            return latched
        pending = await _proxy_get(client, charger_id, 8006, "error-generator/errors/pending")
        return {"latched": latched, "pending": pending}

    async def history():
        return await get(client, "/api/v1/chargers/{charger_id}/history/alarms",
                         charger_id=charger_id)

    reason = "live read failed; alarms served from NOC history"
    if gun_id is not None:
        reason += f" (charger-wide, not filtered to gun {gun_id})"
    return await live_or_history(live, history, reason=reason)


async def _get_session_details_with_fallback(client, charger_id: str, gun_id: Optional[int]) -> dict:
    """Live session details; on failure, the backend's session history.

    ``/history/sessions`` has no gun filter server-side, so a gun-scoped request that falls
    back widens to charger-wide history -- see ``_get_alarms_with_fallback`` for why that
    widening is surfaced in ``reason`` instead of left silent.
    """
    rejected = _rejected_charger_id(charger_id)
    if rejected is not None:
        return rejected

    async def live():
        if gun_id is not None:
            active = await _proxy_get(client, charger_id, 8003,
                                      f"api/v1/session_details/active/{gun_id}")
            if is_error(active):
                return active
            last = await _proxy_get(client, charger_id, 8003,
                                    f"api/v1/session_details/{gun_id}")
            return {"active": active, "last": last}
        history = await _proxy_get(client, charger_id, 8003, "api/v1/session_details/history")
        return history if is_error(history) else {"history": history}

    async def history():
        return await get(client, "/api/v1/chargers/{charger_id}/history/sessions",
                         charger_id=charger_id)

    reason = "live read failed; sessions served from NOC history"
    if gun_id is not None:
        reason += f" (charger-wide, not filtered to gun {gun_id})"
    return await live_or_history(live, history, reason=reason)


def _register_control_center_tools(mcp, client) -> None:
    """Register the curated Tier-1 on-charger tools onto ``mcp``."""

    @mcp.tool
    async def charger_get_charging_status(charger_id: str, gun_id: Optional[int] = None) -> dict:
        """Live charging status for a charger (by NOC charger_id): per-gun connected/PLC/OCPP
        state. Pass gun_id for a single gun. The charger must be ONLINE. Read-only."""
        path = ("api/v1/charging/status" if gun_id is None
                else f"api/v1/charging/status/{gun_id}")
        return await _proxy_get(client, charger_id, 8003, path)

    @mcp.tool
    async def charger_get_session_details(charger_id: str, gun_id: Optional[int] = None) -> dict:
        """Charging-session details for a charger. With gun_id: that gun's active + last
        session. Without: recent session history. Charger must be ONLINE. Read-only.
        If the charger is offline this falls back to NOC-stored history and marks the result
        source="history" with its age."""
        return await _get_session_details_with_fallback(client, charger_id, gun_id)

    @mcp.tool
    async def charger_get_meters(charger_id: str, gun_id: Optional[int] = None) -> dict:
        """Live meter data (AC + per-gun DC). Pass gun_id to also include that gun's IMD
        (insulation) status. Charger must be ONLINE. Read-only."""
        data = await _proxy_get(client, charger_id, 8003, "api/v1/devices/meters")
        if gun_id is not None:
            data = dict(data)
            data["imd"] = await _proxy_get(client, charger_id, 8003, f"api/v1/devices/imd/{gun_id}")
        return data

    @mcp.tool
    async def charger_get_ocpp_status(charger_id: str) -> dict:
        """OCPP connection status + the charger's OCPP configuration keys. Charger must be
        ONLINE. Read-only.
        If the charger is offline this falls back to NOC-stored history and marks the result
        source="history" with its age."""
        return await _get_ocpp_status_with_fallback(client, charger_id)

    @mcp.tool
    async def charger_get_network_status(charger_id: str) -> dict:
        """Connectivity triage: overall network status + GSM signal/operator detail. Charger
        must be ONLINE. Read-only."""
        return {
            "overall": await _proxy_get(client, charger_id, 8000, "api/v1/network/status"),
            "gsm": await _proxy_get(client, charger_id, 8000, "api/v1/network/gsm/status"),
        }

    @mcp.tool
    async def charger_get_system_info(charger_id: str) -> dict:
        """Device identity: hardware, software/firmware version, and RTC clock. Charger must be
        ONLINE. Read-only."""
        return {
            "hardware": await _proxy_get(client, charger_id, 8000, "api/v1/system/hardware"),
            "software": await _proxy_get(client, charger_id, 8000, "api/v1/system/software"),
            "rtc": await _proxy_get(client, charger_id, 8000, "api/v1/system/rtc"),
        }

    @mcp.tool
    async def charger_get_allocation_state(charger_id: str) -> dict:
        """Current power-allocation state (per-gun allocation). Charger must be ONLINE.
        Read-only."""
        return await _proxy_get(client, charger_id, 8002, "api/state")

    @mcp.tool
    async def charger_get_pm_power(charger_id: str) -> dict:
        """Power-module output: module count + per-module power. Charger must be ONLINE.
        Read-only."""
        return {
            "pm_count": await _proxy_get(client, charger_id, 8002, "api/pm_count"),
            "power": await _proxy_get(client, charger_id, 8002, "api/pm/get_all_power"),
        }

    @mcp.tool
    async def charger_get_alarms(charger_id: str, gun_id: Optional[int] = None) -> dict:
        """Active faults. With gun_id: errors for that gun. Without: latched alarms + pending
        errors across the charger. Charger must be ONLINE. Read-only.
        If the charger is offline this falls back to NOC-stored history and marks the result
        source="history" with its age."""
        return await _get_alarms_with_fallback(client, charger_id, gun_id)

    @mcp.tool
    async def charger_get_temperature(charger_id: str) -> dict:
        """Thermal state: sensor readings + configured thresholds. Charger must be ONLINE.
        Read-only."""
        return {
            "sensors": await _proxy_get(client, charger_id, 8006, "temp/sensors"),
            "thresholds": await _proxy_get(client, charger_id, 8006, "temp/thresholds"),
        }

    @mcp.tool
    async def charger_list_log_sources(charger_id: str) -> dict:
        """List the on-charger log sources available to fetch (use with charger_get_logs).
        Charger must be ONLINE. Requires an admin-level NOC service account. Read-only."""
        return await _proxy_get(client, charger_id, 8005, "api/v1/devtools/logs/sources")

    @mcp.tool
    async def charger_get_logs(charger_id: str, source: str) -> dict:
        """Fetch on-charger logs for one source (from charger_list_log_sources). Charger must be
        ONLINE. Requires an admin-level NOC service account. Read-only."""
        return await _proxy_get(client, charger_id, 8005, f"api/v1/devtools/logs/{source}")

    @mcp.tool
    async def charger_get_device_metrics(charger_id: str) -> dict:
        """On-charger compute health: CPU / memory / disk metrics. Charger must be ONLINE.
        Requires an admin-level NOC service account. Read-only."""
        return await _proxy_get(client, charger_id, 8005, "api/v1/devtools/metrics")
