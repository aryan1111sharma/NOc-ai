# CMS Device-Command Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add gated device-command tools to `cms-mcp-server` — read-only diagnostics (all GET commands), a curated 8-command write allow-list behind a two-step confirm gate, and a session/alert state-log reader — over the old NOC's `ExecuteApi` / `GetApiExecutionDetailsByID` / `ReadJsonLogFile` endpoints.

**Architecture:** A new focused module `cms_mcp/device_commands.py` registers 4 MCP tools onto the existing FastMCP server, reusing the charger-id resolver and POST helpers already in `server.py`. Writes mirror the two-step audited token gate in `mcp-server/noc_mcp/ssh_exec.py`. `ExecuteApi` is asynchronous, so the read tool schedules → polls execution history for its own new row (by resolved API path + `ExecutionID > baseline`) → reads the result file via `ReadJsonLogFile`.

**Tech Stack:** Python 3.12, FastMCP, httpx (async), pytest + pytest-asyncio, pydantic-settings. Reference spec: `docs/superpowers/specs/2026-07-14-cms-device-command-tools-design.md`. Command reference: `cms-mcp-server/docs/device-api-catalog.md`.

---

## Conventions for the engineer

- All commands run from `cms-mcp-server/` unless noted. Run tests with `python -m pytest -q`.
- The legacy CMS returns one ~206-field "god" DTO; existing tools trim to whitelists. Reuse the existing `_trim`, `_resolve_charger_id`, `_post_json`, `_datatables_body` from `cms_mcp/server.py` — **do not** re-implement them.
- `ExecuteApi` and `ReadJsonLogFile` take a **non-standard** body: a string like `{vvm: <json>}` / `{Paths: <json>}` where the outer key is unquoted. Build it as a raw string (see `_post_wrapped`), never `json=`.
- `ExecuteApi` is **async**: it returns the JSON string `"API scheduled successfully !"`, not the result. The device result appears later in the execution history with an `Output` file path, read via `ReadJsonLogFile`.
- Import cycle note: `device_commands.py` imports helpers from `server.py` at module top; `server.py` imports `device_commands` **inside** `build_server()` (function-level) to break the cycle.

---

## Task 1: Add device-command settings to config

**Files:**
- Modify: `cms-mcp-server/cms_mcp/config.py`
- Test: `cms-mcp-server/tests/test_device_commands.py` (create)

- [ ] **Step 1: Write the failing test**

Create `cms-mcp-server/tests/test_device_commands.py`:

```python
"""Unit + integration tests for the CMS device-command tools."""

import os

# Settings requires cms_user/cms_pass; provide them before importing config.
os.environ.setdefault("CMS_USER", "u")
os.environ.setdefault("CMS_PASS", "p")


def test_device_settings_defaults():
    from cms_mcp.config import settings
    assert settings.device_poll_timeout == 20.0
    assert settings.device_poll_interval == 2.0
    assert settings.device_pending_ttl_seconds == 120
    assert settings.device_audit_log_file is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_device_commands.py::test_device_settings_defaults -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'device_poll_timeout'`

- [ ] **Step 3: Add the settings**

In `cms_mcp/config.py`, inside the `Settings` class, after the `request_timeout` line add:

```python
    # Device-command tools (ExecuteApi / ReadJsonLogFile).
    device_poll_timeout: float = 20.0       # max wait for an async execution result
    device_poll_interval: float = 2.0       # delay between history polls
    device_pending_ttl_seconds: int = 120   # single-use write-confirm token lifetime
    device_audit_log_file: str | None = None  # audit sink; None -> stderr
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_device_commands.py::test_device_settings_defaults -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cms-mcp-server/cms_mcp/config.py cms-mcp-server/tests/test_device_commands.py
git commit -m "feat(cms): add device-command settings"
```

---

## Task 2: Capture CMS UserID on login

`vvm.UpdatedBy` must carry the CMS UserID (the login response returns it, e.g. `74`). Store it on the auth client.

**Files:**
- Modify: `cms-mcp-server/cms_mcp/auth.py`
- Test: `cms-mcp-server/tests/test_device_commands.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_device_commands.py`:

```python
import httpx
import pytest
from cms_mcp.auth import CmsAuthClient


def _login_ok(request):
    return httpx.Response(200, json={"UserID": 74},
                          headers={"set-cookie": ".ASPXAUTH=abc; path=/"})


@pytest.mark.asyncio
async def test_login_captures_user_id():
    def handler(request):
        if request.url.path.endswith("/Home/GetUsersByEmailAndPassword"):
            return _login_ok(request)
        return httpx.Response(200, json=[])

    async with CmsAuthClient(base_url="http://cms.test", user="u", password="p",
                             transport=httpx.MockTransport(handler)) as c:
        assert c.user_id == 0            # not logged in yet
        await c._ensure_login()
        assert c.user_id == 74
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_device_commands.py::test_login_captures_user_id -v`
Expected: FAIL with `AttributeError: 'CmsAuthClient' object has no attribute 'user_id'`

