"""Focused tests for the Admin -> Sessions HTTP surface (session/app.py):
list/detail shape, filtering, 404 for unknown sessions, and the no-secret-
exposure invariant (this service holds no provider credential at all, but
must also never leak the raw Mongo connection string)."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from session.app import create_app
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


def _turn_pair(conversation_id: str = "conv-1", **assistant_overrides) -> list[dict]:
    user = {
        "messageId": f"{conversation_id}-m1",
        "conversationId": conversation_id,
        "parentMessageId": None,
        "isCreatedByUser": True,
        "sender": "User",
        "model": None,
        "text": "what's the status?",
        "tokenCount": 5,
        "createdAt": datetime(2026, 9, 23, 10, 0, 0, tzinfo=timezone.utc),
    }
    assistant = {
        "messageId": f"{conversation_id}-m2",
        "conversationId": conversation_id,
        "parentMessageId": user["messageId"],
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
    assistant.update(assistant_overrides)
    return [user, assistant]


def make_client(db, event_store, *, settings=None) -> httpx.AsyncClient:
    app = create_app(event_store, db, settings=settings)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_list_sessions_returns_expected_columns(event_store: EventStore):
    db = FakeDB(
        conversations=[_conversation()],
        messages=_turn_pair(
            content=[tool_call_part(id="call-1", name="jira_search", output="ok")],
            feedback={"rating": "thumbsUp"},
        ),
    )

    async with make_client(db, event_store) as client:
        resp = await client.get("/admin/sessions")

    assert resp.status_code == 200
    sessions = resp.json()["sessions"]
    assert len(sessions) == 1
    entry = sessions[0]
    for field in (
        "conversation_id",
        "agent_id",
        "user_id",
        "turn_count",
        "total_tokens",
        "models",
        "tools_used",
        "feedback_summary",
        "started_at",
        "last_activity",
        "status",
    ):
        assert field in entry
    assert entry["models"] == ["kimi-for-coding"]
    assert entry["tools_used"] == ["jira_search"]


@pytest.mark.asyncio
async def test_list_sessions_filters_by_agent_query_param(event_store: EventStore):
    db = FakeDB(
        conversations=[
            _conversation(conversationId="conv-1", agent_id="agent-a"),
            _conversation(conversationId="conv-2", agent_id="agent-b"),
        ],
        messages=_turn_pair("conv-1") + _turn_pair("conv-2"),
    )

    async with make_client(db, event_store) as client:
        resp = await client.get("/admin/sessions", params={"agent": "agent-b"})

    ids = [s["conversation_id"] for s in resp.json()["sessions"]]
    assert ids == ["conv-2"]


@pytest.mark.asyncio
async def test_session_detail_shows_execution_timeline(event_store: EventStore):
    db = FakeDB(conversations=[_conversation()], messages=_turn_pair())
    event_store.record(
        TurnEvent(
            agent_id="agent-x",
            target="Kimi/k3-256k",
            attempted=("Kimi/k3-256k",),
            status_code=200,
            ok=True,
            tokens={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            latency_ms=444.0,
            tool_filter={"applied": True, "tools_before": 20, "tools_after": 4},
            error=None,
            recorded_at="2026-09-23T10:00:06+00:00",
        )
    )

    async with make_client(db, event_store) as client:
        resp = await client.get("/admin/sessions/conv-1")

    assert resp.status_code == 200
    session = resp.json()["session"]
    assert session["conversation_id"] == "conv-1"
    step = session["timeline"][0]
    assert step["query_preview"] == "what's the status?"
    assert step["actual_target"] == "Kimi/k3-256k"
    assert step["tool_filter"]["applied"] is True
    assert step["tokens"]["total_tokens"] == 30
    assert step["latency_ms"] == 444.0


@pytest.mark.asyncio
async def test_session_detail_404_for_unknown_conversation(event_store: EventStore):
    db = FakeDB(conversations=[], messages=[])

    async with make_client(db, event_store) as client:
        resp = await client.get("/admin/sessions/no-such-conversation")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_admin_responses_never_expose_mongo_uri_or_data_file_path(event_store: EventStore):
    db = FakeDB(conversations=[_conversation()], messages=_turn_pair())

    async with make_client(db, event_store) as client:
        list_resp = await client.get("/admin/sessions")
        detail_resp = await client.get("/admin/sessions/conv-1")

    for resp in (list_resp, detail_resp):
        assert "mongodb://" not in resp.text
        assert "session-events.json" not in resp.text


@pytest.mark.asyncio
async def test_healthz_ok(event_store: EventStore):
    db = FakeDB()
    async with make_client(db, event_store) as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
