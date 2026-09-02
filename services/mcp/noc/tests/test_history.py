"""Tests for the per-charger history composites (noc_mcp.history)."""

from datetime import datetime, timezone

import httpx
import pytest

from noc_mcp.auth import NocAuthClient
from noc_mcp.history import (
    _fetch_charger_history,
    _fetch_charger_ocpp_config,
    _fetch_charger_stability,
    _fetch_charger_telemetry,
    _fetch_session_events,
)
from tests.fake_noc import build_test_client


def _client_with_handler(handler) -> NocAuthClient:
    """A bare NocAuthClient wired to a one-off httpx.MockTransport handler."""
    return NocAuthClient(base_url="http://noc.test", email="e", password="p",
                         transport=httpx.MockTransport(handler))


def _login_or(path_map):
    """Build a MockTransport handler: login always succeeds; other paths come from path_map.

    Each path_map value is either an (status, json) tuple or a callable returning one.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/api/v1/auth/login"):
            return httpx.Response(200, json={"access_token": "t.t.t", "token_type": "bearer"})
        if path in path_map:
            status, body = path_map[path]
            return httpx.Response(status, json=body)
        return httpx.Response(404, json={"detail": "unhandled"})
    return handler


@pytest.mark.asyncio
async def test_history_merges_sessions_and_alarms():
    async with build_test_client() as c:
        out = await _fetch_charger_history(c, "CHG-1", limit=None)
    assert out["source"] == "history"
    assert out["sessions"]["sessions"][0]["session_id"] == "S0"
    assert out["alarms"]["alarms"][0]["code"] == "E12"


@pytest.mark.asyncio
async def test_history_works_for_an_offline_charger():
    async with build_test_client() as c:
        out = await _fetch_charger_history(c, "OFFLINE", limit=None)
    assert out["source"] == "history"
    assert out["sessions"]["sessions"][0]["session_id"] == "S-old"


@pytest.mark.asyncio
async def test_telemetry_merges_three_views():
    async with build_test_client() as c:
        out = await _fetch_charger_telemetry(c, "CHG-1", resolution=None, hours=None,
                                             from_ts=None, to_ts=None, limit=None)
    assert out["latest"]["kw"] == 42
    assert out["aggregated"]["buckets"][0]["kw"] == 40
    assert out["history"]["points"][0]["kw"] == 41


@pytest.mark.asyncio
async def test_session_events_returns_lifecycle():
    async with build_test_client() as c:
        out = await _fetch_session_events(c, "CHG-1", "S1")
    assert out["events"][0]["type"] == "SESSION_START"


@pytest.mark.asyncio
async def test_stability_merges_connection_and_command_history():
    async with build_test_client() as c:
        out = await _fetch_charger_stability(c, "CHG-1")
    assert out["stability"]["reconnects_24h"] == 4
    assert out["commands"]["commands"][0]["status"] == "done"
    assert out["ssh"]["connected"] is True


@pytest.mark.asyncio
async def test_ocpp_config_prefers_live_device():
    async with build_test_client() as c:
        out = await _fetch_charger_ocpp_config(c, "CHG-1")
    assert out["source"] == "live"
    assert out["HeartbeatInterval"] == "300"


@pytest.mark.asyncio
async def test_ocpp_config_falls_back_to_cache_when_offline():
    async with build_test_client() as c:
        out = await _fetch_charger_ocpp_config(c, "OFFLINE")
    assert out["source"] == "history"
    assert out["HeartbeatInterval"] == "60"
    assert "offline" in out["reason"].lower()
    assert out["stale_seconds"] is not None


@pytest.mark.asyncio
async def test_ocpp_config_no_cache_is_not_an_error():
    async with build_test_client() as c:
        out = await _fetch_charger_ocpp_config(c, "NOCACHE")
    assert out["source"] == "history"
    assert out["as_of"] is None
    assert "no cached" in out["reason"].lower()
    assert "error" not in out


@pytest.mark.asyncio
async def test_history_as_of_uses_oldest_sub_payload_not_first():
    """sessions is fresher (06:00) than alarms (04:00, two hours older).

    The composite's as_of must reflect the OLDER of the two -- reporting sessions' fresher
    timestamp would let the older alarms data hide behind a newer-looking envelope. This is
    the case that distinguishes `_oldest(sessions, alarms)` from the buggy
    `extract_as_of(sessions) or extract_as_of(alarms)`, which always picks sessions whenever
    it carries a timestamp at all, regardless of which sub-payload is actually older.
    """
    older_iso = "2026-09-01T04:00:00Z"
    handler = _login_or({
        "/api/v1/chargers/CHG-1/history/sessions": (
            200, {"sessions": [{"session_id": "S0"}], "as_of": "2026-09-01T06:00:00Z"}),
        "/api/v1/chargers/CHG-1/history/alarms": (
            200, {"alarms": [{"code": "E12"}], "as_of": older_iso}),
    })
    async with _client_with_handler(handler) as c:
        out = await _fetch_charger_history(c, "CHG-1", limit=None)

    assert out["as_of"] == older_iso
    older_dt = datetime(2026, 9, 1, 4, 0, 0, tzinfo=timezone.utc)
    expected_stale = int((datetime.now(timezone.utc) - older_dt).total_seconds())
    assert abs(out["stale_seconds"] - expected_stale) < 5


@pytest.mark.asyncio
async def test_history_asymmetric_sub_call_failure_keeps_the_good_half():
    """One sub-call 500s, the other succeeds: the composite must return both -- the good data
    intact and the bad one as a nested error -- rather than failing the whole tool call."""
    handler = _login_or({
        "/api/v1/chargers/CHG-1/history/sessions": (
            200, {"sessions": [{"session_id": "S0"}], "as_of": "2026-09-01T06:00:00Z"}),
        "/api/v1/chargers/CHG-1/history/alarms": (500, {"detail": "internal error"}),
    })
    async with _client_with_handler(handler) as c:
        out = await _fetch_charger_history(c, "CHG-1", limit=None)

    assert out["source"] == "history"
    assert "error" not in out
    assert out["sessions"]["sessions"][0]["session_id"] == "S0"
    assert "error" in out["alarms"]
