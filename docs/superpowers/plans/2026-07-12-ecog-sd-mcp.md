# Ecog Service Desk MCP — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-hosted read-only MCP server (`ecog-sd-mcp-server/`) that reads Ecog's Jira **Service Desk** customer API using a browser **session cookie**, auto-refreshing the cookie via a Playwright **password login** when it expires.

**Architecture:** Mirrors `cms-mcp-server/` (session-cookie pattern). `CookieAuthClient` sends the cookie and, on a logged-out response (401 / HTML / redirect), refreshes once and retries — an injected async `refresh` callable does the actual login (Playwright in production, a fake in tests). Read-only by construction. Cookie parsing/trimming/HTML-strip are pure helpers.

**Tech Stack:** Python 3.12, FastMCP ≥3.4, httpx ≥0.28, pydantic-settings ≥2.0, Playwright ≥1.48, pytest + pytest-asyncio. Jira Service Management customer REST API (`/rest/servicedeskapi/*`).

**Spec:** `docs/superpowers/specs/2026-07-12-ecog-sd-mcp-design.md`

**Note for the implementer — running pytest on this machine:** the Bash tool's "RTK" hook mangles plain `python -m pytest` output (may say "No tests collected" when tests ran). Prefix with `rtk proxy`, e.g. `cd ecog-sd-mcp-server && rtk proxy python -m pytest -v`. This affects observation only.

---

## File Structure

| Path | Responsibility |
|---|---|
| `ecog-sd-mcp-server/ecog_sd_mcp/__init__.py` | Package marker + `__version__`. |
| `ecog-sd-mcp-server/ecog_sd_mcp/config.py` | `Settings` from env/.env. |
| `ecog-sd-mcp-server/ecog_sd_mcp/auth.py` | Cookie helpers + `CookieAuthClient` (refresh-on-logout). |
| `ecog-sd-mcp-server/ecog_sd_mcp/login.py` | Playwright password login → cookie header (lazy playwright import). |
| `ecog-sd-mcp-server/ecog_sd_mcp/server.py` | Pure trim/HTML helpers, `build_client`, `build_server`, 4 `sd_*` tools. |
| `ecog-sd-mcp-server/ecog_sd_mcp/__main__.py` | `python -m ecog_sd_mcp` entrypoint. |
| `ecog-sd-mcp-server/tests/*` | conftest + config/auth/helpers/tools tests. |
| `ecog-sd-mcp-server/requirements.txt` `Dockerfile` `README.md` | Deps, Playwright image, docs. |
| `.github/workflows/tests.yml:19` | Add `ecog-sd-mcp-server` to the matrix. |
| `chat/docker-compose.yml` · `chat/librechat.yaml` · `chat/.env.example` | Deploy + register (port 8103). |

Conventions from `cms-mcp-server`: `follow_redirects=False` so a login redirect is detectable; tools trim payloads; `python -m pytest` runs the suite.

---

## Task 1: Scaffold package + config

**Files:**
- Create: `ecog-sd-mcp-server/ecog_sd_mcp/__init__.py`
- Create: `ecog-sd-mcp-server/ecog_sd_mcp/config.py`
- Create: `ecog-sd-mcp-server/requirements.txt`
- Create: `ecog-sd-mcp-server/ecog_sd_mcp/auth.py` (stub), `login.py` (stub)
- Create: `ecog-sd-mcp-server/tests/conftest.py`
- Test: `ecog-sd-mcp-server/tests/test_config.py`

- [ ] **Step 1: `__init__.py`**
```python
"""Read-only MCP server for Ecog's Jira Service Desk (customer API, cookie auth)."""

__version__ = "0.1.0"
```

- [ ] **Step 2: `requirements.txt`**
```
fastmcp>=3.4
httpx>=0.28
pydantic-settings>=2.0
playwright>=1.48
```

- [ ] **Step 3: `config.py`**
```python
"""Configuration for the Ecog Service Desk MCP server, loaded from environment / .env."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Ecog Jira Service Management (customer portal) — cookie auth.
    ecog_base_url: str = "https://ecog.atlassian.net"
    ecog_email: str = ""            # for Playwright auto-login (optional)
    ecog_password: str = ""         # secret; injected. empty => cookie-only mode
    ecog_cookie: str = ""           # optional seed/fallback Cookie header string
    ecog_cookie_file: str = "/data/ecog_cookies.json"   # refreshed-cookie cache
    ecog_login_enabled: bool = True
    ecog_headless: bool = True
    request_timeout: float = 60.0

    # MCP transport
    mcp_transport: str = "stdio"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8103            # noc=8100, cms=8101, jira=8102, ecog-sd=8103


settings = Settings()
```

- [ ] **Step 4: `auth.py` stub** (real impl in Tasks 2–3, so imports resolve)
```python
"""Cookie auth client for Ecog Service Desk. (Filled in Tasks 2–3.)"""

from __future__ import annotations

import httpx

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


class CookieAuthClient(httpx.AsyncClient):
    def __init__(self, *args, cookie: str = "", refresh=None, cookie_file=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
```

- [ ] **Step 5: `login.py` stub**
```python
"""Playwright password login for Ecog. (Filled in Task 4.)"""

from __future__ import annotations
```

- [ ] **Step 6: `tests/conftest.py`**
```python
"""Shared test fixtures for ecog_sd_mcp."""

import httpx
import pytest

from ecog_sd_mcp.auth import CookieAuthClient


@pytest.fixture
def make_client():
    """Factory: build a CookieAuthClient over a MockTransport handler."""
    def _make(handler, **kw):
        return CookieAuthClient(
            base_url="https://ecog.test",
            transport=httpx.MockTransport(handler),
            **kw,
        )
    return _make
```

