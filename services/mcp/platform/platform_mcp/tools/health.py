"""platform_health — basic liveness check for the Platform MCP server itself.

Deliberately shallow: confirms the server is responsive and that its repository mount is
actually present. Every other tool in this package depends on that mount, so a misconfigured
volume shows up here first rather than as a confusing empty result from a deeper tool.
"""

from __future__ import annotations

import os
import time

_START_TIME = time.time()


def register(mcp, settings) -> None:
    @mcp.tool
    def platform_health() -> dict:
        """Report whether the Platform MCP server is up, how long it has been running, and
        whether its read-only repository mount is present. Start here before the other
        platform_* tools — if repo_root_mounted is false, every other tool will fail or
        return empty because the repository isn't visible to this container."""
        return {
            "status": "ok",
            "uptime_seconds": round(time.time() - _START_TIME, 1),
            "repo_root": settings.repo_root,
            "repo_root_mounted": os.path.isdir(settings.repo_root),
        }
