# Cloud Infrastructure Requirements — Manthan.AI Assistant Platform

**Document purpose:** a requirements brief for a cloud-infrastructure **cost
estimate**. Please return an **itemized quote** — one-time setup + monthly
recurring — with **on-demand vs. 1-year committed-use** pricing where applicable.

**Status:** pre-approval, for vendor estimation. Items marked `<confirm>` are open
decisions on our side; please quote them as the stated assumption and flag the
cost sensitivity.

**Version:** 2.0 · 2026-07-20
**Contact:** _[add name/email before sending]_

---

## 0. How to read this document

- **In scope for your quote:** AWS infrastructure (compute, storage, network,
  security, managed data services) for the two sizing tiers in §4.
- **Out of scope (noted for context only):** the Large Language Model (LLM) itself
  is consumed as a **managed API** and billed per-token as **operating expense** —
  see §3E. We are **not** asking you to price LLM API usage, but the sourcing choice
  changes whether GPU infrastructure appears in your quote at all, so it is
  described here.
- **What we want back:** see §7.

---

## 1. Workload summary

A self-hosted AI assistant platform for internal company use (EV-charger Network
Operations Center today; other departments to follow). Users chat in a web UI; an
LLM reasons and calls internal "tool" microservices that query company systems.

- **Containerized workload only** — ~13 small services, all Docker images we build.
- **Stateless app tier**; state lives in MongoDB, a vector DB, object storage, and
  a search index.
- **No public inbound** except the web UI over HTTPS.
- **Traffic profile:** interactive chat, low request rate, latency dominated by the
  LLM API (not by our compute). See §4 for per-tier concurrency assumptions.
- Current footprint: single-team pilot (~25 users). Target: **org-wide,
  `<confirm: ~200–500 users, 3–5 departments>`**.

## 2. Target platform

- **Cloud:** AWS (first), region `<confirm: ap-south-1 Mumbai>`. Design must stay
  portable — no cloud-only services on the core path (company standard).
- **Orchestration:** Kubernetes (EKS), managed node groups, across ≥2 AZs.
- **IaC:** Terraform for all infrastructure; no click-ops in production.
- **Environments:** staging + production. May share one cluster via namespaces
  initially; please price both a **shared-cluster** and a **separate-staging**
  option if the delta is material.
- **Availability target:** `<confirm: 99.5% (business-hours-critical, not 24×7 SLA)>`.

## 3. Bill of materials — services to quote

### 3A. Compute (EKS)

| Item | Spec | Qty (prod) | Notes |
|---|---|---|---|
| Managed node group (app) | `t3.large` (2 vCPU / 8 GB) | see §4 per tier | across AZs; runs all workloads below |
| Cluster control plane | EKS | 1 | shared by prod + staging namespaces |

Workloads hosted on the nodes (for sizing context — sum of requests drives node count):

| Service | Per-instance request | Replicas (prod) |
|---|---|---|
| LibreChat web app (Node.js) | 1 vCPU / 2 GB | 2 |
| MongoDB — **managed, see §3B** | — | — |
| Meilisearch (search index, persistent) | 0.5 vCPU / 1 GB | 1 |
| Qdrant vector DB (persistent) | 1 vCPU / 2 GB | 1 |
| Tool microservices ×7 (Python, stateless) | 0.25–0.5 vCPU / 256–512 MB each | 1–2 each |
| Embedding inference service (CPU/ONNX) `<confirm>` | 1.5 vCPU / 2 GB | 1 |
| Headless-browser login service (Playwright) | 1 vCPU / 1.5 GB | 1 |
| Admin panel | 0.25 vCPU / 256 MB | 1 |

> `<confirm>` **Embedding service:** today embeddings run in-process inside one tool
> service. At org-wide scale we assume a dedicated CPU embedding service (no GPU
> needed — small ONNX model). Quote as listed; it is a modest line item.

Headroom: allow **~1–2 vCPU / 2 GB per future department** for one additional
lightweight tool service each.

### 3B. Data & storage

| Item | Spec | Notes |
|---|---|---|
| MongoDB (managed) | **MongoDB Atlas** M10 prod / M10 staging; VPC peering, automated backups | M20 for the org-wide tier. **Third-party SaaS — billed by MongoDB, not AWS.** Please note it in the quote and estimate the AWS-side data-transfer/peering cost. |
| Block storage | EBS gp3, ~50 GB total, snapshot lifecycle | Qdrant + Meilisearch persistence |
| Object storage | S3, ~100 GB, versioning + lifecycle | generated documents, KB corpus backups |
| Container registry | ECR | ~15 images |
| Terraform state | S3 + DynamoDB lock | — |

### 3C. Network & security

