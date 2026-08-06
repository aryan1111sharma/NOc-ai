# Add Kimi (Moonshot AI) as a second LLM endpoint

**Date:** 2026-08-06
**Status:** Approved
**Scope:** `chat/librechat.yaml`, `chat/agents/agents.json`, `chat/RUNBOOK.md`, `chat/.env` (user-applied)

## Problem

The GLM (z.ai) account backing `GLM_API_KEY` has no balance. Every agent turn fails with
`429 / code 1113 "Insufficient balance or no resource package"`. Verified as upstream and
account-level, not a stack fault: a direct call from inside the `noc-librechat` container
returns 1113 on **both** `https://api.z.ai/api/coding/paas/v4` and `https://api.z.ai/api/paas/v4`.
A revoked key would return 401, so authentication succeeds and only quota is exhausted.

LibreChat surfaces this as a LangChain `MODEL_RATE_LIMIT` link, which is misleading — it maps
every HTTP 429 to that doc. This is billing, not throttling; waiting does not clear it.

## Decision

Add **Kimi (Moonshot AI)** as a *second* custom endpoint alongside GLM rather than replacing it.
GLM stays configured and resumes working the moment it is recharged, and the two can be
A/B'd on identical NOC questions.

Default model: **`kimi-for-coding`** (K2.7 Coding).

## Correction applied during implementation

The design was first written against the **pay-as-you-go** platform (`api.moonshot.ai/v1`) and
chose `kimi-k2.6` on a per-token cost argument. On applying it, the supplied key turned out to
be an **`sk-kimi-*` "Kimi For Coding" subscription key**, which returns
`401 Invalid Authentication` on both `api.moonshot.ai/v1` and `api.moonshot.cn/v1`. It binds to
**`https://api.kimi.com/coding/v1`** — the same coding-plan-vs-general-endpoint split already
documented for GLM.

Two consequences:

1. **The per-token cost table below does not apply to this key.** A subscription is flat-rate
   with rate limits, so cost no longer discriminates between models. Latency and capability do.
2. **`kimi-k2.6` is not a real model id on this endpoint.** `GET /coding/v1/models` returns only
   `kimi-for-coding`, `kimi-for-coding-highspeed`, `k3`, `k3-256k`. The `kimi-k2.6` / `kimi-k3` /
   `kimi-k2.5` / `kimi-k2.7-code` ids are accepted and echoed back but absent from `/models`,
   i.e. undocumented aliases. The config uses canonical ids only.

Measured 2026-08-06, single-tool prompt, all returning `finish_reason: "tool_calls"` correctly:

| Model | Context | Round-trip |
|---|---:|---:|
| `kimi-for-coding-highspeed` | 262,144 | 967 ms |
| `kimi-for-coding` (K2.7) | 262,144 | 2,795 ms |
| `k3` | 1,048,576 | 7,099 ms |
| `k3-256k` | 262,144 | 24,530 ms |

`kimi-for-coding` is the default: per-turn latency compounds across a multi-hop tool answer, and
the ~43k-token tool block fits 262k with ample room, so `k3`'s 1M window buys nothing. All four
are `supports_reasoning: true` thinking models — they spend completion budget on
`reasoning_content` before `content`, so a tight max-output cap yields an empty reply.

## Original rationale (pay-as-you-go; retained for the tool-bloat measurements)

The dominant cost driver is not the model — it is tool-schema bloat. Measured against the
live MCP servers on 2026-08-06:

| MCP server | Tools | Schema bytes | ~Tokens |
|---|---:|---:|---:|
| noc | 169 | 153,793 | 38,448 |
| cms | 17 | 10,743 | 2,686 |
| chargezone | 8 | 5,314 | 1,329 |
| docgen / ecog_sd / jira / kb | 13 | 9,951 | 2,489 |
| **Total** | **207** | **179,801** | **~44,950** |

The `NOC Engineer` agent enables **196** of those tools, so roughly **43k tokens of tool
schemas ride on every request** before system prompt or history.

| | kimi-k2.6 | kimi-k3 |
|---|---|---|
| Context | 262,144 | 1,048,576 |
| Input (cache miss) | $0.95/M | $3.00/M |
| Input (cache hit) | $0.16/M | $0.30/M |
| Output | $4.00/M | $15.00/M |
| Dynamic tool loading | not supported | supported |

