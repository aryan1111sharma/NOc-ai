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
| D7 | **Analysis/improvement model** | The judge, diagnose, cluster, and draft reasoning all run on **GLM 5.2 (advanced)** via the z.ai API — keeps the whole loop on the same provider/residency as the production agents, needs no Claude key at runtime, and reuses the proven GLM+MCP tool-calling path. Claude Code is the **dev-time authoring tool**, not a runtime dependency. |
| D7a | **Candidate replay fidelity** | The candidate-agent replay uses each agent's **own production model** (GLM `glm-4.7` / `glm-5.2` / `glm-5-turbo`) so a fix is tested on the model that will actually run it. |
| D7b | **Self-grading caveat** | Judge and (some) agents share the GLM family, so the judge may grade its own family's output. Mitigated because verdicts are grounded in **re-querying MCP tools (ground truth)**, not model opinion. GLM-5.2 judging quality MUST be validated in a dry-run (`apply: off`) before auto-apply is enabled. |
| D8 | **Escalation format** | Escalations become a **draft GitHub PR** (merge = approve) plus a patch file in `proposals/` and a digest/email entry. Never auto-applied. See §14. |
| D9 | **Feature discovery** | The loop **detects capability gaps** from real sessions and writes **feature proposals** (propose-only). It **never auto-builds** new tools/skillsets/behaviors — those are human-decided and go through their own brainstorm → spec → build. See §12. |
| D10 | **Daily email report** | The nightly run **emails a health+quality digest** to the maintainer via **Gmail SMTP** (Google Workspace, app password in a gitignored `.env`). The emailed report is the same digest rendered as HTML. See §13. |
| D11 | **Tool health** | The report includes **MCP tool-health metrics** (per `cms_`/`noc_` tool: success / error / timeout / empty-response counts) aggregated from the day's session tool-calls **and** the judge's own re-query calls. Degraded tools are flagged **email-only** (no PR — nothing to merge) for manual investigation. See §13. |
| D12 | **Approval channel** | The escalated set is approved via **GitHub draft PR** on `Quench-EV-Charger/noc-mcp-agent`: **merge = approve**, **close = reject**. Merging an instruction PR triggers `restore-agents.ps1`; production is untouched until a human merges (no auto-merge). See §14. |
| D13 | **Human steering** | The maintainer directs Hermes via a plain-English **`policy.md`** (injected into the nightly reasoning prompts) + **pinned regression cases**. New Hermes *machinery* is normal brainstorm → spec → build. Hermes may *propose* changes to its own code but **never auto-applies** them. See §15. |

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
| **Reasoning** (`manthan-review/prompts/`) | The intelligence: **judge**, **diagnose**, **cluster**, **draft** — prompt templates the harness sends to **GLM 5.2** with the MCP tools bound. | GLM 5.2 via z.ai API (D7). |
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
  pr.py             # push branch + open draft PR per escalation (D12)
  replay.py         # candidate-agent replay on the production GLM model + MCP
  regression/       # self-curated golden cases (question + verified answer)
    _index.json     #   registry with pinned/blocklisted flags
  proposals/        # escalated tool/code patches (patch files)
  state/            # reviewed-session cursor, run logs
  digest.py         # writes docs/manthan-review/YYYY-MM-DD.md
  health.py         # aggregates per-tool success/error/timeout/empty from tool-calls (D11)
  mailer.py         # renders digest as HTML + sends via Gmail SMTP (D10)
  email_template.html  # HTML report template
  .env              # GMAIL_APP_PASSWORD etc. — GITIGNORED, never committed
  glm_client.py     # z.ai GLM 5.2 API wrapper (reasoning calls, MCP tools bound)
  prompts/          # judge / diagnose / cluster / draft prompt templates (GLM 5.2)
  config.yaml       # caps, sampling, model id, kill switch defaults
  policy.md         # plain-English review rules/priorities, injected into prompts (D13)
  run.ps1           # entrypoint the scheduler calls
  tests/            # unit tests for guard, apply, extract, regression store
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
Harness.health ── aggregate per-tool success/error/timeout/empty (D11)
  │
  ▼
Harness.digest ── docs/manthan-review/YYYY-MM-DD.md
  │  (sessions / tool-health / found / auto-applied w/ proof / awaiting you / cost)
  ▼
Harness.mailer ── render digest as HTML → send via Gmail SMTP to maintainer (D10)
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

