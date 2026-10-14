"""Test doubles for LibreChat's own MongoDB, matching the duck-typed
contract session/mongo_reader.py depends on: `.conversations`/`.messages`,
each exposing `.find(query).sort().skip().limit()` and `.find_one(query)`
plus `.count_documents(query)`. Built as small hand-rolled fakes rather than
mongomock, mirroring this repo's existing "Fake" testing convention (see
services/llm-router/tests/fakes.py:FakeProviders).
"""

from __future__ import annotations

from typing import Any


def _matches(doc: dict, query: dict) -> bool:
    for key, expected in query.items():
        actual = doc.get(key)
        if isinstance(expected, dict):
            for op, opval in expected.items():
                if op == "$gte" and not (actual is not None and actual >= opval):
                    return False
                if op == "$lte" and not (actual is not None and actual <= opval):
                    return False
        elif actual != expected:
            return False
    return True


class FakeCursor:
    def __init__(self, docs: list[dict]):
        self._docs = list(docs)

    def sort(self, key: str, direction: int) -> "FakeCursor":
        self._docs.sort(key=lambda d: d.get(key), reverse=(direction == -1))
        return self

    def skip(self, n: int) -> "FakeCursor":
        self._docs = self._docs[n:]
        return self

    def limit(self, n: int) -> "FakeCursor":
        self._docs = self._docs[:n]
        return self

    def __iter__(self):
        return iter(self._docs)


class FakeCollection:
    def __init__(self, docs: list[dict]):
        self._docs = docs

    def find(self, query: dict | None = None) -> FakeCursor:
        query = query or {}
        return FakeCursor([d for d in self._docs if _matches(d, query)])

    def find_one(self, query: dict) -> dict | None:
        for doc in self._docs:
            if _matches(doc, query):
                return doc
        return None

    def count_documents(self, query: dict) -> int:
        return len(list(self.find(query)))


class FakeDB:
    def __init__(self, conversations: list[dict] | None = None, messages: list[dict] | None = None):
        self.conversations = FakeCollection(conversations or [])
        self.messages = FakeCollection(messages or [])


def tool_call_part(*, id: str, name: str, output: Any = None, args: Any = None) -> dict:
    return {
        "type": "tool_call",
        "tool_call": {"type": "tool_call", "id": id, "name": name, "output": output, "args": args},
    }
