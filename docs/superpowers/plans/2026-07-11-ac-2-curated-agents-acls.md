# AC-2 Curated Agents + ACLs — Implementation Plan

> Use superpowers:executing-plans. GUI-driven; verification is screenshots + a role-scoped login.

**Goal:** Build "NOC L1 Support" and "NOC Engineer" agents with curated tools, ACL-shared to `noc-l1` / `noc-engineer`, and prove an `noc-l1` user sees only the L1 agent (no proxy tool).

**Tech:** LibreChat v0.8.7 Agent Builder (`:3080`), Playwright.

**Spec:** `docs/superpowers/specs/2026-07-11-ac-2-curated-agents-acls-design.md`

**Note:** the exact Agent Builder / Share UI is explored live (snapshot-driven). No repo code changes → verification is behavioral (screenshots) + Mongo cross-check of the agents + their ACL.

---

### Task 1: Create the "NOC L1 Support" agent (curated, no proxy)

- [ ] Log into `:3080` as `noc-admin@quench.local` (admin) via Playwright.
- [ ] Open Agent Builder → create agent "NOC L1 Support"; select the GLM endpoint/model.
- [ ] Add system prompt: "Always respond in English." + brief L1 instructions.
- [ ] Under Tools/MCP, enable a small `noc` read subset (e.g. `dashboard_summary` + 1–2 status
      tools); **do not** add the charger-proxy tool. Save.
- [ ] Screenshot the L1 agent's tool list (proof: no proxy).

### Task 2: Create the "NOC Engineer" agent (includes proxy)

- [ ] Create agent "NOC Engineer"; GLM model; enable reads **plus** the charger-proxy tool. Save.

### Task 3: Share each agent to its role (ACL)

- [ ] L1 agent → Share → add role **`noc-l1`** at **Viewer**. Save.
- [ ] Engineer agent → Share → add role **`noc-engineer`** at **Viewer**. Save.
- [ ] Keep both **private** otherwise (not public).

### Task 4: Verify role-scoped visibility

- [ ] Log out; log in as `noc-tester@quench.local` (role `noc-l1`).
- [ ] Open the agent picker → confirm **only** "NOC L1 Support" is visible; "NOC Engineer" is
      absent. Screenshot.
- [ ] Cross-check in Mongo: the two agents exist and their ACL entries name the correct roles.

### Task 5: Close out

- [ ] Mark AC-2 `[x]` (or `[~]` with notes) in `docs/PRODUCTION-READINESS.md`; commit.
- [ ] Version bump: `scripts/bump-version.ps1 minor`; push branch + tag.

---

## Self-Review

- Acceptance (`noc-l1` sees only L1 agent, no proxy) → Task 4. ✓
- Curated subset + proxy exclusion → Task 1; proxy in Engineer → Task 2. ✓
- Role ACL sharing → Task 3. ✓
- Version bump per feature → Task 5 (per saved preference). ✓
- No repo code changes; agents live in Mongo (P3-4 caveat noted in spec). ✓