- [ ] **Step 3: Implement**

In `cms_mcp/auth.py`, in `CmsAuthClient.__init__`, after `self._logged_in = False` add:

```python
        self.user_id = 0
```

In `_login`, replace the `ok`-computing block:

```python
        ok = False
        try:
            ok = int(resp.json().get("UserID", 0)) >= 1
        except Exception:
            ok = False
```

with:

```python
        ok = False
        try:
            uid = int(resp.json().get("UserID", 0))
            ok = uid >= 1
            if ok:
                self.user_id = uid
        except Exception:
            ok = False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_device_commands.py::test_login_captures_user_id tests/test_auth.py -q`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add cms-mcp-server/cms_mcp/auth.py cms-mcp-server/tests/test_device_commands.py
git commit -m "feat(cms): capture CMS UserID on login for ExecuteApi audit"
```

---

## Task 3: Device-commands module — allow-lists, path guard, helpers

Create the module skeleton with pure-logic helpers first (no network): write allow-list, path guard, placeholder substitution, and the pending-token store. These mirror `noc_mcp/ssh_exec.py`.

**Files:**
- Create: `cms-mcp-server/cms_mcp/device_commands.py`
- Test: `cms-mcp-server/tests/test_device_commands.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_device_commands.py`:

```python
def test_write_allowlist_membership():
    from cms_mcp.device_commands import WRITE_ALLOWLIST
    assert "reset" in WRITE_ALLOWLIST
    assert "ocpp-client/restart" in WRITE_ALLOWLIST
    assert "outlets/{number}/stop" in WRITE_ALLOWLIST
    # Tier-3 / off-list must NOT be present
    assert "scripts/active" not in WRITE_ALLOWLIST
    assert "outlets/{number}/e-stop" not in WRITE_ALLOWLIST
    assert len(WRITE_ALLOWLIST) == 8


def test_resolve_path_substitutes_and_validates():
    from cms_mcp.device_commands import resolve_path
    assert resolve_path("ocpp-client/restart", None) == "ocpp-client/restart"
    assert resolve_path("outlets/{number}/stop", {"number": 1}) == "outlets/1/stop"
    with __import__("pytest").raises(ValueError) as ei:
        resolve_path("outlets/{number}/stop", {})
    assert "number" in str(ei.value)


def test_log_path_guard():
    from cms_mcp.device_commands import is_safe_log_path
    assert is_safe_log_path(r"E:\ApiResponses\r.txt")
    assert is_safe_log_path(r"E:\CMS_Chargers_State_Logs\x.json")
    assert not is_safe_log_path(r"E:\Windows\System32\config\SAM")
    assert not is_safe_log_path(r"..\..\secret.txt")
    assert not is_safe_log_path(r"E:\ApiResponses\r.exe")


def test_pending_token_single_use_and_expiry():
    from cms_mcp import device_commands as dc
    dc._PENDING.clear()
    token = dc._mint_pending({"charger": "x"})
    assert isinstance(token, str) and len(token) > 20
    got = dc._pop_pending(token, ttl=120)
    assert got["charger"] == "x"
    assert dc._pop_pending(token, ttl=120) is None   # single-use
    # expired
    token2 = dc._mint_pending({"charger": "y"})
    dc._PENDING[token2]["created_at"] -= 999
    assert dc._pop_pending(token2, ttl=120) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_device_commands.py -k "allowlist or resolve_path or log_path or pending" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cms_mcp.device_commands'`

- [ ] **Step 3: Create the module with helpers**

Create `cms_mcp/device_commands.py`:

```python
"""Device-command tools over the old NOC ChargerDetails endpoints.

Three capabilities (4 MCP tools):
  * cms_run_device_query          — read-only device diagnostics (all GET commands)
  * cms_run_device_action_prepare / _execute — gated writes (curated recovery
    allow-list) behind a two-step, audited confirm gate (mirrors
    mcp-server/noc_mcp/ssh_exec.py)
  * cms_read_device_log           — read a session/alert state-log blob

ExecuteApi is asynchronous: it returns a scheduling ack, and the device result
appears later in the execution history with an Output file path read via
ReadJsonLogFile. See docs/superpowers/specs/2026-07-14-cms-device-command-tools-design.md.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from typing import Any, Optional

from .config import settings
from .server import _resolve_charger_id, _post_json, _datatables_body

_EXECUTE_PATH = "/ChargerDetails/ExecuteApi"
_HISTORY_PATH = "/ChargerDetails/GetApiExecutionDetailsByID"
_READLOG_PATH = "/ChargerDetails/ReadJsonLogFile"
_CATALOG_PATH = "/ChargerDetails/GetAllApisByChargerID"

# The ONLY write commands runnable via cms_run_device_action. Matched against the
# catalog `API` template. Everything else write is unreachable in code.
WRITE_ALLOWLIST = frozenset({
    "reset",
    "controllers/{scripts}/api/outlets/ccs/restartStack",
    "controllers/{seccid}/api/system/reboot",
    "ocpp-client/restart",
    "ocpp-client/start",
    "ocpp-client/stop",
    "outlets/{number}/stop",
    "services/rfid/clear",
})

# ReadJsonLogFile appears to read arbitrary server paths; constrain to the known
# result / state-log roots so the tool can't become a file-read primitive.
_SAFE_LOG_PATH_RE = re.compile(
    r"^[A-Za-z]:\\(ApiResponses|CMS[\w ]*State_Logs)\\[^\r\n]+\.(txt|json|log)$",
    re.IGNORECASE,
)
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")

# Single-use write-confirm tokens: token -> {..request.., created_at}
_PENDING: dict[str, dict] = {}


def is_safe_log_path(path: str) -> bool:
    """True if `path` is under a known CMS result/state-log root (guards ReadJsonLogFile)."""
    if not isinstance(path, str) or ".." in path:
        return False
    return bool(_SAFE_LOG_PATH_RE.match(path.strip()))


def resolve_path(api_template: str, args: Optional[dict]) -> str:
    """Substitute {placeholders} in a catalog API template. Raises ValueError if any
    placeholder is unfilled."""
    args = args or {}
    holes = _PLACEHOLDER_RE.findall(api_template)
    missing = [h for h in holes if h not in args]
    if missing:
        raise ValueError(
            f"command '{api_template}' needs args for: {', '.join(missing)}")
    out = api_template
    for h in holes:
        out = out.replace("{" + h + "}", str(args[h]))
    return out


def _mint_pending(req: dict) -> str:
    """Store a pending write request, sweep expired entries, return a single-use token."""
    now = time.time()
    ttl = settings.device_pending_ttl_seconds
    for stale in [t for t, r in _PENDING.items() if now - r["created_at"] > ttl]:
        _PENDING.pop(stale, None)
    token = secrets.token_urlsafe(24)
    _PENDING[token] = {**req, "created_at": now}
    return token


def _pop_pending(token: str, ttl: int) -> Optional[dict]:
    """Consume a pending request by token. None if unknown, used, or expired."""
    req = _PENDING.pop(token, None)
    if req is None or time.time() - req["created_at"] > ttl:
        return None
    return req
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_device_commands.py -k "allowlist or resolve_path or log_path or pending" -v`
Expected: PASS (4)

- [ ] **Step 5: Commit**

```bash
git add cms-mcp-server/cms_mcp/device_commands.py cms-mcp-server/tests/test_device_commands.py
git commit -m "feat(cms): device-commands module — allow-list, path guard, token store"
```

---

## Task 4: Network helpers — wrapped POST, catalog cache, result correlation

**Files:**
- Modify: `cms-mcp-server/cms_mcp/device_commands.py`
- Test: `cms-mcp-server/tests/test_device_commands.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_device_commands.py`:

```python
@pytest.mark.asyncio
async def test_post_wrapped_builds_vvm_body():
    seen = {}

    def handler(request):
        if request.url.path.endswith("/Home/GetUsersByEmailAndPassword"):
            return _login_ok(request)
        seen["body"] = request.content.decode()
        seen["ctype"] = request.headers.get("content-type")
        return httpx.Response(200, json="API scheduled successfully !")

    from cms_mcp import device_commands as dc
    async with CmsAuthClient(base_url="http://cms.test", user="u", password="p",
                             transport=httpx.MockTransport(handler)) as c:
        resp = await dc._post_wrapped(c, dc._EXECUTE_PATH, "vvm",
                                      {"ChargerID": 5, "ApiID": 1})
    assert resp.json() == "API scheduled successfully !"
    assert seen["body"].startswith("{vvm: ")
    assert '"ChargerID": 5' in seen["body"]
    assert "application/json" in seen["ctype"]


@pytest.mark.asyncio
async def test_load_catalog_maps_api_to_row():
    def handler(request):
        if request.url.path.endswith("/Home/GetUsersByEmailAndPassword"):
            return _login_ok(request)
        if request.url.path.endswith("/GetAllApisByChargerID"):
            return httpx.Response(200, json=[
                {"ApiID": 1, "API": "id", "ApiType": "GET", "IsInputRequired": False},
                {"ApiID": 154, "API": "reset", "ApiType": "POST", "IsInputRequired": False},
            ])
        return httpx.Response(404)

    from cms_mcp import device_commands as dc
    async with CmsAuthClient(base_url="http://cms.test", user="u", password="p",
                             transport=httpx.MockTransport(handler)) as c:
        cat = await dc._load_catalog(c, 1429)
    assert cat["id"]["ApiID"] == 1 and cat["id"]["ApiType"] == "GET"
    assert cat["reset"]["ApiID"] == 154
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_device_commands.py -k "post_wrapped or load_catalog" -v`
Expected: FAIL with `AttributeError: module 'cms_mcp.device_commands' has no attribute '_post_wrapped'`