- [ ] **Step 7: failing `tests/test_config.py`**
```python
def test_settings_defaults():
    from ecog_sd_mcp.config import Settings
    s = Settings()
    assert s.ecog_base_url == "https://ecog.atlassian.net"
    assert s.mcp_port == 8103
    assert s.ecog_login_enabled is True
    assert s.ecog_email == "" and s.ecog_password == ""
```

- [ ] **Step 8: run → fails**

Run: `cd ecog-sd-mcp-server && rtk proxy python -m pytest tests/test_config.py -v`
Expected: FAIL (collection error — `ecog_sd_mcp` not importable yet) or assertion error.

- [ ] **Step 9: run → passes** (after files above exist)

Run: `cd ecog-sd-mcp-server && rtk proxy python -m pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 10: commit**
```bash
git add ecog-sd-mcp-server/ecog_sd_mcp ecog-sd-mcp-server/requirements.txt ecog-sd-mcp-server/tests
git commit -m "feat(ecog-sd): scaffold package + config"
```

---

## Task 2: Cookie helpers (pure)

**Files:**
- Modify: `ecog-sd-mcp-server/ecog_sd_mcp/auth.py`
- Test: `ecog-sd-mcp-server/tests/test_helpers.py`

- [ ] **Step 1: failing tests**

`ecog-sd-mcp-server/tests/test_helpers.py`:
```python
import json

from ecog_sd_mcp.auth import cookies_to_header, load_cookie, save_cookie


def test_cookies_to_header_joins_name_value():
    cookies = [{"name": "a", "value": "1"}, {"name": "b", "value": "2"}]
    assert cookies_to_header(cookies) == "a=1; b=2"


def test_cookies_to_header_empty():
    assert cookies_to_header([]) == ""


def test_save_then_load_roundtrip(tmp_path):
    p = tmp_path / "ck.json"
    save_cookie(str(p), "x=1; y=2")
    assert load_cookie(str(p)) == "x=1; y=2"
    assert json.loads(p.read_text())["cookie"] == "x=1; y=2"


def test_load_cookie_missing_file_returns_empty(tmp_path):
    assert load_cookie(str(tmp_path / "nope.json")) == ""
```

- [ ] **Step 2: run → fails**

Run: `cd ecog-sd-mcp-server && rtk proxy python -m pytest tests/test_helpers.py -v`
Expected: FAIL — `ImportError: cannot import name 'cookies_to_header'`.

- [ ] **Step 3: implement helpers** — add to `ecog_sd_mcp/auth.py` (above the class):
```python
import json
import os


def cookies_to_header(cookies: list[dict]) -> str:
    """Turn Playwright-style cookie dicts into a 'k=v; k=v' Cookie header string."""
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies if c.get("name"))


def load_cookie(path: str) -> str:
    """Read a cached Cookie header string; '' if the file is missing/unreadable."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("cookie", "") or ""
    except (OSError, ValueError):
        return ""


def save_cookie(path: str, header: str) -> None:
    """Persist the Cookie header string to the cache file (best effort)."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"cookie": header}, f)
    except OSError:
        pass
```

- [ ] **Step 4: run → passes**

Run: `cd ecog-sd-mcp-server && rtk proxy python -m pytest tests/test_helpers.py -v`
Expected: PASS.

- [ ] **Step 5: commit**
```bash
git add ecog-sd-mcp-server/ecog_sd_mcp/auth.py ecog-sd-mcp-server/tests/test_helpers.py
git commit -m "feat(ecog-sd): cookie serialization + cache helpers"
```

---

## Task 3: CookieAuthClient — refresh-on-logout

**Files:**
- Modify: `ecog-sd-mcp-server/ecog_sd_mcp/auth.py`
- Test: `ecog-sd-mcp-server/tests/test_auth.py`

- [ ] **Step 1: failing tests**

`ecog-sd-mcp-server/tests/test_auth.py`:
```python
import asyncio

import httpx
import pytest

from ecog_sd_mcp.auth import CookieAuthClient


def _handler_factory(state):
    """Return 401 until the request carries the refreshed cookie, then 200."""
    def handler(request: httpx.Request) -> httpx.Response:
        state["seen_cookies"].append(request.headers.get("cookie"))
        if request.headers.get("cookie") == "fresh=1":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(401, json={"err": "nope"})
    return handler


@pytest.mark.asyncio
async def test_refreshes_once_then_succeeds():
    state = {"seen_cookies": [], "logins": 0}
    async def refresh():
        state["logins"] += 1
        return "fresh=1"
    c = CookieAuthClient(base_url="https://ecog.test", cookie="stale=1", refresh=refresh,
                         transport=httpx.MockTransport(_handler_factory(state)))
    async with c:
        r = await c.get("/rest/servicedeskapi/request")
    assert r.status_code == 200
    assert state["logins"] == 1
    assert state["seen_cookies"] == ["stale=1", "fresh=1"]


@pytest.mark.asyncio
async def test_no_refresh_callable_raises():
    def handler(request): return httpx.Response(401, json={})
    c = CookieAuthClient(base_url="https://ecog.test", cookie="stale=1", refresh=None,
                         transport=httpx.MockTransport(handler))
    async with c:
        with pytest.raises(RuntimeError, match="session expired"):
            await c.get("/rest/servicedeskapi/request")


@pytest.mark.asyncio
async def test_concurrent_calls_refresh_once():
    state = {"seen_cookies": [], "logins": 0}
    async def refresh():
        state["logins"] += 1
        await asyncio.sleep(0)
        return "fresh=1"
    c = CookieAuthClient(base_url="https://ecog.test", cookie="stale=1", refresh=refresh,
                         transport=httpx.MockTransport(_handler_factory(state)))
    async with c:
        await asyncio.gather(*[c.get("/rest/servicedeskapi/request") for _ in range(4)])
    assert state["logins"] == 1
