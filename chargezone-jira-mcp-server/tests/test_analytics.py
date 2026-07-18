"""Analytics/export tools: export, stats, project_meta, list_dashboards (mocked Jira)."""

import httpx
import pytest
from fastmcp import Client

from cz_jira_mcp.server import build_server


@pytest.mark.asyncio
async def test_cz_jira_export_rest_curated_columns(mock_client):
    captured = {}
    def handler(request):
        captured["path"] = request.url.path
        captured["jql"] = request.url.params.get("jql")
        captured["fields"] = request.url.params.get("fields")
        return httpx.Response(200, json={"issues": [{"key": "QCH-1", "fields": {
            "issuetype": {"name": "Task"}, "summary": "Offline", "status": {"name": "To Do"},
            "priority": {"name": "Medium"}, "assignee": {"displayName": "Q"},
            "reporter": {"displayName": "R"}, "created": "c", "updated": "u",
            "customfield_10120": {"value": "Priority Level - 01"}, "customfield_10110": "CG0003",
            "customfield_10156": {"value": "Charger Software Issue"}, "customfield_10119": "NA"}}]})
    mcp = build_server(client=mock_client(handler))
    async with Client(mcp) as c:
        res = await c.call_tool("cz_jira_export", {"conditions": "statusCategory != Done", "limit": 500})
    assert captured["path"] == "/rest/api/3/search/jql"
    assert captured["jql"] == 'project = "QCH" AND (statusCategory != Done) ORDER BY updated DESC'
    h = res.data["headers"]
    assert h == ["Issue key","Issue Type","Summary","Status","Priority Level","Priority",
                 "Assignee","Reporter","OCPP ID","Category","Hardware Failure Description",
                 "Created","Updated"]
    row = res.data["rows"][0]
    assert row[0] == "QCH-1" and row[4] == "Priority Level - 01" and row[8] == "CG0003"
    assert res.data["row_count"] == 1 and res.data["truncated"] is False

@pytest.mark.asyncio
async def test_cz_jira_export_all_fields_adds_columns_and_clamps(mock_client):
    def handler(request):
        return httpx.Response(200, json={"issues": [{"key": "QCH-2", "fields": {
            "description": {"type":"doc","version":1,"content":[{"type":"paragraph",
              "content":[{"type":"text","text":"boom"}]}]},
            "parent": {"key": "QCH-1"}, "duedate": "2026-08-01"}}]})
    mcp = build_server(client=mock_client(handler))
    async with Client(mcp) as c:
        res = await c.call_tool("cz_jira_export", {"all_fields": True, "limit": 0})
    assert res.data["headers"][-3:] == ["Description","Parent key","Due date"]
    assert "boom" in res.data["rows"][0][-3]


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


@pytest.mark.asyncio
async def test_cz_jira_stats_skips_unparseable_created(mock_client):
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    good = {"key": "QCH-1", "fields": {"status": {"name": "To Do"}, "issuetype": {"name": "Task"},
            "priority": {"name": "Medium"}, "assignee": {"displayName": "Q"},
            "created": (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.000%z")}}
    bad = {"key": "QCH-2", "fields": {"status": {"name": "QA"}, "issuetype": {"name": "Bug"},
           "priority": {"name": "Medium"}, "assignee": {"displayName": "Q"}, "created": 12345}}
    def handler(request):
        return httpx.Response(200, json={"issues": [good, bad]})
    mcp = build_server(client=mock_client(handler))
    async with Client(mcp) as c:
        res = await c.call_tool("cz_jira_stats", {"conditions": ""})
    assert res.data["sampled"] == 2            # both counted in distributions
    assert res.data["created_last_7d"] == 1    # only the good one; bad one skipped, no crash


@pytest.mark.asyncio
async def test_cz_jira_search_surfaces_priority_level_and_ocpp_and_orders(mock_client):
    captured = {}
    def handler(request):
        captured["jql"] = request.url.params.get("jql")
        captured["fields"] = request.url.params.get("fields")
        return httpx.Response(200, json={"issues": [{"key": "QCH-1", "fields": {
            "summary": "Offline", "status": {"name": "To Do"}, "issuetype": {"name": "Task"},
            "priority": {"name": "Medium"}, "updated": "2026-07-10T10:00:00.000+0000",
            "project": {"key": "QCH"}, "customfield_10120": {"value": "Priority Level - 01"},
            "customfield_10110": "CG0003"}}]})
    mcp = build_server(client=mock_client(handler))
    async with Client(mcp) as c:
        res = await c.call_tool("cz_jira_search", {"conditions": "statusCategory != Done",
                                                   "order_by": "cf[10120] ASC"})
    assert captured["jql"] == 'project = "QCH" AND (statusCategory != Done) ORDER BY cf[10120] ASC'
    assert "customfield_10120" in captured["fields"] and "customfield_10110" in captured["fields"]
    assert res.data[0]["priority_level"] == "P1"
    assert res.data[0]["ocpp_id"] == "CG0003"


@pytest.mark.asyncio
async def test_cz_jira_stats_by_priority_level(mock_client):
    def mk(k, pl):
        f = {"status": {"name": "To Do"}, "issuetype": {"name": "Task"},
             "priority": {"name": "Medium"}, "assignee": {"displayName": "Q"},
             "created": "2026-07-10T10:00:00.000+0000"}
        if pl is not None:
            f["customfield_10120"] = {"value": pl}
        return {"key": k, "fields": f}
    issues = [mk("QCH-1","Priority Level - 01"), mk("QCH-2","Priority Level - 02"),
              mk("QCH-3","Priority Level - 02"), mk("QCH-4","Priority Level - 03"), mk("QCH-5", None)]
    def handler(request):
        assert "customfield_10120" in request.url.params.get("fields")
        return httpx.Response(200, json={"issues": issues})
    mcp = build_server(client=mock_client(handler))
    async with Client(mcp) as c:
        res = await c.call_tool("cz_jira_stats", {"conditions": "statusCategory != Done"})
    assert res.data["by_priority_level"] == {"P1": 1, "P2": 2, "P3": 1, "Unassigned": 1}


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
