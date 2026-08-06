import {
  ACCESS_LEVELS,
  DEMO_USERS,
  ERROR_MESSAGES,
  HUB_API,
  accessMeta,
  buildRunAgentInput,
  filterAgents,
  formatUsage,
  getAgentPrimaryHref,
  normalizeAccessLevel,
  normalizeAgent,
  parseSseBuffer,
  renderMarkdownSafe,
  safeUrl,
  validateManifest,
} from './hub-core.js';
import { mountStarfield } from './starfield.js';

const STORAGE = Object.freeze({
  theme: 'hub_theme',
  user: 'hub_demo_user',
  recent: 'hub_recent_agents',
});

const state = {
  route: parseRoute(location.pathname),
  agents: [],
  adminAgents: [],
  selectedAdminAgentId: '',
  query: '',
  category: '全部',
  level: '全部',
  chip: '全部',
  user: DEMO_USERS.find((user) => user.id === localStorage.getItem(STORAGE.user)) || DEMO_USERS[2],
  loading: false,
  generation: 0,
  activeController: null,
  lastRun: null,
};

const view = document.querySelector('#view');
const shell = document.querySelector('#app');
const searchInput = document.querySelector('#globalSearch');
const identitySelect = document.querySelector('#identitySelect');
const userAvatar = document.querySelector('#userAvatar');
const themeToggle = document.querySelector('#themeToggle');
const mobileNavToggle = document.querySelector('.mobile-nav-toggle');
let portalStarfield = null;

init();

function init() {
  applyTheme();
  bindGlobalEvents();
  populateIdentity();
  setUser(state.user);
  navigate(location.pathname, { replace: true });
}

function bindGlobalEvents() {
  document.body.addEventListener('click', (event) => {
    const link = event.target.closest('[data-link]');
    if (!link) return;
    const url = new URL(link.href, location.origin);
    if (url.origin !== location.origin) return;
    event.preventDefault();
    navigate(url.pathname + url.search);
  });

  window.addEventListener('popstate', () => {
    state.route = parseRoute(location.pathname);
    render();
  });

  searchInput?.addEventListener('input', () => {
    state.query = searchInput.value;
    if (state.route.name !== 'portal') {
      navigate('/hub');
      return;
    }
    syncSearchInputs(searchInput);
    updateAgentGrid();
  });

  identitySelect?.addEventListener('change', () => {
    const next = DEMO_USERS.find((user) => user.id === identitySelect.value) || DEMO_USERS[2];
    setUser(next);
    state.generation += 1;
    if (state.activeController) state.activeController.abort();
    toast(`已切换为 ${next.name}。旧的流式响应不会写入新身份界面。`);
    render();
  });

  themeToggle?.addEventListener('click', () => {
    const current = document.documentElement.dataset.theme || 'dark';
    const next = current === 'dark' ? 'light' : current === 'light' ? 'system' : 'dark';
    localStorage.setItem(STORAGE.theme, next);
    document.documentElement.dataset.theme = next;
    themeToggle.textContent = next === 'dark' ? '深色模式' : next === 'light' ? '浅色模式' : '跟随系统';
    if (state.route.name === 'portal') mountPortalEffects();
  });

  mobileNavToggle?.addEventListener('click', () => shell.classList.toggle('nav-open'));
}

function applyTheme() {
  const theme = localStorage.getItem(STORAGE.theme) || 'dark';
  document.documentElement.dataset.theme = theme;
  if (themeToggle) {
    themeToggle.textContent = theme === 'dark' ? '深色模式' : theme === 'light' ? '浅色模式' : '跟随系统';
  }
}

function populateIdentity() {
  if (!identitySelect) return;
  identitySelect.innerHTML = DEMO_USERS.map((user) => (
    `<option value="${escapeAttr(user.id)}">${escapeHtml(user.name)}</option>`
  )).join('');
}

function setUser(user) {
  state.user = user;
  localStorage.setItem(STORAGE.user, user.id);
  if (identitySelect) identitySelect.value = user.id;
  if (userAvatar) userAvatar.textContent = user.initials;
  document.querySelectorAll('[data-admin-only]').forEach((item) => {
    item.hidden = user.role !== 'admin';
  });
}

function navigate(path, { replace = false } = {}) {
  const normalized = path.startsWith('/hub') ? path : '/hub';
  if (replace) history.replaceState({}, '', normalized);
  else history.pushState({}, '', normalized);
  state.route = parseRoute(normalized);
  shell?.classList.remove('nav-open');
  render();
}

function parseRoute(pathname) {
  const path = pathname.replace(/\/+$/, '') || '/hub';
  const segments = path.split('/').filter(Boolean);
  if (segments[0] !== 'hub') return { name: 'portal' };
  if (segments.length === 1) return { name: 'portal' };
  if (segments[1] === 'recent') return { name: 'recent' };
  if (segments[1] === 'submit') return { name: 'submit' };
  if (segments[1] === 'admin') return { name: 'admin' };
  if (segments[1] === 'agents' && segments[2] && segments[3] === 'chat') return { name: 'chat', id: decodeURIComponent(segments[2]) };
  if (segments[1] === 'agents' && segments[2]) return { name: 'detail', id: decodeURIComponent(segments[2]) };
  return { name: 'portal' };
}

function render() {
  destroyPortalEffects();
  syncNav();
  if (!view) return;
  view.focus({ preventScroll: true });
  if (state.route.name === 'portal') return renderPortal();
  if (state.route.name === 'recent') return renderRecent();
  if (state.route.name === 'detail') return renderDetail(state.route.id);
  if (state.route.name === 'chat') return renderChat(state.route.id);
  if (state.route.name === 'submit') return renderSubmit();
  if (state.route.name === 'admin') return renderAdmin();
}

function syncNav() {
  document.querySelectorAll('[data-nav]').forEach((item) => {
    const key = item.getAttribute('data-nav');
    const active = (
      (state.route.name === 'portal' && key === 'portal') ||
      (state.route.name === 'recent' && key === 'recent') ||
      (state.route.name === 'submit' && key === 'submit') ||
      (state.route.name === 'admin' && key === 'admin')
    );
    if (active) item.setAttribute('aria-current', 'page');
    else item.removeAttribute('aria-current');
  });
  document.body.dataset.route = state.route.name;
}