```

- [ ] **Step 2: run → fails**

Run: `cd ecog-sd-mcp-server && rtk proxy python -m pytest tests/test_auth.py -v`
Expected: FAIL — the stub client ignores `cookie`/`refresh` and does no retry.

- [ ] **Step 3: implement the client** — replace the `CookieAuthClient` class in `ecog_sd_mcp/auth.py`:
```python
import asyncio


class CookieAuthClient(httpx.AsyncClient):
    """httpx client that sends a session cookie and refreshes it once on logout.

    ``refresh`` is an async callable returning a fresh 'k=v; k=v' Cookie header string
    (a Playwright login in production; a fake in tests). On a logged-out response
    (401 / redirect / HTML) it refreshes under a lock and retries once. A single login
    serves concurrent callers.
    """

    def __init__(self, *args, cookie: str = "", refresh=None, cookie_file=None, **kwargs) -> None:
        kwargs.setdefault("follow_redirects", False)   # detect login redirects
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("User-Agent", _UA)
        headers.setdefault("Accept", "application/json")
        kwargs["headers"] = headers
        super().__init__(*args, **kwargs)
        self._cookie = cookie
        self._refresh = refresh
        self._cookie_file = cookie_file
        self._lock = asyncio.Lock()

    @staticmethod
    def _looks_logged_out(resp: httpx.Response) -> bool:
        if resp.status_code == 401 or resp.is_redirect:
            return True
        return "text/html" in resp.headers.get("content-type", "")

    async def _do_refresh(self) -> None:
        if not self._refresh:
            raise RuntimeError(
                "Ecog session expired — provide a fresh ECOG_COOKIE or enable auto-login."
            )
        self._cookie = await self._refresh()
        if self._cookie_file:
            save_cookie(self._cookie_file, self._cookie)

    async def send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        if self._cookie:
            request.headers["Cookie"] = self._cookie
        resp = await super().send(request, **kwargs)

        if self._looks_logged_out(resp):
            await resp.aread()
            await resp.aclose()
            stale = self._cookie
            async with self._lock:
                if self._cookie == stale:          # nobody refreshed while we waited
                    await self._do_refresh()
            request.headers["Cookie"] = self._cookie
            resp = await super().send(request, **kwargs)
        return resp
```

- [ ] **Step 4: run → passes**

Run: `cd ecog-sd-mcp-server && rtk proxy python -m pytest tests/test_auth.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: commit**
```bash
git add ecog-sd-mcp-server/ecog_sd_mcp/auth.py ecog-sd-mcp-server/tests/test_auth.py
git commit -m "feat(ecog-sd): CookieAuthClient with refresh-on-logout + single-login concurrency"
```

---

## Task 4: Playwright login

**Files:**
- Modify: `ecog-sd-mcp-server/ecog_sd_mcp/login.py`
- Test: `ecog-sd-mcp-server/tests/test_login.py`

- [ ] **Step 1: failing test** (only the pure CAPTCHA predicate is unit-tested; the Playwright flow is integration-verified at wiring)

`ecog-sd-mcp-server/tests/test_login.py`:
```python
from ecog_sd_mcp.login import _is_captcha


def test_is_captcha_detects_markers():
    assert _is_captcha("<div>Please verify you are human</div>") is True
    assert _is_captcha("<script src='recaptcha/api.js'>") is True


def test_is_captcha_false_on_normal_page():
    assert _is_captcha("<input id='password' type='password'>") is False
```

- [ ] **Step 2: run → fails**

Run: `cd ecog-sd-mcp-server && rtk proxy python -m pytest tests/test_login.py -v`
Expected: FAIL — `cannot import name '_is_captcha'`.

- [ ] **Step 3: implement `login.py`** (lazy playwright import so cookie-only mode never needs a browser):
```python
"""Playwright password login for Ecog Atlassian Cloud → fresh Cookie header string.

MFA must be OFF (spec). No CAPTCHA solving — if challenged, raise and let the operator
run headed once or inject ECOG_COOKIE manually. Selectors are current-as-of 2026-07 and
should be verified against the live login page.
"""

from __future__ import annotations

from .auth import _UA, cookies_to_header

_CAPTCHA_MARKERS = ("captcha", "recaptcha", "verify you are human", "unusual traffic")


def _is_captcha(html: str) -> bool:
    low = html.lower()
    return any(m in low for m in _CAPTCHA_MARKERS)


async def fetch_cookies_via_login(settings) -> str:
    """Drive Atlassian login with email+password; return the ecog.atlassian.net Cookie header."""
    from playwright.async_api import async_playwright  # lazy: only needed in login mode

    if not (settings.ecog_email and settings.ecog_password):
        raise RuntimeError("Ecog auto-login needs ECOG_EMAIL and ECOG_PASSWORD.")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=settings.ecog_headless)
        try:
            ctx = await browser.new_context(user_agent=_UA)
            page = await ctx.new_page()
            await page.goto(settings.ecog_base_url + "/", wait_until="domcontentloaded")
            await page.fill("input#username", settings.ecog_email)
            await page.click("#login-submit")
            await page.wait_for_selector("input#password", timeout=30000)
            if _is_captcha(await page.content()):
                raise RuntimeError(
                    "Ecog login hit a CAPTCHA/verification screen — run once with "
                    "ECOG_HEADLESS=false or inject a fresh ECOG_COOKIE manually."
                )
            await page.fill("input#password", settings.ecog_password)
            await page.click("#login-submit")
            await page.wait_for_url("**ecog.atlassian.net/**", timeout=45000)
            header = cookies_to_header(await ctx.cookies(settings.ecog_base_url))
            if not header:
                raise RuntimeError("Ecog login produced no cookies.")
            return header
        finally:
            await browser.close()
```

