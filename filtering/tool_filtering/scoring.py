"""Fixed-weight deterministic scoring and tie-breaking.

Every term is a bounded, cheap-to-compute contribution (a 0/1 flag or a ratio in
[0, 1]), and every score decomposes into named parts - see FAST_TOOL_FILTERING_DESIGN.md
§4-5. There is no learned weight and no randomness anywhere in this module: identical
inputs always produce an identical score and an identical breakdown.
"""
from __future__ import annotations

from dataclasses import dataclass

from .signals import RequestSignals

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


def _keyword_overlap(tool_terms: frozenset[str], request_terms: frozenset[str]) -> float:
    """Jaccard-style overlap against the request side only: |intersection| / |request
    terms|. Using the request's term count (not the union) means a short, precise
    request against a long tool description isn't unfairly diluted."""
    if not request_terms:
        return 0.0
    return len(tool_terms & request_terms) / len(request_terms)


def score_tool(tool, tool_terms: frozenset[str], signals: RequestSignals,
               recent_tool_names: frozenset[str]) -> ScoredTool:
    # `is not None` guards matter: a tool from a raw MCP listing has no domain/
    # capability/risk, and `None in signals.domains` would be False anyway - but being
    # explicit keeps a missing facet contributing exactly zero rather than relying on
    # that incidental behavior.
    domain_hit = 1.0 if (tool.domain is not None and tool.domain in signals.domains) else 0.0
    capability_hit = 1.0 if (
        tool.capability is not None and tool.capability in signals.capabilities
    ) else 0.0
    # Entity match is deliberately distinct from domain match: it fires when an
    # extracted entity NAME itself appears in the tool's own text (a direct lexical
    # hit), independent of whether the domain-synonym table happened to map that
    # entity to this tool's domain bucket - a robustness net against synonym-table gaps.
    entity_hit = 1.0 if (signals.entities & tool_terms) else 0.0
    server_hit = 1.0 if (
        signals.server_hint and tool.mcp_server is not None
        and tool.mcp_server == signals.server_hint
    ) else 0.0
    keyword = _keyword_overlap(tool_terms, signals.terms)
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