function syncSearchInputs(source) {
  [searchInput, document.querySelector('#portalSearch')].forEach((input) => {
    if (input && input !== source && input.value !== state.query) input.value = state.query;
  });
}

function isDarkSurface() {
  const theme = document.documentElement.dataset.theme || 'dark';
  if (theme === 'light') return false;
  if (theme === 'system') return window.matchMedia('(prefers-color-scheme: dark)').matches;
  return true;
}

function mountPortalEffects() {
  destroyPortalEffects();
  const container = document.querySelector('[data-starfield]');
  if (!container || !isDarkSurface()) return;
  portalStarfield = mountStarfield(container, { seed: 20260806, density: 0.00042 });
}

function destroyPortalEffects() {
  portalStarfield?.destroy?.();
  portalStarfield = null;
}

async function renderPortal() {
  state.loading = true;
  view.innerHTML = renderPortalShell('应用广场', '发现、比较并使用通过平台治理的校园 Agent。', true);
  mountPortalEffects();
  try {
    state.agents = await loadAgents();
  } catch (error) {
    view.innerHTML = renderPortalShell('应用广场', '发现、比较并使用通过平台治理的校园 Agent。', false);
    mountPortalEffects();
    document.querySelector('#agentGrid').innerHTML = errorState('Agent 列表加载失败', readableError(error), '重试');
    document.querySelector('[data-retry]')?.addEventListener('click', renderPortal);
    return;
  } finally {
    state.loading = false;
  }
  view.innerHTML = renderPortalShell('应用广场', '发现、比较并使用通过平台治理的校园 Agent。', false);
  mountPortalEffects();
  bindFilters();
  updateAgentGrid();
}

async function renderRecent() {
  if (!state.agents.length) {
    try {
      state.agents = await loadAgents();
    } catch (error) {
      view.innerHTML = errorState('最近使用加载失败', readableError(error), '返回广场', '/hub');
      return;
    }
  }
  const recentIds = loadJson(STORAGE.recent, []);
  const agents = state.agents.filter((agent) => recentIds.includes(agent.id));
  view.innerHTML = `
    <section class="hero">
      <div>
        <p class="eyebrow">RECENT</p>
        <h1>我的最近使用</h1>
        <p class="lead">这里保存当前浏览器身份下最近打开过的 Agent，不跨设备同步。</p>
      </div>
    </section>
    <div class="hub-grid">${agents.length ? agents.map(renderAgentCard).join('') : emptyState('还没有最近使用记录', '从 Agent 广场打开一次 Agent 后会显示在这里。')}</div>
  `;
  bindAgentCardActions();
}

function renderPortalShell(title, subtitle, loading) {
  const agents = state.agents;
  const categories = ['全部', ...unique(agents.map((agent) => normalizeAgent(agent).category))];
  const chips = ['全部', ...unique(agents.flatMap((agent) => {
    const normalized = normalizeAgent(agent);
    return [...normalized.tags, ...normalized.capabilities].slice(0, 12);
  })).slice(0, 18)];
  return `
    <section class="portal-stage" data-starfield>
      <div class="portal-stage__content">
        <p class="portal-kicker">AI FOR BETTER LIFE · USTC</p>
        <h1>今天，想解决什么校园问题？</h1>
        <p>从一个问题开始，由你选择最合适的专业 Agent。</p>
        <label class="portal-search" aria-label="搜索校园 Agent">
          <span class="portal-search__plus" aria-hidden="true">＋</span>
          <input id="portalSearch" type="search" placeholder="搜索 Agent、课程或校园服务" value="${escapeAttr(state.query)}" autocomplete="off" />
          <span class="portal-search__meta">校园 Agent</span>
          <span class="portal-search__send" aria-hidden="true">↑</span>
        </label>
      </div>
    </section>

    <section class="portal-directory" aria-label="${escapeAttr(title)}">
      <header class="portal-directory__head">
        <div><p class="eyebrow">CAMPUS AGENTS</p><h2>为你推荐</h2></div>
        <span>${agents.length ? `${agents.length} 个 Agent 已通过平台验收` : escapeHtml(subtitle)}</span>
      </header>
      <details class="filter-panel" aria-label="Agent 筛选">
        <summary>筛选 Agent</summary>
        <div class="hub-tabs" role="tablist" aria-label="分类">
          ${categories.map((category) => `<button class="tab" type="button" data-category="${escapeAttr(category)}" aria-selected="${category === state.category}">${escapeHtml(category)}</button>`).join('')}
        </div>
        <div class="hub-filters" aria-label="接入等级与能力">
          ${['全部', ACCESS_LEVELS.link.label, ACCESS_LEVELS.connected.label, ACCESS_LEVELS.featured.label].map((level) => `<button class="chip" type="button" data-level="${escapeAttr(level)}" aria-pressed="${level === state.level}">${escapeHtml(level)}</button>`).join('')}
        </div>
        <div class="hub-filters" aria-label="标签">
          ${chips.map((chip) => `<button class="chip" type="button" data-chip="${escapeAttr(chip)}" aria-pressed="${chip === state.chip}">${escapeHtml(chip)}</button>`).join('')}
        </div>
      </details>
      <section id="agentGrid" class="hub-grid" aria-live="polite">
        ${loading ? document.querySelector('#skeletonCards')?.innerHTML || '' : ''}
      </section>
    </section>
  `;
}

function bindFilters() {
  const portalSearch = document.querySelector('#portalSearch');
  portalSearch?.addEventListener('input', () => {
    state.query = portalSearch.value;
    syncSearchInputs(portalSearch);
    updateAgentGrid();
  });
  document.querySelectorAll('[data-category]').forEach((button) => {
    button.addEventListener('click', () => {
      state.category = button.dataset.category;
      updateAgentGrid();
      refreshPressedState();
    });
  });
  document.querySelectorAll('[data-level]').forEach((button) => {
    button.addEventListener('click', () => {
      state.level = button.dataset.level;
      updateAgentGrid();
      refreshPressedState();
    });
  });
  document.querySelectorAll('[data-chip]').forEach((button) => {
    button.addEventListener('click', () => {
      state.chip = button.dataset.chip;
      updateAgentGrid();
      refreshPressedState();
    });
  });
}

