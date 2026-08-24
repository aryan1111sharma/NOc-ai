"""platform_mcp_discovery — static, source-level inventory of the MCP servers declared
under services/mcp/ in this repository.

This is static analysis of Python source, not a live query: it never imports, starts, or
connects to another server, so it needs no credentials or network access, and its results
are unaffected by whether that server is currently running. Tool names are found by
scanning each server's .py files for the `@mcp.tool` decorator pattern used throughout
this repo. A server that registers some of its tools imperatively at runtime from
configuration (e.g. kb_mcp, which creates one `kb_search_<profile>` tool per configured
profile via `mcp.tool(name=f"...")` rather than a fixed `@mcp.tool` function) cannot have
those exact names known statically; such a server is marked `dynamic_tools: true` instead
of a guessed name, so a caller does not mistake an undercount for the server's real surface.

This intentionally stops at "what tools does the source declare" — enumerating which
servers are actually reachable/healthy right now is platform_service_health, a separate,
not-yet-built capability (see the Platform MCP README's "Not built yet" list) that needs
Docker/network access this tool does not have.
"""

from __future__ import annotations

import os
import re

_DECORATOR_TOOL = re.compile(
    r"@mcp\.tool\b[^\n]*\n(?:[^\n]*\n){0,2}?\s*(?:async\s+)?def\s+(\w+)\s*\("
)
_DYNAMIC_TOOL_CALL = re.compile(r"\bmcp\.tool\s*\(\s*name\s*=")

_SKIP_DIRS = {"tests", "test", "__pycache__", ".venv", "venv", "node_modules"}


def _scan_server(server_dir: str) -> dict:
    tool_names: list[str] = []
    dynamic_call_sites = 0

    for dirpath, dirnames, filenames in os.walk(server_dir):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            try:
                with open(os.path.join(dirpath, fname), encoding="utf-8") as f:
                    text = f.read()
            except OSError:
                continue
            tool_names.extend(_DECORATOR_TOOL.findall(text))
            dynamic_call_sites += len(_DYNAMIC_TOOL_CALL.findall(text))

    entry = {"tool_count": len(tool_names), "tools": sorted(set(tool_names))}
    if dynamic_call_sites:
        entry["dynamic_tools"] = True
        entry["dynamic_registration_sites"] = dynamic_call_sites
    return entry


def register(mcp, settings) -> None:
    @mcp.tool
    def platform_mcp_discovery() -> dict:
        """List the MCP servers declared under services/mcp/ in this repository and, for
        each, the tool names found via static source analysis of `@mcp.tool` declarations.
        This is a source-code scan, not a live query — it does not start, import, or
        connect to any other server, so results reflect what the code declares, not
        whether that server is currently running or healthy. A server that registers
        tools imperatively from runtime configuration instead of one fixed @mcp.tool
        function per tool is marked dynamic_tools: true, since this scan cannot know
        those tools' real, config-dependent names."""
        root = settings.repo_root
        mcp_dir = os.path.join(root, "services", "mcp")
        if not os.path.isdir(mcp_dir):
            return {"error": f"services/mcp not found under repo root {root}"}

        servers = {}
        for name in sorted(os.listdir(mcp_dir)):
            server_dir = os.path.join(mcp_dir, name)
            if os.path.isdir(server_dir):
                servers[name] = _scan_server(server_dir)

        return {"server_count": len(servers), "servers": servers}
