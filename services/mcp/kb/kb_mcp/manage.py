"""Department-scoped write tools: upload, delete, reindex and list documents.

Mirrors the read side exactly. Just as `kb_search_<profile>` binds its scope filter
server-side, every write tool binds its **target scope** server-side: the model passes a
filename and content, never a scope or a path, so it cannot write into — or delete from —
a knowledge base it was not granted.

The writable scope is *derived*, not configured: a profile's write target is its single
`confidential: true` scope. That has three consequences worth stating, because they are the
isolation guarantees rather than incidental behaviour:

  * A department profile (one confidential scope) is writable, and writes land only there.
  * The shared `ev-core` reference corpus is non-confidential, so it is **never** a write
    target. A department cannot mutate shared reference material.
  * A profile with zero or several confidential scopes is ambiguous, so it gets no write
    tools at all rather than a guessed target.

Indexing is not reimplemented here. Upload and delete both finish by calling the existing
`ingest()` pipeline with `prune=True`, which is idempotent and already handles hashing,
chunking, embedding, replacement and orphan removal.
"""
from __future__ import annotations

import base64
import binascii
import logging
from pathlib import Path

from .ingest import ingest
from .parse import BINARY, SUPPORTED, ParseError, parse_file
from .scopes import Profile, Scope, ScopesConfig

log = logging.getLogger(__name__)

#: Everything the parser can read is uploadable. Binary formats (see `parse.BINARY`)
#: arrive base64-encoded in `content_b64` because an MCP tool argument is a string;
#: text formats arrive verbatim in `content`. Keeping one allowlist means a format
#: cannot be accepted by upload but rejected by the indexer, which would strand a file
#: on disk that never retrieves.
UPLOADABLE = set(SUPPORTED)

#: Text ceiling, in characters. Binary is bounded separately (below) because base64
#: inflates ~33% and the meaningful limit there is decoded bytes, not characters.
#: 100,000,000 characters is the same practical size as the byte ceiling for the
#: ASCII-dominated documents in a NOC corpus, so the two agree on "100 MB" and text
#: uploads are not capped a hundred times tighter than binary ones.
MAX_UPLOAD_CHARS = 100_000_000

#: Binary ceiling, in DECODED bytes. This is the value the whole upload path is aligned
#: to; the client check, the API route, the express body parser and nginx are all set
#: from it, and services/mcp/kb/tests/test_upload_limits.py fails if one of them drifts.
#: Raising it further means raising the two transport limits (which carry the ~4/3
#: base64 inflation) in the same change.
MAX_UPLOAD_BYTES = 100 * 1024 * 1024


class ManageError(ValueError):
    """A write request is not permitted or not usable (bad name, type, or size)."""


def writable_scope(profile: Profile, config: ScopesConfig) -> Scope | None:
    """The one scope `profile` may write to, or None if that is ambiguous."""
    confidential = [config.scopes[s] for s in profile.scopes if config.scopes[s].confidential]
    return confidential[0] if len(confidential) == 1 else None


def safe_filename(name: str) -> str:
    """Reject anything that could escape the scope folder.

    The model supplies this string, so it is untrusted input that is about to become a
    filesystem path. Rejecting rather than sanitising is deliberate: silently rewriting
    `../../etc/passwd` into `passwd` would store the file somewhere the caller did not ask
    for and did not expect.
    """
    if not name or name != name.strip():
        raise ManageError("filename must be non-empty and not padded with whitespace")
    if "/" in name or "\\" in name or "\x00" in name:
        raise ManageError("filename must not contain path separators")
    if name in (".", "..") or name.startswith("."):
        raise ManageError("filename must not be a dotfile or a relative path segment")
    # Path() collapses nothing here, but this catches anything the checks above missed.
    if Path(name).name != name:
        raise ManageError("filename must be a bare file name")
    suffix = Path(name).suffix.lower()
    if suffix not in UPLOADABLE:
        raise ManageError(
            f"unsupported type '{suffix or '(none)'}'; uploadable: "
            f"{', '.join(sorted(UPLOADABLE))}"
        )
    if suffix not in SUPPORTED:  # pragma: no cover - UPLOADABLE is a subset by construction
        raise ManageError(f"'{suffix}' is not indexable by the parser")
    return name


def scope_dir(knowledge_dir: str | Path, scope: Scope) -> Path:
    return Path(knowledge_dir) / scope.folder


def _reindex(knowledge_dir, config, registry, embedder, settings) -> dict:
    return ingest(
        knowledge_dir, config, registry, embedder,
        max_chars=settings.chunk_max_chars,
        overlap=settings.chunk_overlap_chars,
        prune=True,
    )


