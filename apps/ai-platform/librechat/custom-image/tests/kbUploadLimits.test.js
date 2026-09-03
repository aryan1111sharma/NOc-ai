/**
 * Tests for the API-side upload size gate (files/api/kbUploadLimits.js).
 *
 *     node --test "apps/ai-platform/librechat/custom-image/tests/**\/*.test.*"
 *
 * The gate is a separate module from knowledgeBases.js so it can be tested without a
 * LibreChat tree: knowledgeBases.js requires `~/server/middleware`, `@librechat/client`
 * and friends, none of which exist outside the image.
 */
const { test } = require('node:test');
const assert = require('node:assert/strict');

const {
  MAX_UPLOAD_BYTES,
  MAX_UPLOAD_CHARS,
  decodedBytes,
  checkUploadSize,
} = require('../files/api/kbUploadLimits.js');

/** Base64 for exactly `bytes` decoded bytes, with the padding that size really
 *  produces. Built by arithmetic rather than by encoding 100 MB of data. */
function b64OfSize(bytes) {
  const full = Math.floor(bytes / 3);
  const rest = bytes % 3;
  const chars = full * 4 + (rest === 0 ? 0 : 4);
  const padding = rest === 0 ? '' : rest === 1 ? '==' : '=';
  return 'A'.repeat(chars - padding.length) + padding;
}

test('the ceiling is 100 MB of decoded file, not of base64', () => {
  assert.equal(MAX_UPLOAD_BYTES, 100 * 1024 * 1024);
  assert.equal(MAX_UPLOAD_CHARS, 100_000_000);
});

test('decoded size accounts for base64 padding', () => {
  // 4 base64 characters carry 3, 2 or 1 bytes depending on the padding. Ignoring
  // padding would report 3 for all of them.
  assert.equal(decodedBytes('QUJD'), 3); // "ABC"
  assert.equal(decodedBytes('QUI='), 2); // "AB"
  assert.equal(decodedBytes('QQ=='), 1); // "A"
  assert.equal(decodedBytes(''), 0);
});

test('decoded size does not under-report by the base64 inflation factor', () => {
  // Rejecting on the encoded length would silently lower the real limit by a quarter.
  assert.equal(decodedBytes(b64OfSize(MAX_UPLOAD_BYTES)), MAX_UPLOAD_BYTES);
});

test('a text upload under the character limit is accepted', () => {
  assert.equal(checkUploadSize({ content: 'a NOC runbook' }), null);
});

test('a text upload of exactly the character limit is accepted', () => {
  assert.equal(checkUploadSize({ content: 'x'.repeat(MAX_UPLOAD_CHARS) }), null);
});

test('a text upload one character over the limit is rejected', () => {
  const refusal = checkUploadSize({ content: 'x'.repeat(MAX_UPLOAD_CHARS + 1) });
  assert.equal(refusal.status, 413);
  assert.match(refusal.message, /character/);
});

test('a binary upload under the byte limit is accepted', () => {
  assert.equal(checkUploadSize({ content_b64: b64OfSize(5 * 1024 * 1024) }), null);
});

test('a binary upload of exactly the byte limit is accepted', () => {
  assert.equal(checkUploadSize({ content_b64: b64OfSize(MAX_UPLOAD_BYTES) }), null);
});

test('a binary upload one byte over the limit is rejected', () => {
  const refusal = checkUploadSize({ content_b64: b64OfSize(MAX_UPLOAD_BYTES + 1) });
  assert.equal(refusal.status, 413);
  assert.match(refusal.message, /100 MB/);
});

test('the byte limit is applied to binary uploads, not the character limit', () => {
  // Regression: applying MAX_UPLOAD_CHARS to base64 content capped every PDF at
  // three quarters of the character limit instead of at the byte limit.
  const justOverChars = b64OfSize(MAX_UPLOAD_CHARS);
  assert.ok(justOverChars.length > MAX_UPLOAD_CHARS);
  assert.equal(checkUploadSize({ content_b64: justOverChars }), null);
});
