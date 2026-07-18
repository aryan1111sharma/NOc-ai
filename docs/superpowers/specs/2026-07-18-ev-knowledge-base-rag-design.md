# EV / SME Knowledge Base (RAG) — Design Spec

**Date:** 2026-07-18
**Status:** Approved for planning
**Author:** kmittal@quenchchargers.com (with Claude Code)
**Related:** [Improvement program index](./README.md) · [Self-improvement loop](./2026-07-17-manthan-self-improvement-loop-design.md) (its judge will reuse `kb_search`; feature-discovery flags KB gaps).

---

## 1. Problem

The Manthan NOC agents know *live* charger data (via the CMS/NOC MCP tools) but not
*how chargers work* — OCPP semantics, error-code meanings, hardware manuals, and
operational runbooks. So domain questions ("what does OCPP error X mean?", "the
procedure for a stuck connector") get guessed or declined.

**Goal:** give the agents a **retrievable knowledge base** of EV/SME documents.
Documents are ingested (chunked + embedded) into a local vector store; a read-only
`kb_search` MCP tool retrieves the most relevant chunks (with citations) at query
time, and the agents answer *from* those chunks. Adding knowledge = dropping a file
in and re-ingesting — no model retraining.

### Non-goals
- No model fine-tuning. Knowledge lives in the vector store, not model weights.
- No auto-answering beyond retrieved content — agents cite sources or say "not in the
  knowledge base" (anti-confabulation, §7).
- Not the self-improvement loop — that improves *behavior*; this adds *reference knowledge*.
- No document-authoring/editing UI in v1 — ingest is a CLI over a folder (YAGNI).

---

## 2. Key Decisions (locked during brainstorming)

| # | Decision | Choice |
|---|----------|--------|
| D1 | **Storage architecture** | **Self-hosted + MCP, model-agnostic.** Vector store + embedder live in the local Docker stack; agents reach the KB via an MCP tool. Works with GLM now and Claude later without moving the KB. Anthropic offers no turnkey document-RAG, so this is also the only turnkey path. |
| D2 | **Vector store** | **Qdrant** — dedicated vector DB container on the `chat` network; robust metadata filtering, scales past "medium". |
| D3 | **Embedder** | **Local open model** (`BAAI/bge-base-en-v1.5`, 768-dim) via **fastembed** (ONNX/CPU, no PyTorch bloat), run inside `kb-mcp-server`. Fully in-boundary — no external calls. |
| D4 | **Corpus profile** | **Medium & evolving** — dozens–hundreds of docs, updated regularly. Design for easy re-ingest. |
| D5 | **Agent coverage** | **All 4 agents** get the `kb_search` tool (registered as `kb`). |
| D6 | **Residency** | Local embedder + local Qdrant + local docs ⇒ **fully in the z.ai residency boundary**. No document or query text leaves the host. |
| D7 | **Server shape** | A **new `kb-mcp-server`** (Python), matching the repo's one-server-per-domain pattern (sibling to `cms-mcp-server`, `mcp-server`, …). |

---

## 3. Architecture

```
knowledge/  (mounted volume: PDF / Markdown / HTML / txt)   ← you drop docs here
    │
    │  kb-ingest  (CLI):  parse → chunk(+overlap) → embed → upsert
    ▼
┌────────────────────┐        ┌──────────────────────────────┐
│  kb-mcp-server      │  gRPC  │  Qdrant (container)           │
│  (Python, MCP/http) │◄──────►│  collection: ev_kb            │
│  - fastembed (BGE)  │        │  points: vector + payload     │
│  - kb_search tool   │        │  (source, location, text,     │
│  - ingest CLI       │        │   doc_hash, chunk_idx)        │
└─────────┬──────────┘        └──────────────────────────────┘
          │ MCP (http)
          ▼
   LibreChat agents  ── call kb_search(query, top_k) ──► cited chunks
```

- **`kb-mcp-server`** joins the `chat` Docker network (like the other MCP servers),
  serves MCP over http on a new port (e.g. `8106`), and is registered in
  `chat/librechat.yaml` as the `kb` server.
- **Qdrant** runs as its own container (default `6333`), storing one collection.
- The **embedder** runs *inside* `kb-mcp-server` (fastembed), used for BOTH ingestion
  and query embedding, so vectors are always produced the same way.

---

## 4. Components

### 4.1 Ingestion (`kb-ingest` CLI)
- **Input:** the `knowledge/` folder (a mounted volume, gitignored — manuals can be
  large/proprietary). A small tracked `knowledge/MANIFEST.md` records what's loaded.
- **Steps per file:** parse (PDF via PyMuPDF; Markdown/HTML/txt via lightweight
  parsers) → **chunk** (~512-token chunks, ~50-token overlap, respecting page/heading
  boundaries) → **embed** (fastembed BGE) → **upsert** into Qdrant with payload:
  `{ source (filename), location (page # or heading), text, doc_hash, chunk_idx }`.
- **Idempotent:** compute a content hash per document; skip unchanged docs, and for a
  changed doc delete its old points (by `source`) before re-inserting. Deletions:
  removing a file + re-ingesting with `--prune` drops its points.
- **Invocation:** `docker exec kb-mcp-server python -m kb_mcp.ingest [--prune]` (wrapped
  by a small `kb-ingest.ps1`/`.sh` for convenience). Runs on demand — no watcher (YAGNI).

### 4.2 Retrieval tool (`kb_search`)
- **Signature:** `kb_search(query: str, top_k: int = 5, min_score: float | None = None)`.
- **Behavior:** embed the query → Qdrant similarity search → return a list of
  `{ source, location, text, score }`, highest score first. **Read-only** — no writes,
  no ingestion side effects.
- **Empty result:** returns an empty list (not an error), so the agent can honestly say
  "not in the knowledge base."

### 4.3 Chunking
- Token-based windowing with overlap, boundary-aware (don't split mid-heading/table
  where avoidable). Overlap preserves context across chunk edges. Parameters live in
  config (§8) so they can be tuned per corpus without code changes.

---

## 5. How Knowledge Is Added (the "librarian" workflow)

1. Drop documents into `knowledge/` (PDF, Markdown, HTML, txt).
2. Run `kb-ingest`.
3. Done — the agents can now retrieve them. Update = replace the file + re-ingest;
   remove = delete the file + `kb-ingest --prune`. No retraining, ever.

---

## 6. Agent Integration

- Register the `kb` MCP server in `chat/librechat.yaml`.
- Add `kb` to each agent's `mcpServerNames` in `chat/agents/agents.json`, then push via
  `restore-agents.ps1`.
  - ⚠️ **Concurrency hazard (learned the hard way):** `agents.json` is regenerated from
    Mongo by `export-agents`; edit → **restore promptly**, or a concurrent export drops
    the change. Coordinate agent edits.
- Add a short instruction block to each agent: *use `kb_search` for how-it-works /
  procedure / spec / error-code questions; cite the source; if `kb_search` returns
  nothing, say the knowledge base doesn't cover it — do not invent an answer.*

---

## 7. Grounding & Anti-Confabulation

- Every retrieved chunk carries a **citation** (`source` + `location`); agents are
  instructed to cite what they use.
- If retrieval is empty, agents must **say so**, not guess — this extends the existing
  "never confabulate / only report what you actually found" guardrails already in the
  agents' instructions.
- Retrieval is **read-only** and cannot modify the KB, so a query can never corrupt it.

---

## 8. Configuration (`kb-mcp-server/config.yaml`)

```yaml
qdrant_url: http://qdrant:6333
collection: ev_kb
embed_model: BAAI/bge-base-en-v1.5   # 768-dim; bge-small-en-v1.5 (384) is faster/lighter
chunk_tokens: 512
chunk_overlap: 50
default_top_k: 5
knowledge_dir: /knowledge            # mounted volume
```

---

## 9. Open Decisions / Prereqs (resolve first in the plan)

1. **Confirm embed model + dimension (D3)** — `bge-base-en-v1.5` (768) vs `bge-small`
   (384): base = better quality, small = faster/less RAM. The choice fixes the Qdrant
   collection's vector size, so pick before creating the collection.
2. **Document parsing coverage** — confirm the real corpus's formats (scanned PDFs need
   OCR; native-text PDFs don't). Decide whether OCR is in scope for v1.
3. **Corpus (staged — see §11).** *Phase 1 needs nothing from the maintainer:* build and
   smoke-test the pipeline against a **sample corpus** (a public OCPP excerpt + a couple of
   synthetic runbook/spec docs). *Phase 2:* the maintainer drops **3–5 representative real
   docs** into `knowledge/` to validate retrieval quality and tune chunking.
4. **Reranking** — deferred (YAGNI). Add a cross-encoder reranker later only if
   top-k precision proves insufficient.
5. **Agent-instruction + `kb` registration edit** — apply mindful of the §6 concurrency
   hazard (restore immediately after editing `agents.json`).

---

## 10. Success Criteria

- `kb-mcp-server` + `qdrant` come up in `docker compose` and pass a health check.
- Ingesting a sample doc set populates the `ev_kb` collection; re-ingesting an unchanged
  set is a no-op (idempotency).
- `kb_search("<domain question>")` returns relevant chunks **with citations**, and an
  empty list for out-of-corpus queries.
- In LibreChat, an agent asked a doc-answerable question **calls `kb_search`**, answers
  with a citation, and says "not in the knowledge base" when nothing relevant is found.
- **No external network calls** occur during ingest or query (residency check).

---

## 11. Testing Strategy

**Two phases.** *Phase 1 (no maintainer data)* proves the pipeline against a **sample
corpus** built during implementation — a public OCPP excerpt plus 2–3 synthetic docs (a
runbook, an error-code table). *Phase 2 (maintainer supplies 3–5 real docs)* validates
retrieval quality on genuine questions and tunes chunking. Everything below runs in
Phase 1 except the real-corpus quality pass.

- **Unit:** chunking (sizes/overlap/boundaries), ingest idempotency (hash-skip and
  prune), `kb_search` output shape + score ordering + empty-result behavior.
- **Integration (seeded):** ingest a tiny fixture corpus (a known OCPP snippet + a
  runbook) into an ephemeral Qdrant; assert `kb_search` returns the right chunk with the
  right citation, and returns empty for an unrelated query.
- **Residency test:** run ingest + a query with outbound network blocked; both succeed.
- **E2E:** via LibreChat, ask an agent a question answerable only from a loaded doc;
  confirm it retrieves + cites; ask an out-of-corpus question; confirm it declines.

---

## 12. Future (captured, not built here)

- **Hermes tie-in:** the self-improvement loop's judge calls `kb_search` to verify
  domain claims; its feature-discovery flags recurring questions with no KB coverage
  ("KB gaps") as propose-only backlog items.
- **Reranker / hybrid search** (BM25 + vector) if precision needs it.
- **Ingestion UI / auto-watch** if manual ingest becomes a bottleneck.
