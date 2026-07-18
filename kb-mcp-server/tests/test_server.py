import json
from qdrant_client import QdrantClient
from fastmcp import Client
from kb_mcp.config import settings
from kb_mcp.server import build_server
from kb_mcp.store import KbStore
from tests.fakes import FakeEmbedder


def _seeded():
    e = FakeEmbedder()
    store = KbStore(QdrantClient(":memory:"), collection="t", dim=e.dim)
    store.ensure_collection()
    chunks = [{"source": "ocpp.md", "location": "p.1",
               "text": "Error 42 means loss of communication with the charge point.",
               "doc_hash": "h", "chunk_idx": 0}]
    store.upsert(chunks, e.embed([c["text"] for c in chunks]))
    return store, e


async def test_kb_search_returns_cited_chunk():
    store, emb = _seeded()
    server = build_server(store=store, embedder=emb)
    async with Client(server) as c:
        res = await c.call_tool("kb_search", {"query": "communication loss", "top_k": 3})
    data = json.loads(res.content[0].text) if res.content else res.data
    assert data and data[0]["source"] == "ocpp.md" and "location" in data[0]


async def test_kb_search_drops_hits_below_default_min_score(monkeypatch):
    """Off-corpus queries must come back empty, not as low-scoring noise — the agents'
    'not in the knowledge base' behaviour keys off an empty list."""
    monkeypatch.setattr(settings, "min_score_default", 0.999)
    store, emb = _seeded()
    server = build_server(store=store, embedder=emb)
    async with Client(server) as c:
        res = await c.call_tool("kb_search", {"query": "utterly unrelated topic"})
    data = json.loads(res.content[0].text) if res.content else res.data
    assert data == []


async def test_kb_search_min_score_zero_disables_the_floor(monkeypatch):
    monkeypatch.setattr(settings, "min_score_default", 0.999)
    store, emb = _seeded()
    server = build_server(store=store, embedder=emb)
    async with Client(server) as c:
        res = await c.call_tool("kb_search", {"query": "utterly unrelated topic", "min_score": 0})
    data = json.loads(res.content[0].text) if res.content else res.data
    assert data, "min_score=0 must return the raw hits"


async def test_kb_search_empty_when_no_store_content():
    e = FakeEmbedder()
    empty = KbStore(QdrantClient(":memory:"), collection="t2", dim=e.dim)
    empty.ensure_collection()
    server = build_server(store=empty, embedder=e)
    async with Client(server) as c:
        res = await c.call_tool("kb_search", {"query": "anything"})
    data = json.loads(res.content[0].text) if res.content else res.data
    assert data == []
