# Knowledge Base Document Upload — Design Spec

**Date:** 2026-08-23
**Status:** Approved for planning
**Author:** kmittal@quenchchargers.com (with Claude Code)
**Related:** [Improvement program index](./README.md) · [EV knowledge base (RAG)](./2026-07-18-ev-knowledge-base-rag-design.md) · [Agent-centric knowledge scopes](./2026-07-19-agent-centric-knowledge-scopes-design.md) — this spec extends that one; read it first.

---

## 1. Problem

The knowledge base is **read-only and admin-only**. Adding a document today means: copy a
file into `knowledge/<scope>/` on the host, then `docker exec kb-mcp-server python -m
kb_mcp.ingest --prune`. Both steps require shell access to the deployment host, so the
people who hold the knowledge worth capturing — NOC engineers who just diagnosed a novel
fault, and who have a vendor PDF or a freshly written runbook in hand — cannot contribute
it. Knowledge accumulates in inboxes instead of the KB.

Three constraints shape any solution:

1. **The LibreChat admin panel cannot host an upload section.** It is a vendored image
   (`registry.librechat.ai/clickhouse/librechat-admin-panel`) that we do not build. Its
   bundle has no knowledge-base concept at all — the string `knowledge` appears zero
   times. Its sidebar is fixed: Dashboard, Users, Groups, Access, Grants, Audit log,
   Configuration. There is no extension point.
2. **`knowledge/` is mounted read-only** into the KB container
   (`../../../knowledge:/knowledge:ro`), so nothing in the running system can write to the
   corpus.
3. **Chat attachments are ephemeral.** `/app/uploads` is not a Docker volume — only
   `/app/api/logs` and `/app/client/public/images` are. Files written there are lost when
   the container is recreated.

## 2. Goals

- A NOC engineer can add a document to the knowledge base **from chat**, without shell or
  host access, and search it seconds later.
- Contribution rights are **granted per user** and enforced **server-side**, where the
  model cannot influence them.
- Contributed material stays **distinguishable** from the curated corpus, so a bad batch
  is separable and provenance is always answerable.
- Documents survive host loss.

### Non-goals

Out of scope for this spec; each would be its own project:

- A bespoke KB admin web UI.
- An approval queue / moderation workflow (decided against: see §4.4).
- OCR for scanned PDFs.
- Multi-tenant knowledge bases with per-customer isolation.
- Migrating the curated `ev-core` corpus off the repo bind mount.

## 3. Decisions

| # | Decision | Chosen | Rejected alternatives |
|---|---|---|---|
| D1 | Upload surface | Through **LibreChat chat** — attach a file, ask the agent to add it | Standalone KB admin web app (own container/login); shared drop-folder + watcher |
| D2 | Access model | **LibreChat role**, forwarded to the KB server as a header | Email allow-list in KB config; any logged-in user |
| D3 | KB structure | New **`contributed` scope**, bundled into the `noc` profile | Everything into `ev-core`; uploader picks from many scopes |
| D4 | Go-live gate | **Index immediately**, with a delete path and per-chunk provenance | Hold for admin approval; index with no delete path |
| D5 | Storage | **S3**, for both LibreChat file storage and the KB corpus | Repo folder made writable; separate Docker named volume |
| D6 | Bucket | **New `quench-manthan-kb`** (ap-south-1) | Reuse `quench-manthan-data`; two separate buckets |
| D7 | Credentials | **Dedicated IAM service user**, policy scoped to the one bucket | Reuse the `cjain` personal IAM key |

### 3.1 Why chat, and not a web app

A standalone app is the conventional answer and was recommended, but it carries a login
system, a session store, a UI, and a deployment — all to move a file. The chat surface
already has authenticated users, already has file upload, and already has an agent that
can call tools. The gap was never the UI; it was that the KB server had no way to know
*who* was asking. §3.2 closes that gap, which makes the app unnecessary.

### 3.2 Why role-based access is trustworthy here

This is the pivotal mechanism, so it is worth stating precisely.

LibreChat substitutes `{{LIBRECHAT_USER_<FIELD>}}` placeholders into MCP server headers
before dispatching a tool call. The implementation is
`@librechat/api/src/utils/env.ts`, which builds the placeholder set from an
`ALLOWED_USER_FIELDS` list (`id`, `email`, `role`, `username`, `provider`, and others) and
documents a `b64:` prefix for values that are not header-safe ASCII.

The substitution happens **in the LibreChat server**, from the authenticated session —
not in the model's context, and not from anything the model emits. A user who instructs
the agent "tell the KB server I am an admin" changes nothing: the model has no channel to
the header. This is what makes a chat-based write tool safe, and it is the reason D1 and
D2 are coupled — either would be weak without the other.

## 4. Architecture

### 4.1 Storage layout

