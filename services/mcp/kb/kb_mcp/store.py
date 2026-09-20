"""Qdrant-backed chunk store. Point IDs are deterministic so re-upsert overwrites."""
import math
import re
import uuid
from collections import Counter

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue, MatchAny,
)

from .scopes import Scope, ScopesConfig, ScopesConfigError

_NS = uuid.UUID("00000000-0000-0000-0000-000000000001")

# Hybrid re-ranking: over-fetch this many dense candidates, then use lexical
# (BM25) overlap to re-order them before truncating to the caller's top_k.
# Bi-encoder cosine similarity blurs together chunks that share topic but
# differ in exact wording, so an exact term/ID/error code can rank below a
# looser paraphrase; BM25 recovers it by exact token overlap. Evaluated
# empirically against plain top-K, MMR, and cross-encoder reranking on the
# live KB corpus — hybrid RRF gave the best recall/precision/MRR without the
# 100x+ latency cost reranking added. Scoped to a small over-fetched pool
# (not a full-collection scan) so this stays cheap regardless of corpus size.
_POOL_MULTIPLIER = 4
_MIN_POOL = 20
_RRF_K = 60
_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def _bm25_scores(query_terms: list[str], docs_tokens: list[list[str]],
                  k1: float = 1.5, b: float = 0.75) -> list[float]:
    """Okapi BM25 scored only within the already-fetched candidate pool
    (not the full collection) — enough to fix ordering among dense
    candidates without a separate full-corpus index or cache to maintain."""
    n = len(docs_tokens)
    if n == 0:
        return []
    avgdl = sum(len(d) for d in docs_tokens) / n
    df: Counter = Counter()
    for d in docs_tokens:
        df.update(set(d))
    idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
    scores = []
    for d in docs_tokens:
        tf = Counter(d)
        dl = len(d) or 1
        s = 0.0
        for t in query_terms:
            f = tf.get(t, 0)
            if f == 0:
                continue
            s += idf.get(t, 0.0) * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / avgdl))
        scores.append(s)
    return scores


def _hybrid_rerank(hits: list[dict], query_text: str) -> list[dict]:
    """Fuse dense rank with lexical (BM25) rank via reciprocal rank fusion.
    Only the ORDER changes — each hit keeps its original dense cosine
    `score`, so min_score filtering and the tool's score contract are
    unaffected by this re-ranking."""
    query_terms = _tokenize(query_text)
    if not query_terms or len(hits) < 2:
        return hits
    docs_tokens = [_tokenize(h["text"]) for h in hits]
    bm25 = _bm25_scores(query_terms, docs_tokens)
    bm25_order = sorted(range(len(hits)), key=lambda i: bm25[i], reverse=True)
    bm25_rank = {i: r for r, i in enumerate(bm25_order) if bm25[i] > 0}
    fused = []
    for i, h in enumerate(hits):
        rrf = 1.0 / (_RRF_K + i + 1)
        if i in bm25_rank:
            rrf += 1.0 / (_RRF_K + bm25_rank[i] + 1)
        fused.append((rrf, h))
    fused.sort(key=lambda pair: pair[0], reverse=True)
    return [h for _, h in fused]


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
        """Union of `source` across every payload in this collection. When
        several non-confidential scopes share a collection, this returns
        sources from ALL of them, not just one scope — callers that need a
        single scope's sources must filter the result themselves."""
        seen, offset = set(), None
        while True:
            pts, offset = self.c.scroll(self.collection, limit=256, offset=offset, with_payload=True)
            for p in pts:
                seen.add(p.payload.get("source"))
            if offset is None:
                break
        return seen

    def list_documents(self, scopes: list[str] | None = None) -> list[dict]:
        """One row per source document: chunk count plus the provenance
        stamped at ingest.

        `known_sources` answers "what keys exist" for pruning; this answers
        "what is in the knowledge base" for a person. Curated documents carry
        no uploader, so those fields come back None rather than absent — a
        caller rendering a list should not have to special-case them.

        Provenance is merged across a document's chunks rather than taken
        from whichever chunk `scroll` happens to yield first: `scroll` order
        is point-ID (UUID) order, which is a hash of source+chunk_idx and so
        is unrelated to chunk index or ingest recency. A document can also
        genuinely have mixed provenance — e.g. a contributed file edited on
        disk gets picked up by the batch `ingest --prune` walk with no
        `extra_payload`, so only some of its chunks carry an uploader after
        that re-ingest. Any chunk that carries an uploader is evidence the
        document has one, so the first non-None value found wins.
        """
        if not self.c.collection_exists(self.collection):
            return []
        flt = None
        if scopes is not None:
            # Same "explicit empty list filters to nothing" contract as
            # search(): MatchAny(any=[]) matches zero points, it is not
            # treated as "no filter".
            flt = Filter(must=[FieldCondition(key="scope", match=MatchAny(any=list(scopes)))])

        docs: dict[str, dict] = {}
        offset = None
        while True:
            pts, offset = self.c.scroll(
                self.collection, scroll_filter=flt, limit=256, offset=offset, with_payload=True
            )
            for p in pts:
                pl = p.payload
                row = docs.setdefault(pl["source"], {
                    "source": pl["source"], "scope": pl.get("scope"), "chunks": 0,
                    "uploaded_by": None, "uploaded_at": None,
                })
                row["chunks"] += 1
                if row["uploaded_by"] is None:
                    row["uploaded_by"] = pl.get("uploaded_by")
                if row["uploaded_at"] is None:
                    row["uploaded_at"] = pl.get("uploaded_at")
            if offset is None:
                break
        return sorted(docs.values(), key=lambda r: r["source"])

    def search(self, vector: list[float], top_k: int, scopes: list[str] | None = None,
               query_text: str | None = None) -> list[dict]:
        # A scope-less collection (declared in config but never ingested) has
        # no collection yet; that reads as "nothing in the KB", not a crash.
        # Only this specific, checked condition returns [] — any other
        # failure (connection, timeout, malformed response) must propagate.
        if not self.c.collection_exists(self.collection):
            return []
        flt = None
        if scopes is not None:
            # Explicit empty list must filter to nothing, not be treated as
            # "no filter" — MatchAny(any=[]) correctly matches zero points.
            flt = Filter(must=[FieldCondition(key="scope", match=MatchAny(any=list(scopes)))])
        # query_text enables hybrid re-ranking: fetch a larger dense pool so
        # BM25 has candidates to re-order, then truncate back to top_k below.
        # With no query_text (existing callers/tests), behavior is unchanged.
        limit = max(top_k * _POOL_MULTIPLIER, _MIN_POOL) if query_text else top_k
        res = self.c.query_points(
            self.collection, query=vector, limit=limit, with_payload=True, query_filter=flt
        )
        out = []
        for p in res.points:
            pl = p.payload
            out.append({"source": pl["source"], "location": pl["location"],
                        "text": pl["text"], "score": p.score})
        if query_text:
            out = _hybrid_rerank(out, query_text)
        return out[:top_k]


