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

test('homepage assistant uses one unified conversation with optional routing', () => {
  assert.match(coreSource, /homeAssistant:\s*'\/api\/home-assistant\/chat'/);
  assert.match(appSource, /mode: 'auto'/);
  assert.match(appSource, /'Accept': 'text\/event-stream'/);
  assert.match(appSource, /event\.type === 'model\.output_text\.delta'/);
  assert.match(appSource, /event\.type === 'home\.recommendation'/);
  assert.match(appSource, /event\.type === 'home\.completed'/);
  assert.match(appSource, /!completed/);
  assert.doesNotMatch(appSource, /response\.json\(\).*assistantMessage\.recommendation/s);
  assert.doesNotMatch(appSource, /data-assistant-mode=/);
  assert.match(appSource, /sendPortalAssistantMessage/);
  assert.match(appSource, /has-conversation/);
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
  assert.match(stylesSource, /\.portal-stage\.has-conversation/);
  assert.match(stylesSource, /conversation surface/);
  assert.match(stylesSource, /@media \(max-width: 640px\)/);
  assert.match(indexSource, /rel="icon" href="\/assets\/ustc-emblem\.jpg"/);
});
