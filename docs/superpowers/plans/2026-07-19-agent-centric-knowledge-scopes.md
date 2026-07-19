# Agent-Centric Knowledge Scopes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each agent its own knowledge scope, so future agents for other purposes (HR, per-customer, other technical domains) can be added without diluting or exposing existing corpora.

**Architecture:** A document's top-level folder under `knowledge/` is its **scope**. `knowledge/scopes.yaml` declares scopes and **profiles** (named bundles of scopes). `kb-mcp-server` generates one MCP tool per profile — `kb_search_<profile>` — with its scope filter baked in server-side, because LibreChat's only per-agent control is tool selection. Non-confidential scopes share the `ev_kb` Qdrant collection separated by a payload filter; scopes marked `confidential: true` get their own collection.

**Tech Stack:** Python 3.12, FastMCP 3.4.4, Qdrant (`qdrant-client` 1.18), fastembed/BGE, PyYAML 6, pytest + pytest-asyncio (`asyncio_mode = auto`).

**Spec:** `docs/superpowers/specs/2026-07-19-agent-centric-knowledge-scopes-design.md`

---

## Working environment

All commands run from `E:\cjain\noc-mcp-agent\kb-mcp-server` unless stated otherwise.

The Python interpreter is the project venv. In bash:

```bash
cd /e/cjain/noc-mcp-agent/kb-mcp-server
./.venv/Scripts/python.exe -m pytest -q
```

**Critical property to preserve:** the whole suite runs with **no Qdrant container and no model download** — tests use `QdrantClient(":memory:")` and `tests/fakes.FakeEmbedder` (dim 8, sha256-derived vectors). Never import `fastembed` at module scope. Current baseline: **17 tests, ~6s**.

## File structure

| File | Responsibility | Change |
|---|---|---|
| `kb_mcp/scopes.py` | Parse + validate `scopes.yaml` → `Scope`/`Profile`/`ScopesConfig`. No Qdrant, no MCP, no corpus walking. | **create** |
| `kb_mcp/store.py` | Vector reads/writes. Adds scope filtering, `StoreRegistry` (scope→collection), `search_many` (merge across collections). | modify |
| `kb_mcp/ingest.py` | Walk corpus, derive scope from folder, key docs by relative path, route to the right collection. | modify |
| `kb_mcp/server.py` | Generate one MCP tool per profile. | modify |
| `kb_mcp/config.py` | Add `scopes_file`. | modify |
| `requirements.txt` | Declare `pyyaml`. | modify |
| `tests/test_scopes.py` | Config parsing/validation tests. | **create** |
| `tests/test_store.py`, `test_ingest.py`, `test_server.py` | Updated for new signatures + new behaviour tests. | modify |
| `knowledge/scopes.yaml` | Live scope/profile config (tracked in git). | **create** |
| `knowledge/.gitignore` | Add `!scopes.yaml` exception. | modify |

---

## Task 1: Scopes config module

**Files:**
- Create: `kb-mcp-server/kb_mcp/scopes.py`
- Create: `kb-mcp-server/tests/test_scopes.py`
- Modify: `kb-mcp-server/kb_mcp/config.py`
- Modify: `kb-mcp-server/requirements.txt`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scopes.py`:

```python
import logging

import pytest

from kb_mcp.scopes import (
    ScopesConfig,
    ScopesConfigError,
    load_scopes,
    parse_scopes,
    warn_missing_folders,
)

VALID = {
    "scopes": {
        "ev-core": {"folder": "ev-core"},
        "chargezone": {"folder": "chargezone"},
        "hr": {"folder": "hr", "confidential": True},
    },
    "profiles": {
        "noc": {"scopes": ["ev-core"], "description": "EV/OCPP reference."},
        "chargezone": {
            "scopes": ["ev-core", "chargezone"],
            "description": "EV reference plus ChargeZone docs.",
            "min_score": 0.55,
        },
    },
}


def test_parse_valid_config():
    cfg = parse_scopes(VALID, source="test")
    assert isinstance(cfg, ScopesConfig)
    assert cfg.scopes["hr"].confidential is True
    assert cfg.scopes["ev-core"].confidential is False
    assert cfg.profiles["chargezone"].scopes == ("ev-core", "chargezone")
    assert cfg.profiles["chargezone"].min_score == 0.55
    assert cfg.profiles["noc"].min_score is None


def test_folder_defaults_to_scope_name():
    cfg = parse_scopes(
        {"scopes": {"ops": {}}, "profiles": {"p": {"scopes": ["ops"], "description": "d"}}},
        source="test",
    )
    assert cfg.scopes["ops"].folder == "ops"


def test_scope_for_folder_lookup():
    cfg = parse_scopes(VALID, source="test")
    assert cfg.scope_for_folder("hr").name == "hr"
    assert cfg.scope_for_folder("nope") is None


def test_profile_referencing_undeclared_scope_raises():
    bad = {
        "scopes": {"ev-core": {}},
        "profiles": {"noc": {"scopes": ["typo-core"], "description": "d"}},
    }
    with pytest.raises(ScopesConfigError, match="typo-core"):
        parse_scopes(bad, source="test")


