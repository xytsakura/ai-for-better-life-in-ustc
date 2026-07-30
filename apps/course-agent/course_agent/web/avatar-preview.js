(() => {
  'use strict';

  const PROFILE_KEY_PREFIX = 'course-agent:profile-v1:';
  const THEME_KEY = 'course-agent:theme';
  const SECOND_CLICK_WINDOW_MS = 5000;
  const THINKING_DURATION_MS = 2400;
  const WAVE_DURATION_MS = 1600;
  const READING_DURATION_MS = 3600;
  const GREETING_DURATION_MS = 4000;
  const QUOTE_DURATION_MS = 6000;

  const STUDY_QUOTES = Object.freeze([
    '《论语》：学而不思则罔，思而不学则殆。',
    '《论语》：温故而知新，可以为师矣。',
    '《论语》：知之者不如好之者，好之者不如乐之者。',
    '《荀子·劝学》：不积跬步，无以至千里；不积小流，无以成江海。',
    '韩愈《进学解》：业精于勤，荒于嬉；行成于思，毁于随。',
    '陆游《冬夜读书示子聿》：纸上得来终觉浅，绝知此事要躬行。',
    '《礼记·中庸》：博学之，审问之，慎思之，明辨之，笃行之。',
    '朱熹《观书有感》：问渠那得清如许？为有源头活水来。',
  ]);

  const POSES = {
    idle: '/assets/avatar-preview/agent-idle.png',
    thinking: '/assets/avatar-preview/agent-thinking.png',
    waveA: '/assets/avatar-preview/agent-wave-a.png',
    waveB: '/assets/avatar-preview/agent-wave-b.png',
    read: '/assets/avatar-preview/agent-reading.png',
  };

  const LABELS = {
    idle: '待机',
    thinking: '思考中',
    wave: '向你挥手',
    read: '阅读中',
  };

  const ARIA_LABELS = {
    idle: '虚拟形象，点击互动',
    thinking: '虚拟形象正在思考',
    wave: '虚拟形象正在挥手',
    read: '虚拟形象正在看书',
  };

  const state = {
    mode: 'idle',
    name: '同学',
    awaitingSecondClick: false,
    poseTimer: 0,
    bubbleTimer: 0,
    secondClickTimer: 0,
    waveFrameTimer: 0,
    waveFrameIsA: true,
    quoteIndex: 0,
  };

  const elements = {};
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

  function normalizeName(value) {
    const normalized = String(value ?? '').trim().replace(/\s+/g, ' ');
    return Array.from(normalized).slice(0, 24).join('');
  }

  function readHashParameters() {
    const source = window.location.hash.replace(/^#\/?/, '');
    return new URLSearchParams(source);
  }

  function readStoredProfileName(userId) {
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(userId)) return '';
    try {
      const raw = window.localStorage.getItem(`${PROFILE_KEY_PREFIX}${userId}`);
      if (!raw) return '';
      return normalizeName(JSON.parse(raw)?.nickname);
    } catch {
      return '';
    }
  }

  function resolveName() {
    const parameters = readHashParameters();
    const userId = parameters.get('user') || '';
    return readStoredProfileName(userId)
      || normalizeName(parameters.get('name'))
      || '同学';
  }

  function resolveTheme(value = '') {
    if (value === 'dark' || value === 'light') return value;
    try {
      const stored = window.localStorage.getItem(THEME_KEY);
      if (stored === 'dark' || stored === 'light') return stored;
    } catch {}
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  }

  function applyTheme(value = '') {
    document.documentElement.dataset.theme = resolveTheme(value);
  }

  function preloadPoses() {
    Object.values(POSES).forEach(source => {
      const image = new Image();
      image.decoding = 'async';
      image.src = source;
    });
  }

  function clearPoseTimers() {
    window.clearTimeout(state.poseTimer);
    window.clearInterval(state.waveFrameTimer);
    state.poseTimer = 0;
    state.waveFrameTimer = 0;
  }

  function clearSecondClickWindow() {
    window.clearTimeout(state.secondClickTimer);
    state.secondClickTimer = 0;
    state.awaitingSecondClick = false;
  }

  function setPoseSource(source) {
    if (elements.image.getAttribute('src') !== source) {
      elements.image.setAttribute('src', source);
    }
  }

  function setMode(mode) {
    state.mode = mode;
    elements.avatar.dataset.state = mode;
    elements.avatar.setAttribute('aria-label', ARIA_LABELS[mode]);
    elements.avatar.setAttribute('aria-disabled', String(mode === 'thinking'));
    elements.readout.dataset.state = mode;
    elements.stateLabel.textContent = LABELS[mode];
    elements.simulate.disabled = mode === 'thinking';

    if (mode === 'idle') setPoseSource(POSES.idle);
    if (mode === 'thinking') setPoseSource(POSES.thinking);
    if (mode === 'read') setPoseSource(POSES.read);
  }

  function returnToIdle(expectedMode) {
    if (state.mode !== expectedMode) return;
    clearPoseTimers();
    setMode('idle');
  }

  function showSpeech(message, durationMs) {
    window.clearTimeout(state.bubbleTimer);
    elements.speechText.textContent = message;
    elements.bubble.dataset.visible = 'true';
    state.bubbleTimer = window.setTimeout(() => {
      elements.bubble.dataset.visible = 'false';
      state.bubbleTimer = 0;
    }, durationMs);
  }

  function hideSpeech() {
    window.clearTimeout(state.bubbleTimer);
    state.bubbleTimer = 0;
    elements.bubble.dataset.visible = 'false';
  }

  function startThinking() {
    clearPoseTimers();
    clearSecondClickWindow();
    hideSpeech();
    setMode('thinking');
    state.poseTimer = window.setTimeout(() => returnToIdle('thinking'), THINKING_DURATION_MS);
  }

  function startWave() {
    clearPoseTimers();
    clearSecondClickWindow();
    state.awaitingSecondClick = true;
    state.secondClickTimer = window.setTimeout(clearSecondClickWindow, SECOND_CLICK_WINDOW_MS);
    state.waveFrameIsA = true;
    setMode('wave');
    setPoseSource(POSES.waveA);

    if (!reducedMotion.matches) {
      state.waveFrameTimer = window.setInterval(() => {
        state.waveFrameIsA = !state.waveFrameIsA;
        setPoseSource(state.waveFrameIsA ? POSES.waveA : POSES.waveB);
      }, 220);
    }

    showSpeech(`你好呀，${state.name}`, GREETING_DURATION_MS);
    state.poseTimer = window.setTimeout(() => returnToIdle('wave'), WAVE_DURATION_MS);
  }

  function nextStudyQuote() {
    const quote = STUDY_QUOTES[state.quoteIndex];
    state.quoteIndex = (state.quoteIndex + 1) % STUDY_QUOTES.length;
    return quote;
  }

  function startReading() {
    clearPoseTimers();
    clearSecondClickWindow();
    setMode('read');
    showSpeech(nextStudyQuote(), QUOTE_DURATION_MS);
    state.poseTimer = window.setTimeout(() => returnToIdle('read'), READING_DURATION_MS);
  }

  function handleAvatarInteraction() {
    if (state.mode === 'thinking') return;
    if (state.awaitingSecondClick) {
      startReading();
      return;
    }
    startWave();
  }

  function updateName() {
    state.name = resolveName();
  }

  function handleStorageChange(event) {
    if (event.key === THEME_KEY) applyTheme(event.newValue || '');
    const userId = readHashParameters().get('user') || '';
    if (event.key === `${PROFILE_KEY_PREFIX}${userId}`) updateName();
  }

  function init() {
    elements.avatar = document.querySelector('#avatar-button');
    elements.image = document.querySelector('#avatar-image');
    elements.readout = document.querySelector('#state-readout');
    elements.stateLabel = document.querySelector('#state-label');
    elements.bubble = document.querySelector('#speech-bubble');
    elements.speechText = document.querySelector('#speech-text');
    elements.simulate = document.querySelector('#simulate-reply');

    applyTheme();
    updateName();
    preloadPoses();
    setMode('idle');

    elements.avatar.addEventListener('click', handleAvatarInteraction);
    elements.simulate.addEventListener('click', startThinking);
    window.addEventListener('hashchange', updateName);
    window.addEventListener('storage', handleStorageChange);
    window.addEventListener('pagehide', () => {
      clearPoseTimers();
      clearSecondClickWindow();
      window.clearTimeout(state.bubbleTimer);
    });
  }

  init();
})();
