import pytest

from session.config import Settings
from session.store import EventStore


@pytest.fixture
def event_store(tmp_path) -> EventStore:
    return EventStore(tmp_path / "session-events.json", max_events=100)


@pytest.fixture
def settings() -> Settings:
    return Settings(correlation_window_seconds=30.0, default_list_limit=50, max_list_limit=200)
