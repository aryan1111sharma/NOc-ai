# EV Knowledge Base (RAG) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give all 4 NOC agents a read-only `kb_search` MCP tool that retrieves cited chunks from a self-hosted vector store of EV/SME documents, so they answer domain questions from real sources instead of guessing.

**Architecture:** A new Python MCP server `kb-mcp-server` (cloned from `cms-mcp-server`'s conventions, FastMCP v3, port 8106) embeds documents with a local BGE model (fastembed, CPU) and stores chunks in a **Qdrant** container. An idempotent `kb-ingest` CLI populates the store from a `knowledge/` folder; the `kb_search` tool retrieves top-k chunks with citations. Wired into `chat/docker-compose.yml` and `chat/librechat.yaml`; the `kb` tool is added to the agents.

**Tech Stack:** Python 3.12, FastMCP v3, `fastembed` (BAAI/bge-base-en-v1.5, 768-dim), `qdrant-client` + Qdrant container, PyMuPDF (PDF parsing), pytest + pytest-asyncio (in-memory Qdrant + fake embedder for tests).

**Spec:** [`../specs/2026-07-18-ev-knowledge-base-rag-design.md`](../specs/2026-07-18-ev-knowledge-base-rag-design.md)

---

## Conventions (established from the existing servers — follow exactly)
- Repo root holds one dir per server. New dir: `kb-mcp-server/`, package `kb_mcp`, entrypoint `python -m kb_mcp`.
- FastMCP: `from fastmcp import FastMCP`; tools are `async def` with bare `@mcp.tool`; the **docstring is the LLM-facing description**; tool names prefixed `kb_`.
- `build_server(store=None, embedder=None)` takes injectable deps so tests avoid a real Qdrant/model.
- Config via `pydantic-settings` `BaseSettings` (env = UPPER_SNAKE of field). HTTP run in `__main__.py` with `mcp.run(transport="http", host=…, port=…, path="/mcp", host_origin_protection=False)`.
- Tests: pytest, `pytest.ini` with `asyncio_mode = auto`; `conftest.py` sets required env before collection; drive tools through the in-memory `fastmcp.Client`. `pytest`/`pytest-asyncio` are dev-only (not in requirements.txt); `tests/` excluded via `.dockerignore`.
- **Port 8106 is free** (8100 noc, 8101 cms, 8102 jira, 8103 docgen, 8104 ecog, 8105 cz-jira). Qdrant: 6333/6334.

## File Structure
```
kb-mcp-server/
  kb_mcp/
    __init__.py     # docstring + __version__
    __main__.py     # HTTP entrypoint; builds real store+embedder
    config.py       # pydantic-settings Settings
    chunk.py        # pure text-chunking (Task 1)
    embed.py        # Embedder protocol + FastembedEmbedder (Task 2)
    store.py        # Qdrant wrapper: ensure/upsert/search/delete (Task 3)
    parse.py        # doc -> [(text, location)] for pdf/md/html/txt (Task 4)
    ingest.py       # idempotent ingest CLI (Task 5)
    server.py       # build_server() + kb_search tool (Task 6)
  tests/
    conftest.py  fakes.py  test_chunk.py  test_store.py
    test_parse.py  test_ingest.py  test_server.py
  requirements.txt  Dockerfile  .dockerignore  pytest.ini  .env.example  README.md
chat/docker-compose.yml   # + kb-mcp-server + qdrant + qdrant-data volume (Task 8)
chat/librechat.yaml       # + allowlist + kb mcpServers entry (Task 8)
chat/agents/agents.json   # + "kb" in mcpServerNames + instruction block (Task 10)
knowledge/                # gitignored doc volume; sample corpus in Task 9
```

All work happens in a new dir; commit after each task. Work from repo root `E:\cjain\noc-mcp-agent`.

---

## Task 1: Scaffold package + config + chunking (TDD)

**Files:**
- Create: `kb-mcp-server/kb_mcp/__init__.py`, `kb_mcp/config.py`, `kb_mcp/chunk.py`, `kb-mcp-server/pytest.ini`, `kb-mcp-server/tests/conftest.py`
- Test: `kb-mcp-server/tests/test_chunk.py`

- [ ] **Step 1: Create package skeleton + config + pytest config**

`kb-mcp-server/kb_mcp/__init__.py`:
```python
"""kb-mcp-server: read-only RAG knowledge base over EV/SME docs (Qdrant + local BGE)."""
__version__ = "0.1.0"
```

`kb-mcp-server/kb_mcp/config.py`:
```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    qdrant_url: str = "http://qdrant:6333"
    collection: str = "ev_kb"
    embed_model: str = "BAAI/bge-base-en-v1.5"  # 768-dim; bge-small-en-v1.5 = 384, faster
    embed_dim: int = 768                         # MUST match embed_model
    chunk_max_chars: int = 1800                  # ~450 tokens
    chunk_overlap_chars: int = 200
    default_top_k: int = 5
    knowledge_dir: str = "/knowledge"

    mcp_transport: str = "stdio"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8106


settings = Settings()
```

`kb-mcp-server/pytest.ini`:
```ini
[pytest]
asyncio_mode = auto
testpaths = tests
```

`kb-mcp-server/tests/conftest.py`:
```python
# kb_mcp.config has no required (default-less) fields, but keep tests hermetic.
import os
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")
```

- [ ] **Step 2: Write the failing chunk test**

`kb-mcp-server/tests/test_chunk.py`:
```python
from kb_mcp.chunk import chunk_text


def test_short_text_is_one_chunk():
    out = chunk_text("hello world", max_chars=100, overlap_chars=10)
    assert len(out) == 1
    assert out[0] == "hello world"


def test_long_text_splits_with_overlap():
    para = ("A" * 500 + "\n\n") * 6  # ~3000 chars across paragraphs
    out = chunk_text(para, max_chars=1000, overlap_chars=100)
    assert len(out) >= 3
    assert all(len(c) <= 1000 for c in out)
    # consecutive chunks overlap (tail of one appears at head region of next)
    assert out[0][-50:] in out[1]


def test_empty_text_yields_no_chunks():
    assert chunk_text("   ", max_chars=100, overlap_chars=10) == []
```

- [ ] **Step 3: Run it — expect FAIL (module missing)**

Run: `cd kb-mcp-server && python -m pytest tests/test_chunk.py -q`
Expected: FAIL — `ModuleNotFoundError: kb_mcp.chunk`.

- [ ] **Step 4: Implement `chunk.py`**

`kb-mcp-server/kb_mcp/chunk.py`:
```python
"""Boundary-aware text chunking with character overlap (pure, dependency-free)."""


def chunk_text(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    # Split on blank-line paragraph boundaries, then greedily pack.
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    cur = ""
    for p in paras:
        if cur and len(cur) + 2 + len(p) > max_chars:
            chunks.append(cur)
            tail = cur[-overlap_chars:] if overlap_chars else ""
            cur = (tail + "\n\n" + p).strip()
        else:
            cur = (cur + "\n\n" + p).strip() if cur else p
        # A single oversized paragraph: hard-split it.
        while len(cur) > max_chars:
            chunks.append(cur[:max_chars])
            cur = cur[max_chars - overlap_chars:] if overlap_chars else cur[max_chars:]
    if cur.strip():
        chunks.append(cur.strip())
    return chunks
```

- [ ] **Step 5: Run — expect PASS**

Run: `cd kb-mcp-server && python -m pytest tests/test_chunk.py -q`
Expected: 3 passed. (If `pytest`/`pytest-asyncio` aren't installed in the venv: `pip install pytest pytest-asyncio`.)

- [ ] **Step 6: Commit**
```bash
git add kb-mcp-server/kb_mcp/__init__.py kb-mcp-server/kb_mcp/config.py kb-mcp-server/kb_mcp/chunk.py kb-mcp-server/pytest.ini kb-mcp-server/tests/conftest.py kb-mcp-server/tests/test_chunk.py
git commit -m "feat(kb): scaffold kb-mcp-server package, config, and text chunking

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Embedder (protocol + fastembed impl + test fake)

**Files:**
- Create: `kb-mcp-server/kb_mcp/embed.py`, `kb-mcp-server/tests/fakes.py`
- Test: `kb-mcp-server/tests/test_embed_fake.py`

- [ ] **Step 1: Write the fake + its test (the failing test)**

`kb-mcp-server/tests/fakes.py`:
```python
"""Deterministic test doubles: a fake embedder + in-memory Qdrant helper."""
import hashlib


class FakeEmbedder:
    """Deterministic small-dim embeddings so tests need no model download."""
    dim = 8

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            h = hashlib.sha256(t.encode("utf-8")).digest()
            out.append([b / 255.0 for b in h[: self.dim]])
        return out
```

`kb-mcp-server/tests/test_embed_fake.py`:
```python
from tests.fakes import FakeEmbedder


def test_fake_embedder_is_deterministic_and_right_dim():
    e = FakeEmbedder()
    a = e.embed(["hello", "world"])
    assert len(a) == 2 and len(a[0]) == e.dim
    assert e.embed(["hello"])[0] == a[0]  # deterministic
```

- [ ] **Step 2: Run — expect PASS for the fake** (no impl needed yet)
Run: `cd kb-mcp-server && python -m pytest tests/test_embed_fake.py -q` → 1 passed.

- [ ] **Step 3: Implement the real embedder**

`kb-mcp-server/kb_mcp/embed.py`:
```python
"""Local embedding via fastembed (ONNX/CPU). No network at query time after model cache."""
from typing import Protocol


class Embedder(Protocol):
    dim: int
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class FastembedEmbedder:
    def __init__(self, model_name: str, dim: int):
        from fastembed import TextEmbedding  # imported lazily so tests don't need it
        self._model = TextEmbedding(model_name=model_name)
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return [vec.tolist() for vec in self._model.embed(texts)]
```

- [ ] **Step 4: Commit**
```bash
git add kb-mcp-server/kb_mcp/embed.py kb-mcp-server/tests/fakes.py kb-mcp-server/tests/test_embed_fake.py
git commit -m "feat(kb): embedder protocol + fastembed impl + deterministic test fake

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Vector store (Qdrant wrapper), tested in-memory

**Files:**
- Create: `kb-mcp-server/kb_mcp/store.py`
- Test: `kb-mcp-server/tests/test_store.py`

- [ ] **Step 1: Write the failing store test (uses in-memory Qdrant + FakeEmbedder dim)**

`kb-mcp-server/tests/test_store.py`:
```python
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
```

- [ ] **Step 2: Run — expect FAIL** (`kb_mcp.store` missing). Install client if needed: `pip install qdrant-client`.
Run: `cd kb-mcp-server && python -m pytest tests/test_store.py -q` → FAIL.

- [ ] **Step 3: Implement `store.py`**

`kb-mcp-server/kb_mcp/store.py`:
```python
"""Qdrant-backed chunk store. Point IDs are deterministic so re-upsert overwrites."""
import uuid
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue,
)

_NS = uuid.UUID("00000000-0000-0000-0000-0000000000kb".replace("kb", "01"))


def _point_id(source: str, chunk_idx: int) -> str:
    return str(uuid.uuid5(_NS, f"{source}:{chunk_idx}"))


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

    def search(self, vector: list[float], top_k: int) -> list[dict]:
        res = self.c.query_points(self.collection, query=vector, limit=top_k, with_payload=True)
        out = []
        for p in res.points:
            pl = p.payload
            out.append({"source": pl["source"], "location": pl["location"], "text": pl["text"], "score": p.score})
        return out
```

- [ ] **Step 4: Run — expect PASS**
Run: `cd kb-mcp-server && python -m pytest tests/test_store.py -q` → 3 passed.

- [ ] **Step 5: Commit**
```bash
git add kb-mcp-server/kb_mcp/store.py kb-mcp-server/tests/test_store.py
git commit -m "feat(kb): Qdrant store wrapper (ensure/upsert/search/delete/hash lookup)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Document parsing

**Files:**
- Create: `kb-mcp-server/kb_mcp/parse.py`
- Test: `kb-mcp-server/tests/test_parse.py`

- [ ] **Step 1: Write the failing parse test (text formats)**

`kb-mcp-server/tests/test_parse.py`:
```python
from pathlib import Path
from kb_mcp.parse import parse_file, SUPPORTED


def test_parses_markdown(tmp_path: Path):
    f = tmp_path / "doc.md"
    f.write_text("# Title\n\nBody paragraph about OCPP error 42.", encoding="utf-8")
    segs = parse_file(f)
    assert segs and any("OCPP error 42" in s["text"] for s in segs)
    assert all("location" in s for s in segs)


def test_parses_txt_and_html(tmp_path: Path):
    (tmp_path / "a.txt").write_text("plain text body", encoding="utf-8")
    (tmp_path / "b.html").write_text("<h1>H</h1><p>html body</p>", encoding="utf-8")
    assert "plain text body" in parse_file(tmp_path / "a.txt")[0]["text"]
    assert "html body" in parse_file(tmp_path / "b.html")[0]["text"]


def test_unsupported_extension_returns_empty(tmp_path: Path):
    (tmp_path / "x.bin").write_bytes(b"\x00\x01")
    assert parse_file(tmp_path / "x.bin") == []
    assert ".pdf" in SUPPORTED and ".md" in SUPPORTED
```

- [ ] **Step 2: Run — expect FAIL** (`kb_mcp.parse` missing).

- [ ] **Step 3: Implement `parse.py`**

`kb-mcp-server/kb_mcp/parse.py`:
```python
"""Parse a document into segments: [{text, location}]. location = page/heading hint."""
import re
from pathlib import Path

SUPPORTED = {".pdf", ".md", ".markdown", ".txt", ".html", ".htm"}


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+\n", "\n", re.sub(r"[ \t]+", " ", text)).strip()


def parse_file(path: Path) -> list[dict]:
    ext = path.suffix.lower()
    if ext not in SUPPORTED:
        return []
    if ext == ".pdf":
        import fitz  # PyMuPDF
        segs = []
        with fitz.open(path) as doc:
            for page in doc:
                t = page.get_text().strip()
                if t:
                    segs.append({"text": t, "location": f"p.{page.number + 1}"})
        return segs
    raw = path.read_text(encoding="utf-8", errors="replace")
    if ext in (".html", ".htm"):
        raw = _strip_html(raw)
    return [{"text": raw.strip(), "location": path.name}] if raw.strip() else []
```

- [ ] **Step 4: Run — expect PASS** (`pip install pymupdf` if the import path is exercised; text-format tests pass without it since PDF branch isn't hit).
Run: `cd kb-mcp-server && python -m pytest tests/test_parse.py -q` → 3 passed.

- [ ] **Step 5: Commit**
```bash
git add kb-mcp-server/kb_mcp/parse.py kb-mcp-server/tests/test_parse.py
git commit -m "feat(kb): document parsing for pdf/md/html/txt into located segments

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Ingestion CLI (idempotent, prune)

**Files:**
- Create: `kb-mcp-server/kb_mcp/ingest.py`
- Test: `kb-mcp-server/tests/test_ingest.py`

- [ ] **Step 1: Write the failing ingest test**

`kb-mcp-server/tests/test_ingest.py`:
```python
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
```

- [ ] **Step 2: Run — expect FAIL** (`kb_mcp.ingest` missing).

- [ ] **Step 3: Implement `ingest.py`**

`kb-mcp-server/kb_mcp/ingest.py`:
```python
"""Idempotent ingest: knowledge dir -> parse -> chunk -> embed -> upsert into the store."""
import argparse
import hashlib
from pathlib import Path

from .chunk import chunk_text
from .parse import parse_file, SUPPORTED


def _doc_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ingest(knowledge_dir, store, embedder, *, max_chars, overlap, prune=False) -> dict:
    root = Path(knowledge_dir)
    files = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED]
    on_disk = {p.name for p in files}
    stats = {"indexed_docs": 0, "skipped_docs": 0, "chunks": 0, "pruned_docs": 0}

    for path in files:
        source = path.name
        h = _doc_hash(path)
        if store.current_doc_hash(source) == h:
            stats["skipped_docs"] += 1
            continue
        store.delete_source(source)  # replace old version (no-op if new)
        chunks = []
        for seg in parse_file(path):
            for i, text in enumerate(chunk_text(seg["text"], max_chars, overlap)):
                chunks.append({
                    "source": source, "location": seg["location"],
                    "text": text, "doc_hash": h, "chunk_idx": len(chunks),
                })
        if chunks:
            store.upsert(chunks, embedder.embed([c["text"] for c in chunks]))
            stats["indexed_docs"] += 1
            stats["chunks"] += len(chunks)

    if prune:
        for source in store.known_sources() - on_disk:
            store.delete_source(source)
            stats["pruned_docs"] += 1
    return stats


def main() -> None:  # pragma: no cover - thin CLI wiring, exercised in Task 9 E2E
    from qdrant_client import QdrantClient
    from .config import settings
    from .embed import FastembedEmbedder
    from .store import KbStore

    ap = argparse.ArgumentParser(prog="kb_mcp.ingest")
    ap.add_argument("--prune", action="store_true", help="remove chunks for files no longer present")
    args = ap.parse_args()

    store = KbStore(QdrantClient(url=settings.qdrant_url), settings.collection, settings.embed_dim)
    store.ensure_collection()
    emb = FastembedEmbedder(settings.embed_model, settings.embed_dim)
    stats = ingest(settings.knowledge_dir, store, emb,
                   max_chars=settings.chunk_max_chars, overlap=settings.chunk_overlap_chars,
                   prune=args.prune)
    print(f"ingest done: {stats}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run — expect PASS**
Run: `cd kb-mcp-server && python -m pytest tests/test_ingest.py -q` → 3 passed.

- [ ] **Step 5: Commit**
```bash
git add kb-mcp-server/kb_mcp/ingest.py kb-mcp-server/tests/test_ingest.py
git commit -m "feat(kb): idempotent ingest CLI (hash-skip, re-index, prune)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: MCP server + `kb_search` tool

**Files:**
- Create: `kb-mcp-server/kb_mcp/server.py`, `kb-mcp-server/kb_mcp/__main__.py`
- Test: `kb-mcp-server/tests/test_server.py`

- [ ] **Step 1: Write the failing server test (drive the tool via FastMCP Client)**

`kb-mcp-server/tests/test_server.py`:
```python
import json
from qdrant_client import QdrantClient
from fastmcp import Client
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


async def test_kb_search_empty_when_no_store_content():
    e = FakeEmbedder()
    empty = KbStore(QdrantClient(":memory:"), collection="t2", dim=e.dim)
    empty.ensure_collection()
    server = build_server(store=empty, embedder=e)
    async with Client(server) as c:
        res = await c.call_tool("kb_search", {"query": "anything"})
    data = json.loads(res.content[0].text) if res.content else res.data
    assert data == []
```

- [ ] **Step 2: Run — expect FAIL** (`kb_mcp.server` missing). `pip install fastmcp` if needed.

- [ ] **Step 3: Implement `server.py` and `__main__.py`**

`kb-mcp-server/kb_mcp/server.py`:
```python
"""FastMCP server exposing the read-only kb_search tool."""
from fastmcp import FastMCP
from .config import settings


def build_server(store=None, embedder=None) -> FastMCP:
    if store is None or embedder is None:  # pragma: no cover - real wiring covered by Task 9
        from qdrant_client import QdrantClient
        from .embed import FastembedEmbedder
        from .store import KbStore
        store = store or KbStore(QdrantClient(url=settings.qdrant_url), settings.collection, settings.embed_dim)
        store.ensure_collection()
        embedder = embedder or FastembedEmbedder(settings.embed_model, settings.embed_dim)

    mcp = FastMCP(name="EV Knowledge Base (read-only RAG)")

    @mcp.tool
    async def kb_search(query: str, top_k: int = settings.default_top_k,
                        min_score: float | None = None) -> list[dict]:
        """Search the EV/charger knowledge base (manuals, OCPP spec, runbooks) for text
        relevant to the query. Returns up to top_k chunks, each with its source document,
        location, the matching text, and a relevance score (higher = closer). ALWAYS cite
        the returned `source` when you use a chunk. If this returns an empty list, the
        knowledge base does not cover the question — say so; do NOT invent an answer."""
        hits = store.search(embedder.embed([query])[0], top_k=top_k)
        if min_score is not None:
            hits = [h for h in hits if h["score"] >= min_score]
        return hits

    return mcp
```

`kb-mcp-server/kb_mcp/__main__.py`:
```python
from .config import settings
from .server import build_server


def main() -> None:
    mcp = build_server()
    if settings.mcp_transport.lower() == "http":
        mcp.run(transport="http", host=settings.mcp_host, port=settings.mcp_port,
                path="/mcp", host_origin_protection=False)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run — expect PASS**
Run: `cd kb-mcp-server && python -m pytest tests/test_server.py -q` → 2 passed. Then full suite: `python -m pytest -q` (all green).

- [ ] **Step 5: Commit**
```bash
git add kb-mcp-server/kb_mcp/server.py kb-mcp-server/kb_mcp/__main__.py kb-mcp-server/tests/test_server.py
git commit -m "feat(kb): FastMCP server with read-only kb_search tool (cited chunks)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Packaging & containerization

**Files:** Create `requirements.txt`, `Dockerfile`, `.dockerignore`, `.env.example`, `README.md` in `kb-mcp-server/`.

- [ ] **Step 1: `kb-mcp-server/requirements.txt`**
```
fastmcp>=3.4
pydantic-settings>=2.0
qdrant-client>=1.12
fastembed>=0.4
pymupdf>=1.24
```

- [ ] **Step 2: `kb-mcp-server/Dockerfile`**
```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY kb_mcp ./kb_mcp

ENV MCP_TRANSPORT=http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8106 \
    QDRANT_URL=http://qdrant:6333 \
    KNOWLEDGE_DIR=/knowledge

EXPOSE 8106

CMD ["python", "-m", "kb_mcp"]
```

- [ ] **Step 3: `kb-mcp-server/.dockerignore`**
```
.venv/
venv/
__pycache__/
*.pyc
.env
.env.*
!.env.example
tests/
.pytest_cache/
```

- [ ] **Step 4: `kb-mcp-server/.env.example`**
```
# kb-mcp-server config (real values in chat/.env when run via compose)
QDRANT_URL=http://qdrant:6333
KB_COLLECTION=ev_kb
EMBED_MODEL=BAAI/bge-base-en-v1.5
EMBED_DIM=768
KNOWLEDGE_DIR=/knowledge
MCP_TRANSPORT=http
MCP_HOST=0.0.0.0
MCP_PORT=8106
```

- [ ] **Step 5: `kb-mcp-server/README.md`** — short: purpose, env vars, `python -m kb_mcp` to serve, `python -m kb_mcp.ingest [--prune]` to index `KNOWLEDGE_DIR`, `python -m pytest` to test. (Write 15–25 lines mirroring `cms-mcp-server/README.md`.)

- [ ] **Step 6: Verify the image builds**
Run: `docker build -t kb-mcp-server:test kb-mcp-server`
Expected: builds clean (this also validates `requirements.txt` installs — fastembed pulls its ONNX runtime; first build is slow).

- [ ] **Step 7: Commit**
```bash
git add kb-mcp-server/requirements.txt kb-mcp-server/Dockerfile kb-mcp-server/.dockerignore kb-mcp-server/.env.example kb-mcp-server/README.md
git commit -m "feat(kb): packaging — requirements, Dockerfile, dockerignore, env example, README

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Wire into the stack (compose + librechat.yaml)

**Files:** Modify `chat/docker-compose.yml`, `chat/librechat.yaml`.

- [ ] **Step 1: Add the `qdrant` + `kb-mcp-server` services** (in `chat/docker-compose.yml`, alongside the other MCP servers)
```yaml
  qdrant:
    image: qdrant/qdrant:latest
    container_name: qdrant
    volumes:
      - qdrant-data:/qdrant/storage
    restart: unless-stopped

  # Read-only RAG knowledge base over EV/SME docs (Qdrant + local BGE embedder).
  kb-mcp-server:
    build: ../kb-mcp-server
    container_name: kb-mcp-server
    depends_on:
      - qdrant
    environment:
      - MCP_TRANSPORT=http
      - MCP_HOST=0.0.0.0
      - MCP_PORT=8106
      - QDRANT_URL=http://qdrant:6333
      - KNOWLEDGE_DIR=/knowledge
    volumes:
      - ../knowledge:/knowledge:ro
    restart: unless-stopped
```

- [ ] **Step 2: Declare the named volume** — add `qdrant-data:` under the top-level `volumes:` block in `chat/docker-compose.yml`.

- [ ] **Step 3: Register in `chat/librechat.yaml`** — add to `mcpSettings.allowedAddresses`:
```yaml
    - "kb-mcp-server:8106"
```
and add to the `mcpServers` block:
```yaml
  kb:
    type: streamable-http
    url: "http://kb-mcp-server:8106/mcp"
    timeout: 60000
```

- [ ] **Step 4: Validate compose config parses**
Run: `docker compose -f chat/docker-compose.yml config >NUL` (Git Bash: `docker compose -f chat/docker-compose.yml config >/dev/null`)
Expected: no error (YAML + interpolation valid).

- [ ] **Step 5: Commit**
```bash
git add chat/docker-compose.yml chat/librechat.yaml
git commit -m "feat(kb): wire kb-mcp-server + qdrant into compose and register kb in librechat

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Phase-1 sample corpus + live smoke test

**Files:** Create `knowledge/` (gitignored) with sample docs; add `knowledge/.gitignore` + `knowledge/MANIFEST.md` (tracked).

- [ ] **Step 1: Gitignore the corpus but track the manifest**
`knowledge/.gitignore`:
```
*
!.gitignore
!MANIFEST.md
```
`knowledge/MANIFEST.md`: a tracked list of what's loaded (start with the Phase-1 sample entries).

- [ ] **Step 2: Add a sample corpus** (Phase 1 — no maintainer data): create 3 small files in `knowledge/`:
  - `ocpp-errors-sample.md` — a short real OCPP status/error excerpt (public spec text) with a couple of error codes and meanings.
  - `runbook-stuck-connector.md` — a synthetic runbook: symptoms → steps.
  - `charger-spec-sample.md` — a synthetic model spec table (power, connectors, firmware notes).

- [ ] **Step 3: Bring up the stack and ingest**
```bash
docker compose -f chat/docker-compose.yml up -d qdrant kb-mcp-server
docker exec kb-mcp-server python -m kb_mcp.ingest
```
Expected: `ingest done: {'indexed_docs': 3, ...}` (first run downloads the BGE model — slow once).

- [ ] **Step 4: Smoke-test retrieval from inside the container**
```bash
docker exec kb-mcp-server python -c "from qdrant_client import QdrantClient; from kb_mcp.config import settings; from kb_mcp.store import KbStore; from kb_mcp.embed import FastembedEmbedder; s=KbStore(QdrantClient(url=settings.qdrant_url), settings.collection, settings.embed_dim); e=FastembedEmbedder(settings.embed_model, settings.embed_dim); import json; print(json.dumps(s.search(e.embed(['stuck connector'])[0], 2), indent=2))"
```
Expected: the runbook chunk ranks first, with `source` + `location` + `score`. Re-running `ingest` reports all 3 skipped (idempotent).

- [ ] **Step 5: Commit the tracked bits**
```bash
git add knowledge/.gitignore knowledge/MANIFEST.md
git commit -m "feat(kb): Phase-1 sample corpus manifest + gitignore (docs untracked)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Agent integration

**Files:** Modify `chat/agents/agents.json` (via a transform, per the concurrency hazard), then `restore-agents.ps1`.

> ⚠️ **Concurrency hazard:** `agents.json` is regenerated from Mongo by `export-agents`. Edit → **restore immediately** so a concurrent export can't drop it. Do Steps 2–3 back-to-back.

- [ ] **Step 1: Restart LibreChat to load the new `kb` MCP server**
```bash
docker compose -f chat/docker-compose.yml up -d api
```
(LibreChat reads `mcpServers` at startup; restart so `kb` is known before agents reference it.)

- [ ] **Step 2: Add `kb` to each agent + a usage instruction (idempotent transform)**
Write a scratchpad script `add-kb.mjs` that, for each of the 4 agents in `chat/agents/agents.json`:
  (a) adds `"kb"` to `mcpServerNames` if absent;
  (b) inserts this block into `instructions` (before `CHARGER LOOKUP (critical):`, guarded on absence — same anchor pattern as slot-filling):
  > **KNOWLEDGE BASE:** For how-it-works, procedure, spec, or error-code questions (OCPP, hardware, runbooks), call `kb_search` and answer from the returned chunks, **citing the `source`**. If `kb_search` returns nothing, say the knowledge base doesn't cover it — do not invent an answer.
Run it, then verify with node: all 4 agents have `kb` in `mcpServerNames` and the block present once; `agents.json` is valid JSON.

- [ ] **Step 3: Push live immediately + verify**
```bash
docker cp chat/agents/agents.json noc-librechat-mongo:/tmp/agents.restore.json
docker cp chat/agents/_restore.js noc-librechat-mongo:/tmp/_restore.js
docker exec noc-librechat-mongo mongosh --quiet LibreChat --file /tmp/_restore.js
docker exec noc-librechat-mongo rm -f /tmp/agents.restore.json /tmp/_restore.js
docker exec noc-librechat-mongo mongosh --quiet LibreChat --eval "print(db.agents.countDocuments({ mcpServerNames: 'kb' }))"
```
Expected: `restored ... done: 4 agent(s)`, then `4`.

- [ ] **Step 4: Commit**
```bash
git add chat/agents/agents.json
git commit -m "feat(kb): attach kb_search tool + KB usage instruction to all 4 agents

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 5: E2E validation (manual, LibreChat UI)** — in a FRESH chat per agent:
  - Ask a doc-answerable question (e.g. "what does OCPP error 42 mean?" / "procedure for a stuck connector") → the agent calls `kb_search` and answers **with a citation**.
  - Ask an out-of-corpus question → it says the knowledge base doesn't cover it (no confabulation).
  Record results. Non-deterministic — retry 2–3×.

---

## Deferred (not part of this plan)
- **Phase-2 real-corpus validation & chunk tuning** — maintainer drops 3–5 real docs into `knowledge/`, re-ingest, validate retrieval quality (spec §11).
- **Hermes tie-in** — the loop's judge calling `kb_search`; KB-gap feature discovery (loop spec §12).
- **Reranker / hybrid search / OCR for scanned PDFs** — only if needed (spec §12, §9.2).

---

## Self-Review

**Spec coverage:**
- New `kb-mcp-server`, self-hosted + MCP (D1, D7) → Tasks 1–8. ✅
- Qdrant store (D2) → Task 3, 8. ✅
- Local BGE via fastembed (D3) → Task 2, 7. ✅
- All 4 agents get `kb_search` (D5) → Task 10. ✅
- Residency / in-boundary (D6) → local embedder + local Qdrant + `knowledge/` volume; no external calls at query time (fastembed caches the model). ✅
- Ingestion folder → chunk→embed→upsert, idempotent + prune (§4.1) → Task 5. ✅
- `kb_search` read-only, cited, empty-on-miss (§4.2, §7) → Task 6. ✅
- Anti-confabulation instruction (§7) → Task 6 docstring + Task 10 agent block. ✅
- Staged testing: Phase-1 sample corpus (§11) → Task 9; Phase-2 deferred. ✅
- Config keys (§8) → Task 1 `config.py`. ✅

**Placeholder scan:** README (Task 7.5) and sample-doc *content* (Task 9.2) are described rather than transcribed — intentional (prose docs), with exact structure specified. All code files have complete contents. No TBD/TODO.

**Type/name consistency:** `KbStore(client, collection, dim)` with `ensure_collection/upsert/search/delete_source/current_doc_hash/known_sources`; `Embedder.embed(list[str])->list[list[float]]`; `chunk_text(text, max_chars, overlap_chars)`; `parse_file(path)->[{text,location}]`; `ingest(knowledge_dir, store, embedder, *, max_chars, overlap, prune)`; `build_server(store=None, embedder=None)`; tool `kb_search(query, top_k, min_score)`. Payload keys `source/location/text/doc_hash/chunk_idx` consistent across store, ingest, server, tests. ✅