function refreshPressedState() {
  document.querySelectorAll('[data-category]').forEach((button) => button.setAttribute('aria-selected', String(button.dataset.category === state.category)));
  document.querySelectorAll('[data-level]').forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.level === state.level)));
  document.querySelectorAll('[data-chip]').forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.chip === state.chip)));
}

function updateAgentGrid() {
  const grid = document.querySelector('#agentGrid');
  if (!grid) return;
  const filtered = filterAgents(state.agents, {
    query: state.query,
    category: state.category,
    level: state.level,
    chip: state.chip,
  });
  grid.classList.remove('is-refreshing');
  void grid.offsetWidth;
  grid.classList.add('is-refreshing');
  grid.innerHTML = filtered.length
    ? filtered.map(renderAgentCard).join('')
    : emptyState('暂无符合条件的 Agent', '可以清空搜索或提交一个新的校园 Agent。', '<a class="button" href="/hub/submit" data-link>去提交</a>');
  bindAgentCardActions();
}

function renderAgentCard(raw) {
  const agent = normalizeAgent(raw);
  const meta = accessMeta(agent);
  const primaryHref = getAgentPrimaryHref(agent);
  return `
    <article class="hub-card hub-card--${escapeAttr(normalizeAccessLevel(agent))}" data-id="${escapeAttr(agent.id)}" tabindex="0" aria-label="${escapeAttr(agent.name)}">
      <div class="hub-card__head">
        ${renderAgentIcon(agent)}
        <div class="hub-card__titleblock">
          <h3 class="hub-card__name">${escapeHtml(agent.name)}</h3>
          <span class="hub-card__category">${escapeHtml(agent.category)} · ${escapeHtml(agent.owner)}</span>
        </div>
        <span class="badge badge--${meta.tone}">${meta.label}</span>
      </div>
      <p class="hub-card__desc">${escapeHtml(agent.description)}</p>
      <div class="hub-card__subskills">
        ${agent.capabilities.slice(0, 3).map((item) => `<span class="tag">${escapeHtml(item)}</span>`).join('')}
        ${agent.capabilities.length > 3 ? `<span class="tag">+${agent.capabilities.length - 3}</span>` : ''}
      </div>
      <div class="hub-card__foot">
        <span>${formatUsage(agent.usage_count)} 次使用</span>
        <span>${healthBadge(agent.health)} <span class="tag">v${escapeHtml(agent.version)}</span></span>
      </div>
      <div class="action-row" style="margin-top:14px">
        <a class="button" href="${escapeAttr(primaryHref)}" ${normalizeAccessLevel(agent) === 'link' ? 'target="_blank" rel="noopener noreferrer" data-external-launch' : 'data-link'} data-primary-action data-agent-id="${escapeAttr(agent.id)}">${escapeHtml(meta.primary)}</a>
        <a class="ghost-button" href="/hub/agents/${encodeURIComponent(agent.id)}" data-link>查看详情</a>
      </div>
    </article>
  `;
}

function bindAgentCardActions() {
  document.querySelectorAll('.hub-card').forEach((card) => {
    card.addEventListener('click', (event) => {
      if (event.target.closest('a,button')) return;
      navigate(`/hub/agents/${encodeURIComponent(card.dataset.id)}`);
    });
    card.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') navigate(`/hub/agents/${encodeURIComponent(card.dataset.id)}`);
    });
  });
  document.querySelectorAll('[data-primary-action]').forEach((action) => {
    action.addEventListener('click', () => rememberRecent(action.dataset.agentId));
  });
}

async function renderDetail(id) {
  view.innerHTML = skeletonDetail();
  const agent = await loadAgent(id);
  if (!agent) {
    view.innerHTML = errorState('没有找到这个 Agent', '它可能还在审核中，或当前身份没有访问权限。', '返回广场', '/hub');
    return;
  }
  const meta = accessMeta(agent);
  const level = normalizeAccessLevel(agent);
  view.innerHTML = `
    <section class="detail-layout">
      <div>
        <div class="detail-section">
          <div class="detail-title">
            ${renderAgentIcon(agent)}
            <div class="detail-title__copy">
              <p class="eyebrow">${escapeHtml(meta.label)}</p>
              <h1>${escapeHtml(agent.name)}</h1>
              <p class="lead">${escapeHtml(agent.description)}</p>
            </div>
          </div>
          <div class="action-row" style="margin-top:18px">${detailActions(agent)}</div>
        </div>

        <div class="detail-section">
          <h2>能力与适用场景</h2>
          <div class="tag-list">${agent.capabilities.map((item) => `<span class="tag">${escapeHtml(item)}</span>`).join('')}</div>
        </div>

        <div class="detail-section">
          <h2>数据与身份提示</h2>
          <p class="lead">${escapeHtml(formatDataPolicy(agent.data_policy))}</p>
        </div>
      </div>

      <aside class="panel">
        <h2>接入信息</h2>
        <dl class="kv">
          <div><dt>维护者</dt><dd>${escapeHtml(agent.owner)}</dd></div>
          <div><dt>版本</dt><dd>v${escapeHtml(agent.version)}</dd></div>
          <div><dt>分类</dt><dd>${escapeHtml(agent.category)}</dd></div>
          <div><dt>协议</dt><dd>${escapeHtml(agent.integration?.protocol || (level === 'link' ? 'external-link' : 'ag-ui'))}</dd></div>
          <div><dt>健康状态</dt><dd>${healthBadge(agent.health)} ${escapeHtml(agent.health?.checked_at || '')}</dd></div>
          <div><dt>更新</dt><dd>${escapeHtml(agent.updated_at || agent.updated_at === 0 ? String(agent.updated_at) : '未提供')}</dd></div>
        </dl>
        <div class="detail-section">
          <h3>标签</h3>
          <div class="tag-list">${agent.tags.map((item) => `<span class="tag">${escapeHtml(item)}</span>`).join('')}</div>
        </div>
      </aside>
    </section>
  `;
  bindDetailActions(agent);
}

