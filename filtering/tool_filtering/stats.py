"""How much a *particular* match narrows the catalogue.

`scoring.py` fixes what each KIND of evidence is worth (a domain match is worth
W_DOMAIN). That is only half the question. In a catalogue where one domain holds 29 of
136 tools and one server holds 8, the two matches are not comparable even though each
fires exactly once: the first eliminates 79% of the catalogue, the second 94%.

Weighting every match of a kind identically is what lets a broad bucket outscore a narrow
one - the whole bucket gets the same flat bonus, so a request that names a small server
loses to any generic word that happens to map to a large domain. The fix is to scale each
match by the selectivity of the bucket it matched, which is the classic inverse-document-
frequency measure:

    specificity(df, total) = log(total / df) / log(total)        in [0, 1]

Normalised by log(total) so a singleton bucket scores exactly 1.0 and a catalogue-wide
bucket exactly 0.0, which keeps the existing weight constants meaningful as upper bounds
rather than silently rescaling them.

Everything here is derived from the authorized set itself - no server, domain, capability
or term is ever named in this module. Adding an MCP server re-derives every weight on the
next index build with no configuration change, which is the property that makes this
scale past the servers we happen to have today.

A log measure is load-bearing, not incidental: a linear `1 - df/total` was measured
against the real 136-tool catalogue and does NOT separate a 29-tool domain from an 8-tool
server enough to correct the ranking (2.360 vs 1.882 - the broad domain still wins).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


def canonical_server(value):
    """Fold the two spellings of the same MCP server onto one key.

    The server identifier reaches scoring from two independent places: the hint
    vocabulary and registry metadata use the librechat.yaml-style key, while a tool
    object built from a LibreChat tool NAME yields the suffix form. Those differ whenever
    the key contains a separator, and comparing the raw strings makes the server signal
    silently score zero for exactly those servers - a dead signal with no error.

    Separator-only normalisation, deliberately: no server is named here, so a new MCP
    server is covered without touching this function.
    """
    if not isinstance(value, str):
        return value
    return value.replace("-", "_").lower()


def specificity(df: int, total: int) -> float:
    """Weight in [0, 1] for evidence matching `df` of `total` tools.

    Degenerate inputs return 1.0 (inert, never an exception): a bucket that matches
    nothing cannot contribute to any tool's score anyway, and a catalogue of 0 or 1 tools
    has no ranking to do. Ranking must never be able to raise at the caller.
    """
    if total <= 1 or df <= 0:
        return 1.0
    if df >= total:
        return 0.0
    return math.log(total / df) / math.log(total)


@dataclass(frozen=True)
class CatalogStats:
    """Per-bucket specificity for one authorized set, computed once per index build.

    Lookups are plain dict hits on the request path; the logarithms are paid at build
    time along with the inverted indexes they are derived from.
    """

    total: int = 0
    domain: dict = field(default_factory=dict)
    server: dict = field(default_factory=dict)
    capability: dict = field(default_factory=dict)
    term: dict = field(default_factory=dict)

    # An unknown bucket returns 1.0 rather than 0.0 on purpose. It can only be reached by
    # a tool whose facet is absent from the index, which means nothing matched it, so the
    # weight is multiplied by a zero hit either way - and 1.0 keeps a hand-built
    # CatalogStats (tests, callers with no index) behaving exactly like the unweighted
    # scoring this replaced.
    def domain_weight(self, value) -> float:
        return self.domain.get(value, 1.0)

    def server_weight(self, value) -> float:
        return self.server.get(value, 1.0)

    def capability_weight(self, value) -> float:
        return self.capability.get(value, 1.0)

    def term_weight(self, value) -> float:
        return self.term.get(value, 1.0)


#: Used when no stats are supplied. Every lookup misses and returns 1.0, which reproduces
#: the original flat-weight behaviour exactly - so `score_tool` stays callable without an
#: index, and the pre-existing scoring tests keep testing the weight structure itself.
UNWEIGHTED = CatalogStats()


def build_stats(total: int, domain_df: dict, server_df: dict,
                capability_df: dict, term_df: dict) -> CatalogStats:
    """Turn raw document frequencies into specificity weights."""
    conv = lambda df_map: {k: specificity(len(v), total) for k, v in df_map.items()}  # noqa: E731
    return CatalogStats(
        total=total,
        domain=conv(domain_df),
        server=conv(server_df),
        capability=conv(capability_df),
        term=conv(term_df),
    )
