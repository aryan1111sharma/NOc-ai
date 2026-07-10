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
