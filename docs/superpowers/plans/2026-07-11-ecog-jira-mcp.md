# Ecog Jira MCP — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-hosted, read-only MCP server (`jira-mcp-server/`) exposing Ecog's Atlassian Cloud Jira (search, issue detail, comments, status/transitions) to the assistant.

**Architecture:** Mirrors `cms-mcp-server/` exactly — `config.py` (pydantic settings), `auth.py` (an `httpx.AsyncClient` subclass doing HTTP Basic auth), `server.py` (`build_server()` returning a `FastMCP` with `@mcp.tool` read-only closures + pure trim/ADF helpers), `__main__.py`, `tests/`. Read-only is enforced by construction: no write tool exists. Descriptions/comments arrive as Atlassian Document Format (ADF) JSON and are flattened to plain text.

**Tech Stack:** Python 3.12, FastMCP ≥3.4, httpx ≥0.28, pydantic-settings ≥2.0, pytest + pytest-asyncio. Jira Cloud REST API v3.

**Spec:** `docs/superpowers/specs/2026-07-11-ecog-jira-mcp-design.md`

---

## File Structure

| Path | Responsibility |
|---|---|
| `jira-mcp-server/jira_mcp/__init__.py` | Package marker + `__version__`. |
| `jira-mcp-server/jira_mcp/config.py` | `Settings` from env/.env (base URL, email, token, transport). |
| `jira-mcp-server/jira_mcp/auth.py` | `JiraAuthClient` — Basic-auth httpx client. |
| `jira-mcp-server/jira_mcp/server.py` | Pure helpers (`_adf_to_text`, trimmers), `_get_json`, `build_client`, `build_server`, the 4 read tools. |
| `jira-mcp-server/jira_mcp/__main__.py` | `python -m jira_mcp` entrypoint. |
| `jira-mcp-server/tests/conftest.py` | Test env defaults + `mock_client` fixture. |
| `jira-mcp-server/tests/test_auth.py` | Basic-auth header + 401 handling. |
| `jira-mcp-server/tests/test_helpers.py` | `_adf_to_text` + trimmers (pure). |
| `jira-mcp-server/tests/test_tools.py` | End-to-end tool calls via in-memory FastMCP `Client`; read-only guard. |
| `jira-mcp-server/requirements.txt` `Dockerfile` `README.md` | Deps, image, docs. |
| `.github/workflows/tests.yml:19` | Add `jira-mcp-server` to the CI matrix. |
| `chat/docker-compose.yml` · `chat/librechat.yaml` | Deploy + register the server (port 8102). |

Conventions from `cms-mcp-server`: transport/host/port come from settings; `python -m pytest` runs the suite (puts the package on `sys.path`); tools trim payloads to small whitelists.

---

## Task 1: Scaffold package + config

**Files:**
- Create: `jira-mcp-server/jira_mcp/__init__.py`
- Create: `jira-mcp-server/jira_mcp/config.py`
- Create: `jira-mcp-server/requirements.txt`
- Create: `jira-mcp-server/tests/conftest.py`
- Test: `jira-mcp-server/tests/test_config.py`

- [ ] **Step 1: Create the package marker**

`jira-mcp-server/jira_mcp/__init__.py`:
```python
"""Read-only MCP server for Ecog's Atlassian Cloud Jira (REST v3)."""

__version__ = "0.1.0"
```

- [ ] **Step 2: Create `requirements.txt`**

`jira-mcp-server/requirements.txt`:
```
fastmcp>=3.4
httpx>=0.28
pydantic-settings>=2.0
```

- [ ] **Step 3: Create `config.py`**

`jira-mcp-server/jira_mcp/config.py`:
```python
"""Configuration for the Ecog Jira MCP server, loaded from environment / .env."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Ecog Jira — Atlassian Cloud, REST v3 with HTTP Basic (email + API token).
    jira_base_url: str = "https://ecog.atlassian.net"
    jira_email: str                      # shared read-only account
    jira_api_token: str                  # injected as a secret; never committed
    request_timeout: float = 60.0

    # MCP transport: "stdio" or "http"
    mcp_transport: str = "stdio"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8102                 # noc=8100, cms=8101, jira=8102


settings = Settings()
```

- [ ] **Step 4: Create the test `conftest.py`**

`jira-mcp-server/tests/conftest.py`:
```python
"""Shared test setup: dummy Jira creds (so `config.Settings()` imports) + a mock client."""

import os

# Set BEFORE any jira_mcp.config import happens during collection.
os.environ.setdefault("JIRA_EMAIL", "test@ecog.local")
os.environ.setdefault("JIRA_API_TOKEN", "test-token")

import httpx
import pytest

from jira_mcp.auth import JiraAuthClient


@pytest.fixture
def mock_client():
    """Factory: given an httpx handler, return a JiraAuthClient wired to a MockTransport."""
    def _make(handler):
        return JiraAuthClient(
            base_url="https://ecog.test",
            email="e@x.com",
            api_token="tok",
            transport=httpx.MockTransport(handler),
        )
    return _make
```

