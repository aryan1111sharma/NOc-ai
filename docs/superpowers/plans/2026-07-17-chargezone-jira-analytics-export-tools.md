# ChargeZone Jira Analytics & Export Tools — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add 4 read-only, QCH-locked tools to `chargezone-jira-mcp-server` — `cz_jira_export` (CSV→structured, delivered as .xlsx via docgen), `cz_jira_stats`, `cz_jira_project_meta`, `cz_jira_list_dashboards`.

**Architecture:** Extend `cz_jira_mcp/server.py` `_register_tools`, reusing existing `build_scoped_jql`/`require_qch`/`_get_json`. Stats paginates GET search (cap 2000) to keep the GET-only guard. Export parses Jira's CSV-export endpoint. No new files, no new server/port.

**Tech Stack:** Python 3.12, FastMCP, httpx, pytest. `csv`, `io`, `datetime`, `collections.Counter` (stdlib).

**Spec:** `docs/superpowers/specs/2026-07-17-chargezone-jira-analytics-export-tools.md`

**Test run convention:** cwd = `chargezone-jira-mcp-server/`, interpreter `E:\cjain\noc-mcp-agent\.venv\Scripts\python.exe -m pytest`.

---

## File Structure
- Modify: `chargezone-jira-mcp-server/cz_jira_mcp/server.py` (add imports, a CSV helper, 4 tools)
- Create: `chargezone-jira-mcp-server/tests/test_analytics.py`
- Modify: `chargezone-jira-mcp-server/tests/test_tools.py` (registration set → 8 tools)
- Modify: `chargezone-jira-mcp-server/README.md` (document 4 new tools)

---

## Task 1: `cz_jira_export` + CSV helper (TDD)

**Files:**
- Modify: `cz_jira_mcp/server.py`
- Create: `tests/test_analytics.py`

- [ ] **Step 1: Write failing test** — create `tests/test_analytics.py`

```python
"""Analytics/export tools: export, stats, project_meta, list_dashboards (mocked Jira)."""

import httpx
import pytest
from fastmcp import Client

from cz_jira_mcp.server import build_server, _parse_csv


def test_parse_csv_splits_headers_and_rows():
    text = "Issue key,Summary,Status\r\nQCH-1,Charger offline,To Do\r\nQCH-2,Gun A fault,QA\r\n"
    headers, rows = _parse_csv(text)
    assert headers == ["Issue key", "Summary", "Status"]
    assert rows == [["QCH-1", "Charger offline", "To Do"], ["QCH-2", "Gun A fault", "QA"]]


def test_parse_csv_empty():
    assert _parse_csv("") == ([], [])


@pytest.mark.asyncio
async def test_cz_jira_export_scopes_jql_and_parses(mock_client):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["jql"] = request.url.params.get("jqlQuery")
        captured["tempMax"] = request.url.params.get("tempMax")
        csv = "Issue key,Summary,Status\r\nQCH-1,Offline,To Do\r\n"
        return httpx.Response(200, text=csv, headers={"content-type": "text/csv;charset=UTF-8"})

    mcp = build_server(client=mock_client(handler))
    async with Client(mcp) as c:
        res = await c.call_tool("cz_jira_export", {"conditions": 'status = "To Do"', "limit": 500})

    assert captured["path"] == "/sr/jira.issueviews:searchrequest-csv-current-fields/temp/SearchRequest.csv"
    assert captured["jql"] == 'project = "QCH" AND (status = "To Do") ORDER BY updated DESC'
    assert captured["tempMax"] == "500"
    assert res.data["headers"] == ["Issue key", "Summary", "Status"]
    assert res.data["rows"] == [["QCH-1", "Offline", "To Do"]]
    assert res.data["row_count"] == 1
    assert res.data["truncated"] is False


@pytest.mark.asyncio
async def test_cz_jira_export_all_fields_variant_and_truncation(mock_client):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(200, text="A,B\r\n1,2\r\n3,4\r\n",
                              headers={"content-type": "text/csv"})

    mcp = build_server(client=mock_client(handler))
    async with Client(mcp) as c:
        res = await c.call_tool("cz_jira_export", {"all_fields": True, "limit": 2})

    assert captured["path"] == "/sr/jira.issueviews:searchrequest-csv-all-fields/temp/SearchRequest.csv"
    assert res.data["row_count"] == 2
    assert res.data["truncated"] is True   # row_count >= limit


@pytest.mark.asyncio
async def test_cz_jira_export_non_csv_raises(mock_client):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>login</html>", headers={"content-type": "text/html"})

    mcp = build_server(client=mock_client(handler))
    async with Client(mcp) as c:
        with pytest.raises(Exception, match="CSV export"):
            await c.call_tool("cz_jira_export", {"conditions": ""})
```

