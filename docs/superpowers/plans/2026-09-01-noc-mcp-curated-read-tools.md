# NOC MCP Curated Read Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 8 curated read-only tools over NOC backend endpoints that already exist but are unusable under auto-generated names, prune the generated tool set from 169 to ~116, and stamp every response with a freshness envelope so an offline charger stops blinding the agent.

**Architecture:** `server.py` stays a thin builder. Each concern gets its own module exposing a `_register_*_tools(mcp, client)` function, matching the existing `control_center.py` / `ssh_exec.py` convention. A shared `freshness.py` supplies the `source`/`as_of` envelope and the live→history fallback used by both new tools and three existing ones. Read-only is preserved throughout: `ReadOnlyProxyMiddleware` and the GET-only route map are untouched.

**Tech Stack:** Python 3.12, FastMCP 3.4.4, httpx (`NocAuthClient` subclasses `httpx.AsyncClient`), pytest with `asyncio_mode = auto`, `httpx.MockTransport` for the fake NOC backend.

**Spec:** `docs/superpowers/specs/2026-09-01-noc-mcp-curated-read-tools-design.md`

**Working directory for all commands:** `E:\cjain\noc-mcp-agent\services\mcp\noc`
**Python:** `E:/cjain/noc-mcp-agent/.venv/Scripts/python.exe`

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `noc_mcp/freshness.py` | `source`/`as_of`/`stale_seconds` envelope + `live_or_history` fallback | Create |
| `noc_mcp/catalog.py` | On-charger RBAC catalog loader + catalog tool (moved out of `server.py`) | Create |
| `noc_mcp/reports.py` | `noc_report` / `noc_stats` dispatchers | Create |
| `noc_mcp/fleet.py` | `noc_fleet_status` | Create |
| `noc_mcp/history.py` | 5 per-charger composites reading NOC backend history | Create |
| `noc_mcp/server.py` | Thin builder; route maps; middleware wiring | Modify |
| `noc_mcp/control_center.py` | Fallback added to 3 existing tools | Modify |
| `tests/fake_noc.py` | Add canned NOC backend (non-proxy) responses | Modify |
| `tests/test_freshness.py` | Envelope + fallback unit tests | Create |
| `tests/test_catalog.py` | Catalog completeness + role + callable-flag tests | Create |
| `tests/test_reports.py` | Dispatcher routing + enum tests | Create |
| `tests/test_fleet.py` | Fleet filter passthrough tests | Create |
| `tests/test_history.py` | Composite + fallback integration tests | Create |
| `tests/test_pruning.py` | Route-map exclusion + no-duplicate-coverage + safety invariant | Create |

---

## Task 1: Freshness envelope

**Files:**
- Create: `noc_mcp/freshness.py`
- Test: `tests/test_freshness.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_freshness.py`:

```python
"""Tests for the freshness envelope and live->history fallback (noc_mcp.freshness)."""

from datetime import datetime, timedelta, timezone

import pytest

from noc_mcp.freshness import HISTORY, LIVE, is_error, live_or_history, stamp


def test_stamp_live_sets_zero_staleness():
    out = stamp({"guns": []}, source=LIVE)
    assert out["source"] == "live"
    assert out["stale_seconds"] == 0
    assert out["reason"] is None
    assert out["as_of"].endswith("Z")
    assert out["guns"] == []


def test_stamp_history_computes_staleness_from_as_of():
    as_of = datetime.now(timezone.utc) - timedelta(seconds=600)
    out = stamp({"alarms": []}, source=HISTORY, as_of=as_of, reason="charger offline")
    assert out["source"] == "history"
    assert 595 <= out["stale_seconds"] <= 605
    assert out["reason"] == "charger offline"


def test_stamp_history_without_as_of_is_honest_about_unknown():
    out = stamp({"alarms": []}, source=HISTORY, reason="no cache")
    assert out["as_of"] is None
    assert out["stale_seconds"] is None


def test_stamp_does_not_overwrite_payload_keys():
    out = stamp({"source": "device-said-live"}, source=LIVE)
    assert out["source"] == "live"          # envelope wins
    assert out["payload_source"] == "device-said-live"


def test_is_error_detects_proxy_error_shape():
    assert is_error({"error": "HTTPStatusError: 502", "port": 8003, "path": "x"})
    assert not is_error({"guns": []})


@pytest.mark.asyncio
async def test_live_or_history_prefers_live():
    async def live():
        return {"v": 1}

    async def hist():
        raise AssertionError("history must not be called when live succeeds")

    out = await live_or_history(live, hist, reason="offline")
    assert out["source"] == "live"
    assert out["v"] == 1


@pytest.mark.asyncio
async def test_live_or_history_falls_back_on_error():
    async def live():
        return {"error": "boom", "port": 8003, "path": "x"}

    async def hist():
        return {"v": 2, "as_of": "2026-09-01T06:00:00Z"}

    out = await live_or_history(live, hist, reason="charger offline")
    assert out["source"] == "history"
    assert out["v"] == 2
    assert out["reason"] == "charger offline"
    assert out["stale_seconds"] is not None


@pytest.mark.asyncio
async def test_live_or_history_reports_both_failures():
    async def live():
        return {"error": "live-boom"}

    async def hist():
        return {"error": "hist-boom"}

    out = await live_or_history(live, hist, reason="offline")
    assert "error" in out
    assert out["live_error"] == "live-boom"
    assert out["history_error"] == "hist-boom"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `E:/cjain/noc-mcp-agent/.venv/Scripts/python.exe -m pytest tests/test_freshness.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_mcp.freshness'`

- [ ] **Step 3: Write minimal implementation**

Create `noc_mcp/freshness.py`:

