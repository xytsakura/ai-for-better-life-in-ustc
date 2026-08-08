import test from 'node:test';
import assert from 'node:assert/strict';

import {
  createInteractionState,
  createSeededRandom,
  generateStarField,
  resolveLightTheme,
  sampleGaussianPair,
  shouldAnimate,
  stepInteraction,
} from '../web/starfield.js';

test('theme resolution defaults invalid values to dark', () => {
  assert.equal(resolveLightTheme('dark', true), false);
  assert.equal(resolveLightTheme('light', false), true);
  assert.equal(resolveLightTheme('system', true), true);
  assert.equal(resolveLightTheme('system', true, false), false);
  assert.equal(resolveLightTheme('sepia', true), false);
  assert.equal(resolveLightTheme(undefined, true), false);
});

test('seeded random and gaussian sampling are reproducible', () => {
  const first = createSeededRandom('fixed-hub-seed');
  const second = createSeededRandom('fixed-hub-seed');
  const other = createSeededRandom('other-hub-seed');

  const firstValues = Array.from({ length: 8 }, () => first());
  const secondValues = Array.from({ length: 8 }, () => second());
  const otherValues = Array.from({ length: 8 }, () => other());

  assert.deepEqual(firstValues, secondValues);
  assert.notDeepEqual(firstValues, otherValues);
  assert.ok(firstValues.every((value) => value >= 0 && value < 1));

  const gaussA = sampleGaussianPair(createSeededRandom('gaussian'));
  const gaussB = sampleGaussianPair(createSeededRandom('gaussian'));
  assert.deepEqual(gaussA, gaussB);
  assert.ok(gaussA.every(Number.isFinite));
});

test('star field generation is deterministic for the same seed', () => {
  const options = { density: 0.00042, height: 640, seed: 'stable-field', width: 960 };
  const first = generateStarField(options);
  const second = generateStarField(options);
  const third = generateStarField({ ...options, seed: 'different-field' });

  assert.deepEqual(first, second);
  assert.notDeepEqual(first.stars.slice(0, 12), third.stars.slice(0, 12));
});

test('elliptical gaussian cluster is denser near the center than at the edges', () => {
  const field = generateStarField({
    density: 0.00072,
    height: 640,
    maxStars: 520,
    seed: 'density-check',
    width: 1000,
  });

  const center = field.stars.filter((star) => (
    star.x >= 350 && star.x <= 650 && star.y >= 150 && star.y <= 350
  )).length;
  const edge = field.stars.filter((star) => star.x <= 150 || star.x >= 850).length;

  const centerDensity = center / (300 * 200);
  const edgeDensity = edge / (300 * 640);
  assert.ok(centerDensity > edgeDensity * 2.4, `center=${centerDensity} edge=${edgeDensity}`);
});

test('generated stars stay inside canvas bounds with valid visual attributes', () => {
  const field = generateStarField({ density: 0.001, height: 111, maxStars: 80, seed: 'bounds', width: 177 });

  assert.ok(field.stars.length <= 80);
  assert.ok(field.stars.length > 0);
  for (const star of field.stars) {
    assert.ok(star.x >= 0 && star.x <= field.width);
    assert.ok(star.y >= 0 && star.y <= field.height);
    assert.ok(star.radius > 0);
    assert.ok(star.baseAlpha > 0 && star.baseAlpha <= 1);
    assert.ok(['cluster', 'outer'].includes(star.kind));
  }
});

test('interaction state enforces particle caps and decay', () => {
  let state = createInteractionState({
    maxSparks: 3,
    maxWakes: 2,
    minWakeDistance: 0,
    minWakeIntervalMs: 0,
    seed: 'interaction',
    sparkBurst: 4,
    sparkLifeMs: 80,
    wakeLifeMs: 80,
  });

  for (let index = 0; index < 6; index += 1) {
    state = stepInteraction(state, { active: true, moved: true, x: 20 + index * 8, y: 30 }, 16);
    assert.ok(state.wakes.length <= 2);
    assert.ok(state.sparks.length <= 3);
  }

  assert.equal(state.wakes.length, 2);
  assert.equal(state.sparks.length, 3);

  state = stepInteraction(state, { active: false, x: 0, y: 0 }, 120);
  assert.equal(state.wakes.length, 0);
  assert.equal(state.sparks.length, 0);
});

test('stationary pointer creates only low-frequency spark activity', () => {
  let state = createInteractionState({
    maxSparks: 8,
    maxWakes: 4,
    minWakeDistance: 10,
    seed: 'stationary',
    sparkBurst: 4,
    stationarySparkIntervalMs: 100,
  });

  state = stepInteraction(state, { active: true, moved: true, x: 40, y: 40 }, 16);
  const initialSparks = state.sparks.length;
  state = stepInteraction(state, { active: true, x: 41, y: 41 }, 40);
  assert.equal(state.sparks.length, initialSparks);

  state = stepInteraction(state, { active: true, x: 41, y: 41 }, 80);
  assert.ok(state.sparks.length > initialSparks);
  assert.equal(state.wakes.length, 1);
});

test('shouldAnimate disables continuous motion for reduced motion, hidden documents and coarse pointers', () => {
  assert.equal(shouldAnimate(false, true, true), true);
  assert.equal(shouldAnimate(true, true, true), false);
  assert.equal(shouldAnimate(false, false, true), false);
  assert.equal(shouldAnimate(false, true, false), false);
});