- [ ] **Step 5: Write the failing config test**

`jira-mcp-server/tests/test_config.py`:
```python
def test_settings_defaults_and_required(monkeypatch):
    monkeypatch.setenv("JIRA_EMAIL", "svc@ecog.local")
    monkeypatch.setenv("JIRA_API_TOKEN", "abc123")
    from jira_mcp.config import Settings

    s = Settings()
    assert s.jira_email == "svc@ecog.local"
    assert s.jira_api_token == "abc123"
    assert s.jira_base_url == "https://ecog.atlassian.net"
    assert s.mcp_port == 8102
    assert s.mcp_transport == "stdio"
```

- [ ] **Step 6: Run it and verify it fails**

Run: `cd jira-mcp-server && python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jira_mcp.auth'` (conftest imports it) or collection error.

- [ ] **Step 7: Add a minimal `auth.py` stub so imports resolve**

`jira-mcp-server/jira_mcp/auth.py`:
```python
"""HTTP Basic-auth client for Jira Cloud REST v3. (Filled in Task 2.)"""

from __future__ import annotations

import httpx


class JiraAuthClient(httpx.AsyncClient):
    def __init__(self, *args, email: str, api_token: str, **kwargs) -> None:
        super().__init__(*args, **kwargs)
```

- [ ] **Step 8: Run the config test and verify it passes**

Run: `cd jira-mcp-server && python -m pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add jira-mcp-server/jira_mcp/__init__.py jira-mcp-server/jira_mcp/config.py \
        jira-mcp-server/jira_mcp/auth.py jira-mcp-server/requirements.txt \
        jira-mcp-server/tests/conftest.py jira-mcp-server/tests/test_config.py
git commit -m "feat(jira-mcp): scaffold package + config"
```

---

## Task 2: Basic-auth client

**Files:**
- Modify: `jira-mcp-server/jira_mcp/auth.py`
- Test: `jira-mcp-server/tests/test_auth.py`

- [ ] **Step 1: Write the failing auth tests**

`jira-mcp-server/tests/test_auth.py`:
```python
"""JiraAuthClient sets HTTP Basic auth and surfaces auth failures clearly."""

import base64

import httpx
import pytest

from jira_mcp.auth import JiraAuthClient


@pytest.mark.asyncio
async def test_sets_basic_auth_header(mock_client):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["accept"] = request.headers.get("accept")
        return httpx.Response(200, json={})

    async with mock_client(handler) as c:
        await c.get("/rest/api/3/myself")

    expected = "Basic " + base64.b64encode(b"e@x.com:tok").decode()
    assert seen["auth"] == expected
    assert seen["accept"] == "application/json"
```

- [ ] **Step 2: Run and verify it fails**

Run: `cd jira-mcp-server && python -m pytest tests/test_auth.py -v`
Expected: FAIL — `assert None == 'Basic ...'` (stub sets no header).

- [ ] **Step 3: Implement the real client**

Replace `jira-mcp-server/jira_mcp/auth.py`:
```python
"""HTTP Basic-auth client for Jira Cloud REST v3.

Jira Cloud authenticates with `email:api_token` over HTTP Basic — no session/cookie
dance. The token is long-lived, so there is no re-login loop (unlike the legacy CMS).
Every request also asks for JSON.
"""

from __future__ import annotations

import base64

import httpx


class JiraAuthClient(httpx.AsyncClient):
    """httpx.AsyncClient pre-loaded with Jira Cloud Basic-auth + JSON headers."""

    def __init__(self, *args, email: str, api_token: str, **kwargs) -> None:
        token = base64.b64encode(f"{email}:{api_token}".encode()).decode()
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("Authorization", f"Basic {token}")
        headers.setdefault("Accept", "application/json")
        kwargs["headers"] = headers
        kwargs.setdefault("follow_redirects", True)
        super().__init__(*args, **kwargs)
```

- [ ] **Step 4: Run and verify it passes**

Run: `cd jira-mcp-server && python -m pytest tests/test_auth.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jira-mcp-server/jira_mcp/auth.py jira-mcp-server/tests/test_auth.py
git commit -m "feat(jira-mcp): Basic-auth httpx client"
```

---

## Task 3: Pure helpers — ADF flattener, trimmers, `_get_json`

**Files:**
- Create: `jira-mcp-server/jira_mcp/server.py`
- Test: `jira-mcp-server/tests/test_helpers.py`

- [ ] **Step 1: Write the failing helper tests**

