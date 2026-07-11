# noc-mcp-agent · cms-mcp-server

A **read-only** MCP server for the **legacy CMS / old NOC** at **quenchcms.com** — some
chargers still live only in this old system. It complements the current-NOC `mcp-server`;
your assistant can query both.

## How it works

The old CMS is a **legacy ASP.NET MVC** app (no OpenAPI, session/cookie auth), very different
from the FastAPI NOC. So instead of generating tools from a spec, this server hand-wraps the
useful MVC JSON actions as **curated, field-trimmed tools** (the legacy actions return one
~206-field "god" DTO per row, so trimming is essential).

- `CmsAuthClient` (`cms_mcp/auth.py`) logs in via `POST /Home/GetUsersByEmailAndPassword`
  (form-encoded), holds the `.ASPXAUTH` + `ASP.NET_SessionId` cookies, and re-logs-in on
  session expiry (a protected action redirects to login / returns HTML).

## Tools (read-only)

| Tool | Old-CMS action | Returns |
|---|---|---|
| `cms_get_noc_summary` | GetNocDetailsForchart | fleet totals per customer (total/online/offline/faulty) |
| `cms_list_chargers` | GetChargerDetailsForMap | ~1072 chargers (QChargerID + customer), searchable |
| `cms_get_recent_alerts` | GetAlertsForBellNotification | recent alerts (charger, error code, timestamp) |
| `cms_get_charger_types` | GetChargerTypes | charger types/models |
| `cms_list_customers` | Customer/GetCustomersForDD | customers (id, name, contact, country) |
| `cms_list_service_engineers` | Customer/GetServiceEngineerList | service engineers |

### Per-charger tools (by `QChargerID`, e.g. `ab-sam-00585`)

These take the human-facing `QChargerID` and resolve it internally to the numeric
`ChargerID` (one cached call to `GetChargerByCustomerIDForDD` with `CustomerID=0`
maps the whole fleet). They back the CMS `ChargerSession` page's Info / Sessions /
Alerts / Actions tabs.

| Tool | Old-CMS action(s) | Returns |
|---|---|---|
| `cms_get_charger_details` | GetChargerDetailsByChargerID + GetChargerStatusByChargerID | one charger's metadata (type, controllers, capacity, serial, address) merged with live status (OCPP id, location, gun states, last event/ping) |
| `cms_get_charger_sessions` | GetSessionLogsByChargerID | recent charging-session logs (event, outlet/gun, start, duration, energy, session error); `limit` default 10 |
| `cms_get_charger_alerts` | GetAlertLogsByChargerID | recent alert/fault logs for the charger (source, outlet/gun, error id + desc, timestamp); `limit` default 10 |
| `cms_get_charger_action_history` | GetApiExecutionDetailsByID | recent remote-command execution history (which API ran, type, status, when); read-only; `limit` default 10 |
| `cms_list_charger_actions` | GetAllApisByChargerID | catalog of device API commands available for the charger (name, verb, description); read-only reference — never executes |

## Setup / run

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
cp .env.example .env      # set CMS_USER / CMS_PASS (prefer a read-only CMS account)
python -m cms_mcp        # stdio;  MCP_TRANSPORT=http for LibreChat
python -m pip install pytest pytest-asyncio && python -m pytest
```

## Deferred / follow-ups

- **Per-charger session/error/status tools — done.** The parked work is now shipped as the
  per-charger tools above; the numeric-`ChargerID` blocker was solved by resolving
  `QChargerID` via `GetChargerByCustomerIDForDD` (`CustomerID=0` returns the full fleet with
  both ids). Endpoint contracts were captured from the CMS `ChargerSession` frontend.
- **Read-only by design:** the action *catalog* and *history* are exposed, but no tool wraps a
  device-command execute endpoint (`reset`, `unplug`, `restartStack`, …). Adding write actions
  would be a deliberate, separately-reviewed change.