def test_profile_without_description_raises():
    bad = {"scopes": {"a": {}}, "profiles": {"p": {"scopes": ["a"]}}}
    with pytest.raises(ScopesConfigError, match="description"):
        parse_scopes(bad, source="test")


def test_profile_with_empty_scopes_raises():
    bad = {"scopes": {"a": {}}, "profiles": {"p": {"scopes": [], "description": "d"}}}
    with pytest.raises(ScopesConfigError, match="at least one scope"):
        parse_scopes(bad, source="test")


def test_missing_sections_raise():
    with pytest.raises(ScopesConfigError, match="scopes"):
        parse_scopes({"profiles": {}}, source="test")
    with pytest.raises(ScopesConfigError, match="profiles"):
        parse_scopes({"scopes": {"a": {}}}, source="test")


def test_invalid_min_score_raises():
    bad = {
        "scopes": {"a": {}},
        "profiles": {"p": {"scopes": ["a"], "description": "d", "min_score": 5}},
    }
    with pytest.raises(ScopesConfigError, match="min_score"):
        parse_scopes(bad, source="test")


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(ScopesConfigError, match="not found"):
        load_scopes(tmp_path / "nope.yaml")


def test_load_malformed_yaml_raises(tmp_path):
    p = tmp_path / "scopes.yaml"
    p.write_text("scopes: [unclosed\n", encoding="utf-8")
    with pytest.raises(ScopesConfigError, match="valid YAML"):
        load_scopes(p)


def test_duplicate_key_raises(tmp_path):
    p = tmp_path / "scopes.yaml"
    p.write_text(
        "scopes:\n  a: {}\n  a: {}\nprofiles:\n  p:\n    scopes: [a]\n    description: d\n",
        encoding="utf-8",
    )
    with pytest.raises(ScopesConfigError, match="duplicate"):
        load_scopes(p)


def test_load_round_trip(tmp_path):
    p = tmp_path / "scopes.yaml"
    p.write_text(
        "scopes:\n"
        "  ev-core: {folder: ev-core}\n"
        "profiles:\n"
        "  noc:\n"
        "    scopes: [ev-core]\n"
        "    description: EV reference.\n",
        encoding="utf-8",
    )
    cfg = load_scopes(p)
    assert cfg.profiles["noc"].description == "EV reference."


def test_warn_missing_folders_warns_but_does_not_raise(tmp_path, caplog):
    cfg = parse_scopes({"scopes": {"ghost": {}}, "profiles": {"p": {"scopes": ["ghost"], "description": "d"}}}, source="test")
    with caplog.at_level(logging.WARNING):
        warn_missing_folders(cfg, tmp_path)
    assert "ghost" in caplog.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_scopes.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'kb_mcp.scopes'`

- [ ] **Step 3: Write the implementation**

Create `kb_mcp/scopes.py`:

```python
"""Load and validate the scopes/profiles config.

Pure config handling: this module knows nothing about Qdrant, MCP, or how the
corpus is walked. That keeps its validation rules cheap to test exhaustively.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

log = logging.getLogger(__name__)


class ScopesConfigError(ValueError):
    """scopes.yaml is missing, malformed, or internally inconsistent."""


@dataclass(frozen=True)
class Scope:
    name: str
    folder: str
    confidential: bool = False


@dataclass(frozen=True)
class Profile:
    name: str
    scopes: tuple[str, ...]
    description: str
    min_score: float | None = None


@dataclass(frozen=True)
class ScopesConfig:
    scopes: dict[str, Scope]
    profiles: dict[str, Profile]

    def scope_for_folder(self, folder: str) -> Scope | None:
        for scope in self.scopes.values():
            if scope.folder == folder:
                return scope
        return None


class _DupCheckLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys instead of silently
    keeping the last one — a duplicate scope name is an ambiguous grant."""


def _no_duplicates(loader: _DupCheckLoader, node: yaml.MappingNode) -> dict:
    out: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in out:
            raise ScopesConfigError(f"duplicate key {key!r} in scopes config")
        out[key] = loader.construct_object(value_node, deep=True)
    return out


_DupCheckLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicates
)


def _require_mapping(value, what: str, source: str) -> dict:
    if not isinstance(value, dict):
        raise ScopesConfigError(f"{source}: '{what}' must be a mapping, got {type(value).__name__}")
    if not value:
        raise ScopesConfigError(f"{source}: '{what}' must not be empty")
    return value


