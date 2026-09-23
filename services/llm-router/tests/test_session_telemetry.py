"""Focused tests for the NOC Session Intelligence telemetry hook
(session_client.py + app.py's chat_completions wiring): emits on success and
on AllTargetsFailedError, never emits when disabled, never raises/blocks the
real response when the session service is unreachable, and never leaks a
provider credential in the emitted payload.

session_client.emit_success/emit_failure schedule their HTTP POST via
asyncio.create_task (fire-and-forget) -- the `captured_tasks` fixture
patches create_task just enough to keep a handle on those tasks so the test
can await them before asserting, without changing the fire-and-forget
behaviour itself.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from llm_router import session_client as session_client_module
from llm_router.app import create_app
from llm_router.config import Settings
from llm_router.session_client import SessionClient, extract_tool_filter_header
from tests.fakes import FakeProviders, json_ok, status


class SessionServiceCapture:
    def __init__(self):
        self.requests: list[dict] = []

    def handler(self, request: httpx.Request):
        self.requests.append(json.loads(request.content))
        return httpx.Response(202, json={"recorded": True})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


@pytest.fixture
def captured_tasks(monkeypatch):
    """Lets a test await the background emit task deterministically instead
    of sleeping -- keeps create_task's real scheduling behaviour, just also
    remembers each task it created."""
    tasks: list[asyncio.Task] = []
    original_create_task = asyncio.create_task

    def capturing_create_task(coro):
        task = original_create_task(coro)
        tasks.append(task)
        return task

    monkeypatch.setattr(session_client_module.asyncio, "create_task", capturing_create_task)
    return tasks


def make_client(registry, catalog, providers: FakeProviders, session_client) -> httpx.AsyncClient:
    upstream_client = httpx.AsyncClient(transport=providers.transport())
    app = create_app(registry, catalog, http_client=upstream_client, session_client=session_client)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_emits_success_event_with_target_tokens_and_attempted(registry, catalog, captured_tasks):
    registry.upsert_combo(agent_id="agent-x", targets=[{"endpoint": "Kimi", "model": "k3"}])
    providers = FakeProviders()
    providers.script(
        "https://kimi.example",
        json_ok(
            {
                "id": "chatcmpl-1",
                "choices": [],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }
        ),
    )
    capture = SessionServiceCapture()
    session_client = SessionClient(
        http_client=httpx.AsyncClient(transport=capture.transport()),
        settings=Settings(session_service_url="http://session:8112"),
    )

    async with make_client(registry, catalog, providers, session_client) as client:
        resp = await client.post("/v1/chat/completions", json={"model": "agent-x", "messages": []})
    await asyncio.gather(*captured_tasks)

    assert resp.status_code == 200
    assert len(capture.requests) == 1
    payload = capture.requests[0]
    assert payload["agent_id"] == "agent-x"
    assert payload["target"] == "Kimi/k3"
    assert payload["attempted"] == ["Kimi/k3"]
    assert payload["ok"] is True
    assert payload["tokens"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    assert isinstance(payload["latency_ms"], (int, float))


@pytest.mark.asyncio
async def test_emits_failure_event_when_all_targets_fail(registry, catalog, captured_tasks):
    registry.upsert_combo(agent_id="agent-x", targets=[{"endpoint": "Kimi", "model": "k3"}])
    providers = FakeProviders()
    providers.script("https://kimi.example", status(500))
    capture = SessionServiceCapture()
    session_client = SessionClient(
        http_client=httpx.AsyncClient(transport=capture.transport()),
        settings=Settings(session_service_url="http://session:8112"),
    )

    async with make_client(registry, catalog, providers, session_client) as client:
        resp = await client.post("/v1/chat/completions", json={"model": "agent-x", "messages": []})
    await asyncio.gather(*captured_tasks)

    assert resp.status_code == 502
    assert len(capture.requests) == 1
    payload = capture.requests[0]
    assert payload["ok"] is False
    assert payload["target"] is None
    assert payload["attempted"] == ["Kimi/k3"]
    assert "Kimi/k3" in payload["error"]


@pytest.mark.asyncio
async def test_disabled_by_default_emits_nothing(registry, catalog, captured_tasks):
    registry.upsert_combo(agent_id="agent-x", targets=[{"endpoint": "Kimi", "model": "k3"}])
    providers = FakeProviders()
    providers.script("https://kimi.example", json_ok({"id": "ok", "choices": []}))

    # No session_client injected -> create_app builds one from the default
    # Settings, whose session_service_url is "" (disabled).
    async with make_client(registry, catalog, providers, None) as client:
        resp = await client.post("/v1/chat/completions", json={"model": "agent-x", "messages": []})
    await asyncio.gather(*captured_tasks)

    assert resp.status_code == 200
    assert captured_tasks == []


@pytest.mark.asyncio
async def test_unreachable_session_service_never_raises_or_blocks_the_response(registry, catalog, captured_tasks):
    registry.upsert_combo(agent_id="agent-x", targets=[{"endpoint": "Kimi", "model": "k3"}])
    providers = FakeProviders()
    providers.script("https://kimi.example", json_ok({"id": "ok", "choices": []}))

    def failing_handler(request: httpx.Request):
        raise httpx.ConnectError("simulated session service down", request=request)

    session_client = SessionClient(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(failing_handler)),
        settings=Settings(session_service_url="http://session:8112"),
    )

    async with make_client(registry, catalog, providers, session_client) as client:
        resp = await client.post("/v1/chat/completions", json={"model": "agent-x", "messages": []})
    await asyncio.gather(*captured_tasks)  # must not raise

    assert resp.status_code == 200
    assert resp.json() == {"id": "ok", "choices": []}


@pytest.mark.asyncio
async def test_emitted_payload_never_contains_a_provider_api_key(registry, catalog, captured_tasks):
    registry.upsert_combo(agent_id="agent-x", targets=[{"endpoint": "Kimi", "model": "k3"}])
    providers = FakeProviders()
    providers.script("https://kimi.example", json_ok({"id": "ok", "choices": []}))
    capture = SessionServiceCapture()
    session_client = SessionClient(
        http_client=httpx.AsyncClient(transport=capture.transport()),
        settings=Settings(session_service_url="http://session:8112"),
    )

    async with make_client(registry, catalog, providers, session_client) as client:
        await client.post("/v1/chat/completions", json={"model": "agent-x", "messages": []})
    await asyncio.gather(*captured_tasks)

    raw = json.dumps(capture.requests)
    assert "sk-test-kimi-secret" not in raw
    assert "sk-test-glm-secret" not in raw
    assert "apiKey" not in raw
    assert "api_key" not in raw


@pytest.mark.asyncio
async def test_tool_filter_decision_header_is_relayed_into_the_emitted_payload(registry, catalog, captured_tasks):
    registry.upsert_combo(agent_id="agent-x", targets=[{"endpoint": "Kimi", "model": "k3"}])
    providers = FakeProviders()
    providers.script("https://kimi.example", json_ok({"id": "ok", "choices": []}))
    capture = SessionServiceCapture()
    session_client = SessionClient(
        http_client=httpx.AsyncClient(transport=capture.transport()),
        settings=Settings(session_service_url="http://session:8112"),
    )
    decision = {"applied": True, "tools_before": 20, "tools_after": 4}

    async with make_client(registry, catalog, providers, session_client) as client:
        await client.post(
            "/v1/chat/completions",
            json={"model": "agent-x", "messages": []},
            headers={"X-Tool-Filter-Decision": json.dumps(decision)},
        )
    await asyncio.gather(*captured_tasks)

    assert capture.requests[0]["tool_filter"] == decision


def test_extract_tool_filter_header_parses_valid_json():
    assert extract_tool_filter_header('{"applied": true}') == {"applied": True}


def test_extract_tool_filter_header_returns_none_for_missing_or_malformed():
    assert extract_tool_filter_header(None) is None
    assert extract_tool_filter_header("") is None
    assert extract_tool_filter_header("not json") is None
    assert extract_tool_filter_header("[1, 2, 3]") is None
