from pathlib import Path
from qdrant_client import QdrantClient
from kb_mcp.store import KbStore
from kb_mcp.ingest import ingest
from tests.fakes import FakeEmbedder


def _fresh(tmp_path: Path):
    store = KbStore(QdrantClient(":memory:"), collection="t", dim=FakeEmbedder().dim)
    store.ensure_collection()
    return store, FakeEmbedder()


def test_ingest_indexes_and_is_idempotent(tmp_path: Path):
    (tmp_path / "r.md").write_text("# Runbook\n\nReset the connector, then reboot.", encoding="utf-8")
    store, emb = _fresh(tmp_path)
    r1 = ingest(str(tmp_path), store, emb, max_chars=1000, overlap=50)
    assert r1["indexed_docs"] == 1 and r1["chunks"] >= 1
    r2 = ingest(str(tmp_path), store, emb, max_chars=1000, overlap=50)  # unchanged
    assert r2["indexed_docs"] == 0 and r2["skipped_docs"] == 1


def test_ingest_reindexes_changed_file(tmp_path: Path):
    f = tmp_path / "r.md"
    f.write_text("original body", encoding="utf-8")
    store, emb = _fresh(tmp_path)
    ingest(str(tmp_path), store, emb, max_chars=1000, overlap=50)
    f.write_text("completely different body", encoding="utf-8")
    r = ingest(str(tmp_path), store, emb, max_chars=1000, overlap=50)
    assert r["indexed_docs"] == 1
    hits = store.search(emb.embed(["different"])[0], top_k=1)
    assert "different" in hits[0]["text"]


def test_prune_removes_deleted_files(tmp_path: Path):
    f = tmp_path / "r.md"
    f.write_text("body", encoding="utf-8")
    store, emb = _fresh(tmp_path)
    ingest(str(tmp_path), store, emb, max_chars=1000, overlap=50)
    f.unlink()
    r = ingest(str(tmp_path), store, emb, max_chars=1000, overlap=50, prune=True)
    assert r["pruned_docs"] == 1
    assert store.known_sources() == set()
