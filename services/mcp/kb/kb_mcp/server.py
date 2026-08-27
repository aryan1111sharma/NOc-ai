"""FastMCP server exposing one read-only kb_search tool per configured profile.

Two planes are served from this one module, selected by `settings.kb_role`:

    "dept"   the per-department tools  kb_search_<p>, kb_upload_<p>, ...
    "admin"  the Super Admin control tools  kb_admin_*

They are separate processes on separate ports on purpose. Department agents talk
to the dept plane, which has no KB-administration tool to call - so "a department
head cannot create, re-assign or delete knowledge bases" is a property of what is
reachable, not only of who LibreChat's ACL lets through.

The dept plane re-reads the KB registry on an interval, so a KB created on the
admin plane appears here without a restart. That polling is what lets the two
planes stay separate processes while still sharing one source of truth.
"""
from __future__ import annotations

import logging
import threading

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

#: Tool-name prefixes owned by the dept plane's per-profile registration. A
#: reload removes exactly these before re-adding them, so a deleted KB's tools
#: disappear and the global tools (kb_list_scopes, kb_ingest_status) survive.
_PROFILE_TOOL_PREFIXES = ("kb_search_", "kb_upload_", "kb_delete_", "kb_documents_", "kb_reindex_")


def _description(profile: Profile) -> str:
    return f"Search the '{profile.name}' knowledge base. {profile.description} {_CONTRACT}"


class _Plane:
    """Mutable server state, so a KB can be added or removed while running.

    Everything derived from the config - the StoreRegistry, the Qdrant
    collections, the per-profile tools - is rebuilt together from a freshly
    loaded config. Rebuilding wholesale rather than diffing means a reload
    cannot leave a tool bound to a store the config no longer describes.
    """

    def __init__(self, mcp: FastMCP, config: ScopesConfig, store_registry, embedder):
        self.mcp = mcp
        self.config = config
        self.registry = store_registry
        self.embedder = embedder
        # Absent when tests inject a fake registry; reload() degrades to a
        # no-op rather than reaching for a Qdrant client that isn't there.
        self.client = getattr(store_registry, "client", None)
        self._lock = threading.Lock()
        self._stamp = _registry_stamp()

    def register_profiles(self) -> None:
        for profile in self.config.profiles.values():
            _register(self.mcp, profile, self.config, self.registry, self.embedder)

    def reload(self) -> dict:
        """Re-read config from disk and re-derive everything. Returns a summary."""
        from .scopes import load_effective_scopes, warn_missing_folders
        from .store import StoreRegistry

        if self.client is None:
            return {"profiles": sorted(self.config.profiles), "added": [], "removed": [],
                    "reloaded": False}

        with self._lock:
            config = load_effective_scopes(settings.scopes_file, settings.kb_registry_file)
            warn_missing_folders(config, settings.knowledge_dir)

            store_registry = StoreRegistry(
                self.client, config, settings.collection, settings.embed_dim,
            )
            store_registry.ensure_all()

            before = set(self.config.profiles)
            for name in before:
                for prefix in _PROFILE_TOOL_PREFIXES:
                    try:
                        self.mcp.remove_tool(f"{prefix}{name}")
                    except Exception:
                        # A profile without a writable scope never had write
                        # tools; removing them is expected to miss.
                        pass

            self.config = config
            self.registry = store_registry
            self.register_profiles()
            self._stamp = _registry_stamp()

            after = set(config.profiles)
            summary = {
                "profiles": sorted(after),
                "added": sorted(after - before),
                "removed": sorted(before - after),
            }
            if summary["added"] or summary["removed"]:
                log.info("reloaded knowledge bases: %s", summary)
            return summary

    def reload_if_changed(self) -> bool:
        if _registry_stamp() == self._stamp:
            return False
        self.reload()
        return True


