# NOC on-charger control-center Tier-1 tools — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 13 curated, read-only per-charger "control-center" tools to `mcp-server` that wrap the existing path-based charger proxy, giving named tools for the highest-value on-charger triage reads.

**Architecture:** A new `noc_mcp/control_center.py` holds `_proxy_get` (one internal GET to `/api/v1/chargers/{charger_id}/proxy/{port}/{path}`, returning device JSON or a structured `{"error": …}`) and `_register_control_center_tools(mcp, client)`, which registers the 13 `@mcp.tool` wrappers (mirroring the existing `_register_control_center_catalog`). `build_server` gains an optional `client` param and calls the new registrar. Responses are passed through; composites merge a few endpoints under fixed keys, best-effort.

**Tech Stack:** Python, FastMCP 3.4.4 (`FastMCP.from_openapi` + hand-registered tools), httpx (MockTransport for tests), pytest (`asyncio_mode = auto`).

**Spec:** `docs/superpowers/specs/2026-07-12-noc-control-center-tools-design.md`

**Run tests from `mcp-server/` with env vars set** (config reads creds at import; there is no `.env`):
`NOC_EMAIL=x NOC_PASSWORD=x ../.venv/Scripts/python.exe -m pytest -q`

---

## File structure

- **Modify** `mcp-server/noc_mcp/server.py` — add optional `client` param to `build_server`; call `_register_control_center_tools(mcp, client)`.
- **Create** `mcp-server/noc_mcp/control_center.py` — `_proxy_get` + `_register_control_center_tools` (13 wrappers).
- **Create** `mcp-server/tests/fake_noc.py` — `httpx.MockTransport` fake of the NOC (login + proxy route with canned device JSON) + `build_test_server()`.
- **Create** `mcp-server/tests/test_control_center.py` — unit test for `_proxy_get`; integration tests driving each tool via the in-memory FastMCP `Client`.
- **Modify** `mcp-server/README.md` — document the Tier-1 tools.

---

### Task 1: Make `build_server` injectable for tests

**Files:**
- Modify: `mcp-server/noc_mcp/server.py` (the `build_server` function)

- [ ] **Step 1: Add an optional client param and hoist the client into a variable**

Change:
```python
def build_server() -> FastMCP:
    mcp = FastMCP.from_openapi(
        openapi_spec=load_spec(),
        client=build_client(),
        name="QuenchNOC Assistant (read-only)",
        route_maps=_ROUTE_MAPS,
    )
    _register_control_center_catalog(mcp)
    mcp.add_middleware(ReadOnlyProxyMiddleware())
    return mcp
```
to:
```python
def build_server(client: NocAuthClient | None = None) -> FastMCP:
    client = client or build_client()
    mcp = FastMCP.from_openapi(
        openapi_spec=load_spec(),
        client=client,
        name="QuenchNOC Assistant (read-only)",
        route_maps=_ROUTE_MAPS,
    )
    _register_control_center_catalog(mcp)
    mcp.add_middleware(ReadOnlyProxyMiddleware())
    return mcp
```

- [ ] **Step 2: Run the existing suite to confirm no regression**

Run: `NOC_EMAIL=x NOC_PASSWORD=x ../.venv/Scripts/python.exe -m pytest -q`
Expected: existing `test_auth.py` + `test_readonly.py` tests PASS.

- [ ] **Step 3: Commit**

```bash
git add noc_mcp/server.py
git commit -m "refactor(noc): allow injecting a client into build_server for tests"
```

---

### Task 2: `control_center.py` with `_proxy_get` + registrar skeleton, wired in

**Files:**
- Create: `mcp-server/noc_mcp/control_center.py`
- Modify: `mcp-server/noc_mcp/server.py`
- Test: `mcp-server/tests/test_control_center.py`

- [ ] **Step 1: Write the failing unit test for `_proxy_get`**