### 6.2 Judge (GLM 5.2 prompt)
- **Input:** one session record.
- **Behavior:** determine what the agent claimed; for each *checkable* claim,
  independently call the relevant MCP tool and compare. Confabulation, wrong-charger,
  and stale-number errors are caught here because the judge sees ground truth.
- **Output (structured JSON):** `{ verdict, checked_claims[], evidence[], severity }`.
- **Guardrail:** claims that cannot be verified via tools are marked `unverifiable`
  and never drive an auto-apply (they may still be reported).

### 6.3 Diagnose + Cluster (GLM 5.2 prompt)
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
analysis_model: glm-5.2      # GLM 5.2 (advanced) via z.ai — judge/diagnose/draft (D7)
target: yesterday            # or an explicit date
max_sessions_per_run: 100    # sample beyond this
max_autoapplies_per_run: 3   # nightly cap on auto-applied clusters
max_diff_lines: 15           # per-change instruction diff cap
regression_sample: 20        # regression entries replayed per verify
apply: on                    # overridden by MANTHAN_REVIEW_APPLY=off
github_repo: Quench-EV-Charger/noc-mcp-agent   # escalation PRs open here (D12)
escalation_channel: github_pr # draft PR per escalation; merge=approve, close=reject

email:                       # daily report (D10)
  enabled: true
  smtp_host: smtp.gmail.com
  smtp_port: 587             # STARTTLS
  from: manthan-noc@quenchchargers.com   # confirm sender (§9)
  to: [kmittal@quenchchargers.com]
  # app password read from GMAIL_APP_PASSWORD in .env — NEVER in this file
  send_when_empty: true      # still send a "clean night, nothing to report" note

health:                      # tool-health metrics (D11)
  degraded_error_rate: 0.20  # flag a tool if >20% of its calls failed/timed out
  timeout_seconds: 90        # matches CMS device-latency budget
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
4. **GLM 5.2 reasoning access (D7)** — confirm the exact z.ai model id for "GLM 5.2
   advanced," an API key/endpoint usable by the nightly job, and that GLM 5.2 supports
   binding the CMS/NOC MCP tools for the judge's re-verification calls (LibreChat
   already does this for the agents, so the path exists — confirm it outside LibreChat).
5. **GLM-5.2 judging quality (D7b)** — dry-run (`apply: off`) to confirm GLM 5.2 is a
   reliable judge before enabling auto-apply.
6. **Gmail send path (D10)** — confirm quenchchargers.com is Google Workspace, provision
   a **sender** (a dedicated address like `manthan-noc@` is cleaner than a personal one)
   and a **Gmail app password** (requires 2FA on that account), stored only in
   `manthan-review/.env`. Verify the Windows host can reach `smtp.gmail.com:587`.
7. **PR push access + merge→live (D12)** — provision an **SSH deploy key or `gh` token**
   on the host so the nightly job can push branches/PRs. Decide **merge→live for
   instruction PRs**: a GitHub Action on merge (near-immediate) vs. next-run detection
   (no CI, but go-live waits until the next night).

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

---

## 13. Daily Email Report & Tool Health (D10, D11)

### 13.1 Purpose
Deliver the daily digest to the maintainer's inbox so review needs no repo-diving,
and surface **tool health** next to answer **quality**. The email is the same digest
(§6.8) rendered as HTML — one artifact, two channels (git file + inbox).

### 13.2 Tool-health aggregation (`health.py`, D11)
- **Source:** every MCP tool call in the day's sessions (reconstructed by `extract.py`,
  §6.1) **plus** the judge's own re-query calls (§6.2) — the latter is a live probe.
- **Per tool** (`cms_*`, `noc_*`): count outcomes — success / error / timeout / **empty**
  (empty = HTTP 200 but no body, the known CMS content-type failure mode).
- **Degraded** if (error + timeout) rate > `health.degraded_error_rate`.
- A degraded tool is a **tool/code-class** finding → **escalated**, never auto-fixed
  (consistent with D1/D8). This is how "identify issues with the tool and improve it"
  works for infrastructure problems: surfaced to you with evidence, not silently patched.

### 13.3 Email content (Full health + quality)
1. **Header** — date, total sessions, one-line status.
2. **Session summary** — counts by agent; any sessions that errored.
3. **Tool health** — per-tool table (calls / errors / timeouts / empty / status);
   degraded tools called out at the top.
4. **Quality review** — issues found, by severity.
5. **Improvements** — auto-applied (before/after + verify proof + commit hash) and
   awaiting-you (escalated patches + links).
6. **Feature proposals** (§12).
7. **Cost** — tokens / model calls spent.

