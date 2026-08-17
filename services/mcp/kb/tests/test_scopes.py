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


def test_confidential_quoted_string_raises():
    # YAML `confidential: "false"` is a non-empty string -> truthy if bool()-coerced.
    # That would silently flip a non-confidential scope into a confidential one.
    bad = {
        "scopes": {"hr": {"confidential": "false"}},
        "profiles": {"p": {"scopes": ["hr"], "description": "d"}},
    }
    with pytest.raises(ScopesConfigError, match="confidential"):
        parse_scopes(bad, source="test")


def test_confidential_bool_still_accepted():
    cfg = parse_scopes(
        {"scopes": {"hr": {"confidential": True}}, "profiles": {"p": {"scopes": ["hr"], "description": "d"}}},
        source="test",
    )
    assert cfg.scopes["hr"].confidential is True


def test_profile_duplicate_scope_raises():
    bad = {
        "scopes": {"a": {}},
        "profiles": {"p": {"scopes": ["a", "a"], "description": "d"}},
    }
    with pytest.raises(ScopesConfigError, match="a"):
        parse_scopes(bad, source="test")


def test_duplicate_folder_raises():
    bad = {
        "scopes": {"a": {"folder": "shared"}, "b": {"folder": "shared"}},
        "profiles": {"p": {"scopes": ["a"], "description": "d"}},
    }
    with pytest.raises(ScopesConfigError, match="shared"):
        parse_scopes(bad, source="test")


def test_numeric_scope_name_becomes_string():
    cfg = parse_scopes(
        {"scopes": {123: {}}, "profiles": {"p": {"scopes": [123], "description": "d"}}},
        source="test",
    )
    assert "123" in cfg.scopes
    assert isinstance(cfg.scopes["123"].name, str)


def test_duplicate_key_error_names_source_file(tmp_path):
    p = tmp_path / "scopes.yaml"
    p.write_text(
        "scopes:\n  a: {}\n  a: {}\nprofiles:\n  p:\n    scopes: [a]\n    description: d\n",
        encoding="utf-8",
    )
    with pytest.raises(ScopesConfigError, match=r"duplicate.*scopes\.yaml|scopes\.yaml.*duplicate"):
        load_scopes(p)


def test_min_score_boundary_values_accepted():
    for boundary in (0.0, 1.0):
        cfg = parse_scopes(
            {
                "scopes": {"a": {}},
                "profiles": {"p": {"scopes": ["a"], "description": "d", "min_score": boundary}},
            },
            source="test",
        )
        assert cfg.profiles["p"].min_score == boundary


def test_scope_unused_by_any_profile_is_fine():
    cfg = parse_scopes(
        {
            "scopes": {"a": {}, "unused": {}},
            "profiles": {"p": {"scopes": ["a"], "description": "d"}},
        },
        source="test",
    )
    assert "unused" in cfg.scopes


def test_profile_name_with_space_raises():
    """server.py embeds profile.name verbatim into the MCP tool name
    f"kb_search_{profile.name}"; FastMCP itself doesn't reject bad names, it
    just warns and registers a mangled tool, so a bad name must fail loudly
    here instead of surfacing as a silently-unreachable tool in production."""
    bad = {
        "scopes": {"a": {}},
        "profiles": {"my profile": {"scopes": ["a"], "description": "d"}},
    }
    with pytest.raises(ScopesConfigError, match="my profile"):
        parse_scopes(bad, source="test")


def test_profile_name_with_slash_raises():
    bad = {
        "scopes": {"a": {}},
        "profiles": {"noc/l1": {"scopes": ["a"], "description": "d"}},
    }
    with pytest.raises(ScopesConfigError, match="noc/l1"):
        parse_scopes(bad, source="test")


def test_profile_name_non_ascii_raises():
    bad = {
        "scopes": {"a": {}},
        "profiles": {"café": {"scopes": ["a"], "description": "d"}},
    }
    with pytest.raises(ScopesConfigError, match="allowed characters"):
        parse_scopes(bad, source="test")


def test_profile_name_with_underscore_and_hyphen_accepted():
    cfg = parse_scopes(
        {
            "scopes": {"a": {}},
            "profiles": {"noc_l1-tier": {"scopes": ["a"], "description": "d"}},
        },
        source="test",
    )
    assert "noc_l1-tier" in cfg.profiles


def test_scope_name_with_space_raises():
    """Scope names are identifiers (payload filter values, and collection-name
    material for confidential scopes), not filenames. An unsafe-but-unique
    name previously slugified to a working, merely odd collection name with
    no error at all -- so this must be caught at parse time, not left to
    StoreRegistry's collision detection, which only catches TWO unsafe names
    colliding, not one unsafe name on its own."""
    bad = {
        "scopes": {"my scope": {}},
        "profiles": {"p": {"scopes": ["my scope"], "description": "d"}},
    }
    with pytest.raises(ScopesConfigError, match="my scope"):
        parse_scopes(bad, source="test")


def test_scope_name_with_slash_raises():
    bad = {
        "scopes": {"ev/core": {}},
        "profiles": {"p": {"scopes": ["ev/core"], "description": "d"}},
    }
    with pytest.raises(ScopesConfigError, match="ev/core"):
        parse_scopes(bad, source="test")


def test_scope_name_non_ascii_raises():
    bad = {
        "scopes": {"héllo": {}},
        "profiles": {"p": {"scopes": ["héllo"], "description": "d"}},
    }
    with pytest.raises(ScopesConfigError, match="allowed characters"):
        parse_scopes(bad, source="test")


def test_scope_name_with_underscore_and_hyphen_accepted():
    cfg = parse_scopes(
        {
            "scopes": {"ev-core_v2": {}},
            "profiles": {"p": {"scopes": ["ev-core_v2"], "description": "d"}},
        },
        source="test",
    )
    assert "ev-core_v2" in cfg.scopes


def test_confidential_scope_with_unsafe_but_unique_name_raises():
    """Regression: a confidential scope named 'my scope' previously built with
    NO error at all -- StoreRegistry only raises when two DIFFERENT unsafe
    names slugify to the SAME collection (e.g. 'my scope' and 'my-scope' both
    -> kb_my_scope); a lone unsafe-but-unique name like 'my scope' or 'héllo'
    slugified to a working, merely odd collection name (kb_my_scope,
    kb_h_llo) and nothing ever caught it. It must now fail at parse time,
    before StoreRegistry is even built."""
    bad = {
        "scopes": {"my scope": {"confidential": True}},
        "profiles": {"p": {"scopes": ["my scope"], "description": "d"}},
    }
    with pytest.raises(ScopesConfigError, match="my scope"):
        parse_scopes(bad, source="test")
