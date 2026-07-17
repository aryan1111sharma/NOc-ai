# ChargeZone Jira MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a read-only, Quench-only (`QCH` project) MCP server over ChargeZone's Atlassian Cloud Jira, and wire it into the chat stack + NOC agent.

**Architecture:** A near-clone of `jira-mcp-server` (FastMCP over Jira Cloud REST v3, HTTP Basic auth, `streamable-http`). Hardened so every tool is locked to the single `QCH` project three ways: a one-project allow-list, project-injection into search JQL, and a `QCH-####` key guard on the by-key tools. GET-only — no write tool exists.

**Tech Stack:** Python 3.12, FastMCP ≥3.4, httpx ≥0.28, pydantic-settings ≥2.0, pytest. Docker Compose + LibreChat for integration.

**Spec:** `docs/superpowers/specs/2026-07-17-chargezone-jira-mcp-server-design.md`

**Naming (locked):** dir `chargezone-jira-mcp-server`, package `cz_jira_mcp`, tool prefix `cz_jira_`, librechat `mcpServers` key `chargezone`, port `8105`, env prefix `CZ_JIRA_`.

---

## File Structure

```
chargezone-jira-mcp-server/
├── cz_jira_mcp/
│   ├── __init__.py        # version string
│   ├── __main__.py        # entrypoint (stdio/http)
│   ├── config.py          # Settings: CZ_JIRA_* + project_key=QCH + port 8105
│   ├── auth.py            # JiraAuthClient (Basic auth) — ported verbatim
│   └── server.py         # helpers + scope guards + 4 read-only QCH-locked tools
├── tests/
│   ├── conftest.py        # dummy CZ_JIRA_* creds + mock_client fixture
│   ├── test_config.py
│   ├── test_auth.py
│   ├── test_helpers.py
│   ├── test_scope.py      # NEW — scope guard + JQL wrapping
│   └── test_tools.py
├── Dockerfile
├── requirements.txt
└── README.md
```
Modified for integration: `chat/docker-compose.yml`, `chat/librechat.yaml`, `chat/.env`, `chat/.env.example`, `chat/agents/agents.json`.

---

## Task 1: Scaffold package + config (TDD)

**Files:**
- Create: `chargezone-jira-mcp-server/cz_jira_mcp/__init__.py`
- Create: `chargezone-jira-mcp-server/cz_jira_mcp/config.py`
- Create: `chargezone-jira-mcp-server/tests/conftest.py`
- Create: `chargezone-jira-mcp-server/tests/test_config.py`
- Create: `chargezone-jira-mcp-server/requirements.txt`

- [ ] **Step 1: Create `requirements.txt`**

```
fastmcp>=3.4
httpx>=0.28
pydantic-settings>=2.0
```

- [ ] **Step 2: Create `cz_jira_mcp/__init__.py`**

```python
"""Read-only MCP server for ChargeZone's Atlassian Cloud Jira, scoped to the QCH (Quench) project."""

__version__ = "0.1.0"
```

- [ ] **Step 3: Create `tests/conftest.py`** (sets creds BEFORE config import; adds `mock_client`)

```python
"""Shared test setup: dummy CZ Jira creds (so `config.Settings()` imports) + a mock client."""

import os

# Set BEFORE any cz_jira_mcp.config import happens during collection.
os.environ.setdefault("CZ_JIRA_EMAIL", "test@quench.local")
os.environ.setdefault("CZ_JIRA_API_TOKEN", "test-token")

import httpx
import pytest

from cz_jira_mcp.auth import JiraAuthClient


@pytest.fixture
def mock_client():
    """Factory: given an httpx handler, return a JiraAuthClient wired to a MockTransport."""
    def _make(handler):
        return JiraAuthClient(
            base_url="https://cz.test",
            email="e@x.com",
            api_token="tok",
            transport=httpx.MockTransport(handler),
        )
    return _make
```

- [ ] **Step 4: Write the failing config test** — `tests/test_config.py`

```python
def test_settings_defaults_and_required(monkeypatch):
    monkeypatch.setenv("CZ_JIRA_EMAIL", "sprathore@quenchchargers.com")
    monkeypatch.setenv("CZ_JIRA_API_TOKEN", "abc123")
    from cz_jira_mcp.config import Settings

    s = Settings()
    assert s.cz_jira_email == "sprathore@quenchchargers.com"
    assert s.cz_jira_api_token == "abc123"
    assert s.cz_jira_base_url == "https://team-1604552506221.atlassian.net"
    assert s.project_key == "QCH"
    assert s.mcp_port == 8105
    assert s.mcp_transport == "stdio"
```

- [ ] **Step 5: Run it — expect failure**

