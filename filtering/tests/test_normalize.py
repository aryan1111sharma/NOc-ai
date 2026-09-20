"""Tests for the tolerant input adapter and, critically, for filtering working against
the tool shape that actually exists TODAY: a raw LibreChat / MCP tool listing with only
a name and a description (Policy/Agent Access is designed but not live).
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from tool_filtering import MAX_K, NormalizationError, normalize_tool, normalize_tools, rank
from tool_filtering.index import clear_default_cache


@dataclass(frozen=True)
class McpTool:
    """The realistic current shape: `name` + `description`, nothing else."""
    name: str
    description: str


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_default_cache()
    yield
    clear_default_cache()


def _mcp_listing(n: int = 60) -> list[McpTool]:
    tools = [McpTool(f"filler_tool_{i}", f"unrelated widget inventory task number {i}")
             for i in range(n)]
    tools += [
        McpTool("charger_get_charging_status", "Per-gun connected/PLC/OCPP live charging status"),
        McpTool("get_charger_by_id", "Get a single charger by id"),
        McpTool("charger_get_alarms", "Latched alarms and pending errors for a charger"),
        McpTool("ticket_create", "Create a new support ticket"),
        McpTool("jira_search", "Search Jira issues with a JQL query"),
    ]
    return tools


# ── normalize_tool / normalize_tools units ───────────────────────────────────────────────────

def test_normalize_accepts_name_field():
    n = normalize_tool(McpTool("a_tool", "does a thing"))
    assert n.tool_name == "a_tool"
    assert n.description == "does a thing"
    assert n.domain is None and n.capability is None and n.risk is None


def test_normalize_prefers_tool_name_over_name():
    @dataclass(frozen=True)
    class Both:
        tool_name: str
        name: str
        description: str = ""

    assert normalize_tool(Both("canonical", "alias")).tool_name == "canonical"


def test_normalize_accepts_plain_dict():
    n = normalize_tool({"name": "d_tool", "description": "from a dict"})
    assert n.tool_name == "d_tool"
    assert n.description == "from a dict"


def test_normalize_accepts_full_registry_shape():
    @dataclass(frozen=True)
    class Full:
        tool_name: str
        mcp_server: str
        domain: str
        description: str
        capability: str
        risk: str

    n = normalize_tool(Full("t", "noc", "Fleet & Chargers", "d", "READ", "LOW"))
    assert (n.mcp_server, n.domain, n.capability, n.risk) == ("noc", "Fleet & Chargers", "READ", "LOW")


def test_normalize_preserves_original_object_identity():
    """Load-bearing for the subset invariant: results are the caller's own objects."""
    tool = McpTool("a_tool", "desc")
    assert normalize_tool(tool).original is tool


def test_normalize_rejects_nameless_object():
    with pytest.raises(NormalizationError):
        normalize_tool({"description": "no name at all"})


def test_normalize_tools_skips_unusable_entries_instead_of_failing():
    tools = [McpTool("good", "ok"), {"description": "nameless"}, McpTool("also_good", "ok")]
    assert [n.tool_name for n in normalize_tools(tools)] == ["good", "also_good"]


def test_metadata_provider_fills_only_missing_fields():
    @dataclass(frozen=True)
    class PartialTool:
        name: str
        description: str
        domain: str  # already present - provider must NOT override this

    provider = lambda name: {"domain": "PROVIDER_DOMAIN", "capability": "READ", "risk": "LOW"}
    n = normalize_tool(PartialTool("t", "d", "OWN_DOMAIN"), provider)
    assert n.domain == "OWN_DOMAIN"     # object's own value wins
    assert n.capability == "READ"        # absent -> filled from provider
    assert n.risk == "LOW"


def test_metadata_provider_returning_none_is_safe():
    n = normalize_tool(McpTool("t", "d"), lambda name: None)
    assert n.domain is None and n.capability is None


# ── rank() over the realistic current shape ──────────────────────────────────────────────────

