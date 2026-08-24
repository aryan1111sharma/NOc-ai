# Policy / Agent Access Layer — Architecture Proposal

**Status: design only.** Nothing in this document is implemented. No MCP server, no
`registry/` file, no agent, and no UI was created or changed. This designs the layer
that sits between the Tool Registry (built) and Fast Tool Filtering / the LLM (not yet
built), per:

```
MCP Servers → Tool Registry → Policy / Agent Access → Fast Tool Filtering → LLM → MCP
                                    (this document)
```

---

## 1. Policy architecture

Policy answers exactly one question: **"Is Agent X allowed to use Tool Y?"** — nothing
more. It has a strict, narrow contract with its neighbors:

| Layer | Responsibility | What it must NOT do |
|---|---|---|
| Tool Registry | "What is this tool?" — `capability`, `risk`, `enabled`, `approval_required` | Know that agents exist |
| **Policy / Agent Access** | "Who can use this tool?" — combines Registry facts with per-agent grants | Know *why* a tool matters to a request, or filter for relevance |
| Fast Tool Filtering | "Which *allowed* tools are relevant to this request?" — narrows an already-authorized set for the LLM's context | Widen access, or make an authorization decision |
| LLM | "What should I do?" — picks from what Filtering offered | Be trusted as an enforcement point |

Policy reads the Tool Registry (`capability`, `risk`, `enabled`) as an input but never
writes to it, and never touches `services/mcp/*` — it doesn't know a tool's backend
call, only its registry-declared facts and its own grant data. Policy has no concept of
prompts, conversations, or models; it is a pure authorization function plus the admin
data behind it.

Two access patterns, both against the same underlying data:

```
Runtime (read-only, hot path):
  Fast Filtering  → policy.allowed_tools(agent_id)          [cached set]
  Execution gate  → policy.is_allowed(agent_id, tool_name)   [point check, uncached]

Admin (write path, future UI):
  PolicyStore.grant_capability(...) / .grant_tool(...) / .set_agent_enabled(...)
```

The runtime path is read-only and has no dependency on who is administering policy;
the write path is a separate concern (§7) that a future authenticated Admin UI drives.

---

## 2. Data model / schema

