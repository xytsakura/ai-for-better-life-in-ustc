(function installCourseAgentTheme(root) {
  const STORAGE_KEY = 'course-agent:theme';
  const THEMES = Object.freeze(['dark', 'light']);

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

  root.CourseAgentTheme = Object.freeze({ STORAGE_KEY, THEMES, normalize, read, readFromGlobal });
}(globalThis));
