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
