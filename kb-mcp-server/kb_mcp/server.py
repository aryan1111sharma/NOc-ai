"""FastMCP server exposing the read-only kb_search tool."""
from fastmcp import FastMCP
from .config import settings


def build_server(store=None, embedder=None) -> FastMCP:
    if store is None or embedder is None:  # pragma: no cover - real wiring covered by Task 9
        from qdrant_client import QdrantClient
        from .embed import FastembedEmbedder
        from .store import KbStore
        store = store or KbStore(QdrantClient(url=settings.qdrant_url), settings.collection, settings.embed_dim)
        store.ensure_collection()
        embedder = embedder or FastembedEmbedder(settings.embed_model, settings.embed_dim)

    mcp = FastMCP(name="EV Knowledge Base (read-only RAG)")

    @mcp.tool
    async def kb_search(query: str, top_k: int = settings.default_top_k,
                        min_score: float | None = None) -> list[dict]:
        """Search the EV/charger knowledge base (manuals, OCPP spec, runbooks) for text
        relevant to the query. Returns up to top_k chunks, each with its source document,
        location, the matching text, and a relevance score (higher = closer). ALWAYS cite
        the returned `source` when you use a chunk. If this returns an empty list, the
        knowledge base does not cover the question — say so; do NOT invent an answer."""
        hits = store.search(embedder.embed([query])[0], top_k=top_k)
        if min_score is not None:
            hits = [h for h in hits if h["score"] >= min_score]
        return hits

    return mcp
