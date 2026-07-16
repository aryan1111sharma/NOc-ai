"""Curated read-only MCP tools over the legacy CMS (old NOC at quenchcms.com).

The legacy app has no OpenAPI spec and returns one ~206-field "god" ViewModel for every
action, so each tool calls the relevant MVC JSON action and TRIMS the response to a small
whitelist of meaningful fields (dropping nulls/empties) to keep payloads LLM-friendly.
"""

from __future__ import annotations

import json as jsonlib
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
    # The live status action populates the raw /Date(ms)/ keys; the str-prefixed
    # variants are usually null there. Whitelist both so last-event/ping always
    # surfaces regardless of which the endpoint fills.
    "Event_Timestamp", "Ping_Timestamp", "strEvent_Timestamp", "strPing_Timestamp",
]
_SESSION_FIELDS = [
    "SessionEventID", "Description", "Outlet", "ChargerIDGun", "strStartTime",
    "Duration", "Energy", "SessionUserID", "SessionErrorID", "ErrorDesc",
    "strTimestamp",
]
_CHG_ALERT_FIELDS = [
    "SessionEventID", "Description", "Outlet", "ChargerIDGun", "Src",
    "SessionErrorID", "ErrorDesc", "strTimestamp",
]
_ACTION_HIST_FIELDS = [
    "Name", "API", "ApiType", "Description", "StatusID", "strExecutedTime",
    "ExecutionID", "strTimestamp",
]
_ACTION_CATALOG_FIELDS = [
    "ApiID", "Name", "API", "ApiType", "ApiTypeID", "ApiDescription",
    "CategoryID", "IsInputRequired", "IsActive",
]


def _keep(v: Any) -> bool:
    """Whether a field value is meaningful enough to keep.

    Booleans are always kept (``False`` is meaningful — e.g. an inactive charger's
    ``IsActive: False``); everything else drops the null/empty/zero noise the
    legacy god-DTO is padded with. Note ``bool`` must be checked before the
    membership test because ``False == 0`` in Python.
    """
    if isinstance(v, bool):
        return True
    return v not in (None, "", 0, "0")


def _trim(rows: Any, fields: list[str]) -> list[dict]:
    """Keep only whitelisted, non-empty fields from each row."""
    if not isinstance(rows, list):
        rows = [rows] if isinstance(rows, dict) else []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        out.append({k: r[k] for k in fields if _keep(r.get(k))})
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


async def _post_datatables(client, path: str, charger_id: int, limit: int) -> dict:
    """POST a DataTables request to a legacy list action and return the parsed payload.

    Content-type is load-bearing. The page's DataTables config passes
    ``JSON.stringify(d)`` to ``$.ajax`` without setting ``contentType``, so jQuery
    labels the JSON body with its urlencoded default -- and the legacy binder
    accepts only that. Labelling the same bytes ``application/json`` makes the
    action return 200 with an EMPTY body instead of an error, which reads as
    "this charger has no rows" rather than as a failure. Keep this header.

    Returns ``{}`` for an empty body (a CMS flap) so callers see no rows rather
    than a JSON decode error.
    """
    resp = await client.post(
        path, content=jsonlib.dumps(_datatables_body(charger_id, limit)),
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    resp.raise_for_status()
    if not resp.text.strip():
        return {}
    return resp.json()


async def _get_json_via(client, path: str, params: Optional[dict] = None) -> Any:
    """GET a legacy MVC JSON action. Module-level twin of build_server's _get_json,
    for modules (fleet.py) that hold a client but not the closure."""
    resp = await client.get(path, params=params or {})
    resp.raise_for_status()
    if not resp.text.strip():
        return []
    return resp.json()


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

    @mcp.tool
    async def cms_get_charger_sessions(qcharger_id: str, limit: int = 10) -> list[dict]:
        """Recent charging-session logs for ONE charger (by QChargerID) from the
        old CMS: session events, outlet/gun, start time, duration, energy, and any
        session error. Returned in the CMS default order (newest first)."""
        cid = await _resolve_charger_id(client, qcharger_id, _charger_map)
        resp = await _post_datatables(
            client, "/ChargerDetails/GetSessionLogsByChargerID", cid, limit)
        rows = resp.get("data", []) if isinstance(resp, dict) else []
        return _trim(rows, _SESSION_FIELDS)[:limit]

    @mcp.tool
    async def cms_get_charger_alerts(qcharger_id: str, limit: int = 10) -> list[dict]:
        """Recent alert/fault logs for ONE charger (by QChargerID) from the old
        CMS: alert events with source, outlet/gun, error id + description, and
        timestamp. Newest first. (For a fleet-wide alert feed use
        cms_get_recent_alerts instead.)"""
        cid = await _resolve_charger_id(client, qcharger_id, _charger_map)
        resp = await _post_datatables(
            client, "/ChargerDetails/GetAlertLogsByChargerID", cid, limit)
        rows = resp.get("data", []) if isinstance(resp, dict) else []
        return _trim(rows, _CHG_ALERT_FIELDS)[:limit]

    @mcp.tool
    async def cms_get_charger_action_history(
        qcharger_id: str, limit: int = 10
    ) -> list[dict]:
        """Recent remote-command execution history for ONE charger (by QChargerID)
        from the old CMS: which device API was run, its type, execution status,
        and when. Read-only history — this does NOT run any command."""
        cid = await _resolve_charger_id(client, qcharger_id, _charger_map)
        resp = await _post_datatables(
            client, "/ChargerDetails/GetApiExecutionDetailsByID", cid, limit)
        rows = resp.get("data", []) if isinstance(resp, dict) else []
        return _trim(rows, _ACTION_HIST_FIELDS)[:limit]

    @mcp.tool
    async def cms_list_charger_actions(qcharger_id: str) -> list[dict]:
        """Catalog of device API commands available for ONE charger (by
        QChargerID) in the old CMS: command name, HTTP verb, and description.
        Read-only reference (what CAN be run) — these tools never execute a
        command."""
        cid = await _resolve_charger_id(client, qcharger_id, _charger_map)
        rows = await _post_json(
            client, "/ChargerDetails/GetAllApisByChargerID",
            data={"ChargerID": str(cid)})
        return _trim(rows, _ACTION_CATALOG_FIELDS)

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

    # ── Customers / reference ────────────────────────────────────────────────────────────
    @mcp.tool
    async def cms_list_customers() -> list[dict]:
        """Customers in the old CMS (id, name, contact, country)."""
        return _trim(await _get_json("/Customer/GetCustomersForDD"), _CUSTOMER_FIELDS)

    @mcp.tool
    async def cms_list_service_engineers() -> list[dict]:
        """Service engineers registered in the old CMS (id, name, state)."""
        return await _get_json("/Customer/GetServiceEngineerList")

    from .device_commands import register_device_tools
    register_device_tools(mcp, client, _charger_map)

    return mcp
