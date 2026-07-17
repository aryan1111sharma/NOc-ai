"""Configuration for the ChargeZone (Quench-scoped) Jira MCP server, from environment / .env."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ChargeZone Jira — Atlassian Cloud, REST v3 with HTTP Basic (email + API token).
    cz_jira_base_url: str = "https://team-1604552506221.atlassian.net"
    cz_jira_email: str                   # account that owns the API token
    cz_jira_api_token: str               # injected as a secret; never committed
    project_key: str = "QCH"             # HARD scope — only this project is reachable
    request_timeout: float = 60.0

    # MCP transport: "stdio" or "http"
    mcp_transport: str = "stdio"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8105                  # noc=8100, cms=8101, jira=8102, docgen=8103, ecog-sd=8104


settings = Settings()
