"""Proves filtering makes no network, model, or filesystem calls at request time.

Enforced by sabotage rather than inspection: patch the primitives an accidental
network/file call would have to go through so that ANY use raises, then run a full
representative set of rank() calls. Because rank() catches exceptions and fails open,
a violation could otherwise hide - so these tests also assert the trace's fallback_tier
is not "error", which is what a swallowed sabotage exception would produce.
"""
from __future__ import annotations

import builtins
import socket
from dataclasses import dataclass

import pytest

from tool_filtering import rank
from tool_filtering.index import clear_default_cache
from tests.fakes import large_tool_set, medium_tool_set


@dataclass(frozen=True)
class McpTool:
    name: str
    description: str


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_default_cache()
    yield
    clear_default_cache()


_REQUESTS = [
    "Why is charger CHG-123 offline?",
    "create a ticket and assign it to Asha",
    "search in jira for the login issue",
    "asdkfj qqzxcv unrelated gibberish",
    "",
]


def test_no_socket_use_during_filtering(monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("filtering must not open a socket")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(socket, "getaddrinfo", _boom)

    allowed = medium_tool_set()
    for text in _REQUESTS:
        result = rank(text, {}, allowed)
        assert result.trace.fallback_tier != "error", text


def test_no_file_open_during_filtering(monkeypatch):
    real_open = builtins.open

    def _boom(*a, **kw):
        raise AssertionError(f"filtering must not open a file: {a[:1]}")

    monkeypatch.setattr(builtins, "open", _boom)

    allowed = medium_tool_set()
    try:
        for text in _REQUESTS:
            result = rank(text, {}, allowed)
            assert result.trace.fallback_tier != "error", text
    finally:
        monkeypatch.setattr(builtins, "open", real_open)


def test_no_external_calls_on_raw_mcp_listing(monkeypatch):
    """Same guarantee for the tool shape available today (name + description only),
    which goes through the normalization adapter."""
    def _boom(*a, **kw):
        raise AssertionError("filtering must not reach the network")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "getaddrinfo", _boom)

    tools = [McpTool(f"t_{i}", f"unrelated task {i}") for i in range(60)]
    tools.append(McpTool("charger_get_alarms", "alarms for a charger"))

    result = rank("show charger alarms", {}, tools)
    assert result.trace.fallback_tier != "error"
    assert "charger_get_alarms" in [t.name for t in result.tools]


def test_no_llm_or_http_library_is_imported_by_the_package():
    """Static guarantee: the filtering package must not pull in an HTTP client, an
    embedding/vector library, or a model SDK - not even transitively."""
    import sys

    for mod in list(sys.modules):
        if mod.startswith("tool_filtering"):
            del sys.modules[mod]

    forbidden = {
        "httpx", "requests", "aiohttp", "urllib3", "openai", "anthropic",
        "fastembed", "qdrant_client", "sentence_transformers", "numpy", "torch",
    }
    before = set(sys.modules)
    import tool_filtering  # noqa: F401
    newly_imported = set(sys.modules) - before

    offenders = {m for m in newly_imported if m.split(".")[0] in forbidden}
    assert not offenders, f"filtering imported forbidden module(s): {offenders}"


def test_large_catalog_still_makes_no_external_calls(monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("filtering must not reach the network")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "getaddrinfo", _boom)

    allowed = large_tool_set(1200)
    result = rank("Why is charger CHG-123 offline?", {}, allowed)
    assert result.trace.fallback_tier != "error"
    assert len(result.tools) <= 30
