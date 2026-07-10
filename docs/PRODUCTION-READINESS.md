# Production-Readiness & Access-Control Backlog

Backlog for taking the NOC AI Assistant stack (LibreChat → GLM → read-only NOC/CMS
MCP servers) from a **localhost dev stack** to a **shareable, hardened deployment**,
plus the **role-based access-control** workstream.

> Source of truth for this effort. Work items are tackled **one at a time**:
> implement → satisfy the acceptance criterion (test) → check off → move on.
> Substantive/behavior-changing items go through the brainstorming skill before code.

## How to read this

- **Priority:** `P0` blocks any non-localhost deploy · `P1` security hardening ·
  `P2` reliability/ops · `P3` quality · `AC` access-control epic.
- Each item has an **acceptance criterion** — the observable check that proves it's done.
- `[ ]` open · `[~]` in progress · `[x]` done.

## ⚠ Decisions that gate external sharing (get sign-off)

- [ ] **D1 — Data residency of GLM / z.ai.** All prompts **and tool results** (charger
  telemetry, tickets, customer data) are sent to Zhipu (z.ai, China-based) for inference
  (`chat/librechat.yaml`). Internal use may accept this; **external/customer-facing use
  needs explicit approval** or a model swap (in-region / self-hosted / approved provider).
  → *Decision:* acceptable for internal? for external? (record outcome here)
- [ ] **D2 — Shared service-account identity.** Both MCP servers authenticate with **one**
  `NOC_EMAIL` token (`mcp-server/noc_mcp/auth.py`), so every chat user reads the **entire
  fleet**. Restricting *tools* does not restrict *data*. Decide the target model:
  per-user NOC tokens, a role→scope map, or separate scoped instances per audience.
  → *Decision:* (record outcome here) — drives **AC-6**.
- [x] **D3 — Cloud & portability (DECIDED).** Stage 1 = **AWS**; architecture must avoid
  lock-in so a later move to a cheaper cloud/provider is a config change, not a rewrite.
  Realized in the **DEP** epic (EKS + Terraform + open stores + S3-compatible API).
- [ ] **D4 — Capacity + retention scope (confirm to finalize sizing):**
  (a) "500 users at a time" = concurrent **sessions** (current assumption) or simultaneous
  **in-flight requests**? (b) data retention **global** or **per-role/per-user**?
  (c) **hard delete** vs **soft-delete + purge**? (d) can the **NOC backend** take the
  projected read load? → drives **DEP** sizing, **DR-2**.

---

## P0 — Blockers (before any shared deploy)

- [ ] **P0-1 — TLS / reverse proxy.** Put LibreChat behind Caddy/nginx/Cloudflare Tunnel;
  HTTPS terminated; only the proxy is internet-facing.
  *Accept:* app reachable **only** over `https://`, valid cert; direct `http://host:3080`
  not reachable from outside.
- [ ] **P0-2 — Close open registration.** `ALLOW_REGISTRATION=false`; add SSO/OIDC scoped
  to `@quenchchargers.com` or pre-provision accounts (`chat/docker-compose.yml:9`).
  *Accept:* a non-org email cannot self-register or log in.
- [ ] **P0-3 — Keep MCP ports internal.** Never publish `:8100`/`:8101`; they have **no
  inbound auth**. Confirm only `3080` (via proxy) is exposed.
  *Accept:* `curl http://<host>:8100/mcp` from outside the Docker network fails/refused.
- [ ] **P0-4 — Lock charger proxy to GET.** Enforce `method=GET` in code on
  `proxy_api_call_path_based_...`; the catalog is descriptive only
  (`mcp-server/README.md:75-77`).
  *Accept:* a non-GET call through the proxy tool is rejected server-side (unit test).
- [ ] **P0-5 — MongoDB auth.** Enable auth + strong credential on `mongo:7`
  (`chat/docker-compose.yml:35-40`).
  *Accept:* unauthenticated `mongosh` connection is refused; app still works with creds.
