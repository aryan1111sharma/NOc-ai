"""Evidence specificity: how much a matched facet actually narrows the catalogue.

The scoring weights (scoring.py) say how much a *kind* of evidence is worth. They cannot
say how much a *particular* match is worth, and those are different questions: in a
catalogue of 136 tools, "this tool is in domain X" is near-worthless when X holds 29
tools and decisive when it holds 3.

These tests pin the general property - rarer evidence outranks commoner evidence of the
same kind - using synthetic catalogues only. No real server, domain or query appears
here; the regression tests for the observed production failures live beside the proxy.
"""
from __future__ import annotations



from tool_filtering import rank
from tool_filtering.index import build_index
from tool_filtering.stats import specificity
from tests.fakes import FakeTool


# ── the measure itself ────────────────────────────────────────────────────────────────

def test_specificity_is_inverse_to_bucket_size():
    """A facet held by fewer tools must weigh more than one held by many."""
    assert specificity(df=2, total=100) > specificity(df=50, total=100)


def test_bucket_covering_the_whole_catalogue_contributes_nothing():
    """Evidence that matches everything has narrowed nothing, so it must score zero -
    otherwise a catalogue-wide facet (e.g. capability READ on a read-only surface) adds a
    constant to every tool and only dilutes the signals that do discriminate."""
    assert specificity(df=100, total=100) == 0.0


def test_singleton_bucket_gets_full_weight():
    assert specificity(df=1, total=100) == 1.0


def test_specificity_is_bounded_to_unit_interval():
    for df in range(1, 101):
        assert 0.0 <= specificity(df=df, total=100) <= 1.0


def test_specificity_degrades_safely_on_absent_or_degenerate_buckets():
    """Never divide by zero, never return a negative weight - a missing bucket must be
    inert, not an exception, because ranking must not be able to crash the caller."""
    assert specificity(df=0, total=100) == 1.0
    assert specificity(df=5, total=0) == 1.0
    assert specificity(df=5, total=1) == 1.0


def test_relative_ordering_survives_catalogue_growth():
    """Adding MCP servers must not reorder which evidence counts as the more specific.

    Deliberately NOT scale-invariance: a log measure is required (a linear 1-df/total
    does not separate a 29-of-136 domain from an 8-of-136 server strongly enough to fix
    the real failure), and log(total/df)/log(total) is not scale-free. What must hold is
    that the *ordering* is stable as the catalogue grows, so a new MCP server cannot
    silently invert two existing weights.
    """
    small_catalogue = (specificity(df=8, total=136), specificity(df=29, total=136))
    grown_catalogue = (specificity(df=8, total=1360), specificity(df=29, total=1360))
    assert small_catalogue[0] > small_catalogue[1]
    assert grown_catalogue[0] > grown_catalogue[1]


def test_specificity_falls_as_a_bucket_absorbs_more_of_the_catalogue():
    weights = [specificity(df=df, total=100) for df in (1, 5, 25, 50, 99)]
    assert weights == sorted(weights, reverse=True)


# ── the index exposes it ──────────────────────────────────────────────────────────────

def _catalogue(broad_n: int, specialist_n: int):
    """A catalogue shaped like the real one: one big server whose tools share a broad
    domain, plus a small specialist server on its own domain."""
    broad = [
        FakeTool(f"bulk_ticket_op_{i:02d}", "bulk", "Tickets & Support",
                 f"Bulk ticket operation number {i}", "READ", "LOW")
        for i in range(broad_n)
    ]
    specialist = [
        FakeTool(f"spec_issue_op_{i:02d}", "jira", "Issue Tracking",
                 f"Specialist issue operation number {i}", "READ", "LOW")
        for i in range(specialist_n)
    ]
    return broad + specialist


def test_index_measures_specificity_per_facet_bucket():
    index = build_index(_catalogue(40, 4))
    assert index.stats.domain_weight("Issue Tracking") > index.stats.domain_weight("Tickets & Support")
    assert index.stats.server_weight("jira") > index.stats.server_weight("bulk")


