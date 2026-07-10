# AC-1 — Admin Panel + custom roles/groups

**Status:** approved design · **Date:** 2026-07-11 · **Backlog item:** AC-1
**Scope:** `chat/` deployment (compose + env generator + RUNBOOK) and a one-time GUI role setup.

## Problem / goal

The access-control epic needs LibreChat roles/groups to exist. Custom roles landed in LibreChat
**v0.8.5** (we run **v0.8.7**), managed through the **Admin Panel** — a separate service that
ships in LibreChat's official compose but is absent from our custom `chat/docker-compose.yml`.

**Goal (AC-1 acceptance):** the Admin Panel is running, the `noc-l1` / `noc-engineer` /
`noc-legacy` roles (and matching groups) exist, and a test user can be assigned a role.

## Design

### Part A — stand up the Admin Panel (implement + commit + verify)

Add an `admin-panel` service to `chat/docker-compose.yml` (from LibreChat's official def):

```yaml
admin-panel:
  image: registry.librechat.ai/clickhouse/librechat-admin-panel:latest
  container_name: noc-librechat-admin
  depends_on:
    - api
  environment:
    - PORT=3000
    - SESSION_SECRET=${ADMIN_PANEL_SESSION_SECRET}
    - API_SERVER_URL=http://api:3080          # server-to-server (in-network)
    - VITE_API_BASE_URL=http://localhost:3080 # browser-facing LibreChat URL
    - SESSION_COOKIE_SECURE=false             # local http; true behind TLS in prod
  ports:
    - "3091:3000"
  restart: unless-stopped
```

- The panel talks **only to the LibreChat API** (`API_SERVER_URL`), not Mongo — no DB creds.
- **Secret:** add `ADMIN_PANEL_SESSION_SECRET` (32-byte hex) generation to `init-env.ps1`; for
  the existing `.env`, append it (generated, never printed). Document it in the RUNBOOK.
- **Docs:** RUNBOOK section — URL `http://localhost:3091`, first-registered LibreChat user is
  the admin, and a security note that production must gate the panel behind the reverse proxy /
  IP allowlist (ties **P0-1**).
- **Verify:** `docker compose up -d admin-panel` → container Up; the panel login page responds
  at `http://localhost:3091`.

### Part B — create roles (GUI, Playwright-driven)

1. Ensure an admin account (first-registered LibreChat user = auto-admin; register one on the
   local stack if none exists).
2. Log into the panel; create roles `noc-l1`, `noc-engineer`, `noc-legacy` + matching groups.
3. Register a **test user**; assign it a role.
4. Capture screenshots as the acceptance proof.

## Decisions / caveats

- **Host port 3091** for the panel (3080 = LibreChat, 8100/8101 = MCP).
- **Roles live in MongoDB**, created via GUI — a `docker compose down -v` wipes them. Making
  roles reproducible-from-source is out of scope here and tracked by **P3-4** (config as code).
- **Security:** the panel is an admin surface; local `:3091` exposure is for dev only. Prod
  gating is **P0-1** / not in this task.
- No change to application code, MCP servers, or the NOC/CMS backends.

## Out of scope

- Per-role tool/agent curation (AC-2), `chatMenu:false` (AC-3), identity forwarding (AC-4+).
- Reproducible role seeding (P3-4). SSO (P0-2).

## Verification

- Part A: `docker compose ps` shows `noc-librechat-admin` Up; `curl -sI http://localhost:3091`
  returns an HTTP response (login page).
- Part B: screenshots showing the three roles listed and a test user assigned a role.