```python
"""Freshness envelope for read tools.

Every curated read tool stamps its response with where the data came from and how old it is,
so the agent never has to infer freshness -- reporting archived data as current state is the
field-diagnosis failure this exists to prevent.

``live_or_history`` is the fallback primitive: try the live device, and on failure fall back to
the NOC backend's TimescaleDB-backed history, marking the result accordingly. It never raises;
callers get a structured payload either way, matching ``control_center._proxy_get``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

LIVE = "live"
HISTORY = "history"

# Payload keys inspected, in order, to date a historical record.
_AS_OF_KEYS = ("as_of", "cached_at", "timestamp", "updated_at", "created_at")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse(value: Any) -> Optional[datetime]:
    """Best-effort ISO-8601 parse; returns None rather than raising on junk."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def extract_as_of(payload: dict) -> Optional[datetime]:
    """Pull a timestamp out of a backend payload, or None if it carries none."""
    if not isinstance(payload, dict):
        return None
    for key in _AS_OF_KEYS:
        dt = _parse(payload.get(key))
        if dt is not None:
            return dt
    return None


def is_error(payload: Any) -> bool:
    """True for the ``{"error": ...}`` shape used by _proxy_get and the backend helpers."""
    return isinstance(payload, dict) and "error" in payload


def stamp(payload: dict, *, source: str, as_of: Optional[datetime] = None,
          reason: Optional[str] = None) -> dict:
    """Wrap ``payload`` in the freshness envelope.

    Envelope keys win over payload keys; a colliding payload key is preserved under a
    ``payload_``-prefixed name rather than being silently dropped.
    """
    if source == LIVE:
        as_of_dt: Optional[datetime] = as_of or _utcnow()
        stale: Optional[int] = 0
        reason = None
    else:
        as_of_dt = as_of
        stale = None if as_of_dt is None else max(0, int((_utcnow() - as_of_dt).total_seconds()))

    envelope = {
        "source": source,
        "as_of": _iso(as_of_dt) if as_of_dt else None,
        "stale_seconds": stale,
        "reason": reason,
    }
    out = dict(envelope)
    for k, v in (payload or {}).items():
        out[f"payload_{k}" if k in envelope else k] = v
    return out


async def live_or_history(
    live_call: Callable[[], Awaitable[dict]],
    history_call: Callable[[], Awaitable[dict]],
    *,
    reason: str,
) -> dict:
    """Try live; on a structured error fall back to history. Never raises."""
    live_out = await live_call()
    if not is_error(live_out):
        return stamp(live_out, source=LIVE)

    hist_out = await history_call()
    if is_error(hist_out):
        return {
            "error": "live and history both unavailable",
            "live_error": live_out.get("error"),
            "history_error": hist_out.get("error"),
        }
    return stamp(hist_out, source=HISTORY, as_of=extract_as_of(hist_out), reason=reason)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `E:/cjain/noc-mcp-agent/.venv/Scripts/python.exe -m pytest tests/test_freshness.py -v`
Expected: PASS — 8 passed

- [ ] **Step 5: Commit**

```bash
git add noc_mcp/freshness.py tests/test_freshness.py
git commit -m "feat(noc): freshness envelope with live->history fallback"
```

---

## Task 2: Extract the catalog loader out of server.py

Pure move, zero behaviour change. Doing it as its own commit means Task 3's behaviour change has a clean diff.

**Files:**
- Create: `noc_mcp/catalog.py`
- Modify: `noc_mcp/server.py`

- [ ] **Step 1: Run the existing suite to establish the baseline**

Run: `E:/cjain/noc-mcp-agent/.venv/Scripts/python.exe -m pytest -q`
Expected: PASS — 61 passed

- [ ] **Step 2: Create `noc_mcp/catalog.py`**

Move `_CATALOG_CSV`, `_load_control_center_catalog` and `_register_control_center_catalog` verbatim out of `server.py` into a new `noc_mcp/catalog.py`, changing only the import block and the CSV path anchor:

```python
"""On-charger control-center API catalog.

Distils ``catalog/rbac-rules.csv`` -- the QFlex on-charger RBAC API catalog -- into a knowledge
tool describing what a specific charger exposes across its on-device services, so the model knows
what it can fetch through the generic charger proxy.
"""

from __future__ import annotations

import csv
from pathlib import Path

from fastmcp import FastMCP

_CATALOG_CSV = Path(__file__).parent / "catalog" / "rbac-rules.csv"
```

Then paste the bodies of `_load_control_center_catalog()` and `_register_control_center_catalog(mcp)` from `server.py` unchanged beneath it.

- [ ] **Step 3: Update `server.py`**

Delete `import csv`, the `_CATALOG_CSV` constant, and both catalog functions from `server.py`. Add to its import block:

```python
from .catalog import _register_control_center_catalog
```

`build_server()` keeps its existing call to `_register_control_center_catalog(mcp)` unchanged.

- [ ] **Step 4: Run the suite to verify nothing moved**

Run: `E:/cjain/noc-mcp-agent/.venv/Scripts/python.exe -m pytest -q`
Expected: PASS — 61 passed (identical count; this is a pure move)

- [ ] **Step 5: Commit**

```bash
git add noc_mcp/catalog.py noc_mcp/server.py
git commit -m "refactor(noc): move control-center catalog out of server.py"
```

---

## Task 3: Catalog describes all 239 endpoints with roles

**Files:**
- Modify: `noc_mcp/catalog.py`
- Test: `tests/test_catalog.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_catalog.py`:

```python
"""Tests for the on-charger API catalog (noc_mcp.catalog)."""

from noc_mcp.catalog import _load_control_center_catalog


def test_catalog_includes_every_row_not_just_get():
    cat = _load_control_center_catalog()
    eps = cat["read_endpoints"]
    assert len(eps) == 239
    methods = {e["method"] for e in eps}
    assert {"GET", "POST", "PUT", "DELETE"} <= methods


def test_every_endpoint_carries_a_required_role():
    cat = _load_control_center_catalog()
    roles = {e["required_role"] for e in cat["read_endpoints"]}
    assert roles == {"customer", "operator", "admin"}


def test_non_get_endpoints_are_marked_not_callable():
    cat = _load_control_center_catalog()
    for e in cat["read_endpoints"]:
        if e["method"] == "GET":
            assert e["callable"] is True
        else:
            assert e["callable"] is False
            assert "read-only" in e["not_callable_reason"].lower()


def test_how_to_call_still_states_get_only():
    cat = _load_control_center_catalog()
    assert "GET" in cat["how_to_call"]
    assert "read-only" in cat["how_to_call"].lower()


def test_services_list_covers_all_seven_ports():
    cat = _load_control_center_catalog()
    assert {s["port"] for s in cat["services"]} == {8000, 8001, 8002, 8003, 8005, 8006, 8007}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `E:/cjain/noc-mcp-agent/.venv/Scripts/python.exe -m pytest tests/test_catalog.py -v`
Expected: FAIL — `assert 124 == 239` (the loader still filters to GET)

- [ ] **Step 3: Write minimal implementation**

In `noc_mcp/catalog.py`, replace the body of `_load_control_center_catalog()` with:

```python
def _load_control_center_catalog() -> dict:
    """Distil the on-charger RBAC API catalog into every endpoint across all services, keyed by port.

    All methods are described so the model knows what a charger supports and at what role. Only GET
    is callable -- non-GET rows carry ``callable: False`` and a reason, and ReadOnlyProxyMiddleware
    independently rejects any non-GET reaching the proxy.
    """
    endpoints = []
    services: dict[str, str] = {}
    with open(_CATALOG_CSV, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            method = (r.get("Method") or "").strip().upper()
            port = (r.get("Port") or "").strip()
            module = (r.get("Module") or "").strip()
            services.setdefault(port, module)
            is_get = method == "GET"
            endpoints.append({
                "port": int(port) if port.isdigit() else port,
                "module": module,
                "method": method,
                "path": (r.get("Path") or "").strip(),
                "name": (r.get("API Name") or "").strip(),
                "required_role": (r.get("Required Role") or "").strip(),
                "response_schema": (r.get("Resp Body") or "").strip()[:400],
                "callable": is_get,
                "not_callable_reason": None if is_get else (
                    "This server is read-only: only GET endpoints are callable through the proxy. "
                    "Listed so you know the charger supports it and at what role."
                ),
            })
    return {
        "source": "QFlex on-charger RBAC API catalog (rbac-rules.csv)",
        "services": [
            {"port": int(p) if p.isdigit() else p, "module": m}
            for p, m in sorted(services.items())
        ],
        "how_to_call": (
            "These are LIVE endpoints on a specific charger's on-device services. Only entries with "
            "callable=true (GET) can be fetched. To fetch one, call the tool "
            "'proxy_api_call_path_based_api_v1_chargers__charger_id__proxy__port___path__get' "
            "with: charger_id=<the NOC charger id>, port=<the endpoint's 'port' below>, "
            "path=<the endpoint's 'path' WITHOUT the leading slash>. Substitute placeholders "
            "like {gun_id}/{key} with real values. The charger must be ONLINE. Read-only (GET) "
            "endpoints only -- never use other methods against a charger."
        ),
        "example": {
            "goal": "Get live charging status for charger CHG-123",
            "tool": "proxy_api_call_path_based_api_v1_chargers__charger_id__proxy__port___path__get",
            "args": {"charger_id": "CHG-123", "port": 8003, "path": "api/v1/charging/status"},
        },
        "read_endpoints": endpoints,
    }
```

Also update the `get_control_center_api_catalog` docstring inside `_register_control_center_catalog` to end with:

