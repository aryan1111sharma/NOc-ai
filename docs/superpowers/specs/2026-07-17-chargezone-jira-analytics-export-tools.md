# ChargeZone Jira — Analytics & Export Tools (Design)

**Date:** 2026-07-17
**Branch:** `feat/mcp-server`
**Extends:** `chargezone-jira-mcp-server` (shipped v0.6.0) — the read-only, QCH-scoped Jira MCP.
**Status:** Design — awaiting review before implementation planning.

## 1. Purpose

Add four read-only tools to the ChargeZone Jira MCP so the NOC Engineer agent can answer
"dashboard/analytics" questions about Quench (QCH) tickets and **export** them as a
downloadable file. All new tools keep the existing invariant: **QCH-locked, GET-only**, no
write path.

Live discovery (2026-07-17) confirmed against the real token what is and isn't useful:
- CSV export endpoint returns real QCH data (`text/csv`). ✅
- Status/priority/assignee distributions computable from search. ✅
- 6 dashboards exist (incl. "Quench", "Quench R&D") + 56 saved filters. ✅ (names/JQL only;
  gadget chart-data is NOT REST-exposed)
- QCH has **no sprints** (1 simple board), **0 versions, 0 components** → those analytics are
  not applicable and are deliberately out of scope.

## 2. Decisions (locked with user)

- **Stats totals:** keep the GET-only guard intact. The fast native count endpoint
  (`/search/approximate-count`) is a POST, which the read-only client blocks by design.
  `cz_jira_stats` therefore paginates `GET /search/jql` up to a **2000-issue cap** and tallies;
  beyond the cap it returns `truncated: true` and `total: ">=2000"`.
- **Export cap:** `cz_jira_export` default `limit=1000` (Jira's own export default); caller may
  raise it.
- **CSV delivery:** the tool returns structured `{headers, rows}`; the agent chains docgen's
  `create_excel` to return a downloadable `.xlsx` link (docgen is Excel-only).

## 3. Tools (all read-only, QCH-locked, reuse `build_scoped_jql` / `require_qch`)

### 3.1 `cz_jira_export(conditions="", all_fields=False, limit=1000)`
- Calls Jira's issue-navigator CSV export:
  `GET /sr/jira.issueviews:searchrequest-csv-{current|all}-fields/temp/SearchRequest.csv`
  with `jqlQuery=<build_scoped_jql(conditions)>` and `tempMax=<limit>`.
  - `all_fields=False` → `current-fields` (14 columns: Issue Type, Issue key, Issue id,
    Summary, Assignee, Assignee Id, Reporter, Reporter Id, Priority, Status, Resolution,
    Created, Updated, Due date).
  - `all_fields=True` → `all-fields` (every field; wide).
- Parses the returned CSV (`csv` stdlib) into `{headers: list[str], rows: list[list[str]],
  row_count: int, truncated: bool}`. `truncated = row_count >= limit`.
- The existing `JiraAuthClient` allows GET; the CSV endpoint is Basic-auth (verified). Empty
  body / non-CSV (login redirect) → clear `RuntimeError`.
- **Download flow (agent-orchestrated):** agent calls
  `create_excel(filename="quench-QCH-export.xlsx", headers=<headers>, rows=<rows>)` → link.

### 3.2 `cz_jira_stats(conditions="")`
Returns:
```
{ total, sampled, truncated,
  by_status: {name: n}, by_type: {name: n}, by_priority: {name: n},
  top_assignees: {name: n},           # top 10, incl. "Unassigned"
  created_last_7d, created_last_30d }
```
- Paginates `GET /rest/api/3/search/jql` with `jql=<build_scoped_jql(conditions)>`,
  `fields=status,issuetype,priority,assignee,created`, `maxResults=100`, following
  `nextPageToken` until exhausted or **2000 issues** collected.
- `total` = exact sampled count, or the string `">=2000"` when `truncated`.
- `created_last_7d/30d` compare each issue's `created` timestamp to now (Python
  `datetime.now(timezone.utc)`).