def test_rank_actually_narrows_a_raw_mcp_listing():
    """The regression this adapter exists for: before it, rank() raised AttributeError,
    fell through to the fail-open path, and returned every tool - filtering silently did
    nothing against the only tool shape available today."""
    tools = _mcp_listing()
    result = rank("Why is charger CHG-123 offline?", {}, tools)

    assert result.trace.fallback_tier != "error"
    assert len(result.tools) <= MAX_K
    assert len(result.tools) < len(tools)  # genuinely narrowed
    names = [t.name for t in result.tools]
    assert "charger_get_charging_status" in names
    assert "get_charger_by_id" in names


def test_rank_over_raw_listing_returns_original_objects():
    tools = _mcp_listing()
    result = rank("Why is charger CHG-123 offline?", {}, tools)
    # identity, not just equal names - proves result ⊆ allowed_tools structurally
    assert all(any(r is t for t in tools) for r in result.tools)
    assert all(isinstance(r, McpTool) for r in result.tools)


def test_rank_over_raw_listing_never_returns_unavailable_tool():
    tools = [t for t in _mcp_listing() if t.name != "jira_search"]
    result = rank("search jira for the login issue", {}, tools)
    assert "jira_search" not in [t.name for t in result.tools]
    assert {t.name for t in result.tools} <= {t.name for t in tools}


def test_rank_over_raw_listing_still_matches_keywords_without_domain_metadata():
    """With no domain/capability metadata, keyword + entity matching must carry the
    ranking on their own."""
    tools = _mcp_listing()
    result = rank("create a support ticket", {}, tools)
    assert "ticket_create" in [t.name for t in result.tools]


def test_rank_with_metadata_provider_enables_domain_signal():
    tools = _mcp_listing()
    enrichment = {
        "charger_get_charging_status": {"domain": "On-Charger Control Center",
                                         "capability": "READ", "risk": "LOW", "mcp_server": "noc"},
        "get_charger_by_id": {"domain": "Fleet & Chargers", "capability": "READ",
                               "risk": "LOW", "mcp_server": "noc"},
    }
    result = rank("Why is charger CHG-123 offline?", {}, tools,
                  metadata_provider=enrichment.get)
    names = [t.name for t in result.tools]
    assert "charger_get_charging_status" in names
    # the enriched tools should now rank ahead of unenriched filler
    first_filler = next((i for i, n in enumerate(names) if n.startswith("filler_tool_")), len(names))
    assert names.index("charger_get_charging_status") < first_filler


def test_rank_accepts_a_listing_of_plain_dicts():
    tools = [{"name": f"filler_{i}", "description": f"unrelated task {i}"} for i in range(40)]
    tools.append({"name": "charger_get_alarms", "description": "alarms for a charger"})
    result = rank("show charger alarms", {}, tools)
    assert result.trace.fallback_tier != "error"
    assert "charger_get_alarms" in [t["name"] for t in result.tools]


def test_mixed_shapes_in_one_listing_do_not_break_filtering():
    @dataclass(frozen=True)
    class Rich:
        tool_name: str
        mcp_server: str
        domain: str
        description: str
        capability: str
        risk: str

    tools = [McpTool(f"filler_{i}", f"unrelated {i}") for i in range(40)]
    tools.append(Rich("charger_get_meters", "noc", "On-Charger Control Center",
                      "Live AC/DC meter readings", "READ", "LOW"))
    tools.append({"name": "charger_get_temperature", "description": "thermal sensors for a charger"})

    result = rank("check charger meters and temperature", {}, tools)
    assert result.trace.fallback_tier != "error"
    got = {getattr(t, "tool_name", None) or getattr(t, "name", None)
           or (t.get("name") if isinstance(t, dict) else None) for t in result.tools}
    assert "charger_get_meters" in got


def test_provider_identity_is_part_of_the_cache_key():
    """Two different providers over the SAME tool names must not share one index -
    otherwise the second call would silently reuse the first's enrichment."""
    tools = _mcp_listing()
    p1 = lambda n: {"domain": "Fleet & Chargers"} if n == "get_charger_by_id" else None
    p2 = lambda n: {"domain": "Fleet & Chargers"} if n == "charger_get_alarms" else None

    r1 = rank("charger", {}, tools, metadata_provider=p1)
    r2 = rank("charger", {}, tools, metadata_provider=p2)
    # Different enrichment => different resulting order; if the cache key ignored the
    # provider these would be identical.
    assert [t.name for t in r1.tools] != [t.name for t in r2.tools]
