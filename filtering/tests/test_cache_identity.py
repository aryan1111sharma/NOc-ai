"""The index cache must never leak one caller's tool objects into another's result.

`rank()` promises the caller gets back its OWN objects, which is what makes
`result subset-of allowed_tools` hold by identity rather than by name equality. The index
cache is keyed by content, so two requests carrying equal-but-distinct tool objects share
one ToolIndex - and every NormalizedTool in it holds a reference to whichever request
built it first.

Downstream that is not cosmetic: the proxy writes `[t.original for t in selected]` into
the outgoing body, so a cache hit would forward the FIRST request's tool entries. Two
requests whose names and descriptions match can still differ in the part neither the
index nor its key looks at - the JSON Schema in `parameters` - so an MCP server that
changes a tool's arguments would keep shipping the old schema to the provider until the
process restarted.
"""
from __future__ import annotations

from tool_filtering import rank
from tool_filtering.index import clear_default_cache
from tests.fakes import FakeTool


def _catalogue():
    """Large enough that filtering actually engages (> MAX_K)."""
    return [
        FakeTool(f"charger_op_{i:02d}", "noc", "Fleet & Chargers",
                 f"Charger operation number {i}", "READ", "LOW")
        for i in range(40)
    ]


def test_results_come_from_the_current_call_not_a_cached_one():
    clear_default_cache()
    first = _catalogue()
    second = _catalogue()  # equal content, entirely distinct objects

    rank("charger status", {}, first, k=20)
    result = rank("charger status", {}, second, k=20)

    by_identity = {id(t) for t in second}
    leaked = [t.tool_name for t in result.tools if id(t) not in by_identity]
    assert not leaked, f"returned objects belonging to an earlier call: {leaked}"


def test_subset_invariant_holds_by_identity_on_a_cache_hit():
    clear_default_cache()
    first = _catalogue()
    second = _catalogue()

    rank("charger status", {}, first, k=20)
    result = rank("charger status", {}, second, k=20)

    assert all(any(t is candidate for candidate in second) for t in result.tools)


def test_a_changed_description_is_not_served_from_cache():
    """The description is the largest input to keyword scoring, so a cache keyed on names
    alone would keep ranking against wording the caller no longer sends."""
    clear_default_cache()
    original = [
        FakeTool("t_00", "noc", "Fleet & Chargers", "charger telemetry snapshot", "READ", "LOW"),
        *[FakeTool(f"t_{i:02d}", "noc", "Fleet & Chargers", "unrelated filler text", "READ", "LOW")
          for i in range(1, 40)],
    ]
    reworded = [
        FakeTool("t_00", "noc", "Fleet & Chargers", "invoice reconciliation report", "READ", "LOW"),
        *[FakeTool(f"t_{i:02d}", "noc", "Fleet & Chargers", "unrelated filler text", "READ", "LOW")
          for i in range(1, 40)],
    ]
    rank("telemetry", {}, original, k=20)
    after = rank("invoice reconciliation", {}, reworded, k=20)
    assert after.tools[0].tool_name == "t_00"
