"""Tests for read-only charger-proxy enforcement (noc_mcp.readonly)."""

import pytest

from noc_mcp.readonly import _blocked_method

# A representative exposed proxy tool name (path-based GET variant).
PROXY = "proxy_api_call_path_based_api_v1_chargers__charger_id__proxy__port___path__get"


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


from types import SimpleNamespace

from fastmcp.exceptions import ToolError

from noc_mcp.readonly import ReadOnlyProxyMiddleware


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
