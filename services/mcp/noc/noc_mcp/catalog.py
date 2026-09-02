"""On-charger control-center API catalog.

Distils ``catalog/rbac-rules.csv`` -- the QFlex on-charger RBAC API catalog -- into a knowledge
tool describing what a specific charger exposes across its on-device services, so the model knows
what it can fetch through the generic charger proxy.
"""

from __future__ import annotations

import csv
from pathlib import Path

from fastmcp import FastMCP

_CATALOG_CSV = Path(__file__).parent / "catalog" / "rbac-rules.csv"


def _load_control_center_catalog() -> dict:
    """Distil the on-charger RBAC API catalog into every endpoint across all services, keyed by port.

    All methods are described so the model knows what a charger supports and at what role. Only GET
    is callable -- non-GET rows carry ``callable: False`` and a reason, and ReadOnlyProxyMiddleware
    independently rejects any non-GET reaching the proxy.
    """
    endpoints = []
    services: dict[str, str] = {}
    with open(_CATALOG_CSV, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            method = (r.get("Method") or "").strip().upper()
            port = (r.get("Port") or "").strip()
            module = (r.get("Module") or "").strip()
            services.setdefault(port, module)
            is_get = method == "GET"
            endpoints.append({
                "port": int(port) if port.isdigit() else port,
                "module": module,
                "method": method,
                "path": (r.get("Path") or "").strip(),
                "name": (r.get("API Name") or "").strip(),
                "required_role": (r.get("Required Role") or "").strip(),
                "response_schema": (r.get("Resp Body") or "").strip()[:400],
                "callable": is_get,
                "not_callable_reason": None if is_get else (
                    "This server is read-only: only GET endpoints are callable through the proxy. "
                    "Listed so you know the charger supports it and at what role."
                ),
            })
    return {
        "source": "QFlex on-charger RBAC API catalog (rbac-rules.csv)",
        "services": [
            {"port": int(p) if p.isdigit() else p, "module": m}
            for p, m in sorted(services.items())
        ],
        "how_to_call": (
            "These are LIVE endpoints on a specific charger's on-device services. Only entries with "
            "callable=true (GET) can be fetched. To fetch one, call the tool "
            "'proxy_api_call_path_based_api_v1_chargers__charger_id__proxy__port___path__get' "
            "with: charger_id=<the NOC charger id>, port=<the endpoint's 'port' below>, "
            "path=<the endpoint's 'path' WITHOUT the leading slash>. Substitute placeholders "
            "like {gun_id}/{key} with real values. The charger must be ONLINE. Read-only (GET) "
            "endpoints only -- never use other methods against a charger."
        ),
        "example": {
            "goal": "Get live charging status for charger CHG-123",
            "tool": "proxy_api_call_path_based_api_v1_chargers__charger_id__proxy__port___path__get",
            "args": {"charger_id": "CHG-123", "port": 8003, "path": "api/v1/charging/status"},
        },
        "read_endpoints": endpoints,
    }


def _register_control_center_catalog(mcp: FastMCP) -> None:
    """Add a knowledge tool describing the on-charger control-center READ endpoints across
    all services (network :8000, rfid :8001, allocation :8002, charging :8003, platform
    :8005-8007), so the model knows what it can fetch via the generic charger proxy."""
    catalog = _load_control_center_catalog()

    @mcp.tool
    def get_control_center_api_catalog() -> dict:
        """Catalog of LIVE on-charger control-center READ endpoints for a specific charger,
        across all on-device services: network/GSM (port 8000), RFID (8001), power allocation
        (8002), charging controller — gun/charging status, power modules, meters, SECC/SLAC,
        session details, contactors (8003), and platform dev-tools / diagnostics (8005-8007).

        Call this to discover what per-charger live data exists, then fetch a specific endpoint
        through the charger proxy tool using the endpoint's port + path (see the catalog's
        'how_to_call' and 'example'). The charger must be online.

        Entries marked callable=false are documented for awareness only -- they describe what the
        charger supports and at what role, but this server cannot invoke them."""
        return catalog
