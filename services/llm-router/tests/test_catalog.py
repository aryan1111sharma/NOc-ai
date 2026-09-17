"""Tests for llm_router.catalog.Catalog: the read-only view of
librechat.yaml's endpoints.custom list."""

from __future__ import annotations

from llm_router.catalog import Catalog


def test_list_targets_expands_one_endpoint_into_one_target_per_model():
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
    targets = catalog.list_targets()
    assert [(t.endpoint, t.model) for t in targets] == [
        ("Kimi", "kimi-for-coding"),
        ("Kimi", "k3"),
    ]


def test_list_targets_excludes_the_router_own_endpoint():
    catalog = Catalog(
        self_endpoint_name="NOC Router",
        endpoints=[
            {"name": "NOC Router", "baseURL": "http://llm-router:8110/v1", "models": {"default": ["noc-engineer"]}},
            {"name": "Kimi", "baseURL": "https://kimi.example", "models": {"default": ["k3"]}},
        ],
    )
    names = {t.endpoint for t in catalog.list_targets()}
    assert names == {"Kimi"}


def test_resolve_expands_env_var_placeholder(monkeypatch):
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
    resolved = catalog.resolve("Kimi", "k3")
    assert resolved.base_url == "https://kimi.example"
    assert resolved.api_key == "sk-real-value"


def test_resolve_returns_none_for_unregistered_pair():
    catalog = Catalog(
        endpoints=[{"name": "Kimi", "baseURL": "https://kimi.example", "models": {"default": ["k3"]}}]
    )
    assert catalog.resolve("Kimi", "does-not-exist") is None
    assert catalog.resolve("NoSuchEndpoint", "k3") is None


def test_masked_never_contains_a_key():
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
    masked = catalog.list_targets()[0].masked()
    assert masked == {"endpoint": "Kimi", "model": "k3"}
    assert "apiKey" not in masked
    assert "KIMI_API_KEY" not in str(masked)
