# CMS ChargerSession per-charger tools — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 5 read-only per-charger MCP tools (details, sessions, alerts, action-history, action-catalog) to `cms-mcp-server`, keyed by the human-facing `QChargerID`.

**Architecture:** Follow the existing `server.py` pattern (call a legacy MVC JSON action → `_trim` the god-DTO to a whitelist). Add module-level, unit-testable helpers: a POST wrapper, a DataTables body builder, and a `QChargerID → numeric ChargerID` resolver that caches the fleet map from `GetChargerByCustomerIDForDD` (`CustomerID=0`, all 1079 chargers). Tools are `@mcp.tool` closures inside `build_server`, which gains an optional `client` param so tests can inject an `httpx.MockTransport`-backed `CmsAuthClient`.

**Tech Stack:** Python, FastMCP 3.4.4, httpx (MockTransport for tests), pytest (`asyncio_mode = auto`).

**Spec:** `docs/superpowers/specs/2026-07-11-cms-charger-session-tools-design.md`

**Run tests with:** `python -m pytest` from `cms-mcp-server/` (the `pytest` console script mis-collects in this repo — use `python -m pytest`).

---

## File structure

- **Modify** `cms-mcp-server/cms_mcp/server.py` — add helpers, whitelists, resolver, 5 tools; add optional `client` param to `build_server`.
- **Create** `cms-mcp-server/tests/fake_cms.py` — shared `httpx.MockTransport` handler simulating login + all new endpoints, plus a `build_test_server()` helper.
- **Create** `cms-mcp-server/tests/test_helpers.py` — unit tests for `_post_json`, `_datatables_body`, `_resolve_charger_id`.
- **Create** `cms-mcp-server/tests/test_tools.py` — integration tests driving each tool via the in-memory FastMCP `Client`.
- **Modify** `cms-mcp-server/README.md` — document the 5 new tools.

---

### Task 1: Make `build_server` injectable for tests

**Files:**
- Modify: `cms-mcp-server/cms_mcp/server.py:54-56`

- [ ] **Step 1: Change the signature and client acquisition**

In `server.py`, change:
```python
def build_server() -> FastMCP:
    mcp = FastMCP(name="Legacy CMS (old NOC, read-only)")
    client = build_client()
```
to:
```python
def build_server(client: CmsAuthClient | None = None) -> FastMCP:
    mcp = FastMCP(name="Legacy CMS (old NOC, read-only)")
    client = client or build_client()
```

- [ ] **Step 2: Run the existing suite to confirm no regression**

Run: `python -m pytest -q`
Expected: existing `test_auth.py` tests PASS (2 passed).

- [ ] **Step 3: Commit**

```bash
git add cms_mcp/server.py
git commit -m "refactor(cms): allow injecting an httpx client into build_server for tests"
```

---

### Task 2: `_post_json` helper

Low-level POST that sends the AJAX header the legacy actions expect, supports both form (`data=`) and JSON (`json=`) bodies, and returns parsed JSON (or `[]` for an empty body). Mirrors the existing `_get_json` closure but is module-level so it is unit-testable and reusable by the resolver.

**Files:**
- Modify: `cms-mcp-server/cms_mcp/server.py` (add after `_trim`, ~line 43)
- Test: `cms-mcp-server/tests/test_helpers.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_helpers.py`:
```python
"""Unit tests for the per-charger helper functions in server.py."""

import httpx
import pytest

from cms_mcp.auth import CmsAuthClient
from cms_mcp.server import _post_json, _datatables_body

_LOGIN = "/Home/GetUsersByEmailAndPassword"


def _login_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200, json={"UserID": 1},
        headers={"set-cookie": ".ASPXAUTH=abc; path=/"},
    )


def _client(handler) -> CmsAuthClient:
    return CmsAuthClient(
        base_url="http://cms.test", user="u", password="p",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_post_json_sends_form_and_ajax_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(_LOGIN):
            return _login_ok(request)
        seen["xrw"] = request.headers.get("x-requested-with")
        seen["body"] = request.content.decode()
        return httpx.Response(200, json=[{"ChargerID": 5}])

    async with _client(handler) as c:
        out = await _post_json(c, "/ChargerDetails/GetChargerByCustomerIDForDD",
                               data={"CustomerID": "0"})
    assert out == [{"ChargerID": 5}]
    assert seen["xrw"] == "XMLHttpRequest"
    assert "CustomerID=0" in seen["body"]


@pytest.mark.asyncio
async def test_post_json_empty_body_returns_empty_list():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(_LOGIN):
            return _login_ok(request)
        return httpx.Response(200, text="")

    async with _client(handler) as c:
        out = await _post_json(c, "/x", json={"a": 1})
    assert out == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_helpers.py -q`
