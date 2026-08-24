"""Alert lifecycle tools — acknowledge / resolve.

Both backend operations take a required-but-empty JSON body (per the OpenAPI spec's
AlertAcknowledge/AlertResolve schemas — no properties defined), so the tools here take no
extra arguments beyond the alert id.
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


def _register_alert_tools(mcp, client) -> None:
    @mcp.tool
    async def alert_acknowledge(alert_id: int) -> dict:
        """Acknowledge a monitoring alert — signals someone is looking at it, but does not
        resolve or suppress it. Does not change the underlying charger/fleet state."""
        return await _call(client, "POST", f"/api/v1/monitoring/alerts/{alert_id}/acknowledge", json_body={})

    @mcp.tool
    async def alert_resolve(alert_id: int) -> dict:
        """Mark a monitoring alert resolved. Only call this once the underlying issue is
        actually fixed — resolving an alert that's still live hides a real problem from
        anyone else watching the alert list."""
        return await _call(client, "POST", f"/api/v1/monitoring/alerts/{alert_id}/resolve", json_body={})
