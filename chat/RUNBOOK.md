# RUNBOOK — Run the chat stack locally (Windows)

**Adore Manthan.AI** — the NOC assistant: **LibreChat** (web UI :3080) → **GLM** → read-only
**NOC MCP server** (:8100) and legacy **CMS MCP server** (:8101), all via Docker Compose.

```
LibreChat (:3080)  →  GLM (z.ai)  →  mcp-server (:8100)   →  NOC backend
                                  →  cms-mcp-server (:8101) →  legacy CMS
```

## Prerequisites (one-time)

1. **Docker Desktop** installed and able to start
   (default path: `C:\Program Files\Docker\Docker\Docker Desktop.exe`).
2. **`chat/.env`** filled in. If it doesn't exist yet, generate it:
   ```powershell
   cd E:\cjain\noc-mcp-agent\chat
   powershell -ExecutionPolicy Bypass -File .\init-env.ps1
   ```
   Then edit `.env` and set:
   - `GLM_API_KEY` — z.ai GLM key (**account must have balance**, else GLM returns `429 Insufficient balance`)
   - `NOC_EMAIL` / `NOC_PASSWORD` — a **read-only** NOC user
   - `CMS_USER` / `CMS_PASS` — legacy CMS creds (optional; only for the CMS tools)

   > `init-env.ps1` auto-generates the LibreChat secrets (`CREDS_KEY`, `JWT_SECRET`,
   > `MEILI_MASTER_KEY`, …). Don't hand-edit those.

## Run

```powershell
# 1. Start the Docker daemon (skip if Docker Desktop is already running)
& "C:\Program Files\Docker\Docker\Docker Desktop.exe"
#    wait ~30-60s, then confirm it's up:
docker version --format '{{.Server.Version}}'

# 2. From the chat folder, start via the wrapper (RECOMMENDED). It injects the
#    version + commit id into the app footer so you can see exactly what's running.
cd E:\cjain\noc-mcp-agent\chat
.\up.ps1                           # first run builds the two MCP images (a few min)
#    (bash/macOS/Linux: ./up.sh   |   plain `docker compose up -d --build` also works,
#     but the footer then shows "vdev (local)" instead of the real version/commit)

# 3. Verify all 5 services are Up
docker compose ps

# 4. Confirm the NOC MCP server authenticated (no auth errors)
docker compose logs mcp-server
```

Then open **http://localhost:3080** and **register an account** (registration is open).
The footer shows the running build, e.g. **Adore Manthan.AI v0.1.0 (377a150)**, and the
new-chat screen greets you with the version.

## Wire up the tools (one-time, in the LibreChat UI)

MCP tools only work through a LibreChat **Agent**, not the bare GLM endpoint:

1. Open **Agent Builder** → create an agent.
2. Pick the **GLM** endpoint / **`glm-5.2`** model.
3. Under **Tools / MCP**, enable the **`noc`** server's tools.
4. Give it a system prompt starting with **"Always respond in English."**
   (GLM defaults to Chinese otherwise) plus the NOC assistant instructions from `README.md`.
5. Save, then chat with that agent.

**Test prompt:** "How many chargers are online vs offline?" → should call `dashboard_summary`.

## Everyday commands (run from `chat/`)

| Action | Command |
|---|---|
| Status | `docker compose ps` |
| Follow NOC MCP logs (live tool calls) | `docker compose logs -f mcp-server` |
| Restart just LibreChat | `docker compose restart api` |
| Stop (keeps data) | `docker compose down` |
| Stop + wipe chats/search data | `docker compose down -v` |
| Rebuild after MCP code changes | `docker compose up -d --build mcp-server` |

## Versioning & releases

The version shown in the app footer comes from the repo's **`VERSION`** file plus the
current **git commit** (short SHA), injected at start by `up.ps1` / `up.sh`. Git tags
`vX.Y.Z` are the source of truth for the **`CHANGELOG.md`** (generated from Conventional
Commits by git-cliff; CI regenerates it on merge to `main`).

To cut a new version (from the repo root):

```powershell
# bump the semver, tag it, and regenerate the changelog section
.\scripts\bump-version.ps1 patch     # 0.1.0 -> 0.1.1   (or: minor | major | 1.2.3)
#   (bash: ./scripts/bump-version.sh patch)

# review, then publish the commit + tag
git push
git push origin v0.1.1

# redeploy so the footer shows the new version
cd chat; .\up.ps1
```

The bump script updates `VERSION` + the LibreChat welcome message, regenerates
`CHANGELOG.md` (each entry links to its commit id), commits as `chore(release): vX.Y.Z`,
and tags it. It does **not** push — you review first.

## Troubleshooting

- **`cannot connect to the docker API`** → Docker Desktop isn't running. Start it, re-check `docker version`.
- **GLM `429 Insufficient balance`** → recharge the GLM account; not a stack problem.
- **NOC auth errors in `mcp-server` logs** → fix `NOC_EMAIL`/`NOC_PASSWORD` in `.env`, then `docker compose up -d mcp-server`.
- **Port 3080 already in use** → stop the other process or change the `3080:3080` mapping in `docker-compose.yml`.
