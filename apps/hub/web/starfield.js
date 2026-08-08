const DEFAULT_SEED = 'campus-agent-hub-starfield';
const TAU = Math.PI * 2;

const COLOR_PALETTE = Object.freeze([
  { core: '180, 226, 255', glow: '89, 177, 255' },
  { core: '208, 204, 255', glow: '142, 122, 255' },
  { core: '244, 248, 255', glow: '176, 215, 255' },
]);

export function createSeededRandom(seed = DEFAULT_SEED) {
  let state = hashSeed(seed);
  return function random() {
    state += 0x6d2b79f5;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

export function sampleGaussianPair(random = Math.random) {
  let u = 0;
  let v = 0;
  while (u <= Number.EPSILON) u = random();
  while (v <= Number.EPSILON) v = random();
  const magnitude = Math.sqrt(-2 * Math.log(u));
  const angle = TAU * v;
  return [magnitude * Math.cos(angle), magnitude * Math.sin(angle)];
}

export function resolveLightTheme(theme, systemLight = false, followSystemLight = true) {
  if (theme === 'light') return true;
  if (theme === 'system') return Boolean(systemLight) && followSystemLight !== false;
  return false;
}

export function generateStarField(options = {}) {
  const width = Math.max(1, Math.floor(Number(options.width) || 1));
  const height = Math.max(1, Math.floor(Number(options.height) || 1));
  const random = createSeededRandom(options.seed ?? DEFAULT_SEED);
  const area = width * height;
  const density = Number(options.density ?? 0.00018);
  const mobile = Boolean(options.mobile);
  const countLimit = Number(options.maxStars ?? (mobile ? 170 : 420));
  const targetCount = clamp(
    Math.round(area * density * (mobile ? 0.58 : 1)),
    Number(options.minStars ?? 70),
    countLimit,
  );
  const clusterTarget = Math.round(targetCount * Number(options.clusterRatio ?? 0.84));
  const center = {
    x: Number(options.centerX ?? width * 0.5),
    y: Number(options.centerY ?? height * 0.36),
  };
  const sigmaX = Math.max(24, Number(options.sigmaX ?? width * 0.24));
  const sigmaY = Math.max(20, Number(options.sigmaY ?? height * 0.13));
  const stars = [];

  let attempts = 0;
  while (stars.length < clusterTarget && attempts < clusterTarget * 80) {
    attempts += 1;
    const [gx, gy] = sampleGaussianPair(random);
    const candidate = {
      x: center.x + gx * sigmaX,
      y: center.y + gy * sigmaY,
    };
    if (!inside(candidate.x, candidate.y, width, height)) continue;

    const nx = candidate.x / width;
    const ny = candidate.y / height;
    const noise = lowFrequencyValueNoise(nx * 5.4, ny * 4.2, options.seed ?? DEFAULT_SEED);
    const edgeFalloff = 1 - clamp(Math.hypot(gx / 3.2, gy / 2.6), 0, 1);
    const accept = 0.22 + noise * 0.58 + edgeFalloff * 0.2;
    if (random() > accept) continue;
    stars.push(createStar(candidate.x, candidate.y, random, 'cluster'));
  }

  while (stars.length < targetCount) {
    stars.push(createStar(random() * width, random() * height, random, 'outer'));
  }

  return {
    center,
    height,
    seed: options.seed ?? DEFAULT_SEED,
    stars,
    width,
  };
}

export function createInteractionState(options = {}) {
  return {
    elapsedMs: 0,
    interactive: options.interactive !== false,
    lastPointer: null,
    lastSparkAt: -Infinity,
    lastWakeAt: -Infinity,
    maxSparks: Math.max(0, Math.floor(Number(options.maxSparks ?? 80))),
    maxWakes: Math.max(0, Math.floor(Number(options.maxWakes ?? 18))),
    minWakeDistance: Math.max(0, Number(options.minWakeDistance ?? 12)),
    minWakeIntervalMs: Math.max(0, Number(options.minWakeIntervalMs ?? 24)),
    random: typeof options.random === 'function'
      ? options.random
      : createSeededRandom(options.seed ?? `${DEFAULT_SEED}-interaction`),
    sparkBurst: Math.max(0, Math.floor(Number(options.sparkBurst ?? 4))),
    sparkLifeMs: Math.max(1, Number(options.sparkLifeMs ?? 620)),
    sparks: [],
    stationarySparkIntervalMs: Math.max(1, Number(options.stationarySparkIntervalMs ?? 760)),
    wakeLifeMs: Math.max(1, Number(options.wakeLifeMs ?? 920)),
    wakes: [],
  };
}

export function stepInteraction(state, input = {}, deltaMs = 16) {
  const dt = clamp(Number(deltaMs) || 0, 0, 250);
  const next = {
    ...state,
    elapsedMs: (Number(state.elapsedMs) || 0) + dt,
    sparks: ageSparks(state.sparks || [], dt),
    wakes: ageWakes(state.wakes || [], dt),
  };
  const pointer = normalizePointerInput(input);
  if (!next.interactive || !pointer.active) {
    next.lastPointer = null;
    return next;
  }

  const previous = state.lastPointer;
  const distance = previous ? Math.hypot(pointer.x - previous.x, pointer.y - previous.y) : Infinity;
  const moved = input.moved === true || distance >= next.minWakeDistance;
  const canWake = moved && next.elapsedMs - next.lastWakeAt >= next.minWakeIntervalMs;
  if (canWake) {
    next.wakes = capItems([
      ...next.wakes,
      {
        ageMs: 0,
        lifeMs: next.wakeLifeMs,
        radius: 34 + next.random() * 30,
        x: pointer.x,
        y: pointer.y,
      },
    ], next.maxWakes);
    next.lastWakeAt = next.elapsedMs;
    next.sparks = capItems([
      ...next.sparks,
      ...createSparkBurst(pointer.x, pointer.y, next),
    ], next.maxSparks);
    next.lastSparkAt = next.elapsedMs;
  } else if (next.elapsedMs - next.lastSparkAt >= next.stationarySparkIntervalMs) {
    next.sparks = capItems([
      ...next.sparks,
      ...createSparkBurst(pointer.x, pointer.y, { ...next, sparkBurst: Math.min(2, next.sparkBurst) }),
    ], next.maxSparks);
    next.lastSparkAt = next.elapsedMs;
  }

  next.lastPointer = { x: pointer.x, y: pointer.y };
  return next;
}

export function shouldAnimate(mediaReducedMotion, documentVisible, pointerFine) {
  return !mediaReducedMotion && documentVisible && pointerFine;
}

export function mountStarfield(container, options = {}) {
  if (!container || !container.ownerDocument) return { destroy() {} };

  const doc = container.ownerDocument;
  const win = doc.defaultView || globalThis;
  const canvas = doc.createElement('canvas');
  const context = canvas.getContext?.('2d');
  if (!context) return { destroy() {} };

  const previousPosition = container.style.position;
  const computedPosition = win.getComputedStyle?.(container).position;
  if (!computedPosition || computedPosition === 'static') container.style.position = 'relative';

  canvas.dataset.starfieldCanvas = '';
  canvas.setAttribute('aria-hidden', 'true');
  Object.assign(canvas.style, {
    height: '100%',
    inset: '0',
    pointerEvents: 'none',
    position: 'absolute',
    width: '100%',
  });
  container.prepend(canvas);

  const reducedMotionQuery = createMediaQuery(win, '(prefers-reduced-motion: reduce)');
  const pointerFineQuery = createMediaQuery(win, '(pointer: fine)');
  const lightSchemeQuery = createMediaQuery(win, '(prefers-color-scheme: light)');
  let frameId = 0;
  let destroyed = false;
  let lastFrameAt = 0;
  let stars = generateStarField({ ...options, width: 1, height: 1 }).stars;
  let field = { center: { x: 0.5, y: 0.36 }, height: 1, seed: options.seed ?? DEFAULT_SEED, stars, width: 1 };
  let interaction = createInteractionState({ ...options, interactive: isInteractive() });
  const pointer = { active: false, moved: false, x: 0, y: 0 };

  function resize() {
    const rect = container.getBoundingClientRect();
    const width = Math.max(1, Math.round(rect.width || Number(options.width) || 1));
    const height = Math.max(1, Math.round(rect.height || Number(options.height) || 1));
    const ratio = Math.min(2, Math.max(1, Number(win.devicePixelRatio) || 1));
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    field = generateStarField({
      ...options,
      height,
      mobile: !pointerFineQuery.matches || width < 720,
      width,
    });
    stars = field.stars;
    draw(lastFrameAt || 0);
    restartLoop();
  }

  function handlePointerMove(event) {
    if (!isInteractive()) return;
    const target = event.target;
    if (isTextInput(target)) return;
    const rect = container.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    if (!inside(x, y, rect.width, rect.height)) return;
    pointer.active = true;
    pointer.moved = true;
    pointer.x = x;
    pointer.y = y;
  }

  function handlePointerLeave() {
    pointer.active = false;
    pointer.moved = false;
  }

  function handleSelectStart(event) {
    if (!isInteractive() || isTextInput(event.target)) return;
    event.preventDefault();
  }

  function handleVisibilityChange() {
    restartLoop();
  }

  function handleMediaChange() {
    interaction = {
      ...interaction,
      interactive: isInteractive(),
      sparks: isInteractive() ? interaction.sparks : [],
      wakes: isInteractive() ? interaction.wakes : [],
    };
    draw(lastFrameAt || 0);
    restartLoop();
  }

  function loop(now) {
    if (destroyed) return;
    const delta = lastFrameAt ? now - lastFrameAt : 16;
    lastFrameAt = now;
    interaction = stepInteraction(interaction, pointer, delta);
    pointer.moved = false;
    draw(now);
    if (canRunAnimation()) frameId = win.requestAnimationFrame(loop);
  }

  function restartLoop() {
    if (frameId) win.cancelAnimationFrame(frameId);
    frameId = 0;
    if (destroyed) return;
    if (canRunAnimation()) {
      lastFrameAt = 0;
      frameId = win.requestAnimationFrame(loop);
    }
  }

  function canRunAnimation() {
    return shouldAnimate(reducedMotionQuery.matches, doc.visibilityState !== 'hidden', pointerFineQuery.matches)
      && !isLightTheme();
  }

  function isInteractive() {
    return canRunAnimation();
  }

  function draw(now) {
    const light = isLightTheme();
    context.clearRect(0, 0, field.width, field.height);
    if (light) return;
    drawFog(context, field.width, field.height, interaction.wakes);
    drawStars(context, stars, now, canRunAnimation());
    drawSparks(context, interaction.sparks);
  }

  const resizeObserver = typeof win.ResizeObserver === 'function'
    ? new win.ResizeObserver(resize)
    : null;
  resizeObserver?.observe(container);

  const mutationObserver = typeof win.MutationObserver === 'function'
    ? new win.MutationObserver(handleMediaChange)
    : null;
  mutationObserver?.observe(doc.documentElement, { attributes: true, attributeFilter: ['data-theme', 'class'] });

  container.addEventListener('pointermove', handlePointerMove, { passive: true });
  container.addEventListener('pointerleave', handlePointerLeave, { passive: true });
  container.addEventListener('selectstart', handleSelectStart);
  doc.addEventListener('visibilitychange', handleVisibilityChange);
  addMediaListener(reducedMotionQuery, handleMediaChange);
  addMediaListener(pointerFineQuery, handleMediaChange);
  addMediaListener(lightSchemeQuery, handleMediaChange);

  resize();

  return {
    destroy() {
      if (destroyed) return;
      destroyed = true;
      if (frameId) win.cancelAnimationFrame(frameId);
      resizeObserver?.disconnect();
      mutationObserver?.disconnect();
      container.removeEventListener('pointermove', handlePointerMove);
      container.removeEventListener('pointerleave', handlePointerLeave);
      container.removeEventListener('selectstart', handleSelectStart);
      doc.removeEventListener('visibilitychange', handleVisibilityChange);
      removeMediaListener(reducedMotionQuery, handleMediaChange);
      removeMediaListener(pointerFineQuery, handleMediaChange);
      removeMediaListener(lightSchemeQuery, handleMediaChange);
      canvas.remove();
      container.style.position = previousPosition;
    },
  };

  function isLightTheme() {
    const theme = doc.documentElement?.dataset?.theme;
    const activeTheme = ['dark', 'light', 'system'].includes(options.theme) ? options.theme : theme;
    return resolveLightTheme(activeTheme, lightSchemeQuery.matches, options.followSystemLight);
  }
}

function hashSeed(seed) {
  const text = String(seed);
  let hash = 2166136261;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function lowFrequencyValueNoise(x, y, seed) {
  const x0 = Math.floor(x);
  const y0 = Math.floor(y);
  const tx = smoothstep(x - x0);
  const ty = smoothstep(y - y0);
  const a = latticeValue(x0, y0, seed);
  const b = latticeValue(x0 + 1, y0, seed);
  const c = latticeValue(x0, y0 + 1, seed);
  const d = latticeValue(x0 + 1, y0 + 1, seed);
  return lerp(lerp(a, b, tx), lerp(c, d, tx), ty);
}

function latticeValue(x, y, seed) {
  let value = hashSeed(`${seed}:${x}:${y}`);
  value ^= value << 13;
  value ^= value >>> 17;
  value ^= value << 5;
  return (value >>> 0) / 4294967295;
}

function createStar(x, y, random, kind) {
  const bright = random() > 0.88;
  const color = COLOR_PALETTE[Math.floor(random() * COLOR_PALETTE.length)] || COLOR_PALETTE[0];
  return {
    baseAlpha: bright ? 0.68 + random() * 0.24 : 0.28 + random() * 0.38,
    color,
    driftPhase: random() * TAU,
    driftX: (random() - 0.5) * (kind === 'cluster' ? 1.6 : 0.8),
    driftY: (random() - 0.5) * (kind === 'cluster' ? 1.0 : 0.6),
    flare: bright && random() > 0.62,
    kind,
    radius: bright ? 1.15 + random() * 0.85 : 0.55 + random() * 0.85,
    twinklePhase: random() * TAU,
    twinkleSpeed: 0.00018 + random() * 0.00062,
    x,
    y,
  };
}

function normalizePointerInput(input) {
  const pointer = input.pointer || input;
  const x = Number(pointer.x);
  const y = Number(pointer.y);
  return {
    active: pointer.active === true && Number.isFinite(x) && Number.isFinite(y),
    x,
    y,
  };
}

function ageWakes(wakes, deltaMs) {
  return wakes
    .map((wake) => ({ ...wake, ageMs: wake.ageMs + deltaMs, radius: wake.radius + deltaMs * 0.018 }))
    .filter((wake) => wake.ageMs < wake.lifeMs);
}

function ageSparks(sparks, deltaMs) {
  const seconds = deltaMs / 1000;
  return sparks
    .map((spark) => ({
      ...spark,
      ageMs: spark.ageMs + deltaMs,
      x: spark.x + spark.vx * seconds,
      y: spark.y + spark.vy * seconds,
    }))
    .filter((spark) => spark.ageMs < spark.lifeMs);
}

function createSparkBurst(x, y, state) {
  const sparks = [];
  for (let index = 0; index < state.sparkBurst; index += 1) {
    const angle = state.random() * TAU;
    const speed = 10 + state.random() * 34;
    sparks.push({
      ageMs: 0,
      hue: state.random() > 0.48 ? '142, 122, 255' : '89, 177, 255',
      lifeMs: state.sparkLifeMs * (0.65 + state.random() * 0.55),
      radius: 0.8 + state.random() * 1.8,
      vx: Math.cos(angle) * speed,
      vy: Math.sin(angle) * speed,
      x: x + (state.random() - 0.5) * 16,
      y: y + (state.random() - 0.5) * 16,
    });
  }
  return sparks;
}

function capItems(items, maxItems) {
  if (maxItems <= 0) return [];
  return items.length > maxItems ? items.slice(items.length - maxItems) : items;
}

function drawFog(context, width, height, wakes) {
  const gradient = context.createRadialGradient(width * 0.5, height * 0.34, 0, width * 0.5, height * 0.34, Math.max(width, height) * 0.72);
  gradient.addColorStop(0, 'rgba(18, 28, 50, 0.16)');
  gradient.addColorStop(0.58, 'rgba(8, 12, 24, 0.22)');
  gradient.addColorStop(1, 'rgba(4, 6, 12, 0.3)');
  context.globalCompositeOperation = 'source-over';
  context.fillStyle = gradient;
  context.fillRect(0, 0, width, height);

  for (const wake of wakes) {
    const progress = wake.ageMs / wake.lifeMs;
    const alpha = (1 - progress) * 0.72;
    const clear = context.createRadialGradient(wake.x, wake.y, 0, wake.x, wake.y, wake.radius);
    clear.addColorStop(0, `rgba(255,255,255,${alpha})`);
    clear.addColorStop(1, 'rgba(255,255,255,0)');
    context.globalCompositeOperation = 'destination-out';
    context.fillStyle = clear;
    context.beginPath();
    context.arc(wake.x, wake.y, wake.radius, 0, TAU);
    context.fill();
  }
  context.globalCompositeOperation = 'source-over';
}

function drawStars(context, stars, now, animated) {
  for (const star of stars) {
    const time = animated ? now : 0;
    const breath = animated ? 0.78 + Math.sin(time * star.twinkleSpeed + star.twinklePhase) * 0.22 : 0.84;
    const drift = animated ? Math.sin(time * 0.00013 + star.driftPhase) : 0;
    const x = star.x + drift * star.driftX;
    const y = star.y + drift * star.driftY;
    const alpha = clamp(star.baseAlpha * breath, 0, 1);
    context.fillStyle = `rgba(${star.color.core}, ${alpha})`;
    context.shadowColor = `rgba(${star.color.glow}, ${alpha * 0.55})`;
    context.shadowBlur = star.radius * 5;
    context.beginPath();
    context.arc(x, y, star.radius, 0, TAU);
    context.fill();
    if (star.flare && animated && Math.sin(time * 0.00034 + star.twinklePhase) > 0.92) {
      context.strokeStyle = `rgba(${star.color.core}, ${alpha * 0.52})`;
      context.lineWidth = 0.7;
      context.beginPath();
      context.moveTo(x - star.radius * 4, y);
      context.lineTo(x + star.radius * 4, y);
      context.moveTo(x, y - star.radius * 4);
      context.lineTo(x, y + star.radius * 4);
      context.stroke();
    }
  }
  context.shadowBlur = 0;
}

function drawSparks(context, sparks) {
  for (const spark of sparks) {
    const progress = spark.ageMs / spark.lifeMs;
    const alpha = Math.max(0, 1 - progress);
    context.fillStyle = `rgba(${spark.hue}, ${alpha * 0.86})`;
    context.shadowColor = `rgba(${spark.hue}, ${alpha})`;
    context.shadowBlur = spark.radius * 7;
    context.beginPath();
    context.arc(spark.x, spark.y, spark.radius * (1 + progress * 0.8), 0, TAU);
    context.fill();
  }
  context.shadowBlur = 0;
}

function createMediaQuery(win, query) {
  return typeof win.matchMedia === 'function'
    ? win.matchMedia(query)
    : { addEventListener() {}, matches: false, removeEventListener() {} };
}

function addMediaListener(query, listener) {
  if (typeof query.addEventListener === 'function') query.addEventListener('change', listener);
  else if (typeof query.addListener === 'function') query.addListener(listener);
}

function removeMediaListener(query, listener) {
  if (typeof query.removeEventListener === 'function') query.removeEventListener('change', listener);
  else if (typeof query.removeListener === 'function') query.removeListener(listener);
}

function isTextInput(target) {
  return Boolean(target?.closest?.('input, textarea, select, [contenteditable=""], [contenteditable="true"]'));
}

function inside(x, y, width, height) {
  return x >= 0 && x <= width && y >= 0 && y <= height;
}

function smoothstep(value) {
  const t = clamp(value, 0, 1);
  return t * t * (3 - 2 * t);
}

function lerp(a, b, t) {
  return a + (b - a) * t;
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}