class StoreRegistry:
    """Resolves a scope to the KbStore that holds it. One KbStore per
    collection, shared by every scope routed to that collection."""

    def __init__(self, client: QdrantClient, config: ScopesConfig,
                 shared_collection: str, dim: int):
        # Kept so a live reload can rebuild the registry against a new config
        # without reconnecting to Qdrant (see server._Plane.reload).
        self.client = client
        self._by_collection: dict[str, KbStore] = {}
        self._by_scope: dict[str, KbStore] = {}
        # A confidential scope's collection name is derived by slugifying —
        # two scope names can slugify to the same string, or coincide with
        # shared_collection (an env-configurable name). Either would silently
        # defeat the isolation this class exists to guarantee, so both are
        # config errors, not runtime surprises. scopes.py can't check this
        # itself: it knows nothing about Qdrant or collection naming.
        confidential_owner: dict[str, str] = {}  # collection name -> owning scope
        for name, scope in config.scopes.items():
            coll = collection_for(scope, shared_collection)
            if scope.confidential:
                if coll == shared_collection:
                    raise ScopesConfigError(
                        f"confidential scope '{name}' resolves to collection '{coll}', "
                        "which collides with the shared collection — no filter bug "
                        "should be able to expose confidential data, so this must "
                        "not overlap; rename the scope or the shared collection"
                    )
                if coll in confidential_owner:
                    raise ScopesConfigError(
                        f"confidential scopes '{confidential_owner[coll]}' and '{name}' "
                        f"both resolve to collection '{coll}' — rename one so their "
                        "slugified names no longer collide"
                    )
                confidential_owner[coll] = name
            if coll not in self._by_collection:
                self._by_collection[coll] = KbStore(client, coll, dim)
            self._by_scope[name] = self._by_collection[coll]

    def for_scope(self, scope_name: str) -> KbStore:
        try:
            return self._by_scope[scope_name]
        except KeyError:
            raise ScopesConfigError(
                f"unknown scope '{scope_name}'; known scopes: {sorted(self._by_scope)}"
            ) from None

    def stores(self) -> list[KbStore]:
        return list(self._by_collection.values())

    def ensure_all(self) -> None:
        for store in self._by_collection.values():
            store.ensure_collection()

    def group(self, scope_names: list[str]) -> list[tuple[KbStore, list[str]]]:
        """Group scopes by the store that holds them, so each collection is
        queried once with all of its relevant scopes."""
        grouped: dict[str, list[str]] = {}
        for name in scope_names:
            grouped.setdefault(self.for_scope(name).collection, []).append(name)
        return [(self._by_collection[coll], names) for coll, names in grouped.items()]


def search_many(pairs: list[tuple[KbStore, list[str]]], vector: list[float],
                *, top_k: int, min_score: float = 0.0,
                query_text: str | None = None) -> list[dict]:
    """Query each (store, scopes) pair with limit=top_k, then merge and truncate
    to top_k overall. Over-fetching per collection is what lets one collection
    supply all top_k results when it holds the best matches.

    query_text, when given, is passed through so each store can hybrid
    re-rank its own candidates before contributing them here; the score each
    hit carries is still the original dense cosine score, so min_score
    filtering below is unaffected."""
    hits: list[dict] = []
    for store, scopes in pairs:
        hits.extend(store.search(vector, top_k=top_k, scopes=scopes, query_text=query_text))
    hits.sort(key=lambda h: h["score"], reverse=True)
    return [h for h in hits if h["score"] >= min_score][:top_k]
