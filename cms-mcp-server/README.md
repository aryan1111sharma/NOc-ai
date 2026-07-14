# noc-mcp-agent · cms-mcp-server

A mostly **read-only** MCP server for the **legacy CMS / old NOC** at **quenchcms.com** — some
chargers still live only in this old system. It complements the current-NOC `mcp-server`;
your assistant can query both. It also exposes a small, curated set of **gated, audited**
device-command writes for on-charger recovery (see [Device commands (gated)](#device-commands-gated)).

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
| `cms_run_device_query` | ExecuteApi (schedule) + ReadJsonLogFile (poll) | read-only device diagnostic response for a GET command (e.g. `id`, `state`, `outlets`, `ocpp-client/config`) |
| `cms_run_device_action_prepare` | GetAllApisByChargerID (validate against recovery allow-list) | STEP 1 of a gated write: one-time `confirmation_token` + command preview; does not execute |
| `cms_run_device_action_execute` | ExecuteApi | STEP 2: runs the approved write command (requires the token from `_prepare`); audited |
| `cms_read_device_log` | ReadJsonLogFile | contents of a session/alert state-log or command-output blob by its CMS file path |

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
- **Read-only by design — superseded.** The action *catalog* and *history* were exposed first,
  with no execute endpoint wrapped. Write actions have since shipped as a deliberate,
  separately-reviewed change: a curated 8-command recovery allow-list behind a two-step
  confirmation-token gate. See [Device commands (gated)](#device-commands-gated).

## Device commands (gated)

Beyond the read-only CMS tools, four tools run device commands over the old NOC
`ExecuteApi` endpoint (see `docs/device-api-catalog.md` for the full 129-command
catalog and `docs/superpowers/specs/2026-07-14-cms-device-command-tools-design.md`
for the design):

- **Reads** (`cms_run_device_query`) — any of the 60 GET diagnostics
  (`id`, `version`, `state`, `outlets`, `ocpp-client/config`, …). One call
  schedules, polls the execution history, and returns the device output.
- **Writes** (`cms_run_device_action_prepare` → `_execute`) — a **two-step,
  audited** gate over a curated 8-command recovery allow-list: `reset`,
  `restartStack`, controller reboot, `ocpp-client` start/stop/restart,
  `outlets/{number}/stop`, `services/rfid/clear`. **All other writes**
  (scripts, docker, gpio/pwm, hmi, network config, e-stop, …) are
  **hard-blocked in code** and unreachable even with a token.
- **Logs** (`cms_read_device_log`) — read a session/alert `FullStatePath` /
  `StateLogPath` blob; path is restricted to CMS state-log/result roots.

`ExecuteApi` is asynchronous; results are correlated by `ExecutionID` and read
back via `ReadJsonLogFile`. Every executed write is recorded to the
`cms_mcp.device_audit` logger (`DEVICE_AUDIT_LOG_FILE` to persist to a file).