- [ ] **P0-6 — Secrets management.** Inject `GLM_API_KEY`, NOC/CMS creds,
  `MEILI_MASTER_KEY`, `JWT_SECRET`, `CREDS_KEY` via Docker/cloud secrets — not a
  plaintext `.env` baked into an image.
  *Accept:* no secret present in the built image layers or in git; app boots from injected secrets.
- [ ] **P0-7 — Move off the GLM Coding Plan.** The configured z.ai coding endpoint
  (`chat/librechat.yaml`) **prohibits multi-user access** and caps concurrency to ~1 in-flight
  request. Switch to pay-as-you-go GLM (or an approved/self-hosted provider) with concurrency
  raised + per-token billing. **Blocks any multi-user launch.**
  *Accept:* stack runs on a provider/tier whose ToS permits multi-user and whose concurrency
  ≥ projected peak in-flight calls.

## P1 — Security hardening

- [ ] **P1-1 — Read-only service accounts.** Confirm `NOC_EMAIL` and `CMS_USER` hold
  **only** read roles (defense in depth behind the GET route map).
  *Accept:* the account is documented as read-only; a write attempt via the backend fails for it.
- [ ] **P1-2 — Disable public conversation share-links** in LibreChat.
  *Accept:* the "share conversation" public-link option is unavailable to non-admins.
- [ ] **P1-3 — Rate limiting / abuse controls** at the proxy and in LibreChat (proxy tool
  hits live chargers).
  *Accept:* excessive requests from one user are throttled (observed 429/limit).
- [ ] **P1-4 — Trust boundary between LibreChat ↔ MCP servers.** Shared secret or mTLS so
  forwarded identity headers (see AC-4) can't be spoofed by anything else on the network.
  *Accept:* an MCP request without the shared secret is rejected.
- [ ] **P1-5 — Pin image versions.** Replace `librechat:latest` and pin `mongo`,
  `meilisearch` to tags/digests (`chat/docker-compose.yml`).
  *Accept:* compose references immutable tags/digests; no `:latest`.
- [ ] **P1-6 — Container hardening.** Non-root user in both MCP Dockerfiles, drop caps,
  resource limits, read-only FS where feasible.
  *Accept:* `docker inspect` shows non-root + limits; stack runs.
- [ ] **P1-7 — Image & dependency scanning.** Trivy/Grype on the two MCP images;
  `pip-audit` on `requirements.txt`, wired into CI.
  *Accept:* CI job runs and fails on HIGH/CRITICAL findings.

## P2 — Reliability & operations

- [ ] **P2-1 — Health checks.** `healthcheck:` per compose service + a health/ping endpoint
  on both MCP servers; gate `depends_on` on health.
  *Accept:* `docker compose ps` shows healthy states; API waits for MCP health.
- [ ] **P2-2 — Centralized logging + tool-call audit trail** (who / which tool / which
  charger), especially for the live-charger proxy.
  *Accept:* a tool call is traceable to a user + target in the logs.
- [ ] **P2-3 — Monitoring / alerting** — uptime + error rate; alert on NOC/CMS auth
  failures and GLM `429`s.
  *Accept:* a simulated MCP auth failure raises an alert.
- [ ] **P2-4 — MongoDB backups** — scheduled dump + a tested restore.
  *Accept:* a restore from backup into a clean volume reproduces users/chats.
- [ ] **P2-5 — Production runbook** — extend `chat/RUNBOOK.md` with deploy, rollback,
  secret + service-account rotation.
  *Accept:* runbook exists and a dry-run rollback succeeds.
- [ ] **P2-6 — Resource sizing** — memory/CPU limits + concurrency expectations documented.
  *Accept:* limits set in compose; stack stable under a basic load test.

## P3 — Quality & maintainability

