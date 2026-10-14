"""Runtime configuration, read from the environment.

Mirrors services/llm-router/llm_router/config.py: a single pydantic-settings
object, constructed once at import time, with explicit defaults so the
service also runs standalone (outside docker-compose) for local development
and tests.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SESSION_")

    host: str = "0.0.0.0"
    port: int = 8112

    # Best-effort turn-dispatch events ingested from llm-router (see
    # session/store.py:EventStore). Same "small JSON file, atomically
    # rewritten" precedent as llm_router.store.Registry -- this service must
    # never become a second database to operate.
    data_file: str = "/data/session-events.json"

    # Ring-buffer cap on ingested events, oldest dropped first. Bounds the
    # JSON file's size/rewrite cost; this store is a short-lived correlation
    # aid for the admin view, never the system of record (LibreChat's own
    # Mongo is -- see mongo_reader.py).
    max_events: int = 2000

    # LibreChat's own, existing database -- read-only from here. Same value
    # LibreChat's own `api` service is given (see
    # apps/ai-platform/librechat/docker-compose.yml MONGO_URI).
    mongo_uri: str = "mongodb://mongodb:27017/LibreChat"
    mongo_db: str = "LibreChat"

    # How close (in seconds) an ingested llm-router dispatch event's
    # recorded_at must be to a Mongo turn's timestamp to be treated as the
    # same turn. There is no shared identifier between the two sources (see
    # services/session/README.md "Correlation"), so this is a deliberate,
    # documented best-effort join -- never used to decide whether a turn
    # exists, only to enrich one that Mongo already reports.
    correlation_window_seconds: float = 30.0

    default_list_limit: int = 50
    max_list_limit: int = 200


settings = Settings()
