"""Tests for the admin surface (/admin/catalog, /admin/combos), including the
credential-protection invariant: no response body may ever contain a
resolved api_key or a raw ${VAR} placeholder value."""

from __future__ import annotations

import httpx
import pytest

from llm_router.app import create_app
from llm_router.config import Settings
from tests.fakes import FakeProviders


def make_client(registry, catalog, *, settings=None) -> httpx.AsyncClient:
    upstream_client = httpx.AsyncClient(transport=FakeProviders().transport())
    app = create_app(registry, catalog, http_client=upstream_client, settings=settings)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def assert_secret_absent(response: httpx.Response) -> None:
    assert "sk-test-kimi-secret" not in response.text
    assert "sk-test-glm-secret" not in response.text
    assert "apiKey" not in response.text
    assert "api_key" not in response.text


@pytest.mark.asyncio
async def test_catalog_lists_targets_with_no_keys(registry, catalog):
    async with make_client(registry, catalog) as client:
        resp = await client.get("/admin/catalog")

    assert resp.status_code == 200
    assert_secret_absent(resp)
    pairs = {(t["endpoint"], t["model"]) for t in resp.json()["targets"]}
    assert ("Kimi", "kimi-for-coding") in pairs
    assert ("GLM", "glm-5.2") in pairs


@pytest.mark.asyncio
async def test_catalog_cost_type_defaults_to_unknown(registry, catalog):
    async with make_client(registry, catalog) as client:
        resp = await client.get("/admin/catalog")

    cost_types = {t["endpoint"]: t["cost_type"] for t in resp.json()["targets"]}
    assert cost_types == {"Kimi": "UNKNOWN", "GLM": "UNKNOWN"}


@pytest.mark.asyncio
async def test_catalog_cost_type_reflects_configured_settings(registry, catalog):
    settings = Settings(provider_cost_types={"Kimi": "PAID", "GLM": "FREE"})
    async with make_client(registry, catalog, settings=settings) as client:
        resp = await client.get("/admin/catalog")

    cost_types = {t["endpoint"]: t["cost_type"] for t in resp.json()["targets"]}
    assert cost_types == {"Kimi": "PAID", "GLM": "FREE"}


@pytest.mark.asyncio
async def test_catalog_cost_type_ignores_invalid_configured_value(registry, catalog):
    settings = Settings(provider_cost_types={"Kimi": "not-a-real-value"})
    async with make_client(registry, catalog, settings=settings) as client:
        resp = await client.get("/admin/catalog")

    cost_types = {t["endpoint"]: t["cost_type"] for t in resp.json()["targets"]}
    assert cost_types["Kimi"] == "UNKNOWN"


