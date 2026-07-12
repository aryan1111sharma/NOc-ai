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


@pytest.mark.asyncio
async def test_all_control_center_tools_registered():
    async with Client(build_test_server()) as client:
        names = {t.name for t in await client.list_tools()}
    expected = {
        "charger_get_charging_status", "charger_get_session_details", "charger_get_meters",
        "charger_get_ocpp_status", "charger_get_network_status", "charger_get_system_info",
        "charger_get_allocation_state", "charger_get_pm_power", "charger_get_alarms",
        "charger_get_temperature", "charger_list_log_sources", "charger_get_logs",
        "charger_get_device_metrics",
    }
    assert expected <= names


@pytest.mark.asyncio
async def test_composites_merge_under_fixed_keys():
    async with Client(build_test_server()) as c:
        ocpp = (await c.call_tool("charger_get_ocpp_status", {"charger_id": "CHG-1"})).data
        net = (await c.call_tool("charger_get_network_status", {"charger_id": "CHG-1"})).data
        sysinfo = (await c.call_tool("charger_get_system_info", {"charger_id": "CHG-1"})).data
        pm = (await c.call_tool("charger_get_pm_power", {"charger_id": "CHG-1"})).data
        temp = (await c.call_tool("charger_get_temperature", {"charger_id": "CHG-1"})).data
    assert ocpp == {"status": {"connected": True, "url": "wss://ocpp"},
                    "config": {"HeartbeatInterval": "300"}}
    assert set(net) == {"overall", "gsm"} and net["gsm"] == {"rssi": -71, "operator": "Jio"}
    assert set(sysinfo) == {"hardware", "software", "rtc"}
    assert pm == {"pm_count": {"pm_count": 4}, "power": {"modules": [{"id": 1, "kw": 15}]}}
    assert set(temp) == {"sensors", "thresholds"}


@pytest.mark.asyncio
async def test_session_details_gun_vs_history():
    async with Client(build_test_server()) as c:
        gun = (await c.call_tool(
            "charger_get_session_details", {"charger_id": "CHG-1", "gun_id": 1})).data
        hist = (await c.call_tool("charger_get_session_details", {"charger_id": "CHG-1"})).data
    assert set(gun) == {"active", "last"} and gun["active"] == {"session_id": "S1", "energy_kwh": 3.2}
    assert set(hist) == {"history"}


@pytest.mark.asyncio
async def test_meters_and_imd_merge():
    async with Client(build_test_server()) as c:
        base = (await c.call_tool("charger_get_meters", {"charger_id": "CHG-1"})).data
        with_gun = (await c.call_tool(
            "charger_get_meters", {"charger_id": "CHG-1", "gun_id": 1})).data
    assert set(base) == {"ac_meter", "dc_meters"}
    assert with_gun["imd"] == {"gun_id": 1, "resistance_kohm": 999}


@pytest.mark.asyncio
async def test_alarms_fleet_vs_gun():
    async with Client(build_test_server()) as c:
        fleet = (await c.call_tool("charger_get_alarms", {"charger_id": "CHG-1"})).data
        gun = (await c.call_tool(
            "charger_get_alarms", {"charger_id": "CHG-1", "gun_id": 1})).data
    assert set(fleet) == {"latched", "pending"}
    assert set(gun) == {"errors_by_gun"}


@pytest.mark.asyncio
async def test_logs_pair_and_metrics():
    async with Client(build_test_server()) as c:
        sources = (await c.call_tool("charger_list_log_sources", {"charger_id": "CHG-1"})).data
        logs = (await c.call_tool(
            "charger_get_logs", {"charger_id": "CHG-1", "source": "app"})).data
        metrics = (await c.call_tool("charger_get_device_metrics", {"charger_id": "CHG-1"})).data
        alloc = (await c.call_tool("charger_get_allocation_state", {"charger_id": "CHG-1"})).data
    assert sources == {"sources": ["app", "ocpp"]}
    assert logs == {"data": ["line1", "line2"]}
    assert set(metrics) == {"cpu", "memory", "disk"}
    assert alloc == {"state": {"guns": 2, "allocated_kw": 60}}
