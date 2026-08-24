# Fast Deterministic Tool Filtering — Architecture Proposal

**Status: design only.** No filtering code, no agents, no UI, and no change to the Tool
Registry or Policy layer. This designs the layer between Policy/Agent Access (built as a
design) and the LLM:

```
User → Agent Identity → Policy/Agent Access → Fast Deterministic Tool Filtering → LLM → MCP → Backend
                              (security boundary)         (this document — NOT a security boundary)
```

**The one invariant everything below is built to protect:** Filtering's input is
*already* the Policy-authorized set for this agent — never the raw Tool Registry. There
is no code path by which this layer can rank, surface, or otherwise expose a tool Policy
did not authorize, because a denied tool's record is never passed into it. Every design
choice below optimizes relevance and latency inside that fixed boundary.

---

## 1. Filtering architecture

A pure function, no LLM, no network, no embeddings in v1:

```
rank(request_text, session_context, allowed_tools: set[ToolRecord]) -> ordered_list[0:K]
```

`allowed_tools` comes from `policy.allowed_tools(agent_id)` (already resolved to
Registry metadata: `mcp_server`, `domain`, `description`, `capability`, `risk`). Two
pieces of state are precomputed **once per Registry/Policy cache generation**, not per
request — this is what keeps the per-request path cheap:

- **Term index**: `term -> {tool_name, ...}`, built once from each tool's `description`,
  `domain`, and `tool_name` (split on `_`) tokens, over the *full* registry. Reused
  across every agent — indexing doesn't depend on who's asking.
- **Domain/capability/server indexes**: `domain -> {tool_name}`, `capability ->
  {tool_name}`, `mcp_server -> {tool_name}` — trivial groupings, also built once.

Per request, Filtering intersects these precomputed indexes with `allowed_tools` (a set
intersection, not a re-scan of descriptions) and scores only the resulting small
candidate union. Rebuilding cost is `O(total tools × description length)`, paid once
per cache refresh; per-request cost is `O(query terms + |candidates|)`, independent of
total catalog size.

---

## 2. Input signals

| Signal | Source | Cost | Notes |
|---|---|---|---|
| Request terms | Lowercase, tokenize, strip stopwords/punctuation | O(text length) | No stemming/lemmatization — adds cost for little gain at this scale |
| Entities | Regex against known ID shapes (`CHG-\d+`, `[A-Z]+-\d+` for Jira-style keys) + a small noun→domain synonym table (`"charger"→Fleet & Chargers, On-Charger Control Center`; `"ticket"→Tickets & Support`; `"alert"→Alarms & Monitoring`) | O(text length) | Hand-curated table, not learned |
| Capability hint | A small verb lexicon: `{why, what, show, check, is, list}→READ`, `{create, add, assign, close, resolve}→WRITE`, `{restart, reboot, execute, run, stop}→CONTROL/DESTRUCTIVE` | O(terms) | A *hint*, used as a soft boost, not a hard gate (§3) |
| Server hint | Explicit mention ("in Jira", "on the old CMS/portal") | O(terms) | Rare; only narrows when confidently matched |
| Tool tags | `registry` metadata does not currently have a `tags` field | — | **Extension point, not a dependency**: if added later, tags feed the term index with a higher weight; until then, `domain` + `description` + `tool_name` tokens are the entire keyword signal — the design must work with today's data, not a future migration |
| Session recency | Tool names used in the last N turns of this session, passed in by the caller | O(1) lookup | Small continuity bonus only, never a hard filter |

No signal here requires a model call — everything is a regex, a dict lookup, or a set
operation.

---

## 3. Filtering stages

A funnel, but a **soft** one: hard elimination only where a signal is confident and safe
(and always reversible by the fallback tier in §6).

```
Stage 0  Universe = allowed_tools (the input boundary — never exceeded)
Stage 1  Structural narrowing:
           - exact tool_name mention → short-circuit, return it directly
           - otherwise: candidates = UNION of tools with any positive evidence
               (server-hint postings ∪ domain postings ∪ matched-term postings)
             If that union is empty (no evidence at all) → keep the full Stage-0 set
Stage 2  Scoring (§4) over the Stage-1 candidates
Stage 3  Top-K selection with deterministic tie-breaking (§5)
Stage 4  Fallback safety net if Stage 2/3 produced too little signal (§6)
```

