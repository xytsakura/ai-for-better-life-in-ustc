import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const themeSource = await readFile(new URL('../course_agent/web/course-theme.js', import.meta.url), 'utf8');

function loadThemeApi(localStorage) {
  const context = { localStorage };
  vm.createContext(context);
  vm.runInContext(themeSource, context);
  return context.CourseAgentTheme;
}

function storageWith(value) {
  return { getItem: () => value };
}

test('瀚海行 defaults missing, invalid and inaccessible preferences to dark', () => {
  assert.equal(loadThemeApi(storageWith(null)).readFromGlobal(), 'dark');
  assert.equal(loadThemeApi(storageWith('system')).readFromGlobal(), 'dark');
  assert.equal(loadThemeApi({ getItem: () => { throw new Error('blocked'); } }).readFromGlobal(), 'dark');
});

test('瀚海行 preserves valid explicit dark and light preferences', () => {
  assert.equal(loadThemeApi(storageWith('dark')).readFromGlobal(), 'dark');
  assert.equal(loadThemeApi(storageWith('light')).readFromGlobal(), 'light');
});

test('瀚海行 applies the saved theme before loading its stylesheet and app', async () => {
  const html = await readFile(new URL('../course_agent/web/index.html', import.meta.url), 'utf8');
  const themeAsset = html.indexOf('src="/assets/course-theme.js?');
  const bootstrap = html.indexOf('globalThis.CourseAgentTheme.readFromGlobal()');
  const stylesheet = html.indexOf('href="/assets/styles.css?');
  const appScript = html.indexOf('src="/assets/app.js?');

  assert.ok(themeAsset >= 0);
  assert.ok(themeAsset < bootstrap);
  assert.ok(bootstrap < stylesheet);
  assert.ok(stylesheet < appScript);
});
