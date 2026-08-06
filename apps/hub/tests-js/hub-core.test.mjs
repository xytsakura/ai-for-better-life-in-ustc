import test from 'node:test';
import assert from 'node:assert/strict';

import {
  ACCESS_LEVELS,
  buildRunAgentInput,
  errorFromAguiEvent,
  filterAgents,
  getAgentPrimaryHref,
  getAgentSecondaryHref,
  normalizeAccessLevel,
  normalizeAgent,
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

test('agent CTA contract distinguishes link launch from connected chat', () => {
  const link = normalizeAgent({ id: 'map', integration: { mode: 'link', launch_url: 'https://example.edu.cn/' } });
  const connected = normalizeAgent({ id: 'chat', integration: { mode: 'connected' } });
  const featured = normalizeAgent({ id: 'full', featured: true, integration: { mode: 'connected' } });

  assert.equal(ACCESS_LEVELS[normalizeAccessLevel(link)].primary, '打开应用');
  assert.equal(getAgentPrimaryHref(link), '/api/agents/map/launch');
  assert.equal(getAgentPrimaryHref(connected), '/hub/agents/chat/chat');
  assert.equal(getAgentPrimaryHref(featured), '/hub/agents/full/chat');
  assert.equal(getAgentSecondaryHref(featured), '');
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