Run: `cd chargezone-jira-mcp-server && python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cz_jira_mcp.config'` (also `auth` import in conftest fails; that's fine, resolved in Task 2). If collection blocks on the `auth` import, temporarily comment the conftest `from cz_jira_mcp.auth import ...` line and its fixture, then restore in Task 2.

- [ ] **Step 6: Create `cz_jira_mcp/config.py`**

```python
"""Configuration for the ChargeZone (Quench-scoped) Jira MCP server, from environment / .env."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ChargeZone Jira — Atlassian Cloud, REST v3 with HTTP Basic (email + API token).
    cz_jira_base_url: str = "https://team-1604552506221.atlassian.net"
    cz_jira_email: str                   # account that owns the API token
    cz_jira_api_token: str               # injected as a secret; never committed
    project_key: str = "QCH"             # HARD scope — only this project is reachable
    request_timeout: float = 60.0

    # MCP transport: "stdio" or "http"
    mcp_transport: str = "stdio"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8105                  # noc=8100, cms=8101, jira=8102, docgen=8103, ecog-sd=8104


settings = Settings()
```

- [ ] **Step 7: Run the config test — expect PASS**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS (restore the conftest auth import once Task 2 lands).

- [ ] **Step 8: Commit**

```bash
git add chargezone-jira-mcp-server/
git commit -m "feat(cz-jira): scaffold package + QCH-scoped config"
```

---

## Task 2: Auth client (TDD)

**Files:**
- Create: `chargezone-jira-mcp-server/cz_jira_mcp/auth.py`
- Create: `chargezone-jira-mcp-server/tests/test_auth.py`

- [ ] **Step 1: Write the failing test** — `tests/test_auth.py`

```python
"""JiraAuthClient sets HTTP Basic auth and JSON accept header."""

import base64

import httpx
import pytest

from cz_jira_mcp.auth import JiraAuthClient


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

- [ ] **Step 2: Run it — expect failure**

Run: `python -m pytest tests/test_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cz_jira_mcp.auth'`

- [ ] **Step 3: Create `cz_jira_mcp/auth.py`** (ported verbatim from template)

```python
"""HTTP Basic-auth client for Jira Cloud REST v3.

Jira Cloud authenticates with `email:api_token` over HTTP Basic — no session/cookie
dance. The token is long-lived, so there is no re-login loop. Every request also asks
for JSON.
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

- [ ] **Step 4: Run test — expect PASS**

Run: `python -m pytest tests/test_auth.py tests/test_config.py -v`
Expected: PASS (2 files). If `pytest-asyncio` complains about mode, add `chargezone-jira-mcp-server/pytest.ini` with:
```ini
[pytest]
asyncio_mode = auto
```
(Check whether the sibling `jira-mcp-server` has a pytest.ini / pyproject asyncio setting and mirror it.)

- [ ] **Step 5: Commit**

```bash
git add chargezone-jira-mcp-server/cz_jira_mcp/auth.py chargezone-jira-mcp-server/tests/test_auth.py
git commit -m "feat(cz-jira): Basic-auth Jira client"
```

---

## Task 3: Server helpers (ADF → text, shaping) (TDD)

**Files:**
- Create: `chargezone-jira-mcp-server/cz_jira_mcp/server.py` (helpers portion only in this task)
- Create: `chargezone-jira-mcp-server/tests/test_helpers.py`

- [ ] **Step 1: Write the failing helpers test** — `tests/test_helpers.py`

```python
import httpx
import pytest

from cz_jira_mcp.auth import JiraAuthClient
from cz_jira_mcp.server import (
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
        "key": "QCH-1",
        "fields": {
            "summary": "Boot loop", "status": {"name": "Open"},
            "issuetype": {"name": "Bug"}, "priority": {"name": "High"},
            "assignee": {"displayName": "Asha"}, "updated": "2026-07-10T10:00:00.000+0000",
            "project": {"key": "QCH"},
        },
    }
    assert _issue_summary(issue) == {
        "key": "QCH-1", "summary": "Boot loop", "status": "Open", "type": "Bug",
        "priority": "High", "assignee": "Asha",
        "updated": "2026-07-10T10:00:00.000+0000", "project": "QCH",
    }


def test_issue_detail_adds_description_and_components():
    issue = {
        "key": "QCH-2",
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

    async with JiraAuthClient(base_url="https://cz.test", email="e", api_token="t",
                              transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(RuntimeError, match="auth failed"):
            await _get_json(c, "/rest/api/3/myself")


@pytest.mark.asyncio
async def test_get_json_raises_on_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"errorMessages": ["Issue does not exist"]})

    async with JiraAuthClient(base_url="https://cz.test", email="e", api_token="t",
                              transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(RuntimeError, match="404"):
            await _get_json(c, "/rest/api/3/issue/QCH-999")


def test_adf_to_text_reads_mention_and_emoji_attrs():
    doc = {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [
                {"type": "text", "text": "cc "},
                {"type": "mention", "attrs": {"id": "1", "text": "@Asha"}},
            ]},
        ],
    }
    out = _adf_to_text(doc)
    assert "@Asha" in out

    emoji_node = {"type": "emoji", "attrs": {"shortName": ":warning:", "text": "⚠️"}}
    assert _adf_to_text(emoji_node) == "⚠️"