Create `tests/test_control_center.py`:
```python
"""Tests for the on-charger control-center tools (noc_mcp.control_center)."""

import httpx
import pytest

from noc_mcp.auth import NocAuthClient
from noc_mcp.control_center import _proxy_get

_LOGIN = "/api/v1/auth/login"


def _login(request):
    return httpx.Response(200, json={"access_token": "t.t.t", "token_type": "bearer"})


def _client(handler):
    return NocAuthClient(base_url="http://noc.test", email="e", password="p",
                         transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_proxy_get_returns_device_json():
    def handler(request):
        if request.url.path.endswith(_LOGIN):
            return _login(request)
        # path-based proxy: /api/v1/chargers/CHG-1/proxy/8003/api/v1/charging/status
        assert "/proxy/8003/api/v1/charging/status" in request.url.path
        return httpx.Response(200, json={"guns": [{"gun_id": 1, "connected": True}]})

    async with _client(handler) as c:
        out = await _proxy_get(c, "CHG-1", 8003, "api/v1/charging/status")
    assert out == {"guns": [{"gun_id": 1, "connected": True}]}


@pytest.mark.asyncio
async def test_proxy_get_wraps_non_dict_json():
    def handler(request):
        if request.url.path.endswith(_LOGIN):
            return _login(request)
        return httpx.Response(200, json=["a", "b"])

    async with _client(handler) as c:
        out = await _proxy_get(c, "CHG-1", 8005, "api/v1/devtools/logs/x")
    assert out == {"data": ["a", "b"]}


@pytest.mark.asyncio
async def test_proxy_get_returns_error_on_offline():
    def handler(request):
        if request.url.path.endswith(_LOGIN):
            return _login(request)
        return httpx.Response(502, json={"detail": "charger offline"})

    async with _client(handler) as c:
        out = await _proxy_get(c, "OFFLINE", 8003, "api/v1/charging/status")
    assert "error" in out and out["port"] == 8003 and out["path"] == "api/v1/charging/status"
```

- [ ] **Step 2: Run to verify it fails**

Run: `NOC_EMAIL=x NOC_PASSWORD=x ../.venv/Scripts/python.exe -m pytest tests/test_control_center.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_mcp.control_center'`.

- [ ] **Step 3: Create `control_center.py` with `_proxy_get` and an empty registrar**

Create `noc_mcp/control_center.py`:
```python
"""Curated read-only per-charger 'control-center' tools.

Thin wrappers over the NOC path-based charger proxy
(``GET /api/v1/chargers/{charger_id}/proxy/{port}/{path}``) for the highest-value on-charger
triage reads. The device APIs already return lean JSON, so responses are passed through;
composites merge a few related endpoints under fixed keys (best-effort). All calls are GET —
read-only. The long tail of ~110 endpoints stays reachable via ``get_control_center_api_catalog``
plus the generic proxy.
"""

from __future__ import annotations

from typing import Optional


async def _proxy_get(client, charger_id: str, port: int, path: str) -> dict:
    """GET one on-charger endpoint through the NOC proxy; return device JSON.

    On any failure (offline charger, non-2xx, transport error, non-JSON) returns a structured
    ``{"error", "port", "path"}`` instead of raising, so composites degrade to partial results.
    Non-dict JSON (e.g. a top-level list) is wrapped as ``{"data": <value>}``.
    """
    url = f"/api/v1/chargers/{charger_id}/proxy/{port}/{path}"
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {"data": data}
    except Exception as e:  # noqa: BLE001 - surface any failure as a clean error payload
        return {"error": f"{type(e).__name__}: {e}", "port": port, "path": path}


def _register_control_center_tools(mcp, client) -> None:
    """Register the curated Tier-1 on-charger tools onto ``mcp``."""
    # tools are added in later tasks
    return None
```

- [ ] **Step 4: Wire the registrar into `build_server`**

