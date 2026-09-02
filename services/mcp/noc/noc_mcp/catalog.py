"""On-charger control-center API catalog.

Distils ``catalog/rbac-rules.csv`` -- the QFlex on-charger RBAC API catalog -- into a knowledge
tool describing what a specific charger exposes across its on-device services, so the model knows
what it can fetch through the generic charger proxy.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional

from fastmcp import FastMCP

_CATALOG_CSV = Path(__file__).parent / "catalog" / "rbac-rules.csv"

# The path-based charger-proxy tool is identified by the argument shape the catalog tells the
# model to use: it is the only registered tool that REQUIRES charger_id + port + path (the
# query-parameter proxy variant leaves port optional). Capability, not spelling.
_PROXY_REQUIRED_ARGS = frozenset({"charger_id", "port", "path"})

# Last-resort spelling, used only if discovery finds nothing. It is a fallback, never the
# source of truth: FastMCP derives generated tool names from the OpenAPI operation and
# TRUNCATES them, so a literal written here can go stale without anything failing loudly --
# which is exactly what happened. This catalog previously named
# ``proxy_api_call_path_based_api_v1_chargers__charger_id__proxy__port___path__get``, a tool
# that has never existed, so its own instructions made the 239-endpoint long tail
# unreachable. tests/test_catalog.py asserts the name this module publishes is a tool that
# list_tools() actually reports.
_FALLBACK_PROXY_TOOL = "proxy_api_call_path_based_api_v1_chargers"


async def _resolve_proxy_tool_name(mcp: FastMCP) -> str:
    """The *registered* name of the path-based charger-proxy tool, discovered at runtime."""
    tools = await mcp.list_tools(run_middleware=False)
    for tool in tools:
        required = set((tool.parameters or {}).get("required") or ())
        if _PROXY_REQUIRED_ARGS <= required:
            return tool.name
    for tool in tools:  # weaker signal, if the generated schema ever stops marking them required
        if "proxy" in tool.name and "path_based" in tool.name:
            return tool.name
    return _FALLBACK_PROXY_TOOL


def _load_control_center_catalog(proxy_tool: Optional[str] = None) -> dict:
    """Distil the on-charger RBAC API catalog into every endpoint across all services, keyed by port.

    All methods are described so the model knows what a charger supports and at what role. Only GET
    is callable -- non-GET rows carry ``callable: False`` and a reason, and ReadOnlyProxyMiddleware
    independently rejects any non-GET reaching the proxy.
    """
    endpoints = []
    services: dict[str, str] = {}
    not_callable_reason = (
        "NOT CALLABLE. Do not attempt this operation by any route -- not via the proxy, "
        "not via SSH or shell, not by crafting arguments. Listed for awareness only: it "
        "tells you the charger supports this and at what role, so you can advise a human "
        "operator who will perform it through the NOC UI."
    )
    with open(_CATALOG_CSV, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            method = (r.get("Method") or "").strip().upper()
            port = (r.get("Port") or "").strip()
            module = (r.get("Module") or "").strip()
            services.setdefault(port, module)
            is_get = method == "GET"
            # "Req Body" is deliberately never surfaced, on GET or non-GET rows alike: the model
            # may learn an operation EXISTS and at what role, never how to shape its payload. That
            # omission is the load-bearing safety property of this catalog -- do not "complete" it
            # by adding a request_schema field.
            endpoints.append({
                "port": int(port) if port.isdigit() else port,
                "module": module,
                "method": method,
                "path": (r.get("Path") or "").strip(),
                "name": (r.get("API Name") or "").strip(),
                "required_role": (r.get("Required Role") or "").strip(),
                "response_schema": (r.get("Resp Body") or "").strip()[:400],
                "callable": is_get,
            })
    proxy_tool = proxy_tool or _FALLBACK_PROXY_TOOL
    return {
        "source": "QFlex on-charger RBAC API catalog (rbac-rules.csv)",
        "services": [
            {"port": int(p) if p.isdigit() else p, "module": m}
            for p, m in sorted(services.items())
        ],
        "how_to_call": (
            "These are LIVE endpoints on a specific charger's on-device services. Only entries with "
            "callable=true (GET) can be fetched. To fetch one, call the tool "
            f"'{proxy_tool}' "
            "with: charger_id=<the NOC charger id>, port=<the endpoint's 'port' below>, "
            "path=<the endpoint's 'path' WITHOUT the leading slash>. Substitute placeholders "
            "like {gun_id}/{key} with real values. The charger must be ONLINE. Read-only (GET) "
            "endpoints only -- never use other methods against a charger."
        ),
        "not_callable_reason": not_callable_reason,
        "example": {
            "goal": "Get live charging status for charger CHG-123",
            "tool": proxy_tool,
            "args": {"charger_id": "CHG-123", "port": 8003, "path": "api/v1/charging/status"},
        },
        "endpoints": endpoints,
    }


def _register_control_center_catalog(mcp: FastMCP) -> None:
    """Add a knowledge tool describing the on-charger control-center endpoints (GET and
    non-GET) across all services (network :8000, rfid :8001, allocation :8002, charging
    :8003, platform :8005-8007), so the model knows what it can fetch via the generic
    charger proxy -- and what it must never attempt by any route."""
    # Eager parse, discarded: a malformed rbac-rules.csv must still fail at server startup,
    # not on the model's first catalog call. Only the *assembly* is deferred below.
    _load_control_center_catalog()
    cache: dict[str, dict] = {}

    @mcp.tool
    async def get_control_center_api_catalog() -> dict:
        """Catalog of LIVE on-charger control-center endpoints for a specific charger,
        across all on-device services: network/GSM (port 8000), RFID (8001), power allocation
        (8002), charging controller — gun/charging status, power modules, meters, SECC/SLAC,
        session details, contactors (8003), and platform dev-tools / diagnostics (8005-8007).

        Call this to discover what per-charger live data exists, then fetch a specific endpoint
        through the charger proxy tool using the endpoint's port + path (see the catalog's
        'how_to_call' and 'example'). The charger must be online.

        Entries marked callable=false are documented for awareness only -- they describe what the
        charger supports and at what role, but this server exposes no tool that calls them. Do not
        reconstruct them by other means (SSH, shell, or crafted proxy arguments); see the catalog's
        'not_callable_reason'."""
        # Built on first call, not at registration: the proxy tool's registered name can only
        # be read back from the server (list_tools is async), and by call time every tool --
        # generated and curated -- is registered.
        if "catalog" not in cache:
            cache["catalog"] = _load_control_center_catalog(
                await _resolve_proxy_tool_name(mcp))
        return cache["catalog"]
