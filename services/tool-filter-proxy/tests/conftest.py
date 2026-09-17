"""Shared fixtures. Puts the sibling filtering core and registry on sys.path so tests
run from this directory without an install step (mirroring the Dockerfile's PYTHONPATH).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SERVICE_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _SERVICE_DIR.parent.parent
for path in (_SERVICE_DIR, _REPO_ROOT / "filtering", _REPO_ROOT / "registry"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def openai_tool(name: str, description: str = "", **params) -> dict:
    """One entry in the OpenAI `tools` array, with the nested `function` shape."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": params or {}},
        },
    }


def chat_body(tools: list, user: str = "Why is charger CHG-123 offline?", **extra) -> dict:
    body = {
        "model": "kimi-for-coding",
        "messages": [
            {"role": "system", "content": "You are a NOC engineer assistant."},
            {"role": "user", "content": user},
        ],
        "tools": tools,
    }
    body.update(extra)
    return body


@pytest.fixture
def noc_tools() -> list:
    """A tool set large enough (>MAX_K=30) that filtering actually engages, using real
    LibreChat-mangled names so registry enrichment can be exercised."""
    tools = [
        openai_tool("charger_get_charging_status_mcp_noc", "Per-gun live charging status",
                    charger_id={"type": "string"}),
        openai_tool("get_charger_by_id_api_v1_chargers_mcp_noc", "Get a single charger by id",
                    charger_id={"type": "string"}),
        openai_tool("charger_get_alarms_mcp_noc", "Latched alarms and pending errors for a charger"),
        openai_tool("charger_get_network_status_mcp_noc", "Network and GSM signal status"),
        openai_tool("ticket_create_mcp_noc", "Create a new support ticket"),
        openai_tool("ticket_assign_mcp_noc", "Assign a ticket to a user or team"),
        openai_tool("jira_search_mcp_jira", "Search Ecog Jira issues with a JQL query"),
        openai_tool("kb_search_noc_mcp_kb", "Search the noc knowledge base for runbooks"),
        openai_tool("charger_ssh_execute_mcp_noc", "Execute a staged command on a live charger"),
    ]
    tools += [
        openai_tool(f"filler_widget_{i}_mcp_cms", f"unrelated widget inventory task {i}")
        for i in range(45)
    ]
    return tools
