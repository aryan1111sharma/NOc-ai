"""HTTP Basic-auth client for Jira Cloud REST v3.

Jira Cloud authenticates with `email:api_token` over HTTP Basic — no session/cookie
dance. The token is long-lived, so there is no re-login loop (unlike the legacy CMS).
Every request also asks for JSON.
"""

from __future__ import annotations

import base64

import httpx


class JiraAuthClient(httpx.AsyncClient):
    """httpx.AsyncClient pre-loaded with Jira Cloud Basic-auth + JSON headers."""

    def __init__(self, *args, email: str, api_token: str, **kwargs) -> None:
        token = base64.b64encode(f"{email}:{api_token}".encode()).decode()
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("Authorization", f"Basic {token}")
        headers.setdefault("Accept", "application/json")
        kwargs["headers"] = headers
        kwargs.setdefault("follow_redirects", True)
        super().__init__(*args, **kwargs)
