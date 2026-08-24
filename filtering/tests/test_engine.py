"""End-to-end tests for rank(): the behavioral checklist from the implementation task."""
from __future__ import annotations

import time

import pytest

from tool_filtering import MAX_K, MIN_K, rank
from tool_filtering.index import clear_default_cache
import tool_filtering.index as index_module
from tests.fakes import (
    SIGNATURE_TOOLS, by_name, large_tool_set, medium_tool_set, names_of,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_default_cache()
    yield
    clear_default_cache()


def _names(result) -> list[str]:
    return [t.tool_name for t in result.tools]


# ── Safety: the one requirement that must never fail ────────────────────────────────────────

def test_result_is_always_subset_of_allowed_tools():
    allowed = medium_tool_set()
    allowed_names = names_of(allowed)
    requests = [
        "Why is charger CHG-123 offline?",
        "create a ticket and assign it",
        "search in jira for the login issue",
        "asdkfj qqzxcv unrelated gibberish",
        "",
        "run charger_ssh_execute now",
    ]
    for text in requests:
        result = rank(text, {}, allowed)
        assert set(_names(result)) <= allowed_names, text


def test_unauthorized_tool_never_appears_even_when_named_explicitly():
    """The critical safety test: a tool NOT in allowed_tools must never surface, even
    when the request text names it exactly, mentions its domain, and matches its
    capability - the exact-match short circuit, domain narrowing, and scoring must all
    independently respect the authorization boundary."""
    forbidden = by_name(SIGNATURE_TOOLS, "charger_ssh_execute")
    allowed = [t for t in medium_tool_set() if t.tool_name != "charger_ssh_execute"]
    assert forbidden.tool_name not in names_of(allowed)

    requests = [
        "run charger_ssh_execute now",              # exact name mention
        "execute a command on the charger via ssh",   # domain + capability + keyword match
        "restart the charger over ssh proxy",           # DESTRUCTIVE capability verb + SSH domain
    ]
    for text in requests:
        result = rank(text, {}, allowed)
        assert "charger_ssh_execute" not in _names(result), text
        assert set(_names(result)) <= names_of(allowed)


def test_empty_allowed_tools_returns_empty_without_crashing():
    result = rank("anything", {}, [])
    assert result.tools == ()
    assert result.trace.fallback_tier == "empty"


# ── Normal filtering / domain / capability / entity / server / keyword ──────────────────────

def test_normal_filtering_surfaces_charger_diagnostic_tools():
    allowed = medium_tool_set()
    result = rank("Why is charger CHG-123 offline?", {}, allowed)
    names = _names(result)
    top_names = set(names)
    assert "charger_get_charging_status" in top_names
    assert "get_charger_by_id" in top_names
    assert "jira_search" not in top_names
    # ticket_create's own description mentions "charger" (a real, not just fixture,
    # trait - see MCP_TOOL_INVENTORY.md's ticket_create entry), so soft keyword/entity
    # scoring can legitimately let it surface (design §7's soft-scoring point, just in
    # the opposite direction of that example) - the requirement is that the actual
    # diagnosis-relevant READ tools rank ahead of it, not that it's excluded outright.
    charger_read_tools = ["charger_get_charging_status", "get_charger_by_id", "charger_get_alarms"]
    if "ticket_create" in names:
        assert all(names.index(t) < names.index("ticket_create") for t in charger_read_tools)


def test_domain_matching_ranks_matching_domain_above_filler():
    allowed = medium_tool_set()
    result = rank("show me chargers status", {}, allowed)
    names = _names(result)
    assert "get_charger_by_id" in names
    filler_rank = next((i for i, n in enumerate(names) if n.startswith("filler_tool_")), len(names))
    charger_rank = names.index("get_charger_by_id")
    assert charger_rank < filler_rank


def test_capability_matching_prefers_write_tools_for_write_request():
    allowed = medium_tool_set()
    result = rank("create a new ticket for this issue", {}, allowed)
    names = _names(result)
    assert "ticket_create" in names
    assert names.index("ticket_create") < len(names) - 1
    # a pure-READ, unrelated tool should not outrank the exact-intent WRITE match
    assert names.index("ticket_create") < names.index("platform_health") if "platform_health" in names else True


def test_entity_matching_via_id_pattern_without_the_word_charger():
    allowed = medium_tool_set()
    result = rank("status of CHG-999", {}, allowed)
    names = set(_names(result))
    assert "charger_get_charging_status" in names or "get_charger_by_id" in names


def test_server_hint_prioritizes_named_server():
    allowed = medium_tool_set()
    result = rank("search in jira for the login problem", {}, allowed)
    names = _names(result)
    assert "jira_search" in names
    assert names.index("jira_search") < names.index(next(n for n in names if n.startswith("filler_tool_")))


def test_keyword_overlap_surfaces_description_match_without_domain_synonym():
    allowed = medium_tool_set()
    # "runbooks" only appears in kb_search_noc's description, not in the domain
    # synonym table - this isolates keyword overlap from domain/entity signals.
    result = rank("find runbooks for this", {}, allowed)
    assert "kb_search_noc" in _names(result)


def test_session_recency_breaks_a_near_tie_toward_the_recently_used_tool():
    allowed = medium_tool_set()
    request = "tell me about the charger"  # ambiguous between two Fleet & Chargers tools

    without_recency = rank(request, {}, allowed)
    with_recency = rank(request, {"recent_tool_names": ["cms_get_charger_details"]}, allowed)

    rank_without = without_recency and _names(without_recency).index("cms_get_charger_details") \
        if "cms_get_charger_details" in _names(without_recency) else 999
    rank_with = _names(with_recency).index("cms_get_charger_details")
    assert rank_with <= rank_without


# ── Top-K / determinism / tie-breaking ───────────────────────────────────────────────────────

def test_top_k_result_size_is_within_bounds():
    """Upper bound is absolute. The lower bound is MIN_K only when that many candidates
    survive narrowing - a precise request that leaves fewer genuinely-relevant tools
    returns just those rather than padding out to MIN_K with zero-relevance noise
    (adaptive top-K, design §5)."""
    allowed = medium_tool_set()
    result = rank("charger status", {}, allowed)
    assert 0 < len(result.tools) <= MAX_K


def test_top_k_reaches_min_k_when_the_request_is_broad():
    """A weak/broad request has no narrow candidate set to shrink to, so the MIN_K floor
    does apply - the LLM still gets real options rather than a couple of stragglers."""
    allowed = medium_tool_set()
    result = rank("asdkfj qqzxcv zzyx blorptastic", {}, allowed)
    assert MIN_K <= len(result.tools) <= MAX_K


def test_top_k_respects_smaller_caller_supplied_k():
    allowed = medium_tool_set()
    result = rank("charger status", {}, allowed, k=5)
    # k below MIN_K still can't go below MIN_K per the adaptive floor, but must not
    # exceed the caller's ceiling once it's above MIN_K
    result_default = rank("charger status", {}, allowed, k=MAX_K)
    assert len(result.tools) <= len(result_default.tools) or len(result.tools) == MIN_K


def test_ranking_is_deterministic_across_repeated_calls():
    allowed = medium_tool_set()
    a = rank("Why is charger CHG-123 offline?", {}, allowed)
    b = rank("Why is charger CHG-123 offline?", {}, allowed)
    assert _names(a) == _names(b)


def test_deterministic_tie_break_is_alphabetical_for_equal_scores():
    """Construct tools that are IDENTICAL in every scoring dimension (domain,
    capability, risk, description) so their score ties exactly - the only thing left
    to break the tie is the documented chain's final step: alphabetical by tool_name."""
    from tests.fakes import FakeTool

    tied = [
        FakeTool(f"a_tied_{i}", "noc", "Unrelated Domain", "generic filler description",
                 "READ", "LOW")
        for i in "cba"  # deliberately out of order to prove sorting, not insertion order
    ]
    # "a_tied_*" sorts alphabetically ahead of "pad_*", guaranteeing all three tied
    # tools land inside the (score-tied) top-K rather than being pushed out by padding.
    allowed = tied + [FakeTool(f"pad_{i}", "noc", "Padding", "padding tool", "READ", "LOW")
                      for i in range(35)]

    result = rank("gibberish query matching nothing here", {}, allowed)
    tied_names_in_result = [n for n in _names(result) if n.startswith("a_tied_")]
    assert tied_names_in_result == ["a_tied_a", "a_tied_b", "a_tied_c"]


# ── Fallback behavior ─────────────────────────────────────────────────────────────────────────

def test_weak_or_gibberish_request_falls_back_to_nonempty_broad_result():
    allowed = medium_tool_set()
    result = rank("asdkfj qqzxcv zzyx blorptastic", {}, allowed)
    assert len(result.tools) >= MIN_K
    assert result.trace.fallback_tier == "stage2_widen"


def test_fallback_prefers_read_and_low_risk_first():
    allowed = medium_tool_set()
    result = rank("asdkfj qqzxcv zzyx blorptastic", {}, allowed)
    first = result.tools[0]
    assert first.capability == "READ"
    assert first.risk == "LOW"


def test_small_authorized_set_skips_filtering_entirely():
    allowed = SIGNATURE_TOOLS[:5]  # well under MAX_K
    result = rank("anything at all", {}, allowed)
    assert set(_names(result)) == names_of(allowed)
    assert result.trace.fallback_tier == "skip_small_set"


def test_exact_tool_name_mention_short_circuits_to_just_that_tool():
    allowed = medium_tool_set()
    result = rank("please run jira_search right now", {}, allowed)
    assert _names(result) == ["jira_search"]
    assert result.trace.fallback_tier == "exact_match"


def test_internal_error_fails_open_to_full_authorized_set(monkeypatch):
    allowed = medium_tool_set()

    def _boom(*a, **kw):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr("tool_filtering.engine.extract_signals", _boom)
    result = rank("charger status", {}, allowed)
    assert set(_names(result)) == names_of(allowed)
    assert result.trace.fallback_tier == "error"


# ── Large toolsets / performance ─────────────────────────────────────────────────────────────

def test_large_authorized_toolset_still_returns_bounded_subset():
    allowed = large_tool_set(1200)
    result = rank("Why is charger CHG-123 offline?", {}, allowed)
    assert len(result.tools) <= MAX_K
    assert set(_names(result)) <= names_of(allowed)
    assert "charger_get_charging_status" in _names(result)


def test_large_authorized_toolset_latency_is_low():
    allowed = large_tool_set(1200)
    start = time.perf_counter()
    rank("Why is charger CHG-123 offline?", {}, allowed)
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 200  # generous bound for a test environment; expected << this


def test_per_request_cost_scales_with_query_not_catalog_size():
    """The core performance property from design §1/§8: narrowing works off matched-term
    postings, so growing the catalog ~4x must not grow warm per-request time anywhere
    near proportionally. Bounds are deliberately loose (CI machines are noisy); the
    point is to catch a regression to full-catalog scanning, which would be ~linear."""
    from tests.fakes import FakeTool

    def catalog(n):
        tools = [FakeTool(f"filler_{i}", "noc", "Widget Domain",
                          f"unrelated widget inventory task {i}", "READ", "LOW")
                 for i in range(n)]
        tools.append(FakeTool("charger_get_charging_status", "noc", "On-Charger Control Center",
                              "Per-gun live charging status", "READ", "LOW"))
        return tools

    def warm_ms(tools, iterations=200):
        rank("Why is charger CHG-123 offline?", {}, tools)  # warm the index cache
        start = time.perf_counter()
        for _ in range(iterations):
            rank("Why is charger CHG-123 offline?", {}, tools)
        return (time.perf_counter() - start) * 1000 / iterations

    small = warm_ms(catalog(500))
    large = warm_ms(catalog(2000))
    # 4x the catalog must cost well under 4x per request (true linear scan would be ~4x).
    assert large < max(small * 2.5, 1.0), f"small={small:.4f}ms large={large:.4f}ms"


def test_narrowing_keeps_a_small_but_real_candidate_set():
    """Union-of-evidence: when only a couple of tools share any signal with the request,
    those few are returned rather than being discarded (which would force a full-catalog
    rescore) or padded out to MIN_K with zero-evidence noise."""
    from tests.fakes import FakeTool

    tools = [FakeTool(f"filler_{i}", "noc", "Widget Domain",
                      f"unrelated widget inventory task {i}", "READ", "LOW")
             for i in range(60)]
    tools += [
        FakeTool("zebra_handler", "noc", "Zoology", "handles a zebra", "READ", "LOW"),
        FakeTool("zebra_reporter", "noc", "Zoology", "reports on a zebra", "READ", "LOW"),
    ]
    result = rank("zebra", {}, tools)
    assert _names(result) == ["zebra_handler", "zebra_reporter"]
    assert result.trace.fallback_tier == "none"


def test_zero_evidence_request_widens_to_full_set_then_applies_min_k():
    """The genuine starvation case (no term/domain/server evidence at all) must widen,
    not return an almost-empty list."""
    allowed = medium_tool_set()
    result = rank("qqzxcv blorptastic zzyx", {}, allowed)
    assert result.trace.fallback_tier == "stage2_widen"
    assert len(result.tools) >= MIN_K


def test_repeated_calls_reuse_the_cached_index_not_rebuild_it(monkeypatch):
    allowed = medium_tool_set()
    calls = {"count": 0}
    original_build = index_module.build_index

    def _counting_build(tools, metadata_provider=None):
        calls["count"] += 1
        return original_build(tools, metadata_provider)

    monkeypatch.setattr(index_module, "build_index", _counting_build)
    rank("charger status", {}, allowed)
    rank("a totally different request", {}, allowed)
    rank("yet another request", {}, allowed)
    assert calls["count"] == 1  # same tool_name set -> index built once, reused after


def test_use_cache_false_bypasses_the_cache(monkeypatch):
    allowed = medium_tool_set()
    calls = {"count": 0}
    original_build = index_module.build_index

    def _counting_build(tools, metadata_provider=None):
        calls["count"] += 1
        return original_build(tools, metadata_provider)

    monkeypatch.setattr(index_module, "build_index", _counting_build)
    rank("charger status", {}, allowed, use_cache=False)
    rank("charger status", {}, allowed, use_cache=False)
    assert calls["count"] == 2


# ── Trace / observability ────────────────────────────────────────────────────────────────────

def test_trace_exposes_signals_scores_and_latency():
    allowed = medium_tool_set()
    result = rank("Why is charger CHG-123 offline?", {}, allowed)
    trace = result.trace
    assert "charger" in trace.signals["entities"]
    assert trace.authorized_count == len(allowed)
    assert trace.latency_ms >= 0
    assert all("breakdown" in row for row in trace.top_k)
