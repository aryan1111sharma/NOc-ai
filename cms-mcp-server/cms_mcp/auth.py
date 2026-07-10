"""Session-cookie authenticated client for the legacy CMS (ASP.NET MVC).

The old NOC has no token API — it authenticates via a form login that sets Forms-Auth
(`.ASPXAUTH`) + session (`ASP.NET_SessionId`) cookies. This client:

* logs in by POSTing form-encoded credentials to `/Home/GetUsersByEmailAndPassword`,
* keeps the auth cookies in its cookie jar and sends them on every request,
* re-logs-in once when the session has expired (a protected action redirects to login or
  returns the HTML login page instead of JSON), then retries.

All data calls are GETs (read-only, idempotent), so retrying after re-login is safe.
"""

from __future__ import annotations

import asyncio

import httpx

_LOGIN_PATH = "/Home/GetUsersByEmailAndPassword"


class CmsAuthClient(httpx.AsyncClient):
    """httpx.AsyncClient that keeps a legacy-CMS session alive via form re-login."""

    def __init__(self, *args, user: str, password: str, **kwargs) -> None:
        # follow_redirects must stay False so we can detect the login redirect.
        kwargs.setdefault("follow_redirects", False)
        super().__init__(*args, **kwargs)
        self._user = user
        self._password = password
        self._logged_in = False
        self._lock = asyncio.Lock()

    async def _login(self) -> None:
        self.cookies.clear()
        resp = await super().request(
            "POST",
            _LOGIN_PATH,
            data={
                "strUserName": self._user,
                "strPassword": self._password,
                "RemembarMeCheckboxIsChecked": "true",
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        ok = False
        try:
            ok = int(resp.json().get("UserID", 0)) >= 1
        except Exception:
            ok = False
        if not ok:
            raise RuntimeError(f"CMS login failed (status {resp.status_code})")
        self._logged_in = True

    async def _ensure_login(self) -> None:
        if not self._logged_in:
            async with self._lock:
                if not self._logged_in:
                    await self._login()

    @staticmethod
    def _looks_logged_out(resp: httpx.Response) -> bool:
        # Session expired -> MVC redirects to the login page, or serves HTML instead of JSON.
        if resp.is_redirect:
            return True
        ctype = resp.headers.get("content-type", "")
        return "text/html" in ctype

    async def send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        is_login = request.url.path.endswith(_LOGIN_PATH)
        if not is_login:
            await self._ensure_login()
            # httpx bakes the Cookie header at build time (before login ran), so apply the
            # session cookies onto this request explicitly.
            self.cookies.set_cookie_header(request)

        resp = await super().send(request, **kwargs)

        if not is_login and self._looks_logged_out(resp):
            await resp.aread()
            await resp.aclose()
            async with self._lock:
                self._logged_in = False
                await self._login()
            self.cookies.set_cookie_header(request)
            resp = await super().send(request, **kwargs)

        return resp
