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
returns the **124 GET (read) endpoints** across the on-device services — network/GSM (:8000),
RFID (:8001), power allocation (:8002), charging controller (:8003), and platform dev-tools /
diagnostics (:8005-8007) — with each endpoint's port, path, required role, and response schema.

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

## Notes / follow-ups

- All **153** GET operations are exposed (+1 catalog tool = 154). Auto-generated tool names are
  verbose (`dashboard_summary_api_v1_dashboard_summary_get`); a naming-cleanup pass (`mcp_names` /
  `mcp_component_fn`) is recommended to improve LLM tool-selection.
