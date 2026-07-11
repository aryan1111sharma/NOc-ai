"""End-to-end tool calls via an in-memory FastMCP client, backed by a mock Jira."""

import httpx
import pytest
from fastmcp import Client

from jira_mcp.server import build_server


@pytest.mark.asyncio
async def test_jira_search_forwards_jql_and_trims(mock_client):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["jql"] = request.url.params.get("jql")
        captured["max"] = request.url.params.get("maxResults")
        return httpx.Response(200, json={"issues": [
            {"key": "ECOG-1", "fields": {
                "summary": "Charger reboots", "status": {"name": "Open"},
                "issuetype": {"name": "Bug"}, "priority": {"name": "High"},
                "assignee": {"displayName": "Asha"},
                "updated": "2026-07-10T10:00:00.000+0000", "project": {"key": "ECOG"}}}]})

    mcp = build_server(client=mock_client(handler))
    async with Client(mcp) as c:
        res = await c.call_tool(
            "jira_search", {"jql": "project = ECOG AND status = Open", "limit": 10})

    assert captured["path"] == "/rest/api/3/search/jql"
    assert captured["jql"] == "project = ECOG AND status = Open"
    assert captured["max"] == "10"
    assert res.data == [{
        "key": "ECOG-1", "summary": "Charger reboots", "status": "Open", "type": "Bug",
        "priority": "High", "assignee": "Asha",
        "updated": "2026-07-10T10:00:00.000+0000", "project": "ECOG"}]
