import test from 'node:test';
import assert from 'node:assert/strict';

import {
  ACCESS_LEVELS,
  buildRunAgentInput,
  errorFromAguiEvent,
  filterAgents,
  getAgentPrimaryAction,
  getAgentPrimaryHref,
  getAgentSecondaryHref,
  HUB_API,
  normalizeAccessLevel,
  normalizeAgent,
  normalizeModelProfile,
  normalizeModelProfilesPayload,
  normalizeProfileModels,
  parseSseBuffer,
  renderMarkdownSafe,
  safeUrl,
  validateManifest,
} from '../web/hub-core.js';

global.window = {
  location: { origin: 'https://hub.example.edu.cn' },
};

test('normalizes access levels from public manifest mode and platform featured flag', () => {
  assert.equal(normalizeAccessLevel({ integration: { mode: 'link' } }), 'link');
  assert.equal(normalizeAccessLevel({ active_version: { manifest: { integration: { mode: 'connected' } } } }), 'connected');
  assert.equal(normalizeAccessLevel({ featured: true, integration: { mode: 'connected' } }), 'featured');
});

test('developer manifest validation rejects self-declared featured and missing endpoints', () => {
  const invalid = validateManifest({
    schema_version: '1.0',
    id: 'bad-featured',
    name: '自声明 Featured',
    description: '这个 Manifest 不应该允许开发者自己声明 Featured。',
    version: '0.1.0',
    owner: 'demo',
    category: 'demo',
    integration: { mode: 'featured', launch_url: 'https://example.edu.cn/' },
    capabilities: [],
  });
  assert.equal(invalid.ok, false);
  assert.match(invalid.errors.join('\n'), /link 或 connected/);

  const connected = validateManifest({
    schema_version: '1.0',
    id: 'connected-demo',
    name: '标准接入',
    description: '缺少标准接入端点时应该失败。',
    version: '0.1.0',
    owner: 'demo',
    category: 'demo',
    integration: { mode: 'connected', launch_url: 'https://example.edu.cn/' },
    capabilities: [],
  });
  assert.equal(connected.ok, false);
  assert.match(connected.errors.join('\n'), /chat_endpoint/);
  assert.match(connected.errors.join('\n'), /health_endpoint/);
});

test('agent CTA contract distinguishes link launch, connected chat and featured workspace', () => {
  const link = normalizeAgent({ id: 'map', integration: { mode: 'link', launch_url: 'https://example.edu.cn/' } });
  const connected = normalizeAgent({ id: 'chat', integration: { mode: 'connected' } });
  const featured = normalizeAgent({ id: 'full', featured: true, integration: { mode: 'connected' } });

  assert.equal(ACCESS_LEVELS[normalizeAccessLevel(link)].primary, '打开应用');
  assert.deepEqual(getAgentPrimaryAction(link), {
    kind: 'launch',
    href: '/api/agents/map/launch',
    label: '打开应用',
    external: true,
  });
  assert.equal(getAgentPrimaryAction(connected).kind, 'chat');
  assert.equal(getAgentPrimaryAction(featured).kind, 'workspace');
  assert.equal(getAgentPrimaryHref(link), '/api/agents/map/launch');
  assert.equal(getAgentPrimaryHref(connected), '/hub/agents/chat/chat');
  assert.equal(getAgentPrimaryHref(featured), '');
  assert.equal(getAgentSecondaryHref(featured), '/hub/agents/full');
});

test('safe markdown renderer escapes html, strips javascript links, and preserves math fallback', () => {
  const html = renderMarkdownSafe([
    '# 标题',
    '<img src=x onerror=alert(1)>',
    '[bad](javascript:alert(1))',
    '[ok](https://example.edu.cn/path)',
    '\\(q_t\\)',
    '\\[f^\\top L f\\]',
  ].join('\n'));

  assert.doesNotMatch(html, /<img/i);
  assert.doesNotMatch(html, /javascript:/i);
  assert.match(html, /&lt;img/);
  assert.match(html, /href="https:\/\/example\.edu\.cn\/path"/);
  assert.match(html, /class="math"/);
  assert.match(html, /class="math math--block"/);
});