```
        Entries marked callable=false are documented for awareness only -- they describe what the
        charger supports and at what role, but this server cannot invoke them."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `E:/cjain/noc-mcp-agent/.venv/Scripts/python.exe -m pytest tests/test_catalog.py -v`
Expected: PASS — 5 passed

- [ ] **Step 5: Run the full suite for regressions**

Run: `E:/cjain/noc-mcp-agent/.venv/Scripts/python.exe -m pytest -q`
Expected: PASS — 66 passed

- [ ] **Step 6: Commit**

```bash
git add noc_mcp/catalog.py tests/test_catalog.py
git commit -m "feat(noc): catalog describes all 239 on-charger endpoints with roles"
```

---

## Task 4: `noc_report` and `noc_stats` dispatchers

**Files:**
- Create: `noc_mcp/reports.py`
- Modify: `noc_mcp/server.py`, `tests/fake_noc.py`
- Test: `tests/test_reports.py`

- [ ] **Step 1: Extend the fake backend**

In `tests/fake_noc.py`, add a backend response table above `_handler` and a branch inside it.

Add after the `_DEVICE` dict:

```python
# Canned NOC backend (non-proxy) responses keyed by path.
_BACKEND = {
    "/api/v1/reports/session-failures": {"rows": [{"charger_id": "CHG-1", "reason": "PowerLoss"}]},
    "/api/v1/reports/fault-frequency": {"rows": [{"alarm_key": "E12", "count": 9}]},
    "/api/v1/stats/sessions/failed": {"rows": [{"session_id": "S9"}]},
    "/api/v1/stats/error-trends": {"buckets": [{"t": "2026-09-01T00:00:00Z", "n": 3}]},
    "/api/v1/chargers": {"items": [{"charger_id": "CHG-1", "online": False}], "next_cursor": None},
    "/api/v1/chargers/map": {"chargers": [{"charger_id": "CHG-1", "lat": 1.0, "lng": 2.0}]},
    "/api/v1/chargers/CHG-1/history/sessions": {"sessions": [{"session_id": "S0"}],
                                                "as_of": "2026-09-01T06:00:00Z"},
    "/api/v1/chargers/CHG-1/history/alarms": {"alarms": [{"code": "E12"}],
                                              "as_of": "2026-09-01T06:00:00Z"},
    "/api/v1/chargers/CHG-1/telemetry/latest": {"kw": 42, "as_of": "2026-09-01T06:00:00Z"},
    "/api/v1/chargers/CHG-1/telemetry/aggregated": {"buckets": [{"t": "2026-09-01T00:00:00Z", "kw": 40}]},
    "/api/v1/chargers/CHG-1/telemetry/history": {"points": [{"t": "2026-09-01T00:00:00Z", "kw": 41}]},
    "/api/v1/chargers/CHG-1/sessions/S1/events": {"events": [{"type": "SESSION_START"}]},
    "/api/v1/chargers/CHG-1/stability": {"reconnects_24h": 4},
    "/api/v1/chargers/CHG-1/commands": {"commands": [{"id": 1, "status": "done"}]},
    "/api/v1/chargers/CHG-1/ssh-status": {"connected": True},
    "/api/v1/chargers/CHG-1/ocpp-config/cache": {"HeartbeatInterval": "300",
                                                 "cached_at": "2026-09-01T06:00:00Z"},
    "/api/v1/chargers/OFFLINE/ocpp-config/cache": {"HeartbeatInterval": "60",
                                                   "cached_at": "2026-09-01T05:00:00Z"},
    "/api/v1/chargers/OFFLINE/history/alarms": {"alarms": [{"code": "E99"}],
                                                "as_of": "2026-09-01T05:00:00Z"},
    "/api/v1/chargers/OFFLINE/history/sessions": {"sessions": [{"session_id": "S-old"}],
                                                  "as_of": "2026-09-01T05:00:00Z"},
    "/api/v1/chargers/NOCACHE/ocpp-config/cache": None,   # -> 404 code=NO_CACHE
}
```

Then in `_handler`, immediately before the final `return httpx.Response(404, json={"detail": "unhandled"})`, insert:

```python
    if path in _BACKEND:
        payload = _BACKEND[path]
        if payload is None:
            return httpx.Response(404, json={"detail": "no cache", "code": "NO_CACHE"})
        return httpx.Response(200, json=payload)
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_reports.py`:

```python
"""Tests for the reports/stats dispatchers (noc_mcp.reports)."""

import httpx
import pytest

from noc_mcp.auth import NocAuthClient
from noc_mcp.reports import REPORTS, STATS, _fetch_report, _fetch_stats

_LOGIN = "/api/v1/auth/login"


def _client(handler):
    return NocAuthClient(base_url="http://noc.test", email="e", password="p",
                         transport=httpx.MockTransport(handler))


def test_every_report_name_has_a_description():
    assert len(REPORTS) == 13
    assert all(isinstance(v, str) and v for v in REPORTS.values())


def test_every_stats_name_has_a_description():
    assert len(STATS) == 11
    assert all(isinstance(v, str) and v for v in STATS.values())


@pytest.mark.asyncio
async def test_report_routes_to_the_right_path():
    seen = {}

    def handler(request):
        if request.url.path.endswith(_LOGIN):
            return httpx.Response(200, json={"access_token": "t.t.t", "token_type": "bearer"})
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"rows": []})

    async with _client(handler) as c:
        await _fetch_report(c, "session-failures", hours=24, limit=None,
                            customer_name=None, charger_id=None, from_ts=None, to_ts=None)
    assert seen["path"] == "/api/v1/reports/session-failures"
    assert seen["params"] == {"hours": "24"}


@pytest.mark.asyncio
async def test_report_omits_unset_filters():
    seen = {}

    def handler(request):
        if request.url.path.endswith(_LOGIN):
            return httpx.Response(200, json={"access_token": "t.t.t", "token_type": "bearer"})
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"rows": []})

    async with _client(handler) as c:
        await _fetch_report(c, "warranty-summary", hours=None, limit=None,
                            customer_name=None, charger_id=None, from_ts=None, to_ts=None)
    assert seen["params"] == {}


@pytest.mark.asyncio
async def test_unknown_report_returns_the_enum_not_an_exception():
    def handler(request):
        return httpx.Response(200, json={"access_token": "t.t.t", "token_type": "bearer"})

    async with _client(handler) as c:
        out = await _fetch_report(c, "no-such-report", hours=None, limit=None,
                                  customer_name=None, charger_id=None, from_ts=None, to_ts=None)
    assert "error" in out
    assert "session-failures" in out["valid_reports"]


@pytest.mark.asyncio
async def test_stats_routes_to_the_right_path():
    seen = {}

    def handler(request):
        if request.url.path.endswith(_LOGIN):
            return httpx.Response(200, json={"access_token": "t.t.t", "token_type": "bearer"})
        seen["path"] = request.url.path
        return httpx.Response(200, json={"buckets": []})

    async with _client(handler) as c:
        await _fetch_stats(c, "sessions/failed", hours=None, limit=None,
                           customer_name=None, charger_id=None, from_ts=None, to_ts=None)
    assert seen["path"] == "/api/v1/stats/sessions/failed"


