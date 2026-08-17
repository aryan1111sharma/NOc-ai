"""platform_api — self-describing info about this server's own tool surface.

Deliberately scoped to THIS server only: its name, transport, and its own registered
tools. Enumerating every other MCP server's live tools/status is "MCP Discovery" — an
explicitly deferred future capability (see the Platform MCP README) — and is intentionally
NOT built here, so v1 stays small and this tool never needs restructuring when that
capability eventually lands as its own module.
"""

from __future__ import annotations


def register(mcp, settings) -> None:
    @mcp.tool
    async def platform_api() -> dict:
        """Describe this MCP server's own API surface: its name, transport, and the tools
        it currently exposes. Does not reach out to other MCP servers — cross-server
        discovery is a deferred future capability, not implemented here."""
        tools = await mcp.list_tools()
        return {
            "server_name": mcp.name,
            "transport": settings.mcp_transport,
            "tool_count": len(tools),
            "tools": sorted(t.name for t in tools),
        }
