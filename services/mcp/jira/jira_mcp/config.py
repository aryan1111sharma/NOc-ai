"""Configuration for the Ecog Jira MCP server, loaded from environment / .env."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Ecog Jira — Atlassian Cloud, REST v3 with HTTP Basic (email + API token).
    jira_base_url: str = "https://ecog.atlassian.net"
    jira_email: str                      # shared read-only account
    jira_api_token: str                  # injected as a secret; never committed
    request_timeout: float = 60.0

    # MCP transport: "stdio" or "http"
    mcp_transport: str = "stdio"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8102                 # noc=8100, cms=8101, jira=8102


settings = Settings()
