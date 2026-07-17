# Manthan Self-Improvement Loop — Design Spec

**Date:** 2026-07-17
**Status:** Approved for planning
**Author:** kmittal@quenchchargers.com (with Claude Code)

---

## 1. Problem

Manthan AI (the NOC MCP agent, built on LibreChat) is developed and maintained by
a single person. Quality improvement today is fully manual: open individual chat
sessions, judge whether the agent's answer was correct using domain knowledge,
diagnose why it went wrong, then edit agent instructions (or tool code) to fix it.
This is slow, does not scale, and means many bad answers are never reviewed.

**Goal:** an automated, self-improving loop that runs nightly on the local host,
reviews the day's sessions, verifies correctness against ground truth, and
**auto-applies safe instruction fixes** while **escalating risky code fixes** —
shrinking the maintainer's job from "review everything and fix everything" to
"skim a morning digest and approve the occasional code patch."

### Non-goals (out of scope for this spec)
- Rewriting the agents, the MCP tools, or LibreChat.
- Any change to how end users interact with Manthan.
- Cloud/hosted execution (see §3 — everything runs locally by constraint).
- Fine-tuning or model training. This loop edits *prompts and tool code*, not weights.
- **Auto-building new features.** The loop may *propose* new tools/skillsets/behaviors
  (D9, §12) but never designs or ships them autonomously — generative work has no
  ground-truth oracle and must go through a human-led brainstorm → spec → build.

---

## 2. Key Decisions (locked during brainstorming)

| # | Decision | Choice |
|---|----------|--------|
| D1 | **Autonomy** | Auto-apply **low-risk** changes (agent instruction tweaks); **escalate** tool/code changes for human approval. |
| D2 | **Correctness oracle** | **LLM judge + re-verify**: the judge independently re-queries the CMS/NOC MCP tools to confirm checkable claims. No hand-maintained golden set required for detection. |
| D3 | **Run environment** | This **Windows dev machine**, next to the local Docker stack. |
| D4 | **"Hermes" role** | **Scheduler/orchestrator only** — a dumb nightly trigger. All intelligence lives in the pipeline it invokes. |
| D5 | **Verify-before-apply** | **Always verify**: an auto-applied instruction change must (a) make the original failing question pass, and (b) not regress a sampled regression set — before it goes live. |
| D6 | **Regression set** | **Self-curated**: the loop promotes judge-confirmed-correct sessions into a growing regression store; no manual upkeep required (entries can be pinned/blocklisted). |
| D7 | **Candidate replay fidelity** | The judge is **Claude** (best reasoning); the candidate-agent replay uses the **production model** (GLM via z.ai) so a GLM agent's fix is tested on GLM. |
| D8 | **Escalation format** | Tool/code fixes become a **patch file + digest entry**; optionally a **draft GitHub PR**. Never auto-applied. |
| D9 | **Feature discovery** | The loop **detects capability gaps** from real sessions and writes **feature proposals** (propose-only). It **never auto-builds** new tools/skillsets/behaviors — those are human-decided and go through their own brainstorm → spec → build. See §12. |

---

## 3. Environment & Constraints

All state and dependencies are **local Docker** (`chat/docker-compose.yml`), none
publicly exposed:

- **Sessions:** LibreChat MongoDB — service `mongodb`, container `noc-librechat-mongo`,
  URI `mongodb://mongodb:27017/LibreChat`. Relevant collections: `conversations`,
  `messages`, `agents`, `users`.
- **Agents:** 4 agents, all `provider: GLM` (models `glm-4.7`, `glm-5.2`,
  `glm-5-turbo`), using MCP servers `noc` + `cms` (the NOC Engineer also uses
  `docgen`, `jira`, `ecog_sd`).
- **Verification tools:** the CMS MCP (`cms-mcp-server`) and NOC MCP
  (`mcp-server` / `noc-mcp-server`) containers — the same tools the agents use.
- **Apply mechanism (already exists):** `chat/agents/agents.json` is git-committed
  config-as-code; `export-agents.ps1` dumps Mongo → json and `restore-agents.ps1`
  upserts json → Mongo idempotently by string `id`.

**Consequence:** any component that reads sessions, re-queries MCP tools, or applies
changes must run on this host / the `chat` Docker network. Cloud schedulers cannot
reach these endpoints without exposing Mongo + MCP, which is rejected for a
production agent's data. This forces D3.

---

