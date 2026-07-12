"""Cookie auth client for Ecog Service Desk. (Filled in Tasks 2–3.)"""

from __future__ import annotations

import httpx

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


class CookieAuthClient(httpx.AsyncClient):
    def __init__(self, *args, cookie: str = "", refresh=None, cookie_file=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
