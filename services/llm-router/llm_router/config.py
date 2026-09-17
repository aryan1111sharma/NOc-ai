"""Runtime configuration, read from the environment.

Mirrors services/tool-filter-proxy/tool_filter_proxy/config.py: a single
pydantic-settings object, constructed once at import time, with an explicit
default so the service also runs standalone (outside docker-compose) for
local development and tests.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LLM_ROUTER_")

    host: str = "0.0.0.0"
    port: int = 8110

    # Where the Connections/Routes registry is persisted. Same shape as
    # knowledge/kb-registry.json: a JSON file on a bind-mounted/named volume,
    # so an admin edit survives a container restart with no database.
    data_file: str = "/data/llm-router-registry.json"

    # Per-attempt upstream timeout. A provider that never answers must not
    # hang the whole route forever -- it has to fail so the next connection
    # in priority order gets a turn.
    request_timeout_seconds: float = 30.0

    # Timeout applied when a Combo is "tested" from the admin UI. Kept short
    # and separate from request_timeout_seconds: an admin clicking "Test" is
    # waiting synchronously and wants a fast answer, not the same budget a
    # real chat turn gets.
    test_timeout_seconds: float = 10.0

    # The SAME librechat.yaml the LibreChat api container reads (mounted
    # read-only a second time here) -- see llm_router.catalog.Catalog. This
    # service never gets its own copy of provider credentials/models.
    librechat_yaml_path: str = "/librechat-config/librechat.yaml"

    # The custom-endpoint `name` this router is wired into librechat.yaml
    # under. Excluded from the Catalog so a Combo can never target itself.
    self_endpoint_name: str = "NOC Router"


settings = Settings()
