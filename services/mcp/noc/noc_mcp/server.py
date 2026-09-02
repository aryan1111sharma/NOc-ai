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
from .history import _register_history_tools
from .readonly import ReadOnlyProxyMiddleware
from .reports import _register_report_tools
from .ssh_exec import _register_ssh_tools

_SPEC_PATH = Path(__file__).parent / "openapi" / "noc_openapi.json"

# Modules with no NOC-operations value: user/role administration.
_EXCLUDED_PREFIXES = (
    r"^/api/v1/auth",
    r"^/api/v1/admin",
    r"^/api/v1/roles",
    r"^/api/v1/permissions",
    r"^/api/v1/teams",
)

# Endpoints a curated tool already owns. Leaving the generated twin in place would give the model
# two routes to one answer with no basis to choose, which is worse for selection than either alone.
_CURATED_PREFIXES = (
    r"^/api/v1/reports",                              # -> noc_report
    r"^/api/v1/stats",                                # -> noc_stats
    r"^/api/v1/chargers$",                            # -> noc_fleet_status
    r"^/api/v1/chargers/map$",                        # -> noc_fleet_status
    r"^/api/v1/chargers/\{charger_id\}/history",      # -> noc_charger_history
    r"^/api/v1/chargers/\{charger_id\}/telemetry",    # -> noc_charger_telemetry
    r"^/api/v1/chargers/\{charger_id\}/sessions",     # -> noc_session_events
    r"^/api/v1/chargers/\{charger_id\}/stability$",   # -> noc_charger_stability
    r"^/api/v1/chargers/\{charger_id\}/commands$",    # -> noc_charger_stability
    r"^/api/v1/chargers/\{charger_id\}/ssh-status$",  # -> noc_charger_stability
    r"^/api/v1/chargers/\{charger_id\}/ocpp-config",  # -> noc_charger_ocpp_config
)

# Order matters: exclusions are evaluated before the catch-all GET -> TOOL map.
_ROUTE_MAPS = (
    [RouteMap(pattern=p, mcp_type=MCPType.EXCLUDE) for p in _EXCLUDED_PREFIXES]
    + [RouteMap(pattern=p, mcp_type=MCPType.EXCLUDE) for p in _CURATED_PREFIXES]
    + [
        RouteMap(methods=["GET"], mcp_type=MCPType.TOOL),
        RouteMap(mcp_type=MCPType.EXCLUDE),
    ]
)


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
    _register_history_tools(mcp, client)
    _register_ssh_tools(mcp, client, ssh_ws_url_builder)
    mcp.add_middleware(ReadOnlyProxyMiddleware())
    return mcp
