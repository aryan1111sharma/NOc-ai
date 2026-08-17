"""Session-cookie authenticated client for the legacy CMS (ASP.NET MVC).

The old NOC has no token API — it authenticates via a form login that sets Forms-Auth
(`.ASPXAUTH`) + session (`ASP.NET_SessionId`) cookies. This client:

* logs in by POSTing form-encoded credentials to `/Home/GetUsersByEmailAndPassword`,
* keeps the auth cookies in its cookie jar and sends them on every request,
* re-logs-in once when the session has expired (a protected action redirects to login or
  returns the HTML login page instead of JSON), then retries.

Read/idempotent calls are retried on a transient flap; non-idempotent writes (ExecuteApi)
opt out via an X-CMS-No-Retry header so a flap never double-fires them.
"""

from __future__ import annotations

import asyncio

import httpx

_LOGIN_PATH = "/Home/GetUsersByEmailAndPassword"

# Backoff (seconds) before each retry of a transient failure. Its length is the number of
# extra attempts after the first, so N delays => N+1 total tries. The legacy CMS backend is
# known to flap briefly (302-to-login or 5xx); these short retries ride through that without
# making a genuine outage feel slow.
_RETRY_DELAYS = (0.5, 1.5)


class CmsUnavailableError(RuntimeError):
    """Raised when a CMS request keeps failing transiently after re-login and retries.

    Signals a (likely temporary) backend problem — not a code, credential, or permission
    fault — so callers/agents get an accurate, actionable message instead of a raw redirect
    error that reads like an auth failure.
    """

    def __init__(self, request: httpx.Request, response: httpx.Response) -> None:
        self.request = request
        self.response = response
        super().__init__(
            f"CMS temporarily unavailable: '{request.url.path}' still returned "
            f"{response.status_code} after re-login and retries. The legacy CMS backend "
            f"appears to be flapping; retry shortly."
        )


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
        self.user_id = 0

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
            uid = int(resp.json().get("UserID", 0))
            ok = uid >= 1
            if ok:
                self.user_id = uid
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

    async def _relogin(self) -> None:
        async with self._lock:
            self._logged_in = False
            await self._login()

    @staticmethod
    def _looks_logged_out(resp: httpx.Response) -> bool:
        # Session expired -> MVC redirects to the login page, or serves HTML instead of JSON.
        if resp.is_redirect:
            return True
        ctype = resp.headers.get("content-type", "")
        return "text/html" in ctype

    @classmethod
    def _is_transient(cls, resp: httpx.Response) -> bool:
        # A brief backend flap looks like a login redirect / HTML, or a 5xx server error.
        return cls._looks_logged_out(resp) or resp.status_code >= 500

    async def send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        if request.url.path.endswith(_LOGIN_PATH):
            return await super().send(request, **kwargs)

        await self._ensure_login()
        # httpx bakes the Cookie header at build time (before login ran), so apply the
        # session cookies onto this request explicitly.
        self.cookies.set_cookie_header(request)

        # Non-idempotent (write) requests carry X-CMS-No-Retry. Retrying a write after a
        # transient flap could double-fire a command the backend already executed, so we
        # send it exactly once. Strip the marker first so it is never sent upstream.
        no_retry = "x-cms-no-retry" in request.headers
        if no_retry:
            del request.headers["x-cms-no-retry"]

        resp = await super().send(request, **kwargs)

        if no_retry:
            if self._is_transient(resp):
                raise CmsUnavailableError(request, resp)
            return resp

        # Transient failure on an idempotent (read) call: re-establish the session and
        # retry, with backoff, up to the retry budget. Re-login is the correct recovery
        # for both an expired session and a backend flap; it is guarded so a login that
        # itself fails mid-flap doesn't abort the loop. These calls are read-only/
        # idempotent, so retrying is safe.
        for delay in _RETRY_DELAYS:
            if not self._is_transient(resp):
                return resp
            await resp.aread()
            await resp.aclose()
            if delay:
                await asyncio.sleep(delay)
            try:
                await self._relogin()
            except Exception:
                pass  # backend may be mid-flap; let the next attempt try again
            self.cookies.set_cookie_header(request)
            resp = await super().send(request, **kwargs)

        if self._is_transient(resp):
            raise CmsUnavailableError(request, resp)
        return resp
