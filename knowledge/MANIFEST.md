# Knowledge Base — Manifest

Tracked list of documents loaded into the EV knowledge base (`kb_search`). The document
files themselves are **gitignored** (may be large/proprietary); this manifest records what
is loaded so the corpus is auditable.

A document's **top-level folder is its scope** — see `scopes.yaml` for the declared
scopes and the profiles that bundle them. Files at the root (including this one) belong
to no scope and are deliberately not indexed.

Run `docker exec kb-mcp-server python -m kb_mcp.ingest --prune` after changing the
`knowledge/` folder. See
`docs/superpowers/specs/2026-07-19-agent-centric-knowledge-scopes-design.md`.

## Departmental knowledge bases

One confidential scope per control-plane department (see
`apps/ai-platform/librechat/roles/departments.json`). Each gets its **own Qdrant collection**
rather than a shared collection with a payload filter, so cross-department exposure cannot be
caused by a filter defect.

| Department | Scope / folder | Profile → MCP tool | Qdrant collection | Documents |
|---|---|---|---|---|
| Sales | `dept-sales` | `sales` → `kb_search_sales` | `kb_dept_sales` | 0 (empty) |
| Finance | `dept-finance` | `finance` → `kb_search_finance` | `kb_dept_finance` | 0 (empty) |
| NOC | `dept-noc` | `noc` → `kb_search_noc` | `kb_dept_noc` (+ shared `ev_kb`) | 0 (empty) |
| Service | `dept-service` | `service` → `kb_search_service` | `kb_dept_service` | 0 (empty) |

`noc` keeps `ev-core` alongside its own scope because the EV/OCPP reference is genuinely
cross-cutting and `kb_search_noc` was already bound to the existing NOC agent.

A department is granted its knowledge by giving an agent that department's `kb_search_*` tool
and sharing the agent to the matching `dept-*` group. The model never passes a scope and
cannot request one it was not given.

## Phase 1 — sample corpus (synthetic + public OCPP; for pipeline validation)

| File | Scope | Source | Notes |
|------|-------|--------|-------|
| `ev-core/ocpp-status-errors.md` | ev-core | Public OCPP 1.6 (StatusNotification enums) | Charge-point statuses + errorCode meanings |
| `ev-core/runbook-stuck-connector.md` | ev-core | Synthetic | Runbook: connector lock failure / stuck cable |
| `ev-core/charger-spec-sample.md` | ev-core | Synthetic | Example DC charger model spec + firmware notes |

## Phase 2 — OCPP 1.6J specification (official reference)

Full OCPP 1.6J spec (edition 2, 2017-09-28, Open Charge Alliance) as structured Markdown,
sourced from the internal `ocpp-parser` repo (`knowledge/standards/ocpp-1.6/`). The
inline `<json-schema>` blocks were stripped for clean operator retrieval; YAML frontmatter
retained (carries the OCA attribution — spec is **CC BY-ND 4.0**, internal reference use).

| File | Scope | Notes |
|------|-------|-------|
| `ev-core/ocpp-1.6-spec/00-Table-of-Contents.md` | ev-core | Spec index |
| `ev-core/ocpp-1.6-spec/02-Terminology-and-Conventions.md` | ev-core | Terminology |
| `ev-core/ocpp-1.6-spec/03-Introduction.md` | ev-core | Introduction / concepts |
| `ev-core/ocpp-1.6-spec/04-Operations-Initiated-by-Charge-Point.md` | ev-core | CP→CMS operations |
| `ev-core/ocpp-1.6-spec/05-Operations-Initiated-by-Central-System.md` | ev-core | CMS→CP operations |
| `ev-core/ocpp-1.6-spec/06-Messages.md` | ev-core | All message PDUs (fields, cardinality) |
| `ev-core/ocpp-1.6-spec/07-Types.md` | ev-core | Data types + enums |
| `ev-core/ocpp-1.6-spec/08-Firmware-and-Diagnostics-File-Transfer.md` | ev-core | Firmware/diagnostics transfer |
| `ev-core/ocpp-1.6-spec/09-Configuration-Keys.md` | ev-core | Configuration keys |
| `ev-core/ocpp-1.6-spec/J03–J07-*.md` | ev-core | JSON/WebSocket annex (connection, RPC, security, config) |

## Phase 3 — customer/hardware docs (to be added by maintainer)
_(Drop real charger manuals / customer runbooks here and add their rows.)_

---

### Future track (not yet in the KB) — `ocpp-parser` tooling

The `ocpp-parser` repo also contains **tools** (not knowledge): a client-log Parser, a
CMS Excel-log parser for customer logs (CZ/Chargezone live; Mahindra, MSIL planned), and
an OCPP transaction simulator ("training emulator" productization planned). These are
candidate **NOC solutions / future MCP integrations**, not KB documents. Revisit as a
separate feature if we want Manthan to drive or expose them.