Expected: FAIL — `ImportError: cannot import name '_post_json'` (and `_datatables_body`).

- [ ] **Step 3: Write minimal implementation**

In `server.py`, add after the `_trim` function:
```python
async def _post_json(client, path, *, data=None, json=None):
    """POST to a legacy MVC action (form or JSON body) and return parsed JSON.

    Sends the ``X-Requested-With`` header the legacy AJAX actions require.
    Returns ``[]`` for an empty response body.
    """
    resp = await client.post(
        path, data=data, json=json,
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    resp.raise_for_status()
    if not resp.text.strip():
        return []
    return resp.json()
```

- [ ] **Step 4: Run test to verify `_post_json` tests pass**

Run: `python -m pytest tests/test_helpers.py -k post_json -q`
Expected: the two `post_json` tests PASS. (`_datatables_body` import still fails collection — fixed in Task 3.)

- [ ] **Step 5: Commit**

```bash
git add cms_mcp/server.py tests/test_helpers.py
git commit -m "feat(cms): add _post_json helper for legacy AJAX POST actions"
```

---

### Task 3: `_datatables_body` helper

Builds the minimal jQuery-DataTables server-side payload the session/alert/action-history actions expect: `lvm.ChargerID` (string) plus `start`/`length`/`limit`.

**Files:**
- Modify: `cms-mcp-server/cms_mcp/server.py`
- Test: `cms-mcp-server/tests/test_helpers.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_helpers.py`:
```python
def test_datatables_body_shape():
    body = _datatables_body(1429, 10)
    assert body["lvm"] == {"ChargerID": "1429"}   # numeric id, stringified
    assert body["limit"] == 10
    assert body["length"] == 10
    assert body["start"] == 0
    assert body["draw"] == 1
    assert body["columns"] == []
    assert body["order"] == []
    assert body["search"] == {"value": "", "regex": False}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_helpers.py::test_datatables_body_shape -q`
Expected: FAIL — `NameError`/`ImportError` for `_datatables_body`.

- [ ] **Step 3: Write minimal implementation**

In `server.py`, add near the other helpers:
```python
def _datatables_body(charger_id: int, limit: int) -> dict:
    """Minimal jQuery-DataTables server-side request body for the per-charger
    session/alert/action-history actions."""
    return {
        "draw": 1, "start": 0, "length": limit, "columns": [], "order": [],
        "search": {"value": "", "regex": False},
        "lvm": {"ChargerID": str(charger_id)}, "limit": limit,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_helpers.py -q`
Expected: all `test_helpers.py` tests so far PASS.

- [ ] **Step 5: Commit**

```bash
git add cms_mcp/server.py tests/test_helpers.py
git commit -m "feat(cms): add _datatables_body helper for per-charger log actions"
```

---

### Task 4: `_resolve_charger_id` resolver + cache

Resolves a human `QChargerID` to the numeric `ChargerID` via one cached call to `GetChargerByCustomerIDForDD` (`CustomerID=0` → all 1079). Case-insensitive. On a miss, refresh once (handles newly-commissioned chargers); if still missing, raise `ValueError` with up to 5 near-match suggestions.

