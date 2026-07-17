# ChargeZone Jira MCP Server — Design (Quench-scoped, read-only)

**Date:** 2026-07-17
**Branch:** `feat/mcp-server`
**Status:** Design — awaiting user review before implementation planning

## 1. Purpose

Expose **Quench's field-operations tickets** — tracked in the `QCH` ("Quench")
project inside ChargeZone's Atlassian Cloud Jira — as **read-only** MCP tools, so
the NOC assistant can look up and reason over ticket history (charger offline, gun
not working, power failure at sites, etc.) alongside live NOC/CMS charger data.

This mirrors the existing `jira-mcp-server` (an Ecog token pilot) but is hardened
to a **single project (`QCH`)** and wired into the running chat stack + NOC agent.

## 2. Access verification (already done)

Probed the supplied Atlassian API token (account `sprathore@quenchchargers.com`)
against `https://team-1604552506221.atlassian.net`:

- `GET /rest/api/3/myself` → **200** (authenticates as "SPS Rathore").
- `GET /rest/api/3/project/search` → **31 projects** visible.
- Bounded reads confirmed on `QCH`, `CHAR`, `CPD`, `CAI` (real issues returned).
- **`QCH` is readable** — e.g. `QCH-4490`, `QCH-4489` … field-support tasks.
- The `ADOR` project referenced in the original link is **NOT** visible to this
  account (returns empty, not an error) — out of scope; flagged separately.

**Verdict:** the token can read Jira tickets. This is the token-clone path, *not*
the Ecog cookie+Playwright pivot.

