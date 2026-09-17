# MCP Tool Registry

Machine-readable registry for every MCP tool exposed by this repository. Implements the
design in [`TOOL_REGISTRY_DESIGN.md`](../TOOL_REGISTRY_DESIGN.md) (repo root); the data here
is sourced from [`MCP_TOOL_INVENTORY.md`](../MCP_TOOL_INVENTORY.md), not reclassified.

This is **data plus a small access layer** — no policy engine, no UI, no agents, no
human-approval workflow. Those are future consumers of what's built here.

## Structure

```
registry/
  metadata/            system-defined, immutable at runtime — one file per MCP server
    noc.yaml             (184 tools)
    cms.yaml             (17 tools)
    jira.yaml             (5 tools)
    chargezone.yaml       (8 tools)
    ecog-sd.yaml          (5 tools)
    kb.yaml               (3 tools)
    docgen.yaml           (3 tools)
    platform.yaml         (8 tools)
  config/
    tool_config.yaml     admin-managed — one entry per tool, flat namespace
  schema/
    tool_metadata.schema.json   validates metadata/*.yaml — forbids enabled/approval_required
    tool_config.schema.json     validates config/tool_config.yaml — forbids capability/risk
  tool_registry/         Python access layer (the only supported way to read/write this data)
    metadata.py            load_metadata() — read-only
    config_store.py        YamlToolConfigStore — get/set/all
    loader.py               load_registry() — merges the two into ToolRecord
  tests/
    test_registry.py
  requirements.txt       PyYAML, jsonschema
```

233 tools total, matching `MCP_TOOL_INVENTORY.md` exactly (the inventory's own count differs
from an earlier "231" estimate — see that file's validation notes; this registry reflects the
same, source-verified 233).

## The two stores never mix

- **`metadata/*.yaml`** — `tool_name`, `domain`, `description`, `capability`, `risk`. Changed
  only by editing a YAML file and getting it reviewed, the same as a source-code change —
  because capability/risk are conclusions from reading a tool's actual implementation, not a
  runtime setting.
- **`config/tool_config.yaml`** — `enabled`, `approval_required` (+ `updated_by`/`updated_at`
  for audit). This is what a future Admin UI edits, live, without a deploy.

Each store's JSON Schema forbids the other store's fields (`additionalProperties: false`), so
this isn't just a convention — a metadata file with an `enabled` key, or a config entry with a
`risk` key, fails validation and the registry refuses to load. See §5 of
`TOOL_REGISTRY_DESIGN.md` for the reasoning.

## Using the access layer

```python
from tool_registry import load_registry, YamlToolConfigStore

registry = load_registry()                      # {tool_name: ToolRecord}
rec = registry["charger_ssh_execute"]
rec.capability, rec.risk                          # "DESTRUCTIVE", "CRITICAL" — from metadata
rec.enabled, rec.approval_required                # from config

store = YamlToolConfigStore()
store.set("ticket_verify", approval_required=True, actor="admin@example.com")
```

`load_registry()` is the only function Policy/Agents should call — it's the merged, read-only
view. `YamlToolConfigStore` is what a future Admin UI would call to change `enabled` /
`approval_required`; it has no parameter or code path that can touch `capability`/`risk`.

## Adding a tool

1. Add one entry to the owning server's `registry/metadata/<server>.yaml` (or create a new
   file for a new server — no existing file needs to change).
2. Add a matching entry to `registry/config/tool_config.yaml` with `enabled: true` and an
   `approval_required` seeded from the tool's risk (LOW/MEDIUM → `false`, HIGH/CRITICAL →
   `true`). If this step is missed, `load_registry()` does not fail the whole registry over
   one missing row — it applies the same risk-based default at load time and logs a warning
   so the gap stays visible instead of silently masked.
3. Nothing else changes. No other server's file, no schema, no loader code.

## Swapping the config store for a database later

`YamlToolConfigStore` implements `get(tool_name)`, `set(tool_name, *, enabled=, approval_required=,
actor=)`, and `all()`. A future DB-backed store (e.g. one `tool_config` table, `tool_name` as
primary key) should implement the identical three methods. `loader.load_registry()` and every
future Policy/Agent/UI consumer depend on that interface, not on YAML — replacing the class is
a one-file change, not a redesign.

## What is explicitly NOT here

No Policy/Authorization engine, no Approval Engine, no human-approval UI, no Admin UI, and no
per-agent tool grants — the registry has no agent-shaped fields at all. See
`TOOL_REGISTRY_DESIGN.md`'s closing section for the full list of deliberate exclusions.