test('SSE parser reads data JSON frames and reports protocol errors safely', () => {
  const parsed = parseSseBuffer([
    'event: ignored',
    'data: {"type":"RUN_STARTED"}',
    '',
    'data: {"type":"TEXT_MESSAGE_CONTENT","delta":"你好"}',
    '',
    'data: {bad json}',
    '',
    'data: {"type":"RUN_FINISHED"}',
    '',
    'data: {"type":"TEXT_MESSAGE_CONTENT","delta":"partial"}',
  ].join('\n'));

  assert.equal(parsed.events.length, 4);
  assert.equal(parsed.events[0].type, 'RUN_STARTED');
  assert.equal(parsed.events[1].delta, '你好');
  assert.equal(parsed.events[2].type, 'RUN_ERROR');
  assert.equal(parsed.rest, 'data: {"type":"TEXT_MESSAGE_CONTENT","delta":"partial"}');
});

test('AG-UI run errors preserve a safe code for the chat failure state', () => {
  const error = errorFromAguiEvent({
    type: 'RUN_ERROR',
    code: 'agent_timeout',
    message: 'internal detail must not replace the public mapping',
  });

  assert.equal(error.code, 'agent_timeout');
  assert.equal(error.message, 'internal detail must not replace the public mapping');

  const malformed = errorFromAguiEvent({ type: 'RUN_ERROR', error: { code: 'protocol_error' } });
  assert.equal(malformed.code, 'protocol_error');
  assert.equal(malformed.message, 'Agent 返回的协议事件不完整或格式不正确。');
});

test('RunAgentInput preserves AG-UI field names and minimal identity context', () => {
  const input = buildRunAgentInput({
    agentId: 'hanhai-course-agent',
    user: { id: 'demo-c', name: '学生 demo-c', role: 'user' },
    threadId: 'thread-1',
    runId: 'run-1',
    messages: [{ role: 'user', content: '你好' }],
  });

  assert.deepEqual(Object.keys(input), ['threadId', 'runId', 'state', 'messages', 'tools', 'context', 'forwardedProps']);
  assert.equal(Array.isArray(input.context), true);
  assert.equal(input.messages[0].id, 'run-1-message-1');
  assert.equal(input.messages[0].role, 'user');
  const contextValue = JSON.parse(input.context[0].value);
  assert.equal(contextValue.agent_id, 'hanhai-course-agent');
  assert.equal(contextValue.user_id, 'demo-c');
  assert.equal(input.forwardedProps.contract_version, '1.0');
});

test('filtering searches query, category, access level, tags and capabilities', () => {
  const agents = [
    { id: 'a', name: '瀚海行', category: '学习助手', tags: ['课程资料'], capabilities: ['streaming'], featured: true, integration: { mode: 'connected' } },
    { id: 'b', name: '地图', category: '校园生活', tags: ['地图'], capabilities: ['external-link'], integration: { mode: 'link' } },
  ];

  assert.equal(filterAgents(agents, { query: '课程' }).length, 1);
  assert.equal(filterAgents(agents, { category: '校园生活' })[0].id, 'b');
  assert.equal(filterAgents(agents, { level: 'Featured Agent' })[0].id, 'a');
  assert.equal(filterAgents(agents, { chip: 'external-link' })[0].id, 'b');
});

test('safeUrl blocks active content schemes and accepts http(s)/relative URLs', () => {
  assert.equal(safeUrl('javascript:alert(1)'), '');
  assert.equal(safeUrl('data:text/html,<script>'), '');
  assert.equal(safeUrl('/hub'), 'https://hub.example.edu.cn/hub');
  assert.equal(safeUrl('https://example.edu.cn/'), 'https://example.edu.cn/');
});

test('model profile API constants use the approved T4 contract paths', () => {
  assert.equal(HUB_API.modelProfiles, '/api/model-profiles');
  assert.equal(HUB_API.modelProfile('p/1'), '/api/model-profiles/p%2F1');
  assert.equal(HUB_API.modelProfileTest('p1'), '/api/model-profiles/p1/test');
  assert.equal(HUB_API.modelProfileDiscover('p1'), '/api/model-profiles/p1/discover');
  assert.equal(HUB_API.modelBindings, '/api/model-bindings');
  assert.equal(HUB_API.modelBindingGlobal, '/api/model-bindings/global');
  assert.equal(HUB_API.modelBindingAgent('hanhai-course-agent'), '/api/model-bindings/agents/hanhai-course-agent');
  assert.equal(HUB_API.homeAssistant, '/api/home-assistant/chat');
});

