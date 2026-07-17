# Manthan AI — Improvement Program (Design Specs Index)

A program of improvements to the Manthan NOC agent. Each feature follows
**brainstorm → spec → plan → build**. This index tracks all of them and their status.

_Last updated: 2026-07-17_

---

## Features

### 1. Clarify-before-invoke (slot-filling)
- **Spec:** [`2026-07-17-clarify-before-invoke-slot-filling-design.md`](./2026-07-17-clarify-before-invoke-slot-filling-design.md) ✅
- **Status:** ready to plan.
- **What:** prompt-only policy for all 4 agents — ask the 1–2 missing questions *only
  when a required identifier is missing* (e.g. "what session failed?"), never on
  already-specific or fleet-wide queries.
- **Size:** small (instruction edit + `restore-agents` round-trip).

### 2. Self-improvement loop ("Hermes")
- **Spec:** [`2026-07-17-manthan-self-improvement-loop-design.md`](./2026-07-17-manthan-self-improvement-loop-design.md) ✅
- **Status:** ready to plan; has external prerequisites (§9): GLM 5.2 access, Gmail app
  password, SSH/`gh` push access, headless `claude -p`.
- **What:** nightly local loop — review sessions → verify via MCP (GLM 5.2) →
  auto-apply allow-listed instruction fixes (verify-before-apply + git rollback) →
  aggregate tool health → email digest → escalate code fixes as PRs (drafted by local
  Claude Code) → human merge. Steered by `policy.md`; propose-only feature discovery.
- **Size:** large.

### 3. EV / SME knowledge base (RAG `kb_search` MCP tool)
- **Spec:** ⬜ **not yet — needs its own brainstorm.**
- **Status:** requirement captured (document corpus: manuals, OCPP, runbooks).
- **What (intended):** ingest a document corpus → chunk + embed → local vector store →
  a `kb_search` MCP retrieval tool. Used by **the agent** (to answer domain questions)
  and by **Hermes's judge** (to verify domain claims). New machinery — Hermes may
  *propose* it and flag KB gaps, but it is built deliberately, not auto-built.
- **Open questions for its brainstorm:**
  - Embedding model + **residency** — z.ai embeddings vs. a fully-local embedder (must
    respect the z.ai boundary).
  - Vector store — Chroma / pgvector / Qdrant / LanceDB, running in the `chat` Docker network.
  - New `kb-mcp-server` vs. extending the existing `mcp-server`.
  - Ingestion + chunking strategy; re-index trigger; supported doc formats/sources.
  - Which agents get the `kb` tool; **citation/grounding** to avoid confabulation (ties
    to the existing anti-confabulation guardrails).
  - How Hermes's judge queries it, and KB-gap detection via feature-discovery (§12).
- **Size:** medium–large.

---

## Suggested sequence

1. **Slot-filling** — quick win, prompt-only, immediate user-facing value; becomes the
   loop's first regression case.
2. **Knowledge base** — the biggest capability jump for users, and it gives Hermes's
   judge real domain ground truth.
3. **The loop (Hermes)** — maintains and regression-tests the other two; most useful
   once there's behavior + knowledge to review. (Can also be built earlier to start
   capturing quality data — maintainer's call.)

_This ordering is a recommendation, not a constraint — features are largely independent._
