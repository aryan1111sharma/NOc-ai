"""Security regression tests for charger-proxy URL construction (noc_mcp.control_center).

Separate from test_control_center.py (which covers behaviour) because these assert a security
invariant with its own fixture shape: a transport that *captures* outgoing requests so we can
assert on the wire, not on the return value. Keeping them in one named file makes the
invariant easy to find, easy to run in isolation, and hard to delete by accident.

The invariant: no model-controlled string reaching ``_proxy_get`` may alter the *structure* of
the outgoing URL — it may not add a query parameter (the NOC charger-proxy route honours
``?method=`` and will forward that verb to live charger hardware), and it may not escape its
path segment. Reaching ``POST /api/v1/pin/contactor/dual`` on port 8003 (catalog: "Dual
Contactor Control", admin) actuates contactors on live high-voltage EV charging hardware.
"""

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

# The two confirmed attack payloads.
_EVIL_CHARGER_ID = f"CHG-1/proxy/{_WRITE_PORT}/{_WRITE_PATH}?method=POST&x="
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
    """Every outgoing charger-proxy request must be a GET that cannot actuate hardware."""
    assert request.method == "GET"
    # url.params: the NOC proxy route reads ?method= and forwards THAT verb to the charger.
    assert "method" not in request.url.params, f"method query smuggled: {request.url}"
    # raw_path is the un-decoded path+query; url.path percent-decodes %2F back into '/',
    # which would mask exactly the escape we are testing for.
    raw = request.url.raw_path.decode()
    assert "?" not in raw, f"query string smuggled: {raw}"
    assert _WRITE_PATH not in raw, f"reached a write endpoint: {raw}"


# --------------------------------------------------------------------------------------
# charger_id
# --------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_charger_id_cannot_smuggle_method_query():
    seen, handler = _capturing()
    async with _client(handler) as c:
        await _proxy_get(c, _EVIL_CHARGER_ID, 8003, "api/v1/charging/status")
    for r in seen:
        _assert_safe(r)
    # Whatever went out kept the intended endpoint as its real tail.
    for r in seen:
        assert r.url.raw_path.decode().endswith("/proxy/8003/api/v1/charging/status")


@pytest.mark.parametrize("evil", ["?", "&", "#", "/", "?method=POST", "a/b", "x#frag"])
@pytest.mark.asyncio
async def test_charger_id_reserved_chars_are_encoded_not_interpreted(evil):
    seen, handler = _capturing()
    charger_id = f"CHG{evil}1"
    async with _client(handler) as c:
        await _proxy_get(c, charger_id, 8003, "api/v1/charging/status")
    assert len(seen) == 1
    raw = seen[0].url.raw_path.decode()
    _assert_safe(seen[0])
    # No reserved character survives raw inside the charger_id segment.
    segment = raw.split("/api/v1/chargers/", 1)[1].split("/proxy/", 1)[0]
    for ch in "?&#/":
        assert ch not in segment, f"{ch!r} not encoded in {segment!r}"
    assert raw.endswith("/proxy/8003/api/v1/charging/status")


@pytest.mark.parametrize("charger_id", ["", "   ", "\t\n"])
@pytest.mark.asyncio
async def test_empty_charger_id_is_rejected_without_a_request(charger_id):
    seen, handler = _capturing()
    async with _client(handler) as c:
        out = await _proxy_get(c, charger_id, 8003, "api/v1/charging/status")
    assert seen == []
    assert "error" in out and out["port"] == 8003 and out["path"] == "api/v1/charging/status"


# --------------------------------------------------------------------------------------
# port
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("port", ["8003/api/v1/pin/contactor/dual?method=POST", "80x", None,
                                  "..", 0, 70000])
@pytest.mark.asyncio
async def test_non_numeric_or_out_of_range_port_is_rejected(port):
    seen, handler = _capturing()
    async with _client(handler) as c:
        out = await _proxy_get(c, "CHG-1", port, "api/v1/charging/status")
    assert seen == []
    assert "error" in out


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
    assert seen == [], f"a request was made for {path!r}: {[str(r.url) for r in seen]}"
    assert "error" in out and out["port"] == 8005 and out["path"] == path


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
    assert seen == [], f"a request was made for {path!r}: {[str(r.url) for r in seen]}"
    assert "error" in out and out["port"] == 8003 and out["path"] == path


