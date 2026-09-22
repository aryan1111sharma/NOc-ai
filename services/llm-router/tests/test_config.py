"""Tests for llm_router.config.Settings, scoped to the one new piece of
config this task added: the FREE/PAID/UNKNOWN provider metadata mechanism.
Everything else in Settings is exercised indirectly by every other test
module via the `registry`/`catalog` fixtures.
"""

from __future__ import annotations

from llm_router.config import Settings


def test_provider_cost_types_defaults_to_empty(monkeypatch):
    monkeypatch.delenv("LLM_ROUTER_PROVIDER_COST_TYPES", raising=False)
    settings = Settings()
    assert settings.provider_cost_types == {}
    assert settings.cost_type_for("Kimi") == "UNKNOWN"


def test_provider_cost_types_parsed_from_env_as_json(monkeypatch):
    """The real production path: an operator sets one JSON env var, no new
    file or admin UI needed."""
    monkeypatch.setenv("LLM_ROUTER_PROVIDER_COST_TYPES", '{"GLM": "PAID", "Kimi": "PAID"}')
    settings = Settings()
    assert settings.provider_cost_types == {"GLM": "PAID", "Kimi": "PAID"}
    assert settings.cost_type_for("GLM") == "PAID"
    assert settings.cost_type_for("Kimi") == "PAID"


def test_cost_type_for_unregistered_endpoint_is_unknown(monkeypatch):
    monkeypatch.setenv("LLM_ROUTER_PROVIDER_COST_TYPES", '{"GLM": "PAID"}')
    settings = Settings()
    assert settings.cost_type_for("OpenRouter") == "UNKNOWN"


def test_cost_type_for_rejects_invalid_values():
    settings = Settings(provider_cost_types={"GLM": "cheap"})
    assert settings.cost_type_for("GLM") == "UNKNOWN"
