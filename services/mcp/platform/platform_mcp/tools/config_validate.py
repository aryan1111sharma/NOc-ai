"""platform_config_validate — cross-checks that the compose/LibreChat configuration
tying the MCP servers together is internally consistent.

Reads three configuration sources that already exist under the mounted repo root —
nothing here is invented, fetched externally, or executed:
  - apps/ai-platform/librechat/docker-compose.yml  (which MCP containers are defined,
    their container_name + MCP_PORT, and the services/mcp/<name> directory each builds)
  - apps/ai-platform/librechat/librechat.yaml       (mcpServers: the URLs LibreChat
    actually connects to, and mcpSettings.allowedAddresses: the SSRF allowlist)
  - services/mcp/*/                                  (which server directories exist on
    disk, and whether each declares its own requirements.txt)

This is exactly the class of drift — a compose service renamed without updating
librechat.yaml's allowedAddresses/mcpServers, or a build path pointing at a directory
that no longer exists — that has caused real breakage in this stack before. Nothing here
is executed or written; it only reads and cross-references files already on disk.
"""

from __future__ import annotations

import os

import yaml

_COMPOSE_PATH = os.path.join("apps", "ai-platform", "librechat", "docker-compose.yml")
_LIBRECHAT_YAML_PATH = os.path.join("apps", "ai-platform", "librechat", "librechat.yaml")
_MCP_DIR = os.path.join("services", "mcp")


def _load_yaml(path: str):
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _env_port(environment) -> int | None:
    """A compose service's `environment` is a list of "KEY=VAL" strings (the form used
    throughout this repo's docker-compose.yml) or, per the compose spec, a mapping."""
    if isinstance(environment, dict):
        val = environment.get("MCP_PORT")
        return int(val) if val is not None else None
    if isinstance(environment, list):
        for item in environment:
            if isinstance(item, str) and item.startswith("MCP_PORT="):
                try:
                    return int(item.split("=", 1)[1])
                except ValueError:
                    return None
    return None


def _build_context(build) -> str | None:
    """`build` is either a bare path string or a mapping with a `context` key."""
    if isinstance(build, str):
        return build
    if isinstance(build, dict):
        return build.get("context")
    return None


def _compose_mcp_services(compose: dict, compose_dir: str, repo_root: str) -> dict:
    """Compose services that build from services/mcp/<name> — the ones this validator
    cross-checks against librechat.yaml. Other services (mongodb, nginx, ...) are skipped;
    they have no librechat.yaml counterpart to compare against."""
    services = {}
    for key, body in (compose.get("services") or {}).items():
        if not isinstance(body, dict):
            continue
        ctx = _build_context(body.get("build"))
        if not ctx:
            continue
        build_abs = os.path.normpath(os.path.join(compose_dir, ctx))
        rel_to_repo = os.path.relpath(build_abs, repo_root).replace("\\", "/")
        if not rel_to_repo.startswith("services/mcp/"):
            continue
        services[key] = {
            "container_name": body.get("container_name") or key,
            "port": _env_port(body.get("environment")),
            "build_path": rel_to_repo,
            "build_path_exists": os.path.isdir(build_abs),
        }
    return services


def _host_port(url) -> str | None:
    if not isinstance(url, str) or "://" not in url:
        return None
    return url.split("://", 1)[1].split("/", 1)[0]


def register(mcp, settings) -> None:
    @mcp.tool
    def platform_config_validate() -> dict:
        """Cross-check that docker-compose.yml, librechat.yaml, and the services/mcp/
        directories on disk agree with each other: every MCP compose service's
        container_name:port must appear in both librechat.yaml's mcpServers and
        mcpSettings.allowedAddresses, every compose build path must exist on disk, and
        every services/mcp/<name> directory should declare a requirements.txt. Returns
        {ok, problems: [{severity, message}], checked}. Read-only — never modifies
        configuration; only reads and cross-references files already on disk."""
        root = settings.repo_root
        problems: list[dict] = []

        compose_path = os.path.join(root, _COMPOSE_PATH)
        librechat_path = os.path.join(root, _LIBRECHAT_YAML_PATH)
        mcp_dir = os.path.join(root, _MCP_DIR)

        compose = _load_yaml(compose_path)
        if compose is None:
            problems.append({"severity": "error",
                             "message": f"compose file not found: {_COMPOSE_PATH}"})
            compose = {}

        librechat_cfg = _load_yaml(librechat_path)
        if librechat_cfg is None:
            problems.append({"severity": "error",
                             "message": f"librechat config not found: {_LIBRECHAT_YAML_PATH}"})
            librechat_cfg = {}

        mcp_dirs_on_disk = sorted(
            name for name in os.listdir(mcp_dir) if os.path.isdir(os.path.join(mcp_dir, name))
        ) if os.path.isdir(mcp_dir) else []

        for name in mcp_dirs_on_disk:
            if not os.path.isfile(os.path.join(mcp_dir, name, "requirements.txt")):
                problems.append({"severity": "warning",
                                 "message": f"services/mcp/{name} has no requirements.txt"})

        compose_services = _compose_mcp_services(compose, os.path.dirname(compose_path), root)
        for key, info in compose_services.items():
            if not info["build_path_exists"]:
                problems.append({
                    "severity": "error",
                    "message": (f"compose service '{key}' builds from "
                                f"'{info['build_path']}', which does not exist"),
                })

        compose_addresses = {
            f"{info['container_name']}:{info['port']}"
            for info in compose_services.values() if info["port"] is not None
        }

        allowed = set((librechat_cfg.get("mcpSettings") or {}).get("allowedAddresses") or [])
        mcp_servers_cfg = librechat_cfg.get("mcpServers") or {}
        librechat_addresses = {
            name: _host_port((body or {}).get("url", "")) for name, body in mcp_servers_cfg.items()
        }

        for key, info in compose_services.items():
            if info["port"] is None:
                problems.append({
                    "severity": "warning",
                    "message": f"compose service '{key}' has no MCP_PORT in its environment",
                })
                continue
            addr = f"{info['container_name']}:{info['port']}"
            if addr not in allowed:
                problems.append({
                    "severity": "error",
                    "message": (f"compose service '{key}' ({addr}) is missing from "
                                "librechat.yaml mcpSettings.allowedAddresses"),
                })
            if addr not in librechat_addresses.values():
                problems.append({
                    "severity": "error",
                    "message": (f"compose service '{key}' ({addr}) has no matching entry "
                                "in librechat.yaml mcpServers"),
                })

        for addr in allowed:
            if addr not in compose_addresses:
                problems.append({
                    "severity": "warning",
                    "message": (f"librechat.yaml mcpSettings.allowedAddresses lists "
                                f"'{addr}', which matches no compose service"),
                })

        for name, addr in librechat_addresses.items():
            if addr and addr not in compose_addresses:
                problems.append({
                    "severity": "warning",
                    "message": (f"librechat.yaml mcpServers.{name} points at '{addr}', "
                                "which matches no compose service"),
                })

        return {
            "ok": not any(p["severity"] == "error" for p in problems),
            "problems": problems,
            "checked": {
                "compose_services_scanned": sorted(compose_services),
                "mcp_dirs_on_disk": mcp_dirs_on_disk,
                "librechat_mcp_servers": sorted(mcp_servers_cfg),
            },
        }
