/**
 * Contract test for the LLM Routing admin API's authorization wiring
 * (files/api/llmRouting.js).
 *
 * llmRouting.js, like knowledgeBases.js beside it, requires `~/server/middleware`
 * and `@librechat/data-schemas` -- packages that exist only inside a built
 * LibreChat image, not in this repo. It cannot be `require()`d or exercised with a
 * live request here (see kbUploadLimits.test.js's header for the same constraint on
 * knowledgeBases.js, which today has no direct test at all).
 *
 * This test narrows that gap without a built image: it reads the module as text and
 * asserts, structurally, that:
 *
 *   - every browser-facing route (everything except /internal/*) sits behind the same
 *     two-capability gate knowledgeBases.js uses, in registration order -- which is
 *     what makes it apply to all of them under Express's synchronous,
 *     registration-order middleware model.
 *   - the two /internal/* routes (the one deliberate exception -- see the file's own
 *     header comment) are registered BEFORE that gate (so llm-router, which has no
 *     browser session, can reach them) but are each individually gated by
 *     requireInternalSecret, and that gate fails closed when the secret is unset.
 *   - apiKey/api_key only ever appears where the /internal/* routes need it, never on
 *     a browser-facing route's request or response handling.
 */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const SOURCE = fs.readFileSync(
  path.join(__dirname, '../files/api/llmRouting.js'),
  'utf8',
);

const AUTH_GATE_PATTERN =
  /router\.use\(\s*requireJwtAuth,\s*requireCapability\(SystemCapabilities\.ACCESS_ADMIN\),\s*requireCapability\(SystemCapabilities\.MANAGE_MCP_SERVERS\),?\s*\);/;

test('requires JWT auth and both admin capabilities, in the same order as knowledgeBases.js', () => {
  assert.ok(
    AUTH_GATE_PATTERN.test(SOURCE),
    'expected a single router.use(requireJwtAuth, requireCapability(ACCESS_ADMIN), ' +
      'requireCapability(MANAGE_MCP_SERVERS)) call',
  );
});

test('the capability gate is registered before every browser-facing route handler', () => {
  const gateMatch = SOURCE.match(AUTH_GATE_PATTERN);
  assert.ok(gateMatch, 'router.use(requireJwtAuth, ...) not found');
  const gateIndex = gateMatch.index;

  const routeCallPattern = /router\.(get|post|put|delete)\(\s*\n\s*'([^']+)'/g;
  let match;
  let sawAny = false;
  while ((match = routeCallPattern.exec(SOURCE)) !== null) {
    const [, verb, routePath] = match;
    sawAny = true;
    if (routePath.startsWith('/internal/')) {
      // The one deliberate exception -- asserted separately below.
      continue;
    }
    assert.ok(
      match.index > gateIndex,
      `router.${verb}('${routePath}') at offset ${match.index} is registered before the ` +
        'auth/capability gate, so it would run unauthenticated',
    );
  }
  assert.ok(sawAny, 'no router.get/post/put/delete calls found to check');
});

test('/internal/* routes are registered before the JWT gate but each carry requireInternalSecret', () => {
  const gateMatch = SOURCE.match(AUTH_GATE_PATTERN);
  assert.ok(gateMatch, 'router.use(requireJwtAuth, ...) not found');
  const gateIndex = gateMatch.index;

  for (const [verb, routePath] of [
    ['get', '/internal/catalog'],
    ['post', '/internal/resolve'],
  ]) {
    const pattern = new RegExp(
      `router\\.${verb}\\(\\s*\\n\\s*'${routePath.replace('/', '\\/')}',\\s*\\n\\s*requireInternalSecret,`,
    );
    assert.ok(pattern.test(SOURCE), `expected router.${verb}('${routePath}', requireInternalSecret, ...)`);

    const callIndex = SOURCE.search(new RegExp(`router\\.${verb}\\(\\s*\\n\\s*'${routePath.replace('/', '\\/')}'`));
    assert.ok(callIndex !== -1, `route ${routePath} not found`);
    assert.ok(
      callIndex < gateIndex,
      `router.${verb}('${routePath}') must be registered before the requireJwtAuth gate ` +
        '(llm-router has no browser session to satisfy it)',
    );
  }
});

test('requireInternalSecret fails closed when the secret is unset', () => {
  const fnMatch = SOURCE.match(/function requireInternalSecret\(req, res, next\) \{([\s\S]*?)\n\}/);
  assert.ok(fnMatch, 'requireInternalSecret function not found');
  assert.ok(
    /!INTERNAL_SECRET/.test(fnMatch[1]),
    'requireInternalSecret must reject when INTERNAL_SECRET is falsy, not just on mismatch',
  );
});

test('never logs or echoes req.body.api_key', () => {
  // The facade forwards req.body to llm-router untouched but must not itself read,
  // log, or otherwise single out api_key -- it should be opaque cargo to this file.
  assert.ok(!/api_key/.test(SOURCE), 'llmRouting.js references api_key directly');
});

test('routes are declared for every documented Agent/Catalog/Combo/internal operation', () => {
  const expected = [
    ["router.get(\n  '/agents'", 'GET /agents'],
    ["router.get(\n  '/catalog'", 'GET /catalog'],
    ["router.get(\n  '/combos'", 'GET /combos'],
    ["router.get(\n  '/combos/:agentId'", 'GET /combos/:agentId'],
    ["router.put(\n  '/combos/:agentId'", 'PUT /combos/:agentId'],
    ["router.put(\n  '/combos/:agentId/enabled'", 'PUT /combos/:agentId/enabled'],
    ["router.delete(\n  '/combos/:agentId'", 'DELETE /combos/:agentId'],
    ["router.get(\n  '/internal/catalog'", 'GET /internal/catalog'],
    ["router.post(\n  '/internal/resolve'", 'POST /internal/resolve'],
  ];
  for (const [needle, label] of expected) {
    assert.ok(SOURCE.includes(needle), `missing route: ${label}`);
  }
});

test('apiKey only appears in the /internal/* credential-resolution code, never on a browser-facing route', () => {
  // Slice the file at the requireJwtAuth gate: everything after it is the
  // browser-facing surface, which must stay exactly as credential-blind as before.
  const gateMatch = SOURCE.match(AUTH_GATE_PATTERN);
  assert.ok(gateMatch, 'router.use(requireJwtAuth, ...) not found');
  const browserFacingSection = SOURCE.slice(gateMatch.index + gateMatch[0].length);
  assert.ok(
    !/apiKey/.test(browserFacingSection),
    'a browser-facing route (after the requireJwtAuth gate) references apiKey',
  );

  const internalSection = SOURCE.slice(0, gateMatch.index);
  assert.ok(
    /apiKey/.test(internalSection),
    'expected the /internal/resolve route (before the gate) to reference apiKey',
  );
});
