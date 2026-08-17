# noc-mcp-agent

AI assistant stack for the QuenchNOC EV-charger NOC. A web chat interface (LibreChat)
driven by GLM 5.2, answering questions about the NOC fleet, tickets, and troubleshooting —
using a **read-only** MCP server over the existing NOC backend API.

The NOC backend (`noc-server`) is an **external, unchanged** dependency. This repo never
modifies it; it only calls its HTTP API as an authenticated service account.

## Layout (monorepo)

```
services/mcp/noc/   FastMCP read-only server exposing NOC GET endpoints as MCP tools
apps/ai-platform/librechat/                LibreChat deployment (docker-compose + config)   [later phase]
knowledge/           optional RAG assets for troubleshooting          [later phase]
docs/                design + operations notes
```

## Architecture

```
LibreChat (web UI, saved chats + share links, open access)
   └── GLM 5.2 (brain: reasoning + tool calls)
          └── services/mcp/noc (read-only MCP tools, this repo)
                 └── NOC backend  (/api/v1, unchanged)
```

See `services/mcp/noc/README.md` for running the MCP server.
