"""Dispatcher tools over the NOC backend's reports and stats modules.

Both modules are read-only and purpose-built -- 24 endpoints that already implement the fleet
analysis the agent needs. Exposing them as 24 separate tools would swamp tool selection, so each
module gets one dispatcher whose ``report``/``metric`` argument is a described enum. The model
sees every available analysis in a single tool description.

These read the NOC backend's own database, never a charger, so they answer whether or not a
charger is online -- hence always source="history".
"""

from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP

from .freshness import HISTORY, is_error, stamp

# report name -> one-line description, surfaced in the tool schema.
REPORTS = {
    "alert-details": "Alert / alarm details report",
    "charger-energy": "Per-charger session count and energy delivery summary",
    "charger-ticket-status": "Charger status joined with open ticket counts",
    "commission-pending": "Chargers awaiting commissioning",
    "commissioned": "Commissioned chargers",
    "customer-status": "Customer-wise charger status",
    "fault-frequency": "Fault frequency analysis by alarm key and error code",
    "nightly-restart": "Nightly restart job summary",
    "session-details": "Bulk session details",
    "session-failures": "Sessions that ended with abnormal stop reasons",
    "ticket-resolution": "Ticket lifecycle and resolution time (MTTR)",
    "user-permissions": "User roles and permissions",
    "warranty-summary": "Charger warranty summary",
}

# metric name -> one-line description. Keys are paths under /api/v1/stats/.
STATS = {
    "": "System-wide statistics",
    "alarms": "Fleet-wide alarm statistics",
    "alarms/list": "Filtered alarm history",
    "error-trends": "Error trend time series",
    "error-trends/by-client": "Customer error ranking",
    "error-trends/by-error-type": "Error type breakdown",
    "error-trends/export": "Export error trends data",
    "error-trends/summary": "Error trends summary cards",
    "sessions": "Fleet-wide session statistics",
    "sessions/failed": "Detailed failed session analysis",
    "sessions/list": "Filtered session history",
}


def _params(**kwargs) -> dict:
    """Drop unset filters so the backend applies its own defaults."""
    return {k: v for k, v in kwargs.items() if v is not None}


async def _get(client, url: str, params: dict) -> dict:
    """GET a backend URL, returning a structured error rather than raising."""
    try:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {"data": data}
    except Exception as e:  # noqa: BLE001 - surface any failure as a clean payload
        return {"error": f"{type(e).__name__}: {e}", "url": url}


async def _fetch_report(client, report: str, **filters) -> dict:
    if report not in REPORTS:
        return {"error": f"unknown report {report!r}", "valid_reports": sorted(REPORTS)}
    out = await _get(client, f"/api/v1/reports/{report}", _params(**filters))
    return out if is_error(out) else stamp(out, source=HISTORY)


async def _fetch_stats(client, metric: str, **filters) -> dict:
    if metric not in STATS:
        return {"error": f"unknown metric {metric!r}", "valid_metrics": sorted(STATS)}
    suffix = f"/{metric}" if metric else ""
    out = await _get(client, f"/api/v1/stats{suffix}", _params(**filters))
    return out if is_error(out) else stamp(out, source=HISTORY)


def _describe(mapping: dict) -> str:
    return "; ".join(f"{k or '(overall)'} = {v}" for k, v in mapping.items())


# Descriptions are built at import and passed via @mcp.tool(description=...).
# They CANNOT be f-string docstrings: an f-string is not a string literal, so Python sets
# __doc__ to None and the tool would register with no description at all -- which would defeat
# the whole point of the dispatcher shape, since the enum listing IS the discoverability.
_REPORT_DESC = (
    "Run one of the NOC fleet reports. Read-only; reads the NOC database, so it works whether "
    "or not a charger is online.\n\n"
    f"Valid `report` values: {_describe(REPORTS)}.\n\n"
    "Filters are optional and passed through only when set; unset filters let the backend "
    "apply its own defaults."
)

_STATS_DESC = (
    "Fleet-wide statistics and error trends. Read-only; reads the NOC database, so it works "
    "whether or not a charger is online.\n\n"
    f'Valid `metric` values: {_describe(STATS)}. Pass "" for system-wide statistics.'
)


def _register_report_tools(mcp: FastMCP, client) -> None:
    """Register the reports and stats dispatchers onto ``mcp``."""

    @mcp.tool(description=_REPORT_DESC)
    async def noc_report(
        report: str,
        hours: Optional[int] = None,
        limit: Optional[int] = None,
        customer_name: Optional[str] = None,
        charger_id: Optional[str] = None,
        from_ts: Optional[str] = None,
        to_ts: Optional[str] = None,
    ) -> dict:
        return await _fetch_report(client, report, hours=hours, limit=limit,
                                    customer_name=customer_name, charger_id=charger_id,
                                    from_ts=from_ts, to_ts=to_ts)

    @mcp.tool(description=_STATS_DESC)
    async def noc_stats(
        metric: str = "",
        hours: Optional[int] = None,
        limit: Optional[int] = None,
        customer_name: Optional[str] = None,
        charger_id: Optional[str] = None,
        from_ts: Optional[str] = None,
        to_ts: Optional[str] = None,
    ) -> dict:
        return await _fetch_stats(client, metric, hours=hours, limit=limit,
                                   customer_name=customer_name, charger_id=charger_id,
                                   from_ts=from_ts, to_ts=to_ts)