In `noc_mcp/server.py`, add the import near the other `noc_mcp` imports:
```python
from .control_center import _register_control_center_tools
```
and call it in `build_server` right after the catalog registration:
```python
    _register_control_center_catalog(mcp)
    _register_control_center_tools(mcp, client)
    mcp.add_middleware(ReadOnlyProxyMiddleware())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `NOC_EMAIL=x NOC_PASSWORD=x ../.venv/Scripts/python.exe -m pytest tests/test_control_center.py -q`
Expected: the three `_proxy_get` tests PASS.

- [ ] **Step 6: Commit**

```bash
git add noc_mcp/control_center.py noc_mcp/server.py tests/test_control_center.py
git commit -m "feat(noc): add control_center module with proxy-GET helper, wired into build_server"
```

---

### Task 3: Shared fake NOC + first tool `charger_get_charging_status`

**Files:**
- Create: `mcp-server/tests/fake_noc.py`
- Modify: `mcp-server/noc_mcp/control_center.py`
- Test: `mcp-server/tests/test_control_center.py`

- [ ] **Step 1: Create the shared fake NOC harness**

Create `tests/fake_noc.py`:
```python
"""Shared httpx.MockTransport fake of the NOC backend for control-center tool tests.

Handles login + the path-based charger proxy, returning canned lean device JSON keyed by
(port, path). Charger id 'OFFLINE' makes the proxy return 502 (for the error path).
"""

import httpx

from noc_mcp.auth import NocAuthClient
from noc_mcp.server import build_server

_LOGIN = "/api/v1/auth/login"

# Canned device responses keyed by (port, proxy-path-without-leading-slash).
_DEVICE = {
    (8003, "api/v1/charging/status"): {"guns": [{"gun_id": 1, "connected": True, "plc_state": "C"}]},
    (8003, "api/v1/charging/status/1"): {"gun_id": 1, "connected": True, "plc_state": "C"},
    (8003, "api/v1/session_details/active/1"): {"session_id": "S1", "energy_kwh": 3.2},
    (8003, "api/v1/session_details/1"): {"session_id": "S0", "stop_reason": "EVDisconnected"},
    (8003, "api/v1/session_details/history"): {"sessions": [{"session_id": "S0"}]},
    (8003, "api/v1/devices/meters"): {"ac_meter": {"v": 230}, "dc_meters": [{"gun_id": 1, "v": 400}]},
    (8003, "api/v1/devices/imd/1"): {"gun_id": 1, "resistance_kohm": 999},
    (8003, "api/v1/ocpp/status"): {"connected": True, "url": "wss://ocpp"},
    (8003, "api/v1/config/ocpp"): {"HeartbeatInterval": "300"},
    (8000, "api/v1/network/status"): {"active_interface": "gsm", "lan": {"up": False}},
    (8000, "api/v1/network/gsm/status"): {"rssi": -71, "operator": "Jio"},
    (8000, "api/v1/system/hardware"): {"model": "QFlex", "serial": "SN1"},
    (8000, "api/v1/system/software"): {"version": "1.2.3"},
    (8000, "api/v1/system/rtc"): {"datetime": "2026-07-12T06:00:00Z"},
    (8002, "api/state"): {"state": {"guns": 2, "allocated_kw": 60}},
    (8002, "api/pm_count"): {"pm_count": 4},
    (8002, "api/pm/get_all_power"): {"modules": [{"id": 1, "kw": 15}]},
    (8006, "error-generator/alarms/latched"): {"latched": [{"vendor_error_code": "E12", "gun_id": 1}]},
    (8006, "error-generator/errors/pending"): {"pending": []},
    (8006, "error-generator/errors/by-gun/1"): {"errors": [{"code": "E12"}]},
    (8006, "temp/sensors"): {"sensors": [{"name": "inlet", "c": 41}]},
    (8006, "temp/thresholds"): {"thresholds": {"inlet": 70}},
    (8005, "api/v1/devtools/logs/sources"): {"sources": ["app", "ocpp"]},
    (8005, "api/v1/devtools/logs/app"): {"data": ["line1", "line2"]},
    (8005, "api/v1/devtools/metrics"): {"cpu": {"pct": 12}, "memory": {"pct": 40}, "disk": {"pct": 55}},
}


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith(_LOGIN):
        return httpx.Response(200, json={"access_token": "t.t.t", "token_type": "bearer"})
    marker = "/proxy/"
    if marker in path:
        charger = path.split("/proxy/")[0].rsplit("/", 1)[-1]
        rest = path.split("/proxy/", 1)[1]          # "<port>/<device path>"
        port_str, _, device_path = rest.partition("/")
        if charger == "OFFLINE":
            return httpx.Response(502, json={"detail": "charger offline"})
        payload = _DEVICE.get((int(port_str), device_path))
        if payload is None:
            return httpx.Response(404, json={"detail": f"no route {port_str}/{device_path}"})
        return httpx.Response(200, json=payload)
    return httpx.Response(404, json={"detail": "unhandled"})


