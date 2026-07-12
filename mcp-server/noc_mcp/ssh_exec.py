"""SSH command-execution tools for a live charger (the one non-read-only capability).

Runs arbitrary shell commands on a charger over its WebSocket PTY
(``wss://{host}/ws/ssh-terminal/{charger_id}``), gated by a mandatory two-step flow:
``charger_ssh_prepare`` mints a single-use confirmation token (holding the request + SSH
password transiently in memory); ``charger_ssh_execute`` consumes the token and runs the
command. The password is never logged, returned, or persisted. See the design spec.
"""

from __future__ import annotations

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
