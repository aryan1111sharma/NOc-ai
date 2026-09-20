"""Fixed-weight deterministic scoring and tie-breaking.

Every term is a bounded, cheap-to-compute contribution (a 0/1 flag or a ratio in
[0, 1]), and every score decomposes into named parts - see FAST_TOOL_FILTERING_DESIGN.md
§4-5. There is no learned weight and no randomness anywhere in this module: identical
inputs always produce an identical score and an identical breakdown.
"""
from __future__ import annotations

from dataclasses import dataclass

from .signals import RequestSignals
from .stats import UNWEIGHTED, CatalogStats, canonical_server

W_DOMAIN = 3.0
W_CAPABILITY = 2.0
W_ENTITY = 3.0
W_SERVER = 2.0
W_KEYWORD = 2.0
W_RECENCY = 1.0
W_RISK_TIEBREAK = 0.1  # small nudge only - never large enough to outrank a real match

_RISK_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
_CAPABILITY_PRIORITY = {"READ": 0, "WRITE": 1, "CONTROL": 2, "DESTRUCTIVE": 3}


@dataclass(frozen=True)
class ScoredTool:
    tool_name: str
    score: float
    breakdown: dict


def _keyword_overlap(tool_terms: frozenset[str], request_terms: frozenset[str],
                     stats: CatalogStats) -> float:
    """Overlap against the request side only, each matched term weighted by how rare it
    is in the catalogue.

    Unweighted |intersection| / |request terms| treats every word as equally telling,
    which it is not: tool text is dense with boilerplate ('api', 'v1', 'get', 'charger'),
    so a request word that appears in a hundred tools contributed exactly as much as one
    that appears in two. That systematically favoured whichever server owns the most
    tools. Weighting by term specificity makes a rare, meaningful word decisive and a
    ubiquitous one nearly free.

    The divisor is the request's term COUNT, deliberately not the summed weight of the
    request's terms. Dividing by the summed weight would renormalise the rarity straight
    back out: when a request has exactly one term the catalogue knows, matching it scores
    1.0 whether that term sits in three tools or three hundred. Dividing by the count
    keeps the result proportional to how rare the matched terms actually are.

    With the default UNWEIGHTED stats every term weighs 1.0, so this reduces exactly to
    the original |intersection| / |request terms|.
    """
    if not request_terms:
        return 0.0
    matched = sum(stats.term_weight(t) for t in (tool_terms & request_terms))
    return min(matched / len(request_terms), 1.0)


def score_tool(tool, tool_terms: frozenset[str], signals: RequestSignals,
               recent_tool_names: frozenset[str],
               stats: CatalogStats = UNWEIGHTED) -> ScoredTool:
    """Score one tool. `stats` scales each categorical match by how much that particular
    bucket narrows the catalogue (see stats.py); omitting it reproduces the original
    flat-weight behaviour exactly, which is what keeps this callable without an index."""
    # `is not None` guards matter: a tool from a raw MCP listing has no domain/
    # capability/risk, and `None in signals.domains` would be False anyway - but being
    # explicit keeps a missing facet contributing exactly zero rather than relying on
    # that incidental behavior.
    # Each hit is scaled by the selectivity of the bucket it matched. A domain holding a
    # fifth of the catalogue is weak evidence and now scores like it; a server holding
    # three tools is strong evidence and outranks it. Without this the weights below are
    # the only ordering, so the broadest bucket wins every time it fires.
    domain_hit = (
        stats.domain_weight(tool.domain)
        if (tool.domain is not None and tool.domain in signals.domains) else 0.0
    )
    capability_hit = (
        stats.capability_weight(tool.capability)
        if (tool.capability is not None and tool.capability in signals.capabilities) else 0.0
    )
    # Entity match is deliberately distinct from domain match: it fires when an
    # extracted entity NAME itself appears in the tool's own text (a direct lexical
    # hit), independent of whether the domain-synonym table happened to map that
    # entity to this tool's domain bucket - a robustness net against synonym-table gaps.
    entity_hit = 1.0 if (signals.entities & tool_terms) else 0.0
    server_hit = stats.server_weight(canonical_server(tool.mcp_server)) if (
        signals.server_hint and tool.mcp_server is not None
        and canonical_server(tool.mcp_server) == canonical_server(signals.server_hint)
    ) else 0.0
    keyword = _keyword_overlap(tool_terms, signals.terms, stats)
    recency_hit = 1.0 if tool.tool_name in recent_tool_names else 0.0
    # An unknown/absent risk sorts as LOW (rank 0) - no penalty for missing metadata.
    risk_rank = _RISK_RANK.get(tool.risk, 0)

    breakdown = {
        "domain": W_DOMAIN * domain_hit,
        "capability": W_CAPABILITY * capability_hit,
        "entity": W_ENTITY * entity_hit,
        "server": W_SERVER * server_hit,
        "keyword": round(W_KEYWORD * keyword, 4),
        "recency": W_RECENCY * recency_hit,
        "risk_tiebreak": -W_RISK_TIEBREAK * risk_rank,
    }
    return ScoredTool(tool_name=tool.tool_name, score=sum(breakdown.values()), breakdown=breakdown)


def sort_key(scored: ScoredTool, tool_by_name: dict):
    """Total, deterministic ordering: higher score first, then the fixed tie-break
    chain from the design (§5) - lower risk, then capability priority
    (READ > WRITE > CONTROL > DESTRUCTIVE), then alphabetical by tool_name as the final,
    always-decisive tiebreaker."""
    tool = tool_by_name[scored.tool_name]
    return (
        -scored.score,
        _RISK_RANK.get(tool.risk, 0),
        _CAPABILITY_PRIORITY.get(tool.capability, 99),
        tool.tool_name,
    )
