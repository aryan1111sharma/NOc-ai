"""Security regression tests for charger-proxy URL construction (noc_mcp.control_center).

Separate from test_control_center.py (which covers behaviour) because these assert a security
invariant with its own fixture shape: a transport that *captures* outgoing requests so we can
assert on what actually goes out, not on the return value.

THE ORACLE DECODES. An earlier version of this file asserted against ``request.url.raw_path``,
which is still percent-encoded -- so ``"?" not in raw`` and ``_WRITE_PATH not in raw`` passed
trivially, because the ``/`` were ``%2F`` and the ``?`` was ``%3F``. That measured the
client-side wire, which is precisely the layer that does NOT reflect what a decoding receiver
sees, and it passed over a live arbitrary-port / arbitrary-path GET SSRF. Every assertion here
now decodes first. tests/test_receiver_model.py takes the stronger route and asserts on a real
router's resolved path_params.

The invariant: no model-controlled string reaching ``_proxy_get`` may alter the *structure* of
the outgoing URL as a decoding receiver parses it -- it may not add a query parameter, may not
escape its path segment, and may not choose the port or the on-charger path.
"""

from urllib.parse import unquote

import httpx
import pytest
from fastmcp import Client

from noc_mcp.auth import NocAuthClient
from noc_mcp.control_center import _proxy_get
from noc_mcp.server import build_server

_LOGIN = "/api/v1/auth/login"

# A real non-GET on-charger endpoint, from noc_mcp/catalog/rbac-rules.csv line 122:
#   8003,POST,/api/v1/pin/contactor/dual,admin,qflex-charging,Dual Contactor Control
_WRITE_PORT = 8003
_WRITE_PATH = "api/v1/pin/contactor/dual"

# Confirmed attack payloads.
_EVIL_CHARGER_ID = f"CHG-1/proxy/{_WRITE_PORT}/{_WRITE_PATH}?method=POST&x="
# The port+path hijack: survives encoding because the receiver decodes before routing.
_EVIL_CHARGER_ID_HIJACK = "CHG-1/proxy/8005/api/v1/devtools/logs/x"
_EVIL_PATH_TRAVERSAL = f"api/v1/devtools/logs/../../../../{_WRITE_PATH}?method=POST"


