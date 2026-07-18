# Knowledge Base — Manifest

Tracked list of documents loaded into the EV knowledge base (`kb_search`). The document
files themselves are **gitignored** (may be large/proprietary); this manifest records what
is loaded so the corpus is auditable.

Run `kb-ingest` after changing the `knowledge/` folder. See
`docs/superpowers/specs/2026-07-18-ev-knowledge-base-rag-design.md`.

## Phase 1 — sample corpus (synthetic + public OCPP; for pipeline validation)

| File | Source | Notes |
|------|--------|-------|
| `ocpp-status-errors.md` | Public OCPP 1.6 (StatusNotification enums) | Charge-point statuses + errorCode meanings |
| `runbook-stuck-connector.md` | Synthetic | Runbook: connector lock failure / stuck cable |
| `charger-spec-sample.md` | Synthetic | Example DC charger model spec + firmware notes |

## Phase 2 — real docs (to be added by maintainer)
_(Drop 3–5 representative real docs here and add their rows.)_