**Stage 1 is a union of positive evidence, not a chain of intersections.** A tool is a
candidate if it matches the server hint **or** any inferred domain **or** shares any
term with the request. Two properties follow:

- *It cannot wrongly hide a relevant tool.* A tool excluded here has **zero** matching
  signals, so its only possible score contribution is the small negative risk
  tie-break — it could never have outranked a tool with real evidence. Exclusion changes
  no ranking, only the work done to produce it.
- *It is what makes cost scale with the query, not the catalog.* Only matched-term
  postings lists are touched, never the full tool set. (An earlier design revision
  gated this behind a `MIN_CANDIDATES` floor; that turned out to be actively harmful —
  it discarded perfectly good 2-4 tool narrowings and fell back to scanning the entire
  catalog, costing ~60× at 5000 tools. Adaptive top-K, not a candidate-count floor, is
  the right mechanism for "don't return too few".)

The genuine starvation case — no term, domain, or server evidence anywhere (gibberish) —
keeps the full set and hands off to the Stage-2 safe-default fallback in §6.

---

## 4. Ranking / scoring logic

A fixed-weight linear sum, every term bounded and cheap:

```
score(tool) =
      W_domain      * domain_match(tool.domain, signals.domains)          # 0/1
    + W_capability   * capability_match(tool.capability, signals.caps)     # 0/1
    + W_entity        * entity_match(tool, signals.entities)                # 0/1
    + W_server         * server_match(tool.mcp_server, signals.server_hint)  # 0/1
    + W_keyword          * keyword_overlap(tool_terms, request_terms)         # 0.0-1.0 (Jaccard-style)
    + W_recency           * recently_used(tool.tool_name, session_recent)      # 0/1
    - W_risk_tiebreak      * risk_rank(tool.risk)                               # small nudge only
```

Example default weights (tunable constants, not learned): `W_domain=3, W_capability=2,
W_entity=3, W_server=2, W_keyword=2, W_recency=1, W_risk_tiebreak=0.1`. The risk term is
deliberately tiny — Policy already decided this tool is usable; here it only nudges
otherwise-tied candidates toward lower risk, it never suppresses a relevant HIGH/CRITICAL
tool the user clearly asked for.

`keyword_overlap` is `|tool_terms ∩ request_terms| / |request_terms|` where `tool_terms`
= tokens from `description` + `domain` + `tool_name.split('_')` (+ `tags` once that field
exists). All set/dict operations — no similarity model.

**Full score transparency is a first-class property, not an afterthought**: because
every term is a named, bounded contribution, the score for any tool can be broken down
into exactly which signals fired and by how much (§9) — something a vector-similarity
score fundamentally cannot offer without a separate explanation mechanism.

---

## 5. Top-K strategy

