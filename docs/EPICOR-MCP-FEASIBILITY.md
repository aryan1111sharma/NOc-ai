# Epicor ERP MCP — Feasibility & Access Notes

Working notes on whether we can build a **read-only MCP server for Epicor ERP**, mirroring
the existing `noc_mcp` / `cms_mcp` servers in this repo. Captures what we learned so we can
resume without re-deriving it.

> **Status: BLOCKED — pending API-access confirmation.** Not viable to build *today* with the
> access we have. See [Current situation](#current-situation) and [What unblocks this](#what-unblocks-this).

## The core principle

**Desktop-vs-web on the frontend does NOT decide MCP feasibility. The API layer does.**

"Epicor" is an ERP vendor; the flagship is **Epicor Kinetic** (formerly Epicor ERP 10). It
ships with *two frontends over the same backend*:

- **Desktop "smart client"** — classic Windows Forms client (ERP 10 / older Kinetic)
- **Web "Kinetic" UI** — modern browser-based interface

So "is it desktop or web?" is the wrong question. What matters is whether the instance exposes
its **REST API**, which is what an MCP wraps.

## What an MCP would use (once unblocked)

Modern Epicor (Kinetic / ERP 10.2.400+) exposes a documented REST API — a clean fit for the
same `FastMCP.from_openapi` pattern the NOC/CMS servers already use:

| Epicor surface | Use for the MCP |
|---|---|
| **REST API v1/v2 (OData)** at `/api/v2/odata/{Company}/...` | read business objects / query records |
| **Swagger / OpenAPI** at `/api/help` and `/api/v2/odata/{Company}/$metadata` | auto-generate read-only tools (like `noc_openapi.json`) |
| **BAQs** (Business Activity Queries) exposed as REST | curated read-only reporting endpoints |
| **Epicor Functions** | scoped custom endpoints |
| **Auth** | API key **+** user credentials (Basic/OAuth); use a dedicated read-only service account |

Key nuance: **the REST API rides on the same base URL you already log into.** If the web client
is `https://<host>/<instance>/`, the API is typically `https://<host>/<instance>/api/v1|v2/`.

## Current situation

Confirmed constraints as of these notes:

- **Access:** UI login only — no confirmed API key or REST access yet.
- **Admin:** No admin rights to provision an API key / service account ourselves.
- **Version:** Unknown (Kinetic vs ERP 10.x vs ERP 9 not yet established).
- **Install:** A colleague installed it on **his own system** — so it may be a local/on-prem or
  demo/eval instance where *he* holds admin rights. **This is the most promising lead:** if it's
  a local install and he's the admin, the "no admin" blocker likely disappears.

## Decision tree — what each finding means

| Finding | Verdict |
|---|---|
| Version is **Kinetic / ERP 10.2+** AND `/api/help` loads a Swagger page AND creds work against it | ✅ **Buildable** — de-facto API access, no admin needed |
| REST endpoint responds but **demands an API key** we don't have | ⛔ Blocked until an admin issues a key (SaaS/cloud Kinetic enforces this) |
| **404 / not found** at `/api/*`, or version is **ERP 9 / older** | ❌ **Not viable** — no built-in REST API for this instance |

## Self-check (needs only a UI login — no admin)

1. **Version:** Epicor → **Help → About** → note the version (11.x = Kinetic, 10.2.x = ERP 10,
   9.x = legacy).
2. **Probe REST** in a browser tab (swap in the real host/instance):
   - `https://<host>/<instance>/api/help` → Swagger / "REST Help" page? → REST is **on**.
   - `https://<host>/<instance>/api/v2/odata/` → JSON or a login prompt (not a 404)? → endpoint is **live**.
3. Record what each URL returned.

## Questions to ask the colleague who installed it

The two answers that unblock everything are **"local install"** + **"has admin rights."**

1. **Version:** Help → About — what version number? (11.x / 10.2.x / 9.x)
2. **How opened:** browser URL or desktop app icon? If a URL, what's the address?
3. **Where installed:** on his own PC / local server, or a company/hosted server?
4. **Admin rights:** does he have an admin / security-manager login (can create users + manage security)?
5. **API check:** open `https://<epicor-address>/api/help` — Swagger page, login box, or error?

## What unblocks this

Any **one** of these moves us from BLOCKED to buildable:

- The self-check shows REST is on **and** our existing creds authenticate against it, **or**
- The colleague (or an admin) provisions: **REST enabled** + an **API key** + a **read-only
  service account**.

## Design sketch (only once unblocked)

Mirror the existing servers exactly — a new `services/mcp/epicor/` with the same trio:

- `config.py` — `EPICOR_BASE_URL`, API key, service-account creds, `MCP_TRANSPORT`, timeout.
- `auth.py` — attach API key + Basic/OAuth; refresh as needed.
- `server.py` — `FastMCP.from_openapi` over Epicor's Swagger doc, **GET-only route map** so it
  stays read-only (same enforcement as `noc_mcp`).
- Bundle/point at the Epicor OpenAPI spec; optionally curate BAQ-backed tools for the reports
  people actually ask about.

## Decisions

- **D-EP1 — No UI automation.** We will **not** build the MCP on top of browser/desktop-client
  automation. It's brittle, breaks on every Epicor update, and is the wrong foundation for a
  read-only data assistant. Epicor's REST API is the only supported path; if it's unavailable,
  the MCP is not viable for that instance.

## Open tasks

- [ ] **EP-1 — Confirm version + REST reachability** via the self-check / colleague questions above.
- [ ] **EP-2 — Establish API access** (existing creds against REST, or admin-provisioned API key
  + read-only service account).
- [ ] **EP-3 — Decide scope** — which Epicor data the assistant should answer over (orders,
  inventory, jobs, AR/AP, …) before generating tools.
- [ ] **EP-4 — Scaffold `services/mcp/epicor/`** mirroring `noc_mcp` (only after EP-1/EP-2 clear).

_Last updated: 2026-07-11._
