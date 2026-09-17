"""Configuration for the tool-filtering proxy, loaded from environment / .env."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_prefix="PROXY_"
    )

    host: str = "0.0.0.0"
    port: int = 8108  # noc=8100 ... platform=8107; this is the next free port

    # Route prefix -> real upstream base URL. LibreChat points each provider's `baseURL`
    # at http://tool-filter-proxy:8108/<prefix>, so one proxy serves every provider
    # without an extra hop per provider. Override with a JSON object in PROXY_UPSTREAMS.
    upstreams: dict[str, str] = {
        "glm": "https://api.z.ai/api/coding/paas/v4",
        "kimi": "https://api.kimi.com/coding/v1",
    }

    # Passed to rank(); MAX_K in the filtering core still caps the result.
    filter_top_k: int = 20

    # Generous: providers stream for a long time and connect latency varies. `None` read
    # timeout would risk a hung upstream holding a connection forever, so it is bounded.
    connect_timeout: float = 10.0
    read_timeout: float = 600.0

    # When true, filtering is skipped entirely and every request is forwarded verbatim.
    # The instant kill switch: flip this instead of reverting librechat.yaml.
    passthrough_only: bool = False

    # Off by default: request bodies contain user prompts. Only enable for local debugging.
    log_request_details: bool = False


settings = Settings()
