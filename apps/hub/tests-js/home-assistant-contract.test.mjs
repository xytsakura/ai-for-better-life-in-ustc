import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const appSource = readFileSync(resolve(__dirname, '../web/app.js'), 'utf8');
const coreSource = readFileSync(resolve(__dirname, '../web/hub-core.js'), 'utf8');
const stylesSource = readFileSync(resolve(__dirname, '../web/styles.css'), 'utf8');
const indexSource = readFileSync(resolve(__dirname, '../web/index.html'), 'utf8');

test('homepage assistant exposes separate direct and routing modes', () => {
  assert.match(coreSource, /homeAssistant:\s*'\/api\/home-assistant\/chat'/);
  assert.match(appSource, /label: '普通对话'/);
  assert.match(appSource, /label: '需求分析路由'/);
  assert.match(appSource, /requestMode: 'instant'/);
  assert.match(appSource, /requestMode: 'route_stream'/);
  assert.match(appSource, /data-assistant-mode=/);
  assert.match(appSource, /switchPortalAssistantMode/);
  assert.match(appSource, /'Accept': 'text\/event-stream'/);
  assert.match(appSource, /event\.type === 'model\.output_text\.delta'/);
  assert.match(appSource, /event\.type === 'home\.recommendation'/);
  assert.match(appSource, /event\.type === 'home\.completed'/);
  assert.match(appSource, /!completed/);
  assert.doesNotMatch(appSource, /response\.json\(\).*assistantMessage\.recommendation/s);
  assert.match(appSource, /sendPortalAssistantMessage/);
  assert.match(appSource, /has-conversation/);
  assert.match(appSource, /有什么想聊的？我会快速回应/);
  assert.match(appSource, /欢迎说出你的需求，我会分析并推荐合适的平台 Agent/);
  assert.match(appSource, /home.recommendation/);
  assert.match(appSource, /这个 Agent 很适合你，欢迎去那个 Agent 里面进行深度的交流探索/);
});

test('homepage assistant persists identity-scoped conversation archives', () => {
  assert.match(appSource, /conversations: 'hub_assistant_conversations'/);
  assert.match(appSource, /localStorage\.setItem\(STORAGE\.conversations/);
  assert.match(appSource, /portalAssistantWorkspaceKey/);
  assert.match(appSource, /`\$\{userId\}:\$\{mode\}`/);
  assert.match(appSource, /normalizedStoredPortalArchives/);
  assert.match(appSource, /splitLegacyPortalArchives/);
  assert.match(appSource, /message\?\.recommendation/);
  assert.match(appSource, /data-new-conversation/);
  assert.match(appSource, /data-conversation-id/);
  assert.match(appSource, /openPortalConversation/);
  assert.match(appSource, /startNewPortalConversation/);
  assert.match(appSource, /target\.closest\('\[data-conversation-id\]'\)/);
  assert.match(appSource, /target\.closest\('\[data-new-conversation\]'\)/);
  assert.match(appSource, /typeof message\.content === 'string'/);
  assert.match(appSource, /rawContent === '\[object Object\]'/);
  assert.match(appSource, /visibleContent = typeof message\.content === 'string'/);
  assert.match(appSource, /if \(!content && !recommendation\) return null/);
  assert.match(appSource, /isPendingPlaceholder = session\.pending/);
  assert.match(appSource, /!assistantMessage\.content\.trim\(\) && !assistantMessage\.recommendation/);
  assert.match(indexSource, /aria-label="对话存档"/);
  assert.match(indexSource, /conversationArchiveMode/);
  assert.match(stylesSource, /\.hub-conversations/);
});

test('route recommendations activate a validated agent id instead of trusting a model URL', () => {
  assert.match(appSource, /data-route-agent-id/);
  assert.match(appSource, /activateAgentById\(button\.dataset\.routeAgentId\)/);
  assert.match(appSource, /推荐理由：/);
  assert.doesNotMatch(appSource, /recommendation\.(url|href|launch_url)/);
});

test('assistant UI includes bounded history, cancel state and responsive recommendation layout', () => {
  assert.match(appSource, /\.slice\(-11\)/);
  assert.match(appSource, /AbortController/);
  assert.match(appSource, /data-assistant-cancel/);
  assert.match(stylesSource, /\.portal-assistant__messages/);
  assert.match(stylesSource, /\.portal-agent-recommendation/);
  assert.match(stylesSource, /\.portal-stage\.has-conversation/);
  assert.match(stylesSource, /conversation surface/);
  assert.match(stylesSource, /@media \(max-width: 640px\)/);
  assert.match(indexSource, /rel="icon" href="\/assets\/ustc-emblem\.jpg"/);
});