function detailActions(agent) {
  const level = normalizeAccessLevel(agent);
  const meta = accessMeta(agent);
  const primary = level === 'link'
    ? `<a class="button" href="${escapeAttr(HUB_API.launch(agent.id))}" target="_blank" rel="noopener noreferrer">${escapeHtml(meta.primary)}</a>`
    : `<a class="button" href="/hub/agents/${encodeURIComponent(agent.id)}/chat" data-link>${escapeHtml(meta.primary)}</a>`;
  const workspace = level === 'featured'
    ? `<button class="ghost-button" type="button" data-workspace="${escapeAttr(agent.id)}">${escapeHtml(meta.secondary)}</button>`
    : '';
  return `${primary}${workspace}<a class="link-button" href="/hub" data-link>返回广场</a>`;
}

function bindDetailActions(agent) {
  document.querySelector('[data-workspace]')?.addEventListener('click', async () => {
    await openWorkspace(agent);
  });
}

async function renderChat(id) {
  const agent = await loadAgent(id);
  if (!agent || !accessMeta(agent).chatEnabled) {
    view.innerHTML = errorState('该 Agent 不支持 Hub 统一聊天', 'Link App 只能打开外部应用；只有 Connected 或 Featured Agent 可以进入统一聊天。', '查看详情', `/hub/agents/${encodeURIComponent(id)}`);
    return;
  }
  rememberRecent(id);
  state.generation += 1;
  if (state.activeController) state.activeController.abort();
  view.innerHTML = `
    <section class="chat-shell" data-chat-agent="${escapeAttr(agent.id)}">
      <header class="chat-header">
        <a class="ghost-button" href="/hub" data-link>← 返回广场</a>
        ${renderAgentIcon(agent)}
        <div class="chat-header__copy">
          <h1>${escapeHtml(agent.name)}</h1>
          <div class="small-muted">由 ${escapeHtml(agent.owner)} 提供 · ${healthBadge(agent.health)}</div>
        </div>
        ${normalizeAccessLevel(agent) === 'featured' ? `<button class="ghost-button" type="button" data-workspace="${escapeAttr(agent.id)}">进入完整工作台</button>` : ''}
      </header>
      <div id="messages" class="messages" aria-live="polite">
        <div class="message message--system">当前是 Hub 统一聊天容器。它只使用标准 AG-UI 事件，不复制 Agent 的完整工作台能力。</div>
      </div>
      <form id="composer" class="composer">
        <textarea id="prompt" placeholder="输入问题。Enter 发送，Shift+Enter 换行。" aria-label="聊天输入"></textarea>
        <button id="cancelRun" class="ghost-button" type="button" disabled>取消</button>
        <button id="sendRun" class="button" type="submit">发送</button>
      </form>
    </section>
  `;
  document.querySelector('[data-workspace]')?.addEventListener('click', () => openWorkspace(agent));
  bindComposer(agent);
}

function bindComposer(agent) {
  const form = document.querySelector('#composer');
  const prompt = document.querySelector('#prompt');
  const cancel = document.querySelector('#cancelRun');
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const text = prompt.value.trim();
    if (!text) return;
    prompt.value = '';
    sendMessage(agent, text);
  });
  prompt.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  cancel.addEventListener('click', () => {
    if (state.activeController) state.activeController.abort();
    cancel.disabled = true;
    appendSystem('已取消本轮调用。');
  });
}