def _capturing():
    """Return ``(seen, handler)``: handler answers login, records every other request."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(_LOGIN):
            return httpx.Response(200, json={"access_token": "t.t.t", "token_type": "bearer"})
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    return seen, handler


def _client(handler):
    return NocAuthClient(base_url="http://noc.test", email="e", password="p",
                         transport=httpx.MockTransport(handler))


def _assert_safe(request: httpx.Request) -> None:
    """Assert an outgoing request is safe *as a decoding receiver would parse it*."""
    assert request.method == "GET"
    assert "method" not in request.url.params, f"method query smuggled: {request.url}"

    # Decode exactly like the receiving ASGI server does before it routes.
    decoded = unquote(request.url.raw_path.decode())

    assert "?" not in decoded, f"query separator survives decoding: {decoded}"
    assert "#" not in decoded, f"fragment separator survives decoding: {decoded}"
    assert _WRITE_PATH not in decoded, f"reached a write endpoint: {decoded}"
    # The route template splits on '/proxy/'; a second one lets the caller re-split the route
    # and choose the port + on-charger path.
    assert decoded.count("/proxy/") == 1, f"route re-split possible: {decoded}"

    head, _, _tail = decoded.partition("/proxy/")
    assert head.startswith("/api/v1/chargers/"), decoded
    segment = head[len("/api/v1/chargers/"):]
    assert "/" not in segment, f"charger_id escaped its segment: {segment!r}"
    assert segment not in (".", ".."), f"charger_id is a dot segment: {segment!r}"
    assert ".." not in decoded.split("/"), f"dot segment survives decoding: {decoded}"


def _assert_rejected(seen, out, port=None, path=None):
    """No request left the process, and the caller got the structured error shape."""
    assert seen == [], f"a request was made: {[str(r.url) for r in seen]}"
    assert "error" in out
    if port is not None:
        assert out["port"] == port
    if path is not None:
        assert out["path"] == path


# --------------------------------------------------------------------------------------
# charger_id -- validated, not merely encoded
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("charger_id", [
    _EVIL_CHARGER_ID,
    _EVIL_CHARGER_ID_HIJACK,
    "CHG-1/proxy/8003/api/v1/pin/contactor/status?x=",
    "CHG-1?method=POST",
    "CHG-1#frag",
    "CHG-1&a=b",
    "CHG-1/x",
    "a%2Fb",
    "a b",
    "a:b",
    "a~b",
    "a\\b",
    ".",
    "..",
    "../..",
    "a/..",
    "x/../../y",
    "..%2f",
    "",
    "   ",
    "\t\n",
])
@pytest.mark.asyncio
async def test_unsafe_charger_ids_are_rejected_without_a_request(charger_id):
    seen, handler = _capturing()
    async with _client(handler) as c:
        out = await _proxy_get(c, charger_id, 8003, "api/v1/charging/status")
    _assert_rejected(seen, out, port=8003, path="api/v1/charging/status")


@pytest.mark.parametrize("charger_id", [
    "CHG-1", "QC_0012", "abc.def-9", "60f1a2b3c4d5e6f708192a3b", "OFFLINE", "..a", "a..",
])
@pytest.mark.asyncio
async def test_real_charger_ids_still_pass_unchanged(charger_id):
    seen, handler = _capturing()
    async with _client(handler) as c:
        out = await _proxy_get(c, charger_id, 8003, "api/v1/charging/status")
    assert out == {"ok": True}
    assert len(seen) == 1
    _assert_safe(seen[0])
    assert str(seen[0].url) == (
        f"http://noc.test/api/v1/chargers/{charger_id}/proxy/8003/api/v1/charging/status")


# --------------------------------------------------------------------------------------
# port
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("port", ["8003/api/v1/pin/contactor/dual?method=POST", "80x", None,
                                  "..", 0, 70000, True])
@pytest.mark.asyncio
async def test_non_numeric_or_out_of_range_port_is_rejected(port):
    seen, handler = _capturing()
    async with _client(handler) as c:
        out = await _proxy_get(c, "CHG-1", port, "api/v1/charging/status")
    _assert_rejected(seen, out)


@pytest.mark.asyncio
async def test_numeric_string_port_is_coerced_and_works():
    seen, handler = _capturing()
    async with _client(handler) as c:
        await _proxy_get(c, "CHG-1", "8003", "api/v1/charging/status")
    assert len(seen) == 1
    assert seen[0].url.raw_path.decode() == (
        "/api/v1/chargers/CHG-1/proxy/8003/api/v1/charging/status")


# --------------------------------------------------------------------------------------
# path
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    _EVIL_PATH_TRAVERSAL,
    "api/v1/devtools/logs/../../pin/contactor/dual",
    "../pin/contactor/dual",
    "api/./v1/charging/status",
    "api/v1/charging/status/..",
])
@pytest.mark.asyncio
async def test_dot_segment_paths_are_rejected_without_a_request(path):
    seen, handler = _capturing()
    async with _client(handler) as c:
        out = await _proxy_get(c, "CHG-1", 8005, path)
    _assert_rejected(seen, out, port=8005, path=path)


@pytest.mark.parametrize("path", [
    "api/v1/charging/status?method=POST",
    "api/v1/charging/status#frag",
    "api/v1/charging\\status",
    "api/v1/charging/status?x=1",
    "api/v1/devtools/logs/app%2F..%2F..%2Fpin",
    "api/v1/ charging/status",
    "",
    "   ",
    "/api/v1/charging/status",   # leading slash -> empty segment; callers pass none
    "api//v1/charging/status",
])
@pytest.mark.asyncio
async def test_structurally_unsafe_paths_are_rejected_without_a_request(path):
    seen, handler = _capturing()
    async with _client(handler) as c:
        out = await _proxy_get(c, "CHG-1", 8003, path)
    _assert_rejected(seen, out, port=8003, path=path)


@pytest.mark.parametrize("source", [
    "app", "ocpp", "syslog", "kern.log", "app-2026-09-01.log",
    # service/unit-style names the widened allowlist deliberately admits
    "qflex-charging.service", "qflex@1.service", "app:main", "a+b",
])
@pytest.mark.asyncio
async def test_plausible_log_source_names_are_not_rejected(source):
    seen, handler = _capturing()
    async with _client(handler) as c:
        out = await _proxy_get(c, "CHG-1", 8005, f"api/v1/devtools/logs/{source}")
    assert out == {"ok": True}, f"log source {source!r} was rejected"
    assert len(seen) == 1
    _assert_safe(seen[0])


# --------------------------------------------------------------------------------------
# legitimate behaviour is byte-identical to before the hardening
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("port,path", [
    (8003, "api/v1/charging/status"),
    (8003, "api/v1/charging/status/1"),
    (8003, "api/v1/session_details/active/1"),
    (8003, "api/v1/session_details/history"),
    (8003, "api/v1/devices/meters"),
    (8003, "api/v1/devices/imd/1"),
    (8003, "api/v1/ocpp/status"),
    (8003, "api/v1/config/ocpp"),
    (8000, "api/v1/network/status"),
    (8000, "api/v1/network/gsm/status"),
    (8000, "api/v1/system/hardware"),
    (8000, "api/v1/system/software"),
    (8000, "api/v1/system/rtc"),
    (8002, "api/state"),
    (8002, "api/pm_count"),
    (8002, "api/pm/get_all_power"),
    (8006, "error-generator/alarms/latched"),
    (8006, "error-generator/errors/pending"),
    (8006, "error-generator/errors/by-gun/1"),
    (8006, "temp/sensors"),
    (8006, "temp/thresholds"),
    (8005, "api/v1/devtools/logs/sources"),
    (8005, "api/v1/devtools/logs/app"),
    (8005, "api/v1/devtools/metrics"),
])
@pytest.mark.asyncio
async def test_every_curated_url_is_unchanged(port, path):
    """Covers every (port, path) the 13 curated tools can emit -- byte-identical URLs."""
    seen, handler = _capturing()
    async with _client(handler) as c:
        out = await _proxy_get(c, "CHG-1", port, path)
    assert out == {"ok": True}
    assert len(seen) == 1
    assert str(seen[0].url) == f"http://noc.test/api/v1/chargers/CHG-1/proxy/{port}/{path}"


# --------------------------------------------------------------------------------------
# end-to-end: no curated tool can be steered off its intended endpoint
# --------------------------------------------------------------------------------------

_CURATED_CALLS = [
    ("charger_get_charging_status", {}),
    ("charger_get_charging_status", {"gun_id": 1}),
    ("charger_get_session_details", {}),
    ("charger_get_session_details", {"gun_id": 1}),
    ("charger_get_meters", {}),
    ("charger_get_meters", {"gun_id": 1}),
    ("charger_get_ocpp_status", {}),
    ("charger_get_network_status", {}),
    ("charger_get_system_info", {}),
    ("charger_get_allocation_state", {}),
    ("charger_get_pm_power", {}),
    ("charger_get_alarms", {}),
    ("charger_get_alarms", {"gun_id": 1}),
    ("charger_get_temperature", {}),
    ("charger_list_log_sources", {}),
    ("charger_get_logs", {"source": "app"}),
    ("charger_get_device_metrics", {}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("evil_id", [_EVIL_CHARGER_ID, _EVIL_CHARGER_ID_HIJACK])
@pytest.mark.parametrize("tool_name,extra", _CURATED_CALLS)
async def test_no_curated_tool_can_be_steered_by_charger_id(tool_name, extra, evil_id):
    seen, handler = _capturing()
    async with Client(build_server(_client(handler))) as mcp:
        await mcp.call_tool(tool_name, {"charger_id": evil_id, **extra})
    assert seen == [], (
        f"{tool_name} sent a request for a rejected charger_id: {[str(r.url) for r in seen]}")


@pytest.mark.asyncio
async def test_charger_get_logs_source_cannot_traverse_to_a_write_endpoint():
    seen, handler = _capturing()
    evil_sources = [
        "../../../../" + _WRITE_PATH + "?method=POST",
        "..%2F..%2Fpin%2Fcontactor%2Fdual",
        "app?method=POST",
        "app/../../../pin/contactor/dual",
        "app#x",
    ]
    async with Client(build_server(_client(handler))) as mcp:
        for source in evil_sources:
            await mcp.call_tool(
                "charger_get_logs", {"charger_id": "CHG-1", "source": source})
    assert seen == [], f"a request was made: {[str(r.url) for r in seen]}"


@pytest.mark.asyncio
async def test_direct_proxy_get_cannot_reach_the_write_endpoint():
    """Every confirmed reproduction payload, asserted on the decoded view."""
    seen, handler = _capturing()
    async with _client(handler) as c:
        for cid, port, path in [
            (_EVIL_CHARGER_ID, 8003, "api/v1/charging/status"),
            (_EVIL_CHARGER_ID_HIJACK, 8003, "api/v1/charging/status"),
            ("CHG-1", 8005, _EVIL_PATH_TRAVERSAL),
            ("CHG-1", 8003, _EVIL_PATH_TRAVERSAL),
        ]:
            out = await _proxy_get(c, cid, port, path)
            assert "error" in out
    assert seen == [], f"a request was made: {[str(r.url) for r in seen]}"
