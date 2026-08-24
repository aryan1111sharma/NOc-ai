"""Deterministic test fixtures: a small dataclass matching the shape `rank()` expects,
plus a medium curated set (large enough to exceed MAX_K so scoring actually engages)
and a large synthetic set for scale/performance tests.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FakeTool:
    tool_name: str
    mcp_server: str
    domain: str
    description: str
    capability: str
    risk: str


# "Signature" tools with realistic, hand-written names/descriptions used by scenario
# assertions. Mixed in with filler below so the total exceeds engine.MAX_K (30).
SIGNATURE_TOOLS = [
    FakeTool("charger_get_charging_status", "noc", "On-Charger Control Center",
             "Per-gun connected/PLC/OCPP live charging status", "READ", "LOW"),
    FakeTool("get_charger_by_id", "noc", "Fleet & Chargers",
             "Get a single charger by id", "READ", "LOW"),
    FakeTool("charger_get_alarms", "noc", "Alarms & Monitoring",
             "Latched alarms and pending errors for a charger", "READ", "LOW"),
    FakeTool("cms_get_charger_details", "cms", "Fleet & Chargers",
             "Full profile for one charger: metadata plus live status", "READ", "LOW"),
    FakeTool("ticket_create", "noc", "Tickets & Support",
             "Create a new ticket for a charger issue", "WRITE", "LOW"),
    FakeTool("ticket_assign", "noc", "Tickets & Support",
             "Assign a ticket to a user or team", "WRITE", "LOW"),
    FakeTool("jira_search", "jira", "Issue Tracking",
             "Search Ecog Jira issues with a JQL query", "READ", "LOW"),
    FakeTool("jira_get_issue", "jira", "Issue Tracking",
             "Full detail of one Ecog Jira issue by key", "READ", "LOW"),
    FakeTool("kb_search_noc", "kb", "Knowledge Base",
             "Search the noc knowledge base for runbooks", "READ", "LOW"),
    FakeTool("platform_health", "platform", "Platform Intelligence",
             "Liveness check for the platform server", "READ", "LOW"),
    FakeTool("charger_ssh_prepare", "noc", "SSH & Proxy",
             "Stage a shell command for a charger and return a confirmation token",
             "CONTROL", "LOW"),
    FakeTool("charger_ssh_execute", "noc", "SSH & Proxy",
             "Execute the staged command on a live charger's SSH PTY", "DESTRUCTIVE", "CRITICAL"),
]

_FILLER_DOMAINS = [
    ("Widget Management", "widget catalog and inventory records"),
    ("Invoice Processing", "billing invoices and payment records"),
    ("Marketing Campaigns", "email campaign performance metrics"),
    ("Warehouse Logistics", "pallet and shipment tracking records"),
]
_FILLER_CAPABILITIES = ["READ", "READ", "READ", "WRITE", "CONTROL"]
_FILLER_RISKS = ["LOW", "LOW", "MEDIUM"]
_FILLER_SERVERS = ["noc", "cms", "jira", "kb", "docgen"]


def _filler_tools(n: int) -> list[FakeTool]:
    tools = []
    for i in range(n):
        domain, desc = _FILLER_DOMAINS[i % len(_FILLER_DOMAINS)]
        capability = _FILLER_CAPABILITIES[i % len(_FILLER_CAPABILITIES)]
        risk = _FILLER_RISKS[i % len(_FILLER_RISKS)]
        server = _FILLER_SERVERS[i % len(_FILLER_SERVERS)]
        tools.append(FakeTool(
            tool_name=f"filler_tool_{i}", mcp_server=server, domain=domain,
            description=f"Unrelated filler tool #{i}: {desc}", capability=capability, risk=risk,
        ))
    return tools


def medium_tool_set() -> list[FakeTool]:
    """Signature tools + enough filler to exceed MAX_K, so structural narrowing and
    scoring actually engage instead of the small-set passthrough."""
    return SIGNATURE_TOOLS + _filler_tools(40)


def large_tool_set(n: int = 1200) -> list[FakeTool]:
    """A synthetic catalog for scale/performance tests, well beyond current real
    inventory size, to exercise the "hundreds or thousands of tools" requirement."""
    return SIGNATURE_TOOLS + _filler_tools(n - len(SIGNATURE_TOOLS))


def by_name(tools, name: str) -> FakeTool:
    return next(t for t in tools if t.tool_name == name)


def names_of(tools) -> set[str]:
    return {t.tool_name for t in tools}
