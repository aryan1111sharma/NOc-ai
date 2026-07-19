import logging

import pytest

from kb_mcp.scopes import (
    ScopesConfig,
    ScopesConfigError,
    load_scopes,
    parse_scopes,
    warn_missing_folders,
)

VALID = {
    "scopes": {
        "ev-core": {"folder": "ev-core"},
        "chargezone": {"folder": "chargezone"},
        "hr": {"folder": "hr", "confidential": True},
    },
    "profiles": {
        "noc": {"scopes": ["ev-core"], "description": "EV/OCPP reference."},
        "chargezone": {
            "scopes": ["ev-core", "chargezone"],
            "description": "EV reference plus ChargeZone docs.",
            "min_score": 0.55,
        },
    },
}


def test_parse_valid_config():
    cfg = parse_scopes(VALID, source="test")
    assert isinstance(cfg, ScopesConfig)
    assert cfg.scopes["hr"].confidential is True
    assert cfg.scopes["ev-core"].confidential is False
    assert cfg.profiles["chargezone"].scopes == ("ev-core", "chargezone")
    assert cfg.profiles["chargezone"].min_score == 0.55
    assert cfg.profiles["noc"].min_score is None


def test_folder_defaults_to_scope_name():
    cfg = parse_scopes(
        {"scopes": {"ops": {}}, "profiles": {"p": {"scopes": ["ops"], "description": "d"}}},
        source="test",
    )
    assert cfg.scopes["ops"].folder == "ops"


def test_scope_for_folder_lookup():
    cfg = parse_scopes(VALID, source="test")
    assert cfg.scope_for_folder("hr").name == "hr"
    assert cfg.scope_for_folder("nope") is None


def test_profile_referencing_undeclared_scope_raises():
    bad = {
        "scopes": {"ev-core": {}},
        "profiles": {"noc": {"scopes": ["typo-core"], "description": "d"}},
    }
    with pytest.raises(ScopesConfigError, match="typo-core"):
        parse_scopes(bad, source="test")


def test_profile_without_description_raises():
    bad = {"scopes": {"a": {}}, "profiles": {"p": {"scopes": ["a"]}}}
    with pytest.raises(ScopesConfigError, match="description"):
        parse_scopes(bad, source="test")


def test_profile_with_empty_scopes_raises():
    bad = {"scopes": {"a": {}}, "profiles": {"p": {"scopes": [], "description": "d"}}}
    with pytest.raises(ScopesConfigError, match="at least one scope"):
        parse_scopes(bad, source="test")


def test_missing_sections_raise():
    with pytest.raises(ScopesConfigError, match="scopes"):
        parse_scopes({"profiles": {}}, source="test")
    with pytest.raises(ScopesConfigError, match="profiles"):
        parse_scopes({"scopes": {"a": {}}}, source="test")


def test_invalid_min_score_raises():
    bad = {
        "scopes": {"a": {}},
        "profiles": {"p": {"scopes": ["a"], "description": "d", "min_score": 5}},
    }
    with pytest.raises(ScopesConfigError, match="min_score"):
        parse_scopes(bad, source="test")


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(ScopesConfigError, match="not found"):
        load_scopes(tmp_path / "nope.yaml")


def test_load_malformed_yaml_raises(tmp_path):
    p = tmp_path / "scopes.yaml"
    p.write_text("scopes: [unclosed\n", encoding="utf-8")
    with pytest.raises(ScopesConfigError, match="valid YAML"):
        load_scopes(p)


def test_duplicate_key_raises(tmp_path):
    p = tmp_path / "scopes.yaml"
    p.write_text(
        "scopes:\n  a: {}\n  a: {}\nprofiles:\n  p:\n    scopes: [a]\n    description: d\n",
        encoding="utf-8",
    )
    with pytest.raises(ScopesConfigError, match="duplicate"):
        load_scopes(p)


def test_load_round_trip(tmp_path):
    p = tmp_path / "scopes.yaml"
    p.write_text(
        "scopes:\n"
        "  ev-core: {folder: ev-core}\n"
        "profiles:\n"
        "  noc:\n"
        "    scopes: [ev-core]\n"
        "    description: EV reference.\n",
        encoding="utf-8",
    )
    cfg = load_scopes(p)
    assert cfg.profiles["noc"].description == "EV reference."


def test_warn_missing_folders_warns_but_does_not_raise(tmp_path, caplog):
    cfg = parse_scopes({"scopes": {"ghost": {}}, "profiles": {"p": {"scopes": ["ghost"], "description": "d"}}}, source="test")
    with caplog.at_level(logging.WARNING):
        warn_missing_folders(cfg, tmp_path)
    assert "ghost" in caplog.text
