"""Unit tests for the per-charger helper functions in server.py."""

import httpx
import pytest

from cms_mcp.auth import CmsAuthClient
from cms_mcp.server import _post_json, _datatables_body

_LOGIN = "/Home/GetUsersByEmailAndPassword"


def _login_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200, json={"UserID": 1},
        headers={"set-cookie": ".ASPXAUTH=abc; path=/"},
    )


def _client(handler) -> CmsAuthClient:
    return CmsAuthClient(
        base_url="http://cms.test", user="u", password="p",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_post_json_sends_form_and_ajax_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(_LOGIN):
            return _login_ok(request)
        seen["xrw"] = request.headers.get("x-requested-with")
        seen["body"] = request.content.decode()
        return httpx.Response(200, json=[{"ChargerID": 5}])

    async with _client(handler) as c:
        out = await _post_json(c, "/ChargerDetails/GetChargerByCustomerIDForDD",
                               data={"CustomerID": "0"})
    assert out == [{"ChargerID": 5}]
    assert seen["xrw"] == "XMLHttpRequest"
    assert "CustomerID=0" in seen["body"]


@pytest.mark.asyncio
async def test_post_json_empty_body_returns_empty_list():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(_LOGIN):
            return _login_ok(request)
        return httpx.Response(200, text="")

    async with _client(handler) as c:
        out = await _post_json(c, "/x", json={"a": 1})
    assert out == []


def test_datatables_body_shape():
    body = _datatables_body(1429, 10)
    assert body["lvm"] == {"ChargerID": "1429"}   # numeric id, stringified
    assert body["limit"] == 10
    assert body["length"] == 10
    assert body["start"] == 0
    assert body["draw"] == 1
    assert body["columns"] == []
    assert body["order"] == []
    assert body["search"] == {"value": "", "regex": False}
