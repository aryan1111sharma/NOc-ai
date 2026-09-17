"""End-to-end tests through the ASGI app for /v1/models and
/v1/chat/completions, including streaming and fallback via the real HTTP
surface (not calling llm_router.proxy.Router directly)."""

from __future__ import annotations

import httpx
import pytest

from llm_router.app import create_app
from tests.fakes import FakeProviders, json_ok, status, stream_ok


def make_client(registry, catalog, providers: FakeProviders) -> httpx.AsyncClient:
    upstream_client = httpx.AsyncClient(transport=providers.transport())
    app = create_app(registry, catalog, http_client=upstream_client)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_models_lists_only_enabled_combos_by_agent_id(registry, catalog):
    registry.upsert_combo(agent_id="agent-enabled", targets=[{"endpoint": "Kimi", "model": "k3"}])
    registry.upsert_combo(
        agent_id="agent-disabled", targets=[{"endpoint": "Kimi", "model": "k3"}], enabled=False
    )

    async with make_client(registry, catalog, FakeProviders()) as client:
        resp = await client.get("/v1/models")

    assert resp.status_code == 200
    ids = [m["id"] for m in resp.json()["data"]]
    assert ids == ["agent-enabled"]


@pytest.mark.asyncio
async def test_chat_completions_success(registry, catalog):
    registry.upsert_combo(agent_id="agent-x", targets=[{"endpoint": "Kimi", "model": "k3"}])
    providers = FakeProviders()
    providers.script("https://kimi.example", json_ok({"id": "chatcmpl-1", "choices": []}))

    async with make_client(registry, catalog, providers) as client:
        resp = await client.post(
            "/v1/chat/completions", json={"model": "agent-x", "messages": []}
        )

    assert resp.status_code == 200
    assert resp.json() == {"id": "chatcmpl-1", "choices": []}


@pytest.mark.asyncio
async def test_chat_completions_unknown_agent_is_404(registry, catalog):
    async with make_client(registry, catalog, FakeProviders()) as client:
        resp = await client.post(
            "/v1/chat/completions", json={"model": "no-such-agent", "messages": []}
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_chat_completions_all_targets_failing_is_502(registry, catalog):
    registry.upsert_combo(agent_id="agent-x", targets=[{"endpoint": "Kimi", "model": "k3"}])
    providers = FakeProviders()
    providers.script("https://kimi.example", status(500))

    async with make_client(registry, catalog, providers) as client:
        resp = await client.post(
            "/v1/chat/completions", json={"model": "agent-x", "messages": []}
        )

    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_chat_completions_streaming_end_to_end_with_tools(registry, catalog):
    """Also proves tools/tool_choice reach the upstream unmodified through
    the full ASGI stack, not just through llm_router.proxy directly."""
    registry.upsert_combo(agent_id="agent-x", targets=[{"endpoint": "Kimi", "model": "k3"}])
    providers = FakeProviders()
    providers.script(
        "https://kimi.example",
        stream_ok([b"data: chunk-1\n\n", b"data: chunk-2\n\n", b"data: [DONE]\n\n"]),
    )
    tools = [{"type": "function", "function": {"name": "get_weather"}}]

    async with make_client(registry, catalog, providers) as client:
        async with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "agent-x",
                "messages": [],
                "stream": True,
                "tools": tools,
                "tool_choice": "auto",
            },
        ) as resp:
            assert resp.status_code == 200
            body = b""
            async for chunk in resp.aiter_raw():
                body += chunk

    assert body == b"data: chunk-1\n\ndata: chunk-2\n\ndata: [DONE]\n\n"
    sent = providers.request_bodies["https://kimi.example"][0]
    assert sent["tools"] == tools
    assert sent["tool_choice"] == "auto"


@pytest.mark.asyncio
async def test_chat_completions_streaming_falls_back_before_first_byte(registry, catalog):
    registry.upsert_combo(
        agent_id="agent-x",
        targets=[{"endpoint": "Kimi", "model": "k3"}, {"endpoint": "GLM", "model": "glm-5.2"}],
    )
    providers = FakeProviders()
    providers.script("https://kimi.example", status(429))
    providers.script("https://glm.example", stream_ok([b"data: ok\n\n"]))

    async with make_client(registry, catalog, providers) as client:
        async with client.stream(
            "POST",
            "/v1/chat/completions",
            json={"model": "agent-x", "messages": [], "stream": True},
        ) as resp:
            assert resp.status_code == 200
            body = b"".join([c async for c in resp.aiter_raw()])

    assert body == b"data: ok\n\n"
    assert providers.calls == ["https://kimi.example", "https://glm.example"]


@pytest.mark.asyncio
async def test_response_never_contains_the_resolved_api_key(registry, catalog):
    registry.upsert_combo(agent_id="agent-x", targets=[{"endpoint": "Kimi", "model": "k3"}])
    providers = FakeProviders()
    providers.script("https://kimi.example", json_ok({"id": "chatcmpl-1", "choices": []}))

    async with make_client(registry, catalog, providers) as client:
        resp = await client.post(
            "/v1/chat/completions", json={"model": "agent-x", "messages": []}
        )

    assert "sk-test-kimi-secret" not in resp.text
