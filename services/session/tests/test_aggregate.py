"""Focused tests for session/aggregate.py: the Mongo <-> EventStore join,
token fallback, and the list-view rollups (models/tools/feedback/status)."""

from __future__ import annotations

from datetime import datetime, timezone

from session import aggregate
from session.store import EventStore, TurnEvent
from tests.fakes import FakeDB, tool_call_part


def _conversation(**overrides) -> dict:
    base = {
        "conversationId": "conv-1",
        "user": "user-1",
        "agent_id": "agent-x",
        "endpoint": "NOC Router",
        "title": "Test conversation",
        "createdAt": datetime(2026, 9, 23, 10, 0, 0, tzinfo=timezone.utc),
        "updatedAt": datetime(2026, 9, 23, 10, 0, 10, tzinfo=timezone.utc),
    }
    base.update(overrides)
    return base


def _turn_pair(*, assistant_overrides=None, user_overrides=None) -> list[dict]:
    user = {
        "messageId": "m1",
        "conversationId": "conv-1",
        "parentMessageId": None,
        "isCreatedByUser": True,
        "sender": "User",
        "model": None,
        "text": "what's the status?",
        "tokenCount": 5,
        "createdAt": datetime(2026, 9, 23, 10, 0, 0, tzinfo=timezone.utc),
    }
    assistant = {
        "messageId": "m2",
        "conversationId": "conv-1",
        "parentMessageId": "m1",
        "isCreatedByUser": False,
        "sender": "Kimi",
        "model": "kimi-for-coding",
        "text": "all good",
        "tokenCount": 220,
        "createdAt": datetime(2026, 9, 23, 10, 0, 5, tzinfo=timezone.utc),
        "finish_reason": "stop",
        "error": False,
        "unfinished": False,
        "feedback": None,
        "content": [],
    }
    user.update(user_overrides or {})
    assistant.update(assistant_overrides or {})
    return [user, assistant]


def test_build_session_detail_returns_none_for_unknown_conversation(event_store: EventStore):
    db = FakeDB(conversations=[], messages=[])

    view = aggregate.build_session_detail(db, event_store, "no-such", correlation_window_seconds=30)

    assert view is None


def test_build_session_detail_falls_back_to_message_token_counts_without_event(event_store: EventStore):
    db = FakeDB(conversations=[_conversation()], messages=_turn_pair())

    view = aggregate.build_session_detail(db, event_store, "conv-1", correlation_window_seconds=30)

    assert len(view.timeline) == 1
    step = view.timeline[0]
    assert step.tokens == {
        "input_tokens_estimated": 5,
        "output_tokens_estimated": 220,
        "source": "message_token_count",
    }
    assert step.actual_target is None
    assert step.fallback_attempted == []
    assert step.latency_ms is None


def test_build_session_detail_prefers_matched_event_over_message_token_counts(event_store: EventStore):
    db = FakeDB(conversations=[_conversation()], messages=_turn_pair())
    event_store.record(
        TurnEvent(
            agent_id="agent-x",
            target="Kimi/k3-256k",
            attempted=("GLM/glm-5.2", "Kimi/k3-256k"),
            status_code=200,
            ok=True,
            tokens={"prompt_tokens": 12, "completion_tokens": 30, "total_tokens": 42},
            latency_ms=850.0,
            tool_filter={"applied": True, "tools_before": 20, "tools_after": 4},
            error=None,
            recorded_at="2026-09-23T10:00:06+00:00",
        )
    )

    view = aggregate.build_session_detail(db, event_store, "conv-1", correlation_window_seconds=30)
    step = view.timeline[0]

    assert step.tokens == {
        "prompt_tokens": 12,
        "completion_tokens": 30,
        "total_tokens": 42,
        "source": "provider_usage",
    }
    assert step.actual_target == "Kimi/k3-256k"
    assert step.fallback_attempted == ["GLM/glm-5.2", "Kimi/k3-256k"]
    assert step.latency_ms == 850.0
    assert step.tool_filter == {"applied": True, "tools_before": 20, "tools_after": 4}