`jira-mcp-server/tests/test_helpers.py`:
```python
import httpx
import pytest

from jira_mcp.auth import JiraAuthClient
from jira_mcp.server import (
    _adf_to_text,
    _comment,
    _drop_empty,
    _get_json,
    _issue_detail,
    _issue_summary,
)

_ADF = {
    "type": "doc",
    "version": 1,
    "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "Charger reboots on load."}]},
        {"type": "bulletList", "content": [
            {"type": "listItem", "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Firmware 2.3"}]}]},
        ]},
    ],
}


def test_adf_to_text_flattens():
    out = _adf_to_text(_ADF)
    assert "Charger reboots on load." in out
    assert "Firmware 2.3" in out


def test_adf_to_text_handles_none():
    assert _adf_to_text(None) == ""


def test_drop_empty_removes_blanks():
    assert _drop_empty({"a": 1, "b": None, "c": "", "d": [], "e": "x"}) == {"a": 1, "e": "x"}


def test_issue_summary_trims_to_flat_fields():
    issue = {
        "key": "ECOG-1",
        "fields": {
            "summary": "Boot loop", "status": {"name": "Open"},
            "issuetype": {"name": "Bug"}, "priority": {"name": "High"},
            "assignee": {"displayName": "Asha"}, "updated": "2026-07-10T10:00:00.000+0000",
            "project": {"key": "ECOG"},
        },
    }
    assert _issue_summary(issue) == {
        "key": "ECOG-1", "summary": "Boot loop", "status": "Open", "type": "Bug",
        "priority": "High", "assignee": "Asha",
        "updated": "2026-07-10T10:00:00.000+0000", "project": "ECOG",
    }


def test_issue_detail_adds_description_and_components():
    issue = {
        "key": "ECOG-2",
        "fields": {
            "summary": "PM fault", "status": {"name": "In Progress"},
            "issuetype": {"name": "Bug"}, "reporter": {"displayName": "Ravi"},
            "created": "2026-07-01T09:00:00.000+0000", "labels": ["firmware"],
            "components": [{"name": "PowerModule"}], "description": _ADF,
        },
    }
    d = _issue_detail(issue)
    assert d["reporter"] == "Ravi"
    assert d["labels"] == ["firmware"]
    assert d["components"] == ["PowerModule"]
    assert "Charger reboots on load." in d["description"]


def test_comment_trims_and_flattens_body():
    c = {"author": {"displayName": "Asha"}, "created": "2026-07-02T08:00:00.000+0000",
         "body": _ADF}
    out = _comment(c)
    assert out["author"] == "Asha"
    assert "Firmware 2.3" in out["body"]


@pytest.mark.asyncio
async def test_get_json_raises_on_401():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"errorMessages": ["Unauthorized"]})

    async with JiraAuthClient(base_url="https://ecog.test", email="e", api_token="t",
                              transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(RuntimeError, match="auth failed"):
            await _get_json(c, "/rest/api/3/myself")
```

- [ ] **Step 2: Run and verify it fails**

Run: `cd jira-mcp-server && python -m pytest tests/test_helpers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jira_mcp.server'`.

- [ ] **Step 3: Create `server.py` with the helpers (no tools yet)**