@pytest.mark.asyncio
async def test_report_stamps_history_source():
    def handler(request):
        if request.url.path.endswith(_LOGIN):
            return httpx.Response(200, json={"access_token": "t.t.t", "token_type": "bearer"})
        return httpx.Response(200, json={"rows": []})

    async with _client(handler) as c:
        out = await _fetch_report(c, "charger-energy", hours=None, limit=None,
                                  customer_name=None, charger_id=None, from_ts=None, to_ts=None)
    assert out["source"] == "history"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name,needle", [
    ("noc_report", "session-failures"),
    ("noc_stats", "error-trends"),
])
async def test_dispatcher_descriptions_list_their_enum(tool_name, needle):
    """The enum listing IS the discoverability for a dispatcher.

    Guards a specific trap: an f-string docstring is not a string literal, so Python sets
    __doc__ to None and the tool registers with no description at all -- silently, since
    registration still succeeds. Descriptions must be passed via @mcp.tool(description=...).
    """
    from tests.fake_noc import build_test_server

    tool = next(t for t in await build_test_server().list_tools() if t.name == tool_name)
    assert tool.description, f"{tool_name} registered with no description"
    assert needle in tool.description
```

- [ ] **Step 3: Run test to verify it fails**

Run: `E:/cjain/noc-mcp-agent/.venv/Scripts/python.exe -m pytest tests/test_reports.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_mcp.reports'`

- [ ] **Step 4: Write minimal implementation**

Create `noc_mcp/reports.py`:

```python
"""Dispatcher tools over the NOC backend's reports and stats modules.

Both modules are read-only and purpose-built -- 24 endpoints that already implement the fleet
analysis the agent needs. Exposing them as 24 separate tools would swamp tool selection, so each
module gets one dispatcher whose ``report``/``metric`` argument is a described enum. The model
sees every available analysis in a single tool description.

These read the NOC backend's own database, never a charger, so they answer whether or not a
charger is online -- hence always source="history".
"""

from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP

from .freshness import HISTORY, stamp

# report name -> one-line description, surfaced in the tool schema.
REPORTS = {
    "alert-details": "Alert / alarm details report",
    "charger-energy": "Per-charger session count and energy delivery summary",
    "charger-ticket-status": "Charger status joined with open ticket counts",
    "commission-pending": "Chargers awaiting commissioning",
    "commissioned": "Commissioned chargers",
    "customer-status": "Customer-wise charger status",
    "fault-frequency": "Fault frequency analysis by alarm key and error code",
    "nightly-restart": "Nightly restart job summary",
    "session-details": "Bulk session details",
    "session-failures": "Sessions that ended with abnormal stop reasons",
    "ticket-resolution": "Ticket lifecycle and resolution time (MTTR)",
    "user-permissions": "User roles and permissions",
    "warranty-summary": "Charger warranty summary",
}

# metric name -> one-line description. Keys are paths under /api/v1/stats/.
STATS = {
    "": "System-wide statistics",
    "alarms": "Fleet-wide alarm statistics",
    "alarms/list": "Filtered alarm history",
    "error-trends": "Error trend time series",
    "error-trends/by-client": "Customer error ranking",
    "error-trends/by-error-type": "Error type breakdown",
    "error-trends/export": "Export error trends data",
    "error-trends/summary": "Error trends summary cards",
    "sessions": "Fleet-wide session statistics",
    "sessions/failed": "Detailed failed session analysis",
    "sessions/list": "Filtered session history",
}

_FILTERS = ("hours", "limit", "customer_name", "charger_id", "from_ts", "to_ts")


def _params(**kwargs) -> dict:
    """Drop unset filters so the backend applies its own defaults."""
    return {k: v for k, v in kwargs.items() if v is not None}


async def _get(client, url: str, params: dict) -> dict:
    """GET a backend URL, returning a structured error rather than raising."""
    try:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {"data": data}
    except Exception as e:  # noqa: BLE001 - surface any failure as a clean payload
        return {"error": f"{type(e).__name__}: {e}", "url": url}


async def _fetch_report(client, report: str, **filters) -> dict:
    if report not in REPORTS:
        return {"error": f"unknown report {report!r}", "valid_reports": sorted(REPORTS)}
    out = await _get(client, f"/api/v1/reports/{report}", _params(**filters))
    return out if "error" in out else stamp(out, source=HISTORY)


async def _fetch_stats(client, metric: str, **filters) -> dict:
    if metric not in STATS:
        return {"error": f"unknown metric {metric!r}", "valid_metrics": sorted(STATS)}
    suffix = f"/{metric}" if metric else ""
    out = await _get(client, f"/api/v1/stats{suffix}", _params(**filters))
    return out if "error" in out else stamp(out, source=HISTORY)


def _describe(mapping: dict) -> str:
    return "; ".join(f"{k or '(overall)'} = {v}" for k, v in mapping.items())


# Descriptions are built at import and passed via @mcp.tool(description=...).
# They CANNOT be f-string docstrings: an f-string is not a string literal, so Python sets
# __doc__ to None and the tool would register with no description at all -- which would defeat
# the whole point of the dispatcher shape, since the enum listing IS the discoverability.
_REPORT_DESC = (
    "Run one of the NOC fleet reports. Read-only; reads the NOC database, so it works whether "
    "or not a charger is online.\n\n"
    f"Valid `report` values: {_describe(REPORTS)}.\n\n"
    "Filters are optional and passed through only when set; unset filters let the backend "
    "apply its own defaults."
)

_STATS_DESC = (
    "Fleet-wide statistics and error trends. Read-only; reads the NOC database, so it works "
    "whether or not a charger is online.\n\n"
    f'Valid `metric` values: {_describe(STATS)}. Pass "" for system-wide statistics.'
)


def _register_report_tools(mcp: FastMCP, client) -> None:
    """Register the reports and stats dispatchers onto ``mcp``."""

    @mcp.tool(description=_REPORT_DESC)
    async def noc_report(
        report: str,
        hours: Optional[int] = None,
        limit: Optional[int] = None,
        customer_name: Optional[str] = None,
        charger_id: Optional[str] = None,
        from_ts: Optional[str] = None,
        to_ts: Optional[str] = None,
    ) -> dict:
        return await _fetch_report(client, report, hours=hours, limit=limit,
                                   customer_name=customer_name, charger_id=charger_id,
                                   from_ts=from_ts, to_ts=to_ts)

    @mcp.tool(description=_STATS_DESC)
    async def noc_stats(
        metric: str = "",
        hours: Optional[int] = None,
        limit: Optional[int] = None,
        customer_name: Optional[str] = None,
        charger_id: Optional[str] = None,
        from_ts: Optional[str] = None,
        to_ts: Optional[str] = None,
    ) -> dict:
        return await _fetch_stats(client, metric, hours=hours, limit=limit,
                                  customer_name=customer_name, charger_id=charger_id,
                                  from_ts=from_ts, to_ts=to_ts)
```

- [ ] **Step 5: Wire it into `server.py`**

Add to the import block in `noc_mcp/server.py`:

```python
from .reports import _register_report_tools
```

And inside `build_server()`, after `_register_control_center_tools(mcp, client)`:

```python
    _register_report_tools(mcp, client)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `E:/cjain/noc-mcp-agent/.venv/Scripts/python.exe -m pytest tests/test_reports.py -v`
Expected: PASS — 9 passed

- [ ] **Step 7: Commit**

```bash
git add noc_mcp/reports.py noc_mcp/server.py tests/test_reports.py tests/fake_noc.py
git commit -m "feat(noc): noc_report and noc_stats dispatchers over 24 endpoints"
```

---

## Task 5: `noc_fleet_status`

**Files:**
- Create: `noc_mcp/fleet.py`
- Modify: `noc_mcp/server.py`
- Test: `tests/test_fleet.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_fleet.py`:

```python
"""Tests for the fleet-status tool (noc_mcp.fleet)."""

import httpx
import pytest

from noc_mcp.auth import NocAuthClient
from noc_mcp.fleet import _fetch_fleet_status

_LOGIN = "/api/v1/auth/login"


