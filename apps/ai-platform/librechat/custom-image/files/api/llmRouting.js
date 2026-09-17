/**
 * LLM Routing admin API.
 *
 * A thin, authenticated HTTP facade over the EXISTING llm-router service, plus the
 * minimal Agent CRUD needed to point an agent at it. It owns no credential state of
 * its own: the set of selectable (credential, model) targets is llm-router's own
 * read-only /admin/catalog, itself sourced from librechat.yaml's endpoints.custom list
 * -- the SAME provider registration LibreChat's own agents already use. This file
 * never accepts, stores or returns an API key -- same design as knowledgeBases.js.
 *
 *   GET    /api/llm-routing/agents                 -> LibreChat's own Agent collection (getAgents)
 *   GET    /api/llm-routing/catalog                -> GET    llm-router /admin/catalog
 *   GET    /api/llm-routing/combos                 -> GET    llm-router /admin/combos
 *   GET    /api/llm-routing/combos/:agentId        -> GET    llm-router /admin/combos/:agentId
 *   PUT    /api/llm-routing/combos/:agentId        -> PUT    llm-router /admin/combos/:agentId (+ Agent sync)
 *   PUT    /api/llm-routing/combos/:agentId/enabled -> PUT   llm-router /admin/combos/:agentId/enabled (+ Agent sync)
 *   DELETE /api/llm-routing/combos/:agentId        -> DELETE llm-router /admin/combos/:agentId (+ Agent revert)
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
 * this boundary in either direction: a Combo target is an {endpoint, model} reference,
 * never a key.
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
