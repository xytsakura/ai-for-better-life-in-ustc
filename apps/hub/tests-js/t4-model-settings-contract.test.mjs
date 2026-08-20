import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const appSource = readFileSync(resolve(__dirname, '../web/app.js'), 'utf8');
const coreSource = readFileSync(resolve(__dirname, '../web/hub-core.js'), 'utf8');
const indexSource = readFileSync(resolve(__dirname, '../web/index.html'), 'utf8');

test('T4 settings page uses service-backed model profile endpoints', () => {
  assert.match(coreSource, /modelProfiles:\s*'\/api\/model-profiles'/);
  assert.match(coreSource, /modelBindingGlobal:\s*'\/api\/model-bindings\/global'/);
  assert.match(coreSource, /modelBindingAgent:\s*\(id\) => `\/api\/model-bindings\/agents\//);
  assert.match(appSource, /async function loadModelProfiles/);
  assert.match(appSource, /normalizeModelProfilesPayload/);
  assert.match(appSource, /HUB_API\.modelProfileTest/);
  assert.match(appSource, /HUB_API\.modelProfileDiscover/);
});

test('T4 never repopulates API key inputs or falls back to localStorage saves for new profiles', () => {
  assert.match(appSource, /name="api_key" type="password" value=""/);
  assert.doesNotMatch(appSource, /value="\$\{escapeAttr\(.*apiKey/);
  assert.doesNotMatch(appSource, /toast\('已保存到本地（前端原型）/);
  assert.match(coreSource, /api_key:\s*_apiKey/);
  assert.match(coreSource, /encrypted_api_key:\s*_encryptedApiKey/);
});

test('T4 legacy migration is explicit and rejects non-local plain HTTP secrets', () => {
  assert.match(appSource, /data-migrate-legacy/);
  assert.match(appSource, /canMigrateSecretOnCurrentOrigin/);
  assert.match(appSource, /location\.protocol === 'https:'/);
  assert.match(appSource, /host === 'localhost'/);
  assert.match(appSource, /host === '127\.0\.0\.1'/);
  assert.match(appSource, /clearSettings\(\)/);
});

test('T4 keeps request identity aligned with active demo user and bumps module cache versions', () => {
  assert.match(appSource, /'X-Hub-User':\s*state\.user\.id/);
  assert.doesNotMatch(appSource, /options\.admin\s*\?\s*['"]demo-a['"]/);
  assert.match(appSource, /hub-core\.js\?v=20260820-2/);
  assert.match(indexSource, /styles\.css\?v=20260820-3/);
  assert.match(indexSource, /app\.js\?v=20260820-3/);
});

test('T4 only offers declared platform agents and chat-eligible models for bindings', () => {
  assert.match(appSource, /normalized\.capabilities\.includes\('platform-model-gateway'\)/);
  assert.match(appSource, /mode === 'platform_optional' \|\| mode === 'platform_required'/);
  assert.doesNotMatch(appSource, /normalized\.id === 'hanhai-course-agent'/);
  assert.match(appSource, /filter\(\(model\) => model\.chat_eligible\)/);
});

test('T4 shows a complete model routing progress summary instead of a vague form', () => {
  assert.match(appSource, /function renderModelSettingsOverview/);
  assert.match(appSource, /MODEL ROUTING FLOW/);
  assert.match(appSource, /创建 Profile/);
  assert.match(appSource, /发现模型/);
  assert.match(appSource, /全局默认/);
  assert.match(appSource, /Agent 绑定/);
  assert.match(appSource, /chatModelCount/);
  assert.match(appSource, /renderBindingText/);
});