def _client(handler):
    return NocAuthClient(base_url="http://noc.test", email="e", password="p",
                         transport=httpx.MockTransport(handler))


def _ok_login(request):
    return httpx.Response(200, json={"access_token": "t.t.t", "token_type": "bearer"})


@pytest.mark.asyncio
async def test_offline_only_is_passed_through():
    seen = {}

    def handler(request):
        if request.url.path.endswith(_LOGIN):
            return _ok_login(request)
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"items": []})

    async with _client(handler) as c:
        await _fetch_fleet_status(c, offline_only=True, online_only=False, search=None,
                                  customer_name=None, model=None, limit=None, as_map=False)
    assert seen["path"] == "/api/v1/chargers"
    assert seen["params"]["offline_only"] == "true"
    assert "online_only" not in seen["params"]


@pytest.mark.asyncio
async def test_as_map_uses_the_map_endpoint():
    seen = {}

    def handler(request):
        if request.url.path.endswith(_LOGIN):
            return _ok_login(request)
        seen["path"] = request.url.path
        return httpx.Response(200, json={"chargers": []})

    async with _client(handler) as c:
        await _fetch_fleet_status(c, offline_only=False, online_only=False, search=None,
                                  customer_name="Ador", model=None, limit=None, as_map=True)
    assert seen["path"] == "/api/v1/chargers/map"


@pytest.mark.asyncio
async def test_mutually_exclusive_filters_are_rejected():
    def handler(request):
        return _ok_login(request)

    async with _client(handler) as c:
        out = await _fetch_fleet_status(c, offline_only=True, online_only=True, search=None,
                                        customer_name=None, model=None, limit=None, as_map=False)
    assert "error" in out
    assert "mutually exclusive" in out["error"]


@pytest.mark.asyncio
async def test_result_is_stamped_history():
    def handler(request):
        if request.url.path.endswith(_LOGIN):
            return _ok_login(request)
        return httpx.Response(200, json={"items": []})

    async with _client(handler) as c:
        out = await _fetch_fleet_status(c, offline_only=False, online_only=False, search=None,
                                        customer_name=None, model=None, limit=None, as_map=False)
    assert out["source"] == "history"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `E:/cjain/noc-mcp-agent/.venv/Scripts/python.exe -m pytest tests/test_fleet.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_mcp.fleet'`

- [ ] **Step 3: Write minimal implementation**

Create `noc_mcp/fleet.py`:

```python
"""Fleet-level charger status.

The NOC backend derives online status from its live WebSocket connection registry, so
``/api/v1/chargers`` answers "which chargers are down right now" -- the question a NOC shift
starts with. Reads the backend, never a charger.
"""

from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP

from .freshness import HISTORY, stamp
from .reports import _get


async def _fetch_fleet_status(client, *, offline_only: bool, online_only: bool,
                              search: Optional[str], customer_name: Optional[str],
                              model: Optional[str], limit: Optional[int],
                              as_map: bool) -> dict:
    if offline_only and online_only:
        return {"error": "offline_only and online_only are mutually exclusive"}

    if as_map:
        params = {k: v for k, v in {"customer_name": customer_name}.items() if v is not None}
        out = await _get(client, "/api/v1/chargers/map", params)
    else:
        params = {k: v for k, v in {
            "offline_only": "true" if offline_only else None,
            "online_only": "true" if online_only else None,
            "search": search,
            "customer_name": customer_name,
            "model": model,
            "limit": limit,
        }.items() if v is not None}
        out = await _get(client, "/api/v1/chargers", params)

    return out if "error" in out else stamp(out, source=HISTORY)


def _register_fleet_tools(mcp: FastMCP, client) -> None:
    """Register the fleet-status tool onto ``mcp``."""

    @mcp.tool
    async def noc_fleet_status(
        offline_only: bool = False,
        online_only: bool = False,
        search: Optional[str] = None,
        customer_name: Optional[str] = None,
        model: Optional[str] = None,
        limit: Optional[int] = None,
        as_map: bool = False,
    ) -> dict:
        """Which chargers are currently online or offline, across the fleet.

        Start here for "what needs attention" questions. Set offline_only=True to list the
        chargers that are down right now (not just a count). Filter with search (charger id or
        model), customer_name, or model. Set as_map=True for every charger with lightweight
        map fields instead of a paginated page.

        Reads the NOC backend, so it works regardless of any individual charger's state."""
        return await _fetch_fleet_status(
            client, offline_only=offline_only, online_only=online_only, search=search,
            customer_name=customer_name, model=model, limit=limit, as_map=as_map)
```

- [ ] **Step 4: Wire it into `server.py`**

Add to the import block:

```python
from .fleet import _register_fleet_tools
```

And inside `build_server()`, after `_register_report_tools(mcp, client)`:

```python
    _register_fleet_tools(mcp, client)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `E:/cjain/noc-mcp-agent/.venv/Scripts/python.exe -m pytest tests/test_fleet.py -v`
Expected: PASS — 4 passed

- [ ] **Step 6: Commit**

```bash
git add noc_mcp/fleet.py noc_mcp/server.py tests/test_fleet.py
git commit -m "feat(noc): noc_fleet_status answers which chargers are down"
```

---

## Task 6: Per-charger history composites

**Files:**
- Create: `noc_mcp/history.py`
- Modify: `noc_mcp/server.py`
- Test: `tests/test_history.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_history.py`:

```python
"""Tests for the per-charger history composites (noc_mcp.history)."""

import pytest

from noc_mcp.history import (
    _fetch_charger_history,
    _fetch_charger_ocpp_config,
    _fetch_charger_stability,
    _fetch_charger_telemetry,
    _fetch_session_events,
)
from tests.fake_noc import build_test_client


@pytest.mark.asyncio
async def test_history_merges_sessions_and_alarms():
    async with build_test_client() as c:
        out = await _fetch_charger_history(c, "CHG-1", limit=None)
    assert out["source"] == "history"
    assert out["sessions"]["sessions"][0]["session_id"] == "S0"
    assert out["alarms"]["alarms"][0]["code"] == "E12"


@pytest.mark.asyncio
async def test_history_works_for_an_offline_charger():
    async with build_test_client() as c:
        out = await _fetch_charger_history(c, "OFFLINE", limit=None)
    assert out["source"] == "history"
    assert out["sessions"]["sessions"][0]["session_id"] == "S-old"


@pytest.mark.asyncio
async def test_telemetry_merges_three_views():
    async with build_test_client() as c:
        out = await _fetch_charger_telemetry(c, "CHG-1", resolution=None, hours=None,
                                             from_ts=None, to_ts=None, limit=None)
    assert out["latest"]["kw"] == 42
    assert out["aggregated"]["buckets"][0]["kw"] == 40
    assert out["history"]["points"][0]["kw"] == 41


@pytest.mark.asyncio
async def test_session_events_returns_lifecycle():
    async with build_test_client() as c:
        out = await _fetch_session_events(c, "CHG-1", "S1")
    assert out["events"][0]["type"] == "SESSION_START"


@pytest.mark.asyncio
async def test_stability_merges_connection_and_command_history():
    async with build_test_client() as c:
        out = await _fetch_charger_stability(c, "CHG-1")
    assert out["stability"]["reconnects_24h"] == 4
    assert out["commands"]["commands"][0]["status"] == "done"
    assert out["ssh"]["connected"] is True


@pytest.mark.asyncio
async def test_ocpp_config_prefers_live_device():
    async with build_test_client() as c:
        out = await _fetch_charger_ocpp_config(c, "CHG-1")
    assert out["source"] == "live"
    assert out["HeartbeatInterval"] == "300"