- [ ] **Step 4: run → passes**

Run: `cd ecog-sd-mcp-server && rtk proxy python -m pytest tests/test_login.py -v`
Expected: PASS.

- [ ] **Step 5: commit**
```bash
git add ecog-sd-mcp-server/ecog_sd_mcp/login.py ecog-sd-mcp-server/tests/test_login.py
git commit -m "feat(ecog-sd): Playwright password login + CAPTCHA guard"
```

---

## Task 5: server helpers + factories

**Files:**
- Create: `ecog-sd-mcp-server/ecog_sd_mcp/server.py`
- Test: append to `ecog-sd-mcp-server/tests/test_helpers.py`

- [ ] **Step 1: failing tests** — append to `tests/test_helpers.py`:
```python
from ecog_sd_mcp.server import _strip_html, _field, _request_summary, _comment

_REQ = {
    "issueKey": "SASW-2911",
    "reporter": {"displayName": "Chitransh Jain"},
    "createdDate": {"jira": "2026-07-01T09:00:00.000+0000"},
    "currentStatus": {"status": "Resolved"},
    "serviceDesk": {"projectKey": "SASW", "projectName": "Support"},
    "requestFieldValues": [
        {"fieldId": "summary", "label": "Summary", "value": "Invalid Undervoltage due to AC Meter"},
        {"fieldId": "description", "label": "Description", "value": "<p>Meter reads <b>low</b></p>"},
    ],
}


def test_strip_html():
    assert _strip_html("<p>Meter reads <b>low</b></p>") == "Meter reads low"
    assert _strip_html(None) == ""


def test_field_extracts_by_id():
    assert _field(_REQ, "summary") == "Invalid Undervoltage due to AC Meter"
    assert _field(_REQ, "missing") is None


def test_request_summary():
    assert _request_summary(_REQ) == {
        "key": "SASW-2911", "summary": "Invalid Undervoltage due to AC Meter",
        "status": "Resolved", "created": "2026-07-01T09:00:00.000+0000",
        "reporter": "Chitransh Jain", "serviceDesk": "SASW",
    }


def test_comment_strips_body():
    c = {"author": {"displayName": "Asha"}, "created": {"jira": "2026-07-02T08:00:00.000+0000"},
         "body": "<p>Replaced the meter</p>"}
    assert _comment(c) == {"author": "Asha", "created": "2026-07-02T08:00:00.000+0000",
                           "body": "Replaced the meter"}
```

- [ ] **Step 2: run → fails**

Run: `cd ecog-sd-mcp-server && rtk proxy python -m pytest tests/test_helpers.py -v`
Expected: FAIL — `No module named 'ecog_sd_mcp.server'`.

- [ ] **Step 3: create `server.py`** (helpers + factories; tools added in Tasks 6–9):
```python
"""Read-only MCP tools over Ecog's Jira Service Desk customer API (/rest/servicedeskapi).

Authenticated with a browser session cookie (see auth.py); no write tool exists.
"""

from __future__ import annotations

import re
from typing import Any, Optional

import httpx
from fastmcp import FastMCP

from .auth import CookieAuthClient, load_cookie
from .config import settings

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: Optional[str]) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", _TAG_RE.sub("", s)).strip()


def _drop_empty(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


def _field(request: dict, field_id: str) -> Optional[str]:
    for f in request.get("requestFieldValues", []) or []:
        if f.get("fieldId") == field_id:
            return f.get("value")
    return None


def _request_summary(r: dict) -> dict:
    return _drop_empty({
        "key": r.get("issueKey"),
        "summary": _field(r, "summary"),
        "status": (r.get("currentStatus") or {}).get("status"),
        "created": (r.get("createdDate") or {}).get("jira"),
        "reporter": (r.get("reporter") or {}).get("displayName"),
        "serviceDesk": (r.get("serviceDesk") or {}).get("projectKey"),
    })


def _request_detail(r: dict) -> dict:
    detail = _request_summary(r)
    detail.update(_drop_empty({
        "requestType": (r.get("requestType") or {}).get("name"),
        "description": _strip_html(_field(r, "description")),
    }))
    return detail


def _comment(c: dict) -> dict:
    return _drop_empty({
        "author": (c.get("author") or {}).get("displayName"),
        "created": (c.get("created") or {}).get("jira"),
        "body": _strip_html(c.get("body")),
    })


async def _get_json(client: httpx.AsyncClient, path: str, params: Optional[dict] = None) -> Any:
    resp = await client.get(path, params=params or {})
    resp.raise_for_status()
    return resp.json() if resp.text.strip() else {}


def build_client() -> CookieAuthClient:
    refresh = None
    if settings.ecog_login_enabled and settings.ecog_email and settings.ecog_password:
        async def refresh():  # noqa: E306  (lazy import keeps playwright optional)
            from .login import fetch_cookies_via_login
            return await fetch_cookies_via_login(settings)
    cookie = load_cookie(settings.ecog_cookie_file) or settings.ecog_cookie
    return CookieAuthClient(
        base_url=settings.ecog_base_url.rstrip("/"),
        cookie=cookie,
        refresh=refresh,
        cookie_file=settings.ecog_cookie_file,
        timeout=settings.request_timeout,
    )


def build_server(client: Optional[CookieAuthClient] = None) -> FastMCP:
    mcp = FastMCP(name="Ecog Service Desk (read-only)")
    client = client or build_client()
    _register_tools(mcp, client)
    return mcp


def _register_tools(mcp: FastMCP, client: CookieAuthClient) -> None:
    pass  # filled in Tasks 6–9
```

