"""FastMCP server exposing one read-only kb_search tool per configured profile."""
import logging

from fastmcp import FastMCP

from .config import settings
from .identity import parse_caller
from .parse import SUPPORTED
from .scopes import Profile, ScopesConfig
from .store import search_many

log = logging.getLogger(__name__)

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


def build_server(config: ScopesConfig | None = None, registry=None, embedder=None,
                 writer=None, headers_source=None) -> FastMCP:
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

    if writer is None:  # pragma: no cover - real wiring
        writer = build_writer(settings, config, registry, embedder)
    if writer is not None:
        _register_writes(mcp, writer, headers_source)

    return mcp


def build_writer(settings, config: ScopesConfig, registry, embedder):
    """Assemble the KbWriter, or return None if uploads are not configured.

    Returning None rather than raising is deliberate: a KB server with no
    bucket must still serve search. The write tools simply do not appear,
    which is a clearer signal — to a person and to a model — than tools that
    exist and always fail.
    """
    if not settings.s3_bucket:
        log.warning("S3_BUCKET is unset — upload tools disabled, search still available")
        return None
    if settings.contributed_scope not in config.scopes:
        log.warning(
            "contributed scope %r is not declared in scopes.yaml — upload tools disabled",
            settings.contributed_scope,
        )
        return None

    from .s3 import S3Corpus
    from .writes import KbWriter

    try:
        import boto3  # lazy: tests must never need AWS libraries at import time

        client = boto3.client("s3", region_name=settings.aws_region)
    except Exception:  # noqa: BLE001 - any AWS-side failure degrades, never crashes
        # Uploads are optional; search is not. A malformed AWS_REGION or a
        # broken boto3 install would otherwise propagate out of build_server
        # and crash-loop the container, taking search down because an OPTIONAL
        # write dependency is misconfigured — the opposite of what the
        # docstring above promises. Log loudly and register no write tools.
        log.exception("could not build the S3 client — upload tools disabled, search still available")
        return None

    return KbWriter(
        corpus=S3Corpus(client, settings.s3_bucket),
        registry=registry, embedder=embedder,
        scope=settings.contributed_scope,
        # Deliberately NOT contributed_dir: a cache inside the walked corpus
        # tree gets indexed a second time by the next ingest pass.
        cache_dir=settings.upload_cache_dir,
        contributor_roles=settings.contributor_roles,
        max_upload_mb=settings.kb_max_upload_mb,
        max_chars=settings.chunk_max_chars,
        overlap=settings.chunk_overlap_chars,
    )


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


def _caller_from(headers_source):
    """Resolve the caller for the current request.

    `get_http_headers()` returns {} when there is no HTTP request at all, and
    parse_caller turns that into a refusal — a missing identity is never an
    implicit grant.
    """
    if headers_source is None:  # pragma: no cover - real wiring
        from fastmcp.server.dependencies import get_http_headers

        headers_source = get_http_headers
    return parse_caller(headers_source())


def _register_writes(mcp: FastMCP, writer, headers_source) -> None:
    accepted = ", ".join(sorted(SUPPORTED))

    @mcp.tool(
        name="kb_upload_document",
        description=(
            "Add a document the user has ALREADY attached to this chat into the knowledge "
            "base, making it searchable by everyone who can search this KB. Pass the "
            f"file's name exactly as attached. Accepted types: {accepted}. Requires a "
            "contributor role; the caller's identity comes from the request, not from you, "
            "so do not ask the user for it and do not claim a role on their behalf. If the "
            "result says `replaced` is true, tell the user their upload replaced an "
            "existing document of the same name. Spreadsheets index well when they are "
            "textual (issue lists, test matrices, notes); for live numeric fleet or charger "
            "data prefer the NOC and CMS tools, which query current systems instead of a "
            "stale export."
        ),
    )
    async def kb_upload_document(filename: str) -> dict:
        return writer.upload(_caller_from(headers_source), filename)

    @mcp.tool(
        name="kb_remove_document",
        description=(
            "Remove a previously uploaded document from the knowledge base by its `source` "
            "value (as shown by kb_list_documents), deleting both the stored file and its "
            "indexed text. Only uploaded documents can be removed; curated material is "
            "managed in git. Requires a contributor role."
        ),
    )
    async def kb_remove_document(source: str) -> dict:
        return writer.remove(_caller_from(headers_source), source)

    @mcp.tool(
        name="kb_list_documents",
        description=(
            "List the documents users have uploaded into the knowledge base, with who "
            "added each one, when, and how many chunks it produced. Read-only; any user "
            "may call it. Use it to check whether something is already loaded before "
            "uploading, and to find the `source` value kb_remove_document needs."
        ),
    )
    async def kb_list_documents() -> list[dict]:
        return writer.list_documents(_caller_from(headers_source))