@pytest.mark.asyncio
async def test_usage_endpoint_lists_recorded_stats_with_no_secrets(registry, catalog):
    registry.record_usage_success(
        "Kimi", "k3", tokens={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    )
    registry.record_usage_failure("GLM", "glm-5.2", failure_class="429")

    async with make_client(registry, catalog) as client:
        resp = await client.get("/admin/usage")

    assert resp.status_code == 200
    assert_secret_absent(resp)
    by_target = {(u["endpoint"], u["model"]): u for u in resp.json()["usage"]}
    assert by_target[("Kimi", "k3")]["total_tokens"] == 15
    assert by_target[("GLM", "glm-5.2")]["last_failure_class"] == "429"


@pytest.mark.asyncio
async def test_usage_endpoint_empty_when_nothing_observed_yet(registry, catalog):
    async with make_client(registry, catalog) as client:
        resp = await client.get("/admin/usage")

    assert resp.status_code == 200
    assert resp.json()["usage"] == []


@pytest.mark.asyncio
async def test_kimi_quota_endpoint_returns_parsed_quota_with_no_secrets(registry, catalog):
    providers = FakeProviders()
    providers.script(
        "https://kimi.example",
        httpx.Response(
            200,
            json={
                "usage": {"limit": "100", "used": "47", "remaining": "53"},
                "limits": [],
            },
        ),
    )
    upstream_client = httpx.AsyncClient(transport=providers.transport())
    from llm_router.app import create_app

    app = create_app(registry, catalog, http_client=upstream_client)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/admin/quota/kimi")

    assert resp.status_code == 200
    assert_secret_absent(resp)
    quota = resp.json()["quota"]
    assert quota["provider"] == "Kimi"
    assert quota["status"] == "ok"
    assert quota["windows"][0]["used"] == 47.0


@pytest.mark.asyncio
async def test_kimi_quota_endpoint_degrades_to_unavailable_on_upstream_failure(registry, catalog):
    providers = FakeProviders()
    providers.script("https://kimi.example", httpx.Response(401, json={"error": "bad key"}))
    upstream_client = httpx.AsyncClient(transport=providers.transport())
    from llm_router.app import create_app

    app = create_app(registry, catalog, http_client=upstream_client)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/admin/quota/kimi")

    assert resp.status_code == 200
    assert_secret_absent(resp)
    assert resp.json()["quota"]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_local_usage_endpoint_unaffected_by_kimi_quota_route(registry, catalog):
    registry.record_usage_success(
        "Kimi", "k3", tokens={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
    )
    async with make_client(registry, catalog) as client:
        resp = await client.get("/admin/usage")

    assert resp.status_code == 200
    assert resp.json()["usage"][0]["total_tokens"] == 2


@pytest.mark.asyncio
async def test_create_combo_from_catalog_targets(registry, catalog):
    async with make_client(registry, catalog) as client:
        resp = await client.put(
            "/admin/combos/agent-x",
            json={
                "targets": [
                    {"endpoint": "Kimi", "model": "kimi-for-coding"},
                    {"endpoint": "Kimi", "model": "k3"},
                    {"endpoint": "GLM", "model": "glm-5.2"},
                ]
            },
        )

    assert resp.status_code == 201
    assert_secret_absent(resp)
    body = resp.json()["combo"]
    assert body["agent_id"] == "agent-x"
    assert [t["model"] for t in body["targets"]] == ["kimi-for-coding", "k3", "glm-5.2"]


@pytest.mark.asyncio
async def test_combo_target_not_in_catalog_is_rejected(registry, catalog):
    async with make_client(registry, catalog) as client:
        resp = await client.put(
            "/admin/combos/agent-x",
            json={"targets": [{"endpoint": "Kimi", "model": "not-a-real-model"}]},
        )

    assert resp.status_code == 400
    assert registry.get_combo("agent-x") is None


@pytest.mark.asyncio
async def test_reorder_combo_targets_changes_priority(registry, catalog):
    async with make_client(registry, catalog) as client:
        await client.put(
            "/admin/combos/agent-x",
            json={
                "targets": [
                    {"endpoint": "Kimi", "model": "k3"},
                    {"endpoint": "GLM", "model": "glm-5.2"},
                ]
            },
        )
        resp = await client.put(
            "/admin/combos/agent-x",
            json={
                "targets": [
                    {"endpoint": "GLM", "model": "glm-5.2"},
                    {"endpoint": "Kimi", "model": "k3"},
                ]
            },
        )

    assert resp.status_code == 200
    assert [t["endpoint"] for t in resp.json()["combo"]["targets"]] == ["GLM", "Kimi"]


@pytest.mark.asyncio
async def test_disable_and_reenable_a_single_target(registry, catalog):
    async with make_client(registry, catalog) as client:
        await client.put(
            "/admin/combos/agent-x",
            json={"targets": [{"endpoint": "Kimi", "model": "k3", "enabled": True}]},
        )
        resp = await client.put(
            "/admin/combos/agent-x",
            json={"targets": [{"endpoint": "Kimi", "model": "k3", "enabled": False}]},
        )

    assert resp.json()["combo"]["targets"][0]["enabled"] is False


@pytest.mark.asyncio
async def test_set_combo_enabled(registry, catalog):
    async with make_client(registry, catalog) as client:
        await client.put(
            "/admin/combos/agent-x", json={"targets": [{"endpoint": "Kimi", "model": "k3"}]}
        )
        resp = await client.put("/admin/combos/agent-x/enabled", json={"enabled": False})

    assert resp.status_code == 200
    assert resp.json()["combo"]["enabled"] is False
    assert registry.get_combo("agent-x").enabled is False


@pytest.mark.asyncio
async def test_delete_combo(registry, catalog):
    async with make_client(registry, catalog) as client:
        await client.put(
            "/admin/combos/agent-x", json={"targets": [{"endpoint": "Kimi", "model": "k3"}]}
        )
        resp = await client.delete("/admin/combos/agent-x")

    assert resp.status_code == 200
    assert registry.get_combo("agent-x") is None


@pytest.mark.asyncio
async def test_get_unknown_combo_is_404(registry, catalog):
    async with make_client(registry, catalog) as client:
        resp = await client.get("/admin/combos/no-such-agent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_combos_never_exposes_keys(registry, catalog):
    async with make_client(registry, catalog) as client:
        await client.put(
            "/admin/combos/agent-x", json={"targets": [{"endpoint": "Kimi", "model": "k3"}]}
        )
        resp = await client.get("/admin/combos")

    assert_secret_absent(resp)