`jira-mcp-server/jira_mcp/server.py`:
```python
"""Read-only MCP tools over Ecog's Atlassian Cloud Jira (REST v3).

Descriptions and comments come back as Atlassian Document Format (ADF) JSON; we flatten
them to plain text and trim each issue/comment to a small whitelist so payloads stay
LLM-friendly. There is deliberately NO write tool — the server is read-only by construction.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx
from fastmcp import FastMCP

from .auth import JiraAuthClient
from .config import settings

# ── Pure helpers ─────────────────────────────────────────────────────────────────────────

_BLOCK_TYPES = {"paragraph", "heading", "listItem", "blockquote", "codeBlock"}


def _adf_to_text(node: Any) -> str:
    """Flatten an Atlassian Document Format node tree to plain text."""
    if node is None:
        return ""
    if isinstance(node, list):
        return "".join(_adf_to_text(n) for n in node)
    if not isinstance(node, dict):
        return ""
    ntype = node.get("type")
    if ntype == "text":
        return node.get("text", "")
    if ntype == "hardBreak":
        return "\n"
    text = _adf_to_text(node.get("content"))
    if ntype in _BLOCK_TYPES:
        return text + "\n"
    return text


def _drop_empty(d: dict) -> dict:
    """Drop keys whose value is None / empty string / empty list / empty dict."""
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


def _issue_summary(issue: dict) -> dict:
    """Flat, list-friendly view of one issue (used by search)."""
    f = issue.get("fields", {}) or {}
    return _drop_empty({
        "key": issue.get("key"),
        "summary": f.get("summary"),
        "status": (f.get("status") or {}).get("name"),
        "type": (f.get("issuetype") or {}).get("name"),
        "priority": (f.get("priority") or {}).get("name"),
        "assignee": (f.get("assignee") or {}).get("displayName"),
        "updated": f.get("updated"),
        "project": (f.get("project") or {}).get("key"),
    })


def _issue_detail(issue: dict) -> dict:
    """Full single-issue view: summary fields plus reporter, dates, labels, description."""
    f = issue.get("fields", {}) or {}
    detail = _issue_summary(issue)
    detail.update(_drop_empty({
        "reporter": (f.get("reporter") or {}).get("displayName"),
        "created": f.get("created"),
        "labels": f.get("labels") or None,
        "components": [c.get("name") for c in (f.get("components") or [])] or None,
        "description": _adf_to_text(f.get("description")).strip() or None,
    }))
    return detail


def _comment(c: dict) -> dict:
    return _drop_empty({
        "author": (c.get("author") or {}).get("displayName"),
        "created": c.get("created"),
        "body": _adf_to_text(c.get("body")).strip(),
    })


async def _get_json(client: httpx.AsyncClient, path: str, params: Optional[dict] = None) -> Any:
    """GET + parse JSON; turn auth failures into a clear message."""
    resp = await client.get(path, params=params or {})
    if resp.status_code in (401, 403):
        raise RuntimeError(
            f"Jira auth failed ({resp.status_code}) — check JIRA_EMAIL / JIRA_API_TOKEN "
            "and that the account has read access."
        )
    resp.raise_for_status()
    return resp.json() if resp.text.strip() else {}


# ── Client + server factories ────────────────────────────────────────────────────────────

def build_client() -> JiraAuthClient:
    return JiraAuthClient(
        base_url=settings.jira_base_url.rstrip("/"),
        email=settings.jira_email,
        api_token=settings.jira_api_token,
        timeout=settings.request_timeout,
    )


def build_server(client: Optional[JiraAuthClient] = None) -> FastMCP:
    mcp = FastMCP(name="Ecog Jira (read-only)")
    client = client or build_client()
    # Tools are registered in Tasks 4–7.
    _register_tools(mcp, client)
    return mcp


def _register_tools(mcp: FastMCP, client: JiraAuthClient) -> None:
    pass  # filled in Tasks 4–7
```

- [ ] **Step 4: Run and verify it passes**

Run: `cd jira-mcp-server && python -m pytest tests/test_helpers.py -v`
Expected: PASS (all helper tests green).

- [ ] **Step 5: Commit**

```bash
git add jira-mcp-server/jira_mcp/server.py jira-mcp-server/tests/test_helpers.py
git commit -m "feat(jira-mcp): ADF flattener, trimmers, and _get_json"
```

---

## Task 4: `jira_search` tool

**Files:**
- Modify: `jira-mcp-server/jira_mcp/server.py` (`_register_tools`)
- Test: `jira-mcp-server/tests/test_tools.py`

- [ ] **Step 1: Write the failing tool test**

`jira-mcp-server/tests/test_tools.py`:
```python
"""End-to-end tool calls via an in-memory FastMCP client, backed by a mock Jira."""

import httpx
import pytest
from fastmcp import Client

from jira_mcp.server import build_server


@pytest.mark.asyncio
async def test_jira_search_forwards_jql_and_trims(mock_client):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["jql"] = request.url.params.get("jql")
        captured["max"] = request.url.params.get("maxResults")
        return httpx.Response(200, json={"issues": [
            {"key": "ECOG-1", "fields": {
                "summary": "Charger reboots", "status": {"name": "Open"},
                "issuetype": {"name": "Bug"}, "priority": {"name": "High"},
                "assignee": {"displayName": "Asha"},
                "updated": "2026-07-10T10:00:00.000+0000", "project": {"key": "ECOG"}}}]})

    mcp = build_server(client=mock_client(handler))
    async with Client(mcp) as c:
        res = await c.call_tool(
            "jira_search", {"jql": "project = ECOG AND status = Open", "limit": 10})

    assert captured["path"] == "/rest/api/3/search/jql"
    assert captured["jql"] == "project = ECOG AND status = Open"
    assert captured["max"] == "10"
    assert res.data == [{
        "key": "ECOG-1", "summary": "Charger reboots", "status": "Open", "type": "Bug",
        "priority": "High", "assignee": "Asha",
        "updated": "2026-07-10T10:00:00.000+0000", "project": "ECOG"}]
```

- [ ] **Step 2: Run and verify it fails**

Run: `cd jira-mcp-server && python -m pytest tests/test_tools.py -v`
Expected: FAIL — `ToolError`/unknown tool `jira_search` (not registered yet).

