"""Shared httpx.MockTransport fake of the legacy CMS for tool integration tests.

Simulates login + every per-charger endpoint the new tools call, keyed to the
sim charger ab-sam-sim (numeric ChargerID 1429).

Content-type is enforced per endpoint, mirroring the live CMS (verified against
quenchcms.com 2026-07-15). The legacy MVC binder does not error on a wrong
content-type -- it answers 200 with an EMPTY body -- so a fake that accepts any
content-type lets a silently-broken tool pass its tests. It bit us once already:
the DataTables tools shipped sending application/json and returned [] for every
charger in production while these tests stayed green.

  - DataTables actions (jQuery sends a JSON string with jQuery's *default*
    content-type): application/x-www-form-urlencoded
  - ExecuteApi / ReadJsonLogFile ($.ajax with an explicit contentType): application/json
"""

import json
import time

import httpx

from cms_mcp.auth import CmsAuthClient
from cms_mcp.server import build_server

_LOGIN = "/Home/GetUsersByEmailAndPassword"

# god-DTO rows with real-shaped noise fields the tools must trim away.
_CHARGERS = [
    {"ChargerID": 1429, "QChargerID": "ab-sam-sim"},
    {"ChargerID": 1187, "QChargerID": "ab-sam-00585"},
    {"ChargerID": 1188, "QChargerID": "ab-sam-00591"},
]
_DETAILS = {
    "QChargerID": "ab-sam-sim", "ChargerTypeID": 1, "CustomerID": 101,
    "Description": "Dual CCS", "CapacityKW": 60.0, "SerialNumber": "noc_sim",
    "ControllerADescription": "SIMSECC", "ControllerBDescription": "SIMSECCLE",
    "Address": "Ador Powertron Limited, Pune", "StateName": "Maharashtra",
    "CountryName": "India", "Name": "Ador", "IsActive": True,
    "StateLogPath": "E:\\CMS\\x.json",  # noise -> must be dropped
}
_STATUS = {
    "QChargerID": "ab-sam-sim", "OCPPID": "noc_sim", "Latitude": "18.64",
    "Longitude": "73.79", "Gun_A": 2, "Gun_B": 2,
    # Mirrors the live capture: the raw /Date(ms)/ keys are populated while the
    # str-prefixed variants come back null on the status action.
    "Event_Timestamp": "/Date(1781076309716)/", "strEvent_Timestamp": None,
    "Ping_Timestamp": "/Date(1781076330550)/", "strPing_Timestamp": None,
    "Name": "Ador",
}
_SESSIONS = {"draw": 1, "iTotalRecords": 22, "iTotalDisplayRecords": 22, "data": [
    {"SessionEventID": 2, "Description": "SESSION_STOP", "Outlet": 1,
     "ChargerIDGun": "secc2300-21641", "strStartTime": "01/01/1970 05:30:00",
     "strTimestamp": "10/06/2026 12:54:40", "Energy": None,
     "StateLogPath": "E:\\CMS\\x.json"},  # noise -> dropped
]}
_ALERTS = {"draw": 1, "iTotalRecords": 6, "iTotalDisplayRecords": 6, "data": [
    {"SessionEventID": 5, "Description": "ALERT", "Outlet": 2,
     "ChargerIDGun": "secc2300-22700", "Src": "stack", "SessionErrorID": 56,
     "ErrorDesc": "NoError", "strTimestamp": "10/06/2026 12:55:21",
     "FullStatePath": "E:\\CMS\\x.text"},  # noise -> dropped
]}
_ACTION_HIST = {"draw": 1, "iTotalRecords": 1, "iTotalDisplayRecords": 1, "data": [
    {"Name": "ID", "API": "id", "ApiType": "GET", "Description": "Executed",
     "StatusID": 2, "strExecutedTime": "10/06/2026 12:48:25", "ExecutionID": 414827,
     "strTimestamp": "10/06/2026 12:47:34", "Output": "E:\\ApiResponses\\r.txt"},
]}
_CATALOG = [
    {"ApiID": 1, "Name": "ID", "API": "id", "ApiType": "GET", "ApiTypeID": 1,
     "ApiDescription": "Returns device ID", "CategoryID": 1,
     "IsInputRequired": False, "IsActive": True},
    {"ApiID": 16, "Name": "Reset", "API": "reset", "ApiType": "POST", "ApiTypeID": 3,
     "ApiDescription": "Reset", "CategoryID": 1, "IsInputRequired": False,
     "IsActive": True},
]


def _ping(minutes_ago: float) -> str:
    """A /Date(ms)/ ping stamp `minutes_ago` in the past, as the live CMS emits."""
    return "/Date(%d)/" % int((time.time() - minutes_ago * 60) * 1000)