```

- [ ] **Step 2: Run it — expect failure**

Run: `python -m pytest tests/test_helpers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cz_jira_mcp.server'`

- [ ] **Step 3: Create `cz_jira_mcp/server.py` with the helpers block** (ported from template; tools added in Task 5)

```python
"""Read-only MCP tools over ChargeZone's Atlassian Cloud Jira (REST v3), scoped to QCH.

Descriptions and comments come back as Atlassian Document Format (ADF) JSON; we flatten
them to plain text and trim each issue/comment to a small whitelist so payloads stay
LLM-friendly. There is deliberately NO write tool — the server is read-only by construction,
and every tool is locked to the QCH (Quench) project.
"""

from __future__ import annotations

import re
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
    attrs = node.get("attrs") or {}
    if "content" not in node:
        leaf = attrs.get("text") or attrs.get("url")
        if leaf:
            return leaf
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
            f"Jira auth failed ({resp.status_code}) — check CZ_JIRA_EMAIL / CZ_JIRA_API_TOKEN "
            "and that the account has read access."
        )
    if resp.status_code == 404:
        raise RuntimeError(
            f"Jira returned 404 for {path} — no such issue/resource or not visible to this account."
        )
    resp.raise_for_status()
    return resp.json() if resp.text.strip() else {}
```

- [ ] **Step 4: Run helpers test — expect PASS**

Run: `python -m pytest tests/test_helpers.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add chargezone-jira-mcp-server/cz_jira_mcp/server.py chargezone-jira-mcp-server/tests/test_helpers.py
git commit -m "feat(cz-jira): ADF-flatten + issue/comment shaping helpers"
```

---

## Task 4: Scope guards — QCH key guard + JQL wrapping (TDD)

This is the core hardening. `require_qch` rejects non-QCH keys; `build_scoped_jql` prepends the project filter and correctly keeps any `ORDER BY` OUTSIDE the parentheses (JQL forbids `ORDER BY` inside `()`).

**Files:**
- Modify: `chargezone-jira-mcp-server/cz_jira_mcp/server.py` (append scope helpers after `_get_json`)
- Create: `chargezone-jira-mcp-server/tests/test_scope.py`

- [ ] **Step 1: Write the failing scope test** — `tests/test_scope.py`

```python
import pytest

from cz_jira_mcp.server import build_scoped_jql, require_qch


@pytest.mark.parametrize("key", ["QCH-1", "QCH-4490", "  QCH-7  "])
def test_require_qch_accepts_valid_keys(key):
    assert require_qch(key) == key.strip()


@pytest.mark.parametrize("key", ["CHAR-1", "ADOR-9", "qch-1", "QCH-", "QCHX-1", "QCH1", "", "  "])
def test_require_qch_rejects_foreign_or_malformed(key):
    with pytest.raises(ValueError, match="QCH"):
        require_qch(key)


def test_build_scoped_jql_empty_uses_default_order():
    assert build_scoped_jql("") == 'project = "QCH" ORDER BY updated DESC'


def test_build_scoped_jql_wraps_conditions_and_adds_default_order():
    assert build_scoped_jql('status = "To Do"') == \
        'project = "QCH" AND (status = "To Do") ORDER BY updated DESC'


def test_build_scoped_jql_keeps_user_order_by_outside_parens():
    assert build_scoped_jql("status = Open ORDER BY created ASC") == \
        'project = "QCH" AND (status = Open) ORDER BY created ASC'


def test_build_scoped_jql_order_by_only():
    assert build_scoped_jql("ORDER BY created DESC") == 'project = "QCH" ORDER BY created DESC'


def test_build_scoped_jql_foreign_project_is_neutralized_by_and():
    # A smuggled project filter stays ANDed under QCH → resolves to empty, never widens scope.
    assert build_scoped_jql("project = ADNOC") == \
        'project = "QCH" AND (project = ADNOC) ORDER BY updated DESC'
```

- [ ] **Step 2: Run it — expect failure**

Run: `python -m pytest tests/test_scope.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_scoped_jql'`

- [ ] **Step 3: Append scope helpers to `cz_jira_mcp/server.py`** (after `_get_json`, before the factories)

