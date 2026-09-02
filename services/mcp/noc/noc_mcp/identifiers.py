"""Validation for NOC identifiers that get interpolated into URLs.

Shared by ``control_center`` (HTTP charger proxy) and ``ssh_exec`` (WebSocket PTY), which
both splice a caller-supplied ``charger_id`` into a URL path.

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


def validate_charger_id(charger_id: Any) -> Optional[str]:
    """Return an error reason if ``charger_id`` is unsafe to interpolate, else ``None``.

    Returns a reason rather than raising: both call sites answer with a structured error
    payload instead of propagating exceptions.
    """
    if not isinstance(charger_id, str) or not charger_id.strip():
        return "ValueError: charger_id must be a non-empty string"
    if not _CHARGER_ID_ALLOWED.match(charger_id):
        return f"ValueError: charger_id has an illegal character ({charger_id!r})"
    if charger_id in (".", ".."):
        # Matches the allowlist but is a dot segment: a decoding receiver would normalise it
        # away and shift every following segment.
        return f"ValueError: charger_id must not be a path segment ({charger_id!r})"
    return None
