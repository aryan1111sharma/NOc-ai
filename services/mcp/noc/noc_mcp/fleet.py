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
from .reports import _get, _params


async def _fetch_fleet_status(client, *, offline_only: bool, online_only: bool,
                              search: Optional[str], customer_name: Optional[str],
                              model: Optional[str], limit: Optional[int],
                              as_map: bool) -> dict:
    if offline_only and online_only:
        return {"error": "offline_only and online_only are mutually exclusive"}

    if as_map:
        ignored = [n for n, v in (
            ("offline_only", offline_only), ("online_only", online_only),
            ("search", search), ("model", model), ("limit", limit),
        ) if v]
        if ignored:
            return {"error": (
                f"as_map=True cannot apply {', '.join(ignored)}: the /chargers/map endpoint "
                "accepts only customer_name. Re-run with as_map=False to use these filters."
            )}
        params = _params(customer_name=customer_name)
        out = await _get(client, "/api/v1/chargers/map", params)
    else:
        params = _params(search=search, customer_name=customer_name, model=model, limit=limit)
        params.update(_params(
            offline_only="true" if offline_only else None,
            online_only="true" if online_only else None,
        ))
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
        map fields instead of a paginated page -- but the map endpoint accepts only
        customer_name, so offline_only, online_only, search, model, and limit are rejected
        (with an error, not silently ignored) when as_map=True.

        Reads the NOC backend, so it works regardless of any individual charger's state."""
        return await _fetch_fleet_status(
            client, offline_only=offline_only, online_only=online_only, search=search,
            customer_name=customer_name, model=model, limit=limit, as_map=as_map)
