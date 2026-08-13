# RUNBOOK — Run the chat stack locally (Windows)

**Ador Manthan.AI** — the NOC assistant: **LibreChat** (web UI :3080) → **GLM** → read-only
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
   > `MEILI_MASTER_KEY`, `ADMIN_PANEL_SESSION_SECRET`, …). Don't hand-edit those.

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

# 3. Verify all services are Up
docker compose ps

# 4. Confirm the NOC MCP server authenticated (no auth errors)
docker compose logs mcp-server
```

Then open **http://localhost/** and **register an account** (registration is open). nginx
fronts everything on port 80 — for LAN/remote access use `http://<SERVER_HOST>/` instead
(see "Serving other machines" below).
The footer shows the running build, e.g. **Ador Manthan.AI v0.1.0 (377a150)**, and the
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

## LLM providers — GLM and Kimi

Two OpenAI-compatible endpoints are configured in `librechat.yaml`. Pick one per agent:

| Endpoint | Base URL | Default model | Key in `.env` |
|---|---|---|---|
| **GLM** (Zhipu / z.ai) | `https://api.z.ai/api/coding/paas/v4` | `glm-5.2` | `GLM_API_KEY` |
| **Kimi** (Moonshot AI) | `https://api.kimi.com/coding/v1` | `kimi-for-coding` | `KIMI_API_KEY` |

⚠ **Both providers ship two incompatible endpoints and it is easy to pick the wrong one.**
An `sk-kimi-*` key is a **Kimi For Coding subscription** key and is bound to
`api.kimi.com/coding/v1`. It returns `401 Invalid Authentication` against
`api.moonshot.ai/v1` (pay-as-you-go) and `api.moonshot.cn/v1` (mainland) — those are separate
accounts with their own keys. A 401 here means *wrong endpoint or wrong key*, never no balance.

Add the Kimi key to `chat/.env` (no `docker-compose.yml` change needed — the `api` service
already loads the whole file via `env_file`):

```
KIMI_API_KEY=your_kimi_api_key_here
```

Then **recreate** — a plain `restart` will NOT pick up a new `.env` value:

```powershell
docker compose up -d --force-recreate api
```

Verify the key reaches the provider before blaming the stack:

```powershell
docker exec noc-librechat node -e "fetch('https://api.kimi.com/coding/v1/chat/completions',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+process.env.KIMI_API_KEY},body:JSON.stringify({model:'kimi-for-coding',messages:[{role:'user',content:'hi'}],max_tokens:200})}).then(r=>r.text().then(t=>console.log(r.status,t.slice(0,200))))"
```

`200` = good. `401` = wrong endpoint or bad key. `429` = plan quota/rate limit. Note LibreChat
mislabels **every** 429 with a LangChain `MODEL_RATE_LIMIT` link, including pure billing errors.

To list the models a key can actually reach:

```powershell
docker exec noc-librechat node -e "fetch('https://api.kimi.com/coding/v1/models',{headers:{Authorization:'Bearer '+process.env.KIMI_API_KEY}}).then(r=>r.json()).then(j=>j.data.forEach(m=>console.log(m.id,m.context_length,m.display_name)))"
```

**Model notes**
- Use only the **canonical ids** from `/models`. `kimi-k2.6`, `kimi-k3`, `kimi-k2.5` and
  `kimi-k2.7-code` are accepted and echoed back but do not appear in `/models` — undocumented
  aliases that can disappear without notice.
- `kimi-for-coding` (K2.7 Coding, 262k) is the default: the best latency/capability balance for
  multi-hop tool chains. Measured 2026-08-06 on a single-tool prompt — `-highspeed` 1.0s,
  `kimi-for-coding` 2.8s, `k3` 7.1s, `k3-256k` 24.5s. Per-turn latency compounds across a
  multi-tool answer, so the fast models matter more than the big ones here.
- `k3` is the only 1M-context option. Our 196 enabled tools are **~43k tokens of schema on every
  request**, which still leaves ample room inside 262k — so reach for `k3` for hard reasoning,
  not for context.
- These are **thinking models** (`supports_reasoning: true`, `supports_thinking_type: "only"`).
  They always spend completion budget on `reasoning_content` before emitting `content`.
- ⚠ **`kimi-for-coding-highspeed` is not a safe default.** Verified end-to-end on 2026-08-07:
  on a *hard/ambiguous* question ("synopsis of all chargers offline on manthan today") it
  returned a **single `think` block with no tool call and no answer** — twice, with
  `error: false` and `unfinished: false`, so the UI shows an empty reply and nothing is logged.
  It answers simple questions ("how many chargers online vs offline") correctly. Direct API
  calls always return `finish_reason: tool_calls` even with all 207 tool schemas attached, so
  this only reproduces through the LibreChat agent loop. **`kimi-for-coding` handled both
  questions correctly** (2 tool calls, real answer) and is the default. `k3` also works and
  went deepest — 8 tool calls across NOC and CMS — but is the slowest.
- Debugging an empty reply: the answer lives in the message's **`content` parts array**, not the
  top-level `text` field, which is `""` for agent messages. A healthy run looks like
  `think, tool_call, think, text`; a failed one is `think` alone.
  ```powershell
  docker exec noc-librechat-mongo mongosh LibreChat --quiet --eval "const m=db.messages.findOne({conversationId:'<id>',isCreatedByUser:false}); print((m.content||[]).map(c=>c.type).join(', '))"
  ```
- On GLM stay on `glm-5.2`. `glm-4.7` buries its answer in `reasoning_content`, so the reply
  renders as an empty message under a collapsed "Thought" block.

## Agents — capture & restore (config as code)