- [ ] **Step 2: Run — expect failure**

Run: `python -m pytest tests/test_analytics.py -v`
Expected: FAIL — `ImportError: cannot import name '_parse_csv'`.

- [ ] **Step 3: Implement** — in `cz_jira_mcp/server.py`:

(a) Extend imports at top (after `import re`):
```python
import csv
import io
from collections import Counter
from datetime import datetime, timezone
```

(b) Add the CSV helper after `_get_json` (before the scope-guards section):
```python
def _parse_csv(text: str) -> tuple[list[str], list[list[str]]]:
    """Parse CSV text into (headers, rows). Empty text -> ([], [])."""
    if not text.strip():
        return [], []
    reader = list(csv.reader(io.StringIO(text)))
    if not reader:
        return [], []
    return reader[0], [r for r in reader[1:]]
```

(c) Add the tool inside `_register_tools` (after `cz_jira_get_issue_transitions`):
```python
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
```

- [ ] **Step 4: Run — expect PASS**

Run: `python -m pytest tests/test_analytics.py -v`
Expected: PASS (export + `_parse_csv` tests).

- [ ] **Step 5: Commit**

```bash
git add chargezone-jira-mcp-server/cz_jira_mcp/server.py chargezone-jira-mcp-server/tests/test_analytics.py
git commit -m "feat(cz-jira): cz_jira_export (QCH CSV export -> structured rows)"
```

---

## Task 2: `cz_jira_stats` (TDD)

**Files:**
- Modify: `cz_jira_mcp/server.py`
- Modify: `tests/test_analytics.py`

- [ ] **Step 1: Add failing tests** to `tests/test_analytics.py`:

```python
def _iso(dt):
    # Jira REST v3 created format, e.g. 2026-07-10T10:00:00.000+0000
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000%z").replace("+0000", "+0000")


@pytest.mark.asyncio
async def test_cz_jira_stats_tallies_and_paginates(mock_client):
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)

    def mk(key, status, itype, prio, assignee, days_ago):
        return {"key": key, "fields": {
            "status": {"name": status}, "issuetype": {"name": itype},
            "priority": {"name": prio},
            "assignee": ({"displayName": assignee} if assignee else None),
            "created": (now - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.000%z")}}

    page1 = {"issues": [
        mk("QCH-1", "To Do", "Task", "Medium", "Quench Support", 2),
        mk("QCH-2", "QA", "Bug", "Medium", "Quench Support", 10),
    ], "nextPageToken": "tok2"}
    page2 = {"issues": [
        mk("QCH-3", "To Do", "Task", "High", None, 40),
    ]}  # no nextPageToken -> last page

    calls = {"n": 0}
    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert request.url.path == "/rest/api/3/search/jql"
        assert request.url.params.get("jql") == 'project = "QCH" ORDER BY updated DESC'
        return httpx.Response(200, json=(page1 if request.url.params.get("nextPageToken") is None else page2))

    mcp = build_server(client=mock_client(handler))
    async with Client(mcp) as c:
        res = await c.call_tool("cz_jira_stats", {"conditions": ""})

    d = res.data
    assert calls["n"] == 2                       # followed nextPageToken
    assert d["total"] == 3 and d["sampled"] == 3 and d["truncated"] is False
    assert d["by_status"] == {"To Do": 2, "QA": 1}
    assert d["by_type"] == {"Task": 2, "Bug": 1}
    assert d["top_assignees"] == {"Quench Support": 2, "Unassigned": 1}
    assert d["created_last_7d"] == 1             # only QCH-1 (2 days)
    assert d["created_last_30d"] == 2            # QCH-1 (2d) + QCH-2 (10d)
```

