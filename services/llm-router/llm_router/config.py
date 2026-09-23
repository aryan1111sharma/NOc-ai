"""Runtime configuration, read from the environment.

Mirrors services/tool-filter-proxy/tool_filter_proxy/config.py: a single
pydantic-settings object, constructed once at import time, with an explicit
default so the service also runs standalone (outside docker-compose) for
local development and tests.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

_VALID_COST_TYPES = frozenset({"FREE", "PAID"})


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

    # LibreChat's own api container, reached over the internal Docker
    # network -- see llm_router.catalog.Catalog. This service never parses
    # librechat.yaml or holds a provider credential itself; it asks
    # LibreChat's own backend, which already resolves endpoints.custom
    # (base file + any admin-panel-authored overrides) for direct model
    # execution, for the exact same answer.
    librechat_api_url: str = "http://api:3080"

    # Shared secret proving a request to LibreChat's internal LLM-routing
    # catalog/resolve routes came from this service, not a browser -- those
    # routes sit outside LibreChat's normal requireJwtAuth gate (this caller
    # has no browser session) so this is the only thing standing between
    # them and the Docker network boundary. Never a provider API key.
    internal_secret: str = ""

    # The custom-endpoint `name` this router is wired into librechat.yaml
    # under. Excluded from the Catalog so a Combo can never target itself.
    self_endpoint_name: str = "NOC Router"

    # Admin-declared FREE/PAID label per Catalog endpoint name, e.g.
    # '{"GLM": "PAID", "Kimi": "PAID"}'. This is the smallest mechanism that
    # satisfies the requirement without touching librechat.yaml or
    # LibreChat's own endpoint schema (neither has a cost-type field, and
    # nothing here should infer one from per-model pricing): a single env
    # var, parsed by pydantic-settings as JSON since the field type is
    # dict[str, str]. An endpoint with no entry -- e.g. one an admin just
    # added through LibreChat's own Admin Panel -- reads as "UNKNOWN" via
    # cost_type_for() below, never a guess.
    provider_cost_types: dict[str, str] = {}

    # Base URL of the optional NOC Session Intelligence service (see
    # services/session/). Empty (the default) disables telemetry emission
    # entirely -- session_client.py checks this before doing anything, so an
    # existing deployment that never sets this env var behaves exactly as it
    # did before this field existed. See session_client.py's module
    # docstring for the "observe, never disrupt" guarantee this implies.
    session_service_url: str = ""

    # Upper bound on how long dispatch() may be blocked waiting for the
    # telemetry POST to even be attempted. Deliberately tiny: this is a
    # fire-and-forget emit, not a dependency -- see session_client.py.
    session_service_timeout_seconds: float = 2.0

    def cost_type_for(self, endpoint: str) -> str:
        value = self.provider_cost_types.get(endpoint)
        return value if value in _VALID_COST_TYPES else "UNKNOWN"


settings = Settings()
