import json

import pytest
from fastmcp import Client
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
    assert names == {"kb_search_noc", "kb_search_chargezone", "kb_search_hr"}


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


async def test_store_failure_raises_rather_than_returning_empty():
    """An empty list means 'not in the knowledge base'. A search failure must
    never be mistaken for that."""
    reg, emb = _seeded()

    class Boom:
        collection = "ev_kb"

        def search(self, *a, **kw):
            raise RuntimeError("qdrant unreachable")

    reg._by_scope["ev-core"] = Boom()
    reg._by_collection["ev_kb"] = Boom()
    server = build_server(config=CFG, registry=reg, embedder=emb)
    with pytest.raises(Exception):
        await _call(server, "kb_search_noc", {"query": "anything"})