At ~43k tokens/request that is **$0.041 vs $0.129 cold**, **$0.007 vs $0.013 warm**. The 43k
block fits k2.6's 262k window with room for long sessions, so k3's 1M buys nothing here.

Context caching is **automatic** on both models, covers tool definitions, and needs no client
change — it only requires a stable request prefix and a prior request over 256 prompt tokens.

k3's dynamic tool loading is the one genuine k3 advantage, but it is a **client-side** pattern
(injecting `tools` into `messages` mid-conversation) that LibreChat does not implement. Paying
flagship rates for an unusable feature is not justified.

`moonshot-v1-*` is deliberately excluded: the series is sunset on **2026-08-31**.

## Changes

1. **`chat/.env`** (user-applied; the file is blocked by the secrets deny rules)
   ```
   KIMI_API_KEY=<key>
   ```
   No `docker-compose.yml` change is needed — the `api` service already declares
   `env_file: - .env`, so any variable there reaches the container.

2. **`chat/librechat.yaml`** — new `endpoints.custom` entry after the GLM block:
   `baseURL: https://api.kimi.com/coding/v1`, models `kimi-for-coding` (default),
   `kimi-for-coding-highspeed`, `k3`, `k3-256k`; `titleModel: kimi-for-coding-highspeed`
   (titles are throwaway, use the fastest). GLM's block is untouched.

3. **`chat/agents/agents.json`** — new `NOC Engineer (Kimi)` agent cloned from `NOC Engineer`:
   identical instructions, identical 196-tool list, `maxContextTokens: 120000` held at parity
   so provider is the only changed variable; `provider: Kimi`, `model: kimi-k2.6`. Applied via
   the existing idempotent `restore-agents.ps1`. Existing GLM agents are not modified.

   The clone's `mcpServerNames` **adds `chargezone`**, which is absent from the original
   `NOC Engineer` despite its 8 `cz_jira_*_mcp_chargezone` tools being enabled (see Known issues).

4. **`chat/RUNBOOK.md`** — a "Switching LLM provider" section covering the key, the
   `--force-recreate` requirement, and the verification probe.

## Verification

1. `docker compose up -d --force-recreate api` — recreate, not restart: `.env` changes are
   only re-read on container recreation.
2. Direct probe from inside `noc-librechat` to `api.moonshot.ai/v1/chat/completions` —
   expect HTTP 200, then a second call carrying a real tool schema to confirm a `tool_calls`
   round-trip with `finish_reason: "tool_calls"`.
3. `docker compose logs api | Select-String 'Initialized with'` — still 7 servers / 207 tools.
4. `restore-agents.ps1`, then drive two real NOC questions end-to-end:
   - synopsis of chargers offline today
   - ChargeZone open P1/P2/P3 ticket counts (expected P1=44 / P2=52 / P3=9 as of the last
     user-confirmed count; P1/P2/P3 come from the custom field `Priority Level` `cf[10120]`,
     **not** the built-in Priority field)

## Known issues (documented, not fixed here)

- **`NOC Engineer.mcpServerNames` omits `chargezone`** while enabling its 8 tools. Pre-existing;
  the Kimi clone corrects it, the GLM original is left alone pending a decision.
- **196 tools ≈ 43k tokens/request** is the real cost and latency lever, and the likely cause of
  the July 429 rate-limit incidents. Curating to ~30-40 tools would help more than any model
  choice. Separate piece of work.
- **`chat/up.ps1` does not parse under Windows PowerShell 5.1** — a UTF-8 em dash with no BOM,
  and `pwsh` 7 is not installed. Documented inline equivalent is used instead.
- **`chat/librechat.yaml` contains literal `???`** in four comments from an old encoding
  round-trip (the file has zero non-ASCII bytes). Cosmetic, comments only.

## Out of scope

Data residency. Moonshot is, like z.ai, a Chinese provider; `api.moonshot.ai` is its
international endpoint and `api.moonshot.cn` a separate mainland platform with its own account.
This change does **not** resolve the open D1 residency decision — it is a lateral move.