Three entities, physically separate from the Tool Registry and from each other by
concern (mirrors the Registry's own metadata/config split):

```
policy/
  agents/agents.yaml              # WHO exists
  grants/capability_grants.yaml   # broad, tier-based ALLOW
  grants/tool_grants.yaml         # specific, tool-level ALLOW or DENY
```

**Agent** (`agents.yaml`)
```yaml
agents:
  - agent_id: operations-agent      # stable, opaque key — never a display label
    display_name: "Operations Agent"
    description: "Front-line NOC operator assistant"
    enabled: true
    created_by: "admin@example.com"
    created_at: "2026-08-19T00:00:00Z"
```

**Capability grant** (`capability_grants.yaml`) — one row per (agent, capability); only
positive entries are ever stored — absence means "not granted," so there is no
capability-level "deny" record to reason about:
```yaml
grants:
  - agent_id: operations-agent
    capability: READ        # READ | CONTROL | WRITE | DESTRUCTIVE
    updated_by: "admin@example.com"
    updated_at: "2026-08-19T00:00:00Z"
```

**Tool grant** (`tool_grants.yaml`) — one row per (agent, tool_name), `effect` is
`ALLOW` or `DENY`:
```yaml
grants:
  - agent_id: operations-agent
    tool_name: charger_ssh_execute
    effect: DENY
    updated_by: "admin@example.com"
    updated_at: "2026-08-19T00:00:00Z"
```

No table stores `capability` or `risk` — those are always read live from the Tool
Registry at evaluation time, never copied in. This is the same structural-separation
technique the Registry itself uses (schemas with `additionalProperties: false`) applied
one layer up: a policy schema has no field that could express a classification.

---

## 3. Agent-policy relationship

- One **Agent** has zero or more **CapabilityGrants** and zero or more **ToolGrants** —
  a straightforward 1-to-many, no inheritance or grouping in v1 (a "team"/"role" concept
  that grants to many agents at once is a clean future extension — same shape, one more
  join table — not built now because nothing requires it yet).
- `agent_id` is **Policy's own namespace**. It is not a LibreChat agent ID, a model spec
  name, or anything else borrowed from a chat frontend. Whatever maps "the LibreChat
  agent a user is chatting with" to "the Policy `agent_id` that governs it" is a thin
  adapter that lives *outside* this layer — Policy neither imports nor assumes
  LibreChat's shape, satisfying "no LibreChat dependency" and "easy replacement of the
  UI/client" simultaneously: replace LibreChat, keep the same `agent_id` values, nothing
  in `policy/` changes.
- No agent name is ever hardcoded in code. `operations-agent` and `engineering-agent` in
  this document are *data* (rows in `agents.yaml`), exactly as `jira_search` is data in
  the Tool Registry, not a Python constant anywhere.

---

## 4. Capability vs. direct-tool permissions model

Both grant types are supported; they answer different administrative needs and combine
via precedence (§5), not by picking one.

| | Capability grant | Direct tool grant |
|---|---|---|
| Grants | A whole tier (e.g. all READ tools) | One named tool |
| Scales with new tools | Yes — a new READ tool is covered automatically | No — must be added explicitly |
| Use case | Broad roles ("this agent does read-only NOC support") | Exceptions: escalate one tool beyond the agent's tiers, or carve one tool out of a tier the agent otherwise has |
| Risk ceiling | **Only reaches LOW/MEDIUM-risk tools of that capability** | Reaches any risk tier, including HIGH/CRITICAL |

**The risk ceiling is the key safety rule this design adds beyond a plain RBAC tier
system:** granting an agent the `WRITE` or `DESTRUCTIVE` capability broadly must never
silently hand it a HIGH- or CRITICAL-risk tool the moment one is reclassified or added
to the Registry. A HIGH/CRITICAL tool is reachable *only* via an explicit `ALLOW` tool
grant, named individually, reviewable in a diff — never as a side effect of a tier
checkbox. This is why `charger_ssh_execute` (DESTRUCTIVE/CRITICAL) needs its own grant
row even for an agent broadly allowed `DESTRUCTIVE` — that combination should be rare
enough that "rare" is enforced structurally, not left to admin discipline.

---

## 5. Allow / deny precedence rules

Evaluated top to bottom; the first matching rule decides:

1. **Registry `enabled: false`** → **DENY**. A platform-wide kill switch, above every
   agent's policy — an incident responder pulls a tool for everyone without touching
   per-agent grants.
2. **Agent `enabled: false`** → **DENY** everything for that agent.
3. **Explicit tool grant, `effect: DENY`** → **DENY**. Beats any capability grant.
4. **Explicit tool grant, `effect: ALLOW`** → **ALLOW**. Reaches tools a capability grant
   can't (including HIGH/CRITICAL, per §4).
5. **Capability grant covers the tool's `capability`, AND the tool's `risk` is LOW or
   MEDIUM** → **ALLOW**.
6. **Otherwise** → **DENY** (fail closed — no grant of any kind means no access).

Restated as principles: *specific beats broad* (tool grant beats capability grant),
*deny beats allow at the same specificity* (a DENY tool grant beats an ALLOW tool
grant — though in practice only one `effect` should exist per (agent, tool) pair; if
duplicates ever occur, this rule resolves them safely), and *the system fails closed*.

Worked example from the brief:
```
Agent: operations-agent
Capabilities: READ, WRITE
Tool grants: DENY charger_ssh_execute
```
`charger_ssh_execute` is DESTRUCTIVE/CRITICAL — outside `{READ, WRITE}` and outside the
LOW/MEDIUM ceiling even if it weren't — so it would already be denied by rule 6 with no
explicit entry at all. The explicit `DENY` is still worth writing: it's self-documenting
in an audit view ("we know about this tool and deliberately excluded it," not "we never
thought about it"), and it stays correct even if a future reclassification ever moved
this tool to a lower risk tier.

---

## 6. Runtime lookup flow

```
1. Session/turn starts for agent_id
      ▼
2. Fast Filtering asks Policy for the agent's full allowed set (cached — §8)
      allowed = policy.allowed_tools(agent_id)
      ▼
3. Fast Filtering narrows `allowed` for relevance to this request
   (semantic/keyword ranking, top-K) — filtering only ever SUBSETS `allowed`,
   never adds anything Policy didn't already authorize
      ▼
4. LLM picks a tool from what Filtering offered
      ▼
5. Execution gate — a synchronous, uncached point check right before the MCP call:
      if not policy.is_allowed(agent_id, tool_name): reject + log
      ▼
6. If allowed: read `approval_required` from the Tool Registry (not from Policy —
   Policy doesn't own this fact) and hand off to the Approval Engine if true
   (not built; this is the same hook TOOL_REGISTRY_DESIGN.md already defined)
      ▼
7. Execute
```

Step 5 exists because step 2's cached set is a **performance optimization for
Filtering, not a security boundary** — it can be milliseconds to minutes stale, and an
LLM's tool choice is never proof of authorization (prompt injection, a stale cache, or a
bug in Filtering could all produce an out-of-policy tool name reaching this point).
Authorization is enforced authoritatively once, at step 5, against live data; step 2 is
allowed to be eventually-consistent because step 5 backstops it.

---

## 7. Future UI interaction flow

A `PolicyStore` interface (parallel to the Registry's `YamlToolConfigStore`) is the
*only* write path — a UI never edits `policy/*.yaml` directly, exactly as an Admin UI
never edits `registry/metadata/*.yaml` directly:

```
PolicyStore.create_agent(agent_id, display_name, ..., actor)
PolicyStore.set_agent_enabled(agent_id, enabled, actor)
PolicyStore.grant_capability(agent_id, capability, actor)
PolicyStore.revoke_capability(agent_id, capability, actor)
PolicyStore.grant_tool(agent_id, tool_name, effect, actor)     # ALLOW or DENY
PolicyStore.revoke_tool_grant(agent_id, tool_name, actor)
PolicyStore.get_effective_policy(agent_id) -> [ {tool_name, decision, reason}, ... ]
```

`get_effective_policy` pre-resolves precedence (§5) for every tool in the Registry, so
the UI renders "here is exactly what this agent can and cannot do, and why" without
reimplementing the rules itself — the UI is a thin, disposable client over this
interface, matching "easy replacement of the UI/client in the future."

**Flow:** Admin opens an Agents screen → creates or selects an agent → toggles
capability checkboxes (READ/CONTROL/WRITE/DESTRUCTIVE) → optionally searches the Tool
Registry's tool list to add a named ALLOW (escalation) or DENY (carve-out) → saves →
`PolicyStore` persists with `updated_by`/`updated_at` → the next tool call for that
agent observes the change on its next cache refresh (§8), no redeploy.

---

## 8. Fast lookup / caching strategy

Filtering needs `allowed_tools(agent_id)` cheaply, likely once per turn or per session,
against a registry that may hold 200+ tools.

- **Materialize, don't re-derive per call.** Precompute `allowed_tools(agent_id)` once
  per (agent, policy-version, registry-version) tuple and cache it in memory
  (dict/LRU keyed by `agent_id`).
- **Invalidate by version stamp, not TTL.** Both `PolicyStore` and the Tool Registry
  loader expose a cheap version marker (a monotonic counter or content hash bumped on
  every write / every registry reload). The cache checks the stamp before trusting a
  cached entry — correct on every write, no polling, no stale-window guesswork.
- **`is_allowed(agent_id, tool_name)` reuses the same cached set** (`tool_name in
  cached_set`) for the hot path — no separate mechanism — but the execution-gate call
  in §6 step 5 should force a fresh (or very-recently-invalidated) read, since that is
  the authoritative check, not the optimization.
- **The interface doesn't change when the backing store does.** Swapping YAML for a
  database later changes only what happens on a cache miss (a file read → a SQL query);
  `allowed_tools()` and `is_allowed()` keep the same signature for Filtering, the
  execution gate, and the UI.

---

## 9. Security considerations

- **Fail closed everywhere**: an agent with no record, no grants, a load error, or an
  unrecognized `tool_name` gets **DENY**, never default-allow.
- **Fast Filtering is not a trust boundary** (§6) — restated here because it's the
  single most likely place a future implementer accidentally treats a performance
  optimization as an enforcement point.
- **The risk ceiling (§4) is the main defense against capability-grant scope creep** —
  without it, "give operations-agent WRITE" silently expands every time a new WRITE
  tool is added anywhere, including a future HIGH-risk one nobody reviewed for this
  agent specifically.
- **The Registry `enabled` kill switch outranks all agent policy** (§5 rule 1) — an
  incident doesn't require touching N agents' grants to pull one tool platform-wide.
- **Full audit trail**: every grant/revoke records `updated_by`/`updated_at`, matching
  the Tool Registry's existing discipline — required before any DESTRUCTIVE/CRITICAL
  access review is credible.
- **No self-service escalation path**: the runtime read path (`is_allowed`,
  `allowed_tools`) is read-only; only `PolicyStore`'s write methods can change a grant,
  and those are assumed to require an authenticated admin caller once auth exists
  (auth itself is explicitly out of scope here — this only asserts *where* the
  boundary belongs).
- **Tool-name stability is a dependency this layer inherits, not solves.** Grants key on
  `tool_name` string; if the Tool Registry's names ever churn (e.g., NOC's
  OpenAPI-`operationId`-derived names shifting on a spec regeneration), every grant
  referencing that name silently orphans. Flagged as a limitation, not fixed here — a
  stable internal tool ID separate from the display name is a reasonable future
  Registry enhancement if this becomes a real problem.

---

## 10. Example policies

**Read-only operations agent** — broad, capability-only, no exceptions needed:
```yaml
agent_id: readonly-ops-agent
enabled: true
capabilities: [READ]
tool_grants: []
```
Resolves to: every enabled READ tool at LOW/MEDIUM risk (rule 5) — e.g. `jira_search`,
`kb_search_noc`, `platform_health`. Any READ tool that happened to be HIGH/CRITICAL risk
would still be denied (rule 6) unless explicitly granted — none exist in the current
inventory, but the rule holds regardless.

**Engineering agent** — broader tiers, plus one explicit escalation and one explicit
carve-out:
```yaml
agent_id: engineering-agent
enabled: true
capabilities: [READ, WRITE, CONTROL]
tool_grants:
  - tool_name: charger_ssh_prepare      # CONTROL/LOW - already covered by CONTROL tier;
                                          # listed for clarity/audit, not strictly required
    effect: ALLOW
  - tool_name: charger_ssh_execute       # DESTRUCTIVE/CRITICAL - capability tiers cannot
    effect: ALLOW                        # reach this; explicit escalation is the only path
  - tool_name: ticket_verify              # WRITE/MEDIUM - would be covered by the WRITE
    effect: DENY                          # tier, but verification is carved out for this role
```
Resolves to: all enabled READ/WRITE/CONTROL tools at LOW/MEDIUM risk, minus
`ticket_verify` (explicit deny wins), plus `charger_ssh_execute` (explicit allow reaches
past the risk ceiling) and `charger_ssh_prepare` (already covered, listed for clarity).

---

## 11. Recommended implementation structure

For a future implementation pass (not built now), mirroring the Tool Registry's own
layout and interface-first discipline:

```
policy/
  agents/agents.yaml
  grants/capability_grants.yaml
  grants/tool_grants.yaml
  schema/
    agent.schema.json
    capability_grant.schema.json
    tool_grant.schema.json
  policy_access/                 # Python package, analogous to registry/tool_registry/
    agents.py                     # load/validate agents.yaml
    grants.py                      # load/validate both grant files
    engine.py                       # is_allowed() / allowed_tools() - precedence (§5)
    store.py                        # PolicyStore - admin get/set/create/revoke (§7)
    cache.py                         # version-stamped materialized-set cache (§8)
  tests/
```

`engine.py` is the only module that implements precedence logic — it depends on
`tool_registry.load_registry()` (read-only) plus `agents.py`/`grants.py`, and exposes
nothing but `is_allowed()`/`allowed_tools()`. `store.py` is the only module with write
methods. This split means a future Approval Engine, Fast Filtering implementation, or
Admin UI backend each depends on exactly one of these two modules, never both, keeping
"who can use a tool" and "how that answer gets changed" as separable as the Registry
keeps "what a tool is" separable from "how that gets changed."

---

## What this proposal deliberately does not include

No implementation of `policy/` (no files created this pass), no Approval Engine, no
Admin UI, no authentication, no agent records, and no change to the Tool Registry or
any MCP server. Populating real `agents.yaml`/grant files and building `policy_access/`
is a mechanical follow-up once this shape is agreed on.
