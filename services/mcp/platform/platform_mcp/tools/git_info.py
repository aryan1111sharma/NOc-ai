"""platform_git — the repository's current git state (branch, latest commit, cleanliness).

Shells out to the real `git` CLI against the read-only repo mount. Read-only: only ever
runs inspection subcommands (rev-parse, log, status --porcelain), never anything that could
mutate the repository.
"""

from __future__ import annotations

import subprocess


def _run(repo_root: str, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", repo_root, *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return result.stdout.strip()
    except Exception:
        return None


def register(mcp, settings) -> None:
    @mcp.tool
    def platform_git() -> dict:
        """Report the repository's git state: current branch, latest commit (short SHA,
        author, date, subject), and whether the working tree is clean. Read-only — never
        writes, commits, or pushes."""
        root = settings.repo_root
        branch = _run(root, "rev-parse", "--abbrev-ref", "HEAD")
        if branch is None:
            return {"error": "not a git repository, or git is unavailable, at the mounted repo root"}

        return {
            "branch": branch,
            "commit": {
                "sha": _run(root, "rev-parse", "--short", "HEAD"),
                "author": _run(root, "log", "-1", "--format=%an"),
                "date": _run(root, "log", "-1", "--format=%ad", "--date=iso-strict"),
                "subject": _run(root, "log", "-1", "--format=%s"),
            },
            "working_tree_clean": _run(root, "status", "--porcelain") == "",
        }
