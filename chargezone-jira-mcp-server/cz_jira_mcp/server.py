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