def parse_scopes(raw, *, source: str) -> ScopesConfig:
    if not isinstance(raw, dict):
        raise ScopesConfigError(f"{source}: top level must be a mapping")

    raw_scopes = _require_mapping(raw.get("scopes"), "scopes", source)
    raw_profiles = _require_mapping(raw.get("profiles"), "profiles", source)

    scopes: dict[str, Scope] = {}
    for name, body in raw_scopes.items():
        body = body or {}
        if not isinstance(body, dict):
            raise ScopesConfigError(f"{source}: scope '{name}' must be a mapping")
        scopes[name] = Scope(
            name=name,
            folder=str(body.get("folder", name)),
            confidential=bool(body.get("confidential", False)),
        )

    profiles: dict[str, Profile] = {}
    for name, body in raw_profiles.items():
        if not isinstance(body, dict):
            raise ScopesConfigError(f"{source}: profile '{name}' must be a mapping")

        names = body.get("scopes") or []
        if not isinstance(names, list) or not names:
            raise ScopesConfigError(f"{source}: profile '{name}' must list at least one scope")
        for scope_name in names:
            if scope_name not in scopes:
                raise ScopesConfigError(
                    f"{source}: profile '{name}' references undeclared scope '{scope_name}'"
                )

        description = body.get("description")
        if not isinstance(description, str) or not description.strip():
            raise ScopesConfigError(
                f"{source}: profile '{name}' needs a non-empty description "
                "(it becomes the tool's docstring)"
            )

        min_score = body.get("min_score")
        if min_score is not None:
            if not isinstance(min_score, (int, float)) or isinstance(min_score, bool):
                raise ScopesConfigError(f"{source}: profile '{name}' min_score must be a number")
            if not 0.0 <= float(min_score) <= 1.0:
                raise ScopesConfigError(f"{source}: profile '{name}' min_score must be between 0 and 1")
            min_score = float(min_score)

        profiles[name] = Profile(
            name=name,
            scopes=tuple(names),
            description=description.strip(),
            min_score=min_score,
        )

    return ScopesConfig(scopes=scopes, profiles=profiles)


def load_scopes(path) -> ScopesConfig:
    p = Path(path)
    if not p.is_file():
        raise ScopesConfigError(f"scopes config not found: {p}")
    try:
        raw = yaml.load(p.read_text(encoding="utf-8"), Loader=_DupCheckLoader)
    except ScopesConfigError:
        raise
    except yaml.YAMLError as exc:
        raise ScopesConfigError(f"{p} is not valid YAML: {exc}") from exc
    return parse_scopes(raw, source=str(p))


def warn_missing_folders(config: ScopesConfig, knowledge_dir) -> None:
    """A declared scope whose folder does not exist yet is legitimate (config
    written before docs arrive), so warn rather than fail."""
    root = Path(knowledge_dir)
    for scope in config.scopes.values():
        if not (root / scope.folder).is_dir():
            log.warning("scope %r declares folder %r which does not exist under %s",
                        scope.name, scope.folder, root)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_scopes.py -q`
Expected: `14 passed`

- [ ] **Step 5: Add the config setting**

In `kb_mcp/config.py`, add after the `knowledge_dir` line:

```python
    scopes_file: str = "/knowledge/scopes.yaml"
```

- [ ] **Step 6: Declare pyyaml**

In `requirements.txt`, add after `pymupdf>=1.24`:

```
pyyaml>=6.0
```

(PyYAML 6.0.3 is already present transitively in the venv, so no install is needed for tests — this makes the dependency explicit for the Docker build.)

- [ ] **Step 7: Run the whole suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: `31 passed` (17 existing + 14 new)

- [ ] **Step 8: Commit**

```bash
git add kb-mcp-server/kb_mcp/scopes.py kb-mcp-server/tests/test_scopes.py kb-mcp-server/kb_mcp/config.py kb-mcp-server/requirements.txt
git commit -m "feat(kb): scopes.yaml config module with validation

Scopes declare folders; profiles bundle scopes and carry the description that
becomes a generated tool's docstring. Duplicate keys, undeclared scope
references, and missing descriptions fail at load rather than silently
narrowing an agent's knowledge."
```

---

## Task 2: Scope filtering, store registry, cross-collection search

**Files:**
- Modify: `kb-mcp-server/kb_mcp/store.py`
- Modify: `kb-mcp-server/tests/test_store.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_store.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_store.py -q`
Expected: `ImportError: cannot import name 'StoreRegistry' from 'kb_mcp.store'`

- [ ] **Step 3: Write the implementation**

Replace `kb_mcp/store.py` entirely:

```python
"""Qdrant-backed chunk store. Point IDs are deterministic so re-upsert overwrites."""
import re
import uuid

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue, MatchAny,
)

from .scopes import Scope, ScopesConfig

_NS = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _point_id(source: str, chunk_idx: int) -> str:
    return str(uuid.uuid5(_NS, f"{source}:{chunk_idx}"))


def collection_for(scope: Scope, shared_collection: str) -> str:
    """Shared scopes co-habit one collection and are separated by a payload
    filter; a confidential scope gets its own collection so no filter bug can
    expose it."""
    if not scope.confidential:
        return shared_collection
    slug = re.sub(r"[^a-z0-9]+", "_", scope.name.lower()).strip("_")
    return f"kb_{slug}"


