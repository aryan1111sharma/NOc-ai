# Ador Manthan.AI — System Overview

_Living reference for the Manthan NOC assistant: what it is, how it's built, and how to operate it. Import into Notion via **Import → Markdown**._

**Last updated:** 2026-07-21 · **Repo:** `noc-mcp-agent` (branch `feat/mcp-server`) · **Released version:** v0.9.0

---

## 1. What Manthan is

**Ador Manthan.AI** is a chat assistant for the Quench NOC (Network Operations Centre). An operator asks questions in plain English — about charger status, failed sessions, tickets, alarms, error-code meanings, runbooks — and Manthan answers by calling **read-only tools** against the live systems (the NOC backend, the legacy CMS, Jira/Service-Desk trackers) and a **knowledge base** of reference documents.

It is built on **LibreChat** (the web UI + orchestration) with **GLM models** (via z.ai) as the reasoning brain, and a set of **MCP tool servers** that expose each backend system as a safe, scoped set of tools.

**Design principles that shaped it:**
- **Read-only by default.** Almost every tool only *reads*. The rare write/exec capabilities are explicitly gated and audited.
- **Data residency.** GLM (z.ai) is the model provider; the knowledge base embedder and vector store are **self-hosted**, so documents and queries never leave the host.
- **Grounded answers.** For reference questions the agent retrieves and **cites** source documents, and says "not in the knowledge base" rather than inventing.
- **Ask before firing.** Agents clarify vague queries (which charger? what time window?) before calling tools.

---

## 2. Architecture at a glance

```
        ┌─────────────────────────────────────────────┐
        │  Operator (browser)  →  http://localhost:3080│
        └───────────────────────┬─────────────────────┘
                                 │
                     ┌───────────▼───────────┐
                     │  LibreChat (web + API) │   noc-librechat
                     │  agents, chat, auth    │
                     └─────┬──────────────┬───┘
        GLM 5.2 / 4.7      │              │  MongoDB   (agents, chats)   noc-librechat-mongo
        (z.ai) — the brain │              │  Meilisearch (search)        noc-librechat-meili
                           │              │  Admin panel                 noc-librechat-admin
             ┌─────────────▼──────────────▼─────────────┐
             │            MCP tool servers               │  (one per backend, FastMCP/HTTP)
             │  noc · cms · jira · docgen · ecog_sd ·     │
             │  chargezone · kb                          │
             └───────────────────┬───────────────────────┘
                                 │  kb only
                          ┌──────▼──────┐
                          │   Qdrant    │  vector store for the knowledge base
                          └─────────────┘
```

Everything runs as Docker containers on a single host, defined in `apps/ai-platform/librechat/docker-compose.yml`.

---

## 3. The agents

Agents are configured in LibreChat (stored in MongoDB; the source of truth is `apps/ai-platform/librechat/agents/agents.json`, restored into Mongo via `apps/ai-platform/librechat/agents/restore-agents`). Each agent is a GLM model + a curated set of MCP tools + an instruction prompt.

| Agent | Model | Knowledge / tool servers | Role |
|---|---|---|---|
| **NOC Engineer** | glm-5.2 | noc, cms, docgen, jira, ecog_sd, chargezone, kb | Full-capability agent — live fleet, both trackers, document generation, and the knowledge base. |
| **NOC L1 Support** | glm-5.2 | noc, cms, kb | Front-line: charger status, tickets, dashboard, plus reference knowledge. No live on-charger actions. |
| **NOC server agent glm-4.7** | glm-4.7 | noc, cms, kb | Baseline NOC agent on the older model. |
| _NOC server agent glam-_ | glm-5-turbo | _(none)_ | Stub — no tools attached. Not in active use. |

