/**
 * LLM Routing admin API.
 *
 * A thin, authenticated HTTP facade over the EXISTING llm-router service, plus the
 * minimal Agent CRUD needed to point an agent at it. It owns no credential state of
 * its own: the set of selectable (credential, model) targets, and the real credential
 * behind any one of them, come from LibreChat's OWN existing provider registration --
 * getAppConfig/getCustomEndpointConfig, the EXACT functions LibreChat's own backend
 * already calls to resolve a custom endpoint (librechat.yaml's base endpoints.custom,
 * merged with whatever an admin has since added or changed through LibreChat's own
 * Admin Panel, e.g. a new OpenRouter entry) for direct model execution. This file
 * re-implements none of that resolution and keeps no copy of a credential at rest --
 * same design as knowledgeBases.js, extended only by the two /internal/* routes below.
 *
 *   GET    /api/llm-routing/agents                 -> LibreChat's own Agent collection (getAgents)
 *   GET    /api/llm-routing/catalog                -> GET    llm-router /admin/catalog
 *   GET    /api/llm-routing/combos                 -> GET    llm-router /admin/combos
 *   GET    /api/llm-routing/combos/:agentId        -> GET    llm-router /admin/combos/:agentId
 *   PUT    /api/llm-routing/combos/:agentId        -> PUT    llm-router /admin/combos/:agentId (+ Agent sync)
 *   PUT    /api/llm-routing/combos/:agentId/enabled -> PUT   llm-router /admin/combos/:agentId/enabled (+ Agent sync)
 *   DELETE /api/llm-routing/combos/:agentId        -> DELETE llm-router /admin/combos/:agentId (+ Agent revert)
 *
 *   GET    /api/llm-routing/internal/catalog  -> every registered {endpoint, model}, masked
 *   POST   /api/llm-routing/internal/resolve  -> one {endpoint, model} -> {baseURL, apiKey}
 *
 * Authorization uses LibreChat's own capability system, not a new one -- identical
 * to knowledgeBases.js, see that file's comment for the full rationale:
 *
 *   access:admin        - necessary but NOT sufficient on its own.
 *   manage:mcpservers   - held only by ADMIN in this deployment; the bit that
 *                         actually keeps delegated department admins out.
 *
 * llm-router is internal to the Docker network and is never reachable from the
 * browser; it has no published port and no auth of its own (see
 * services/llm-router/README.md "Security model"). No provider API key ever crosses
 * the browser-facing boundary in either direction: every route below the requireJwtAuth
 * gate treats a Combo target as an {endpoint, model} reference only, never a key.
 *
 * The two /internal/* routes are the one deliberate exception, and exist ONLY for
 * llm-router itself to call: it is a separate process with no browser session, so it
 * cannot satisfy requireJwtAuth, and is registered ABOVE that gate for exactly this
 * reason. It is instead gated by a shared secret (LLM_ROUTER_INTERNAL_SECRET, known
 * only to this process and llm-router -- never a provider API key). /internal/resolve
 * hands back a real credential, in-memory, for llm-router to make ONE outbound call
 * with; nothing here writes it anywhere, logs it, or returns it from any other route.
 *
 * Agent <-> Combo wiring, without touching Agent creation/editing or its schema:
 * enabling a Combo (PUT .../combos/:agentId with enabled targets, or PUT .../enabled)
 * points that agent's own `provider`/`model` at "NOC Router"/<agentId> -- the existing,
 * already-in-use mechanism (see LibreChat's getCustomEndpointConfig in
 * server/services/Endpoints/agents/initialize.js) that resolves a custom-endpoint
 * provider by name. Disabling or deleting a Combo restores whatever `provider`/`model`
 * the agent had before its first Combo was enabled, stashed as opaque metadata inside
 * llm-router's own Combo record (original_provider/original_model) -- never on the
 * Agent schema itself.
 */
