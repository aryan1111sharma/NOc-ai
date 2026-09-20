"""Adapter behaviour: OpenAI unwrapping, pinning, the safety guard, and fallbacks."""

from __future__ import annotations

import pytest

from tests.conftest import chat_body, openai_tool
from tool_filter_proxy.adapter import (
    collect_pinned_names, extract_request_text, filter_body, unwrap_tools,
)
from tool_filter_proxy.metadata import RegistryMetadata


# ── unwrapping the nested OpenAI shape ──────────────────────────────────────────────────────

def test_unwrap_reads_the_nested_function_name():
    tools = [openai_tool("jira_search_mcp_jira", "Search Jira")]
    [t] = unwrap_tools(tools)
    assert t.name == "jira_search_mcp_jira"
    assert t.description == "Search Jira"
    assert t.mcp_server == "jira"
    assert t.original is tools[0]  # identity preserved for byte-faithful rewrite


def test_unwrap_skips_malformed_entries_without_failing():
    tools = [
        openai_tool("good_mcp_noc", "ok"),
        {"type": "function"},                      # no function body
        {"type": "function", "function": {}},       # no name
        {"function": {"name": 123}},                 # non-string name
        "not-a-dict",
        openai_tool("also_good_mcp_noc", "ok"),
    ]
    assert [t.name for t in unwrap_tools(tools)] == ["good_mcp_noc", "also_good_mcp_noc"]


def test_unwrap_handles_missing_description():
    tools = [{"type": "function", "function": {"name": "t_mcp_noc"}}]
    assert unwrap_tools(tools)[0].description == ""


def test_unwrap_of_non_list_is_empty():
    assert unwrap_tools(None) == []
    assert unwrap_tools({"tools": []}) == []


# ── request text extraction ──────────────────────────────────────────────────────────────────

def test_request_text_uses_the_latest_user_message():
    messages = [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "why is charger CHG-1 offline"},
    ]
    assert extract_request_text(messages) == "why is charger CHG-1 offline"


def test_request_text_handles_multimodal_content_parts():
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "check charger"},
        {"type": "image_url", "image_url": {"url": "data:..."}},
        {"type": "text", "text": "CHG-9"},
    ]}]
    assert extract_request_text(messages) == "check charger CHG-9"


def test_request_text_falls_back_to_last_message_when_no_user_turn():
    """A tool-loop continuation can arrive with the newest content being a tool result;
    ranking against that beats ranking against an empty string."""
    messages = [{"role": "tool", "content": "charger is offline, comms lost"}]
    assert "comms lost" in extract_request_text(messages)


def test_request_text_empty_when_nothing_usable():
    assert extract_request_text([]) == ""
    assert extract_request_text([{"role": "user", "content": None}]) == ""


# ── pinning ──────────────────────────────────────────────────────────────────────────────────

def test_collect_pins_prior_assistant_tool_calls():
    body = {"messages": [
        {"role": "user", "content": "q"},
        {"role": "assistant", "tool_calls": [
            {"id": "1", "type": "function", "function": {"name": "alpha_mcp_noc", "arguments": "{}"}},
            {"id": "2", "type": "function", "function": {"name": "beta_mcp_noc", "arguments": "{}"}},
        ]},
    ]}
    assert collect_pinned_names(body) == {"alpha_mcp_noc", "beta_mcp_noc"}


def test_collect_pins_tool_choice_function():
    body = {"messages": [], "tool_choice": {"type": "function", "function": {"name": "forced_mcp_noc"}}}
    assert collect_pinned_names(body) == {"forced_mcp_noc"}


def test_collect_ignores_string_tool_choice_modes():
    for mode in ("auto", "none", "required"):
        assert collect_pinned_names({"messages": [], "tool_choice": mode}) == set()