- Default `K = 20`, clamped `MIN_K = 10, MAX_K = 30` (matches the target "20-30
  candidates" for a large authorized set).
- **Adaptive, not rigid**: take every candidate scoring above a small relevance floor,
  then clamp the count into `[MIN_K, MAX_K]` — a strong single-domain match can return
  fewer than 20 without padding with noise; a weak/broad match still returns at least
  `MIN_K` so the LLM has real options.
- An exact tool-name match (Stage 1 short-circuit) is always included regardless of K.
- If `|allowed_tools| ≤ MAX_K`, **skip filtering entirely** and pass the whole authorized
  set through — filtering only pays for itself when the universe is large; a
  narrowly-scoped agent gains nothing from being filtered.
- **Deterministic tie-breaking** (fixed, documented order, so identical inputs always
  produce identical output — required for debugging and regression tests):
  1. Higher total score first.
  2. Lower `risk` (LOW < MEDIUM < HIGH < CRITICAL).
  3. Capability priority: `READ > WRITE > CONTROL > DESTRUCTIVE`.
  4. Alphabetical by `tool_name` (final, total-order guarantee).

---

## 6. Fallback behavior

Ordered safety net — each tier only engages if the one above produced too little:

1. **Stage 1 found no evidence at all** → keep the full Stage-0 universe as candidates
   (nothing was narrowed away, so nothing needs re-scoring).
2. **Stage 2 produces nothing above the relevance floor** (weak/no signal extracted at
   all, e.g. gibberish input) → widen to a **safe default ordering** over the full
   authorized set: capability `READ` first, then lowest risk, then session recency, then
   alphabetical — never an empty list.
3. **`allowed_tools` is already ≤ `MAX_K`** → pass through unfiltered (§5).
4. **Any internal error** (bad regex, corrupted index, unexpected data shape) → fail
   open to "return the full authorized set, unranked." Filtering's failure mode is
   *slower/noisier LLM context*, never *dropped access* and never a crashed request —
   the set returned on total failure is still exactly Policy's boundary, nothing more.

---

## 7. Example requests

**"Why is charger CHG-123 offline?"** (the brief's own example)
- Entity: `CHG-123` matches the charger-ID pattern → entity=charger. Verb "why...offline"
  → capability hint READ. Domain synonym: charger → `Fleet & Chargers`, `Telemetry &
  Sessions`, `On-Charger Control Center`, `Alarms & Monitoring`. No server hint.
- Stage 1 restricts to those domains (plenty of candidates remain → safe to cut).
- Top result set (assuming an agent authorized on both NOC and CMS): `charger_get_charging_status`,
  `get_charger_by_id`, `charger_get_network_status`, `get_charger_communication_health`,
  `charger_get_alarms`, `get_alarm_history`, `cms_get_charger_details`, ... — all READ,
  all charger/diagnostics-domain. Ticket/Jira/KB tools score ~0 and don't surface.

**"Create a ticket for the offline charger and assign it to Asha"**
- Verbs "create"/"assign" → capability hint WRITE. Domain: `Tickets & Support` (from
  "ticket"). Entity: charger (soft, secondary).
- `ticket_create`, `ticket_assign` rank highest; a couple of charger-diagnostic READ
  tools still surface with a lower score (the user likely needs charger context too) —
  illustrating that soft scoring, not hard elimination, is what lets an adjacent-but-useful
  tool survive into the top-K even when one intent dominates.

**"Help me with the NOC dashboard"**
- Weak/generic signal — domain guess only (`Fleet & Chargers`/reporting-adjacent), no
  strong entity or capability verb. Falls through to a broader top-K within that loose
  domain guess, ordered mostly by the tie-break chain (§5) rather than strong scores —
  shows soft narrowing without a confident cut.

**"asdkfj unrelated gibberish"**
- Zero domain/entity/keyword/capability matches → Stage 1 has nothing to narrow on,
  Stage 2 produces no candidate above the relevance floor → **fallback tier 2** engages:
  safe default ordering over the full authorized set (READ-first, lowest-risk-first,
  alphabetical) — never an empty tool list.

---

## 8. Expected latency characteristics

| Step | Cost | When paid |
|---|---|---|
| Term/domain/capability/server index build | `O(total tools × description length)` | Once per Registry/Policy cache refresh |
| Tokenize request | `O(request length)` — microseconds | Per request |
| Index lookups + set intersection with `allowed_tools` | `O(query terms)` | Per request |
| Score candidates | `O(|candidates|)`, typically tens, not hundreds | Per request |
| Sort + tie-break | `O(n log n)`, n ≈ tens | Per request |

No network I/O, no disk I/O, no model inference per request — everything is in-memory
data-structure work. Expected end-to-end cost: **low single-digit milliseconds**,
dominated by interpreter/data-structure overhead rather than algorithmic complexity.
Critically, **per-request cost does not scale with total catalog size** once the index
exists — it scales with query-term count and candidate-set size, which is what makes
this design viable for the "1000+ tools" future case (§10) without redesign.

---

## 9. Observability / debugging strategy

Every call emits a structured, inspectable trace — this is a direct payoff of choosing a
deterministic, additive scoring model over an opaque one:

```json
{
  "agent_id": "operations-agent",
  "extracted_signals": {"entities": ["charger"], "domains": ["Fleet & Chargers", "Alarms & Monitoring"],
                         "capabilities": ["READ"], "server_hint": null, "terms": ["why","charger","chg-123","offline"]},
  "authorized_count": 61, "post_narrowing_count": 14, "fallback_tier": "none",
  "top_k": [
    {"tool_name": "charger_get_charging_status", "score": 8.0,
     "breakdown": {"domain": 3, "entity": 3, "keyword": 0.5, "capability": 2, "risk_tiebreak": -0.1}}
  ]
}
```

- **"Why wasn't tool X offered?"** is answerable by computing its score breakdown
  directly — no black-box explanation layer needed, unlike a vector-similarity ranker.
- Recommend a standalone **replay/explain mode**: `explain(agent_id, request_text) ->
  same trace, no LLM call` — usable by an admin tuning weights/synonym tables, and as a
  regression check whenever a tool is added to the Registry (does its ranking behave as
  expected, in isolation, before it ships).
- Metrics worth tracking over time (not required for v1, but cheap to add): fallback-tier
  trigger rate (rising rate ⇒ synonym/verb tables need expansion), and "LLM picked a tool
  outside the offered top-K" rate (⇒ a real miss — the single most actionable signal for
  tuning weights).

---

## 10. Future extension points

| Extension | Where it plugs in | Why no redesign is needed |
|---|---|---|
| Semantic/vector ranking | One more additive term, `W_semantic * cosine(embedding)`, in the §4 formula; vector search can also seed *extra* candidates before scoring | Scoring is already a weighted sum — adding a term is additive, not a rewrite |
| Hierarchical routing (server → domain → tool) | Formalizes Stage 1 (§3) into an explicit multi-level index | Stage 1 is already a coarse-to-fine funnel; this only adds levels |
| Lightweight router model | Replaces/augments signal extraction (§2) only — must still emit the same `{entities, domains, capabilities, server_hint}` shape | Scoring/Top-K/fallback (§4-6) don't care whether signals came from a dict lookup or a model |
| Hybrid deterministic + semantic | Falls out of §4's additive formula directly — semantic term is optional and fails open to deterministic-only | No structural change; a weight can be set to 0 |
| 1000+ tool catalogs | Hierarchical routing (above) prunes before scoring; index rebuild can go incremental using the Registry's own version stamps instead of full rebuilds | Per-request cost already doesn't scale with catalog size (§8) — only the one-time index step does, and that's already cache-refresh-triggered, not per-request |

---

## Why this is faster than giving all tools to the LLM

Every tool sent to an LLM costs input tokens — name, description, and parameter
schema — **on every single turn**, not once. Filtering spends microseconds of local,
in-memory computation to shrink that payload *before* it reaches the model, trading a
fixed, negligible, local cost against a recurring, token-billed, latency-bearing remote
one. A larger tool list also measurably degrades a model's own tool-selection accuracy
(more near-duplicate, verbosely-named options to disambiguate among), so filtering isn't
purely a cost optimization — it improves correctness too.

## How this avoids the 39k-token / 216-tool problem

With ~216-233 authorized tools, each averaging roughly 150-200 tokens of name +
description + parameter schema (consistent with NOC's verbose, `operationId`-derived
tool names), the full set costs on the order of the observed ~39k tokens *before* any
actual conversation content. Narrowing to a top-K of 10-30 *before* that payload is
built shrinks it by roughly the same ratio (K⁄233), typically to low thousands of
tokens — while simultaneously handing the model a shorter, less confusable, more
relevant list to reason over.

## How this remains safe despite not being the security boundary

Its input parameter is structurally the Policy-authorized set only — never the raw Tool
Registry — so there is no code path by which it can surface a tool Policy denied,
mirroring how Policy itself can't reach a tool the Registry marked `enabled: false`.
Every failure mode here (wrong ranking, weak signal, an internal error) degrades to
*returning a differently-shaped subset of the already-authorized set*, never to
*returning something outside it* (§6 tier 4 explicitly fails open to the full authorized
set, not to "everything"). And regardless of what this layer or the LLM proposes, the
Policy design's execution-time gate (`policy.is_allowed(agent_id, tool_name)`,
re-checked immediately before the MCP call) is the actual, authoritative enforcement
point — this layer is purely additive to cost and UX, never subtractive to the security
guarantee Policy already established.

---

## What this proposal deliberately does not include

No filtering code, no semantic/vector ranking, no router model, no agents, no UI, and no
change to the Tool Registry or Policy implementation. Building `filtering/` (indexes,
scoring, the explain-mode CLI) is a mechanical follow-up once this shape is agreed on.