**Files:**
- Modify: `cms-mcp-server/cms_mcp/server.py`
- Test: `cms-mcp-server/tests/test_helpers.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_helpers.py`:
```python
from cms_mcp.server import _resolve_charger_id


def _resolver_handler(counter):
    """MockTransport handler: login + GetChargerByCustomerIDForDD returns 2 chargers."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(_LOGIN):
            return _login_ok(request)
        if request.url.path.endswith("/GetChargerByCustomerIDForDD"):
            counter.append(1)
            return httpx.Response(200, json=[
                {"ChargerID": 1187, "QChargerID": "ab-sam-00585"},
                {"ChargerID": 1429, "QChargerID": "ab-sam-sim"},
            ])
        return httpx.Response(404)
    return handler


@pytest.mark.asyncio
async def test_resolve_hit_and_caches():
    calls = []
    async with _client(_resolver_handler(calls)) as c:
        cache = {}
        assert await _resolve_charger_id(c, "ab-sam-00585", cache) == 1187
        assert await _resolve_charger_id(c, "ab-sam-sim", cache) == 1429
    assert len(calls) == 1  # map fetched once, then served from cache


@pytest.mark.asyncio
async def test_resolve_is_case_insensitive():
    calls = []
    async with _client(_resolver_handler(calls)) as c:
        assert await _resolve_charger_id(c, "AB-SAM-SIM", {}) == 1429


@pytest.mark.asyncio
async def test_resolve_miss_refreshes_then_raises_with_suggestion():
    calls = []
    async with _client(_resolver_handler(calls)) as c:
        with pytest.raises(ValueError) as ei:
            await _resolve_charger_id(c, "ab-sam-9999", {})
    assert len(calls) == 2            # initial load + one refresh on miss
    assert "ab-sam-9999" in str(ei.value)
    assert "ab-sam" in str(ei.value)  # near-match suggestion included
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_helpers.py -k resolve -q`
Expected: FAIL — `ImportError: cannot import name '_resolve_charger_id'`.

- [ ] **Step 3: Write minimal implementation**

In `server.py`, add:
```python
async def _load_charger_map(client) -> dict[str, int]:
    """Fetch the fleet-wide QChargerID(lower) -> numeric ChargerID map."""
    rows = await _post_json(
        client, "/ChargerDetails/GetChargerByCustomerIDForDD",
        data={"CustomerID": "0"},
    )
    out = {}
    for r in rows:
        qid, cid = r.get("QChargerID"), r.get("ChargerID")
        if qid and cid:
            out[str(qid).lower()] = int(cid)
    return out


async def _resolve_charger_id(client, qcharger_id: str, cache: dict) -> int:
    """QChargerID -> numeric ChargerID (case-insensitive), with a cached fleet map.

    Refreshes the map once on a miss (new chargers); if still absent, raises
    ValueError listing up to 5 near matches.
    """
    key = str(qcharger_id).strip().lower()
    if not cache:
        cache.update(await _load_charger_map(client))
    if key in cache:
        return cache[key]
    # miss -> refresh once (charger may have been added since last load)
    cache.clear()
    cache.update(await _load_charger_map(client))
    if key in cache:
        return cache[key]
    stem = key.rsplit("-", 1)[0]
    near = sorted(k for k in cache if stem and stem in k)[:5]
    hint = f" Did you mean: {', '.join(near)}?" if near else ""
    raise ValueError(f"Unknown QChargerID '{qcharger_id}'.{hint}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_helpers.py -q`
Expected: all `test_helpers.py` tests PASS.

- [ ] **Step 5: Commit**

```bash
git add cms_mcp/server.py tests/test_helpers.py
git commit -m "feat(cms): add cached QChargerID -> numeric ChargerID resolver"
```

---

### Task 5: Shared fake CMS + `cms_get_charger_details` tool

Adds the shared test harness and the first tool. `cms_get_charger_details` resolves the id, calls `GetChargerDetailsByChargerID` **and** `GetChargerStatusByChargerID`, trims each to its whitelist, and merges them into one object.

