# noc-mcp-agent · noc-mcp-server

A [FastMCP](https://gofastmcp.com) server that exposes the QuenchNOC backend as MCP tools.
It is **mostly read-only**: every `GET` operation in the backend's OpenAPI spec is
auto-exposed as a tool, and every other method is excluded at the route-map layer by
default. On top of that, a small, deliberately curated set of write operations is
hand-registered — see "Curated write tools" below — each going through its own module so
every write this server can make is explicit and reviewable in one place, rather than a
blanket route-map flip. See `NOC_MCP_REMAINING_TOOLS.md` (repo root) for what was
deliberately left out and why.

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

## Curated write tools

Each family below is one module, each hand-registers its own tools onto the shared
`NocAuthClient` — no shared abstraction beyond that, matching this server's existing
`control_center.py`/`ssh_exec.py` convention of one file per concern. None of these are
1:1 wrappers over a raw backend endpoint with an arbitrary body — every argument is a named,
typed parameter read from the backend's actual OpenAPI schema. See
`NOC_MCP_REMAINING_TOOLS.md` for what was deliberately excluded from this wave and why.

- **`tickets.py`** — `ticket_create`, `ticket_add_comment`, `ticket_assign`,
  `ticket_set_status`, `ticket_add_visit`, `ticket_reopen`, `ticket_verify`,
  `ticket_update`, `ticket_link`.
- **`alerts.py`** — `alert_acknowledge`, `alert_resolve`.
- **`diagnostics.py`** — `charger_request_log_download`, `charger_export_state_logs`
  (both create an async export/download job; neither returns log content directly).
- **`maintenance.py`** — `maintenance_window_create` (suppresses alerts/notifications for
  the window by default — the tool's own docstring tells the calling model to confirm scope
  and time range with the user first).
- **`charger_support.py`** — `charger_generate_qr_token`.

None of these use the two-step prepare/execute gate `charger_ssh_execute` uses below — that
pattern is reserved for HIGH/CRITICAL-risk physical-hardware actions. These are all
LOW/MEDIUM-risk business-data writes (backend-validated, individually scoped, no
fleet-wide blast radius), consistent with the risk classification in
`NOC_MCP_REMAINING_TOOLS.md`.

## SSH command execution (still the one non-read-only *physical hardware* capability)

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

- All **153** GET operations are exposed (+1 catalog tool = 154). Auto-generated tool names are
  verbose (`dashboard_summary_api_v1_dashboard_summary_get`); a naming-cleanup pass (`mcp_names` /
  `mcp_component_fn`) is recommended to improve LLM tool-selection.