def build_test_server():
    """FastMCP server wired to the fake NOC (no network)."""
    client = NocAuthClient(base_url="http://noc.test", email="e", password="p",
                           transport=httpx.MockTransport(_handler))
    return build_server(client)
```

- [ ] **Step 2: Write the failing integration test**

Append to `tests/test_control_center.py`:
```python
from fastmcp import Client
from tests.fake_noc import build_test_server


@pytest.mark.asyncio
async def test_charging_status_fleet_and_per_gun():
    async with Client(build_test_server()) as client:
        fleet = await client.call_tool("charger_get_charging_status", {"charger_id": "CHG-1"})
        gun = await client.call_tool(
            "charger_get_charging_status", {"charger_id": "CHG-1", "gun_id": 1})
    assert fleet.data == {"guns": [{"gun_id": 1, "connected": True, "plc_state": "C"}]}
    assert gun.data == {"gun_id": 1, "connected": True, "plc_state": "C"}


@pytest.mark.asyncio
async def test_charging_status_offline_returns_error():
    async with Client(build_test_server()) as client:
        res = await client.call_tool("charger_get_charging_status", {"charger_id": "OFFLINE"})
    assert "error" in res.data and res.data["port"] == 8003
```

- [ ] **Step 3: Run to verify it fails**

Run: `NOC_EMAIL=x NOC_PASSWORD=x ../.venv/Scripts/python.exe -m pytest tests/test_control_center.py -k charging_status -q`
Expected: FAIL — tool `charger_get_charging_status` not found.

- [ ] **Step 4: Register the first tool**

In `control_center.py`, replace the body of `_register_control_center_tools` with:
```python
def _register_control_center_tools(mcp, client) -> None:
    """Register the curated Tier-1 on-charger tools onto ``mcp``."""

    @mcp.tool
    async def charger_get_charging_status(charger_id: str, gun_id: Optional[int] = None) -> dict:
        """Live charging status for a charger (by NOC charger_id): per-gun connected/PLC/OCPP
        state. Pass gun_id for a single gun. The charger must be ONLINE. Read-only."""
        path = ("api/v1/charging/status" if gun_id is None
                else f"api/v1/charging/status/{gun_id}")
        return await _proxy_get(client, charger_id, 8003, path)
```

- [ ] **Step 5: Run to verify it passes**

Run: `NOC_EMAIL=x NOC_PASSWORD=x ../.venv/Scripts/python.exe -m pytest tests/test_control_center.py -q`
Expected: PASS (proxy_get unit tests + charging_status tests).

- [ ] **Step 6: Commit**

```bash
git add noc_mcp/control_center.py tests/fake_noc.py tests/test_control_center.py
git commit -m "feat(noc): add charger_get_charging_status tool + shared fake NOC harness"
```

---

### Task 4: Register the remaining 12 tool functions

**Files:**
- Modify: `mcp-server/noc_mcp/control_center.py`
- Test: `mcp-server/tests/test_control_center.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_control_center.py`:
```python
@pytest.mark.asyncio
async def test_all_control_center_tools_registered():
    async with Client(build_test_server()) as client:
        names = {t.name for t in await client.list_tools()}
    expected = {
        "charger_get_charging_status", "charger_get_session_details", "charger_get_meters",
        "charger_get_ocpp_status", "charger_get_network_status", "charger_get_system_info",
        "charger_get_allocation_state", "charger_get_pm_power", "charger_get_alarms",
        "charger_get_temperature", "charger_list_log_sources", "charger_get_logs",
        "charger_get_device_metrics",
    }
    assert expected <= names


