"""Shared test fixtures for ecog_sd_mcp."""

import httpx
import pytest

from ecog_sd_mcp.auth import CookieAuthClient


@pytest.fixture
def make_client():
    """Factory: build a CookieAuthClient over a MockTransport handler."""
    def _make(handler, **kw):
        return CookieAuthClient(
            base_url="https://ecog.test",
            transport=httpx.MockTransport(handler),
            **kw,
        )
    return _make
