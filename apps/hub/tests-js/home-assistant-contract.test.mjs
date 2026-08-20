import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const appSource = readFileSync(resolve(__dirname, '../web/app.js'), 'utf8');
const coreSource = readFileSync(resolve(__dirname, '../web/hub-core.js'), 'utf8');
const stylesSource = readFileSync(resolve(__dirname, '../web/styles.css'), 'utf8');
const indexSource = readFileSync(resolve(__dirname, '../web/index.html'), 'utf8');

test('homepage assistant exposes explicit instant and route modes', () => {
  assert.match(coreSource, /homeAssistant:\s*'\/api\/home-assistant\/chat'/);
  assert.match(appSource, /data-assistant-mode="instant"/);
  assert.match(appSource, /data-assistant-mode="route"/);
  assert.match(appSource, /sendPortalAssistantMessage/);
  assert.match(appSource, /consumePortalAssistantStream/);
});

test('route recommendations activate a validated agent id instead of trusting a model URL', () => {
  assert.match(appSource, /data-route-agent-id/);
  assert.match(appSource, /activateAgentById\(button\.dataset\.routeAgentId\)/);
  assert.match(appSource, /推荐理由：/);
  assert.doesNotMatch(appSource, /recommendation\.(url|href|launch_url)/);
});

test('assistant UI includes bounded history, cancel state and responsive recommendation layout', () => {
  assert.match(appSource, /\.slice\(-11\)/);
  assert.match(appSource, /AbortController/);
  assert.match(appSource, /data-assistant-cancel/);
  assert.match(stylesSource, /\.portal-assistant__messages/);
  assert.match(stylesSource, /\.portal-agent-recommendation/);
  assert.match(stylesSource, /@media \(max-width: 640px\)/);
  assert.match(indexSource, /rel="icon" href="\/assets\/ustc-emblem\.jpg"/);
});
