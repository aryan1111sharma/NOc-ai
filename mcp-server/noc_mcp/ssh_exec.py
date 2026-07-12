"""SSH command-execution tools for a live charger (the one non-read-only capability).

Runs arbitrary shell commands on a charger over its WebSocket PTY
(``wss://{host}/ws/ssh-terminal/{charger_id}``), gated by a mandatory two-step flow:
``charger_ssh_prepare`` mints a single-use confirmation token (holding the request + SSH
password transiently in memory); ``charger_ssh_execute`` consumes the token and runs the
command. The password is never logged, returned, or persisted. See the design spec.
"""

from __future__ import annotations

import re
import secrets
import time
from typing import Optional

# token -> {charger_id, command, username, password, created_at}
_PENDING: dict[str, dict] = {}


def _mint_pending(req: dict) -> str:
    """Store a pending SSH request and return its single-use confirmation token."""
    token = secrets.token_urlsafe(24)
    _PENDING[token] = {**req, "created_at": time.time()}
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
