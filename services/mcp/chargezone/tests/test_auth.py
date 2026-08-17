"""JiraAuthClient sets HTTP Basic auth and JSON accept header."""

import base64

import httpx
import pytest

from cz_jira_mcp.auth import JiraAuthClient


@pytest.mark.asyncio
async def test_sets_basic_auth_header(mock_client):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["accept"] = request.headers.get("accept")
        return httpx.Response(200, json={})

    async with mock_client(handler) as c:
        await c.get("/rest/api/3/myself")

    expected = "Basic " + base64.b64encode(b"e@x.com:tok").decode()
    assert seen["auth"] == expected
    assert seen["accept"] == "application/json"


@pytest.mark.asyncio
async def test_write_methods_are_blocked(mock_client):
    def handler(request):  # pragma: no cover - should never be hit
        return httpx.Response(200, json={})
    async with mock_client(handler) as c:
        for call in (c.post("/x"), c.put("/x"), c.patch("/x"), c.delete("/x")):
            with pytest.raises(RuntimeError, match="read-only"):
                await call
