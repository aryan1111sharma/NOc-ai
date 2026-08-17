"""Shared test setup: dummy CZ Jira creds (so `config.Settings()` imports) + a mock client."""

import os

# Set BEFORE any cz_jira_mcp.config import happens during collection.
os.environ.setdefault("CZ_JIRA_EMAIL", "test@quench.local")
os.environ.setdefault("CZ_JIRA_API_TOKEN", "test-token")

import httpx
import pytest

from cz_jira_mcp.auth import JiraAuthClient


@pytest.fixture
def mock_client():
    """Factory: given an httpx handler, return a JiraAuthClient wired to a MockTransport."""
    def _make(handler):
        return JiraAuthClient(
            base_url="https://cz.test",
            email="e@x.com",
            api_token="tok",
            transport=httpx.MockTransport(handler),
        )
    return _make