# GetChargerDetailsForMap: the fleet-wide map feed. The live payload carries per-charger
# Ping_Timestamp/StatusID; StatusID reads 0 even on healthy chargers, so ping recency is
# the only usable online signal (mirrored here deliberately).
def _fleet_map() -> list[dict]:
    return [
        {"QChargerID": "ab-sam-sim", "ChargerID": 1429, "Name": "Ador",
         "StatusID": 0, "IsActive": True, "Ping_Timestamp": _ping(1)},      # online
        {"QChargerID": "ab-sam-00585", "ChargerID": 1187, "Name": "Ador",
         "StatusID": 0, "IsActive": True, "Ping_Timestamp": _ping(2)},      # online
        {"QChargerID": "ab-sam-00591", "ChargerID": 1188, "Name": "Ador",
         "StatusID": 0, "IsActive": True, "Ping_Timestamp": _ping(600)},    # stale -> offline
    ]

# Stateful execution store for the ExecuteApi -> history -> ReadJsonLogFile cycle.
# Seeded with the historical action-history row that test_tools.py asserts on.
_EXECUTIONS = [dict(_ACTION_HIST["data"][0])]
_NEXT_EXECID = [500000]
_LOG_FILES = {_ACTION_HIST["data"][0]["Output"]: {"api": "id", "value": "device-ok"}}


def reset_execution_state():
    """Restore the execution store to its seed (the one historical row + its output)."""
    _EXECUTIONS[:] = [dict(_ACTION_HIST["data"][0])]
    _LOG_FILES.clear()
    _LOG_FILES[_ACTION_HIST["data"][0]["Output"]] = {"api": "id", "value": "device-ok"}
    _NEXT_EXECID[0] = 500000


_ROUTES = {
    "/ChargerDetails/GetChargerDetailsByChargerID": _DETAILS,
    "/ChargerDetails/GetChargerStatusByChargerID": _STATUS,
    "/ChargerDetails/GetSessionLogsByChargerID": _SESSIONS,
    "/ChargerDetails/GetAlertLogsByChargerID": _ALERTS,
    "/ChargerDetails/GetAllApisByChargerID": _CATALOG,
}

# Actions whose live counterpart only binds a jQuery-DataTables body, which jQuery
# posts under its default content-type. Anything else -> 200 with an empty body.
_DATATABLES_PATHS = (
    "/GetSessionLogsByChargerID",
    "/GetAlertLogsByChargerID",
    "/GetApiExecutionDetailsByID",
)

# The live CMS's silent rejection: 200 OK, no body, no error.
_SILENT_EMPTY = httpx.Response(200, content=b"",
                               headers={"content-type": "application/json"})


def _content_type(request: httpx.Request) -> str:
    return request.headers.get("content-type", "").lower()


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith(_LOGIN):
        return httpx.Response(200, json={"UserID": 1},
                              headers={"set-cookie": ".ASPXAUTH=abc; path=/"})
    if path.endswith("/GetChargerByCustomerIDForDD"):
        return httpx.Response(200, json=_CHARGERS)
    if path.endswith("/GetChargerDetailsForMap"):
        return httpx.Response(200, json=_fleet_map())
    if any(path.endswith(p) for p in _DATATABLES_PATHS):
        if "application/x-www-form-urlencoded" not in _content_type(request):
            return _SILENT_EMPTY
    elif path.endswith("/ExecuteApi") or path.endswith("/ReadJsonLogFile"):
        if "application/json" not in _content_type(request):
            return _SILENT_EMPTY
    if path.endswith("/ExecuteApi"):
        vvm = json.loads(request.content.decode().replace("{vvm:", '{"vvm":', 1))["vvm"]
        execid = _NEXT_EXECID[0]; _NEXT_EXECID[0] += 1
        outpath = f"E:\\ApiResponses\\{execid}.txt"
        _LOG_FILES[outpath] = {"api": vvm["API"], "value": "device-ok"}
        _EXECUTIONS.insert(0, {
            "Name": vvm["API"], "API": vvm["API"], "ApiType": "GET",
            "Description": "Executed", "StatusID": 2, "ExecutionID": execid,
            "strExecutedTime": "10/06/2026 12:48:25", "Output": outpath})
        return httpx.Response(200, json="API scheduled successfully !")
    if path.endswith("/GetApiExecutionDetailsByID"):
        return httpx.Response(200, json={
            "draw": 1, "iTotalRecords": len(_EXECUTIONS),
            "iTotalDisplayRecords": len(_EXECUTIONS), "data": list(_EXECUTIONS)})
    if path.endswith("/ReadJsonLogFile"):
        p = json.loads(request.content.decode().replace("{Paths:", '{"Paths":', 1))["Paths"]
        return httpx.Response(200, json=_LOG_FILES.get(p, "not found"))
    for suffix, payload in _ROUTES.items():
        if path.endswith(suffix):
            return httpx.Response(200, json=payload)
    return httpx.Response(404)


def build_test_server():
    """Return a FastMCP server wired to the fake CMS (no network)."""
    client = CmsAuthClient(
        base_url="http://cms.test", user="u", password="p",
        transport=httpx.MockTransport(_handler),
    )
    return build_server(client)


def build_test_client():
    """A CmsAuthClient wired to the fake CMS (for engine-level tests)."""
    return CmsAuthClient(base_url="http://cms.test", user="u", password="p",
                         transport=httpx.MockTransport(_handler))
