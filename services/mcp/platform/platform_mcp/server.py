"""Build the Platform MCP server — read-only introspection tools for this repository.

This is the start of the Platform Intelligence layer: tools that help the platform
understand itself (engineering health, repository structure, git state) rather than any
external backend, the way the other MCP servers in this repo do.

Extensibility contract: every capability lives in its own module under `tools/`, exposing
`register(mcp, settings)`. Adding a new capability later (Docker, CI/CD, logs, metrics,
security, dependencies, ...) means writing one new module and adding it to
`_TOOL_MODULES` below — no existing module changes.
"""

from __future__ import annotations

from fastmcp import FastMCP

from .config import settings as _default_settings
from .tools import (
    api_info, config_validate, filesystem, git_info, health, mcp_discovery, project,
    python_env,
)

_TOOL_MODULES = (
    health, project, python_env, git_info, filesystem, api_info, mcp_discovery, config_validate,
)


def build_server(settings=None) -> FastMCP:
    """``settings`` is an injection point: tests pass a ``Settings(repo_root=...)`` pointed
    at a fixture directory instead of the real container mount; real wiring uses the
    module-level singleton loaded from the environment."""
    settings = settings or _default_settings
    mcp = FastMCP(name="Platform Intelligence (read-only)")
    for module in _TOOL_MODULES:
        module.register(mcp, settings)
    return mcp