@pytest.mark.asyncio
async def test_ocpp_config_falls_back_to_cache_when_offline():
    async with build_test_client() as c:
        out = await _fetch_charger_ocpp_config(c, "OFFLINE")
    assert out["source"] == "history"
    assert out["HeartbeatInterval"] == "60"
    assert "offline" in out["reason"].lower()
    assert out["stale_seconds"] is not None


@pytest.mark.asyncio
async def test_ocpp_config_no_cache_is_not_an_error():
    async with build_test_client() as c:
        out = await _fetch_charger_ocpp_config(c, "NOCACHE")
    assert out["source"] == "history"
    assert out["as_of"] is None
    assert "no cached" in out["reason"].lower()
    assert "error" not in out
```

- [ ] **Step 2: Add `build_test_client` to the fake**

`tests/fake_noc.py` currently only exposes `build_test_server()`. Append:

```python
def build_test_client():
    """Bare NocAuthClient wired to the fake NOC (for unit-testing module helpers)."""
    return NocAuthClient(base_url="http://noc.test", email="e", password="p",
                         transport=httpx.MockTransport(_handler))
```

Note `NOCACHE` must also resolve through the proxy branch for the live attempt to fail. Add to `_handler`, immediately after the `charger == "OFFLINE"` check:

```python
        if charger == "NOCACHE":
            return httpx.Response(502, json={"detail": "charger offline"})
```

- [ ] **Step 3: Run test to verify it fails**

Run: `E:/cjain/noc-mcp-agent/.venv/Scripts/python.exe -m pytest tests/test_history.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_mcp.history'`

- [ ] **Step 4: Write minimal implementation**

Create `noc_mcp/history.py`:

```python
"""Per-charger history and telemetry composites.

These read the NOC backend's TimescaleDB-backed endpoints rather than the device, so unlike the
live ``charger_*`` tools they answer *after* a charger drops offline. That is the point: the
moment a charger goes dark is exactly when a NOC engineer needs its recent history.

``noc_charger_ocpp_config`` is the one tool here that prefers the live device, falling back to
the backend's cached copy and labelling the result.
"""

from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP

from .control_center import _proxy_get
from .freshness import HISTORY, extract_as_of, live_or_history, stamp
from .reports import _get


def _p(**kwargs) -> dict:
    return {k: v for k, v in kwargs.items() if v is not None}


async def _fetch_charger_history(client, charger_id: str, *, limit: Optional[int]) -> dict:
    base = f"/api/v1/chargers/{charger_id}"
    params = _p(limit=limit)
    sessions = await _get(client, f"{base}/history/sessions", params)
    alarms = await _get(client, f"{base}/history/alarms", params)
    merged = {"sessions": sessions, "alarms": alarms}
    return stamp(merged, source=HISTORY, as_of=extract_as_of(sessions) or extract_as_of(alarms),
                 reason="NOC backend history (independent of charger state)")


async def _fetch_charger_telemetry(client, charger_id: str, *, resolution: Optional[str],
                                   hours: Optional[int], from_ts: Optional[str],
                                   to_ts: Optional[str], limit: Optional[int]) -> dict:
    base = f"/api/v1/chargers/{charger_id}/telemetry"
    latest = await _get(client, f"{base}/latest", {})
    aggregated = await _get(client, f"{base}/aggregated", _p(resolution=resolution, hours=hours))
    history = await _get(client, f"{base}/history", _p(limit=limit, from_ts=from_ts, to_ts=to_ts))
    merged = {"latest": latest, "aggregated": aggregated, "history": history}
    return stamp(merged, source=HISTORY, as_of=extract_as_of(latest),
                 reason="NOC backend telemetry (independent of charger state)")


async def _fetch_session_events(client, charger_id: str, session_id: str) -> dict:
    out = await _get(client, f"/api/v1/chargers/{charger_id}/sessions/{session_id}/events", {})
    return out if "error" in out else stamp(out, source=HISTORY, as_of=extract_as_of(out))


async def _fetch_charger_stability(client, charger_id: str) -> dict:
    base = f"/api/v1/chargers/{charger_id}"
    stability = await _get(client, f"{base}/stability", {})
    commands = await _get(client, f"{base}/commands", {})
    ssh = await _get(client, f"{base}/ssh-status", {})
    merged = {"stability": stability, "commands": commands, "ssh": ssh}
    return stamp(merged, source=HISTORY, as_of=extract_as_of(stability),
                 reason="NOC backend connection history")


async def _fetch_charger_ocpp_config(client, charger_id: str) -> dict:
    """Live OCPP config, falling back to the backend's cached copy when the charger is offline.

    A 404 with code=NO_CACHE means the backend has never cached this charger's config. That is a
    normal empty result, not a failure, so it is reported as such rather than as an error.
    """
    async def live():
        return await _proxy_get(client, charger_id, 8003, "api/v1/config/ocpp")

    async def cached():
        return await _get(client, f"/api/v1/chargers/{charger_id}/ocpp-config/cache", {})

    out = await live_or_history(live, cached, reason="charger offline; served from NOC cache")
    if "error" in out and "NO_CACHE" in str(out.get("history_error", "")):
        return stamp({}, source=HISTORY,
                     reason="charger offline and no cached OCPP config exists for it yet")
    return out


def _register_history_tools(mcp: FastMCP, client) -> None:
    """Register the per-charger history composites onto ``mcp``."""

    @mcp.tool
    async def noc_charger_history(charger_id: str, limit: Optional[int] = None) -> dict:
        """Recent charging sessions and alarm history for a charger, from the NOC database.

        Unlike the live charger_get_* tools this works when the charger is OFFLINE, which is when
        you usually need it. Use this to reconstruct what happened before a charger went dark."""
        return await _fetch_charger_history(client, charger_id, limit=limit)

    @mcp.tool
    async def noc_charger_telemetry(charger_id: str, resolution: Optional[str] = None,
                                    hours: Optional[int] = None, from_ts: Optional[str] = None,
                                    to_ts: Optional[str] = None,
                                    limit: Optional[int] = None) -> dict:
        """Telemetry for a charger: latest snapshot, time-bucketed aggregates, and raw history.

        Reads TimescaleDB, so it works when the charger is OFFLINE. Use `resolution` and `hours`
        for trends; `from_ts`/`to_ts` (ISO-8601) to bound the raw history."""
        return await _fetch_charger_telemetry(client, charger_id, resolution=resolution,
                                              hours=hours, from_ts=from_ts, to_ts=to_ts,
                                              limit=limit)

    @mcp.tool
    async def noc_session_events(charger_id: str, session_id: str) -> dict:
        """Lifecycle events for one charging session: SESSION_START, START_CHARGING,
        STOP_CHARGING, SESSION_STOP.

        Use this to explain why a session failed. Works when the charger is OFFLINE."""
        return await _fetch_session_events(client, charger_id, session_id)

    @mcp.tool
    async def noc_charger_stability(charger_id: str) -> dict:
        """Connection stability for a charger: reconnect frequency, recent command history, and
        SSH connection status.

        Start here for "this charger keeps dropping" complaints. Works when the charger is
        OFFLINE."""
        return await _fetch_charger_stability(client, charger_id)

    @mcp.tool
    async def noc_charger_ocpp_config(charger_id: str) -> dict:
        """The charger's OCPP configuration keys, read live from the device where possible.

        If the charger is offline this falls back to the NOC backend's cached copy and marks the
        result source="history" with its age -- so a stale value is never mistaken for current."""
        return await _fetch_charger_ocpp_config(client, charger_id)
```

- [ ] **Step 5: Wire it into `server.py`**

Add to the import block:

```python
from .history import _register_history_tools
```

