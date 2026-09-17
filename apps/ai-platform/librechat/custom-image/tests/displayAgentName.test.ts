/**
 * Tests for the chat-header display-name stripping (files/client/displayAgentName.ts).
 *
 * Run with the host's own Node test runner -- no jest, no LibreChat tree:
 *
 *     node --test apps/ai-platform/librechat/custom-image/tests
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { displayAgentName } from '../files/client/displayAgentName.ts';

test('strips a trailing provider suffix from the displayed name', () => {
  assert.equal(displayAgentName('NOC Engineer (Kimi)'), 'NOC Engineer');
});

test('strips a trailing suffix regardless of which provider/model it names', () => {
  assert.equal(displayAgentName('NOC Engineer (GLM)'), 'NOC Engineer');
  assert.equal(displayAgentName('Research Assistant (GPT-4o)'), 'Research Assistant');
});

test('leaves a name with no parenthetical suffix unchanged', () => {
  assert.equal(displayAgentName('NOC Engineer'), 'NOC Engineer');
});

test('only strips a suffix anchored at the end of the string', () => {
  assert.equal(displayAgentName('(Kimi) NOC Engineer'), '(Kimi) NOC Engineer');
});

test('leaves parentheses that are part of the name, not a trailing suffix, alone', () => {
  assert.equal(displayAgentName('NOC Engineer (Kimi) v2'), 'NOC Engineer (Kimi) v2');
});