class KbStore:
    def __init__(self, client: QdrantClient, collection: str, dim: int):
        self.c, self.collection, self.dim = client, collection, dim

    def ensure_collection(self) -> None:
        if not self.c.collection_exists(self.collection):
            self.c.create_collection(
                self.collection,
                vectors_config=VectorParams(size=self.dim, distance=Distance.COSINE),
            )

    def upsert(self, chunks: list[dict], vectors: list[list[float]]) -> None:
        points = [
            PointStruct(id=_point_id(ch["source"], ch["chunk_idx"]), vector=vec, payload=ch)
            for ch, vec in zip(chunks, vectors)
        ]
        self.c.upsert(self.collection, points=points)

    def _source_filter(self, source: str) -> Filter:
        return Filter(must=[FieldCondition(key="source", match=MatchValue(value=source))])

    def delete_source(self, source: str) -> None:
        self.c.delete(self.collection, points_selector=self._source_filter(source))

    def current_doc_hash(self, source: str) -> str | None:
        pts, _ = self.c.scroll(
            self.collection, scroll_filter=self._source_filter(source), limit=1, with_payload=True
        )
        return pts[0].payload.get("doc_hash") if pts else None

    def known_sources(self) -> set[str]:
        seen, offset = set(), None
        while True:
            pts, offset = self.c.scroll(self.collection, limit=256, offset=offset, with_payload=True)
            for p in pts:
                seen.add(p.payload.get("source"))
            if offset is None:
                break
        return seen

    def search(self, vector: list[float], top_k: int, scopes: list[str] | None = None) -> list[dict]:
        flt = None
        if scopes:
            flt = Filter(must=[FieldCondition(key="scope", match=MatchAny(any=list(scopes)))])
        res = self.c.query_points(
            self.collection, query=vector, limit=top_k, with_payload=True, query_filter=flt
        )
        out = []
        for p in res.points:
            pl = p.payload
            out.append({"source": pl["source"], "location": pl["location"],
                        "text": pl["text"], "score": p.score})
        return out


class StoreRegistry:
    """Resolves a scope to the KbStore that holds it. One KbStore per
    collection, shared by every scope routed to that collection."""

    def __init__(self, client: QdrantClient, config: ScopesConfig,
                 shared_collection: str, dim: int):
        self._by_collection: dict[str, KbStore] = {}
        self._by_scope: dict[str, KbStore] = {}
        for name, scope in config.scopes.items():
            coll = collection_for(scope, shared_collection)
            if coll not in self._by_collection:
                self._by_collection[coll] = KbStore(client, coll, dim)
            self._by_scope[name] = self._by_collection[coll]

    def for_scope(self, scope_name: str) -> KbStore:
        return self._by_scope[scope_name]

    def stores(self) -> list[KbStore]:
        return list(self._by_collection.values())

    def ensure_all(self) -> None:
        for store in self._by_collection.values():
            store.ensure_collection()

    def group(self, scope_names) -> list[tuple[KbStore, list[str]]]:
        """Group scopes by the store that holds them, so each collection is
        queried once with all of its relevant scopes."""
        grouped: dict[str, list[str]] = {}
        for name in scope_names:
            grouped.setdefault(self._by_scope[name].collection, []).append(name)
        return [(self._by_collection[coll], names) for coll, names in grouped.items()]


def search_many(pairs: list[tuple[KbStore, list[str]]], vector: list[float],
                *, top_k: int, min_score: float) -> list[dict]:
    """Query each (store, scopes) pair with limit=top_k, then merge and truncate
    to top_k overall. Over-fetching per collection is what lets one collection
    supply all top_k results when it holds the best matches."""
    hits: list[dict] = []
    for store, scopes in pairs:
        hits.extend(store.search(vector, top_k=top_k, scopes=scopes))
    hits.sort(key=lambda h: h["score"], reverse=True)
    return [h for h in hits if h["score"] >= min_score][:top_k]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_store.py -q`
Expected: `9 passed` (3 existing + 6 new)

- [ ] **Step 5: Run the whole suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: `37 passed`

- [ ] **Step 6: Commit**

```bash
git add kb-mcp-server/kb_mcp/store.py kb-mcp-server/tests/test_store.py
git commit -m "feat(kb): scope filtering, store registry, cross-collection search

Scopes are separated by a payload filter within the shared collection;
confidential scopes route to their own collection via StoreRegistry.
search_many queries each collection with limit=top_k then merges and
truncates, so one collection can supply all results when it deserves to."
```

---

## Task 3: Scope-aware ingest (fixes the filename-collision bug)

**Files:**
- Modify: `kb-mcp-server/kb_mcp/ingest.py`
- Modify: `kb-mcp-server/tests/test_ingest.py`

**Note:** the three existing tests in `test_ingest.py` write files at the corpus **root**, which the new rules skip. They are rewritten below — this is expected, not a regression.

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_ingest.py` entirely:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_ingest.py -q`
Expected: `TypeError: ingest() takes ... positional arguments` on every test

- [ ] **Step 3: Write the implementation**

Replace `kb_mcp/ingest.py` entirely:

```python
"""Idempotent ingest: corpus -> scope -> parse -> chunk -> embed -> upsert."""
import argparse
import hashlib
import logging
from pathlib import Path

from .chunk import chunk_text
from .parse import parse_file, SUPPORTED
from .scopes import Scope, ScopesConfig

log = logging.getLogger(__name__)