## 4. Architecture

### 4.1 Components

| Component | Responsibility | Runtime |
|-----------|----------------|---------|
| **Scheduler** (D4, "Hermes") | Wake once daily, invoke `run.ps1`, exit. No logic. | Container on the `chat` network **or** Windows Task Scheduler. |
| **Harness** (`manthan-review/`) | Deterministic, safety-critical code: extract sessions, enforce the allow-list **guard**, edit `agents.json` + git commit + `restore`, manage the regression store, write the digest. | Python, headless. |
| **Reasoning skill** (`.claude/skills/manthan-review/`) | The intelligence: **judge**, **diagnose**, **cluster**, **draft**. Invoked as `claude -p`. | Claude Code headless. |
| **Verification tools** | Existing CMS/NOC MCP servers, called by the judge (for detection) and by candidate replay (for verify). | Local MCP containers. |

**Design principle — the safety boundary is code, not prompt.** The decision to
auto-apply and the write itself live in the Harness (`guard.py` / `apply.py`). The
LLM proposes; it can never widen its own permissions or write to Mongo directly.

### 4.2 Repository layout

```
manthan-review/
  extract.py        # Mongo → sessions.json for the target day (dedup via state)
  guard.py          # allow-list enforcement — THE safety boundary
  apply.py          # agents.json edit → git commit → restore-agents wrapper
  replay.py         # candidate-agent replay on the production GLM model + MCP
  regression/       # self-curated golden cases (question + verified answer)
    _index.json     #   registry with pinned/blocklisted flags
  proposals/        # escalated tool/code patches (patch files)
  state/            # reviewed-session cursor, run logs
  digest.py         # writes docs/manthan-review/YYYY-MM-DD.md
  config.yaml       # caps, sampling, kill switch defaults
  run.ps1           # entrypoint the scheduler calls
  tests/            # unit tests for guard, apply, extract, regression store
.claude/skills/manthan-review/
  SKILL.md          # judge / diagnose / cluster / draft reasoning contract
docs/manthan-review/
  YYYY-MM-DD.md     # daily digests (git-tracked history)
```

---

## 5. Nightly Data Flow

```
Scheduler
  │
  ▼
Harness.extract ── yesterday's sessions from Mongo, dedup vs state cursor
  │
  ▼  (per session)
Claude JUDGE ── reads transcript + tool-call trace
  │            ── re-queries CMS/NOC MCP to verify checkable claims (D2)
  │            ── verdict: correct | wrong | unverifiable
  ├── correct & verifiable ─────────────► promote to regression store (D6)
  │
  ▼  (wrong)
DIAGNOSE ── classify root cause from the tool-call trace:
  │           • model behavior (confabulation, stale number, skipped/mis-chosen
  │             tool, bad format)      → INSTRUCTION class
  │           • tool returned wrong/empty/errored → TOOL/CODE class
  ▼
CLUSTER ── group the day's failures by root cause (one fix per pattern)
  │
  ├─ INSTRUCTION cluster ─────────────────────────────────────────────┐
  │   DRAFT additive guardrail edit to the agent's `instructions`      │
  │   Harness.guard (allow-list): instructions-field-only, additive,   │
  │        no tools/model/mcp change, within diff-size cap             │
  │        ├ fail → ESCALATE                                            │
  │        └ pass ▼                                                     │
  │   VERIFY (D5): replay on candidate GLM agent —                     │
  │        (a) original failing question now passes                    │
  │        (b) sampled regression set still passes                     │
  │        ├ fail → ESCALATE (with evidence)                            │
  │        └ pass ▼                                                     │
  │   Harness.apply: edit agents.json → git commit "manthan-auto:" →   │
  │        restore-agents.ps1 → LIVE                                    │
  │                                                                     │
  └─ TOOL/CODE cluster ────────────────────────────────────────────────┤
      DRAFT Python patch → ALWAYS ESCALATE:                            │
        write patch to proposals/ + digest entry (+ optional draft PR) │
                                                                        │
  ▼                                                                     ▼
Harness.digest ── docs/manthan-review/YYYY-MM-DD.md
     (found / auto-applied w/ before-after + verify proof / awaiting you / cost)
```

---

## 6. Component Contracts

### 6.1 `extract.py`
- **Input:** target date (default: yesterday, local tz), Mongo connection.
- **Output:** `state/sessions-YYYY-MM-DD.json` — one record per conversation with
  its ordered messages and, where available, the tool-call trace (tool name, args,
  result) reconstructed from the `messages` collection.
