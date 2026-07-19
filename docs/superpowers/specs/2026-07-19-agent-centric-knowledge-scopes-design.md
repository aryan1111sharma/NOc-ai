# Agent-Centric Knowledge Scopes — Design Spec

**Date:** 2026-07-19
**Status:** Approved for planning
**Author:** kmittal@quenchchargers.com (with Claude Code)
**Related:** [Improvement program index](./README.md) · [EV knowledge base (RAG)](./2026-07-18-ev-knowledge-base-rag-design.md) — this spec extends that one; read it first.

---

## 1. Problem

The knowledge base shipped on 2026-07-18 is a **single flat corpus**: every document
lives in `knowledge/`, lands in one Qdrant collection, and every agent holding
`kb_search` can retrieve all of it. That is correct for one team of NOC agents sharing
one body of EV knowledge. It does not survive the next agent.

Planned agents span **business/ops functions** (HR, finance), **per-customer scopes**
(ChargeZone, Ecog), and **other technical domains** (BESS, firmware, CMS). A flat corpus
fails these three ways:

1. **Relevance** — an HR policy chunk can surface for a charger question, and vice versa.
   Retrieval quality degrades as unrelated corpora are added.
2. **Confidentiality** — HR/finance material and one customer's manuals become readable
   by every agent that holds the tool.
3. **Curation** — one owner's re-ingest touches every agent's knowledge.

**Goal:** let each agent see exactly the knowledge relevant to its purpose, so new agents
for new purposes can be added without diluting or exposing existing corpora.

**Why now:** the KB is live but holds only three synthetic sample documents. Restructuring
today costs one re-ingest. After real manuals are loaded it becomes a migration.

### Non-goals

- Not a knowledge-authoring or upload UI. Ingest stays a CLI over a folder (see §10).
- Not per-user or per-conversation scoping. The grant is per **agent**, not per person.
- No change to chunking, the embedding model, or the citation contract.
- Not multi-tenancy in the security-product sense. See the honest limits in §7.

---

## 2. Key Decisions (locked during brainstorming)

| # | Decision | Choice |
|---|----------|--------|
| D1 | **Primary motive** | **Relevance**, not hard isolation. Scopes exist so each agent retrieves from material relevant to its job. This permits a shared store and overlapping scopes. |
| D2 | **Isolation model** | **Mixed, opt-in per scope.** Filtering by default; a scope marked `confidential: true` gets its own physically separate Qdrant collection. Pay the isolation cost only where it matters. |
| D3 | **Scope assignment** | **Folder = scope.** A document's top-level folder under `knowledge/` determines its scope. No syntax for curators; works for PDFs, which front-matter cannot. |
| D4 | **Composition** | **Profiles bundle scopes.** An agent is granted one profile; a profile lists one or more scopes. Composition is resolved in config, never by the model. |
| D5 | **Enforcement point** | **Tool presence.** One MCP tool per profile, filters baked in server-side. LibreChat's only per-agent control is tool selection, and there is no reliable agent identity at the MCP layer — so a `scope` *parameter* could not be enforced and is deliberately not offered. |
| D6 | **Server shape** | **One `kb-mcp-server` instance**, generating N tools from config. One container, one embedding model in memory. A confidential scope may later be promoted to its own instance without disturbing the rest. |
| D7 | **Document identity** | **Path relative to `knowledge/`** replaces bare filename. Fixes an existing collision bug (§8) and improves citations. |

---

## 3. Architecture

```
knowledge/                              ← corpus root (docs gitignored)
  scopes.yaml                           ← config, TRACKED in git
  MANIFEST.md                           ← audit trail, TRACKED
  ev-core/ocpp/spec.pdf                 → scope "ev-core"
  chargezone/sla.pdf                    → scope "chargezone"
  hr/leave-policy.pdf                   → scope "hr" (confidential)
        │
        │  kb-ingest: derive scope → parse → chunk → embed → upsert
        ▼
┌─────────────────────┐        ┌────────────────────────────────────┐
│  kb-mcp-server      │        │  Qdrant                            │
│  - scopes.py        │◄──────►│  ev_kb   (all shared scopes,       │
│  - fastembed (BGE)  │        │           separated by `scope`)    │
│  - N generated tools│        │  kb_hr   (confidential scope)      │
└──────────┬──────────┘        └────────────────────────────────────┘
           │ MCP (http)
           ▼
   kb_search_noc         → scopes [ev-core]
   kb_search_chargezone  → scopes [ev-core, chargezone]
   kb_search_hr          → scopes [hr]  (own collection)
```

Files at the corpus **root belong to no scope and are skipped with a warning**. This makes
misplaced files visible and retires the `MANIFEST.md`-gets-indexed bug (§8) without a
special case.

### `knowledge/scopes.yaml`

