"""Unit tests for cheap, deterministic signal extraction (no rank() involved)."""
from __future__ import annotations

from tool_filtering.signals import (
    extract_entities, extract_exact_tool_name, extract_signals, infer_capabilities,
    infer_domains, infer_server_hint, tokenize,
)


def test_tokenize_lowercases_strips_punctuation_and_stopwords():
    terms = tokenize("Why is Charger CHG-123 offline?")
    assert "why" in terms
    assert "chg-123" in terms
    assert "is" not in terms  # stopword


def test_tokenize_keeps_underscored_tool_names_as_one_token():
    terms = tokenize("please run jira_search now")
    assert "jira_search" in terms


def test_extract_entities_detects_charger_id():
    assert "charger" in extract_entities("status of CHG-123 please")


def test_extract_entities_detects_ticket_key():
    assert "ticket" in extract_entities("see ECOG-456 for details")


def test_extract_entities_ambiguous_id_can_set_both():
    # CHG-123 also matches the generic uppercase-letters-dash-digits ticket shape -
    # both entities firing is intentional (soft signal, never a hard grant).
    entities = extract_entities("CHG-123")
    assert "charger" in entities


def test_extract_entities_no_match_is_empty():
    assert extract_entities("hello there") == frozenset()


def test_infer_domains_from_keyword():
    domains = infer_domains(frozenset({"ticket"}), frozenset())
    assert "Tickets & Support" in domains


def test_infer_domains_from_entity():
    domains = infer_domains(frozenset(), frozenset({"charger"}))
    assert "Fleet & Chargers" in domains


def test_infer_domains_unknown_term_yields_nothing():
    assert infer_domains(frozenset({"zzzznotarealword"}), frozenset()) == frozenset()


def test_infer_capabilities_read_verb():
    assert infer_capabilities(frozenset({"why", "status"})) == frozenset({"READ"})


def test_infer_capabilities_write_verb():
    assert "WRITE" in infer_capabilities(frozenset({"create"}))


def test_infer_capabilities_destructive_verb():
    assert "DESTRUCTIVE" in infer_capabilities(frozenset({"restart"}))


def test_infer_capabilities_can_combine():
    caps = infer_capabilities(frozenset({"show", "create"}))
    assert caps == frozenset({"READ", "WRITE"})


def test_infer_capabilities_no_verb_is_empty():
    assert infer_capabilities(frozenset({"banana"})) == frozenset()


def test_infer_server_hint_matches_known_phrase():
    assert infer_server_hint("search in jira for the login issue") == "jira"


def test_infer_server_hint_none_when_absent():
    assert infer_server_hint("why is the charger offline") is None


def test_infer_server_hint_prefers_more_specific_before_generic_noc():
    # "ecog" should resolve to jira (Ecog's vendor tracker), not fall through to "noc"
    assert infer_server_hint("check ecog for this ticket") == "jira"


def test_extract_exact_tool_name_found():
    allowed = frozenset({"jira_search", "ticket_create"})
    assert extract_exact_tool_name(frozenset({"please", "run", "jira_search"}), allowed) == "jira_search"


def test_extract_exact_tool_name_not_in_allowed_set_returns_none():
    """A name mentioned in the text but NOT in allowed_tool_names must never resolve -
    this is the mechanism that keeps the exact-match short circuit inside the
    authorization boundary."""
    allowed = frozenset({"jira_search"})
    assert extract_exact_tool_name(frozenset({"charger_ssh_execute"}), allowed) is None


def test_extract_exact_tool_name_absent_returns_none():
    assert extract_exact_tool_name(frozenset({"hello"}), frozenset({"jira_search"})) is None


def test_extract_signals_is_deterministic():
    allowed = frozenset({"jira_search"})
    a = extract_signals("Why is charger CHG-123 offline?", allowed)
    b = extract_signals("Why is charger CHG-123 offline?", allowed)
    assert a == b