### 13.4 Send path (`mailer.py`, D10)
- Renders `email_template.html`; sends via **Gmail SMTP** (`smtp.gmail.com:587`,
  STARTTLS) authenticated with `GMAIL_APP_PASSWORD` from `.env`.
- Runs as the **final** step, after `digest.py`. **Non-blocking:** a send failure is
  logged and retried once; it never fails the run (the git digest stays the source of
  truth).
- `send_when_empty: true` still sends a short "clean night — nothing to report" note,
  so a silent inbox means *"the job didn't run"*, not *"nothing happened"*.

### 13.5 Secrets
The Gmail app password lives **only** in `manthan-review/.env` (gitignored, per repo
secrets policy). Never in `config.yaml`, never committed, never logged. Requires 2FA
on the sender account to mint an app password (§9 item 6).

### 13.6 Testing
- `health.py` unit tests: outcome classification (esp. empty-body detection) and the
  degraded threshold.
- `mailer.py` test: render + send against a local capture (e.g. write the `.eml` to
  disk or a dev SMTP sink) — no real send in tests.
- End-to-end dry-run: a full nightly run with `apply: off` produces a digest file and
  a well-formed email to the maintainer.

---

## 14. Approval Workflow — Escalations (D12)

### 14.1 Scope
Only the **escalated set** needs approval: tool/code fixes, and instruction edits that
failed the allow-list (§6.4) or the verify gate (D5). Allow-listed instruction fixes
that *passed* verify are auto-applied and merely reported — not part of this flow.
Degraded-tool health alerts are **email-only** (D11) — no PR, nothing to merge.

### 14.2 Mechanism
1. **Notify** — the daily email's "Awaiting you" section lists each escalation: id,
   type, why it was escalated, evidence (failing session), the proposed diff, and the
   PR link.
2. **Stage** — `pr.py` pushes each escalation as a branch
   (`manthan-auto/<date>-<slug>`) and opens a **draft PR** on
   `Quench-EV-Charger/noc-mcp-agent`. A copy of the patch is also written to
   `proposals/` for local reference.
3. **Decide (human):**
   - **Merge = approve.** For an *instruction* change, merge triggers
     `restore-agents.ps1` (a GitHub Action on merge, or detection at the next run's
     start — §9 item 7) to push it live to Mongo. For a *code* change, merge → rebuild
     / redeploy the affected MCP container (normal deploy).
   - **Close = reject.** Recorded so the loop does not re-propose the same cluster.
4. **No auto-merge, ever** — draft PRs never merge themselves; production is untouched
   until a human merges.

### 14.3 Prerequisites
- **Push access** for the nightly job — an SSH deploy key or `gh` token on the Windows
  host, since `origin` is `git@github.com:Quench-EV-Charger/noc-mcp-agent.git`.

### 14.4 Decision memory
Merges and rejections are recorded in `state/`, keyed by root-cause signature, so the
loop learns your calls — it neither re-proposes a rejected change nor re-opens a merged
one (dedup).

---

## 15. Human Steering & Input Channels (D13)

You extend Hermes through three channels, ordered by weight of change.

### 15.1 Steer what it reviews — `policy.md` (no code)
`manthan-review/policy.md` is plain-English guidance injected into the judge/diagnose
prompts each run. Use it for new rules and priorities — e.g. "flag prices quoted
without a date", "treat >30 s tool responses as issues", "watch customer ACME this
month". Edit → commit → effective next run. Being in git, every steering change is
auditable and revertible.

> **Not the same as agent instructions.** `policy.md` steers *Hermes's review*; it is
> distinct from the *agents'* `instructions` in `agents.json` (which are what Hermes
> edits). One tells the reviewer what to look for; the other is the thing being reviewed.

### 15.2 Lock a behavior — pinned regression cases (no code)
Add a question + expected answer to `regression/` and pin it (§6.7). Hermes enforces it
indefinitely and the verify gate (D5) blocks any change that breaks it. Use this for
hard "must always work" guarantees (e.g. the slot-filling success cases).

### 15.3 Add new machinery — code (brainstorm → spec → build)
New capabilities (a new email section, a new check type, a new data source) are code in
`manthan-review/` and go through the normal brainstorm → spec → build cycle, shipped as
a PR. Hermes is an ordinary component of the repo.

### 15.4 Self-improvement boundary
Hermes may *review and propose* improvements to its own code (it can read the repo), but
any change to `manthan-review/` is **escalated as a PR like other code changes** — never
auto-applied. It does not rewrite itself unattended.
