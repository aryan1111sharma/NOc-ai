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

    @mcp.tool
    async def sd_get_request(key: str) -> dict:
        """Full detail of one Ecog Service Desk ticket by key (e.g. 'SASW-2911'):
        summary, status, request type, reporter, dates, and description (HTML stripped).
        Read-only."""
        data = await _get_json(client, f"/rest/servicedeskapi/request/{key}")
        return _request_detail(data)