- [ ] **Step 3: Implement `jira_search` in `_register_tools`**

Replace the `_register_tools` body in `jira-mcp-server/jira_mcp/server.py`:
```python
def _register_tools(mcp: FastMCP, client: JiraAuthClient) -> None:

    _SEARCH_FIELDS = "summary,status,issuetype,priority,assignee,updated,project"

    @mcp.tool
    async def jira_search(jql: str, limit: int = 50) -> list[dict]:
        """Search Ecog Jira issues with a JQL query (e.g.
        'project = ECOG AND status = Open ORDER BY updated DESC'). Returns a compact list:
        key, summary, status, type, priority, assignee, updated, project. Read-only."""
        data = await _get_json(client, "/rest/api/3/search/jql", params={
            "jql": jql, "maxResults": limit, "fields": _SEARCH_FIELDS,
        })
        return [_issue_summary(i) for i in data.get("issues", [])]
```

- [ ] **Step 4: Run and verify it passes**

Run: `cd jira-mcp-server && python -m pytest tests/test_tools.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jira-mcp-server/jira_mcp/server.py jira-mcp-server/tests/test_tools.py
git commit -m "feat(jira-mcp): jira_search (JQL) tool"
```

---

## Task 5: `jira_get_issue` tool

**Files:**
- Modify: `jira-mcp-server/jira_mcp/server.py` (`_register_tools`)
- Test: `jira-mcp-server/tests/test_tools.py`

- [ ] **Step 1: Write the failing test (append to `test_tools.py`)**

```python
@pytest.mark.asyncio
async def test_jira_get_issue_returns_detail(mock_client):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(200, json={
            "key": "ECOG-2",
            "fields": {
                "summary": "PM fault", "status": {"name": "In Progress"},
                "issuetype": {"name": "Bug"}, "reporter": {"displayName": "Ravi"},
                "created": "2026-07-01T09:00:00.000+0000", "labels": ["firmware"],
                "components": [{"name": "PowerModule"}],
                "description": {"type": "doc", "version": 1, "content": [
                    {"type": "paragraph", "content": [
                        {"type": "text", "text": "Sink PM overheats."}]}]}}})

    mcp = build_server(client=mock_client(handler))
    async with Client(mcp) as c:
        res = await c.call_tool("jira_get_issue", {"key": "ECOG-2"})

    assert captured["path"] == "/rest/api/3/issue/ECOG-2"
    assert res.data["key"] == "ECOG-2"
    assert res.data["reporter"] == "Ravi"
    assert res.data["components"] == ["PowerModule"]
    assert "Sink PM overheats." in res.data["description"]
```

- [ ] **Step 2: Run and verify it fails**

Run: `cd jira-mcp-server && python -m pytest tests/test_tools.py::test_jira_get_issue_returns_detail -v`
Expected: FAIL — unknown tool `jira_get_issue`.

- [ ] **Step 3: Add `jira_get_issue` inside `_register_tools`** (below `jira_search`)

```python
    _DETAIL_FIELDS = (
        "summary,status,issuetype,priority,assignee,reporter,created,updated,"
        "labels,components,description"
    )

    @mcp.tool
    async def jira_get_issue(key: str) -> dict:
        """Full detail of one Ecog Jira issue by key (e.g. 'ECOG-123'): summary, status,
        type, priority, reporter/assignee, dates, labels, components, and the description
        (flattened to text). Read-only."""
        data = await _get_json(client, f"/rest/api/3/issue/{key}", params={
            "fields": _DETAIL_FIELDS,
        })
        return _issue_detail(data)
```

- [ ] **Step 4: Run and verify it passes**

Run: `cd jira-mcp-server && python -m pytest tests/test_tools.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jira-mcp-server/jira_mcp/server.py jira-mcp-server/tests/test_tools.py
git commit -m "feat(jira-mcp): jira_get_issue tool"
```

---

## Task 6: `jira_get_comments` tool

**Files:**
- Modify: `jira-mcp-server/jira_mcp/server.py` (`_register_tools`)
- Test: `jira-mcp-server/tests/test_tools.py`

- [ ] **Step 1: Write the failing test (append to `test_tools.py`)**

```python
@pytest.mark.asyncio
async def test_jira_get_comments_flattens_bodies(mock_client):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["max"] = request.url.params.get("maxResults")
        return httpx.Response(200, json={"comments": [
            {"author": {"displayName": "Asha"}, "created": "2026-07-02T08:00:00.000+0000",
             "body": {"type": "doc", "version": 1, "content": [
                 {"type": "paragraph", "content": [
                     {"type": "text", "text": "Replaced the module."}]}]}}]})

    mcp = build_server(client=mock_client(handler))
    async with Client(mcp) as c:
        res = await c.call_tool("jira_get_comments", {"key": "ECOG-2", "limit": 25})

    assert captured["path"] == "/rest/api/3/issue/ECOG-2/comment"
    assert captured["max"] == "25"
    assert res.data == [{"author": "Asha", "created": "2026-07-02T08:00:00.000+0000",
                         "body": "Replaced the module."}]
```

