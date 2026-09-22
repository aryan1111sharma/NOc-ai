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


def test_new_combo_has_no_last_dispatch_yet(registry: Registry):
    combo = registry.upsert_combo(agent_id="agent-x", targets=[{"endpoint": "Kimi", "model": "k3"}])
    assert combo.last_target is None
    assert combo.last_attempted == ()
    assert combo.last_dispatched_at is None


def test_record_dispatch_sets_last_target_attempted_and_timestamp(registry: Registry):
    registry.upsert_combo(agent_id="agent-x", targets=[{"endpoint": "Kimi", "model": "k3"}])

    updated = registry.record_dispatch(
        "agent-x", target="Kimi/k3", attempted=["Kimi/k3"]
    )

    assert updated.last_target == "Kimi/k3"
    assert updated.last_attempted == ("Kimi/k3",)
    assert updated.last_dispatched_at is not None
    assert registry.get_combo("agent-x").last_target == "Kimi/k3"


def test_record_dispatch_captures_the_fallback_attempt_list(registry: Registry):
    """The whole point of the feature this supports: an admin can see that
    Kimi was tried and failed before GLM actually answered, not just that
    GLM answered."""
    registry.upsert_combo(
        agent_id="agent-x",
        targets=[{"endpoint": "Kimi", "model": "k3"}, {"endpoint": "GLM", "model": "glm-5.2"}],
    )

    updated = registry.record_dispatch(
        "agent-x", target="GLM/glm-5.2", attempted=["Kimi/k3", "GLM/glm-5.2"]
    )

    assert updated.last_target == "GLM/glm-5.2"
    assert updated.last_attempted == ("Kimi/k3", "GLM/glm-5.2")


def test_record_dispatch_on_unknown_agent_is_a_noop(registry: Registry):
    assert registry.record_dispatch("no-such-agent", target="Kimi/k3", attempted=["Kimi/k3"]) is None


def test_record_dispatch_does_not_touch_updated_at(registry: Registry):
    """updated_at reflects an admin config edit; a dispatch is not one."""
    combo = registry.upsert_combo(agent_id="agent-x", targets=[{"endpoint": "Kimi", "model": "k3"}])
    updated = registry.record_dispatch("agent-x", target="Kimi/k3", attempted=["Kimi/k3"])
    assert updated.updated_at == combo.updated_at


def test_last_dispatch_fields_persist_across_target_edits(registry: Registry):
    """Editing a Combo's targets (reorder, add, remove) is a config change,
    not a dispatch -- it must not silently wipe what last actually answered,
    the same way original_provider/original_model already survive an edit."""
    registry.upsert_combo(agent_id="agent-x", targets=[{"endpoint": "Kimi", "model": "k3"}])
    registry.record_dispatch("agent-x", target="Kimi/k3", attempted=["Kimi/k3"])

    edited = registry.upsert_combo(
        agent_id="agent-x", targets=[{"endpoint": "GLM", "model": "glm-5.2"}]
    )

    assert edited.last_target == "Kimi/k3"
    assert edited.last_attempted == ("Kimi/k3",)


def test_last_dispatch_fields_round_trip_through_json(registry: Registry, tmp_path):
    registry.upsert_combo(agent_id="agent-x", targets=[{"endpoint": "Kimi", "model": "k3"}])
    registry.record_dispatch("agent-x", target="Kimi/k3", attempted=["Kimi/k3", "GLM/glm-5.2"])

    combo = registry.get_combo("agent-x")
    assert combo.to_json()["last_target"] == "Kimi/k3"
    assert combo.to_json()["last_attempted"] == ["Kimi/k3", "GLM/glm-5.2"]


# -- usage (locally observed) ---------------------------------------------


def test_new_target_has_no_usage_yet(registry: Registry):
    assert registry.get_usage("Kimi", "k3") is None
    assert registry.list_usage() == []


def test_record_usage_success_without_tokens(registry: Registry):
    stats = registry.record_usage_success("Kimi", "k3")

    assert stats.request_count == 1
    assert stats.success_count == 1
    assert stats.failure_count == 0
    assert stats.last_success_at is not None
    assert stats.last_used_at is not None
    assert stats.last_failure_at is None
    assert stats.prompt_tokens == 0
    assert stats.completion_tokens == 0
    assert stats.total_tokens == 0


def test_record_usage_success_accumulates_tokens(registry: Registry):
    registry.record_usage_success(
        "Kimi", "k3", tokens={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    )
    stats = registry.record_usage_success(
        "Kimi", "k3", tokens={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
    )

    assert stats.request_count == 2
    assert stats.success_count == 2
    assert stats.prompt_tokens == 13
    assert stats.completion_tokens == 7
    assert stats.total_tokens == 20


def test_record_usage_failure_sets_failure_class(registry: Registry):
    stats = registry.record_usage_failure("Kimi", "k3", failure_class="429")

    assert stats.request_count == 1
    assert stats.failure_count == 1
    assert stats.success_count == 0
    assert stats.last_failure_class == "429"
    assert stats.last_failure_at is not None
    assert stats.last_success_at is None


def test_usage_tracks_success_and_failure_independently_per_target(registry: Registry):
    registry.record_usage_success("Kimi", "k3", tokens={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
    registry.record_usage_failure("Kimi", "k3", failure_class="500")
    registry.record_usage_failure("GLM", "glm-5.2", failure_class="429")

    kimi = registry.get_usage("Kimi", "k3")
    glm = registry.get_usage("GLM", "glm-5.2")

    assert kimi.request_count == 2
    assert kimi.success_count == 1
    assert kimi.failure_count == 1
    assert kimi.last_failure_class == "500"
    assert glm.request_count == 1
    assert glm.failure_count == 1
    assert glm.last_failure_class == "429"


def test_usage_persists_across_registry_instances(tmp_path):
    from llm_router.store import Registry as R

    path = tmp_path / "registry.json"
    R(path).record_usage_success("Kimi", "k3", tokens={"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10})

    reloaded = R(path).get_usage("Kimi", "k3")
    assert reloaded.total_tokens == 10