def test_build_session_detail_ignores_event_outside_correlation_window(event_store: EventStore):
    db = FakeDB(conversations=[_conversation()], messages=_turn_pair())
    event_store.record(
        TurnEvent(
            agent_id="agent-x",
            target="Kimi/k3-256k",
            attempted=("Kimi/k3-256k",),
            status_code=200,
            ok=True,
            tokens={"total_tokens": 42},
            latency_ms=850.0,
            tool_filter=None,
            error=None,
            recorded_at="2026-09-23T10:10:00+00:00",
        )
    )

    view = aggregate.build_session_detail(db, event_store, "conv-1", correlation_window_seconds=30)
    step = view.timeline[0]

    assert step.actual_target is None
    assert step.tokens["source"] == "message_token_count"


def test_status_is_error_when_mongo_message_has_error_flag(event_store: EventStore):
    db = FakeDB(conversations=[_conversation()], messages=_turn_pair(assistant_overrides={"error": True}))

    view = aggregate.build_session_detail(db, event_store, "conv-1", correlation_window_seconds=30)

    assert view.timeline[0].status == "error"


def test_status_is_error_when_matched_event_failed(event_store: EventStore):
    db = FakeDB(conversations=[_conversation()], messages=_turn_pair())
    event_store.record(
        TurnEvent(
            agent_id="agent-x",
            target=None,
            attempted=("Kimi/k3-256k", "GLM/glm-5.2"),
            status_code=502,
            ok=False,
            tokens=None,
            latency_ms=500.0,
            tool_filter=None,
            error="all targets failed",
            recorded_at="2026-09-23T10:00:05+00:00",
        )
    )

    view = aggregate.build_session_detail(db, event_store, "conv-1", correlation_window_seconds=30)

    assert view.timeline[0].status == "error"


def test_timeline_reports_tools_and_feedback(event_store: EventStore):
    db = FakeDB(
        conversations=[_conversation()],
        messages=_turn_pair(
            assistant_overrides={
                "content": [tool_call_part(id="call-1", name="jira_search", output="ok")],
                "feedback": {"rating": "thumbsUp"},
            }
        ),
    )

    view = aggregate.build_session_detail(db, event_store, "conv-1", correlation_window_seconds=30)
    step = view.timeline[0]

    assert [t.name for t in step.tools] == ["jira_search"]
    assert step.tools[0].has_output is True
    assert step.feedback == {"rating": "thumbsUp"}


def test_build_session_list_rolls_up_models_tools_tokens_and_feedback(event_store: EventStore):
    db = FakeDB(
        conversations=[_conversation()],
        messages=_turn_pair(
            assistant_overrides={
                "content": [tool_call_part(id="call-1", name="jira_search", output="ok")],
                "feedback": {"rating": "thumbsDown"},
            }
        ),
    )

    entries = aggregate.build_session_list(db, event_store, correlation_window_seconds=30)

    assert len(entries) == 1
    entry = entries[0]
    assert entry.conversation_id == "conv-1"
    assert entry.turn_count == 1
    assert entry.models == ["kimi-for-coding"]
    assert entry.tools_used == ["jira_search"]
    assert entry.feedback_summary == {"thumbs_up": 0, "thumbs_down": 1}
    assert entry.total_tokens == 225  # 5 + 220, message_token_count fallback


def test_build_session_list_filters_by_agent_id(event_store: EventStore):
    db = FakeDB(
        conversations=[
            _conversation(conversationId="conv-1", agent_id="agent-a"),
            _conversation(conversationId="conv-2", agent_id="agent-b"),
        ],
        messages=(
            [{**m, "conversationId": "conv-1"} for m in _turn_pair()]
            + [{**m, "conversationId": "conv-2", "messageId": m["messageId"] + "-b"} for m in _turn_pair()]
        ),
    )

    entries = aggregate.build_session_list(db, event_store, agent_id="agent-b", correlation_window_seconds=30)

    assert [e.conversation_id for e in entries] == ["conv-2"]