async function sendMessage(agent, text) {
  const messages = document.querySelector('#messages');
  const cancel = document.querySelector('#cancelRun');
  const generation = state.generation;
  appendMessage('user', text);
  const agentMessage = appendMessage('agent', '');
  const controller = new AbortController();
  state.activeController = controller;
  cancel.disabled = false;
  const threadId = `thread-${agent.id}-${state.user.id}`;
  const runId = `run-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  const body = buildRunAgentInput({
    agentId: agent.id,
    user: state.user,
    threadId,
    runId,
    messages: [{ id: `${runId}-user`, role: 'user', content: text }],
  });
  state.lastRun = { agent, text };

  try {
    const response = await fetch(HUB_API.gatewayRun(agent.id), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'text/event-stream',
        'X-Hub-User': state.user.id,
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    if (!response.ok || !response.body) {
      throw await normalizeHttpError(response);
    }
    await consumeAguiStream(response.body, {
      generation,
      onEvent: (event) => applyAguiEvent(event, agentMessage),
    });
  } catch (error) {
    if (error.name === 'AbortError') return;
    if (generation !== state.generation) return;
    renderMessageMarkdown(agentMessage, '调用失败，本轮没有生成 Agent 回答。');
    appendError(readableError(error), () => sendMessage(agent, text));
  } finally {
    if (generation === state.generation) {
      cancel.disabled = true;
      state.activeController = null;
      messages.scrollTop = messages.scrollHeight;
    }
  }
}

async function consumeAguiStream(stream, handlers) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parsed = parseSseBuffer(buffer);
    buffer = parsed.rest;
    for (const event of parsed.events) {
      if (handlers.generation !== state.generation) return;
      handlers.onEvent(event);
    }
  }
}

function applyAguiEvent(event, agentMessage) {
  const type = event.type || event.event || event.name;
  if (type === 'RUN_STARTED' || type === 'TEXT_MESSAGE_START') return;
  if (type === 'TEXT_MESSAGE_CONTENT') {
    const delta = event.delta || event.content || event.text || '';
    const previous = agentMessage.dataset.raw || '';
    agentMessage.dataset.raw = previous + delta;
    renderMessageMarkdown(agentMessage, agentMessage.dataset.raw);
    return;
  }
  if (type === 'TEXT_MESSAGE_END') return;
  if (type === 'RUN_FINISHED') {
    renderCitations(event.citations);
    return;
  }
  if (type?.startsWith('TOOL_CALL')) {
    renderToolCall(event);
    return;
  }
  if (type === 'RUN_ERROR') {
    appendError(readableError(event.error || event), () => state.lastRun && sendMessage(state.lastRun.agent, state.lastRun.text));
  }
}

function renderSubmit() {
  view.innerHTML = `
    <section class="hero">
      <div>
        <p class="eyebrow">DEVELOPER</p>
        <h1>开发者接入</h1>
        <p class="lead">开发者只能提交 Link App 或 Connected Agent。Featured 由平台在 Connected 与完整工作台验收后授予。</p>
      </div>
    </section>
    <section class="submit-layout">
      <form id="submitForm" class="form-grid">
        <label class="field"><span>接入方式</span><select name="mode"><option value="link">快速入驻：提供应用网址</option><option value="connected">标准接入：聊天端点 + 健康检查</option></select></label>
        <label class="field"><span>Agent ID</span><input name="id" value="campus-demo-agent" required /></label>
        <label class="field"><span>名称</span><input name="name" value="校园 Demo Agent" required /></label>
        <label class="field"><span>简介</span><textarea name="description" required>用于演示 Campus Agent Hub 渐进式接入流程的独立校园 Agent。</textarea></label>
        <label class="field"><span>维护者</span><input name="owner" value="${escapeAttr(state.user.name)}" required /></label>
        <label class="field"><span>分类</span><input name="category" value="校园生活" required /></label>
        <label class="field"><span>版本</span><input name="version" value="0.1.0" required /></label>
        <label class="field"><span>标签，逗号分隔</span><input name="tags" value="演示,校园服务" /></label>
        <label class="field"><span>应用网址 launch_url</span><input name="launch_url" value="https://example.edu.cn/agent" required /></label>
        <div data-connected-fields hidden>
          <label class="field"><span>聊天端点 chat_endpoint</span><input name="chat_endpoint" value="https://example.edu.cn/api/chat" /></label>
          <label class="field"><span>健康检查 health_endpoint</span><input name="health_endpoint" value="https://example.edu.cn/api/health" /></label>
          <label class="field"><span>协议</span><select name="protocol"><option value="ag-ui">AG-UI</option><option value="simple-chat">simple-chat adapter</option></select></label>
        </div>
        <div class="action-row">
          <button class="ghost-button" type="button" data-validate-manifest>校验 Manifest</button>
          <button class="ghost-button" type="button" data-test-endpoint>测试网址 / Endpoint</button>
          <button class="button" type="submit">提交审核</button>
        </div>
      </form>
      <aside class="panel">
        <h2>预览与验收项</h2>
        <div id="manifestPreview"></div>
        <h3>自动验收证据</h3>
        <ul id="validationList" class="validation-list"></ul>
      </aside>
    </section>
  `;
  bindSubmitForm();
}

function bindSubmitForm() {
  const form = document.querySelector('#submitForm');
  const connectedFields = document.querySelector('[data-connected-fields]');
  const refresh = () => {
    const mode = fieldValue(form, 'mode');
    connectedFields.hidden = mode !== 'connected';
    const manifest = formToManifest(form);
    document.querySelector('#manifestPreview').innerHTML = renderAgentCard({
      ...manifest,
      access_level: manifest.integration.mode,
      health: { status: 'unknown', label: '待审核' },
    });
    bindAgentCardActions();
  };
  form.elements.namedItem('mode').addEventListener('change', refresh);
  form.addEventListener('input', refresh);
  document.querySelector('[data-validate-manifest]').addEventListener('click', () => showManifestValidation(formToManifest(form)));
  document.querySelector('[data-test-endpoint]').addEventListener('click', () => {
    const manifest = formToManifest(form);
    const checks = [
      ['launch_url 格式', Boolean(safeUrl(manifest.integration.launch_url))],
      ['Connected 端点完整', manifest.integration.mode === 'link' || Boolean(safeUrl(manifest.integration.chat_endpoint) && safeUrl(manifest.integration.health_endpoint))],
      ['Featured 未由开发者声明', !manifest.featured && manifest.integration.mode !== 'featured'],
    ];
    showChecks(checks.map(([name, ok]) => ({ name, status: ok ? 'passed' : 'failed', detail: ok ? '通过' : '需要修正' })));
  });
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const manifest = formToManifest(form);
    const result = validateManifest(manifest);
    if (!result.ok) {
      showManifestValidation(manifest);
      return;
    }
    try {
      await apiJson(HUB_API.registry, { method: 'POST', body: manifest });
      toast('已提交到 Registry，状态为 pending。');
    } catch (error) {
      toast(`提交失败：${readableError(error)}`);
      return;
    }
    navigate('/hub/admin');
  });
  refresh();
  showManifestValidation(formToManifest(form));
}

function formToManifest(form) {
  const tags = fieldValue(form, 'tags').split(',').map((item) => item.trim()).filter(Boolean);
  const mode = fieldValue(form, 'mode');
  const integration = {
    mode,
    launch_url: fieldValue(form, 'launch_url').trim(),
  };
  if (mode === 'connected') {
    integration.chat_endpoint = fieldValue(form, 'chat_endpoint').trim();
    integration.health_endpoint = fieldValue(form, 'health_endpoint').trim();
    integration.protocol = fieldValue(form, 'protocol');
  }
  return {
    schema_version: '1.0',
    id: fieldValue(form, 'id').trim(),
    name: fieldValue(form, 'name').trim(),
    description: fieldValue(form, 'description').trim(),
    version: fieldValue(form, 'version').trim(),
    owner: fieldValue(form, 'owner').trim(),
    category: fieldValue(form, 'category').trim(),
    tags,
    integration,
    capabilities: mode === 'connected' ? ['streaming'] : ['external-link'],
  };
}

function fieldValue(form, name) {
  return String(form.elements.namedItem(name)?.value || '');
}

function showManifestValidation(manifest) {
  const result = validateManifest(manifest);
  const items = result.ok
    ? [{ name: 'Manifest 基础校验', status: 'passed', detail: '可以提交审核' }]
    : result.errors.map((error) => ({ name: 'Manifest 基础校验', status: 'failed', detail: error }));
  showChecks([
    ...items,
    { name: 'SSRF 与重定向安全', status: 'pending', detail: '由服务端审核执行，前端不信任自填结果' },
    { name: '协议验收', status: manifest.integration.mode === 'connected' ? 'pending' : 'skipped', detail: manifest.integration.mode === 'connected' ? '提交后执行 AG-UI/SSE 测试' : 'Link App 不要求聊天端点' },
  ]);
}

function showChecks(checks) {
  document.querySelector('#validationList').innerHTML = checks.map((check) => (
    `<li>${statusDot(check.status)} <strong>${escapeHtml(check.name)}</strong><br><span class="small-muted">${escapeHtml(check.detail)}</span></li>`
  )).join('');
}

async function renderAdmin() {
  if (state.user.role !== 'admin') {
    view.innerHTML = errorState('需要管理员身份', '请在右上角切换为 demo-a 管理员后查看审核台。', '返回广场', '/hub');
    return;
  }
  view.innerHTML = skeletonAdmin();
  try {
    state.adminAgents = await loadAdminAgents();
  } catch (error) {
    view.innerHTML = errorState('管理审核加载失败', readableError(error), '返回广场', '/hub');
    return;
  }
  if (!state.selectedAdminAgentId && state.adminAgents.length) state.selectedAdminAgentId = normalizeAgent(state.adminAgents[0]).id;
  paintAdmin();
}

function paintAdmin() {
  const selected = state.adminAgents.find((item) => normalizeAgent(item).id === state.selectedAdminAgentId) || state.adminAgents[0];
  view.innerHTML = `
    <section class="hero">
      <div>
        <p class="eyebrow">ADMIN</p>
        <h1>管理审核</h1>
        <p class="lead">审核、暂停、恢复和回滚都要求填写原因；前端只展示证据，最终治理由服务端 Registry 记录。</p>
      </div>
    </section>
    <section class="admin-layout">
      <div class="admin-list">
        ${state.adminAgents.map((agent) => renderAdminRow(agent, selected)).join('')}
      </div>
      <aside class="panel" id="adminDetail">
        ${selected ? renderAdminDetail(selected) : emptyState('暂无待审核 Agent', '开发者提交 Manifest 后会出现在这里。')}
      </aside>
    </section>
  `;
  bindAdminActions();
}

function renderAdminRow(raw, selected) {
  const agent = normalizeAgent(raw);
  const rowStatus = raw.status || agent.status || raw.review_status || 'pending';
  return `
    <article class="admin-row" data-admin-id="${escapeAttr(agent.id)}" tabindex="0" aria-selected="${String(selected && normalizeAgent(selected).id === agent.id)}">
      <div class="admin-row__head">
        ${renderAgentIcon(agent)}
        <div>
          <strong>${escapeHtml(agent.name)}</strong>
          <div class="small-muted">${escapeHtml(agent.id)} · ${escapeHtml(agent.category)}</div>
        </div>
      </div>
      <div class="admin-row__meta">
        ${statusBadge(rowStatus)}
        <span>v${escapeHtml(agent.version)}</span>
        <span>${escapeHtml(raw.submitted_at || raw.updated_at || '')}</span>
      </div>
    </article>
  `;
}

function renderAdminDetail(raw) {
  const agent = normalizeAgent(raw);
  const version = raw.versions?.find((item) => item.review_status === 'pending') || raw.active_version || raw.versions?.[0] || {};
  const versionId = version.version_id || version.id || raw.active_version_id || `${agent.id}@${agent.version}`;
  const versionIsConnected = version.manifest?.integration?.mode === 'connected';
  const checks = raw.checks || version.checks || [
    { name: 'Manifest Schema', status: version.review_status === 'approved' ? 'passed' : 'pending', detail: '等待服务端自动检查结果' },
  ];
  return `
    <h2>${escapeHtml(agent.name)}</h2>
    <p class="lead">${escapeHtml(agent.description)}</p>
    <div class="tag-list">${statusBadge(raw.status || agent.status || 'pending')} ${accessBadge(agent)}</div>
    <h3>自动检查</h3>
    <div class="check-list">
      ${checks.map((check) => `<div class="check-item"><span>${statusDot(check.status)}</span><span><strong>${escapeHtml(check.name)}</strong><br><span class="small-muted">${escapeHtml(check.detail || check.safe_detail || check.error_code || '')}</span></span></div>`).join('')}
    </div>
    <h3>Manifest</h3>
    <pre class="manifest-box">${escapeHtml(JSON.stringify(raw.active_version?.manifest || raw.manifest || raw, null, 2))}</pre>
    <div class="action-row" style="margin-top:14px">
      <button class="ghost-button" type="button" data-run-checks data-version="${escapeAttr(versionId)}">重新执行机器验收</button>
      ${version.review_status === 'pending' || raw.review_status === 'pending' || raw.status === 'pending' ? `<button class="button" type="button" data-review="approved" data-version="${escapeAttr(versionId)}">批准</button><button class="danger-button" type="button" data-review="rejected" data-version="${escapeAttr(versionId)}">拒绝</button>` : ''}
      ${version.review_status === 'pending' && versionIsConnected ? `<button class="ghost-button" type="button" data-review-featured data-version="${escapeAttr(versionId)}">批准为 Featured</button>` : ''}
      ${raw.status === 'active' ? `<button class="danger-button" type="button" data-status-action="suspend">暂停</button>` : ''}
      ${raw.status === 'suspended' ? `<button class="button" type="button" data-status-action="restore">恢复</button>` : ''}
      ${['active', 'suspended'].includes(raw.status) ? `<button class="danger-button" type="button" data-status-action="deprecate">废弃</button>` : ''}
      <button class="ghost-button" type="button" data-status-action="rollback">回滚</button>
    </div>
  `;
}

function bindAdminActions() {
  document.querySelectorAll('[data-admin-id]').forEach((row) => {
    row.addEventListener('click', () => {
      state.selectedAdminAgentId = row.dataset.adminId;
      paintAdmin();
    });
    row.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        state.selectedAdminAgentId = row.dataset.adminId;
        paintAdmin();
      }
    });
  });
  document.querySelectorAll('[data-review]').forEach((button) => {
    button.addEventListener('click', () => adminReview(button.dataset.review, button.dataset.version, false));
  });
  document.querySelector('[data-review-featured]')?.addEventListener('click', (event) => {
    adminReview('approved', event.currentTarget.dataset.version, true);
  });
  document.querySelector('[data-run-checks]')?.addEventListener('click', (event) => {
    adminRunChecks(event.currentTarget.dataset.version);
  });
  document.querySelectorAll('[data-status-action]').forEach((button) => {
    button.addEventListener('click', () => adminStatus(button.dataset.statusAction));
  });
}

async function adminReview(decision, versionId, featured) {
  const reason = prompt(decision === 'approved' ? '批准原因' : '拒绝原因', featured ? 'Connected 与完整工作台验收通过，授予 Featured。' : '');
  if (reason === null) return;
  try {
    const updated = await apiJson(HUB_API.reviewVersion(state.selectedAdminAgentId, versionId), {
      method: 'POST',
      body: { decision, notes: reason, featured },
      admin: true,
    });
    replaceAdminAgent(updated);
  } catch (error) {
    toast(`审核失败：${readableError(error)}`);
  }
  paintAdmin();
}

async function adminRunChecks(versionId) {
  try {
    const result = await apiJson(HUB_API.checkVersion(state.selectedAdminAgentId, versionId), {
      method: 'POST',
      body: {},
      admin: true,
    });
    toast(result.overall_status === 'passed' ? '机器验收通过。' : '机器验收未通过，请查看检查项。');
    state.adminAgents = await loadAdminAgents();
  } catch (error) {
    toast(`机器验收失败：${readableError(error)}`);
  }
  paintAdmin();
}

async function adminStatus(action) {
  const reason = prompt(`${action} 原因`, '');
  if (reason === null) return;
  try {
    const endpoint = action === 'suspend' ? HUB_API.suspend(state.selectedAdminAgentId)
      : action === 'restore' ? HUB_API.restore(state.selectedAdminAgentId)
      : action === 'deprecate' ? HUB_API.deprecate(state.selectedAdminAgentId)
      : HUB_API.rollback(state.selectedAdminAgentId);
    const body = action === 'rollback' ? { reason, version_id: null } : { reason };
    const updated = await apiJson(endpoint, { method: 'POST', body, admin: true });
    replaceAdminAgent(updated);
  } catch (error) {
    toast(`治理操作失败：${readableError(error)}`);
  }
  paintAdmin();
}

async function openWorkspace(agent) {
  if (normalizeAccessLevel(agent) !== 'featured') return;
  try {
    const response = await apiJson(HUB_API.workspaceStart(agent.id), {
      method: 'POST',
      body: { state: randomState() },
    });
    const url = response.launch_url;
    if (safeUrl(url)) window.location.assign(url);
  } catch (error) {
    toast(`完整工作台启动失败：${readableError(error)}`);
  }
}

async function loadAgents() {
  const payload = await apiJson(HUB_API.agents);
  const agents = Array.isArray(payload) ? payload : payload.agents || [];
  return agents.map(normalizeAgent);
}

async function loadAgent(id) {
  try {
    const payload = await apiJson(HUB_API.agent(id));
    return normalizeAgent(payload);
  } catch {
    return null;
  }
}

async function loadAdminAgents() {
  const payload = await apiJson(HUB_API.adminAgents, { admin: true });
  return Array.isArray(payload) ? payload : payload.agents || [];
}

async function apiJson(url, options = {}) {
  const headers = {
    'Accept': 'application/json',
    'X-Hub-User': options.admin ? 'demo-a' : state.user.id,
    ...(options.body ? { 'Content-Type': 'application/json' } : {}),
  };
  const response = await fetch(url, {
    method: options.method || 'GET',
    headers,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  if (!response.ok) throw await normalizeHttpError(response);
  if (response.status === 204) return {};
  return response.json();
}

async function normalizeHttpError(response) {
  let payload = {};
  try {
    payload = await response.json();
  } catch {
    // keep empty payload
  }
  const detail = payload.detail || payload;
  const code = detail.error || detail.code || response.statusText || 'upstream_error';
  return { code, message: ERROR_MESSAGES[code] || detail.message || `HTTP ${response.status}` };
}

function renderAgentIcon(agent) {
  const rawSrc = String(agent.icon || '');
  const src = rawSrc.startsWith('./') || rawSrc.startsWith('/') || rawSrc.startsWith('assets/')
    ? rawSrc
    : safeUrl(rawSrc);
  const fallback = escapeHtml((agent.name || agent.id || 'A').slice(0, 1).toUpperCase());
  if (!src) return `<span class="agent-icon" aria-hidden="true">${fallback}</span>`;
  return `<span class="agent-icon" aria-hidden="true"><img src="${escapeAttr(src)}" alt="" loading="lazy" /></span>`;
}

function healthBadge(health) {
  const status = health?.status || 'unknown';
  const tone = status === 'ok' || status === 'healthy' ? 'success'
    : status === 'degraded' || status === 'unknown' ? 'warning'
    : status === 'offline' || status === 'error' ? 'danger'
    : 'neutral';
  return `<span class="badge badge--${tone}">${escapeHtml(health?.label || '未检查')}</span>`;
}

function formatDataPolicy(policy) {
  if (!policy || typeof policy !== 'object') return String(policy || '平台按最小必要原则传递身份和请求上下文。');
  return [
    policy.receives_user_identity ? '会接收本次请求所需的短期用户身份' : '不接收 Hub 用户身份',
    policy.receives_files ? '可能接收用户明确授权的文件' : '不接收用户文件',
    policy.stores_conversation ? 'Agent 声明会保存对话' : 'Agent 声明不保存对话正文',
  ].join('；') + '。';
}

function accessBadge(agent) {
  const meta = accessMeta(agent);
  return `<span class="badge badge--${meta.tone}">${escapeHtml(meta.label)}</span>`;
}

function statusBadge(status) {
  const tone = {
    active: 'success',
    approved: 'success',
    pending: 'warning',
    suspended: 'warning',
    rejected: 'danger',
    deprecated: 'neutral',
  }[status] || 'neutral';
  return `<span class="badge badge--${tone}">${escapeHtml(status)}</span>`;
}

function statusDot(status) {
  const symbol = status === 'passed' || status === 'approved' ? '✅'
    : status === 'failed' || status === 'rejected' ? '❌'
    : status === 'warning' ? '⚠️'
    : status === 'skipped' ? '⏭️'
    : '⏳';
  return `<span aria-hidden="true">${symbol}</span>`;
}

function appendMessage(role, text) {
  const node = document.createElement('div');
  node.className = `message message--${role}`;
  if (role === 'agent') {
    node.classList.add('markdown');
    renderMessageMarkdown(node, text);
  } else {
    node.textContent = text;
  }
  document.querySelector('#messages')?.appendChild(node);
  node.scrollIntoView({ block: 'end' });
  return node;
}

function renderMessageMarkdown(node, text) {
  node.dataset.raw = text;
  node.innerHTML = renderMarkdownSafe(text || '正在等待 Agent 响应…');
}

function appendSystem(text) {
  appendMessage('system', text);
}

function appendError(text, retry) {
  const node = document.createElement('div');
  node.className = 'message message--error';
  node.innerHTML = `<span>${escapeHtml(text)}</span> <button class="ghost-button" type="button">重试</button>`;
  node.querySelector('button').addEventListener('click', retry);
  document.querySelector('#messages')?.appendChild(node);
  node.scrollIntoView({ block: 'end' });
}

function renderToolCall(event) {
  const details = document.createElement('details');
  details.className = 'tool-call';
  details.open = event.type === 'TOOL_CALL_START';
  const name = event.toolCallName || event.name || event.toolName || 'tool';
  details.innerHTML = `
    <summary>🔧 调用了 ${escapeHtml(name)} · ${escapeHtml(event.type || '')}</summary>
    <div class="tool-call__body"><pre><code>${escapeHtml(JSON.stringify(event, null, 2))}</code></pre></div>
  `;
  document.querySelector('#messages')?.appendChild(details);
}

function renderCitations(citations) {
  if (!Array.isArray(citations) || !citations.length) return;
  const section = document.createElement('section');
  section.className = 'citation-list';
  section.setAttribute('aria-label', '引用来源');
  section.innerHTML = `
    <h3>引用来源</h3>
    <ol>${citations.map((citation, index) => {
      const title = citation?.title || citation?.label || `来源 ${index + 1}`;
      const href = safeUrl(citation?.url || citation?.href || '');
      const location = citation?.page ? `第 ${escapeHtml(citation.page)} 页` : citation?.location || '';
      return `<li>${href ? `<a href="${escapeAttr(href)}" target="_blank" rel="noopener noreferrer">${escapeHtml(title)}</a>` : `<strong>${escapeHtml(title)}</strong>`}${location ? ` <span class="small-muted">${escapeHtml(location)}</span>` : ''}</li>`;
    }).join('')}</ol>
  `;
  document.querySelector('#messages')?.appendChild(section);
}

function skeletonDetail() {
  return `<section class="panel"><div class="skeleton-grid"><div class="skeleton-card"></div><div class="skeleton-card"></div></div></section>`;
}

function skeletonAdmin() {
  return `<section class="panel"><div class="skeleton-grid"><div class="skeleton-card"></div><div class="skeleton-card"></div></div></section>`;
}

function emptyState(title, detail, action = '') {
  return `<div class="empty-state"><div><div class="empty-state__icon">⌁</div><h2>${escapeHtml(title)}</h2><p>${escapeHtml(detail)}</p>${action}</div></div>`;
}

function errorState(title, detail, actionLabel = '', href = '') {
  const action = href ? `<a class="button" href="${escapeAttr(href)}" data-link>${escapeHtml(actionLabel)}</a>` : actionLabel ? `<button class="button" type="button" data-retry>${escapeHtml(actionLabel)}</button>` : '';
  return `<div class="error-state"><div><div class="empty-state__icon">!</div><h2>${escapeHtml(title)}</h2><p>${escapeHtml(detail)}</p>${action}</div></div>`;
}

function readableError(error) {
  const code = error?.code || error?.error;
  if (code && ERROR_MESSAGES[code]) return ERROR_MESSAGES[code];
  return error?.message || String(error || '未知错误');
}

function rememberRecent(id) {
  if (!id) return;
  const recent = loadJson(STORAGE.recent, []).filter((item) => item !== id);
  recent.unshift(id);
  localStorage.setItem(STORAGE.recent, JSON.stringify(recent.slice(0, 12)));
}

function replaceAdminAgent(updated) {
  const id = normalizeAgent(updated).id;
  const index = state.adminAgents.findIndex((item) => normalizeAgent(item).id === id);
  if (index >= 0) state.adminAgents[index] = updated;
  else state.adminAgents.unshift(updated);
}

function randomState() {
  if (globalThis.crypto?.randomUUID) return `hub-${globalThis.crypto.randomUUID()}`;
  const bytes = new Uint8Array(24);
  globalThis.crypto.getRandomValues(bytes);
  return `hub-${Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('')}`;
}

function loadJson(key, fallback) {
  try {
    const value = localStorage.getItem(key);
    return value ? JSON.parse(value) : fallback;
  } catch {
    return fallback;
  }
}

function unique(values) {
  return [...new Set(values.filter(Boolean))];
}

function toast(message) {
  const node = document.createElement('div');
  node.className = 'toast';
  node.textContent = message;
  document.body.appendChild(node);
  setTimeout(() => node.remove(), 4200);
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll('`', '&#96;');
}