Tracked in git via a `.gitignore` exception: the corpus stays private, the governance is
reviewable.

```yaml
scopes:
  ev-core:    { folder: ev-core }
  chargezone: { folder: chargezone }
  hr:         { folder: hr, confidential: true }

profiles:
  noc:
    scopes: [ev-core]
    description: "EV/OCPP protocol reference, charger specs, NOC runbooks."
  chargezone:
    scopes: [ev-core, chargezone]
    description: "EV/OCPP reference plus ChargeZone site manuals and SLAs."
  hr:
    scopes: [hr]
    description: "Internal HR policy."
    min_score: 0.55
```

`description` becomes the generated tool's docstring — it is how the agent learns what
that knowledge base contains and when reaching for it is appropriate. `min_score` is an
optional per-profile override of the global default (§6).

---

## 4. Components

One new module; four modified. Each has one responsibility and is testable alone.

| Module | Responsibility | Change |
|---|---|---|
| `kb_mcp/scopes.py` | Parse and validate `scopes.yaml` → `Scope` / `Profile` objects. No Qdrant or MCP dependency. | **new** |
| `kb_mcp/store.py` | Vector reads/writes. Adds a scope filter and `search_many()` across collections. | modified |
| `kb_mcp/ingest.py` | Derive scope from folder, key by relative path, route to the right collection. | modified |
| `kb_mcp/server.py` | Generate one MCP tool per profile with its filter baked in. | modified |
| `kb_mcp/config.py` | Add `scopes_file` path. Existing settings unchanged. | modified |

All files remain far below the 2000-line limit.

---

## 5. Data flow

**Ingest**

```
knowledge/ev-core/ocpp/spec.pdf
  → scope  "ev-core"                    (top-level folder)
  → source "ev-core/ocpp/spec.pdf"      (relative path = identity)
  → parse → chunk → embed
  → upsert into ev_kb
     payload {scope, source, location, text, doc_hash, chunk_idx}
```

Confidential scopes follow the same path into their own collection. Content-hash skipping
is unchanged, so re-ingest stays cheap.

Ingest remains a **single pass over the whole corpus**. Per-scope commands were considered
and rejected: hashing already makes unchanged files nearly free, and one command means a
curator cannot forget to re-index the scope they touched.

**Query**

```
agent calls kb_search_chargezone("SLA response time")
  → profile "chargezone" → scopes [ev-core, chargezone]
  → embed query
  → query ev_kb filtered to scope IN [ev-core, chargezone], limit=top_k
  → (plus each confidential collection the profile includes, limit=top_k)
  → merge → sort by score → drop below min_score → truncate to top_k
  → return chunks with source paths for citation
```

Each collection is queried with `limit=top_k`, then results are merged and truncated to
`top_k` overall. Over-fetching per collection is what allows one collection to supply all
`top_k` results when it genuinely holds the best matches — a global limit applied per
collection would instead reserve slots for weaker hits.

The model passes only `query` and optionally `top_k`. Scope is never a parameter, so it
cannot be hallucinated or argued with.

**The generic `kb_search` tool ceases to exist.** Every tool is generated from a profile;
there is no unscoped fallback, because an unscoped tool would defeat the entire design.

---

## 6. Error handling

**Governing principle:** an empty result means *"we do not have that knowledge."* Therefore
**nothing else may ever produce an empty result.** If an infrastructure failure returns
`[]`, the agent tells the user the material is not in the knowledge base — a confident,
wrong, unfalsifiable answer. This is the same class of defect as the `min_score` bug fixed
in `b16e272`.

| Situation | Behaviour | Rationale |
|---|---|---|
| `scopes.yaml` missing or malformed | **Server refuses to start**, naming the problem | A KB with zero scopes answers "not in the knowledge base" to everything while appearing healthy |
| Profile references an undeclared scope | Startup validation error | A typo would silently narrow an agent's knowledge |
| Duplicate scope or profile name | Startup validation error | Ambiguous grants are a governance problem |
| Declared scope folder absent on disk | Warn, keep running | Legitimate mid-setup state |
| **Qdrant unreachable / query fails** | **Tool raises** — never returns `[]` | "Search failed" must stay distinguishable from "no relevant knowledge" |
| Document parses to zero chunks | Counted and reported in ingest summary | Catches scanned PDFs, which otherwise appear loaded but hold nothing |
| Profile's scopes hold no documents | Returns empty; agent says not in KB | Correct — the knowledge genuinely is not there |

`min_score` keeps its global default (0.5) with an optional per-profile override. Not
speculative: the current value was calibrated on synthetic text with only a ~0.1 gap
between on-topic (0.51–0.83) and off-topic (0.41–0.42) scores, and a legal or HR corpus
will score differently from charger manuals.

---

## 7. Security posture — honest limits