And inside `build_server()`, after `_register_fleet_tools(mcp, client)`:

```python
    _register_history_tools(mcp, client)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `E:/cjain/noc-mcp-agent/.venv/Scripts/python.exe -m pytest tests/test_history.py -v`
Expected: PASS — 8 passed

- [ ] **Step 7: Run the full suite**

Run: `E:/cjain/noc-mcp-agent/.venv/Scripts/python.exe -m pytest -q`
Expected: PASS — 87 passed

- [ ] **Step 8: Commit**

```bash
git add noc_mcp/history.py noc_mcp/server.py tests/test_history.py tests/fake_noc.py
git commit -m "feat(noc): per-charger history composites that survive an offline charger"
```

---

## Task 7: Prune the generated tool set

**Files:**
- Modify: `noc_mcp/server.py`
- Test: `tests/test_pruning.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_pruning.py`:

```python
"""Route-map pruning and the read-only safety invariant."""

import pytest

from tests.fake_noc import build_test_server

# Prefixes that must contribute zero tools.
_EXCLUDED_FRAGMENTS = [
    "api_v1_auth", "api_v1_admin", "api_v1_roles", "api_v1_permissions", "api_v1_teams",
    "api_v1_reports", "api_v1_stats",
]

# Curated tools that must exist after pruning.
_REQUIRED = {
    "noc_report", "noc_stats", "noc_fleet_status", "noc_charger_history",
    "noc_charger_telemetry", "noc_session_events", "noc_charger_stability",
    "noc_charger_ocpp_config", "get_control_center_api_catalog",
    "charger_get_charging_status", "charger_ssh_prepare", "charger_ssh_execute",
}


async def _names():
    return {t.name for t in await build_test_server().list_tools()}


@pytest.mark.asyncio
@pytest.mark.parametrize("fragment", _EXCLUDED_FRAGMENTS)
async def test_excluded_modules_register_no_tools(fragment):
    offenders = sorted(n for n in await _names() if fragment in n)
    assert offenders == [], f"{fragment} still registers: {offenders}"


@pytest.mark.asyncio
async def test_curated_tools_are_present():
    missing = _REQUIRED - await _names()
    assert missing == set()


@pytest.mark.asyncio
async def test_charger_proxy_survives_pruning():
    # The ~110-endpoint on-charger long tail is reached through this; it must not be pruned.
    assert any("proxy" in n for n in await _names())


@pytest.mark.asyncio
async def test_surface_is_materially_smaller():
    total = len(await _names())
    assert total < 130, f"expected pruning to land under 130 tools, got {total}"


@pytest.mark.asyncio
async def test_no_registered_tool_can_issue_a_non_get():
    """Safety invariant: this server must never expose a write path to a charger."""
    for name in await _names():
        assert not name.endswith(("_post", "_put", "_delete", "_patch")), \
            f"non-GET tool registered: {name}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `E:/cjain/noc-mcp-agent/.venv/Scripts/python.exe -m pytest tests/test_pruning.py -v`
Expected: FAIL — the `api_v1_auth`, `api_v1_reports`, `api_v1_stats` cases fail, and `test_surface_is_materially_smaller` reports ~177

- [ ] **Step 3: Write minimal implementation**

In `noc_mcp/server.py`, replace the `_ROUTE_MAPS` definition with:

```python
# Modules with no NOC-operations value: user/role administration.
_EXCLUDED_PREFIXES = (
    r"^/api/v1/auth",
    r"^/api/v1/admin",
    r"^/api/v1/roles",
    r"^/api/v1/permissions",
    r"^/api/v1/teams",
)

# Endpoints a curated tool already owns. Leaving the generated twin in place would give the model
# two routes to one answer with no basis to choose, which is worse for selection than either alone.
_CURATED_PREFIXES = (
    r"^/api/v1/reports",                        # -> noc_report
    r"^/api/v1/stats",                          # -> noc_stats
    r"^/api/v1/chargers$",                      # -> noc_fleet_status
    r"^/api/v1/chargers/map$",                  # -> noc_fleet_status
    r"^/api/v1/chargers/\{charger_id\}/history",      # -> noc_charger_history
    r"^/api/v1/chargers/\{charger_id\}/telemetry",    # -> noc_charger_telemetry
    r"^/api/v1/chargers/\{charger_id\}/sessions",     # -> noc_session_events
    r"^/api/v1/chargers/\{charger_id\}/stability$",   # -> noc_charger_stability
    r"^/api/v1/chargers/\{charger_id\}/commands$",    # -> noc_charger_stability
    r"^/api/v1/chargers/\{charger_id\}/ssh-status$",  # -> noc_charger_stability
    r"^/api/v1/chargers/\{charger_id\}/ocpp-config",  # -> noc_charger_ocpp_config
)

# Order matters: exclusions are evaluated before the catch-all GET -> TOOL map.
_ROUTE_MAPS = (
    [RouteMap(pattern=p, mcp_type=MCPType.EXCLUDE) for p in _EXCLUDED_PREFIXES]
    + [RouteMap(pattern=p, mcp_type=MCPType.EXCLUDE) for p in _CURATED_PREFIXES]
    + [
        RouteMap(methods=["GET"], mcp_type=MCPType.TOOL),
        RouteMap(mcp_type=MCPType.EXCLUDE),
    ]
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `E:/cjain/noc-mcp-agent/.venv/Scripts/python.exe -m pytest tests/test_pruning.py -v`
Expected: PASS — 11 passed

If `test_surface_is_materially_smaller` still fails, print the surviving names to find what else the curated tools duplicate:

```bash
E:/cjain/noc-mcp-agent/.venv/Scripts/python.exe -c "import asyncio;from tests.fake_noc import build_test_server;print('\n'.join(sorted(t.name for t in asyncio.run(build_test_server().list_tools()))))"
```

- [ ] **Step 5: Run the full suite**

Run: `E:/cjain/noc-mcp-agent/.venv/Scripts/python.exe -m pytest -q`
Expected: PASS — 98 passed

- [ ] **Step 6: Commit**

```bash
git add noc_mcp/server.py tests/test_pruning.py
git commit -m "feat(noc): prune generated tool set from 169 to ~116"
```

---

## Task 8: Fallback in the three existing live tools

**Files:**
- Modify: `noc_mcp/control_center.py`
- Test: `tests/test_control_center.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_control_center.py`:

```python
from noc_mcp.control_center import (
    _get_alarms_with_fallback,
    _get_ocpp_status_with_fallback,
    _get_session_details_with_fallback,
)
from tests.fake_noc import build_test_client


@pytest.mark.asyncio
async def test_ocpp_status_live_is_stamped_live():
    async with build_test_client() as c:
        out = await _get_ocpp_status_with_fallback(c, "CHG-1")
    assert out["source"] == "live"
    assert out["status"]["connected"] is True


@pytest.mark.asyncio
async def test_ocpp_status_falls_back_for_offline_charger():
    async with build_test_client() as c:
        out = await _get_ocpp_status_with_fallback(c, "OFFLINE")
    assert out["source"] == "history"
    assert out["HeartbeatInterval"] == "60"


@pytest.mark.asyncio
async def test_alarms_fall_back_to_history_for_offline_charger():
    async with build_test_client() as c:
        out = await _get_alarms_with_fallback(c, "OFFLINE", None)
    assert out["source"] == "history"
    assert out["alarms"][0]["code"] == "E99"


@pytest.mark.asyncio
async def test_alarms_live_path_unchanged():
    async with build_test_client() as c:
        out = await _get_alarms_with_fallback(c, "CHG-1", None)
    assert out["source"] == "live"
    assert out["latched"]["latched"][0]["vendor_error_code"] == "E12"


