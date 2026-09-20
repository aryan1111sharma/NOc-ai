"""Tests for the routing/fallback engine (llm_router/proxy.py).

FakeProviders (tests/fakes.py) scripts one behaviour queue per base_url and
records call order, so every assertion here is either "which base_urls were
called, in what order" or "what did the client end up receiving" -- never an
assertion on a mock having-been-called, which would only prove the code
called something, not that routing behaved correctly.
"""

from __future__ import annotations

import httpx
import pytest

from llm_router.catalog import Catalog
from llm_router.proxy import (
    AllTargetsFailedError,
    ComboDisabledError,
    ComboNotFoundError,
    NoEnabledTargetsError,
    Router,
)
from llm_router.store import Registry
from tests.fakes import FakeProviders, json_ok, status, stream_ok


def make_router(registry: Registry, catalog: Catalog, providers: FakeProviders) -> Router:
    client = httpx.AsyncClient(transport=providers.transport())
    return Router(registry, catalog, http_client=client)


@pytest.mark.asyncio
async def test_combo_not_found_raises(registry, catalog):
    router = make_router(registry, catalog, FakeProviders())
    with pytest.raises(ComboNotFoundError):
        await router.dispatch({"model": "no-such-agent", "messages": []})


@pytest.mark.asyncio
async def test_disabled_combo_raises(registry, catalog):
    registry.upsert_combo(
        agent_id="agent-x", targets=[{"endpoint": "Kimi", "model": "k3"}], enabled=False
    )
    router = make_router(registry, catalog, FakeProviders())
    with pytest.raises(ComboDisabledError):
        await router.dispatch({"model": "agent-x", "messages": []})


@pytest.mark.asyncio
async def test_no_enabled_targets_raises(registry, catalog):
    registry.upsert_combo(
        agent_id="agent-x", targets=[{"endpoint": "Kimi", "model": "k3", "enabled": False}]
    )
    router = make_router(registry, catalog, FakeProviders())
    with pytest.raises(NoEnabledTargetsError):
        await router.dispatch({"model": "agent-x", "messages": []})


@pytest.mark.asyncio
async def test_target_no_longer_in_catalog_is_skipped(registry, catalog):
    """Config drift (the endpoint/model was removed from librechat.yaml since
    the combo was built) is not an error -- the target is simply skipped."""
    registry.upsert_combo(
        agent_id="agent-x",
        targets=[
            {"endpoint": "Kimi", "model": "no-longer-registered"},
            {"endpoint": "GLM", "model": "glm-5.2"},
        ],
    )
    providers = FakeProviders()
    providers.script("https://glm.example", json_ok({"id": "ok"}))
    router = make_router(registry, catalog, providers)

    result = await router.dispatch({"model": "agent-x", "messages": []})

    assert result.target == "GLM/glm-5.2"
    assert providers.calls == ["https://glm.example"]


@pytest.mark.asyncio
async def test_disabled_target_is_skipped_without_being_called(registry, catalog):
    registry.upsert_combo(
        agent_id="agent-x",
        targets=[
            {"endpoint": "Kimi", "model": "kimi-for-coding", "enabled": False},
            {"endpoint": "Kimi", "model": "k3"},
        ],
    )
    providers = FakeProviders()
    providers.script("https://kimi.example", json_ok({"id": "x", "choices": []}))
    router = make_router(registry, catalog, providers)

    result = await router.dispatch({"model": "agent-x", "messages": []})

    assert result.target == "Kimi/k3"
    assert providers.calls == ["https://kimi.example"]


@pytest.mark.asyncio
async def test_one_credential_multiple_models_first_succeeds(registry, catalog):
    """Item 2 of the proof list: one credential (Kimi), two models, no
    fallback needed."""
    registry.upsert_combo(
        agent_id="agent-x",
        targets=[
            {"endpoint": "Kimi", "model": "kimi-for-coding"},
            {"endpoint": "Kimi", "model": "k3"},
        ],
    )
    providers = FakeProviders()
    providers.script("https://kimi.example", json_ok({"id": "x", "choices": []}))
    router = make_router(registry, catalog, providers)

    result = await router.dispatch({"model": "agent-x", "messages": []})

    assert result.target == "Kimi/kimi-for-coding"
    assert providers.calls == ["https://kimi.example"]


@pytest.mark.asyncio
async def test_one_credential_multiple_models_falls_back_to_second_model(registry, catalog):
    registry.upsert_combo(
        agent_id="agent-x",
        targets=[
            {"endpoint": "Kimi", "model": "kimi-for-coding"},
            {"endpoint": "Kimi", "model": "k3"},
        ],
    )
    providers = FakeProviders()
    providers.script("https://kimi.example", status(500), json_ok({"id": "ok"}))
    router = make_router(registry, catalog, providers)

    result = await router.dispatch({"model": "agent-x", "messages": []})

    assert result.target == "Kimi/k3"
    sent = providers.request_bodies["https://kimi.example"]
    assert sent[0]["model"] == "kimi-for-coding"
    assert sent[1]["model"] == "k3"


