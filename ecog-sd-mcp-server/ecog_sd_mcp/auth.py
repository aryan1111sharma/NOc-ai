"""Cookie auth client for Ecog Service Desk. (Filled in Tasks 2–3.)"""

from __future__ import annotations

import json
import os

import httpx

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def cookies_to_header(cookies: list[dict]) -> str:
    """Turn Playwright-style cookie dicts into a 'k=v; k=v' Cookie header string."""
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies if c.get("name"))


def load_cookie(path: str) -> str:
    """Read a cached Cookie header string; '' if the file is missing/unreadable."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("cookie", "") or ""
    except (OSError, ValueError):
        return ""


def save_cookie(path: str, header: str) -> None:
    """Persist the Cookie header string to the cache file (best effort)."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"cookie": header}, f)
    except OSError:
        pass


import asyncio


class CookieAuthClient(httpx.AsyncClient):
    """httpx client that sends a session cookie and refreshes it once on logout.

    ``refresh`` is an async callable returning a fresh 'k=v; k=v' Cookie header string
    (a Playwright login in production; a fake in tests). On a logged-out response
    (401 / redirect / HTML) it refreshes under a lock and retries once. A single login
    serves concurrent callers.
    """

    def __init__(self, *args, cookie: str = "", refresh=None, cookie_file=None, **kwargs) -> None:
        kwargs.setdefault("follow_redirects", False)   # detect login redirects
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("User-Agent", _UA)
        headers.setdefault("Accept", "application/json")
        kwargs["headers"] = headers
        super().__init__(*args, **kwargs)
        self._cookie = cookie
        self._refresh = refresh
        self._cookie_file = cookie_file
        self._lock = asyncio.Lock()

    @staticmethod
    def _looks_logged_out(resp: httpx.Response) -> bool:
        if resp.status_code == 401 or resp.is_redirect:
            return True
        return "text/html" in resp.headers.get("content-type", "")

    async def _do_refresh(self) -> None:
        if not self._refresh:
            raise RuntimeError(
                "Ecog session expired — provide a fresh ECOG_COOKIE or enable auto-login."
            )
        self._cookie = await self._refresh()
        if self._cookie_file:
            save_cookie(self._cookie_file, self._cookie)

    async def send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        if self._cookie:
            request.headers["Cookie"] = self._cookie
        resp = await super().send(request, **kwargs)

        if self._looks_logged_out(resp):
            await resp.aread()
            await resp.aclose()
            stale = self._cookie
            async with self._lock:
                if self._cookie == stale:          # nobody refreshed while we waited
                    await self._do_refresh()
            request.headers["Cookie"] = self._cookie
            resp = await super().send(request, **kwargs)
        return resp