- [ ] **Step 3: Implement the helpers**

Append to `cms_mcp/device_commands.py`:

```python
async def _post_wrapped(client, path: str, wrapper_key: str, obj: Any):
    """POST a legacy `{<key>: <json>}` wrapper body (the unquoted-key shape the CMS
    JS uses) with the AJAX + JSON content-type headers. Returns the raw response."""
    body = "{%s: %s}" % (wrapper_key, json.dumps(obj))
    resp = await client.post(
        path, content=body,
        headers={"X-Requested-With": "XMLHttpRequest",
                 "Content-Type": "application/json; charset=utf-8"},
    )
    resp.raise_for_status()
    return resp


async def _load_catalog(client, charger_id: int) -> dict[str, dict]:
    """Fetch the per-charger device-API catalog as {API-template: row}."""
    rows = await _post_json(client, _CATALOG_PATH, data={"ChargerID": str(charger_id)})
    out = {}
    for r in rows:
        api = r.get("API")
        if api:
            out[str(api)] = r
    return out


async def _read_log(client, path: str) -> Any:
    """POST ReadJsonLogFile for a server-side path; parse JSON, else return raw text."""
    resp = await _post_wrapped(client, _READLOG_PATH, "Paths", path)
    try:
        return resp.json()
    except Exception:
        return resp.text


async def _history_rows(client, charger_id: int, n: int) -> list[dict]:
    """Fetch the latest `n` execution-history rows; [] on empty/flap."""
    try:
        resp = await _post_json(client, _HISTORY_PATH,
                                json=_datatables_body(charger_id, n))
    except Exception:
        return []
    return resp.get("data", []) if isinstance(resp, dict) else []


async def _baseline_execid(client, charger_id: int) -> int:
    """Max ExecutionID for this charger right now (correlation baseline)."""
    rows = await _history_rows(client, charger_id, 5)
    return max((int(r.get("ExecutionID") or 0) for r in rows), default=0)


async def _await_result(client, charger_id: int, resolved_api: str,
                        baseline: int, *, timeout: float, interval: float) -> Optional[dict]:
    """Poll history until our new row appears: ExecutionID > baseline, matching API
    path, with a non-empty Output. Returns the row or None on timeout."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        await asyncio.sleep(interval)
        for r in await _history_rows(client, charger_id, 10):
            if (int(r.get("ExecutionID") or 0) > baseline
                    and str(r.get("API")) == resolved_api
                    and r.get("Output")):
                return r
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_device_commands.py -k "post_wrapped or load_catalog" -v`
Expected: PASS (2)

- [ ] **Step 5: Commit**

```bash
git add cms-mcp-server/cms_mcp/device_commands.py cms-mcp-server/tests/test_device_commands.py
git commit -m "feat(cms): device-command network helpers (wrapped POST, catalog, correlation)"
```

---

## Task 5: Register the 4 tools + wire into server

**Files:**
- Modify: `cms-mcp-server/cms_mcp/device_commands.py`
- Modify: `cms-mcp-server/cms_mcp/server.py`
- Test: (covered by Task 6/7 integration tests)

- [ ] **Step 1: Add the audit logger + `register_device_tools` to the module**

Append to `cms_mcp/device_commands.py`:

