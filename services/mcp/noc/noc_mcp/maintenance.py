"""Maintenance-window tools — schedule a window that suppresses alerts/notifications.

Deliberately limited to *creating* a window (the approved first-wave capability). Editing
or deleting an existing window, and any other monitoring/alert-rule configuration, is not
implemented — see NOC_MCP_REMAINING_TOOLS.md.
"""

from __future__ import annotations

import httpx


async def _call(client, method: str, path: str, *, json_body=None) -> dict:
    try:
        resp = await client.request(method, path, json=json_body)
    except httpx.HTTPError as e:
        return {"error": f"{type(e).__name__}: {e}"}
    if resp.status_code >= 400:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        return {"error": f"NOC backend returned {resp.status_code}", "detail": detail}
    try:
        return resp.json()
    except Exception:
        return {"status_code": resp.status_code, "note": "non-JSON response body"}


def _register_maintenance_tools(mcp, client) -> None:
    @mcp.tool
    async def maintenance_window_create(
        name: str,
        start_at: str,
        end_at: str,
        suppress_alerts: bool = True,
        suppress_notifications: bool = True,
    ) -> dict:
        """Create a maintenance window. name (1-200 chars), start_at/end_at are timestamps
        (pass ISO-8601). By default this SUPPRESSES both alerts and notifications for the
        window's duration — confirm the intended scope and time range with the user before
        calling, since an overly broad window can hide real problems fleet-wide."""
        body = {
            "name": name,
            "start_at": start_at,
            "end_at": end_at,
            "suppress_alerts": suppress_alerts,
            "suppress_notifications": suppress_notifications,
        }
        return await _call(client, "POST", "/api/v1/monitoring/maintenance-windows", json_body=body)
