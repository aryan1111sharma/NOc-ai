"""Shared test setup: dummy Jira creds (so `config.Settings()` imports) + a mock client."""

import os

# Set BEFORE any jira_mcp.config import happens during collection.
os.environ.setdefault("JIRA_EMAIL", "test@ecog.local")
os.environ.setdefault("JIRA_API_TOKEN", "test-token")

import httpx
import pytest

from jira_mcp.auth import JiraAuthClient


@pytest.fixture
def mock_client():
    """Factory: given an httpx handler, return a JiraAuthClient wired to a MockTransport."""
    def _make(handler):
        return JiraAuthClient(
            base_url="https://ecog.test",
            email="e@x.com",
            api_token="tok",
            transport=httpx.MockTransport(handler),
        )
    return _make