def test_index_gives_a_catalogue_wide_capability_no_weight():
    index = build_index(_catalogue(40, 4))
    assert index.stats.capability_weight("READ") == 0.0


def test_index_measures_term_rarity():
    """A term in every tool's text must weigh less than one in a handful."""
    index = build_index(_catalogue(40, 4))
    assert index.stats.term_weight("specialist") > index.stats.term_weight("operation")


# ── the property that matters ─────────────────────────────────────────────────────────

def _specialist_server_catalogue():
    """A small server whose own domain is NOT reachable from the request's words, so its
    only evidence is the server hint - exactly the shape of the observed failure."""
    return [
        FakeTool(f"bulk_ticket_op_{i:02d}", "bulk", "Tickets & Support",
                 f"Bulk ticket operation number {i}", "READ", "LOW")
        for i in range(40)
    ] + [
        FakeTool(f"spec_op_{i:02d}", "cms", "Fleet & Chargers",
                 f"Specialist operation number {i}", "READ", "LOW")
        for i in range(4)
    ]


def test_specialist_server_is_not_crowded_out_by_a_broad_domain():
    """THE regression. A request naming a small server must keep some of that server's
    tools, even when a generic word in the same request maps to a domain held by ten times
    as many tools.

    Pre-fix this evicts the specialist entirely: every one of the 40 bulk tools scores
    domain 3.0 + keyword 0.667 = 3.667 against the specialist's server 2.0, so top-K is
    filled before a single specialist tool is reached.
    """
    result = rank("cms ticket counts", {}, _specialist_server_catalogue(), k=20)
    servers = {t.mcp_server for t in result.tools}
    assert "cms" in servers, f"specialist server evicted; kept only {servers}"


def test_a_named_specialist_server_leads_the_ranking():
    """Stronger form: naming a server that holds 4 of 44 tools is the most specific
    evidence in the request, so those tools should lead - not merely survive."""
    result = rank("cms ticket counts", {}, _specialist_server_catalogue(), k=20)
    assert result.tools[0].mcp_server == "cms"


def test_specialist_domain_is_not_crowded_out_by_a_broad_domain():
    """The same failure with no server hint at all - two domains compete and the broader
    one wins purely because both carry the same flat weight."""
    tools = [
        FakeTool(f"bulk_charger_op_{i:02d}", "bulk", "Fleet & Chargers",
                 f"Bulk charger operation number {i}", "READ", "LOW")
        for i in range(40)
    ] + [
        FakeTool(f"doc_op_{i:02d}", "tiny", "Document Generation",
                 f"Build a document number {i}", "READ", "LOW")
        for i in range(3)
    ]
    result = rank("charger excel export", {}, tools, k=20)
    servers = {t.mcp_server for t in result.tools}
    assert "tiny" in servers, f"specialist domain evicted; kept only {servers}"


def test_a_rare_request_term_outweighs_a_ubiquitous_one():
    """Keyword overlap must be weighted by term rarity, not by raw count.

    Both groups match exactly one of the two request terms, so unweighted overlap ties
    them at 1.0 and the alphabetical tiebreak hands every top-K slot to the 40 tools that
    matched the word 39 other tools also contain.
    """
    tools = [
        FakeTool(f"common_op_{i:02d}", "bulk", "Tickets & Support",
                 "shared boilerplate entry", "READ", "LOW")
        for i in range(40)
    ] + [
        FakeTool("rare_op", "bulk", "Tickets & Support",
                 "shared distinctive entry", "READ", "LOW"),
    ]
    result = rank("boilerplate distinctive", {}, tools, k=20)
    assert result.tools[0].tool_name == "rare_op"


# ── guards against the naive alternative ──────────────────────────────────────────────
# These pass before AND after the change. They exist to fail if anyone "fixes" the
# regression above by simply reordering the weight constants (e.g. W_SERVER > W_DOMAIN),
# which trades this bug for its mirror image.