```python
_audit = logging.getLogger("cms_mcp.device_audit")


def _configure_audit_logger() -> None:
    """Attach a handler so device_audit records are emitted (idempotent). Audit is the
    compensating control for the write path, so it must not be a silent no-op."""
    if _audit.handlers:
        return
    _audit.setLevel(logging.INFO)
    path = settings.device_audit_log_file
    handler = logging.FileHandler(path, encoding="utf-8") if path else logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    _audit.addHandler(handler)


def register_device_tools(mcp, client, charger_map: dict) -> None:
    """Register the device-command tools onto `mcp`, sharing the server's charger map."""
    _configure_audit_logger()

    @mcp.tool
    async def cms_run_device_query(
        qcharger_id: str, command: str, args: Optional[dict] = None
    ) -> dict:
        """Run a READ-ONLY device diagnostic on ONE charger (by QChargerID) via the old
        NOC and return the device's response. `command` is the exact API path from
        cms_list_charger_actions (e.g. "id", "version", "state", "outlets",
        "ocpp-client/config"); fill any {placeholder} via `args`
        (e.g. command="outlets/{number}/state", args={"number": 1}). Only GET commands
        are allowed — for writes use cms_run_device_action_prepare."""
        cid = await _resolve_charger_id(client, qcharger_id, charger_map)
        catalog = await _load_catalog(client, cid)
        row = catalog.get(command)
        if row is None:
            near = sorted(k for k in catalog if command.split("/")[0] in k)[:5]
            hint = f" Did you mean: {', '.join(near)}?" if near else ""
            raise ValueError(f"Unknown command '{command}' for {qcharger_id}.{hint}")
        if str(row.get("ApiType")) != "GET":
            raise ValueError(
                f"'{command}' is a {row.get('ApiType')} (write); cms_run_device_query "
                f"runs read-only GET commands only. Use cms_run_device_action_prepare.")
        resolved = resolve_path(command, args)
        await client._ensure_login()
        vvm = {"ChargerID": cid, "ApiID": row["ApiID"], "Input": resolved,
               "API": resolved, "UpdatedBy": getattr(client, "user_id", 0)}
        baseline = await _baseline_execid(client, cid)
        await _post_wrapped(client, _EXECUTE_PATH, "vvm", vvm)
        hit = await _await_result(
            client, cid, resolved, baseline,
            timeout=settings.device_poll_timeout, interval=settings.device_poll_interval)
        if hit is None:
            raise RuntimeError(
                f"No result for '{command}' on {qcharger_id} within "
                f"{settings.device_poll_timeout:.0f}s — the charger may be offline or the "
                f"CMS is busy; retry shortly.")
        return {
            "qcharger_id": qcharger_id, "command": command,
            "execution_id": hit.get("ExecutionID"), "status_id": hit.get("StatusID"),
            "executed_at": hit.get("strExecutedTime"),
            "result": await _read_log(client, hit["Output"]),
        }

    @mcp.tool
    async def cms_run_device_action_prepare(
        qcharger_id: str, command: str, args: Optional[dict] = None
    ) -> dict:
        """STEP 1 of running a WRITE command on a LIVE charger (by QChargerID). Does NOT
        execute — validates the command against the recovery allow-list and returns a
        one-time confirmation_token + preview. Show the user the command + charger, get
        explicit approval, then call cms_run_device_action_execute with the token.
        Allowed: reset, restartStack, controller reboot, ocpp-client start/stop/restart,
        outlets/{number}/stop, services/rfid/clear."""
        if command not in WRITE_ALLOWLIST:
            raise ValueError(
                f"'{command}' is not on the device-action allow-list. Allowed writes: "
                f"{', '.join(sorted(WRITE_ALLOWLIST))}.")
        cid = await _resolve_charger_id(client, qcharger_id, charger_map)
        catalog = await _load_catalog(client, cid)
        row = catalog.get(command)
        if row is None:
            raise ValueError(f"'{command}' is not in {qcharger_id}'s catalog.")
        resolved = resolve_path(command, args)
        token = _mint_pending({
            "qcharger_id": qcharger_id, "charger_id": cid, "api_id": row["ApiID"],
            "command": command, "resolved": resolved,
        })
        return {
            "confirmation_token": token, "qcharger_id": qcharger_id,
            "command": command, "resolved_path": resolved,
            "expires_in_seconds": settings.device_pending_ttl_seconds,
            "warning": ("Runs on a LIVE charger. Show this command to the user and get "
                        "explicit approval before calling cms_run_device_action_execute."),
        }

    @mcp.tool
    async def cms_run_device_action_execute(confirmation_token: str) -> dict:
        """STEP 2: run the WRITE command from a cms_run_device_action_prepare the user
        approved. Requires the one-time confirmation_token. Schedules the command on the
        charger and returns the ack (+ execution_id if it resolves quickly)."""
        req = _pop_pending(confirmation_token, ttl=settings.device_pending_ttl_seconds)
        if req is None:
            return {"error": "Unknown, expired, or already-used confirmation_token. "
                             "Call cms_run_device_action_prepare again and get approval."}
        if req["command"] not in WRITE_ALLOWLIST:   # defensive re-check
            return {"error": f"'{req['command']}' is not on the allow-list."}
        cid = req["charger_id"]
        await client._ensure_login()
        vvm = {"ChargerID": cid, "ApiID": req["api_id"], "Input": req["resolved"],
               "API": req["resolved"], "UpdatedBy": getattr(client, "user_id", 0)}
        baseline = await _baseline_execid(client, cid)
        resp = await _post_wrapped(client, _EXECUTE_PATH, "vvm", vvm)
        _audit.info("device_execute charger=%s command=%r resolved=%r user=%s",
                    req["qcharger_id"], req["command"], req["resolved"],
                    getattr(client, "user_id", 0))
        ack = resp.json() if resp.text.strip() else ""
        hit = await _await_result(client, cid, req["resolved"], baseline,
                                  timeout=min(settings.device_poll_timeout, 10.0),
                                  interval=settings.device_poll_interval)
        return {"qcharger_id": req["qcharger_id"], "command": req["command"],
                "resolved_path": req["resolved"], "ack": ack,
                "execution_id": hit.get("ExecutionID") if hit else None}

    @mcp.tool
    async def cms_read_device_log(qcharger_id: str, path: str) -> Any:
        """Read a session/alert state-log or command-output blob from the old NOC by its
        server-side file path (the FullStatePath / StateLogPath / Output value returned by
        cms_get_charger_sessions / _alerts / device tools). Read-only; path must be a known
        CMS state-log/result file."""
        if not is_safe_log_path(path):
            raise ValueError(
                "path must be a CMS state-log/result file under ApiResponses or a "
                "*State_Logs root (e.g. E:\\ApiResponses\\r.txt).")
        return await _read_log(client, path)
```