- **Idempotency:** maintains a cursor in `state/` of already-reviewed conversation
  ids; never re-reviews the same session unless it has new messages.
- **Volume control:** caps at `config.max_sessions_per_run`; if exceeded, samples
  (prioritizing sessions with user-dissatisfaction signals, then most recent) and
  records what was dropped in the digest (no silent truncation).

### 6.2 Judge (in `SKILL.md`)
- **Input:** one session record.
- **Behavior:** determine what the agent claimed; for each *checkable* claim,
  independently call the relevant MCP tool and compare. Confabulation, wrong-charger,
  and stale-number errors are caught here because the judge sees ground truth.
- **Output (structured JSON):** `{ verdict, checked_claims[], evidence[], severity }`.
- **Guardrail:** claims that cannot be verified via tools are marked `unverifiable`
  and never drive an auto-apply (they may still be reported).

### 6.3 Diagnose + Cluster (in `SKILL.md`)
- Classify each failure as `instruction` or `tool_code` from the tool-call trace.
- Group failures with the same root cause into a single cluster carrying: the
  pattern description, representative failing session(s), target agent id, and the
  proposed fix class.

### 6.4 `guard.py` — the safety boundary (deterministic)
Auto-apply is permitted **only** if the drafted change satisfies **all**:
1. Touches **only** the `instructions` field of a **known** agent id in `agents.json`.
2. Is **additive** — does not delete or weaken existing instruction lines
   (validated by diff analysis; deletions/rewrites → escalate).
3. Does **not** touch `tools`, `model`, `provider`, `mcpServerNames`, or any other
   field.
4. Diff size ≤ `config.max_diff_lines`.
5. Under the nightly cap `config.max_autoapplies_per_run`.
Any failure → the change is **escalated**, not applied. `guard.py` is unit-tested
against adversarial drafts (e.g. a draft that also flips a tool off must be rejected).

### 6.5 `replay.py` — verify-before-apply (D5, D7)
- Builds a **candidate agent** = target agent's config with the patched instructions.
- Replays it on the **production GLM model** with the real MCP tools, capturing its
  answer to (a) the original failing question and (b) a sampled slice of the
  regression set.
- Re-runs the judge on each replay answer.
- **Pass criteria:** (a) now correct **and** (b) no regression-set entry flips from
  correct to wrong. Otherwise → escalate with the evidence.
- **Isolation:** the candidate must not disturb the live agent. Recommended:
  replay through LibreChat's agent API against a scratch agent id created for the
  run and deleted afterward. *(Exact isolation mechanism is the first item to
  validate in the implementation plan — see §9.)*

### 6.6 `apply.py`
- Edits `agents.json`, makes **one** git commit per applied cluster with message
  `manthan-auto: <agent> — <pattern>` and the digest link in the body.
- Runs `restore-agents.ps1` to push the change live.
- On any failure, aborts and leaves a clean tree (no partial apply).

### 6.7 Regression store (`regression/`) (D6)
- Judge-confirmed-correct, verifiable sessions are promoted to
  `regression/<id>.json` = `{ question, agent_id, verified_answer, checks[], added }`.
- `_index.json` tracks `pinned` (always replayed) and `blocklisted` (never used).
- Sampling: each run replays `min(config.regression_sample, available)` entries,
  always including pinned ones and any entry touching the same agent being changed.

### 6.8 `digest.py`
Writes `docs/manthan-review/YYYY-MM-DD.md`:
- Summary counts (reviewed / correct / wrong / unverifiable / dropped).
- **Auto-applied** section: per change — before/after diff, the failing case, and
  the verify proof (fix passed + regression clean), with the commit hash.
- **Awaiting you** section: escalated tool/code patches with root cause, failing
  session, and the `proposals/` patch path (+ PR link if opened).
- **Feature proposals** section (D9, §12): clustered capability gaps with the
  evidence (how many sessions, representative asks) and a one-line suggested
  direction — for you to triage, never auto-built.
- **Cost:** tokens/model calls spent.

---

## 7. Safety, Rollback & Kill Switch

- **Allow-list in code** (D1, §6.4) — the LLM cannot self-widen permissions.
- **Verify-before-apply** (D5) — nothing untested reaches the live agent.
- **One commit per auto-apply**, tagged `manthan-auto:` — `git log` is the audit
  trail; `git revert <hash>` + `restore-agents.ps1` is one-step rollback.
