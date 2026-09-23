"""Kimi (Moonshot AI) provider-reported quota/usage adapter.

Fetches account-level quota from Kimi's own `/usages` endpoint and normalizes
it for the Usage/Status page, alongside (never mixed with) the locally
observed per-(endpoint, model) counters in llm_router.store.UsageStats.

Credential resolution reuses the EXACT mechanism llm_router.proxy already
uses to call Kimi for chat: llm_router.catalog.Catalog.resolve(). This module
keeps no credential store of its own -- it resolves one (endpoint, model)
pair already registered for "Kimi" in the catalog (the usage API is
account/key-level, not model-level, so which registered model is used to
resolve the credential is irrelevant) and holds the returned api_key
in-memory for exactly one outbound call. It is never logged, written to the
registry, or included in any response this module returns.

Response shape observed from a real call to
`GET {baseURL}/usages` (baseURL == the same Kimi `coding/v1` endpoint chat
completions already use), Authorization: Bearer <key>:

    {
      "usage": {"limit": "100", "used": "47", "remaining": "53",
                "resetTime": "2026-09-26T09:36:28.070325Z"},
      "limits": [{"window": {"duration": 300, "timeUnit": "TIME_UNIT_MINUTE"},
                  "detail": {"limit": "100", "used": "19", "remaining": "81",
                             "resetTime": "2026-09-23T10:36:28.070325Z"}}],
      "booster_wallet": {"balance": {...}, "monthlyChargeLimit": {...},
                          "monthlyUsed": {...}, ...},
      "usages": {"limit_5h": {"used_ratio": 0, "reset_time": "..."},
                 "limit_7d": {"used_ratio": 0, "reset_time": "..."}}
    }

`usage` + `limits[]` are the authoritative quota source. `usages.*.used_ratio`
is a SECOND, independently-computed set of numbers that was observed
disagreeing with `usage`/`limits[].detail` in the same payload during manual
verification (both windows read 0 there while `usage`/`limits[].detail`
showed real non-zero usage) -- never read here. Only `usages.*`'s window
names and reset_time are used, to label which window is which; never its
used_ratio.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone

import httpx

from .catalog import Catalog

KIMI_ENDPOINT_NAME = "Kimi"
_USAGE_PATH = "/usages"
_DEFAULT_CACHE_TTL_SECONDS = 60.0
_RESET_TIME_MATCH_TOLERANCE_SECONDS = 5.0

#: Window identifiers Kimi's own response names its two known windows under
#: (the `usages` block) -- used only to derive a human label by matching
#: reset_time, never the disputed used_ratio value alongside them.
_KNOWN_WINDOW_LABELS = {"limit_5h": "5h", "limit_7d": "7d"}


@dataclass(frozen=True)
class QuotaWindow:
    name: str
    used: float | None
    limit: float | None
    remaining: float | None
    used_percent: float | None
    remaining_percent: float | None
    reset_at: str | None

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "used": self.used,
            "limit": self.limit,
            "remaining": self.remaining,
            "used_percent": self.used_percent,
            "remaining_percent": self.remaining_percent,
            "reset_at": self.reset_at,
        }


@dataclass(frozen=True)
class BoosterWallet:
    """The Kimi subscription's prepaid monthly credit balance -- a distinct,
    dollar-denominated quota dimension, never mixed with the request-count
    windows above. Built only from fields with an explicit, unambiguous unit
    (`priceInCents` + `currency`); the wallet ledger's own `balance.amount` /
    `amountLeft` fields carry no documented unit/scale and are deliberately
    not surfaced, to avoid fabricating a conversion."""

    currency: str | None
    monthly_limit: float | None
    monthly_used: float | None
    monthly_remaining: float | None
    period_start: str | None
    period_end: str | None

    def to_json(self) -> dict:
        return {
            "currency": self.currency,
            "monthly_limit": self.monthly_limit,
            "monthly_used": self.monthly_used,
            "monthly_remaining": self.monthly_remaining,
            "period_start": self.period_start,
            "period_end": self.period_end,
        }


@dataclass(frozen=True)
class ProviderQuota:
    provider: str
    account_scope: str
    windows: tuple[QuotaWindow, ...]
    booster_wallet: BoosterWallet | None
    fetched_at: str
    status: str
    source: str
    error: str | None = None

    def to_json(self) -> dict:
        return {
            "provider": self.provider,
            "account_scope": self.account_scope,
            "windows": [w.to_json() for w in self.windows],
            "booster_wallet": self.booster_wallet.to_json() if self.booster_wallet else None,
            "fetched_at": self.fetched_at,
            "status": self.status,
            "source": self.source,
            "error": self.error,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_number(value) -> float | None:
    """Numeric strings ("100"), real numbers, both accepted -- anything else
    (None, "", a non-numeric string) becomes None rather than raising or
    silently defaulting to 0, so a parse failure stays visible as a missing
    value, never a fabricated one."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_reset_time(value) -> str | None:
    """Kimi returns RFC3339 with a trailing 'Z' and either second or
    microsecond precision. Normalized to an ISO 8601 string with an explicit
    UTC offset so the frontend never has to guess a timezone. Unparseable
    input becomes None, never a fabricated time."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _reset_times_match(a: str, b: str) -> bool:
    try:
        ta = datetime.fromisoformat(a)
        tb = datetime.fromisoformat(b)
    except ValueError:
        return False
    return abs((ta - tb).total_seconds()) <= _RESET_TIME_MATCH_TOLERANCE_SECONDS


def _duration_label(window: dict) -> str:
    """A generic, computed-not-guessed label for a `limits[]` entry that
    doesn't match a known `usages` window by reset_time -- e.g. an account
    with a differently-shaped set of windows than the 5h/7d pair observed
    during manual verification."""
    duration = window.get("duration")
    unit = window.get("timeUnit", "")
    if not isinstance(duration, (int, float)) or not duration:
        return "window"
    if unit == "TIME_UNIT_MINUTE":
        if duration % 1440 == 0:
            return f"{int(duration // 1440)}d"
        if duration % 60 == 0:
            return f"{int(duration // 60)}h"
        return f"{int(duration)}m"
    if unit == "TIME_UNIT_HOUR":
        return f"{int(duration)}h"
    if unit == "TIME_UNIT_DAY":
        return f"{int(duration)}d"
    return f"{int(duration)}{unit}" if unit else f"{int(duration)}"


def _window_label(reset_at: str | None, known_reset_times: dict[str, str]) -> str | None:
    if not reset_at:
        return None
    for key, reset_time in known_reset_times.items():
        if _reset_times_match(reset_time, reset_at):
            return _KNOWN_WINDOW_LABELS.get(key, key)
    return None


def _build_window(name: str, detail: dict, reset_at: str | None) -> QuotaWindow:
    used = _to_number(detail.get("used"))
    limit = _to_number(detail.get("limit"))
    remaining = _to_number(detail.get("remaining"))
    used_percent = round(used / limit * 100, 1) if used is not None and limit else None
    remaining_percent = (
        round(remaining / limit * 100, 1) if remaining is not None and limit else None
    )
    return QuotaWindow(
        name=name,
        used=used,
        limit=limit,
        remaining=remaining,
        used_percent=used_percent,
        remaining_percent=remaining_percent,
        reset_at=reset_at,
    )


def _parse_windows(payload: dict) -> list[QuotaWindow]:
    known_reset_times = {
        key: (payload.get("usages") or {}).get(key, {}).get("reset_time")
        for key in _KNOWN_WINDOW_LABELS
    }
    known_reset_times = {k: v for k, v in known_reset_times.items() if v}

    windows: list[QuotaWindow] = []

    for entry in payload.get("limits") or []:
        if not isinstance(entry, dict):
            continue
        detail = entry.get("detail") or {}
        reset_at = _parse_reset_time(detail.get("resetTime"))
        name = _window_label(reset_at, known_reset_times) or _duration_label(
            entry.get("window") or {}
        )
        windows.append(_build_window(name, detail, reset_at))

    usage = payload.get("usage")
    if isinstance(usage, dict) and usage:
        reset_at = _parse_reset_time(usage.get("resetTime"))
        name = _window_label(reset_at, known_reset_times) or "account"
        # Don't show the same window twice if `usage` and a `limits[]` entry
        # both resolved to the same label.
        if not any(w.name == name for w in windows):
            windows.append(_build_window(name, usage, reset_at))

    return windows


def _cents_to_amount(value) -> float | None:
    number = _to_number(value)
    return round(number / 100, 2) if number is not None else None


def _parse_booster_wallet(payload: dict) -> BoosterWallet | None:
    wallet = payload.get("booster_wallet")
    if not isinstance(wallet, dict):
        return None
    limit_block = wallet.get("monthlyChargeLimit") or {}
    used_block = wallet.get("monthlyUsed") or {}
    balance_block = wallet.get("balance") or {}
    monthly_limit = _cents_to_amount(limit_block.get("priceInCents"))
    monthly_used = _cents_to_amount(used_block.get("priceInCents"))
    monthly_remaining = (
        round(monthly_limit - monthly_used, 2)
        if monthly_limit is not None and monthly_used is not None
        else None
    )
    return BoosterWallet(
        currency=limit_block.get("currency"),
        monthly_limit=monthly_limit,
        monthly_used=monthly_used,
        monthly_remaining=monthly_remaining,
        period_start=_parse_reset_time(balance_block.get("periodStart")),
        period_end=_parse_reset_time(balance_block.get("periodEnd")),
    )


def _unavailable(provider: str, *, error: str, source: str = "provider") -> ProviderQuota:
    return ProviderQuota(
        provider=provider,
        account_scope="account",
        windows=(),
        booster_wallet=None,
        fetched_at=_now_iso(),
        status="unavailable",
        source=source,
        error=error,
    )


class KimiQuotaAdapter:
    """Fetches and normalizes Kimi's account-level quota, with a short-lived
    cache so opening/refreshing the Usage page repeatedly doesn't hammer the
    provider. Never raises: every failure mode (no registered credential,
    network error, non-200, unparseable body) becomes a `status="unavailable"`
    ProviderQuota so the Usage page can degrade to "Quota unavailable" without
    losing the locally observed usage data next to it."""

    def __init__(
        self,
        catalog: Catalog,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
        cache_ttl_seconds: float = _DEFAULT_CACHE_TTL_SECONDS,
    ):
        self._catalog = catalog
        self._client = http_client
        self._timeout = timeout
        self._cache_ttl = cache_ttl_seconds
        self._cached: ProviderQuota | None = None
        self._cached_at: float = 0.0

    async def get_quota(self, *, force_refresh: bool = False) -> ProviderQuota:
        now = time.monotonic()
        if (
            not force_refresh
            and self._cached is not None
            and (now - self._cached_at) < self._cache_ttl
        ):
            return replace(self._cached, source="cache")
        quota = await self._fetch()
        self._cached = quota
        self._cached_at = now
        return quota

    async def _fetch(self) -> ProviderQuota:
        target = await self._first_kimi_target()
        if target is None:
            return _unavailable(
                KIMI_ENDPOINT_NAME, error="no registered Kimi credential/model in the catalog"
            )

        resolved = await self._catalog.resolve(KIMI_ENDPOINT_NAME, target.model)
        if resolved is None or not resolved.api_key:
            return _unavailable(KIMI_ENDPOINT_NAME, error="Kimi credential not resolvable")

        url = resolved.base_url.rstrip("/") + _USAGE_PATH
        headers = {"authorization": f"Bearer {resolved.api_key}", "accept": "application/json"}
        try:
            response = await self._request(url, headers)
        except httpx.HTTPError as exc:
            # Only the exception class, never str(exc) -- httpx can echo
            # request details (including headers) into its message.
            return _unavailable(KIMI_ENDPOINT_NAME, error=f"request failed: {type(exc).__name__}")

        if response.status_code != 200:
            return _unavailable(
                KIMI_ENDPOINT_NAME, error=f"upstream returned HTTP {response.status_code}"
            )

        try:
            payload = response.json()
        except ValueError:
            return _unavailable(KIMI_ENDPOINT_NAME, error="upstream response was not valid JSON")

        return ProviderQuota(
            provider=KIMI_ENDPOINT_NAME,
            account_scope="account",
            windows=tuple(_parse_windows(payload)),
            booster_wallet=_parse_booster_wallet(payload),
            fetched_at=_now_iso(),
            status="ok",
            source="provider",
        )

    async def _first_kimi_target(self):
        for t in await self._catalog.list_targets():
            if t.endpoint == KIMI_ENDPOINT_NAME:
                return t
        return None

    async def _request(self, url: str, headers: dict) -> httpx.Response:
        if self._client is not None:
            return await self._client.get(url, headers=headers, timeout=self._timeout)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await client.get(url, headers=headers)