- [ ] **Step 2: Wire the registration into `build_server`**

In `cms_mcp/server.py`, at the end of `build_server`, immediately **before** `return mcp`, add:

```python
    from .device_commands import register_device_tools
    register_device_tools(mcp, client, _charger_map)
```

(`_charger_map` is the resolver cache already defined in `build_server`; sharing it avoids a second fleet-map fetch.)

- [ ] **Step 3: Verify the server still imports and builds**

Run: `python -c "from cms_mcp.server import build_server; import httpx; from cms_mcp.auth import CmsAuthClient; c=CmsAuthClient(base_url='http://x', user='u', password='p', transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[]))); m=build_server(c); print('tools ok')"`
Expected: prints `tools ok` (no ImportError / circular-import error)

- [ ] **Step 4: Commit**

```bash
git add cms-mcp-server/cms_mcp/device_commands.py cms-mcp-server/cms_mcp/server.py
git commit -m "feat(cms): register device-query, gated-action, and read-log tools"
```

---

## Task 6: Gate + path-guard behavior tests (via the built server)

**Files:**
- Modify: `cms-mcp-server/tests/fake_cms.py`
- Test: `cms-mcp-server/tests/test_device_commands.py`

- [ ] **Step 1: Extend the fake CMS with a stateful execution cycle**

In `tests/fake_cms.py`, after the `_CATALOG` definition, add a mutable execution store and result payloads:

```python
# Stateful execution store for ExecuteApi -> history -> ReadJsonLogFile round-trip.
_EXECUTIONS = []          # list of history rows, newest first
_NEXT_EXECID = [500000]
_LOG_FILES = {}           # Output path -> content served by ReadJsonLogFile
```

Replace the `_ROUTES` dict entry for `GetApiExecutionDetailsByID` with a dynamic
handler by editing `_handler` (see Step 2). Add a fuller catalog so allow-list
commands resolve — extend `_CATALOG` to include the recovery commands:

```python
_CATALOG += [
    {"ApiID": 154, "Name": "Reset", "API": "reset", "ApiType": "POST",
     "IsInputRequired": False, "IsActive": True},
    {"ApiID": 132, "Name": "Restart client", "API": "ocpp-client/restart",
     "ApiType": "POST", "IsInputRequired": False, "IsActive": True},
    {"ApiID": 52, "Name": "Stop", "API": "outlets/{number}/stop", "ApiType": "POST",
     "IsInputRequired": True, "IsActive": True},
    {"ApiID": 8, "Name": "Version", "API": "version", "ApiType": "GET",
     "IsInputRequired": False, "IsActive": True},
]
```

- [ ] **Step 2: Make the fake handle ExecuteApi + ReadJsonLogFile**

In `tests/fake_cms.py`, replace the `_handler` function body with:

```python
def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith(_LOGIN):
        return httpx.Response(200, json={"UserID": 74},
                              headers={"set-cookie": ".ASPXAUTH=abc; path=/"})
    if path.endswith("/GetChargerByCustomerIDForDD"):
        return httpx.Response(200, json=_CHARGERS)
    if path.endswith("/ExecuteApi"):
        body = json.loads(request.content.decode().replace("{vvm:", '{"vvm":', 1))
        vvm = body["vvm"]
        execid = _NEXT_EXECID[0]; _NEXT_EXECID[0] += 1
        outpath = f"E:\\ApiResponses\\{execid}.txt"
        _LOG_FILES[outpath] = {"api": vvm["API"], "value": "device-ok"}
        _EXECUTIONS.insert(0, {
            "Name": vvm["API"], "API": vvm["API"], "ApiType": "GET",
            "Description": "Executed", "StatusID": 2, "ExecutionID": execid,
            "strExecutedTime": "10/06/2026 12:48:25", "Output": outpath})
        return httpx.Response(200, json="API scheduled successfully !")
    if path.endswith("/GetApiExecutionDetailsByID"):
        return httpx.Response(200, json={
            "draw": 1, "iTotalRecords": len(_EXECUTIONS),
            "iTotalDisplayRecords": len(_EXECUTIONS), "data": list(_EXECUTIONS)})
    if path.endswith("/ReadJsonLogFile"):
        raw = request.content.decode().replace("{Paths:", '{"Paths":', 1)
        p = json.loads(raw)["Paths"]
        return httpx.Response(200, json=_LOG_FILES.get(p, "not found"))
    for suffix, payload in _ROUTES.items():
        if path.endswith(suffix):
            return httpx.Response(200, json=payload)
    return httpx.Response(404)
```