- **Nightly caps** — `max_autoapplies_per_run` bounds blast radius on a bad day.
- **Kill switch** — `MANTHAN_REVIEW_APPLY=off` drops the entire loop to
  report-only (still reviews + digests, applies nothing).
- **Escalation-by-default** — anything ambiguous, unverifiable, or outside the
  allow-list is reported, never applied.

---

## 8. Configuration (`manthan-review/config.yaml`)

```yaml
target: yesterday            # or an explicit date
max_sessions_per_run: 100    # sample beyond this
max_autoapplies_per_run: 3   # nightly cap on auto-applied clusters
max_diff_lines: 15           # per-change instruction diff cap
regression_sample: 20        # regression entries replayed per verify
apply: on                    # overridden by MANTHAN_REVIEW_APPLY=off
open_pr: false               # escalations as patch file only unless true
```

---

## 9. Open Implementation Decisions (resolve first in the plan)

1. **Candidate-replay isolation (§6.5)** — confirm the exact, least-invasive way to
   run a patched agent on GLM without touching the live agent (scratch agent via
   LibreChat API vs. direct GLM+MCP invocation). This is the riskiest unknown and
   the first thing the plan should spike.
2. **Tool-call trace reconstruction (§6.1)** — verify how faithfully tool calls +
   results are recoverable from the `messages` collection for a given session.
3. **Scheduler choice (D4)** — Windows Task Scheduler vs. a compose container.
   Default to Task Scheduler for simplicity unless container is preferred.

---

## 10. Success Criteria

- A nightly run reviews the prior day's sessions unattended and produces a digest.
- The judge catches at least the known failure classes from history (wrong-charger
  confabulation, stale count, hyphen/space search mismatch) on replayed examples.
- An instruction-class fix is auto-applied **only** after passing verify, is a
  single revertible commit, and is reflected live via `restore-agents.ps1`.
- A tool-class issue is **never** auto-applied — it appears as a patch + digest entry.
- `guard.py`, `apply.py`, `extract.py`, and the regression store have unit tests,
  including adversarial guard tests.
- `MANTHAN_REVIEW_APPLY=off` fully disables application while preserving review.

---

## 11. Testing Strategy

- **Unit:** `guard.py` (adversarial drafts), `apply.py` (commit + restore + abort
  cleanliness), `extract.py` (dedup, sampling, drop-reporting), regression store
  (promote/pin/blocklist/sample).
- **Integration (seeded):** a fixture DB with known-bad and known-good sessions;
  assert the loop classifies, drafts, verifies, and applies/escalates correctly.
- **End-to-end (dry-run):** full nightly run with `apply: off` against a copy of
  real sessions; inspect the digest for correctness before enabling apply.
- **Regression self-test:** confirm a promoted good session, replayed, still passes.

---

## 12. Feature-Discovery Extension (propose-only) (D9)

The loop already reads every real session, so it is the ideal *scout* for what to
build next — but it must **never build**. This extension adds detection + proposal
only; humans decide and build.

### 12.1 What it detects
While judging/diagnosing, a session is also tagged for **capability signals**:
- **Unmet need** — the user asked for something no current tool/skill can answer
  (agent had to decline, guess, or gave a partial answer despite calling tools
  correctly). This is distinct from a *wrong* answer (a corrective failure).
- **Friction pattern** — recurring behavioral gaps that a *feature* (not a one-off
  guardrail) would solve, e.g. many vague queries that fired tools prematurely
  → signals a "clarify-before-invoke / slot-filling" feature.

### 12.2 Output
Capability signals are clustered weekly-over-daily (a proposal needs recurrence,
not a single occurrence) and written to a backlog file
(`manthan-review/proposals/features.md`) and the digest's **Feature proposals**
section, each with: the gap, how many sessions/how often, 1–3 representative asks,
and a one-line suggested direction. Nothing here is drafted as code or applied.

### 12.3 Boundary (why this stays safe)
- A feature proposal is **data for a human**, never an input to the auto-apply path.
- The allow-list (§6.4) is unchanged — it still forbids anything but additive
  instruction edits, so a proposal can never leak into an auto-applied change.
- Turning a proposal into a feature is a separate **brainstorm → spec → build**
  cycle. The loop's role ends at "here's what's worth building, and why."
```
