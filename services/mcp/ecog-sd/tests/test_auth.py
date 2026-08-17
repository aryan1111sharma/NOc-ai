import asyncio

import httpx
import pytest

from ecog_sd_mcp.auth import CookieAuthClient


def _handler_factory(state):
    """Return 401 until the request carries the refreshed cookie, then 200."""
    def handler(request: httpx.Request) -> httpx.Response:
        state["seen_cookies"].append(request.headers.get("cookie"))
        if request.headers.get("cookie") == "fresh=1":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(401, json={"err": "nope"})
    return handler


@pytest.mark.asyncio
async def test_refreshes_once_then_succeeds():
    state = {"seen_cookies": [], "logins": 0}
    async def refresh():
        state["logins"] += 1
        return "fresh=1"
    c = CookieAuthClient(base_url="https://ecog.test", cookie="stale=1", refresh=refresh,
                         transport=httpx.MockTransport(_handler_factory(state)))
    async with c:
        r = await c.get("/rest/servicedeskapi/request")
    assert r.status_code == 200
    assert state["logins"] == 1
    assert state["seen_cookies"] == ["stale=1", "fresh=1"]


@pytest.mark.asyncio
async def test_no_refresh_callable_raises():
    def handler(request): return httpx.Response(401, json={})
    c = CookieAuthClient(base_url="https://ecog.test", cookie="stale=1", refresh=None,
                         transport=httpx.MockTransport(handler))
    async with c:
        with pytest.raises(RuntimeError, match="session expired"):
            await c.get("/rest/servicedeskapi/request")


@pytest.mark.asyncio
async def test_concurrent_calls_refresh_once():
    state = {"seen_cookies": [], "logins": 0}
    async def refresh():
        state["logins"] += 1
        await asyncio.sleep(0)
        return "fresh=1"
    c = CookieAuthClient(base_url="https://ecog.test", cookie="stale=1", refresh=refresh,
                         transport=httpx.MockTransport(_handler_factory(state)))
    async with c:
        await asyncio.gather(*[c.get("/rest/servicedeskapi/request") for _ in range(4)])
    assert state["logins"] == 1
