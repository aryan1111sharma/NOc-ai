# Clarify-Before-Invoke (Slot-Filling) — Design Spec

**Date:** 2026-07-17
**Status:** Approved for planning
**Author:** kmittal@quenchchargers.com (with Claude Code)
**Related:** [Manthan Self-Improvement Loop](./2026-07-17-manthan-self-improvement-loop-design.md) — the loop maintains and regression-tests this behavior.

---

## 1. Problem

Manthan's NOC agents invoke tools **immediately**, even on underspecified queries.
The current instructions actively push this — they tell the agent to retry searches
with shorter tokens, paginate, and try `offline_only`, but never to *scope a vague
query first*. So a generic ask like **"What is the session which was failed?"**
(no charger, no customer, no time window) makes the agent guess and blind-search
instead of asking the one or two questions that would make it answerable.

**Goal:** before calling any tool, the agent checks whether the query contains the
identifiers needed to answer it. If a **required** identifier is missing, it asks —
in one short message — rather than guessing. Once the query is specified enough, the
existing guardrails (charger lookup, data-source routing) apply unchanged.

### Non-goals
- No new tools or code — this is a **prompt-only** policy (see D2).
- Does not change anti-confabulation or CMS-vs-NOC routing behavior — it runs
  *before* tool use and coexists with them.
- Does not add a clarify step to queries that are already specific, nor to
  legitimately fleet-wide queries.

---

## 2. Key Decisions (locked during brainstorming)

| # | Decision | Choice |
|---|----------|--------|
| D1 | **Ask trigger** | Ask **only when a required identifier is missing**. If enough is present, proceed straight to tools. Minimal friction. |
| D2 | **Implementation** | **Prompt-only** — a "required-slots" policy in each agent's `instructions` (`agents.json`), shipped via the existing `restore-agents.ps1` round-trip. No code, no new tools. |
| D3 | **Agent scope** | **All 4 agents** (NOC L1 Support `glm-5.2`, NOC Engineer `glm-4.7`, both `NOC server agent` variants `glm-4.7` / `glm-5-turbo`). |
| D4 | **Maintenance** | The self-improvement loop tunes and regression-tests this policy over time. A tool-assisted planner is deferred unless the loop shows soft adherence is insufficient. |

---

## 3. Required-Slots Map

The agent classifies the query's intent and checks for the minimum identifiers
needed. Missing → ask; present → proceed.

| Request type | Required to proceed | If missing, ask… |
|---|---|---|
| **Per-charger info** (status, firmware, telemetry, health) | a charger id/model/location **OR** a customer/site name | "Which charger (id) or customer/site?" |
| **Failed sessions / errors / transactions** | a charger **OR** customer, **AND** a time window | "Which charger or customer?" **+** "What time range (today / last 24h / a date)?" |
| **Tickets** | a ticket id **OR** a charger/customer **OR** a status + time window | the narrowest missing identifier |
| **Fleet-wide counts / totals** (how many chargers, online vs offline) | **none** — answer directly, default to the legacy CMS | **never asks** (key exception — avoids nagging on global queries) |

The fleet-wide exception is deliberate and important: it prevents the policy from
over-triggering on queries that are *supposed* to be broad.

---

## 4. Behavioral Rules

1. **Check before tools.** The slot check happens *before* any tool invocation.
2. **Batch, don't interrogate.** Ask the 1–2 missing slots together in a single
   short message. Never one-at-a-time, never more than needed.
3. **Never re-ask.** If the user already supplied a slot, do not ask for it. If the
   query is already specified, go straight to tools with no clarifying turn.
4. **Escape hatch — degrade, don't loop.** If the user cannot give a detail
   ("I don't know the charger" / "show all failures today"), proceed with the
   **broadest safe query** for what they *did* give, **state the scope assumed**,
   and offer to narrow. The agent must never loop asking the same question.
5. **Coexistence.** Once slots are satisfied, the existing CHARGER LOOKUP guardrail
   (literal substring match, retry with shorter tokens, hyphens-not-spaces, never
   substitute another charger) and the CMS-vs-NOC routing apply unchanged.

---

## 5. Deliverable — Instruction Block

The following block is inserted into each agent's `instructions` (immediately
**before** the existing `CHARGER LOOKUP` section, so "once you have enough, follow
the CHARGER LOOKUP rules below" resolves correctly):

> **CLARIFY BEFORE ACTING:** Before calling any tool, check that the query names
> what you need to answer it. If a required identifier is missing, ASK for it in one
> short message (the 1–2 key questions) instead of guessing or searching blindly.
> Required by request type: *per-charger info* (status, firmware, telemetry, health)
> → a charger id/model/location or a customer/site; *failed sessions / errors /
> transactions* → a charger or customer AND a time window (e.g. today, last 24h, a
> date); *tickets* → a ticket id, or a charger/customer, or a status plus a time
> window; *fleet-wide counts/totals* (how many chargers, online vs offline) → none,
> answer directly (default to the legacy CMS). If the user cannot give a detail
> ("I don't know the charger" / "show all today"), proceed with the broadest safe
> query for what they gave, state the scope you assumed, and offer to narrow. Never
> ask more than needed, never re-ask what was already provided, and never loop. Once
> you have enough, follow the CHARGER LOOKUP rules below.

**Size check:** current instructions are ~1000–1400 chars; this block adds ~900,
keeping every agent well under any practical limit.

---

## 6. Rollout

1. Edit the `instructions` field of all 4 agents in `chat/agents/agents.json`
   (insert the block before `CHARGER LOOKUP`).
2. `git commit` the change (audit + one-step rollback via `git revert`).
3. Run `restore-agents.ps1` to push it live to Mongo.
4. Validate in the LibreChat UI with the success-criteria prompts (§7), then via the
   self-improvement loop's regression set.

Because the two `NOC server agent` variants share the same base block, the same
insert applies uniformly to all four.

---

## 7. Success Criteria

Given the policy is live, the agent should:

| Prompt | Expected |
|---|---|
| "What is the session which was failed?" | **Ask** — which charger/customer? + what time range? (no premature tool call) |
| "Status of QF-1023?" | **Do NOT ask** — a charger is named; proceed to tools. |
| "How many chargers are online?" | **Do NOT ask** — answer directly via CMS. |
| "Failures for ACME today" | **Do NOT ask** — customer + time window present; proceed. |
| "Show all failures today" (after being asked for a charger) | **Degrade** — broadest safe query, state scope, offer to narrow; do not loop. |

---

## 8. Testing Strategy

- **Manual UI pass:** run the §7 prompt matrix against each of the 4 agents in
  LibreChat; confirm ask/no-ask/degrade behavior.
- **Loop regression cases:** add the §7 prompts as pinned regression entries in the
  self-improvement loop (`manthan-review/regression/`) so future instruction changes
  can't silently regress this behavior — the loop's judge checks that the agent
  asked vs. acted appropriately.
- **Coexistence check:** confirm a specified charger query (e.g. "status of QF-1023")
  still triggers the existing lookup/retry guardrails unchanged.

---

## 9. Interaction with the Self-Improvement Loop

This feature is the first real exercise of the loop's two roles:
- **Corrective:** if the model fires tools prematurely despite the policy, that's an
  instruction-class failure the loop can detect and reinforce (an additive guardrail
  edit) — within its allow-list.
- **Feature-discovery (propose-only):** if the loop's data later shows soft prompt
  adherence is consistently insufficient, it *proposes* the deferred tool-assisted
  planner (D4) as a backlog item — it does not build it.
