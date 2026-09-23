"""Focused tests for llm_router.kimi_quota: parsing of a real Kimi /usages
shape, numeric/timestamp edge cases, the deliberate disregard of
usages.*.used_ratio, and safe degradation on failure. See kimi_quota.py's
module docstring for the response shape these are built from."""

from __future__ import annotations

import httpx
import pytest

from llm_router.kimi_quota import KimiQuotaAdapter

REAL_SHAPE_PAYLOAD = {
    "usage": {
        "limit": "100",
        "used": "47",
        "remaining": "53",
        "resetTime": "2026-09-26T09:36:28.070325Z",
    },
    "limits": [
        {
            "window": {"duration": 300, "timeUnit": "TIME_UNIT_MINUTE"},
            "detail": {
                "limit": "100",
                "used": "19",
                "remaining": "81",
                "resetTime": "2026-09-23T10:36:28.070325Z",
            },
        }
    ],
    "booster_wallet": {
        "currency": "USD",
        "monthlyChargeLimit": {"priceInCents": "2000", "currency": "USD"},
        "monthlyUsed": {"priceInCents": "150"},
        "balance": {
            "amount": "999999",
            "amountLeft": "888888",
            "periodStart": "2026-09-01T00:00:00Z",
            "periodEnd": "2026-10-01T00:00:00Z",
        },
    },
    "usages": {
        "limit_5h": {"used_ratio": 0, "reset_time": "2026-09-23T10:36:28.070325Z"},
        "limit_7d": {"used_ratio": 0, "reset_time": "2026-09-26T09:36:28.070325Z"},
    },
}


def make_adapter(payload=None, *, status_code=200, exc=None, cache_ttl=60.0):
    def handler(request: httpx.Request) -> httpx.Response:
        if exc is not None:
            raise exc
        return httpx.Response(status_code, json=payload if payload is not None else {})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


@pytest.mark.asyncio
async def test_parses_5h_and_7d_windows_from_usage_and_limits(catalog):
    client = make_adapter(REAL_SHAPE_PAYLOAD)
    adapter = KimiQuotaAdapter(catalog, http_client=client)

    quota = await adapter.get_quota()

    assert quota.status == "ok"
    by_name = {w.name: w for w in quota.windows}
    assert set(by_name) == {"5h", "7d"}

    five_h = by_name["5h"]
    assert five_h.used == 19.0
    assert five_h.limit == 100.0
    assert five_h.remaining == 81.0
    assert five_h.used_percent == 19.0

    seven_d = by_name["7d"]
    assert seven_d.used == 47.0
    assert seven_d.limit == 100.0
    assert seven_d.remaining == 53.0


@pytest.mark.asyncio
async def test_ignores_usages_used_ratio_even_though_it_disagrees(catalog):
    # used_ratio is 0 for both windows above while usage/limits[].detail show
    # real non-zero usage -- assert we never surface a 0/"0%" value from it.
    client = make_adapter(REAL_SHAPE_PAYLOAD)
    adapter = KimiQuotaAdapter(catalog, http_client=client)

    quota = await adapter.get_quota()

    for w in quota.windows:
        assert w.used != 0.0
        assert w.used_percent != 0.0


@pytest.mark.asyncio
async def test_reset_time_parsed_to_iso_with_utc_offset(catalog):
    client = make_adapter(REAL_SHAPE_PAYLOAD)
    adapter = KimiQuotaAdapter(catalog, http_client=client)

    quota = await adapter.get_quota()

    for w in quota.windows:
        assert w.reset_at is not None
        assert w.reset_at.endswith("+00:00")


@pytest.mark.asyncio
async def test_booster_wallet_parsed_from_cents_never_from_undocumented_balance(catalog):
    client = make_adapter(REAL_SHAPE_PAYLOAD)
    adapter = KimiQuotaAdapter(catalog, http_client=client)

    quota = await adapter.get_quota()

    wallet = quota.booster_wallet
    assert wallet is not None
    assert wallet.currency == "USD"
    assert wallet.monthly_limit == 20.0
    assert wallet.monthly_used == 1.5
    assert wallet.monthly_remaining == 18.5


@pytest.mark.asyncio
async def test_numeric_strings_and_missing_fields_never_crash_or_fabricate(catalog):
    payload = {
        "usage": {"limit": "not-a-number", "used": None, "remaining": "10"},
        "limits": [],
    }
    client = make_adapter(payload)
    adapter = KimiQuotaAdapter(catalog, http_client=client)

    quota = await adapter.get_quota()

    assert quota.status == "ok"
    assert len(quota.windows) == 1
    w = quota.windows[0]
    assert w.limit is None
    assert w.used is None
    assert w.remaining == 10.0
    assert w.used_percent is None


@pytest.mark.asyncio
async def test_upstream_error_degrades_to_unavailable_without_raising(catalog):
    client = make_adapter(status_code=401, payload={"error": "invalid_authentication_error"})
    adapter = KimiQuotaAdapter(catalog, http_client=client)

    quota = await adapter.get_quota()

    assert quota.status == "unavailable"
    assert quota.windows == ()
    assert quota.error is not None


@pytest.mark.asyncio
async def test_network_failure_degrades_to_unavailable_and_never_leaks_key(catalog):
    client = make_adapter(exc=httpx.ConnectTimeout("boom"))
    adapter = KimiQuotaAdapter(catalog, http_client=client)

    quota = await adapter.get_quota()

    assert quota.status == "unavailable"
    assert "sk-test-kimi-secret" not in (quota.error or "")


@pytest.mark.asyncio
async def test_result_never_contains_the_resolved_api_key(catalog):
    client = make_adapter(REAL_SHAPE_PAYLOAD)
    adapter = KimiQuotaAdapter(catalog, http_client=client)

    quota = await adapter.get_quota()

    assert "sk-test-kimi-secret" not in str(quota.to_json())


@pytest.mark.asyncio
async def test_cached_result_reused_until_force_refresh(catalog):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=REAL_SHAPE_PAYLOAD)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = KimiQuotaAdapter(catalog, http_client=client, cache_ttl_seconds=60.0)

    await adapter.get_quota()
    await adapter.get_quota()
    assert calls["n"] == 1

    await adapter.get_quota(force_refresh=True)
    assert calls["n"] == 2
