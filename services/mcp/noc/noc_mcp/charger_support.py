"""Charger-support tools — generate a signed QR token for a charger.

Read-adjacent in effect (mints a token, doesn't change charger state or configuration),
but it's a write from the backend's point of view (POST, creates a new signed token), so it
lives outside control_center.py's pure-read composites.
"""

from __future__ import annotations

from typing import Optional

import httpx


async def _call(client, method: str, path: str, *, params=None) -> dict:
    try:
        resp = await client.request(method, path, params=params)
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


def _register_charger_support_tools(mcp, client) -> None:
    @mcp.tool
    async def charger_generate_qr_token(
        charger_id: str,
        session_id: Optional[str] = None,
        gun_id: Optional[int] = None,
        ttl_hours: Optional[int] = None,
    ) -> dict:
        """Generate a signed QR token for a charger (used for the customer-facing QR-scan
        flow, e.g. so a customer can raise a ticket for that specific charger). Optionally
        bind it to a session_id/gun_id, and set ttl_hours to override the default expiry."""
        params = {k: v for k, v in {
            "session_id": session_id, "gun_id": gun_id, "ttl_hours": ttl_hours,
        }.items() if v is not None}
        return await _call(
            client, "POST", f"/api/v1/admin/chargers/{charger_id}/qr-token", params=params
        )
