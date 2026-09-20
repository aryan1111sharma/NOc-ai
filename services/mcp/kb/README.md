# kb-mcp-server

Read-only RAG knowledge base over EV/SME documents (manuals, OCPP spec, runbooks),
exposed to the NOC agents as MCP tools. Self-hosted: a local BGE embedder
(fastembed, CPU) + a Qdrant vector store. Nothing leaves the host.

## Scopes and profiles

A document's **top-level folder** under `KNOWLEDGE_DIR` is its **scope**.
`knowledge/scopes.yaml` declares scopes and **profiles** (named bundles of scopes).
Each profile becomes one MCP tool, `kb_search_<profile>`, with its scope filter bound
server-side.

```yaml
scopes:
  ev-core:    { folder: ev-core }
  chargezone: { folder: chargezone }
  hr:         { folder: hr, confidential: true }   # own Qdrant collection

profiles:
  noc:
    scopes: [ev-core]
    description: "EV/OCPP reference, charger specs, NOC runbooks."
  chargezone:
    scopes: [ev-core, chargezone]                  # profiles compose scopes
    description: "EV reference plus ChargeZone manuals and SLAs."
    min_score: 0.55                                # optional per-profile override
```

An agent is granted knowledge by being given a profile's tool. **The model never passes
a scope and cannot request one** — there is no scope parameter. That is deliberate:
LibreChat's only per-agent control is which tools an agent holds, and there is no agent
identity at the MCP layer, so a `scope` argument could not be enforced.

Names (scopes and profiles) must match `^[A-Za-z0-9_-]+$` — they become tool names and
collection names, and an unsafe one fails silently downstream, so it is rejected at
startup instead. The `folder` field is **not** constrained and may contain spaces:
`{ folder: "EV Core (2026)" }`.

### Two strengths of separation

- **Non-confidential** scopes share the `ev_kb` collection, separated by a `scope`
  payload filter.
- **Confidential** scopes each get their **own collection** (`kb_<scope>`), so no filter
  defect can expose them.

Neither substitutes for not loading material that must never be retrievable. A knowledge
base is a retrieval system, not an access-control system: anything ingested is one
well-phrased question away from being quoted to whoever holds the tool.

## Tools

One per profile: `kb_search_<profile>(query, top_k=4, min_score=None)` — returns up to
`top_k` chunks, each with `source` (path relative to the corpus root), `location`, `text`,
and a relevance `score`. Read-only.

Internally, each collection is queried with a larger dense pool than `top_k`, then
re-ranked by fusing dense (cosine) rank with lexical (BM25) rank over that pool before
truncating to `top_k` — this recovers exact terms/IDs/error codes that rank low on
embedding similarity alone. `score` on a returned chunk is always the original dense
cosine score, never a fused score, so `min_score` filtering is unaffected by re-ranking.

**An empty list means the knowledge base does not cover the query**, and agents are
instructed to say so rather than confabulate. Nothing else may produce an empty list — a
search failure raises instead, so an outage can never masquerade as "we don't have that."

`min_score` resolution: explicit argument → the profile's pinned `min_score` →
`MIN_SCORE_DEFAULT`. Pass `min_score=0` to see every hit when debugging retrieval.

## Add knowledge

Drop `.pdf` / `.md` / `.markdown` / `.html` / `.htm` / `.txt` files into
`KNOWLEDGE_DIR/<scope>/` (subfolders below that are free), then ingest:

```
python -m kb_mcp.ingest            # index new/changed docs (idempotent, content-hashed)
python -m kb_mcp.ingest --prune    # also drop chunks for deleted files
```

In the running stack: `docker exec kb-mcp-server python -m kb_mcp.ingest --prune`

**Read the ingest summary** — it reports what was *not* indexed:

| Stat | Meaning |
|---|---|
| `skipped_root_files` | Files at the corpus root belong to no scope. Move them into a scope folder. |
| `skipped_unscoped_files` | Files under a folder no scope declares. Add the scope, or move the files. |
| `empty_docs` | Parsed to zero text. **Usually a scanned PDF** — the parser extracts embedded text only, so scans ingest as nothing. Needs OCR. |
| `skipped_docs` | Unchanged since last ingest (content hash matched). Normal. |

Unsupported extensions (`.docx`, `.xlsx`) are ignored silently and appear in no stat.

## Add an agent for a new purpose

1. Create `KNOWLEDGE_DIR/<new-scope>/` and add documents
2. Declare the scope and a profile in `scopes.yaml` (add `confidential: true` if the
   material warrants physical isolation)
3. Re-ingest, then restart the server so the new tool is registered
4. Grant `kb_search_<profile>` to the agent in LibreChat

## Config (env / `.env`)

Settings are pydantic-settings fields with **no env prefix**, so each env var is the
field name uppercased (`collection` → `COLLECTION`).

`QDRANT_URL`, `COLLECTION` (default `ev_kb`, the shared collection),
`EMBED_MODEL` (`BAAI/bge-base-en-v1.5`, 768-dim), `EMBED_DIM`,
`KNOWLEDGE_DIR` (default `/knowledge`), `SCOPES_FILE` (default `/knowledge/scopes.yaml`),
`MIN_SCORE_DEFAULT` (default `0.5`), `DEFAULT_TOP_K` (default `4`),
`CHUNK_MAX_CHARS`, `CHUNK_OVERLAP_CHARS`,
`MCP_TRANSPORT` / `MCP_HOST` / `MCP_PORT` (8106).

If `SCOPES_FILE` is missing or invalid the server **refuses to start**. That is
intentional: a knowledge base that silently came up with zero scopes would answer "not in
the knowledge base" to everything while appearing healthy.

`MIN_SCORE_DEFAULT` is calibrated per corpus. The current `0.5` was set against the
Phase-1 sample corpus (on-topic 0.51–0.83, off-topic 0.41–0.42) — a ~0.1 gap, so
**re-calibrate when real documents land**.

## Run

```
python -m kb_mcp                   # serves MCP (http on MCP_PORT, path /mcp)
```

## Test

```
python -m pytest                   # in-memory Qdrant + fake embedder; no model download
```

The suite deliberately needs no Qdrant container and no model download — keep it that way
(`fastembed` must never be imported at module scope).
