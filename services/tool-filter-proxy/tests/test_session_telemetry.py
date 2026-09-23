"""Focused tests for the X-Tool-Filter-Decision header (app.py:Proxy.handle):
a purely additive relay of the FilterDecision already computed for filtering
itself, read downstream by llm-router's session_client (see
services/llm-router/llm_router/session_client.py) and, from there, by
services/session/. Never changes what gets filtered or forwarded.
"""

from __future__ import annotations

import json

import httpx

from tests.conftest import chat_body, openai_tool
from tests.test_app import SETTINGS, _captured_handler, _client


def test_decision_header_present_and_reflects_an_applied_filter(noc_tools):
    cap = {}
    with _client(_captured_handler(cap)) as c:
        c.post("/kimi/chat/completions", json=chat_body(noc_tools))

    decision = json.loads(cap["headers"]["x-tool-filter-decision"])
    assert decision["applied"] is True
    assert decision["reason"] == "filtered"
    assert decision["tools_before"] == len(noc_tools)
    assert decision["tools_after"] < decision["tools_before"]


def test_decision_header_present_but_not_applied_when_no_tools_in_body():
    cap = {}
    with _client(_captured_handler(cap)) as c:
        c.post("/kimi/chat/completions", json=chat_body([]))

    decision = json.loads(cap["headers"]["x-tool-filter-decision"])
    assert decision["applied"] is False
    assert decision["reason"] == "passthrough_no_tools"


def test_decision_header_absent_for_non_chat_completions_paths():
    cap = {}
    with _client(_captured_handler(cap)) as c:
        c.get("/kimi/models")

    assert "x-tool-filter-decision" not in cap["headers"]


def test_decision_header_never_carries_prompt_text_or_tool_descriptions(noc_tools):
    cap = {}
    body = chat_body(noc_tools, user="a very specific secret NOC incident description")
    with _client(_captured_handler(cap)) as c:
        c.post("/kimi/chat/completions", json=body)

    raw_header = cap["headers"]["x-tool-filter-decision"]
    assert "secret NOC incident" not in raw_header
    for tool in noc_tools:
        assert tool["function"]["name"] not in raw_header
