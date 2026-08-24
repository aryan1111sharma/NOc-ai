# NOC MCP — Remaining Tools Roadmap

This is a roadmap, not an implementation plan. It lists every NOC backend capability that
was **not** implemented in the first write-capability wave (tickets/alerts/diagnostics/
maintenance/charger-support QR tokens — see `services/mcp/noc/noc_mcp/{tickets,alerts,
diagnostics,maintenance,charger_support}.py`), sourced directly from the bundled OpenAPI
spec (`services/mcp/noc/noc_mcp/openapi/noc_openapi.json` — 210 total paths, 129 non-`GET`).
No API listed here was invented; every entry below is a real, existing backend operation.

---

## 1. Recommended Future Candidates

Reasonable next-wave work — no new authorization architecture needed, just prioritization.

### Tickets
| API | Method | What it does | Capability | Risk | Why deferred |
|---|---|---|---|---|---|
| `/tickets/{id}/attachments` | POST | Upload a file attachment to a ticket | WRITE | LOW | Not in the approved first wave; needs a file-upload story this server doesn't have yet (every existing tool is JSON in/out) |
| `/tickets/{id}/merge` | POST | Merge one ticket into another | WRITE | MEDIUM | Semantically more complex (data consolidation) than the core CRUD wave; lower daily-use frequency |
| `/ticket-notifications/read-all`, `/ticket-notifications/{id}/read` | POST | Mark notification(s) read | WRITE | LOW | Minor utility, not requested in this wave |
| `/ticket-settings` | PUT | Update general ticket-system settings | CONTROL | MEDIUM | Admin/config surface, not an operator-facing action |
| `/ticket-auto-rules` | POST/PUT | Create/update ticket automation rules | CONTROL | MEDIUM-HIGH | Changes future ticket routing behavior fleet-wide; wants its own review, not bundled into this wave |
| `/ticket-settings/sla-rules` | POST/PUT | Create/update SLA rules | CONTROL | MEDIUM | Same reasoning as auto-rules |

### Alerts
| API | Method | What it does | Capability | Risk | Why deferred |
|---|---|---|---|---|---|
| `/monitoring/dashboards` | POST | Save a dashboard configuration | WRITE | LOW | Low operational value as a conversational tool |
| `/monitoring/alert-rules` | POST/PUT | Create/update what triggers an alert | CONTROL | MEDIUM-HIGH | Changes future alerting behavior, not a point-in-time action like acknowledge/resolve; wants its own review |
| `/monitoring/alert-rules/auto-discover` | POST | Auto-create CloudWatch alert rules for AWS resources | CONTROL | MEDIUM | Same reasoning |

### Charger Operations
| API | Method | What it does | Capability | Risk | Why deferred |
|---|---|---|---|---|---|
| `/chargers` | POST | Create a new charger record | WRITE | LOW-MEDIUM | Fleet-inventory management, not a NOC-operator conversational action; low first-wave value |
| `/chargers/{id}` | PUT | Update charger metadata | WRITE | LOW-MEDIUM | Same reasoning |
| `/chargers/{id}/upload-chunk` | POST | "Upload file chunk for large file transfers" | **UNCLEAR** | UNCLEAR | Direction/purpose not established from the OpenAPI spec alone (could be firmware push, log upload, or something else) — needs a source-code or backend-team check before it can even be classified, let alone implemented |
| `/charger-models` | POST/PUT | Create/update a charger model | WRITE | LOW-MEDIUM | Admin/catalog management, low conversational value |

### Fleet Operations
| API | Method | What it does | Capability | Risk | Why deferred |
|---|---|---|---|---|---|
| `/cpos` | POST/PUT | Create/update a Charge Point Operator | WRITE | LOW-MEDIUM | Entity CRUD, low conversational value |
| `/customers` | POST/PUT | Create/update a customer | WRITE | LOW-MEDIUM | Same reasoning |
| `/vendors` | POST/PUT | Create/update a vendor | WRITE | LOW-MEDIUM | Same reasoning |
| `/nightly-restart/settings` | PUT | Update the recurring fleet-restart window/schedule | CONTROL | MEDIUM | Directly tied to the restart-execution endpoints in §2 — sequence after those, not before |

### Configuration
| API | Method | What it does | Capability | Risk | Why deferred |
|---|---|---|---|---|---|
| `/admin/rules/alarm-mappings` (+ `actions/append`) | POST/PUT | Define what an alarm maps to / triggers | CONTROL | MEDIUM-HIGH | Changes downstream automated behavior fleet-wide |
| `/admin/rules/genone-api` | POST/PUT | Vendor-gateway integration config | CONTROL | MEDIUM | Purpose not fully clear from the endpoint name alone; needs backend-team context first |
| `/admin/rule-files/upload` | POST | Upload a rules file | CONTROL | MEDIUM | Bulk config change via file upload, wants review |
| `/error-code-configs` | POST/PUT | Create/update error-code mappings | CONTROL | MEDIUM | Admin config, low conversational frequency |
| `/email-notification-settings`, `/smtp-settings` (+ `/test`, `/trigger-digest`) | PUT/POST | Outbound-notification admin config | CONTROL | LOW | One-time admin setup, not an ongoing NOC-operator action |

