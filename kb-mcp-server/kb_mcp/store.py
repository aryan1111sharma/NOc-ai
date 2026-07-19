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
