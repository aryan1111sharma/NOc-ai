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
