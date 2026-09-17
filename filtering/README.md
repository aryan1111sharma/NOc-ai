# Fast Deterministic Tool Filtering

Implements [`FAST_TOOL_FILTERING_DESIGN.md`](../FAST_TOOL_FILTERING_DESIGN.md) (repo
root). Sits between the agent's available-tool set and the LLM:

```
available tools (LibreChat today; Policy later) → Fast Deterministic Tool Filtering → LLM
                                                       (performance/context only)
```

Filtering is **not** a permission or security layer. It can reduce what the LLM sees;
it can never expand it.

No LLM call, no embeddings, no vector search, no network or filesystem access at
request time - every operation is a regex, a dict/set lookup, or a sort over an
in-memory index built once and cached.

## Usage

```python
from tool_filtering import rank

result = rank(
    request_text="Why is charger CHG-123 offline?",
    session_context={"agent_id": "operations-agent", "recent_tool_names": []},
    allowed_tools=tools_available_to_this_agent,
)

result.tools    # tuple of the CALLER'S OWN tool objects, length <= 30, always a subset
result.trace    # FilterTrace - signals extracted, stage, scores, latency (see below)
```

### Input shapes accepted

`allowed_tools` entries need only a **name** (`tool_name` or `name`). Everything else is
optional and each field independently enables its own scoring signal:

| Field (aliases) | If absent |
|---|---|
| `tool_name` / `name` | **Required** - entry is skipped |
| `description` / `desc` | Treated as `""` (keyword signal weakens) |
| `domain` / `category` | Domain signal disabled |
| `mcp_server` / `server` | Server-hint signal disabled |
| `capability` | Capability signal disabled |
| `risk` | Sorts as LOW - no penalty for missing metadata |

Dataclasses, plain objects, and dicts all work. This matters because Policy/Agent Access
is designed but **not live**: today the set comes from LibreChat / a raw MCP tool
listing, which carries only a name and description. Filtering therefore works on that
shape directly (keyword + entity matching carry the ranking), rather than failing open
and returning everything.

To get the full signal set over a raw listing, pass `metadata_provider` - a
`(tool_name) -> mapping | None` callable that supplies `domain`/`capability`/`risk`/
`mcp_server` for tools whose objects lack them. It never overrides a field the object
already has. **This is the seam where Tool Registry enrichment plugs in later**; nothing
in `tool_filtering/` imports the `registry` package (only
`tests/test_registry_interop.py` does, to prove real interop).

## Safety invariant

**Results are the caller's own objects, by identity** - `normalize.py` keeps each input
object in `NormalizedTool.original`, and `engine.py` returns those. So
`result ⊆ allowed_tools` holds structurally, not merely by name equality.

Every index (`tool_filtering/index.py`) is built exclusively from `allowed_tools`;
nothing in this package reads the Tool Registry, a Policy store, or any other source
that could introduce a tool the caller didn't offer. Every failure path
(`tool_filtering/engine.py`) degrades to returning the full, unranked input set - never
to an empty result when tools were available, and never to a tool from outside it.

## Module map

| Module | Responsibility |
|---|---|
| `tool_filtering/normalize.py` | Tolerant adapter from any caller tool shape to the scoring fields; preserves original object identity |
| `tool_filtering/signals.py` | Tokenize the request; extract entities (charger/ticket IDs), capability hints (verb lexicon), domain hints (synonym table), server hints |
| `tool_filtering/index.py` | Build (and cache) term/domain/capability/server indexes from `allowed_tools` |
| `tool_filtering/scoring.py` | Fixed-weight scoring + deterministic tie-breaking |
| `tool_filtering/engine.py` | `rank()` - orchestrates structural narrowing, scoring, top-K, fallback |

## Observability

`FilterResult.trace` is a `FilterTrace` with: the extracted signals, authorized/candidate
counts, which fallback tier fired (`none`, `exact_match`, `skip_small_set`,
`stage1_widen`, `stage2_widen`, `error`, `empty`), the ranked list with a full score
breakdown per tool, and latency in milliseconds. It never includes tool descriptions
beyond tool_name, and never anything not already present in the request/config the
caller supplied.

## What this package deliberately does not do

No LLM calls, no embeddings/vector search, no semantic ranking, no router model
(see `FAST_TOOL_FILTERING_DESIGN.md` §10 for how each would plug in later without a
redesign), no agent identity, no UI, and no Policy logic - it consumes an
already-authorized set and only ever narrows or reorders it.
