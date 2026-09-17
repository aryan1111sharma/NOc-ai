"""Ranking quality against the REAL agent export and the REAL registry.

`test_specificity.py` (filtering/) pins the general property on synthetic catalogues.
This file pins the consequence on the actual 136-tool surface the NOC Engineer agent
ships, because that is where the failure was observed and where a future registry edit
or MCP-server addition could reintroduce it.

Assertions are on which SERVER survives filtering, never on an exact tool list: the
latter would break on any harmless description edit and would be a change-detector
rather than a test. The invariant that matters operationally is "the tools needed to
answer this class of question are still reachable".
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import chat_body, openai_tool
from tool_filter_proxy.adapter import filter_body
from tool_filter_proxy.librechat_names import build_reverse_index
from tool_filter_proxy.metadata import RegistryMetadata

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_AGENTS_JSON = _REPO_ROOT / "apps" / "ai-platform" / "librechat" / "agents" / "agents.json"

pytestmark = pytest.mark.skipif(
    not _AGENTS_JSON.is_file(), reason="agents.json export not present"
)


@pytest.fixture(scope="module")
def registry_index():
    from tool_registry import load_registry
    return build_reverse_index(load_registry())


@pytest.fixture(scope="module")
def provider():
    return RegistryMetadata.load()


@pytest.fixture
def agent_tools(registry_index):
    """The live agent's tool array in OpenAI wire shape, with the descriptions LibreChat
    sends. Rebuilt per test so object identity is per-request, as in production."""
    agent = json.loads(_AGENTS_JSON.read_text(encoding="utf-8"))[0]
    return [
        openai_tool(name, registry_index[name].description if name in registry_index else "")
        for name in agent["tools"]
    ]


def _servers_kept(body, provider, registry_index):
    new_body, decision = filter_body(body, metadata_provider=provider, top_k=20)
    kept = set()
    for entry in new_body["tools"]:
        name = entry["function"]["name"]
        kept.add(registry_index[name].mcp_server if name in registry_index else "?")
    return kept, decision


def _all_servers(registry_index):
    agent = json.loads(_AGENTS_JSON.read_text(encoding="utf-8"))[0]
    return {
        registry_index[n].mcp_server
        for n in agent["tools"] if n in registry_index
    }


# ── the two observed production failures ──────────────────────────────────────────────

def test_naming_a_specialist_server_keeps_its_tools(agent_tools, provider, registry_index):
    """Observed failure #1: the broad NOC 'Tickets & Support' domain (29 tools) evicted
    every ChargeZone tool from a request that named ChargeZone explicitly."""
    kept, _ = _servers_kept(
        chat_body(agent_tools, user="ChargeZone open P1/P2/P3 ticket counts"),
        provider, registry_index)
    assert "chargezone" in kept, f"named server evicted; kept {sorted(kept)}"


def test_a_narrow_domain_survives_a_broad_one(agent_tools, provider, registry_index):
    """Observed failure #2: no server was named at all - 'excel' maps to a 3-tool domain
    and 'charger' to a 24-tool one, and the broad domain took every slot."""
    kept, _ = _servers_kept(
        chat_body(agent_tools, user="export this month's offline charger report to excel"),
        provider, registry_index)
    assert "docgen" in kept, f"document-generation tools evicted; kept {sorted(kept)}"


# ── the general property, over every server the agent actually has ────────────────────

def test_every_specialist_server_is_reachable_through_its_own_vocabulary(
        agent_tools, provider, registry_index):
    """Scales with the deployment: drives every phrase in the hint vocabulary rather than
    a hardcoded list, so adding an MCP server (and its hint) extends this test
    automatically.

    Probing by hint PHRASE rather than by server key is deliberate. The key is not always
    a phrase a user would type - and worse, one key can contain another ('ecog-sd'
    contains 'ecog', which the vocabulary maps to a different server), so probing by key
    would assert something about the vocabulary rather than about ranking. What ranking
    owes us is: when a phrase resolves to a server, that server's tools survive.

    `noc` is excluded as a subject, not special-cased away: it holds 113 of 136 tools, so
    naming it is not specific evidence, and the whole point of the change is that it no
    longer behaves as if it were.
    """
    from tool_filtering.signals import _SERVER_HINTS

    present = _all_servers(registry_index)
    probes = [
        (phrase, server) for phrase, server in _SERVER_HINTS
        if server in present and server != "noc"
    ]
    assert probes, "expected at least one specialist server with a hint phrase"

    unreachable = []
    for phrase, server in probes:
        kept, _ = _servers_kept(
            chat_body(agent_tools, user=f"check {phrase} for this"),
            provider, registry_index)
        if server not in kept:
            unreachable.append((phrase, server, sorted(kept)))
    assert not unreachable, f"hint phrases that did not surface their server: {unreachable}"


# ── no regression on the cases that already worked ────────────────────────────────────

@pytest.mark.parametrize("question", [
    "Why is charger CHG-123 offline right now?",
    "synopsis of all chargers offline on manthan today",
])
def test_charger_questions_still_return_charger_tools(agent_tools, provider, registry_index,
                                                       question):
    new_body, _ = filter_body(chat_body(agent_tools, user=question),
                              metadata_provider=provider, top_k=20)
    names = [e["function"]["name"] for e in new_body["tools"]]
    assert sum("charger" in n or "fleet" in n for n in names) >= 10, names


def test_knowledge_base_question_still_reaches_the_kb(agent_tools, provider, registry_index):
    kept, _ = _servers_kept(
        chat_body(agent_tools,
                  user="what does the knowledge base say about OCPP error code 1042"),
        provider, registry_index)
    assert "kb" in kept


# ── adversarial ───────────────────────────────────────────────────────────────────────

def test_a_generic_domain_word_alone_still_selects_that_domain(agent_tools, provider,
                                                                registry_index):
    """The fix must not invert into 'broad evidence is worthless'. With nothing more
    specific in the request, the broad domain IS the best available evidence."""
    new_body, _ = filter_body(chat_body(agent_tools, user="show me open tickets"),
                              metadata_provider=provider, top_k=20)
    names = [e["function"]["name"] for e in new_body["tools"]]
    assert sum("ticket" in n for n in names) >= 5, names


def test_two_named_servers_both_survive(agent_tools, provider, registry_index):
    kept, _ = _servers_kept(
        chat_body(agent_tools, user="compare chargezone tickets against ecog jira issues"),
        provider, registry_index)
    assert {"chargezone", "jira"} <= kept, f"kept {sorted(kept)}"


def test_naming_the_largest_server_does_not_collapse_the_selection(agent_tools, provider,
                                                                    registry_index):
    """Naming the 113-tool server narrows almost nothing; the request's other words must
    still drive the ranking."""
    new_body, _ = filter_body(
        chat_body(agent_tools, user="use noc to check the knowledge base for OCPP errors"),
        metadata_provider=provider, top_k=20)
    names = [e["function"]["name"] for e in new_body["tools"]]
    assert any(n.endswith("_mcp_kb") for n in names), names


def test_gibberish_still_returns_a_usable_set(agent_tools, provider):
    new_body, decision = filter_body(chat_body(agent_tools, user="zzzz qqqq xyzzy"),
                                     metadata_provider=provider, top_k=20)
    assert 0 < len(new_body["tools"]) <= len(agent_tools)
    assert decision.reason in ("filtered", "guard_tripped")


def test_empty_request_text_still_returns_a_usable_set(agent_tools, provider):
    new_body, _ = filter_body(chat_body(agent_tools, user="   "),
                              metadata_provider=provider, top_k=20)
    assert 0 < len(new_body["tools"]) <= len(agent_tools)


def test_entity_only_request_selects_charger_tools(agent_tools, provider):
    new_body, _ = filter_body(chat_body(agent_tools, user="CHG-123"),
                              metadata_provider=provider, top_k=20)
    names = [e["function"]["name"] for e in new_body["tools"]]
    assert any("charger" in n for n in names), names


def test_ranking_is_stable_across_repeated_identical_requests(agent_tools, provider):
    first, _ = filter_body(chat_body(agent_tools, user="ChargeZone ticket counts"),
                           metadata_provider=provider, top_k=20)
    second, _ = filter_body(chat_body(agent_tools, user="ChargeZone ticket counts"),
                            metadata_provider=provider, top_k=20)
    assert [e["function"]["name"] for e in first["tools"]] == \
           [e["function"]["name"] for e in second["tools"]]


def test_specificity_does_not_break_pinning(agent_tools, provider):
    """Pinned tools bypass ranking entirely; re-assert it here so a future scoring change
    cannot quietly strand a multi-hop tool chain."""
    pinned = "cz_jira_get_issue_mcp_chargezone"
    body = chat_body(agent_tools, user="Why is charger CHG-123 offline?")
    body["messages"].insert(1, {
        "role": "assistant",
        "tool_calls": [{"id": "c1", "type": "function",
                        "function": {"name": pinned, "arguments": "{}"}}],
    })
    new_body, decision = filter_body(body, metadata_provider=provider, top_k=20)
    assert pinned in [e["function"]["name"] for e in new_body["tools"]]
    assert decision.pinned == 1


def test_result_is_always_a_subset_of_the_request(agent_tools, provider):
    sent = {id(t) for t in agent_tools}
    for question in ("ChargeZone ticket counts", "export to excel",
                     "why is CHG-9 offline", "zzz"):
        new_body, _ = filter_body(chat_body(agent_tools, user=question),
                                  metadata_provider=provider, top_k=20)
        assert all(id(t) in sent for t in new_body["tools"]), question