def test_pinned_tool_survives_filtering_even_when_irrelevant(noc_tools):
    """The correctness case: an unrelated tool already called earlier in the loop must
    stay declared, or the provider may reject the request / the chain cannot continue."""
    body = chat_body(noc_tools, user="Why is charger CHG-123 offline?")
    body["messages"].append({"role": "assistant", "tool_calls": [
        {"id": "1", "type": "function",
         "function": {"name": "filler_widget_42_mcp_cms", "arguments": "{}"}},
    ]})

    out, decision = filter_body(body)
    names = [t["function"]["name"] for t in out["tools"]]
    assert decision.applied and decision.pinned == 1
    assert "filler_widget_42_mcp_cms" in names


def test_tool_choice_target_survives_filtering(noc_tools):
    body = chat_body(noc_tools, user="Why is charger CHG-123 offline?")
    body["tool_choice"] = {"type": "function", "function": {"name": "filler_widget_7_mcp_cms"}}
    out, decision = filter_body(body)
    assert "filler_widget_7_mcp_cms" in [t["function"]["name"] for t in out["tools"]]


def test_pins_beyond_max_k_are_all_retained(noc_tools):
    """Correctness beats the size target: if a conversation genuinely references more
    than MAX_K tools, the cap lifts rather than dropping a referenced one."""
    from tool_filtering.engine import MAX_K

    pin_names = [f"filler_widget_{i}_mcp_cms" for i in range(MAX_K + 5)]
    body = chat_body(noc_tools)
    body["messages"].append({"role": "assistant", "tool_calls": [
        {"id": str(i), "type": "function", "function": {"name": n, "arguments": "{}"}}
        for i, n in enumerate(pin_names)
    ]})
    out, decision = filter_body(body)
    got = set(t["function"]["name"] for t in out["tools"])
    assert set(pin_names) <= got
    assert decision.tools_after >= MAX_K + 5


def test_pin_for_a_tool_absent_from_the_payload_is_ignored():
    """A tool_call naming something not in this request's tools[] cannot be pinned - we
    have no object for it. Must not crash or fabricate an entry."""
    tools = [openai_tool(f"t_{i}_mcp_noc", f"thing {i}") for i in range(40)]
    body = chat_body(tools)
    body["messages"].append({"role": "assistant", "tool_calls": [
        {"id": "1", "type": "function", "function": {"name": "not_in_payload_mcp_noc"}},
    ]})
    out, decision = filter_body(body)
    assert decision.applied
    assert "not_in_payload_mcp_noc" not in [t["function"]["name"] for t in out["tools"]]


# ── the safety guard ─────────────────────────────────────────────────────────────────────────

def test_filtering_reduces_and_stays_a_subset(noc_tools):
    body = chat_body(noc_tools)
    out, decision = filter_body(body)
    assert decision.applied
    assert 0 < decision.tools_after <= decision.tools_before
    original_names = {t["function"]["name"] for t in noc_tools}
    assert {t["function"]["name"] for t in out["tools"]} <= original_names


def test_relevant_tools_are_selected(noc_tools):
    out, _ = filter_body(chat_body(noc_tools, user="Why is charger CHG-123 offline?"))
    names = {t["function"]["name"] for t in out["tools"]}
    assert "charger_get_charging_status_mcp_noc" in names
    assert "get_charger_by_id_api_v1_chargers_mcp_noc" in names


def test_guard_forwards_original_when_every_entry_is_unusable():
    """The failure this guard exists for: handing the core a list it cannot normalize
    yields zero tools, which would silently disable every tool. Must forward untouched."""
    tools = [{"type": "function", "function": {"nope": "x"}} for _ in range(50)]
    body = chat_body(tools)
    out, decision = filter_body(body)
    assert out is body                      # same object, not a rewritten copy
    assert decision.applied is False
    assert decision.reason == "guard_tripped"
    assert decision.tools_after == len(tools)


def test_never_emits_empty_tools_array(noc_tools):
    """Across a spread of inputs, `tools` must never come back empty."""
    for user in ["Why is charger CHG-1 offline?", "asdkfj qqzxcv gibberish", "", "create a ticket"]:
        out, _ = filter_body(chat_body(noc_tools, user=user))
        assert len(out.get("tools") or []) > 0, user


