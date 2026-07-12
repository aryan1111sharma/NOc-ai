"""SSH command-execution tools for a live charger (the one non-read-only capability).

Runs arbitrary shell commands on a charger over its WebSocket PTY
(``wss://{host}/ws/ssh-terminal/{charger_id}``), gated by a mandatory two-step flow:
``charger_ssh_prepare`` mints a single-use confirmation token (holding the request + SSH
password transiently in memory); ``charger_ssh_execute`` consumes the token and runs the
command. The password is never logged, returned, or persisted. See the design spec.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from typing import Optional

from websockets.asyncio.client import connect as _ws_connect
from websockets.exceptions import WebSocketException

from .config import settings

# token -> {charger_id, command, username, password, created_at}
_PENDING: dict[str, dict] = {}


def _mint_pending(req: dict) -> str:
    """Store a pending SSH request and return its single-use confirmation token.

    Also sweeps any expired pending entries (prepares that were never executed), so the
    in-memory store can't grow unbounded.
    """
    now = time.time()
    ttl = settings.ssh_pending_ttl_seconds
    for stale in [t for t, r in _PENDING.items() if now - r["created_at"] > ttl]:
        _PENDING.pop(stale, None)
    token = secrets.token_urlsafe(24)
    _PENDING[token] = {**req, "created_at": now}
    return token


def _pop_pending(token: str, ttl: int) -> Optional[dict]:
    """Consume a pending request by token. Returns None if unknown, already used, or older
    than ``ttl`` seconds (expired entries are evicted)."""
    req = _PENDING.pop(token, None)
    if req is None:
        return None
    if time.time() - req["created_at"] > ttl:
        return None
    return req


_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_SENTINEL_RE = re.compile(r"__QMCP_EXIT_(\d+)__")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _parse_output(raw: str, command: str) -> tuple[str, Optional[int]]:
    """From accumulated PTY output, strip ANSI, split off the sentinel to get the exit code,
    and drop a leading echoed-command line if the PTY echoed it."""
    clean = _strip_ansi(raw).replace("\r\n", "\n").replace("\r", "\n")
    m = _SENTINEL_RE.search(clean)
    exit_code = int(m.group(1)) if m else None
    body = clean[: m.start()] if m else clean
    lines = body.split("\n")
    if lines and lines[0].strip() == command.strip():   # PTY echoed the typed command
        lines = lines[1:]
    return "\n".join(lines).strip(), exit_code


async def _run_ssh_command(
    ws_url: str, token: str, username: str, password: str, command: str,
    *, connect_timeout: float, command_timeout: float, max_bytes: int,
) -> dict:
    """Open the charger SSH PTY, run one command, return {output, exit_code, truncated}.

    Wraps the command with an ``__QMCP_EXIT_$?__`` sentinel to recover clean output + exit
    code from a raw PTY stream. Any failure returns a structured ``{"error": ...}``.
    """
    payload = f"{command}\necho __QMCP_EXIT_$?__\n"
    try:
        async with _ws_connect(ws_url, open_timeout=connect_timeout) as ws:
            await ws.send(json.dumps({
                "action": "connect", "token": token,
                "username": username, "password": password, "cols": 120, "rows": 30,
            }))
            # phase 1: await the 'connected' frame
            deadline = asyncio.get_event_loop().time() + connect_timeout
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    return {"error": "timed out waiting for SSH connect"}
                frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
                if frame.get("type") == "connected":
                    break
                if frame.get("type") == "error":
                    return {"error": f"SSH connect error: {frame.get('message', '')}"}
            # phase 2: send command, accumulate output until the sentinel
            await ws.send(json.dumps({"action": "data", "data": payload}))
            buf: list[str] = []
            total = 0
            truncated = False
            scan_tail = ""
            sentinel_exit: Optional[int] = None
            deadline = asyncio.get_event_loop().time() + command_timeout
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    return {"error": f"command timed out after {command_timeout}s"}
                try:
                    frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
                except asyncio.TimeoutError:
                    return {"error": f"command timed out after {command_timeout}s"}
                if frame.get("type") == "error":
                    return {"error": f"SSH error: {frame.get('message', '')}"}
                if frame.get("type") != "data":
                    continue
                d = frame.get("data", "")
                room = max_bytes - total          # keep the FIRST max_bytes of output
                if room > 0:
                    buf.append(d[:room])
                    if len(d) > room:
                        truncated = True
                else:
                    truncated = True
                total += len(d)
                # Scan the rolling tail (which always sees the sentinel, even when the
                # sentinel-bearing chunk was truncated out of buf) for end-of-command + exit code.
                scan_tail = (scan_tail + d)[-256:]
                m = _SENTINEL_RE.search(_strip_ansi(scan_tail))
                if m:
                    sentinel_exit = int(m.group(1))
                    break
            output, parsed_exit = _parse_output("".join(buf), command)
            # On truncation the sentinel is dropped from buf, so fall back to the exit code
            # captured from the always-scanned tail.
            exit_code = parsed_exit if parsed_exit is not None else sentinel_exit
            return {"output": output, "exit_code": exit_code,
                    "truncated": truncated, "bytes_received": total}
    except (WebSocketException, OSError, asyncio.TimeoutError, json.JSONDecodeError) as e:
        return {"error": f"{type(e).__name__}: {e}"}


_audit = logging.getLogger("noc_mcp.ssh_audit")


def _configure_audit_logger() -> None:
    """Attach a handler so ssh_audit records are actually emitted (idempotent).

    Without this, ``_audit.info(...)`` is dropped by default (no handler + root WARNING).
    Audit is the compensating control for allowing arbitrary commands with no denylist, so it
    must not be a silent no-op. ``propagate`` stays True so pytest ``caplog`` still captures.
    Writes to ``SSH_AUDIT_LOG_FILE`` if configured, else stderr.
    """
    if _audit.handlers:
        return
    _audit.setLevel(logging.INFO)
    path = settings.ssh_audit_log_file
    handler = logging.FileHandler(path, encoding="utf-8") if path else logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    _audit.addHandler(handler)


def _default_ws_url_builder(charger_id: str) -> str:
    base = settings.noc_base_url.rstrip("/")
    ws = base.replace("https://", "wss://").replace("http://", "ws://")
    return f"{ws}/ws/ssh-terminal/{charger_id}"


def _register_ssh_tools(mcp, client, ws_url_builder=None) -> None:
    """Register the two-step SSH command tools onto ``mcp``."""
    _configure_audit_logger()
    build_ws_url = ws_url_builder or _default_ws_url_builder

    @mcp.tool
    async def charger_ssh_prepare(
        charger_id: str, command: str, ssh_username: str, ssh_password: str,
    ) -> dict:
        """STEP 1 of running a shell command on a LIVE charger over SSH. Does NOT execute —
        it returns a one-time confirmation_token and a preview. You MUST show the user the
        command + charger and get their explicit approval, then call charger_ssh_execute with
        the token. This runs on real hardware; treat it as such."""
        token = _mint_pending({
            "charger_id": charger_id, "command": command,
            "username": ssh_username, "password": ssh_password,
        })
        return {
            "confirmation_token": token,
            "charger_id": charger_id,
            "command": command,
            "expires_in_seconds": settings.ssh_pending_ttl_seconds,
            "warning": ("Runs on a LIVE charger. Show this command to the user and get "
                        "explicit approval before calling charger_ssh_execute."),
        }

    @mcp.tool
    async def charger_ssh_execute(confirmation_token: str) -> dict:
        """STEP 2: run the command from a charger_ssh_prepare the user approved. Requires the
        one-time confirmation_token. Returns {charger_id, command, output, exit_code,
        truncated} or {error}. Read-only servers reject this; this is the one write path."""
        req = _pop_pending(confirmation_token, ttl=settings.ssh_pending_ttl_seconds)
        if req is None:
            return {"error": "Unknown, expired, or already-used confirmation_token. "
                             "Call charger_ssh_prepare again and get user approval."}
        token = await client._ensure_token()
        result = await _run_ssh_command(
            build_ws_url(req["charger_id"]), token, req["username"], req["password"],
            req["command"],
            connect_timeout=settings.ssh_connect_timeout,
            command_timeout=settings.ssh_command_timeout,
            max_bytes=settings.ssh_max_output_bytes,
        )
        _audit.info(
            "ssh_execute charger=%s command=%r exit=%s bytes=%s",
            req["charger_id"], req["command"],
            result.get("exit_code"), result.get("bytes_received", len(result.get("output", ""))),
        )
        return {"charger_id": req["charger_id"], "command": req["command"], **result}
