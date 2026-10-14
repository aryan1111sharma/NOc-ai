"""Focused tests for POST /ingest/turn: the endpoint llm_router.session_client
fire-and-forgets to (not yet built in this repo -- see
services/llm-router/llm_router/session_client.py). Confirms valid payloads
are recorded and retrievable via correlation, malformed ones are rejected
with 400 (so a buggy caller notices), and the payload/response never need a
provider credential."""

from __future__ import annotations

import httpx
import pytest

from session.app import create_app
from session.store import EventStore
from tests.fakes import FakeDB


def make_client(event_store: EventStore) -> httpx.AsyncClient:
    app = create_app(event_store, FakeDB())
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_ingest_turn_records_a_retrievable_event(event_store: EventStore):
    async with make_client(event_store) as client:
        resp = await client.post(
            "/ingest/turn",
            json={
                "agent_id": "agent-x",
                "target": "Kimi/k3-256k",
                "attempted": ["Kimi/k3-256k"],
                "status_code": 200,
                "ok": True,
                "tokens": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                "latency_ms": 12.5,
                "tool_filter": {"applied": True, "tools_before": 10, "tools_after": 3},
            },
        )

    assert resp.status_code == 202
    events = event_store.list_events(agent_id="agent-x")
    assert len(events) == 1
    assert events[0].target == "Kimi/k3-256k"
    assert events[0].tokens["total_tokens"] == 2


@pytest.mark.asyncio
async def test_ingest_turn_records_a_failure_event(event_store: EventStore):
    async with make_client(event_store) as client:
        resp = await client.post(
            "/ingest/turn",
            json={
                "agent_id": "agent-x",
                "target": None,
                "attempted": ["Kimi/k3-256k", "GLM/glm-5.2"],
                "status_code": 502,
                "ok": False,
                "error": "all targets failed",
            },
        )

    assert resp.status_code == 202
    events = event_store.list_events(agent_id="agent-x")
    assert events[0].ok is False
    assert events[0].error == "all targets failed"


@pytest.mark.asyncio
async def test_ingest_turn_rejects_missing_agent_id(event_store: EventStore):
    async with make_client(event_store) as client:
        resp = await client.post("/ingest/turn", json={"ok": True})

    assert resp.status_code == 400
    assert event_store.list_events() == []


@pytest.mark.asyncio
async def test_ingest_turn_rejects_missing_ok_flag(event_store: EventStore):
    async with make_client(event_store) as client:
        resp = await client.post("/ingest/turn", json={"agent_id": "agent-x"})

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_ingest_turn_rejects_non_json_body(event_store: EventStore):
    async with make_client(event_store) as client:
        resp = await client.post(
            "/ingest/turn", content=b"not json", headers={"content-type": "application/json"}
        )

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_ingest_turn_works_with_only_required_fields(event_store: EventStore):
    async with make_client(event_store) as client:
        resp = await client.post("/ingest/turn", json={"agent_id": "agent-x", "ok": True})

    assert resp.status_code == 202
    event = event_store.list_events(agent_id="agent-x")[0]
    assert event.target is None
    assert event.tokens is None
