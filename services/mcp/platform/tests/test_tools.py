"""End-to-end tests: real fastmcp.Client against a server built over the fake_repo fixture."""

from __future__ import annotations

import json


def _payload(result):
    return json.loads(result.content[0].text)


async def test_all_eight_tools_registered(client):
    tools = await client.list_tools()
    names = {t.name for t in tools}
    assert names == {
        "platform_health",
        "platform_project",
        "platform_python",
        "platform_git",
        "platform_filesystem",
        "platform_api",
        "platform_mcp_discovery",
        "platform_config_validate",
    }


async def test_health_reports_mounted_repo(client):
    data = _payload(await client.call_tool("platform_health", {}))
    assert data["status"] == "ok"
    assert data["repo_root_mounted"] is True
    assert data["uptime_seconds"] >= 0


async def test_health_missing_repo_root(tmp_path):
    from platform_mcp.config import Settings
    from platform_mcp.server import build_server
    from fastmcp import Client

    missing = tmp_path / "does-not-exist"
    server = build_server(settings=Settings(repo_root=str(missing)))
    async with Client(server) as c:
        data = _payload(await c.call_tool("platform_health", {}))
    assert data["repo_root_mounted"] is False


async def test_project_reports_version_and_services(client):
    data = _payload(await client.call_tool("platform_project", {}))
    assert data["version"] == "9.9.9"
    assert data["mcp_server_count"] == 2
    assert data["mcp_servers"] == ["fake-a", "fake-b"]
    assert "docs" in data["top_level_directories"]
    # .env must never surface even in a directory listing, let alone here.
    assert ".env" not in data["top_level_directories"]


async def test_python_reports_declared_dependencies(client):
    data = _payload(await client.call_tool("platform_python", {}))
    assert "python_version" in data["runtime"]
    deps = data["declared_dependencies_by_service"]
    assert deps["fake-a"] == ["fastmcp>=3.4", "httpx>=0.28"]
    assert deps["fake-b"] == ["fastmcp>=3.4"]


async def test_git_reports_the_commit_just_made(client):
    data = _payload(await client.call_tool("platform_git", {}))
    assert data["working_tree_clean"] is True
    assert data["commit"]["subject"] == "initial commit"
    assert data["commit"]["author"] == "Test"
    assert data["branch"]  # some branch name, exact default varies by git config


async def test_git_detects_dirty_tree(client, fake_repo):
    (fake_repo / "untracked.txt").write_text("x", encoding="utf-8")
    data = _payload(await client.call_tool("platform_git", {}))
    assert data["working_tree_clean"] is False


async def test_filesystem_lists_top_level_and_excludes_dotfiles(client):
    data = _payload(await client.call_tool("platform_filesystem", {}))
    names = {e["name"] for e in data["entries"]}
    assert "docs" in names
    assert "VERSION" in names
    assert ".env" not in names  # the load-bearing assertion for this tool


async def test_filesystem_reports_file_sizes_not_dir_sizes(client):
    data = _payload(await client.call_tool("platform_filesystem", {"path": "docs"}))
    entry = next(e for e in data["entries"] if e["name"] == "readme.md")
    assert entry["type"] == "file"
    assert entry["size_bytes"] == len("hello\n")


async def test_filesystem_rejects_path_traversal(client):
    data = _payload(await client.call_tool("platform_filesystem", {"path": "../../etc"}))
    assert "error" in data


async def test_filesystem_rejects_non_directory(client):
    data = _payload(await client.call_tool("platform_filesystem", {"path": "VERSION"}))
    assert "error" in data


async def test_api_describes_itself_not_other_servers(client):
    data = _payload(await client.call_tool("platform_api", {}))
    assert data["server_name"] == "Platform Intelligence (read-only)"
    assert data["tool_count"] == 8
    assert "platform_health" in data["tools"]
    # Scope discipline: no cross-server keys like "other_servers" / "discovered_servers".
    assert set(data.keys()) == {"server_name", "transport", "tool_count", "tools"}
