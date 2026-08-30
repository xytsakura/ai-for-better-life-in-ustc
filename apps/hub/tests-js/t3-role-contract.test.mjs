import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const appSource = readFileSync(resolve(__dirname, '../web/app.js'), 'utf8');
const indexSource = readFileSync(resolve(__dirname, '../web/index.html'), 'utf8');
const stylesSource = readFileSync(resolve(__dirname, '../web/styles.css'), 'utf8');

test('T3 front-end never swaps the active identity to demo-a for admin requests', () => {
  assert.doesNotMatch(appSource, /options\.admin\s*\?\s*['"]demo-a['"]/);
  assert.doesNotMatch(appSource, /admin\s*:\s*true/);
  assert.match(appSource, /'X-Hub-User':\s*state\.user\.id/);
});

test('T3 role navigation exposes developer and admin modules separately', () => {
  assert.match(indexSource, /data-developer-only/);
  assert.match(indexSource, /href="\/hub\/submissions"/);
  assert.match(indexSource, /data-admin-only/);
  assert.match(appSource, /function canSubmitAgents/);
  assert.match(appSource, /function renderSubmissions/);
  assert.match(stylesSource, /\[hidden\]\s*\{[^}]*display:\s*none\s*!important/s);
});
