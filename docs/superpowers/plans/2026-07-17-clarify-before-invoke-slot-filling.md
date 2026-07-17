# Clarify-Before-Invoke (Slot-Filling) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all 4 NOC agents ask the 1–2 missing questions before invoking tools when a required identifier is absent, without changing their existing behavior on already-specific or fleet-wide queries.

**Architecture:** Prompt-only change. Insert one additive "CLARIFY BEFORE ACTING" block into each agent's `instructions` in `chat/agents/agents.json` (immediately before the existing `CHARGER LOOKUP (critical):` anchor), then push it live to MongoDB via the existing `restore-agents.ps1` round-trip. No application code changes. The insertion is done by an idempotent Node transform (safe JSON escaping, minimal 4-line diff) and guarded by a Node verification check.

**Tech Stack:** Node.js (transform + verify scripts, run from the scratchpad — not committed), the existing `chat/agents/restore-agents.ps1` (Docker + mongosh), LibreChat UI for behavioral validation.

**Spec:** [`../specs/2026-07-17-clarify-before-invoke-slot-filling-design.md`](../specs/2026-07-17-clarify-before-invoke-slot-filling-design.md)

---

## Facts established before planning
- `CHARGER LOOKUP (critical):` appears **exactly 4 times** in `agents.json` — once per agent's top-level `instructions`. **Version history does NOT contain it**, so a global replace touches only the 4 live instruction strings.
- The CLARIFY block is **not yet present** (idempotency guard will confirm).
- File is plain UTF-8, ~143.8 KB, single-object-per-agent array of 4 agents (`NOC server agent glm-4.7`, `NOC L1 Support`, `NOC Engineer`, `NOC server agent glam-`).
- Deploy path: `restore-agents.ps1` copies `agents.json` into `noc-librechat-mongo` and upserts by string `id`. Requires the mongo container up.

## File Structure
- **Modify (committed):** `chat/agents/agents.json` — 4 additive insertions, nothing else.
- **Scratchpad (NOT committed):** `apply-clarify.mjs` (transform), `verify-clarify.mjs` (check). Scratchpad dir:
  `C:\Users\Admin\AppData\Local\Temp\claude\E--cjain-noc-mcp-agent\2debaac9-f7e6-4940-a183-ea03853c878f\scratchpad`
- **Reused (unchanged):** `chat/agents/restore-agents.ps1`, `chat/agents/_restore.js`.

---

## Task 1: Insert the CLARIFY block into all 4 agents

**Files:**
- Modify: `chat/agents/agents.json`
- Test (scratchpad): `verify-clarify.mjs`
- Transform (scratchpad): `apply-clarify.mjs`

- [ ] **Step 1: Write the verification check (the failing test)**

Create `verify-clarify.mjs` in the scratchpad dir:

```javascript
import fs from 'node:fs';
const P = 'E:/cjain/noc-mcp-agent/chat/agents/agents.json';
const raw = fs.readFileSync(P, 'utf8').replace(/^﻿/, '');
const ANCHOR = 'CHARGER LOOKUP (critical):';
const MARK = 'CLARIFY BEFORE ACTING:';
let ok = true;
const fail = (m) => { console.error('FAIL:', m); ok = false; };
const agents = JSON.parse(raw);
if (agents.length !== 4) fail(`expected 4 agents, got ${agents.length}`);
for (const a of agents) {
  const ins = a.instructions || '';
  if (!ins.includes(MARK)) { fail(`${a.name}: missing CLARIFY block`); continue; }
  if (!ins.includes(ANCHOR)) { fail(`${a.name}: missing CHARGER LOOKUP anchor`); continue; }
  if (ins.indexOf(MARK) > ins.indexOf(ANCHOR)) fail(`${a.name}: CLARIFY not before CHARGER LOOKUP`);
}
const n = raw.split(MARK).length - 1;
if (n !== 4) fail(`expected 4 CLARIFY blocks in raw file, got ${n}`);
console.log(ok ? 'PASS: all 4 agents have the CLARIFY block positioned correctly' : 'FAILED');
process.exit(ok ? 0 : 1);
```

- [ ] **Step 2: Run the check to verify it FAILS**

