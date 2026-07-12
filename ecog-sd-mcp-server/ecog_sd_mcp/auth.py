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


class CookieAuthClient(httpx.AsyncClient):
    def __init__(self, *args, cookie: str = "", refresh=None, cookie_file=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
