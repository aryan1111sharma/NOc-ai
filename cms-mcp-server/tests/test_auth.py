"""Tests for CmsAuthClient's session login / re-login (the custom auth logic).

Uses httpx.MockTransport to simulate the legacy CMS — no network required.
"""

import httpx
import pytest

from cms_mcp.auth import CmsAuthClient

_LOGIN = "/Home/GetUsersByEmailAndPassword"


class FakeCms:
    """Legacy-CMS stand-in: form login sets a cookie; protected paths 302 when logged out."""

    def __init__(self) -> None:
        self.valid = False
        self.logins = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(_LOGIN):
            self.logins += 1
            self.valid = True
            return httpx.Response(
                200, json={"UserID": 74, "UserName": "Harsh"},
                headers={"set-cookie": ".ASPXAUTH=abc; path=/"},
            )
        cookie = request.headers.get("cookie", "")
        if self.valid and ".ASPXAUTH=abc" in cookie:
            return httpx.Response(200, json=[{"ok": 1}])
        return httpx.Response(302, headers={"location": "/"})  # logged out -> login redirect

    def client(self) -> CmsAuthClient:
        return CmsAuthClient(
            base_url="http://cms.test", user="harsh", password="secret",
            transport=httpx.MockTransport(self.handler),
        )


@pytest.mark.asyncio
async def test_lazy_login_then_success():
    fake = FakeCms()
    async with fake.client() as c:
        r = await c.get("/ChargerDetails/GetNocDetailsForchart")
    assert r.status_code == 200
    assert fake.logins == 1  # logged in lazily, once


@pytest.mark.asyncio
async def test_relogin_on_session_expiry():
    fake = FakeCms()
    async with fake.client() as c:
        assert (await c.get("/ChargerDetails/GetChargerTypes")).status_code == 200
        fake.valid = False  # server drops the session
        r = await c.get("/ChargerDetails/GetChargerTypes")
    assert r.status_code == 200      # recovered via re-login + retry
    assert fake.logins == 2
