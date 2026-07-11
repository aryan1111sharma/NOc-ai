"""HTTP Basic-auth client for Jira Cloud REST v3. (Filled in Task 2.)"""

from __future__ import annotations

import httpx


class JiraAuthClient(httpx.AsyncClient):
    def __init__(self, *args, email: str, api_token: str, **kwargs) -> None:
        super().__init__(*args, **kwargs)
