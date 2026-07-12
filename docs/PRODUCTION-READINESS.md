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
- [x] **P0-4 — Lock charger proxy to GET.** Enforce `method=GET` in code on
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
- [ ] **P1-2 — Govern public conversation share-links (keep the feature, gate it safely).**
  Sharable links are a **wanted feature**, but a shared chat can contain NOC data — so control
  it rather than disable it: restrict who can create public links via RBAC (role/group), add
  link **expiry + revocation**, and treat shared content under the retention/residency rules
  (ties **D1**, **DR**).
  *Accept:* only permitted roles can create a public share-link; a link can be set to expire
  and be revoked; who-shared-what is auditable.
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

- [ ] **P2-1 — Health checks + upstream-backend surfacing.** `healthcheck:` per compose service +
  a health/ping endpoint on both MCP servers; gate `depends_on` on health. Also surface **upstream
  backend** reachability (NOC / legacy CMS), not just the MCP process.
  *Accept:* `docker compose ps` shows healthy states; API waits for MCP health; a down upstream is
  visible without hand-probing.
  > *Evidence (live probe, 2026-07-12):* the legacy CMS backend `https://quenchcms.com` returned
  > **HTTP 503 on every attempt**, so all 6 CMS tools failed at login — invisible until manually
  > probed. A process-only healthcheck would have shown CMS "healthy". Reinforces the need to
  > surface *upstream* health (and ties to **P2-3** alerting on NOC/CMS auth failures).
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

- [~] **P3-1 — CI test gate.** Run `pytest` for both MCP servers on PR (tests already exist:
  `mcp-server/tests`, `cms-mcp-server/tests`); today only the changelog workflow runs.
  *Accept:* PRs run both test suites and block on failure.
  *Status:* workflow `.github/workflows/tests.yml` added (matrix over both servers, Python 3.12)
  and **running green on GitHub Actions** (it correctly caught a real import bug red, then went
  green after the fix). **Remaining (manual, GitHub UI):** enable branch protection → required
  checks `pytest (mcp-server)` / `pytest (cms-mcp-server)` so red checks block merge.
- [ ] **P3-2 — Tool-name cleanup + de-dup overlapping tools.** Apply `mcp_names`/`mcp_component_fn`
  to the verbose auto-names (`mcp-server/README.md:81-83`) for better LLM tool selection, and
  resolve **near-duplicate tools** that invite wrong-tool selection.
  *Accept:* tools expose concise names; overlapping pairs are disambiguated or pruned; tests
  updated; agent still selects correctly.
  > *Evidence (live probe, 2026-07-12):* NOC registers **154 tools**; the probe surfaced concrete
  > confusable pairs the model could pick wrong between — `get_control_center_api_catalog` vs
  > `get_endpoints_catalog_api_v1_admin_endpoints_catalog_get`; **two** `dashboard_summary` tools
  > (`…api_v1_dashboard_summary_get` vs `…api_v1_monitoring_dashboard_summary…`); and multiple
  > `proxy`-named tools. Disambiguation matters as much as brevity here.
- [ ] **P3-3 — OpenAPI spec freshness check** for bundled `noc_openapi.json` vs. live backend.
  *Accept:* CI (or a script) flags drift between bundled spec and backend.
- [ ] **P3-4 — Agent config as code.** The LibreChat Agent (endpoint/tools/system prompt) is
  hand-wired in the UI (`chat/RUNBOOK.md`); capture it reproducibly so it survives `down -v`.
  *Accept:* agent config is restorable from source without manual UI steps.
- [ ] **P3-5 — Testing standard for new MCP servers.** Every new MCP server (see **CAP-3**,
  **EVT-3**) ships a `pytest` suite mirroring `mcp-server/tests`, wired into the **P3-1** CI gate.
  *Accept:* the new server's suite runs in CI and blocks the PR on failure.
