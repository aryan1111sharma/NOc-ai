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


def _writer_server(tmp_path, headers):
    """Build a server with write tools and a stubbed header source."""
    from kb_mcp.s3 import S3Corpus
    from kb_mcp.writes import KbWriter
    from tests.fakes import FakeS3

    s3 = FakeS3()
    e = FakeEmbedder()
    reg = StoreRegistry(QdrantClient(":memory:"), CFG, shared_collection="ev_kb", dim=e.dim)
    reg.ensure_all()
    writer = KbWriter(
        corpus=S3Corpus(s3, "b"), registry=reg, embedder=e, scope="ev-core",
        cache_dir=tmp_path, contributor_roles=("kb-contributor",), max_upload_mb=25,
        max_chars=1800, overlap=200, now=lambda: "2026-08-23T10:00:00Z",
    )
    mcp = build_server(config=CFG, registry=reg, embedder=e, writer=writer,
                       headers_source=lambda: headers)
    return mcp, s3


@pytest.mark.asyncio
async def test_write_tools_are_registered_when_a_writer_is_supplied(tmp_path):
    mcp, _ = _writer_server(tmp_path, {})
    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}
    assert {"kb_upload_document", "kb_remove_document", "kb_list_documents"} <= names


@pytest.mark.asyncio
async def test_write_tools_are_absent_without_a_writer():
    """A server with no bucket configured must still serve search. Tools that
    exist and always fail are a worse signal than tools that are not there."""
    e = FakeEmbedder()
    reg = StoreRegistry(QdrantClient(":memory:"), CFG, shared_collection="ev_kb", dim=e.dim)
    reg.ensure_all()
    mcp = build_server(config=CFG, registry=reg, embedder=e)
    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}
    assert "kb_upload_document" not in names
    assert "kb_search_noc" in names


@pytest.mark.asyncio
async def test_upload_tool_succeeds_for_a_contributor(tmp_path):
    headers = {"x-user-id": "u1", "x-user-email": "noc@quench.local",
               "x-user-role": "kb-contributor"}
    mcp, s3 = _writer_server(tmp_path, headers)
    s3.put("chat-uploads/u1/abc__note.md", b"Gun 2 fails to unlock after a session ends.")
    async with Client(mcp) as client:
        result = await client.call_tool("kb_upload_document", {"filename": "note.md"})
    payload = json.loads(result.content[0].text)
    assert payload["chunks"] == 1
    assert payload["source"] == "ev-core/note.md"


@pytest.mark.asyncio
async def test_upload_tool_refuses_a_non_contributor(tmp_path):
    headers = {"x-user-id": "u1", "x-user-email": "a@b.com", "x-user-role": "noc-engineer"}
    mcp, s3 = _writer_server(tmp_path, headers)
    s3.put("chat-uploads/u1/abc__note.md", b"body")
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="permission"):
            await client.call_tool("kb_upload_document", {"filename": "note.md"})


@pytest.mark.asyncio
async def test_upload_tool_refuses_when_identity_headers_are_missing(tmp_path):
    """get_http_headers() returns {} when there is no HTTP request. That must
    never read as an implicit grant."""
    mcp, s3 = _writer_server(tmp_path, {})
    s3.put("chat-uploads/u1/abc__note.md", b"body")
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="X-User-Id"):
            await client.call_tool("kb_upload_document", {"filename": "note.md"})


@pytest.mark.asyncio
async def test_list_documents_tool_needs_no_contributor_role(tmp_path):
    contributor = {"x-user-id": "u1", "x-user-email": "noc@quench.local",
                   "x-user-role": "kb-contributor"}
    mcp, s3 = _writer_server(tmp_path, contributor)
    s3.put("chat-uploads/u1/abc__note.md", b"listed content for the document list")
    async with Client(mcp) as client:
        await client.call_tool("kb_upload_document", {"filename": "note.md"})

    reader = {"x-user-id": "u9", "x-user-email": "reader@quench.local",
              "x-user-role": "noc-engineer"}
    mcp2, _ = _writer_server(tmp_path, reader)
    # Rebuild against the same store is not possible here; assert instead that
    # the tool is callable without a contributor role and returns a list.
    async with Client(mcp2) as client:
        result = await client.call_tool("kb_list_documents", {})
    # An empty list yields no content block at all (result.content == []),
    # while result.data still carries the parsed value -- so this reads
    # result.data directly rather than result.content[0].text, which would
    # IndexError whenever the store this server was built against is empty.
    assert isinstance(result.data, list)


@pytest.mark.asyncio
async def test_remove_tool_refuses_a_non_contributor(tmp_path):
    headers = {"x-user-id": "u1", "x-user-email": "a@b.com", "x-user-role": "noc-engineer"}
    mcp, _ = _writer_server(tmp_path, headers)
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="permission"):
            await client.call_tool("kb_remove_document", {"source": "ev-core/note.md"})


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
