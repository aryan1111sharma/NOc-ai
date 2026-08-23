"""Idempotent ingest: corpus -> scope -> parse -> chunk -> embed -> upsert."""
import argparse
import hashlib
import logging
from pathlib import Path

from .chunk import chunk_tabular, chunk_text
from .parse import parse_file, SUPPORTED, TABULAR, DocumentUnreadable
from .scopes import Scope, ScopesConfig

log = logging.getLogger(__name__)


def _doc_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _collect(root: Path, config: ScopesConfig) -> tuple[list[tuple[Path, Scope]], int, int]:
    """Walk the corpus. A document's top-level folder is its scope; files
    outside any declared scope folder are skipped so misplaced files stay
    visible instead of being silently indexed.

    Note: folder-to-scope matching (`scope_for_folder`) is an exact string
    compare, so on a case-insensitive filesystem (Windows, default macOS) a
    folder named e.g. `EV-Core` against a declared scope `ev-core` is silently
    treated as unscoped. Not a production issue (deployment target is
    Docker/Linux) but worth knowing when testing locally.
    """
    files: list[tuple[Path, Scope]] = []
    skipped_root = skipped_unscoped = 0

    for entry in sorted(root.iterdir()):
        if entry.is_file():
            if entry.suffix.lower() in SUPPORTED:
                skipped_root += 1
                log.warning("skipping %s: files at the corpus root belong to no scope", entry.name)
            continue

        scope = config.scope_for_folder(entry.name)
        for path in sorted(entry.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED:
                continue
            if scope is None:
                skipped_unscoped += 1
                continue
            files.append((path, scope))

        if scope is None:
            log.warning("skipping folder %r: no scope declares it", entry.name)

    return files, skipped_root, skipped_unscoped


def ingest_file(path, source: str, scope_name: str, store, embedder, *,
                max_chars: int, overlap: int, doc_hash: str,
                extra_payload: dict | None = None) -> int:
    """Index exactly one document, replacing any previous version of it.

    Shared by the corpus walk and by chat upload, so both paths chunk, embed
    and stamp payloads identically. Returns the chunk count; 0 means nothing
    was extractable (a scanned PDF or an empty file), which callers must treat
    as a failure rather than a silent success.
    """
    path = Path(path)
    store.delete_source(source)  # replace old version (no-op if new)
    splitter = chunk_tabular if path.suffix.lower() in TABULAR else chunk_text

    try:
        segments = parse_file(path)
    except Exception as exc:  # noqa: BLE001 - any parser failure is the same to callers
        raise DocumentUnreadable(
            f"{path.name} could not be read as {path.suffix} — the file may be corrupt, "
            "or saved in a different format than its extension claims."
        ) from exc

    chunks: list[dict] = []
    for seg in segments:
        for text in splitter(seg["text"], max_chars, overlap):
            chunk = {
                "source": source, "scope": scope_name, "location": seg["location"],
                "text": text, "doc_hash": doc_hash, "chunk_idx": len(chunks),
            }
            if extra_payload:
                chunk.update(extra_payload)
            chunks.append(chunk)

    if not chunks:
        return 0
    store.upsert(chunks, embedder.embed([c["text"] for c in chunks]))
    return len(chunks)


def ingest(knowledge_dir, config: ScopesConfig, registry, embedder, *,
           max_chars: int, overlap: int, prune: bool = False) -> dict:
    root = Path(knowledge_dir)
    files, skipped_root, skipped_unscoped = _collect(root, config)

    stats = {
        "indexed_docs": 0, "skipped_docs": 0, "chunks": 0, "pruned_docs": 0,
        "empty_docs": 0, "unreadable_docs": 0, "skipped_root_files": skipped_root,
        "skipped_unscoped_files": skipped_unscoped,
    }

    on_disk_by_collection: dict[str, set[str]] = {}

    for path, scope in files:
        source = path.relative_to(root).as_posix()
        store = registry.for_scope(scope.name)
        on_disk_by_collection.setdefault(store.collection, set()).add(source)

        h = _doc_hash(path)
        if store.current_doc_hash(source) == h:
            stats["skipped_docs"] += 1
            continue

        try:
            n = ingest_file(path, source=source, scope_name=scope.name, store=store,
                            embedder=embedder, max_chars=max_chars, overlap=overlap,
                            doc_hash=h)
        except DocumentUnreadable as exc:
            stats["unreadable_docs"] += 1
            log.warning("%s", exc)
            continue
        if n == 0:
            stats["empty_docs"] += 1
            log.warning("%s produced no text — scanned PDF or empty file?", source)
            continue
        stats["indexed_docs"] += 1
        stats["chunks"] += n

    if prune:
        for collection_store in registry.stores():
            present = on_disk_by_collection.get(collection_store.collection, set())
            for source in collection_store.known_sources() - present:
                collection_store.delete_source(source)
                stats["pruned_docs"] += 1

    return stats


def main() -> None:  # pragma: no cover - thin CLI wiring, exercised by the live smoke test
    from qdrant_client import QdrantClient

    from .config import settings
    from .embed import FastembedEmbedder
    from .scopes import load_scopes, warn_missing_folders
    from .store import StoreRegistry

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    ap = argparse.ArgumentParser(prog="kb_mcp.ingest")
    ap.add_argument("--prune", action="store_true", help="remove chunks for files no longer present")
    args = ap.parse_args()

    config = load_scopes(settings.scopes_file)
    warn_missing_folders(config, settings.knowledge_dir)

    registry = StoreRegistry(
        QdrantClient(url=settings.qdrant_url), config, settings.collection, settings.embed_dim
    )
    registry.ensure_all()
    emb = FastembedEmbedder(settings.embed_model, settings.embed_dim)

    stats = ingest(settings.knowledge_dir, config, registry, emb,
                   max_chars=settings.chunk_max_chars, overlap=settings.chunk_overlap_chars,
                   prune=args.prune)
    print(f"ingest done: {stats}")


if __name__ == "__main__":
    main()
