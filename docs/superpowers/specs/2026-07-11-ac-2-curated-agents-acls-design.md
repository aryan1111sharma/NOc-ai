# AC-2 — Curated agents + ACLs

**Status:** approved design · **Date:** 2026-07-11 · **Backlog item:** AC-2
**Scope:** LibreChat agent configuration (GUI) — no repo code changes.

## Problem / goal

The `noc-*` roles exist (AC-1) but nothing yet limits what a role can see. AC-2 makes per-role
**tool visibility** real: build one agent per role that bundles only that role's tool subset, and
share each agent's ACL to the matching role.

**Goal (AC-2 acceptance):** an `noc-l1` user sees **only** the L1 agent and its tools — **not**
the charger-proxy tool, and **not** the Engineer agent.

## Mechanism (LibreChat v0.8.7)

- Agents are built in the LibreChat **Agent Builder** (`:3080`, as an admin/owner): pick the
  GLM model, enable a chosen subset of the `noc` MCP server's tools, add a system prompt.
- Each agent has its own **ACL**; the **Share** dialog can grant a **role** access at
  **Viewer** (use-only; can't open the builder or see instructions) / Editor / Owner.
- Agents are **private** until shared, so a user sees only agents shared to their roles.

## Design

Create two agents, both private (shared only to their role):

1. **"NOC L1 Support"** — GLM model; enable a small curated set of read tools (e.g.
   `dashboard_summary` + 1–2 status reads), **excluding** the charger-proxy tool; system prompt
   starting "Always respond in English." + L1 assistant instructions. Share → role **`noc-l1`**,
   Viewer.
2. **"NOC Engineer"** — GLM model; include the charger-proxy tool (+ reads). Share → role
   **`noc-engineer`**, Viewer.

## Verification (Playwright)

1. As admin, confirm both agents exist with the intended tools (L1 has no proxy tool).
2. Log in as `noc-tester@quench.local` (role `noc-l1`) → agent picker shows **only** "NOC L1
   Support"; "NOC Engineer" is absent. Screenshot.
3. (If visible) the L1 agent's tool list has no charger-proxy tool. Screenshot.

## Decisions / caveats

- **Share to roles directly** (roles are ACL principals) — groups not needed yet (deferred AC-1).
- **Scope:** L1 + Engineer now (needed to prove the negative). Legacy-CMS agent later.
- Creating agents needs the GLM endpoint selectable but **not** a working GLM response (P0-7);
  AC-2 tests *visibility*, not chat.
- Agents live in Mongo (like roles) — reproducible-from-source is `P3-4`.
- Known LibreChat issue #12306: role-level agent CREATE/SHARE perms can reset on restart —
  doesn't affect admin-created, role-shared Viewer access; note if sharing UI misbehaves.
- **Not** data-scope enforcement — that's AC-5/AC-6 (server-side). AC-2 is visibility only.

## Out of scope

- MCP-side tool filtering / data scoping (AC-5/AC-6), identity forwarding (AC-4), groups.
