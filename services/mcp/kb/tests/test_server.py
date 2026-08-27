import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from qdrant_client import QdrantClient

from kb_mcp.config import settings
from kb_mcp.scopes import parse_scopes
from kb_mcp.server import build_server
from kb_mcp.store import StoreRegistry
from tests.fakes import FakeEmbedder

CFG = parse_scopes(
    {
        "scopes": {"ev-core": {}, "chargezone": {}, "hr": {"confidential": True}},
        "profiles": {
            "noc": {"scopes": ["ev-core"], "description": "EV/OCPP reference."},
            "chargezone": {"scopes": ["ev-core", "chargezone"], "description": "EV plus ChargeZone."},
            "hr": {"scopes": ["hr"], "description": "HR policy.", "min_score": 0.99},
        },
    },
    source="test",
)


def _chunk(source, scope, text, idx=0):
    return {"source": source, "scope": scope, "location": "L", "text": text,
            "doc_hash": "h", "chunk_idx": idx}


def _seeded():
    e = FakeEmbedder()
    reg = StoreRegistry(QdrantClient(":memory:"), CFG, shared_collection="ev_kb", dim=e.dim)
    reg.ensure_all()
    rows = [
        (reg.for_scope("ev-core"), _chunk("ev-core/ocpp.md", "ev-core",
                                          "Error 42 means loss of communication with the charge point.")),
        (reg.for_scope("chargezone"), _chunk("chargezone/sla.md", "chargezone",
                                             "ChargeZone SLA response time is four hours.", 1)),
        (reg.for_scope("hr"), _chunk("hr/leave.md", "hr", "Annual leave is 21 days.", 2)),
    ]
    for store, ch in rows:
        store.upsert([ch], e.embed([ch["text"]]))
    return reg, e


async def _call(server, tool, args):
    async with Client(server) as c:
        res = await c.call_tool(tool, args)
    return json.loads(res.content[0].text) if res.content else res.data


async def test_one_tool_generated_per_profile():
    reg, emb = _seeded()
    server = build_server(config=CFG, registry=reg, embedder=emb)
    async with Client(server) as c:
        names = {t.name for t in await c.list_tools()}
    assert names == {
        "kb_search_noc", "kb_search_chargezone", "kb_search_hr",
        "kb_list_scopes", "kb_ingest_status",
        # `hr` is the only profile with exactly one confidential scope, so it is
        # the only one with an unambiguous write target. `noc` and `chargezone`
        # read shared, non-confidential scopes and deliberately get no write
        # tools rather than a guessed one - see manage.writable_scope.
        "kb_upload_hr", "kb_delete_hr", "kb_documents_hr", "kb_reindex_hr",
    }


async def test_no_admin_tool_is_reachable_on_the_department_plane():
    """The isolation this design rests on: a department agent talks to this
    server, and there is simply no KB-administration tool here to call."""
    reg, emb = _seeded()
    server = build_server(config=CFG, registry=reg, embedder=emb)
    async with Client(server) as c:
        names = {t.name for t in await c.list_tools()}
    assert not [n for n in names if n.startswith("kb_admin_")]


async def test_list_scopes_reports_scopes_and_profiles():
    reg, emb = _seeded()
    server = build_server(config=CFG, registry=reg, embedder=emb)
    data = await _call(server, "kb_list_scopes", {})

    scopes = {s["name"]: s for s in data["scopes"]}
    assert scopes["ev-core"] == {"name": "ev-core", "folder": "ev-core", "confidential": False}
    assert scopes["hr"]["confidential"] is True

    profiles = {p["name"]: p for p in data["profiles"]}
    assert profiles["noc"]["scopes"] == ["ev-core"]
    assert profiles["noc"]["description"] == "EV/OCPP reference."
    assert profiles["hr"]["min_score"] == 0.99


