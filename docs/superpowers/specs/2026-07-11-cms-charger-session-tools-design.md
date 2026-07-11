# CMS ChargerSession per-charger tools — design

**Date:** 2026-07-11
**Status:** Proposed
**Author:** kmittal (with Claude)
**Component:** `cms-mcp-server` (Legacy CMS MCP server)

## 1. Problem

The legacy-CMS MCP server currently exposes six fleet-level, read-only tools
(`cms_list_chargers`, `cms_get_charger_types`, `cms_get_noc_summary`,
`cms_get_recent_alerts`, `cms_list_customers`, `cms_list_service_engineers`).
None of them answer **per-charger** operational questions ("show me the recent
sessions / alerts / actions for charger `ab-sam-00585`").

`server.py:99-102` records that per-charger session/error tools were parked
because the legacy actions need a **numeric `ChargerID`** and an undocumented
param contract, while the fleet map action exposes only the human-facing
**`QChargerID`**. This spec closes that gap using the contracts captured live
from `https://quenchcms.com/ChargerDetails/ChargerSession`.

## 2. Scope

**In scope:** 5 new **read-only** tools + one internal id-resolver, all
following the existing "call MVC JSON action → trim god-DTO to a whitelist"
pattern in `server.py`.

**Out of scope (explicit):** executing device commands. The Actions tab's
`GetAllApisByChargerID` lists 129 device APIs including writes (`reset`,
`unplug`, `restartStack`, `POST outlets/state`). We wrap only the **catalog**
(what exists) and the **execution history** (what already ran). We deliberately
do **not** wrap any execute endpoint. This keeps the server strictly read-only,
consistent with its current design and the deployment access-model decision.

## 3. Endpoint contracts (captured live, 2026-07-11)

All are `POST` on the `/ChargerDetails/` controller and require the auth-cookie
session already implemented in `CmsAuthClient`.

### 3.1 Resolver — `GetChargerByCustomerIDForDD`
- **Body:** `CustomerID=0` (form-encoded) → returns **all 1079** chargers.
  `CustomerID=<id>` scopes to one customer. Empty string → HTTP 500.
- **Response:** JSON array of god-DTOs; meaningful fields per row:
  `ChargerID` (numeric), `QChargerID` (string), `Address`, `IsCommissioned`.
- **Use:** one call builds the full `QChargerID → ChargerID` map for the fleet.

### 3.2 Info — `GetChargerDetailsByChargerID`
- **Body:** `ChargerID=<numeric>` (form-encoded).
- **Response:** single JSON object. Meaningful fields:
  `QChargerID`, `ChargerTypeID`, `Description` (model, e.g. "Dual CCS"),
  `CapacityKW`, `SerialNumber`, `ControllerADescription`, `ControllerBDescription`,
  `Address`, `StateName`, `CountryName`, `Name` (customer), `IsActive`.

### 3.3 Info — `GetChargerStatusByChargerID`
- **Body:** `ChargerID=<numeric>` (form-encoded).
- **Response:** single JSON object. Meaningful fields:
  `OCPPID`, `Latitude`, `Longitude`, `Gun_A`, `Gun_B` (outlet state codes),
  `strEvent_Timestamp` / `Event_Timestamp`, `strPing_Timestamp` / `Ping_Timestamp`.
  (`TotalDuration` is a .NET TimeSpan object — skip.)

### 3.4 Sessions — `GetSessionLogsByChargerID`
- **Body:** jQuery DataTables server-side JSON. Minimal working payload:
  `{"draw":1,"start":0,"length":<n>,"columns":[],"order":[],
    "search":{"value":"","regex":false},"lvm":{"ChargerID":"<numeric>"},"limit":<n>}`
- **Response:** `{"draw":1,"data":[...],"iTotalRecords":N,"iTotalDisplayRecords":M}`
  (legacy DataTables key names). Meaningful row fields:
  `SessionEventID`, `Description` (e.g. `SESSION_STOP`), `Outlet`, `ChargerIDGun`,
  `strStartTime`, `Duration`, `Energy`, `SessionUserID`, `SessionErrorID`,
  `ErrorDesc`, `strTimestamp`.

### 3.5 Alerts — `GetAlertLogsByChargerID`
- **Body:** same DataTables shape as 3.4.
- **Response:** DataTables. Meaningful row fields:
  `SessionEventID`, `Description` (e.g. `ALERT`), `Outlet`, `ChargerIDGun`,
  `Src`, `SessionErrorID`, `ErrorDesc`, `strTimestamp`.

### 3.6 Action history — `GetApiExecutionDetailsByID`
- **Body:** DataTables shape as 3.4 (`lvm:{ChargerID}`).
- **Response:** DataTables. Meaningful row fields:
  `Name`, `API`, `ApiType` (GET/POST…), `Description` (e.g. `Executed`),
  `StatusID`, `strExecutedTime`, `ExecutionID`, `strTimestamp`.

### 3.7 Action catalog — `GetAllApisByChargerID`
- **Body:** `ChargerID=<numeric>` (form-encoded).
- **Response:** JSON array (129 rows). Meaningful row fields:
  `ApiID`, `Name`, `API`, `ApiType`, `ApiTypeID`, `ApiDescription`,
  `CategoryID`, `IsInputRequired`, `IsActive`.

### Noise to drop everywhere
`RecordsCount` / `DisplayRecordsCount` (global counters),
`StateLogPath` / `FullStatePath` / `Output` (server filesystem paths like
`E:\CMS_Chargers_State_Logs\...`), and all the null/zero god-DTO fields (reuse
the existing `_trim` helper, which already drops `None/""/0/"0"`).

## 4. New tools

All accept the human-facing `qcharger_id` (string) and resolve it internally to
the numeric `ChargerID`. All are read-only.

1. **`cms_get_charger_details(qcharger_id)`** — merges 3.2 + 3.3 into one
   "about this charger" object: static metadata **and** live status (OCPP id,
   location, gun states, last event/ping). One call answers "tell me about X".
2. **`cms_get_charger_sessions(qcharger_id, limit=10)`** — recent charging
   session logs (3.4), newest first, trimmed.
3. **`cms_get_charger_alerts(qcharger_id, limit=10)`** — recent alert/fault
   logs (3.5), trimmed.
4. **`cms_get_charger_action_history(qcharger_id, limit=10)`** — recent remote
   command execution history (3.6), trimmed.
5. **`cms_list_charger_actions(qcharger_id)`** — the device command catalog
   (3.7): what commands exist for this charger, read-only reference. No `limit`
   (bounded set of ~129).

## 5. Internal design

### 5.1 Id resolver
```
_charger_map: dict[str, int] | None = None   # QChargerID -> ChargerID, cached

async def _resolve_charger_id(qcharger_id) -> int:
    # lazy-load the full map once via GetChargerByCustomerIDForDD (CustomerID=0)
    # match case-insensitively; on miss, refresh once (new chargers), then
    # raise a clear ValueError listing near matches if still not found.
```
- **Caching:** module/server-lifetime cache (the map is ~1079 entries and
  changes rarely). One refresh-on-miss handles newly-commissioned chargers.
- **POST helper:** add a `_post_json(path, data)` alongside the existing
  `_get_json`, using `client.post(path, data=...)` with the
  `X-Requested-With: XMLHttpRequest` header. The existing `CmsAuthClient.send`
  re-login logic already covers POSTs.

### 5.2 DataTables helper
```
def _datatables_body(charger_id: int, limit: int) -> dict:
    return {"draw":1,"start":0,"length":limit,"columns":[],"order":[],
            "search":{"value":"","regex":False},
            "lvm":{"ChargerID":str(charger_id)},"limit":limit}
```
Posted as JSON. Response parsed via `resp.json()["data"]`, then `_trim`ed to the
per-tool whitelist and sliced to `limit`. Rows come back in the CMS default order
(observed newest-first: `Sr_No` 1 = most recent timestamp); we do not send an
explicit `order`, so we inherit whatever the legacy action returns.

### 5.3 Field whitelists
Add module-level lists mirroring §3 (e.g. `_SESSION_FIELDS`, `_CHG_ALERT_FIELDS`,
`_ACTION_HIST_FIELDS`, `_ACTION_CATALOG_FIELDS`, `_CHG_DETAIL_FIELDS`,
`_CHG_STATUS_FIELDS`), reusing the existing `_trim` helper.

### 5.4 File-size guard
`server.py` is ~116 lines today; +5 tools + helpers keeps it well under the
2000-line hard limit. No split needed. If it grows past ~400 lines later,
consider splitting whitelists into `fields.py` and the resolver into
`resolver.py` — out of scope now.

## 6. Error handling
- **Unknown QChargerID:** `_resolve_charger_id` raises `ValueError` with a helpful
  message (and up to ~5 near-match suggestions) after a single map refresh.
- **Empty DataTables result:** return `[]` (a charger with no sessions/alerts is
  valid, not an error).
- **HTTP 500 from a legacy action:** let `resp.raise_for_status()` surface it,
  matching current tool behavior.

## 7. Testing / verification
- **Unit:** `_resolve_charger_id` (hit, case-insensitive hit, miss→refresh→raise),
  `_datatables_body` shape, `_trim` on a captured god-DTO sample → whitelist only.
  Use a mocked `CmsAuthClient` returning captured fixtures (sanitized).
- **Integration (manual, gated on creds):** run the stdio server, call each tool
  for a known charger (`ab-sam-sim` / `ab-sam-00585`), confirm trimmed, non-empty
  output and that unknown ids raise the clear error.
- **Success criteria:** all 5 tools return trimmed data for a valid QChargerID;
  unknown id raises a helpful error; no server filesystem paths or god-DTO nulls
  leak into any payload.

## 8. Docs / release
- Update `cms-mcp-server/README.md` tool table with the 5 new tools.
- Version bump per the team convention (`scripts/bump-version.ps1`, `feat` →
  minor) on completion; push branch + tag.