---

## 2. High-Risk — Requires Stronger Authorization Architecture Before Implementation

These are explicitly **not implemented** per this task's constraints. Unlike §1, these
aren't just lower-priority — they need a gating mechanism this server doesn't have yet
(the two-step prepare/execute + mandatory audit pattern already used for
`charger_ssh_execute`, extended with a real allowlist), not just "get to it later."

| API | Method | What it does | Capability | Risk | Notes |
|---|---|---|---|---|---|
| `/chargers/{id}/command` | POST | Sends a proxy command directly to a charger | CONTROL | **CRITICAL** | No visible allowlist in the spec — this is broader and less constrained than `charger_ssh_execute`. Must never be exposed as a thin wrapper; would need a curated, allowlisted command set mirroring `services/mcp/cms/cms_mcp/device_commands.py`'s `WRITE_ALLOWLIST` pattern |
| `/chargers/{id}/config/ocpp/{key}` | PUT | Remote-writes an OCPP config key on a live charger | CONTROL | **HIGH** | Can affect charging/billing/safety-relevant behavior; needs a key-level allowlist, not a blanket key parameter |
| `/chargers/bulk-whitelist` | POST | Bulk enable/disable many chargers at once | CONTROL | **HIGH** | Fleet-wide blast radius per call; needs a dry-run/preview step and a max-chargers-per-call limit |
| `/nightly-restart/jobs/run-now` | POST | Trigger a fleet-wide restart batch immediately | CONTROL | **HIGH** | Restarts live chargers, interrupts active sessions; needs the same class of gate as bulk-whitelist |
| `/nightly-restart/jobs/{id}/restart-skipped` | POST | Restart chargers a batch skipped | CONTROL | **HIGH** | Same reasoning |
| `/tickets/bulk-update` | POST | Mutate many tickets in one call | WRITE | **HIGH** | Same "one call, many records" risk class applied to business data instead of hardware |

---

## 3. Do-Not-Expose APIs

Not deferred — these should not become MCP tools under the current trust model at all,
per this task's explicit constraints and the prior discovery report.

### Users / Roles / RBAC (privilege-escalation surface)
`/auth/users/{id}` (create/update/delete), `/auth/users/{id}/denied-permissions`,
`/auth/users/{id}/extra-permissions`, `/auth/users/{id}/notification-preferences`,
`/roles` (+ `/permissions`), `/teams` (+ `/permissions`), `/admin/rules/rbac` — all
POST/PUT/DELETE. Capability: CONTROL/DESTRUCTIVE. Risk: **CRITICAL**. An LLM able to grant
permissions or create/delete accounts is a fundamentally different risk class than anything
in this codebase.

### Auth / password plumbing
`/auth/login`, `/auth/logout`, `/auth/refresh`, `/auth/register`, `/auth/me/change-password`.
This is exactly what `NocAuthClient` already uses for the service account — exposing it
would let an agent manage (or break) its own credentials.

### Raw SQL
`/query-executor` (POST — "Execute a SQL query"). Capability: DESTRUCTIVE. Risk:
**CRITICAL**. Full, unconstrained database access. Not a candidate for gating or
allowlisting at any point — this is categorically out of scope.

### Raw charger command / generic proxy writes
The write (`POST`/`PUT`/`DELETE`) variants of `/chargers/{id}/proxy` and
`/chargers/{id}/proxy/{port}/{path}`. This is exactly what `readonly.py`'s
`ReadOnlyProxyMiddleware` exists to block on the read-side generic proxy tool — the
non-`GET` OpenAPI operations for the same paths are excluded at the route-map level for
the identical reason.

### Internal / irrelevant systems (not backend capability gaps — out of scope by nature)
- `/releases`, `/releases/{id}/bugs`, `/releases/{id}/tasks` (+ comments/move) — an internal
  engineering release/bug tracker built into the NOC backend, unrelated to NOC operations.
- `genone-gateway` endpoints (`ExpertPostFullStateData`, `LiveFeeds`, `LogEvent*`,
  `PostStateData`, `UpdateExecutionStatus`) — machine-to-machine telemetry **ingestion**;
  an LLM calling these would be fabricating fleet data, not consuming it.
- `/public/tickets/raise`, `/public/tickets/{id}/attachments`, `/public/visits/confirm` —
  designed for unauthenticated end-customers (QR-scan flow), a different trust boundary
  than this internal, authenticated-service-account assistant.

### Deletes (all of them)
Every `DELETE` endpoint in the spec (`chargers`, `customers`, `cpos`, `vendors`,
`charger-models`, `tickets`, `ticket-attachments`, `ticket-auto-rules`, SLA rules,
alarm-mappings, error-code-configs, alert-rules, roles, teams, RBAC rules, genone-api
records) — 27 total. Capability: DESTRUCTIVE. Risk: **HIGH-CRITICAL**. Irreversible; no
compensating control pattern in this codebase currently reaches "safe enough for an LLM to
trigger deletion."
