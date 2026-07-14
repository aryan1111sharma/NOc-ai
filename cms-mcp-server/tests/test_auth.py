"""Tests for CmsAuthClient's session login / re-login (the custom auth logic).

Uses httpx.MockTransport to simulate the legacy CMS — no network required.
"""

import httpx
import pytest

import cms_mcp.auth as auth
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


@pytest.fixture(autouse=True)
def _instant_retries(monkeypatch):
    """Keep the backoff schedule but drop the sleeps so tests run fast."""
    monkeypatch.setattr(auth, "_RETRY_DELAYS", (0, 0))


class FlapCms:
    """CMS stand-in that injects a queue of fault statuses on protected requests.

    ``faults`` is a list of HTTP status codes returned for successive *protected*
    (non-login) requests, simulating a backend flap; once the queue drains, protected
    requests return 200. Login always succeeds. Mirrors the real flap: a 302 redirects
    to ``/`` (looks logged out), a 5xx is a server error.
    """

    def __init__(self, faults):
        self.faults = list(faults)
        self.logins = 0
        self.data_calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(_LOGIN):
            self.logins += 1
            return httpx.Response(
                200, json={"UserID": 74},
                headers={"set-cookie": ".ASPXAUTH=abc; path=/"},
            )
        self.data_calls += 1
        if self.faults:
            code = self.faults.pop(0)
            if code == 302:
                return httpx.Response(302, headers={"location": "/"})
            return httpx.Response(code)
        return httpx.Response(200, json=[{"ok": 1}])

    def client(self) -> CmsAuthClient:
        return CmsAuthClient(
            base_url="http://cms.test", user="harsh", password="secret",
            transport=httpx.MockTransport(self.handler),
        )


@pytest.mark.asyncio
async def test_rides_through_transient_flap():
    # 302 twice (a brief flap) then recovers -> 3 total tries covers 2 leading faults.
    fake = FlapCms(faults=[302, 302])
    async with fake.client() as c:
        r = await c.get("/ChargerDetails/GetNocDetailsForchart")
    assert r.status_code == 200
    assert r.json() == [{"ok": 1}]


@pytest.mark.asyncio
async def test_transient_5xx_recovers():
    fake = FlapCms(faults=[503])
    async with fake.client() as c:
        r = await c.get("/ChargerDetails/GetChargerTypes")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_raises_clear_error_when_flap_persists():
    # Flap outlasts the retry budget -> clear CmsUnavailableError, not a raw redirect error.
    fake = FlapCms(faults=[302, 302, 302, 302, 302])
    with pytest.raises(auth.CmsUnavailableError) as excinfo:
        async with fake.client() as c:
            await c.get("/ChargerDetails/GetNocDetailsForchart")
    msg = str(excinfo.value).lower()
    assert "unavailable" in msg or "retry" in msg
