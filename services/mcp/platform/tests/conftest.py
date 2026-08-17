"""Shared fixtures: a small, deterministic fake repository tree + a server built against it.

Tests never point at the real live repository — a fixture tree keeps results deterministic
and lets us assert on exact values (version string, dependency lists, dotfile exclusion)
without depending on this repo's actual, changing content.
"""

from __future__ import annotations

import subprocess

import pytest
from fastmcp import Client

from platform_mcp.config import Settings
from platform_mcp.server import build_server


@pytest.fixture
def fake_repo(tmp_path):
    root = tmp_path
    (root / "VERSION").write_text("9.9.9\n", encoding="utf-8")
    # Must never appear in platform_filesystem's listing.
    (root / ".env").write_text("SECRET=should-never-be-listed\n", encoding="utf-8")

    mcp_dir = root / "services" / "mcp"
    for name, reqs in (("fake-a", "fastmcp>=3.4\nhttpx>=0.28\n"), ("fake-b", "fastmcp>=3.4\n")):
        service_dir = mcp_dir / name
        service_dir.mkdir(parents=True)
        (service_dir / "requirements.txt").write_text(reqs, encoding="utf-8")

    docs_dir = root / "docs"
    docs_dir.mkdir()
    # newline="" avoids platform-specific \n -> \r\n translation so the byte count tests
    # assert against is deterministic across Windows/Linux/macOS.
    (docs_dir / "readme.md").write_text("hello\n", encoding="utf-8", newline="")

    run = lambda *args: subprocess.run(args, cwd=root, check=True, capture_output=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "test@example.com")
    run("git", "config", "user.name", "Test")
    run("git", "add", "-A")
    run("git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "initial commit")

    return root


@pytest.fixture
def settings(fake_repo):
    return Settings(repo_root=str(fake_repo))


@pytest.fixture
def server(settings):
    return build_server(settings=settings)


@pytest.fixture
async def client(server):
    async with Client(server) as c:
        yield c