- [ ] **Step 4: run → passes**

Run: `cd ecog-sd-mcp-server && rtk proxy python -m pytest tests/test_helpers.py -v`
Expected: PASS.

- [ ] **Step 5: commit**
```bash
git add ecog-sd-mcp-server/ecog_sd_mcp/server.py ecog-sd-mcp-server/tests/test_helpers.py
git commit -m "feat(ecog-sd): service-desk trim/HTML helpers + client/server factories"
```

---

## Task 6: `sd_list_requests` tool

**Files:**
- Modify: `ecog-sd-mcp-server/ecog_sd_mcp/server.py` (`_register_tools`)
- Test: `ecog-sd-mcp-server/tests/test_tools.py`

- [ ] **Step 1: failing test**

`ecog-sd-mcp-server/tests/test_tools.py`:
```python
import httpx
import pytest
from fastmcp import Client

from ecog_sd_mcp.server import build_server


def _client(handler):
    from ecog_sd_mcp.auth import CookieAuthClient
    return CookieAuthClient(base_url="https://ecog.test", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_sd_list_requests_maps_params_and_trims():
    cap = {}

    def handler(request: httpx.Request) -> httpx.Response:
        cap["path"] = request.url.path
        cap["ownership"] = request.url.params.get("requestOwnership")
        cap["status"] = request.url.params.get("requestStatus")
        cap["search"] = request.url.params.get("searchTerm")
        cap["limit"] = request.url.params.get("limit")
        return httpx.Response(200, json={"values": [{
            "issueKey": "SASW-2911", "currentStatus": {"status": "Resolved"},
            "createdDate": {"jira": "2026-07-01T09:00:00.000+0000"},
            "reporter": {"displayName": "Chitransh Jain"},
            "serviceDesk": {"projectKey": "SASW"},
            "requestFieldValues": [{"fieldId": "summary", "value": "Invalid Undervoltage"}]}]})

    mcp = build_server(client=_client(handler))
    async with Client(mcp) as c:
        res = await c.call_tool("sd_list_requests",
                                {"search": "undervoltage", "status": "CLOSED", "ownership": "ALL", "limit": 10})

    assert cap["path"] == "/rest/servicedeskapi/request"
    assert cap["ownership"] == "ALL_REQUESTS"
    assert cap["status"] == "CLOSED_REQUESTS"
    assert cap["search"] == "undervoltage"
    assert cap["limit"] == "10"
    assert res.data == [{"key": "SASW-2911", "summary": "Invalid Undervoltage",
                         "status": "Resolved", "created": "2026-07-01T09:00:00.000+0000",
                         "reporter": "Chitransh Jain", "serviceDesk": "SASW"}]
```

- [ ] **Step 2: run → fails**

Run: `cd ecog-sd-mcp-server && rtk proxy python -m pytest tests/test_tools.py -v`
Expected: FAIL — unknown tool `sd_list_requests`.

- [ ] **Step 3: implement** — replace the `_register_tools` body in `server.py`:
```python
def _register_tools(mcp: FastMCP, client: CookieAuthClient) -> None:

    _OWN = {"OWNED": "OWNED_REQUESTS", "PARTICIPATED": "PARTICIPATED_REQUESTS", "ALL": "ALL_REQUESTS"}
    _STAT = {"OPEN": "OPEN_REQUESTS", "CLOSED": "CLOSED_REQUESTS", "ALL": "ALL_REQUESTS"}

    @mcp.tool
    async def sd_list_requests(search: Optional[str] = None, status: str = "ALL",
                               ownership: str = "OWNED", limit: int = 50) -> list[dict]:
        """List/search your Ecog Service Desk tickets. `status` ∈ OPEN|CLOSED|ALL,
        `ownership` ∈ OWNED|PARTICIPATED|ALL, `search` = free-text. Returns key, summary,
        status, created, reporter, serviceDesk. Read-only."""
        params = {
            "requestOwnership": _OWN.get(ownership.upper(), "OWNED_REQUESTS"),
            "requestStatus": _STAT.get(status.upper(), "ALL_REQUESTS"),
            "limit": limit,
        }
        if search:
            params["searchTerm"] = search
        data = await _get_json(client, "/rest/servicedeskapi/request", params=params)
        return [_request_summary(r) for r in data.get("values", [])]
```

- [ ] **Step 4: run → passes**

Run: `cd ecog-sd-mcp-server && rtk proxy python -m pytest tests/test_tools.py -v`
Expected: PASS.

- [ ] **Step 5: commit**
```bash
git add ecog-sd-mcp-server/ecog_sd_mcp/server.py ecog-sd-mcp-server/tests/test_tools.py
git commit -m "feat(ecog-sd): sd_list_requests tool"
```

---

## Task 7: `sd_get_request` tool

**Files:**
- Modify: `ecog-sd-mcp-server/ecog_sd_mcp/server.py` (`_register_tools`)
- Test: append to `ecog-sd-mcp-server/tests/test_tools.py`

- [ ] **Step 1: failing test** (append):
```python
@pytest.mark.asyncio
async def test_sd_get_request_returns_detail():
    cap = {}

    def handler(request: httpx.Request) -> httpx.Response:
        cap["path"] = request.url.path
        return httpx.Response(200, json={
            "issueKey": "SASW-2911", "currentStatus": {"status": "Resolved"},
            "createdDate": {"jira": "2026-07-01T09:00:00.000+0000"},
            "reporter": {"displayName": "Chitransh Jain"},
            "serviceDesk": {"projectKey": "SASW"},
            "requestType": {"name": "Technical support"},
            "requestFieldValues": [
                {"fieldId": "summary", "value": "Invalid Undervoltage"},
                {"fieldId": "description", "value": "<p>Meter reads low</p>"}]})

    mcp = build_server(client=_client(handler))
    async with Client(mcp) as c:
        res = await c.call_tool("sd_get_request", {"key": "SASW-2911"})

    assert cap["path"] == "/rest/servicedeskapi/request/SASW-2911"
    assert res.data["key"] == "SASW-2911"
    assert res.data["requestType"] == "Technical support"
    assert res.data["description"] == "Meter reads low"
```

