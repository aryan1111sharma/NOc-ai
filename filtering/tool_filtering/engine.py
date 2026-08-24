"""Fast Deterministic Tool Filtering - the single public entry point is `rank()`.

Implements FAST_TOOL_FILTERING_DESIGN.md. Read that document for the full rationale;
this module is the executable form of its §1-6.

SAFETY INVARIANT (never relaxed, enforced structurally, not just by convention):
every tool `rank()` can possibly return is drawn from the `allowed_tools` the caller
passed in. No function in this package reads the Tool Registry, a Policy store, or any
other authorization source - `allowed_tools` IS the authorization boundary, established
upstream by Policy, and this module treats it as opaque input data to reorder and
subset, never to expand. Every internal helper below only ever narrows a set that was
built from `allowed_tools` (see index.py); there is no code path that introduces a
tool_name from anywhere else.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable

from .index import ToolIndex, get_index
from .scoring import ScoredTool, score_tool, sort_key
from .signals import RequestSignals, extract_signals

DEFAULT_K = 20
MIN_K = 10
MAX_K = 30
RELEVANCE_FLOOR = 0.05


@dataclass(frozen=True)
class FilterTrace:
    """Structured, explainable record of one filtering decision. See design §9. Never
    includes tool descriptions or request text beyond what the caller already sent in -
    nothing here is more sensitive than the request itself."""
    agent_hint: str | None
    signals: dict
    authorized_count: int
    post_narrowing_count: int
    fallback_tier: str  # none | exact_match | skip_small_set | stage1_widen | stage2_widen | error | empty
    top_k: list  # [{tool_name, score, breakdown}]
    latency_ms: float


@dataclass(frozen=True)
class FilterResult:
    tools: tuple
    trace: FilterTrace


def rank(request_text: str, session_context: dict | None, allowed_tools: Iterable,
          *, k: int = DEFAULT_K, use_cache: bool = True,
          metadata_provider=None) -> FilterResult:
    """Return up to `k` tools drawn from `allowed_tools`, ranked by relevance to
    `request_text`. This function only ever reorders/subsets `allowed_tools`, never
    expands it - the returned objects are the caller's own objects, by identity.

    `allowed_tools` entries need only a name (`tool_name` or `name`); optional
    `mcp_server`/`domain`/`capability`/`risk` each enable their own scoring signal when
    present and are simply inert when absent, so a raw MCP/LibreChat tool listing works
    directly (keyword + entity matching still apply). Dicts and plain objects are both
    accepted - see normalize.py.

    `metadata_provider(tool_name) -> mapping | None` optionally supplies
    domain/capability/risk/mcp_server for tools whose objects lack them (it never
    overrides a field already present). This is the seam for Tool Registry enrichment;
    filtering itself never reads the registry.

    Never raises for a filtering-logic reason: any internal failure degrades to
    returning the full, unranked authorized set (design §6 tier 4) rather than
    propagating an exception or returning nothing.
    """
    start = time.perf_counter()
    session_context = session_context or {}
    recent_tool_names = frozenset(session_context.get("recent_tool_names") or ())
    agent_hint = session_context.get("agent_id")

    tools = list(allowed_tools)
    authorized_count = len(tools)

    try:
        return _rank_impl(request_text, tools, recent_tool_names, agent_hint, k, use_cache,
                          metadata_provider, start)
    except Exception as exc:  # noqa: BLE001 - deliberate: fail open, never crash the caller
        return FilterResult(
            tools=tuple(tools),
            trace=FilterTrace(
                agent_hint=agent_hint, signals={"error": repr(exc)},
                authorized_count=authorized_count, post_narrowing_count=authorized_count,
                fallback_tier="error", top_k=[], latency_ms=_elapsed_ms(start),
            ),
        )


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 4)


def _rank_impl(request_text, tools, recent_tool_names, agent_hint, k, use_cache,
                metadata_provider, start) -> FilterResult:
    authorized_count = len(tools)

    if authorized_count == 0:
        return FilterResult(tools=(), trace=FilterTrace(
            agent_hint=agent_hint, signals={}, authorized_count=0, post_narrowing_count=0,
            fallback_tier="empty", top_k=[], latency_ms=_elapsed_ms(start),
        ))

    if authorized_count <= MAX_K:
        # Nothing to gain narrowing an already-small authorized set (design §5).
        # Returns the caller's objects untouched, so no normalization is even needed.
        return FilterResult(tools=tuple(tools), trace=FilterTrace(
            agent_hint=agent_hint, signals={}, authorized_count=authorized_count,
            post_narrowing_count=authorized_count, fallback_tier="skip_small_set",
            top_k=[], latency_ms=_elapsed_ms(start),
        ))

    index = get_index(tools, use_cache=use_cache, metadata_provider=metadata_provider)
    signals = extract_signals(request_text, index.tool_names)

    if signals.exact_tool_name is not None:
        chosen = index.by_name[signals.exact_tool_name]
        return FilterResult(tools=(chosen.original,), trace=FilterTrace(
            agent_hint=agent_hint, signals=_signals_to_dict(signals),
            authorized_count=authorized_count, post_narrowing_count=1,
            fallback_tier="exact_match",
            top_k=[{"tool_name": chosen.tool_name, "score": None, "breakdown": {"exact_name": 1.0}}],
            latency_ms=_elapsed_ms(start),
        ))

    candidate_names, narrowed = _structural_narrow(index, signals)
    fallback_tier = "stage1_widen" if not narrowed else "none"

    scored = _score_all(index, candidate_names, signals, recent_tool_names)

    if not scored or scored[0].score < RELEVANCE_FLOOR:
        # Stage 2 fallback: discard structural narrowing, score the FULL authorized
        # set with a safe default ordering rather than surfacing nothing (design §6).
        # Skip the rescore when narrowing never happened - the candidate set already IS
        # the full set, so recomputing it would be identical work for an identical result.
        if candidate_names is not index.tool_names and len(candidate_names) < len(index.tool_names):
            scored = _score_all(index, index.tool_names, signals, recent_tool_names)
            candidate_names = index.tool_names
        fallback_tier = "stage2_widen"

    top_k_scored = _select_top_k(scored, k)
    # `.original` is load-bearing: the caller gets back its OWN objects, so
    # result ⊆ allowed_tools holds by identity, not merely by name equality.
    result_tools = tuple(index.by_name[s.tool_name].original for s in top_k_scored)

    return FilterResult(tools=result_tools, trace=FilterTrace(
        agent_hint=agent_hint, signals=_signals_to_dict(signals),
        authorized_count=authorized_count, post_narrowing_count=len(candidate_names),
        fallback_tier=fallback_tier,
        top_k=[{"tool_name": s.tool_name, "score": round(s.score, 4), "breakdown": s.breakdown}
               for s in top_k_scored],
        latency_ms=_elapsed_ms(start),
    ))


def _structural_narrow(index: ToolIndex, signals: RequestSignals) -> tuple[frozenset[str], bool]:
    """Narrow to the UNION of tools with any positive evidence, via the precomputed
    inverted indexes (design §1/§3).

    Union-of-evidence, not a chain of intersections: a tool is a candidate if it matches
    the server hint OR any inferred domain OR shares any term with the request. A tool
    with none of those can only score the (negative) risk tie-break, so excluding it
    changes no ranking - it would never outrank a tool with real evidence. This is what
    makes per-request cost scale with the QUERY rather than the catalog: matched-term
    postings, not every tool.

    Returns the full set unnarrowed only when there is NO evidence at all (e.g.
    gibberish) - that's the genuine starvation case, and it hands off to the Stage-2
    safe-default fallback in the caller. A small-but-real candidate set (even 1-2 tools)
    is kept: adaptive top-K would rather return few precise tools than pad with noise.
    """
    evidence: set[str] = set()

    if signals.server_hint:
        evidence |= index.server_index.get(signals.server_hint, frozenset())

    for domain in signals.domains:
        evidence |= index.domain_index.get(domain, frozenset())

    for term in signals.terms:
        evidence |= index.term_index.get(term, frozenset())

    if not evidence:
        return index.tool_names, False

    return frozenset(evidence) & index.tool_names, True


def _score_all(index: ToolIndex, names: Iterable[str], signals: RequestSignals,
                recent_tool_names: frozenset[str]) -> list[ScoredTool]:
    scored = [
        score_tool(index.by_name[name], index.tool_terms[name], signals, recent_tool_names)
        for name in names
    ]
    scored.sort(key=lambda s: sort_key(s, index.by_name))
    return scored


def _select_top_k(scored: list[ScoredTool], k: int) -> list[ScoredTool]:
    """Adaptive count, per design §5: take every candidate at/above the relevance
    floor, then clamp into [MIN_K, MAX_K] (and never exceed the caller's own `k` if it's
    smaller than MAX_K, and never exceed how many candidates actually exist)."""
    if not scored:
        return []
    above_floor = sum(1 for s in scored if s.score >= RELEVANCE_FLOOR)
    ceiling = min(k, MAX_K) if k else MAX_K
    count = max(MIN_K, above_floor)
    count = min(count, ceiling, len(scored))
    return scored[:count]


def _signals_to_dict(signals: RequestSignals) -> dict:
    return {
        "terms": sorted(signals.terms),
        "entities": sorted(signals.entities),
        "domains": sorted(signals.domains),
        "capabilities": sorted(signals.capabilities),
        "server_hint": signals.server_hint,
        "exact_tool_name": signals.exact_tool_name,
    }