def _doc_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _collect(root: Path, config: ScopesConfig) -> tuple[list[tuple[Path, Scope]], int, int]:
    """Walk the corpus. A document's top-level folder is its scope; files
    outside any declared scope folder are skipped so misplaced files stay
    visible instead of being silently indexed."""
    files: list[tuple[Path, Scope]] = []
    skipped_root = skipped_unscoped = 0

    for entry in sorted(root.iterdir()):
        if entry.is_file():
            if entry.suffix.lower() in SUPPORTED:
                skipped_root += 1
                log.warning("skipping %s: files at the corpus root belong to no scope", entry.name)
            continue

        scope = config.scope_for_folder(entry.name)
        for path in sorted(entry.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED:
                continue
            if scope is None:
                skipped_unscoped += 1
                continue
            files.append((path, scope))

        if scope is None:
            log.warning("skipping folder %r: no scope declares it", entry.name)

    return files, skipped_root, skipped_unscoped


def ingest(knowledge_dir, config: ScopesConfig, registry, embedder, *,
           max_chars: int, overlap: int, prune: bool = False) -> dict:
    root = Path(knowledge_dir)
    files, skipped_root, skipped_unscoped = _collect(root, config)

    stats = {
        "indexed_docs": 0, "skipped_docs": 0, "chunks": 0, "pruned_docs": 0,
        "empty_docs": 0, "skipped_root_files": skipped_root,
        "skipped_unscoped_files": skipped_unscoped,
    }

    on_disk_by_collection: dict[str, set[str]] = {}

    for path, scope in files:
        source = path.relative_to(root).as_posix()
        store = registry.for_scope(scope.name)
        on_disk_by_collection.setdefault(store.collection, set()).add(source)

        h = _doc_hash(path)
        if store.current_doc_hash(source) == h:
            stats["skipped_docs"] += 1
            continue

        store.delete_source(source)  # replace old version (no-op if new)
        chunks = []
        for seg in parse_file(path):
            for text in chunk_text(seg["text"], max_chars, overlap):
                chunks.append({
                    "source": source, "scope": scope.name, "location": seg["location"],
                    "text": text, "doc_hash": h, "chunk_idx": len(chunks),
                })

        if not chunks:
            stats["empty_docs"] += 1
            log.warning("%s produced no text — scanned PDF or empty file?", source)
            continue

        store.upsert(chunks, embedder.embed([c["text"] for c in chunks]))
        stats["indexed_docs"] += 1
        stats["chunks"] += len(chunks)

    if prune:
        for store in registry.stores():
            present = on_disk_by_collection.get(store.collection, set())
            for source in store.known_sources() - present:
                store.delete_source(source)
                stats["pruned_docs"] += 1

    return stats


def main() -> None:  # pragma: no cover - thin CLI wiring, exercised by the live smoke test
    from qdrant_client import QdrantClient

    from .config import settings
    from .embed import FastembedEmbedder
    from .scopes import load_scopes, warn_missing_folders
    from .store import StoreRegistry

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    ap = argparse.ArgumentParser(prog="kb_mcp.ingest")
    ap.add_argument("--prune", action="store_true", help="remove chunks for files no longer present")
    args = ap.parse_args()

    config = load_scopes(settings.scopes_file)
    warn_missing_folders(config, settings.knowledge_dir)

    registry = StoreRegistry(
        QdrantClient(url=settings.qdrant_url), config, settings.collection, settings.embed_dim
    )
    registry.ensure_all()
    emb = FastembedEmbedder(settings.embed_model, settings.embed_dim)

    stats = ingest(settings.knowledge_dir, config, registry, emb,
                   max_chars=settings.chunk_max_chars, overlap=settings.chunk_overlap_chars,
                   prune=args.prune)
    print(f"ingest done: {stats}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_ingest.py -q`
Expected: `9 passed`

- [ ] **Step 5: Run the whole suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: `43 passed`

- [ ] **Step 6: Commit**

```bash
git add kb-mcp-server/kb_mcp/ingest.py kb-mcp-server/tests/test_ingest.py
git commit -m "feat(kb): scope-aware ingest keyed by relative path

Top-level folder determines scope; documents are now identified by their path
relative to the corpus root. Fixes a silent data-loss bug where ev-core/README.md
and hr/README.md were treated as one document. Root-level files and undeclared
folders are skipped and reported, which also stops MANIFEST.md being indexed as
knowledge. Documents that parse to zero chunks are counted, surfacing scanned
PDFs that would otherwise look loaded."
```

---

## Task 4: Generate one MCP tool per profile

**Files:**
- Modify: `kb-mcp-server/kb_mcp/server.py`
- Modify: `kb-mcp-server/tests/test_server.py`

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_server.py` entirely:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_server.py -q`
Expected: `TypeError: build_server() got an unexpected keyword argument 'config'`

- [ ] **Step 3: Write the implementation**

Replace `kb_mcp/server.py` entirely:

```python
"""FastMCP server exposing one read-only kb_search tool per configured profile."""
from fastmcp import FastMCP

from .config import settings
from .scopes import Profile, ScopesConfig
from .store import search_many

_CONTRACT = (
    "Returns up to top_k chunks, each with its source document, location, the matching "
    "text, and a relevance score (higher = closer). ALWAYS cite the returned `source` "
    "when you use a chunk. If this returns an empty list, the knowledge base does not "
    "cover the question — say so; do NOT invent an answer. Hits below min_score are "
    "dropped as noise; omit min_score for the calibrated default, or pass 0 to see every "
    "hit when debugging retrieval."
)


def _description(profile: Profile) -> str:
    return f"Search the '{profile.name}' knowledge base. {profile.description} {_CONTRACT}"


def build_server(config: ScopesConfig | None = None, registry=None, embedder=None) -> FastMCP:
    if config is None or registry is None or embedder is None:  # pragma: no cover - real wiring
        from qdrant_client import QdrantClient

        from .embed import FastembedEmbedder
        from .scopes import load_scopes, warn_missing_folders
        from .store import StoreRegistry

        config = config or load_scopes(settings.scopes_file)
        warn_missing_folders(config, settings.knowledge_dir)
        if registry is None:
            registry = StoreRegistry(
                QdrantClient(url=settings.qdrant_url), config,
                settings.collection, settings.embed_dim,
            )
            registry.ensure_all()
        embedder = embedder or FastembedEmbedder(settings.embed_model, settings.embed_dim)

    mcp = FastMCP(name="EV Knowledge Base (read-only RAG)")

    for profile in config.profiles.values():
        _register(mcp, profile, registry, embedder)

    return mcp


def _register(mcp: FastMCP, profile: Profile, registry, embedder) -> None:
    """Bind the profile's scope filter server-side. The model never passes a
    scope, so it cannot request knowledge the agent was not granted."""
    pairs = registry.group(profile.scopes)

    async def kb_search(query: str, top_k: int = settings.default_top_k,
                        min_score: float | None = None) -> list[dict]:
        floor = min_score
        if floor is None:
            floor = profile.min_score if profile.min_score is not None else settings.min_score_default
        return search_many(pairs, embedder.embed([query])[0], top_k=top_k, min_score=floor)

    mcp.tool(name=f"kb_search_{profile.name}", description=_description(profile))(kb_search)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_server.py -q`
Expected: `9 passed`

- [ ] **Step 5: Run the whole suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: `49 passed`

- [ ] **Step 6: Commit**

```bash
git add kb-mcp-server/kb_mcp/server.py kb-mcp-server/tests/test_server.py
git commit -m "feat(kb): generate one kb_search tool per profile

Each profile becomes kb_search_<profile> with its scope filter bound
server-side, so the model never passes a scope and cannot reach knowledge its
agent was not granted. The generic unscoped kb_search no longer exists.
Per-profile min_score overrides the global default."
```

---

## Task 5: Corpus migration and live rebuild

**Files:**
- Create: `knowledge/scopes.yaml`
- Modify: `knowledge/.gitignore`
- Move: `knowledge/*.md` (3 sample docs) → `knowledge/ev-core/`
- Modify: `knowledge/MANIFEST.md`

- [ ] **Step 1: Move the sample documents into a scope folder**

```bash
cd /e/cjain/noc-mcp-agent/knowledge
mkdir -p ev-core
mv ocpp-status-errors.md runbook-stuck-connector.md charger-spec-sample.md ev-core/
ls ev-core/
```

Expected: the three files listed. `MANIFEST.md` stays at the root (it is the audit trail, not knowledge — and root files are now skipped by design).

- [ ] **Step 2: Create `knowledge/scopes.yaml`**

```yaml
# Knowledge scopes and the profiles that bundle them.
# A document's top-level folder under knowledge/ is its scope.
# Each profile becomes one MCP tool: kb_search_<profile>.
# Run `kb-ingest` after changing this file or the corpus.

scopes:
  ev-core:
    folder: ev-core

profiles:
  noc:
    scopes: [ev-core]
    description: >-
      EV/OCPP protocol reference, charger model specs and firmware notes, and
      NOC operational runbooks.
```

- [ ] **Step 3: Track the config in git**

Replace `knowledge/.gitignore` with:

```
*
!.gitignore
!MANIFEST.md
!scopes.yaml
```

- [ ] **Step 4: Update the manifest**

In `knowledge/MANIFEST.md`, replace the Phase-1 table with one that records scopes:

```markdown
## Phase 1 — sample corpus (synthetic + public OCPP; for pipeline validation)

| File | Scope | Source | Notes |
|------|-------|--------|-------|
| `ev-core/ocpp-status-errors.md` | ev-core | Public OCPP 1.6 (StatusNotification enums) | Charge-point statuses + errorCode meanings |
| `ev-core/runbook-stuck-connector.md` | ev-core | Synthetic | Runbook: connector lock failure / stuck cable |
| `ev-core/charger-spec-sample.md` | ev-core | Synthetic | Example DC charger model spec + firmware notes |
```

- [ ] **Step 5: Rebuild the image and restart**

```bash
cd /e/cjain/noc-mcp-agent/chat
docker compose build kb-mcp-server
docker compose up -d kb-mcp-server
```

Expected: `Container kb-mcp-server Started`

- [ ] **Step 6: Drop the stale collection**

The existing `ev_kb` points are keyed by bare filename and carry no `scope` field, so they must go. Five chunks — rebuilding is seconds.

```bash
MSYS_NO_PATHCONV=1 docker exec kb-mcp-server python -c "
from qdrant_client import QdrantClient
from kb_mcp.config import settings
c = QdrantClient(url=settings.qdrant_url)
if c.collection_exists(settings.collection):
    c.delete_collection(settings.collection)
    print('dropped', settings.collection)
"
```

Expected: `dropped ev_kb`

- [ ] **Step 7: Re-ingest**

```bash
MSYS_NO_PATHCONV=1 docker exec kb-mcp-server python -m kb_mcp.ingest --prune
```

Expected: `indexed_docs: 3`, `skipped_root_files: 1` (MANIFEST.md), `empty_docs: 0`

- [ ] **Step 8: Live retrieval smoke test**

```bash
cat > /tmp/kb_smoke.py <<'PY'
import asyncio, json
from fastmcp import Client

async def main():
    async with Client("http://localhost:8106/mcp") as c:
        print("tools:", sorted(t.name for t in await c.list_tools()))
        for q in ["what does ConnectorLockFailure mean",
                  "QFlex Neo 240 rated power and connectors",
                  "how do I bake sourdough bread"]:
            res = await c.call_tool("kb_search_noc", {"query": q})
            data = json.loads(res.content[0].text) if res.content else res.data
            print(f"\nQ: {q!r}")
            if not data:
                print("   -> EMPTY")
            for h in data:
                print(f"   [{h['score']:.3f}] {h['source']}")

asyncio.run(main())
PY
MSYS_NO_PATHCONV=1 docker cp /tmp/kb_smoke.py kb-mcp-server:/tmp/kb_smoke.py
MSYS_NO_PATHCONV=1 docker exec kb-mcp-server python /tmp/kb_smoke.py
```

Expected: `tools: ['kb_search_noc']`; the two on-topic queries return hits whose sources are prefixed `ev-core/`; the sourdough query returns `EMPTY`; **no `MANIFEST.md` in any result**.

- [ ] **Step 9: Commit**

```bash
cd /e/cjain/noc-mcp-agent
git add knowledge/.gitignore knowledge/scopes.yaml knowledge/MANIFEST.md
git commit -m "feat(kb): migrate sample corpus into the ev-core scope

Sample docs move under knowledge/ev-core/; scopes.yaml declares the ev-core
scope and the noc profile and is tracked in git so corpus governance is
reviewable while the documents stay private. MANIFEST.md now stays at the root
where it is skipped by ingest rather than indexed as knowledge."
```

---

## Task 6: Rewire the live agents

**Files:**
- Modify: `chat/agents/agents.json` (via idempotent transform)

⚠️ **Concurrency hazard:** `agents.json` is regenerated from Mongo by `export-agents`. An edit not restored to Mongo promptly is silently dropped. Steps 2 and 3 must run back to back.

- [ ] **Step 1: Write the idempotent transform**

Create `/tmp/rewire-kb.mjs`:

```javascript
// Idempotent: point the MCP-enabled agents at the profile-scoped KB tool.
import fs from 'fs';

const PATH = 'E:/cjain/noc-mcp-agent/chat/agents/agents.json';
const OLD_TOOL = 'kb_search_mcp_kb';
const NEW_TOOL = 'kb_search_noc_mcp_kb';
const OLD_NAME = 'The kb_search tool searches';
const NEW_NAME = 'The kb_search_noc tool searches';

const agents = JSON.parse(fs.readFileSync(PATH, 'utf8'));
let changed = 0;

for (const a of agents) {
  const tools = a.tools || [];
  if (tools.length === 0) { console.log(`SKIP  ${a.name} — no MCP tools`); continue; }

  const before = JSON.stringify([a.tools, a.instructions]);

  const i = tools.indexOf(OLD_TOOL);
  if (i !== -1) tools[i] = NEW_TOOL;
  else if (!tools.includes(NEW_TOOL)) tools.push(NEW_TOOL);
  a.tools = tools;

  if ((a.instructions || '').includes(OLD_NAME)) {
    a.instructions = a.instructions.split(OLD_NAME).join(NEW_NAME);
  }

  if (before !== JSON.stringify([a.tools, a.instructions])) changed++;
  console.log(`OK    ${a.name} — newTool=${a.tools.includes(NEW_TOOL)} oldTool=${a.tools.includes(OLD_TOOL)}`);
}

fs.writeFileSync(PATH, JSON.stringify(agents, null, 2) + '\n');
console.log(`\n${changed} agent(s) modified.`);
```

Run it twice to prove idempotency:

```bash
node /tmp/rewire-kb.mjs && echo "=== re-run ===" && node /tmp/rewire-kb.mjs
```

Expected: first run `3 agent(s) modified.`, second run `0 agent(s) modified.`

- [ ] **Step 2: Restore to Mongo immediately**

```bash
cd /e/cjain/noc-mcp-agent/chat/agents
MSYS_NO_PATHCONV=1 docker cp agents.json noc-librechat-mongo:/tmp/agents.restore.json
MSYS_NO_PATHCONV=1 docker cp _restore.js noc-librechat-mongo:/tmp/_restore.js
MSYS_NO_PATHCONV=1 docker exec noc-librechat-mongo mongosh --quiet LibreChat --file /tmp/_restore.js
```

Expected: `done: 4 agent(s)`

- [ ] **Step 3: Restart LibreChat and verify tool discovery**

```bash
cd /e/cjain/noc-mcp-agent/chat
docker compose restart api
```

Then confirm discovery (wait for startup to finish before running):

```bash
docker logs --since 5m noc-librechat 2>&1 | grep -E "\[MCP\]\[kb\] Tools"
```

Expected: `[MCP][kb] Tools: kb_search_noc`

- [ ] **Step 4: Verify live state in Mongo**

```bash
cat > /tmp/verify-kb.js <<'EOF'
db.agents.find({}, {name:1, tools:1, instructions:1}).forEach(a => {
  const t = a.tools || [];
  print([a.name, 'tools=' + t.length,
    'new=' + t.includes('kb_search_noc_mcp_kb'),
    'old=' + t.includes('kb_search_mcp_kb'),
    'clarify=' + (a.instructions||'').includes('CLARIFY BEFORE ACTING'),
    'czJira=' + t.filter(x => String(x).includes('cz_jira')).length].join(' | '));
});
EOF
MSYS_NO_PATHCONV=1 docker cp /tmp/verify-kb.js noc-librechat-mongo:/tmp/verify-kb.js
MSYS_NO_PATHCONV=1 docker exec noc-librechat-mongo mongosh --quiet LibreChat --file /tmp/verify-kb.js
MSYS_NO_PATHCONV=1 docker exec noc-librechat-mongo rm -f /tmp/agents.restore.json /tmp/_restore.js /tmp/verify-kb.js
```

Expected for the 3 MCP-enabled agents: `new=true`, `old=false`, `clarify=true`, and NOC Engineer `czJira=8`.

- [ ] **Step 5: Commit**

```bash
cd /e/cjain/noc-mcp-agent
git add chat/agents/agents.json
git commit -m "feat(kb): point NOC agents at the profile-scoped kb_search_noc tool

Replaces the unscoped kb_search with the noc profile's generated tool and
updates the KNOWLEDGE BASE instruction block to name it. Restored to Mongo in
the same step."
```

---

## Task 7: Documentation

**Files:**
- Modify: `kb-mcp-server/README.md`

> `.env.example` is **not** edited here. It matches the `.env*` deny rule in the
> repo's security settings and cannot be read or written by an agent. The new
> `SCOPES_FILE` setting is documented in the README instead; a human can add it
> to `.env.example` if they want it there.

- [ ] **Step 1: Document scopes in the README**

Add a section to `kb-mcp-server/README.md`:

```markdown
## Scopes and profiles

A document's top-level folder under `knowledge/` is its **scope**. `knowledge/scopes.yaml`
declares scopes and **profiles** (named bundles of scopes); each profile becomes one MCP
tool, `kb_search_<profile>`, whose scope filter is bound server-side. An agent is granted
knowledge by being given that tool — the model never passes a scope.

```yaml
scopes:
  ev-core:    { folder: ev-core }
  hr:         { folder: hr, confidential: true }   # own Qdrant collection

profiles:
  noc:
    scopes: [ev-core]
    description: "EV/OCPP reference, charger specs, NOC runbooks."
```

Non-confidential scopes share the `ev_kb` collection and are separated by a payload
filter. A scope marked `confidential: true` gets its own collection (`kb_<scope>`), so no
filter defect can expose it.

**Adding knowledge:** drop files into `knowledge/<scope>/`, then re-ingest:

```bash
docker exec kb-mcp-server python -m kb_mcp.ingest --prune
```

Files at the corpus root and folders no scope declares are skipped and reported — check
the ingest summary. `empty_docs` counts documents that produced no text, which is how
scanned PDFs show up.

**Adding an agent for a new purpose:** create the folder, add a scope and a profile to
`scopes.yaml`, re-ingest, restart the server, and grant the new `kb_search_<profile>` tool
to the agent.
```

- [ ] **Step 2: Document the new setting in the same README section**

```markdown
### Configuration

`SCOPES_FILE` — path to the scopes/profiles config inside the container.
Defaults to `/knowledge/scopes.yaml`. If it is missing or invalid the server
**refuses to start**: a knowledge base that silently comes up with zero scopes
would answer "not in the knowledge base" to everything while appearing healthy.
```

- [ ] **Step 3: Commit**

```bash
git add kb-mcp-server/README.md
git commit -m "docs(kb): document scopes, profiles, and adding a new agent's knowledge"
```

---

## Verification checklist

- [ ] `./.venv/Scripts/python.exe -m pytest -q` → **49 passed**, no Qdrant container, no model download
- [ ] `test_same_filename_in_two_scopes_does_not_collide` passes (the data-loss regression)
- [ ] `test_profile_cannot_retrieve_another_scopes_chunk` passes (the isolation claim)
- [ ] `test_store_failure_raises_rather_than_returning_empty` passes (failure ≠ "not in KB")
- [ ] Live: `[MCP][kb] Tools: kb_search_noc`
- [ ] Live: on-topic queries return `ev-core/`-prefixed sources; off-corpus returns empty
- [ ] Live: `MANIFEST.md` appears in no result
- [ ] Mongo: 3 agents have `kb_search_noc_mcp_kb`, none have `kb_search_mcp_kb`, CLARIFY intact, NOC Engineer `czJira=8`

## Out of scope

Per spec §12: no admin upload UI, no repo/code ingestion, no LibreChat `rag_api`/pgvector
stack, no per-user document upload. Real-document ingestion and `min_score` recalibration
are Phase 2, after this lands.
