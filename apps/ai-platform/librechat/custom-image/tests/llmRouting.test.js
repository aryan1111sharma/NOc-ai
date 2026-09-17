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
 * asserts, structurally, that the exact same two-capability gate knowledgeBases.js
 * uses is applied to every route in this file, in registration order, and that
 * `router.use(requireJwtAuth, ...)` appears before any `router.get/post/put/delete`
 * call -- which is what makes it apply to all of them under Express's synchronous,
 * registration-order middleware model. A future edit that accidentally drops the
 * capability gate, reorders it after a route, or narrows it to only one capability
 * fails this test even though it cannot be caught by running the route.
 */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const SOURCE = fs.readFileSync(
  path.join(__dirname, '../files/api/llmRouting.js'),
  'utf8',
);

test('requires JWT auth and both admin capabilities, in the same order as knowledgeBases.js', () => {
  const useCallMatch = SOURCE.match(
    /router\.use\(\s*requireJwtAuth,\s*requireCapability\(SystemCapabilities\.ACCESS_ADMIN\),\s*requireCapability\(SystemCapabilities\.MANAGE_MCP_SERVERS\),?\s*\);/,
  );
  assert.ok(
    useCallMatch,
    'expected a single router.use(requireJwtAuth, requireCapability(ACCESS_ADMIN), ' +
      'requireCapability(MANAGE_MCP_SERVERS)) call',
  );
});

test('the capability gate is registered before any route handler', () => {
  const gateIndex = SOURCE.indexOf('router.use(');
  assert.notEqual(gateIndex, -1, 'router.use(...) not found');

  const routeCallPattern = /router\.(get|post|put|delete)\(/g;
  let match;
  while ((match = routeCallPattern.exec(SOURCE)) !== null) {
    assert.ok(
      match.index > gateIndex,
      `router.${match[1]}(...) at offset ${match.index} is registered before the ` +
        'auth/capability gate, so it would run unauthenticated',
    );
  }
});

test('never logs or echoes req.body.api_key', () => {
  // The facade forwards req.body to llm-router untouched but must not itself read,
  // log, or otherwise single out api_key -- it should be opaque cargo to this file.
  assert.ok(!/api_key/.test(SOURCE), 'llmRouting.js references api_key directly');
});

test('routes are declared for every documented Agent/Catalog/Combo admin operation', () => {
  const expected = [
    ["router.get(\n  '/agents'", 'GET /agents'],
    ["router.get(\n  '/catalog'", 'GET /catalog'],
    ["router.get(\n  '/combos'", 'GET /combos'],
    ["router.get(\n  '/combos/:agentId'", 'GET /combos/:agentId'],
    ["router.put(\n  '/combos/:agentId'", 'PUT /combos/:agentId'],
    ["router.put(\n  '/combos/:agentId/enabled'", 'PUT /combos/:agentId/enabled'],
    ["router.delete(\n  '/combos/:agentId'", 'DELETE /combos/:agentId'],
  ];
  for (const [needle, label] of expected) {
    assert.ok(SOURCE.includes(needle), `missing route: ${label}`);
  }
});

test('never accepts or forwards a raw apiKey field from the admin', () => {
  // A Combo target is an {endpoint, model} reference only -- this file must never
  // read, store, or pass through a credential value.
  assert.ok(!/apiKey/.test(SOURCE), 'llmRouting.js references apiKey directly');
});
