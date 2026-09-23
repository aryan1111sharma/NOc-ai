/**
 * Contract test for the Session Intelligence admin API's authorization
 * wiring (files/api/sessions.js). Same structural-source-read approach as
 * llmRouting.test.js beside it, for the same reason: sessions.js requires
 * `~/server/middleware` and `@librechat/data-schemas`, which exist only
 * inside a built LibreChat image.
 */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const SOURCE = fs.readFileSync(
  path.join(__dirname, '../files/api/sessions.js'),
  'utf8',
);

const AUTH_GATE_PATTERN =
  /router\.use\(\s*requireJwtAuth,\s*requireCapability\(SystemCapabilities\.ACCESS_ADMIN\),\s*requireCapability\(SystemCapabilities\.MANAGE_MCP_SERVERS\),?\s*\);/;

test('requires JWT auth and both admin capabilities, same as llmRouting.js', () => {
  assert.ok(
    AUTH_GATE_PATTERN.test(SOURCE),
    'expected a single router.use(requireJwtAuth, requireCapability(ACCESS_ADMIN), ' +
      'requireCapability(MANAGE_MCP_SERVERS)) call',
  );
});

test('every route is registered after the capability gate', () => {
  const gateMatch = SOURCE.match(AUTH_GATE_PATTERN);
  assert.ok(gateMatch, 'router.use(requireJwtAuth, ...) not found');
  const gateIndex = gateMatch.index;

  const routeCallPattern = /router\.(get|post|put|delete)\(\s*\n\s*'([^']+)'/g;
  let match;
  let sawAny = false;
  while ((match = routeCallPattern.exec(SOURCE)) !== null) {
    const [, verb, routePath] = match;
    sawAny = true;
    assert.ok(
      match.index > gateIndex,
      `router.${verb}('${routePath}') at offset ${match.index} is registered before the ` +
        'auth/capability gate, so it would run unauthenticated',
    );
  }
  assert.ok(sawAny, 'no router.get/post/put/delete calls found to check');
});

test('no provider credential field is ever referenced', () => {
  // Unlike llmRouting.js, this facade has no /internal/* exception at all --
  // the session service never returns a credential, so apiKey/api_key must
  // never appear anywhere in this file.
  assert.ok(!/apiKey/.test(SOURCE), 'sessions.js references apiKey');
  assert.ok(!/api_key/.test(SOURCE), 'sessions.js references api_key');
});

test('routes are declared for session list and session detail', () => {
  const expected = [
    ["router.get(\n  '/',", 'GET /'],
    ["router.get(\n  '/:sessionId'", 'GET /:sessionId'],
  ];
  for (const [needle, label] of expected) {
    assert.ok(SOURCE.includes(needle), `missing route: ${label}`);
  }
});

test('session detail route validates the id before proxying', () => {
  assert.ok(
    /router\.get\(\s*\n\s*'\/:sessionId',\s*\n\s*validSessionId,/.test(SOURCE),
    "expected router.get('/:sessionId', validSessionId, ...)",
  );
});
