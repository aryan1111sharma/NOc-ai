"""Precomputed indexes over an authorized tool set.

SAFETY INVARIANT: indexes are built ONLY from `allowed_tools` as handed to this module -
never from the Tool Registry, never from any other server's data. A tool that never
appears in `allowed_tools` cannot appear in any index built here, so nothing downstream
(scoring, top-K selection) has any way to surface it. See FAST_TOOL_FILTERING_DESIGN.md §1.

Indexes are expensive to build only in proportion to the authorized set's size, and that
cost is paid once and cached - not on every request. Two calls with the same set of
tool_names reuse the same ToolIndex object regardless of the exact object identities
handed in, since content is addressed by tool_name.

Tool objects are passed through `normalize.py` first, so a caller's entries need only a
name (`tool_name` or `name`); `mcp_server`/`domain`/`capability`/`risk` are optional and
simply disable their own signal when absent. This module does not require (and does not
read) `enabled`/`approval_required` - those are Policy's concern, resolved upstream.
"""
from __future__ import annotations

import re
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from typing import Iterable

from .normalize import NormalizedTool, normalize_tools

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_\-]*")


def _terms_for(tool: NormalizedTool) -> frozenset[str]:
    text = f"{tool.description} {tool.domain or ''} {tool.tool_name.replace('_', ' ')}"
    return frozenset(_WORD_RE.findall(text.lower()))


@dataclass(frozen=True)
class ToolIndex:
    tool_names: frozenset[str]
    by_name: dict            # tool_name -> NormalizedTool (.original holds the caller's object)
    term_index: dict          # term -> frozenset[tool_name]
    domain_index: dict         # domain -> frozenset[tool_name]
    capability_index: dict      # capability -> frozenset[tool_name]
    server_index: dict           # mcp_server -> frozenset[tool_name]
    tool_terms: dict               # tool_name -> frozenset[str] (for keyword overlap)


def build_index(allowed_tools: Iterable, metadata_provider=None) -> ToolIndex:
    by_name: dict = {}
    term_index: dict[str, set[str]] = defaultdict(set)
    domain_index: dict[str, set[str]] = defaultdict(set)
    capability_index: dict[str, set[str]] = defaultdict(set)
    server_index: dict[str, set[str]] = defaultdict(set)
    tool_terms: dict[str, frozenset[str]] = {}

    for tool in normalize_tools(allowed_tools, metadata_provider):
        by_name[tool.tool_name] = tool
        terms = _terms_for(tool)
        tool_terms[tool.tool_name] = terms
        for term in terms:
            term_index[term].add(tool.tool_name)
        # A missing facet is simply not indexed, so it can never match - rather than
        # being bucketed under a None key that a lookup could accidentally hit.
        if tool.domain is not None:
            domain_index[tool.domain].add(tool.tool_name)
        if tool.capability is not None:
            capability_index[tool.capability].add(tool.tool_name)
        if tool.mcp_server is not None:
            server_index[tool.mcp_server].add(tool.tool_name)

    return ToolIndex(
        tool_names=frozenset(by_name),
        by_name=by_name,
        term_index={k: frozenset(v) for k, v in term_index.items()},
        domain_index={k: frozenset(v) for k, v in domain_index.items()},
        capability_index={k: frozenset(v) for k, v in capability_index.items()},
        server_index={k: frozenset(v) for k, v in server_index.items()},
        tool_terms=tool_terms,
    )


class _LRUIndexCache:
    """Bounded LRU cache keyed by the *content* (set of tool_names) of an authorized
    set - not by agent_id (this module has no concept of agents; that's Policy's job,
    and it stays that way so this cache works for any caller). Two `rank()` calls in a
    row for the same agent's stable authorized set reuse one ToolIndex instead of
    rebuilding it, which is what makes "don't rebuild indexes on every request" true in
    practice, not just in principle.
    """

    def __init__(self, maxsize: int = 64) -> None:
        self._maxsize = maxsize
        self._store: OrderedDict[tuple, ToolIndex] = OrderedDict()

    def get_or_build(self, allowed_tools: Iterable, metadata_provider=None) -> ToolIndex:
        tools = list(allowed_tools)
        # Cheap key derived without normalizing: an entry with neither field is
        # unusable anyway and gets dropped during the build. The provider's identity is
        # part of the key because it can change the indexed facets for the SAME tool
        # names - without it, swapping providers would silently reuse a stale index.
        names = frozenset(
            (t.get("tool_name") or t.get("name")) if isinstance(t, dict)
            else (getattr(t, "tool_name", None) or getattr(t, "name", None))
            for t in tools
        )
        key = (names, id(metadata_provider) if metadata_provider is not None else None)
        cached = self._store.get(key)
        if cached is not None:
            self._store.move_to_end(key)
            return cached
        index = build_index(tools, metadata_provider)
        self._store[key] = index
        if len(self._store) > self._maxsize:
            self._store.popitem(last=False)
        return index

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)


_DEFAULT_CACHE = _LRUIndexCache()


def get_index(allowed_tools: Iterable, *, use_cache: bool = True,
               cache: "_LRUIndexCache | None" = None, metadata_provider=None) -> ToolIndex:
    if not use_cache:
        return build_index(allowed_tools, metadata_provider)
    return (cache or _DEFAULT_CACHE).get_or_build(allowed_tools, metadata_provider)


def clear_default_cache() -> None:
    """Test/ops hook - clears the module-level cache used when callers don't supply
    their own. Never needed for correctness (content-addressed keys mean a changed
    tool_name set just misses and rebuilds), only for freeing memory or test isolation."""
    _DEFAULT_CACHE.clear()
