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
