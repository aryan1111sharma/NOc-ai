"""platform_python — this server's own Python runtime, plus declared dependencies across
every MCP service's requirements.txt.

Reports what's *declared*, not what's actually installed in each service's own container —
a lightweight way to spot version drift (e.g. two services pinning different fastmcp
versions) without needing to reach into every container.
"""

from __future__ import annotations

import os
import sys


def _declared_requirements(mcp_dir: str) -> dict[str, list[str]]:
    per_service: dict[str, list[str]] = {}
    if not os.path.isdir(mcp_dir):
        return per_service
    for name in sorted(os.listdir(mcp_dir)):
        req_path = os.path.join(mcp_dir, name, "requirements.txt")
        if not os.path.isfile(req_path):
            continue
        with open(req_path, encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]
        per_service[name] = lines
    return per_service


def register(mcp, settings) -> None:
    @mcp.tool
    def platform_python() -> dict:
        """Report this server's own Python version, and the dependencies each MCP service
        declares in its requirements.txt (declared, not verified installed) — a quick way
        to compare dependency versions across services."""
        mcp_dir = os.path.join(settings.repo_root, "services", "mcp")
        return {
            "runtime": {
                "python_version": sys.version.split()[0],
                "platform": sys.platform,
            },
            "declared_dependencies_by_service": _declared_requirements(mcp_dir),
        }