@pytest.mark.asyncio
async def test_composites_merge_under_fixed_keys():
    async with Client(build_test_server()) as c:
        ocpp = (await c.call_tool("charger_get_ocpp_status", {"charger_id": "CHG-1"})).data
        net = (await c.call_tool("charger_get_network_status", {"charger_id": "CHG-1"})).data
        sysinfo = (await c.call_tool("charger_get_system_info", {"charger_id": "CHG-1"})).data
        pm = (await c.call_tool("charger_get_pm_power", {"charger_id": "CHG-1"})).data
        temp = (await c.call_tool("charger_get_temperature", {"charger_id": "CHG-1"})).data
    assert ocpp == {"status": {"connected": True, "url": "wss://ocpp"},
                    "config": {"HeartbeatInterval": "300"}}
    assert set(net) == {"overall", "gsm"} and net["gsm"] == {"rssi": -71, "operator": "Jio"}
    assert set(sysinfo) == {"hardware", "software", "rtc"}
    assert pm == {"pm_count": {"pm_count": 4}, "power": {"modules": [{"id": 1, "kw": 15}]}}
    assert set(temp) == {"sensors", "thresholds"}


@pytest.mark.asyncio
async def test_session_details_gun_vs_history():
    async with Client(build_test_server()) as c:
        gun = (await c.call_tool(
            "charger_get_session_details", {"charger_id": "CHG-1", "gun_id": 1})).data
        hist = (await c.call_tool("charger_get_session_details", {"charger_id": "CHG-1"})).data
    assert set(gun) == {"active", "last"} and gun["active"] == {"session_id": "S1", "energy_kwh": 3.2}
    assert set(hist) == {"history"}


@pytest.mark.asyncio
async def test_meters_and_imd_merge():
    async with Client(build_test_server()) as c:
        base = (await c.call_tool("charger_get_meters", {"charger_id": "CHG-1"})).data
        with_gun = (await c.call_tool(
            "charger_get_meters", {"charger_id": "CHG-1", "gun_id": 1})).data
    assert set(base) == {"ac_meter", "dc_meters"}
    assert with_gun["imd"] == {"gun_id": 1, "resistance_kohm": 999}


@pytest.mark.asyncio
async def test_alarms_fleet_vs_gun():
    async with Client(build_test_server()) as c:
        fleet = (await c.call_tool("charger_get_alarms", {"charger_id": "CHG-1"})).data
        gun = (await c.call_tool(
            "charger_get_alarms", {"charger_id": "CHG-1", "gun_id": 1})).data
    assert set(fleet) == {"latched", "pending"}
    assert set(gun) == {"errors_by_gun"}


@pytest.mark.asyncio
async def test_logs_pair_and_metrics():
    async with Client(build_test_server()) as c:
        sources = (await c.call_tool("charger_list_log_sources", {"charger_id": "CHG-1"})).data
        logs = (await c.call_tool(
            "charger_get_logs", {"charger_id": "CHG-1", "source": "app"})).data
        metrics = (await c.call_tool("charger_get_device_metrics", {"charger_id": "CHG-1"})).data
        alloc = (await c.call_tool("charger_get_allocation_state", {"charger_id": "CHG-1"})).data
    assert sources == {"sources": ["app", "ocpp"]}
    assert logs == {"data": ["line1", "line2"]}
    assert set(metrics) == {"cpu", "memory", "disk"}
    assert alloc == {"state": {"guns": 2, "allocated_kw": 60}}
