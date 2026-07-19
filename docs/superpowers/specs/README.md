# Manthan AI — Improvement Program (Design Specs Index)

A program of improvements to the Manthan NOC agent. Each feature follows
**brainstorm → spec → plan → build**. This index tracks all of them and their status.

_Last updated: 2026-07-19_

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
- **Status:** ✅ **BUILT & LIVE** (2026-07-18, commits `98c62be`..`084555a`). All 10 plan
  tasks done; 17 tests; `qdrant` + `kb-mcp-server` up; `kb_search` wired into the 3
  MCP-enabled agents. Corpus is still the 3 synthetic samples — real docs pending.
- **What:** new `kb-mcp-server` + **Qdrant** vector store + **local BGE embedder**
  (fastembed, in-boundary). Drop docs in `knowledge/` → `kb-ingest` → agents call a
  read-only `kb_search` tool that returns cited chunks. All 4 agents get it. Self-hosted
  + MCP so it's model-agnostic (GLM now / Claude later). Hermes's judge will reuse it.
- **Decisions locked:** self-hosted+MCP (D1), Qdrant (D2), local BGE via fastembed (D3),
  medium/evolving corpus (D4), all 4 agents (D5), fully in z.ai residency (D6).
- **Prereqs (spec §9):** confirm bge-base vs bge-small; corpus formats (OCR?); the first
  real doc set to ingest.
- **Size:** medium.

### 3a. Agent-centric knowledge scopes
- **Spec:** [`2026-07-19-agent-centric-knowledge-scopes-design.md`](./2026-07-19-agent-centric-knowledge-scopes-design.md) ✅
- **Status:** spec done, ready to plan. Extends feature 3.
- **What:** folder-per-scope corpus + `knowledge/scopes.yaml` defining scopes and
  **profiles**; the server generates one `kb_search_<profile>` tool per profile with its
  scope filter baked in server-side. Shared scopes are filtered within `ev_kb`; scopes
  marked `confidential` get their own collection. Lets future agents (HR, per-customer,
  other technical domains) each hold their own knowledge.
- **Decisions locked:** relevance-first (D1), mixed opt-in isolation (D2), folder=scope
  (D3), profiles compose scopes (D4), enforcement via tool presence (D5), single server
  instance (D6), relative-path document identity (D7).
- **Also fixes two live bugs:** bare-filename collision (`ev-core/README.md` vs
  `hr/README.md` overwrite each other) and `MANIFEST.md` being indexed as knowledge.
- **Size:** medium. Cheap now (3 sample docs); becomes a migration once real docs land.

---

## Suggested sequence

1. **Slot-filling** — ✅ SHIPPED v0.8.0.
2. **Knowledge base** — ✅ BUILT & LIVE (2026-07-18). Biggest capability jump; gives
   Hermes's judge real domain ground truth.
3. **Agent-centric scopes** — spec done (2026-07-19); ready to plan. Do this **before**
   loading real documents, while re-foldering is one re-ingest rather than a migration.
4. **The loop (Hermes)** — spec done; needs external prereqs before planning.

_This ordering is a recommendation, not a constraint — features are largely independent._
