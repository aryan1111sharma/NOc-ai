"""platform_filesystem — shallow, metadata-only structural view of the repository.

Security boundary, deliberate and load-bearing: this tool returns names/types/sizes only,
NEVER file contents, and always excludes dotfiles/dotdirs (.env, .git, .venv, etc.). A
platform-introspection tool that could read arbitrary file contents from a mounted repo
root would be a secret-disclosure primitive (.env files live right next to the code this
mounts) — so that capability does not exist here, by construction, not by convention.
"""

from __future__ import annotations

import os

_SKIP_NAMES = {"node_modules", "__pycache__"}


def _is_hidden(name: str) -> bool:
    return name.startswith(".")


def _list_dir(path: str) -> list[dict]:
    entries: list[dict] = []
    try:
        with os.scandir(path) as it:
            for entry in it:
                if _is_hidden(entry.name) or entry.name in _SKIP_NAMES:
                    continue
                try:
                    is_dir = entry.is_dir()
                    size = None if is_dir else entry.stat().st_size
                except OSError:
                    continue
                entries.append({"name": entry.name, "type": "dir" if is_dir else "file", "size_bytes": size})
    except OSError:
        return []
    return sorted(entries, key=lambda e: (e["type"], e["name"]))


def register(mcp, settings) -> None:
    @mcp.tool
    def platform_filesystem(path: str = "") -> dict:
        """List the immediate contents (name, type, size in bytes) of a directory in the
        repository. Metadata only — this NEVER returns file contents, and dotfiles/dotdirs
        (.env, .git, .venv, etc.) are always excluded, so it cannot be used to discover or
        read secrets. `path` is relative to the repository root; omit it for the top level."""
        root = os.path.abspath(settings.repo_root)
        target = os.path.abspath(os.path.join(root, path or "."))

        # Path-traversal guard: refuse to look outside repo_root (e.g. path="../../etc").
        if os.path.commonpath([root, target]) != root:
            return {"error": "path escapes the repository root"}
        if not os.path.isdir(target):
            return {"error": f"not a directory: {path or '.'}"}

        return {"path": path or ".", "entries": _list_dir(target)}
