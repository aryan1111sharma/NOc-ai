# AWS Deployment — Manthan.AI (noc-mcp-agent) — Design

**Status:** approved design, pre-implementation
**Date:** 2026-09-07
**Scope:** Stage-1 AWS deployment of the 14-service chat stack, delivered as
validated Terraform. **No infrastructure is provisioned by this spec.**

---

## 1. Goal

Move the Manthan.AI stack off a Windows/Docker-Desktop laptop onto AWS
(`ap-south-1`), serving ~25 internal NOC users over HTTPS at
`manthan.manthanquench.online`.

Delivered in two steps, in this order:

1. **Architecture decision** (this document).
2. **Terraform written and validated, nothing applied** — `init`, `validate`,
   `fmt -check` and lint pass; `terraform plan` deferred until working AWS
   credentials exist.

Out of scope: the org-wide Tier-B build, EKS, and any change to the NOC backend.

---

## 2. Decisions driving the design

| Input | Value | Consequence |
|---|---|---|
| Ops ownership | Solo operator, no Kubernetes depth | Rules out EKS for stage 1 |
| Tier-B (200–500 users) | Direction of travel, no date | Optimise for today, keep the door open |
| Domain | `manthanquench.online`, registrar-held (Namecheap) | Delegate zone to Route 53 |
| Access | Public internet, app login | Makes P0-2 a launch blocker |
| Identity | Admin pre-provisioned accounts | Passwords in Mongo → P0-5 + encryption mandatory |
| Instance size | `t3.large` (2 vCPU / 8 GB) | Tight; mitigations required (§8) |
| Budget | ~$152/mo (§13) | Accepted |

---

## 3. Architecture — Option A: single EC2 running the existing compose stack

```
Internet
   │
   ▼
[ Route 53 ]  manthan.manthanquench.online
   │
   ▼
[ ALB :443 ]  ACM cert · HTTP:80 → 301 → HTTPS · WAF (managed rules + rate limit)
   │  public subnets, ap-south-1a / 1b
   ▼
[ EC2 t3.large ]  private subnet 1a · no public IP · SG: only ALB reaches :80
   │  docker compose, 14 services, base compose file UNCHANGED
   │  deploy tree at /data/app · Docker data-root at /data/docker
   │  both on the encrypted EBS gp3 volume — nothing stateful on root
   ├──► SSM Session Manager   (shell access; no SSH, no port 22, no key pairs)
   └──► NAT GW ──► z.ai · Jira · EcoG SD · CMS · NOC backend
```

### 3.1 Why not EKS (supersedes DEP-1 for stage 1)

`PRODUCTION-READINESS.md` DEP-1 specifies "Kubernetes, not ECS/App Runner".
That decision is reversed for stage 1, for three reasons:

1. **Portability (D3) is better served by compose.** `docker-compose.yml` runs on
   any VPS, any cloud, a laptop, or k3s. Helm is the heavier portability story,
   not the lighter one. D3 is satisfied more cheaply by staying with compose.
2. **There is no scaling problem yet.** The estimation brief itself states
   concurrency is low (~5 concurrent chats at Tier A) and latency sits in the LLM
   API, not our compute. It sets **RPO 24 h / RTO 1 business day** against a
   99.5% business-hours target — which a single instance with nightly snapshots
   already meets. Multi-AZ HA buys availability the requirements do not ask for.
3. **The compose file carries load-bearing detail.** The pinned project name
   `chat` (changing it silently orphans every account and chat history), the
   bind-mounted `departments.json`, and the `SERVER_HOST` default that prevents a
   startup crash loop. A 14-service translation to Helm is exactly where those
   get dropped.

**Revisit EKS when** any of: a platform team takes ownership; user count passes
~150; or DEP-15 (registering new MCP servers without a stack redeploy) becomes a
firm requirement. The network, ECR, secrets and state layout below are all
reusable, so that becomes a migration rather than a rewrite.

### 3.2 Why not ECS Fargate

Estimated ~$200–260/mo, between Option A and EKS.

The three stateful services (MongoDB, Qdrant, Meilisearch) are the blocker.
Fargate's only persistent storage is EFS, and MongoDB over NFS is a known-bad
pairing — the file-locking semantics databases assume do not hold. Making it work
means pushing Mongo to Atlas or DocumentDB, which is precisely the added cost and
lock-in the option would be chosen to avoid. The least portable of the three.

---

## 4. Network & edge

- **VPC** with 4 subnets across `ap-south-1a` / `1b`: 2 public (ALB), 2 private
  (app; second AZ reserved and unused at launch). The ALB requires 2 or more AZs
  regardless, so the spare subnets cost nothing and remove re-plumbing later.
