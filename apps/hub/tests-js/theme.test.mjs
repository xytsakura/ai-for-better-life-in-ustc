import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const themeSource = await readFile(new URL('../web/hub-theme.js', import.meta.url), 'utf8');

function loadThemeApi(localStorage) {
  const context = { localStorage };
  vm.createContext(context);
  vm.runInContext(themeSource, context);
  return context.HubTheme;
}

function storageWith(value) {
  return { getItem: () => value };
}

test('Hub defaults missing, invalid and inaccessible preferences to dark', () => {
  assert.equal(loadThemeApi(storageWith(null)).readFromGlobal(), 'dark');
  assert.equal(loadThemeApi(storageWith('sepia')).readFromGlobal(), 'dark');
  assert.equal(loadThemeApi({ getItem: () => { throw new Error('blocked'); } }).readFromGlobal(), 'dark');
});

test('Hub preserves valid explicit themes and cycles through all choices', () => {
  for (const theme of ['dark', 'light', 'system']) {
    assert.equal(loadThemeApi(storageWith(theme)).readFromGlobal(), theme);
  }
  const api = loadThemeApi(storageWith(null));
  assert.equal(api.next('dark'), 'light');
  assert.equal(api.next('light'), 'system');
  assert.equal(api.next('system'), 'dark');
  assert.equal(api.next('invalid'), 'light');
});

test('Hub applies the saved theme before loading its stylesheet or app module', async () => {
  const html = await readFile(new URL('../web/index.html', import.meta.url), 'utf8');
  const themeAsset = html.indexOf('src="/hub-theme.js?');
  const bootstrap = html.indexOf('globalThis.HubTheme.readFromGlobal()');
  const stylesheet = html.indexOf('href="/styles.css?');
  const appModule = html.indexOf('src="/app.js?');

  assert.ok(themeAsset >= 0);
  assert.ok(themeAsset < bootstrap);
  assert.ok(bootstrap < stylesheet);
  assert.ok(stylesheet < appModule);
});
