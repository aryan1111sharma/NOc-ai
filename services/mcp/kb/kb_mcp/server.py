"""FastMCP server exposing one read-only kb_search tool per configured profile."""
from fastmcp import FastMCP

from .config import settings
from .scopes import Profile, ScopesConfig
from .store import search_many

_CONTRACT = (
    "Returns up to top_k chunks, each with its source document, location, the matching "
    "text, and a relevance score (higher = closer). ALWAYS cite the returned `source` "
    "when you use a chunk. If this returns an empty list, the knowledge base does not "
    "cover the question — say so; do NOT invent an answer. Hits below min_score are "
    "dropped as noise; omit min_score for the calibrated default, or pass 0 to see every "
    "hit when debugging retrieval."
)


def _description(profile: Profile) -> str:
    return f"Search the '{profile.name}' knowledge base. {profile.description} {_CONTRACT}"


def build_server(config: ScopesConfig | None = None, registry=None, embedder=None) -> FastMCP:
    """config, registry, and embedder are independent injection points: any
    subset may be supplied (as tests do) and the rest are wired for real. Each
    is gated on its own `is None` so passing two of three doesn't drag in the
    third's real wiring too."""
    if config is None:  # pragma: no cover - real wiring
        from .scopes import load_scopes, warn_missing_folders

        config = load_scopes(settings.scopes_file)
        warn_missing_folders(config, settings.knowledge_dir)

    if registry is None:  # pragma: no cover - real wiring
        from qdrant_client import QdrantClient

        from .store import StoreRegistry

        registry = StoreRegistry(
            QdrantClient(url=settings.qdrant_url), config,
            settings.collection, settings.embed_dim,
        )
        registry.ensure_all()

    if embedder is None:  # pragma: no cover - real wiring
        from .embed import FastembedEmbedder

        embedder = FastembedEmbedder(settings.embed_model, settings.embed_dim)

    mcp = FastMCP(name="EV Knowledge Base (read-only RAG)")

    for profile in config.profiles.values():
        _register(mcp, profile, registry, embedder)

    _register_list_scopes(mcp, config)
    _register_ingest_status(mcp, config, registry)

    return mcp


def _register(mcp: FastMCP, profile: Profile, registry, embedder) -> None:
    """Bind the profile's scope filter server-side. The model never passes a
    scope, so it cannot request knowledge the agent was not granted."""
    pairs = registry.group(profile.scopes)

    async def kb_search(query: str, top_k: int = settings.default_top_k,
                        min_score: float | None = None) -> list[dict]:
        floor = min_score
        if floor is None:
            floor = profile.min_score if profile.min_score is not None else settings.min_score_default
        return search_many(pairs, embedder.embed([query])[0], top_k=top_k, min_score=floor)

    mcp.tool(name=f"kb_search_{profile.name}", description=_description(profile))(kb_search)


_LIST_SCOPES_DESCRIPTION = (
    "List the knowledge scopes and search profiles currently configured (from "
    "scopes.yaml): each scope's name, source folder, and whether it is confidential; "
    "and each profile's name, description, min_score, and which scopes it searches. "
    "Read-only — reports configuration shape only, never document contents. Use this to "
    "see which kb_search_<profile> tool covers a given topic, or to check why a search "
    "came back empty."
)


def _register_list_scopes(mcp: FastMCP, config: ScopesConfig) -> None:
    """One global, config-only tool (not per-profile): the scopes/profiles shape is not
    sensitive beyond what list_tools() already reveals via each kb_search_<profile>
    tool's own name and description."""

    def kb_list_scopes() -> dict:
        return {
            "scopes": [
                {"name": s.name, "folder": s.folder, "confidential": s.confidential}
                for s in config.scopes.values()
            ],
            "profiles": [
                {
                    "name": p.name,
                    "description": p.description,
                    "scopes": list(p.scopes),
                    "min_score": p.min_score,
                }
                for p in config.profiles.values()
            ],
        }

    mcp.tool(name="kb_list_scopes", description=_LIST_SCOPES_DESCRIPTION)(kb_list_scopes)


_INGEST_STATUS_DESCRIPTION = (
    "Report what is currently indexed, per underlying vector-store collection: which "
    "scopes route to it, whether the collection has been created yet, and how many "
    "distinct source documents it holds. Reads live index state only — it does NOT run "
    "or trigger ingestion; re-indexing is a separate, human-run operation "
    "(`python -m kb_mcp.ingest`)."
)


def _register_ingest_status(mcp: FastMCP, config: ScopesConfig, registry) -> None:
    def kb_ingest_status() -> dict:
        scopes_by_collection: dict[str, list[str]] = {}
        for name in config.scopes:
            store = registry.for_scope(name)
            scopes_by_collection.setdefault(store.collection, []).append(name)

        collections = []
        for store in registry.stores():
            exists = store.c.collection_exists(store.collection)
            collections.append({
                "collection": store.collection,
                "scopes": sorted(scopes_by_collection.get(store.collection, [])),
                "indexed": exists,
                "document_count": len(store.known_sources()) if exists else 0,
            })
        return {"collections": sorted(collections, key=lambda c: c["collection"])}

    mcp.tool(name="kb_ingest_status", description=_INGEST_STATUS_DESCRIPTION)(kb_ingest_status)
