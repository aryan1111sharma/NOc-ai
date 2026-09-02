"""Read-only enforcement for the charger-proxy tools.

The NOC MCP server exposes only GET operations as tools, but the GET charger-proxy tools carry
a ``method`` query parameter that the NOC backend uses to choose which HTTP method it forwards
to a live charger. Without this guard a *GET* proxy tool could be called with ``method=POST``
and write to a charger. ``_blocked_method`` decides when to reject; ``ReadOnlyProxyMiddleware``
enforces it on every tool call.
"""

from __future__ import annotations

from typing import Any, Optional

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware

# Name fragments that mark a tool as charger-reaching. "proxy" covers the OpenAPI-generated
# charger-proxy tools (their names are derived from the route path); "charger" covers the
# curated control-center and SSH tools.
_CHARGER_NAME_HINTS = ("proxy", "charger")

# Argument names that identify a specific charger. A tool that accepts one can reach a charger
# by definition, whatever it is called -- this is what couples the check to capability rather
# than to naming convention.
_CHARGER_ARG_NAMES = frozenset({"charger_id", "chargerid", "charger_ids", "charger"})


def _reaches_charger(tool_name: str, arguments: dict[str, Any]) -> bool:
    """True if this call can reach a live charger, and so must be held to GET-only.

    Deliberately NOT a list of tool names. The original check was ``"proxy" in tool_name``,
    which is a name-based allowlist with no coupling to capability: all 13 curated
    ``charger_get_*`` tools reach the very same NOC charger-proxy route, none has "proxy" in
    its name, and so none was inspected. Any 14th such tool would have been exempt too.

    Coverage is now the union of two signals, either of which is sufficient:

    * the tool name carries a charger-related fragment (``proxy`` / ``charger``), and
    * the call carries a charger-identifying argument (``charger_id`` and friends).

    The argument signal is the load-bearing one: a tool cannot reach a specific charger without
    being told which charger, so a newly added tool is covered automatically, with no registry
    to keep in sync. The name signal remains for generated proxy tools whose charger id may be
    templated into the route rather than passed under a name we recognise.

    Tools that are neither named for chargers nor given one (fleet dashboards, summaries, KB
    lookups) cannot actuate hardware and are left alone, so a stray ``method`` argument on them
    is not this control's business.
    """
    name = tool_name.lower()
    if any(hint in name for hint in _CHARGER_NAME_HINTS):
        return True
    return any(key.lower() in _CHARGER_ARG_NAMES for key in (arguments or {}))


def _blocked_method(tool_name: str, arguments: dict[str, Any]) -> Optional[str]:
    """Return the offending method string if this call must be blocked, else ``None``.

    Blocks charger-reaching tools (see ``_reaches_charger``) whose ``method`` argument is present
    and not GET (case-insensitive, whitespace-tolerant). Absent or ``None`` ``method`` is allowed
    (the NOC backend defaults to the request's GET method).
    """
    if not _reaches_charger(tool_name, arguments):
        return None
    if not arguments or "method" not in arguments:
        return None
    method = arguments.get("method")
    if method is None:
        return None
    if isinstance(method, str) and method.strip().upper() == "GET":
        return None
    return str(method)


class ReadOnlyProxyMiddleware(Middleware):
    """Reject non-GET ``method`` on any tool that can reach a live charger."""

    async def on_call_tool(self, context, call_next):
        message = context.message
        offending = _blocked_method(message.name, message.arguments or {})
        if offending is not None:
            raise ToolError(
                "Read-only server: only GET is permitted on charger proxy tools "
                f"(got method={offending!r})"
            )
        return await call_next(context)
