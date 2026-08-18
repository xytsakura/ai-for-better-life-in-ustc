import test from 'node:test';
import assert from 'node:assert/strict';
import { access, readdir, readFile } from 'node:fs/promises';
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

function referencedLocalFonts(css) {
  return [...css.matchAll(/url\('\.\/([^']+\.woff2)'\)/g)].map((match) => match[1]);
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

test('瀚海行 uses self-hosted font assets without remote font providers', async () => {
  const html = await readFile(new URL('../course_agent/web/index.html', import.meta.url), 'utf8');
  const styles = await readFile(new URL('../course_agent/web/styles.css', import.meta.url), 'utf8');
  const fontCss = await readFile(new URL('../course_agent/web/assets/fonts/font-system.css', import.meta.url), 'utf8');
  const fontFiles = await readdir(new URL('../course_agent/web/assets/fonts/', import.meta.url));

  for (const source of [html, styles, fontCss]) {
    assert.equal(source.includes('fonts.googleapis.com'), false);
    assert.equal(source.includes('fonts.gstatic.com'), false);
  }
  assert.match(styles, /@import url\('assets\/fonts\/font-system\.css'\);/);
  assert.match(styles, /--font: "Inter Variable", "Noto Sans SC Variable"/);
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
  await access(new URL('../course_agent/web/assets/fonts/LICENSE-Inter.txt', import.meta.url));
  await access(new URL('../course_agent/web/assets/fonts/LICENSE-Noto-Sans-SC.txt', import.meta.url));
  await access(new URL('../course_agent/web/assets/fonts/LICENSE-Noto-Serif-SC.txt', import.meta.url));
});
