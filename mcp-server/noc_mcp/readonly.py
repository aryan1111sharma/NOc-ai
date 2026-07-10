"""Read-only enforcement for the charger-proxy tools.

The NOC MCP server exposes only GET operations as tools, but the GET charger-proxy tools carry
a ``method`` query parameter that the NOC backend uses to choose which HTTP method it forwards
to a live charger. Without this guard a *GET* proxy tool could be called with ``method=POST``
and write to a charger. ``_blocked_method`` decides when to reject; ``ReadOnlyProxyMiddleware``
(Task 2) enforces it on every tool call.
"""

from __future__ import annotations

from typing import Any, Optional

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware


def _blocked_method(tool_name: str, arguments: dict[str, Any]) -> Optional[str]:
    """Return the offending method string if this call must be blocked, else ``None``.

    Blocks only proxy tools (name contains ``"proxy"``) whose ``method`` argument is present and
    not GET (case-insensitive, whitespace-tolerant). Absent or ``None`` ``method`` is allowed
    (the NOC backend defaults to the request's GET method).
    """
    if "proxy" not in tool_name.lower():
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
    """Reject non-GET ``method`` on charger-proxy tools before the call reaches NOC."""

    async def on_call_tool(self, context, call_next):
        message = context.message
        offending = _blocked_method(message.name, message.arguments or {})
        if offending is not None:
            raise ToolError(
                "Read-only server: only GET is permitted on charger proxy tools "
                f"(got method={offending!r})"
            )
        return await call_next(context)
