# NOC on-charger control-center Tier-1 tools — design

**Date:** 2026-07-12
**Status:** Proposed
**Author:** kmittal (with Claude)
**Component:** `mcp-server` (`noc_mcp` — read-only NOC MCP server)

## 1. Problem

The NOC server exposes 153 auto-generated GET tools plus two "meta" pieces for live
per-charger data: `get_control_center_api_catalog` (discovery of the 124 on-charger GET
endpoints) and the generic **charger proxy** (`/api/v1/chargers/{charger_id}/proxy/{port}/{path}`).
Today, reading any live device endpoint means the model must call the catalog, pick a
port+path, and drive the raw proxy. That works but is unergonomic: no named tools, no
composition of related endpoints, and the model must assemble the port/path itself.

We want a small set of **first-class, named, read-only tools** for the highest-value on-charger
triage reads — the "Tier-1" of a tiered-hybrid design.

## 2. Scope

**In scope:** ~12 curated read-only Tier-1 tools that wrap the existing path-based charger
proxy, each answering one NOC triage question, with device JSON passed through (composites
merge a few related endpoints).

**Already done — NOT in scope (Tier-2):** The generic proxy is already **GET-locked** by
`ReadOnlyProxyMiddleware` (`noc_mcp/readonly.py`), and `get_control_center_api_catalog`
already exposes all 124 endpoints. The long-tail ~110 endpoints stay reachable via
catalog + proxy. No changes there.

**Explicitly out of scope:** device writes (the 104 POST / 7 PUT / 4 DELETE control endpoints
in `rbac-rules.csv`) — not wrapped. All Tier-1 calls are GET.

## 3. Architecture

New module **`noc_mcp/control_center.py`** (keeps `server.py` lean), mirroring the existing
`_register_control_center_catalog` pattern already in `server.py`.

- **`_proxy_get(client, charger_id, port, path)`** — issue `GET
  /api/v1/chargers/{charger_id}/proxy/{port}/{path}` via the shared `NocAuthClient`, return the
  device JSON. On any failure (charger offline, non-2xx from the proxy, transport error,
  non-JSON body) it returns a structured `{"error": <msg>, "port": <port>, "path": <path>}`
  instead of raising — so composites degrade gracefully to partial results.
  ```python
  async def _proxy_get(client, charger_id, port, path):
      url = f"/api/v1/chargers/{charger_id}/proxy/{port}/{path}"
      try:
          resp = await client.get(url)
          resp.raise_for_status()
          return resp.json()
      except Exception as e:
          return {"error": f"{type(e).__name__}: {e}", "port": port, "path": path}
  ```
  Note: `path` carries embedded slashes (e.g. `api/v1/charging/status`); the proxy route uses a
  path converter, so the URL is built by direct string interpolation.

- **`_register_control_center_tools(mcp, client)`** — registers the ~12 `@mcp.tool` wrappers
  below. Called from `build_server()` with one added line, right after
  `_register_control_center_catalog(mcp)`:
  ```python
  _register_control_center_tools(mcp, client)   # client is the NocAuthClient already built
  ```
  (`build_server` already holds the `NocAuthClient` via `build_client()`; pass it through.)

- **Read-only guarantee:** every call is a GET to the proxy; no method/body params are exposed
  on these tools. (`ReadOnlyProxyMiddleware` additionally guards the generic proxy tool, but
  these wrappers are GET-only by construction.)

- **Charger identifier:** the NOC `charger_id` — the same id the other NOC tools use. No
  resolution step (unlike the CMS QChargerID work).

## 4. The 12 Tier-1 tools

All take `charger_id: str`; some take `gun_id: int | None = None`. All return the device JSON
**pass-through**. Composites issue several `_proxy_get` calls and merge under fixed keys
(best-effort: each key holds either the data or an `{"error": ...}`). Proxy `path` values omit
the leading slash, per the catalog's `how_to_call`.

