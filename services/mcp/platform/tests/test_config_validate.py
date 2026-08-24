"""Unit tests for platform_config_validate's compose/librechat.yaml cross-checks.

Builds its own minimal apps/ai-platform/librechat/{docker-compose.yml,librechat.yaml} +
services/mcp/ tree per test (independent of the shared fake_repo fixture, which has
neither file) so each drift scenario can be constructed and asserted on precisely.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastmcp import Client

from platform_mcp.config import Settings
from platform_mcp.server import build_server


def _payload(result):
    return json.loads(result.content[0].text)


_COMPOSE_OK = """
services:
  noc-mcp-server:
    build: ../../../services/mcp/noc
    container_name: noc-mcp-server
    environment:
      - MCP_PORT=8100
      - MCP_TRANSPORT=http
  mongodb:
    image: mongo:7
"""

_LIBRECHAT_OK = """
mcpSettings:
  allowedAddresses:
    - "noc-mcp-server:8100"
mcpServers:
  noc:
    type: streamable-http
    url: "http://noc-mcp-server:8100/mcp"
"""


def _scaffold(root: Path, compose: str, librechat: str, *, with_noc_dir=True, with_reqs=True):
    lc_dir = root / "apps" / "ai-platform" / "librechat"
    lc_dir.mkdir(parents=True)
    (lc_dir / "docker-compose.yml").write_text(compose, encoding="utf-8")
    (lc_dir / "librechat.yaml").write_text(librechat, encoding="utf-8")
    if with_noc_dir:
        noc_dir = root / "services" / "mcp" / "noc"
        noc_dir.mkdir(parents=True)
        if with_reqs:
            (noc_dir / "requirements.txt").write_text("fastmcp>=3.4\n", encoding="utf-8")


async def _validate(root: Path) -> dict:
    server = build_server(settings=Settings(repo_root=str(root)))
    async with Client(server) as c:
        return _payload(await c.call_tool("platform_config_validate", {}))


async def test_consistent_config_reports_ok(tmp_path: Path):
    _scaffold(tmp_path, _COMPOSE_OK, _LIBRECHAT_OK)
    data = await _validate(tmp_path)
    assert data["ok"] is True
    assert data["problems"] == []
    assert data["checked"]["compose_services_scanned"] == ["noc-mcp-server"]


async def test_missing_build_path_is_an_error(tmp_path: Path):
    _scaffold(tmp_path, _COMPOSE_OK, _LIBRECHAT_OK, with_noc_dir=False)
    data = await _validate(tmp_path)
    assert data["ok"] is False
    assert any("does not exist" in p["message"] for p in data["problems"])


async def test_missing_requirements_txt_is_a_warning(tmp_path: Path):
    _scaffold(tmp_path, _COMPOSE_OK, _LIBRECHAT_OK, with_reqs=False)
    data = await _validate(tmp_path)
    problems = [p for p in data["problems"] if "requirements.txt" in p["message"]]
    assert len(problems) == 1 and problems[0]["severity"] == "warning"
    assert data["ok"] is True  # a warning alone must not flip ok to False


async def test_compose_service_missing_from_allowed_addresses_is_an_error(tmp_path: Path):
    librechat_no_allowlist = """
mcpSettings:
  allowedAddresses: []
mcpServers:
  noc:
    type: streamable-http
    url: "http://noc-mcp-server:8100/mcp"
"""
    _scaffold(tmp_path, _COMPOSE_OK, librechat_no_allowlist)
    data = await _validate(tmp_path)
    assert data["ok"] is False
    assert any("allowedAddresses" in p["message"] and p["severity"] == "error"
               for p in data["problems"])


async def test_compose_service_missing_from_mcp_servers_is_an_error(tmp_path: Path):
    librechat_no_server_entry = """
mcpSettings:
  allowedAddresses:
    - "noc-mcp-server:8100"
mcpServers: {}
"""
    _scaffold(tmp_path, _COMPOSE_OK, librechat_no_server_entry)
    data = await _validate(tmp_path)
    assert data["ok"] is False
    assert any("mcpServers" in p["message"] and p["severity"] == "error"
               for p in data["problems"])


async def test_orphaned_allowed_address_is_a_warning(tmp_path: Path):
    librechat_extra_address = """
mcpSettings:
  allowedAddresses:
    - "noc-mcp-server:8100"
    - "ghost-mcp-server:9999"
mcpServers:
  noc:
    type: streamable-http
    url: "http://noc-mcp-server:8100/mcp"
"""
    _scaffold(tmp_path, _COMPOSE_OK, librechat_extra_address)
    data = await _validate(tmp_path)
    assert data["ok"] is True
    assert any("ghost-mcp-server:9999" in p["message"] and p["severity"] == "warning"
               for p in data["problems"])


async def test_missing_compose_file_is_an_error(tmp_path: Path):
    lc_dir = tmp_path / "apps" / "ai-platform" / "librechat"
    lc_dir.mkdir(parents=True)
    (lc_dir / "librechat.yaml").write_text(_LIBRECHAT_OK, encoding="utf-8")
    data = await _validate(tmp_path)
    assert data["ok"] is False
    assert any("compose file not found" in p["message"] for p in data["problems"])


async def test_missing_librechat_yaml_is_an_error(tmp_path: Path):
    lc_dir = tmp_path / "apps" / "ai-platform" / "librechat"
    lc_dir.mkdir(parents=True)
    (lc_dir / "docker-compose.yml").write_text(_COMPOSE_OK, encoding="utf-8")
    data = await _validate(tmp_path)
    assert data["ok"] is False
    assert any("librechat config not found" in p["message"] for p in data["problems"])


async def test_never_writes_anything(tmp_path: Path):
    """Read-only guarantee: running validation must not create or modify any file."""
    _scaffold(tmp_path, _COMPOSE_OK, _LIBRECHAT_OK)
    before = {p: p.stat().st_mtime for p in tmp_path.rglob("*") if p.is_file()}
    await _validate(tmp_path)
    after = {p: p.stat().st_mtime for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after