**Files:**
- Create: `cms-mcp-server/tests/fake_cms.py`
- Modify: `cms-mcp-server/cms_mcp/server.py`
- Test: `cms-mcp-server/tests/test_tools.py`

- [ ] **Step 1: Create the shared fake CMS harness**

Create `tests/fake_cms.py`:
```python
"""Shared httpx.MockTransport fake of the legacy CMS for tool integration tests.

Simulates login + every per-charger endpoint the new tools call, keyed to the
sim charger ab-sam-sim (numeric ChargerID 1429).
"""

import httpx

from cms_mcp.auth import CmsAuthClient
from cms_mcp.server import build_server

_LOGIN = "/Home/GetUsersByEmailAndPassword"

# god-DTO rows with real-shaped noise fields the tools must trim away.
_CHARGERS = [
    {"ChargerID": 1429, "QChargerID": "ab-sam-sim"},
    {"ChargerID": 1187, "QChargerID": "ab-sam-00585"},
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
    "strEvent_Timestamp": "10/06/2026 12:55:09", "Name": "Ador",
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

_ROUTES = {
    "/ChargerDetails/GetChargerDetailsByChargerID": _DETAILS,
    "/ChargerDetails/GetChargerStatusByChargerID": _STATUS,
    "/ChargerDetails/GetSessionLogsByChargerID": _SESSIONS,
    "/ChargerDetails/GetAlertLogsByChargerID": _ALERTS,
    "/ChargerDetails/GetApiExecutionDetailsByID": _ACTION_HIST,
    "/ChargerDetails/GetAllApisByChargerID": _CATALOG,
}


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith(_LOGIN):
        return httpx.Response(200, json={"UserID": 1},
                              headers={"set-cookie": ".ASPXAUTH=abc; path=/"})
    if path.endswith("/GetChargerByCustomerIDForDD"):
        return httpx.Response(200, json=_CHARGERS)
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
```

- [ ] **Step 2: Write the failing integration test**

Create `tests/test_tools.py`:
```python
"""Integration tests: drive each tool through the in-memory FastMCP client."""

import pytest
from fastmcp import Client

from tests.fake_cms import build_test_server


@pytest.mark.asyncio
async def test_get_charger_details_merges_and_trims():
    async with Client(build_test_server()) as client:
        res = await client.call_tool(
            "cms_get_charger_details", {"qcharger_id": "ab-sam-sim"})
    d = res.data
    # merged metadata + live status
    assert d["QChargerID"] == "ab-sam-sim"
    assert d["Description"] == "Dual CCS"
    assert d["OCPPID"] == "noc_sim"
    assert d["Gun_A"] == 2
    # server filesystem-path noise trimmed out
    assert "StateLogPath" not in d
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_tools.py -q`
Expected: FAIL — tool `cms_get_charger_details` not found / not registered.

- [ ] **Step 4: Add whitelists + the tool**

In `server.py`, add these whitelists next to the existing ones (after `_CUSTOMER_FIELDS`):
```python
_CHG_DETAIL_FIELDS = [
    "QChargerID", "ChargerTypeID", "Description", "CapacityKW", "SerialNumber",
    "ControllerADescription", "ControllerBDescription", "Address", "StateName",
    "CountryName", "Name", "IsActive",
]
_CHG_STATUS_FIELDS = [
    "QChargerID", "OCPPID", "Latitude", "Longitude", "Gun_A", "Gun_B",
    "strEvent_Timestamp", "strPing_Timestamp",
]
```
Then inside `build_server`, after the client/`_get_json` setup, add the cache and the tool:
```python
    _charger_map: dict[str, int] = {}  # QChargerID(lower) -> numeric ChargerID

    @mcp.tool
    async def cms_get_charger_details(qcharger_id: str) -> dict:
        """Full profile for ONE charger in the old CMS, by its QChargerID
        (e.g. "ab-sam-00585"): static metadata (type, controllers, capacity,
        serial, address) merged with live status (OCPP id, location, gun states,
        last event/ping)."""
        cid = await _resolve_charger_id(client, qcharger_id, _charger_map)
        body = {"ChargerID": str(cid)}
        details = await _post_json(
            client, "/ChargerDetails/GetChargerDetailsByChargerID", data=body)
        status = await _post_json(
            client, "/ChargerDetails/GetChargerStatusByChargerID", data=body)
        d = _trim(details, _CHG_DETAIL_FIELDS)
        s = _trim(status, _CHG_STATUS_FIELDS)
        merged = {}
        if d:
            merged.update(d[0])
        if s:
            merged.update(s[0])
        return merged
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_tools.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add cms_mcp/server.py tests/fake_cms.py tests/test_tools.py
git commit -m "feat(cms): add cms_get_charger_details tool (metadata + live status)"
```