- [ ] **P3-1 — CI test gate.** Run `pytest` for both MCP servers on PR (tests already exist:
  `mcp-server/tests`, `cms-mcp-server/tests`); today only the changelog workflow runs.
  *Accept:* PRs run both test suites and block on failure.
- [ ] **P3-2 — Tool-name cleanup.** Apply `mcp_names`/`mcp_component_fn` to the 153 verbose
  auto-names (`mcp-server/README.md:81-83`) for better LLM tool selection.
  *Accept:* tools expose concise names; tests updated; agent still selects correctly.
- [ ] **P3-3 — OpenAPI spec freshness check** for bundled `noc_openapi.json` vs. live backend.
  *Accept:* CI (or a script) flags drift between bundled spec and backend.
- [ ] **P3-4 — Agent config as code.** The LibreChat Agent (endpoint/tools/system prompt) is
  hand-wired in the UI (`chat/RUNBOOK.md`); capture it reproducibly so it survives `down -v`.
  *Accept:* agent config is restorable from source without manual UI steps.

---

## AC — Access Control / Role-Based Tool & Data Access (epic)

Goal: **org role → which tools + which data.** Layer 1 (native LibreChat) gates *visibility*;
Layer 2 (MCP code) *enforces* tools + data. Both needed; Layer 2 resolves **D2**.

**Target role matrix (draft — refine during brainstorming):**

| Org role | LibreChat role | Agent (curated tools) | Backend scope |
|---|---|---|---|
| L1 support | `noc-l1` | dashboard + ticket/charger **status** reads | read, all sites (or region) |
| Field/NOC engineer | `noc-engineer` | + live charger-proxy (GET-locked) | read, all sites |
| Legacy CMS | `noc-legacy` | `cms` MCP tools | legacy CMS read |
| Admin | `ADMIN` | everything + config | full |

### Layer 1 — native LibreChat RBAC (config, no code)

- [ ] **AC-1 — Admin Panel + custom roles/groups.** Stand up the LibreChat Admin Panel;
  define `noc-l1`, `noc-engineer`, `noc-legacy` roles and matching groups.
  *Accept:* roles exist; a test user can be assigned a role.
- [ ] **AC-2 — Curated agents + ACLs.** Build one agent per role bundling only its tool
  subset; share each agent's ACL with the matching role/group.
  *Accept:* an `noc-l1` user sees only the L1 agent and its tools; not the proxy tool.
- [ ] **AC-3 — Force users through agents.** Set `chatMenu: false` on `mcpServers` entries
  (`chat/librechat.yaml`) so raw tools aren't à-la-carte selectable.
  *Accept:* raw `noc`/`cms` servers don't appear in the chat dropdown.
- [ ] **AC-8 — Admin governance of MCP ↔ agent ↔ role (native, runtime).** Grant admins
  `manage:mcpservers`; let them (a) add/update MCP servers live, (b) edit which agents expose
  which tools, (c) set each agent's ACL to the intended roles/groups. Decide the delegation
  policy: who besides full admins may add MCP servers vs. only build agents from an approved
  catalog (`interface.mcpServers` USE/CREATE flags; optional delegated "tool-admin" role).
  *Accept:* an admin adds a tool, enables it on an agent, and scopes that agent to one role —
  all without a redeploy; an L1 user cannot add MCP servers.
  > Governs **tool access** only; **data scope** per role still requires **AC-5/AC-6**.

### Layer 2 — MCP server enforcement (code in this repo; resolves D2)

- [ ] **AC-4 — Forward user identity to MCP.** Add `headers:` with
  `X-User-Role: '{{LIBRECHAT_USER_ROLE}}'` (+ id/email) to both `mcpServers` in
  `chat/librechat.yaml`.
  *Accept:* MCP servers receive the header (logged) on each tool call.
- [ ] **AC-5 — Role-based tool filtering (FastMCP middleware).** In
  `mcp-server/noc_mcp/server.py`, read `X-User-Role` and expose only that role's tool set
  (e.g. charger-proxy only for engineer/admin).
  *Accept:* unit test — an `l1` role cannot invoke the proxy tool even if requested.
