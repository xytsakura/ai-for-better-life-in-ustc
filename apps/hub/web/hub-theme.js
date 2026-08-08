(function installHubTheme(root) {
  const STORAGE_KEY = 'hub_theme';
  const THEMES = Object.freeze(['dark', 'light', 'system']);

  function normalize(value) {
    return THEMES.includes(value) ? value : 'dark';
  }

  function read(storage) {
    try {
      return normalize(storage?.getItem(STORAGE_KEY));
    } catch {
      return 'dark';
    }
  }

  function readFromGlobal() {
    try {
      return read(root.localStorage);
    } catch {
      return 'dark';
    }
  }

  function next(value) {
    const current = normalize(value);
    return current === 'dark' ? 'light' : current === 'light' ? 'system' : 'dark';
  }

  function label(value) {
    const theme = normalize(value);
    return theme === 'dark' ? '深色模式' : theme === 'light' ? '浅色模式' : '跟随系统';
  }

  root.HubTheme = Object.freeze({ STORAGE_KEY, THEMES, normalize, read, readFromGlobal, next, label });
}(globalThis));