- [ ] **P3-6 — Tool-level tests for `noc`/`cms` (close the coverage gap).** Today only
  `test_auth.py` exists — the **actual tools are untested**, so "green" proves only auth works.
  Add **FastMCP in-memory `Client`** tests (mock the backend with `httpx.MockTransport`, same
  trick as `test_auth.py`) that `list_tools` + `call_tool` and assert each tool's fields / shape /
  empty+limit handling. Covers CMS's 7 curated tools and NOC's catalog + proxy tools. (Test
  levels for reference: ① in-memory tool tests · ② MCP Inspector `fastmcp dev` · ③ http transport
  probe · ④ end-to-end in LibreChat.) *Accept:* every CMS tool + NOC catalog/proxy tool has a test;
  suite runs offline and is in the **P3-1** gate.
  > *Note (live probe, 2026-07-12):* an in-container FastMCP in-memory `Client` smoke test confirmed
  > **NOC healthy end-to-end** (154 tools; `dashboard_summary` returned live `{total, online,
  > offline, by_controller}`) and **CMS = 6 tools** (not 7 as first stated) that register + fail
  > *gracefully* on the upstream 503. That was a *live* smoke test; **P3-6 must still be the offline
  > `MockTransport` kind** so it catches code regressions when backends are down.
- [ ] **P3-7 — Injectable backend client (testability enabler for P3-6).** `build_server()`
  hardcodes `build_client()`, so tools can't be tested against a mock. Make `build_server(client=…)`
  accept an injected client (both servers). *Accept:* `build_server(client=mock)` works and **P3-6**
  uses it; production path (no arg) unchanged.

---

## AC — Access Control / Role-Based Tool & Data Access (epic)

Goal: **org role → which tools + which data.** Layer 1 (native LibreChat) gates *visibility*;
Layer 2 (MCP code) *enforces* tools + data. Both needed; Layer 2 resolves **D2**.

> **Decision (2026-07-11): single full-access user for now → per-role enforcement deferred.**
> The portal will be used by the **service manager only**, who needs **full access** (new NOC +
> legacy CMS + charger-proxy). So **AC-3, AC-4, AC-5, AC-6 are deferred** — per-role gating is
> speculative for one full-access user. **Current setup:** the **`noc-engineer`** role = the
> service-manager role; the **"NOC Engineer"** agent carries all 162 tools (155 noc incl. proxy
> + 7 cms) and is shared to it; the test account `noc-tester` holds `noc-engineer`. AC-1/AC-2
> groundwork stays as the base; revisit AC-3..AC-6 when multiple user types are onboarded.
> (Config lives in Mongo → not yet reproducible: **P3-4**.)

**Target role matrix (draft — refine during brainstorming):**

| Org role | LibreChat role | Agent (curated tools) | Backend scope |
|---|---|---|---|
| L1 support | `noc-l1` | dashboard + ticket/charger **status** reads | read, all sites (or region) |
| Field/NOC engineer | `noc-engineer` | + live charger-proxy (GET-locked) | read, all sites |
| Legacy CMS | `noc-legacy` | `cms` MCP tools | legacy CMS read |
| Admin | `ADMIN` | everything + config | full |

### Layer 1 — native LibreChat RBAC (config, no code)

- [x] **AC-1 — Admin Panel + custom roles/groups.** Stand up the LibreChat Admin Panel;
  define `noc-l1`, `noc-engineer`, `noc-legacy` roles and matching groups.
  *Accept:* roles exist; a test user can be assigned a role.
  *Status:* `admin-panel` service added (`:3091`); the three `noc-*` roles created + verified in
  Mongo; test user `noc-tester@quench.local` assigned `noc-l1` (persisted across reload).
  Matching **groups** deferred to AC-2 (same panel; created when agents/config attach to them).
  Caveats: roles live in Mongo only — reproducible-from-source is **P3-4**; a local test admin
  `noc-admin@quench.local` was created + promoted (ADMIN) to bootstrap the panel.
