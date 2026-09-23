"""Focused tests for session/store.py's EventStore: ingestion persistence,
ring-buffer trimming, and the best-effort nearest-timestamp correlation used
by aggregate.py."""

from __future__ import annotations

from session.store import EventStore, TurnEvent


def _event(agent_id: str = "agent-x", *, recorded_at: str, ok: bool = True) -> TurnEvent:
    return TurnEvent(
        agent_id=agent_id,
        target="Kimi/k3-256k",
        attempted=("Kimi/k3-256k",),
        status_code=200,
        ok=ok,
        tokens={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        latency_ms=123.4,
        tool_filter={"applied": True, "tools_before": 20, "tools_after": 5},
        error=None,
        recorded_at=recorded_at,
    )


def test_record_and_list_roundtrip(event_store: EventStore):
    event_store.record(_event(recorded_at="2026-09-23T10:00:00+00:00"))

    events = event_store.list_events(agent_id="agent-x")

    assert len(events) == 1
    assert events[0].target == "Kimi/k3-256k"
    assert events[0].tokens["total_tokens"] == 15


def test_events_persist_across_new_store_instance(tmp_path):
    path = tmp_path / "events.json"
    EventStore(path).record(_event(recorded_at="2026-09-23T10:00:00+00:00"))

    reloaded = EventStore(path)

    assert len(reloaded.list_events()) == 1


def test_ring_buffer_trims_oldest_first(tmp_path):
    store = EventStore(tmp_path / "events.json", max_events=3)
    for i in range(5):
        store.record(_event(recorded_at=f"2026-09-23T10:0{i}:00+00:00"))

    events = store.list_events()

    assert len(events) == 3
    assert [e.recorded_at for e in events] == [
        "2026-09-23T10:02:00+00:00",
        "2026-09-23T10:03:00+00:00",
        "2026-09-23T10:04:00+00:00",
    ]


def test_find_nearest_within_window_returns_closest_match(event_store: EventStore):
    event_store.record(_event(recorded_at="2026-09-23T10:00:00+00:00"))
    event_store.record(_event(recorded_at="2026-09-23T10:00:10+00:00"))

    match = event_store.find_nearest(
        "agent-x", "2026-09-23T10:00:12+00:00", window_seconds=30
    )

    assert match is not None
    assert match.recorded_at == "2026-09-23T10:00:10+00:00"


def test_find_nearest_outside_window_returns_none(event_store: EventStore):
    event_store.record(_event(recorded_at="2026-09-23T10:00:00+00:00"))

    match = event_store.find_nearest(
        "agent-x", "2026-09-23T10:05:00+00:00", window_seconds=30
    )

    assert match is None


def test_find_nearest_ignores_other_agents(event_store: EventStore):
    event_store.record(_event("agent-y", recorded_at="2026-09-23T10:00:00+00:00"))

    match = event_store.find_nearest(
        "agent-x", "2026-09-23T10:00:00+00:00", window_seconds=30
    )

    assert match is None


def test_find_nearest_with_no_events_returns_none(event_store: EventStore):
    assert event_store.find_nearest("agent-x", "2026-09-23T10:00:00+00:00", window_seconds=30) is None
