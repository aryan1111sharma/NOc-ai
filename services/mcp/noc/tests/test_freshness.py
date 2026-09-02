"""Tests for the freshness envelope and live->history fallback (noc_mcp.freshness)."""

from datetime import datetime, timedelta, timezone

import pytest

from noc_mcp.freshness import HISTORY, LIVE, is_error, live_or_history, stamp


def test_stamp_live_sets_zero_staleness():
    out = stamp({"guns": []}, source=LIVE)
    assert out["source"] == "live"
    assert out["stale_seconds"] == 0
    assert out["reason"] is None
    assert out["as_of"].endswith("Z")
    assert out["guns"] == []


def test_stamp_history_computes_staleness_from_as_of():
    as_of = datetime.now(timezone.utc) - timedelta(seconds=600)
    out = stamp({"alarms": []}, source=HISTORY, as_of=as_of, reason="charger offline")
    assert out["source"] == "history"
    assert 595 <= out["stale_seconds"] <= 605
    assert out["reason"] == "charger offline"


def test_stamp_history_without_as_of_is_honest_about_unknown():
    out = stamp({"alarms": []}, source=HISTORY, reason="no cache")
    assert out["as_of"] is None
    assert out["stale_seconds"] is None


def test_stamp_does_not_overwrite_payload_keys():
    out = stamp({"source": "device-said-live"}, source=LIVE)
    assert out["source"] == "live"          # envelope wins
    assert out["payload_source"] == "device-said-live"


def test_is_error_detects_proxy_error_shape():
    assert is_error({"error": "HTTPStatusError: 502", "port": 8003, "path": "x"})
    assert not is_error({"guns": []})


@pytest.mark.asyncio
async def test_live_or_history_prefers_live():
    async def live():
        return {"v": 1}

    async def hist():
        raise AssertionError("history must not be called when live succeeds")

    out = await live_or_history(live, hist, reason="offline")
    assert out["source"] == "live"
    assert out["v"] == 1


@pytest.mark.asyncio
async def test_live_or_history_falls_back_on_error():
    async def live():
        return {"error": "boom", "port": 8003, "path": "x"}

    async def hist():
        return {"v": 2, "as_of": "2026-09-01T06:00:00Z"}

    out = await live_or_history(live, hist, reason="charger offline")
    assert out["source"] == "history"
    assert out["v"] == 2
    assert out["reason"] == "charger offline"
    assert out["stale_seconds"] is not None


@pytest.mark.asyncio
async def test_live_or_history_reports_both_failures():
    async def live():
        return {"error": "live-boom"}

    async def hist():
        return {"error": "hist-boom"}

    out = await live_or_history(live, hist, reason="offline")
    assert "error" in out
    assert out["live_error"] == "live-boom"
    assert out["history_error"] == "hist-boom"
