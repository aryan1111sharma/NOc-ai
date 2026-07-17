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
