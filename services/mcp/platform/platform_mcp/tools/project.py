"""platform_project — high-level structural summary of the repository.

Reports the declared VERSION, the top-level layout, and which MCP servers exist under
services/mcp/. Metadata only, derived from directory listings and the VERSION file — never
reads arbitrary file contents.
"""

from __future__ import annotations

import os


def register(mcp, settings) -> None:
    @mcp.tool
    def platform_project() -> dict:
        """Summarize the repository: declared version (from VERSION), top-level directory
        layout, and the list of MCP servers under services/mcp/. Read-only, metadata only."""
        root = settings.repo_root
        if not os.path.isdir(root):
            return {"error": f"repo root not mounted at {root}"}

        version = None
        version_path = os.path.join(root, "VERSION")
        if os.path.isfile(version_path):
            with open(version_path, encoding="utf-8") as f:
                version = f.read().strip()

        top_level = sorted(
            name
            for name in os.listdir(root)
            if not name.startswith(".") and os.path.isdir(os.path.join(root, name))
        )

        mcp_dir = os.path.join(root, "services", "mcp")
        mcp_servers = (
            sorted(
                name
                for name in os.listdir(mcp_dir)
                if os.path.isdir(os.path.join(mcp_dir, name))
            )
            if os.path.isdir(mcp_dir)
            else []
        )

        return {
            "version": version,
            "top_level_directories": top_level,
            "mcp_server_count": len(mcp_servers),
            "mcp_servers": mcp_servers,
        }