def test_error_inside_filtering_forwards_original(monkeypatch, noc_tools):
    def boom(*a, **kw):
        raise RuntimeError("simulated filtering fault")

    monkeypatch.setattr("tool_filter_proxy.adapter.rank", boom)
    body = chat_body(noc_tools)
    out, decision = filter_body(body)
    assert out is body
    assert decision.reason == "error"
    assert decision.tools_after == len(noc_tools)


# ── passthrough cases ────────────────────────────────────────────────────────────────────────

def test_body_without_tools_is_untouched():
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    out, decision = filter_body(body)
    assert out is body
    assert decision.reason == "passthrough_no_tools"


def test_empty_tools_array_is_untouched():
    body = chat_body([])
    out, decision = filter_body(body)
    assert out is body
    assert decision.reason == "passthrough_no_tools"


def test_small_tool_set_is_returned_intact():
    """At or below MAX_K the core passes everything through; the payload is unchanged in
    content even though the adapter did run."""
    tools = [openai_tool(f"t_{i}_mcp_noc", f"thing {i}") for i in range(8)]
    out, decision = filter_body(chat_body(tools))
    assert {t["function"]["name"] for t in out["tools"]} == {t["function"]["name"] for t in tools}
    assert decision.tools_after == 8


def test_disabled_flag_skips_filtering_entirely(noc_tools):
    body = chat_body(noc_tools)
    out, decision = filter_body(body, enabled=False)
    assert out is body
    assert decision.reason == "disabled"


def test_other_body_fields_are_preserved(noc_tools):
    body = chat_body(noc_tools, stream=True, temperature=0.3,
                      tool_choice="auto", parallel_tool_calls=False)
    out, decision = filter_body(body)
    assert decision.applied
    for key in ("model", "messages", "stream", "temperature", "tool_choice", "parallel_tool_calls"):
        assert out[key] == body[key]
    assert body["tools"] is not out["tools"]  # original body object not mutated


# ── registry enrichment ──────────────────────────────────────────────────────────────────────

def test_registry_enrichment_improves_ranking(noc_tools):
    """With domain/capability supplied, the charger diagnostics should rank ahead of the
    unrelated filler - the signal the raw OpenAI payload cannot provide on its own."""
    metadata = RegistryMetadata.load()
    out, decision = filter_body(chat_body(noc_tools), metadata_provider=metadata)
    names = [t["function"]["name"] for t in out["tools"]]
    assert decision.applied
    first_filler = next((i for i, n in enumerate(names) if n.startswith("filler_widget_")), len(names))
    assert names.index("charger_get_charging_status_mcp_noc") < first_filler


def test_missing_registry_degrades_but_still_filters(noc_tools):
    empty = RegistryMetadata({})   # simulates registry unavailable
    out, decision = filter_body(chat_body(noc_tools), metadata_provider=empty)
    assert decision.applied
    assert 0 < decision.tools_after <= decision.tools_before
    assert "charger_get_charging_status_mcp_noc" in [t["function"]["name"] for t in out["tools"]]


def test_metadata_provider_returns_none_for_unknown_tool():
    metadata = RegistryMetadata.load()
    assert metadata("definitely_not_a_real_tool_mcp_noc") is None


def test_determinism_across_repeated_calls(noc_tools):
    metadata = RegistryMetadata.load()
    a, _ = filter_body(chat_body(noc_tools), metadata_provider=metadata)
    b, _ = filter_body(chat_body(noc_tools), metadata_provider=metadata)
    assert [t["function"]["name"] for t in a["tools"]] == [t["function"]["name"] for t in b["tools"]]


def test_large_tool_set_is_bounded(noc_tools):
    from tool_filtering.engine import MAX_K

    tools = noc_tools + [openai_tool(f"bulk_{i}_mcp_noc", f"bulk task {i}") for i in range(1200)]
    out, decision = filter_body(chat_body(tools), metadata_provider=RegistryMetadata.load())
    assert decision.tools_before == len(tools)
    assert 0 < decision.tools_after <= MAX_K
