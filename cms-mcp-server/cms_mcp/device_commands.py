"""Device-command tools over the old NOC ChargerDetails endpoints.

Three capabilities (4 MCP tools):
  * cms_run_device_query          — read-only device diagnostics (all GET commands)
  * cms_run_device_action_prepare / _execute — gated writes (curated recovery
    allow-list) behind a two-step, audited confirm gate (mirrors
    mcp-server/noc_mcp/ssh_exec.py)
  * cms_read_device_log           — read a session/alert state-log blob

ExecuteApi is asynchronous: it returns a scheduling ack, and the device result
appears later in the execution history with an Output file path read via
ReadJsonLogFile. See docs/superpowers/specs/2026-07-14-cms-device-command-tools-design.md.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from typing import Any, Optional

from .config import settings
from .server import _resolve_charger_id, _post_json, _datatables_body

_EXECUTE_PATH = "/ChargerDetails/ExecuteApi"
_HISTORY_PATH = "/ChargerDetails/GetApiExecutionDetailsByID"
_READLOG_PATH = "/ChargerDetails/ReadJsonLogFile"
_CATALOG_PATH = "/ChargerDetails/GetAllApisByChargerID"

# The ONLY write commands runnable via cms_run_device_action. Matched against the
# catalog `API` template. Everything else write is unreachable in code.
WRITE_ALLOWLIST = frozenset({
    "reset",
    "controllers/{scripts}/api/outlets/ccs/restartStack",
    "controllers/{seccid}/api/system/reboot",
    "ocpp-client/restart",
    "ocpp-client/start",
    "ocpp-client/stop",
    "outlets/{number}/stop",
    "services/rfid/clear",
})

# ReadJsonLogFile appears to read arbitrary server paths; constrain to the known
# result / state-log roots so the tool can't become a file-read primitive.
_SAFE_LOG_PATH_RE = re.compile(
    r"^[A-Za-z]:\\(ApiResponses|CMS[\w ]*State_Logs)\\[^\r\n]+\.(txt|json|log)$",
    re.IGNORECASE,
)
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")

# Single-use write-confirm tokens: token -> {..request.., created_at}
_PENDING: dict[str, dict] = {}


def is_safe_log_path(path: str) -> bool:
    """True if `path` is under a known CMS result/state-log root (guards ReadJsonLogFile)."""
    if not isinstance(path, str) or ".." in path:
        return False
    return bool(_SAFE_LOG_PATH_RE.match(path.strip()))


def resolve_path(api_template: str, args: Optional[dict]) -> str:
    """Substitute {placeholders} in a catalog API template. Raises ValueError if any
    placeholder is unfilled, or if a value contains a path separator / '..' (which
    could smuggle extra path segments and escape the intended endpoint)."""
    args = args or {}
    holes = _PLACEHOLDER_RE.findall(api_template)
    missing = [h for h in holes if h not in args]
    if missing:
        raise ValueError(
            f"command '{api_template}' needs args for: {', '.join(missing)}")
    out = api_template
    for h in holes:
        val = str(args[h])
        if "/" in val or "\\" in val or ".." in val:
            raise ValueError(
                f"arg '{h}' must be a single path segment (no '/', '\\', or '..'): {val!r}")
        out = out.replace("{" + h + "}", val)
    return out


def _mint_pending(req: dict) -> str:
    """Store a pending write request, sweep expired entries, return a single-use token."""
    now = time.time()
    ttl = settings.device_pending_ttl_seconds
    for stale in [t for t, r in _PENDING.items() if now - r["created_at"] > ttl]:
        _PENDING.pop(stale, None)
    token = secrets.token_urlsafe(24)
    _PENDING[token] = {**req, "created_at": now}
    return token


def _pop_pending(token: str, ttl: int) -> Optional[dict]:
    """Consume a pending request by token. None if unknown, used, or expired."""
    req = _PENDING.pop(token, None)
    if req is None or time.time() - req["created_at"] > ttl:
        return None
    return req


async def _post_wrapped(client, path: str, wrapper_key: str, obj: Any):
    """POST a legacy `{<key>: <json>}` wrapper body (the unquoted-key shape the CMS
    JS uses) with the AJAX + JSON content-type headers. Returns the raw response."""
    body = "{%s: %s}" % (wrapper_key, json.dumps(obj))
    resp = await client.post(
        path, content=body,
        headers={"X-Requested-With": "XMLHttpRequest",
                 "Content-Type": "application/json; charset=utf-8"},
    )
    resp.raise_for_status()
    return resp


async def _load_catalog(client, charger_id: int) -> dict[str, dict]:
    """Fetch the per-charger device-API catalog as {API-template: row}."""
    rows = await _post_json(client, _CATALOG_PATH, data={"ChargerID": str(charger_id)})
    out = {}
    for r in rows:
        api = r.get("API")
        if api:
            out[str(api)] = r
    return out


async def _read_log(client, path: str) -> Any:
    """POST ReadJsonLogFile for a server-side path; parse JSON, else return raw text."""
    resp = await _post_wrapped(client, _READLOG_PATH, "Paths", path)
    try:
        return resp.json()
    except Exception:
        return resp.text


async def _history_rows(client, charger_id: int, n: int) -> list[dict]:
    """Fetch the latest `n` execution-history rows; [] on empty/flap."""
    try:
        resp = await _post_json(client, _HISTORY_PATH,
                                json=_datatables_body(charger_id, n))
    except Exception:
        return []
    return resp.get("data", []) if isinstance(resp, dict) else []


async def _baseline_execid(client, charger_id: int) -> int:
    """Max ExecutionID for this charger right now (correlation baseline)."""
    rows = await _history_rows(client, charger_id, 5)
    return max((int(r.get("ExecutionID") or 0) for r in rows), default=0)


async def _await_result(client, charger_id: int, resolved_api: str,
                        baseline: int, *, timeout: float, interval: float) -> Optional[dict]:
    """Poll history until our new row appears: ExecutionID > baseline, matching API
    path, with a non-empty Output. Returns the row or None on timeout."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        await asyncio.sleep(interval)
        for r in await _history_rows(client, charger_id, 10):
            if (int(r.get("ExecutionID") or 0) > baseline
                    and str(r.get("API")) == resolved_api
                    and r.get("Output")):
                return r
    return None
