"""Read-only MCP tools over ChargeZone's Atlassian Cloud Jira (REST v3), scoped to QCH.

Descriptions and comments come back as Atlassian Document Format (ADF) JSON; we flatten
them to plain text and trim each issue/comment to a small whitelist so payloads stay
LLM-friendly. There is deliberately NO write tool — the server is read-only by construction,
and every tool is locked to the QCH (Quench) project.
"""

from __future__ import annotations

import csv
import io
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from fastmcp import FastMCP

from .auth import JiraAuthClient
from .config import settings

# ── Pure helpers ─────────────────────────────────────────────────────────────────────────

_BLOCK_TYPES = {"paragraph", "heading", "listItem", "blockquote", "codeBlock"}
_MAX_ADF_DEPTH = 200


def _adf_to_text(node: Any, _depth: int = 0) -> str:
    """Flatten an Atlassian Document Format node tree to plain text."""
    if node is None or _depth > _MAX_ADF_DEPTH:
        return ""
    if isinstance(node, list):
        return "".join(_adf_to_text(n, _depth + 1) for n in node)
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
    text = _adf_to_text(node.get("content"), _depth + 1)
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
        "priority_level": (_PLEVEL_MAP.get((issue.get("fields", {}).get(_PRIORITY_LEVEL_FIELD) or {}).get("value"))
                           if isinstance(issue.get("fields", {}).get(_PRIORITY_LEVEL_FIELD), dict) else None),
        "ocpp_id": _field_str(issue.get("fields", {}) or {}, _OCPP_ID_FIELD),
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
    if not resp.text.strip():
        return {}
    try:
        return resp.json()
    except ValueError as exc:  # includes json.JSONDecodeError
        raise RuntimeError(
            f"Jira returned a non-JSON response for {path} (status {resp.status_code}); "
            "the backend may be temporarily unavailable."
        ) from exc


def _parse_csv(text: str) -> tuple[list[str], list[list[str]]]:
    """Parse CSV text into (headers, rows). Empty text -> ([], []). Strips a leading UTF-8 BOM."""
    text = text.lstrip("﻿")
    if not text.strip():
        return [], []
    reader = list(csv.reader(io.StringIO(text)))
    if not reader:
        return [], []
    return reader[0], [r for r in reader[1:]]


# ── Scope guards (QCH-only) ──────────────────────────────────────────────────────────────

_KEY_RE = re.compile(rf"^{re.escape(settings.project_key)}-[0-9]+$")

_PRIORITY_LEVEL_FIELD = "customfield_10120"   # QCH "Priority Level" (P1/P2/P3)
_OCPP_ID_FIELD = "customfield_10110"           # QCH "OCPP ID" (charger id)
_CATEGORY_FIELD = "customfield_10156"          # QCH "Category"
_HW_FAIL_FIELD = "customfield_10119"           # QCH "Hardware Failure Description"
_PLEVEL_MAP = {"Priority Level - 01": "P1", "Priority Level - 02": "P2", "Priority Level - 03": "P3"}

_ORDER_FIELD = r'(?:"[^"]+"|cf\[[0-9]+\]|[A-Za-z][A-Za-z0-9_]*)'
_ORDER_RE = re.compile(
    rf'^{_ORDER_FIELD}(?:\s+(?:ASC|DESC))?(?:\s*,\s*{_ORDER_FIELD}(?:\s+(?:ASC|DESC))?)*$',
    re.IGNORECASE,
)


def _validate_order_by(order_by: str) -> str:
    """Return a safe 'ORDER BY ...' clause. Empty -> default. Whitelist: comma-separated
    <field> [ASC|DESC], where field is an identifier, cf[NNN], or a quoted name. Rejects
    anything else (parens, operators, semicolons) so it can't widen scope."""
    ob = (order_by or "").strip()
    if not ob:
        return "ORDER BY updated DESC"
    ob = re.sub(r'^order\s+by\s+', '', ob, flags=re.IGNORECASE).strip()
    if not _ORDER_RE.match(ob):
        raise ValueError(
            "Rejected order_by: use comma-separated '<field> [ASC|DESC]', e.g. "
            "'cf[10120] ASC, assignee ASC'."
        )
    return f"ORDER BY {ob}"