- [ ] **AC-6 — Backend data scoping (resolves D2).** Per the D2 decision, replace/augment the
  shared token with per-user tokens or a role→site/permission map so data is scoped, not just
  tools.
  *Accept:* two users of different roles get **different** result sets for the same query.
- [ ] **AC-7 — Enforce the trust boundary** (ties to **P1-4**) so `X-User-Role` is only
  honored on authenticated LibreChat→MCP connections.
  *Accept:* a spoofed `X-User-Role` from outside the trusted path is rejected.

---

## DEP — Deployment & Capacity (AWS first, built to stay portable)

**Decision (D3):** Stage 1 runs on **AWS**, architected to avoid lock-in so a later move to a
cheaper cloud/provider is a config change. Levers: containers + **Kubernetes (EKS)**,
**Terraform** IaC, **open-source data stores** via managed services with an exit path, and the
**S3-compatible** storage API.

**Portability scorecard — pick the portable option at each layer:**

| Layer | AWS choice (stage 1) | Lock-in | Exit path |
|---|---|---|---|
| Orchestration | **EKS** (managed K8s) | Low | Same Helm/manifests → GKE/AKS/k3s |
| IaC | **Terraform** | Low | Multi-cloud providers; swap module |
| App + MCP tiers | containers on EKS | None | OCI images run anywhere |
| MongoDB | **Atlas on AWS** (not DocumentDB) | Low | Atlas is multi-cloud; live-migrate |
| Redis | ElastiCache **or** Redis on K8s | Very low | Standard protocol; trivial swap |
| Search | **Meilisearch** StatefulSet on EKS | None | Self-hosted; portable |
| Object store | **S3 via S3-compatible API** | Low | R2 / GCS / MinIO speak S3 |
| Ingress/TLS | nginx-ingress + **cert-manager** | Low | No ACM dependency |
| Registry | ECR **or** GHCR | None | Any OCI registry |
| Secrets | Secrets Manager + **External Secrets Operator** | Low | Refs portable; swap backend |
| Observability | **OTel/Fluent Bit** → CloudWatch | Low | Repoint exporter |
| CI/CD | **GitHub Actions** | None | Cloud-agnostic |

**Anti-lock-in rules (constraints to enforce):**
- [ ] **DEP-1 — Kubernetes, not ECS/App Runner.** Deploy on EKS via the LibreChat Helm chart.
  *Accept:* the same chart deploys to local k3s/kind with no AWS-specific edits.
- [ ] **DEP-2 — Terraform, not CloudFormation/CDK.** Provider-specific resources isolated in a
  swappable module. *Accept:* `terraform plan` clean; cloud-specific bits in one module dir.
- [ ] **DEP-3 — Real MongoDB (Atlas), never DocumentDB.** *Accept:* app runs on Atlas; no
  DocumentDB dependency.
- [ ] **DEP-4 — S3-compatible storage only.** Configure file/image storage via the S3 API, no
  S3-proprietary features. *Accept:* repointing the endpoint to MinIO works in a test.