Agents live in **MongoDB**, so `docker compose down -v` wipes them (the Artifacts toggle,
context-window size, curated tool list, and system prompt all disappear). Capture them to git
and restore after a fresh start with the scripts in **`chat/agents/`**:

| When | Command (from `chat/agents/`) |
|---|---|
| After changing an agent in the UI — capture it to git | `./export-agents.ps1`  (bash: `./export-agents.sh`) |
| After a fresh `up`, once the admin user is registered — recreate agents | `./restore-agents.ps1`  (bash: `./restore-agents.sh`) |

- **What's captured** (`agents.json`): the full agent — model, **Artifacts** setting,
  **maxContextTokens**, the tool list (including docgen), and instructions. The owner is stored
  as an **email**, not a raw ObjectId, so it re-links correctly on a fresh database.
- **Permissions are captured too**, as `__acl`. LibreChat v0.8.x gates agent visibility on the
  **`aclentries`** collection keyed by the agent's Mongo `_id` — *not* on the agent document.
  An agent with no acl entries is **invisible to everyone, including its own author**, and the
  failure is silent: the agent exists, is correctly configured, and simply never appears in the
  picker. Principals are stored portably (users as emails, roles as role names, access role as
  `agent_owner` / `agent_viewer`). An entry with no `__acl` gets owner grants synthesised on
  restore. This is why a role grant matters: `NOC Engineer` is usable by NOC Tester only
  because it carries `agent / role:noc-engineer permBits=1`.
- **Owner resolution on restore:** matches the emailed owner, falling back to the first `ADMIN`
  user — so **register the admin first**, then restore.
- **Idempotent:** re-running `restore` updates in place. Commit `agents.json` whenever it changes.
- `agents.json` captures **every** agent in the DB — delete any test/junk agents in the UI and
  re-export to keep the committed config clean.

## Admin Panel — roles & access control

The **Admin Panel** (LibreChat's admin GUI) manages custom **roles**, **groups**, and
permissions. It starts with the stack as the `admin-panel` service, reachable at
**http://localhost:3091**.

- **Who's admin:** the **first-registered** LibreChat user is the admin (or set `role: ADMIN`
  on a user in Mongo). Log into the panel with that account.
- **Secret:** the panel needs `ADMIN_PANEL_SESSION_SECRET` in `.env` (generated by
  `init-env.ps1`; on an older `.env`, add a 64-hex-char value for it).
- **Requires** LibreChat **v0.8.5+** for the admin APIs (we run v0.8.7).
- **⚠ Security:** the panel is a powerful admin surface. `:3091` is fine for local dev, but in
  production it MUST sit behind the reverse proxy with auth + an IP allowlist — never raw.

## Serving other machines (LAN access)

LibreChat and generated-file downloads are served through nginx (`chat/nginx.conf`) on port
**80** — those two are no longer published on their own ports (`3080`/`8103`). The admin
panel keeps its own port, **3091** (see "Admin Panel" above — it's a prebuilt SPA that isn't
confirmed to support being served from a subpath, so it's not routed through nginx). Only
one setting needs the machine's real address instead of `localhost`. Find it with `ipconfig`
(the `Ethernet` adapter).

Set in `chat/.env` — substitute your own IP or domain for `172.16.12.188`:

```
# Change this ONE line if the IP/domain changes. Every browser-facing URL (download links,
# share links, the admin panel) is derived from it in chat/docker-compose.yml.
SERVER_HOST=172.16.12.188

# Required when serving over plain HTTP to anything other than localhost — see below.
SESSION_COOKIE_SECURE=false
```

`DOCGEN_PUBLIC_BASE_URL`, `DOMAIN_CLIENT`, `DOMAIN_SERVER`, and `VITE_API_BASE_URL` are **no
longer set by hand** — `chat/docker-compose.yml` derives all four from `SERVER_HOST`
automatically.

Then **recreate** (a `restart` will not reload `.env`):

```powershell
cd E:\cjain\noc-mcp-agent\chat
docker compose up -d --force-recreate
```

Browse to `http://<SERVER_HOST>/` (LibreChat), `http://<SERVER_HOST>:3091` (admin panel).

### Why each setting matters

- **`SERVER_HOST`** — the single source of truth for every browser-facing URL. Download links
  (`docgen-mcp-server/docgen_mcp/config.py:21`) and share links are resolved by the *user's*
  browser, so pointing them at `localhost` sends users to their own machine, where nothing is
  listening — the download/share fails. `docker-compose.yml` substitutes `${SERVER_HOST}` into
  `DOCGEN_PUBLIC_BASE_URL`, `DOMAIN_CLIENT`, `DOMAIN_SERVER`, and `VITE_API_BASE_URL` directly,
  so changing this one value updates all four consistently.
- **`SESSION_COOKIE_SECURE=false`** — **without this, LAN users are logged out on every page
  reload.** LibreChat runs with `NODE_ENV=production` (injected by its own npm script, so it
  is invisible in `.env`), which makes `shouldUseSecureCookie()` flag the auth cookies
  `Secure`. Browsers refuse to store a `Secure` cookie delivered over plain HTTP — except on
  `localhost`, which they treat as a trustworthy origin. So the refresh-token cookie is
  silently dropped for LAN users and the session cannot survive a reload. This still applies
  with nginx in front, since nginx here proxies plain HTTP — it does not terminate TLS.

  > ⚠ **Security tradeoff.** This sends a 7-day refresh token in cleartext over the network,
  > sniffable by anyone on that LAN. Acceptable on a trusted internal VLAN for dev/UAT; **not
  > acceptable in production.** The correct fix is to add TLS termination to the nginx config
  > (`chat/nginx.conf`), after which this override should be removed (P0-1).

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
- **Port 80 already in use** → stop the other process or change the nginx `"80:80"` mapping in `docker-compose.yml`.
