# CMS firmware/release tools — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two read-only MCP tools — `cms_get_charger_firmware` (one charger) and `cms_get_fleet_firmware` (filtered, online-only, rollup + per-charger rows) — over a shared fan-out engine, and fix two defects they would otherwise inherit.

**Architecture:** A new `cms_mcp/fleet.py` holds the fan-out engine plus both tools, registered from `build_server` exactly like `register_device_tools`. The single-device schedule→poll→read core is extracted out of `cms_run_device_query` into a reusable `query_device()` so both the existing tool and the new engine share one code path. Later per-command tools (OCPP status, config) become ~5-line wrappers.

**Tech Stack:** Python 3.12, FastMCP, httpx (`CmsAuthClient`), pydantic-settings, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-07-15-cms-firmware-release-tools-design.md`

---

## Environment notes (read before Task 1)

- **Run tests from `cms-mcp-server/` and ALWAYS prefix with `rtk proxy`:**
  `cd cms-mcp-server && rtk proxy python -m pytest -q`
  A bare `python -m pytest` goes through an `rtk` hook that mangles results (false
  `ModuleNotFoundError: No module named 'cms_mcp'` / "No tests collected"). This is an
  environment quirk, not a real failure.
- Baseline before you start: **37 passed**.
- The tests use `httpx.MockTransport` (`tests/fake_cms.py`). **No network.**
- `tests/fake_cms.py` enforces content-type per endpoint on purpose. Do not loosen it —
  a permissive fake is exactly why a silently-broken tool shipped green once already
  (see `docs/superpowers/specs/2026-07-15-cms-firmware-release-tools-design.md` §1.1).

## File structure

| File | Responsibility |
|---|---|
| `cms_mcp/config.py` (modify) | 3 new fleet settings |
| `cms_mcp/device_commands.py` (modify) | Honest flap-vs-empty polling; extract reusable `query_device()` |
| `cms_mcp/fleet.py` (**create**) | Fleet status, fan-out engine, `_unwrap`, both firmware tools |
| `cms_mcp/server.py` (modify) | Register the fleet tools |
| `tests/fake_cms.py` (modify) | Fleet-map route with controllable pings; flap simulation |
| `tests/test_fleet.py` (**create**) | All new behaviour |
| `cms-mcp-server/README.md` (modify) | Document the two tools |

---

### Task 1: Fleet settings

**Files:**
- Modify: `cms-mcp-server/cms_mcp/config.py:19-23`

- [ ] **Step 1: Add the settings**

In `cms_mcp/config.py`, directly after the `device_audit_log_file` line, add:

```python
    # Fleet fan-out (cms_get_fleet_firmware and later per-command fleet tools).
    fleet_concurrency: int = 8              # max simultaneous device queries
    fleet_online_window_minutes: int = 15   # ping newer than this => charger is online
    fleet_max_targets: int = 100            # cap per call; overflow is reported, not dropped
```

- [ ] **Step 2: Verify settings load**

Run: `cd cms-mcp-server && rtk proxy python -m pytest -q`
Expected: **37 passed** (no behaviour change yet).

- [ ] **Step 3: Commit**

```bash
git add cms-mcp-server/cms_mcp/config.py
git commit -m "feat(cms): add fleet fan-out settings"
```

---

### Task 2: Poll honestly — a CMS flap is not "no result" (spec §6.1)

Today `_history_rows` has a bare `except Exception: return []`, so a `CmsUnavailableError`
(CMS flap) is indistinguishable from "the device hasn't answered yet" and surfaces as *"the
charger may be offline"* — pointing an engineer at healthy hardware.

Fix, preserving resilience: `_history_rows` stops swallowing; `_await_result` catches per
poll, keeps polling (a flap mid-poll must NOT fail the query), and if the budget expires
**having seen a flap**, raises `CmsUnavailableError` instead of returning `None`.

**Files:**
- Modify: `cms-mcp-server/cms_mcp/device_commands.py:143-171`
- Test: `cms-mcp-server/tests/test_device_commands.py`

> **Note for the implementer:** `CmsUnavailableError` **subclasses `RuntimeError`**
> (`auth.py:30`). Every `except` that handles both MUST list `CmsUnavailableError` FIRST,
> or a flap is silently reclassified as a timeout — the very bug this task fixes. This
> bites in Task 3 and Task 6 too.
>
> These tests drive `_await_result` directly by monkeypatching `_history_rows`. Do **not**
> test it by returning 502s from the transport: `CmsAuthClient` retries transient responses
> internally (`_RETRY_DELAYS = (0.5, 1.5)`, `auth.py:27`), so a 502 test would spend ~2s
> exercising the auth client's retry rather than `_await_result`'s logic.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_device_commands.py`:

```python
import httpx
import pytest

from cms_mcp import device_commands as dc
from cms_mcp.auth import CmsUnavailableError


def _flap() -> CmsUnavailableError:
    return CmsUnavailableError(httpx.Request("POST", "http://cms.test/x"),
                               httpx.Response(502))


@pytest.mark.asyncio
async def test_await_result_raises_cms_error_when_flapping_not_timeout(monkeypatch):
    """A flapping CMS must NOT be reported as a device/charger problem."""
    async def always_flap(client, charger_id, n):
        raise _flap()

    monkeypatch.setattr(dc, "_history_rows", always_flap)
    with pytest.raises(CmsUnavailableError):
        await dc._await_result(None, 1429, "release", 0, timeout=0.3, interval=0.01)


@pytest.mark.asyncio
async def test_await_result_returns_none_on_clean_timeout(monkeypatch):
    """Polls succeeded, device never answered => a genuine timeout, not a CMS error."""
    async def no_rows(client, charger_id, n):
        return []

    monkeypatch.setattr(dc, "_history_rows", no_rows)
    assert await dc._await_result(None, 1429, "release", 0,
                                  timeout=0.2, interval=0.01) is None


@pytest.mark.asyncio
async def test_await_result_survives_a_transient_flap_then_succeeds(monkeypatch):
    """A flap on ONE poll must not fail the query if a later poll finds the row."""
    calls = {"n": 0}
    row = {"ExecutionID": 5, "API": "release", "StatusID": 2,
           "Output": "E:\\ApiResponses\\r.txt"}

    async def flap_once(client, charger_id, n):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _flap()
        return [row]

    monkeypatch.setattr(dc, "_history_rows", flap_once)
    hit = await dc._await_result(None, 1429, "release", 0, timeout=2.0, interval=0.01)
    assert hit is not None and hit["ExecutionID"] == 5
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd cms-mcp-server && rtk proxy python -m pytest tests/test_device_commands.py -q -k await_result`
Expected: `test_await_result_raises_cms_error_when_flapping_not_timeout` FAILS —
`DID NOT RAISE CmsUnavailableError` (today the bare `except Exception` in `_history_rows`
swallows it and `_await_result` returns `None`, i.e. reports "timeout").

Note `_await_result` must call the module-global `_history_rows` for `monkeypatch.setattr`
to take effect — it already does. Passing `None` as the client is fine: the patched
`_history_rows` never uses it.

- [ ] **Step 3: Implement**

In `cms_mcp/device_commands.py`, add `CmsUnavailableError` to the auth import:

```python
from .auth import CmsUnavailableError
```

Replace `_history_rows` (currently lines 143-150) with:

```python
async def _history_rows(client, charger_id: int, n: int) -> list[dict]:
    """Fetch the latest `n` execution-history rows; [] when there are none.

    Does NOT swallow CmsUnavailableError: callers must be able to tell "the CMS is
    flapping" from "the device hasn't answered yet". Conflating the two is what makes
    a backend hiccup read as "the charger may be offline".
    """
    resp = await _post_datatables(client, _HISTORY_PATH, charger_id, n)
    return resp.get("data", []) if isinstance(resp, dict) else []
```

Replace `_await_result` (currently lines 159-171) with:

```python
async def _await_result(client, charger_id: int, resolved_api: str,
                        baseline: int, *, timeout: float, interval: float) -> Optional[dict]:
    """Poll history until our new row appears: ExecutionID > baseline, matching API
    path, with a non-empty Output.

    Returns the row, or None on a clean timeout (polls succeeded, the device never
    answered). Raises CmsUnavailableError if the budget expires and the CMS was
    flapping — that is a backend problem and must not be reported as a device one.
    A flap on a single poll is tolerated: we keep polling.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    last_flap: Optional[CmsUnavailableError] = None
    while loop.time() < deadline:
        await asyncio.sleep(interval)
        try:
            rows = await _history_rows(client, charger_id, 10)
        except CmsUnavailableError as exc:
            last_flap = exc
            continue
        last_flap = None
        for r in rows:
            if (int(r.get("ExecutionID") or 0) > baseline
                    and str(r.get("API")) == resolved_api
                    and r.get("Output")):
                return r
    if last_flap is not None:
        raise last_flap
    return None
```

Note `last_flap = None` after a successful poll: only a flap that persists to the end of
the budget is reported as a CMS error.

- [ ] **Step 4: Run tests**

Run: `cd cms-mcp-server && rtk proxy python -m pytest -q`
Expected: **40 passed** (37 + 3 new).

- [ ] **Step 5: Commit**

```bash
git add cms-mcp-server/cms_mcp/device_commands.py cms-mcp-server/tests/test_device_commands.py
git commit -m "fix(cms): report a flapping CMS as a CMS error, not 'charger may be offline'"
```

---

### Task 3: Extract the reusable single-device query core

Behaviour-preserving refactor: `cms_run_device_query` keeps its exact contract; the engine
gets a callable core. **No existing test may change.**

**Files:**
- Modify: `cms-mcp-server/cms_mcp/device_commands.py` (add `query_device`; rewire the tool)

- [ ] **Step 1: Add `query_device` above `register_device_tools`**

