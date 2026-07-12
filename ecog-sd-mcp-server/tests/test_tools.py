import httpx
import pytest
from fastmcp import Client

from ecog_sd_mcp.server import build_server


def _client(handler):
    from ecog_sd_mcp.auth import CookieAuthClient
    return CookieAuthClient(base_url="https://ecog.test", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_sd_list_requests_maps_params_and_trims():
    cap = {}

    def handler(request: httpx.Request) -> httpx.Response:
        cap["path"] = request.url.path
        cap["ownership"] = request.url.params.get("requestOwnership")
        cap["status"] = request.url.params.get("requestStatus")
        cap["search"] = request.url.params.get("searchTerm")
        cap["limit"] = request.url.params.get("limit")
        return httpx.Response(200, json={"values": [{
            "issueKey": "SASW-2911", "currentStatus": {"status": "Resolved"},
            "createdDate": {"jira": "2026-07-01T09:00:00.000+0000"},
            "reporter": {"displayName": "Chitransh Jain"},
            "serviceDesk": {"projectKey": "SASW"},
            "requestFieldValues": [{"fieldId": "summary", "value": "Invalid Undervoltage"}]}]})

    mcp = build_server(client=_client(handler))
    async with Client(mcp) as c:
        res = await c.call_tool("sd_list_requests",
                                {"search": "undervoltage", "status": "CLOSED", "ownership": "ALL", "limit": 10})

    assert cap["path"] == "/rest/servicedeskapi/request"
    assert cap["ownership"] == "ALL_REQUESTS"
    assert cap["status"] == "CLOSED_REQUESTS"
    assert cap["search"] == "undervoltage"
    assert cap["limit"] == "10"
    assert res.data == [{"key": "SASW-2911", "summary": "Invalid Undervoltage",
                         "status": "Resolved", "created": "2026-07-01T09:00:00.000+0000",
                         "reporter": "Chitransh Jain", "serviceDesk": "SASW"}]


@pytest.mark.asyncio
async def test_sd_get_request_returns_detail():
    cap = {}

    def handler(request: httpx.Request) -> httpx.Response:
        cap["path"] = request.url.path
        return httpx.Response(200, json={
            "issueKey": "SASW-2911", "currentStatus": {"status": "Resolved"},
            "createdDate": {"jira": "2026-07-01T09:00:00.000+0000"},
            "reporter": {"displayName": "Chitransh Jain"},
            "serviceDesk": {"projectKey": "SASW"},
            "requestType": {"name": "Technical support"},
            "requestFieldValues": [
                {"fieldId": "summary", "value": "Invalid Undervoltage"},
                {"fieldId": "description", "value": "<p>Meter reads low</p>"}]})

    mcp = build_server(client=_client(handler))
    async with Client(mcp) as c:
        res = await c.call_tool("sd_get_request", {"key": "SASW-2911"})

    assert cap["path"] == "/rest/servicedeskapi/request/SASW-2911"
    assert res.data["key"] == "SASW-2911"
    assert res.data["requestType"] == "Technical support"
    assert res.data["description"] == "Meter reads low"


@pytest.mark.asyncio
async def test_sd_get_request_comments_public_and_stripped():
    cap = {}

    def handler(request: httpx.Request) -> httpx.Response:
        cap["path"] = request.url.path
        cap["public"] = request.url.params.get("public")
        return httpx.Response(200, json={"values": [
            {"author": {"displayName": "Asha"}, "created": {"jira": "2026-07-02T08:00:00.000+0000"},
             "body": "<p>Replaced the meter</p>"}]})

    mcp = build_server(client=_client(handler))
    async with Client(mcp) as c:
        res = await c.call_tool("sd_get_request_comments", {"key": "SASW-2911", "limit": 20})

    assert cap["path"] == "/rest/servicedeskapi/request/SASW-2911/comment"
    assert cap["public"] == "true"
    assert res.data == [{"author": "Asha", "created": "2026-07-02T08:00:00.000+0000",
                         "body": "Replaced the meter"}]


@pytest.mark.asyncio
async def test_sd_list_servicedesks():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/servicedeskapi/servicedesk"
        return httpx.Response(200, json={"values": [
            {"id": "1", "projectKey": "SASW", "projectName": "Support"},
            {"id": "2", "projectKey": "HW", "projectName": "Hardware"}]})

    mcp = build_server(client=_client(handler))
    async with Client(mcp) as c:
        res = await c.call_tool("sd_list_servicedesks", {})

    assert res.data == [{"id": "1", "projectKey": "SASW", "projectName": "Support"},
                        {"id": "2", "projectKey": "HW", "projectName": "Hardware"}]


@pytest.mark.asyncio
async def test_only_read_tools_registered():
    def handler(request): return httpx.Response(200, json={"values": []})
    mcp = build_server(client=_client(handler))
    names = {t.name for t in await mcp.list_tools()}
    assert names == {"sd_list_requests", "sd_get_request",
                     "sd_get_request_comments", "sd_list_servicedesks"}
    forbidden = ("create", "update", "delete", "add", "edit", "post", "transition", "close")
    assert not [n for n in names if any(f in n for f in forbidden)]
