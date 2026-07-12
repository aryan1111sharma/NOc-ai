# Ecog Service Desk MCP — Design (session-cookie + Playwright auto-reauth)

**Date:** 2026-07-12
**Backlog epic:** `JIRA` (JR-*) in `docs/PRODUCTION-READINESS.md` — this is the **Ecog fallback path** after the token approach proved impossible.
**Status:** Approved approach (validated against live Ecog data); pending spec review → plan → build.

## Why this exists (the pivot)

`ccjain@quenchchargers.com` has only **Jira Service Management customer/portal access** on `ecog.atlassian.net`, **not** a licensed Jira user seat. Consequences, all verified live:

- API-token Basic auth → **401** on every authenticated endpoint (`/rest/api/3/*`, `/rest/servicedeskapi/*`). So the token-based `jira-mcp-server` cannot read Ecog. Ecog declined to grant a licensed seat.
- A **browser session cookie** → **200**. Proven: `servicedeskapi/servicedesk` returns 2 desks; `servicedeskapi/request?requestOwnership=OWNED_REQUESTS` returns real tickets (e.g. `SASW-2911 | Invalid Undervoltage due to AC Meter`).

So we authenticate the way the portal does — with a **session cookie** — and read the customer-facing **Service Desk API** (structured JSON, no HTML scraping). Login is a native **password** with **MFA off**, so the cookie can be refreshed by an automated browser login.

> **Caveats accepted by the user (record):** (1) Scraping/automating a **vendor's** system is a ToS + relationship risk. (2) Data is vendor-confidential → **internal/dev use only** until **D1**. (3) Auto-login runs headless from a container IP → **bot-detection/CAPTCHA risk**; a manually-injected cookie is always supported as a fallback. (4) Cookies/credentials are live secrets — never committed.

## Architecture

New top-level `ecog-sd-mcp-server/`, mirroring **`cms-mcp-server/`** (the existing session-cookie pattern):

```
ecog-sd-mcp-server/
  ecog_sd_mcp/
    __init__.py
    config.py       # pydantic Settings from env/.env
    auth.py         # CookieAuthClient(httpx.AsyncClient) + cookie providers
    login.py        # Playwright password login → fresh cookies (Phase 2)
    server.py       # build_server() -> FastMCP with @mcp.tool read-only funcs
    __main__.py     # entrypoint
  tests/            # pytest (mocked httpx + mocked login), per P3-5
  README.md  Dockerfile  requirements.txt
```

Port **8104** (noc=8100, cms=8101, jira=8102, docgen=8103, **ecog-sd=8104**).

### Config (`config.py`)

| Field | Default | Notes |
|---|---|---|
| `ecog_base_url` | `https://ecog.atlassian.net` | |
| `ecog_email` | *(required for auto-login)* | Atlassian account email. |
| `ecog_password` | *(required for auto-login)* | **secret, injected** (P0-6). |
| `ecog_cookie` | `""` | Optional seed/fallback cookie string (from a real browser). |
| `ecog_cookie_file` | `/data/ecog_cookies.json` | Where a refreshed cookie is cached between runs. |
| `ecog_login_enabled` | `true` | If false → cookie-only mode (no Playwright). |
| `ecog_headless` | `true` | Allow headed mode to dodge bot-detection when needed. |
| `request_timeout` | `60.0` | |
| `mcp_transport`/`mcp_host`/`mcp_port` | `stdio`/`127.0.0.1`/`8104` | Internal only (P0-3). |

### Auth (`auth.py` + `login.py`)

`CookieAuthClient(httpx.AsyncClient)` holds the current cookie and sends it as the `Cookie` header (+ a browser-like `User-Agent`, which the live probe required).

**Cookie lifecycle (single source of truth):**
1. Load cookie from cache file → else from `ecog_cookie` seed → else empty.
2. Each request sends the current cookie.
3. On a response that means "logged out" (`401`, or an HTML/login redirect instead of JSON), **refresh once and retry** — mirroring `cms_mcp`'s re-login pattern:
   - If `ecog_login_enabled` and credentials present → run **`login.py`** (Playwright) to get fresh cookies, persist to the cache file, retry.
   - Else → raise a clear `RuntimeError("Ecog session expired — provide a fresh ECOG_COOKIE or enable auto-login")`.
4. An `asyncio.Lock` serialises refresh so concurrent tool calls trigger only one login.

