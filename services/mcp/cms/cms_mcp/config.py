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
    # 90s, not 20s: measured live 2026-07-16, a healthy charger took 44s to answer while
    # the CMS was busy (it managed 6.7s idle the day before). At 20s the poll gave up
    # first and reported a working charger as "timeout" — the misdiagnosis these tools
    # exist to prevent. Sized off the observed 44s with headroom; the cost is that a
    # genuinely dead charger now takes 90s to report.
    device_poll_timeout: float = 90.0       # max wait for an async execution result
    device_poll_interval: float = 2.0       # delay between history polls
    device_pending_ttl_seconds: int = 120   # single-use write-confirm token lifetime
    device_audit_log_file: str | None = None  # audit sink; None -> stderr

    # Fleet fan-out (cms_get_fleet_firmware and later per-command fleet tools).
    fleet_concurrency: int = 8              # max simultaneous device queries
    fleet_online_window_minutes: int = 15   # ping newer than this => charger is online
    fleet_max_targets: int = 100            # cap per call; overflow is reported, not dropped

    # MCP transport: "stdio" or "http"
    mcp_transport: str = "stdio"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8101


settings = Settings()