- [ ] **Step 2: Run — expect failure**

Run: `python -m pytest tests/test_analytics.py::test_cz_jira_stats_tallies_and_paginates -v`
Expected: FAIL — unknown tool `cz_jira_stats`.

- [ ] **Step 3: Implement** — add module constants after `_SEARCH_FIELDS` is defined is not possible (that's inside the function); instead add near the top-level scope guards:
```python
_STATS_CAP = 2000
_STATS_PAGE = 100
_STATS_FIELDS = "status,issuetype,priority,assignee,created"
```
Then add the tool inside `_register_tools` (after `cz_jira_export`):
```python
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
            except ValueError:
                return None

        by_status: Counter = Counter()
        by_type: Counter = Counter()
        by_priority: Counter = Counter()
        by_assignee: Counter = Counter()
        c7 = c30 = 0
        for i in issues:
            f = i.get("fields", {}) or {}
            by_status[(f.get("status") or {}).get("name") or "Unknown"] += 1
            by_type[(f.get("issuetype") or {}).get("name") or "Unknown"] += 1
            by_priority[(f.get("priority") or {}).get("name") or "None"] += 1
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
            "top_assignees": dict(by_assignee.most_common(10)),
            "created_last_7d": c7,
            "created_last_30d": c30,
        }
```

- [ ] **Step 4: Run — expect PASS**

Run: `python -m pytest tests/test_analytics.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add chargezone-jira-mcp-server/cz_jira_mcp/server.py chargezone-jira-mcp-server/tests/test_analytics.py
git commit -m "feat(cz-jira): cz_jira_stats (GET-only paginated QCH analytics, cap 2000)"
```

---

## Task 3: `cz_jira_project_meta` (TDD)

**Files:** Modify `cz_jira_mcp/server.py`, `tests/test_analytics.py`

- [ ] **Step 1: Add failing test** to `tests/test_analytics.py`:

```python
@pytest.mark.asyncio
async def test_cz_jira_project_meta_flattens(mock_client):
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p == "/rest/api/3/project/QCH":
            return httpx.Response(200, json={"key": "QCH", "name": "Quench",
                "issueTypes": [{"name": "Task"}, {"name": "Bug"}, {"name": "Epic"}]})
        if p == "/rest/api/3/project/QCH/statuses":
            return httpx.Response(200, json=[
                {"name": "Task", "statuses": [{"name": "To Do"}, {"name": "Done"}]},
                {"name": "Bug", "statuses": [{"name": "To Do"}, {"name": "QA"}]}])
        if p == "/rest/api/3/priority":
            return httpx.Response(200, json=[{"name": "High"}, {"name": "Medium"}, {"name": "Low"}])
        return httpx.Response(404, json={})

    mcp = build_server(client=mock_client(handler))
    async with Client(mcp) as c:
        res = await c.call_tool("cz_jira_project_meta", {})

    d = res.data
    assert d["key"] == "QCH" and d["name"] == "Quench"
    assert d["issue_types"] == ["Task", "Bug", "Epic"]
    assert d["statuses"] == ["To Do", "Done", "QA"]        # unique, first-seen order
    assert d["priorities"] == ["High", "Medium", "Low"]
```

- [ ] **Step 2: Run — expect failure** (`unknown tool cz_jira_project_meta`).

Run: `python -m pytest tests/test_analytics.py::test_cz_jira_project_meta_flattens -v`

- [ ] **Step 3: Implement** — add tool inside `_register_tools`:
```python
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
```

- [ ] **Step 4: Run — expect PASS**

Run: `python -m pytest tests/test_analytics.py -v`

- [ ] **Step 5: Commit**

```bash
git add chargezone-jira-mcp-server/cz_jira_mcp/server.py chargezone-jira-mcp-server/tests/test_analytics.py
git commit -m "feat(cz-jira): cz_jira_project_meta (QCH statuses/types/priorities)"
```

---

## Task 4: `cz_jira_list_dashboards` (TDD)

**Files:** Modify `cz_jira_mcp/server.py`, `tests/test_analytics.py`

- [ ] **Step 1: Add failing test** to `tests/test_analytics.py`:

```python
@pytest.mark.asyncio
async def test_cz_jira_list_dashboards_filters_to_qch(mock_client):
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p == "/rest/api/3/dashboard":
            return httpx.Response(200, json={"dashboards": [
                {"id": "10189", "name": "Quench"}, {"id": "10000", "name": "Default dashboard"}]})
        if p == "/rest/api/3/filter/search":
            return httpx.Response(200, json={"values": [
                {"id": "1", "name": "Filter for QCH board", "jql": "project = QCH ORDER BY Rank ASC"},
                {"id": "2", "name": "Quench R&D open", "jql": "status = Open"},
                {"id": "3", "name": "CDB - Blocked", "jql": "project = CDB"}]})
        return httpx.Response(404, json={})

    mcp = build_server(client=mock_client(handler))
    async with Client(mcp) as c:
        res = await c.call_tool("cz_jira_list_dashboards", {})

    d = res.data
    assert {x["id"] for x in d["dashboards"]} == {"10189", "10000"}
    got = {f["id"] for f in d["quench_filters"]}
    assert got == {"1", "2"}          # QCH in jql, "Quench" in name; CDB excluded
```

- [ ] **Step 2: Run — expect failure** (`unknown tool cz_jira_list_dashboards`).

Run: `python -m pytest tests/test_analytics.py::test_cz_jira_list_dashboards_filters_to_qch -v`

- [ ] **Step 3: Implement** — add tool inside `_register_tools`:
```python
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
```

- [ ] **Step 4: Run — expect PASS**

Run: `python -m pytest tests/test_analytics.py -v`

- [ ] **Step 5: Commit**

```bash
git add chargezone-jira-mcp-server/cz_jira_mcp/server.py chargezone-jira-mcp-server/tests/test_analytics.py
git commit -m "feat(cz-jira): cz_jira_list_dashboards (dashboards + QCH filters)"
```

---

## Task 5: Registration test + README + full suite

**Files:** Modify `tests/test_tools.py`, `README.md`

- [ ] **Step 1: Update the registration test** in `tests/test_tools.py` — replace the `names ==` set in `test_server_registers_only_read_tools` with all 8 tools:
```python
    assert names == {
        "cz_jira_search", "cz_jira_get_issue", "cz_jira_get_comments",
        "cz_jira_get_issue_transitions", "cz_jira_export", "cz_jira_stats",
        "cz_jira_project_meta", "cz_jira_list_dashboards",
    }
```
(Leave the `forbidden` write-verb assertion as-is — note "export" contains no forbidden substring; verify the forbidden list still passes: none of create/update/delete/add/edit/post/assign/move appear in the 8 names.)

- [ ] **Step 2: Run the FULL suite — expect PASS**

Run: `python -m pytest -v`
Expected: PASS — all existing tests + `test_analytics.py` (all 4 tools). Report the total count.

- [ ] **Step 3: Update `README.md`** — add the 4 tools under the Tools list:
```markdown
- `cz_jira_export(conditions="", all_fields=False, limit=1000)` — export QCH issues as
  {headers, rows, row_count, truncated}; pass to docgen `create_excel` for a download link.
- `cz_jira_stats(conditions="")` — QCH analytics: totals + by status/type/priority/assignee +
  created last 7/30 days (GET-only, samples up to 2000 issues).
- `cz_jira_project_meta()` — QCH statuses, issue types, priorities.
- `cz_jira_list_dashboards()` — dashboards + saved filters referencing QCH/Quench.
```

- [ ] **Step 4: Commit**

```bash
git add chargezone-jira-mcp-server/tests/test_tools.py chargezone-jira-mcp-server/README.md
git commit -m "test(cz-jira): register 8 tools; document analytics/export in README"
```

---

## Task 6: Deploy, wire agent, release (controller-run, in-session)

> Run by the controller (not a subagent): rebuilds the live container, edits the live agents DB, restarts LibreChat, releases. Uses PowerShell for docker/mongo path safety (see prior deploy).

- [ ] **Step 1: Rebuild + recreate the container**

`docker compose --project-directory <repo>/chat -f <repo>/chat/docker-compose.yml up -d --build chargezone-jira-mcp-server`
Expected: image rebuilt, container started, 0 errors in `docker logs chargezone-jira-mcp-server`.

- [ ] **Step 2: Live smoke test** (real QCH)

```
docker exec chargezone-jira-mcp-server python -c "
import asyncio, json
from cz_jira_mcp.server import build_server
from fastmcp import Client
async def main():
    async with Client(build_server()) as c:
        s = await c.call_tool('cz_jira_stats', {'conditions': ''}); print('stats total:', s.data['total'], 'by_status:', s.data['by_status'])
        e = await c.call_tool('cz_jira_export', {'conditions': 'status = \"To Do\"', 'limit': 5}); print('export cols:', e.data['headers'][:4], 'rows:', e.data['row_count'])
        m = await c.call_tool('cz_jira_project_meta', {}); print('statuses:', m.data['statuses'])
        d = await c.call_tool('cz_jira_list_dashboards', {}); print('dashboards:', len(d.data['dashboards']), 'qch_filters:', len(d.data['quench_filters']))
asyncio.run(main())
"
```
Expected: real stats distribution, export headers + row count, QCH statuses, dashboard/filter counts.

- [ ] **Step 3: Wire the NOC Engineer agent** — export live DB, add tool IDs, restore.
  - `& <repo>\chat\agents\export-agents.ps1`
  - Patch `agents.json` (JSON script targeting top-level `"NOC Engineer"`): add
    `cz_jira_export_mcp_chargezone`, `cz_jira_stats_mcp_chargezone`,
    `cz_jira_project_meta_mcp_chargezone`, `cz_jira_list_dashboards_mcp_chargezone`; and, if not
    already present on that agent, add `create_excel_mcp_docgen` (so the export→download chain
    works).
  - Optionally append one instruction sentence: "For Quench/QCH exports call cz_jira_export then
    docgen create_excel and return the download link; for QCH ticket analytics use cz_jira_stats;
    use cz_jira_project_meta to build valid filters."
  - Restore byte-safe (PowerShell, NOT Git Bash — MSYS mangles `/tmp` paths):
    `docker cp agents.json noc-librechat-mongo:/tmp/agents.restore.json; docker cp _restore.js noc-librechat-mongo:/tmp/_restore.js; docker exec noc-librechat-mongo mongosh --quiet LibreChat --file /tmp/_restore.js`

- [ ] **Step 4: Restart LibreChat + verify**

`docker restart noc-librechat`; poll `docker logs --since 2m noc-librechat | grep '[MCP][chargezone] Tools:'` until it lists all 8 cz_jira tools; confirm "configured servers" total and no chargezone init error.

- [ ] **Step 5: Version bump (minor) + push**

`& <repo>\scripts\bump-version.ps1 minor`; then `git push origin feat/mcp-server && git push origin v<new>`.

---

## Self-Review

- **Spec coverage:** §3.1 export → Task 1; §3.2 stats → Task 2; §3.3 project_meta → Task 3; §3.4 list_dashboards → Task 4; §6 integration → Task 6; §7 testing → Tasks 1–5. All covered.
- **Placeholders:** none — every step has concrete code/commands.
- **Name/type consistency:** tools `cz_jira_export/stats/project_meta/list_dashboards`; helper `_parse_csv`; constants `_STATS_CAP/_STATS_PAGE/_STATS_FIELDS`; all reuse `build_scoped_jql`/`_get_json`/`settings.project_key`. Registration set (Task 5) lists exactly the 8 tool names defined. Tool IDs in Task 6 match `<tool>_mcp_chargezone`.
- **GET-only preserved:** export uses `client.get`; stats/meta/dashboards use `_get_json` (GET). No POST. The `JiraAuthClient` still blocks writes.