Add `import json` at the top of `tests/fake_cms.py` if not already present, and
remove the now-superseded `GetApiExecutionDetailsByID` key from `_ROUTES` (it is
handled dynamically above). Add a reset helper:

```python
def reset_execution_state():
    _EXECUTIONS.clear()
    _LOG_FILES.clear()
    _NEXT_EXECID[0] = 500000
```

- [ ] **Step 2b: Run existing tests to verify no regression**

Run: `python -m pytest tests/test_tools.py -q`
Expected: PASS (existing per-charger tool tests still green — the dynamic
`GetApiExecutionDetailsByID` returns the same `data` shape they expect, i.e. `[]`
when no executions have run).

- [ ] **Step 3: Write the gate + guard tests**

Append to `tests/test_device_commands.py`:

```python
from fake_cms import build_test_server, reset_execution_state


async def _call(server, name, **kw):
    tool = await server.get_tool(name)
    return await tool.run(kw)


@pytest.mark.asyncio
async def test_action_prepare_rejects_off_allowlist():
    reset_execution_state()
    server = build_test_server()
    with pytest.raises(Exception) as ei:
        await _call(server, "cms_run_device_action_prepare",
                    qcharger_id="ab-sam-sim", command="scripts/active")
    assert "allow-list" in str(ei.value)


@pytest.mark.asyncio
async def test_action_execute_rejects_bad_token():
    reset_execution_state()
    server = build_test_server()
    res = await _call(server, "cms_run_device_action_execute",
                      confirmation_token="nope")
    # FastMCP wraps the dict result; assert the error surfaced
    assert "error" in str(res).lower()


@pytest.mark.asyncio
async def test_query_rejects_write_command():
    reset_execution_state()
    server = build_test_server()
    with pytest.raises(Exception) as ei:
        await _call(server, "cms_run_device_query",
                    qcharger_id="ab-sam-sim", command="reset")
    assert "read-only" in str(ei.value).lower()


@pytest.mark.asyncio
async def test_read_device_log_path_guard():
    reset_execution_state()
    server = build_test_server()
    with pytest.raises(Exception) as ei:
        await _call(server, "cms_read_device_log",
                    qcharger_id="ab-sam-sim", path=r"E:\Windows\System32\x.txt")
    assert "state-log" in str(ei.value).lower() or "path must" in str(ei.value).lower()
```

> Note: if `server.get_tool(...).run(...)` does not match the installed FastMCP
> API, call the underlying function directly instead — the tools are also plain
> module-level closures. Confirm the call shape against `tests/test_tools.py`,
> which already exercises `build_test_server()`; mirror whatever pattern it uses.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_device_commands.py -k "action_prepare or action_execute or query_rejects or path_guard" -v`
Expected: PASS (4)

- [ ] **Step 5: Commit**

```bash
git add cms-mcp-server/tests/fake_cms.py cms-mcp-server/tests/test_device_commands.py
git commit -m "test(cms): device-command gate + path-guard behavior via fake CMS"
```

---

## Task 7: End-to-end happy-path tests (query + action round-trip)

**Files:**
- Test: `cms-mcp-server/tests/test_device_commands.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_device_commands.py`:

```python
@pytest.mark.asyncio
async def test_query_end_to_end_reads_device_output(monkeypatch):
    reset_execution_state()
    # speed up polling
    from cms_mcp.config import settings
    monkeypatch.setattr(settings, "device_poll_interval", 0.01)
    monkeypatch.setattr(settings, "device_poll_timeout", 2.0)
    server = build_test_server()
    res = await _call(server, "cms_run_device_query",
                      qcharger_id="ab-sam-sim", command="version")
    data = res.structured_content if hasattr(res, "structured_content") else res
    text = str(data)
    assert "device-ok" in text
    assert "version" in text


