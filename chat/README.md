# noc-mcp-agent · chat

The NOC AI Assistant web app: **LibreChat** (ChatGPT-like UI with saved chats + shareable
links) driven by **GLM 5.2**, using the read-only **NOC MCP server** (`../mcp-server`) for
live NOC data.

```
LibreChat (web UI :3080)  →  GLM 5.2 (z.ai API)  →  mcp-server (:8100, 153 read-only tools)  →  NOC backend
```

## Prerequisites

1. **Docker Desktop** installed and running (this stack is Docker-based).
2. A **GLM account with balance** — the API returns `429 "Insufficient balance"` until the
   account is recharged / has a resource package.
3. A **dedicated read-only NOC user** (an admin creates it in the NOC app — no `noc-server`
   change). Read-only is enforced twice: the MCP server exposes only GET tools, *and* this
   account should hold only read permissions.

## Setup

```bash
cd chat
cp .env.example .env
```

Fill in `.env`:

```bash
# generate secrets (Git Bash / WSL / macOS / Linux):
openssl rand -hex 32   # -> CREDS_KEY, JWT_SECRET, JWT_REFRESH_SECRET
openssl rand -hex 16   # -> CREDS_IV, MEILI_MASTER_KEY
```
Then set `GLM_API_KEY` and the `NOC_*` read-only credentials.

## Run

```bash
docker compose up -d          # builds mcp-server, starts LibreChat + mongo + meilisearch
# open http://localhost:3080  — register an account (open registration)
docker compose logs -f mcp-server   # check the MCP server logged in to NOC
```

## Wire GLM + NOC tools together (in LibreChat)

The NOC MCP tools are attached to LibreChat **Agents**:

1. In LibreChat, open the **Agent Builder**.
2. Create an agent → pick the **GLM** endpoint / `glm-5.2` model.
3. Under **Tools / MCP**, enable the **`noc`** server's tools.
4. Give it a NOC system prompt (see below) and save. Chat with the agent.

### Suggested system prompt (starter)

> You are the QuenchNOC assistant for EV-charger operations. Answer questions about the
> charger fleet, tickets, alarms, and troubleshooting using the NOC tools. Prefer
> `dashboard_summary` for fleet online/offline counts. For issues, look up the charger's
> alarm history and error-code configs and suggest likely causes and fixes. Be concise and
> cite the numbers you retrieved.

## Verify (once GLM has balance)

Ask the agent:
- "How many chargers are online vs offline?" → should call `dashboard_summary`.
- "Show open service tickets and summarize them." → ticket list/stats tools.
- "Charger &lt;id&gt; keeps faulting — likely cause and fix?" → alarm history + error-code tools.

Confirm: a chat can be **saved** and **shared via link** (LibreChat share feature).

## Notes / follow-ups

- **Tool count:** all 153 NOC GET tools are exposed. If GLM misfires on tool selection,
  clean up tool names in `../mcp-server` (`mcp_names`) or curate the RouteMap.
- **RAG** (optional, later): add pgvector + LibreChat's rag_api for grounding troubleshooting
  on `merged_error_mapping.csv` and docs.
- **Untested end-to-end here:** authored without Docker/GLM-balance available on the build
  machine — validate on first run and adjust `librechat.yaml` version pin if needed.
