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
