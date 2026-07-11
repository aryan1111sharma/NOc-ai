# Ecog Jira MCP — Read-Only Pilot (Design)

**Date:** 2026-07-11
**Backlog epic:** `JIRA` (JR-0…JR-5) in `docs/PRODUCTION-READINESS.md`
**Status:** Approved design — ready for implementation plan (writing-plans).

## Goal

Stand up a **self-hosted, read-only MCP server** that surfaces **Ecog's** (charging-controller
vendor) Jira into the assistant, so the team can search issues, read detail + comments, and see
issue status/available transitions — primarily to support **RCA / charger-diagnostics** work.

This is the **pilot** for the two-tenant Jira effort. The ChargeZone tenant is a later,
separate replication of the same proven pattern.

## Decisions locked during brainstorming

| Decision | Choice | Rationale |
|---|---|---|
| Scope | **Pilot one tenant first** | Lowest risk; prove the pattern before replicating. |
| Tenant | **Ecog** (controller vendor) | Fits the RCA/charger-diagnostics direction. |
| Hosting | **Atlassian Cloud** (`ecog.atlassian.net`) | Enables Jira Cloud REST v3 + Basic auth. |
| Auth model | **One shared read-only account** (API token) | Quench holds one login in Ecog's tenant; matches the **D2** shared-identity model. |
| Build path | **Self-hosted FastMCP** (mirror `noc_mcp`/`cms_mcp`) | Reuses every existing repo pattern (tests, audit, trust boundary, scoping). The hosted Atlassian remote MCP assumes per-user OAuth we don't have and is hard to lock to read-only. |
| Tool surface | search · get issue · comments · transitions/status | RCA-focused read set. `list_projects` and agile boards/sprints deferred. |

## Non-goals (out of scope for this pilot)