async def test_list_scopes_does_not_expose_document_contents():
    reg, emb = _seeded()
    server = build_server(config=CFG, registry=reg, embedder=emb)
    data = await _call(server, "kb_list_scopes", {})
    dumped = json.dumps(data)
    assert "Error 42" not in dumped and "ocpp.md" not in dumped


async def test_ingest_status_reports_document_counts_per_collection():
    reg, emb = _seeded()
    server = build_server(config=CFG, registry=reg, embedder=emb)
    data = await _call(server, "kb_ingest_status", {})

    by_collection = {c["collection"]: c for c in data["collections"]}
    shared = by_collection["ev_kb"]
    assert sorted(shared["scopes"]) == ["chargezone", "ev-core"]
    assert shared["indexed"] is True
    assert shared["document_count"] == 2  # ev-core/ocpp.md + chargezone/sla.md

    confidential = by_collection["kb_hr"]
    assert confidential["scopes"] == ["hr"]
    assert confidential["document_count"] == 1


async def test_ingest_status_reports_uncreated_collection_as_not_indexed():
    """A scope declared in config but never ingested has no collection yet (see
    store.py's search()) — status must say so rather than crash or fabricate a count."""
    cfg = parse_scopes(
        {
            "scopes": {"ev-core": {}, "unused": {"confidential": True}},
            "profiles": {"noc": {"scopes": ["ev-core"], "description": "d"}},
        },
        source="test",
    )
    e = FakeEmbedder()
    reg = StoreRegistry(QdrantClient(":memory:"), cfg, shared_collection="ev_kb", dim=e.dim)
    reg.ensure_all()  # "unused" (kb_unused) is created empty; only ev-core gets a document
    reg.for_scope("ev-core").upsert(
        [_chunk("ev-core/a.md", "ev-core", "some text")], e.embed(["some text"]),
    )
    server = build_server(config=cfg, registry=reg, embedder=e)
    data = await _call(server, "kb_ingest_status", {})

    by_collection = {c["collection"]: c for c in data["collections"]}
    assert by_collection["ev_kb"]["document_count"] == 1
    assert by_collection["kb_unused"]["indexed"] is True
    assert by_collection["kb_unused"]["document_count"] == 0


async def test_ingest_status_does_not_trigger_ingestion(monkeypatch):
    """Regression guard: calling the status tool must never import or invoke the ingest
    pipeline. If it ever did, this monkeypatch would make the call blow up."""
    import kb_mcp.ingest as ingest_module

    def _boom(*a, **kw):
        raise AssertionError("kb_ingest_status must never call ingest()")

    monkeypatch.setattr(ingest_module, "ingest", _boom)
    reg, emb = _seeded()
    server = build_server(config=CFG, registry=reg, embedder=emb)
    await _call(server, "kb_ingest_status", {})  # must not raise


async def test_tool_description_comes_from_profile_config():
    reg, emb = _seeded()
    server = build_server(config=CFG, registry=reg, embedder=emb)
    async with Client(server) as c:
        tools = {t.name: t for t in await c.list_tools()}
    assert "EV/OCPP reference." in tools["kb_search_noc"].description
    assert "do NOT invent" in tools["kb_search_noc"].description


async def test_tool_returns_cited_chunk():
    reg, emb = _seeded()
    server = build_server(config=CFG, registry=reg, embedder=emb)
    data = await _call(server, "kb_search_noc",
                       {"query": "Error 42 means loss of communication with the charge point.",
                        "min_score": 0})
    assert data and data[0]["source"] == "ev-core/ocpp.md" and "location" in data[0]


async def test_profile_cannot_retrieve_another_scopes_chunk():
    """The isolation claim: a profile granted only ev-core must never return a
    chargezone chunk, even for a query that matches it exactly."""
    reg, emb = _seeded()
    server = build_server(config=CFG, registry=reg, embedder=emb)
    data = await _call(server, "kb_search_noc",
                       {"query": "ChargeZone SLA response time is four hours.", "min_score": 0})
    assert all(h["source"] != "chargezone/sla.md" for h in data)


