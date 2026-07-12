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
