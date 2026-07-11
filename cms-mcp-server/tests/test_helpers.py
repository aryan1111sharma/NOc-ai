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


from cms_mcp.server import _resolve_charger_id


def _resolver_handler(counter):
    """MockTransport handler: login + GetChargerByCustomerIDForDD returns 2 chargers."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(_LOGIN):
            return _login_ok(request)
        if request.url.path.endswith("/GetChargerByCustomerIDForDD"):
            counter.append(1)
            return httpx.Response(200, json=[
                {"ChargerID": 1187, "QChargerID": "ab-sam-00585"},
                {"ChargerID": 1429, "QChargerID": "ab-sam-sim"},
            ])
        return httpx.Response(404)
    return handler


@pytest.mark.asyncio
async def test_resolve_hit_and_caches():
    calls = []
    async with _client(_resolver_handler(calls)) as c:
        cache = {}
        assert await _resolve_charger_id(c, "ab-sam-00585", cache) == 1187
        assert await _resolve_charger_id(c, "ab-sam-sim", cache) == 1429
    assert len(calls) == 1  # map fetched once, then served from cache


@pytest.mark.asyncio
async def test_resolve_is_case_insensitive():
    calls = []
    async with _client(_resolver_handler(calls)) as c:
        assert await _resolve_charger_id(c, "AB-SAM-SIM", {}) == 1429


@pytest.mark.asyncio
async def test_resolve_miss_refreshes_then_raises_with_suggestion():
    calls = []
    async with _client(_resolver_handler(calls)) as c:
        with pytest.raises(ValueError) as ei:
            await _resolve_charger_id(c, "ab-sam-9999", {})
    assert len(calls) == 2            # initial load + one refresh on miss
    assert "ab-sam-9999" in str(ei.value)
    assert "ab-sam" in str(ei.value)  # near-match suggestion included
