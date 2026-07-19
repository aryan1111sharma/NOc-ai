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


from kb_mcp.scopes import parse_scopes
from kb_mcp.store import StoreRegistry, collection_for, search_many

CFG = parse_scopes(
    {
        "scopes": {
            "ev-core": {},
            "chargezone": {},
            "hr": {"confidential": True},
        },
        "profiles": {
            "noc": {"scopes": ["ev-core"], "description": "d"},
            "all": {"scopes": ["ev-core", "hr"], "description": "d"},
        },
    },
    source="test",
)


def _chunk(source, scope, text, idx=0):
    return {"source": source, "scope": scope, "location": "L", "text": text,
            "doc_hash": "h", "chunk_idx": idx}


def test_collection_for_shared_and_confidential():
    assert collection_for(CFG.scopes["ev-core"], "ev_kb") == "ev_kb"
    assert collection_for(CFG.scopes["hr"], "ev_kb") == "kb_hr"


def test_search_filters_by_scope():
    e = FakeEmbedder()
    s = KbStore(QdrantClient(":memory:"), collection="t", dim=e.dim)
    s.ensure_collection()
    chunks = [
        _chunk("ev-core/a.md", "ev-core", "connector lock failure", 0),
        _chunk("chargezone/b.md", "chargezone", "connector lock failure", 1),
    ]
    s.upsert(chunks, e.embed([c["text"] for c in chunks]))

    both = s.search(e.embed(["connector lock failure"])[0], top_k=5)
    assert len(both) == 2

    only_ev = s.search(e.embed(["connector lock failure"])[0], top_k=5, scopes=["ev-core"])
    assert [h["source"] for h in only_ev] == ["ev-core/a.md"]


def test_registry_routes_confidential_scope_to_own_collection():
    client = QdrantClient(":memory:")
    reg = StoreRegistry(client, CFG, shared_collection="ev_kb", dim=FakeEmbedder().dim)
    assert reg.for_scope("ev-core").collection == "ev_kb"
    assert reg.for_scope("chargezone").collection == "ev_kb"
    assert reg.for_scope("hr").collection == "kb_hr"
    assert {s.collection for s in reg.stores()} == {"ev_kb", "kb_hr"}


def test_registry_group_pairs_stores_with_their_scopes():
    client = QdrantClient(":memory:")
    reg = StoreRegistry(client, CFG, shared_collection="ev_kb", dim=FakeEmbedder().dim)
    grouped = {store.collection: scopes for store, scopes in reg.group(["ev-core", "hr"])}
    assert grouped == {"ev_kb": ["ev-core"], "kb_hr": ["hr"]}


def test_search_many_merges_across_collections_by_score():
    e = FakeEmbedder()
    client = QdrantClient(":memory:")
    reg = StoreRegistry(client, CFG, shared_collection="ev_kb", dim=e.dim)
    reg.ensure_all()

    shared = reg.for_scope("ev-core")
    shared.upsert([_chunk("ev-core/a.md", "ev-core", "alpha text")],
                  e.embed(["alpha text"]))
    conf = reg.for_scope("hr")
    conf.upsert([_chunk("hr/b.md", "hr", "beta text")], e.embed(["beta text"]))

    hits = search_many(reg.group(["ev-core", "hr"]), e.embed(["beta text"])[0],
                       top_k=5, min_score=0.0)
    assert {h["source"] for h in hits} == {"ev-core/a.md", "hr/b.md"}
    assert hits == sorted(hits, key=lambda h: h["score"], reverse=True)
    assert hits[0]["source"] == "hr/b.md"


def test_search_many_applies_min_score_and_top_k():
    e = FakeEmbedder()
    client = QdrantClient(":memory:")
    reg = StoreRegistry(client, CFG, shared_collection="ev_kb", dim=e.dim)
    reg.ensure_all()
    shared = reg.for_scope("ev-core")
    chunks = [_chunk(f"ev-core/{i}.md", "ev-core", f"text {i}", i) for i in range(4)]
    shared.upsert(chunks, e.embed([c["text"] for c in chunks]))

    capped = search_many(reg.group(["ev-core"]), e.embed(["text 1"])[0],
                         top_k=2, min_score=0.0)
    assert len(capped) == 2
    assert search_many(reg.group(["ev-core"]), e.embed(["text 1"])[0],
                       top_k=5, min_score=1.1) == []
