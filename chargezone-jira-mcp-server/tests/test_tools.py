"""End-to-end tool calls via an in-memory FastMCP client, backed by a mock Jira."""

import httpx
import pytest
from fastmcp import Client

from cz_jira_mcp.server import build_server


@pytest.mark.asyncio
async def test_cz_jira_search_scopes_jql_to_qch_and_trims(mock_client):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["jql"] = request.url.params.get("jql")
        captured["max"] = request.url.params.get("maxResults")
        return httpx.Response(200, json={"issues": [
            {"key": "QCH-1", "fields": {
                "summary": "Charger offline", "status": {"name": "To Do"},
                "issuetype": {"name": "Task"}, "priority": {"name": "High"},
                "assignee": {"displayName": "Asha"},
                "updated": "2026-07-10T10:00:00.000+0000", "project": {"key": "QCH"}}}]})

    mcp = build_server(client=mock_client(handler))
    async with Client(mcp) as c:
        res = await c.call_tool("cz_jira_search", {"conditions": 'status = "To Do"', "limit": 10})

    assert captured["path"] == "/rest/api/3/search/jql"
    assert captured["jql"] == 'project = "QCH" AND (status = "To Do") ORDER BY updated DESC'
    assert captured["max"] == "10"
    assert res.data == [{
        "key": "QCH-1", "summary": "Charger offline", "status": "To Do", "type": "Task",
        "priority": "High", "assignee": "Asha",
        "updated": "2026-07-10T10:00:00.000+0000", "project": "QCH"}]


@pytest.mark.asyncio
async def test_cz_jira_get_issue_returns_detail(mock_client):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(200, json={
            "key": "QCH-2",
            "fields": {
                "summary": "PM fault", "status": {"name": "In Progress"},
                "issuetype": {"name": "Bug"}, "reporter": {"displayName": "Ravi"},
                "created": "2026-07-01T09:00:00.000+0000", "labels": ["firmware"],
                "components": [{"name": "PowerModule"}],
                "description": {"type": "doc", "version": 1, "content": [
                    {"type": "paragraph", "content": [
                        {"type": "text", "text": "Sink PM overheats."}]}]}}})

    mcp = build_server(client=mock_client(handler))
    async with Client(mcp) as c:
        res = await c.call_tool("cz_jira_get_issue", {"key": "QCH-2"})

    assert captured["path"] == "/rest/api/3/issue/QCH-2"
    assert res.data["key"] == "QCH-2"
    assert res.data["reporter"] == "Ravi"
    assert res.data["components"] == ["PowerModule"]
    assert "Sink PM overheats." in res.data["description"]


@pytest.mark.asyncio
async def test_cz_jira_get_issue_rejects_foreign_key(mock_client):
    called = {"hit": False}

    def handler(request: httpx.Request) -> httpx.Response:
        called["hit"] = True
        return httpx.Response(200, json={})

    mcp = build_server(client=mock_client(handler))
    async with Client(mcp) as c:
        with pytest.raises(Exception, match="QCH"):
            await c.call_tool("cz_jira_get_issue", {"key": "CHAR-1"})
    assert called["hit"] is False  # rejected before any network call


@pytest.mark.asyncio
async def test_cz_jira_get_comments_flattens_bodies(mock_client):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["max"] = request.url.params.get("maxResults")
        return httpx.Response(200, json={"comments": [
            {"author": {"displayName": "Asha"}, "created": "2026-07-02T08:00:00.000+0000",
             "body": {"type": "doc", "version": 1, "content": [
                 {"type": "paragraph", "content": [
                     {"type": "text", "text": "Replaced the module."}]}]}}]})

    mcp = build_server(client=mock_client(handler))
    async with Client(mcp) as c:
        res = await c.call_tool("cz_jira_get_comments", {"key": "QCH-2", "limit": 25})

    assert captured["path"] == "/rest/api/3/issue/QCH-2/comment"
    assert captured["max"] == "25"
    assert res.data == [{"author": "Asha", "created": "2026-07-02T08:00:00.000+0000",
                         "body": "Replaced the module."}]


@pytest.mark.asyncio
async def test_cz_jira_get_issue_transitions_reads_status_and_targets(mock_client):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["method"] = request.method
        captured["expand"] = request.url.params.get("expand")
        return httpx.Response(200, json={
            "fields": {"status": {"name": "In Progress"}},
            "transitions": [
                {"id": "41", "name": "Done", "to": {"name": "Done"}},
                {"id": "21", "name": "Reopen", "to": {"name": "To Do"}}]})

    mcp = build_server(client=mock_client(handler))
    async with Client(mcp) as c:
        res = await c.call_tool("cz_jira_get_issue_transitions", {"key": "QCH-2"})

    assert captured["method"] == "GET"                      # read-only, never POST
    assert captured["path"] == "/rest/api/3/issue/QCH-2"
    assert captured["expand"] == "transitions"
    assert res.data == {
        "status": "In Progress",
        "transitions": [{"id": "41", "name": "Done", "to": "Done"},
                        {"id": "21", "name": "Reopen", "to": "To Do"}]}


@pytest.mark.asyncio
async def test_server_registers_only_read_tools(mock_client):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    mcp = build_server(client=mock_client(handler))
    tools = await mcp.list_tools()
    names = {t.name for t in tools}

    assert names == {
        "cz_jira_search", "cz_jira_get_issue", "cz_jira_get_comments",
        "cz_jira_get_issue_transitions", "cz_jira_export", "cz_jira_stats",
        "cz_jira_project_meta", "cz_jira_list_dashboards",
    }
    forbidden = ("create", "update", "delete", "add", "edit", "post", "assign", "move")
    assert not [n for n in names if any(f in n for f in forbidden)]
