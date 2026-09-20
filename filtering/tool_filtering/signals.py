"""Cheap, deterministic signal extraction from a user request.

No LLM, no embeddings, no network - regex, a fixed verb lexicon, and a hand-curated
synonym table only. See FAST_TOOL_FILTERING_DESIGN.md §2 for the design this implements.

Everything here operates on plain strings and small dicts/sets; nothing touches the Tool
Registry, Policy, or any external data source - signal extraction only looks at the
request text and the set of tool_names it's allowed to consider for an exact match.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "to", "for", "of", "on", "in", "and",
    "or", "with", "my", "me", "i", "please", "can", "you", "this", "that", "it", "do",
    "does", "am", "be", "been", "at", "by", "from", "as", "if", "so", "not", "has", "have",
})

# Tokens keep '_' and '-' so a literal tool_name (e.g. "jira_search") or a hyphenated
# id (e.g. "CHG-123") survives as a single token instead of being split apart.
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_\-]*")

# Structured entity detectors. Deliberately permissive: an ambiguous match (e.g.
# "CHG-123" also fitting the generic ticket-key shape) is allowed to set BOTH entities -
# this only ever *adds* a soft scoring signal, never removes or grants anything, so a
# false-positive entity guess is harmless by construction.
_ENTITY_PATTERNS = {
    "charger": re.compile(r"\bCHG-\d+\b", re.IGNORECASE),
    "ticket": re.compile(r"\b[A-Z]{2,10}-\d+\b"),  # Jira-style key: ECOG-123, QCH-45, ...
}

# entity/keyword -> domain(s) it's evidence for. Hand-curated against the domains that
# actually appear in registry/metadata/*.yaml (see MCP_TOOL_INVENTORY.md) - extend this
# table, not the algorithm, when a new domain needs recognizing.
_DOMAIN_SYNONYMS: dict[str, frozenset[str]] = {
    "charger": frozenset({"Fleet & Chargers", "On-Charger Control Center", "Telemetry & Sessions"}),
    "chargers": frozenset({"Fleet & Chargers", "On-Charger Control Center"}),
    "offline": frozenset({"Alarms & Monitoring", "Telemetry & Sessions", "Fleet & Chargers"}),
    "online": frozenset({"Fleet & Chargers", "Telemetry & Sessions"}),
    "session": frozenset({"Telemetry & Sessions"}),
    "sessions": frozenset({"Telemetry & Sessions"}),
    "ticket": frozenset({"Tickets & Support"}),
    "tickets": frozenset({"Tickets & Support"}),
    "issue": frozenset({"Issue Tracking", "Tickets & Support"}),
    "jira": frozenset({"Issue Tracking"}),
    "alert": frozenset({"Alarms & Monitoring"}),
    "alerts": frozenset({"Alarms & Monitoring"}),
    "alarm": frozenset({"Alarms & Monitoring"}),
    "alarms": frozenset({"Alarms & Monitoring"}),
    "knowledge": frozenset({"Knowledge Base"}),
    "kb": frozenset({"Knowledge Base"}),
    "report": frozenset({"Reporting"}),
    "reports": frozenset({"Reporting"}),
    "vendor": frozenset({"Vendors & CPOs"}),
    "cpo": frozenset({"Vendors & CPOs"}),
    "release": frozenset({"Releases & Engineering"}),
    "log": frozenset({"Logs"}),
    "logs": frozenset({"Logs"}),
    "ssh": frozenset({"SSH & Proxy"}),
    "proxy": frozenset({"SSH & Proxy"}),
    "document": frozenset({"Document Generation"}),
    "excel": frozenset({"Document Generation"}),
    "pdf": frozenset({"Document Generation"}),
    "customer": frozenset({"Customers & Engineers", "Fleet & Chargers"}),
    "engineer": frozenset({"Customers & Engineers"}),
    "user": frozenset({"Auth / RBAC"}),
    "users": frozenset({"Auth / RBAC"}),
    "role": frozenset({"Auth / RBAC"}),
    "permission": frozenset({"Auth / RBAC"}),
    "config": frozenset({"Admin & Config"}),
    "platform": frozenset({"Platform Intelligence"}),
    "device": frozenset({"Device Commands", "On-Charger Control Center"}),
}

_READ_VERBS = frozenset({
    "why", "what", "show", "check", "is", "list", "get", "view", "find", "display",
    "status", "see", "search", "read", "lookup",
})
_WRITE_VERBS = frozenset({
    "create", "add", "open", "file", "assign", "update", "edit", "set", "link", "comment",
})
_CONTROL_VERBS = frozenset({"acknowledge", "suppress", "maintenance", "reopen", "verify"})
_DESTRUCTIVE_VERBS = frozenset({
    "restart", "reboot", "execute", "run", "stop", "reset", "delete", "remove", "clear",
})

# substring hints (checked against the lowercased raw text, not the token set, because
# some are multi-word phrases) -> mcp_server value in the Tool Registry.
_SERVER_HINTS: tuple[tuple[str, str], ...] = (
    ("service desk", "ecog-sd"),
    ("servicedesk", "ecog-sd"),
    ("chargezone", "chargezone"),
    ("knowledge base", "kb"),
    ("old noc", "cms"),
    ("legacy", "cms"),
    ("docgen", "docgen"),
    ("jira", "jira"),
    ("ecog", "jira"),
    ("cms", "cms"),
    ("platform", "platform"),
    ("noc", "noc"),
)


@dataclass(frozen=True)
class RequestSignals:
    terms: frozenset[str]
    entities: frozenset[str]
    domains: frozenset[str]
    capabilities: frozenset[str]
    server_hint: str | None
    exact_tool_name: str | None


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


def extract_entities(text: str) -> frozenset[str]:
    return frozenset(name for name, pattern in _ENTITY_PATTERNS.items() if pattern.search(text))


def infer_domains(terms: frozenset[str], entities: frozenset[str]) -> frozenset[str]:
    domains: set[str] = set()
    for term in terms | entities:
        domains |= _DOMAIN_SYNONYMS.get(term, frozenset())
    return frozenset(domains)


def infer_capabilities(terms: frozenset[str]) -> frozenset[str]:
    caps: set[str] = set()
    if terms & _READ_VERBS:
        caps.add("READ")
    if terms & _WRITE_VERBS:
        caps.add("WRITE")
    if terms & _CONTROL_VERBS:
        caps.add("CONTROL")
    if terms & _DESTRUCTIVE_VERBS:
        caps.add("DESTRUCTIVE")
    return frozenset(caps)


def infer_server_hint(text_lower: str) -> str | None:
    for phrase, server in _SERVER_HINTS:
        if phrase in text_lower:
            return server
    return None


def extract_exact_tool_name(terms: frozenset[str], allowed_tool_names: frozenset[str]) -> str | None:
    """Deterministic O(1)-per-term lookup, scoped to `allowed_tool_names` only - this
    can never resolve to a tool outside what the caller already authorized, since it
    has no other data source to consult."""
    hits = sorted(terms & allowed_tool_names)  # sorted: deterministic pick if >1 (rare)
    return hits[0] if hits else None


def extract_signals(request_text: str, allowed_tool_names: frozenset[str]) -> RequestSignals:
    terms = frozenset(tokenize(request_text))
    entities = extract_entities(request_text)
    domains = infer_domains(terms, entities)
    capabilities = infer_capabilities(terms)
    server_hint = infer_server_hint(request_text.lower())
    exact = extract_exact_tool_name(terms, allowed_tool_names)
    return RequestSignals(
        terms=terms, entities=entities, domains=domains, capabilities=capabilities,
        server_hint=server_hint, exact_tool_name=exact,
    )