@pytest.mark.asyncio
async def test_multiple_credentials_same_model_name(registry, catalog):
    """Item 3 of the proof list: two different credentials (Kimi, GLM) both
    registered under a model literally named "k3" in this fixture -- the
    router must treat them as distinct targets, not dedupe by model name."""
    registry.upsert_combo(
        agent_id="agent-x",
        targets=[
            {"endpoint": "Kimi", "model": "k3"},
            {"endpoint": "GLM", "model": "k3"},
        ],
    )
    providers = FakeProviders()
    providers.script("https://kimi.example", status(429))
    providers.script("https://glm.example", json_ok({"id": "ok"}))
    router = make_router(registry, catalog, providers)

    result = await router.dispatch({"model": "agent-x", "messages": []})

    assert result.target == "GLM/k3"
    assert providers.calls == ["https://kimi.example", "https://glm.example"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_status", [429, 500, 502, 503])
async def test_falls_back_on_retryable_status(registry, catalog, failure_status):
    registry.upsert_combo(
        agent_id="agent-x",
        targets=[{"endpoint": "Kimi", "model": "k3"}, {"endpoint": "GLM", "model": "glm-5.2"}],
    )
    providers = FakeProviders()
    providers.script("https://kimi.example", status(failure_status))
    providers.script("https://glm.example", json_ok({"id": "ok"}))
    router = make_router(registry, catalog, providers)

    result = await router.dispatch({"model": "agent-x", "messages": []})

    assert result.target == "GLM/glm-5.2"
    assert providers.calls == ["https://kimi.example", "https://glm.example"]


@pytest.mark.asyncio
async def test_falls_back_on_timeout_and_connection_error(registry, catalog):
    registry.upsert_combo(
        agent_id="agent-x",
        targets=[
            {"endpoint": "Kimi", "model": "k3"},
            {"endpoint": "Kimi", "model": "kimi-for-coding"},
            {"endpoint": "GLM", "model": "glm-5.2"},
        ],
    )
    providers = FakeProviders()
    providers.script("https://kimi.example", httpx.ConnectTimeout("timed out"), httpx.ConnectError("refused"))
    providers.script("https://glm.example", json_ok({"id": "ok"}))
    router = make_router(registry, catalog, providers)

    result = await router.dispatch({"model": "agent-x", "messages": []})

    assert result.target == "GLM/glm-5.2"


@pytest.mark.asyncio
async def test_non_retryable_status_is_returned_without_fallback(registry, catalog):
    registry.upsert_combo(
        agent_id="agent-x",
        targets=[{"endpoint": "Kimi", "model": "k3"}, {"endpoint": "GLM", "model": "glm-5.2"}],
    )
    providers = FakeProviders()
    providers.script("https://kimi.example", status(400, {"error": {"message": "bad request"}}))
    router = make_router(registry, catalog, providers)

    result = await router.dispatch({"model": "agent-x", "messages": []})

    assert result.target == "Kimi/k3"
    assert result.status_code == 400
    assert providers.calls == ["https://kimi.example"]


@pytest.mark.asyncio
async def test_priority_order_is_respected_across_three_targets(registry, catalog):
    registry.upsert_combo(
        agent_id="agent-x",
        targets=[
            {"endpoint": "Kimi", "model": "kimi-for-coding"},
            {"endpoint": "Kimi", "model": "k3"},
            {"endpoint": "GLM", "model": "glm-5.2"},
        ],
    )
    providers = FakeProviders()
    providers.script("https://kimi.example", status(500), status(503))
    providers.script("https://glm.example", json_ok({"id": "ok"}))
    router = make_router(registry, catalog, providers)

    result = await router.dispatch({"model": "agent-x", "messages": []})

    assert result.target == "GLM/glm-5.2"
    assert providers.calls == ["https://kimi.example", "https://kimi.example", "https://glm.example"]


@pytest.mark.asyncio
async def test_all_targets_failing_raises_with_full_attempt_list(registry, catalog):
    registry.upsert_combo(
        agent_id="agent-x",
        targets=[{"endpoint": "Kimi", "model": "k3"}, {"endpoint": "GLM", "model": "glm-5.2"}],
    )
    providers = FakeProviders()
    providers.script("https://kimi.example", status(500))
    providers.script("https://glm.example", httpx.ConnectError("refused"))
    router = make_router(registry, catalog, providers)

    with pytest.raises(AllTargetsFailedError) as exc_info:
        await router.dispatch({"model": "agent-x", "messages": []})

    assert exc_info.value.attempted == ["Kimi/k3", "GLM/glm-5.2"]


@pytest.mark.asyncio
async def test_model_field_is_rewritten_to_targets_own_model(registry, catalog):
    registry.upsert_combo(agent_id="agent-x", targets=[{"endpoint": "Kimi", "model": "k3"}])
    providers = FakeProviders()
    providers.script("https://kimi.example", json_ok({"id": "ok"}))
    router = make_router(registry, catalog, providers)

    await router.dispatch({"model": "agent-x", "messages": []})

    sent = providers.request_bodies["https://kimi.example"][0]
    assert sent["model"] == "k3"


@pytest.mark.asyncio
async def test_tools_tool_choice_and_messages_pass_through_unchanged(registry, catalog):
    registry.upsert_combo(agent_id="agent-x", targets=[{"endpoint": "Kimi", "model": "k3"}])
    providers = FakeProviders()
    providers.script("https://kimi.example", json_ok({"id": "ok"}))
    router = make_router(registry, catalog, providers)

    tools = [{"type": "function", "function": {"name": "get_weather"}}]
    messages = [{"role": "user", "content": "hi"}]
    await router.dispatch(
        {
            "model": "agent-x",
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.3,
            "max_tokens": 128,
        }
    )

    sent = providers.request_bodies["https://kimi.example"][0]
    assert sent["messages"] == messages
    assert sent["tools"] == tools
    assert sent["tool_choice"] == "auto"
    assert sent["temperature"] == 0.3
    assert sent["max_tokens"] == 128


@pytest.mark.asyncio
async def test_api_key_is_sent_upstream_but_never_returned(registry, catalog):
    """The resolved secret must reach the provider (as a bearer token) but
    never appear anywhere on the DispatchResult that flows back to a
    client."""
    registry.upsert_combo(agent_id="agent-x", targets=[{"endpoint": "Kimi", "model": "k3"}])
    providers = FakeProviders()
    providers.script("https://kimi.example", json_ok({"id": "ok"}))
    router = make_router(registry, catalog, providers)

    result = await router.dispatch({"model": "agent-x", "messages": []})

    assert "sk-test-kimi-secret" not in str(result.body)
    assert "sk-test-kimi-secret" not in str(result.target)
    assert "sk-test-kimi-secret" not in "".join(f"{k}:{v}" for k, v in result.headers.items())


@pytest.mark.asyncio
async def test_streaming_success_relays_chunks_with_no_fallback(registry, catalog):
    registry.upsert_combo(
        agent_id="agent-x",
        targets=[{"endpoint": "Kimi", "model": "k3"}, {"endpoint": "GLM", "model": "glm-5.2"}],
    )
    providers = FakeProviders()
    providers.script("https://kimi.example", stream_ok([b"data: 1\n\n", b"data: 2\n\n"]))
    router = make_router(registry, catalog, providers)

    result = await router.dispatch({"model": "agent-x", "messages": [], "stream": True})
    chunks = [c async for c in result.stream_response.aiter_raw()]

    assert result.target == "Kimi/k3"
    assert chunks == [b"data: 1\n\n", b"data: 2\n\n"]
    assert providers.calls == ["https://kimi.example"]


@pytest.mark.asyncio
async def test_streaming_pre_first_byte_failure_falls_back(registry, catalog):
    registry.upsert_combo(
        agent_id="agent-x",
        targets=[{"endpoint": "Kimi", "model": "k3"}, {"endpoint": "GLM", "model": "glm-5.2"}],
    )
    providers = FakeProviders()
    providers.script("https://kimi.example", status(429))
    providers.script("https://glm.example", stream_ok([b"data: 1\n\n"]))
    router = make_router(registry, catalog, providers)

    result = await router.dispatch({"model": "agent-x", "messages": [], "stream": True})
    chunks = [c async for c in result.stream_response.aiter_raw()]

    assert result.target == "GLM/glm-5.2"
    assert chunks == [b"data: 1\n\n"]
    assert providers.calls == ["https://kimi.example", "https://glm.example"]


@pytest.mark.asyncio
async def test_streaming_mid_stream_failure_does_not_fall_back(registry, catalog):
    """Once the first byte has gone out, the response is committed -- a drop
    mid-stream ends the stream, it never triggers a second target."""
    registry.upsert_combo(
        agent_id="agent-x",
        targets=[{"endpoint": "Kimi", "model": "k3"}, {"endpoint": "GLM", "model": "glm-5.2"}],
    )
    providers = FakeProviders()
    providers.script("https://kimi.example", stream_ok([b"data: 1\n\n", b"data: 2\n\n"], fail_after=1))
    router = make_router(registry, catalog, providers)

    result = await router.dispatch({"model": "agent-x", "messages": [], "stream": True})
    assert result.target == "Kimi/k3"

    received = []
    with pytest.raises(httpx.ReadError):
        async for chunk in result.stream_response.aiter_raw():
            received.append(chunk)

    assert received == [b"data: 1\n\n"]
    assert providers.calls == ["https://kimi.example"]
