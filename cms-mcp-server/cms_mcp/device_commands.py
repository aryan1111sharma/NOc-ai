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
from .server import _resolve_charger_id, _post_json, _post_datatables

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
    placeholder is unfilled, or if a value contains a path separator, '..', '%', or
    non-ASCII/non-printable characters (which could smuggle extra path segments, via
    literal or percent-encoded separators, and escape the intended endpoint)."""
    args = args or {}
    holes = _PLACEHOLDER_RE.findall(api_template)
    missing = [h for h in holes if h not in args]
    if missing:
        raise ValueError(
            f"command '{api_template}' needs args for: {', '.join(missing)}")
    out = api_template
    for h in holes:
        val = str(args[h])
        if ("/" in val or "\\" in val or ".." in val or "%" in val
                or not val.isascii() or not val.isprintable()):
            raise ValueError(
                f"arg '{h}' must be a single path segment "
                f"(no '/', '\\', '..', '%', or non-ASCII characters): {val!r}")
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


async def _post_wrapped(client, path: str, wrapper_key: str, obj: Any, *,
                        idempotent: bool = True):
    """POST a legacy `{<key>: <json>}` wrapper body (the unquoted-key shape the CMS
    JS uses) with the AJAX + JSON content-type headers. Returns the raw response.
    Set idempotent=False for non-idempotent writes (ExecuteApi) so a transient flap
    is surfaced (CmsUnavailableError) rather than retried — retrying would double-fire."""
    body = "{%s: %s}" % (wrapper_key, json.dumps(obj))
    headers = {"X-Requested-With": "XMLHttpRequest",
               "Content-Type": "application/json; charset=utf-8"}
    if not idempotent:
        headers["X-CMS-No-Retry"] = "1"
    resp = await client.post(path, content=body, headers=headers)
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
        resp = await _post_datatables(client, _HISTORY_PATH, charger_id, n)
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


_audit = logging.getLogger("cms_mcp.device_audit")


def _configure_audit_logger() -> None:
    """Attach a handler so device_audit records are emitted (idempotent). Audit is the
    compensating control for the write path, so it must not be a silent no-op."""
    if _audit.handlers:
        return
    _audit.setLevel(logging.INFO)
    path = settings.device_audit_log_file
    handler = logging.FileHandler(path, encoding="utf-8") if path else logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    _audit.addHandler(handler)


def register_device_tools(mcp, client, charger_map: dict) -> None:
    """Register the device-command tools onto `mcp`, sharing the server's charger map."""
    _configure_audit_logger()

    @mcp.tool
    async def cms_run_device_query(
        qcharger_id: str, command: str, args: Optional[dict] = None
    ) -> dict:
        """Run a READ-ONLY device diagnostic on ONE charger (by QChargerID) via the old
        NOC and return the device's response. `command` is the exact API path from
        cms_list_charger_actions (e.g. "id", "version", "state", "outlets",
        "ocpp-client/config"); fill any {placeholder} via `args`
        (e.g. command="outlets/{number}/state", args={"number": 1}). Only GET commands
        are allowed — for writes use cms_run_device_action_prepare."""
        cid = await _resolve_charger_id(client, qcharger_id, charger_map)
        catalog = await _load_catalog(client, cid)
        row = catalog.get(command)
        if row is None:
            near = sorted(k for k in catalog if command.split("/")[0] in k)[:5]
            hint = f" Did you mean: {', '.join(near)}?" if near else ""
            raise ValueError(f"Unknown command '{command}' for {qcharger_id}.{hint}")
        if str(row.get("ApiType")) != "GET":
            raise ValueError(
                f"'{command}' is a {row.get('ApiType')} (write); cms_run_device_query "
                f"runs read-only GET commands only. Use cms_run_device_action_prepare.")
        resolved = resolve_path(command, args)
        await client._ensure_login()
        vvm = {"ChargerID": cid, "ApiID": row["ApiID"], "Input": resolved,
               "API": resolved, "UpdatedBy": getattr(client, "user_id", 0)}
        baseline = await _baseline_execid(client, cid)
        await _post_wrapped(client, _EXECUTE_PATH, "vvm", vvm, idempotent=False)
        hit = await _await_result(
            client, cid, resolved, baseline,
            timeout=settings.device_poll_timeout, interval=settings.device_poll_interval)
        if hit is None:
            raise RuntimeError(
                f"No result for '{command}' on {qcharger_id} within "
                f"{settings.device_poll_timeout:.0f}s — the charger may be offline or the "
                f"CMS is busy; retry shortly.")
        return {
            "qcharger_id": qcharger_id, "command": command,
            "execution_id": hit.get("ExecutionID"), "status_id": hit.get("StatusID"),
            "executed_at": hit.get("strExecutedTime"),
            "result": await _read_log(client, hit["Output"]),
        }

    @mcp.tool
    async def cms_run_device_action_prepare(
        qcharger_id: str, command: str, args: Optional[dict] = None
    ) -> dict:
        """STEP 1 of running a WRITE command on a LIVE charger (by QChargerID). Does NOT
        execute — validates the command against the recovery allow-list and returns a
        one-time confirmation_token + preview. Show the user the command + charger, get
        explicit approval, then call cms_run_device_action_execute with the token.
        Allowed: reset, restartStack, controller reboot, ocpp-client start/stop/restart,
        outlets/{number}/stop, services/rfid/clear."""
        if command not in WRITE_ALLOWLIST:
            raise ValueError(
                f"'{command}' is not on the device-action allow-list. Allowed writes: "
                f"{', '.join(sorted(WRITE_ALLOWLIST))}.")
        cid = await _resolve_charger_id(client, qcharger_id, charger_map)
        catalog = await _load_catalog(client, cid)
        row = catalog.get(command)
        if row is None:
            raise ValueError(f"'{command}' is not in {qcharger_id}'s catalog.")
        resolved = resolve_path(command, args)
        token = _mint_pending({
            "qcharger_id": qcharger_id, "charger_id": cid, "api_id": row["ApiID"],
            "command": command, "resolved": resolved,
        })
        return {
            "confirmation_token": token, "qcharger_id": qcharger_id,
            "command": command, "resolved_path": resolved,
            "expires_in_seconds": settings.device_pending_ttl_seconds,
            "warning": ("Runs on a LIVE charger. Show this command to the user and get "
                        "explicit approval before calling cms_run_device_action_execute."),
        }

    @mcp.tool
    async def cms_run_device_action_execute(confirmation_token: str) -> dict:
        """STEP 2: run the WRITE command from a cms_run_device_action_prepare the user
        approved. Requires the one-time confirmation_token. Schedules the command on the
        charger and returns the ack (+ execution_id if it resolves quickly)."""
        req = _pop_pending(confirmation_token, ttl=settings.device_pending_ttl_seconds)
        if req is None:
            return {"error": "Unknown, expired, or already-used confirmation_token. "
                             "Call cms_run_device_action_prepare again and get approval."}
        if req["command"] not in WRITE_ALLOWLIST:   # defensive re-check
            return {"error": f"'{req['command']}' is not on the allow-list."}
        cid = req["charger_id"]
        await client._ensure_login()
        vvm = {"ChargerID": cid, "ApiID": req["api_id"], "Input": req["resolved"],
               "API": req["resolved"], "UpdatedBy": getattr(client, "user_id", 0)}
        baseline = await _baseline_execid(client, cid)
        resp = await _post_wrapped(client, _EXECUTE_PATH, "vvm", vvm, idempotent=False)
        _audit.info("device_execute charger=%s command=%r resolved=%r user=%s",
                    req["qcharger_id"], req["command"], req["resolved"],
                    getattr(client, "user_id", 0))
        ack = resp.json() if resp.text.strip() else ""
        hit = await _await_result(client, cid, req["resolved"], baseline,
                                  timeout=min(settings.device_poll_timeout, 10.0),
                                  interval=settings.device_poll_interval)
        return {"qcharger_id": req["qcharger_id"], "command": req["command"],
                "resolved_path": req["resolved"], "ack": ack,
                "execution_id": hit.get("ExecutionID") if hit else None}

    @mcp.tool
    async def cms_read_device_log(qcharger_id: str, path: str) -> Any:
        """Read a session/alert state-log or command-output blob from the old NOC by its
        server-side file path (the FullStatePath / StateLogPath / Output value returned by
        cms_get_charger_sessions / _alerts / device tools). Read-only; path must be a known
        CMS state-log/result file."""
        if not is_safe_log_path(path):
            raise ValueError(
                "path must be a CMS state-log/result file under ApiResponses or a "
                "*State_Logs root (e.g. E:\\ApiResponses\\r.txt).")
        return await _read_log(client, path)