- **NAT gateway** for egress. Keeps the instance private, satisfying the
  estimation brief §6 ("all non-web services private — no public IPs/ports").
  The brief §3D already anticipates NAT as a line item.
- **No SSH.** Access is SSM Session Manager only: no port 22, no key pairs, and
  every session logged to CloudWatch. Strictly better than a bastion host.
- **WAF** on the ALB: AWS managed common rule set plus a **rate-based rule on
  `/api/auth/*`**. This closes **P1-3** and is the only brute-force control that
  exists, since pre-provisioned accounts provide no MFA.

## 5. DNS & TLS

Delegate `manthanquench.online` from Namecheap to a Route 53 hosted zone
(~$0.50/mo) by changing nameservers once. Terraform then owns the zone.

Rationale: with the zone in Route 53, **ACM performs DNS validation and renews
the certificate indefinitely with no human action**. Leaving DNS at the registrar
means hand-adding a validation CNAME, which is the classic step nobody remembers
when re-validation is eventually needed. The domain is dedicated to this project,
so delegating the whole zone is low-risk.

Served at `manthan.manthanquench.online` → ALB alias → ACM cert, TLS 1.2+,
HTTP→HTTPS redirect. Closes **P0-1**.

## 6. Authentication (launch blocker)

Public access plus today's `ALLOW_REGISTRATION=true` would let anyone on the
internet create an account and query live charger telemetry, tickets and customer
data. **P0-2 is therefore a launch blocker, not a follow-up.**

Three layers, all required:

