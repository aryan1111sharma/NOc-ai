"""Build the read-only NOC MCP server from the bundled OpenAPI spec.

The NOC backend does not serve its OpenAPI spec publicly, so a snapshot generated from the
FastAPI app is bundled at ``noc_mcp/openapi/noc_openapi.json``. Regenerate it with
``scripts/generate_openapi.py`` when the backend API changes.

Only ``GET`` operations are exposed (as MCP tools); every other method is excluded, which
enforces the read-only guarantee at the MCP layer regardless of the service account's role.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.server.providers.openapi import MCPType, RouteMap

from .auth import NocAuthClient
from .catalog import _register_control_center_catalog
from .config import settings
from .control_center import _register_control_center_tools
from .fleet import _register_fleet_tools
from .readonly import ReadOnlyProxyMiddleware
from .reports import _register_report_tools
from .ssh_exec import _register_ssh_tools

_SPEC_PATH = Path(__file__).parent / "openapi" / "noc_openapi.json"

# GET -> tool; everything else excluded (read-only guarantee).
_ROUTE_MAPS = [
    RouteMap(methods=["GET"], mcp_type=MCPType.TOOL),
    RouteMap(mcp_type=MCPType.EXCLUDE),
]


def load_spec() -> dict:
    return json.loads(_SPEC_PATH.read_text(encoding="utf-8"))


def build_client() -> NocAuthClient:
    return NocAuthClient(
        base_url=settings.noc_base_url.rstrip("/"),
        email=settings.noc_email,
        password=settings.noc_password,
        timeout=settings.request_timeout,
    )


def build_server(client: NocAuthClient | None = None, ssh_ws_url_builder=None) -> FastMCP:
    client = client or build_client()
    mcp = FastMCP.from_openapi(
        openapi_spec=load_spec(),
        client=client,
        name="QuenchNOC Assistant (read-only)",
        route_maps=_ROUTE_MAPS,
    )
    _register_control_center_catalog(mcp)
    _register_control_center_tools(mcp, client)
    _register_report_tools(mcp, client)
    _register_fleet_tools(mcp, client)
    _register_ssh_tools(mcp, client, ssh_ws_url_builder)
    mcp.add_middleware(ReadOnlyProxyMiddleware())
    return mcp
