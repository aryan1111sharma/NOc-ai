"""Tests for NocAuthClient's login / re-login behavior (the only custom auth logic).

Uses an httpx.MockTransport to simulate the NOC backend so no network is required.
"""

import base64
import json
import time

import httpx
import pytest

from noc_mcp.auth import NocAuthClient


def _make_jwt(exp_offset: float = 3600.0) -> str:
    """Build an unsigned JWT with a given exp offset (only the payload matters here)."""

    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    return f"{b64({'alg': 'none'})}.{b64({'exp': time.time() + exp_offset})}.sig"


class FakeNoc:
    """Minimal NOC stand-in: issues tokens on login, 401s on unknown/invalid tokens."""

    def __init__(self, token_exp_offset: float = 3600.0) -> None:
        self.valid: set[str] = set()
        self.login_count = 0
        self.token_exp_offset = token_exp_offset

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/v1/auth/login"):
            self.login_count += 1
            tok = _make_jwt(self.token_exp_offset)
            self.valid.add(tok)
            return httpx.Response(200, json={"access_token": tok, "token_type": "bearer"})
        tok = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if tok and tok in self.valid:
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(401, json={"detail": "token expired"})

    def client(self) -> NocAuthClient:
        return NocAuthClient(
            base_url="http://noc.test",
            email="svc@noc.test",
            password="secret",
            transport=httpx.MockTransport(self.handler),
        )


@pytest.mark.asyncio
async def test_lazy_login_and_attaches_bearer():
    fake = FakeNoc()
    async with fake.client() as c:
        r = await c.get("/api/v1/dashboard/summary")
    assert r.status_code == 200
    assert fake.login_count == 1  # logged in exactly once, lazily


@pytest.mark.asyncio
async def test_reactive_relogin_on_401():
    fake = FakeNoc()
    async with fake.client() as c:
        r1 = await c.get("/api/v1/chargers")
        assert r1.status_code == 200
        # Server rotates/invalidates the token behind our back.
        fake.valid.clear()
        r2 = await c.get("/api/v1/chargers")
    assert r2.status_code == 200      # recovered transparently
    assert fake.login_count == 2      # re-logged in once on the 401


@pytest.mark.asyncio
async def test_proactive_relogin_before_expiry():
    # Tokens already within the 60s skew window -> every call re-logs-in proactively,
    # so a request never even hits a 401.
    fake = FakeNoc(token_exp_offset=10.0)
    async with fake.client() as c:
        await c.get("/api/v1/chargers")
        await c.get("/api/v1/chargers")
    assert fake.login_count == 2  # proactively refreshed on the second call