```python
async def query_device(client, charger_id: int, command: str,
                       args: Optional[dict] = None, *,
                       timeout: Optional[float] = None) -> dict:
    """Run ONE read-only (GET) device command and return its result.

    The shared core behind cms_run_device_query and the fleet fan-out: resolve the
    per-charger catalog (ApiID is per-charger), schedule via ExecuteApi, poll history
    for our row, then read the Output blob.

    Raises ValueError for an unknown/non-GET command, RuntimeError on a clean timeout,
    and CmsUnavailableError when the CMS is flapping (see _await_result).
    """
    catalog = await _load_catalog(client, charger_id)
    row = catalog.get(command)
    if row is None:
        near = sorted(k for k in catalog if command.split("/")[0] in k)[:5]
        hint = f" Did you mean: {', '.join(near)}?" if near else ""
        raise ValueError(f"Unknown command '{command}' for charger {charger_id}.{hint}")
    if str(row.get("ApiType")) != "GET":
        raise ValueError(
            f"'{command}' is a {row.get('ApiType')} (write); query_device runs read-only "
            f"GET commands only. Use cms_run_device_action_prepare.")
    resolved = resolve_path(command, args)
    await client._ensure_login()
    vvm = {"ChargerID": charger_id, "ApiID": row["ApiID"], "Input": resolved,
           "API": resolved, "UpdatedBy": getattr(client, "user_id", 0)}
    baseline = await _baseline_execid(client, charger_id)
    await _post_wrapped(client, _EXECUTE_PATH, "vvm", vvm, idempotent=False)
    hit = await _await_result(
        client, charger_id, resolved, baseline,
        timeout=timeout if timeout is not None else settings.device_poll_timeout,
        interval=settings.device_poll_interval)
    if hit is None:
        raise RuntimeError(
            f"No result for '{command}' on charger {charger_id} within "
            f"{timeout if timeout is not None else settings.device_poll_timeout:.0f}s — "
            f"the charger did not answer; it may be offline.")
    return {
        "command": command,
        "execution_id": hit.get("ExecutionID"), "status_id": hit.get("StatusID"),
        "executed_at": hit.get("strExecutedTime"),
        "result": await _read_log(client, hit["Output"]),
    }
```

- [ ] **Step 2: Rewire `cms_run_device_query` to call it**

Replace the body of `cms_run_device_query` (keep the decorator and docstring verbatim) with:

```python
        cid = await _resolve_charger_id(client, qcharger_id, charger_map)
        try:
            out = await query_device(client, cid, command, args)
        except CmsUnavailableError:
            # MUST precede `except RuntimeError`: CmsUnavailableError subclasses it.
            # Re-raise untouched — its message names the CMS as the culprit, which is
            # the whole point of Task 2. Folding it into the message below would blame
            # the charger for a backend flap.
            raise
        except RuntimeError as exc:
            raise RuntimeError(
                f"No result for '{command}' on {qcharger_id} within "
                f"{settings.device_poll_timeout:.0f}s — the charger did not answer; it "
                f"may be offline.") from exc
        return {"qcharger_id": qcharger_id, **out}
```

> **Why the message changed:** the old text said *"the charger may be offline **or the CMS
> is busy**"* — it hedged because the code genuinely could not tell the two apart. After
> Task 2 it can, so each path states one fact. Hedged text that covers both is what sent us
> chasing healthy hardware on 2026-07-15.

- [ ] **Step 3: Run the full suite — existing tests must be untouched and green**

Run: `cd cms-mcp-server && rtk proxy python -m pytest -q`
Expected: **40 passed**. If `test_query_end_to_end_reads_device_output` fails, the refactor
changed behaviour — fix the code, not the test.

- [ ] **Step 4: Commit**

```bash
git add cms-mcp-server/cms_mcp/device_commands.py
git commit -m "refactor(cms): extract reusable query_device core from cms_run_device_query"
```

---

### Task 4: Fake CMS — fleet map with controllable pings