- [ ] **DEP-5 — Portable ingress + TLS.** nginx-ingress + cert-manager (Let's Encrypt).
  *Accept:* TLS issued without ACM; ingress def has no AWS-only fields.
- [ ] **DEP-6 — No AWS-only services on the core path** (Lambda/DynamoDB/SQS/Step Functions).
  *Accept:* core request path touches only portable components.

**Capacity (AWS realization of the sizing):**
- [ ] **DEP-7 — LLM provider off the coding plan** (see **P0-7**) — gates load.
- [ ] **DEP-8 — Autoscaled app tier.** LibreChat Deployment + HPA (2→10) behind ALB/ingress.
  *Accept:* HPA scales under load; streams survive replica loss (Redis mode on).
- [ ] **DEP-9 — Redis (required for multi-replica).** Stream resumption + Meili index lock +
  rate limits. *Accept:* two app replicas share sessions/streams via Redis.
- [ ] **DEP-10 — Managed MongoDB (Atlas) + backups/PITR.** *Accept:* replica set healthy;
  tested restore.
- [ ] **DEP-11 — Meilisearch StatefulSet.** Single indexer, EBS PVC, snapshots; other replicas
  skip indexing. *Accept:* search works multi-replica without index conflicts.
- [ ] **DEP-12 — MCP tier scaled + NOC-account throughput fix.** 2–4 replicas each; cache hot
  reads, pool/rotate service accounts so 500 users don't throttle one NOC login (ties **D2**).
  *Accept:* peak tool-call load doesn't trip NOC per-account limits (load test).
- [ ] **DEP-13 — Load-test the NOC backend** at projected read volume; confirm with its owners.
  *Accept:* NOC sustains peak read load with headroom; sign-off recorded.
- [ ] **DEP-14 — Edge security.** WAF + DDoS + global rate limiting on the ingress
  (internet-facing, "from anywhere"). *Accept:* WAF active; abusive traffic throttled.
- [ ] **DEP-15 — Runtime extensibility (add MCP/tools without a stack redeploy).** Enable
  **DB-backed / Admin-Panel MCP management** (`manage:mcpservers` grant + MCP ACLs; DB
  overrides `librechat.yaml`); deploy each MCP server as its **own Deployment/Helm sub-chart**
  so it rolls independently; pin a LibreChat version that includes stable Admin-Panel MCP
  management (ties **P1-5**). Rule: stable base in YAML, everything-future via Admin Panel.
  *Accept:* an admin registers a **remote** MCP server live (no redeploy) and its tools attach
  to an agent; a **new self-built** MCP server deploys as one rolling service without
  redeploying LibreChat/Mongo/Meili. (Agents + prompts are already runtime — no redeploy.)

## DR — Data Retention (admin-configurable)

Admin sets how long chat data is kept. LibreChat covers **temp chats** natively; **regular
chats** need a purge job. Must cascade (Mongo + Meili + files) and stay in sync with backups.

- [ ] **DR-1 — Temporary Chat retention (native, config).** Enable Temporary Chat; set
  `interface.temporaryChatRetention`; expose per-role via Admin Panel config-override.
  *Accept:* a temp chat auto-deletes after the window; two roles can differ.
- [ ] **DR-2 — Retention purge job for regular chats.** Config-driven; deletes chats older than
  N days and cascades to Meilisearch + file/image storage; honors exemptions (bookmarks/legal
  hold); logs purged items (audit). *Accept:* an over-age chat is gone from DB, search, and
  file store, and the purge is logged.
- [ ] **DR-3 — Admin-editable retention value** (DB-backed, no redeploy). *Accept:* admin
  changes the window; the next purge run honors it.
- [ ] **DR-4 — Backup retention aligned** to the policy (ties **P2-4**). *Accept:* backups do
  not outlive the retention window.

> Caveat: retention governs **our** store only. Data sent to GLM/z.ai for inference is subject
> to the provider's retention — outside our control (reinforces **D1**).

## Suggested sequencing

1. **Decisions:** D1 (residency), D2 (identity/scope), D4 (capacity/retention scope).
   D3 decided (AWS + portable).
2. **P0 set incl. P0-7 (LLM provider)** → safe, multi-user-capable baseline.
3. **DEP-1..6 (portable foundation)** → EKS + Terraform + portable stores on AWS.
4. **DEP-8..14 (capacity)** + **P1** → scale to 500 + hardening; load-test NOC.
5. **AC Layer 1 → Layer 2** → per-role tools, then real enforcement.
6. **DR** (retention) alongside P1.
7. **P2 / P3** → operational maturity, ongoing.

_Last updated: 2026-07-11._
