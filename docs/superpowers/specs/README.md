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
- **Spec:** [`2026-07-18-ev-knowledge-base-rag-design.md`](./2026-07-18-ev-knowledge-base-rag-design.md) ✅
- **Status:** spec done, ready to plan.
- **What:** new `kb-mcp-server` + **Qdrant** vector store + **local BGE embedder**
  (fastembed, in-boundary). Drop docs in `knowledge/` → `kb-ingest` → agents call a
  read-only `kb_search` tool that returns cited chunks. All 4 agents get it. Self-hosted
  + MCP so it's model-agnostic (GLM now / Claude later). Hermes's judge will reuse it.
- **Decisions locked:** self-hosted+MCP (D1), Qdrant (D2), local BGE via fastembed (D3),
  medium/evolving corpus (D4), all 4 agents (D5), fully in z.ai residency (D6).
- **Prereqs (spec §9):** confirm bge-base vs bge-small; corpus formats (OCR?); the first
  real doc set to ingest.
- **Size:** medium.

---

## Suggested sequence

1. **Slot-filling** — ✅ SHIPPED v0.8.0.
2. **Knowledge base** — spec done (2026-07-18); ready to plan. Biggest capability jump,
   gives Hermes's judge real domain ground truth.
3. **The loop (Hermes)** — spec done; needs external prereqs before planning.

_This ordering is a recommendation, not a constraint — features are largely independent._