Run:
```bash
node "C:/Users/Admin/AppData/Local/Temp/claude/E--cjain-noc-mcp-agent/2debaac9-f7e6-4940-a183-ea03853c878f/scratchpad/verify-clarify.mjs"
```
Expected: **FAIL** — "missing CLARIFY block" for each agent, exit code 1.

- [ ] **Step 3: Write the idempotent transform**

Create `apply-clarify.mjs` in the scratchpad dir (the `BLOCK` text is verbatim from spec §5):

```javascript
import fs from 'node:fs';
const P = 'E:/cjain/noc-mcp-agent/chat/agents/agents.json';
const ANCHOR = 'CHARGER LOOKUP (critical):';
const BLOCK = `CLARIFY BEFORE ACTING: Before calling any tool, check that the query names what you need to answer it. If a required identifier is missing, ASK for it in one short message (the 1–2 key questions) instead of guessing or searching blindly. Required by request type: per-charger info (status, firmware, telemetry, health) → a charger id/model/location or a customer/site; failed sessions / errors / transactions → a charger or customer AND a time window (e.g. today, last 24h, a date); tickets → a ticket id, or a charger/customer, or a status plus a time window; fleet-wide counts/totals (how many chargers, online vs offline) → none, answer directly (default to the legacy CMS). If the user cannot give a detail ("I don't know the charger" / "show all today"), proceed with the broadest safe query for what they gave, state the scope you assumed, and offer to narrow. Never ask more than needed, never re-ask what was already provided, and never loop. Once you have enough, follow the CHARGER LOOKUP rules below.`;

const raw = fs.readFileSync(P, 'utf8');
const bom = raw.startsWith('﻿') ? '﻿' : '';
const body = bom ? raw.slice(1) : raw;
if (body.includes('CLARIFY BEFORE ACTING:')) { console.log('already applied — no change'); process.exit(0); }
const before = body.split(ANCHOR).length - 1;
if (before !== 4) { console.error(`expected 4 anchors, found ${before} — aborting`); process.exit(1); }
const segment = BLOCK + '\n\n' + ANCHOR;                 // real newlines
const escaped = JSON.stringify(segment).slice(1, -1);    // JSON-escaped for embedding in a string value
const out = body.split(ANCHOR).join(escaped);
JSON.parse(out);                                         // sanity: still valid JSON (throws if not)
fs.writeFileSync(P, bom + out, 'utf8');
console.log(`applied CLARIFY block to ${out.split('CLARIFY BEFORE ACTING:').length - 1} agents`);
```

- [ ] **Step 4: Run the transform**

Run:
```bash
node "C:/Users/Admin/AppData/Local/Temp/claude/E--cjain-noc-mcp-agent/2debaac9-f7e6-4940-a183-ea03853c878f/scratchpad/apply-clarify.mjs"
```
Expected: `applied CLARIFY block to 4 agents`

- [ ] **Step 5: Run the check to verify it PASSES + inspect the diff**

Run:
```bash
node "C:/Users/Admin/AppData/Local/Temp/claude/E--cjain-noc-mcp-agent/2debaac9-f7e6-4940-a183-ea03853c878f/scratchpad/verify-clarify.mjs"
git -C E:/cjain/noc-mcp-agent diff --stat chat/agents/agents.json
```
Expected: `PASS: all 4 agents have the CLARIFY block positioned correctly` (exit 0), and the diff touches **only** `chat/agents/agents.json` with 4 changed regions (one per agent). If the diff shows reformatting beyond the 4 insertions, `git checkout chat/agents/agents.json` and re-run — do not commit a reformatted file.

- [ ] **Step 6: Commit**