def _plevel(fields: dict) -> str:
    """Map the QCH Priority Level field value to P1/P2/P3, or 'Unassigned'."""
    v = fields.get(_PRIORITY_LEVEL_FIELD)
    val = v.get("value") if isinstance(v, dict) else v
    return _PLEVEL_MAP.get(val, "Unassigned")


def _field_str(fields: dict, fid: str):
    """Read a custom field as a string: select fields -> .value/.name; else the raw value."""
    v = fields.get(fid)
    if isinstance(v, dict):
        return v.get("value") or v.get("name")
    return v


def _parens_balanced(s: str) -> bool:
    """True iff parentheses in s are balanced, ignoring parens inside quoted JQL string
    literals. Depth must never go negative and must return to 0 (and no unterminated quote)."""
    depth = 0
    quote = None       # active string delimiter, or None
    escaped = False
    for ch in s:
        if quote is not None:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in ('"', "'"):
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and quote is None


def _contains_order_by(s: str) -> bool:
    """True iff an 'order by' keyword appears OUTSIDE any quoted string literal."""
    quote = None
    escaped = False
    lowered = s.lower()
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if quote is not None:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            i += 1
            continue
        if ch in ('"', "'"):
            quote = ch
            i += 1
            continue
        if lowered.startswith("order", i) and re.match(r"order\s+by\b", lowered[i:]):
            return True
        i += 1
    return False


def require_qch(key: str) -> str:
    """Return the trimmed key iff it is a QCH issue key; otherwise raise. Runs before any call."""
    k = key.strip() if isinstance(key, str) else key
    if not isinstance(k, str) or not _KEY_RE.match(k):
        raise ValueError(
            f"Only {settings.project_key} (Quench) issues are accessible through this server; "
            f"got {key!r}."
        )
    return k


def build_scoped_jql(conditions: str, order_by: str = "") -> str:
    """Return a project-locked JQL query. `conditions` (WHERE only, no ORDER BY) are wrapped in
    `project = "<KEY>" AND (...)`; ordering comes ONLY from the validated `order_by` (default
    most-recently-updated). Structurally impossible for a clause to escape the project filter."""
    proj = f'project = "{settings.project_key}"'
    order = _validate_order_by(order_by)
    conditions = (conditions or "").strip()
    if not conditions:
        return f"{proj} {order}"
    if _contains_order_by(conditions):
        raise ValueError(
            "Rejected search conditions: remove ORDER BY from conditions; use the order_by "
            "parameter instead. Pass only filter conditions."
        )
    if not _parens_balanced(conditions):
        raise ValueError(
            "Rejected search conditions: unbalanced parentheses. Pass a self-contained JQL "
            "filter fragment."
        )
    return f"{proj} AND ({conditions}) {order}"