```python
# ── Scope guards (QCH-only) ──────────────────────────────────────────────────────────────

_KEY_RE = re.compile(rf"^{re.escape(settings.project_key)}-\d+$")
_ORDER_BY_RE = re.compile(r"\border\s+by\b", re.IGNORECASE)


def require_qch(key: str) -> str:
    """Return the trimmed key iff it is a QCH issue key; otherwise raise. Runs before any call."""
    k = key.strip() if isinstance(key, str) else key
    if not isinstance(k, str) or not _KEY_RE.match(k):
        raise ValueError(
            f"Only {settings.project_key} (Quench) issues are accessible through this server; "
            f"got {key!r}."
        )
    return k


def build_scoped_jql(conditions: str) -> str:
    """Prepend the QCH project filter so no query can leave the project.

    Any user `ORDER BY` is kept OUTSIDE the parentheses (JQL forbids ORDER BY inside `()`).
    Empty conditions → newest-first QCH listing. The result is always project-bounded, which
    also satisfies Jira's "no unbounded JQL" rule on /rest/api/3/search/jql.
    """
    proj = f'project = "{settings.project_key}"'
    conditions = (conditions or "").strip()
    if not conditions:
        return f"{proj} ORDER BY updated DESC"
    m = _ORDER_BY_RE.search(conditions)
    if m:
        where = conditions[: m.start()].strip()
        order = conditions[m.start():].strip()
    else:
        where, order = conditions, "ORDER BY updated DESC"
    return f"{proj} AND ({where}) {order}" if where else f"{proj} {order}"
```

- [ ] **Step 4: Run scope test — expect PASS**

Run: `python -m pytest tests/test_scope.py -v`
Expected: PASS (all parametrized cases).

- [ ] **Step 5: Commit**

```bash
git add chargezone-jira-mcp-server/cz_jira_mcp/server.py chargezone-jira-mcp-server/tests/test_scope.py
git commit -m "feat(cz-jira): QCH key guard + project-scoped JQL builder"
```

---

## Task 5: The 4 read-only tools + factories (TDD)

**Files:**
- Modify: `chargezone-jira-mcp-server/cz_jira_mcp/server.py` (append factories + `_register_tools`)
- Create: `chargezone-jira-mcp-server/tests/test_tools.py`

- [ ] **Step 1: Write the failing tools test** — `tests/test_tools.py`

```python
"""End-to-end tool calls via an in-memory FastMCP client, backed by a mock Jira."""

import httpx
import pytest
from fastmcp import Client

from cz_jira_mcp.server import build_server


@pytest.mark.asyncio
async def test_cz_jira_search_scopes_jql_to_qch_and_trims(mock_client):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["jql"] = request.url.params.get("jql")
        captured["max"] = request.url.params.get("maxResults")
        return httpx.Response(200, json={"issues": [
            {"key": "QCH-1", "fields": {
                "summary": "Charger offline", "status": {"name": "To Do"},
                "issuetype": {"name": "Task"}, "priority": {"name": "High"},
                "assignee": {"displayName": "Asha"},
                "updated": "2026-07-10T10:00:00.000+0000", "project": {"key": "QCH"}}}]})

    mcp = build_server(client=mock_client(handler))
    async with Client(mcp) as c:
        res = await c.call_tool("cz_jira_search", {"conditions": 'status = "To Do"', "limit": 10})

    assert captured["path"] == "/rest/api/3/search/jql"
    assert captured["jql"] == 'project = "QCH" AND (status = "To Do") ORDER BY updated DESC'
    assert captured["max"] == "10"
    assert res.data == [{
        "key": "QCH-1", "summary": "Charger offline", "status": "To Do", "type": "Task",
        "priority": "High", "assignee": "Asha",
        "updated": "2026-07-10T10:00:00.000+0000", "project": "QCH"}]


@pytest.mark.asyncio
async def test_cz_jira_get_issue_returns_detail(mock_client):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(200, json={
            "key": "QCH-2",
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
        res = await c.call_tool("cz_jira_get_issue", {"key": "QCH-2"})

    assert captured["path"] == "/rest/api/3/issue/QCH-2"
    assert res.data["key"] == "QCH-2"
    assert res.data["reporter"] == "Ravi"
    assert res.data["components"] == ["PowerModule"]
    assert "Sink PM overheats." in res.data["description"]


@pytest.mark.asyncio
async def test_cz_jira_get_issue_rejects_foreign_key(mock_client):
    called = {"hit": False}

    def handler(request: httpx.Request) -> httpx.Response:
        called["hit"] = True
        return httpx.Response(200, json={})

    mcp = build_server(client=mock_client(handler))
    async with Client(mcp) as c:
        with pytest.raises(Exception, match="QCH"):
            await c.call_tool("cz_jira_get_issue", {"key": "CHAR-1"})
    assert called["hit"] is False  # rejected before any network call


@pytest.mark.asyncio
async def test_cz_jira_get_comments_flattens_bodies(mock_client):
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
        res = await c.call_tool("cz_jira_get_comments", {"key": "QCH-2", "limit": 25})

    assert captured["path"] == "/rest/api/3/issue/QCH-2/comment"
    assert captured["max"] == "25"
    assert res.data == [{"author": "Asha", "created": "2026-07-02T08:00:00.000+0000",
                         "body": "Replaced the module."}]


@pytest.mark.asyncio
async def test_cz_jira_get_issue_transitions_reads_status_and_targets(mock_client):
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
        res = await c.call_tool("cz_jira_get_issue_transitions", {"key": "QCH-2"})

    assert captured["method"] == "GET"                      # read-only, never POST
    assert captured["path"] == "/rest/api/3/issue/QCH-2"
    assert captured["expand"] == "transitions"
    assert res.data == {
        "status": "In Progress",
        "transitions": [{"id": "41", "name": "Done", "to": "Done"},
                        {"id": "21", "name": "Reopen", "to": "To Do"}]}


@pytest.mark.asyncio
async def test_server_registers_only_read_tools(mock_client):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    mcp = build_server(client=mock_client(handler))
    tools = await mcp.list_tools()
    names = {t.name for t in tools}

    assert names == {
        "cz_jira_search", "cz_jira_get_issue", "cz_jira_get_comments",
        "cz_jira_get_issue_transitions",
    }
    forbidden = ("create", "update", "delete", "add", "edit", "post", "assign", "move")
    assert not [n for n in names if any(f in n for f in forbidden)]
```