def upload_document(
    filename: str, content: str = "", *, scope: Scope,
    knowledge_dir, config, registry, embedder, settings, content_b64: str = "",
) -> dict:
    """Write one document into `scope`'s folder and index it.

    Text formats come through `content`; binary formats (`parse.BINARY`) come through
    `content_b64`. The file is written first and parsed by the indexer immediately
    after, so an unreadable upload is rolled back rather than left on disk as a
    permanently unretrievable document.
    """
    name = safe_filename(filename)
    ext = Path(name).suffix.lower()
    is_binary = ext in BINARY

    if is_binary:
        if not content_b64:
            raise ManageError(f"'{ext}' is a binary format and requires content_b64")
        # Refuse on the ENCODED length first. Now that the ceiling is 100 MB, decoding
        # before measuring would let a caller make this process allocate an arbitrarily
        # large bytes object just to be told it was too big. 4 base64 characters carry
        # at most 3 bytes, so this cannot reject a file that is actually within the
        # limit - the exact check on the decoded length still follows.
        if len(content_b64) > ((MAX_UPLOAD_BYTES + 2) // 3) * 4:
            raise ManageError(f"file exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
        try:
            payload = base64.b64decode(content_b64, validate=True)
        except (binascii.Error, ValueError) as e:
            raise ManageError(f"content_b64 is not valid base64: {e}") from e
        if not payload:
            raise ManageError("content is empty")
        if len(payload) > MAX_UPLOAD_BYTES:
            raise ManageError(f"file exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    else:
        if content_b64 and not content:
            # Tolerate a client that base64s everything; decode it back to text.
            try:
                content = base64.b64decode(content_b64, validate=True).decode(
                    "utf-8", errors="replace")
            except (binascii.Error, ValueError) as e:
                raise ManageError(f"content_b64 is not valid base64: {e}") from e
        if not content or not content.strip():
            raise ManageError("content is empty")
        if len(content) > MAX_UPLOAD_CHARS:
            raise ManageError(f"content exceeds {MAX_UPLOAD_CHARS} characters")

    folder = scope_dir(knowledge_dir, scope)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    replaced = target.exists()
    backup = target.read_bytes() if replaced else None

    if is_binary:
        target.write_bytes(payload)
    else:
        target.write_text(content, encoding="utf-8")
    log.info("uploaded %s into scope %s (%d bytes)", name, scope.name, target.stat().st_size)

    def rollback() -> None:
        if backup is None:
            target.unlink(missing_ok=True)
        else:
            target.write_bytes(backup)

    # Parse-check THIS file explicitly before indexing. `ingest()` deliberately skips
    # unreadable files so one bad document cannot abort a whole reindex — which means it
    # would also swallow a bad upload and report success. Checking here keeps upload
    # strict and the corpus free of documents that can never be retrieved.
    try:
        segments = parse_file(target)
    except ParseError as e:
        rollback()
        raise ManageError(f"'{name}' could not be parsed: {e}") from e
    if not segments:
        rollback()
        raise ManageError(
            f"'{name}' contains no extractable text, so it could never be retrieved"
        )

    stats = _reindex(knowledge_dir, config, registry, embedder, settings)

    size = target.stat().st_size
    return {
        "uploaded": name, "scope": scope.name, "replaced": replaced,
        "bytes": size, "characters": (0 if is_binary else len(content)), "index": stats,
    }


def delete_document(
    filename: str, *, scope: Scope,
    knowledge_dir, config, registry, embedder, settings,
) -> dict:
    """Remove one document from `scope` and drop its vectors."""
    name = safe_filename(filename)
    target = scope_dir(knowledge_dir, scope) / name
    if not target.is_file():
        raise ManageError(f"'{name}' is not in the {scope.name} knowledge base")
    target.unlink()
    log.info("deleted %s from scope %s", name, scope.name)

    # prune=True is what actually removes the chunks now that the file is gone.
    stats = _reindex(knowledge_dir, config, registry, embedder, settings)
    return {"deleted": name, "scope": scope.name, "index": stats}


def list_documents(*, scope: Scope, knowledge_dir) -> dict:
    folder = scope_dir(knowledge_dir, scope)
    if not folder.is_dir():
        return {"scope": scope.name, "documents": []}
    docs = sorted(
        (
            {"name": p.name, "bytes": p.stat().st_size}
            for p in folder.rglob("*")
            if p.is_file() and p.name != ".gitkeep" and p.suffix.lower() in SUPPORTED
        ),
        key=lambda d: d["name"],
    )
    return {"scope": scope.name, "documents": docs, "count": len(docs)}


def reindex(*, scope: Scope, knowledge_dir, config, registry, embedder, settings) -> dict:
    """Re-run the corpus ingest. Whole-corpus by design: `ingest()` is idempotent and
    content-hashed, so unchanged documents in other scopes are skipped, not re-embedded."""
    stats = _reindex(knowledge_dir, config, registry, embedder, settings)
    return {"reindexed_scope": scope.name, "index": stats}