- [ ] **Step 2: run → fails**

Run: `cd ecog-sd-mcp-server && rtk proxy python -m pytest tests/test_tools.py::test_sd_get_request_returns_detail -v`
Expected: FAIL — unknown tool `sd_get_request`.

- [ ] **Step 3: implement** — add inside `_register_tools`:
```python
    @mcp.tool
    async def sd_get_request(key: str) -> dict:
        """Full detail of one Ecog Service Desk ticket by key (e.g. 'SASW-2911'):
        summary, status, request type, reporter, dates, and description (HTML stripped).
        Read-only."""
        data = await _get_json(client, f"/rest/servicedeskapi/request/{key}")
        return _request_detail(data)
```

- [ ] **Step 4: run → passes**

Run: `cd ecog-sd-mcp-server && rtk proxy python -m pytest tests/test_tools.py -v`
Expected: PASS.

- [ ] **Step 5: commit**
```bash
git add ecog-sd-mcp-server/ecog_sd_mcp/server.py ecog-sd-mcp-server/tests/test_tools.py
git commit -m "feat(ecog-sd): sd_get_request tool"
```

---

## Task 8: `sd_get_request_comments` tool

**Files:**
- Modify: `ecog-sd-mcp-server/ecog_sd_mcp/server.py` (`_register_tools`)
- Test: append to `ecog-sd-mcp-server/tests/test_tools.py`

- [ ] **Step 1: failing test** (append):
```python
@pytest.mark.asyncio
async def test_sd_get_request_comments_public_and_stripped():
    cap = {}

    def handler(request: httpx.Request) -> httpx.Response:
        cap["path"] = request.url.path
        cap["public"] = request.url.params.get("public")
        return httpx.Response(200, json={"values": [
            {"author": {"displayName": "Asha"}, "created": {"jira": "2026-07-02T08:00:00.000+0000"},
             "body": "<p>Replaced the meter</p>"}]})

    mcp = build_server(client=_client(handler))
    async with Client(mcp) as c:
        res = await c.call_tool("sd_get_request_comments", {"key": "SASW-2911", "limit": 20})

    assert cap["path"] == "/rest/servicedeskapi/request/SASW-2911/comment"
    assert cap["public"] == "true"
    assert res.data == [{"author": "Asha", "created": "2026-07-02T08:00:00.000+0000",
                         "body": "Replaced the meter"}]
```

- [ ] **Step 2: run → fails**

Run: `cd ecog-sd-mcp-server && rtk proxy python -m pytest tests/test_tools.py::test_sd_get_request_comments_public_and_stripped -v`
Expected: FAIL — unknown tool `sd_get_request_comments`.

- [ ] **Step 3: implement** — add inside `_register_tools`:
```python
    @mcp.tool
    async def sd_get_request_comments(key: str, limit: int = 50) -> list[dict]:
        """Public comments on an Ecog Service Desk ticket by key: author, created, and body
        text (HTML stripped). Read-only."""
        data = await _get_json(client, f"/rest/servicedeskapi/request/{key}/comment",
                               params={"public": "true", "limit": limit})
        return [_comment(c) for c in data.get("values", [])]
```

- [ ] **Step 4: run → passes**

Run: `cd ecog-sd-mcp-server && rtk proxy python -m pytest tests/test_tools.py -v`
Expected: PASS.

- [ ] **Step 5: commit**
```bash
git add ecog-sd-mcp-server/ecog_sd_mcp/server.py ecog-sd-mcp-server/tests/test_tools.py
git commit -m "feat(ecog-sd): sd_get_request_comments tool"
```

---

## Task 9: `sd_list_servicedesks` tool

**Files:**
- Modify: `ecog-sd-mcp-server/ecog_sd_mcp/server.py` (`_register_tools`)
- Test: append to `ecog-sd-mcp-server/tests/test_tools.py`

- [ ] **Step 1: failing test** (append):
```python
@pytest.mark.asyncio
async def test_sd_list_servicedesks():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/servicedeskapi/servicedesk"
        return httpx.Response(200, json={"values": [
            {"id": "1", "projectKey": "SASW", "projectName": "Support"},
            {"id": "2", "projectKey": "HW", "projectName": "Hardware"}]})

    mcp = build_server(client=_client(handler))
    async with Client(mcp) as c:
        res = await c.call_tool("sd_list_servicedesks", {})

    assert res.data == [{"id": "1", "projectKey": "SASW", "projectName": "Support"},
                        {"id": "2", "projectKey": "HW", "projectName": "Hardware"}]
```

- [ ] **Step 2: run → fails**

Run: `cd ecog-sd-mcp-server && rtk proxy python -m pytest tests/test_tools.py::test_sd_list_servicedesks -v`
Expected: FAIL — unknown tool `sd_list_servicedesks`.

- [ ] **Step 3: implement** — add inside `_register_tools`:
```python
    @mcp.tool
    async def sd_list_servicedesks() -> list[dict]:
        """List the Ecog service desks you can access (id, projectKey, projectName).
        Read-only."""
        data = await _get_json(client, "/rest/servicedeskapi/servicedesk")
        return [{"id": d.get("id"), "projectKey": d.get("projectKey"),
                 "projectName": d.get("projectName")} for d in data.get("values", [])]
```

