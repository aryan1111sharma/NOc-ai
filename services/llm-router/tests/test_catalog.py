"""Tests for llm_router.catalog.Catalog.

Two data sources, both exercised here:

  * `endpoints=[...]` (static, in-memory) -- what every other test file's
    `catalog` fixture (tests/conftest.py) uses, so admin-CRUD/fallback tests
    never need a real LibreChat backend or network access.
  * the real path: an HTTP call to LibreChat's own internal LLM-routing
    catalog/resolve routes (apps/ai-platform/librechat/custom-image/files/
    api/llmRouting.js), scripted here with httpx.MockTransport -- this is
    what proves the Catalog asks LibreChat for the answer instead of
    re-parsing librechat.yaml or holding its own copy of a credential.
"""

from __future__ import annotations

import httpx
import pytest

from llm_router.catalog import Catalog

API_URL = "http://api:3080"


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# -- static `endpoints=` path (used by every other test file's fixtures) ----


@pytest.mark.asyncio
async def test_list_targets_expands_one_endpoint_into_one_target_per_model():
    catalog = Catalog(
        endpoints=[
            {
                "name": "Kimi",
                "apiKey": "${KIMI_API_KEY}",
                "baseURL": "https://kimi.example",
                "models": {"default": ["kimi-for-coding", "k3"]},
            }
        ]
    )
    targets = await catalog.list_targets()
    assert [(t.endpoint, t.model) for t in targets] == [
        ("Kimi", "kimi-for-coding"),
        ("Kimi", "k3"),
    ]


@pytest.mark.asyncio
async def test_list_targets_excludes_the_router_own_endpoint():
    catalog = Catalog(
        self_endpoint_name="NOC Router",
        endpoints=[
            {"name": "NOC Router", "baseURL": "http://llm-router:8110/v1", "models": {"default": ["noc-engineer"]}},
            {"name": "Kimi", "baseURL": "https://kimi.example", "models": {"default": ["k3"]}},
        ],
    )
    names = {t.endpoint for t in await catalog.list_targets()}
    assert names == {"Kimi"}


@pytest.mark.asyncio
async def test_resolve_expands_env_var_placeholder(monkeypatch):
    monkeypatch.setenv("KIMI_API_KEY", "sk-real-value")
    catalog = Catalog(
        endpoints=[
            {
                "name": "Kimi",
                "apiKey": "${KIMI_API_KEY}",
                "baseURL": "https://kimi.example",
                "models": {"default": ["k3"]},
            }
        ]
    )
    resolved = await catalog.resolve("Kimi", "k3")
    assert resolved.base_url == "https://kimi.example"
    assert resolved.api_key == "sk-real-value"


@pytest.mark.asyncio
async def test_resolve_returns_none_for_unregistered_pair():
    catalog = Catalog(
        endpoints=[{"name": "Kimi", "baseURL": "https://kimi.example", "models": {"default": ["k3"]}}]
    )
    assert await catalog.resolve("Kimi", "does-not-exist") is None
    assert await catalog.resolve("NoSuchEndpoint", "k3") is None


@pytest.mark.asyncio
async def test_masked_never_contains_a_key():
    catalog = Catalog(
        endpoints=[
            {
                "name": "Kimi",
                "apiKey": "${KIMI_API_KEY}",
                "baseURL": "https://kimi.example",
                "models": {"default": ["k3"]},
            }
        ]
    )
    targets = await catalog.list_targets()
    masked = targets[0].masked()
    assert masked == {"endpoint": "Kimi", "model": "k3"}
    assert "apiKey" not in masked
    assert "KIMI_API_KEY" not in str(masked)


# -- real path: HTTP against LibreChat's internal catalog/resolve routes ----


@pytest.mark.asyncio
async def test_list_targets_calls_librechats_internal_catalog_route():
    seen = {}

    def handler(request: httpx.Request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["secret"] = request.headers.get("x-internal-secret")
        return httpx.Response(
            200,
            json={
                "endpoints": [
                    {"name": "OpenRouter", "models": {"default": ["openrouter/auto"]}},
                    {"name": "Kimi", "models": {"default": ["kimi-for-coding"]}},
                ]
            },
        )

    catalog = Catalog(API_URL, internal_secret="s3cr3t", http_client=_client(handler))
    targets = await catalog.list_targets()

    assert seen["method"] == "GET"
    assert seen["url"] == f"{API_URL}/api/llm-routing/internal/catalog"
    assert seen["secret"] == "s3cr3t"
    assert [(t.endpoint, t.model) for t in targets] == [
        ("OpenRouter", "openrouter/auto"),
        ("Kimi", "kimi-for-coding"),
    ]


@pytest.mark.asyncio
async def test_list_targets_excludes_router_own_endpoint_over_http():
    def handler(request: httpx.Request):
        return httpx.Response(
            200,
            json={
                "endpoints": [
                    {"name": "NOC Router", "models": {"default": ["noc-engineer"]}},
                    {"name": "Kimi", "models": {"default": ["k3"]}},
                ]
            },
        )

    catalog = Catalog(API_URL, self_endpoint_name="NOC Router", http_client=_client(handler))
    names = {t.endpoint for t in await catalog.list_targets()}
    assert names == {"Kimi"}


@pytest.mark.asyncio
async def test_resolve_posts_endpoint_and_model_and_returns_real_credential():
    seen = {}

    def handler(request: httpx.Request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["secret"] = request.headers.get("x-internal-secret")
        seen["body"] = request.content
        return httpx.Response(200, json={"baseURL": "https://openrouter.ai/api/v1", "apiKey": "sk-or-real"})

    catalog = Catalog(API_URL, internal_secret="s3cr3t", http_client=_client(handler))
    resolved = await catalog.resolve("OpenRouter", "openrouter/auto")

    assert seen["method"] == "POST"
    assert seen["url"] == f"{API_URL}/api/llm-routing/internal/resolve"
    assert seen["secret"] == "s3cr3t"
    import json

    assert json.loads(seen["body"]) == {"endpoint": "OpenRouter", "model": "openrouter/auto"}
    assert resolved.base_url == "https://openrouter.ai/api/v1"
    assert resolved.api_key == "sk-or-real"


@pytest.mark.asyncio
async def test_resolve_returns_none_on_404_from_librechat():
    def handler(request: httpx.Request):
        return httpx.Response(404, json={"message": "not registered"})

    catalog = Catalog(API_URL, http_client=_client(handler))
    assert await catalog.resolve("Missing", "model") is None


@pytest.mark.asyncio
async def test_resolve_never_calls_out_for_the_routers_own_endpoint():
    def handler(request: httpx.Request):
        raise AssertionError("must not call out for the router's own endpoint")

    catalog = Catalog(API_URL, self_endpoint_name="NOC Router", http_client=_client(handler))
    assert await catalog.resolve("NOC Router", "noc-engineer") is None


@pytest.mark.asyncio
async def test_no_api_url_configured_yields_empty_catalog_not_a_crash():
    catalog = Catalog(None)
    assert await catalog.list_targets() == []
    assert await catalog.resolve("Kimi", "k3") is None
