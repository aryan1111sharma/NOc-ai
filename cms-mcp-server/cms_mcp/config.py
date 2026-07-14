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

    # Device-command tools (ExecuteApi / ReadJsonLogFile).
    device_poll_timeout: float = 20.0       # max wait for an async execution result
    device_poll_interval: float = 2.0       # delay between history polls
    device_pending_ttl_seconds: int = 120   # single-use write-confirm token lifetime
    device_audit_log_file: str | None = None  # audit sink; None -> stderr

    # MCP transport: "stdio" or "http"
    mcp_transport: str = "stdio"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8101


settings = Settings()
