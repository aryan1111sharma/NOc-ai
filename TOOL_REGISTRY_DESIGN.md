# Tool Registry — Architecture Proposal (v1)

**Status: design only.** Nothing in this document is implemented. No MCP tool was modified,
no agent was created, no UI or policy engine was built. This proposes the *shape* of a
future centralized Tool Registry, building on the data already captured in
`MCP_TOOL_INVENTORY.md` (233 tools, 8 servers).

The core design decision this document makes: **system-defined metadata and admin-managed
configuration are two physically separate stores from day one**, joined only at read time.
Everything else follows from that split.

---

## 1. Recommended registry location

A new top-level directory, parallel to `services/`, `apps/`, `docs/`, `knowledge/`:

```
registry/
  metadata/              # system-defined — one file per MCP server
    noc.yaml
    cms.yaml
    jira.yaml
    chargezone.yaml
    ecog-sd.yaml
    kb.yaml
    docgen.yaml
    platform.yaml
  config/
    tool_config.yaml      # admin-managed — one flat store, all tools
  schema/
    tool_metadata.schema.json
    tool_config.schema.json
  README.md               # ownership + "how to add a tool" instructions
```

Why not inside `services/mcp/`: the registry describes tools, it isn't one. Putting it under
`services/mcp/<name>/` would tie tool metadata to a server's own release cycle and blur the
exact boundary this design depends on — that metadata is *about* a server, not *owned* by it.

Why per-server metadata files: it mirrors `services/mcp/<name>/` 1:1. Whoever owns the NOC
server edits `registry/metadata/noc.yaml` when they add a tool; nobody touring KB or Platform
changes needs to touch a shared monolith. **Adding a new MCP server means adding one new file
here — never restructuring existing ones.**

Why one flat file for config: admin-managed state isn't naturally partitioned by server from
an admin's point of view ("show me everything that needs approval" cuts across servers), and
it's the piece designed to be swapped for a real datastore later (§3), at which point "one
file" becomes "one table" — still one flat namespace keyed by `tool_name`.

The registry directory holds **data**, not a running service. A future `services/registry-api/`
(an MCP-adjacent HTTP/gRPC service, not itself an MCP tool server) would be the thing that
loads this data and serves the merged view to Policy, Agents, and the Admin UI — that's a
separate, later build, not part of this proposal.

---

## 2. Recommended format

| Store | v1 format | Why |
|---|---|---|
| `registry/metadata/*.yaml` | **YAML**, git-tracked | Matches this repo's existing convention (`knowledge/scopes.yaml`, `docker-compose.yml`, `librechat.yaml`) — human-diffable, PR-reviewable. Metadata changes should go through code review, same as a source-code change, because capability/risk are *derived from* source inspection, not opinion. |
| `registry/config/tool_config.yaml` | **YAML**, git-tracked, for v1 | No Admin UI or database exists yet — a git-tracked file is the honest v1 for "how does an admin change this today." Designed to migrate. |
| `registry/schema/*.json` | **JSON Schema** | Validates both stores at load time (uniqueness, enum membership, no orphan config entries — see §5). |

**Migration path for the config store**, planned from the start rather than retrofitted:
once a real Admin UI exists, `tool_config.yaml` is replaced by a small table —

```sql
CREATE TABLE tool_config (
  tool_name          TEXT PRIMARY KEY,
  enabled            BOOLEAN NOT NULL DEFAULT true,
  approval_required  BOOLEAN NOT NULL,
  updated_by         TEXT,
  updated_at         TIMESTAMPTZ
);
```

— behind the **same read/write interface** every consumer already uses:

```
ToolConfigStore.get(tool_name)   -> {enabled, approval_required, updated_by, updated_at}
ToolConfigStore.set(tool_name, *, enabled=None, approval_required=None, actor)
```

Policy, Agents, and the future UI never talk to "a YAML file" or "a database" directly — they
talk to `ToolConfigStore`. Swapping the file for a table changes one adapter implementation,
not every caller. This is *why* a live toggle doesn't require a git commit once the UI exists:
`ToolConfigStore.set(...)` writes straight to the table, and the next `ToolConfigStore.get(...)`
(by Policy, at call time) sees it immediately.

A generated JSON artifact (`registry/build/tool_registry.json`, analogous to NOC's bundled
`noc_openapi.json`) can be produced from the YAML sources as a build step, for consumers that
prefer a single machine-readable file over a directory of YAML — optional, not required for v1.

---

## 3. Example schema

### 3a. System-defined metadata (`registry/metadata/<server>.yaml`)