---

### Task 6: `cms_get_charger_sessions` tool

**Files:**
- Modify: `cms-mcp-server/cms_mcp/server.py`
- Test: `cms-mcp-server/tests/test_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tools.py`:
```python
@pytest.mark.asyncio
async def test_get_charger_sessions_trims_and_limits():
    async with Client(build_test_server()) as client:
        res = await client.call_tool(
            "cms_get_charger_sessions", {"qcharger_id": "ab-sam-sim", "limit": 5})
    rows = res.data
    assert isinstance(rows, list) and len(rows) == 1
    row = rows[0]
    assert row["Description"] == "SESSION_STOP"
    assert row["ChargerIDGun"] == "secc2300-21641"
    assert "StateLogPath" not in row   # filesystem-path noise dropped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tools.py::test_get_charger_sessions_trims_and_limits -q`
Expected: FAIL — tool not found.

- [ ] **Step 3: Add whitelist + the tool**

In `server.py`, add the whitelist:
```python
_SESSION_FIELDS = [
    "SessionEventID", "Description", "Outlet", "ChargerIDGun", "strStartTime",
    "Duration", "Energy", "SessionUserID", "SessionErrorID", "ErrorDesc",
    "strTimestamp",
]
```
Add inside `build_server`:
```python
    @mcp.tool
    async def cms_get_charger_sessions(qcharger_id: str, limit: int = 10) -> list[dict]:
        """Recent charging-session logs for ONE charger (by QChargerID) from the
        old CMS: session events, outlet/gun, start time, duration, energy, and any
        session error. Returned in the CMS default order (newest first)."""
        cid = await _resolve_charger_id(client, qcharger_id, _charger_map)
        resp = await _post_json(
            client, "/ChargerDetails/GetSessionLogsByChargerID",
            json=_datatables_body(cid, limit))
        rows = resp.get("data", []) if isinstance(resp, dict) else []
        return _trim(rows, _SESSION_FIELDS)[:limit]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tools.py -q`
Expected: PASS (all tool tests).

- [ ] **Step 5: Commit**

```bash
git add cms_mcp/server.py tests/test_tools.py
git commit -m "feat(cms): add cms_get_charger_sessions tool"
```

---

### Task 7: `cms_get_charger_alerts` tool

**Files:**
- Modify: `cms-mcp-server/cms_mcp/server.py`
- Test: `cms-mcp-server/tests/test_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tools.py`:
```python
@pytest.mark.asyncio
async def test_get_charger_alerts_trims():
    async with Client(build_test_server()) as client:
        res = await client.call_tool(
            "cms_get_charger_alerts", {"qcharger_id": "ab-sam-sim"})
    rows = res.data
    assert len(rows) == 1
    row = rows[0]
    assert row["Description"] == "ALERT"
    assert row["ErrorDesc"] == "NoError"
    assert row["Src"] == "stack"
    assert "FullStatePath" not in row
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tools.py::test_get_charger_alerts_trims -q`
Expected: FAIL — tool not found.

- [ ] **Step 3: Add whitelist + the tool**