- [ ] **Step 2: Run it — expect failure**

Run: `python -m pytest tests/test_tools.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_server'`

- [ ] **Step 3: Append factories + tools to `cz_jira_mcp/server.py`**

```python
# ── Client + server factories ────────────────────────────────────────────────────────────

def build_client() -> JiraAuthClient:
    return JiraAuthClient(
        base_url=settings.cz_jira_base_url.rstrip("/"),
        email=settings.cz_jira_email,
        api_token=settings.cz_jira_api_token,
        timeout=settings.request_timeout,
    )


def build_server(client: Optional[JiraAuthClient] = None) -> FastMCP:
    mcp = FastMCP(name="ChargeZone Jira — Quench (read-only)")
    client = client or build_client()
    _register_tools(mcp, client)
    return mcp


def _register_tools(mcp: FastMCP, client: JiraAuthClient) -> None:

    _SEARCH_FIELDS = "summary,status,issuetype,priority,assignee,updated,project"

    @mcp.tool
    async def cz_jira_search(conditions: str = "", limit: int = 50) -> list[dict]:
        """Search Quench (QCH) Jira issues. Pass JQL *conditions* only — the part AFTER the
        project filter — e.g. 'status = "To Do"' or 'text ~ "offline" ORDER BY created DESC'.
        The server always scopes the query to project = QCH; you cannot query any other
        project. Empty conditions returns the most recently updated QCH issues. Returns a
        compact list: key, summary, status, type, priority, assignee, updated, project.
        Read-only. Single page only (Jira caps maxResults ~100)."""
        jql = build_scoped_jql(conditions)
        data = await _get_json(client, "/rest/api/3/search/jql", params={
            "jql": jql, "maxResults": limit, "fields": _SEARCH_FIELDS,
        })
        return [_issue_summary(i) for i in data.get("issues", [])]

    _DETAIL_FIELDS = (
        "summary,status,issuetype,priority,assignee,reporter,created,updated,"
        "labels,components,description"
    )

    @mcp.tool
    async def cz_jira_get_issue(key: str) -> dict:
        """Full detail of one Quench (QCH) Jira issue by key (e.g. 'QCH-4490'): summary,
        status, type, priority, reporter/assignee, dates, labels, components, and the
        description (flattened to text). Only QCH keys are accepted. Read-only."""
        key = require_qch(key)
        data = await _get_json(client, f"/rest/api/3/issue/{key}", params={
            "fields": _DETAIL_FIELDS,
        })
        return _issue_detail(data)

    @mcp.tool
    async def cz_jira_get_comments(key: str, limit: int = 50) -> list[dict]:
        """Comments on a Quench (QCH) Jira issue by key: author, created, and body text (ADF
        flattened). Useful for RCA discussion history. Only QCH keys are accepted. Read-only."""
        key = require_qch(key)
        data = await _get_json(client, f"/rest/api/3/issue/{key}/comment", params={
            "maxResults": limit,
        })
        return [_comment(c) for c in data.get("comments", [])]

    @mcp.tool
    async def cz_jira_get_issue_transitions(key: str) -> dict:
        """Current status of a Quench (QCH) Jira issue plus the transitions *available* from
        it — {status, transitions:[{id, name, to}]}. This only READS workflow options; it
        never performs a transition. Only QCH keys are accepted. Read-only."""
        key = require_qch(key)
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

- [ ] **Step 4: Run the whole suite — expect PASS**

Run: `python -m pytest -v`
Expected: PASS — all files (config, auth, helpers, scope, tools). Note: `test_cz_jira_get_issue_rejects_foreign_key` asserts a `ValueError` surfaces as a tool error (FastMCP wraps tool exceptions); if the raised type differs, keep `match="QCH"` and broaden to `Exception` (already done).

- [ ] **Step 5: Commit**

```bash
git add chargezone-jira-mcp-server/cz_jira_mcp/server.py chargezone-jira-mcp-server/tests/test_tools.py
git commit -m "feat(cz-jira): 4 read-only QCH-locked tools + server factory"
```

---

## Task 6: Entrypoint, Dockerfile, README

**Files:**
- Create: `chargezone-jira-mcp-server/cz_jira_mcp/__main__.py`
- Create: `chargezone-jira-mcp-server/Dockerfile`
- Create: `chargezone-jira-mcp-server/README.md`

- [ ] **Step 1: Create `cz_jira_mcp/__main__.py`**

```python
"""Entry point: ``python -m cz_jira_mcp`` runs the ChargeZone (Quench) Jira MCP server."""

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