def test_an_uninformative_server_hint_must_not_outrank_a_narrow_domain():
    """When the NAMED server is the one holding almost every tool, naming it has narrowed
    nothing and must not beat a domain that genuinely discriminates. A constant
    W_SERVER > W_DOMAIN makes this fail."""
    tools = [
        FakeTool(f"noc_op_{i:02d}", "noc", "Fleet & Chargers",
                 f"Fleet operation number {i}", "READ", "LOW")
        for i in range(40)
    ] + [
        FakeTool(f"kb_shard_{i:02d}", "noc", "Knowledge Base",
                 f"Knowledge shard number {i}", "READ", "LOW")
        for i in range(3)
    ]
    result = rank("noc knowledge lookup", {}, tools, k=20)
    assert result.tools[0].domain == "Knowledge Base"


def test_specificity_weighting_preserves_determinism():
    tools = _specialist_server_catalogue()
    a = rank("cms ticket counts", {}, tools, k=20, use_cache=False)
    b = rank("cms ticket counts", {}, tools, k=20, use_cache=False)
    assert [t.tool_name for t in a.tools] == [t.tool_name for t in b.tools]


# ── server-key canonicalisation ───────────────────────────────────────────────────────

def test_server_hint_matches_across_separator_styles():
    """The same MCP server is spelled two ways in this deployment: the registry and the
    hint vocabulary use hyphens (`ecog-sd`) while the runtime tool names LibreChat sends
    yield underscores (`ecog_sd`). Comparing the raw strings makes the server signal
    silently dead for any server whose key contains a separator - so the hint must be
    matched on a canonical form, not on the literal spelling.

    This is separator normalisation, not a per-server rule: no server name appears in the
    production code path.
    """
    from tool_filtering.scoring import score_tool
    from tool_filtering.signals import RequestSignals

    tool = FakeTool("sd_op_00", "ecog_sd", "Tickets & Support", "desk op", "READ", "LOW")
    signals = RequestSignals(
        terms=frozenset(), entities=frozenset(), domains=frozenset(),
        capabilities=frozenset(), server_hint="ecog-sd", exact_tool_name=None,
    )
    scored = score_tool(tool, frozenset(), signals, frozenset())
    assert scored.breakdown["server"] > 0.0, (
        "hint 'ecog-sd' scored no server evidence for a tool keyed 'ecog_sd'"
    )


def test_server_hint_reaches_candidates_across_separator_styles():
    """Canonicalisation has to apply at NARROWING too, not only at scoring.

    `_structural_narrow` looks the hint up in `server_index` directly. If that index is
    keyed by the raw spelling, a hyphen/underscore mismatch means the server's tools never
    enter the candidate set at all - and a scoring fix alone is invisible, because nothing
    is ever scored.
    """
    tools = [
        FakeTool(f"bulk_op_{i:02d}", "bulk", "Fleet & Chargers",
                 f"Bulk operation number {i}", "READ", "LOW")
        for i in range(40)
    ] + [
        FakeTool(f"sd_op_{i:02d}", "ecog_sd", "Vendors & CPOs",
                 f"Desk record number {i}", "READ", "LOW")
        for i in range(3)
    ] + [
        FakeTool(f"probe_op_{i:02d}", "bulk", "Fleet & Chargers",
                 f"Diagnostic probe number {i}", "READ", "LOW")
        for i in range(2)
    ]
    # "servicedesk" resolves to the hyphenated key and shares no term with any tool, so
    # the server hint is the only evidence that can surface these tools.
    #
    # The second word is load-bearing. It must give OTHER tools term evidence strong
    # enough to clear RELEVANCE_FLOOR, otherwise one of two safety nets hides the bug:
    # an empty evidence union makes narrowing return the whole catalogue, and an
    # all-below-floor result triggers the stage-2 rescore of the whole catalogue. Either
    # way the specialist gets scored after all and the broken index lookup goes unseen.
    result = rank("servicedesk diagnostic", {}, tools, k=20)
    assert any(t.mcp_server == "ecog_sd" for t in result.tools), (
        "hyphenated hint never reached the underscore-keyed server index"
    )
