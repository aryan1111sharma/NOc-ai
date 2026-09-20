# tool-filter-proxy — OpenAI-compatible tool-filtering proxy

Sits between LibreChat and the model providers, shrinking the `tools[]` array on every
chat-completion request and streaming the provider response straight back:

```
LibreChat ──▶ tool-filter-proxy ──▶ Kimi / GLM
              (one extra hop, ~1.2 ms)
```

Measured on the live `NOC Engineer (Kimi)` agent: **192 tools / 142.9 KB → 20 tools /
15.0 KB (90% smaller) in 1.16 ms**. Provider latency is 2.8–7.1 s, so the added cost is
~0.04% of a request.

This is a **context/performance optimization, never an authorization mechanism.** It can
only remove entries from the array LibreChat already sent, so it cannot introduce a tool
an agent did not have. MCP servers remain the enforcement point.

## Why a proxy

LibreChat runs as a prebuilt image (`ghcr.io/danny-avila/librechat:latest`) with no
source in this repo, and it does not implement dynamic tool loading. The outbound
provider request is the only place where `{messages, tools, model}` exist together,
request-scoped — and it is reachable by changing one config value per provider. No fork,
no custom LibreChat image, no MCP change.

Filtering at the MCP layer is not an option: `tools/list` carries no user message, so
request-aware ranking cannot exist there.

## Layering

| Component | Knows about |
|---|---|
| `filtering/tool_filtering/` (untouched) | nothing platform-specific — pure `rank()` |
| `registry/` (read-only) | tool metadata; this service never writes to it |
| `tool_filter_proxy/librechat_names.py` | LibreChat's tool-name transformation |
| `tool_filter_proxy/adapter.py` | the OpenAI wire format, pinning, the safety guard |
| `tool_filter_proxy/app.py` | HTTP routing, streaming, header/error relay |

Replacing LibreChat with another OpenAI-compatible front end means changing
`librechat_names.py` and `adapter.py` — nothing else.

## LibreChat's tool-name transformation

```
<base cut at the first "__">[:64 - len("_mcp_<server>")] + "_mcp_<server>"
```

Two distinct effects. The cut at `__` drops the bundled NOC OpenAPI spec's
path-parameter markers — `get_charger_by_id_api_v1_chargers__charger_id__get` becomes
`get_charger_by_id_api_v1_chargers`, a 49-char name, so this is **not** a length effect;
a length-only rule silently mismatches 63 of 192 NOC tools. The result is then truncated
to 64 characters, the OpenAI function-name limit. Server keys use librechat.yaml's
`mcpServers` keys (`ecog_sd`), while the registry uses `ecog-sd`.

Verified: 100% of tools on all four exported agents resolve to a registry record, zero
collisions (`tests/test_names.py`).

## Safety guard

```
0 < len(filtered) <= len(original)      else forward the ORIGINAL body unchanged
```

This is load-bearing, not defensive decoration. Handing the filtering core a raw OpenAI
tool array makes it skip every entry (the name is nested under `function`) and return
**zero** tools — which would send `tools: []` and silently disable every tool while
appearing healthy. The guard converts that into a transparent passthrough.

**Pinned tools always survive filtering:** any function named in a prior
`assistant.tool_calls` or in an explicit `tool_choice`. Dropping one can make a provider
reject the request or strand a multi-hop tool chain, so if a conversation legitimately
references more than `MAX_K` tools the cap lifts to fit them — correctness beats the size
target.

## Transparency

- **Streaming** relayed via `aiter_raw()` — no buffering, no re-chunking, no re-encoding.
- **Status and body** relayed untouched, so provider errors surface exactly as sent. The
  proxy never retries; retry policy stays with LibreChat.
- **`Authorization` forwarded verbatim** — the proxy holds no provider credential.
- **`content-length` and hop-by-hop headers dropped** — the body changes length when
  tools are removed, so a stale length would corrupt the request.
- **One shared `AsyncClient`** keeps provider connections warm, so the extra hop does not
  cost a TLS handshake per request.

## Failure behavior

| Failure | Result |
|---|---|
| Filtering raises | Original request forwarded |
| All tool entries unusable | Original request forwarded (never `tools: []`) |
| Registry missing/malformed | Starts anyway; filters with keyword+entity signals only |
| Body is not JSON | Forwarded verbatim |
| No `tools` (e.g. title generation) | Forwarded verbatim |
| Unknown path prefix | 404 without contacting any upstream |
| Provider timeout | 504 |
| Provider unreachable | 502 |
| Client disconnects mid-stream | Upstream connection closed in a `finally` |

## Config (env, `PROXY_` prefix)

| Var | Default | Meaning |
|---|---|---|
| `PROXY_HOST` / `PROXY_PORT` | `0.0.0.0` / `8108` | Bind address |
| `PROXY_UPSTREAMS` | GLM + Kimi | JSON: `{"<prefix>": "<base URL>"}` |
| `PROXY_FILTER_TOP_K` | `20` | Passed to `rank()`; `MAX_K` still caps it |
| `PROXY_PASSTHROUGH_ONLY` | `false` | Kill switch — forward everything unfiltered |
| `PROXY_CONNECT_TIMEOUT` / `PROXY_READ_TIMEOUT` | `10` / `600` | Upstream timeouts |
| `PROXY_LOG_REQUEST_DETAILS` | `false` | Never enable in production |

Logs carry counts, timings, and status only — never prompt content, tool descriptions, or
credentials.

## Rollback

1. **Softest, no config edit:** set `PROXY_PASSTHROUGH_ONLY=true` and restart this
   service. Requests are forwarded verbatim.
2. **Full removal:** restore the two direct provider `baseURL` values in
   `librechat.yaml` (both recorded inline as comments there and in `PROXY_UPSTREAMS`)
   and `docker compose up -d api`.

## Run / test locally

```bash
cd services/tool-filter-proxy
pip install -r requirements.txt
PYTHONPATH=.:../../filtering:../../registry python -m tool_filter_proxy   # :8108
python -m pytest                                                          # 68 tests
```

`GET /healthz` reports upstreams, indexed registry tool count, and whether filtering is
enabled.

## Limitations

- **`agent_id` is not in the provider payload** — only `model`. Per-agent filtering
  policy would need a LibreChat-side change; ranking currently uses the request text and
  the tools present.
- **Varying `tools` reduces provider prompt-cache hit rate.** Still a net win: a filtered
  cache miss (~$0.0048) beats an unfiltered cache hit (~$0.007) at measured pricing.
- **The proxy is in the critical path.** If it is down, chat is down. Mitigated by
  `restart: unless-stopped` and a seconds-long rollback.
- **The name-transformation rule is empirical**, derived from the agent export. A future
  LibreChat release could change it; `tests/test_names.py` fails loudly if the export and
  the rule ever disagree.
