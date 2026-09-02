"""Receiver-model tests: assert on what the NOC backend PARSES, not on the client's wire.

Percent-encoding on the wire is not, by itself, a defence against a receiver that decodes
before it routes -- and FastAPI/Starlette do exactly that. A test oracle that inspects the
encoded request therefore cannot see a route re-split at all: every ``/`` is ``%2F`` and every
``?`` is ``%3F``, so naive "no slash / no query" assertions pass while the receiver happily
resolves a caller-chosen port and on-charger path.

These tests close that blind spot by routing the real outgoing request through a real
Starlette router built from the exact path templates in ``noc_mcp/openapi/noc_openapi.json``::

    /api/v1/chargers/{charger_id}/proxy/{port}/{path}
    /api/v1/chargers/{charger_id}/proxy

and asserting on ``request.path_params`` -- the values the backend would actually use to pick
a charger, a port, and an on-charger endpoint.

Historic failure this pins (arbitrary-port / arbitrary-path GET SSRF)::

    charger_id = "CHG-1/proxy/8005/api/v1/devtools/logs/x"
    SERVER PARSED charger_id = 'CHG-1'
    SERVER PARSED port       = '8005'   <-- caller-chosen, not the 8003 the tool passed
    SERVER PARSED path       = 'api/v1/devtools/logs/x/proxy/8003/api/v1/charging/status'
"""

import httpx
import pytest
from fastmcp import Client
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from noc_mcp.auth import NocAuthClient
from noc_mcp.control_center import _proxy_get
from noc_mcp.server import build_server

# Exact templates from noc_openapi.json (see module docstring).
_PROXY_PATH_TEMPLATE = "/api/v1/chargers/{charger_id}/proxy/{port}/{path:path}"
_PROXY_TEMPLATE = "/api/v1/chargers/{charger_id}/proxy"

_WRITE_PATH = "api/v1/pin/contactor/dual"
_EVIL_HIJACK = "CHG-1/proxy/8005/api/v1/devtools/logs/x"
_EVIL_CONTACTOR = f"CHG-1/proxy/8003/{_WRITE_PATH}?method=POST&x="


def _receiver():
    """Return ``(parsed, transport)`` where parsed collects what the router resolved."""
    parsed: list[dict] = []

    async def proxy_endpoint(request):
        parsed.append({
            "path_params": dict(request.path_params),
            "query_params": dict(request.query_params),
            "method": request.method,
        })
        return JSONResponse({"ok": True})

    async def login(request):
        return JSONResponse({"access_token": "t.t.t", "token_type": "bearer"})

    app = Starlette(routes=[
        Route(_PROXY_PATH_TEMPLATE, proxy_endpoint),
        Route(_PROXY_TEMPLATE, proxy_endpoint),
        Route("/api/v1/auth/login", login, methods=["POST"]),
    ])
    return parsed, httpx.ASGITransport(app=app)


def _client(transport):
    return NocAuthClient(base_url="http://noc.test", email="e", password="p",
                         transport=transport)


def _assert_parsed_safe(entry: dict, expected_port: int, expected_path: str) -> None:
    """The backend must resolve exactly the charger, port and path the tool intended."""
    pp = entry["path_params"]
    assert entry["method"] == "GET"
    assert entry["query_params"] == {}, f"NOC query params smuggled: {entry['query_params']}"
    assert str(pp["port"]) == str(expected_port), (
        f"port hijacked: tool passed {expected_port}, backend parsed {pp['port']!r}")
    assert pp["path"] == expected_path, (
        f"on-charger path hijacked: tool passed {expected_path!r}, "
        f"backend parsed {pp['path']!r}")
    assert "/" not in pp["charger_id"], (
        f"charger_id escaped its segment: {pp['charger_id']!r}")


@pytest.mark.asyncio
async def test_baseline_legitimate_call_parses_as_intended():
    parsed, transport = _receiver()
    async with _client(transport) as c:
        out = await _proxy_get(c, "CHG-1", 8003, "api/v1/charging/status")
    assert out == {"ok": True}
    assert len(parsed) == 1
    assert parsed[0]["path_params"] == {
        "charger_id": "CHG-1", "port": "8003", "path": "api/v1/charging/status"}
    _assert_parsed_safe(parsed[0], 8003, "api/v1/charging/status")


@pytest.mark.parametrize("charger_id", [
    _EVIL_HIJACK,                                     # the exact reported payload
    _EVIL_CONTACTOR,
    "CHG-1/proxy/8003/api/v1/pin/contactor/status?x=",  # clean device path via '?' truncation
    "CHG-1%2Fproxy%2F8005%2Fapi%2Fv1%2Fdevtools%2Flogs%2Fx",  # pre-encoded by the caller
    "CHG-1/proxy/8005/api/v1/devtools/logs/x#",
    "CHG-1/../CHG-2",
    "..",
])
@pytest.mark.asyncio
async def test_charger_id_cannot_re_split_the_route(charger_id):
    """The receiver must never resolve a port or path the tool did not choose."""
    parsed, transport = _receiver()
    async with _client(transport) as c:
        out = await _proxy_get(c, charger_id, 8003, "api/v1/charging/status")
    assert parsed == [], (
        f"backend resolved a request for a rejected charger_id: {parsed}")
    assert "error" in out


@pytest.mark.parametrize("path", [
    f"api/v1/devtools/logs/../../../../{_WRITE_PATH}?method=POST",
    f"api/v1/devtools/logs/../../{_WRITE_PATH}",
    "api/v1/charging/status?method=POST",
])
@pytest.mark.asyncio
async def test_path_cannot_re_split_the_route(path):
    parsed, transport = _receiver()
    async with _client(transport) as c:
        out = await _proxy_get(c, "CHG-1", 8005, path)
    assert parsed == [], f"backend resolved a hostile path: {parsed}"
    assert "error" in out


