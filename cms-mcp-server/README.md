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

## Setup / run

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
cp .env.example .env      # set CMS_USER / CMS_PASS (prefer a read-only CMS account)
python -m cms_mcp        # stdio;  MCP_TRANSPORT=http for LibreChat
python -m pip install pytest pytest-asyncio && python -m pytest
```

## Deferred / follow-ups

- **Per-charger session & error tools** (`GetSessionDetailByChargerID`,
  `GetErrorCountByChargerID`) need a numeric `ChargerID` and an undocumented param contract
  the legacy actions expect (the map action exposes only `QChargerID`). The alerts action
  does return numeric `ChargerID`, giving a QChargerID→ChargerID bridge to build these later —
  capture the exact AJAX payload from the CMS frontend to finish them.
- Live per-charger status isn't available from a no-param action; fleet status comes from
  `cms_get_noc_summary`.