| Tool | Port | Proxy path(s) → return shape |
|---|---|---|
| `charger_get_charging_status(charger_id, gun_id=None)` | 8003 | `api/v1/charging/status` — or `api/v1/charging/status/{gun_id}` if `gun_id` set |
| `charger_get_session_details(charger_id, gun_id=None)` | 8003 | `gun_id` set → `{"active": api/v1/session_details/active/{gun_id}, "last": api/v1/session_details/{gun_id}}`; else `{"history": api/v1/session_details/history}` |
| `charger_get_meters(charger_id, gun_id=None)` | 8003 | `api/v1/devices/meters` (already AC+DC); if `gun_id` set also merge `{"imd": api/v1/devices/imd/{gun_id}}` |
| `charger_get_ocpp_status(charger_id)` | 8003 | `{"status": api/v1/ocpp/status, "config": api/v1/config/ocpp}` |
| `charger_get_network_status(charger_id)` | 8000 | `{"overall": api/v1/network/status, "gsm": api/v1/network/gsm/status}` |
| `charger_get_system_info(charger_id)` | 8000 | `{"hardware": api/v1/system/hardware, "software": api/v1/system/software, "rtc": api/v1/system/rtc}` |
| `charger_get_allocation_state(charger_id)` | 8002 | `api/state` |
| `charger_get_pm_power(charger_id)` | 8002 | `{"pm_count": api/pm_count, "power": api/pm/get_all_power}` |
| `charger_get_alarms(charger_id, gun_id=None)` | 8006 | `gun_id` set → `{"errors_by_gun": error-generator/errors/by-gun/{gun_id}}`; else `{"latched": error-generator/alarms/latched, "pending": error-generator/errors/pending}` |
| `charger_get_temperature(charger_id)` | 8006 | `{"sensors": temp/sensors, "thresholds": temp/thresholds}` |
| `charger_list_log_sources(charger_id)` | 8005 | `api/v1/devtools/logs/sources` |
| `charger_get_logs(charger_id, source)` | 8005 | `api/v1/devtools/logs/{source}` |
| `charger_get_device_metrics(charger_id)` | 8005 | `api/v1/devtools/metrics` (all CPU/mem/disk) |

That is 13 tool functions (the logs pair counts as two); "~12" tools overall.

Each tool's docstring states: what it reads, that the charger must be **online**, and that it
is **read-only**. Names use the `charger_*` prefix for clean LLM tool-selection (the
auto-generated NOC tools are verbose by contrast).

## 5. Response handling

**Pass-through.** The device APIs return lean, purpose-built JSON, so no field-whitelisting —
nothing useful is dropped. Single-endpoint tools return the device JSON verbatim; composite
tools return a dict keyed by sub-part. A failed sub-call surfaces as `{"error": ...}` under its
key rather than failing the whole tool.

## 6. Error handling

- **Charger offline / unreachable / proxy non-2xx / non-JSON:** `_proxy_get` returns
  `{"error": ..., "port", "path"}`; the model relays a clear message instead of a stack trace.
- **Single-endpoint tools** return that error dict directly; **composites** return partial
  results with per-key errors.

## 7. Role / permission risk

The `:8005` dev-tools endpoints (`charger_list_log_sources`, `charger_get_logs`,
`charger_get_device_metrics`) are **admin**-gated in `rbac-rules.csv`; the rest are
customer/operator level. If the NOC service account lacks the admin role, those three tools
will get a 403 (surfaced as an `{"error": ...}` by §6). The live smoke test (§8) confirms
coverage; if 403, document that logs/metrics require an admin service account. Build all
thirteen regardless — they are valid and degrade gracefully.

## 8. Testing

- **`tests/fake_noc.py`** — shared `httpx.MockTransport` fake of the NOC: handles login and the
  path-based proxy route, returning canned lean device JSON keyed by `(port, path)`; also a case
  simulating an offline charger (proxy 502/timeout) for the error path. Plus `build_test_server()`
  wiring a `NocAuthClient` (MockTransport) into `build_server`'s registration. Note: `build_server`
  currently takes no client; add an optional `client` param (mirroring the CMS change) so tests
  can inject the fake without hitting the network.
- **`tests/test_control_center.py`** — drive each tool through the in-memory FastMCP `Client`
  (`res = await client.call_tool(name, args); res.data`): assert pass-through for single-endpoint
  tools, correct composite merge keys, `gun_id` path selection, the logs pair, and the
  offline-error path (`{"error": ...}`).
- **Live smoke test (manual, gated on an online charger):** run the server against the real NOC
  (backend is up) and call `charger_get_charging_status` + `charger_get_network_status` for a
  known-online charger; confirm real device JSON, and check whether the admin-gated logs/metrics
  tools return data or 403 for the service account (§7).
- **Success criteria:** all 13 tools return device JSON (or a clean `{"error": ...}`) for a valid
  charger; composites merge under the documented keys; no tool exposes a write path; the offline
  path returns a structured error, not an exception.

## 9. File structure & size

- **Create** `noc_mcp/control_center.py` (~200–300 lines: `_proxy_get` + `_register_control_center_tools`
  with 13 wrappers) — well under the 2000-line limit.
- **Modify** `noc_mcp/server.py`: add optional `client` param to `build_server`; call
  `_register_control_center_tools(mcp, client)`. (Keep the existing catalog registration.)
- **Create** `tests/fake_noc.py`, `tests/test_control_center.py`.
- **Modify** `mcp-server/README.md`: add a short "Tier-1 on-charger tools" section.

## 10. Docs / release

- README: document the 13 Tier-1 tools and note Tier-2 (catalog + GET-locked proxy) already
  covers the long tail.
- Version bump per team convention on completion; **release handling per the same hold** as the
  CMS work (shared branch) — do not push without coordinating.