Two API behaviors learned, baked into the design:
- The `/rest/api/3/search/jql` endpoint **rejects unbounded JQL** ("Please add a
  search restriction"). Our project-scoped wrapping makes every query bounded.
- Some project keys are **reserved JQL words** (e.g. `CHAR`) and must be quoted.
  We always quote the project key: `project = "QCH"`.

## 3. Scope & constraints

- **Data scope:** `QCH` project only. No other project is reachable through any
  tool, even though the token itself can see 30 more.
- **Read-only / GET-only:** no PUT/POST/DELETE. No write tool exists. Even the
  transitions tool only *reads* available transitions via GET; it never executes
  one.
- **Secrets:** email + API token injected as env/secret, never committed. The
  supplied token must be **rotated** after this work (it was pasted in chat).

## 4. Architecture

A near-clone of `jira-mcp-server`: FastMCP server over Jira Cloud REST v3, HTTP
Basic auth (`email:api_token`, base64), served as `streamable-http`.

```
chargezone-jira-mcp-server/
├── cz_jira_mcp/
│   ├── __init__.py
│   ├── __main__.py        # entrypoint; stdio or http transport
│   ├── config.py          # Settings: base_url, email, token, project_key=QCH, port=8105
│   ├── auth.py            # JiraAuthClient (Basic auth + JSON headers) — identical to template
│   └── server.py         # 4 read-only, QCH-locked tools + pure helpers (ADF→text)
├── tests/                # mocked httpx; ports template suite + new scope tests
├── Dockerfile
├── requirements.txt
└── README.md
```

### Naming (locked with user)
| Thing | Value |
|---|---|
| Directory | `chargezone-jira-mcp-server` |
| Python package | `cz_jira_mcp` |
| Tool prefix | `cz_jira_` |
| Docker service | `chargezone-jira-mcp-server` |
| librechat `mcpServers` key | `chargezone` |
| Tool IDs in agents.json | `cz_jira_<x>_mcp_chargezone` |
| HTTP port | `8105` (noc=8100, cms=8101, jira=8102, docgen=8103, ecog-sd=8104) |

> The librechat key is a clean single token (`chargezone`) — not the hyphenated
> `chargezone-jira` — to avoid tool-ID sanitization issues, matching the existing
> `noc`/`cms`/`jira` convention.

## 5. Tools (all read-only, all QCH-locked)

| Tool | Signature | Behavior & guard |
|---|---|---|
| `cz_jira_search` | `(conditions: str = "", limit: int = 50)` | Builds `project = "QCH" AND (<conditions>) ORDER BY updated DESC` (drops the `AND (...)` when `conditions` is empty). Always bounded. A smuggled `project = X` inside `conditions` yields empty via AND-contradiction. Returns compact issue summaries. |
| `cz_jira_get_issue` | `(key: str)` | `require_qch(key)` then `GET /issue/{key}`; full detail, ADF description flattened. |
| `cz_jira_get_comments` | `(key: str, limit: int = 50)` | `require_qch(key)` then comment list (author, created, body text). |
| `cz_jira_get_issue_transitions` | `(key: str)` | `require_qch(key)` then `GET /issue/{key}?fields=status&expand=transitions`; returns current status + *available* transitions. Read-only. |

**`require_qch(key)`** — rejects any key not matching `^QCH-\d+$` with a clear error
before any network call.

**Search input choice:** the tool takes JQL **conditions** (the part after the
project filter), not a full JQL string. This keeps full query power (status,
assignee, text, dates, ordering overrides) while making it structurally impossible
to leave the `QCH` project. Passing `""` returns the most-recently-updated issues.

Pure helpers (`_adf_to_text`, `_issue_summary`, `_issue_detail`, `_comment`,
`_drop_empty`, `_get_json`) are ported verbatim from the template.

## 6. Scope enforcement — defense in depth

1. **Allow-list of one:** `project_key = "QCH"` constant in `config.py`.
2. **Search injection:** every search is wrapped in `project = "QCH" AND (...)`;
   no JQL can widen scope, and every query is bounded (satisfies the API).
3. **Key guard:** `get_issue` / `get_comments` / `get_issue_transitions` reject
   non-`QCH-` keys before calling Jira.
4. **No writes:** zero write tools; every Jira call is a GET.

## 7. Error handling

Ported from template plus scope errors:
- `401/403` → "Jira auth failed — check credentials / account read access."
- `404` → "No such issue/resource or not visible to this account."
- `400` unbounded-JQL → cannot occur (queries always project-bounded), but a
  generic `raise_for_status` still surfaces any other 4xx/5xx clearly.
- Non-QCH key → "Only QCH (Quench) issues are accessible; got '<key>'."

## 8. Integration (this pass)

1. **Docker Compose** (`chat/docker-compose.yml`): add `chargezone-jira-mcp-server`
   service (build context = new dir, port 8105, env for email/token secret).
2. **librechat.yaml:** add under `mcpServers:`
   ```yaml
   chargezone:
     type: streamable-http
     url: "http://chargezone-jira-mcp-server:8105/mcp"
   ```
3. **NOC agent** (`chat/agents/agents.json`): add the 4 `cz_jira_*_mcp_chargezone`
   tool IDs to the active NOC agent's `tools[]`, then push via
   `chat/agents/restore-agents.ps1`.
   - **Open implementation detail:** `agents.json` contains several NOC-flavored
     records (`NOC server agent glm-4.7`, `NOC server agent`, `NOC L1 Support`,
     plus duplicates). The exact record(s) to patch will be confirmed against the
     live LibreChat DB (the currently-served NOC agent) during implementation, not
     guessed here.
4. **Rebuild** the affected containers and smoke-test one live query
   (`cz_jira_search("status = \"To Do\"", 5)` and a `QCH-####` fetch).

## 9. Testing (boil the lake)

Port the template's mocked test suite and add scope tests. No live network in CI.
- `test_config.py` — settings load; `project_key` defaults to `QCH`.
- `test_auth.py` — Basic-auth header is correct base64 of `email:token`.
- `test_helpers.py` — ADF→text, summary/detail/comment shaping, `_drop_empty`.
- `test_tools.py` — each tool calls the right endpoint/params against a mocked
  client; ADF flattening end-to-end.
- **`test_scope.py` (new):**
  - `require_qch` rejects `CHAR-1`, `ADOR-9`, `qch-1` (case), empty, malformed.
  - `cz_jira_search` builds `project = "QCH" AND (...)`; empty conditions →
    `project = "QCH" ORDER BY updated DESC` (no dangling `AND ()`).
  - a `conditions` containing `project = ADNOC` still resolves to a QCH-only,
    empty-yielding query (assert the emitted JQL string).

## 10. Data residency (D1)

`QCH` ticket contents (Quench field-ops data: site names, addresses, fault
descriptions) reach the inference provider (GLM / z.ai) when the agent uses these
tools. This is Quench's own data, but it is the same **D1 residency** gate as the
Ecog Jira and CMS work: internal/dev use until residency sign-off. README carries
the note.

## 11. Success criteria

- `python -m pytest` green in the new package (template suite + scope tests).
- Server starts on `:8105`; `cz_jira_search("", 5)` returns real `QCH-####`
  issues live; `cz_jira_get_issue("QCH-4490")` returns detail.
- `cz_jira_get_issue("CHAR-1")` is rejected without a network call.
- NOC agent in the running chat can answer a "show me open Quench charger tickets"
  style question end-to-end after container rebuild.
- No tool can read any project other than `QCH`; no write path exists.

## 12. Out of scope

- `ADOR` and the other 29 projects (deliberately unreachable).
- Any write/transition-execution capability.
- Pagination/cursor follow-up (single page per search, like the template).
- Version bump / release (handled per the standard per-feature bump at completion).