| Item | Notes |
|---|---|
| ALB (TLS termination) + ACM certificate | the **only** public ingress (web UI) |
| WAF (managed rules) on ALB | — |
| NAT gateway | egress for private app + data tiers; **please estimate NAT + inter-AZ data-processing cost separately** — often underquoted |
| Route 53 hosted zone | 1 domain |
| Secrets management | AWS Secrets Manager / SSM Parameter Store (~20 secrets) |
| SSO / OIDC integration | LibreChat → `<confirm: Entra ID or Keycloak>`; no public sign-up |
| CloudWatch logs + alarms | 30-day log retention |

### 3D. Data transfer & support (commonly missed — please include)

| Item | Notes |
|---|---|
| Internet egress | web UI responses + outbound calls to the LLM API and SaaS ticketing systems. `<confirm>` estimate `~50–150 GB/mo` |
| Inter-AZ traffic | app tier ↔ data tier across AZs |
| AWS Support plan | please quote **Business** support (or your recommended tier) as a line item |

### 3E. LLM sourcing — decides whether GPU infra is in your quote

The platform is model-agnostic. **This is the single largest cost lever**, and it is
tied to a data-residency decision on our side (`<confirm: D1 residency ruling>`).
Please price **only the infrastructure implications** of each option:

| Option | What we host | GPU line item? | Residency posture |
|---|---|---|---|
| **E1 — Managed LLM API via AWS Bedrock** *(preferred)* | nothing extra — Claude/Bedrock is called over the network from inside our VPC | **None** | Inference runs inside AWS, in-region `<confirm: Claude tier available in ap-south-1>`. Keeps prompts + data in the AWS estate. |
| **E2 — Managed LLM API, external endpoint** | nothing extra | **None** | External SaaS endpoint; residency depends on provider region controls — a policy call on our side. |
| **E3 — Self-hosted open model on GPU** | GPU node(s), model server (vLLM) | **Yes — quote below** | Full residency control; highest fixed cost + ops burden. |

**GPU line item — quote only if E3 is selected (price both sizes, on-demand + committed):**

| Sub-option | Spec | Notes |
|---|---|---|
| GPU node, medium | `g6.2xlarge` (1× NVIDIA L4 24 GB) | quantized 12–32B model via vLLM |
| GPU node, comfortable | `g6.12xlarge` (4× L4) | 32B model, higher concurrency |

> **Note for the estimate:** under **E1/E2** the LLM is an operating-expense API
> billed per-token (our cost, not in your quote) and **no GPU infrastructure is
> required**. Under **E3** the model is a fixed monthly GPU cost. We expect to
> choose **E1** unless the residency ruling forbids any external inference; please
> make the GPU line clearly separable so we can compare.

## 4. Sizing tiers to quote

Please quote monthly recurring for both tiers, on-demand and 1-year committed.

| Tier | Users | Peak concurrent `<confirm>` | App nodes | MongoDB |
|---|---|---|---|---|
| **A — Hardened pilot** | ~25 | ~5 concurrent chats | 2× `t3.large` | Atlas M10 |
| **B — Org-wide** | 200–500, 3–5 depts | ~30–50 concurrent chats | 3–4× `t3.large` | Atlas M20 |

> Concurrency is low because sessions are interactive and latency sits in the LLM
> API, not our compute. If your sizing math suggests different node counts for these
> concurrency figures, please propose them.

## 5. Backup, DR, ops

- Automated daily backups: MongoDB (Atlas), EBS snapshots, S3 versioning.
- **RPO 24 h / RTO 1 business day** is sufficient — all services rebuildable from
  IaC + backups.
- Basic monitoring/alerting: node, pod, disk, ALB 5xx, certificate expiry.
- OS/image patching via managed node-group updates.

## 6. Constraints

- Everything as Terraform; no click-ops in production.
- No AWS-only managed services where a portable equivalent exists (e.g., MongoDB
  Atlas, not DocumentDB; S3-compatible object storage).
- All non-web services private — no public IPs/ports.

## 7. What we expect back

1. **Itemized monthly cost per tier (A/B)** — on-demand and 1-year committed.
2. **One-time setup / migration cost** (IaC build-out, managed-service onboarding).
3. **GPU line item** (both sizes, on-demand + committed) — **only** as a separable
   option per §3E, so we can compare E1/E2 vs. E3.
4. **Commonly-missed lines called out explicitly:** NAT + inter-AZ, internet
   egress, AWS Support plan, Atlas (third-party) with the AWS-side transfer cost.
5. **Your assumptions list** + anything we've missed.
6. **Optional:** where you see the largest savings (e.g., committed use, right-sizing,
   Graviton `t4g` equivalents) without breaking the portability constraint.
