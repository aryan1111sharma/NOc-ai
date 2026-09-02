"""Tests for the freshness envelope and live->history fallback (noc_mcp.freshness)."""

from datetime import datetime, timedelta, timezone

import pytest

from noc_mcp.freshness import HISTORY, LIVE, extract_as_of, is_error, live_or_history, stamp


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
    assert 599 <= out["stale_seconds"] <= 602
    assert out["reason"] == "charger offline"


def test_stamp_history_without_as_of_is_honest_about_unknown():
    out = stamp({"alarms": []}, source=HISTORY, reason="no cache")
    assert out["as_of"] is None
    assert out["stale_seconds"] is None


def test_stamp_does_not_overwrite_payload_keys():
    out = stamp({"source": "device-said-live"}, source=LIVE)
    assert out["source"] == "live"          # envelope wins
    assert out["payload_source"] == "device-said-live"


def test_stamp_naive_as_of_does_not_raise():
    naive = datetime(2026, 9, 1, 6, 0, 0)  # no tzinfo -- what a DB driver commonly hands back
    out = stamp({"v": 1}, source=HISTORY, as_of=naive, reason="test")
    assert out["source"] == "history"
    assert out["stale_seconds"] is not None
    assert out["stale_seconds"] >= 0


def test_stamp_future_as_of_reports_negative_staleness_and_clock_skew():
    future = datetime.now(timezone.utc) + timedelta(seconds=300)
    out = stamp({"v": 1}, source=HISTORY, as_of=future, reason="rtc drift")
    assert out["stale_seconds"] < 0
    assert out["clock_skew"] is True


def test_stamp_normal_as_of_has_no_clock_skew():
    past = datetime.now(timezone.utc) - timedelta(seconds=60)
    out = stamp({"v": 1}, source=HISTORY, as_of=past, reason="ok")
    assert out["clock_skew"] is False


def test_stamp_double_collision_preserves_both_values():
    out = stamp({"source": "A", "payload_source": "B"}, source=LIVE)
    assert out["source"] == "live"                    # envelope wins
    assert out["payload_source"] == "A"                # first collision escalates here
    assert out["payload_payload_source"] == "B"        # pre-existing key escalates further


def test_extract_as_of_key_priority():
    payload = {
        "as_of": "2026-09-01T06:00:00Z",
        "cached_at": "2026-09-01T05:00:00Z",
        "timestamp": "2026-09-01T04:00:00Z",
    }
    dt = extract_as_of(payload)
    assert dt == datetime(2026, 9, 1, 6, 0, 0, tzinfo=timezone.utc)


def test_extract_as_of_returns_none_on_malformed_timestamp():
    assert extract_as_of({"as_of": "not-a-timestamp"}) is None


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