```

- [ ] **Step 2: Run to verify it fails**

Run: `NOC_EMAIL=x NOC_PASSWORD=x ../.venv/Scripts/python.exe -m pytest tests/test_control_center.py -q`
Expected: FAIL — the new tools are not registered.

- [ ] **Step 3: Add the remaining 12 tool functions**

In `control_center.py`, inside `_register_control_center_tools` (after `charger_get_charging_status`), add:
```python
    @mcp.tool
    async def charger_get_session_details(charger_id: str, gun_id: Optional[int] = None) -> dict:
        """Charging-session details for a charger. With gun_id: that gun's active + last
        session. Without: recent session history. Charger must be ONLINE. Read-only."""
        if gun_id is not None:
            return {
                "active": await _proxy_get(client, charger_id, 8003, f"api/v1/session_details/active/{gun_id}"),
                "last": await _proxy_get(client, charger_id, 8003, f"api/v1/session_details/{gun_id}"),
            }
        return {"history": await _proxy_get(client, charger_id, 8003, "api/v1/session_details/history")}

    @mcp.tool
    async def charger_get_meters(charger_id: str, gun_id: Optional[int] = None) -> dict:
        """Live meter data (AC + per-gun DC). Pass gun_id to also include that gun's IMD
        (insulation) status. Charger must be ONLINE. Read-only."""
        data = await _proxy_get(client, charger_id, 8003, "api/v1/devices/meters")
        if gun_id is not None:
            data = dict(data)
            data["imd"] = await _proxy_get(client, charger_id, 8003, f"api/v1/devices/imd/{gun_id}")
        return data

    @mcp.tool
    async def charger_get_ocpp_status(charger_id: str) -> dict:
        """OCPP connection status + the charger's OCPP configuration keys. Read-only."""
        return {
            "status": await _proxy_get(client, charger_id, 8003, "api/v1/ocpp/status"),
            "config": await _proxy_get(client, charger_id, 8003, "api/v1/config/ocpp"),
        }

    @mcp.tool
    async def charger_get_network_status(charger_id: str) -> dict:
        """Connectivity triage: overall network status + GSM signal/operator detail. Read-only."""
        return {
            "overall": await _proxy_get(client, charger_id, 8000, "api/v1/network/status"),
            "gsm": await _proxy_get(client, charger_id, 8000, "api/v1/network/gsm/status"),
        }

    @mcp.tool
    async def charger_get_system_info(charger_id: str) -> dict:
        """Device identity: hardware, software/firmware version, and RTC clock. Read-only."""
        return {
            "hardware": await _proxy_get(client, charger_id, 8000, "api/v1/system/hardware"),
            "software": await _proxy_get(client, charger_id, 8000, "api/v1/system/software"),
            "rtc": await _proxy_get(client, charger_id, 8000, "api/v1/system/rtc"),
        }

    @mcp.tool
    async def charger_get_allocation_state(charger_id: str) -> dict:
        """Current power-allocation state (per-gun allocation). Read-only."""
        return await _proxy_get(client, charger_id, 8002, "api/state")

    @mcp.tool
    async def charger_get_pm_power(charger_id: str) -> dict:
        """Power-module output: module count + per-module power. Read-only."""
        return {
            "pm_count": await _proxy_get(client, charger_id, 8002, "api/pm_count"),
            "power": await _proxy_get(client, charger_id, 8002, "api/pm/get_all_power"),
        }

    @mcp.tool
    async def charger_get_alarms(charger_id: str, gun_id: Optional[int] = None) -> dict:
        """Active faults. With gun_id: errors for that gun. Without: latched alarms + pending
        errors across the charger. Read-only."""
        if gun_id is not None:
            return {"errors_by_gun": await _proxy_get(
                client, charger_id, 8006, f"error-generator/errors/by-gun/{gun_id}")}
        return {
            "latched": await _proxy_get(client, charger_id, 8006, "error-generator/alarms/latched"),
            "pending": await _proxy_get(client, charger_id, 8006, "error-generator/errors/pending"),
        }

    @mcp.tool
    async def charger_get_temperature(charger_id: str) -> dict:
        """Thermal state: sensor readings + configured thresholds. Read-only."""
        return {
            "sensors": await _proxy_get(client, charger_id, 8006, "temp/sensors"),
            "thresholds": await _proxy_get(client, charger_id, 8006, "temp/thresholds"),
        }

    @mcp.tool
    async def charger_list_log_sources(charger_id: str) -> dict:
        """List the on-charger log sources available to fetch (use with charger_get_logs).
        Requires an admin-level NOC service account. Read-only."""
        return await _proxy_get(client, charger_id, 8005, "api/v1/devtools/logs/sources")

    @mcp.tool
    async def charger_get_logs(charger_id: str, source: str) -> dict:
        """Fetch on-charger logs for one source (from charger_list_log_sources). Requires an
        admin-level NOC service account. Read-only."""
        return await _proxy_get(client, charger_id, 8005, f"api/v1/devtools/logs/{source}")

    @mcp.tool
    async def charger_get_device_metrics(charger_id: str) -> dict:
        """On-charger compute health: CPU / memory / disk metrics. Requires an admin-level NOC
        service account. Read-only."""
        return await _proxy_get(client, charger_id, 8005, "api/v1/devtools/metrics")
