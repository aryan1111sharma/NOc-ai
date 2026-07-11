# jira-mcp-server — Ecog Jira (read-only)

Self-hosted [FastMCP](https://github.com/jlowin/fastmcp) server exposing **Ecog's Atlassian
Cloud Jira** (REST API v3) as read-only tools, mirroring `cms-mcp-server`.

## Tools
- `jira_search(jql, limit=50)` — search issues by JQL.
- `jira_get_issue(key)` — full detail of one issue (description flattened from ADF).
- `jira_get_comments(key, limit=50)` — comments (author, created, body text).
- `jira_get_issue_transitions(key)` — current status + *available* transitions, via
  `GET /rest/api/3/issue/{key}?fields=status&expand=transitions` (read only).

Read-only by construction: there is no write tool.

## Configuration (env / `.env`)
| Var | Default | Notes |
|---|---|---|
| `JIRA_BASE_URL` | `https://ecog.atlassian.net` | Ecog Cloud site. |
| `JIRA_EMAIL` | *(required)* | Shared read-only account. |
| `JIRA_API_TOKEN` | *(required)* | Atlassian API token — **inject as a secret**. |
| `MCP_TRANSPORT` | `stdio` | `stdio` or `http`. |
| `MCP_HOST` / `MCP_PORT` | `127.0.0.1` / `8102` | Keep internal (never publish). |

Create an API token at <https://id.atlassian.com/manage-profile/security/api-tokens>.

## Run
```bash
pip install -r requirements.txt
JIRA_EMAIL=svc@… JIRA_API_TOKEN=… python -m jira_mcp        # stdio
MCP_TRANSPORT=http python -m jira_mcp                        # http on :8102
```

## Test
```bash
python -m pytest -v
```

> **Data note:** Ecog Jira is vendor-confidential; tool results reach the inference provider.
> Internal/dev use only until the residency decision (D1) is signed off.
