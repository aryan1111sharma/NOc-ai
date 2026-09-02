"""Tests for the fleet-status tool (noc_mcp.fleet)."""

import httpx
import pytest

from noc_mcp.auth import NocAuthClient
from noc_mcp.fleet import _fetch_fleet_status

_LOGIN = "/api/v1/auth/login"


def _client(handler):
    return NocAuthClient(base_url="http://noc.test", email="e", password="p",
                         transport=httpx.MockTransport(handler))


def _ok_login(request):
    return httpx.Response(200, json={"access_token": "t.t.t", "token_type": "bearer"})


@pytest.mark.asyncio
async def test_offline_only_is_passed_through():
    seen = {}

    def handler(request):
        if request.url.path.endswith(_LOGIN):
            return _ok_login(request)
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"items": []})

    async with _client(handler) as c:
        await _fetch_fleet_status(c, offline_only=True, online_only=False, search=None,
                                  customer_name=None, model=None, limit=None, as_map=False)
    assert seen["path"] == "/api/v1/chargers"
    assert seen["params"]["offline_only"] == "true"
    assert "online_only" not in seen["params"]


@pytest.mark.asyncio
async def test_as_map_uses_the_map_endpoint():
    seen = {}

    def handler(request):
        if request.url.path.endswith(_LOGIN):
            return _ok_login(request)
        seen["path"] = request.url.path
        return httpx.Response(200, json={"chargers": []})

    async with _client(handler) as c:
        await _fetch_fleet_status(c, offline_only=False, online_only=False, search=None,
                                  customer_name="Ador", model=None, limit=None, as_map=True)
    assert seen["path"] == "/api/v1/chargers/map"


@pytest.mark.asyncio
async def test_mutually_exclusive_filters_are_rejected():
    async with _client(_ok_login) as c:
        out = await _fetch_fleet_status(c, offline_only=True, online_only=True, search=None,
                                        customer_name=None, model=None, limit=None, as_map=False)
    assert "error" in out
    assert "mutually exclusive" in out["error"]


@pytest.mark.asyncio
async def test_result_is_stamped_history():
    def handler(request):
        if request.url.path.endswith(_LOGIN):
            return _ok_login(request)
        return httpx.Response(200, json={"items": []})

    async with _client(handler) as c:
        out = await _fetch_fleet_status(c, offline_only=False, online_only=False, search=None,
                                        customer_name=None, model=None, limit=None, as_map=False)
    assert out["source"] == "history"