In `server.py`, add the whitelist:
```python
_CHG_ALERT_FIELDS = [
    "SessionEventID", "Description", "Outlet", "ChargerIDGun", "Src",
    "SessionErrorID", "ErrorDesc", "strTimestamp",
]
```
Add inside `build_server`:
```python
    @mcp.tool
    async def cms_get_charger_alerts(qcharger_id: str, limit: int = 10) -> list[dict]:
        """Recent alert/fault logs for ONE charger (by QChargerID) from the old
        CMS: alert events with source, outlet/gun, error id + description, and
        timestamp. Newest first. (For a fleet-wide alert feed use
        cms_get_recent_alerts instead.)"""
        cid = await _resolve_charger_id(client, qcharger_id, _charger_map)
        resp = await _post_json(
            client, "/ChargerDetails/GetAlertLogsByChargerID",
            json=_datatables_body(cid, limit))
        rows = resp.get("data", []) if isinstance(resp, dict) else []
        return _trim(rows, _CHG_ALERT_FIELDS)[:limit]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tools.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cms_mcp/server.py tests/test_tools.py
git commit -m "feat(cms): add cms_get_charger_alerts tool"
```

---

### Task 8: `cms_get_charger_action_history` tool

**Files:**
- Modify: `cms-mcp-server/cms_mcp/server.py`
- Test: `cms-mcp-server/tests/test_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tools.py`:
```python
@pytest.mark.asyncio
async def test_get_charger_action_history_trims():
    async with Client(build_test_server()) as client:
        res = await client.call_tool(
            "cms_get_charger_action_history", {"qcharger_id": "ab-sam-sim"})
    rows = res.data
    assert len(rows) == 1
    row = rows[0]
    assert row["API"] == "id"
    assert row["ApiType"] == "GET"
    assert row["Description"] == "Executed"
    assert row["ExecutionID"] == 414827
    assert "Output" not in row   # server filesystem path dropped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tools.py::test_get_charger_action_history_trims -q`
Expected: FAIL — tool not found.

- [ ] **Step 3: Add whitelist + the tool**

In `server.py`, add the whitelist:
```python
_ACTION_HIST_FIELDS = [
    "Name", "API", "ApiType", "Description", "StatusID", "strExecutedTime",
    "ExecutionID", "strTimestamp",
]
```
Add inside `build_server`:
```python
    @mcp.tool
    async def cms_get_charger_action_history(
        qcharger_id: str, limit: int = 10
    ) -> list[dict]:
        """Recent remote-command execution history for ONE charger (by QChargerID)
        from the old CMS: which device API was run, its type, execution status,
        and when. Read-only history — this does NOT run any command."""
        cid = await _resolve_charger_id(client, qcharger_id, _charger_map)
        resp = await _post_json(
            client, "/ChargerDetails/GetApiExecutionDetailsByID",
            json=_datatables_body(cid, limit))
        rows = resp.get("data", []) if isinstance(resp, dict) else []
        return _trim(rows, _ACTION_HIST_FIELDS)[:limit]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tools.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cms_mcp/server.py tests/test_tools.py
git commit -m "feat(cms): add cms_get_charger_action_history tool"
```

---

### Task 9: `cms_list_charger_actions` tool (command catalog)

**Files:**
- Modify: `cms-mcp-server/cms_mcp/server.py`
- Test: `cms-mcp-server/tests/test_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tools.py`:
```python
@pytest.mark.asyncio
async def test_list_charger_actions_catalog():
    async with Client(build_test_server()) as client:
        res = await client.call_tool(
            "cms_list_charger_actions", {"qcharger_id": "ab-sam-sim"})
    rows = res.data
    assert len(rows) == 2
    names = {r["API"] for r in rows}
    assert {"id", "reset"} <= names
    reset = next(r for r in rows if r["API"] == "reset")
    assert reset["ApiType"] == "POST"
    assert reset["ApiDescription"] == "Reset"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tools.py::test_list_charger_actions_catalog -q`
Expected: FAIL — tool not found.

- [ ] **Step 3: Add whitelist + the tool**

