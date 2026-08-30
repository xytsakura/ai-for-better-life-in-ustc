import test from 'node:test';
import assert from 'node:assert/strict';
import { access, readdir, readFile } from 'node:fs/promises';
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

function referencedLocalFonts(css) {
  return [...css.matchAll(/url\('\.\/([^']+\.woff2)'\)/g)].map((match) => match[1]);
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

test('Hub uses self-hosted font assets without remote font providers', async () => {
  const html = await readFile(new URL('../web/index.html', import.meta.url), 'utf8');
  const styles = await readFile(new URL('../web/styles.css', import.meta.url), 'utf8');
  const fontCss = await readFile(new URL('../web/assets/fonts/font-system.css', import.meta.url), 'utf8');
  const fontFiles = await readdir(new URL('../web/assets/fonts/', import.meta.url));

  for (const source of [html, styles, fontCss]) {
    assert.equal(source.includes('fonts.googleapis.com'), false);
    assert.equal(source.includes('fonts.gstatic.com'), false);
  }
  assert.match(styles, /@import url\('\/assets\/fonts\/font-system\.css'\);/);
  assert.match(styles, /--font-sans: "Inter Variable", "Noto Sans SC Variable"/);
  assert.match(styles, /--font-serif: "Noto Serif SC", "Noto Sans SC Variable"/);
  assert.match(fontCss, /font-family: 'Inter Variable'/);
  assert.match(fontCss, /font-family: 'Noto Sans SC Variable'/);
  assert.match(fontCss, /font-family: 'Noto Serif SC'/);
  assert.ok(fontFiles.includes('inter-latin-wght-normal.woff2'));
  assert.ok(fontFiles.includes('noto-sans-sc-latin-wght-normal.woff2'));
  assert.ok(fontFiles.includes('noto-serif-sc-chinese-simplified-700-normal.woff2'));
  assert.ok(referencedLocalFonts(fontCss).length > 90);
  for (const fontFile of referencedLocalFonts(fontCss)) {
    assert.ok(fontFiles.includes(fontFile), `${fontFile} should be present locally`);
  }
  await access(new URL('../web/assets/fonts/LICENSE-Inter.txt', import.meta.url));
  await access(new URL('../web/assets/fonts/LICENSE-Noto-Sans-SC.txt', import.meta.url));
  await access(new URL('../web/assets/fonts/LICENSE-Noto-Serif-SC.txt', import.meta.url));
});