- **Any write path** — create/transition/comment issues (deferred to **JR-5**).
- The **ChargeZone** tenant (later replication).
- **Per-user OAuth** identity (team doesn't hold individual Ecog accounts).
- **Boards & sprints** (Jira Software agile) and **`list_projects`**.

## Architecture

New top-level package mirroring `cms-mcp-server/`:

```
jira-mcp-server/
  jira_mcp/
    __init__.py
    config.py      # pydantic Settings from env/.env
    auth.py        # JiraAuthClient(httpx.AsyncClient) — HTTP Basic
    server.py      # build_server() -> FastMCP with @mcp.tool read-only funcs
    __main__.py    # entrypoint (transport from settings)
  tests/           # pytest, mirrors cms-mcp-server/tests (per P3-5)
  README.md
  Dockerfile
  requirements.txt # fastmcp, httpx, pydantic-settings
```

### Config (`config.py`)

`Settings(BaseSettings)` with `env_file=".env"`, `extra="ignore"`:

| Field | Default | Notes |
|---|---|---|
| `jira_base_url` | `https://ecog.atlassian.net` | Ecog Cloud site. |
| `jira_email` | *(required)* | Shared account email. |
| `jira_api_token` | *(required)* | Atlassian API token — **injected as a secret, never committed** (P0-6). |
| `request_timeout` | `60.0` | Jira REST is fast vs. the legacy CMS. |
| `mcp_transport` | `stdio` | `stdio` or `http`. |
| `mcp_host` | `127.0.0.1` | Internal only (P0-3). |
| `mcp_port` | `8102` | noc=8100, cms=8101, **jira=8102**. |

### Auth (`auth.py`)

`JiraAuthClient(httpx.AsyncClient)` — Jira Cloud REST v3 uses **HTTP Basic** with
`base64(email:api_token)`. Far simpler than the CMS cookie/re-login dance:

- Set `Authorization: Basic <b64>` and `Accept: application/json` once (in `__init__` or via
  `httpx.BasicAuth`).
- No session state, no re-login loop (the API token doesn't expire per session).
- A `401`/`403` surfaces as a clear `RuntimeError` (bad/insufficient token) rather than being
  silently retried.

### Tools (`server.py`)

`build_server()` returns `FastMCP(name="Ecog Jira (read-only)")`. Each tool is **GET-only** and
trims the response to a small, LLM-friendly field set (parallel to CMS's `_trim`).

| Tool | Endpoint | Returns (trimmed) |
|---|---|---|
| `jira_search(jql, limit=50)` | `GET /rest/api/3/search/jql` | per issue: `key, summary, status, type, priority, assignee, updated, project` |
| `jira_get_issue(key)` | `GET /rest/api/3/issue/{key}` | `key, summary, status, type, priority, reporter, assignee, created, updated, labels, components, description` |
| `jira_get_comments(key, limit=50)` | `GET /rest/api/3/issue/{key}/comment` | per comment: `author, created, body` |
| `jira_get_issue_transitions(key)` | `GET /rest/api/3/issue/{key}/transitions` | current `status` + available transitions `[{id, name, to}]` (**read only — never POSTs**) |

**ADF flattener (`_adf_to_text`)** — the one nontrivial helper. Jira Cloud v3 returns
`description` and comment `body` as **Atlassian Document Format** (nested JSON), not plain text.
A small recursive flattener walks the ADF node tree and concatenates `text` nodes (with sensible
newlines for paragraphs/list items) so payloads stay readable and compact.

## Read-only guarantee

Stronger than the charger-proxy's GET-lock (**P0-4**): **no write tool exists in the code.**
There is no POST/PUT/DELETE path at all. `jira_get_issue_transitions` reads what transitions are
*possible* but never executes one. A test asserts no write tool is registered on the server.

## Data flow, identity & residency

- **Identity:** single shared Ecog account → every assistant user sees exactly what that one
  account can see (shared-identity, consistent with **D2**). Acceptable for an internal pilot;
  a per-user model is out of scope here.
- **Residency (D1):** Ecog data is **vendor-confidential**, and tool results flow to the
  configured inference provider (z.ai/GLM today). Therefore **this pilot is internal/dev use
  only** until **D1** is signed off for this data.

## LibreChat wiring (JR-3 / JR-4)

1. Build and validate **standalone first**: pytest green + one live JQL read against Ecog.
2. Then add a `jira` entry to `chat/librechat.yaml` `mcpServers`, HTTP transport on internal
   port **8102**, **never published** (P0-3).
3. Expose it **only behind a scoped agent** so just the intended audience sees it (JR-4 / AC-8) —
   Ecog tools must not be visible to ChargeZone-facing users.

## Testing (P3-5)

pytest mirroring `cms-mcp-server/tests`, using mocked httpx responses:

- **Auth:** the `Authorization: Basic` header is set correctly from email + token.
- **Trimming:** each tool returns only the whitelisted fields, dropping nulls/empties.
- **ADF:** `_adf_to_text` flattens representative ADF (paragraphs, lists, nested marks) to text.
- **Read-only:** the built server registers **no** write tool.
- **Search:** JQL param is forwarded and `limit` is honored.

Wired into the **P3-1** CI gate alongside the noc/cms suites.

## Acceptance criteria (maps to backlog JR-*)

- **JR-1:** an authenticated `jira_search` against Ecog returns real issues.
- **JR-2:** the four read tools above are implemented; write deferred.
- **JR-3:** the server appears in LibreChat and a tool call succeeds end-to-end from chat.
- **JR-4:** a user not scoped to Ecog cannot see/call these tools.
- Pilot done when the RCA trio + transitions work from chat, behind a scoped agent, on internal
  ports, with the test suite green in CI.

## Open items to confirm during implementation

- Exact shared-account **email + API token** provisioning (who creates the token; read-only
  project permissions in Ecog's tenant — mirrors **P1-1**).
- The Ecog **project key(s)** the team cares about for RCA (informs example JQL in the README;
  not required since `jira_search` spans all visible projects).
- Whether Ecog's site uses the newer `/rest/api/3/search/jql` or the older `/search` endpoint
  (verify against the live tenant during JR-1).