- [ ] **Step 4: run → passes**

Run: `cd ecog-sd-mcp-server && rtk proxy python -m pytest tests/test_tools.py -v`
Expected: PASS.

- [ ] **Step 5: commit**
```bash
git add ecog-sd-mcp-server/ecog_sd_mcp/server.py ecog-sd-mcp-server/tests/test_tools.py
git commit -m "feat(ecog-sd): sd_list_servicedesks tool"
```

---

## Task 10: Read-only guard test

**Files:**
- Test: append to `ecog-sd-mcp-server/tests/test_tools.py`

- [ ] **Step 1: guard test** (append):
```python
@pytest.mark.asyncio
async def test_only_read_tools_registered():
    def handler(request): return httpx.Response(200, json={"values": []})
    mcp = build_server(client=_client(handler))
    names = {t.name for t in await mcp.list_tools()}
    assert names == {"sd_list_requests", "sd_get_request",
                     "sd_get_request_comments", "sd_list_servicedesks"}
    forbidden = ("create", "update", "delete", "add", "edit", "post", "transition", "close")
    assert not [n for n in names if any(f in n for f in forbidden)]
```
> Note: FastMCP here exposes `await mcp.list_tools()` returning `list[Tool]` (not `get_tools()`).

- [ ] **Step 2: run → passes; then full suite**

Run: `cd ecog-sd-mcp-server && rtk proxy python -m pytest -v`
Expected: all PASS. If the tool set differs, fix the tool names — do not weaken the assertion.

- [ ] **Step 3: commit**
```bash
git add ecog-sd-mcp-server/tests/test_tools.py
git commit -m "test(ecog-sd): assert server is read-only (no write tools)"
```

---

## Task 11: Entrypoint, Dockerfile, README

**Files:**
- Create: `ecog-sd-mcp-server/ecog_sd_mcp/__main__.py`, `Dockerfile`, `README.md`

- [ ] **Step 1: `__main__.py`**
```python
"""Entry point: ``python -m ecog_sd_mcp`` runs the Ecog Service Desk MCP server."""

from __future__ import annotations

from .config import settings
from .server import build_server


def main() -> None:
    mcp = build_server()
    if settings.mcp_transport.lower() == "http":
        mcp.run(transport="http", host=settings.mcp_host, port=settings.mcp_port,
                path="/mcp", host_origin_protection=False)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: boot check (tool names)**

Run: `cd ecog-sd-mcp-server && rtk proxy python -c "import asyncio; from ecog_sd_mcp.server import build_server; print(sorted(t.name for t in asyncio.run(build_server().list_tools())))"`
Expected: `['sd_get_request', 'sd_get_request_comments', 'sd_list_requests', 'sd_list_servicedesks']`

- [ ] **Step 3: `Dockerfile`** (Playwright base image for Chromium):
```dockerfile
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ecog_sd_mcp ./ecog_sd_mcp

ENV MCP_TRANSPORT=http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8103 \
    ECOG_COOKIE_FILE=/data/ecog_cookies.json

EXPOSE 8103
VOLUME ["/data"]

# ECOG_EMAIL / ECOG_PASSWORD / ECOG_COOKIE provided at runtime (env / compose).
CMD ["python", "-m", "ecog_sd_mcp"]
```

- [ ] **Step 4: `README.md`**
```markdown
# ecog-sd-mcp-server — Ecog Jira Service Desk (read-only)

Self-hosted FastMCP server exposing **Ecog's Jira Service Management customer API**
(`/rest/servicedeskapi`) as read-only tools, authenticated with a **browser session cookie**.
Used because the account has customer/portal access, not a licensed Jira user seat (so the
platform REST API and API tokens are rejected).

## Tools
- `sd_list_requests(search, status, ownership, limit)` — list/search your tickets.
- `sd_get_request(key)` — one ticket's detail.
- `sd_get_request_comments(key, limit)` — public comments.
- `sd_list_servicedesks()` — the service desks you can access.

Read-only by construction (no write tool).

## Auth
Sends a session cookie; on logout (401/redirect/HTML) it refreshes **once** and retries.
Refresh = a **Playwright password login** (`login.py`) when `ECOG_EMAIL`/`ECOG_PASSWORD`
are set and `ECOG_LOGIN_ENABLED=true`; otherwise it errors asking for a fresh `ECOG_COOKIE`.
Requires **MFA off**. If login hits a CAPTCHA, run once with `ECOG_HEADLESS=false` or inject
a fresh `ECOG_COOKIE` (copy the `cookie:` request header from a logged-in browser).

## Configuration (env / `.env`)
| Var | Default | Notes |
|---|---|---|
| `ECOG_BASE_URL` | `https://ecog.atlassian.net` | |
| `ECOG_EMAIL` / `ECOG_PASSWORD` | — | for auto-login; **secret** |
| `ECOG_COOKIE` | — | optional seed/fallback Cookie header |
| `ECOG_COOKIE_FILE` | `/data/ecog_cookies.json` | refreshed-cookie cache |
| `ECOG_LOGIN_ENABLED` | `true` | false ⇒ cookie-only |
| `ECOG_HEADLESS` | `true` | set false to solve a one-off challenge |
| `MCP_TRANSPORT`/`MCP_HOST`/`MCP_PORT` | `stdio`/`127.0.0.1`/`8103` | keep internal |

## Test
`python -m pytest -v`  (fully offline; Playwright login is integration-verified separately)

