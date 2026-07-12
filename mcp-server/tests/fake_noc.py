"""Shared httpx.MockTransport fake of the NOC backend for control-center tool tests.

Handles login + the path-based charger proxy, returning canned lean device JSON keyed by
(port, path). Charger id 'OFFLINE' makes the proxy return 502 (for the error path).
"""

import httpx

from noc_mcp.auth import NocAuthClient
from noc_mcp.server import build_server

_LOGIN = "/api/v1/auth/login"

# Canned device responses keyed by (port, proxy-path-without-leading-slash).
_DEVICE = {
    (8003, "api/v1/charging/status"): {"guns": [{"gun_id": 1, "connected": True, "plc_state": "C"}]},
    (8003, "api/v1/charging/status/1"): {"gun_id": 1, "connected": True, "plc_state": "C"},
    (8003, "api/v1/session_details/active/1"): {"session_id": "S1", "energy_kwh": 3.2},
    (8003, "api/v1/session_details/1"): {"session_id": "S0", "stop_reason": "EVDisconnected"},
    (8003, "api/v1/session_details/history"): {"sessions": [{"session_id": "S0"}]},
    (8003, "api/v1/devices/meters"): {"ac_meter": {"v": 230}, "dc_meters": [{"gun_id": 1, "v": 400}]},
    (8003, "api/v1/devices/imd/1"): {"gun_id": 1, "resistance_kohm": 999},
    (8003, "api/v1/ocpp/status"): {"connected": True, "url": "wss://ocpp"},
    (8003, "api/v1/config/ocpp"): {"HeartbeatInterval": "300"},
    (8000, "api/v1/network/status"): {"active_interface": "gsm", "lan": {"up": False}},
    (8000, "api/v1/network/gsm/status"): {"rssi": -71, "operator": "Jio"},
    (8000, "api/v1/system/hardware"): {"model": "QFlex", "serial": "SN1"},
    (8000, "api/v1/system/software"): {"version": "1.2.3"},
    (8000, "api/v1/system/rtc"): {"datetime": "2026-07-12T06:00:00Z"},
    (8002, "api/state"): {"state": {"guns": 2, "allocated_kw": 60}},
    (8002, "api/pm_count"): {"pm_count": 4},
    (8002, "api/pm/get_all_power"): {"modules": [{"id": 1, "kw": 15}]},
    (8006, "error-generator/alarms/latched"): {"latched": [{"vendor_error_code": "E12", "gun_id": 1}]},
    (8006, "error-generator/errors/pending"): {"pending": []},
    (8006, "error-generator/errors/by-gun/1"): {"errors": [{"code": "E12"}]},
    (8006, "temp/sensors"): {"sensors": [{"name": "inlet", "c": 41}]},
    (8006, "temp/thresholds"): {"thresholds": {"inlet": 70}},
    (8005, "api/v1/devtools/logs/sources"): {"sources": ["app", "ocpp"]},
    (8005, "api/v1/devtools/logs/app"): {"data": ["line1", "line2"]},
    (8005, "api/v1/devtools/metrics"): {"cpu": {"pct": 12}, "memory": {"pct": 40}, "disk": {"pct": 55}},
}


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith(_LOGIN):
        return httpx.Response(200, json={"access_token": "t.t.t", "token_type": "bearer"})
    marker = "/proxy/"
    if marker in path:
        charger = path.split("/proxy/")[0].rsplit("/", 1)[-1]
        rest = path.split("/proxy/", 1)[1]          # "<port>/<device path>"
        port_str, _, device_path = rest.partition("/")
        if charger == "OFFLINE":
            return httpx.Response(502, json={"detail": "charger offline"})
        payload = _DEVICE.get((int(port_str), device_path))
        if payload is None:
            return httpx.Response(404, json={"detail": f"no route {port_str}/{device_path}"})
        return httpx.Response(200, json=payload)
    return httpx.Response(404, json={"detail": "unhandled"})


def build_test_server():
    """FastMCP server wired to the fake NOC (no network)."""
    client = NocAuthClient(base_url="http://noc.test", email="e", password="p",
                           transport=httpx.MockTransport(_handler))
    return build_server(client)