### 3.3 `cz_jira_project_meta()`
Returns `{key: "QCH", name, statuses: [...], issue_types: [...], priorities: [...]}`.
- `GET /rest/api/3/project/QCH` (name + issueTypes), `GET /rest/api/3/project/QCH/statuses`
  (flatten unique status names across issue types), `GET /rest/api/3/priority` (global names).
- No params. Helps the agent build valid `conditions` for search/stats/export.

### 3.4 `cz_jira_list_dashboards()`
Returns `{dashboards: [{id, name}], quench_filters: [{id, name, jql}]}`.
- `GET /rest/api/3/dashboard?maxResults=50` → all dashboards (id, name).
- `GET /rest/api/3/filter/search?maxResults=100&expand=jql` → keep only filters whose `name`
  OR `jql` mentions `QCH`/`Quench` (case-insensitive) to stay on-scope.
- Reference/navigation aid: dashboard chart-data is not API-exposed; this gives the IDs to open
  in Jira plus reusable filter JQL the agent can feed to `cz_jira_search`.

## 4. Scope & safety

- `cz_jira_export` and `cz_jira_stats` route user input through `build_scoped_jql` — no query can
  leave `project = "QCH"`, no `ORDER BY`, balanced parens (the invariant verified in v0.6.0).
- `cz_jira_project_meta` / `cz_jira_list_dashboards` are fixed metadata reads; `list_dashboards`
  filters saved-filter results to QCH/Quench mentions only.
- Still GET-only by construction (the `JiraAuthClient` blocks non-GET); no write tool added.

## 5. Error handling

Reuse `_get_json` semantics (401/403 → auth message, 404 → not-visible, non-JSON → clear
error). Export adds: non-CSV/login-redirect body → `RuntimeError("Jira CSV export failed …")`.

## 6. Integration

1. Extend `chargezone-jira-mcp-server/cz_jira_mcp/server.py` with the 4 tools (+ a small CSV
   helper). No new server/port; rebuild the existing container.
2. **NOC Engineer agent** (`agents.json`): add tool IDs `cz_jira_export_mcp_chargezone`,
   `cz_jira_stats_mcp_chargezone`, `cz_jira_project_meta_mcp_chargezone`,
   `cz_jira_list_dashboards_mcp_chargezone`; ensure docgen `create_excel_mcp_docgen` is present
   (add if missing) so the export→download chain works; restore + restart LibreChat.
3. One instruction line: route Quench/QCH export & analytics requests to these tools, and for
   exports chain `create_excel` to return a download link.
4. Version bump (minor) + push.

## 7. Testing (boil the lake, mocked httpx)

- `cz_jira_export`: mock CSV endpoint → asserts scoped `jqlQuery`, parses headers+rows, sets
  `truncated` at the cap, `all_fields` picks the right variant, non-CSV body raises.
- `cz_jira_stats`: mock 2 pages via `nextPageToken` → asserts tallies, `top_assignees` top-N +
  "Unassigned", `created_last_*` windows, cap/`truncated` behavior, scoped JQL.
- `cz_jira_project_meta`: mock 3 endpoints → asserts flattened unique statuses/types/priorities.
- `cz_jira_list_dashboards`: mock dashboard + filter/search → asserts QCH/Quench filter filtering.
- Reuse existing scope tests; add tool-level checks that export/stats reject a foreign/ORDER-BY
  `conditions` via `build_scoped_jql`.
- Registration test updated: server now exposes 8 tools, all read-only (no write verbs).

## 8. Success criteria

- New unit tests green; total server tools = 8.
- Live: `cz_jira_stats("")` returns real QCH distributions; `cz_jira_export("")` returns
  parseable headers/rows; agent produces a working `.xlsx` download link via docgen;
  `cz_jira_project_meta()` lists QCH statuses/types; `cz_jira_list_dashboards()` returns the
  Quench dashboards + QCH filters. Foreign/ORDER-BY conditions still rejected.

## 9. Out of scope

- Sprint/velocity, versions, components (not used by QCH).
- Dashboard gadget chart-data (not REST-exposed).
- Any write/mutation; the count POST endpoint (kept blocked to preserve GET-only).