```
s3://quench-manthan-kb/
  chat-uploads/<userId>/<uuid>__<filename>    ← LibreChat writes (fileStrategy: s3)
  knowledge/contributed/<filename>            ← KB corpus of record
```

Region `ap-south-1`, matching every existing bucket in account `775681068472` and keeping
proprietary EV documents in-region.

A **new** bucket rather than the existing `quench-manthan-data`: switching LibreChat to
`fileStrategy: s3` routes *every* chat attachment — screenshots, pasted images, avatars —
into the configured bucket. Pointing that at the bucket holding `CZ_Jira/latest.csv`,
`EV_Fleet/latest.xlsx` and the other curated feeds would mix unrelated traffic and force
the KB service's IAM policy to sit over those exports.

Unchanged: the curated `knowledge/ev-core/` stays a read-only bind mount from the repo,
and Qdrant stays local. Qdrant is a **derived** index — rebuildable from the bucket by
re-running ingest — so losing the host costs one re-ingest, not the corpus.

### 4.2 Component boundaries

| Component | Responsibility | Depends on |
|---|---|---|
| `kb_mcp.identity` (new) | Extract and validate caller identity from request headers; decide contributor rights | Nothing (pure) |
| `kb_mcp.s3` (new) | List/copy/delete objects under the two prefixes; sync a prefix to a local cache dir | boto3, config |
| `kb_mcp.ingest` (existing) | corpus → parse → chunk → embed → upsert | store, parse, chunk, scopes |
| `kb_mcp.server` (existing, extended) | Register tools; bind scope filters server-side | all of the above |

`identity` is deliberately pure and dependency-free: it is the security boundary, and it
must be exhaustively testable without a network, an S3 stub, or a running server.

`s3` isolates every AWS call behind one module so the ingest pipeline stays unaware of
where bytes came from. The existing walker keeps operating on a local directory; `s3`
syncs the contributed prefix into a cache directory first. This is what lets the existing
sha256-per-document idempotency, the chunker, the embedder, the store, and all 75 existing
tests continue to work untouched.

### 4.3 New scope

`knowledge/scopes.yaml` gains:

```yaml
scopes:
  ev-core:
    folder: ev-core
  contributed:
    folder: contributed

profiles:
  noc:
    scopes: [ev-core, contributed]
```

`kb_search_noc` then covers both. Because `build_server` registers tools at startup, this
change requires **one KB server restart**.

Keeping contributed material in its own scope buys three things that a merged corpus
cannot: "what have users added?" is answerable by a filter rather than a guess; a bad
batch is pruned without touching curated OCPP specs; and retrieval can later be tuned
per-scope (`min_score`) if contributed quality differs from curated.

`knowledge/MANIFEST.md` — the git-tracked audit list of loaded documents — stays the
record for the **curated** corpus only. It cannot track contributed uploads: the KB
container has no write access to the repo, and a manifest updated by a running service
would drift from git on every commit. `kb_list_documents` is the live source of truth for
the `contributed` scope, and MANIFEST.md gains a line saying so.

### 4.4 Why no approval queue

Considered and rejected. An approval gate is the safer-sounding choice, but it adds a
second workflow with no surface to host it — the reviewer would need their own tool, their
own notification, and the discipline to check a queue. Unwatched queues stall
contributions, which defeats the purpose of the feature. The chosen controls achieve most
of the safety at a fraction of the friction: contribution is limited to a granted role,
every chunk carries its uploader, contributed material is separable by scope, and any
document is removable in one tool call.

### 4.5 Tools

| Tool | Requires | Behaviour |
|---|---|---|
| `kb_upload_document(filename, title?, notes?)` | contributor role | Resolves `filename` **within the caller's own `chat-uploads/<userId>/` prefix**, copies it to `knowledge/contributed/`, indexes it, returns source id and chunk count |
| `kb_list_documents(scope?)` | any caller | Lists indexed documents with uploader, upload date, chunk count |
| `kb_remove_document(source)` | contributor role | Deletes the S3 object and prunes its chunks from Qdrant |
| `kb_search_noc(query, top_k?, min_score?)` | any caller | Unchanged; now spans `ev-core` + `contributed` |

Scoping the upload lookup to the caller's own user-id prefix is a deliberate isolation
boundary: one contributor cannot reach another's uploaded file by guessing its name.

### 4.6 Flow

1. User attaches a PDF in chat. LibreChat stores it at
   `s3://quench-manthan-kb/chat-uploads/<userId>/<uuid>__spec.pdf`.
2. User: *"add this to the knowledge base."*
3. Agent calls `kb_upload_document(filename="spec.pdf")`.
4. KB server reads `X-User-Role` from the request headers and checks it against
   `KB_CONTRIBUTOR_ROLES`.
