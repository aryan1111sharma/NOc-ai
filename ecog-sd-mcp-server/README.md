# ecog-sd-mcp-server — Ecog Jira Service Desk (read-only)

Self-hosted FastMCP server exposing **Ecog's Jira Service Management customer API**
(`/rest/servicedeskapi`) as read-only tools, authenticated with a **browser session cookie**.
Used because the account has customer/portal access, not a licensed Jira user seat (so the
platform REST API and API tokens are rejected).

## Tools
- `sd_list_requests(search, status, ownership, limit)` — list/search your tickets.
- `sd_get_request(key)` — one ticket's detail.
- `sd_get_request_comments(key, limit)` — public comments.
- `sd_list_servicedesks()` — the service desks you can access.

Read-only by construction (no write tool).

## Auth
Sends a session cookie; on logout (401/redirect/HTML) it refreshes **once** and retries.
Refresh = a **Playwright password login** (`login.py`) when `ECOG_EMAIL`/`ECOG_PASSWORD`
are set and `ECOG_LOGIN_ENABLED=true`; otherwise it errors asking for a fresh `ECOG_COOKIE`.
Requires **MFA off**. If login hits a CAPTCHA, run once with `ECOG_HEADLESS=false` or inject
a fresh `ECOG_COOKIE` (copy the `cookie:` request header from a logged-in browser).

## Configuration (env / `.env`)
| Var | Default | Notes |
|---|---|---|
| `ECOG_BASE_URL` | `https://ecog.atlassian.net` | |
| `ECOG_EMAIL` / `ECOG_PASSWORD` | — | for auto-login; **secret** |
| `ECOG_COOKIE` | — | optional seed/fallback Cookie header |
| `ECOG_COOKIE_FILE` | `/data/ecog_cookies.json` | refreshed-cookie cache |
| `ECOG_LOGIN_ENABLED` | `true` | false ⇒ cookie-only |
| `ECOG_HEADLESS` | `true` | set false to solve a one-off challenge |
| `MCP_TRANSPORT`/`MCP_HOST`/`MCP_PORT` | `stdio`/`127.0.0.1`/`8103` | keep internal |

## Test
`python -m pytest -v`  (fully offline; Playwright login is integration-verified separately)

> **Data note:** Ecog is a vendor; this reads vendor-confidential data via a customer
> session. Internal/dev use only until the residency decision (D1). Automating a vendor's
> portal carries ToS/relationship risk — see the spec.