test('normalizes model profiles from wrapped payloads without exposing raw keys', () => {
  const normalized = normalizeModelProfilesPayload({
    data: {
      profiles: [{
        profile_id: 'p1',
        label: 'GPT 主力',
        provider: 'openai',
        apiStyle: 'responses',
        baseUrl: 'https://api.example/v1',
        hasApiKey: true,
        apiKeyFingerprint: 'fp_1234',
        models: { data: [{ id: 'gpt-5.6', displayName: 'GPT 5.6' }] },
        default_model: 'gpt-5.6',
      }],
      bindings: [{ scope_type: 'global', profile_id: 'p1', model_id: 'gpt-5.6' }],
    },
  });

  assert.equal(normalized.profiles.length, 1);
  assert.equal(normalized.profiles[0].id, 'p1');
  assert.equal(normalized.profiles[0].name, 'GPT 主力');
  assert.equal(normalized.profiles[0].base_url, 'https://api.example/v1');
  assert.equal(normalized.profiles[0].has_api_key, true);
  assert.equal(normalized.profiles[0].api_key_fingerprint, 'fp_1234');
  assert.equal(normalized.profiles[0].models[0].id, 'gpt-5.6');
  assert.equal(normalized.bindings.global.profile_id, 'p1');
});

test('normalizes model bindings from object and model lists from strings', () => {
  const models = normalizeProfileModels(['deepseek-chat', { model_id: 'deepseek-reasoner', chatEligible: false }]);
  assert.deepEqual(models.map((model) => model.id), ['deepseek-chat', 'deepseek-reasoner']);
  assert.equal(models[1].chat_eligible, false);

  const normalized = normalizeModelProfilesPayload({
    profiles: [{ id: 'p2', name: 'DeepSeek', models }],
    bindings: {
      global: { profileId: 'p2', modelId: 'deepseek-chat' },
      agents: {
        'hanhai-course-agent': { profileId: 'p2', modelId: 'deepseek-reasoner' },
      },
    },
  });

  assert.equal(normalized.bindings.global.profile_id, 'p2');
  assert.equal(normalized.bindings.agents['hanhai-course-agent'].model_id, 'deepseek-reasoner');
});

test('normalizes nested bindings returned by the Hub backend', () => {
  const normalized = normalizeModelProfilesPayload({
    profiles: [{ id: 'p1', name: 'GPT', models: ['gpt-5.6-sol'] }],
    bindings: {
      global: {
        scope_type: 'global',
        scope_id: 'global',
        binding: { profile_id: 'p1', model_id: 'gpt-5.6-sol' },
      },
      agents: [{
        scope_type: 'agent',
        scope_id: 'hanhai-course-agent',
        agent_id: 'hanhai-course-agent',
        binding: { profile_id: 'p1', model_id: 'gpt-5.6-sol' },
      }],
    },
  });

  assert.equal(normalized.bindings.global.profile_id, 'p1');
  assert.equal(normalized.bindings.global.model_id, 'gpt-5.6-sol');
  assert.equal(normalized.bindings.agents['hanhai-course-agent'].profile_id, 'p1');
  assert.equal(normalized.bindings.agents['hanhai-course-agent'].model_id, 'gpt-5.6-sol');
});

test('model profile normalization never creates an api_key field for display', () => {
  const profile = normalizeModelProfile({
    id: 'p3',
    name: 'Masked',
    api_key_mask: 'sk-••••1234',
    api_key: 'sk-raw-secret-should-not-be-used-by-ui',
  });
  assert.equal(profile.api_key_mask, 'sk-••••1234');
  assert.equal(Object.hasOwn(profile, 'api_key'), false);
  assert.equal(Object.hasOwn(profile, 'apiKey'), false);
  assert.equal(profile.has_api_key, true);
});