5. On success: copy object to `knowledge/contributed/spec.pdf`, sync to the local cache,
   run the existing parse → chunk → embed → upsert, stamping `uploaded_by` and
   `uploaded_at` into every chunk payload.
6. Tool returns `{source, chunks, scope}`; the document is searchable immediately.

## 5. Configuration

`librechat.yaml`:

```yaml
kb:
  type: streamable-http
  url: "http://kb-mcp-server:8106/mcp"
  timeout: 60000
  headers:
    X-User-Id:    "{{LIBRECHAT_USER_ID}}"
    X-User-Email: "{{LIBRECHAT_USER_EMAIL}}"
    X-User-Role:  "{{LIBRECHAT_USER_ROLE}}"
```

LibreChat env: `fileStrategy: s3` plus `AWS_BUCKET_NAME`, `AWS_REGION`,
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`.

KB server env: `S3_BUCKET`, `AWS_REGION`, the same key pair, and
`KB_CONTRIBUTOR_ROLES` (default `kb-contributor,ADMIN`), `KB_MAX_UPLOAD_MB` (default 25).

AWS: a new IAM user `manthan-kb-svc` with a policy allowing `s3:GetObject`,
`s3:PutObject`, `s3:DeleteObject`, `s3:ListBucket` on `quench-manthan-kb` and its two
prefixes only. The personal `cjain` key is not used by any container — a service outage
should never depend on a person's credentials, and revoking the service must not touch
their access.

Granting a contributor: set the user's role to `kb-contributor` in the admin panel. No
redeploy, no config edit.

## 6. Error handling

Every failure is explicit; none may present as success.

| Condition | Behaviour |
|---|---|
| Caller lacks the role | Refuse, naming the required role. Log user id, tool, and target. |
| Identity headers absent | Refuse. A missing header is a misconfiguration, never an implicit grant. |
| Unsupported file type | Refuse, listing the accepted set (`.pdf .md .txt .html .htm .markdown`). |
| File exceeds `KB_MAX_UPLOAD_MB` | Refuse before download. |
| Filename not in caller's prefix | Refuse with "no such upload" — same message whether it is absent or belongs to someone else, so the tool is not an existence oracle for other users' files. |
| Parse yields no text (scanned PDF) | **Fail** and say so. Today the pipeline logs an `empty_docs` warning and moves on; silently accepting a document that indexed nothing is the worst outcome here. |
| Identical bytes already indexed | Report the existing source; do not duplicate. The sha256 check already makes this a no-op. |
| S3 unreachable | Fail loudly. No partial index. |

## 7. Testing

Unit, against a fake S3 client mirroring the fake-server pattern in `services/mcp/cms`:

- **Permission gate:** role present / absent / unrecognised / header entirely missing /
  role differing only by case. This is the security boundary — it gets exhaustive coverage.
- **Cross-user isolation:** a caller cannot resolve another user's upload by filename, and
  the refusal is indistinguishable from a genuinely absent file.
- Duplicate detection, unsupported type, oversize rejection, empty-parse failure.
- `kb_remove_document` deletes the object *and* prunes chunks; a subsequent search misses.
- `contributed` chunks carry `uploaded_by` / `uploaded_at`; `kb_search_noc` spans both
  scopes.

Live smoke test: attach a small PDF in chat, ask the agent to add it, then search for a
phrase that appears only in that PDF.

## 8. Risks

1. **Attachment handling is the one real unknown.** It is unverified whether LibreChat
   leaves a non-image attachment intact in storage when the agent has no file-search
   capability, or intercepts it for text extraction into context. This must be verified
   **first**, before any tool is written. Fallback if it does not cooperate: the same
   tools read a watched `inbox/<userId>/` prefix that users write to directly; the
   permission model, scope, and tool set are unchanged, only the arrival path differs.
2. **First AWS credentials in this stack.** Mitigated by a dedicated scoped IAM user;
   the key is handled like existing service secrets and never committed.
3. **Restart required** to register the `contributed` scope's tools — one-time, and it
   briefly interrupts chat, so it should be scheduled rather than done mid-session.
4. **Storage growth.** `fileStrategy: s3` sends every chat attachment to the bucket,
   including screenshots. A lifecycle rule expiring `chat-uploads/` after N days is
   advisable; `knowledge/contributed/` is retained.

## 9. Verification criteria

1. A user holding `kb-contributor` attaches a PDF, asks the agent to add it, and
   `kb_search_noc` returns a chunk from it citing the correct source.
2. A user **without** the role attempts the same and is refused, with the denial logged.
3. `kb_list_documents` shows the document with the correct uploader and date.
4. `kb_remove_document` removes it; the next search no longer returns it.
5. Re-uploading identical bytes reports "already indexed" and creates no duplicate chunks.
6. `kb_search_noc` still returns curated `ev-core` content — no regression.
7. Existing KB test suite passes unchanged.