> **Data note:** Ecog is a vendor; this reads vendor-confidential data via a customer
> session. Internal/dev use only until the residency decision (D1). Automating a vendor's
> portal carries ToS/relationship risk — see the spec.
```

- [ ] **Step 5: commit**
```bash
git add ecog-sd-mcp-server/ecog_sd_mcp/__main__.py ecog-sd-mcp-server/Dockerfile ecog-sd-mcp-server/README.md
git commit -m "feat(ecog-sd): entrypoint, Playwright Dockerfile, README"
```

---

## Task 12: CI matrix

**Files:**
- Modify: `.github/workflows/tests.yml:19`

- [ ] **Step 1: add to matrix** — change line 19:
```yaml
        server: [mcp-server, cms-mcp-server, jira-mcp-server]
```
to:
```yaml
        server: [mcp-server, cms-mcp-server, jira-mcp-server, ecog-sd-mcp-server]
```
> The tests are offline and never launch a browser (Playwright import is lazy, login is never called), so CI needs only the `playwright` pip package from `requirements.txt` — no `playwright install`.

- [ ] **Step 2: verify locally the way CI runs**

Run: `cd ecog-sd-mcp-server && python -m pip install -r requirements.txt pytest pytest-asyncio && rtk proxy python -m pytest -v`
Expected: all PASS.

- [ ] **Step 3: commit**
```bash
git add .github/workflows/tests.yml
git commit -m "ci(ecog-sd): add ecog-sd-mcp-server to the pytest matrix"
```

---

## Task 13: Deploy + register in LibreChat (config; live verify with user)

**Files:**
- Modify: `chat/docker-compose.yml`, `chat/librechat.yaml`, `chat/.env.example`

> Config wiring only. The live login/read (Playwright against Ecog) and scoping behind the
> `noc-engineer` agent are done with the user (needs real `ECOG_PASSWORD` / a fresh cookie).

- [ ] **Step 1: compose service** — after the `jira-mcp-server:` block in `chat/docker-compose.yml`, add:
```yaml
  # Read-only MCP for Ecog's Jira Service Desk (customer API, cookie/Playwright auth).
  ecog-sd-mcp-server:
    build: ../ecog-sd-mcp-server
    container_name: ecog-sd-mcp-server
    environment:
      - ECOG_BASE_URL=${ECOG_BASE_URL:-https://ecog.atlassian.net}
      - ECOG_EMAIL=${ECOG_EMAIL}
      - ECOG_PASSWORD=${ECOG_PASSWORD}
      - ECOG_COOKIE=${ECOG_COOKIE:-}
      - ECOG_HEADLESS=${ECOG_HEADLESS:-true}
      - MCP_TRANSPORT=http
      - MCP_HOST=0.0.0.0
      - MCP_PORT=8103
    volumes:
      - ecog-sd-cookies:/data
    restart: unless-stopped
```
And add `ecog-sd-cookies:` under the top-level `volumes:` key (where `librechat-images` etc. are declared).

- [ ] **Step 2: register in `librechat.yaml`** — add to `mcpSettings.allowedAddresses`:
```yaml
    - "ecog-sd-mcp-server:8103"
```
and to `mcpServers` (after the `jira:` entry):
```yaml
  ecog_sd:
    type: streamable-http
    url: "http://ecog-sd-mcp-server:8103/mcp"
    timeout: 60000
```

- [ ] **Step 3: `.env.example`** — add a documented (non-secret) `ECOG_*` block:
```
# Ecog Jira Service Desk MCP (cookie/Playwright auth; MFA must be off)
ECOG_BASE_URL=https://ecog.atlassian.net
ECOG_EMAIL=
ECOG_PASSWORD=
ECOG_COOKIE=
ECOG_HEADLESS=true
```
Verify no secret is committed:
```bash
git grep -nI "ECOG_PASSWORD" -- chat/ | grep -v ".env.example"
```
Expected: only `${ECOG_PASSWORD}` references.

- [ ] **Step 4: commit**
```bash
git add chat/docker-compose.yml chat/librechat.yaml chat/.env.example
git commit -m "feat(ecog-sd): register Ecog Service Desk MCP in compose + librechat (port 8103)"
```

- [ ] **Step 5: (with the user) live verification** — with real `ECOG_EMAIL`/`ECOG_PASSWORD` (MFA off) in `chat/.env`:
```bash
cd chat && docker compose up -d --build ecog-sd-mcp-server
docker compose logs ecog-sd-mcp-server
```
Then add the `ecog_sd` tools to the `noc-engineer` agent and ask *"list my Ecog service desk tickets about undervoltage."* Confirm real tickets return. If login hits a CAPTCHA, set `ECOG_HEADLESS=false` for a one-off run or inject a fresh `ECOG_COOKIE`.

---

## Self-Review Notes (for the implementer)

- **Spec coverage:** package/config (T1) · cookie helpers (T2) · refresh-on-logout + single-login concurrency (T3) · Playwright login + CAPTCHA guard (T4) · trim/HTML helpers + factories (T5) · 4 `sd_*` tools (T6–T9) · read-only guard (T10) · entrypoint/Playwright Docker/README (T11) · CI (T12) · compose+librechat+secrets wiring, noc-engineer scoping, D1 note (T13, README).
- **FastMCP API:** uses `await mcp.list_tools()` (list of Tool) and `res.data` for tool results — consistent with the sibling `jira-mcp-server`. If a FastMCP version differs, adjust consistently and report.
- **Playwright not unit-tested live:** only `_is_captcha` and the refresh wiring (via injected fake `refresh`) are covered. The real login is a T13 smoke check — this is intentional (external/flaky), and the `_looks_logged_out`→refresh→retry contract that *uses* it is fully covered in T3.
- **Selectors caveat:** `#username` / `#password` / `#login-submit` are current-as-of 2026-07; verify against the live Atlassian login during T13 and adjust `login.py` if changed (logic unchanged).
```
