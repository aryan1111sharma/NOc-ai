"""Entry point: ``python -m noc_mcp`` runs the NOC MCP server.

Transport is chosen by ``MCP_TRANSPORT`` (``stdio`` default, or ``http``).
"""

from __future__ import annotations

from .config import settings
from .server import build_server


def main() -> None:
    mcp = build_server()
    transport = settings.mcp_transport.lower()
    if transport == "http":
        # Served at exactly /mcp (matches the LibreChat mcpServers url). Host-origin
        # protection is disabled because clients reach this over an internal container
        # network by service name (e.g. http://mcp-server:8100/mcp), not localhost.
        mcp.run(
            transport="http",
            host=settings.mcp_host,
            port=settings.mcp_port,
            path="/mcp",
            host_origin_protection=False,
        )
    else:
        mcp.run()  # stdio


if __name__ == "__main__":
    main()
