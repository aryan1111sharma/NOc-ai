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
        mcp.run(transport="http", host=settings.mcp_host, port=settings.mcp_port)
    else:
        mcp.run()  # stdio


if __name__ == "__main__":
    main()
