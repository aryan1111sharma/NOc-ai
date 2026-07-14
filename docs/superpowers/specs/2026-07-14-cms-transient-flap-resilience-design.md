# CMS transient-flap resilience — design

**Date:** 2026-07-14
**Component:** `cms-mcp-server` (`cms_mcp/auth.py`)
**Status:** Approved

## Problem

During a live LibreChat session the `cms_get_noc_summary` tool failed with:

```
Redirect response '302 Found' for url
'https://quenchcms.com/ChargerDetails/GetNocDetailsForchart'
Redirect location: '/'
```

Root-cause investigation (browser-driven, against live quenchcms.com) established:

- The endpoint is **up and returns 200** with valid data (14 rows) right now.
- It works as a **plain GET**, with or without the `X-Requested-With` AJAX header.
- It works **cold** — immediately after the AJAX login, without first loading `/Home/Dashboard`.
- The MCP bot account (`CMS_USER=harsh`, UserID 74) is the **same account** that succeeds
  through the identical flow.

Every controllable variable was held identical to the bot's flow and it succeeded, so the
302 was a **transient flap** of the quenchcms.com backend (consistent with its known 503/
flapping history) — not a defect in the code, credentials, permissions, or request shape.

Two real weaknesses in the client made a transient upstream blip worse than it needed to be:

1. **Single-shot retry.** `CmsAuthClient.send()` retries exactly once, and only by
   re-logging-in. When the cause is backend instability (not an expired session), the one
   immediate retry lands in the same flap window and still 302s.
2. **Misleading error.** The final 302 surfaces via `resp.raise_for_status()` as a raw
   httpx "302 Found redirect" error, which the agent misread as a permanent auth/permission
   failure and reported as such to the user.

## Goals

- Ride through brief backend flaps automatically for the read-only CMS tools.
- When a flap persists, surface a **clear, accurate** "temporarily unavailable — retry
  shortly" error instead of a raw redirect error.
- No behavior change for genuine session-expiry (must still re-login and recover) or for
  genuine non-transient errors (e.g. 404).

## Non-goals

- No change to any tool in `server.py`.
- No configurability (backoff/attempts are hardcoded constants; can be lifted to settings
  later if a need appears).
- No change to the login mechanism itself.

## Design

All changes in `cms_mcp/auth.py` (~20 lines) plus tests in `tests/test_auth.py`.

### Retry loop in `send()`

Replace the current single re-login block with a bounded retry loop:

- Attempt the request. While the response is **transient**, retry up to **2 extra attempts**
  (3 tries total) with backoff **0.5s then 1.5s**.
- **Transient** = `_looks_logged_out(resp)` (redirect or HTML body) **OR** `status_code >= 500`.
- Before each retry: drain the previous response, `sleep(backoff)`, re-establish the session
  via a **guarded** re-login (a flap can drop server-side session; re-login is the correct
  recovery for both expiry and flap), re-apply cookies, resend.
- Re-login is wrapped in `try/except` so a login that itself fails mid-flap does not abort
  the loop — the next attempt retries.
- The `is_login` request stays excluded from the loop (no recursion).
- Applies to all data calls (GET and the DataTables POST reads), since `send()` is the shared
  hook and every CMS call is read-only/idempotent.

Backoff schedule lives in a module constant `_RETRY_DELAYS = (0.5, 1.5)`.

### Terminal error

After the budget is exhausted, if the response is still transient, raise a new
`CmsUnavailableError(RuntimeError)` carrying the request path and final status, e.g.:

> CMS temporarily unavailable: '/ChargerDetails/GetNocDetailsForchart' still returned 302
> after re-login and retries. The legacy CMS backend (quenchcms.com) appears to be flapping;
> retry shortly.

Non-transient responses are returned unchanged, so `server.py`'s existing
`resp.raise_for_status()` continues to handle genuine 4xx as before.

## Testing

Extend `tests/test_auth.py`, reusing the `httpx.MockTransport` `FakeCms` pattern; monkeypatch
`_RETRY_DELAYS` to `(0, 0)` to keep tests fast.

- `test_rides_through_transient_flap` — endpoint returns 302 for the first N calls then 200;
  the call returns data with no error.
- `test_transient_5xx_recovers` — 503 then 200 recovers.
- `test_raises_clear_error_when_flap_persists` — always-302 raises `CmsUnavailableError`
  (not a raw `HTTPStatusError`); message mentions unavailability / retry.
- Existing `test_lazy_login_then_success` and `test_relogin_on_session_expiry` still pass
  (session-expiry recovery unchanged, now with a ≤0.5s delay).

## Verification

- `pytest` green (all existing + 3 new tests).
- End-to-end: confirm `cms_get_noc_summary` returns data via the live LibreChat NOC Engineer
  agent.