**Shared behaviours (in every agent's instructions):**
- **CLARIFY before acting** — ask the 1–2 missing questions (which charger? what time window?) instead of guessing, then proceed.
- **CHARGER LOOKUP discipline** — only ever report data for the charger the user actually named; never substitute another.
- **Data-source routing** — the legacy **CMS** is authoritative for total fleet counts (~1079 chargers); the **NOC** platform covers the newer fleet (~346) plus dashboard/tickets/telemetry.
- **KNOWLEDGE BASE** — use `kb_search_noc` for "what does X mean / how do I…" questions, cite the source, or say it's not in the KB.

---

## 4. The MCP tool servers

Each backend system is exposed by its own FastMCP server over HTTP. LibreChat reaches them by container name (see `apps/ai-platform/librechat/librechat.yaml` → `mcpServers`).

| Server (MCP name) | Port | Backend | What it does | Access |
|---|---|---|---|---|
| **noc** (`noc-mcp-server`) | 8100 | QuenchNOC backend (current fleet, ~346) | Charger status, telemetry, tickets, control-centre on-charger tools; one audited SSH-exec capability. | Read-only + 1 gated exec |
| **cms** (`cms-mcp-server`) | 8101 | Legacy CMS at quenchcms.com (~1079) | Per-charger details, sessions, firmware/release info; some gated device commands. Authoritative for fleet totals. | Mostly read-only |
| **jira** (`jira-mcp-server`) | 8102 | Ecog Atlassian Cloud Jira (REST v3) | Ecog vendor tickets. _Note: primary live Ecog ticket access is via **ecog_sd** below._ | Read-only |
| **docgen** (`docgen-mcp-server`) | 8103 | — (local) | Generates Excel/Word files from tabular/report data and returns a download link. | Generates files |
| **ecog_sd** (`ecog-sd-mcp-server`) | 8104 | Ecog Jira Service Management (customer API) | Live Ecog Service-Desk tickets, authenticated by browser session cookie. | Read-only |
| **chargezone** (`chargezone-jira-mcp-server`) | 8105 | ChargeZone Atlassian Cloud Jira, `QCH` project | ChargeZone/Quench tickets, priority/OCPP-ID aware. | Read-only |
| **kb** (`kb-mcp-server`) | 8106 | Self-hosted RAG + Qdrant | Retrieval over EV/charger reference documents (see §5). | Read-only |
| **platform** (`platform-mcp-server`) | 8107 | This repository (read-only bind mount) | Platform Intelligence: repo health/structure/git-state introspection — the platform understanding itself, not an external backend. | Read-only |

> **Health check:** after a restart, `docker logs noc-librechat` shows one `[MCP][<name>] Tools: …` line per server. All eight should appear.

---

## 5. The knowledge base (RAG)

The knowledge base answers "what does this mean / how do I handle this" questions from **reference documents** — OCPP specs, charger manuals, NOC runbooks — rather than from live systems. It is a **RAG** (retrieval-augmented generation) system: documents are chunked, embedded into vectors, and stored in **Qdrant**; at query time the most relevant chunks are retrieved and the agent answers *from* them, with citations.

Adding knowledge is instant and reversible — **no model retraining**. Drop a file in, re-ingest, done.

### 5.1 Scopes and profiles (agent-centric)

The knowledge base is **agent-centric**: different agents can have different knowledge, so future agents (per-customer, HR, other technical domains) don't all share one pool.

- A document's **top-level folder** under `knowledge/` is its **scope**.
- `knowledge/scopes.yaml` declares scopes and **profiles** (named bundles of scopes).
- The server generates **one tool per profile** — `kb_search_<profile>` — with the scope filter **bound server-side**. An agent granted `kb_search_noc` can only ever reach the `noc` profile's scopes; the model never passes a scope and cannot request one it wasn't given.
- Non-confidential scopes share one Qdrant collection (separated by a filter); a scope marked `confidential: true` gets its **own** collection.

Current live config:

```yaml
# knowledge/scopes.yaml
scopes:
  ev-core:
    folder: ev-core
profiles:
  noc:
    scopes: [ev-core]
    description: "EV/OCPP protocol reference, charger specs, NOC runbooks."
```

So today there is one scope (`ev-core`) and one profile (`noc`) → one tool, `kb_search_noc`, held by the three active agents.

> ⚠️ A knowledge base is a **retrieval** system, not an access-control system. Anything ingested can be quoted to whoever holds the tool. Don't load material that must never be retrievable.

### 5.2 Add or update documents

1. Drop files into the scope folder: `knowledge/ev-core/`
   - **Supported:** `.pdf`, `.md`, `.txt`, `.html` (subfolders are fine)
   - **Not supported:** `.docx` / `.xlsx` (convert to PDF first); scanned/image-only PDFs (no embedded text — need OCR)
2. Re-ingest:
   ```bash
   docker exec kb-mcp-server python -m kb_mcp.ingest --prune
   ```
   Idempotent: unchanged files skipped by content hash, changed ones re-indexed, `--prune` drops chunks for deleted files. **No restart needed** — new chunks are live immediately.
3. **Read the summary line** it prints:
   | Stat | Meaning |
   |---|---|
   | `indexed_docs` | Added / updated this run. |
   | `empty_docs` | Parsed to **zero text → a scanned PDF**. Needs OCR before it will work. |
   | `skipped_root_files` | A file landed at `knowledge/` root instead of inside a scope folder. |
   | `skipped_unscoped_files` | A file is under a folder no scope declares. |
4. Record what you loaded in `knowledge/MANIFEST.md` (the tracked audit list).

> **Calibration:** the retrieval relevance floor (`MIN_SCORE_DEFAULT`, currently 0.5) was tuned on the small sample corpus. **Re-check it after loading real documents** — real content scores differently.

### 5.3 Give a new agent its own knowledge area

1. Create `knowledge/<new-scope>/` and add documents.
2. Add the scope and a profile to `knowledge/scopes.yaml` (add `confidential: true` if it needs physical isolation).
3. Re-ingest, then **restart** `kb-mcp-server` so the new `kb_search_<profile>` tool registers.
4. Grant `kb_search_<profile>` to the agent in LibreChat.

Names (scopes and profiles) must match `^[A-Za-z0-9_-]+$`; the `folder:` field is unconstrained and may contain spaces.

---

## 6. Deployment & operations

### 6.1 Start / restart the stack

From `apps/ai-platform/librechat/`:

```bash
./up.ps1            # Windows — build + start everything, wiring version+commit into the footer
./up.sh             # bash equivalent
```

`up.ps1`/`up.sh` read `VERSION` and the current git commit, then run `docker compose up -d --build`.

> **Footer gotcha:** the UI footer (`Ador Manthan.AI vX.Y.Z (sha)`) is **baked into the LibreChat container when it is created**. `docker compose restart api` reuses the frozen value — it does **not** refresh the footer. Only re-creating the container via `up.ps1`/`up.sh` (or `docker compose up`) updates it. A stale footer does **not** mean the code is stale; check `VERSION` + `git log` for the truth.

### 6.2 Where things live

| Concern | Location |
|---|---|
| Stack definition | `apps/ai-platform/librechat/docker-compose.yml` |
| MCP server + agent wiring | `apps/ai-platform/librechat/librechat.yaml` |
| Agent definitions (source of truth) | `apps/ai-platform/librechat/agents/agents.json` |
| Knowledge base documents | `knowledge/<scope>/` (gitignored; `scopes.yaml` + `MANIFEST.md` tracked) |
| Version | `VERSION` (single source of truth) |
| Changelog | `CHANGELOG.md` (git-cliff generated) |
| Design specs & plans | `docs/superpowers/specs/` and `docs/superpowers/plans/` |

### 6.3 Editing agents (important gotcha)

`agents.json` is **regenerated from Mongo by `export-agents`**. If you edit the file, **restore it to Mongo immediately** (`apps/ai-platform/librechat/agents/restore-agents`) — an edit left un-restored is silently dropped by any later export. After restoring, restart `api` so LibreChat re-reads the agents.

### 6.4 Version bump

```bash
./scripts/bump-version.ps1 minor   # feat → new minor;  patch → fix
```

Updates `VERSION`, the welcome line, regenerates `CHANGELOG.md`, commits `chore(release): vX.Y.Z`, and tags. Does **not** push. Convention: **validate in the UI first, then bump** (matches how v0.8.0 shipped). Run `up.ps1` afterward so the footer reflects the new version.

---

## 7. Common operations (quick runbook)

| Task | How |
|---|---|
| **Add knowledge** | Drop file in `knowledge/ev-core/` → `docker exec kb-mcp-server python -m kb_mcp.ingest --prune` → check summary → update `MANIFEST.md`. |
| **Check what an agent did in a session** | Query MongoDB `messages` by `conversationId` (from the chat URL `/c/<id>`); the message `content` array shows `tool_call` (name + args + output) and the final `text` part. |
| **Confirm KB tools loaded** | `docker logs noc-librechat \| grep "\[MCP\]\[kb\] Tools"` → expect `kb_search_noc`. |
| **Refresh the UI footer** | Re-run `apps/ai-platform/librechat/up.ps1` (recreates the container). `restart` alone won't do it. |
| **Restart one server** | `docker compose -f apps/ai-platform/librechat/docker-compose.yml restart <service>`. |
| **Rebuild one server after code change** | `docker compose -f apps/ai-platform/librechat/docker-compose.yml up -d --build <service>`. |

---

## 8. Feature history (the improvement program)

Manthan is being improved through a program of designed features, each brainstorm → spec → plan → build. Index: `docs/superpowers/specs/README.md`.

| Feature | Status |
|---|---|
| Clarify-before-invoke (slot-filling) | ✅ Shipped v0.8.0 |
| EV knowledge base (RAG `kb_search`) | ✅ Live |
| Agent-centric knowledge scopes | ✅ Live (folder = scope, one tool per profile) |
| Per-customer / Jira MCP servers (Ecog SD, ChargeZone QCH) | ✅ Live |
| Self-improvement loop ("Hermes") — nightly session review → verify → improve | 📄 Spec done, not built |

---

## 9. Known constraints & backlog

- **`min_score` needs recalibration** once real documents replace the synthetic sample corpus (current 0.5 was tuned on a ~0.1-wide score gap).
- **Scanned PDFs need OCR** — the parser extracts embedded text only; a scan ingests as zero chunks. The ingest summary's `empty_docs` count surfaces this.
- **`.docx` / `.xlsx` unsupported** by the ingester — convert to PDF.
- **Zero-chunk documents are re-parsed on every ingest run** (a deferred efficiency fix; harmless).
- **z.ai data residency** governs the model choice; the KB stays self-hosted so it works regardless of GLM-vs-Claude.

---

## 10. Reference

- **Design specs:** `docs/superpowers/specs/` — notably `2026-07-19-agent-centric-knowledge-scopes-design.md`, `2026-07-18-ev-knowledge-base-rag-design.md`, `2026-07-17-manthan-self-improvement-loop-design.md`.
- **kb-mcp-server details:** `services/mcp/kb/README.md`.
- **Production readiness:** `docs/PRODUCTION-READINESS.md`.
- **Repo:** `Quench-EV-Charger/noc-mcp-agent`.
