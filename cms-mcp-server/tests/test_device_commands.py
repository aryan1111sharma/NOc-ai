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


def test_write_allowlist_membership():
    from cms_mcp.device_commands import WRITE_ALLOWLIST
    assert "reset" in WRITE_ALLOWLIST
    assert "ocpp-client/restart" in WRITE_ALLOWLIST
    assert "outlets/{number}/stop" in WRITE_ALLOWLIST
    # Tier-3 / off-list must NOT be present
    assert "scripts/active" not in WRITE_ALLOWLIST
    assert "outlets/{number}/e-stop" not in WRITE_ALLOWLIST
    assert len(WRITE_ALLOWLIST) == 8


def test_resolve_path_substitutes_and_validates():
    from cms_mcp.device_commands import resolve_path
    assert resolve_path("ocpp-client/restart", None) == "ocpp-client/restart"
    assert resolve_path("outlets/{number}/stop", {"number": 1}) == "outlets/1/stop"
    with __import__("pytest").raises(ValueError) as ei:
        resolve_path("outlets/{number}/stop", {})
    assert "number" in str(ei.value)


def test_log_path_guard():
    from cms_mcp.device_commands import is_safe_log_path
    assert is_safe_log_path(r"E:\ApiResponses\r.txt")
    assert is_safe_log_path(r"E:\CMS_Chargers_State_Logs\x.json")
    assert not is_safe_log_path(r"E:\Windows\System32\config\SAM")
    assert not is_safe_log_path(r"..\..\secret.txt")
    assert not is_safe_log_path(r"E:\ApiResponses\r.exe")


def test_pending_token_single_use_and_expiry():
    from cms_mcp import device_commands as dc
    dc._PENDING.clear()
    token = dc._mint_pending({"charger": "x"})
    assert isinstance(token, str) and len(token) > 20
    got = dc._pop_pending(token, ttl=120)
    assert got["charger"] == "x"
    assert dc._pop_pending(token, ttl=120) is None   # single-use
    # expired
    token2 = dc._mint_pending({"charger": "y"})
    dc._PENDING[token2]["created_at"] -= 999
    assert dc._pop_pending(token2, ttl=120) is None