```yaml
schema_version: 1
mcp_server: jira            # matches the services/mcp/<name>/ directory name
tools:
  - tool_name: jira_search
    domain: "Issue Tracking"
    description: "Search Ecog Jira issues with a JQL query"
    capability: READ         # enum: READ | CONTROL | WRITE | DESTRUCTIVE
    risk: LOW                 # enum: LOW | MEDIUM | HIGH | CRITICAL
    source:
      classified_from: "source-inspection"   # or "openapi-generated"
      last_reviewed: "2026-08-19"
```

Fields are exactly the ones already established in `MCP_TOOL_INVENTORY.md` — this design adds
no new taxonomy, it gives the existing one a machine-readable home. `source.classified_from`
and `last_reviewed` are provenance, not policy — they record *how* a classification was
reached (e.g. "OpenAPI GET auto-exposure" vs. "hand-read the implementation") so a future
re-audit knows what to re-check first, without affecting how the tool behaves.

**This file has no `enabled` or `approval_required` field, structurally.** See §5.

### 3b. Admin-managed configuration (`registry/config/tool_config.yaml`)

```yaml
schema_version: 1
tools:
  jira_search:
    enabled: true
    approval_required: false
    updated_by: "system-default"
    updated_at: "2026-08-19T00:00:00Z"
```

**This file has no `capability` or `risk` field, structurally.** See §5.

Every tool gets exactly one config record, auto-seeded when it's first added to a metadata
file (see the risk → default-`approval_required` table in §5), then freely editable by an
admin forever after, completely decoupled from its risk tier from that point on.

### 3c. The merged view (what Policy/Agents actually query)

Neither file is queried directly by a consumer. A thin loader joins them on `tool_name`:

```
ToolRecord = {
  # from metadata — immutable at runtime
  tool_name, mcp_server, domain, description, capability, risk,
  # from config — admin-managed
  enabled, approval_required,
}
```

If a metadata entry has no matching config record (shouldn't happen post-seeding, but must be
handled), the loader applies the risk-based default from §5 and flags it — it does not treat
a missing config row as "reject the tool."

---

## 4. Example entries (one READ, one WRITE, one CRITICAL — real tools from the inventory)

**READ — `jira_search`**
```yaml
# metadata (registry/metadata/jira.yaml)
tool_name: jira_search
mcp_server: jira
domain: "Issue Tracking"
description: "Search Ecog Jira issues with a JQL query"
capability: READ
risk: LOW
```
```yaml
# config (registry/config/tool_config.yaml)
jira_search:
  enabled: true
  approval_required: false
```

**WRITE — `ticket_create`**
```yaml
# metadata (registry/metadata/noc.yaml)
tool_name: ticket_create
mcp_server: noc
domain: "Tickets & Support"
description: "Create a new ticket, optionally linked to a charger/session/alarm"
capability: WRITE
risk: LOW
```
```yaml
# config (registry/config/tool_config.yaml)
ticket_create:
  enabled: true
  approval_required: false
```

**CRITICAL — `charger_ssh_execute`** (the example from the task brief)
```yaml
# metadata (registry/metadata/noc.yaml)
tool_name: charger_ssh_execute
mcp_server: noc
domain: "SSH & Proxy"
description: >-
  Execute the staged command on a live charger's SSH PTY using the one-time
  confirmation token issued by charger_ssh_prepare (no command denylist)
capability: DESTRUCTIVE
risk: CRITICAL
```
```yaml
# config (registry/config/tool_config.yaml)
charger_ssh_execute:
  enabled: true
  approval_required: true    # can be toggled false by an admin — risk stays CRITICAL either way
```

If an admin later sets `charger_ssh_execute.approval_required: false`, the metadata file above
does not change and does not need to — `risk: CRITICAL` is untouched. That's the property the
task calls out explicitly, and it's enforced structurally (§5), not just by convention.

---

## 5. Immutable metadata vs. admin-managed fields

| | `capability`, `risk`, `description`, `domain` | `enabled`, `approval_required` |
|---|---|---|
| **What it answers** | What the tool inherently *is* | What the platform currently *allows* |
| **Changes when** | The implementation is re-inspected and reclassified (a PR, reviewed like code) | An admin decides policy (a UI action, no deploy) |
| **Lives in** | `registry/metadata/*.yaml` (git) | `registry/config/tool_config.yaml` → later a DB table |
| **Who can write it** | A developer, via PR review | An admin, via the future Admin UI (or, in v1, a PR to the config file) |
| **Effect of a change** | Re-audits how dangerous the tool *is* | Re-decides whether it currently *runs*, and whether a human must sign off |