In `server.py`, add the whitelist:
```python
_ACTION_CATALOG_FIELDS = [
    "ApiID", "Name", "API", "ApiType", "ApiTypeID", "ApiDescription",
    "CategoryID", "IsInputRequired", "IsActive",
]
```
Add inside `build_server`:
```python
    @mcp.tool
    async def cms_list_charger_actions(qcharger_id: str) -> list[dict]:
        """Catalog of device API commands available for ONE charger (by
        QChargerID) in the old CMS: command name, HTTP verb, and description.
        Read-only reference (what CAN be run) — these tools never execute a
        command."""
        cid = await _resolve_charger_id(client, qcharger_id, _charger_map)
        rows = await _post_json(
            client, "/ChargerDetails/GetAllApisByChargerID",
            data={"ChargerID": str(cid)})
        return _trim(rows, _ACTION_CATALOG_FIELDS)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tools.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: all tests PASS (auth + helpers + tools).

- [ ] **Step 6: Commit**

```bash
git add cms_mcp/server.py tests/test_tools.py
git commit -m "feat(cms): add cms_list_charger_actions catalog tool"
```

---

### Task 10: Update README tool table

**Files:**
- Modify: `cms-mcp-server/README.md`

- [ ] **Step 1: Read the current tool table**

Run: `python -m pytest -q` first (guard), then open `README.md` and locate the tools table/list.

- [ ] **Step 2: Add the 5 new tools**

Add these rows/entries to the tool documentation, matching the existing formatting:
```markdown
| `cms_get_charger_details` | Full profile for one charger by QChargerID: metadata (type, controllers, capacity, serial, address) merged with live status (OCPP id, location, gun states, last event/ping). |
| `cms_get_charger_sessions` | Recent charging-session logs for one charger (events, outlet/gun, start, duration, energy, session error). `limit` default 10. |
| `cms_get_charger_alerts` | Recent alert/fault logs for one charger (source, outlet/gun, error id + description, timestamp). `limit` default 10. |
| `cms_get_charger_action_history` | Recent remote-command execution history for one charger (which API ran, type, status, when). Read-only. `limit` default 10. |
| `cms_list_charger_actions` | Catalog of device API commands available for one charger (name, verb, description). Read-only reference; never executes. |
```
> If the README uses a bulleted list rather than a table, mirror that format instead. All five take `qcharger_id` (the human-facing id, e.g. `ab-sam-00585`).

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(cms): document the 5 new per-charger tools"
```

---

### Task 11: Version bump + release

**Files:**
- Modify: version files via `scripts/bump-version.ps1`

- [ ] **Step 1: Confirm the full suite is green**

Run: `python -m pytest -q`
Expected: all PASS.

- [ ] **Step 2: Bump the minor version (feat)**

Per the team convention ([[version-bump-per-feature]]), run from the repo root in PowerShell:
```powershell
scripts/bump-version.ps1 -Type minor
```
Expected: version bumped (e.g. 0.3.1 → 0.4.0), release commit + tag created.

- [ ] **Step 3: Push branch + tag**

```bash
git push origin feat/mcp-server
git push origin --tags
```

- [ ] **Step 4: Report the new version and tool count to the user.**

---

## Self-review notes

- **Spec coverage:** resolver (§3.1 → Task 4), details+status merge (§3.2/3.3/§4.1 → Task 5), sessions (§3.4 → Task 6), alerts (§3.5 → Task 7), action history (§3.6 → Task 8), action catalog (§3.7 → Task 9), noise-trimming asserted in every tool test (§3 "noise to drop"), error handling for unknown id (§6 → Task 4 tests), README + release (§8 → Tasks 10-11).
- **DataTables empty-result → `[]`:** handled by `resp.get("data", [])`; a charger with no logs returns `[]`, matching §6.
- **Type consistency:** `_resolve_charger_id(client, qcharger_id, cache)`, `_post_json(client, path, *, data, json)`, `_datatables_body(charger_id, limit)`, and `_charger_map` are used identically across every task.
- **Read-only guarantee:** no tool calls any execute endpoint; the catalog is descriptive only. Docstrings state this explicitly.