- [x] **AC-2 — Curated agents + ACLs (agent visibility).** Build one agent per role and share
  each agent's ACL with the matching role.
  *Accept (revised):* an `noc-l1` user sees only the L1 agent; not other agents.
  *Status:* "NOC L1 Support" (→ `noc-l1`, Viewer) and "NOC Engineer" (→ `noc-engineer`, Viewer)
  created + shared. Verified: logged in as `noc-tester` (`noc-l1`) → agent picker shows **only**
  "NOC L1 Support"; "NOC Engineer" absent (screenshot `ac2-noc-l1-sees-only-l1-agent.png`).
  **Finding:** LibreChat v0.8.7 adds MCP tools at **server granularity** (whole `noc` server, no
  per-tool selection in the agent builder), so *per-tool* exclusion of the charger-proxy from L1
  can't be done here — **moved to AC-5** (MCP-side role filtering). The proxy is already
  GET-locked (P0-4), so L1 seeing it is read-only. Agents live in Mongo (P3-4).
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
  (e.g. charger-proxy only for engineer/admin). **Now owns the proxy-exclusion for `noc-l1`**
  (AC-2 can't do per-tool exclusion — LibreChat adds MCP tools per-server, not per-tool).
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

## CAP — Claude-app-style output (visual rendering + documents)

Goal: bring Manthan AI's **output quality** toward the Claude app — rich visual rendering plus
document **create** and **read**. Delivered via **native LibreChat features + self-hosted MCP**,
**not** by importing Claude Code skills/plugins.

> **Harness note (why not "just add the plugin"):** LibreChat has **no skill runtime** — it can't
> read a `SKILL.md`, has no Skill tool, no progressive disclosure. So a Claude Code skill/plugin
> **cannot be installed here regardless of the model** (swapping GLM→Claude does **not** change
> this — it's the *app*, not the cook). LibreChat only consumes **MCP** (open protocol) + its own
> native features. The reusable asset from a skill is its *code* (e.g. Anthropic's open-source
> docx/xlsx/pptx/pdf skills), re-packaged as MCP tools. Same constraint on Open WebUI, which reaches
> MCP only via an `mcpo` (MCP→OpenAPI) proxy.

- [ ] **CAP-1 — Enable Artifacts (visual output, native).** Turn on `interface.artifacts` in
  `chat/librechat.yaml` for the rich side panel (React/HTML/Mermaid), like Claude Artifacts.
  *Accept:* a GLM reply renders a Mermaid diagram / HTML table in the Artifacts panel.
- [ ] **CAP-2 — Enable file uploads + RAG (document reading, native).** Turn on uploads + the
  `rag_api` service so PDF/DOCX/XLSX/CSV are parsed and queryable; images via GLM vision models.
  *Accept:* a user uploads a PDF/xlsx, asks about it, and gets a grounded answer citing it.
- [ ] **CAP-3 — `docgen` MCP (Word/Excel/PDF generation).** Self-hosted FastMCP mirroring
  `noc`/`cms`, exposing tools that build `.docx`/`.xlsx`/`.pdf` (python-docx / openpyxl /
  reportlab); implementation reference = Anthropic's open-source document skills. Keeps NOC data
  off third-party sandboxes (vs LibreChat's paid Code Interpreter). *Accept:* "make an Excel of X"
  returns a downloadable `.xlsx`; likewise `.docx` and `.pdf`.
- [ ] **CAP-4 — (Optional) Google Workspace MCP.** Add a Google Docs/Sheets/Slides/Drive MCP for
  **live Google files** (which export to Word/Excel/PDF natively). Heavier: needs a Google Cloud
  OAuth setup. Gated on: does the team want Google-hosted docs or just downloadable files?
  *Accept:* the assistant creates a Google Sheet the user can open/export.
- [ ] **CAP-5 — GLM output-quality validation (gate).** Artifacts + document formatting depend
  heavily on model capability; **Claude ≫ GLM** at emitting clean React/HTML and structured docs.
  Validate a fixed set of "make a chart / report" prompts on GLM before promising these features,
  or select a stronger model (ties **D1**, **P0-7**).
  *Accept:* the prompt set produces acceptable output on the chosen model, recorded here.

## EVT — EV-charger test MCP (exploratory, from ev-charger-toolkit)

The `ev-charger-toolkit` plugin (`E:\cjain\my_plugins\ev-charger-toolkit`) is **skills-only** (no
MCP server), so it **cannot be added to LibreChat directly**. To use it here, re-implement its
*actions* — drive a CSMS via its operator API, fetch OTPs from the internal REST API, generate
pytest/checklists from its templates — as a **self-hosted MCP**, mirroring `noc_mcp`/`cms_mcp`.
Separate exploratory track (like **EPIC**).

- [ ] **EVT-1 — Confirm target APIs reachable + credentialed.** The skill drives a CSMS operator
  API and an internal OTP REST API. *Accept:* an authenticated call to each returns data from the
  stack's network.
- [ ] **EVT-2 — Decide the tool surface (brainstorming).** Which actions become tools — status
  reads / test-file generation / full end-to-end run. *Accept:* agreed tool list before code.
- [ ] **EVT-3 — Scaffold `ev-test-mcp-server/`.** Mirror `noc_mcp`; implement the agreed tools;
  use the skill's `.py.template` files as generators; ship tests (see **P3-5**).
  *Accept:* GLM can invoke e.g. `fetch_otp` / `generate_test_suite`; tests pass.
- [ ] **EVT-4 — Wire into `librechat.yaml`** as a third MCP server next to `noc`/`cms`.
  *Accept:* the server appears and a tool call succeeds end-to-end from chat.

## EPIC — Epicor ERP MCP (exploratory, BLOCKED)

Potential **read-only MCP for Epicor ERP**, mirroring `noc_mcp`/`cms_mcp`. Separate from the
NOC-stack productionization above. **Blocked on API access** — full analysis, decision tree,
self-check, and colleague questions in [`EPICOR-MCP-FEASIBILITY.md`](./EPICOR-MCP-FEASIBILITY.md).
Current constraints: UI login only, no admin, version unknown, colleague-installed (possible
local/admin instance — most promising lead).

- [ ] **EP-1 — Confirm version + REST reachability** (Help→About; probe `/api/help` &
  `/api/v2/odata/`). *Accept:* version known and we know whether a REST API exists/responds.
- [ ] **EP-2 — Establish API access** — existing creds against REST, or admin-provisioned API
  key + read-only service account. *Accept:* an authenticated REST call returns data.
- [ ] **EP-3 — Decide data scope** the assistant answers over (orders/inventory/jobs/AR-AP…).
  *Accept:* scoped tool list agreed before generation.
- [ ] **EP-4 — Scaffold `epicor-mcp-server/`** mirroring `noc_mcp` (GET-only route map), only
  after EP-1/EP-2 clear. *Accept:* server exposes read-only Epicor tools; tests pass.
  > Decision **D-EP1:** no UI-automation fallback — REST API is the only supported path.

## JIRA — Jira MCP integration (two-tenant, exploratory)

Two **separate** Jira tenants to surface in the assistant: **ChargeZone** (a client's tracker)
and **Ecog** (the charging-controller **vendor's** tracker). MCP does **not** merge tenants —
one MCP connection per Jira, each with its own credential, RBAC gate, and access-control surface.
Fits the multi-MCP boundary rule (split by backend / ownership / audience). **Read-only first**;
write (create/transition issues in a client's or vendor's tracker) is a separate trust decision.
Separate exploratory track (like **EPIC**, **EVT**).

> **Data-boundary caveat (gates external sharing):** ChargeZone data is **client-confidential**
> and Ecog data is **vendor-confidential / cross-company**. Piping either into the shared
> Adore Manthan.AI stack sends it to the configured inference provider — reinforces **D1**
> (residency) and means each Jira MCP must be scoped to the right audience only (**AC-8**),
> not visible fleet-wide.

- [ ] **JR-0 — Decide hosting + auth per tenant (gate).** For **each** Jira: Atlassian **Cloud**
  or on-prem **Server/Data Center**? (decides hosted Atlassian remote MCP vs. self-hosted server)
  and per-user OAuth vs. a shared read-only service token (per-user inherits Jira's own RBAC —
  safer than a blended service account, mirrors **D2**). *Accept:* hosting + auth model recorded
  here for both ChargeZone and Ecog.
- [ ] **JR-1 — Confirm API reachability + credentials, both tenants.** Authenticated call
  (REST/JQL) against each Jira returns data from the stack's network. *Accept:* a `search`/issue
  read succeeds against **both** ChargeZone and Ecog.
- [ ] **JR-2 — Decide the tool surface (brainstorming).** Which read actions become tools
  (JQL search, issue detail, status/transitions view, comments, sprint/board reads); write
  deferred. *Accept:* agreed read-only tool list before code.
- [ ] **JR-3 — Stand up one MCP connection per tenant.** Two connections (hosted Atlassian remote
  MCP and/or self-hosted per JR-0), each with its own credential — never one blended Jira MCP;
  new self-built servers ship tests (see **P3-5**). *Accept:* each tenant's tools list and a read
  call succeeds end-to-end from chat.
- [ ] **JR-4 — Scope visibility by audience (ties AC-8).** ChargeZone MCP not exposed to
  Ecog-facing users and vice-versa; wire each to the intended agent/role. *Accept:* a user
  scoped to one tenant cannot see or call the other tenant's tools.
- [ ] **JR-5 — (Deferred) Write access decision.** Whether/where to allow create/transition, and
  under what guardrails (which tenant, which roles, GET-locked read stays the default elsewhere).
  *Accept:* explicit decision recorded before any write tool is built.

## RCA — Autonomous alarm-triage / root-cause agent (exploratory)

Beyond the **reactive** LibreChat chat, a **proactive** agent: on a charger alarm, pull that
charger's logs + diagnostics, correlate them, check the relevant firmware/code, and post a
root-cause summary to the team (Slack / ticket). **Reuses the existing MCP tools unchanged** — this
is a different *harness* (autonomous), not different tools. Candidate harness: a **Hermes-style
agent framework** ([`NousResearch/hermes-agent`](https://github.com/NousResearch/hermes-agent) —
MCP-native, built-in cron/scheduler, multi-platform delivery, persistent memory) **or** a small
custom trigger→MCP→LLM loop. Separate exploratory track (like **EPIC** / **EVT** / **JIRA**).
**Human-in-the-loop: RCA output is a triage first-draft an engineer confirms — never auto-close.**

**Tool-audit findings (feasibility basis):**
- **Logs exist only via `noc_mcp`** → per-charger dev-tools service (`:8005`): `logs/sources`,
  `logs/{source}` (returns **JSON lines** — agent-friendly), plus `metrics` (cpu/mem/disk),
  `services` + `docker-compose-status`, `test-connectivity`, `m7/status` (firmware), and config/file
  `view` = a real RCA toolkit. The NOC **backend** API has **no** log endpoints.
- **`cms_mcp` = alarm signal only** (`cms_get_recent_alerts`: error code + timestamp) — **no logs**.
- **⚠ All `:8005` log/diagnostic endpoints require `admin` role**, colliding with the read-only
  service-account posture (**P1-1**, **D2**) — the **#1 gating question**.

- [ ] **RCA-1 — Resolve log access vs read-only posture (gate).** How the agent reads admin-gated
  `:8005` logs without violating **P1-1/D2** (a scoped elevated credential for the RCA agent only?
  per-charger scoping?). *Accept:* a documented access model that retrieves logs without granting
  broad admin.
- [ ] **RCA-2 — Verify log content is useful on a live charger.** Confirm `logs/{source}` returns
  root-cause-grade lines (not empty/rotated) for a real fault. *Accept:* logs pulled for one real
  alarm contain the diagnostic signal.
- [ ] **RCA-3 — Alarm trigger.** Event/webhook from NOC, or a poll loop (cron) over new alarms /
  `cms_get_recent_alerts`. *Accept:* a new alarm wakes the agent within N minutes.
- [ ] **RCA-4 — Code/firmware access for correlation.** Read access to the controller/firmware repo
  so a fault code maps to a code path. *Accept:* the agent cites the relevant source for a known fault.
- [ ] **RCA-5 — Spike the loop (one alarm type, human-reviewed).** Minimal
  trigger→gather(logs+diagnostics)→analyze→post-to-Slack on a single fault; **read-only, no charger
  actions**. *Accept:* one real/simulated alarm yields a human-reviewed RCA note in Slack/ticket.
- [ ] **RCA-6 — Decide harness (Hermes vs custom).** Only after the spike shows value: weigh
  `hermes-agent` (autonomy/memory/cron/multi-platform, but **young/unproven**) vs a small custom
  loop. *Accept:* harness decision recorded with rationale.

## Suggested sequencing

1. **Decisions:** D1 (residency), D2 (identity/scope), D4 (capacity/retention scope).
   D3 decided (AWS + portable).
2. **P0 set incl. P0-7 (LLM provider)** → safe, multi-user-capable baseline.
3. **DEP-1..6 (portable foundation)** → EKS + Terraform + portable stores on AWS.
4. **DEP-8..14 (capacity)** + **P1** → scale to 500 + hardening; load-test NOC.
5. **AC Layer 1 → Layer 2** → per-role tools, then real enforcement.
6. **DR** (retention) alongside P1.
7. **P2 / P3** → operational maturity, ongoing.

_Epicor MCP (EP-*) and Jira MCP (JR-*) are separate exploratory tracks, gated on API access
(and JR-0 hosting/auth) — not in this sequence._

_Capability-parity (CAP-*) and the EV-charger test MCP (EVT-*) are **parallel product tracks**,
independent of the productionization sequence above. CAP-1/CAP-2 are cheap native toggles gated
only by **CAP-5** (GLM output quality); CAP-3 (docgen MCP) is a small self-contained build. EVT-*
is exploratory, gated on **EVT-1** (API access) and **EVT-2** (tool-surface brainstorming)._

_Autonomous RCA agent (RCA-*) is a separate exploratory track, gated on **RCA-1** (log access vs
read-only posture) and **RCA-2** (log content usefulness); harness (Hermes vs custom) is decided
last, after a value-proving spike._

_Last updated: 2026-07-11._