@pytest.mark.asyncio
async def test_action_prepare_then_execute_schedules(monkeypatch):
    reset_execution_state()
    from cms_mcp.config import settings
    monkeypatch.setattr(settings, "device_poll_interval", 0.01)
    monkeypatch.setattr(settings, "device_poll_timeout", 2.0)
    server = build_test_server()
    prep = await _call(server, "cms_run_device_action_prepare",
                       qcharger_id="ab-sam-sim", command="ocpp-client/restart")
    token = str(prep.structured_content["confirmation_token"]) \
        if hasattr(prep, "structured_content") else prep["confirmation_token"]
    res = await _call(server, "cms_run_device_action_execute",
                      confirmation_token=token)
    assert "scheduled successfully" in str(res)
```

> Adjust the result-unwrapping (`structured_content` vs raw dict) to match the
> FastMCP version, exactly as `tests/test_tools.py` does.

- [ ] **Step 2: Run tests to verify they fail then pass**

Run: `python -m pytest tests/test_device_commands.py -k "end_to_end or prepare_then_execute" -v`
Expected: initially may FAIL on result-unwrapping shape; fix unwrapping to match
`test_tools.py`, then PASS (2).

- [ ] **Step 3: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS (all — the 14 existing CMS tool tests + the new device-command tests)

- [ ] **Step 4: Commit**

```bash
git add cms-mcp-server/tests/test_device_commands.py
git commit -m "test(cms): device-command query + action end-to-end round-trip"
```

---

## Task 8: Docs — README tool table + boundary section

**Files:**
- Modify: `cms-mcp-server/README.md`

- [ ] **Step 1: Add the tools to the README**

Open `cms-mcp-server/README.md`, find the tool table, and add these rows:

```markdown
| `cms_run_device_query` | Run a read-only device diagnostic (any GET command) on one charger and return the device response. |
| `cms_run_device_action_prepare` | STEP 1 of a gated write: validate against the recovery allow-list, return a one-time confirmation token + preview. Does not execute. |
| `cms_run_device_action_execute` | STEP 2: run the approved write command (requires the token). Audited. |
| `cms_read_device_log` | Read a session/alert state-log or command-output blob by its CMS file path. |
```

- [ ] **Step 2: Add a "Device commands (gated)" section**

Append to `README.md`:

```markdown
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
```

- [ ] **Step 3: Commit**

```bash
git add cms-mcp-server/README.md
git commit -m "docs(cms): document device-command tools + read/write boundary"
```

---

## Task 9: Full verification + release gate

- [ ] **Step 1: Run the whole test suite**

Run: `python -m pytest -q`
Expected: PASS (all).

- [ ] **Step 2: Security review (required before release)**

This adds a write path to field hardware. Request `superpowers:requesting-code-review`
and a focused security review per spec §8: allow-list bypass via `args`
path-injection, `cms_read_device_log` path-guard bypass, token single-use, audit
coverage, and no double-`ExecuteApi` on flap/retry. Do NOT release until this passes.

- [ ] **Step 3: Live smoke test (gated on creds + an online/sim charger)**

Rebuild the container (`docker compose build cms-mcp-server && docker compose up -d cms-mcp-server` from `chat/`). Through a real MCP session:
- `cms_run_device_query` `id` / `version` / `state` on the sim charger → real device JSON.
- One `ocpp-client/restart` prepare→execute on a **non-production / sim** charger with explicit approval → confirm the audit line and the scheduled execution appear.
- `cms_read_device_log` on a real session `FullStatePath`.
Confirm the `StatusID` enum and that `Output` populates as expected.

- [ ] **Step 4: Version bump + coordinated release**

Per team convention run `scripts/bump-version.ps1` (`feat` → minor). **Coordinate the release** — `feat/mcp-server` is shared with other in-progress work (see the held-release note in project memory); land the security review first, then push branch + tag.

---

## Self-review notes (author)

- **Spec coverage:** §4 tools → Tasks 5–7; §4 allow-lists → Tasks 3,5,6; §5.1 query correlation → Task 4 `_await_result` + Task 7; §5.2 gate → Task 5 + Task 6; §5.3 path guard → Task 3 + Task 6; §5.4 settings/plumbing → Tasks 1,2,4; §6 error handling → covered in tool bodies; §7 testing → Tasks 6,7; §8 security review → Task 9; §9 docs/release → Tasks 8,9. No gaps.
- **Type consistency:** helper names (`_post_wrapped`, `_load_catalog`, `_await_result`, `resolve_path`, `is_safe_log_path`, `_mint_pending`, `_pop_pending`, `register_device_tools`) are used identically across tasks. `WRITE_ALLOWLIST` is a `frozenset`; membership tests only. Tool names match between module, README, and tests.
- **Known impl-time confirmations (flagged, not placeholders):** the FastMCP tool-invocation shape in tests (mirror `test_tools.py`), and the CMS `StatusID` enum (live smoke). Both are called out where they occur.