def _registry_stamp() -> tuple:
    """(mtime, size) of the config inputs. Cheap enough to poll every few
    seconds, and size catches same-second rewrites that mtime alone misses."""
    from pathlib import Path

    out = []
    for path in (settings.kb_registry_file, settings.scopes_file):
        try:
            st = Path(path).stat()
            out.append((st.st_mtime_ns, st.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


def _start_watcher(plane: _Plane) -> None:  # pragma: no cover - timing dependent
    interval = settings.reload_interval_seconds
    if interval <= 0:
        return

    def loop() -> None:
        import time

        while True:
            time.sleep(interval)
            try:
                plane.reload_if_changed()
            except Exception as exc:
                # A registry that is temporarily invalid (mid hand-edit) must
                # not take down a server that is serving the previous config
                # perfectly well. Log and keep the last good state.
                log.warning("knowledge-base reload failed, keeping previous config: %s", exc)

    threading.Thread(target=loop, name="kb-registry-watcher", daemon=True).start()


def build_server(config: ScopesConfig | None = None, registry=None, embedder=None,
                 writer=None, headers_source=None) -> FastMCP:
    """config, registry, and embedder are independent injection points: any
    subset may be supplied (as tests do) and the rest are wired for real. Each
    is gated on its own `is None` so passing two of three doesn't drag in the
    third's real wiring too."""
    role = settings.kb_role.lower()

    if role == "admin":
        return _build_admin_server()

    if config is None:  # pragma: no cover - real wiring
        from .scopes import load_effective_scopes, warn_missing_folders

        config = load_effective_scopes(settings.scopes_file, settings.kb_registry_file)
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

    plane = _Plane(mcp, config, registry, embedder)
    plane.register_profiles()

    _register_list_scopes(mcp, plane)
    _register_ingest_status(mcp, plane)

    # Contributed uploads (S3). Additive and optional: with no bucket configured
    # build_writer returns None and these tools simply do not appear, which is a
    # clearer signal - to a person and to a model - than tools that always fail.
    # This is reached only on the dept plane; the admin plane returned above, so a
    # KB administrator still has no document-write tool on their endpoint.
    if writer is None:  # pragma: no cover - real wiring
        writer = build_writer(settings, config, registry, embedder)
    if writer is not None:
        _register_writes(mcp, writer, headers_source)

    mcp.kb_plane = plane  # test/introspection handle; FastMCP ignores unknown attributes
    _start_watcher(plane)

    return mcp


def _register(mcp: FastMCP, profile: Profile, config, registry, embedder) -> None:
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

    _register_manage(mcp, profile, config, registry, embedder)


_LIST_SCOPES_DESCRIPTION = (
    "List the knowledge scopes and search profiles currently configured (from "
    "scopes.yaml and the KB registry): each scope's name, source folder, and whether it "
    "is confidential; and each profile's name, description, min_score, and which scopes "
    "it searches. Read-only — reports configuration shape only, never document contents. "
    "Use this to see which kb_search_<profile> tool covers a given topic, or to check why "
    "a search came back empty."
)


def _register_list_scopes(mcp: FastMCP, plane: _Plane) -> None:
    """One global, config-only tool (not per-profile): the scopes/profiles shape is not
    sensitive beyond what list_tools() already reveals via each kb_search_<profile>
    tool's own name and description."""

    def kb_list_scopes() -> dict:
        config = plane.config
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


def _register_ingest_status(mcp: FastMCP, plane: _Plane) -> None:
    def kb_ingest_status() -> dict:
        config, registry = plane.config, plane.registry
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


_MANAGE_CONTRACT = (
    "Scope is bound server-side: this tool only ever touches the '{scope}' knowledge base, "
    "and there is no scope parameter, so another department's knowledge base cannot be "
    "reached through it. Uploadable types: .pdf .docx .xlsx .xlsm .xls .pptx .rtf (binary, send base64 in content_b64) and .md .markdown .txt .html .htm .csv .tsv .json .xml (text, send content)."
)


def _register_manage(mcp: FastMCP, profile, config, registry, embedder) -> None:
    """Register the write tools for `profile`, if it has an unambiguous write target.

    Same shape as kb_search: the scope is captured here and never accepted as an argument.
    A profile without exactly one confidential scope (e.g. one that only reads the shared
    ev-core corpus) intentionally gets no write tools rather than a guessed target.
    """
    from .manage import (
        ManageError, delete_document, list_documents, reindex, upload_document, writable_scope,
    )

    scope = writable_scope(profile, config)
    if scope is None:
        return

    contract = _MANAGE_CONTRACT.format(scope=scope.name)
    kw = dict(knowledge_dir=settings.knowledge_dir, config=config,
              registry=registry, embedder=embedder, settings=settings)

    def _guard(fn):
        """Surface a refusal as a readable result instead of an opaque tool error, so the
        model can relay the reason to the operator."""
        try:
            return fn()
        except ManageError as e:
            return {"error": str(e), "scope": scope.name}

    def kb_upload(filename: str, content: str = "", content_b64: str = "") -> dict:
        """Text formats pass `content`; binary formats (.pdf .docx .xlsx .xls .pptx .rtf)
        pass base64 in `content_b64`, because an MCP tool argument is a string."""
        return _guard(
            lambda: upload_document(
                filename, content, scope=scope, content_b64=content_b64, **kw
            )
        )

    def kb_delete(filename: str) -> dict:
        return _guard(lambda: delete_document(filename, scope=scope, **kw))

    def kb_documents() -> dict:
        return list_documents(scope=scope, knowledge_dir=settings.knowledge_dir)

    def kb_reindex() -> dict:
        return reindex(scope=scope, **kw)

    n = profile.name
    mcp.tool(
        name=f"kb_upload_{n}",
        description=(f"Upload (or replace) one text document in the '{n}' knowledge base and "
                     f"index it immediately. {contract}"),
    )(kb_upload)
    mcp.tool(
        name=f"kb_delete_{n}",
        description=(f"Delete one document from the '{n}' knowledge base and remove its "
                     f"chunks from the vector store. {contract}"),
    )(kb_delete)
    mcp.tool(
        name=f"kb_documents_{n}",
        description=(f"List the documents currently held in the '{n}' knowledge base. "
                     f"{contract}"),
    )(kb_documents)
    mcp.tool(
        name=f"kb_reindex_{n}",
        description=(f"Re-index the corpus after files were added or removed outside chat "
                     f"(e.g. a PDF copied into the folder). Idempotent: unchanged documents "
                     f"are skipped, not re-embedded. {contract}"),
    )(kb_reindex)


# ---------------------------------------------------------------------------
# Admin plane
# ---------------------------------------------------------------------------

_ADMIN_NOTE = (
    "Super Admin control-plane tool. Knowledge bases are configuration, not code: creating "
    "one writes a record to the KB registry on the /knowledge volume, and the scope, the "
    "Qdrant collection, the search profile and the kb_search_/kb_upload_ tools are all "
    "derived from that record automatically — no scopes.yaml edit, no image rebuild, no "
    "restart. Departments are read from the platform's departments.json and are never "
    "defined here."
)


def _admin_store_registry(config):  # pragma: no cover - real wiring
    from qdrant_client import QdrantClient

    from .store import StoreRegistry

    return StoreRegistry(
        QdrantClient(url=settings.qdrant_url), config, settings.collection, settings.embed_dim,
    )


def _build_admin_server(deps=None) -> FastMCP:
    """The admin plane deliberately registers no kb_search_* tool.

    It also never constructs an embedder: none of create/assign/delete/list
    needs one, and skipping it keeps this plane's cold start free of the
    several-minute model download the dept plane pays.
    """
    from . import admin
    from .registry import RegistryError

    mcp = FastMCP(name="EV Knowledge Base — administration (Super Admin)")

    deps = deps or {}
    make_store_registry = deps.get("store_registry_factory", _admin_store_registry)

    def _load_config():
        from .scopes import load_effective_scopes

        return load_effective_scopes(settings.scopes_file, settings.kb_registry_file)

    def _store_registry():
        """Built per call from the current config: the registry changes under
        this process, so a cached one would describe the KB set as it was at
        startup and silently miss the collection we just created."""
        try:
            return make_store_registry(_load_config())
        except Exception as exc:
            log.warning("vector store unavailable to admin plane: %s", exc)
            return None

    def _guard(fn):
        try:
            return fn()
        except RegistryError as e:
            return {"error": str(e)}

    common = dict(registry_file=settings.kb_registry_file,
                  departments_file=settings.departments_file)

    def kb_admin_list_departments() -> dict:
        """Department labels available to group a knowledge base under."""
        return _guard(lambda: admin.list_departments(**common))

    def kb_admin_list_kbs() -> dict:
        return _guard(lambda: admin.list_kbs(
            config=None, store_registry=_store_registry(), **common))

    def kb_admin_create_kb(name: str, department: str, description: str,
                           shared_scopes: list[str] | None = None,
                           min_score: float | None = None) -> dict:
        def run():
            result = admin.create_kb(
                name=name, department=department, description=description,
                shared_scopes=shared_scopes, min_score=min_score,
                knowledge_dir=settings.knowledge_dir, **common,
            )
            # Create the Qdrant collection now rather than waiting for the dept
            # plane's next poll, so the KB is fully backed by storage by the
            # time this call returns and the operator can verify it at once.
            sr = _store_registry()
            if sr is not None:
                sr.ensure_all()
                try:
                    result["collection"] = sr.for_scope(result["scope"]).collection
                except Exception as exc:  # pragma: no cover - live Qdrant only
                    result["collection_error"] = str(exc)
            return result

        return _guard(run)

    def kb_admin_assign_department(name: str, department: str) -> dict:
        return _guard(lambda: admin.assign_department(
            name=name, department=department, **common))

    def kb_admin_delete_kb(name: str, purge_documents: bool = False) -> dict:
        return _guard(lambda: admin.delete_kb(
            name=name, registry_file=settings.kb_registry_file,
            knowledge_dir=settings.knowledge_dir,
            store_registry=_store_registry(), purge_documents=purge_documents,
        ))

    def kb_admin_access_matrix() -> dict:
        return _guard(lambda: admin.access_matrix(**common))

    mcp.tool(
        name="kb_admin_list_departments",
        description=("List every department label in play - those declared in the control "
                     "plane and those simply used on a knowledge base - with the delegated "
                     "admin role that owns each and the knowledge bases it groups. A "
                     "department is an optional label: several knowledge bases may share "
                     f"one, and a new one needs no separate creation step. {_ADMIN_NOTE}"),
    )(kb_admin_list_departments)
    mcp.tool(
        name="kb_admin_list_kbs",
        description=("List every knowledge base: its department, scope, folder, the tools it "
                     "exposes, whether its vector collection exists yet, and how many documents "
                     f"it holds. Read-only. {_ADMIN_NOTE}"),
    )(kb_admin_list_kbs)
    mcp.tool(
        name="kb_admin_create_kb",
        description=("Create a knowledge base and assign it to an existing department. `name` "
                     "becomes the kb_search_<name> tool; `department` must already exist "
                     "(use kb_admin_list_departments). Optional `shared_scopes` additionally "
                     "grants read access to non-confidential shared corpora such as ev-core. "
                     "Refused if the name, scope or department is already taken — one "
                     f"department has exactly one knowledge base. {_ADMIN_NOTE}"),
    )(kb_admin_create_kb)
    mcp.tool(
        name="kb_admin_assign_department",
        description=("Re-assign an existing knowledge base to a different department. Changes "
                     "who can reach the KB; does not touch its documents, scope or vectors. "
                     f"{_ADMIN_NOTE}"),
    )(kb_admin_assign_department)
    mcp.tool(
        name="kb_admin_delete_kb",
        description=("Delete a knowledge base. Its record is removed and its vector collection "
                     "dropped, which makes it immediately unreachable. Documents on disk are "
                     "KEPT unless purge_documents=true is passed explicitly, so an accidental "
                     f"delete stays recoverable. {_ADMIN_NOTE}"),
    )(kb_admin_delete_kb)
    mcp.tool(
        name="kb_admin_access_matrix",
        description=("Show, for every department, the single knowledge base, search tool and "
                     "vector collection it can reach. Use this to verify department isolation: "
                     "a department listing more than one reachable knowledge base is a defect. "
                     f"Read-only. {_ADMIN_NOTE}"),
    )(kb_admin_access_matrix)

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