const express = require('express');
const { logger } = require('@librechat/data-schemas');
const { SystemCapabilities } = require('@librechat/data-schemas');
const { requireCapability } = require('~/server/middleware/roles/capabilities');
const { requireJwtAuth } = require('~/server/middleware');
const { getAgent, getAgents, updateAgent } = require('~/models');
const { getAppConfig } = require('~/server/services/Config');
const { getCustomEndpointConfig, isUserProvided } = require('@librechat/api');
const { extractEnvVariable, EModelEndpoint } = require('librechat-data-provider');

const router = express.Router();

/** Internal service URL. Overridable so a test container can point elsewhere. */
const LLM_ROUTER_URL = process.env.LLM_ROUTER_URL || 'http://llm-router:8110';

/** The custom-endpoint `name` an agent's `provider` is set to when routed through
 *  llm-router. Must match the "NOC Router" entry in librechat.yaml. */
const ROUTER_PROVIDER_NAME = process.env.LLM_ROUTER_PROVIDER_NAME || 'NOC Router';

/** `agentId` becomes a path segment on llm-router's own admin API. LibreChat's own
 *  Agent.id values (e.g. "agent_kimi26NOCEngineerV1") and Mongo ObjectId hex strings
 *  both match this. */
const SAFE_ID = /^[A-Za-z0-9_-]+$/;

function validAgentId(req, res, next) {
  if (!SAFE_ID.test(req.params.agentId || '')) {
    return res.status(400).json({ message: 'Invalid agent id' });
  }
  return next();
}

function handle(fn) {
  return async (req, res) => {
    try {
      return await fn(req, res);
    } catch (error) {
      logger.error('[llmRouting]', error);
      return res.status(502).json({ message: 'LLM router service unavailable' });
    }
  };
}

/**
 * Forward one call to llm-router and relay its status + JSON body verbatim.
 *
 * This file makes no decision about what counts as success or failure -- llm-router
 * already decided (404 for an unknown agent id, 400 for a target not in the catalog)
 * and re-deciding here would risk disagreeing with it.
 */
async function proxyToRouter(method, path, body) {
  const init = { method, headers: { 'Content-Type': 'application/json' } };
  if (body !== undefined) {
    init.body = JSON.stringify(body);
  }
  const upstream = await fetch(`${LLM_ROUTER_URL}${path}`, init);
  const text = await upstream.text();
  return { status: upstream.status, data: text ? JSON.parse(text) : {} };
}

/**
 * Point an agent at the router, WITHOUT touching anything else on the Agent
 * document. `updateAgent`/`getAgent` are LibreChat's own existing Agent CRUD
 * (~/models) -- the smallest existing integration mechanism available, used here only
 * for the two fields this feature needs.
 *
 * Returns the agent's pre-routing provider/model, but ONLY the first time -- once an
 * agent is already routed (provider === "NOC Router"), there is nothing left to
 * capture, so `originalProvider`/`originalModel` come back undefined and the caller
 * must NOT overwrite whatever original is already stashed in the Combo record.
 */
async function pointAgentAtRouter(agentId) {
  const agent = await getAgent({ id: agentId });
  if (!agent) {
    return { ok: false };
  }
  if (agent.provider === ROUTER_PROVIDER_NAME) {
    return { ok: true };
  }
  const originalProvider = agent.provider;
  const originalModel = agent.model;
  await updateAgent({ id: agentId }, { provider: ROUTER_PROVIDER_NAME, model: agentId });
  return { ok: true, originalProvider, originalModel };
}

async function restoreAgentProvider(agentId, originalProvider, originalModel) {
  if (!originalProvider || !originalModel) {
    return;
  }
  const agent = await getAgent({ id: agentId });
  if (!agent || agent.provider !== ROUTER_PROVIDER_NAME) {
    return;
  }
  await updateAgent({ id: agentId }, { provider: originalProvider, model: originalModel });
}

/**
 * Gates the two /internal/* routes below. They exist for llm-router (a separate
 * process, no browser session) to call, so they cannot go through requireJwtAuth --
 * this shared secret is the only thing standing between them and the Docker network
 * boundary. Fails closed: an unconfigured secret refuses every request rather than
 * silently allowing one, so a deployment can never end up with this route effectively
 * unauthenticated because a .env line was forgotten.
 */
