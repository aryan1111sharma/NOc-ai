"""Tests for llm_router.store.Registry: Agent -> Combo persistence.

Registry itself does not know about the Catalog -- it enforces shape only
(non-empty targets, each with an endpoint+model, safe agent_id). Catalog
membership is validated one layer up, in llm_router.app, which is exercised
by test_app_admin.py.
"""

from __future__ import annotations

import pytest

from llm_router.store import Registry, RegistryError


def test_upsert_and_get_combo_round_trips(registry: Registry):
    combo = registry.upsert_combo(
        agent_id="agent_kimi26NOCEngineerV1",
        targets=[
            {"endpoint": "Kimi", "model": "kimi-for-coding"},
            {"endpoint": "Kimi", "model": "k3"},
        ],
    )
    assert combo.agent_id == "agent_kimi26NOCEngineerV1"
    assert [(t.endpoint, t.model) for t in combo.targets] == [
        ("Kimi", "kimi-for-coding"),
        ("Kimi", "k3"),
    ]

    fetched = registry.get_combo("agent_kimi26NOCEngineerV1")
    assert fetched == combo


def test_same_endpoint_can_repeat_with_different_models(registry: Registry):
    """The exact shape the feature exists for: one credential (endpoint),
    multiple models, each its own priority-ordered target."""
    combo = registry.upsert_combo(
        agent_id="agent-x",
        targets=[
            {"endpoint": "Kimi", "model": "kimi-for-coding"},
            {"endpoint": "Kimi", "model": "k3"},
            {"endpoint": "GLM", "model": "glm-5.2"},
        ],
    )
    assert len(combo.targets) == 3
    assert combo.targets[0].endpoint == combo.targets[1].endpoint == "Kimi"
    assert combo.targets[0].model != combo.targets[1].model


def test_multiple_endpoints_can_share_the_same_model_name(registry: Registry):
    combo = registry.upsert_combo(
        agent_id="agent-x",
        targets=[
            {"endpoint": "Kimi", "model": "k3"},
            {"endpoint": "GLM", "model": "k3"},
        ],
    )
    assert [t.endpoint for t in combo.targets] == ["Kimi", "GLM"]
    assert all(t.model == "k3" for t in combo.targets)


def test_target_order_is_preserved_as_priority(registry: Registry):
    combo = registry.upsert_combo(
        agent_id="agent-x",
        targets=[
            {"endpoint": "GLM", "model": "glm-5.2"},
            {"endpoint": "Kimi", "model": "kimi-for-coding"},
            {"endpoint": "Kimi", "model": "k3"},
        ],
    )
    assert [(t.endpoint, t.model) for t in combo.targets] == [
        ("GLM", "glm-5.2"),
        ("Kimi", "kimi-for-coding"),
        ("Kimi", "k3"),
    ]


def test_upsert_is_idempotent_by_agent_id(registry: Registry):
    registry.upsert_combo(agent_id="agent-x", targets=[{"endpoint": "Kimi", "model": "k3"}])
    registry.upsert_combo(
        agent_id="agent-x",
        targets=[{"endpoint": "GLM", "model": "glm-5.2"}],
    )
    assert len(registry.list_combos()) == 1
    assert registry.get_combo("agent-x").targets[0].endpoint == "GLM"


def test_a_target_can_be_individually_disabled(registry: Registry):
    combo = registry.upsert_combo(
        agent_id="agent-x",
        targets=[
            {"endpoint": "Kimi", "model": "k3", "enabled": False},
            {"endpoint": "GLM", "model": "glm-5.2", "enabled": True},
        ],
    )
    assert combo.targets[0].enabled is False
    assert combo.targets[1].enabled is True


def test_set_combo_enabled_toggles_the_whole_combo(registry: Registry):
    registry.upsert_combo(agent_id="agent-x", targets=[{"endpoint": "Kimi", "model": "k3"}])
    disabled = registry.set_combo_enabled("agent-x", False)
    assert disabled.enabled is False
    assert registry.get_combo("agent-x").enabled is False


def test_delete_combo_removes_it(registry: Registry):
    registry.upsert_combo(agent_id="agent-x", targets=[{"endpoint": "Kimi", "model": "k3"}])
    registry.delete_combo("agent-x")
    assert registry.get_combo("agent-x") is None


def test_delete_unknown_combo_raises(registry: Registry):
    with pytest.raises(RegistryError):
        registry.delete_combo("no-such-agent")


def test_empty_targets_is_rejected(registry: Registry):
    with pytest.raises(RegistryError):
        registry.upsert_combo(agent_id="agent-x", targets=[])


def test_unsafe_agent_id_is_rejected(registry: Registry):
    with pytest.raises(RegistryError):
        registry.upsert_combo(
            agent_id="../etc/passwd", targets=[{"endpoint": "Kimi", "model": "k3"}]
        )


def test_original_provider_and_model_persist_across_updates(registry: Registry):
    registry.upsert_combo(
        agent_id="agent-x",
        targets=[{"endpoint": "Kimi", "model": "k3"}],
        original_provider="Kimi",
        original_model="kimi-for-coding",
    )
    updated = registry.upsert_combo(
        agent_id="agent-x", targets=[{"endpoint": "GLM", "model": "glm-5.2"}]
    )
    assert updated.original_provider == "Kimi"
    assert updated.original_model == "kimi-for-coding"


def test_registry_persists_across_instances(tmp_path):
    path = tmp_path / "registry.json"
    Registry(path).upsert_combo(agent_id="agent-x", targets=[{"endpoint": "Kimi", "model": "k3"}])
    reloaded = Registry(path)
    assert reloaded.get_combo("agent-x").targets[0].endpoint == "Kimi"