_STATS_CAP = 2000
_STATS_PAGE = 100
assert _STATS_CAP % _STATS_PAGE == 0  # truncation math assumes the final page can't cross the cap
_STATS_FIELDS = "status,issuetype,priority,assignee,created,customfield_10120"


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

    _SEARCH_FIELDS = (
        f"summary,status,issuetype,priority,assignee,updated,project,"
        f"{_PRIORITY_LEVEL_FIELD},{_OCPP_ID_FIELD}"
    )

    @mcp.tool
    async def cz_jira_search(conditions: str = "", limit: int = 50, order_by: str = "") -> list[dict]:
        """Search Quench (QCH) Jira issues. Pass JQL filter *conditions* only — the part AFTER
        the project filter — e.g. 'status = "To Do"' or 'text ~ "offline"'. Do NOT include
        ORDER BY in conditions — use the separate `order_by` param instead (e.g.
        'cf[10120] ASC, assignee ASC'); default is most-recently-updated. The server always
        scopes the query to project = QCH; you cannot query any other project. Empty
        conditions returns the most recently updated QCH issues. Returns a compact list: key,
        summary, status, type, priority, assignee, updated, project, priority_level (P1/P2/P3
        from the Priority Level field), ocpp_id. Read-only. Single page only (Jira caps
        maxResults ~100)."""
        jql = build_scoped_jql(conditions, order_by)
        data = await _get_json(client, "/rest/api/3/search/jql", params={
            "jql": jql, "maxResults": limit, "fields": _SEARCH_FIELDS,
        })
        return [_issue_summary(i) for i in data.get("issues", [])]

    _DETAIL_FIELDS = (
        "summary,status,issuetype,priority,assignee,reporter,created,updated,"
        f"labels,components,description,{_PRIORITY_LEVEL_FIELD},{_OCPP_ID_FIELD}"
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

    @mcp.tool
    async def cz_jira_export(conditions: str = "", all_fields: bool = False,
                             limit: int = 1000) -> dict:
        """Export Quench (QCH) Jira issues as tabular data for download. Pass JQL filter
        *conditions* only (no ORDER BY; always QCH-scoped), same as cz_jira_search. Returns
        {headers, rows, row_count, truncated}. all_fields=False -> 14 tidy columns (key,
        summary, assignee, status, priority, dates...); all_fields=True -> every field.
        To give the user a downloadable file, pass the returned headers+rows to the docgen
        `create_excel` tool and present its download link. Read-only. `limit` caps rows
        (default 1000); truncated=true means more matched than were returned."""
        limit = max(1, min(int(limit), 5000))
        jql = build_scoped_jql(conditions)
        variant = "searchrequest-csv-all-fields" if all_fields else "searchrequest-csv-current-fields"
        path = f"/sr/jira.issueviews:{variant}/temp/SearchRequest.csv"
        resp = await client.get(path, params={"jqlQuery": jql, "tempMax": limit})
        if resp.status_code in (401, 403):
            raise RuntimeError(
                f"Jira auth failed ({resp.status_code}) — check CZ_JIRA_EMAIL / CZ_JIRA_API_TOKEN."
            )
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        if "csv" not in ctype.lower():
            raise RuntimeError(
                f"Jira CSV export failed — expected text/csv, got {ctype!r} "
                "(likely an auth redirect to a login page)."
            )
        headers, rows = _parse_csv(resp.text)
        return {
            "headers": headers,
            "rows": rows,
            "row_count": len(rows),
            "truncated": len(rows) >= limit,
        }

    @mcp.tool
    async def cz_jira_stats(conditions: str = "") -> dict:
        """Analytics summary of Quench (QCH) tickets. Pass JQL filter *conditions* only (no
        ORDER BY; always QCH-scoped), or empty for the whole project. Returns totals and
        breakdowns: {total, sampled, truncated, by_status, by_type, by_priority,
        top_assignees (top 10), created_last_7d, created_last_30d}. Read-only. Paginates up
        to 2000 issues; beyond that total is ">=2000" and truncated=true."""
        jql = build_scoped_jql(conditions)
        issues: list[dict] = []
        token = None
        while len(issues) < _STATS_CAP:
            params = {"jql": jql, "maxResults": _STATS_PAGE, "fields": _STATS_FIELDS}
            if token:
                params["nextPageToken"] = token
            data = await _get_json(client, "/rest/api/3/search/jql", params=params)
            batch = data.get("issues", [])
            issues.extend(batch)
            token = data.get("nextPageToken")
            if not token or not batch:
                break
        truncated = len(issues) >= _STATS_CAP and bool(token)
        issues = issues[:_STATS_CAP]

        now = datetime.now(timezone.utc)

        def _created(i: dict):
            c = (i.get("fields") or {}).get("created")
            if not c:
                return None
            try:
                return datetime.strptime(c, "%Y-%m-%dT%H:%M:%S.%f%z")
            except (ValueError, TypeError):
                return None

        by_status: Counter = Counter()
        by_type: Counter = Counter()
        by_priority: Counter = Counter()
        by_plevel: Counter = Counter()
        by_assignee: Counter = Counter()
        c7 = c30 = 0
        for i in issues:
            f = i.get("fields", {}) or {}
            by_status[(f.get("status") or {}).get("name") or "Unknown"] += 1
            by_type[(f.get("issuetype") or {}).get("name") or "Unknown"] += 1
            by_priority[(f.get("priority") or {}).get("name") or "None"] += 1
            by_plevel[_plevel(f)] += 1
            by_assignee[(f.get("assignee") or {}).get("displayName") or "Unassigned"] += 1
            dt = _created(i)
            if dt:
                age = (now - dt).days
                if age <= 7:
                    c7 += 1
                if age <= 30:
                    c30 += 1
        return {
            "total": (f">={_STATS_CAP}" if truncated else len(issues)),
            "sampled": len(issues),
            "truncated": truncated,
            "by_status": dict(by_status.most_common()),
            "by_type": dict(by_type.most_common()),
            "by_priority": dict(by_priority.most_common()),
            "by_priority_level": {k: by_plevel[k] for k in ("P1", "P2", "P3", "Unassigned") if by_plevel.get(k)},
            "top_assignees": dict(by_assignee.most_common(10)),
            "created_last_7d": c7,
            "created_last_30d": c30,
        }

    @mcp.tool
    async def cz_jira_project_meta() -> dict:
        """Reference metadata for the Quench (QCH) project: {key, name, statuses, issue_types,
        priorities}. Use it to build valid `conditions` for cz_jira_search / cz_jira_stats /
        cz_jira_export. Read-only, no parameters."""
        proj = await _get_json(client, f"/rest/api/3/project/{settings.project_key}")
        status_groups = await _get_json(
            client, f"/rest/api/3/project/{settings.project_key}/statuses")
        priorities = await _get_json(client, "/rest/api/3/priority")

        issue_types = [t.get("name") for t in (proj.get("issueTypes") or []) if t.get("name")]
        statuses: list[str] = []
        seen = set()
        for grp in (status_groups or []):
            for s in grp.get("statuses", []):
                n = s.get("name")
                if n and n not in seen:
                    seen.add(n)
                    statuses.append(n)
        prio_names = [p.get("name") for p in (priorities or []) if p.get("name")]
        return _drop_empty({
            "key": settings.project_key,
            "name": proj.get("name"),
            "statuses": statuses,
            "issue_types": issue_types,
            "priorities": prio_names,
        })

    @mcp.tool
    async def cz_jira_list_dashboards() -> dict:
        """List Jira dashboards and the saved filters that reference Quench (QCH). Returns
        {dashboards:[{id,name}], quench_filters:[{id,name,jql}]}. Dashboard chart data is not
        API-exposed — this is a navigation aid: open a dashboard by id in Jira, or reuse a
        filter's JQL via cz_jira_search. Read-only, no parameters."""
        dash = await _get_json(client, "/rest/api/3/dashboard", params={"maxResults": 50})
        dashboards = [
            {"id": d.get("id"), "name": d.get("name")} for d in dash.get("dashboards", [])
        ]
        filt = await _get_json(
            client, "/rest/api/3/filter/search", params={"maxResults": 100, "expand": "jql"})
        key = settings.project_key.lower()
        quench_filters = []
        for f in filt.get("values", []):
            name = f.get("name") or ""
            jql = f.get("jql") or ""
            hay = (name + " " + jql).lower()
            if key in hay or "quench" in hay:
                quench_filters.append({"id": f.get("id"), "name": name, "jql": jql})
        return {"dashboards": dashboards, "quench_filters": quench_filters}
