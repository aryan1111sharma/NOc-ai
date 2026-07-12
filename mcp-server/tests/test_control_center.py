"""Tests for the on-charger control-center tools (noc_mcp.control_center)."""

import httpx
import pytest

from noc_mcp.auth import NocAuthClient
from noc_mcp.control_center import _proxy_get

_LOGIN = "/api/v1/auth/login"


def _login(request):
    return httpx.Response(200, json={"access_token": "t.t.t", "token_type": "bearer"})


def _client(handler):
    return NocAuthClient(base_url="http://noc.test", email="e", password="p",
                         transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_proxy_get_returns_device_json():
    def handler(request):
        if request.url.path.endswith(_LOGIN):
            return _login(request)
        # path-based proxy: /api/v1/chargers/CHG-1/proxy/8003/api/v1/charging/status
        assert "/proxy/8003/api/v1/charging/status" in request.url.path
        return httpx.Response(200, json={"guns": [{"gun_id": 1, "connected": True}]})

    async with _client(handler) as c:
        out = await _proxy_get(c, "CHG-1", 8003, "api/v1/charging/status")
    assert out == {"guns": [{"gun_id": 1, "connected": True}]}


@pytest.mark.asyncio
async def test_proxy_get_wraps_non_dict_json():
    def handler(request):
        if request.url.path.endswith(_LOGIN):
            return _login(request)
        return httpx.Response(200, json=["a", "b"])

    async with _client(handler) as c:
        out = await _proxy_get(c, "CHG-1", 8005, "api/v1/devtools/logs/x")
    assert out == {"data": ["a", "b"]}


@pytest.mark.asyncio
async def test_proxy_get_returns_error_on_offline():
    def handler(request):
        if request.url.path.endswith(_LOGIN):
            return _login(request)
        return httpx.Response(502, json={"detail": "charger offline"})

    async with _client(handler) as c:
        out = await _proxy_get(c, "OFFLINE", 8003, "api/v1/charging/status")
    assert "error" in out and out["port"] == 8003 and out["path"] == "api/v1/charging/status"


from fastmcp import Client
from tests.fake_noc import build_test_server


@pytest.mark.asyncio
async def test_charging_status_fleet_and_per_gun():
    async with Client(build_test_server()) as client:
        fleet = await client.call_tool("charger_get_charging_status", {"charger_id": "CHG-1"})
        gun = await client.call_tool(
            "charger_get_charging_status", {"charger_id": "CHG-1", "gun_id": 1})
    assert fleet.data == {"guns": [{"gun_id": 1, "connected": True, "plc_state": "C"}]}
    assert gun.data == {"gun_id": 1, "connected": True, "plc_state": "C"}


@pytest.mark.asyncio
async def test_charging_status_offline_returns_error():
    async with Client(build_test_server()) as client:
        res = await client.call_tool("charger_get_charging_status", {"charger_id": "OFFLINE"})
    assert "error" in res.data and res.data["port"] == 8003
