# AC-1 Admin Panel + Roles — Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Stand up the LibreChat Admin Panel service and create the `noc-l1` / `noc-engineer` / `noc-legacy` roles + a test-user assignment.

**Architecture:** Add an `admin-panel` service (talks to the LibreChat API only) to `chat/docker-compose.yml`; generate its `SESSION_SECRET`; create roles via the panel GUI (Playwright-driven).

**Tech Stack:** Docker Compose, LibreChat v0.8.7 + admin-panel image, Playwright (browser automation).

**Spec:** `docs/superpowers/specs/2026-07-11-ac-1-admin-panel-roles-design.md`

**Note:** GUI role creation has no TDD red/green; verification is container-up + reachable login page (Part A) and screenshots of the created roles / assignment (Part B).

---

### Task 1: Add the admin-panel service + secret + docs

**Files:**
- Modify: `chat/docker-compose.yml` (add `admin-panel` service)
- Modify: `chat/init-env.ps1` (generate `ADMIN_PANEL_SESSION_SECRET`)
- Modify: `chat/RUNBOOK.md` (admin-panel section)

- [ ] **Step 1:** Add the `admin-panel` service block (see spec) after the `cms-mcp-server` service in `chat/docker-compose.yml`.
- [ ] **Step 2:** In `chat/init-env.ps1`, add `ADMIN_PANEL_SESSION_SECRET=$(Rand 32)` to the generated `.env` content (near the other secrets).
- [ ] **Step 3:** Add a RUNBOOK section: panel at `http://localhost:3091`, first-registered user is admin, `docker compose up -d admin-panel`, and the prod-gating security note.
- [ ] **Step 4:** Commit.

```bash
git add chat/docker-compose.yml chat/init-env.ps1 chat/RUNBOOK.md
git commit -m "feat(chat): add LibreChat admin-panel service (AC-1)"
```

---

### Task 2: Provision the secret + bring the panel up (verify Part A)

**Files:** `chat/.env` (append generated secret — never printed)

- [ ] **Step 1:** Append `ADMIN_PANEL_SESSION_SECRET` to the existing `chat/.env` if absent, generating it inline without printing the value (PowerShell, mirroring `init-env.ps1`'s `Rand`).
- [ ] **Step 2:** `cd chat && docker compose up -d admin-panel` (pulls the image, starts it).
- [ ] **Step 3:** Verify: `docker compose ps admin-panel` shows Up; `curl -sI http://localhost:3091` returns an HTTP status line (login page served).

---

### Task 3: Create roles + assign a test user (Part B, Playwright)

- [ ] **Step 1:** Ensure an admin account exists — check for a registered LibreChat user; if none, register one at `http://localhost:3080` (first user = auto-admin). Record the credentials for the session only.
- [ ] **Step 2:** Open `http://localhost:3091`, log in as the admin.
- [ ] **Step 3:** Create roles `noc-l1`, `noc-engineer`, `noc-legacy` (+ matching groups if the panel separates them). Screenshot the roles list.
- [ ] **Step 4:** Register a test user at `http://localhost:3080`; in the panel assign it a role (e.g. `noc-l1`). Screenshot the assignment.

---

### Task 4: Close out AC-1

**Files:** `docs/PRODUCTION-READINESS.md`

- [ ] **Step 1:** Mark AC-1 `[x]` (or `[~]` with a note if any sub-step is deferred), noting the P3-4 "roles live in Mongo / not yet config-as-code" caveat.
- [ ] **Step 2:** Commit.

```bash
git add docs/PRODUCTION-READINESS.md
git commit -m "chore: mark AC-1 (admin panel + roles) status"
```

---

## Self-Review

- Spec coverage: panel service (T1) + secret (T1/T2) + verify reachable (T2) + roles/groups + test-user assignment (T3) + backlog (T4). ✓
- Placeholder scan: none — service block in spec, exact commands here.
- Consistency: port 3091, image `registry.librechat.ai/clickhouse/librechat-admin-panel:latest`, env `ADMIN_PANEL_SESSION_SECRET` used consistently across compose, init-env, and .env provisioning.
- Secret handling: generated + appended, never read/printed (respects secrets guardrail).
