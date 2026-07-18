from qdrant_client import QdrantClient
from kb_mcp.store import KbStore
from tests.fakes import FakeEmbedder


def _store():
    return KbStore(QdrantClient(":memory:"), collection="t", dim=FakeEmbedder().dim)


def test_upsert_then_search_returns_payload():
    s, e = _store(), FakeEmbedder()
    s.ensure_collection()
    chunks = [
        {"source": "a.md", "location": "h1", "text": "charger overheating fault", "doc_hash": "h", "chunk_idx": 0},
        {"source": "a.md", "location": "h2", "text": "network reconnect procedure", "doc_hash": "h", "chunk_idx": 1},
    ]
    s.upsert(chunks, e.embed([c["text"] for c in chunks]))
    hits = s.search(e.embed(["overheating"])[0], top_k=1)
    assert hits and hits[0]["source"] == "a.md"
    assert set(hits[0]) == {"source", "location", "text", "score"}


def test_delete_by_source():
    s, e = _store(), FakeEmbedder()
    s.ensure_collection()
    c = {"source": "a.md", "location": "h1", "text": "x", "doc_hash": "h", "chunk_idx": 0}
    s.upsert([c], e.embed(["x"]))
    s.delete_source("a.md")
    assert s.search(e.embed(["x"])[0], top_k=5) == []


def test_doc_hash_lookup():
    s, e = _store(), FakeEmbedder()
    s.ensure_collection()
    c = {"source": "a.md", "location": "h1", "text": "x", "doc_hash": "HASH1", "chunk_idx": 0}
    s.upsert([c], e.embed(["x"]))
    assert s.current_doc_hash("a.md") == "HASH1"
    assert s.current_doc_hash("missing.md") is None