`GetChargerDetailsForMap` currently 404s in the fake (it isn't in `_ROUTES`), so
`cms_list_chargers` is untested too. Add it with pings we control.

**Files:**
- Modify: `cms-mcp-server/tests/fake_cms.py`

- [ ] **Step 1: Add the fleet-map fixture and route**

Add near the other fixtures (after `_CATALOG`):

```python
def _ping(minutes_ago: float) -> str:
    """A /Date(ms)/ ping stamp `minutes_ago` in the past, as the live CMS emits."""
    return "/Date(%d)/" % int((time.time() - minutes_ago * 60) * 1000)


# GetChargerDetailsForMap: the fleet-wide map feed. The live payload carries per-charger
# Ping_Timestamp/StatusID; StatusID reads 0 even on healthy chargers, so ping recency is
# the only usable online signal (mirrored here deliberately).
def _fleet_map() -> list[dict]:
    return [
        {"QChargerID": "ab-sam-sim", "ChargerID": 1429, "Name": "Ador",
         "StatusID": 0, "IsActive": True, "Ping_Timestamp": _ping(1)},      # online
        {"QChargerID": "ab-sam-00585", "ChargerID": 1187, "Name": "Ador",
         "StatusID": 0, "IsActive": True, "Ping_Timestamp": _ping(2)},      # online
        {"QChargerID": "ab-sam-00591", "ChargerID": 1188, "Name": "Ador",
         "StatusID": 0, "IsActive": True, "Ping_Timestamp": _ping(600)},    # stale -> offline
    ]
```

Add `import time` to the imports at the top of the file.

In `_handler`, immediately after the `GetChargerByCustomerIDForDD` branch, add:

```python
    if path.endswith("/GetChargerDetailsForMap"):
        return httpx.Response(200, json=_fleet_map())
```

Extend `_CHARGERS` so the two extra chargers resolve:

```python
_CHARGERS = [
    {"ChargerID": 1429, "QChargerID": "ab-sam-sim"},
    {"ChargerID": 1187, "QChargerID": "ab-sam-00585"},
    {"ChargerID": 1188, "QChargerID": "ab-sam-00591"},
]
```

- [ ] **Step 2: Run the suite**

Run: `cd cms-mcp-server && rtk proxy python -m pytest -q`
Expected: **40 passed** (the fake gained a route; nothing else changed).

- [ ] **Step 3: Commit**

```bash
git add cms-mcp-server/tests/fake_cms.py
git commit -m "test(cms): fake the fleet-map feed with controllable ping stamps"
```

---

### Task 5: Fleet status + unwrap helpers

**Files:**
- Create: `cms-mcp-server/cms_mcp/fleet.py`
- Test: `cms-mcp-server/tests/test_fleet.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fleet.py`:

```python
"""Fleet fan-out engine and the firmware tools."""

import json
import time

import pytest
from fastmcp import Client

from cms_mcp.fleet import _unwrap, load_fleet_status
from tests.fake_cms import build_test_client, build_test_server


def test_unwrap_strips_double_encoding():
    # ReadJsonLogFile hands back a JSON string containing a JSON string.
    assert _unwrap(json.loads('"\\"release-1.2.65\\""')) == "release-1.2.65"


def test_unwrap_passes_through_plain_values():
    assert _unwrap("release-1.2.65") == "release-1.2.65"
    assert _unwrap("  release-1.2.65  ") == "release-1.2.65"
    assert _unwrap({"a": 1}) == {"a": 1}
    assert _unwrap(None) is None


@pytest.mark.asyncio
async def test_load_fleet_status_marks_online_by_ping_recency():
    async with build_test_client() as client:
        status = await load_fleet_status(client)
    assert status["ab-sam-sim"]["online"] is True
    assert status["ab-sam-sim"]["charger_id"] == 1429
    assert status["ab-sam-00591"]["online"] is False   # ping 600 min ago
```

- [ ] **Step 2: Add the `build_test_client` helper the tests need**

In `tests/fake_cms.py`, add below `build_test_server`:

```python
def build_test_client():
    """A CmsAuthClient wired to the fake CMS (for engine-level tests)."""
    return CmsAuthClient(base_url="http://cms.test", user="u", password="p",
                         transport=httpx.MockTransport(_handler))
```

- [ ] **Step 3: Run to verify failure**

Run: `cd cms-mcp-server && rtk proxy python -m pytest tests/test_fleet.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'cms_mcp.fleet'`.

- [ ] **Step 4: Create `cms_mcp/fleet.py` with the helpers**

```python
"""Fleet fan-out: ask one read-only device command across many chargers.

The old CMS stores no per-device state (firmware, config, ...) — its own UI asks each
charger at runtime, one at a time. Any fleet-wide answer is therefore N device
round-trips, which is why this module filters to online chargers first, caps the
target count, and bounds concurrency: the legacy backend flaps under load.

See docs/superpowers/specs/2026-07-15-cms-firmware-release-tools-design.md.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Optional

from .auth import CmsUnavailableError
from .config import settings
from .device_commands import query_device
from .server import _get_json_via, _resolve_charger_id

_MAP_PATH = "/ChargerDetails/GetChargerDetailsForMap"
_DATE_RE = re.compile(r"/Date\((\d+)")


def _ping_ms(value: Any) -> Optional[int]:
    """Epoch-ms out of the legacy `/Date(1781076330550)/` stamp; None if absent."""
    m = _DATE_RE.search(str(value or ""))
    return int(m.group(1)) if m else None


def _unwrap(result: Any) -> Any:
    """Normalise a device payload for display.

    ReadJsonLogFile double-encodes text results: the file holds a JSON string, so a
    parse yields '"release-1.2.65"' — quotes and all. Decode one extra layer when the
    value is itself a JSON scalar, and trim. Non-string payloads pass through
    untouched. Applied by the firmware wrappers only; query_device keeps output raw.
    """
    if not isinstance(result, str):
        return result
    text = result.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        try:
            inner = json.loads(text)
        except ValueError:
            return text
        if isinstance(inner, str):
            return inner.strip()
    return text


async def load_fleet_status(client) -> dict[str, dict]:
    """ONE call -> {qcharger_id: {charger_id, ping_ms, online}} for the whole fleet.

    `online` is ping recency, NOT StatusID: the live feed returns StatusID 0 even for
    healthy, actively-pinging chargers, so it cannot be used for this.
    """
    rows = await _get_json_via(client, _MAP_PATH)
    window_ms = settings.fleet_online_window_minutes * 60_000
    now_ms = time.time() * 1000
    out: dict[str, dict] = {}
    for r in rows:
        qid = r.get("QChargerID")
        if not qid:
            continue
        ping = _ping_ms(r.get("Ping_Timestamp"))
        out[str(qid)] = {
            "charger_id": r.get("ChargerID"),
            "ping_ms": ping,
            "online": ping is not None and (now_ms - ping) <= window_ms,
        }
    return out
```

- [ ] **Step 5: Add the `_get_json_via` helper `fleet.py` imports**

`server.py`'s `_get_json` is a closure inside `build_server`, so it isn't importable. Add
this module-level helper to `cms_mcp/server.py`, directly after `_post_datatables`:

```python
async def _get_json_via(client, path: str, params: Optional[dict] = None) -> Any:
    """GET a legacy MVC JSON action. Module-level twin of build_server's _get_json,
    for modules (fleet.py) that hold a client but not the closure."""
    resp = await client.get(path, params=params or {})
    resp.raise_for_status()
    if not resp.text.strip():
        return []
    return resp.json()
```

- [ ] **Step 6: Run tests**

Run: `cd cms-mcp-server && rtk proxy python -m pytest tests/test_fleet.py -q`
Expected: **3 passed**.

- [ ] **Step 7: Commit**

```bash
git add cms-mcp-server/cms_mcp/fleet.py cms-mcp-server/cms_mcp/server.py cms-mcp-server/tests/fake_cms.py cms-mcp-server/tests/test_fleet.py
git commit -m "feat(cms): fleet status (ping-recency online) and output unwrap helpers"
```

---

### Task 6: The fan-out engine

**Files:**
- Modify: `cms-mcp-server/cms_mcp/fleet.py`
- Test: `cms-mcp-server/tests/test_fleet.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fleet.py`:

```python
from cms_mcp.fleet import fan_out


@pytest.mark.asyncio
async def test_fan_out_classifies_each_charger():
    async with build_test_client() as client:
        rows = await fan_out(client, {"ab-sam-sim": 1429}, "id", timeout=5.0)
    assert rows[0]["qcharger_id"] == "ab-sam-sim"
    assert rows[0]["status"] == "ok"
    assert rows[0]["result"] == "device-ok"


@pytest.mark.asyncio
async def test_fan_out_marks_unsupported_when_catalog_lacks_command():
    async with build_test_client() as client:
        rows = await fan_out(client, {"ab-sam-sim": 1429}, "no-such-command", timeout=5.0)
    assert rows[0]["status"] == "unsupported"
    assert rows[0]["result"] is None


@pytest.mark.asyncio
async def test_fan_out_never_exceeds_configured_concurrency(monkeypatch):
    monkeypatch.setattr(settings, "fleet_concurrency", 2)
    live = {"now": 0, "peak": 0}
    real = fleet_mod.query_device

    async def spy(client, cid, command, args=None, *, timeout=None):
        live["now"] += 1
        live["peak"] = max(live["peak"], live["now"])
        try:
            await asyncio.sleep(0.05)
            return await real(client, cid, command, args, timeout=timeout)
        finally:
            live["now"] -= 1

    monkeypatch.setattr(fleet_mod, "query_device", spy)
    targets = {f"c{i}": 1429 for i in range(8)}
    async with build_test_client() as client:
        await fan_out(client, targets, "id", timeout=5.0)
    assert live["peak"] <= 2, f"peak concurrency {live['peak']} exceeded cap 2"


@pytest.mark.asyncio
async def test_fan_out_one_failure_does_not_fail_the_batch():
    async with build_test_client() as client:
        rows = await fan_out(client, {"ab-sam-sim": 1429, "bad": 9999}, "id", timeout=5.0)
    by_id = {r["qcharger_id"]: r for r in rows}
    assert by_id["ab-sam-sim"]["status"] == "ok"
    assert by_id["bad"]["status"] in ("timeout", "unsupported", "error")
```

Add these imports at the top of `tests/test_fleet.py`:

```python
import asyncio

from cms_mcp import fleet as fleet_mod
from cms_mcp.config import settings
```

- [ ] **Step 2: Run to verify failure**

Run: `cd cms-mcp-server && rtk proxy python -m pytest tests/test_fleet.py -q -k fan_out`
Expected: FAIL — `ImportError: cannot import name 'fan_out'`.

- [ ] **Step 3: Implement `fan_out` in `cms_mcp/fleet.py`**

```python
async def _query_one(client, sem: asyncio.Semaphore, qcharger_id: str,
                     charger_id: int, command: str, args: Optional[dict],
                     timeout: float) -> dict:
    """One charger's result, classified. Never raises: a batch must survive any
    single charger, and every failure mode is a reportable fact, not an error."""
    row: dict = {"qcharger_id": qcharger_id, "result": None, "status": "error"}
    async with sem:
        try:
            out = await query_device(client, charger_id, command, args, timeout=timeout)
            row.update(status="ok", result=_unwrap(out.get("result")),
                       execution_id=out.get("execution_id"),
                       executed_at=out.get("executed_at"))
        except CmsUnavailableError:
            # The CMS flapped. Says nothing about the charger — retryable.
            # MUST stay above `except RuntimeError`: CmsUnavailableError subclasses it,
            # so reversing these two silently reclassifies every flap as a device fault.
            row["status"] = "cms_error"
        except RuntimeError:
            # Scheduled, but the device never answered within the budget.
            row["status"] = "timeout"
        except ValueError as exc:
            # This charger's catalog has no such command (ApiID is per-charger).
            row["status"] = "unsupported"
            row["detail"] = str(exc)
        except Exception as exc:  # noqa: BLE001 - never let one charger fail the batch
            row["detail"] = f"{type(exc).__name__}: {exc}"
    return row


async def fan_out(client, targets: dict[str, int], command: str,
                  args: Optional[dict] = None, *,
                  timeout: Optional[float] = None) -> list[dict]:
    """Run `command` across {qcharger_id: charger_id}, bounded by fleet_concurrency.

    Returns one classified row per target, in the order given. Concurrency is bounded
    because the legacy CMS flaps under load — this is a safety limit, not a tuning knob.
    """
    sem = asyncio.Semaphore(settings.fleet_concurrency)
    budget = timeout if timeout is not None else settings.device_poll_timeout
    return list(await asyncio.gather(*(
        _query_one(client, sem, qid, cid, command, args, budget)
        for qid, cid in targets.items())))
```

- [ ] **Step 4: Run tests**

Run: `cd cms-mcp-server && rtk proxy python -m pytest tests/test_fleet.py -q`
Expected: **7 passed**.

- [ ] **Step 5: Commit**

```bash
git add cms-mcp-server/cms_mcp/fleet.py cms-mcp-server/tests/test_fleet.py
git commit -m "feat(cms): bounded fleet fan-out engine with per-charger status classification"
```

---

### Task 7: The two firmware tools

**Files:**
- Modify: `cms-mcp-server/cms_mcp/fleet.py`
- Modify: `cms-mcp-server/cms_mcp/server.py` (register)
- Test: `cms-mcp-server/tests/test_fleet.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fleet.py`:

```python
async def _call(name, **kw):
    async with Client(build_test_server()) as client:
        return await client.call_tool(name, kw)


@pytest.mark.asyncio
async def test_get_charger_firmware_returns_unquoted_release():
    res = await _call("cms_get_charger_firmware", qcharger_id="ab-sam-sim")
    assert res.data["status"] == "ok"
    assert res.data["release"] == "release-1.2.65"   # NOT '"release-1.2.65"'
    assert res.data["qcharger_id"] == "ab-sam-sim"


@pytest.mark.asyncio
async def test_fleet_firmware_skips_offline_and_names_them():
    res = await _call("cms_get_fleet_firmware", search="ab-sam")
    d = res.data
    assert d["skipped_offline"] == 1
    assert d["skipped_offline_ids"] == ["ab-sam-00591"]      # ping 600 min ago
    assert "ab-sam-00591" not in [c["qcharger_id"] for c in d["chargers"]]
    assert d["queried"] == len(d["chargers"])
    assert sum(d["rollup"].values()) == d["queried"]         # spec §4.2 invariant


@pytest.mark.asyncio
async def test_fleet_firmware_include_offline_queries_them():
    res = await _call("cms_get_fleet_firmware", search="ab-sam", include_offline=True)
    assert res.data["skipped_offline"] == 0
    assert "ab-sam-00591" in [c["qcharger_id"] for c in res.data["chargers"]]


@pytest.mark.asyncio
async def test_fleet_firmware_caps_targets_and_reports_the_overflow():
    res = await _call("cms_get_fleet_firmware", search="ab-sam", limit=1)
    d = res.data
    assert d["queried"] == 1
    assert d["skipped_over_cap"] == 1        # 2 online match; 1 queried
    assert d["queried"] + d["skipped_offline"] + d["skipped_over_cap"] == 3


@pytest.mark.asyncio
async def test_fleet_firmware_requires_a_filter():
    with pytest.raises(Exception, match="search"):
        await _call("cms_get_fleet_firmware")


@pytest.mark.asyncio
async def test_fleet_firmware_explicit_ids_are_queried_and_rollup_groups_by_release():
    res = await _call("cms_get_fleet_firmware", qcharger_ids=["ab-sam-sim", "ab-sam-00591"],
                      include_offline=True)
    d = res.data
    assert d["rollup"].get("release-1.2.65") == 2
    assert sum(d["rollup"].values()) == d["queried"]


@pytest.mark.asyncio
async def test_fleet_firmware_rollup_buckets_a_failure_as_unreachable(monkeypatch):
    """A charger that cannot be reached must be counted, not dropped: rollup must still
    reconcile with `queried`, so 'we couldn't ask' never reads as 'it has no firmware'."""
    real = fleet_mod.query_device

    async def fail_one(client, cid, command, args=None, *, timeout=None):
        if cid == 1187:                      # ab-sam-00585
            raise RuntimeError("device silent")
        return await real(client, cid, command, args, timeout=timeout)

    monkeypatch.setattr(fleet_mod, "query_device", fail_one)
    res = await _call("cms_get_fleet_firmware", search="ab-sam")
    d = res.data
    assert d["rollup"].get("unreachable") == 1
    assert d["rollup"].get("release-1.2.65") == 1
    assert sum(d["rollup"].values()) == d["queried"] == 2
    assert [c["status"] for c in d["chargers"] if c["qcharger_id"] == "ab-sam-00585"] == ["timeout"]


@pytest.mark.asyncio
async def test_firmware_tools_never_reach_a_write(monkeypatch):
    """Spec §10.10 / §11: these tools are read-only. `release` is a GET, and nothing here
    may touch the gated write path, with or without a confirmation token."""
    seen: list[str] = []
    real = fleet_mod.query_device

    async def spy(client, cid, command, args=None, *, timeout=None):
        seen.append(command)
        return await real(client, cid, command, args, timeout=timeout)

    monkeypatch.setattr(fleet_mod, "query_device", spy)
    await _call("cms_get_charger_firmware", qcharger_id="ab-sam-sim")
    await _call("cms_get_fleet_firmware", search="ab-sam")
    assert set(seen) == {"release"}, f"unexpected commands issued: {seen}"
    # query_device itself refuses non-GET, so a write cannot be reached through it.
    assert "reset" not in seen
```

- [ ] **Step 2: Make the fake return a release for the `release` command**

In `tests/fake_cms.py`, add a `release` row to `_CATALOG`:

```python
    {"ApiID": 7, "Name": "Release", "API": "release", "ApiType": "GET", "ApiTypeID": 1,
     "ApiDescription": "Release", "CategoryID": 1, "IsInputRequired": False,
     "IsActive": True},
```

`test_list_charger_actions_catalog` in `tests/test_tools.py` asserts exactly 2 entries —
update that assertion to 3 and add `"release"` to its expected names:

```python
    assert len(rows) == 3
    names = {r["API"] for r in rows}
    assert {"id", "reset", "release"} <= names
```

In `_handler`'s `/ExecuteApi` branch, make the stored output double-encoded for `release`,
mirroring the live CMS (this is what `_unwrap` must strip):

```python
        _LOG_FILES[outpath] = ('"release-1.2.65"' if vvm["API"] == "release"
                               else {"api": vvm["API"], "value": "device-ok"})
```

- [ ] **Step 3: Run to verify failure**

Run: `cd cms-mcp-server && rtk proxy python -m pytest tests/test_fleet.py -q -k firmware`
Expected: FAIL — `Unknown tool: cms_get_charger_firmware`.

- [ ] **Step 4: Implement the tools in `cms_mcp/fleet.py`**

```python
def register_fleet_tools(mcp, client, charger_map: dict) -> None:
    """Register the firmware tools onto `mcp`, sharing the server's charger map."""

    @mcp.tool
    async def cms_get_charger_firmware(qcharger_id: str) -> dict:
        """Firmware/release version running on ONE charger (by QChargerID, e.g.
        "ab-sam-00585") in the old CMS — e.g. "release-1.2.65". Read-only: runs the
        device's `release` GET. The CMS does not store firmware, so this asks the
        charger live and takes a few seconds. For several chargers at once use
        cms_get_fleet_firmware. `status` is "ok" when `release` is populated; a
        charger that cannot be reached reports status "timeout" (device silent) or
        "cms_error" (CMS busy — retry) rather than failing."""
        cid = await _resolve_charger_id(client, qcharger_id, charger_map)
        sem = asyncio.Semaphore(1)
        row = await _query_one(client, sem, qcharger_id, cid, "release", None,
                               settings.device_poll_timeout)
        return {"qcharger_id": qcharger_id, "release": row["result"],
                "status": row["status"],
                **({"detail": row["detail"]} if "detail" in row else {}),
                "execution_id": row.get("execution_id"),
                "executed_at": row.get("executed_at")}

    @mcp.tool
    async def cms_get_fleet_firmware(
        search: Optional[str] = None,
        qcharger_ids: Optional[list[str]] = None,
        include_offline: bool = False,
        limit: int = 0,
    ) -> dict:
        """Firmware/release versions across MANY chargers in the old CMS, e.g. "what
        releases are running across Ador's chargers?". Give EITHER `search` (matches
        QChargerID or customer name, as cms_list_chargers) OR an explicit
        `qcharger_ids` list — an unfiltered whole-fleet sweep is not available, because
        the CMS stores no firmware and each charger must be asked live.

        Offline chargers (no recent ping) are skipped and named in
        skipped_offline_ids; pass include_offline=True to query them anyway. Returns a
        `rollup` of release -> count (failures counted as "unreachable") plus a
        `chargers` row per charger queried. `queried + skipped_offline +
        skipped_over_cap` always equals the number of chargers matched, so a partial
        answer is never mistaken for a complete one."""
        if bool(search) == bool(qcharger_ids):
            raise ValueError(
                "Give exactly one of `search` (e.g. a customer name or QChargerID "
                "prefix) or `qcharger_ids`. An unfiltered fleet sweep is not offered: "
                "every charger must be asked live.")
        cap = limit if limit and limit > 0 else settings.fleet_max_targets

        status = await load_fleet_status(client)
        if qcharger_ids:
            matched = [q for q in (str(x) for x in qcharger_ids)]
            missing = [q for q in matched if q not in status]
            if missing:
                raise ValueError(f"Unknown QChargerID(s): {', '.join(missing[:5])}")
        else:
            s = search.lower()
            matched = sorted(q for q in status if s in q.lower())
            if not matched:
                raise ValueError(f"No chargers matched '{search}'.")

        offline_ids = ([] if include_offline
                       else [q for q in matched if not status[q]["online"]])
        eligible = [q for q in matched if q not in set(offline_ids)]
        targets_ids, over_cap = eligible[:cap], eligible[cap:]
        targets = {q: status[q]["charger_id"] for q in targets_ids}

        rows = await fan_out(client, targets, "release")

        rollup: dict[str, int] = {}
        for r in rows:
            key = r["result"] if r["status"] == "ok" else "unreachable"
            rollup[str(key)] = rollup.get(str(key), 0) + 1
        return {
            "rollup": rollup,
            "queried": len(rows),
            "skipped_offline": len(offline_ids),
            "skipped_offline_ids": offline_ids,
            "skipped_over_cap": len(over_cap),
            "chargers": [{"qcharger_id": r["qcharger_id"], "release": r["result"],
                          "status": r["status"]} for r in rows],
        }
```

- [ ] **Step 5: Register in `server.py`**

In `cms_mcp/server.py`, in `build_server`, next to the existing device-tool registration
immediately before `return mcp` (function-level import breaks the import cycle — `fleet.py`
imports from `server.py` at module top):

```python
    from .fleet import register_fleet_tools
    register_fleet_tools(mcp, client, _charger_map)
```

- [ ] **Step 6: Run the full suite**

Run: `cd cms-mcp-server && rtk proxy python -m pytest -q`
Expected: **55 passed** (37 baseline + 3 Task 2 + 3 Task 5 + 4 Task 6 + 8 Task 7).

- [ ] **Step 7: Commit**

```bash
git add cms-mcp-server/cms_mcp/fleet.py cms-mcp-server/cms_mcp/server.py cms-mcp-server/tests/fake_cms.py cms-mcp-server/tests/test_fleet.py cms-mcp-server/tests/test_tools.py
git commit -m "feat(cms): cms_get_charger_firmware and cms_get_fleet_firmware"
```

---

### Task 8: Documentation

**Files:**
- Modify: `cms-mcp-server/README.md`

- [ ] **Step 1: Add the two tools to the per-charger tool table**

After the `cms_read_device_log` row, add:

```markdown
| `cms_get_charger_firmware` | ExecuteApi `release` | firmware/release running on ONE charger (e.g. `release-1.2.65`) |
| `cms_get_fleet_firmware` | GetChargerDetailsForMap + ExecuteApi `release` | releases across MANY chargers (filtered, online-only): rollup + per-charger rows |
```

- [ ] **Step 2: Add a Firmware section after "Device commands (gated)"**

```markdown
## Firmware / release

The old CMS **stores no firmware** — its own Actions page asks each charger at runtime.
So a fleet-wide answer is inherently one device round-trip per charger (~7s each), and
`cms_get_fleet_firmware` is shaped around that:

- **A filter is required** (`search` or `qcharger_ids`). No whole-fleet sweep: ~700
  online chargers would mean ~10 minutes of load on a backend that flaps under load.
- **Online-only by default.** Online is decided by `Ping_Timestamp` recency
  (`FLEET_ONLINE_WINDOW_MINUTES`, default 15) — *not* `StatusID`, which reads 0 even on
  healthy chargers. Skipped chargers are **named** in `skipped_offline_ids`.
- **Bounded:** `FLEET_CONCURRENCY` (8) simultaneous queries, `FLEET_MAX_TARGETS` (100)
  per call. Overflow is reported in `skipped_over_cap`, never silently dropped.
- `queried + skipped_offline + skipped_over_cap` always equals the number matched.

Per-charger `status` distinguishes `ok` / `timeout` (device silent) / `cms_error` (CMS
busy — retry) / `unsupported` (this charger's catalog has no `release`) / `error`.
`timeout` and `cms_error` are deliberately separate: a flapping backend must never be
reported as a charger fault.
```

- [ ] **Step 3: Commit**

```bash
git add cms-mcp-server/README.md
git commit -m "docs(cms): document the firmware/release tools"
```

---

### Task 9: Live smoke test (human-run, needs credentials)

Not runnable in CI — needs live CMS credentials and online chargers. **Do not skip:** every
unit test here runs against a fake, and a permissive fake is exactly what let a fully-broken
tool ship green once already.

- [ ] **Step 1: Rebuild and restart the container**

```bash
cd chat && docker compose build cms-mcp-server && docker compose up -d cms-mcp-server
```

- [ ] **Step 2: Single charger — expect a clean, unquoted release**

```bash
docker exec cms-mcp-server python -c "
import asyncio
from fastmcp import Client
from cms_mcp.server import build_server
async def main():
    async with Client(build_server()) as c:
        r = await c.call_tool('cms_get_charger_firmware', {'qcharger_id':'ab-sam-04104'})
        print(r.data)
asyncio.run(main())
"
```

Expected: `release` is exactly `release-1.2.65` — **no surrounding quotes** — and
`status` is `ok`. Quotes present ⇒ `_unwrap` is not wired in.

- [ ] **Step 3: Small fleet — verify the reconciliation invariant on real data**

```bash
docker exec cms-mcp-server python -c "
import asyncio, json
from fastmcp import Client
from cms_mcp.server import build_server
async def main():
    async with Client(build_server()) as c:
        r = await c.call_tool('cms_get_fleet_firmware', {'search':'ab-int64','limit':5})
        d = r.data
        print(json.dumps({k:v for k,v in d.items() if k!='chargers'}, indent=2))
        for row in d['chargers']: print(' ', row)
        assert sum(d['rollup'].values()) == d['queried'], 'rollup != queried'
        print('INVARIANT OK')
asyncio.run(main())
"
```

Expected: `sum(rollup) == queried`; every charger row carries a status from §5; the run
takes roughly `queried/8 * 7s`.

- [ ] **Step 4: Record the outcome**

Note the observed releases and any charger reporting `unsupported` (a model whose catalog
lacks `release`) in the PR/commit message. If any charger returns `cms_error`, re-run —
that is the backend, not the tool.

---

## Definition of done

- [ ] `rtk proxy python -m pytest -q` → **55 passed**
- [ ] Live smoke (Task 9) done: real `release-1.2.65`, unquoted, invariant holds
- [ ] No write path touched: `WRITE_ALLOWLIST` unchanged; the new tools only ever run GETs
- [ ] `cms_run_device_query` behaviour unchanged (its tests untouched and green)
- [ ] Release/version bump deliberately NOT done — `feat/mcp-server` is shared and the
      release is coordinated. Do not bump, tag, or push without the user's say-so.