**`login.py` — Playwright password login (Phase 2):**
- Launch Chromium (`headless=ecog_headless`), navigate to `https://ecog.atlassian.net/`.
- Atlassian id flow (native password): fill `input#username` → click `#login-submit` → fill `input#password` → click `#login-submit`. *(Selectors are current-as-of-2026-07; verify at build — the login page is Atlassian-controlled and may change.)*
- Wait for redirect back to `ecog.atlassian.net`; export `context.cookies("https://ecog.atlassian.net")`; serialise to the cache file as a `Cookie` header string.
- If a CAPTCHA/verification screen is detected → raise a clear error instructing the operator to run once in headed mode / re-capture a cookie manually. **No CAPTCHA-solving.**

### Tools (`server.py`, read-only, `servicedeskapi`)

`FastMCP(name="Ecog Service Desk (read-only)")`, each GET-only, trimmed to LLM-friendly fields:

| Tool | Endpoint | Returns |
|---|---|---|
| `sd_list_requests(search=None, status="ALL", ownership="OWNED", limit=50)` | `/rest/servicedeskapi/request` (`searchTerm`, `requestStatus`∈OPEN/CLOSED/ALL, `requestOwnership`∈OWNED/PARTICIPATED/ALL) | per request: `key, summary, status, created, reporter, serviceDesk` |
| `sd_get_request(key)` | `/rest/servicedeskapi/request/{key}` | detail: the above + request-type, all field values |
| `sd_get_request_comments(key, limit=50)` | `/rest/servicedeskapi/request/{key}/comment?public=true` | per comment: `author, created, body` |
| `sd_list_servicedesks()` | `/rest/servicedeskapi/servicedesk` | per desk: `id, projectKey, projectName` |

Service Desk request fields come as `requestFieldValues` (`fieldId`,`label`,`value`) — a helper extracts `summary`/`description` from those; `currentStatus.status` gives status. No ADF in the customer API for the common fields (values are plain/HTML), so a light HTML-strip helper handles description/comment bodies.

### Read-only guarantee

Only GET tools; no POST/PUT/DELETE (no create-request, no comment, no transition). A test asserts no write tool is registered.

## Docker

Base image **`mcr.microsoft.com/playwright/python:v1.48.0-jammy`** (Chromium + system deps preinstalled) rather than `python:3.12-slim`, because Phase 2 needs a browser. `requirements.txt`: `fastmcp>=3.4`, `httpx>=0.28`, `pydantic-settings>=2.0`, `playwright>=1.48`. Cache-file volume mounted so a refreshed cookie survives restarts. This image is large (~1.5 GB) — noted; it only affects this one service.

## Testing (P3-5)

pytest mirroring `cms-mcp-server/tests`, all offline:
- **Auth/refresh:** with a `MockTransport` returning 401 once then 200, and `login.py` monkeypatched to return a fake cookie, assert one refresh + retry + success; assert only one login under concurrent calls.
- **Cookie-only mode:** `ecog_login_enabled=false` + expired cookie → clear RuntimeError, no Playwright import needed.
- **Tools:** mocked `servicedeskapi` JSON → assert field trimming (`requestFieldValues`→summary, `currentStatus`→status), HTML-strip on bodies, ownership/status/search params forwarded.
- **Read-only:** built server registers only the 4 `sd_*` tools.
- `login.py` Playwright flow is **not** unit-tested against live Atlassian (flaky/external); it's covered by a smoke check at wiring time.

## Wiring

Add `ecog-sd-mcp-server` service to `chat/docker-compose.yml` (env: `ECOG_EMAIL/PASSWORD/COOKIE`, cache volume), register `ecog_sd` in `chat/librechat.yaml` (`mcpServers` + `allowedAddresses`, port 8104), expose behind the **`noc-engineer`** agent only (JR-4).

## Non-goals

- Any write/create/transition of Ecog requests.
- CAPTCHA solving; MFA handling (MFA is off).
- The platform Jira API / `jira-mcp-server` (kept as-is; usable for Adorpower if ever wanted).
- Per-user identity — single shared `ccjain` (later a dedicated account); consistent with D2.

## Open items to confirm at build

- Verify Atlassian login selectors (`#username`/`#password`/`#login-submit`) against the live page.
- Confirm which `requestOwnership` value surfaces the team's tickets (`OWNED` vs `ALL`/`ORGANIZATION`) — live probe showed OWNED returns data.
- Confirm the exact minimal cookie set Playwright must export (replay all `ecog.atlassian.net` cookies).
