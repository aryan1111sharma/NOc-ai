"""LibreChat's MCP tool-name transformation, and the reverse map onto the Tool Registry.

LibreChat renames every MCP tool before handing it to the model:

    <base cut at the first "__">[:64 - len("_mcp_<server>")] + "_mcp_<server>"

Two separate effects, both verified against `apps/ai-platform/librechat/agents/agents.json`
(the live agent export) and the 233-tool registry:

  1. The name is cut at the first `__`, which is where the bundled NOC OpenAPI spec's
     path-parameter markers begin. `get_charger_by_id_api_v1_chargers__charger_id__get`
     becomes `get_charger_by_id_api_v1_chargers` - a 49-char name, so this is NOT a
     length effect and a length-only rule silently mismatches 63 of 192 NOC tools.
  2. The result is then truncated so the whole thing fits 64 characters, the OpenAI
     function-name limit.

Server keys are LibreChat's `mcpServers` keys from librechat.yaml, which use underscores
(`ecog_sd`), while the repo directory and registry `mcp_server` value use a hyphen
(`ecog-sd`) - hence SERVER_ALIASES.

Verified coverage: 100% of tools on all four exported agents (171/171, 172/172, 192/192,
192/192) with zero collisions. This module is the ONLY place that encodes this rule; the
filtering core knows nothing about it.
"""

from __future__ import annotations

MAX_OPENAI_TOOL_NAME = 64
SERVER_SUFFIX = "_mcp_"

# registry / directory name -> librechat.yaml mcpServers key
SERVER_ALIASES = {"ecog-sd": "ecog_sd"}


def librechat_server_key(mcp_server: str) -> str:
    return SERVER_ALIASES.get(mcp_server, mcp_server)


def to_librechat_name(tool_name: str, mcp_server: str) -> str:
    """Apply LibreChat's transformation to a registry tool name."""
    suffix = f"{SERVER_SUFFIX}{librechat_server_key(mcp_server)}"
    base = tool_name.split("__")[0]
    return base[: MAX_OPENAI_TOOL_NAME - len(suffix)] + suffix


def split_server(librechat_name: str) -> str | None:
    """Recover the MCP server key from a LibreChat tool name, or None if the name does
    not carry one (a non-MCP tool, or a naming scheme this module does not know)."""
    idx = librechat_name.rfind(SERVER_SUFFIX)
    if idx == -1:
        return None
    server = librechat_name[idx + len(SERVER_SUFFIX):]
    return server or None


def build_reverse_index(registry: dict) -> dict[str, object]:
    """Map every LibreChat-mangled name back to its registry record.

    `registry` is {tool_name: ToolRecord} from tool_registry.load_registry(). A collision
    would mean two registry tools mangle to one LibreChat name, making enrichment
    ambiguous; verified to be zero today, and first-wins keeps it deterministic if a
    future tool ever introduces one.
    """
    out: dict[str, object] = {}
    for record in registry.values():
        mangled = to_librechat_name(record.tool_name, record.mcp_server)
        out.setdefault(mangled, record)
    return out
