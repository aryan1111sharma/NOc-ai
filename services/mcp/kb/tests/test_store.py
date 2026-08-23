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


import pytest

from kb_mcp.scopes import ScopesConfigError, parse_scopes
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


def test_registry_rejects_slug_collision_between_confidential_scopes():
    """'a-b' and 'a_b' both slugify to 'kb_a_b' — that must not silently merge
    two confidential corpora into one collection."""
    cfg = parse_scopes(
        {
            "scopes": {"a-b": {"confidential": True}, "a_b": {"confidential": True}},
            "profiles": {"p": {"scopes": ["a-b"], "description": "d"}},
        },
        source="test",
    )
    with pytest.raises(ScopesConfigError):
        StoreRegistry(QdrantClient(":memory:"), cfg, shared_collection="ev_kb", dim=8)


def test_registry_rejects_confidential_collection_colliding_with_shared():
    """A confidential scope named 'hr' resolves to 'kb_hr'; if the shared
    collection is configured as 'kb_hr' the confidential data would land in
    the shared, non-isolated collection."""
    cfg = parse_scopes(
        {
            "scopes": {"hr": {"confidential": True}},
            "profiles": {"p": {"scopes": ["hr"], "description": "d"}},
        },
        source="test",
    )
    with pytest.raises(ScopesConfigError):
        StoreRegistry(QdrantClient(":memory:"), cfg, shared_collection="kb_hr", dim=8)


def test_search_empty_scope_list_does_not_fail_open():
    """scopes=[] must filter to nothing, not be treated as 'no filter'."""
    e = FakeEmbedder()
    s = KbStore(QdrantClient(":memory:"), collection="t2", dim=e.dim)
    s.ensure_collection()
    chunks = [
        _chunk("ev-core/a.md", "ev-core", "connector lock failure", 0),
        _chunk("hr/b.md", "hr", "connector lock failure", 1),
    ]
    s.upsert(chunks, e.embed([c["text"] for c in chunks]))
    assert s.search(e.embed(["connector lock failure"])[0], top_k=5, scopes=[]) == []


def test_search_on_missing_collection_returns_empty_not_crash():
    """A confidential scope declared in config but never ingested has no
    collection yet; querying it must read as 'nothing in the KB', not crash
    the MCP tool."""
    client = QdrantClient(":memory:")
    reg = StoreRegistry(client, CFG, shared_collection="ev_kb", dim=FakeEmbedder().dim)
    hr_store = reg.for_scope("hr")  # collection "kb_hr" never created
    assert hr_store.search(FakeEmbedder().embed(["x"])[0], top_k=5) == []


def test_for_scope_unknown_raises_named_error():
    client = QdrantClient(":memory:")
    reg = StoreRegistry(client, CFG, shared_collection="ev_kb", dim=FakeEmbedder().dim)
    with pytest.raises(ScopesConfigError):
        reg.for_scope("does-not-exist")


def test_group_unknown_raises_named_error():
    client = QdrantClient(":memory:")
    reg = StoreRegistry(client, CFG, shared_collection="ev_kb", dim=FakeEmbedder().dim)
    with pytest.raises(ScopesConfigError):
        reg.group(["does-not-exist"])


def test_list_documents_groups_chunks_by_source_with_provenance():
    from qdrant_client import QdrantClient

    from kb_mcp.scopes import parse_scopes
    from kb_mcp.store import StoreRegistry
    from tests.fakes import FakeEmbedder

    cfg = parse_scopes(
        {"scopes": {"ev-core": {}, "contributed": {}},
         "profiles": {"noc": {"scopes": ["ev-core", "contributed"], "description": "d"}}},
        source="test",
    )
    e = FakeEmbedder()
    reg = StoreRegistry(QdrantClient(":memory:"), cfg, shared_collection="ev_kb", dim=e.dim)
    reg.ensure_all()
    store = reg.for_scope("contributed")

    rows = [
        {"source": "contributed/a.md", "scope": "contributed", "location": "a.md",
         "text": "alpha one", "doc_hash": "h1", "chunk_idx": 0,
         "uploaded_by": "noc@quench.local", "uploaded_at": "2026-08-23T10:00:00Z"},
        {"source": "contributed/a.md", "scope": "contributed", "location": "a.md",
         "text": "alpha two", "doc_hash": "h1", "chunk_idx": 1,
         "uploaded_by": "noc@quench.local", "uploaded_at": "2026-08-23T10:00:00Z"},
        {"source": "ev-core/ocpp.md", "scope": "ev-core", "location": "ocpp.md",
         "text": "curated", "doc_hash": "h2", "chunk_idx": 0},
    ]
    store.upsert(rows, e.embed([r["text"] for r in rows]))

    everything = store.list_documents()
    assert [d["source"] for d in everything] == ["contributed/a.md", "ev-core/ocpp.md"]
    a = everything[0]
    assert a["chunks"] == 2
    assert a["uploaded_by"] == "noc@quench.local"
    assert a["scope"] == "contributed"

    only_contributed = store.list_documents(scopes=["contributed"])
    assert [d["source"] for d in only_contributed] == ["contributed/a.md"]

    assert store.list_documents(scopes=[]) == []


def test_list_documents_on_a_missing_collection_is_empty():
    from qdrant_client import QdrantClient

    from kb_mcp.store import KbStore

    store = KbStore(QdrantClient(":memory:"), "never_created", dim=8)
    assert store.list_documents() == []


def test_list_documents_reports_curated_documents_with_no_provenance():
    """Curated corpus documents carry no uploader — the field must be present
    and None rather than missing, so a caller can render it uniformly."""
    from qdrant_client import QdrantClient

    from kb_mcp.scopes import parse_scopes
    from kb_mcp.store import StoreRegistry
    from tests.fakes import FakeEmbedder

    cfg = parse_scopes(
        {"scopes": {"ev-core": {}},
         "profiles": {"noc": {"scopes": ["ev-core"], "description": "d"}}},
        source="test",
    )
    e = FakeEmbedder()
    reg = StoreRegistry(QdrantClient(":memory:"), cfg, shared_collection="ev_kb", dim=e.dim)
    reg.ensure_all()
    store = reg.for_scope("ev-core")
    row = {"source": "ev-core/ocpp.md", "scope": "ev-core", "location": "ocpp.md",
           "text": "curated text", "doc_hash": "h", "chunk_idx": 0}
    store.upsert([row], e.embed([row["text"]]))

    doc = store.list_documents()[0]
    assert doc["uploaded_by"] is None
    assert doc["uploaded_at"] is None
    assert doc["chunks"] == 1
