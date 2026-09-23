/**
 * Session Intelligence admin API.
 *
 * A thin, authenticated HTTP facade over the EXISTING `session` service (see
 * services/session/README.md), mirroring llmRouting.js beside it exactly:
 * same auth gate, same proxyToRouter/handle shape, same "owns no state of
 * its own" design. The `session` service is internal to the Docker network,
 * has no published port, and no auth of its own -- the browser-facing
 * boundary is entirely this file's requireJwtAuth + requireCapability gate.
 *
 *   GET /api/sessions            -> GET session-service /admin/sessions
 *   GET /api/sessions/:sessionId -> GET session-service /admin/sessions/:sessionId
 *
 * Both routes forward query params verbatim (agent/model/date_from/date_to/
 * limit/skip) -- this file makes no filtering decision itself, the session
 * service already does.
 *
 * The session service never returns a provider credential (it has none to
 * begin with -- see that service's README "Security model"), so unlike
 * llm-router this facade needs no /internal/* exception and no credential
 * handling anywhere.
 */
const express = require('express');
const { logger } = require('@librechat/data-schemas');
const { SystemCapabilities } = require('@librechat/data-schemas');
const { requireCapability } = require('~/server/middleware/roles/capabilities');
const { requireJwtAuth } = require('~/server/middleware');

const router = express.Router();

/** Internal service URL. Overridable so a test container can point elsewhere. */
const SESSION_SERVICE_URL = process.env.SESSION_SERVICE_URL || 'http://session:8112';

/** LibreChat conversationId values are UUIDs; this also accepts the same
 *  safe character set llmRouting.js uses for agentId. */
const SAFE_ID = /^[A-Za-z0-9_-]+$/;

function validSessionId(req, res, next) {
  if (!SAFE_ID.test(req.params.sessionId || '')) {
    return res.status(400).json({ message: 'Invalid session id' });
  }
  return next();
}

function handle(fn) {
  return async (req, res) => {
    try {
      return await fn(req, res);
    } catch (error) {
      logger.error('[sessions]', error);
      return res.status(502).json({ message: 'Session service unavailable' });
    }
  };
}

/**
 * Forward one call to the session service and relay its status + JSON body
 * verbatim -- this file makes no decision about what counts as success or
 * failure, same rationale as llmRouting.js's proxyToRouter.
 */
async function proxyToSessionService(method, path) {
  const upstream = await fetch(`${SESSION_SERVICE_URL}${path}`, { method });
  const text = await upstream.text();
  return { status: upstream.status, data: text ? JSON.parse(text) : {} };
}

/**
 * Both capabilities are required. Order matches knowledgeBases.js and
 * llmRouting.js: access:admin first keeps a plain USER's 403 identical to
 * every other admin surface.
 */
router.use(
  requireJwtAuth,
  requireCapability(SystemCapabilities.ACCESS_ADMIN),
  requireCapability(SystemCapabilities.MANAGE_MCP_SERVERS),
);

/** Session list -- basic filtering by agent/model/date, per the Admin ->
 *  Sessions spec. Query params are forwarded untouched. */
router.get(
  '/',
  handle(async (req, res) => {
    const query = new URLSearchParams(req.query).toString();
    const path = query ? `/admin/sessions?${query}` : '/admin/sessions';
    const { status, data } = await proxyToSessionService('GET', path);
    return res.status(status).json(data);
  }),
);

/** Session detail -- the full User request -> Tool Filter -> RAG ->
 *  Provider/model -> fallback -> tool calls -> result -> feedback timeline. */
router.get(
  '/:sessionId',
  validSessionId,
  handle(async (req, res) => {
    const { status, data } = await proxyToSessionService(
      'GET',
      `/admin/sessions/${req.params.sessionId}`,
    );
    return res.status(status).json(data);
  }),
);

module.exports = router;
