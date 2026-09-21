# NOC LLM Router

A small OpenAI-compatible chat-completions proxy that resolves an **agent id**
(LibreChat's own `Agent.id`) to that agent's admin-managed, ordered **Combo**
of `{endpoint, model}` targets, and falls back to the next target only when
the current one fails before producing any response bytes.

It never holds its own copy of a provider credential. Every target it can
route to, and the real credential behind any one of them, is resolved live by
LibreChat's own backend -- the exact `getAppConfig`/`getCustomEndpointConfig`
resolution direct model execution already uses (`librechat.yaml`'s base
`endpoints.custom` list, merged with anything an admin has since added or
changed through LibreChat's own Admin Panel, e.g. a new OpenRouter entry) --
this service adds fallback ordering on top of that, nothing else.

## Why this exists

`librechat.yaml` wires each agent directly to one provider (GLM, Kimi). If
that provider has an outage or quota failure, the agent has no fallback. This
service sits between LibreChat and the providers: an agent's `provider` is
pointed at `"NOC Router"` (see [Integrating with LibreChat](#integrating-with-librechat)),
the router resolves that agent's Combo, tries its targets in priority order,
and the first one that answers (or starts streaming) wins. No mid-stream
fallback -- once bytes have gone to the caller, that target is committed.

## Core concepts

- **Catalog** (`llm_router/catalog.py`) -- a read-only view of every
  `endpoints.custom` entry LibreChat itself currently resolves, fetched over
  the internal Docker network from two routes added to the LLM Routing admin
  facade (`apps/ai-platform/librechat/custom-image/files/api/llmRouting.js`):
  `GET .../internal/catalog` (every `{endpoint, model}` pair, masked -- what
  `list_targets()` returns) and `POST .../internal/resolve` (one pair's real
  `{baseURL, apiKey}` -- what `resolve()` returns, used only in-memory to dial
  upstream for a single request). Both calls are authorized by a shared
  secret (`LLM_ROUTER_INTERNAL_SECRET`), never a provider API key. This is
  the *entire* set of targets an admin is allowed to pick from -- exactly
  what LibreChat's own Admin Panel has registered, with zero duplication;
  there is no second credential store, and this service parses no yaml.
- **Combo** (`llm_router/store.py`) -- one agent's ordered, admin-managed list
  of `ComboTarget`s (`{endpoint, model, enabled}` -- never a key), persisted
  in this service's own JSON registry, keyed by the LibreChat `Agent.id`
  string. The same `endpoint` (credential) can appear more than once with
  different models (e.g. Kimi credential A -> `kimi-for-coding`, then the
  same Kimi credential A -> `k3`).

## API

### OpenAI-compatible surface

- `GET /v1/models` -- lists agent ids that have an **enabled** Combo, as
  OpenAI `Model` objects (`id` = agent id).
- `POST /v1/chat/completions` -- `model` must be an agent id with an enabled
  Combo. Every other field (`messages`, `tools`, `tool_choice`, `stream`,
  ...) is forwarded to the resolved target's provider unchanged except
  `model`, which is rewritten to that target's own model name.

### Admin surface (`/admin/*`)

No auth of its own -- see [Security model](#security-model).

- `GET /admin/catalog` -- every selectable `{endpoint, model}` pair, masked
  (no keys, ever).
- `GET /admin/combos` -- every agent's Combo.
- `GET /admin/combos/{agent_id}` -- one agent's Combo (404 if none exists).
- `PUT /admin/combos/{agent_id}` -- create/replace a Combo's `targets`
  (priority = array order) and `enabled` flag. Each target must already be in
  the Catalog, or the request is rejected with 400 -- an admin can never
  reference a credential/model that isn't actually registered.
- `PUT /admin/combos/{agent_id}/enabled` -- flip the whole Combo on/off
  without touching its targets.
- `DELETE /admin/combos/{agent_id}` -- remove the Combo entirely.

## Fallback rules

Falls back to the next enabled target when, and only when, the current one
fails **before the first response byte** with:

- HTTP 429
- Any HTTP 5xx
- A request timeout
- A connection error (refused, DNS, TLS, etc.)

Any other status (400, 401, 403, 404, ...) is returned to the caller from
whichever target produced it -- retrying elsewhere would not fix a malformed
request or bad credential. Once streaming has started, a drop ends the
stream; it never triggers a second target.

## Persistence

Combos are stored as JSON on disk (`LLM_ROUTER_DATA_FILE`, default
`/data/llm-router-registry.json`), the same pattern this repo already uses
for admin-managed config (`knowledge/kb-registry.json`) rather than a MongoDB
collection. The file holds only `{endpoint, model, enabled}` references and
agent metadata -- no API key is ever written to it, so it carries no secret
material.

## Security model

This service has no published port in `docker-compose.yml` and no auth of
its own on `/admin/*`, the same as `kb-admin-mcp-server`. The boundary is the
Docker network plus the LibreChat custom-image facade
(`apps/ai-platform/librechat/custom-image/files/api/llmRouting.js`), which
requires `access:admin` **and** `manage:mcpservers` before any browser
request reaches this service's `/admin/*` (proxied through `GET/PUT/DELETE
.../combos*` and `GET .../catalog` on that facade).

This service's own outbound calls -- to `GET/POST
.../api/llm-routing/internal/{catalog,resolve}` on that same facade, to learn
the catalog and to resolve a Combo target's real credential -- carry no
browser session, so they cannot satisfy that gate. Those two routes are
registered outside it and instead check a shared secret
(`LLM_ROUTER_INTERNAL_SECRET`, `X-Internal-Secret` header) known only to this
service and that facade. It authorizes exactly one caller (this service) to
resolve one thing (a registered provider credential) -- it is not, and must
never become, a provider API key itself.

## Integrating with LibreChat

Already wired into the live `librechat.yaml`:

```yaml
endpoints:
  custom:
    - name: "NOC Router"
      apiKey: "not-needed"          # unused: this endpoint's requests are relayed
                                     # through tool-filter-proxy to llm-router, which
                                     # resolves each target's real credential itself
      baseURL: "http://tool-filter-proxy:8108/router/v1"
      models:
        default:
          - "noc-engineer"
        fetch: true                 # LibreChat calls this router's own GET /v1/models
                                     # to learn which agent ids have an enabled Combo
      titleConvo: true
      titleModel: "noc-engineer"
      modelDisplayLabel: "NOC Router"
```

`fetch: true` is required: LibreChat's `validateAgentModel` only accepts an
agent whose `provider` is `"NOC Router"` if that agent's own id is a member
of this endpoint's dynamically-fetched model list, which is exactly what
`GET /v1/models` returns.

An agent is pointed at the router by setting its `provider` to `"NOC Router"`
and its `model` to its own `Agent.id` -- LibreChat's existing custom-endpoint
resolution (`getCustomEndpointConfig`) then routes every request for that
agent here with zero LibreChat code changes. The LLM Routing admin page
(`apps/ai-platform/librechat/custom-image/files/client/LlmRoutingView.tsx`)
does this automatically when a Combo is enabled, and reverts the agent to its
original provider/model when the Combo is disabled or deleted.

`docker-compose.yml`'s `llm-router` service calls LibreChat's own `api`
service (`LLM_ROUTER_LIBRECHAT_API_URL`, default `http://api:3080`) for the
catalog and for every credential resolution, authorized by
`LLM_ROUTER_INTERNAL_SECRET` (from the same `.env` LibreChat itself reads) --
it parses no yaml and is never given a copy of `GLM_API_KEY`/`KIMI_API_KEY`
or any other provider credential.

## Running the tests

```
cd services/llm-router
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt   # .venv/bin on Linux/Mac
PYTHONPATH=. .venv/Scripts/python -m pytest -q
```
