"""Entry point: ``python -m cz_jira_mcp`` runs the ChargeZone (Quench) Jira MCP server."""

from __future__ import annotations

from .config import settings
from .server import build_server


def main() -> None:
    mcp = build_server()
    transport = settings.mcp_transport.lower()
    if transport == "http":
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