- [ ] **Step 2: Run and verify it fails**

Run: `cd jira-mcp-server && python -m pytest tests/test_tools.py::test_jira_get_comments_flattens_bodies -v`
Expected: FAIL — unknown tool `jira_get_comments`.

- [ ] **Step 3: Add `jira_get_comments` inside `_register_tools`**

```python
    @mcp.tool
    async def jira_get_comments(key: str, limit: int = 50) -> list[dict]:
        """Comments on an Ecog Jira issue by key: author, created, and body text (ADF
        flattened). Useful for RCA discussion history. Read-only."""
        data = await _get_json(client, f"/rest/api/3/issue/{key}/comment", params={
            "maxResults": limit,
        })
        return [_comment(c) for c in data.get("comments", [])]
```

- [ ] **Step 4: Run and verify it passes**

Run: `cd jira-mcp-server && python -m pytest tests/test_tools.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jira-mcp-server/jira_mcp/server.py jira-mcp-server/tests/test_tools.py
git commit -m "feat(jira-mcp): jira_get_comments tool"
```

---

## Task 7: `jira_get_issue_transitions` tool

**Files:**
- Modify: `jira-mcp-server/jira_mcp/server.py` (`_register_tools`)
- Test: `jira-mcp-server/tests/test_tools.py`

- [ ] **Step 1: Write the failing test (append to `test_tools.py`)**

```python
@pytest.mark.asyncio
async def test_jira_get_issue_transitions_reads_status_and_targets(mock_client):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["method"] = request.method
        captured["expand"] = request.url.params.get("expand")
        return httpx.Response(200, json={
            "fields": {"status": {"name": "In Progress"}},
            "transitions": [
                {"id": "41", "name": "Done", "to": {"name": "Done"}},
                {"id": "21", "name": "Reopen", "to": {"name": "To Do"}}]})

    mcp = build_server(client=mock_client(handler))
    async with Client(mcp) as c:
        res = await c.call_tool("jira_get_issue_transitions", {"key": "ECOG-2"})

    assert captured["method"] == "GET"                      # read-only, never POST
    assert captured["path"] == "/rest/api/3/issue/ECOG-2"
    assert captured["expand"] == "transitions"
    assert res.data == {
        "status": "In Progress",
        "transitions": [{"id": "41", "name": "Done", "to": "Done"},
                        {"id": "21", "name": "Reopen", "to": "To Do"}]}
```

- [ ] **Step 2: Run and verify it fails**

Run: `cd jira-mcp-server && python -m pytest tests/test_tools.py::test_jira_get_issue_transitions_reads_status_and_targets -v`
Expected: FAIL — unknown tool `jira_get_issue_transitions`.

- [ ] **Step 3: Add `jira_get_issue_transitions` inside `_register_tools`**

```python
    @mcp.tool
    async def jira_get_issue_transitions(key: str) -> dict:
        """Current status of an Ecog Jira issue plus the transitions that are *available*
        from it — {status, transitions:[{id, name, to}]}. This only READS workflow options;
        it never performs a transition. Read-only."""
        data = await _get_json(client, f"/rest/api/3/issue/{key}", params={
            "fields": "status", "expand": "transitions",
        })
        status = ((data.get("fields") or {}).get("status") or {}).get("name")
        transitions = [
            {"id": t.get("id"), "name": t.get("name"), "to": (t.get("to") or {}).get("name")}
            for t in data.get("transitions", [])
        ]
        return _drop_empty({"status": status, "transitions": transitions})
```

- [ ] **Step 4: Run and verify it passes**

Run: `cd jira-mcp-server && python -m pytest tests/test_tools.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jira-mcp-server/jira_mcp/server.py jira-mcp-server/tests/test_tools.py
git commit -m "feat(jira-mcp): jira_get_issue_transitions (read-only status view)"
```

---

## Task 8: Read-only guard test

**Files:**
- Test: `jira-mcp-server/tests/test_tools.py`

- [ ] **Step 1: Write the failing guard test (append to `test_tools.py`)**

```python
@pytest.mark.asyncio
async def test_server_registers_only_read_tools(mock_client):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    mcp = build_server(client=mock_client(handler))
    tools = await mcp.get_tools()          # dict: name -> Tool
    names = set(tools)

    assert names == {
        "jira_search", "jira_get_issue", "jira_get_comments", "jira_get_issue_transitions",
    }
    forbidden = ("create", "update", "delete", "add", "edit", "post", "assign", "move")
    assert not [n for n in names if any(f in n for f in forbidden)]
```

