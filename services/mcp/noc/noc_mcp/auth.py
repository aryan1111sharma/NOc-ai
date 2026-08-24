"""Authenticated httpx client for the NOC backend.

The NOC ``POST /api/v1/auth/login`` returns only a short-lived access token (no refresh
token), so this client:

* logs in on demand (lazily, on the first authenticated request),
* attaches the bearer token to every request,
* proactively re-logs-in shortly before the token's ``exp``, and
* reactively re-logs-in once on a ``401`` and retries the request.

Retrying after a 401 is safe regardless of HTTP method: a 401 means the backend's auth
middleware rejected the request before any handler/business logic ran, so no write (ticket
creation, alert resolve, etc.) has been applied yet — the retried request is the first
attempt that actually reaches the handler. This is a narrower guarantee than "safe to retry
any failure" — it does not cover, and this client does not attempt to cover, retrying after
a timeout or connection error, where whether the write already landed is genuinely unknown.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Optional

import httpx

_LOGIN_PATH = "/api/v1/auth/login"
_EXPIRY_SKEW_SECONDS = 60  # re-login this long before the token actually expires


def _decode_jwt_exp(token: str) -> Optional[float]:
    """Best-effort read of the JWT ``exp`` claim without verifying the signature."""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)  # restore base64 padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = payload.get("exp")
        return float(exp) if exp is not None else None
    except Exception:
        return None


class NocAuthClient(httpx.AsyncClient):
    """``httpx.AsyncClient`` that keeps a NOC access token fresh via re-login."""

    def __init__(self, *args, email: str, password: str, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._email = email
        self._password = password
        self._token: Optional[str] = None
        self._exp: Optional[float] = None
        self._lock = asyncio.Lock()

    async def _login(self) -> None:
        # Goes through send() with the login guard, so no auth header / no recursion.
        resp = await super().request(
            "POST",
            _LOGIN_PATH,
            json={"email": self._email, "password": self._password},
        )
        resp.raise_for_status()
        data = resp.json()
        token = data.get("access_token") or data.get("accessToken")
        if not token:
            raise RuntimeError("NOC login succeeded but no access_token was returned")
        self._token = token
        self._exp = _decode_jwt_exp(token)

    def _needs_login(self) -> bool:
        if self._token is None:
            return True
        if self._exp is not None and time.time() >= self._exp - _EXPIRY_SKEW_SECONDS:
            return True
        return False

    async def _ensure_token(self) -> str:
        if self._needs_login():
            async with self._lock:
                if self._needs_login():  # re-check under lock
                    await self._login()
        assert self._token is not None
        return self._token

    async def send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        is_login = request.url.path.endswith(_LOGIN_PATH)
        if not is_login:
            token = await self._ensure_token()
            request.headers["Authorization"] = f"Bearer {token}"

        resp = await super().send(request, **kwargs)

        if resp.status_code == 401 and not is_login:
            # Token rejected (expired/rotated) — re-login once and retry the request.
            await resp.aread()
            await resp.aclose()
            async with self._lock:
                await self._login()
            request.headers["Authorization"] = f"Bearer {self._token}"
            resp = await super().send(request, **kwargs)

        return resp
