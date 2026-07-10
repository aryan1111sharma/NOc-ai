# P0-4 Read-Only Charger-Proxy Enforcement — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hard-reject any non-GET `method` on the exposed GET charger-proxy tools, before the call reaches the NOC backend, so the read-only guarantee holds for the proxy path too.

**Architecture:** A FastMCP `on_call_tool` middleware (`ReadOnlyProxyMiddleware`) inspects each proxy tool call and raises `ToolError` when its `method` argument is present and not GET. A pure helper `_blocked_method(tool_name, arguments)` holds the decision logic so it is testable without middleware context objects. Registered in `build_server()` via `mcp.add_middleware(...)`. No tool names or schemas change.

**Tech Stack:** Python, FastMCP 3.3.1 (`fastmcp.server.middleware.Middleware`, `fastmcp.exceptions.ToolError`), pytest / pytest-asyncio (configured in `mcp-server/pytest.ini`).

**Spec:** `docs/superpowers/specs/2026-07-11-p0-4-readonly-proxy-design.md`

**Note on commands:** run everything from the `mcp-server/` directory using the repo venv, e.g. `cd mcp-server && ../.venv/Scripts/python -m pytest ...` (Windows). Adjust the interpreter path if running elsewhere.

---

### Task 1: Decision helper `_blocked_method`

**Files:**
- Create: `mcp-server/noc_mcp/readonly.py`
- Test: `mcp-server/tests/test_readonly.py`

- [ ] **Step 1: Write the failing test**

Create `mcp-server/tests/test_readonly.py`:

```python
"""Tests for read-only charger-proxy enforcement (noc_mcp.readonly)."""

import pytest

from noc_mcp.readonly import _blocked_method

# A representative exposed proxy tool name (path-based GET variant).
PROXY = "proxy_api_call_path_based_api_v1_chargers__charger_id__proxy__port___path__get"


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "post", "Delete", "", "   ", 123])
def test_blocks_non_get_method_on_proxy(method):
    assert _blocked_method(PROXY, {"method": method}) is not None


@pytest.mark.parametrize("method", ["GET", "get", " GET "])
def test_allows_get_method_on_proxy(method):
    assert _blocked_method(PROXY, {"method": method}) is None


def test_allows_absent_method_on_proxy():
    assert _blocked_method(PROXY, {"charger_id": "CHG-1", "port": 8003, "path": "x"}) is None


def test_allows_explicit_none_method_on_proxy():
    assert _blocked_method(PROXY, {"method": None}) is None


def test_ignores_non_proxy_tools_even_with_method():
    assert _blocked_method("dashboard_summary_api_v1_dashboard_summary_get", {"method": "POST"}) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mcp-server && ../.venv/Scripts/python -m pytest tests/test_readonly.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_mcp.readonly'` (collection error).

- [ ] **Step 3: Write minimal implementation**

Create `mcp-server/noc_mcp/readonly.py`:

```python
"""Read-only enforcement for the charger-proxy tools.

The NOC MCP server exposes only GET operations as tools, but the GET charger-proxy tools carry
a ``method`` query parameter that the NOC backend uses to choose which HTTP method it forwards
to a live charger. Without this guard a *GET* proxy tool could be called with ``method=POST``
and write to a charger. ``_blocked_method`` decides when to reject; ``ReadOnlyProxyMiddleware``
(Task 2) enforces it on every tool call.
"""

from __future__ import annotations

from typing import Any, Optional


def _blocked_method(tool_name: str, arguments: dict[str, Any]) -> Optional[str]:
    """Return the offending method string if this call must be blocked, else ``None``.

    Blocks only proxy tools (name contains ``"proxy"``) whose ``method`` argument is present and
    not GET (case-insensitive, whitespace-tolerant). Absent or ``None`` ``method`` is allowed
    (the NOC backend defaults to the request's GET method).
    """
    if "proxy" not in tool_name.lower():
        return None
    if not arguments or "method" not in arguments:
        return None
    method = arguments.get("method")
    if method is None:
        return None
    if isinstance(method, str) and method.strip().upper() == "GET":
        return None
    return str(method)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mcp-server && ../.venv/Scripts/python -m pytest tests/test_readonly.py -v`
Expected: PASS (all 13 parametrized/standalone cases green).

- [ ] **Step 5: Commit**

```bash
git add mcp-server/noc_mcp/readonly.py mcp-server/tests/test_readonly.py
git commit -m "feat(mcp-server): add _blocked_method read-only proxy decision helper"
```

---

### Task 2: `ReadOnlyProxyMiddleware.on_call_tool`

**Files:**
- Modify: `mcp-server/noc_mcp/readonly.py`
- Test: `mcp-server/tests/test_readonly.py`

- [ ] **Step 1: Write the failing test**

Append to `mcp-server/tests/test_readonly.py`:

```python
from types import SimpleNamespace

from fastmcp.exceptions import ToolError

from noc_mcp.readonly import ReadOnlyProxyMiddleware


def _ctx(name, arguments):
    """Minimal stand-in for a MiddlewareContext[CallToolRequestParams]."""
    return SimpleNamespace(message=SimpleNamespace(name=name, arguments=arguments))


@pytest.mark.asyncio
async def test_middleware_rejects_non_get_proxy_call():
    mw = ReadOnlyProxyMiddleware()
    called = False

    async def call_next(ctx):
        nonlocal called
        called = True
        return "ok"

    with pytest.raises(ToolError):
        await mw.on_call_tool(_ctx(PROXY, {"method": "POST"}), call_next)
    assert called is False  # NOC is never contacted for a blocked call


@pytest.mark.asyncio
async def test_middleware_allows_get_proxy_call():
    mw = ReadOnlyProxyMiddleware()

    async def call_next(ctx):
        return "ok"

    result = await mw.on_call_tool(_ctx(PROXY, {"method": "GET"}), call_next)
    assert result == "ok"


@pytest.mark.asyncio
async def test_middleware_allows_absent_method():
    mw = ReadOnlyProxyMiddleware()

    async def call_next(ctx):
        return "ok"

    result = await mw.on_call_tool(_ctx(PROXY, {"charger_id": "CHG-1"}), call_next)
    assert result == "ok"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mcp-server && ../.venv/Scripts/python -m pytest tests/test_readonly.py -k middleware -v`