1. `ALLOW_REGISTRATION=false` and `ALLOW_SOCIAL_LOGIN=false`.
2. Accounts created by an admin (LibreChat's create-user path), ~25 of them.
3. WAF rate limiting on the login endpoint (§4).

Accepted trade-offs of pre-provisioned accounts: passwords live in MongoDB
(see §7 and P0-5), offboarding is manual, and there is no MFA. Revisit SSO/OIDC
(Entra ID or Google Workspace) before onboarding a second department.

## 7. Secrets

All ~20 secrets in **SSM Parameter Store SecureString** under `/manthan/prod/*`,
encrypted with a dedicated KMS CMK. Not Secrets Manager: that costs ~$8/mo for
rotation we do not need, whereas SecureString is free and is a flat key-value
store that ports to any cloud.

**Terraform creates the parameters but never holds their values.** Each is
created with a placeholder and `lifecycle { ignore_changes = [value] }`, then
populated out-of-band via `aws ssm put-parameter`. If Terraform owned the values
they would sit in plaintext in `terraform.tfstate` — which is `.env` with extra
steps, and would leave **P0-6** exactly as open as it is today.

At boot, an entrypoint renders `.env` from Parameter Store to a **tmpfs** path
(`/run/manthan/.env`) that compose reads. Secrets never touch disk and never
enter an image layer.

The instance profile is scoped to `ssm:GetParametersByPath` on that one prefix
plus `kms:Decrypt` on that one CMK.

Also in scope: **P0-5** — enable MongoDB auth with a strong credential sourced
from Parameter Store. Mandatory now that user passwords live in that database.

## 8. Compute, sizing, and the 8 GB risk

`t3.large` (2 vCPU / 8 GB), chosen to start cheap and measure.

**Known risk.** Estimated resident footprint: Mongo ~1.5 GB, Qdrant ~1.5 GB,
Meilisearch ~1 GB, LibreChat (Node) ~1.5 GB, `kb-mcp-server` with the ONNX
embedding model ~1.5 GB, `ecog-sd` with headless Playwright ~0.75 GB, plus six
Python MCP servers at ~200 MB each — roughly **9–10 GB against 8 GB available**.
The failure mode is the Linux OOM killer terminating the largest process
(typically Mongo or the embedding service), which presents as unexplained random
breakage rather than a clean out-of-memory error.

Mitigations, all in scope:

1. `instance_type` is a Terraform variable (default `t3.large`) — moving to
   `t3.xlarge` is one line and a ~3-minute reboot.
2. **4 GB swap** on the data volume, converting a hard OOM kill into observable
   latency degradation.
3. **`mem_limit`** on non-critical MCP containers in the AWS override, so the OOM
   killer takes a tool service rather than the database.
4. **Memory alarm at 85%**, for warning rather than postmortem.

Note: `t3` defaults to **unlimited** CPU credit mode, so sustained load bills
surplus credits instead of throttling. A billing alarm makes that visible.

## 9. Images and deployment

**ECR**, **eight** repositories — one per distinct image we build:

| # | Image | Build context | Serves |
|---|---|---|---|
| 1 | custom LibreChat | `./custom-image` | `api` |
| 2 | NOC MCP | `services/mcp/noc` | `mcp-server` |
| 3 | KB MCP | `services/mcp/kb` | **both** `kb-mcp-server` and `kb-admin-mcp-server` |
| 4 | Jira MCP | `services/mcp/jira` | `jira-mcp-server` |
| 5 | ChargeZone Jira MCP | `services/mcp/chargezone` | `chargezone-jira-mcp-server` |
| 6 | EcoG SD MCP | `services/mcp/ecog-sd` | `ecog-sd-mcp-server` |
| 7 | Docgen MCP | `services/mcp/docgen` | `docgen-mcp-server` |
| 8 | Platform MCP | `services/mcp/platform` | `platform-mcp-server` |

Note #3: `services/mcp/kb` is built once and run twice with different commands.
Building it as two ECR repos would be wasteful and could let the two drift.

**Five third-party images** are pulled, not built: `mongo:7`,
`getmeili/meilisearch:v1.12`, `qdrant/qdrant:latest`, `nginx:alpine`, and
`registry.librechat.ai/clickhouse/librechat-admin-panel:latest` — the admin panel
is **upstream, not ours**. All five get **pinned to digests**; the first two are
already tagged, so **P1-5** is really about the three floating `:latest` tags.
Pinning also removes Docker Hub rate limits from the critical path.

**Build:** a new GitHub Actions workflow builds and pushes all eight images on
release tags, authenticating via **OIDC** — no long-lived AWS keys in GitHub.
This extends the existing `.github/workflows/` (tests, changelog).

**Deploy:** `docker compose pull && docker compose up -d` executed on the
instance via an **SSM Run Command** document invoked from CI. No inbound
connection, no SSH, no self-hosted runner in the VPC. Rollback is re-running the
document against the previous tag.

**`docker-compose.aws.yml` override** swaps `build:` for `image:` refs and adds
`mem_limit` and the `awslogs` log driver. **The base `docker-compose.yml` is not
modified**, so the local workflow and its load-bearing comments survive intact.

## 10. State, growth, backup, recovery

### 10.1 What grows

Nine state stores. Five have no ceiling, one is unverified, three are bounded.

| Store | Holds | Bounded |
|---|---|---|
| `mongo-data` | conversations, messages, users, file metadata | **No** |
| `meili-data` | search index over conversations; tracks Mongo | **No** |
| `knowledge/` | KB documents uploaded from chat (bind mount, §10.2) | **No** |
| `qdrant-data` | BGE vectors, ~3 KB/chunk; tracks the corpus | **No** |
| `librechat-images` | user uploads and generated images | **No** |
| `librechat-logs` | application logs | Unverified — action item |
| `docgen-files` | generated documents; capped at 200, oldest evicted (verified in `docgen_mcp/server.py:58-60`) | Yes |
| `kb-model-cache` | BGE embedding model, ~450 MB, downloaded once | Yes |
| `ecog-sd-cookies` | session cookies, kilobytes | Yes |

The dominant growth driver is **`mongo-data`**, and the reason is specific to
this stack: LibreChat stores full message content, and our messages carry *tool
results* — charger telemetry payloads, ticket dumps, fleet queries — which are
far larger than ordinary chat text. Meilisearch then indexes all of it again. So
the two largest consumers scale with tool-call volume, not user count.

### 10.2 Everything stateful on the data volume

Docker `data-root` is set to `/data/docker`, so all 8 named volumes keep their
exact current names. Combined with the pinned project name `chat`, this
guarantees no volume-orphaning path.

**`knowledge/` is a bind mount, not a Docker volume** (`../../../knowledge`,
writable so the KB tools can upload from chat), so `data-root` does not capture
it — it resolves to wherever the deploy tree sits. Left on the root volume this
fails twice: KB uploads fill an 8 GB root disk and take the instance down, and
the corpus is absent from the daily snapshot, so replacing the instance loses it.

The second failure is the serious one. `knowledge/.gitignore` deliberately
excludes the corpus ("may be large or proprietary"), so those documents exist in
no git history and no other backup. Currently 25 MB across 74 files, 24 MB of it
`dept-noc/`.

Therefore **nothing stateful sits on the root volume**:

```
/data/                          <- EBS gp3, encrypted, snapshotted daily
├── docker/                     <- data-root: all 8 named volumes
└── app/                        <- deploy tree (compose + bind mounts)
    ├── knowledge/              <- KB corpus, nightly S3 sync
    └── roles/departments.json  <- created by deploy script before compose starts
```

`departments.json` must exist before the stack starts: if it is missing, Docker
creates a *directory* at that path rather than failing, and every `kb_admin_*`
call breaks with no obvious cause.

The data volume is separate from root, making the instance disposable —
replaceable, resizable and re-imageable without touching data.

### 10.3 Backup

Two layers, because they fail differently:

| Layer | Contents | Cadence | Rationale |
|---|---|---|---|
| EBS snapshot via DLM | whole volume | daily, 14-day retention | fast whole-box recovery |
| `mongodump` + Qdrant snapshot + `knowledge/` sync to S3 | logical | nightly | a snapshot of a *running* mongod is only crash-consistent; a logical dump also allows single-collection restore |

Meilisearch is deliberately excluded — a rebuildable index, not a source of
truth. `knowledge/` is deliberately **included** for the opposite reason: it is
not rebuildable from anything.

The S3 backup bucket has versioning, public access blocked, Glacier IR
transition at 30 days, expiry at 180.

Meets the stated **RPO 24 h / RTO 1 business day**. A documented restore drill
contributes to **P2-5**.

### 10.4 Sizing

100 GB gp3 is comfortable for a 25-user pilot — realistically single-digit GB in
year one. Two properties make that safe rather than lucky: gp3 resizes online, so
100 GB is a starting point rather than a commitment; and the disk alarm at 80%
(§11) converts "we ran out of disk" into "we grew the volume last month". The
wildcard is the corpus: a few GB of departmental manuals implies several GB of
Qdrant vectors on top.

## 11. Observability

CloudWatch agent for host metrics (CPU, memory, disk, swap). Container logs via
the `awslogs` driver, 30-day retention per the brief §3C. Alarms to SNS to email
on: instance status check failure, CPU >85% sustained, memory >85%, disk >80%,
ALB 5xx rate, ALB target response time, and estimated charges.

## 12. Terraform layout

### 12.1 Modules

```
infra/
  bootstrap/          S3 state bucket + DynamoDB lock  (run once, local state)
  modules/
    network/          VPC, 4 subnets across 2 AZs, IGW, NAT, routes, SGs
    edge/             ACM, ALB, target group, listeners, WAF
    dns/              Route 53 zone + records
    compute/          EC2, EBS data volume, IAM instance profile, user_data
    secrets/          KMS CMK + SSM parameters (placeholders only)
    registry/         8 ECR repos + lifecycle policies
    cicd/             GitHub OIDC provider, CI role, SSM Run Command document
    backup/           DLM policy, S3 backup bucket
    observability/    log groups, alarms, SNS topic
  envs/prod/          main.tf, variables.tf, backend.tf, outputs.tf
```

> The `cicd/` module is easy to miss. §9 requires GitHub Actions to authenticate
> by OIDC and deploys to run through an SSM Run Command document — but neither
> the IAM OIDC provider, the CI role, nor the SSM document belongs to any other
> module. Without it the build pipeline has no way to authenticate and the deploy
> path does not exist.

### 12.2 Files outside `infra/`

- `apps/ai-platform/librechat/docker-compose.aws.yml` — the override
- `.github/workflows/build-images.yml` — OIDC to ECR
- `scripts/render-env-from-ssm.sh` — boot-time `.env` renderer
- `scripts/backup-to-s3.sh` — nightly `mongodump` + Qdrant snapshot + `knowledge/` sync (§10.3)
- `RUNBOOK.md` — AWS deploy, rollback and restore procedures

### 12.3 AWS resource inventory

Everything Terraform creates, so the module list above can be checked against a
complete set. Twenty-one services; six carry meaningful cost.

| Service | Purpose | Module | Billed |
|---|---|---|---|
| EC2 | one `t3.large` running all 14 containers | `compute` | $62/mo |
| EBS gp3 | `/data` — databases, corpus, deploy tree; encrypted | `compute` | ~$10/mo |
| EBS snapshots | daily, 14-day retention | `backup` | ~$5/mo |
| DLM | schedules those snapshots | `backup` | free |
| S3 | backup bucket + Terraform state | `backup`, `bootstrap` | small |
| DynamoDB | Terraform state lock | `bootstrap` | ~$0 |
| VPC | 4 subnets across 2 AZs | `network` | free |
| Internet Gateway | public subnet egress | `network` | free |
| NAT Gateway | egress for the private instance | `network` | $35/mo |
| Security Groups | only the ALB may reach the instance | `network` | free |
| ALB | TLS termination, the only public ingress | `edge` | $20/mo |
| Route 53 | hosted zone + ALB alias record | `dns` | $0.50/mo |
| ACM | TLS certificate, auto-renewing | `edge` | **free** |
| WAF | managed rules + login rate limit | `edge` | $12/mo |
| KMS | one CMK — encrypts EBS *and* the secrets | `secrets` | ~$1/mo |
| SSM Parameter Store | ~20 SecureString secrets | `secrets` | free |
| SSM Session Manager | shell access — replaces SSH | `compute` | free |
| SSM Run Command | executes deploys | `cicd` | free |
| IAM | instance profile, CI role, OIDC provider | `compute`, `cicd` | free |
| ECR | 8 image repositories | `registry` | ~$1/mo |
| CloudWatch | logs (30 d), alarms, agent metrics | `observability` | ~$3/mo |
| SNS | delivers alarms to email | `observability` | negligible |

Two notes for whoever applies this. **ACM is free**, so there is no cost argument
for running without valid TLS. **NAT at $35/mo is the most questionable line** —
it exists solely to keep the instance off a public IP (§4); it is the first thing
to revisit if the number must come down, at the price of the brief §6 constraint.

## 13. Cost

`ap-south-1`, on-demand, USD/month:

| Item | ~$/mo |
|---|---|
| EC2 `t3.large` | 62 |
| NAT gateway | 35 |
| ALB | 20 |
| WAF | 12 |
| EBS 100 GB gp3 + snapshots | 15 |
| CloudWatch, ECR, Route 53, S3 | 8 |
| **Total** | **~152** |

Excludes LLM API usage (per-token opex) and assumes no Savings Plan. Compare:
~$300–450/mo for the EKS design in `_internal-infra-ballpark.md`.

## 14. Acceptance criteria

This phase is complete when:

1. `terraform init`, `terraform validate` and `terraform fmt -check` pass in
   `infra/envs/prod` and every module.
2. `tflint` and `checkov` pass, or every finding is explicitly waived with a
   recorded reason.
3. `docker-compose.aws.yml` is valid (`docker compose -f ... config`) and the
   base `docker-compose.yml` is byte-identical to its pre-change state.
4. The GitHub Actions build workflow passes YAML lint and has no hard-coded AWS
   credentials.
5. The `user_data` places the deploy tree at `/data/app` and Docker's
   `data-root` at `/data/docker`, and creates `roles/departments.json` before
   compose starts. No stateful path resolves to the root volume.
6. No AWS resource has been created; no `terraform apply` has been run.
7. `terraform plan` is **explicitly deferred** — it requires credentials, and
   `aws sts get-caller-identity` currently fails on the operator's machine.

## 15. Prerequisites before anything is applied

Not blockers for writing the IaC, but blockers for go-live. Each needs a named
owner — three are decisions rather than tasks, and decisions without an owner
come back "approved in principle" and undecided in fact:

| # | Gate | Owner | Type |
|---|---|---|---|
| 0 | Accept reversing DEP-1 for stage 1 (§3.1) | Engineering lead | Decision |
| 1 | Working AWS credentials | Operator | Task |
| 2 | Move off the GLM Coding Plan (P0-7) | Procurement | Decision — longest lead time |
| 3 | D1 — data residency ruling | Security / compliance | Decision |
| 4 | Nameserver delegation | Operator | Task |
| 5 | The ~25 user accounts | Operator | Task |
| 6 | Approve ~$152/mo | Budget holder | Decision |

Detail on gates 1–5. Gates 0 and 6 are sign-offs on this document itself: the
architecture decision in §3.1 and the cost in §13.

1. **Working AWS credentials** — `aws sts get-caller-identity` currently fails.
2. **P0-7 — the LLM endpoint.** The configured z.ai GLM Coding Plan endpoint
   prohibits multi-user access and caps concurrency at ~1 in-flight request.
   Deploying to AWS does not change this: it makes a single-user-licensed
   endpoint the bottleneck for every user onboarded. This is a procurement
   action and it gates the value of the entire deployment.
3. **D1 — data residency.** Prompts and tool results (charger telemetry,
   tickets, customer data) are sent to Zhipu (z.ai, China-based). Internal use
   may accept this; a public-facing deployment warrants an explicit ruling.
4. **Nameserver delegation** of `manthanquench.online` to Route 53.
5. **The ~25 user accounts** to pre-provision.

## 16. Open items deliberately not addressed

- **D2 — shared service-account identity.** Every chat user reads the entire
  fleet through one `NOC_EMAIL` token. Unchanged by this deployment; tracked as
  AC-6.
- **DEP-15 — runtime MCP registration** without a redeploy. Not achievable on
  single-instance compose; revisit with EKS.
- Staging environment. The laptop remains staging for stage 1.
- Multi-AZ or multi-region HA. Explicitly out of scope per §3.1.
