import pytest

from llm_router.catalog import Catalog
from llm_router.store import Registry


@pytest.fixture(autouse=True)
def provider_api_keys(monkeypatch):
    """Stands in for the real .env values these ${VAR} placeholders resolve
    against -- real-looking secrets so a leak (e.g. into an HTTP response
    body) is unmistakable in a failing assertion, not a blank string that
    would pass by accident."""
    monkeypatch.setenv("KIMI_API_KEY", "sk-test-kimi-secret")
    monkeypatch.setenv("GLM_API_KEY", "sk-test-glm-secret")


@pytest.fixture
def registry(tmp_path):
    return Registry(tmp_path / "llm-router-registry.json")


@pytest.fixture
def catalog():
    """A static catalog standing in for librechat.yaml's endpoints.custom,
    modelled on the real Kimi/GLM entries: one credential (endpoint) with
    several models, plus a second credential sharing one of those model
    names -- exactly the two catalog shapes the routing feature has to
    support (one credential/many models, and many credentials/one model)."""
    return Catalog(
        endpoints=[
            {
                "name": "Kimi",
                "apiKey": "${KIMI_API_KEY}",
                "baseURL": "https://kimi.example/coding/v1",
                "models": {"default": ["kimi-for-coding", "k3"]},
            },
            {
                "name": "GLM",
                "apiKey": "${GLM_API_KEY}",
                "baseURL": "https://glm.example/api/coding/paas/v4",
                "models": {"default": ["glm-5.2", "k3"]},
            },
        ]
    )
