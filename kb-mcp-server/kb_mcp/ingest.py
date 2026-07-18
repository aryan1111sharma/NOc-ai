"""Idempotent ingest: knowledge dir -> parse -> chunk -> embed -> upsert into the store."""
import argparse
import hashlib
from pathlib import Path

from .chunk import chunk_text
from .parse import parse_file, SUPPORTED


def _doc_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ingest(knowledge_dir, store, embedder, *, max_chars, overlap, prune=False) -> dict:
    root = Path(knowledge_dir)
    files = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED]
    on_disk = {p.name for p in files}
    stats = {"indexed_docs": 0, "skipped_docs": 0, "chunks": 0, "pruned_docs": 0}

    for path in files:
        source = path.name
        h = _doc_hash(path)
        if store.current_doc_hash(source) == h:
            stats["skipped_docs"] += 1
            continue
        store.delete_source(source)  # replace old version (no-op if new)
        chunks = []
        for seg in parse_file(path):
            for i, text in enumerate(chunk_text(seg["text"], max_chars, overlap)):
                chunks.append({
                    "source": source, "location": seg["location"],
                    "text": text, "doc_hash": h, "chunk_idx": len(chunks),
                })
        if chunks:
            store.upsert(chunks, embedder.embed([c["text"] for c in chunks]))
            stats["indexed_docs"] += 1
            stats["chunks"] += len(chunks)

    if prune:
        for source in store.known_sources() - on_disk:
            store.delete_source(source)
            stats["pruned_docs"] += 1
    return stats


def main() -> None:  # pragma: no cover - thin CLI wiring, exercised in Task 9 E2E
    from qdrant_client import QdrantClient
    from .config import settings
    from .embed import FastembedEmbedder
    from .store import KbStore

    ap = argparse.ArgumentParser(prog="kb_mcp.ingest")
    ap.add_argument("--prune", action="store_true", help="remove chunks for files no longer present")
    args = ap.parse_args()

    store = KbStore(QdrantClient(url=settings.qdrant_url), settings.collection, settings.embed_dim)
    store.ensure_collection()
    emb = FastembedEmbedder(settings.embed_model, settings.embed_dim)
    stats = ingest(settings.knowledge_dir, store, emb,
                   max_chars=settings.chunk_max_chars, overlap=settings.chunk_overlap_chars,
                   prune=args.prune)
    print(f"ingest done: {stats}")


if __name__ == "__main__":
    main()
