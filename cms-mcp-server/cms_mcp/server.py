"""Curated read-only MCP tools over the legacy CMS (old NOC at quenchcms.com).

The legacy app has no OpenAPI spec and returns one ~206-field "god" ViewModel for every
action, so each tool calls the relevant MVC JSON action and TRIMS the response to a small
whitelist of meaningful fields (dropping nulls/empties) to keep payloads LLM-friendly.
"""

from __future__ import annotations

from typing import Any, Optional

from fastmcp import FastMCP

from .auth import CmsAuthClient
from .config import settings

# ── Field whitelists (subset of the 206-field god DTO) ───────────────────────────────────
_ALERT_FIELDS = [
    "ChargerID", "QChargerID", "CustomerID", "Name", "ErrorID", "ErrorCode", "ErrorCode_A",
    "ErrorCode_B", "ErrorDesc", "Gun_A", "Gun_B", "strEvent_Timestamp", "strStartTime",
    "Duration", "StatusID",
]
_NOC_SUMMARY_FIELDS = [
    "Name", "CustomerID", "TotalChargers", "TotalOnlineChargers", "TotalOfflineChargers",
    "TotalFaultyChargers",
]
_CUSTOMER_FIELDS = [
    "CustomerID", "Name", "EmailID", "ContactDetails", "Address", "CountryID", "CountryName",
    "RegionID",
]


def _trim(rows: Any, fields: list[str]) -> list[dict]:
    """Keep only whitelisted, non-empty fields from each row."""
    if not isinstance(rows, list):
        rows = [rows] if isinstance(rows, dict) else []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        out.append({k: r[k] for k in fields if r.get(k) not in (None, "", 0, "0")})
    return out


def build_client() -> CmsAuthClient:
    return CmsAuthClient(
        base_url=settings.cms_base_url.rstrip("/"),
        user=settings.cms_user,
        password=settings.cms_pass,
        timeout=settings.request_timeout,
    )


def build_server() -> FastMCP:
    mcp = FastMCP(name="Legacy CMS (old NOC, read-only)")
    client = build_client()

    async def _get_json(path: str, params: Optional[dict] = None) -> Any:
        resp = await client.get(path, params=params or {})
        resp.raise_for_status()
        if not resp.text.strip():
            return []
        return resp.json()

    # ── Chargers ─────────────────────────────────────────────────────────────────────────
    @mcp.tool
    async def cms_list_chargers(search: Optional[str] = None, limit: int = 500) -> list[dict]:
        """List chargers that live in the OLD CMS (quenchcms.com): their charger id
        (QChargerID) and owning customer. ~1072 chargers. Optionally filter by a search
        string (matched against QChargerID or customer name). Note: this list does not carry
        live status — use `cms_get_noc_summary` for fleet online/offline/faulty totals."""
        rows = await _get_json("/ChargerDetails/GetChargerDetailsForMap")
        out = [{"QChargerID": r.get("QChargerID"), "Customer": r.get("Name")}
               for r in rows if r.get("QChargerID")]
        if search:
            s = search.lower()
            out = [r for r in out
                   if s in str(r["QChargerID"]).lower() or s in str(r.get("Customer", "")).lower()]
        return out[:limit]

    @mcp.tool
    async def cms_get_charger_types() -> list[dict]:
        """Charger types/models defined in the old CMS."""
        fields = ["ChargerTypeID", "Name", "Description", "CapacityKW", "Is30Kw"]
        return _trim(await _get_json("/ChargerDetails/GetChargerTypes"), fields)

    @mcp.tool
    async def cms_get_noc_summary() -> list[dict]:
        """Fleet summary per customer from the old CMS: total / online / offline / faulty
        charger counts (from the NOC dashboard chart data)."""
        return _trim(await _get_json("/ChargerDetails/GetNocDetailsForchart"), _NOC_SUMMARY_FIELDS)

    # ── Alerts / faults ──────────────────────────────────────────────────────────────────
    @mcp.tool
    async def cms_get_recent_alerts(limit: int = 50) -> list[dict]:
        """Most recent charger alerts/faults from the old CMS (error codes + timestamps)."""
        return _trim(await _get_json("/ChargerDetails/GetAlertsForBellNotification"), _ALERT_FIELDS)[:limit]

    # NOTE: per-charger session/error tools (GetSessionDetailByChargerID,
    # GetErrorCountByChargerID) are deferred — they require a numeric ChargerID and an
    # undocumented param contract the legacy actions expect; the map action exposes only
    # QChargerID. Revisit by capturing the exact AJAX payload from the CMS frontend.

    # ── Customers / reference ────────────────────────────────────────────────────────────
    @mcp.tool
    async def cms_list_customers() -> list[dict]:
        """Customers in the old CMS (id, name, contact, country)."""
        return _trim(await _get_json("/Customer/GetCustomersForDD"), _CUSTOMER_FIELDS)

    @mcp.tool
    async def cms_list_service_engineers() -> list[dict]:
        """Service engineers registered in the old CMS (id, name, state)."""
        return await _get_json("/Customer/GetServiceEngineerList")

    return mcp
