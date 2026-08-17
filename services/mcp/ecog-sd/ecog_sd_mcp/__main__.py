"""Entry point: ``python -m ecog_sd_mcp`` runs the Ecog Service Desk MCP server."""

from __future__ import annotations

from .config import settings
from .server import build_server


def main() -> None:
    mcp = build_server()
    if settings.mcp_transport.lower() == "http":
        mcp.run(transport="http", host=settings.mcp_host, port=settings.mcp_port,
                path="/mcp", host_origin_protection=False)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
