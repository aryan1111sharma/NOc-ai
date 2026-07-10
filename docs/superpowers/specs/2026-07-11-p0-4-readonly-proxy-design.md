# P0-4 — Lock the charger proxy to GET (read-only enforcement)

**Status:** approved design · **Date:** 2026-07-11 · **Backlog item:** P0-4
**Scope:** `mcp-server` (NOC MCP server) only. No changes to LibreChat, CMS server, or the NOC backend.

## Problem

The NOC MCP server exposes only `GET` operations as tools (route map in
`mcp-server/noc_mcp/server.py:27-30`), which correctly excludes the separate
`POST`/`PUT`/`DELETE` proxy operations. **But** the exposed **GET** charger-proxy tools carry a
`method` **query parameter**, and the NOC backend uses that parameter to choose which HTTP
method it forwards to the live on-charger service. So a caller can invoke a *GET* proxy tool
with `method=POST` (or `PUT`/`DELETE`) and cause a **write to a live charger** — through a tool
the route map treats as read-only.

Two exposed tool families are affected (both `GET`, both carry `method`):

- `proxy_api_call_path_based_..._get` — port/path in the URL
- `proxy_api_call_..._get` — path/port as query params

The route map does not protect against this because the dangerous capability is a *parameter*
of a GET operation, not a separate non-GET operation.

## Goal / acceptance criterion

A non-GET call through a proxy tool is **rejected server-side**, before any request reaches the
NOC backend. (Matches the backlog acceptance criterion for P0-4.)

## Decision

**Hard-reject** a non-GET `method` (chosen over silent coercion) so every write attempt fails
loudly and is visible in logs/audit.

## Approach — FastMCP `on_call_tool` middleware

Chosen over typed GET-only wrappers (heavier; renames tools, breaking the on-charger catalog
and agent wiring) and over schema-stripping (hides rather than rejects). The middleware is a
single central chokepoint, covers both proxy families and any future proxy-like tool, changes
no tool names or schemas, and is trivially unit-testable.

### Components

- **New module** `mcp-server/noc_mcp/readonly.py`
  - `class ReadOnlyProxyMiddleware(fastmcp.server.middleware.Middleware)` implementing
    `async def on_call_tool(self, context, call_next)`.
  - A small pure helper, e.g. `def _blocked_method(tool_name: str, arguments: dict) -> str | None`,
    that returns the offending method string when the call must be blocked, else `None` — so the
    decision logic is testable without constructing middleware context objects.
- **Wire-up** in `mcp-server/noc_mcp/server.py` `build_server()`:
  `mcp.add_middleware(ReadOnlyProxyMiddleware())` (after `_register_control_center_catalog`).

### Rule

For a tool call:

1. If the tool name does **not** contain `"proxy"` → allow (`await call_next(context)`).
   (Scoping to proxy tools avoids false-positives on any unrelated GET endpoint that happens to
   expose a `method` query param; every non-GET *operation* is already excluded by the route
   map, so nothing else can write.)
2. Read the `method` argument from the call arguments.
   - Absent / `None` → allow (NOC defaults to the request's GET method).
   - Present: normalize as `str(method).strip().upper()`.
     - `== "GET"` → allow.
     - anything else (incl. non-string, empty, `POST`/`PUT`/`DELETE`) → **reject**: raise
       `fastmcp.exceptions.ToolError("Read-only server: only GET is permitted on charger proxy "
       "tools (got method=<original value>)")`. No NOC call is made.

Case-insensitive and whitespace-tolerant. Rejection raises before `call_next`, so the request
never reaches `NocAuthClient` / the NOC backend.

### Data flow

```
LibreChat/Agent → MCP tool call (proxy, method=POST)
      → ReadOnlyProxyMiddleware.on_call_tool
            method != GET → ToolError (returned to caller)   ✗ NOC never contacted
            method == GET / absent → call_next → NocAuthClient → NOC → charger (read)  ✓
```

## Testing (`mcp-server/tests/test_readonly.py`)

Follows the existing `httpx.MockTransport` style in `tests/test_auth.py`; uses
`pytest`/`pytest-asyncio` already configured in `pytest.ini`.

- **Unit (decision helper):**
  - proxy tool + `method` ∈ {`POST`,`PUT`,`DELETE`,`post`,`Delete`,``, non-string} → blocked.
  - proxy tool + `method` ∈ {`GET`,`get`,` GET `} or absent → allowed.
  - non-proxy tool + any `method` → allowed.
- **Integration (through the built server):**
  - Build the server with a mock NOC transport (as in `test_auth.py`), call a proxy tool via
    FastMCP's in-memory `Client`, assert:
    - `method="POST"` raises/returns an error (tool call fails), and
    - the mock NOC transport recorded **no** forwarded non-GET request.
  - `method="GET"` (or absent) succeeds and reaches the mock NOC.

## Out of scope (noted, not changed)

- The `token` query param on the proxy tools (auth-token override) — not a write risk; separate
  follow-up if we decide to lock it down.
- Any change to LibreChat, the CMS MCP server, or the NOC backend.

## Verification

- `cd mcp-server && python -m pytest` — all tests green, including the new file.
- No tool names/schemas change → the on-charger catalog and any existing agent wiring remain valid.