# --------------------------------------------------------------------------------------
# legitimate behaviour is unchanged
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("port,path", [
    (8003, "api/v1/charging/status"),
    (8003, "api/v1/charging/status/1"),
    (8003, "api/v1/session_details/active/1"),
    (8002, "api/pm/get_all_power"),
    (8006, "error-generator/errors/by-gun/1"),
    (8006, "temp/thresholds"),
    (8005, "api/v1/devtools/logs/app"),
    (8000, "api/v1/system/rtc"),
])
@pytest.mark.asyncio
async def test_legitimate_calls_produce_the_exact_same_url_as_before(port, path):
    seen, handler = _capturing()
    async with _client(handler) as c:
        out = await _proxy_get(c, "CHG-1", port, path)
    assert out == {"ok": True}
    assert len(seen) == 1
    assert str(seen[0].url) == f"http://noc.test/api/v1/chargers/CHG-1/proxy/{port}/{path}"


@pytest.mark.asyncio
async def test_realistic_charger_ids_are_not_mangled():
    # NOC charger ids seen in the wild: alphanumerics, dashes, underscores, dots.
    seen, handler = _capturing()
    async with _client(handler) as c:
        for cid in ["CHG-1", "QC_0012", "abc.def-9", "60f1a2b3c4d5e6f708192a3b"]:
            await _proxy_get(c, cid, 8003, "api/v1/charging/status")
    urls = [str(r.url) for r in seen]
    assert urls == [
        "http://noc.test/api/v1/chargers/CHG-1/proxy/8003/api/v1/charging/status",
        "http://noc.test/api/v1/chargers/QC_0012/proxy/8003/api/v1/charging/status",
        "http://noc.test/api/v1/chargers/abc.def-9/proxy/8003/api/v1/charging/status",
        "http://noc.test/api/v1/chargers/60f1a2b3c4d5e6f708192a3b/proxy/8003"
        "/api/v1/charging/status",
    ]


# --------------------------------------------------------------------------------------
# end-to-end: no curated tool can be steered onto a write endpoint
# --------------------------------------------------------------------------------------

# Every curated tool, with the hostile value in each of its free-form string arguments.
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
@pytest.mark.parametrize("tool_name,extra", _CURATED_CALLS)
async def test_no_curated_tool_can_be_steered_onto_a_write_endpoint(tool_name, extra):
    seen, handler = _capturing()
    async with Client(build_server(_client(handler))) as mcp:
        await mcp.call_tool(tool_name, {"charger_id": _EVIL_CHARGER_ID, **extra})
    assert seen, "expected at least the (harmless, encoded) request to be attempted"
    for r in seen:
        _assert_safe(r)


@pytest.mark.asyncio
async def test_charger_get_logs_source_cannot_traverse_to_a_write_endpoint():
    seen, handler = _capturing()
    evil_sources = [
        "../../../../" + _WRITE_PATH + "?method=POST",
        "..%2F..%2Fpin%2Fcontactor%2Fdual",
        "app?method=POST",
        "app/../../../pin/contactor/dual",
    ]
    async with Client(build_server(_client(handler))) as mcp:
        for source in evil_sources:
            await mcp.call_tool(
                "charger_get_logs", {"charger_id": "CHG-1", "source": source})
    for r in seen:
        _assert_safe(r)


@pytest.mark.asyncio
async def test_direct_proxy_get_cannot_reach_the_write_endpoint():
    """Both confirmed reproduction payloads, asserted on the wire."""
    seen, handler = _capturing()
    async with _client(handler) as c:
        await _proxy_get(c, _EVIL_CHARGER_ID, 8003, "api/v1/charging/status")
        await _proxy_get(c, "CHG-1", 8005, _EVIL_PATH_TRAVERSAL)
        # And the same traversal aimed at the port that actually hosts the endpoint.
        await _proxy_get(c, "CHG-1", 8003, _EVIL_PATH_TRAVERSAL)
    for r in seen:
        _assert_safe(r)
