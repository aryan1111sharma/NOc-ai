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

## Phase 1 — sample corpus (synthetic + public OCPP; for pipeline validation)

| File | Scope | Source | Notes |
|------|-------|--------|-------|
| `ev-core/ocpp-status-errors.md` | ev-core | Public OCPP 1.6 (StatusNotification enums) | Charge-point statuses + errorCode meanings |
| `ev-core/runbook-stuck-connector.md` | ev-core | Synthetic | Runbook: connector lock failure / stuck cable |
| `ev-core/charger-spec-sample.md` | ev-core | Synthetic | Example DC charger model spec + firmware notes |

## Phase 2 — real docs (to be added by maintainer)
_(Drop 3–5 representative real docs here and add their rows.)_
