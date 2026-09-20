"""Tests for the admin surface (/admin/catalog, /admin/combos), including the
credential-protection invariant: no response body may ever contain a
resolved api_key or a raw ${VAR} placeholder value."""

from __future__ import annotations

import httpx
import pytest

from llm_router.app import create_app
from tests.fakes import FakeProviders


def make_client(registry, catalog) -> httpx.AsyncClient:
    upstream_client = httpx.AsyncClient(transport=FakeProviders().transport())
    app = create_app(registry, catalog, http_client=upstream_client)
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
