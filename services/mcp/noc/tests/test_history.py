"""Tests for the per-charger history composites (noc_mcp.history)."""

import pytest

from noc_mcp.history import (
    _fetch_charger_history,
    _fetch_charger_ocpp_config,
    _fetch_charger_stability,
    _fetch_charger_telemetry,
    _fetch_session_events,
)
from tests.fake_noc import build_test_client


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
