"""Fleet-level charger status.

The NOC backend derives online status from its live WebSocket connection registry, so
``/api/v1/chargers`` answers "which chargers are down right now" -- the question a NOC shift
starts with, and one the legacy CMS tools can only answer as a count. Reads the backend, never
a charger.
"""

from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP

from .freshness import HISTORY, is_error, stamp
from .reports import _get


async def _fetch_fleet_status(client, *, offline_only: bool, online_only: bool,
                              search: Optional[str], customer_name: Optional[str],
                              model: Optional[str], limit: Optional[int],
                              as_map: bool) -> dict:
    if offline_only and online_only:
        return {"error": "offline_only and online_only are mutually exclusive"}

    if as_map:
        params = {k: v for k, v in {"customer_name": customer_name}.items() if v is not None}
        out = await _get(client, "/api/v1/chargers/map", params)
    else:
        params = {k: v for k, v in {
            "offline_only": "true" if offline_only else None,
            "online_only": "true" if online_only else None,
            "search": search,
            "customer_name": customer_name,
            "model": model,
            "limit": limit,
        }.items() if v is not None}
        out = await _get(client, "/api/v1/chargers", params)

    return out if is_error(out) else stamp(out, source=HISTORY)


def _register_fleet_tools(mcp: FastMCP, client) -> None:
    """Register the fleet-status tool onto ``mcp``."""

    @mcp.tool
    async def noc_fleet_status(
        offline_only: bool = False,
        online_only: bool = False,
        search: Optional[str] = None,
        customer_name: Optional[str] = None,
        model: Optional[str] = None,
        limit: Optional[int] = None,
        as_map: bool = False,
    ) -> dict:
        """Which chargers are currently online or offline, across the fleet.

        Start here for "what needs attention" questions. Set offline_only=True to list the
        chargers that are down right now (not just a count). Filter with search (charger id or
        model), customer_name, or model. Set as_map=True for every charger with lightweight
        map fields instead of a paginated page.

        Reads the NOC backend, so it works regardless of any individual charger's state."""
        return await _fetch_fleet_status(
            client, offline_only=offline_only, online_only=online_only, search=search,
            customer_name=customer_name, model=model, limit=limit, as_map=as_map)
