"""Unit + integration tests for the CMS device-command tools."""

import os

# Settings requires cms_user/cms_pass; provide them before importing config.
os.environ.setdefault("CMS_USER", "u")
os.environ.setdefault("CMS_PASS", "p")


def test_device_settings_defaults():
    from cms_mcp.config import settings
    assert settings.device_poll_timeout == 20.0
    assert settings.device_poll_interval == 2.0
    assert settings.device_pending_ttl_seconds == 120
    assert settings.device_audit_log_file is None


import httpx
import pytest
from cms_mcp.auth import CmsAuthClient


def _login_ok(request):
    return httpx.Response(200, json={"UserID": 74},
                          headers={"set-cookie": ".ASPXAUTH=abc; path=/"})


@pytest.mark.asyncio
async def test_login_captures_user_id():
    def handler(request):
        if request.url.path.endswith("/Home/GetUsersByEmailAndPassword"):
            return _login_ok(request)
        return httpx.Response(200, json=[])

    async with CmsAuthClient(base_url="http://cms.test", user="u", password="p",
                             transport=httpx.MockTransport(handler)) as c:
        assert c.user_id == 0            # not logged in yet
        await c._ensure_login()
        assert c.user_id == 74
