"""LibreChat tool-name transformation and the reverse index onto the Tool Registry.

The end-to-end assertion (100% of every exported agent's tools resolving, zero
collisions) is the one that matters: it validates the rule against the live agent export
rather than against hand-written examples.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tool_filter_proxy.librechat_names import (
    build_reverse_index, librechat_server_key, split_server, to_librechat_name,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_AGENTS_JSON = _REPO_ROOT / "apps" / "ai-platform" / "librechat" / "agents" / "agents.json"


def test_short_name_gets_server_suffix():
    assert to_librechat_name("jira_search", "jira") == "jira_search_mcp_jira"


def test_name_is_cut_at_the_path_parameter_marker():
    """Not a length effect: this name is 49 chars, well inside the 64 limit, yet the
    `__charger_id__` segment is still dropped. A length-only rule mismatches it."""
    name = "get_charger_by_id_api_v1_chargers__charger_id__get"
    assert len(name) < 64
    assert to_librechat_name(name, "noc") == "get_charger_by_id_api_v1_chargers_mcp_noc"


def test_long_name_is_truncated_to_64_including_suffix():
    long = "get_my_effective_notification_preferences_api_v1_auth_me_notification_preferences_get"
    out = to_librechat_name(long, "noc")
    assert len(out) == 64
    assert out.endswith("_mcp_noc")


def test_hyphenated_server_is_aliased_to_underscore():
    assert librechat_server_key("ecog-sd") == "ecog_sd"
    assert to_librechat_name("sd_list_requests", "ecog-sd") == "sd_list_requests_mcp_ecog_sd"


def test_server_key_passthrough_when_no_alias():
    assert librechat_server_key("noc") == "noc"


def test_split_server_recovers_the_server():
    assert split_server("jira_search_mcp_jira") == "jira"
    assert split_server("sd_list_requests_mcp_ecog_sd") == "ecog_sd"


def test_split_server_returns_none_for_non_mcp_name():
    assert split_server("some_builtin_tool") is None


def test_split_server_uses_the_last_marker():
    """A base name that itself contains `_mcp_` must not confuse server recovery."""
    assert split_server("run_mcp_thing_mcp_noc") == "noc"


# ── The load-bearing check: against the real registry and the real agent export ────────────

@pytest.fixture(scope="module")
def reverse_index():
    from tool_registry import load_registry
    return build_reverse_index(load_registry())


def test_reverse_index_has_no_collisions(reverse_index):
    from tool_registry import load_registry
    # One entry per registry tool means no two tools mangled to the same name.
    assert len(reverse_index) == len(load_registry())


@pytest.mark.skipif(not _AGENTS_JSON.is_file(), reason="agents.json export not present")
def test_every_exported_agent_tool_resolves_to_a_registry_record(reverse_index):
    agents = json.loads(_AGENTS_JSON.read_text(encoding="utf-8"))
    checked = 0
    for agent in agents:
        # `sys__*` entries are LibreChat internals, not MCP tools.
        names = [t for t in (agent.get("tools") or []) if not t.startswith("sys__")]
        if not names:
            continue
        unresolved = [n for n in names if n not in reverse_index]
        assert not unresolved, f"{agent['id']}: {len(unresolved)} unresolved, e.g. {unresolved[:3]}"
        checked += len(names)
    assert checked > 500  # sanity: the export really was exercised


@pytest.mark.skipif(not _AGENTS_JSON.is_file(), reason="agents.json export not present")
def test_resolved_records_carry_usable_metadata(reverse_index):
    agents = json.loads(_AGENTS_JSON.read_text(encoding="utf-8"))
    agent = next(a for a in agents if a["id"] == "agent_kimi26NOCEngineerV1")
    for name in (t for t in agent["tools"] if not t.startswith("sys__")):
        rec = reverse_index[name]
        assert rec.capability in ("READ", "CONTROL", "WRITE", "DESTRUCTIVE")
        assert rec.risk in ("LOW", "MEDIUM", "HIGH", "CRITICAL")
        assert rec.domain