@pytest.mark.asyncio
async def test_session_details_fall_back_for_offline_charger():
    async with build_test_client() as c:
        out = await _get_session_details_with_fallback(c, "OFFLINE", None)
    assert out["source"] == "history"
    assert out["sessions"][0]["session_id"] == "S-old"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `E:/cjain/noc-mcp-agent/.venv/Scripts/python.exe -m pytest tests/test_control_center.py -v`
Expected: FAIL — `ImportError: cannot import name '_get_ocpp_status_with_fallback'`

- [ ] **Step 3: Write minimal implementation**

In `noc_mcp/control_center.py`, add to the imports:

```python
from .freshness import live_or_history
from .reports import _get
```

Add these three helpers above `_register_control_center_tools`:

```python
async def _get_ocpp_status_with_fallback(client, charger_id: str) -> dict:
    """Live OCPP status + config; on failure, the backend's cached config."""
    async def live():
        status = await _proxy_get(client, charger_id, 8003, "api/v1/ocpp/status")
        if isinstance(status, dict) and "error" in status:
            return status
        config = await _proxy_get(client, charger_id, 8003, "api/v1/config/ocpp")
        return {"status": status, "config": config}

    async def cached():
        return await _get(client, f"/api/v1/chargers/{charger_id}/ocpp-config/cache", {})

    return await live_or_history(live, cached,
                                 reason="charger offline; OCPP config served from NOC cache")


async def _get_alarms_with_fallback(client, charger_id: str, gun_id) -> dict:
    """Live alarms; on failure, the backend's alarm history."""
    async def live():
        if gun_id is not None:
            errors = await _proxy_get(client, charger_id, 8006,
                                      f"error-generator/errors/by-gun/{gun_id}")
            return errors if "error" in errors else {"errors_by_gun": errors}
        latched = await _proxy_get(client, charger_id, 8006, "error-generator/alarms/latched")
        if isinstance(latched, dict) and "error" in latched:
            return latched
        pending = await _proxy_get(client, charger_id, 8006, "error-generator/errors/pending")
        return {"latched": latched, "pending": pending}

    async def history():
        return await _get(client, f"/api/v1/chargers/{charger_id}/history/alarms", {})

    return await live_or_history(live, history,
                                 reason="charger offline; alarms served from NOC history")


async def _get_session_details_with_fallback(client, charger_id: str, gun_id) -> dict:
    """Live session details; on failure, the backend's session history."""
    async def live():
        if gun_id is not None:
            active = await _proxy_get(client, charger_id, 8003,
                                      f"api/v1/session_details/active/{gun_id}")
            if isinstance(active, dict) and "error" in active:
                return active
            last = await _proxy_get(client, charger_id, 8003,
                                    f"api/v1/session_details/{gun_id}")
            return {"active": active, "last": last}
        history = await _proxy_get(client, charger_id, 8003, "api/v1/session_details/history")
        return history if "error" in history else {"history": history}

    async def history():
        return await _get(client, f"/api/v1/chargers/{charger_id}/history/sessions", {})

    return await live_or_history(live, history,
                                 reason="charger offline; sessions served from NOC history")
```

Then rewrite the three tool bodies to delegate. Replace the body of `charger_get_ocpp_status` with:

```python
        return await _get_ocpp_status_with_fallback(client, charger_id)
```

Replace the body of `charger_get_alarms` with:

```python
        return await _get_alarms_with_fallback(client, charger_id, gun_id)
```

Replace the body of `charger_get_session_details` with:

```python
        return await _get_session_details_with_fallback(client, charger_id, gun_id)
```

Append this sentence to each of those three tools' docstrings:

```
        If the charger is offline this falls back to NOC-stored history and marks the result
        source="history" with its age.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `E:/cjain/noc-mcp-agent/.venv/Scripts/python.exe -m pytest tests/test_control_center.py -v`
Expected: PASS — all existing control-center tests plus 5 new ones

- [ ] **Step 5: Run the full suite**

Run: `E:/cjain/noc-mcp-agent/.venv/Scripts/python.exe -m pytest -q`
Expected: PASS — 103 passed

- [ ] **Step 6: Commit**

```bash
git add noc_mcp/control_center.py tests/test_control_center.py
git commit -m "feat(noc): live->history fallback in three existing charger tools"
```

---

## Task 9: Docs and version bump

**Files:**
- Modify: `services/mcp/noc/README.md`, `VERSION`

- [ ] **Step 1: Update the service README**

In `services/mcp/noc/README.md`, add a section after the existing tool list:

```markdown
## Curated NOC-backend tools

These read the NOC backend's database rather than a charger, so they answer whether or not the
charger is online.

| Tool | Purpose |
|---|---|
| `noc_report(report, ...)` | 13 fleet reports: session failures, fault frequency, MTTR, energy, warranty |
| `noc_stats(metric, ...)` | 11 fleet statistics: alarm stats, error trends, session stats |
| `noc_fleet_status(...)` | Which chargers are online/offline right now, with filters |
| `noc_charger_history(charger_id)` | Recent sessions + alarm history for one charger |
| `noc_charger_telemetry(charger_id)` | Latest snapshot, time-bucketed aggregates, raw history |
| `noc_session_events(charger_id, session_id)` | Session lifecycle events (why a session failed) |
| `noc_charger_stability(charger_id)` | Reconnect frequency, command history, SSH status |
| `noc_charger_ocpp_config(charger_id)` | OCPP keys, live with cached fallback |

### Freshness envelope

Every curated tool stamps its response so archived data is never mistaken for current state:

```json
{"source": "live" | "history", "as_of": "...", "stale_seconds": 0, "reason": null}
```

`charger_get_ocpp_status`, `charger_get_alarms` and `charger_get_session_details` fall back to
NOC-stored history when the charger is offline, marked `source: "history"`.

The server remains read-only: only GET operations are exposed, and `ReadOnlyProxyMiddleware`
rejects any non-GET `method` on the charger proxy.
```

- [ ] **Step 2: Bump the version**

Run from the repo root:

```bash
E:/cjain/noc-mcp-agent/scripts/bump-version.ps1 -Part patch
```

Expected: `VERSION` moves `0.12.2` → `0.12.3`

- [ ] **Step 3: Run the full suite one final time**

Run: `E:/cjain/noc-mcp-agent/.venv/Scripts/python.exe -m pytest -q`
Expected: PASS — 103 passed

- [ ] **Step 4: Commit**

```bash
git add services/mcp/noc/README.md VERSION
git commit -m "docs(noc): document curated read tools and freshness envelope"
```

---

## Verification

After all tasks, confirm end-to-end:

- [ ] `E:/cjain/noc-mcp-agent/.venv/Scripts/python.exe -m pytest -q` → 103 passed
- [ ] Tool count is under 130:
  ```bash
  E:/cjain/noc-mcp-agent/.venv/Scripts/python.exe -c "import asyncio;from tests.fake_noc import build_test_server;print(len(asyncio.run(build_test_server().list_tools())))"
  ```
- [ ] Rebuild and confirm LibreChat still initialises the server:
  ```bash
  cd /e/cjain/noc-mcp-agent/apps/ai-platform/librechat && ./up.sh
  docker logs noc-librechat 2>&1 | grep -a "\[MCP\]\[noc\] Tools:"
  ```
  Expected: the `noc` tool list now contains `noc_report`, `noc_fleet_status`, etc., and no
  `api_v1_auth`/`api_v1_admin` names.
- [ ] Total across all servers has fallen from 245:
  ```bash
  docker logs noc-librechat 2>&1 | grep -a "Initialized with"
  ```