- [ ] **Step 2: Create `Dockerfile`**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY cz_jira_mcp ./cz_jira_mcp

ENV MCP_TRANSPORT=http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8105

EXPOSE 8105

# CZ_JIRA_BASE_URL / CZ_JIRA_EMAIL / CZ_JIRA_API_TOKEN must be provided at runtime (env / compose).
CMD ["python", "-m", "cz_jira_mcp"]
```

- [ ] **Step 3: Create `README.md`**

```markdown
# chargezone-jira-mcp-server — ChargeZone Jira, Quench project (read-only)

Self-hosted [FastMCP](https://github.com/jlowin/fastmcp) server exposing the **`QCH`
(Quench)** project inside **ChargeZone's Atlassian Cloud Jira** (REST API v3) as read-only
tools. Scoped to QCH only — no other project is reachable. Mirrors `jira-mcp-server`.

## Tools
- `cz_jira_search(conditions="", limit=50)` — search QCH issues by JQL *conditions* (the
  server prepends `project = "QCH"`; you cannot escape the project). Empty → newest QCH issues.
- `cz_jira_get_issue(key)` — full detail of one QCH issue (description flattened from ADF).
- `cz_jira_get_comments(key, limit=50)` — comments (author, created, body text).
- `cz_jira_get_issue_transitions(key)` — current status + *available* transitions (read only).

By-key tools accept **only `QCH-####` keys**. Read-only by construction — there is no write tool.

## Configuration (env / `.env`)
| Var | Default | Notes |
|---|---|---|
| `CZ_JIRA_BASE_URL` | `https://team-1604552506221.atlassian.net` | ChargeZone Cloud site. |
| `CZ_JIRA_EMAIL` | *(required)* | Account that owns the API token. |
| `CZ_JIRA_API_TOKEN` | *(required)* | Atlassian API token — **inject as a secret**. |
| `PROJECT_KEY` | `QCH` | Hard scope; do not change without a scope review. |
| `MCP_TRANSPORT` | `stdio` | `stdio` or `http`. |
| `MCP_HOST` / `MCP_PORT` | `127.0.0.1` / `8105` | Keep internal (never publish). |

Create an API token at <https://id.atlassian.com/manage-profile/security/api-tokens>.

## Run
```bash
pip install -r requirements.txt
CZ_JIRA_EMAIL=svc@… CZ_JIRA_API_TOKEN=… python -m cz_jira_mcp        # stdio
MCP_TRANSPORT=http python -m cz_jira_mcp                             # http on :8105
```

## Test
```bash
python -m pytest -v
```

> **Data note:** QCH tickets are Quench field-ops data; tool results reach the inference
> provider. Internal/dev use only until the residency decision (D1) is signed off.
```

- [ ] **Step 4: Smoke-check the module imports (stdio path, no network)**

Run: `cd chargezone-jira-mcp-server && CZ_JIRA_EMAIL=x@y.z CZ_JIRA_API_TOKEN=t python -c "from cz_jira_mcp.server import build_server; print('ok', {t.name for t in __import__('asyncio').run(build_server().list_tools())})"`
Expected: `ok {'cz_jira_search', 'cz_jira_get_issue', 'cz_jira_get_comments', 'cz_jira_get_issue_transitions'}`

- [ ] **Step 5: Commit**

```bash
git add chargezone-jira-mcp-server/cz_jira_mcp/__main__.py chargezone-jira-mcp-server/Dockerfile chargezone-jira-mcp-server/README.md
git commit -m "feat(cz-jira): entrypoint, Dockerfile, README"
```

---

## Task 7: Wire into Docker Compose + LibreChat

**Files:**
- Modify: `chat/docker-compose.yml` (add service after the `jira-mcp-server` block, ~line 97)
- Modify: `chat/librechat.yaml` (add `mcpServers.chargezone` after the `ecog-sd`/`docgen` entries)

- [ ] **Step 1: Add the compose service** — in `chat/docker-compose.yml`, after the `jira-mcp-server:` block (before `ecog-sd-mcp-server:`), matching the existing indentation:

```yaml
  # Read-only MCP for ChargeZone's Jira, scoped to the QCH (Quench) project.
  chargezone-jira-mcp-server:
    build: ../chargezone-jira-mcp-server
    container_name: chargezone-jira-mcp-server
    environment:
      - CZ_JIRA_BASE_URL=${CZ_JIRA_BASE_URL}
      - CZ_JIRA_EMAIL=${CZ_JIRA_EMAIL}
      - CZ_JIRA_API_TOKEN=${CZ_JIRA_API_TOKEN}
      - MCP_TRANSPORT=http
      - MCP_HOST=0.0.0.0
      - MCP_PORT=8105
    restart: unless-stopped
```

- [ ] **Step 2: Register the MCP server** — in `chat/librechat.yaml`, under `mcpServers:`, add (mirroring the `jira:` entry’s shape):

```yaml
  chargezone:
    type: streamable-http
    url: "http://chargezone-jira-mcp-server:8105/mcp"
```

- [ ] **Step 3: Validate compose syntax**

Run: `cd chat && docker compose config >/dev/null && echo "compose OK"`
Expected: `compose OK` (no YAML/interpolation errors). Missing `CZ_JIRA_*` vars only warn here; they’re added in Task 8.

- [ ] **Step 4: Commit**

```bash
git add chat/docker-compose.yml chat/librechat.yaml
git commit -m "feat(cz-jira): wire chargezone-jira into compose + librechat (:8105)"
```

---

## Task 8: Environment variables

**Files:**
- Modify: `chat/.env` (git-ignored — real secret; DO NOT commit)
- Modify: `chat/.env.example` (committed — placeholder names only)

- [ ] **Step 1: Confirm `chat/.env` is git-ignored**

Run: `git check-ignore chat/.env && echo IGNORED`
Expected: prints `chat/.env` then `IGNORED`. If NOT ignored, STOP — add `chat/.env` to `.gitignore` before writing any secret.

- [ ] **Step 2: Append real values to `chat/.env`** (do not echo the token in logs; do not `git add` this file)

```env
CZ_JIRA_BASE_URL=https://team-1604552506221.atlassian.net
CZ_JIRA_EMAIL=sprathore@quenchchargers.com
CZ_JIRA_API_TOKEN=<paste the ChargeZone API token here>
```

- [ ] **Step 3: Append placeholder keys to `chat/.env.example`** (committed — no secret value)

```env
# ChargeZone Jira (Quench / QCH project) — chargezone-jira-mcp-server
CZ_JIRA_BASE_URL=https://team-1604552506221.atlassian.net
CZ_JIRA_EMAIL=
CZ_JIRA_API_TOKEN=
```

- [ ] **Step 4: Commit ONLY the example** (verify the real `.env` is not staged)

```bash
git add chat/.env.example
git status --porcelain chat/.env    # must print nothing (untracked+ignored)
git commit -m "docs(cz-jira): add CZ_JIRA_* keys to .env.example"
```

---

## Task 9: Attach tools to the NOC agent

The NOC agent lives in the LibreChat DB, seeded/restored from `chat/agents/agents.json` via `chat/agents/restore-agents.ps1`. Tool IDs follow `<toolname>_mcp_<serverKey>`, so ours are `cz_jira_*_mcp_chargezone`. `agents.json` has several NOC-flavored records + duplicates, so confirm the active one first.

**Files:**
- Modify: `chat/agents/agents.json` (add 4 tool IDs to the active NOC agent's `tools[]`)

- [ ] **Step 1: Snapshot current DB agents** (so agents.json matches what’s live before editing)

Run: `cd chat/agents && ./export-agents.ps1`
Expected: `agents.json` refreshed from the running LibreChat DB. Note the `id` + `name` of the NOC agent currently served in the UI (the one the user uses — "NOC server agent glm-4.7"). If unsure which record is active, confirm with the user before editing.

- [ ] **Step 2: Add the 4 tool IDs** to that agent's `tools` array in `chat/agents/agents.json`:

```json
"cz_jira_search_mcp_chargezone",
"cz_jira_get_issue_mcp_chargezone",
"cz_jira_get_comments_mcp_chargezone",
"cz_jira_get_issue_transitions_mcp_chargezone"
```

Insert them alongside the existing `_mcp_noc` / `_mcp_cms` entries in the SAME agent record identified in Step 1 (valid JSON — mind the commas).

- [ ] **Step 3: Restore agents to the DB**

Run: `cd chat/agents && ./restore-agents.ps1`
Expected: script reports the NOC agent updated. (Requires the LibreChat DB/container running.)

- [ ] **Step 4: Commit**

```bash
git add chat/agents/agents.json
git commit -m "feat(cz-jira): attach cz_jira tools to NOC agent"
```

---

## Task 10: Live smoke test + version bump

**Files:**
- Modify: `VERSION`, `CHANGELOG.md` (via the repo's bump script)

- [ ] **Step 1: Build + start the new service**

Run: `cd chat && docker compose up -d --build chargezone-jira-mcp-server`
Expected: container `chargezone-jira-mcp-server` healthy; `docker compose logs --tail=30 chargezone-jira-mcp-server` shows FastMCP serving on `:8105` with no auth errors.

- [ ] **Step 2: Live tool smoke test from inside the container** (proves real Jira reads work)

Run:
```bash
docker compose exec chargezone-jira-mcp-server python -c "
import asyncio, json
from cz_jira_mcp.server import build_server
from fastmcp import Client
async def main():
    async with Client(build_server()) as c:
        r = await c.call_tool('cz_jira_search', {'conditions': '', 'limit': 3})
        print('search:', json.dumps(r.data, ensure_ascii=False)[:400])
        first = r.data[0]['key']
        d = await c.call_tool('cz_jira_get_issue', {'key': first})
        print('issue:', first, d.data.get('status'))
asyncio.run(main())
"
```
Expected: prints real `QCH-####` issues and one issue's status. If `401/403` → check `CZ_JIRA_*` in `chat/.env` and that the container was recreated after Task 8.

- [ ] **Step 3: Scope smoke test — foreign key is rejected**

Run:
```bash
docker compose exec chargezone-jira-mcp-server python -c "
import asyncio
from cz_jira_mcp.server import build_server
from fastmcp import Client
async def main():
    async with Client(build_server()) as c:
        try:
            await c.call_tool('cz_jira_get_issue', {'key': 'CHAR-1'})
            print('FAIL: CHAR-1 was not rejected')
        except Exception as e:
            print('OK rejected:', str(e)[:80])
asyncio.run(main())
"
```
Expected: `OK rejected: Only QCH ...`

- [ ] **Step 4: End-to-end in chat** (manual)

In the LibreChat UI, on the NOC agent, ask: *"Show me the 3 most recent open Quench (QCH) charger tickets."* Expect it to call `cz_jira_search` and return real QCH issues. (Restart the LibreChat/api container if the new tools don't appear: `docker compose restart api`.)

- [ ] **Step 5: Version bump (per-feature convention)**

Run: `pwsh scripts/bump-version.ps1 -Type minor` (feature → minor). Follow its prompts; it updates `VERSION` + `CHANGELOG.md` and tags. If the script errors mid-run on Windows PowerShell stderr escalation (known issue), complete the `VERSION`/`CHANGELOG` edits manually and tag.

- [ ] **Step 6: Final commit + push branch**

```bash
git add VERSION CHANGELOG.md
git commit -m "chore(release): cz-jira read-only MCP server"
git push origin feat/mcp-server
```

---

## Self-Review (completed)

- **Spec coverage:** §4 architecture → Tasks 1–6; §5 tools → Task 5; §6 scope enforcement → Tasks 4–5; §7 error handling → Tasks 3–5; §8 integration → Tasks 7–9; §9 testing → Tasks 1–5 + Task 10 live; §10 residency → README (Task 6); §11 success criteria → Task 10. All covered.
- **Placeholders:** none — every code/test/command step is concrete. The single deliberate human value is the token line in `chat/.env` (Task 8 Step 2), which must not be committed.
- **Type/name consistency:** `build_scoped_jql` / `require_qch` (Task 4) are used verbatim in Task 5; env fields `cz_jira_*` + `project_key` (Task 1) match `build_client`/`_get_json`/README/compose; tool names `cz_jira_*` consistent across Task 5 tests, README, and Task 9 tool IDs (`_mcp_chargezone`); port `8105` consistent across config, Dockerfile, compose, librechat.
- **Known wrinkle flagged:** which NOC agent record to patch (Task 9 Step 1) — resolved by exporting live DB first.
