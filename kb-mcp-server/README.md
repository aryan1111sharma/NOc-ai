# kb-mcp-server

Read-only RAG knowledge base over EV/SME documents (manuals, OCPP spec, runbooks),
exposed to the NOC agents as an MCP tool. Self-hosted: a local BGE embedder
(fastembed, CPU) + a Qdrant vector store. Nothing leaves the host.

## Tool
- `kb_search(query, top_k=5, min_score=None)` — returns up to `top_k` chunks, each with
  `source`, `location`, `text`, and a relevance `score`. Read-only. Empty list = the
  knowledge base doesn't cover the query (agents must say so, not confabulate).

## Config (env / `.env`, see `.env.example`)
`QDRANT_URL`, `KB_COLLECTION` (default `ev_kb`), `EMBED_MODEL` (`BAAI/bge-base-en-v1.5`,
768-dim), `EMBED_DIM`, `KNOWLEDGE_DIR` (default `/knowledge`), `MCP_TRANSPORT`/`MCP_HOST`/
`MCP_PORT` (8106).

## Add knowledge
Drop `.pdf` / `.md` / `.html` / `.txt` files into `KNOWLEDGE_DIR`, then ingest:
```
python -m kb_mcp.ingest            # index new/changed docs (idempotent)
python -m kb_mcp.ingest --prune    # also drop chunks for deleted files
```

## Run
```
python -m kb_mcp                   # serves MCP (http on MCP_PORT, path /mcp)
```

## Test
```
python -m pytest                   # in-memory Qdrant + fake embedder; no model download
```
