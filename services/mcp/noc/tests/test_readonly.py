"""Tests for read-only charger-proxy enforcement (noc_mcp.readonly)."""

from types import SimpleNamespace

import pytest
from fastmcp.exceptions import ToolError

from noc_mcp.readonly import ReadOnlyProxyMiddleware, _blocked_method

# A representative exposed proxy tool name (path-based GET variant).
#
# This is the name FastMCP actually registers, verified against list_tools() by
# test_the_proxy_tool_name_used_here_is_real below. The literal that used to sit here --
# "..._api_v1_chargers__charger_id__proxy__port___path__get" -- named no tool at all: FastMCP
# truncates generated names, and nothing checked. _blocked_method only substring-matches
# "proxy", so the fiction went unnoticed here while the same dead string in noc_mcp/catalog.py
# was telling the model to call a tool that does not exist.
PROXY = "proxy_api_call_path_based_api_v1_chargers"


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "post", "Delete", "", "   ", 123])
def test_blocks_non_get_method_on_proxy(method):
    assert _blocked_method(PROXY, {"method": method}) is not None


@pytest.mark.parametrize("method", ["GET", "get", " GET "])
def test_allows_get_method_on_proxy(method):
    assert _blocked_method(PROXY, {"method": method}) is None


def test_allows_absent_method_on_proxy():
    assert _blocked_method(PROXY, {"charger_id": "CHG-1", "port": 8003, "path": "x"}) is None


def test_allows_explicit_none_method_on_proxy():
    assert _blocked_method(PROXY, {"method": None}) is None


def test_ignores_non_proxy_tools_even_with_method():
    assert _blocked_method("dashboard_summary_api_v1_dashboard_summary_get", {"method": "POST"}) is None


# --- charger-reaching tools without "proxy" in the name -------------------------------
# The curated control-center tools reach a live charger through the same NOC proxy route,
# but none of their names contains "proxy". A name-substring test therefore exempted all
# 13 of them; coverage must follow "can this tool reach a charger", not the tool's name.

CURATED = [
    "charger_get_charging_status", "charger_get_session_details", "charger_get_meters",
    "charger_get_ocpp_status", "charger_get_network_status", "charger_get_system_info",
    "charger_get_allocation_state", "charger_get_pm_power", "charger_get_alarms",
    "charger_get_temperature", "charger_list_log_sources", "charger_get_logs",
    "charger_get_device_metrics",
]


@pytest.mark.parametrize("tool_name", CURATED)
def test_blocks_non_get_method_on_curated_charger_tools(tool_name):
    assert _blocked_method(tool_name, {"charger_id": "CHG-1", "method": "POST"}) is not None


@pytest.mark.parametrize("tool_name", CURATED)
def test_allows_get_method_on_curated_charger_tools(tool_name):
    assert _blocked_method(tool_name, {"charger_id": "CHG-1", "method": "GET"}) is None
    assert _blocked_method(tool_name, {"charger_id": "CHG-1"}) is None


def test_blocks_a_hypothetical_14th_charger_tool():
    # A tool added later with neither "proxy" nor "charger" in its name is still covered
    # because it takes a charger_id -- i.e. it can reach a charger.
    assert _blocked_method("gun_diagnostics_snapshot",
                           {"charger_id": "CHG-1", "method": "DELETE"}) is not None


def test_blocks_ssh_tools_with_non_get_method():
    assert _blocked_method("charger_ssh_run_command",
                           {"charger_id": "CHG-1", "method": "POST"}) is not None


@pytest.mark.asyncio
async def test_the_proxy_tool_name_used_here_is_real():
    """The PROXY constant above must name a tool the server actually registers."""
    from tests.fake_noc import build_test_server

    names = {t.name for t in await build_test_server().list_tools()}
    assert PROXY in names, f"{PROXY} is not registered; nearest: {sorted(n for n in names if 'proxy' in n)}"


def _ctx(name, arguments):
    """Minimal stand-in for a MiddlewareContext[CallToolRequestParams]."""
    return SimpleNamespace(message=SimpleNamespace(name=name, arguments=arguments))


@pytest.mark.asyncio
async def test_middleware_rejects_non_get_proxy_call():
    mw = ReadOnlyProxyMiddleware()
    called = False

    async def call_next(ctx):
        nonlocal called
        called = True
        return "ok"

    with pytest.raises(ToolError):
        await mw.on_call_tool(_ctx(PROXY, {"method": "POST"}), call_next)
    assert called is False  # NOC is never contacted for a blocked call


@pytest.mark.asyncio
async def test_middleware_allows_get_proxy_call():
    mw = ReadOnlyProxyMiddleware()

    async def call_next(ctx):
        return "ok"

    result = await mw.on_call_tool(_ctx(PROXY, {"method": "GET"}), call_next)
    assert result == "ok"


@pytest.mark.asyncio
async def test_middleware_allows_absent_method():
    mw = ReadOnlyProxyMiddleware()

    async def call_next(ctx):
        return "ok"

    result = await mw.on_call_tool(_ctx(PROXY, {"charger_id": "CHG-1"}), call_next)
    assert result == "ok"


def test_build_server_registers_readonly_middleware(monkeypatch):
    # build_server() -> config.Settings() reads env at import; set creds first.
    monkeypatch.setenv("NOC_EMAIL", "svc@noc.test")
    monkeypatch.setenv("NOC_PASSWORD", "secret")

    from noc_mcp.server import build_server  # deferred import (needs env above)

    mcp = build_server()
    assert any(isinstance(m, ReadOnlyProxyMiddleware) for m in mcp.middleware)


@pytest.mark.asyncio
async def test_every_tool_that_accepts_a_method_arg_is_covered():
    """Spec-driven: no tool may expose a ``method`` argument the guard does not inspect.

    This is the invariant the old ``"proxy" in tool_name`` check could not express. It is
    checked against the tools the server actually registers, so a future spec regeneration or
    a new hand-written tool that exposes ``method`` fails here instead of shipping exempt.
    """
    from fastmcp import Client

    from tests.fake_noc import build_test_server

    async with Client(build_test_server()) as client:
        tools = await client.list_tools()

    with_method = [t for t in tools
                   if "method" in ((t.inputSchema or {}).get("properties") or {})]
    assert with_method, "expected the charger-proxy tools to expose a method argument"
    for tool in with_method:
        assert _blocked_method(tool.name, {"method": "POST"}) is not None, (
            f"{tool.name} exposes a 'method' argument but is not guarded")