```bash
git -C E:/cjain/noc-mcp-agent add chat/agents/agents.json
git -C E:/cjain/noc-mcp-agent commit -m "feat(agents): add clarify-before-invoke (slot-filling) policy to all 4 NOC agents

Ask the 1-2 missing questions only when a required identifier is absent;
unchanged on already-specific or fleet-wide queries. Prompt-only.
Spec: docs/superpowers/specs/2026-07-17-clarify-before-invoke-slot-filling-design.md

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Push the change live to MongoDB

**Files:** none modified — runs the existing `chat/agents/restore-agents.ps1`.

- [ ] **Step 1: Confirm the mongo container is up**

Run:
```bash
docker ps --filter name=noc-librechat-mongo --format "{{.Names}}"
```
Expected: `noc-librechat-mongo`. If empty, bring the stack up first (`chat/up.ps1` / `docker compose -f chat/docker-compose.yml up -d`) and re-check.

- [ ] **Step 2: Run the restore**

Run (PowerShell):
```powershell
pwsh -File E:/cjain/noc-mcp-agent/chat/agents/restore-agents.ps1
```
Expected: 4 lines `restored agent_... (<name>) owner=...`, then `done: 4 agent(s)`.

- [ ] **Step 3: Verify the live agents carry the block**

Run:
```bash
docker exec noc-librechat-mongo mongosh --quiet LibreChat --eval "db.agents.countDocuments({ instructions: /CLARIFY BEFORE ACTING/ })"
```
Expected: `4`

---

## Task 3: Behavioral validation in the LibreChat UI (spec §7)

**Files:** none — manual acceptance test. No commit.

- [ ] **Step 1: Open LibreChat and run the acceptance matrix against each of the 4 agents**

Open the LibreChat UI (the `api` service — default http://localhost:3080). For **each** agent, send each prompt and record the outcome:

| Prompt | Required behavior |
|---|---|
| `What is the session which was failed?` | **ASK** — which charger/customer? + what time range? (no tool call yet) |
| `Status of QF-1023?` | **NO ASK** — a charger is named; go straight to tools |
| `How many chargers are online?` | **NO ASK** — answer directly via CMS |
| `Failures for ACME today` | **NO ASK** — customer + time window present; proceed |
| Reply `show all failures today` after it asks for a charger | **DEGRADE** — broadest safe query, states scope, offers to narrow; does NOT loop |

- [ ] **Step 2: Record results and decide**

Note pass/fail per agent per prompt. Expected: all pass. If a specific agent fires tools prematurely on prompt 1, or asks on prompts 2–4, capture the transcript — the fix is a wording tweak to that agent's block (repeat Task 1 for the adjusted text). Chat models are non-deterministic; re-run a failing prompt 2–3 times before concluding it's a real miss.

---

## Deferred (not part of this plan)
- **Regression seeding for the loop.** Spec §8 says to add the Task 3 matrix as pinned regression cases in `manthan-review/regression/`. That directory belongs to the self-improvement loop, which is not built yet. These 5 cases are already captured in the slot-filling spec §7 and will be seeded when the loop is implemented. No orphan structure is created now.

---

## Self-Review

**Spec coverage:**
- Trigger = missing required identifier (D1) → the BLOCK text + Task 3 prompts 2–4 (no-ask) and 1 (ask). ✅
- Prompt-only (D2) → Task 1 edits only `instructions`; no code paths. ✅
- All 4 agents (D3) → transform guarantees 4 insertions; verify asserts 4. ✅
- Required-slots map (§3) → encoded verbatim in BLOCK. ✅
- Batch / never-re-ask / escape-hatch / coexistence (§4) → all present in BLOCK wording; escape-hatch validated by Task 3 step 1 row 5; coexistence preserved because the block is inserted *before* the untouched `CHARGER LOOKUP` section. ✅
- Insert position "before CHARGER LOOKUP" (§5) → transform anchors on `CHARGER LOOKUP (critical):`; verify asserts MARK index < ANCHOR index. ✅
- Rollout via restore round-trip (§6) → Task 2. ✅
- Success criteria (§7) → Task 3 matrix is a 1:1 copy. ✅
- Testing (§8): manual UI pass ✅ (Task 3); loop regression cases → deferred (loop not built); coexistence check ✅ (prompt 2 exercises the existing lookup path).

**Placeholder scan:** no TBD/TODO; every script and command is complete and literal. ✅

**Type/name consistency:** `ANCHOR` = `'CHARGER LOOKUP (critical):'` and `MARK`/guard = `'CLARIFY BEFORE ACTING:'` are identical across the verify and apply scripts. ✅