const INTERNAL_SECRET = process.env.LLM_ROUTER_INTERNAL_SECRET || '';

function requireInternalSecret(req, res, next) {
  if (!INTERNAL_SECRET || req.headers['x-internal-secret'] !== INTERNAL_SECRET) {
    return res.status(401).json({ message: 'invalid or missing internal secret' });
  }
  return next();
}

/** Every {endpoint, model} pair llm-router may currently route to, masked -- the
 *  live equivalent of the old librechat.yaml-parsing Catalog, now sourced from
 *  getAppConfig() so an admin-added endpoint (e.g. OpenRouter) appears with zero
 *  change on either side. getAppConfig() already includes the LibreChat Admin/
 *  MongoDB `__base__` configuration override merged on top of librechat.yaml --
 *  an agent-level Combo target resolves against that same global, admin-registered
 *  config, not any one browser session's per-principal overrides. */
router.get(
  '/internal/catalog',
  requireInternalSecret,
  handle(async (req, res) => {
    const appConfig = await getAppConfig({});
    const customEndpoints = appConfig?.endpoints?.[EModelEndpoint.custom] ?? [];
    const endpoints = customEndpoints
      .filter((endpoint) => endpoint.name !== ROUTER_PROVIDER_NAME)
      .map((endpoint) => ({
        name: endpoint.name,
        models: { default: endpoint.models?.default ?? [] },
      }));
    return res.status(200).json({ endpoints });
  }),
);

/** Resolves ONE {endpoint, model} pair to its real {baseURL, apiKey}, via the same
 *  getAppConfig()/getCustomEndpointConfig() LibreChat's own direct-model execution
 *  uses (see server/services/Endpoints/agents/initialize.js) -- never a second,
 *  hand-rolled credential lookup. getAppConfig() (no baseOnly) includes the
 *  LibreChat Admin Panel/MongoDB `__base__` configuration override, so this resolves
 *  against the same effective config an admin-added endpoint (e.g. OpenRouter) would
 *  have. The returned apiKey is for llm-router to use for ONE outbound call; this
 *  route neither logs it nor persists it. A "user_provided" (bring-your-own-
 *  key) endpoint has no single resolvable credential for an agent-level Combo target,
 *  so it 404s exactly like an endpoint/model that was never registered -- llm-router
 *  already treats that as ordinary config drift (see Catalog.resolve). */
router.post(
  '/internal/resolve',
  requireInternalSecret,
  handle(async (req, res) => {
    const { endpoint, model } = req.body || {};
    if (typeof endpoint !== 'string' || typeof model !== 'string') {
      return res.status(400).json({ message: '`endpoint` and `model` (strings) are required' });
    }
    // getAppConfig() already includes the LibreChat Admin/MongoDB `__base__`
    // configuration override merged on top of librechat.yaml -- the same
    // effective config direct model execution resolves against.
    const appConfig = await getAppConfig({});
    const endpointConfig = getCustomEndpointConfig({ endpoint, appConfig });
    const models = endpointConfig?.models?.default ?? [];
    if (!endpointConfig || !models.includes(model)) {
      return res.status(404).json({ message: `not a registered credential/model: ${endpoint} / ${model}` });
    }
    const apiKey = extractEnvVariable(endpointConfig.apiKey ?? '');
    if (!apiKey || isUserProvided(apiKey)) {
      return res.status(404).json({ message: `no resolvable credential for endpoint: ${endpoint}` });
    }
    return res.status(200).json({ baseURL: endpointConfig.baseURL || '', apiKey });
  }),
);

/**
 * Both capabilities are required. Order matches knowledgeBases.js: access:admin
 * first keeps a plain USER's 403 identical to every other admin surface.
 */
router.use(
  requireJwtAuth,
  requireCapability(SystemCapabilities.ACCESS_ADMIN),
  requireCapability(SystemCapabilities.MANAGE_MCP_SERVERS),
);

/** Existing agents, for the "LLM Routing -> list existing agents" step. Only the
 *  fields the picker needs -- never anything credential-shaped. */
