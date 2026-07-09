# noc-mcp-agent · mcp-server

A **read-only** [FastMCP](https://gofastmcp.com) server that exposes the QuenchNOC backend's
GET endpoints as MCP tools. It authenticates to the NOC as a service account and never
performs writes — only `GET` operations are surfaced (every other method is excluded at the
route-map layer).

## How it works

- The NOC OpenAPI spec is **bundled** at `noc_mcp/openapi/noc_openapi.json` (the backend
  does not serve it publicly). `FastMCP.from_openapi` turns each GET operation into a tool.
- `NocAuthClient` logs in via `POST /api/v1/auth/login`, attaches the bearer token,
  re-logs-in shortly before expiry, and re-logs-in once on a `401` (there is no refresh
  token). See `noc_mcp/auth.py`.

## Setup

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Windows
cp .env.example .env        # then fill in NOC_EMAIL / NOC_PASSWORD
```

> **Use a dedicated read-only NOC user** for `NOC_EMAIL`/`NOC_PASSWORD`, not an admin
> account. Read-only is enforced two ways: this server exposes only GET tools, *and* the
> account should hold only read permissions.

## Run

```bash
# stdio (Claude Desktop / local MCP clients)
python -m noc_mcp

# http (remote / LibreChat)
MCP_TRANSPORT=http python -m noc_mcp     # serves on MCP_HOST:MCP_PORT (default 127.0.0.1:8100)
```

## Test

```bash
python -m pip install pytest pytest-asyncio
python -m pytest
```

## Regenerate the bundled spec (when the NOC API changes)

```bash
# from an env with the *backend's* requirements installed:
python scripts/generate_openapi.py /path/to/noc-server/noc_backend
```

## Config (env / `.env`)

| Var | Default | Purpose |
|---|---|---|
| `NOC_BASE_URL` | `https://www.manthanquench.online` | NOC backend base URL |
| `NOC_EMAIL` / `NOC_PASSWORD` | — | service-account credentials (read-only user) |
| `MCP_TRANSPORT` | `stdio` | `stdio` or `http` |
| `MCP_HOST` / `MCP_PORT` | `127.0.0.1` / `8100` | http transport bind |
| `REQUEST_TIMEOUT` | `30` | per-request timeout (seconds) |

## Notes / follow-ups

- All **153** GET operations are exposed. Auto-generated tool names are verbose
  (`dashboard_summary_api_v1_dashboard_summary_get`); a naming-cleanup pass (`mcp_names` /
  `mcp_component_fn`) is recommended to improve LLM tool-selection.