**The independence is structural, not just documented.** `tool_metadata.schema.json`
disallows `enabled`/`approval_required` as properties on a metadata entry;
`tool_config.schema.json` disallows `capability`/`risk`/`description`/`domain` on a config
entry. Neither file is capable of expressing the other's concern — an admin cannot raise or
lower a tool's risk tier by any action the config store exposes, because the config store's
schema has no field that means "risk."

**Seeding defaults** (applied once, when a tool is first added to metadata — not re-applied
afterward, so it never fights an admin's later choice):

| Risk | Default `approval_required` |
|---|---|
| LOW | `false` |
| MEDIUM | `false` |
| HIGH | `true` |
| CRITICAL | `true` |

`enabled` always seeds `true` — a newly-added tool is assumed live unless an admin disables it.

---

## 6. How the future flow works

**Static pipeline — how a tool becomes known to the platform:**

```
MCP Tool                 (implementation exists in services/mcp/<name>/)
   │  developer adds one entry to registry/metadata/<name>.yaml
   ▼
Registry                 (metadata seeds a default config row in tool_config.yaml)
   │  Policy reads the merged (metadata ⋈ config) view
   ▼
Policy                   (evaluates enabled / approval_required / capability / risk,
   │                       plus whatever additional rules Policy owns — e.g. per-agent
   │                       grants, time-of-day, rate limits — none of which live in
   │                       the registry itself)
   ▼
Agent                    (only ever offered tools Policy has already cleared to exist
                           in its toolset — the registry never lists agents, and no
                           agent identity is stored here; "which agent may use which
                           tool" is a separate, future Agent Tool Grants concern that
                           sits inside Policy, not inside the Registry)
```

**Runtime call sequence — what happens on one actual tool invocation:**

```
Agent calls tool
   ▼
Policy check              — is enabled == true? (if false: reject, tool doesn't exist right now)
   ▼
if approval_required == true
   ▼
Approval Engine (future)  — not built yet; this is the hook this proposal creates
   ▼
Human approval UI (future)
   ▼
Execution                 — the actual MCP server call
```

`enabled` and `approval_required` are independent gates, both sourced from the same config
row: a disabled tool never reaches the approval question at all; an enabled tool with
`approval_required: false` skips straight to execution; an enabled tool with
`approval_required: true` stops at the (currently unbuilt) Approval Engine. The Approval
Engine and its UI are out of scope for this proposal — the hook (`approval_required`) is all
that's built.

---

## 7. How a future Admin UI would modify `enabled` / `approval_required`

1. UI reads the merged view via the Registry loader (§3c) — same data Policy sees, so the
   admin is looking at ground truth, not a stale copy.
2. UI calls `ToolConfigStore.set(tool_name, approval_required=True, actor=admin_user)` (or
   `enabled=`). This writes **only** to `registry/config/tool_config.yaml` (v1) or the
   `tool_config` table (post-migration) — it has no code path that can reach
   `registry/metadata/*.yaml`, by construction (different schema, different files/tables,
   no shared writer).
3. The write records `updated_by` / `updated_at` for audit purposes — necessary once this is
   admin-editable rather than developer-reviewed.
4. Because Policy reads the config store live at call time (not a compiled snapshot baked into
   an agent or an MCP server), the change takes effect on the **next tool call** — no MCP
   server restart, no agent redeploy, no code change. This is the concrete payoff of the
   read/write interface in §2: the UI, once built, is a thin client over `ToolConfigStore`,
   and nothing else in the stack needs to know the UI exists.

This also answers "replacing LibreChat later does not require redesigning the registry": the
registry and `ToolConfigStore` know nothing about LibreChat, agents, or chat UIs at all — they
expose tool facts and tool policy-state as data. Whatever sits in front (LibreChat today, or
something else later) consumes that data through Policy; the registry's contract doesn't
change when the frontend does.

---

## What this proposal deliberately does not include

- No Policy/Authorization engine implementation (§6's "Policy" box is a consumer contract,
  not code).
- No Approval Engine or human-approval UI (the hook exists; the engine behind it doesn't).
- No Admin UI implementation (§7 describes the interface it would use, not the UI itself).
- No per-agent tool grants (`registry/` has zero agent-shaped fields — that's a Policy-layer
  concern for later).
- No migration of all 233 inventory tools into `registry/metadata/*.yaml` yet — §4's three
  entries are illustrative. Populating the full registry from `MCP_TOOL_INVENTORY.md` is a
  mechanical follow-up once this shape is agreed on, not part of this architecture proposal.
- No actual `registry/` directory was created in this pass — this document specifies the
  design for that directory's future contents.
