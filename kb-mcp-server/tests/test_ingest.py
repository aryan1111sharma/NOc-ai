import shutil
from pathlib import Path

from qdrant_client import QdrantClient

from kb_mcp.ingest import ingest
from kb_mcp.scopes import parse_scopes
from kb_mcp.store import StoreRegistry
from tests.fakes import FakeEmbedder

CFG = parse_scopes(
    {
        "scopes": {"ev-core": {}, "hr": {"confidential": True}},
        "profiles": {"noc": {"scopes": ["ev-core"], "description": "d"}},
    },
    source="test",
)


def _registry():
    e = FakeEmbedder()
    reg = StoreRegistry(QdrantClient(":memory:"), CFG, shared_collection="ev_kb", dim=e.dim)
    reg.ensure_all()
    return reg, e


def _write(root: Path, rel: str, body: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def _ingest(root, reg, emb, **kw):
    return ingest(str(root), CFG, reg, emb, max_chars=1000, overlap=50, **kw)


def test_ingest_indexes_and_is_idempotent(tmp_path: Path):
    _write(tmp_path, "ev-core/r.md", "# Runbook\n\nReset the connector, then reboot.")
    reg, emb = _registry()
    r1 = _ingest(tmp_path, reg, emb)
    assert r1["indexed_docs"] == 1 and r1["chunks"] >= 1
    r2 = _ingest(tmp_path, reg, emb)
    assert r2["indexed_docs"] == 0 and r2["skipped_docs"] == 1


def test_ingest_reindexes_changed_file(tmp_path: Path):
    _write(tmp_path, "ev-core/r.md", "original body")
    reg, emb = _registry()
    _ingest(tmp_path, reg, emb)
    _write(tmp_path, "ev-core/r.md", "completely different body")
    r = _ingest(tmp_path, reg, emb)
    assert r["indexed_docs"] == 1
    hits = reg.for_scope("ev-core").search(emb.embed(["different"])[0], top_k=1)
    assert "different" in hits[0]["text"]


def test_source_is_path_relative_to_corpus_root(tmp_path: Path):
    _write(tmp_path, "ev-core/ocpp/spec.md", "status notification codes")
    reg, emb = _registry()
    _ingest(tmp_path, reg, emb)
    assert reg.for_scope("ev-core").known_sources() == {"ev-core/ocpp/spec.md"}


def test_same_filename_in_two_scopes_does_not_collide(tmp_path: Path):
    """Regression: documents were keyed by bare filename, so the second
    README.md silently deleted the first."""
    _write(tmp_path, "ev-core/README.md", "charger overview alpha")
    _write(tmp_path, "hr/README.md", "leave policy beta")
    reg, emb = _registry()
    r = _ingest(tmp_path, reg, emb)
    assert r["indexed_docs"] == 2
    assert reg.for_scope("ev-core").known_sources() == {"ev-core/README.md"}
    assert reg.for_scope("hr").known_sources() == {"hr/README.md"}


def test_confidential_scope_lands_in_its_own_collection(tmp_path: Path):
    _write(tmp_path, "hr/policy.md", "leave policy")
    reg, emb = _registry()
    _ingest(tmp_path, reg, emb)
    assert reg.for_scope("hr").collection == "kb_hr"
    assert reg.for_scope("ev-core").known_sources() == set()


def test_root_level_files_are_skipped(tmp_path: Path):
    _write(tmp_path, "MANIFEST.md", "corpus manifest, not knowledge")
    _write(tmp_path, "ev-core/r.md", "real knowledge")
    reg, emb = _registry()
    r = _ingest(tmp_path, reg, emb)
    assert r["indexed_docs"] == 1
    assert r["skipped_root_files"] == 1
    assert reg.for_scope("ev-core").known_sources() == {"ev-core/r.md"}


def test_undeclared_folder_is_skipped(tmp_path: Path):
    _write(tmp_path, "mystery/x.md", "who put this here")
    reg, emb = _registry()
    r = _ingest(tmp_path, reg, emb)
    assert r["indexed_docs"] == 0
    assert r["skipped_unscoped_files"] == 1


def test_empty_document_is_counted(tmp_path: Path):
    _write(tmp_path, "ev-core/blank.md", "   \n\n  ")
    reg, emb = _registry()
    r = _ingest(tmp_path, reg, emb)
    assert r["indexed_docs"] == 0
    assert r["empty_docs"] == 1


def test_prune_removes_deleted_files_per_collection(tmp_path: Path):
    _write(tmp_path, "ev-core/r.md", "body")
    _write(tmp_path, "hr/p.md", "policy")
    reg, emb = _registry()
    _ingest(tmp_path, reg, emb)
    (tmp_path / "hr" / "p.md").unlink()
    r = _ingest(tmp_path, reg, emb, prune=True)
    assert r["pruned_docs"] == 1
    assert reg.for_scope("hr").known_sources() == set()
    assert reg.for_scope("ev-core").known_sources() == {"ev-core/r.md"}


def test_prune_does_not_delete_docs_of_other_scope_sharing_collection(tmp_path: Path):
    """Regression guard: known_sources() on a shared collection returns the
    union across every scope routed there. Prune must only remove sources
    that are actually missing from disk, not everything not seen for THIS
    scope's walk — otherwise deleting one scope's file could wipe a sibling
    scope's live documents that happen to share the same collection."""
    cfg = parse_scopes(
        {
            "scopes": {"ev-core": {}, "cms": {}},
            "profiles": {"noc": {"scopes": ["ev-core"], "description": "d"}},
        },
        source="test",
    )
    e = FakeEmbedder()
    reg = StoreRegistry(QdrantClient(":memory:"), cfg, shared_collection="ev_kb", dim=e.dim)
    reg.ensure_all()
    assert reg.for_scope("ev-core").collection == reg.for_scope("cms").collection == "ev_kb"

    _write(tmp_path, "ev-core/a.md", "connector reset steps")
    _write(tmp_path, "cms/b.md", "cms dashboard overview")
    ingest(str(tmp_path), cfg, reg, e, max_chars=1000, overlap=50)

    (tmp_path / "ev-core" / "a.md").unlink()
    r = ingest(str(tmp_path), cfg, reg, e, max_chars=1000, overlap=50, prune=True)

    # ev-core and cms share one collection, so known_sources() on either scope
    # returns the SAME union (per StoreRegistry/KbStore's documented behavior).
    # The real assertion is that cms's document survived the prune triggered
    # by deleting ev-core's file — not that ev-core's view is now empty.
    assert r["pruned_docs"] == 1
    assert reg.for_scope("cms").known_sources() == {"cms/b.md"}
    assert reg.for_scope("ev-core").known_sources() == {"cms/b.md"}


def test_reingest_after_chunk_shrinkage_leaves_no_stale_chunks(tmp_path: Path):
    """delete_source-before-re-upsert must remove ALL old chunk points for a
    source, including high-index ones. A document that shrinks from many
    chunks to one must not leave the old version's tail chunks behind."""
    reg, emb = _registry()
    many_paras = "\n\n".join(f"paragraph {i} filler text to force a new chunk" for i in range(12))
    _write(tmp_path, "ev-core/r.md", many_paras)
    r1 = ingest(str(tmp_path), CFG, reg, emb, max_chars=40, overlap=0)
    assert r1["indexed_docs"] == 1
    store = reg.for_scope("ev-core")
    count_before = store.c.count(store.collection, count_filter=store._source_filter("ev-core/r.md")).count
    assert count_before > 5  # confirm the first version genuinely chunked into many pieces

    _write(tmp_path, "ev-core/r.md", "one short paragraph")
    r2 = ingest(str(tmp_path), CFG, reg, emb, max_chars=40, overlap=0)
    assert r2["indexed_docs"] == 1
    count_after = store.c.count(store.collection, count_filter=store._source_filter("ev-core/r.md")).count
    assert count_after == r2["chunks"]
    assert count_after < count_before


def test_prune_false_does_not_remove_stale_sources(tmp_path: Path):
    _write(tmp_path, "ev-core/r.md", "body")
    reg, emb = _registry()
    _ingest(tmp_path, reg, emb)
    (tmp_path / "ev-core" / "r.md").unlink()
    r = _ingest(tmp_path, reg, emb, prune=False)
    assert r["pruned_docs"] == 0
    assert reg.for_scope("ev-core").known_sources() == {"ev-core/r.md"}


def test_prune_removes_entire_deleted_scope_folder(tmp_path: Path):
    """Distinct from test_prune_removes_deleted_files_per_collection, which
    removes one file but leaves the folder in place: here the whole scope
    folder disappears between runs, so it never even appears in the walk."""
    _write(tmp_path, "ev-core/r.md", "body")
    _write(tmp_path, "hr/p.md", "policy")
    reg, emb = _registry()
    _ingest(tmp_path, reg, emb)
    shutil.rmtree(tmp_path / "hr")
    r = _ingest(tmp_path, reg, emb, prune=True)
    assert r["pruned_docs"] == 1
    assert reg.for_scope("hr").known_sources() == set()
    assert reg.for_scope("ev-core").known_sources() == {"ev-core/r.md"}
