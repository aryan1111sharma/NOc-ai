"""Unit tests for platform_mcp_discovery's static source scan.

Builds its own small services/mcp/ tree per test rather than reusing the shared
fake_repo fixture (whose fake-a/fake-b services only have a requirements.txt, no source)
so the decorator-scanning and dynamic-registration-detection logic get real .py content
to scan.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastmcp import Client

from platform_mcp.config import Settings
from platform_mcp.server import build_server


def _payload(result):
    return json.loads(result.content[0].text)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


async def test_discovers_servers_and_decorator_tools(tmp_path: Path):
    mcp_dir = tmp_path / "services" / "mcp"
    _write(mcp_dir / "widget" / "widget_mcp" / "server.py", """
from fastmcp import FastMCP

def build_server():
    mcp = FastMCP(name="widget")

    @mcp.tool
    def widget_get_status() -> dict:
        \"\"\"Read-only.\"\"\"
        return {}

    @mcp.tool
    async def widget_list_things(limit: int = 10) -> list:
        \"\"\"Read-only.\"\"\"
        return []

    return mcp
""")

    server = build_server(settings=Settings(repo_root=str(tmp_path)))
    async with Client(server) as c:
        data = _payload(await c.call_tool("platform_mcp_discovery", {}))

    assert data["server_count"] == 1
    widget = data["servers"]["widget"]
    assert widget["tool_count"] == 2
    assert widget["tools"] == ["widget_get_status", "widget_list_things"]
    assert "dynamic_tools" not in widget


async def test_flags_dynamic_registration_without_guessing_names(tmp_path: Path):
    mcp_dir = tmp_path / "services" / "mcp"
    _write(mcp_dir / "dyn" / "dyn_mcp" / "server.py", """
def build_server(mcp):
    for profile in ["a", "b"]:
        def handler():
            return {}
        mcp.tool(name=f"dyn_search_{profile}", description="d")(handler)
    return mcp
""")

    server = build_server(settings=Settings(repo_root=str(tmp_path)))
    async with Client(server) as c:
        data = _payload(await c.call_tool("platform_mcp_discovery", {}))

    dyn = data["servers"]["dyn"]
    assert dyn["tool_count"] == 0          # no fixed @mcp.tool functions to name
    assert dyn["dynamic_tools"] is True
    assert dyn["dynamic_registration_sites"] == 1


async def test_ignores_tests_directory_and_non_python_files(tmp_path: Path):
    mcp_dir = tmp_path / "services" / "mcp"
    _write(mcp_dir / "widget" / "widget_mcp" / "server.py", """
def register(mcp):
    @mcp.tool
    def widget_real_tool():
        return {}
""")
    _write(mcp_dir / "widget" / "tests" / "test_server.py", """
def register(mcp):
    @mcp.tool
    def widget_should_not_be_counted():
        return {}
""")
    _write(mcp_dir / "widget" / "README.md", "@mcp.tool\ndef widget_from_docs(): pass\n")

    server = build_server(settings=Settings(repo_root=str(tmp_path)))
    async with Client(server) as c:
        data = _payload(await c.call_tool("platform_mcp_discovery", {}))

    assert data["servers"]["widget"]["tools"] == ["widget_real_tool"]


async def test_missing_services_mcp_directory_reports_error(tmp_path: Path):
    server = build_server(settings=Settings(repo_root=str(tmp_path)))
    async with Client(server) as c:
        data = _payload(await c.call_tool("platform_mcp_discovery", {}))
    assert "error" in data
