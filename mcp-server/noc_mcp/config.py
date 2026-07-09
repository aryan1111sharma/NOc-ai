"""Configuration for the NOC MCP server, loaded from environment / .env."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # NOC backend
    noc_base_url: str = "https://www.manthanquench.online"
    noc_email: str
    noc_password: str
    request_timeout: float = 30.0

    # MCP transport: "stdio" (local clients) or "http" (remote / LibreChat via mcpo etc.)
    mcp_transport: str = "stdio"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8100


settings = Settings()  # reads env / .env at import time
