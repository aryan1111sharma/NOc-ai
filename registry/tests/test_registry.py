"""Validates the Tool Registry: coverage against the real repo data, schema-enforced
separation between system metadata and admin config, and the get/set access layer.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tool_registry import (
    ConfigError, MetadataError, RegistryError, VALID_CAPABILITIES, VALID_RISKS,
    YamlToolConfigStore, load_metadata, load_registry,
)
from tool_registry.config_store import CONFIG_SCHEMA_PATH
from tool_registry.metadata import METADATA_SCHEMA_PATH

REGISTRY_ROOT = Path(__file__).resolve().parent.parent
REAL_METADATA_DIR = REGISTRY_ROOT / "metadata"
REAL_CONFIG_PATH = REGISTRY_ROOT / "config" / "tool_config.yaml"

EXPECTED_TOTAL = 233
EXPECTED_PER_SERVER = {
    "noc": 184, "cms": 17, "jira": 5, "chargezone": 8,
    "ecog-sd": 5, "kb": 3, "docgen": 3, "platform": 8,
}


# ── Real-data integration: "registry loads successfully" / "all tools represented" ──────────

def test_real_registry_loads_successfully():
    registry = load_registry()
    assert len(registry) == EXPECTED_TOTAL


def test_real_metadata_matches_inventory_count_per_server():
    metadata = load_metadata()
    assert len(metadata) == EXPECTED_TOTAL
    counts = {}
    for m in metadata.values():
        counts[m.mcp_server] = counts.get(m.mcp_server, 0) + 1
    assert counts == EXPECTED_PER_SERVER


def test_real_metadata_has_no_duplicate_tool_names():
    # load_metadata() already raises on a duplicate; a dict of the expected size is
    # itself proof no collision silently overwrote an entry.
    metadata = load_metadata()
    assert len(metadata) == len(set(metadata))  # dict keys are already unique by construction
    assert len(metadata) == EXPECTED_TOTAL


def test_every_real_tool_has_valid_capability_and_risk():
    for meta in load_metadata().values():
        assert meta.capability in VALID_CAPABILITIES, meta.tool_name
        assert meta.risk in VALID_RISKS, meta.tool_name


def test_every_real_tool_has_config_entry():
    store = YamlToolConfigStore()
    configs = store.all()
    metadata = load_metadata()
    assert set(configs) == set(metadata)
    for cfg in configs.values():
        assert isinstance(cfg.enabled, bool)
        assert isinstance(cfg.approval_required, bool)


def test_known_critical_tool_charger_ssh_execute():
    registry = load_registry()
    rec = registry["charger_ssh_execute"]
    assert rec.capability == "DESTRUCTIVE"
    assert rec.risk == "CRITICAL"
    assert rec.approval_required is True
    assert rec.enabled is True


def test_known_read_tool_jira_search():
    registry = load_registry()
    rec = registry["jira_search"]
    assert rec.mcp_server == "jira"
    assert rec.capability == "READ"
    assert rec.risk == "LOW"
    assert rec.approval_required is False


def test_known_write_tool_ticket_create():
    registry = load_registry()
    rec = registry["ticket_create"]
    assert rec.mcp_server == "noc"
    assert rec.capability == "WRITE"
    assert rec.risk == "LOW"


def test_no_agent_or_permission_fields_anywhere_in_registry_data():
    """The design explicitly forbids agent names/permissions inside the registry -
    a raw text scan across every YAML file is a cheap, direct regression guard."""
    forbidden = ("agent_id", "agent_name", "agent_permissions", "allowed_agents")
    for path in list(REAL_METADATA_DIR.glob("*.yaml")) + [REAL_CONFIG_PATH]:
        text = path.read_text(encoding="utf-8").lower()
        for term in forbidden:
            assert term not in text, f"{path} unexpectedly contains {term!r}"


# ── Structural immutability: schema physically forbids cross-store fields ───────────────────

def test_metadata_schema_rejects_enabled_field(tmp_path: Path):
    bad_dir = tmp_path / "metadata"
    bad_dir.mkdir()
    (bad_dir / "bad.yaml").write_text(yaml.safe_dump({
        "schema_version": 1,
        "mcp_server": "bad",
        "tools": [{
            "tool_name": "x", "domain": "d", "description": "d",
            "capability": "READ", "risk": "LOW",
            "enabled": True,  # not allowed here - admin-managed field leaking into metadata
        }],
    }), encoding="utf-8")

    with pytest.raises(MetadataError, match="schema violation"):
        load_metadata(bad_dir, METADATA_SCHEMA_PATH)


def test_metadata_schema_rejects_approval_required_field(tmp_path: Path):
    bad_dir = tmp_path / "metadata"
    bad_dir.mkdir()
    (bad_dir / "bad.yaml").write_text(yaml.safe_dump({
        "schema_version": 1,
        "mcp_server": "bad",
        "tools": [{
            "tool_name": "x", "domain": "d", "description": "d",
            "capability": "READ", "risk": "LOW",
            "approval_required": False,
        }],
    }), encoding="utf-8")

    with pytest.raises(MetadataError, match="schema violation"):
        load_metadata(bad_dir, METADATA_SCHEMA_PATH)


def test_config_schema_rejects_risk_field(tmp_path: Path):
    bad_path = tmp_path / "tool_config.yaml"
    bad_path.write_text(yaml.safe_dump({
        "schema_version": 1,
        "tools": {"x": {"enabled": True, "approval_required": False, "risk": "CRITICAL"}},
    }), encoding="utf-8")

    with pytest.raises(ConfigError, match="schema violation"):
        YamlToolConfigStore(bad_path, CONFIG_SCHEMA_PATH)


def test_config_schema_rejects_capability_field(tmp_path: Path):
    bad_path = tmp_path / "tool_config.yaml"
    bad_path.write_text(yaml.safe_dump({
        "schema_version": 1,
        "tools": {"x": {"enabled": True, "approval_required": False, "capability": "WRITE"}},
    }), encoding="utf-8")

    with pytest.raises(ConfigError, match="schema violation"):
        YamlToolConfigStore(bad_path, CONFIG_SCHEMA_PATH)


def test_duplicate_tool_name_across_metadata_files_raises(tmp_path: Path):
    d = tmp_path / "metadata"
    d.mkdir()
    entry = lambda server: {
        "schema_version": 1, "mcp_server": server,
        "tools": [{"tool_name": "dup", "domain": "d", "description": "d",
                   "capability": "READ", "risk": "LOW"}],
    }
    (d / "a.yaml").write_text(yaml.safe_dump(entry("a")), encoding="utf-8")
    (d / "b.yaml").write_text(yaml.safe_dump(entry("b")), encoding="utf-8")

    with pytest.raises(MetadataError, match="already defined"):
        load_metadata(d, METADATA_SCHEMA_PATH)


def test_invalid_capability_value_raises(tmp_path: Path):
    d = tmp_path / "metadata"
    d.mkdir()
    (d / "a.yaml").write_text(yaml.safe_dump({
        "schema_version": 1, "mcp_server": "a",
        "tools": [{"tool_name": "x", "domain": "d", "description": "d",
                   "capability": "DELETE_EVERYTHING", "risk": "LOW"}],
    }), encoding="utf-8")

    with pytest.raises(MetadataError):
        load_metadata(d, METADATA_SCHEMA_PATH)


# ── Config store get/set behavior ────────────────────────────────────────────────────────────

@pytest.fixture
def isolated_config(tmp_path: Path) -> Path:
    path = tmp_path / "tool_config.yaml"
    path.write_text(yaml.safe_dump({
        "schema_version": 1,
        "tools": {
            "alpha": {"enabled": True, "approval_required": False},
            "beta": {"enabled": True, "approval_required": True},
        },
    }), encoding="utf-8")
    return path


def test_config_store_get(isolated_config: Path):
    store = YamlToolConfigStore(isolated_config, CONFIG_SCHEMA_PATH)
    cfg = store.get("beta")
    assert cfg.enabled is True
    assert cfg.approval_required is True


def test_config_store_get_missing_tool_raises(isolated_config: Path):
    store = YamlToolConfigStore(isolated_config, CONFIG_SCHEMA_PATH)
    with pytest.raises(ConfigError):
        store.get("does-not-exist")


def test_config_store_set_missing_tool_raises(isolated_config: Path):
    store = YamlToolConfigStore(isolated_config, CONFIG_SCHEMA_PATH)
    with pytest.raises(ConfigError):
        store.set("does-not-exist", enabled=False, actor="tester")


def test_config_store_set_requires_at_least_one_field(isolated_config: Path):
    store = YamlToolConfigStore(isolated_config, CONFIG_SCHEMA_PATH)
    with pytest.raises(ConfigError):
        store.set("alpha", actor="tester")


def test_config_store_set_persists_and_stamps_audit_fields(isolated_config: Path):
    store = YamlToolConfigStore(isolated_config, CONFIG_SCHEMA_PATH)
    updated = store.set("alpha", approval_required=True, actor="admin@example.com")
    assert updated.approval_required is True
    assert updated.updated_by == "admin@example.com"
    assert updated.updated_at  # timestamp stamped

    # persisted to disk, not just in-memory
    reloaded = YamlToolConfigStore(isolated_config, CONFIG_SCHEMA_PATH)
    assert reloaded.get("alpha").approval_required is True

    # untouched sibling entry proves set() only rewrites the one tool it targeted
    assert reloaded.get("beta").approval_required is True
    assert reloaded.get("beta").enabled is True


def test_config_store_set_cannot_express_capability_or_risk(isolated_config: Path):
    """There is no keyword for it - this proves the method signature itself, not just
    the schema, has no path to a capability/risk change."""
    import inspect
    sig = inspect.signature(YamlToolConfigStore.set)
    assert "capability" not in sig.parameters
    assert "risk" not in sig.parameters


# ── Registry-level integrity checks ──────────────────────────────────────────────────────────

def test_registry_rejects_orphan_config_entry(tmp_path: Path):
    metadata_dir = tmp_path / "metadata"
    metadata_dir.mkdir()
    (metadata_dir / "a.yaml").write_text(yaml.safe_dump({
        "schema_version": 1, "mcp_server": "a",
        "tools": [{"tool_name": "known", "domain": "d", "description": "d",
                   "capability": "READ", "risk": "LOW"}],
    }), encoding="utf-8")

    config_path = tmp_path / "tool_config.yaml"
    config_path.write_text(yaml.safe_dump({
        "schema_version": 1,
        "tools": {
            "known": {"enabled": True, "approval_required": False},
            "ghost": {"enabled": True, "approval_required": False},  # no metadata for this
        },
    }), encoding="utf-8")

    with pytest.raises(RegistryError, match="ghost"):
        load_registry(metadata_dir, METADATA_SCHEMA_PATH, config_path, CONFIG_SCHEMA_PATH)


def test_registry_applies_risk_default_when_config_row_missing(tmp_path: Path):
    metadata_dir = tmp_path / "metadata"
    metadata_dir.mkdir()
    (metadata_dir / "a.yaml").write_text(yaml.safe_dump({
        "schema_version": 1, "mcp_server": "a",
        "tools": [{"tool_name": "unseeded", "domain": "d", "description": "d",
                   "capability": "DESTRUCTIVE", "risk": "CRITICAL"}],
    }), encoding="utf-8")

    config_path = tmp_path / "tool_config.yaml"
    config_path.write_text(yaml.safe_dump({"schema_version": 1, "tools": {}}), encoding="utf-8")

    registry = load_registry(metadata_dir, METADATA_SCHEMA_PATH, config_path, CONFIG_SCHEMA_PATH)
    rec = registry["unseeded"]
    assert rec.enabled is True
    assert rec.approval_required is True  # CRITICAL risk defaults to requiring approval
