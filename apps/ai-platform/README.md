# AI Platform

This directory holds the chat and orchestration layer that fronts the MCP tool servers in
`services/mcp/`. It is organized as a category, not a single product: the AI platform in use
today is `librechat/`, but nothing outside this directory should assume LibreChat specifically.

```
ai-platform/
└── librechat/   the current AI platform implementation (LibreChat + its Docker Compose stack)
```

If the platform is ever replaced (Open WebUI, a custom UI, etc.), the new implementation is
expected to live as a sibling directory here (e.g. `ai-platform/open-webui/`), consuming the
same `services/mcp/*` servers over MCP. No other part of the repository should need to change
for that swap — the MCP servers have no knowledge of which platform is calling them.

See `librechat/README.md` and `librechat/RUNBOOK.md` for the current implementation.
