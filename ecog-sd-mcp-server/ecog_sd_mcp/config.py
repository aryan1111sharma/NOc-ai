"""Configuration for the Ecog Service Desk MCP server, loaded from environment / .env."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Ecog Jira Service Management (customer portal) — cookie auth.
    ecog_base_url: str = "https://ecog.atlassian.net"
    ecog_email: str = ""            # for Playwright auto-login (optional)
    ecog_password: str = ""         # secret; injected. empty => cookie-only mode
    ecog_cookie: str = ""           # optional seed/fallback Cookie header string
    ecog_cookie_file: str = "/data/ecog_cookies.json"   # refreshed-cookie cache
    ecog_login_enabled: bool = True
    ecog_headless: bool = True
    request_timeout: float = 60.0

    # MCP transport
    mcp_transport: str = "stdio"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8104            # noc=8100, cms=8101, jira=8102, docgen=8103, ecog-sd=8104


settings = Settings()
