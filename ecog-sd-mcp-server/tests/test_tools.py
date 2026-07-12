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