- [ ] **Step 2: Run and verify it passes immediately**

Run: `cd jira-mcp-server && python -m pytest tests/test_tools.py::test_server_registers_only_read_tools -v`
Expected: PASS (this is a regression guard, not new behavior). If the tool set differs, fix the tool names — do not relax the assertion.

- [ ] **Step 3: Run the whole suite**

Run: `cd jira-mcp-server && python -m pytest -v`
Expected: all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add jira-mcp-server/tests/test_tools.py
git commit -m "test(jira-mcp): assert the server is read-only (no write tools)"
```

---

## Task 9: Entrypoint, Dockerfile, README

**Files:**
- Create: `jira-mcp-server/jira_mcp/__main__.py`
- Create: `jira-mcp-server/Dockerfile`
- Create: `jira-mcp-server/README.md`

- [ ] **Step 1: Create `__main__.py`**

`jira-mcp-server/jira_mcp/__main__.py`:
```python
"""Entry point: ``python -m jira_mcp`` runs the Ecog Jira MCP server."""

from __future__ import annotations

from .config import settings
from .server import build_server


def main() -> None:
    mcp = build_server()
    transport = settings.mcp_transport.lower()
    if transport == "http":
        mcp.run(
            transport="http",
            host=settings.mcp_host,
            port=settings.mcp_port,
            path="/mcp",
            host_origin_protection=False,
        )
    else:
        mcp.run()  # stdio


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify the server boots over stdio**

Run (from `jira-mcp-server/`, with the test creds so `Settings()` loads):
```bash
JIRA_EMAIL=test@ecog.local JIRA_API_TOKEN=x MCP_TRANSPORT=stdio \
  python -c "from jira_mcp.server import build_server; import asyncio; \
  print(sorted(asyncio.run(build_server().get_tools())))"
```
Expected output:
```
['jira_get_comments', 'jira_get_issue', 'jira_get_issue_transitions', 'jira_search']
```

- [ ] **Step 3: Create `Dockerfile`**

`jira-mcp-server/Dockerfile`:
```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY jira_mcp ./jira_mcp

ENV MCP_TRANSPORT=http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8102

EXPOSE 8102

# JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN must be provided at runtime (env / compose).
CMD ["python", "-m", "jira_mcp"]
```

- [ ] **Step 4: Create `README.md`**

`jira-mcp-server/README.md`:
```markdown
# jira-mcp-server — Ecog Jira (read-only)

Self-hosted [FastMCP](https://github.com/jlowin/fastmcp) server exposing **Ecog's Atlassian
Cloud Jira** (REST API v3) as read-only tools, mirroring `cms-mcp-server`.

## Tools
- `jira_search(jql, limit=50)` — search issues by JQL.
- `jira_get_issue(key)` — full detail of one issue (description flattened from ADF).
- `jira_get_comments(key, limit=50)` — comments (author, created, body text).
- `jira_get_issue_transitions(key)` — current status + *available* transitions (read only).

Read-only by construction: there is no write tool.

## Configuration (env / `.env`)
| Var | Default | Notes |
|---|---|---|
| `JIRA_BASE_URL` | `https://ecog.atlassian.net` | Ecog Cloud site. |
| `JIRA_EMAIL` | *(required)* | Shared read-only account. |
| `JIRA_API_TOKEN` | *(required)* | Atlassian API token — **inject as a secret**. |
| `MCP_TRANSPORT` | `stdio` | `stdio` or `http`. |
| `MCP_HOST` / `MCP_PORT` | `127.0.0.1` / `8102` | Keep internal (never publish). |

Create an API token at <https://id.atlassian.com/manage-profile/security/api-tokens>.

## Run
```bash
pip install -r requirements.txt
JIRA_EMAIL=svc@… JIRA_API_TOKEN=… python -m jira_mcp        # stdio
MCP_TRANSPORT=http python -m jira_mcp                        # http on :8102
```

## Test
```bash
python -m pytest -v
```

> **Data note:** Ecog Jira is vendor-confidential; tool results reach the inference provider.
> Internal/dev use only until the residency decision (D1) is signed off.
```

- [ ] **Step 5: Commit**

```bash
git add jira-mcp-server/jira_mcp/__main__.py jira-mcp-server/Dockerfile jira-mcp-server/README.md
git commit -m "feat(jira-mcp): entrypoint, Dockerfile, README"
```

---

## Task 10: Wire into the CI test gate

**Files:**
- Modify: `.github/workflows/tests.yml:19`

- [ ] **Step 1: Add the server to the matrix**

In `.github/workflows/tests.yml`, change line 19 from:
```yaml
        server: [mcp-server, cms-mcp-server]
