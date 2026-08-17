"""Curated read-only per-charger 'control-center' tools.

Thin wrappers over the NOC path-based charger proxy
(``GET /api/v1/chargers/{charger_id}/proxy/{port}/{path}``) for the highest-value on-charger
triage reads. The device APIs already return lean JSON, so responses are passed through;
composites merge a few related endpoints under fixed keys (best-effort). All calls are GET —
read-only. The long tail of ~110 endpoints stays reachable via ``get_control_center_api_catalog``
plus the generic proxy.
"""

from __future__ import annotations

from typing import Optional


async def _proxy_get(client, charger_id: str, port: int, path: str) -> dict:
    """GET one on-charger endpoint through the NOC proxy; return device JSON.

    On any failure (offline charger, non-2xx, transport error, non-JSON) returns a structured
    ``{"error", "port", "path"}`` instead of raising, so composites degrade to partial results.
    Non-dict JSON (e.g. a top-level list) is wrapped as ``{"data": <value>}``.
    """
    url = f"/api/v1/chargers/{charger_id}/proxy/{port}/{path}"
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