```

- [ ] **Step 4: Run to verify all pass**

Run: `NOC_EMAIL=x NOC_PASSWORD=x ../.venv/Scripts/python.exe -m pytest -q`
Expected: all PASS (auth + readonly + control_center).

- [ ] **Step 5: Commit**

```bash
git add noc_mcp/control_center.py tests/test_control_center.py
git commit -m "feat(noc): add the remaining 12 Tier-1 control-center tools"
```

---

### Task 5: Update README

**Files:**
- Modify: `mcp-server/README.md`

- [ ] **Step 1: Add a Tier-1 tools section**

After the existing "On-charger control-center catalog" section, add:
```markdown
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
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs(noc): document the 13 Tier-1 on-charger tools"
```

---

### Task 6: Version bump (HELD — do not push)

**Files:**
- (release scripts)

- [ ] **Step 1: Confirm the full suite is green**

Run: `NOC_EMAIL=x NOC_PASSWORD=x ../.venv/Scripts/python.exe -m pytest -q`
Expected: all PASS.

- [ ] **Step 2: Do NOT bump/tag/push in this session.**

Per the standing decision, the `feat/mcp-server` branch is shared with concurrent work and the
release is HELD. Leave the commits on the branch and report completion + tool count. A version
bump (`scripts/bump-version.ps1 minor`) and push happen only when the user coordinates the
release. See the CMS work's identical hold.

- [ ] **Step 3: Report** the new tool count (13) and that all tests pass, to the user.

---

## Self-review notes

- **Spec coverage:** injectable build_server (§3 → Task 1), `_proxy_get` incl. error + non-dict wrap (§3/§6 → Task 2), fake harness + first tool (§8 → Task 3), all 13 tools with composites/gun_id/logs-pair (§4 → Tasks 3–4), pass-through + merge keys asserted (§5 → Task 4 tests), offline error path (§6 → Tasks 2–3), README + held release (§10 → Tasks 5–6). Admin-role risk (§7) is surfaced in the logs/metrics docstrings + README and confirmed by the live smoke test (not automatable here).
- **Type consistency:** `_proxy_get(client, charger_id, port, path)` and `_register_control_center_tools(mcp, client)` signatures are used identically across tasks; every tool returns `dict`; composites use the exact keys asserted in the Task 4 tests (`status`/`config`, `overall`/`gsm`, `hardware`/`software`/`rtc`, `pm_count`/`power`, `sensors`/`thresholds`, `active`/`last`, `history`, `imd`, `latched`/`pending`, `errors_by_gun`).
- **Live smoke test** (spec §8) is intentionally manual (needs an online charger + real creds) and is tracked as a follow-up, not a plan task.
- **No placeholders**; every code step shows complete code.