@pytest.mark.parametrize("source", ["app", "ocpp", "kern.log", "qflex-charging.service"])
@pytest.mark.asyncio
async def test_legitimate_log_sources_parse_as_intended(source):
    """Widening the path allowlist must not let a source alter routing."""
    parsed, transport = _receiver()
    async with _client(transport) as c:
        out = await _proxy_get(c, "CHG-1", 8005, f"api/v1/devtools/logs/{source}")
    assert out == {"ok": True}
    assert len(parsed) == 1
    _assert_parsed_safe(parsed[0], 8005, f"api/v1/devtools/logs/{source}")


# --------------------------------------------------------------------------------------
# end-to-end through the registered MCP tools
# --------------------------------------------------------------------------------------

_TOOL_EXPECTATIONS = [
    ("charger_get_charging_status", {}, 8003, "api/v1/charging/status"),
    ("charger_get_allocation_state", {}, 8002, "api/state"),
    ("charger_get_device_metrics", {}, 8005, "api/v1/devtools/metrics"),
    ("charger_list_log_sources", {}, 8005, "api/v1/devtools/logs/sources"),
    ("charger_get_logs", {"source": "app"}, 8005, "api/v1/devtools/logs/app"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name,extra,port,path", _TOOL_EXPECTATIONS)
async def test_tools_resolve_to_their_intended_endpoint(tool_name, extra, port, path):
    parsed, transport = _receiver()
    async with Client(build_server(_client(transport))) as mcp:
        await mcp.call_tool(tool_name, {"charger_id": "CHG-1", **extra})
    assert parsed, f"{tool_name} made no request"
    _assert_parsed_safe(parsed[0], port, path)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name,extra,port,path", _TOOL_EXPECTATIONS)
async def test_tools_cannot_be_steered_by_charger_id_at_the_receiver(
        tool_name, extra, port, path):
    parsed, transport = _receiver()
    async with Client(build_server(_client(transport))) as mcp:
        await mcp.call_tool(tool_name, {"charger_id": _EVIL_HIJACK, **extra})
    assert parsed == [], f"{tool_name} let the backend resolve a hijacked route: {parsed}"


@pytest.mark.asyncio
async def test_log_source_cannot_re_split_the_route_at_the_receiver():
    parsed, transport = _receiver()
    async with Client(build_server(_client(transport))) as mcp:
        for source in [f"../../../../{_WRITE_PATH}?method=POST", "app?method=POST",
                       "../../../../../proxy/8003/" + _WRITE_PATH]:
            await mcp.call_tool("charger_get_logs",
                                {"charger_id": "CHG-1", "source": source})
    assert parsed == [], f"backend resolved a hijacked route: {parsed}"


# --------------------------------------------------------------------------------------
# KNOWN, PRE-EXISTING GAP -- documented here so it cannot be lost
# --------------------------------------------------------------------------------------

@pytest.mark.xfail(
    strict=True,
    reason="PRE-EXISTING and OUT OF SCOPE for the history hardening: the FastMCP-generated "
           "OpenAPI tools build their own URLs and percent-encode charger_id rather than "
           "validating it, so a decoding receiver still re-splits the route. GET-only (no "
           "verb override: the smuggled '?' stays inside the path, so 'method' never becomes "
           "a real query parameter), and present before this branch. Closing it means "
           "validating identifiers for the ~18 generated tools too -- a deliberate decision "
           "for a human, not a silent side effect of this fix. When it is closed this test "
           "XPASSes and fails, which is the point.")
@pytest.mark.asyncio
async def test_generated_openapi_tools_should_reject_a_hostile_charger_id():
    """The generated path-based charger-proxy tool should be as fail-closed as the curated ones.

    Today it is not::

        proxy_api_call_path_based_api_v1_chargers(charger_id=_EVIL_HIJACK, port=8003,
                                                  path="api/v1/charging/status")
        SERVER PARSED charger_id = 'CHG-1'
        SERVER PARSED port       = '8005'   <-- caller-chosen
        SERVER PARSED path       = 'api/v1/devtools/logs/x/proxy/8003/api/v1/charging/status'
    """
    parsed, transport = _receiver()
    async with Client(build_server(_client(transport))) as mcp:
        await mcp.call_tool("proxy_api_call_path_based_api_v1_chargers",
                            {"charger_id": _EVIL_HIJACK, "port": 8003,
                             "path": "api/v1/charging/status"})
    assert parsed == [], f"backend resolved a hijacked route: {parsed}"


@pytest.mark.asyncio
async def test_the_generated_gap_is_at_least_read_only():
    """Whatever else it allows, the generated tool must not smuggle a verb override.

    This is the invariant that separates the pre-existing GET-SSRF above from the regression
    this branch fixed: a '?' carried inside charger_id reaches the wire percent-encoded, so it
    stays part of the path and never becomes the ``method`` query parameter the NOC backend
    forwards as an HTTP verb to live hardware.
    """
    parsed, transport = _receiver()
    async with Client(build_server(_client(transport))) as mcp:
        await mcp.call_tool("proxy_api_call_path_based_api_v1_chargers",
                            {"charger_id": _EVIL_CONTACTOR, "port": 8003,
                             "path": "api/v1/charging/status"})
    for entry in parsed:
        assert entry["method"] == "GET"
        assert "method" not in entry["query_params"], (
            f"verb override reached the receiver: {entry['query_params']}")
