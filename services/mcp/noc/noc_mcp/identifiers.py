"""Validation for NOC identifiers that get interpolated into URLs.

Shared by ``control_center`` (HTTP charger proxy), ``history`` (backend history routes, via
``noc_mcp.http``) and ``ssh_exec`` (WebSocket PTY) -- every place that splices a
caller-supplied identifier into a URL path.

Why validation and not just encoding
------------------------------------
Percent-encoding the id is **not** sufficient on its own. The receiving ASGI/FastAPI server
percent-DECODES the path before it routes, so ``%2F`` becomes ``/`` again and re-splits the
route. Verified against a Starlette router using this repo's own path template
(``/api/v1/chargers/{charger_id}/proxy/{port}/{path:path}``)::

    wire  : /api/v1/chargers/CHG-1%2Fproxy%2F8005%2Fapi%2Fv1%2Fdevtools%2Flogs%2Fx/proxy/8003/...
    parsed: charger_id='CHG-1'  port='8005'  path='api/v1/devtools/logs/x/proxy/8003/...'

i.e. the caller, not the tool, chose the port and the on-charger path. The only durable
defence is to reject the id up front; encoding then remains as a harmless backstop.

The allowlist is deliberately tight: NOC charger ids seen in this codebase and its fixtures
are alphanumerics with ``-``, ``_`` and ``.`` (``CHG-1``, ``QC_0012``, ``abc.def-9``, and
24-hex object-id style). Anything else is rejected rather than sanitised.
"""

from __future__ import annotations

import re
from typing import Any, Optional

# No '/', '?', '#', '%', '\', ':' or '~' -- none of which belong in a charger id, and each of
# which changes how a decoding receiver parses the URL.
_CHARGER_ID_ALLOWED = re.compile(r"^[A-Za-z0-9._-]+$")

# Session ids carry the *same* hazard as charger ids -- ``/chargers/{charger_id}/sessions/
# {session_id}/events`` splices them into the same decoded-then-routed path -- so they get the
# same character class, and for reasons the bundled spec states rather than by assumption:
#
#   * the spec declares ``session_id`` as ``{"type": "string"}`` (not an integer), and
#   * that endpoint's own description names the two concrete shapes the backend must accept:
#     "Numeric session_id -> query TimescaleDB genone_full_state by session_id field" and
#     chargers that "use UUID session IDs".
#
# Digits cover the numeric fallback, hex + '-' cover a UUID, and alphanumerics + '-'/'_' cover
# the short prefixed ids this repo's fixtures use (``S1``, ``S-old``). '.' is admitted for the
# same reason it is on charger ids: it is RFC 3986 *unreserved*, so it cannot change how a
# decoding receiver parses the path -- and the dot-segment check below still rejects '.'/'..'.
_SESSION_ID_ALLOWED = re.compile(r"^[A-Za-z0-9._-]+$")


def _validate(value: Any, *, kind: str, allowed: re.Pattern) -> Optional[str]:
    """Shared body for the identifier validators: one rule set, two names.

    Returns a reason rather than raising: every call site answers with a structured error
    payload instead of propagating exceptions.
    """
    if not isinstance(value, str) or not value.strip():
        return f"ValueError: {kind} must be a non-empty string"
    if not allowed.match(value):
        return f"ValueError: {kind} has an illegal character ({value!r})"
    if value in (".", ".."):
        # Matches the allowlist but is a dot segment: a decoding receiver would normalise it
        # away and shift every following segment.
        return f"ValueError: {kind} must not be a path segment ({value!r})"
    return None


def validate_charger_id(charger_id: Any) -> Optional[str]:
    """Return an error reason if ``charger_id`` is unsafe to interpolate, else ``None``."""
    return _validate(charger_id, kind="charger_id", allowed=_CHARGER_ID_ALLOWED)


def validate_session_id(session_id: Any) -> Optional[str]:
    """Return an error reason if ``session_id`` is unsafe to interpolate, else ``None``.

    Same contract and same rules as ``validate_charger_id`` -- see ``_SESSION_ID_ALLOWED``
    for what the bundled OpenAPI spec says a session id can actually be.
    """
    return _validate(session_id, kind="session_id", allowed=_SESSION_ID_ALLOWED)