D2 gives **two different strengths of separation**, and they should not be conflated:

- **Shared scopes** are separated by a Qdrant payload filter applied server-side. An agent
  cannot request another scope, because no parameter exists to request it. But the chunks
  are co-resident in one collection, so a defect in filter construction is a leak. The
  scope-leak test (§9) exists precisely to hold that line.
- **Confidential scopes** are separated by collection. Reaching one requires a tool that
  targets that collection, and no such tool is generated unless a profile names the scope.

Neither is a substitute for not loading material that must not be retrievable at all. A
knowledge base is a retrieval system, not an access-control system: anything ingested is
one well-phrased question away from being quoted to whoever holds the tool.

---

## 8. Bugs fixed as part of this work

Both exist in the shipped code today and both are made materially worse by folders.

1. **Filename collision** (`ingest.py`, `source = path.name`). Documents are keyed by bare
   filename, so `ev-core/README.md` and `hr/README.md` are treated as the same document —
   the second ingested silently deletes the first. Fixed by D7 (relative-path identity).
2. **`MANIFEST.md` indexed as knowledge.** It currently scores ~0.51, clearing the 0.5
   floor, so the agent can cite the corpus manifest as a source document. Fixed by the
   root-file skip rule (§3).

---

## 9. Testing

The suite must keep running **without a Qdrant container and without a model download** —
in-memory Qdrant plus the deterministic `FakeEmbedder`. The current 17 tests run in ~6s,
which is what makes them worth running constantly. Built test-first.

- **`scopes.py`** — valid config parses; undeclared scope reference raises; duplicate names
  raise; malformed YAML raises usefully; declared-but-missing folder warns without failing.
- **`store.py`** — scope-filtered search returns only granted scopes; `search_many` merges
  across collections ordered strictly by score, not by collection.
- **`ingest.py`** — scope derived from top-level folder; root files skipped; undeclared
  folders skipped; zero-chunk documents counted; prune operates per collection.
- **`server.py`** — one correctly-named tool per profile with its config description;
  `min_score` honoured including per-profile override.
- **Failure mode** — an unreachable store raises rather than returning empty (§6).

Two tests carry most of the weight and are named explicitly:

- **Collision regression** — seed `ev-core/README.md` and `hr/README.md` with different
  content; assert both survive with distinct sources. This bug exists today, fails
  silently, and folders make it likely.
- **Scope leak** — grant a profile only `ev-core`, seed a distinctive chunk into
  `chargezone`, run a query that strongly matches it, assert it does not return. This
  verifies the §7 claim rather than assuming it.

Existing tests either keep passing or are updated where the API genuinely changed.

---

## 10. Migration

Small today — the reason for doing it now.

1. Move the 3 sample docs into `knowledge/ev-core/`.
2. Add `scopes.yaml` with one scope (`ev-core`) and one profile (`noc`); add the
   `.gitignore` exception so it is tracked.
3. Drop and rebuild the `ev_kb` collection — 5 chunks, seconds.
4. Rewire the 3 MCP-enabled agents from `kb_search` to `kb_search_noc` and **restore to
   Mongo in the same step**. In `agents.json` these appear with LibreChat's server suffix,
   so the edit is `kb_search_mcp_kb` → `kb_search_noc_mcp_kb`. The KNOWLEDGE BASE
   instruction block added in `084555a` must be updated to name the new tool.

⚠️ Step 4 carries a known hazard: `agents.json` is regenerated from Mongo by
`export-agents`, so a file edit not promptly restored is silently dropped by any
concurrent export. Use the same idempotent anchor-based transform as commit `084555a`.

From the NOC agents' point of view the only change is the tool's name. Retrieval,
threshold, and citation contract are unchanged.

---

## 11. Open questions (resolve during planning or first real ingest)

- **Scope taxonomy for the first real corpus.** `ev-core` is certain; whether runbooks
  warrant their own scope depends on the documents.
- **`min_score` recalibration.** The synthetic corpus has only a ~0.1 signal/noise gap;
  real documents will move it, possibly per profile.
- **Scanned PDFs.** The parser extracts embedded text only. If real manuals are scans, OCR
  becomes a prerequisite. The zero-chunk report (§6) will reveal this on first ingest.
- **`.docx` / `.xlsx`.** Unsupported today. If NOC documentation lives in Word, a parser or
  a conversion step is needed.

---

## 12. Out of scope

- Admin/curator upload UI (discussed 2026-07-18; folder + CLI first, UI once curators and
  cadence are known).
- Repository/code ingestion — genuinely different problem needing syntax-aware chunking
  and likely a different embedding model.
- LibreChat's native `rag_api` + pgvector stack. Deliberately left off: it would create a
  second, uncurated knowledge pool with its own retrieval rules competing with this one.
- Per-user or per-conversation document upload.
