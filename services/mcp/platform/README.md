# platform-mcp-server — Platform Intelligence (read-only)

The beginning of the **Platform Intelligence** layer: a small FastMCP server whose job is to
help the platform understand *itself* — repository structure, git state, declared
dependencies, basic health — rather than any external backend the way the other MCP servers
in this repo do (NOC, CMS, Jira, etc.).

Everything here is **read-only**. No tool writes to the repository, runs arbitrary shell
commands, or returns raw file contents.

## Tools (v1)

| Tool | Reports |
|---|---|
| `platform_health` | Liveness + whether the repo mount is actually present (check this first) |
| `platform_project` | Declared `VERSION`, top-level layout, list of MCP servers under `services/mcp/` |
| `platform_python` | This server's own Python runtime + each MCP service's declared `requirements.txt` |
| `platform_git` | Current branch, latest commit (SHA/author/date/subject), working-tree cleanliness |
| `platform_filesystem` | Shallow directory listing (name/type/size) — metadata only, never file contents |
| `platform_api` | This server's own name/transport/tool list (not cross-server discovery — see below) |

## Why it needs the repository mounted

Unlike the other MCP servers, this one's "backend" *is* the repository. In Docker, the
service in `apps/ai-platform/librechat/docker-compose.yml` bind-mounts the repo root
read-only at `/workspace` (`REPO_ROOT`). Without that mount, `platform_health` reports
`repo_root_mounted: false` and every other tool degrades to an explicit error rather than
guessing or silently returning nothing.

## Security boundary: metadata only, never contents

`platform_filesystem` deliberately returns names, types, and sizes — never file contents —
and always excludes dotfiles/dotdirs (`.env`, `.git`, `.venv`, etc.), by construction, not
convention. A platform-introspection tool able to read arbitrary file contents from a
mounted repo root would be a secret-disclosure primitive, since `.env` files live right next
to the code this mounts. `platform_git` only ever runs read-only `git` subcommands
(`rev-parse`, `log`, `status --porcelain`) — never anything that could mutate the repo.

## Extensibility contract

Every capability is one module under `platform_mcp/tools/`, exposing a single function:

```python
def register(mcp, settings) -> None:
    @mcp.tool
    def platform_whatever() -> dict:
        """..."""
        ...
```

`server.py`'s `_TOOL_MODULES` tuple lists which modules are active. **Adding a new
capability is: write one new module, add one line to that tuple** — no existing tool or
module needs to change. This is what "additional capabilities can be added without
restructuring the server" means concretely.

## Not built yet (deliberately out of scope for v1)

These are real future directions for this server, listed so the next contributor doesn't
duplicate the thinking — **none of them exist yet**:

- Docker (container/image inspection)
- CI/CD (workflow run status)
- Logs
- Metrics / Monitoring
- Security (scanning, secret-detection)
- Dependencies (installed vs. declared drift, vulnerability data)
- Database health
- Network diagnostics
- Performance analysis
- Configuration validation
- **MCP Discovery** (cross-server tool enumeration — `platform_api` today is deliberately
  scoped to *this* server only so it doesn't collide with this later)
- Architecture validation
- Documentation validation
- Infrastructure intelligence

Each of these should become its own module under `tools/`, following the same
`register(mcp, settings)` contract, when actually needed.

## Running locally

```bash
cd services/mcp/platform
pip install -r requirements.txt
REPO_ROOT=../../.. python -m platform_mcp   # stdio; point REPO_ROOT at a real checkout
```

## Config (env vars)

| Var | Default | Meaning |
|---|---|---|
| `MCP_TRANSPORT` | `stdio` | `stdio` for local MCP clients, `http` for LibreChat |
| `MCP_HOST` / `MCP_PORT` | `127.0.0.1` / `8107` | Bind address for `http` transport |
| `REPO_ROOT` | `/workspace` | Where the repository is mounted (see docker-compose) |