```
to:
```yaml
        server: [mcp-server, cms-mcp-server, jira-mcp-server]
```

- [ ] **Step 2: Verify the suite runs green locally the same way CI will**

Run: `cd jira-mcp-server && python -m pip install -r requirements.txt pytest pytest-asyncio && python -m pytest -v`
Expected: all tests PASS (matches the CI `Run tests` step).

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/tests.yml
git commit -m "ci(jira-mcp): add jira-mcp-server to the pytest matrix"
```

---

## Task 11: Deploy + register in LibreChat (config; live verify gated on credentials)

**Files:**
- Modify: `chat/docker-compose.yml`
- Modify: `chat/librechat.yaml`

> This task only wires configuration. The live end-to-end check (JR-3) and scoping it behind
> an agent (JR-4) require the real shared-account token and a running stack, done with the user.

- [ ] **Step 1: Add the compose service**

In `chat/docker-compose.yml`, after the `cms-mcp-server:` block (ends at the `restart: unless-stopped` around line 84), add a sibling service:
```yaml
  # Read-only MCP server for Ecog's Atlassian Cloud Jira (vendor tracker).
  jira-mcp-server:
    build: ../jira-mcp-server
    container_name: jira-mcp-server
    environment:
      - JIRA_BASE_URL=${JIRA_BASE_URL}
      - JIRA_EMAIL=${JIRA_EMAIL}
      - JIRA_API_TOKEN=${JIRA_API_TOKEN}
      - MCP_TRANSPORT=http
      - MCP_HOST=0.0.0.0
      - MCP_PORT=8102
    restart: unless-stopped
```

- [ ] **Step 2: Register the server + allow its address in `librechat.yaml`**

In `chat/librechat.yaml`, add to `mcpSettings.allowedAddresses` (currently `mcp-server:8100`,
`cms-mcp-server:8101`):
```yaml
    - "jira-mcp-server:8102"
```
and add to `mcpServers` (after the `cms:` entry):
```yaml
  jira:
    type: streamable-http
    url: "http://jira-mcp-server:8102/mcp"
    # Jira Cloud REST is fast; a modest timeout is fine.
    timeout: 60000
```

- [ ] **Step 3: Add the required env keys (no secrets in git)**

Add `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` to `chat/.env.example` (documentation only)
if that file exists; put the real values only in the untracked `chat/.env` / injected secrets.
Verify no token is committed:
```bash
git grep -nI "JIRA_API_TOKEN" -- chat/ | grep -v ".env.example"
```
Expected: only compose/yaml *references* (`${JIRA_API_TOKEN}`), never a literal token.

- [ ] **Step 4: Commit the wiring**

```bash
git add chat/docker-compose.yml chat/librechat.yaml chat/.env.example
git commit -m "feat(jira-mcp): register Ecog Jira MCP in compose + librechat (port 8102)"
```

- [ ] **Step 5: (With the user — live verification, JR-1/JR-3)**

With the real shared-account token in `chat/.env`:
```bash
cd chat && docker compose up -d --build jira-mcp-server
```
Then in LibreChat, on an agent that includes the `jira` tools, run a real query
(e.g. *"search Ecog Jira for open bugs updated this week"*). Confirm results return.
Then scope the agent to the intended role only (JR-4 / AC-8) so Ecog tools aren't fleet-wide.

- [ ] **Step 6: Mark backlog items**

In `docs/PRODUCTION-READINESS.md`, once verified, flip `JR-1`/`JR-2`/`JR-3` to `[x]` and note
`JR-4` status. Commit:
```bash
git add docs/PRODUCTION-READINESS.md
git commit -m "docs(backlog): Ecog Jira MCP pilot JR-1..JR-3 done"
```

---

## Self-Review Notes (for the implementer)

- **Spec coverage:** package layout (Task 1,3,9) · Basic auth (Task 2) · 4 read tools (Tasks 4–7) · ADF flattener (Task 3) · read-only guarantee (Task 8) · tests/P3-5 (Tasks 1–8) · CI gate/P3-1 (Task 10) · LibreChat wiring JR-3/JR-4 + internal port P0-3 (Task 11) · secrets/P0-6 (Task 11 Step 3) · D1/D2 caveats (README + Task 11 note).
- **Endpoint caveat (from spec):** this plan targets `/rest/api/3/search/jql`. If Ecog's tenant only answers the older `/rest/api/3/search`, adjust the path in `jira_search` and its test in Task 4 — the trim logic is unchanged. Verify during Task 11 Step 5 (JR-1).
- **FastMCP result access:** tool tests read `res.data` (deserialized structured output). If a FastMCP version returns results only under `res.structured_content`, adjust the assertions consistently — behavior is unchanged.
```
