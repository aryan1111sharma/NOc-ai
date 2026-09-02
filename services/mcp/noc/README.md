# noc-mcp-agent · mcp-server

A **read-only** [FastMCP](https://gofastmcp.com) server that exposes the QuenchNOC backend's
GET endpoints as MCP tools. It authenticates to the NOC as a service account and never
performs writes — only `GET` operations are surfaced (every other method is excluded at the
route-map layer).

## How it works

- The NOC OpenAPI spec is **bundled** at `noc_mcp/openapi/noc_openapi.json` (the backend
  does not serve it publicly). `FastMCP.from_openapi` turns each GET operation into a tool.
- `NocAuthClient` logs in via `POST /api/v1/auth/login`, attaches the bearer token,
  re-logs-in shortly before expiry, and re-logs-in once on a `401` (there is no refresh
  token). See `noc_mcp/auth.py`.

## Setup

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Windows
cp .env.example .env        # then fill in NOC_EMAIL / NOC_PASSWORD
```

> **Use a dedicated read-only NOC user** for `NOC_EMAIL`/`NOC_PASSWORD`, not an admin
> account. Read-only is enforced two ways: this server exposes only GET tools, *and* the
> account should hold only read permissions.

## Run

```bash
# stdio (Claude Desktop / local MCP clients)
python -m noc_mcp

# http (remote / LibreChat)
MCP_TRANSPORT=http python -m noc_mcp     # serves on MCP_HOST:MCP_PORT (default 127.0.0.1:8100)
```

## Test

```bash
python -m pip install pytest pytest-asyncio
python -m pytest
```

## Regenerate the bundled spec (when the NOC API changes)

```bash
# from an env with the *backend's* requirements installed:
python scripts/generate_openapi.py /path/to/noc-server/noc_backend
```

## Config (env / `.env`)

| Var | Default | Purpose |
|---|---|---|
| `NOC_BASE_URL` | `https://www.manthanquench.online` | NOC backend base URL |
| `NOC_EMAIL` / `NOC_PASSWORD` | — | service-account credentials (read-only user) |
| `MCP_TRANSPORT` | `stdio` | `stdio` or `http` |
| `MCP_HOST` / `MCP_PORT` | `127.0.0.1` / `8100` | http transport bind |
| `REQUEST_TIMEOUT` | `30` | per-request timeout (seconds) |

## On-charger control-center catalog

Beyond the NOC API tools, the server adds one knowledge tool, **`get_control_center_api_catalog`**,
built from `noc_mcp/catalog/rbac-rules.csv` (the RBAC-authoritative on-charger API list). It
returns **all 239 endpoints** across the on-device services — network/GSM (:8000), RFID (:8001),
power allocation (:8002), charging controller (:8003), and platform dev-tools / diagnostics
(:8005-8007) — with each endpoint's port, path, required role, and response schema. Only the
**124 GET endpoints** are marked `callable: true`; the remaining non-GET entries carry
`callable: false` and a `not_callable_reason` — listed for awareness (so the model can tell an
operator what a charger supports) but never callable by any route.

The model calls this to discover what live per-charger data exists, then fetches a specific
endpoint through the **generic charger proxy** tool
(`proxy_api_call_path_based_...`) using the endpoint's port + path. To refresh the catalog,
replace `noc_mcp/catalog/rbac-rules.csv` and rebuild.

> Safety note: the generic proxy could technically forward non-GET methods to a charger, so
> `ReadOnlyProxyMiddleware` (`noc_mcp/readonly.py`) **rejects any `method` other than GET** on the
> proxy tools before the call reaches the NOC — a hard read-only guarantee.

## Curated on-charger tools (Tier-1)

For the highest-value on-charger reads, the server also registers **13 named, read-only tools**
that wrap the charger proxy (so the model doesn't have to assemble port+path). All take the NOC
`charger_id` (the charger must be online) and pass the device JSON through; composites merge a
few related endpoints under fixed keys.

| Tool | Reads |
|---|---|
| `charger_get_charging_status` | per-gun charging/PLC/OCPP state (`gun_id` optional) |
| `charger_get_session_details` | per-gun active+last session, or recent history |
| `charger_get_meters` | AC + per-gun DC meters (+ IMD when `gun_id` given) |
| `charger_get_ocpp_status` | OCPP connection status + config keys |
| `charger_get_network_status` | overall network + GSM signal/operator |
| `charger_get_system_info` | hardware, software version, RTC |
| `charger_get_allocation_state` | power-allocation state |
| `charger_get_pm_power` | power-module count + per-module power |
| `charger_get_alarms` | latched alarms + pending errors (or per-gun) |
| `charger_get_temperature` | sensor readings + thresholds |
| `charger_list_log_sources` / `charger_get_logs` | on-charger log sources + fetch (admin) |
| `charger_get_device_metrics` | device CPU/memory/disk (admin) |

The long tail (~110 other on-charger GET endpoints) stays reachable via
`get_control_center_api_catalog` + the GET-locked generic proxy. The `logs`/`metrics` tools need
an admin-level service account; others are customer/operator level.

## Curated NOC-backend tools

The 13 Tier-1 tools above all read a live charger through the proxy — they need it online. These
8 tools instead read the NOC backend's own database, so they answer whether or not the charger is
online:

| Tool | Reads |
|---|---|
| `noc_report(report, ...)` | 13 fleet reports: session failures, fault frequency, MTTR, energy, warranty, ticket resolution |
| `noc_stats(metric, ...)` | 11 fleet statistics: alarm stats, error trends, session stats |
| `noc_fleet_status(...)` | Which chargers are online/offline right now, with filters |
| `noc_charger_history(charger_id)` | Recent sessions + alarm history for one charger |
| `noc_charger_telemetry(charger_id)` | Latest snapshot, time-bucketed aggregates, raw history |
| `noc_session_events(charger_id, session_id)` | Session lifecycle events (why a session failed) |
| `noc_charger_stability(charger_id)` | Reconnect frequency, command history, SSH status |
| `noc_charger_ocpp_config(charger_id)` | OCPP keys, live with cached fallback |

`noc_report` and `noc_stats` are dispatchers: rather than exposing the backend's 24 reports/stats
endpoints as 24 separate tools (which would swamp tool selection), each takes a `report`/`metric`
enum documented in the tool's own description.

### Freshness envelope

Every curated read tool stamps its response so archived data is never mistaken for current state
(`noc_mcp/freshness.py`):

```json
{"source": "live" | "history", "as_of": "...", "stale_seconds": 0,
 "reason": null, "clock_skew": false}
```

`stale_seconds` may be negative when the source's clock is ahead of the server, with
`clock_skew: true` — charger RTC drift is real, and silently clamping it would make stale data
look fresh.

`charger_get_ocpp_status`, `charger_get_alarms` and `charger_get_session_details` (all Tier-1)
fall back to NOC-stored history when the live read fails, marked `source: "history"`. For a
gun-scoped query the fallback is charger-wide and says so in `reason`, since the history
endpoints accept no gun filter. `noc_charger_ocpp_config` does the same the other way round: it
prefers the live device and only falls back to the backend's cached copy when the charger is
offline.

### Read-only guarantee

Only GET operations are exposed. `ReadOnlyProxyMiddleware` (`noc_mcp/readonly.py`) rejects any
non-GET `method` argument on a tool that can reach a live charger, and `charger_id`/`path` are
validated fail-closed before being interpolated into the proxy URL (`validate_charger_id` in
`noc_mcp/identifiers.py`; an allowlist for `path` in `noc_mcp/control_center.py`) — the NOC
backend percent-decodes the path before routing, so an unvalidated id or path could otherwise
hijack the port and device path. `noc_mcp/identifiers.py` is shared with `ssh_exec.py`, which
splices `charger_id` into a WebSocket URL the same way, and with `validate_session_id` for the
one route that takes a session id.

There are exactly **two** ways out to the network, and every tool uses one of them:

| choke point | reaches | validates |
| --- | --- | --- |
| `control_center._proxy_get` | the charger proxy (live device) | `_validate_proxy_url`: charger id + port + device path |
| `http.get` (`noc_mcp/http.py`) | plain NOC backend routes | identifier keywords (`charger_id=` / `session_id=`), then the assembled URL |

`http.get` always sends `params` (`{}` when unset) and re-validates the URL it assembled
whatever it was handed, so a query string smuggled inside an identifier cannot survive as real
query parameters — which matters because the backend's charger-proxy route reads `?method=` to
choose the HTTP verb it forwards to live hardware. Feature modules do not call `client.get`
themselves; `tests/test_backend_url_hardening.py` asserts that.

## SSH command execution (the one non-read-only capability)

Two tools run arbitrary shell commands on a **live** charger over its WebSocket PTY, behind a
mandatory two-step guardrail:

1. `charger_ssh_prepare(charger_id, command, ssh_username, ssh_password)` — does **not** execute;
   returns a one-time `confirmation_token` + preview. The assistant must show you the command and
   get explicit approval.
2. `charger_ssh_execute(confirmation_token)` — runs the command (single-use token, ~2 min TTL),
   returns `{output, exit_code, truncated}`, and writes an audit record (`noc_mcp.ssh_audit`).

The SSH password is passed only to `prepare`, held in memory keyed to the token, used once, and
never logged, returned, or persisted. Arbitrary commands are permitted once confirmed (no
denylist). The WebSocket auth uses the server's NOC token, which must have `tools:ssh_terminal`
+ `chargers:control`. Config: `SSH_COMMAND_TIMEOUT`, `SSH_CONNECT_TIMEOUT`,
`SSH_MAX_OUTPUT_BYTES`, `SSH_PENDING_TTL_SECONDS`.

## Notes / follow-ups

- The server exposes **114 tools**: 8 curated NOC-backend tools, 13 curated Tier-1 on-charger
  tools, 2 SSH-exec tools, 1 catalog tool, 3 proxy/status tools, and 87 remaining
  auto-generated GET operations. User-admin endpoints and every endpoint a curated tool now
  owns are excluded from generation to keep the surface findable. Auto-generated tool names are
  still verbose (`dashboard_summary_api_v1_dashboard_summary_get`); a naming-cleanup pass
  (`mcp_names` / `mcp_component_fn`) is recommended to improve LLM tool-selection for the
  remaining 87.
