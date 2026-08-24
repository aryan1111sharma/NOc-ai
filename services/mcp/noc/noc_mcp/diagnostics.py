"""Diagnostics tools — request a log download / export state logs for a charger.

Both are job-creation calls (the backend packages data asynchronously), not raw reads —
that's why they live here rather than in control_center.py's read-only composites. Neither
touches charger configuration or behavior; they only package existing data for download.
"""

from __future__ import annotations

from typing import Optional

import httpx

_STATE_LOG_SOURCES = ("genone_full_state", "genone_state_data")


async def _call(client, method: str, path: str, *, json_body=None, params=None) -> dict:
    try:
        resp = await client.request(method, path, json=json_body, params=params)
    except httpx.HTTPError as e:
        return {"error": f"{type(e).__name__}: {e}"}
    if resp.status_code >= 400:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        return {"error": f"NOC backend returned {resp.status_code}", "detail": detail}
    try:
        return resp.json()
    except Exception:
        return {"status_code": resp.status_code, "note": "non-JSON response body"}


def _register_diagnostics_tools(mcp, client) -> None:
    @mcp.tool
    async def charger_request_log_download(
        charger_id: str, source: str = "all", lines: int = 100
    ) -> dict:
        """Kick off a log-download job for a charger. source is a log source name or 'all'
        (default); lines is how many lines to include (1-10000, default 100). Returns
        {job_id, status, message} immediately — this starts the job, it does not return
        the log content itself."""
        body = {"source": source, "lines": lines}
        return await _call(client, "POST", f"/api/v1/chargers/{charger_id}/logs/download", json_body=body)

    @mcp.tool
    async def charger_export_state_logs(
        charger_id: str,
        log_id: Optional[str] = None,
        from_ts: Optional[str] = None,
        to_ts: Optional[str] = None,
        source: Optional[str] = None,
    ) -> dict:
        """Request a state-log zip export for a charger. Either pass log_id to export one
        specific log, or from_ts/to_ts (ISO-8601) for a time range. source optionally
        filters by log source type ('genone_full_state' or 'genone_state_data')."""
        if source is not None and source not in _STATE_LOG_SOURCES:
            return {"error": f"source must be one of {_STATE_LOG_SOURCES}, got {source!r}"}
        params = {k: v for k, v in {
            "log_id": log_id, "from_ts": from_ts, "to_ts": to_ts, "source": source,
        }.items() if v is not None}
        return await _call(
            client, "POST", f"/api/v1/chargers/{charger_id}/state-logs/export", params=params
        )
