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
from .config import settings

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


def build_server() -> FastMCP:
    return FastMCP.from_openapi(
        openapi_spec=load_spec(),
        client=build_client(),
        name="QuenchNOC Assistant (read-only)",
        route_maps=_ROUTE_MAPS,
    )
