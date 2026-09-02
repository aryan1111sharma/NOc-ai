"""Tests for the reports/stats dispatchers (noc_mcp.reports)."""

import httpx
import pytest

from noc_mcp.auth import NocAuthClient
from noc_mcp.reports import REPORTS, STATS, _fetch_report, _fetch_stats

_LOGIN = "/api/v1/auth/login"


def _client(handler):
    return NocAuthClient(base_url="http://noc.test", email="e", password="p",
                         transport=httpx.MockTransport(handler))


def _ok_login(request):
    return httpx.Response(200, json={"access_token": "t.t.t", "token_type": "bearer"})


def test_every_report_name_has_a_description():
    assert len(REPORTS) == 13
    assert all(isinstance(v, str) and v for v in REPORTS.values())


def test_every_stats_name_has_a_description():
    assert len(STATS) == 11
    assert all(isinstance(v, str) and v for v in STATS.values())


@pytest.mark.asyncio
async def test_report_routes_to_the_right_path():
    seen = {}

    def handler(request):
        if request.url.path.endswith(_LOGIN):
            return _ok_login(request)
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"rows": []})

    async with _client(handler) as c:
        await _fetch_report(c, "session-failures", hours=24, limit=None,
                            customer_name=None, charger_id=None, from_ts=None, to_ts=None)
    assert seen["path"] == "/api/v1/reports/session-failures"
    assert seen["params"] == {"hours": "24"}


@pytest.mark.asyncio
async def test_report_omits_unset_filters():
    seen = {}

    def handler(request):
        if request.url.path.endswith(_LOGIN):
            return _ok_login(request)
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"rows": []})

    async with _client(handler) as c:
        await _fetch_report(c, "warranty-summary", hours=None, limit=None,
                            customer_name=None, charger_id=None, from_ts=None, to_ts=None)
    assert seen["params"] == {}


@pytest.mark.asyncio
async def test_unknown_report_returns_the_enum_not_an_exception():
    async with _client(_ok_login) as c:
        out = await _fetch_report(c, "no-such-report", hours=None, limit=None,
                                  customer_name=None, charger_id=None, from_ts=None, to_ts=None)
    assert "error" in out
    assert "session-failures" in out["valid_reports"]


@pytest.mark.asyncio
async def test_stats_routes_to_the_right_path():
    seen = {}

    def handler(request):
        if request.url.path.endswith(_LOGIN):
            return _ok_login(request)
        seen["path"] = request.url.path
        return httpx.Response(200, json={"buckets": []})

    async with _client(handler) as c:
        await _fetch_stats(c, "sessions/failed", hours=None, limit=None,
                           customer_name=None, charger_id=None, from_ts=None, to_ts=None)
    assert seen["path"] == "/api/v1/stats/sessions/failed"


@pytest.mark.asyncio
async def test_report_stamps_history_source():
    def handler(request):
        if request.url.path.endswith(_LOGIN):
            return _ok_login(request)
        return httpx.Response(200, json={"rows": []})

    async with _client(handler) as c:
        out = await _fetch_report(c, "charger-energy", hours=None, limit=None,
                                  customer_name=None, charger_id=None, from_ts=None, to_ts=None)
    assert out["source"] == "history"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name,needle", [
    ("noc_report", "session-failures"),
    ("noc_stats", "error-trends"),
])
async def test_dispatcher_descriptions_list_their_enum(tool_name, needle):
    """The enum listing IS the discoverability for a dispatcher.

    Guards a specific trap: an f-string docstring is not a string literal, so Python sets
    __doc__ to None and the tool registers with no description at all -- silently, since
    registration still succeeds. Descriptions must be passed via @mcp.tool(description=...).
    """
    from tests.fake_noc import build_test_server

    tool = next(t for t in await build_test_server().list_tools() if t.name == tool_name)
    assert tool.description, f"{tool_name} registered with no description"
    assert needle in tool.description
