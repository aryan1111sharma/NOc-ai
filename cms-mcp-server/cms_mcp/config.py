"""Configuration for the legacy-CMS MCP server, loaded from environment / .env."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Legacy CMS (old NOC) — ASP.NET MVC app with session/cookie auth.
    cms_base_url: str = "https://quenchcms.com"
    cms_user: str
    cms_pass: str
    request_timeout: float = 90.0  # legacy actions return large payloads

    # MCP transport: "stdio" or "http"
    mcp_transport: str = "stdio"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8101


settings = Settings()
