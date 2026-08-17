"""Configuration for the platform MCP server, loaded from environment / .env."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # MCP transport: "stdio" (local clients) or "http" (remote / LibreChat).
    mcp_transport: str = "stdio"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8107

    # Read-only view of the repository this server introspects. In Docker this is a bind
    # mount (see apps/ai-platform/librechat/docker-compose.yml's platform-mcp-server
    # service); override via REPO_ROOT for local runs against a real checkout.
    repo_root: str = "/workspace"


settings = Settings()  # reads env / .env at import time
