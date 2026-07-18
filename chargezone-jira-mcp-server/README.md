# chargezone-jira-mcp-server — ChargeZone Jira, Quench project (read-only)

Self-hosted [FastMCP](https://github.com/jlowin/fastmcp) server exposing the **`QCH`
(Quench)** project inside **ChargeZone's Atlassian Cloud Jira** (REST API v3) as read-only
tools. Scoped to QCH only — no other project is reachable. Mirrors `jira-mcp-server`.

## Tools
- `cz_jira_search(conditions="", limit=50, order_by="")` — search QCH issues by JQL filter
  *conditions* only (no `ORDER BY` in conditions — pass the separate, whitelist-validated
  `order_by`, e.g. `'cf[10120] ASC, assignee ASC'`; default is most-recently-updated). The
  server prepends `project = "QCH"`; you cannot escape the project. Empty → newest QCH issues.
  Results include `priority_level` (P1/P2/P3, from the real ChargeZone "Priority Level" custom
  field — not Jira's built-in Priority) and `ocpp_id` (charger id) when set.
- `cz_jira_get_issue(key)` — full detail of one QCH issue (description flattened from ADF);
  also includes `priority_level` / `ocpp_id`.
- `cz_jira_get_comments(key, limit=50)` — comments (author, created, body text).
- `cz_jira_get_issue_transitions(key)` — current status + *available* transitions (read only).
- `cz_jira_export(conditions="", all_fields=False, limit=1000, order_by="")` — export QCH
  issues as {headers, rows, row_count, truncated} via the REST search API (no CSV servlet).
  Curated columns: Issue key, Issue Type, Summary, Status, Priority Level, Priority, Assignee,
  Reporter, OCPP ID, Category, Hardware Failure Description, Created, Updated (plus
  Description/Parent key/Due date when `all_fields=True`). Pass to docgen `create_excel` for a
  download link.
- `cz_jira_stats(conditions="")` — QCH analytics: totals + by status/type/priority/
  **priority_level** (real P1/P2/P3)/assignee + created last 7/30 days (GET-only, samples up
  to 2000 issues).
- `cz_jira_project_meta()` — QCH statuses, issue types, priorities.
- `cz_jira_list_dashboards()` — dashboards + saved filters referencing QCH/Quench.

`order_by` (on search/export) is validated by a strict whitelist — comma-separated
`<field> [ASC|DESC]`, where field is an identifier, `cf[NNN]`, or a quoted name — so it can
never be used to escape the QCH project scope.

By-key tools accept **only `QCH-####` keys**. Read-only by construction — there is no write tool.

## Configuration (env / `.env`)
| Var | Default | Notes |
|---|---|---|
| `CZ_JIRA_BASE_URL` | `https://team-1604552506221.atlassian.net` | ChargeZone Cloud site. |
| `CZ_JIRA_EMAIL` | *(required)* | Account that owns the API token. |
| `CZ_JIRA_API_TOKEN` | *(required)* | Atlassian API token — **inject as a secret**. |
| `PROJECT_KEY` | `QCH` | Hard scope; do not change without a scope review. |
| `MCP_TRANSPORT` | `stdio` | `stdio` or `http`. |
| `MCP_HOST` / `MCP_PORT` | `127.0.0.1` / `8105` | Keep internal (never publish). |

Create an API token at <https://id.atlassian.com/manage-profile/security/api-tokens>.

## Run
```bash
pip install -r requirements.txt
CZ_JIRA_EMAIL=svc@… CZ_JIRA_API_TOKEN=… python -m cz_jira_mcp        # stdio
MCP_TRANSPORT=http python -m cz_jira_mcp                             # http on :8105
```

## Test
```bash
python -m pytest -v
```

> **Data note:** QCH tickets are Quench field-ops data; tool results reach the inference
> provider. Internal/dev use only until the residency decision (D1) is signed off.
