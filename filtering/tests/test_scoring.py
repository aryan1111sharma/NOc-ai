"""Unit tests for the fixed-weight scoring formula and deterministic tie-breaking."""
from __future__ import annotations

from tool_filtering.scoring import ScoredTool, score_tool, sort_key
from tool_filtering.signals import RequestSignals
from tests.fakes import FakeTool


def _signals(**kw) -> RequestSignals:
    base = dict(terms=frozenset(), entities=frozenset(), domains=frozenset(),
                capabilities=frozenset(), server_hint=None, exact_tool_name=None)
    base.update(kw)
    return RequestSignals(**base)


def test_domain_match_contributes_score():
    tool = FakeTool("t1", "noc", "Fleet & Chargers", "get a charger", "READ", "LOW")
    hit = score_tool(tool, frozenset({"get", "a", "charger"}), _signals(domains=frozenset({"Fleet & Chargers"})), frozenset())
    miss = score_tool(tool, frozenset({"get", "a", "charger"}), _signals(domains=frozenset({"Tickets & Support"})), frozenset())
    assert hit.score > miss.score
    assert hit.breakdown["domain"] > 0
    assert miss.breakdown["domain"] == 0


def test_capability_match_contributes_score():
    tool = FakeTool("t1", "noc", "d", "desc", "WRITE", "LOW")
    hit = score_tool(tool, frozenset(), _signals(capabilities=frozenset({"WRITE"})), frozenset())
    miss = score_tool(tool, frozenset(), _signals(capabilities=frozenset({"READ"})), frozenset())
    assert hit.breakdown["capability"] > 0
    assert miss.breakdown["capability"] == 0


def test_entity_match_is_lexical_not_just_domain():
    """entity_hit fires from the entity NAME appearing in the tool's own terms,
    independent of the domain field - a robustness net, not a duplicate of domain_hit."""
    tool = FakeTool("t1", "noc", "Some Other Domain", "reads charger data", "READ", "LOW")
    scored = score_tool(tool, frozenset({"reads", "charger", "data"}),
                        _signals(entities=frozenset({"charger"}), domains=frozenset()), frozenset())
    assert scored.breakdown["entity"] > 0
    assert scored.breakdown["domain"] == 0  # domain didn't match, entity still did


def test_server_match_contributes_score():
    tool = FakeTool("t1", "jira", "d", "desc", "READ", "LOW")
    hit = score_tool(tool, frozenset(), _signals(server_hint="jira"), frozenset())
    miss = score_tool(tool, frozenset(), _signals(server_hint="cms"), frozenset())
    assert hit.breakdown["server"] > 0
    assert miss.breakdown["server"] == 0


def test_keyword_overlap_scales_with_shared_terms():
    tool_terms = frozenset({"search", "issues", "jql"})
    full_overlap = score_tool(FakeTool("t1", "s", "d", "desc", "READ", "LOW"), tool_terms,
                              _signals(terms=frozenset({"search", "issues"})), frozenset())
    no_overlap = score_tool(FakeTool("t1", "s", "d", "desc", "READ", "LOW"), tool_terms,
                            _signals(terms=frozenset({"unrelated", "words"})), frozenset())
    assert full_overlap.breakdown["keyword"] > no_overlap.breakdown["keyword"]
    assert no_overlap.breakdown["keyword"] == 0


def test_recency_bonus_applies_only_when_recently_used():
    tool = FakeTool("t1", "s", "d", "desc", "READ", "LOW")
    recent = score_tool(tool, frozenset(), _signals(), frozenset({"t1"}))
    not_recent = score_tool(tool, frozenset(), _signals(), frozenset({"other_tool"}))
    assert recent.breakdown["recency"] > not_recent.breakdown["recency"]


def test_risk_tiebreak_is_small_and_never_dominates_a_real_match():
    low = FakeTool("low_risk", "s", "d", "desc", "READ", "LOW")
    critical = FakeTool("critical_risk", "s", "d", "desc", "READ", "CRITICAL")
    signals = _signals(domains=frozenset(), capabilities=frozenset({"READ"}))
    low_score = score_tool(low, frozenset(), signals, frozenset())
    critical_score = score_tool(critical, frozenset(), signals, frozenset())
    # both get the same capability credit; only the tiny risk tiebreak differs
    assert low_score.score > critical_score.score
    assert abs(low_score.score - critical_score.score) < 1.0  # small nudge, not a veto


def test_score_is_deterministic():
    tool = FakeTool("t1", "noc", "Fleet & Chargers", "get a charger", "READ", "LOW")
    signals = _signals(domains=frozenset({"Fleet & Chargers"}), terms=frozenset({"charger"}))
    a = score_tool(tool, frozenset({"get", "a", "charger"}), signals, frozenset())
    b = score_tool(tool, frozenset({"get", "a", "charger"}), signals, frozenset())
    assert a == b


def test_sort_key_tiebreak_prefers_lower_risk_then_capability_then_alphabetical():
    by_name = {
        "b_tool": FakeTool("b_tool", "s", "d", "desc", "WRITE", "LOW"),
        "a_tool": FakeTool("a_tool", "s", "d", "desc", "WRITE", "LOW"),
        "critical_tool": FakeTool("critical_tool", "s", "d", "desc", "READ", "CRITICAL"),
    }
    scored = [
        ScoredTool("critical_tool", 5.0, {}),
        ScoredTool("b_tool", 5.0, {}),
        ScoredTool("a_tool", 5.0, {}),
    ]
    ordered = sorted(scored, key=lambda s: sort_key(s, by_name))
    # all tied at score=5.0: LOW risk beats CRITICAL risk first...
    assert ordered[0].tool_name in ("a_tool", "b_tool")
    # ...then alphabetical breaks the LOW-risk tie between a_tool and b_tool
    assert [s.tool_name for s in ordered[:2]] == ["a_tool", "b_tool"]
    assert ordered[2].tool_name == "critical_tool"
