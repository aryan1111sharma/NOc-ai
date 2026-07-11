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
_CHG_DETAIL_FIELDS = [
    "QChargerID", "ChargerTypeID", "Description", "CapacityKW", "SerialNumber",
    "ControllerADescription", "ControllerBDescription", "Address", "StateName",
    "CountryName", "Name", "IsActive",
]
_CHG_STATUS_FIELDS = [
    "QChargerID", "OCPPID", "Latitude", "Longitude", "Gun_A", "Gun_B",
    "strEvent_Timestamp", "strPing_Timestamp",
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


async def _post_json(client, path, *, data=None, json=None):
    """POST to a legacy MVC action (form or JSON body) and return parsed JSON.

    Sends the ``X-Requested-With`` header the legacy AJAX actions require.
    Returns ``[]`` for an empty response body.
    """
    resp = await client.post(
        path, data=data, json=json,
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    resp.raise_for_status()
    if not resp.text.strip():
        return []
    return resp.json()


def _datatables_body(charger_id: int, limit: int) -> dict:
    """Minimal jQuery-DataTables server-side request body for the per-charger
    session/alert/action-history actions."""
    return {
        "draw": 1, "start": 0, "length": limit, "columns": [], "order": [],
        "search": {"value": "", "regex": False},
        "lvm": {"ChargerID": str(charger_id)}, "limit": limit,
    }


async def _load_charger_map(client) -> dict[str, int]:
    """Fetch the fleet-wide QChargerID(lower) -> numeric ChargerID map."""
    rows = await _post_json(
        client, "/ChargerDetails/GetChargerByCustomerIDForDD",
        data={"CustomerID": "0"},
    )
    out = {}
    for r in rows:
        qid, cid = r.get("QChargerID"), r.get("ChargerID")
        if qid and cid:
            out[str(qid).lower()] = int(cid)
    return out


async def _resolve_charger_id(client, qcharger_id: str, cache: dict) -> int:
    """QChargerID -> numeric ChargerID (case-insensitive), with a cached fleet map.

    Refreshes the map once on a miss (new chargers); if still absent, raises
    ValueError listing up to 5 near matches.
    """
    key = str(qcharger_id).strip().lower()
    if not cache:
        cache.update(await _load_charger_map(client))
    if key in cache:
        return cache[key]
    # miss -> refresh once (charger may have been added since last load)
    cache.clear()
    cache.update(await _load_charger_map(client))
    if key in cache:
        return cache[key]
    stem = key.rsplit("-", 1)[0]
    near = sorted(k for k in cache if stem and stem in k)[:5]
    hint = f" Did you mean: {', '.join(near)}?" if near else ""
    raise ValueError(f"Unknown QChargerID '{qcharger_id}'.{hint}")


def build_client() -> CmsAuthClient:
    return CmsAuthClient(
        base_url=settings.cms_base_url.rstrip("/"),
        user=settings.cms_user,
        password=settings.cms_pass,
        timeout=settings.request_timeout,
    )


def build_server(client: CmsAuthClient | None = None) -> FastMCP:
    mcp = FastMCP(name="Legacy CMS (old NOC, read-only)")
    client = client or build_client()

    async def _get_json(path: str, params: Optional[dict] = None) -> Any:
        resp = await client.get(path, params=params or {})
        resp.raise_for_status()
        if not resp.text.strip():
            return []
        return resp.json()

    _charger_map: dict[str, int] = {}  # QChargerID(lower) -> numeric ChargerID

    @mcp.tool
    async def cms_get_charger_details(qcharger_id: str) -> dict:
        """Full profile for ONE charger in the old CMS, by its QChargerID
        (e.g. "ab-sam-00585"): static metadata (type, controllers, capacity,
        serial, address) merged with live status (OCPP id, location, gun states,
        last event/ping)."""
        cid = await _resolve_charger_id(client, qcharger_id, _charger_map)
        body = {"ChargerID": str(cid)}
        details = await _post_json(
            client, "/ChargerDetails/GetChargerDetailsByChargerID", data=body)
        status = await _post_json(
            client, "/ChargerDetails/GetChargerStatusByChargerID", data=body)
        d = _trim(details, _CHG_DETAIL_FIELDS)
        s = _trim(status, _CHG_STATUS_FIELDS)
        merged = {}
        if d:
            merged.update(d[0])
        if s:
            merged.update(s[0])
        return merged

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