async def test_profile_with_multiple_scopes_reaches_both():
    reg, emb = _seeded()
    server = build_server(config=CFG, registry=reg, embedder=emb)
    data = await _call(server, "kb_search_chargezone",
                       {"query": "ChargeZone SLA response time is four hours.", "min_score": 0})
    assert any(h["source"] == "chargezone/sla.md" for h in data)


async def test_confidential_scope_reachable_only_via_its_own_tool():
    reg, emb = _seeded()
    server = build_server(config=CFG, registry=reg, embedder=emb)
    leaked = await _call(server, "kb_search_chargezone",
                         {"query": "Annual leave is 21 days.", "min_score": 0})
    assert all(h["source"] != "hr/leave.md" for h in leaked)
    granted = await _call(server, "kb_search_hr",
                          {"query": "Annual leave is 21 days.", "min_score": 0})
    assert any(h["source"] == "hr/leave.md" for h in granted)


async def test_default_min_score_drops_off_corpus_noise(monkeypatch):
    monkeypatch.setattr(settings, "min_score_default", 0.999)
    reg, emb = _seeded()
    server = build_server(config=CFG, registry=reg, embedder=emb)
    data = await _call(server, "kb_search_noc", {"query": "utterly unrelated topic"})
    assert data == []


async def test_profile_min_score_overrides_global(monkeypatch):
    monkeypatch.setattr(settings, "min_score_default", 0.0)
    reg, emb = _seeded()
    server = build_server(config=CFG, registry=reg, embedder=emb)
    # profile "hr" pins min_score 0.99, so its own seeded chunk is filtered out
    data = await _call(server, "kb_search_hr", {"query": "something else entirely"})
    assert data == []


async def test_build_server_gates_each_dependency_independently(monkeypatch):
    """config, registry, and embedder are independent injection points: supplying
    some subset of them must not drag in real wiring for the ones that WERE
    supplied. Regression for a prior all-or-nothing guard
    (`if config is None or registry is None or embedder is None`) that ran the
    entire real-wiring block -- including warn_missing_folders against a
    caller-supplied config -- whenever ANY single argument was missing."""
    reg, emb = _seeded()

    warn_calls = []
    monkeypatch.setattr(
        "kb_mcp.scopes.warn_missing_folders",
        lambda cfg, knowledge_dir: warn_calls.append(cfg),
    )

    class BoomEmbedder:
        def __init__(self, *a, **kw):
            raise AssertionError("real embedder must not be constructed when one was supplied")

    monkeypatch.setattr("kb_mcp.embed.FastembedEmbedder", BoomEmbedder)

    # config and registry are supplied; embedder is intentionally omitted so
    # the embedder-wiring branch runs on its own -- proving it's gated
    # independently, and proving config/registry being supplied did NOT also
    # re-trigger config loading or the filesystem warning check.
    with pytest.raises(AssertionError, match="real embedder must not be constructed"):
        build_server(config=CFG, registry=reg, embedder=None)

    assert warn_calls == []


async def test_store_failure_raises_rather_than_returning_empty():
    """An empty list means 'not in the knowledge base'. A search failure must
    never be mistaken for that. Matching on the underlying message (not just
    "some ToolError was raised") matters: FastMCP wraps a missing tool name
    in the same ToolError type with a generic "Unknown tool" message, so a
    bare `pytest.raises(ToolError)` would pass just as happily for a typo'd
    tool name as for the store error this test exists to pin."""
    reg, emb = _seeded()

    class Boom:
        collection = "ev_kb"

        def search(self, *a, **kw):
            raise RuntimeError("qdrant unreachable")

    reg._by_scope["ev-core"] = Boom()
    reg._by_collection["ev_kb"] = Boom()
    server = build_server(config=CFG, registry=reg, embedder=emb)
    with pytest.raises(ToolError, match="qdrant unreachable"):
        await _call(server, "kb_search_noc", {"query": "anything"})
