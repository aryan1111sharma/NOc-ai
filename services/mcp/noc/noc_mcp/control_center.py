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

# Conservative allowlist for the device path. Every path this module builds is a fixed
# template plus substituted values (gun_id, log source), and all of them are drawn from
# this set: letters, digits, '-', '_', '.', '~' and the '/' separators.
_PATH_ALLOWED = re.compile(r"^[A-Za-z0-9._~/-]+$")

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
    if not isinstance(charger_id, str) or not charger_id.strip():
        return None, "ValueError: charger_id must be a non-empty string"
    # safe="" so '/', '?', '&' and '#' are percent-encoded and cannot escape this segment.
    charger_seg = quote(charger_id, safe="")
    if charger_seg in (".", ".."):
        # '.' and '..' are RFC 3986 *unreserved*, so quote() passes them through untouched and
        # httpx then normalises the dot segment away -- the one input that still escapes the
        # segment after encoding. Any other dotted id ("a/..") keeps its '/' encoded and stays
        # one opaque segment, so only the bare dot segments need rejecting.
        return None, f"ValueError: charger_id must not be a path segment ({charger_id!r})"

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
        session. Without: recent session history. Charger must be ONLINE. Read-only."""
        if gun_id is not None:
            return {
                "active": await _proxy_get(client, charger_id, 8003, f"api/v1/session_details/active/{gun_id}"),
                "last": await _proxy_get(client, charger_id, 8003, f"api/v1/session_details/{gun_id}"),
            }
        return {"history": await _proxy_get(client, charger_id, 8003, "api/v1/session_details/history")}

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
        ONLINE. Read-only."""
        return {
            "status": await _proxy_get(client, charger_id, 8003, "api/v1/ocpp/status"),
            "config": await _proxy_get(client, charger_id, 8003, "api/v1/config/ocpp"),
        }

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
        errors across the charger. Charger must be ONLINE. Read-only."""
        if gun_id is not None:
            return {"errors_by_gun": await _proxy_get(
                client, charger_id, 8006, f"error-generator/errors/by-gun/{gun_id}")}
        return {
            "latched": await _proxy_get(client, charger_id, 8006, "error-generator/alarms/latched"),
            "pending": await _proxy_get(client, charger_id, 8006, "error-generator/errors/pending"),
        }

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