router.get(
  '/agents',
  handle(async (req, res) => {
    const agents = await getAgents({});
    return res.status(200).json({
      agents: (agents || []).map((a) => ({
        id: a.id,
        name: a.name,
        provider: a.provider,
        model: a.model,
      })),
    });
  }),
);

router.get(
  '/catalog',
  handle(async (req, res) => {
    const { status, data } = await proxyToRouter('GET', '/admin/catalog');
    return res.status(status).json(data);
  }),
);

router.get(
  '/combos',
  handle(async (req, res) => {
    const { status, data } = await proxyToRouter('GET', '/admin/combos');
    return res.status(status).json(data);
  }),
);

router.get(
  '/combos/:agentId',
  validAgentId,
  handle(async (req, res) => {
    const { status, data } = await proxyToRouter('GET', `/admin/combos/${req.params.agentId}`);
    return res.status(status).json(data);
  }),
);

/** Create/update a Combo's targets. When it ends up enabled, the agent is pointed at
 *  the router (capturing its pre-routing provider/model exactly once); when it ends up
 *  disabled, the agent is restored -- the admin never has to separately edit the
 *  agent. */
router.put(
  '/combos/:agentId',
  validAgentId,
  handle(async (req, res) => {
    const agentId = req.params.agentId;
    const enabled = req.body?.enabled !== false;
    const body = { ...req.body };

    if (enabled) {
      const routed = await pointAgentAtRouter(agentId);
      if (!routed.ok) {
        return res.status(404).json({ message: `no such agent: ${agentId}` });
      }
      if (routed.originalProvider) {
        body.original_provider = routed.originalProvider;
        body.original_model = routed.originalModel;
      }
    }

    const { status, data } = await proxyToRouter('PUT', `/admin/combos/${agentId}`, body);
    if (status < 300 && !enabled) {
      await restoreAgentProvider(agentId, data.combo?.original_provider, data.combo?.original_model);
    }
    return res.status(status).json(data);
  }),
);

/** Enable/disable a Combo without touching its targets -- restores the agent's
 *  pre-routing provider/model on disable. */
router.put(
  '/combos/:agentId/enabled',
  validAgentId,
  handle(async (req, res) => {
    const agentId = req.params.agentId;
    if (typeof req.body?.enabled !== 'boolean') {
      return res.status(400).json({ message: '`enabled` (boolean) is required' });
    }

    if (req.body.enabled) {
      const routed = await pointAgentAtRouter(agentId);
      if (!routed.ok) {
        return res.status(404).json({ message: `no such agent: ${agentId}` });
      }
      if (routed.originalProvider) {
        // The /enabled endpoint only flips the flag -- it cannot store a
        // newly-captured original, so persist it through a full upsert instead.
        const existing = await proxyToRouter('GET', `/admin/combos/${agentId}`);
        if (existing.status === 200) {
          await proxyToRouter('PUT', `/admin/combos/${agentId}`, {
            targets: existing.data.combo.targets,
            enabled: true,
            original_provider: routed.originalProvider,
            original_model: routed.originalModel,
          });
        }
      }
    }

    const { status, data } = await proxyToRouter(
      'PUT',
      `/admin/combos/${agentId}/enabled`,
      req.body,
    );
    if (status < 300 && !req.body.enabled) {
      await restoreAgentProvider(agentId, data.combo?.original_provider, data.combo?.original_model);
    }
    return res.status(status).json(data);
  }),
);

router.delete(
  '/combos/:agentId',
  validAgentId,
  handle(async (req, res) => {
    const agentId = req.params.agentId;
    const existing = await proxyToRouter('GET', `/admin/combos/${agentId}`);
    const { status, data } = await proxyToRouter('DELETE', `/admin/combos/${agentId}`);
    if (status < 300 && existing.status === 200) {
      const combo = existing.data.combo;
      await restoreAgentProvider(agentId, combo?.original_provider, combo?.original_model);
    }
    return res.status(status).json(data);
  }),
);

module.exports = router;
