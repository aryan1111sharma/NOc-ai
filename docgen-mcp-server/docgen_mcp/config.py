"""Configuration for the docgen MCP server, loaded from environment / .env."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # MCP transport: "stdio" (local clients) or "http" (remote / LibreChat).
    mcp_transport: str = "stdio"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8103

    # Browser-facing base URL for generated-file download links. Must be reachable from the
    # user's browser (not the internal container name). Locally this is the published port;
    # in production it should be a path behind the reverse proxy (ties to P0-1/P0-3).
    docgen_public_base_url: str = "http://localhost:8103"

    # Directory where generated files are stored. Backed by a Docker volume so links survive
    # container restarts. Bounded by DOCGEN_MAX_FILES (oldest evicted first).
    docgen_data_dir: str = "/data/files"
    docgen_max_files: int = 200


settings = Settings()  # reads env / .env at import time