Expected: FAIL — `ImportError: cannot import name 'ReadOnlyProxyMiddleware'`.

- [ ] **Step 3: Write minimal implementation**

Add imports at the top of `mcp-server/noc_mcp/readonly.py` (after `from typing import ...`):

```python
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware
```

Append the class to `mcp-server/noc_mcp/readonly.py`:

```python
class ReadOnlyProxyMiddleware(Middleware):
    """Reject non-GET ``method`` on charger-proxy tools before the call reaches NOC."""

    async def on_call_tool(self, context, call_next):
        message = context.message
        offending = _blocked_method(message.name, message.arguments or {})
        if offending is not None:
            raise ToolError(
                "Read-only server: only GET is permitted on charger proxy tools "
                f"(got method={offending!r})"
            )
        return await call_next(context)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mcp-server && ../.venv/Scripts/python -m pytest tests/test_readonly.py -v`
Expected: PASS (Task 1 cases + 3 middleware cases).

- [ ] **Step 5: Commit**

```bash
git add mcp-server/noc_mcp/readonly.py mcp-server/tests/test_readonly.py
git commit -m "feat(mcp-server): reject non-GET proxy calls via ReadOnlyProxyMiddleware"
```

---

### Task 3: Register the middleware in `build_server()`

**Files:**
- Modify: `mcp-server/noc_mcp/server.py`
- Test: `mcp-server/tests/test_readonly.py`

- [ ] **Step 1: Write the failing test**

Append to `mcp-server/tests/test_readonly.py`:

```python
def test_build_server_registers_readonly_middleware(monkeypatch):
    # build_server() -> config.Settings() reads env at import; set creds first.
    monkeypatch.setenv("NOC_EMAIL", "svc@noc.test")
    monkeypatch.setenv("NOC_PASSWORD", "secret")

    from noc_mcp.server import build_server  # deferred import (needs env above)

    mcp = build_server()
    assert any(isinstance(m, ReadOnlyProxyMiddleware) for m in mcp.middleware)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mcp-server && ../.venv/Scripts/python -m pytest tests/test_readonly.py::test_build_server_registers_readonly_middleware -v`
Expected: FAIL — assertion error (no `ReadOnlyProxyMiddleware` in `mcp.middleware`).

- [ ] **Step 3: Write minimal implementation**

In `mcp-server/noc_mcp/server.py`, add the import next to the other `from .` imports (near line 20):

```python
from .readonly import ReadOnlyProxyMiddleware
```

Then in `build_server()` register it right after the catalog is registered:

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

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mcp-server && ../.venv/Scripts/python -m pytest tests/test_readonly.py::test_build_server_registers_readonly_middleware -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mcp-server/noc_mcp/server.py mcp-server/tests/test_readonly.py
git commit -m "feat(mcp-server): register ReadOnlyProxyMiddleware in build_server"
```

---

### Task 4: Full suite + close out P0-4

**Files:**
- Modify: `docs/PRODUCTION-READINESS.md`

- [ ] **Step 1: Run the full test suite**

Run: `cd mcp-server && ../.venv/Scripts/python -m pytest -v`
Expected: PASS — existing `test_auth.py` (3 tests) + `test_readonly.py` all green.

- [ ] **Step 2: Mark P0-4 done in the backlog**

In `docs/PRODUCTION-READINESS.md`, change the P0-4 checkbox from `- [ ]` to `- [x]`:

```markdown
- [x] **P0-4 — Lock charger proxy to GET.** Enforce `method=GET` in code on
```

- [ ] **Step 3: Commit**

```bash
git add docs/PRODUCTION-READINESS.md
git commit -m "chore: mark P0-4 (read-only charger proxy) done"
```

---

## Self-Review

**Spec coverage:**
- Problem (`method` param on GET proxy tools) → addressed by `_blocked_method` scoping + middleware (Tasks 1–2). ✓
- Acceptance criterion (non-GET rejected server-side before NOC) → `test_middleware_rejects_non_get_proxy_call` asserts `ToolError` raised **and** `call_next` never called. ✓
- Hard-reject decision → `ToolError`, no coercion. ✓
- Both proxy families → helper matches any tool name containing `proxy` (covers path-based and query-based). ✓
- Edge cases (case-insensitive, whitespace, non-string, absent, None) → covered by Task 1 parametrized cases. ✓
- Wire-up → Task 3 + registration test. ✓
- No tool renames/schema changes → only middleware added; catalog/agent wiring untouched. ✓
- Out of scope (`token` param; other servers) → not touched. ✓

**Placeholder scan:** none — every step has concrete code/commands.

**Type consistency:** `_blocked_method(tool_name: str, arguments: dict) -> Optional[str]` used identically in the helper and inside `on_call_tool`; `ReadOnlyProxyMiddleware` name consistent across tests, module, and server import; `mcp.middleware` list attribute matches FastMCP's `add_middleware` implementation.
