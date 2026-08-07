import {
  ACCESS_LEVELS,
  DEMO_USERS,
  ERROR_MESSAGES,
  HUB_API,
  accessMeta,
  buildRunAgentInput,
  errorFromAguiEvent,
  filterAgents,
  formatUsage,
  getAgentPrimaryHref,
  normalizeAccessLevel,
  normalizeAgent,
  parseSseBuffer,
  renderMarkdownSafe,
  safeUrl,
  validateManifest,
} from './hub-core.js?v=20260806-6';
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
const userNickname = document.querySelector('#userNickname');
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
  syncTopbarUser();
  document.querySelectorAll('[data-admin-only]').forEach((item) => {
    item.hidden = user.role !== 'admin';
  });
}

function renderUserAvatar(node, user, profile) {
  const photo = profile?.avatarDataUrl;
  if (photo) {
    node.innerHTML = `<img src="${escapeAttr(photo)}" alt="${escapeAttr(user.name)}" />`;
  } else {
    node.textContent = user.initials;
  }
}

function syncTopbarUser() {
  if (!state.user) return;
  const profile = loadProfile(state.user.id);
  if (userAvatar) renderUserAvatar(userAvatar, state.user, profile);
  if (userNickname) userNickname.textContent = profile.displayName?.trim() || state.user.name;
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
  if (segments[1] === 'agents' && segments[2] && segments[3] === 'chat') return { name: 'chat', id: decodeURIComponent(segments[2]) };
  if (segments[1] === 'agents' && segments[2]) return { name: 'detail', id: decodeURIComponent(segments[2]) };
  if (segments[1] === 'agents') return { name: 'directory' };
  if (segments[1] === 'submit') return { name: 'submit' };
  if (segments[1] === 'admin') return { name: 'admin' };
  if (segments[1] === 'settings') return { name: 'settings' };
  if (segments[1] === 'profile') return { name: 'profile' };
  return { name: 'portal' };
}

function render() {
  destroyPortalEffects();
  syncNav();
  syncTopbarUser();
  if (!view) return;
  view.focus({ preventScroll: true });
  if (state.route.name === 'portal') return renderPortal();
  if (state.route.name === 'directory') return renderDirectory();
  if (state.route.name === 'detail') return renderDetail(state.route.id);
  if (state.route.name === 'chat') return renderChat(state.route.id);
  if (state.route.name === 'submit') return renderSubmit();
  if (state.route.name === 'admin') return renderAdmin();
  if (state.route.name === 'settings') return renderSettings();
  if (state.route.name === 'profile') return renderProfile();
}

function syncNav() {
  document.querySelectorAll('[data-nav]').forEach((item) => {
    const key = item.getAttribute('data-nav');
    const active = (
      (state.route.name === 'portal' && key === 'portal') ||
      (state.route.name === 'directory' && key === 'directory') ||
      (state.route.name === 'recent' && key === 'recent') ||
      (state.route.name === 'submit' && key === 'submit') ||
      (state.route.name === 'admin' && key === 'admin') ||
      (state.route.name === 'settings' && key === 'settings') ||
      (state.route.name === 'profile' && key === 'profile')
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
  view.innerHTML = renderPortalStage();
  mountPortalEffects();
  bindPortalSearch();
  try {
    state.agents = await loadAgents();
  } catch {
    // 主页不展示 Agent 列表错误，静默忽略；用户可前往 Agent 广场查看
  } finally {
    state.loading = false;
  }
}

function renderPortalStage() {
  return `
    <section class="portal-stage" data-starfield>
      <div class="portal-stage__content">
        <p class="portal-kicker">AI FOR USTCERS</p>
        <h1>为科大学生服务的智能 <span class="latin">Agent</span></h1>
        <label class="portal-search" aria-label="搜索校园 Agent">
          <span class="portal-search__plus" aria-hidden="true">＋</span>
          <input id="portalSearch" type="search" placeholder="搜索 Agent、课程或校园服务" value="${escapeAttr(state.query)}" autocomplete="off" />
          <span class="portal-search__send" aria-hidden="true">↑</span>
        </label>
      </div>
    </section>
  `;
}

function renderPortalQuickRow() {
  return `
    <section class="portal-quickrow" aria-label="Agent 广场快捷入口">
      <header class="portal-quickrow__head">
        <div><p class="eyebrow">CAMPUS AGENTS</p><h2>为你推荐</h2></div>
        <a class="portal-quickrow__more" href="/hub/agents" data-link>查看全部 Agent 广场 →</a>
      </header>
      <section id="quickRowGrid" class="hub-grid hub-grid--quick" aria-live="polite">
        ${state.loading ? (document.querySelector('#skeletonCards')?.innerHTML || '') : renderQuickRowCards()}
      </section>
    </section>
  `;
}

function renderQuickRowCards() {
  const agents = state.agents.slice(0, 4);
  if (!agents.length) return '<p class="portal-quickrow__empty">暂无可用 Agent。</p>';
  return agents.map((agent) => {
    const normalized = normalizeAgent(agent);
    return `
      <button class="hub-card hub-card--quick" type="button" data-agent-id="${escapeAttr(normalized.id)}">
        ${renderAgentIcon(normalized)}
        <div class="hub-card__body">
          <h3 class="hub-card__name">${escapeHtml(normalized.name)}</h3>
          <p class="hub-card__desc">${escapeHtml(normalized.description)}</p>
        </div>
        <span class="hub-card__chevron" aria-hidden="true">→</span>
      </button>
    `;
  }).join('');
}

function bindPortalSearch() {
  const portalSearch = document.querySelector('#portalSearch');
  portalSearch?.addEventListener('input', () => {
    state.query = portalSearch.value;
    syncSearchInputs(portalSearch);
    updateAgentGrid();
  });
}

function bindQuickRowCards() {
  document.querySelectorAll('#quickRowGrid [data-agent-id]').forEach((card) => {
    card.addEventListener('click', () => navigate(`/hub/agents/${encodeURIComponent(card.dataset.agentId)}`));
  });
}

async function renderDirectory() {
  state.loading = true;
  view.innerHTML = renderDirectoryShell('应用广场', '发现、比较并使用通过平台治理的校园 Agent。', true);
  mountPortalEffects();
  try {
    state.agents = await loadAgents();
  } catch (error) {
    state.loading = false;
    view.innerHTML = renderDirectoryShell('应用广场', '发现、比较并使用通过平台治理的校园 Agent。', false);
    mountPortalEffects();
    document.querySelector('#agentGrid').innerHTML = errorState('Agent 列表加载失败', readableError(error), '重试');
    document.querySelector('[data-retry]')?.addEventListener('click', renderDirectory);
    return;
  } finally {
    state.loading = false;
  }
  view.innerHTML = renderDirectoryShell('应用广场', '发现、比较并使用通过平台治理的校园 Agent。', false);
  bindFilters();
  updateAgentGrid();
}

function renderDirectoryShell(title, subtitle, loading) {
  const agents = state.agents;
  const categories = ['全部', ...unique(agents.map((agent) => normalizeAgent(agent).category))];
  const chips = ['全部', ...unique(agents.flatMap((agent) => {
    const normalized = normalizeAgent(agent);
    return [...normalized.tags, ...normalized.capabilities].slice(0, 12);
  })).slice(0, 18)];
  return `
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
    action.addEventListener('click', async (event) => {
      const id = action.dataset.agentId;
      rememberRecent(id);
      const agent = state.agents.find((item) => normalizeAgent(item).id === id);
      if (!agent) return;
      const level = normalizeAccessLevel(agent);
      if (level === 'featured') {
        event.preventDefault();
        await openWorkspace(agent);
      }
      // link（target=_blank 默认外链）与 connected（href=/chat 默认进入聊天页）放行默认行为
    });
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
    if (state.activeController) return;
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
  });
}

async function sendMessage(agent, text) {
  if (state.activeController) return;
  const messages = document.querySelector('#messages');
  const cancel = document.querySelector('#cancelRun');
  const send = document.querySelector('#sendRun');
  const form = document.querySelector('#composer');
  const generation = state.generation;
  appendMessage('user', text);
  const agentMessage = appendMessage('agent', '');
  const controller = new AbortController();
  state.activeController = controller;
  cancel.disabled = false;
  send.disabled = true;
  form.setAttribute('aria-busy', 'true');
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
    if (generation !== state.generation) return;
    if (error.name === 'AbortError') {
      renderMessageMarkdown(agentMessage, '已取消本轮调用。');
      return;
    }
    renderMessageMarkdown(agentMessage, '调用失败，本轮没有生成 Agent 回答。');
    appendError(readableError(error), () => sendMessage(agent, text));
  } finally {
    if (generation === state.generation && state.activeController === controller) {
      cancel.disabled = true;
      send.disabled = false;
      form.removeAttribute('aria-busy');
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
    if (done) {
      buffer += decoder.decode();
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const parsed = parseSseBuffer(buffer);
    buffer = parsed.rest;
    for (const event of parsed.events) {
      if (handlers.generation !== state.generation) return;
      handlers.onEvent(event);
    }
  }
  if (buffer.trim()) {
    const parsed = parseSseBuffer(`${buffer}\n\n`);
    for (const event of parsed.events) {
      if (handlers.generation !== state.generation) return;
      handlers.onEvent(event);
    }
    if (parsed.rest.trim()) throw errorFromAguiEvent({ code: 'protocol_error' });
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
    throw errorFromAguiEvent(event);
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

function renderSettings() {
  const saved = loadSettings();
  view.innerHTML = `
    <section class="hero">
      <div>
        <p class="eyebrow">SETTINGS</p>
        <h1>模型配置</h1>
        <p class="lead">在这里配置你自己的大模型 Key。配置后，Hub 主页面与所有 Hub 调度的子 Agent（如瀚海行、校园助手 Demo）都可以调用你的模型。前端先打通界面，后端持久化与透传将在后续接入。</p>
      </div>
    </section>
    <section class="submit-layout">
      <form id="settingsForm" class="form-grid">
        <label class="field">
          <span>默认模型厂商</span>
          <select name="provider">
            <option value="openai" ${saved.provider === 'openai' ? 'selected' : ''}>OpenAI 兼容（gpt-4o / gpt-5 等）</option>
            <option value="anthropic" ${saved.provider === 'anthropic' ? 'selected' : ''}>Anthropic（claude 系列）</option>
            <option value="custom" ${saved.provider === 'custom' ? 'selected' : ''}>自定义（自建 / 校园网关）</option>
          </select>
        </label>
        <label class="field">
          <span>模型名称</span>
          <input name="model" value="${escapeAttr(saved.model)}" placeholder="例如 gpt-4o-mini / claude-3.7-sonnet" />
        </label>
        <label class="field">
          <span>API Base URL</span>
          <input name="baseUrl" value="${escapeAttr(saved.baseUrl)}" placeholder="https://api.openai.com/v1" />
        </label>
        <label class="field">
          <span>API Key</span>
          <input name="apiKey" type="password" value="${escapeAttr(saved.apiKey)}" placeholder="sk-..." autocomplete="off" />
        </label>
        <label class="field">
          <span>调用温度（temperature）</span>
          <input name="temperature" type="number" min="0" max="2" step="0.1" value="${escapeAttr(saved.temperature ?? '0.7')}" />
        </label>
        <label class="field">
          <span>最大输出 token</span>
          <input name="maxTokens" type="number" min="64" max="32000" step="64" value="${escapeAttr(saved.maxTokens ?? '2048')}" />
        </label>
        <div class="action-row">
          <button class="ghost-button" type="button" data-test-model>测试连通</button>
          <button class="ghost-button" type="button" data-reset-model>清空</button>
          <button class="button" type="submit">保存配置</button>
        </div>
      </form>
      <aside class="panel">
        <h2>说明</h2>
        <p class="lead">目前仅在浏览器本地保存（localStorage），用于前端原型演示。后续会在 Hub 后端增加 <code>POST /api/settings</code> 持久化到用户档案，并由 Hub 网关按身份读取后在网关层注入到子 Agent 调用。</p>
        <h3>生效范围</h3>
        <ul class="validation-list">
          <li>Hub 主页面搜索 / 问答（占位）</li>
          <li>统一聊天页（Connected Agent，透传 <code>custom_llm</code> 字段）</li>
          <li>完整工作台（Featured Agent，如瀚海行 Agent）</li>
        </ul>
        <h3>安全</h3>
        <p class="lead">生产环境不允许把 Key 直接暴露给浏览器。请选择以下方式之一：</p>
        <ul class="validation-list">
          <li>由 Hub 在服务端持有 Key，网关层替换 <code>Authorization</code> 头；</li>
          <li>改为走 SSO/校园统一身份，由网关签发短期访问令牌。</li>
        </ul>
      </aside>
    </section>
  `;
  bindSettingsForm();
}

function bindSettingsForm() {
  const form = document.querySelector('#settingsForm');
  if (!form) return;
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const data = {
      provider: fieldValue(form, 'provider'),
      model: fieldValue(form, 'model').trim(),
      baseUrl: fieldValue(form, 'baseUrl').trim(),
      apiKey: fieldValue(form, 'apiKey').trim(),
      temperature: fieldValue(form, 'temperature'),
      maxTokens: fieldValue(form, 'maxTokens'),
      savedAt: new Date().toISOString(),
    };
    saveSettings(data);
    toast('已保存到本地（前端原型）。后续将打通 Hub 后端持久化。');
  });
  document.querySelector('[data-test-model]')?.addEventListener('click', () => {
    const data = currentSettings(form);
    if (!data.apiKey) {
      toast('请先填写 API Key 再测试。');
      return;
    }
    toast(`已记录 ${data.provider}/${data.model}。联通性验证接口待接入。`);
  });
  document.querySelector('[data-reset-model]')?.addEventListener('click', () => {
    form.reset();
    clearSettings();
    toast('已清空本地模型配置。');
  });
}

function currentSettings(form) {
  return {
    provider: fieldValue(form, 'provider'),
    model: fieldValue(form, 'model').trim(),
    baseUrl: fieldValue(form, 'baseUrl').trim(),
    apiKey: fieldValue(form, 'apiKey').trim(),
    temperature: fieldValue(form, 'temperature'),
    maxTokens: fieldValue(form, 'maxTokens'),
  };
}

const SETTINGS_KEY = 'hub_user_model_settings';
function loadSettings() {
  try {
    return JSON.parse(localStorage.getItem(SETTINGS_KEY)) || {};
  } catch {
    return {};
  }
}
function saveSettings(data) {
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(data));
}
function clearSettings() {
  localStorage.removeItem(SETTINGS_KEY);
}

async function renderProfile() {
  if (!state.agents.length) {
    try { state.agents = await loadAgents(); } catch { state.agents = []; }
  }
  const profile = loadProfile(state.user.id);
  const recentAgents = pickRecentAgents(state.agents, profile.pinnedAgentIds);
  view.innerHTML = `
    <section class="hero">
      <div>
        <p class="eyebrow">PROFILE</p>
        <h1>个人主页</h1>
        <p class="lead">在这里管理你的头像、签名和常用 Agent。后续将由 Hub 后端持久化到用户档案。</p>
      </div>
    </section>
    <section class="profile-layout">
      <form id="profileForm" class="form-grid">
        <div class="profile-avatar">
          <div class="profile-avatar__preview" id="avatarPreview">
            ${profile.avatarDataUrl ? `<img src="${escapeAttr(profile.avatarDataUrl)}" alt="${escapeAttr(state.user.name)}" />` : escapeHtml(state.user.initials)}
          </div>
          <label class="ghost-button profile-avatar__upload">
            上传头像
            <input type="file" accept="image/png,image/jpeg,image/webp" hidden data-avatar-input />
          </label>
          <button class="link-button" type="button" data-reset-avatar>使用默认</button>
        </div>
        <label class="field">
          <span>显示名</span>
          <input name="displayName" value="${escapeAttr(profile.displayName ?? state.user.name)}" />
        </label>
        <label class="field">
          <span>个性签名（一句话）</span>
          <input name="signature" maxlength="60" value="${escapeAttr(profile.signature ?? '')}" placeholder="例如：USTC 2024 级 · 想做出有用的 Agent" />
        </label>
        <label class="field">
          <span>个人简介（最多 280 字）</span>
          <textarea name="bio" maxlength="280" rows="5" placeholder="一句话介绍你自己，感兴趣的方向，做过的项目…">${escapeHtml(profile.bio ?? '')}</textarea>
        </label>
        <div class="action-row">
          <button class="button" type="submit">保存</button>
          <button class="ghost-button" type="button" data-reset-profile>恢复默认</button>
        </div>
      </form>
      <aside class="panel">
        <h2>常用 Agent</h2>
        <p class="lead">从 Agent 广场挑选常用的 Agent 钉到这里，方便在主页面与统一聊天中快速访问。</p>
        <ul id="profilePinnedList" class="profile-pinned"></ul>
        <h3>未钉选的 Agent</h3>
        <ul id="profileAvailableList" class="profile-available"></ul>
      </aside>
    </section>
  `;
  bindProfileForm(recentAgents);
  paintProfileLists(recentAgents);
}

function pickRecentAgents(allAgents, pinnedIds = []) {
  const pinnedSet = new Set(pinnedIds);
  const pinned = allAgents
    .filter((agent) => pinnedSet.has(agent.id))
    .map((agent) => ({ ...agent, pinned: true }));
  const rest = allAgents
    .filter((agent) => !pinnedSet.has(agent.id))
    .map((agent) => ({ ...agent, pinned: false }));
  return [...pinned, ...rest];
}

function paintProfileLists({ pinned, rest }) {
  const renderItem = (agent) => `
    <li class="profile-agent" data-agent-id="${escapeAttr(agent.id)}">
      <div class="profile-agent__head">
        <strong>${escapeHtml(agent.name)}</strong>
        <span class="small-muted">${escapeHtml(agent.category)} · ${escapeHtml(agent.owner)}</span>
      </div>
      <button class="ghost-button" type="button" data-toggle-pin="${escapeAttr(agent.id)}" data-pinned="${agent.pinned ? '1' : '0'}">${agent.pinned ? '取消钉选' : '钉选'}</button>
    </li>
  `;
  const pinnedList = document.querySelector('#profilePinnedList');
  const availableList = document.querySelector('#profileAvailableList');
  if (pinnedList) pinnedList.innerHTML = pinned.length ? pinned.map(renderItem).join('') : '<li class="small-muted">尚未钉选任何 Agent。</li>';
  if (availableList) availableList.innerHTML = rest.length ? rest.slice(0, 8).map(renderItem).join('') : '<li class="small-muted">没有更多 Agent。</li>';
}

function persistProfileFromForm(form, currentAgents, workingAvatar) {
  const profile = {
    displayName: fieldValue(form, 'displayName').trim() || state.user.name,
    signature: fieldValue(form, 'signature').trim(),
    bio: fieldValue(form, 'bio').trim(),
    avatarDataUrl: workingAvatar,
    pinnedAgentIds: currentAgents.filter((agent) => agent.pinned).map((agent) => agent.id),
    updatedAt: new Date().toISOString(),
  };
  saveProfile(state.user.id, profile);
  syncTopbarUser();
}

function bindProfileForm(currentAgents) {
  const form = document.querySelector('#profileForm');
  const avatarInput = form?.querySelector('[data-avatar-input]');
  const avatarPreview = document.querySelector('#avatarPreview');
  let workingAvatar = loadProfile(state.user.id).avatarDataUrl || '';

  avatarInput?.addEventListener('change', async () => {
    const file = avatarInput.files?.[0];
    if (!file) return;
    avatarInput.value = '';
    const dataUrl = await readFileAsDataUrl(file);
    if (!dataUrl) return;
    try {
      const cropped = await openAvatarCropper(dataUrl);
      if (cropped) {
        workingAvatar = cropped;
        avatarPreview.innerHTML = `<img src="${escapeAttr(workingAvatar)}" alt="${escapeAttr(state.user.name)}" />`;
        // 头像裁剪后立即持久化，避免刷新丢失
        const existing = loadProfile(state.user.id);
        const merged = { ...existing, avatarDataUrl: workingAvatar, updatedAt: new Date().toISOString() };
        saveProfile(state.user.id, merged);
        syncTopbarUser();
        toast('头像已更新。');
      }
    } catch (error) {
      toast(`裁剪失败：${readableError(error)}`);
    }
  });

  form?.addEventListener('submit', (event) => {
    event.preventDefault();
    persistProfileFromForm(form, currentAgents, workingAvatar);
    toast('个人主页已保存到本地（前端原型）。');
  });

  // 输入框失焦时立即持久化，避免刷新丢失
  ['displayName', 'signature', 'bio'].forEach((name) => {
    form?.elements.namedItem(name)?.addEventListener('change', () => {
      persistProfileFromForm(form, currentAgents, workingAvatar);
      syncTopbarUser();
    });
  });

  document.querySelector('[data-reset-avatar]')?.addEventListener('click', () => {
    workingAvatar = '';
    avatarPreview.textContent = state.user.initials;
    avatarInput.value = '';
  });

  document.querySelector('[data-reset-profile]')?.addEventListener('click', () => {
    clearProfile(state.user.id);
    form.reset();
    avatarPreview.textContent = state.user.initials;
    workingAvatar = '';
    if (userAvatar) renderUserAvatar(userAvatar, state.user, {});
    const fresh = pickRecentAgents(state.agents, []);
    paintProfileLists(fresh);
    toast('已恢复默认个人主页。');
  });

  document.querySelectorAll('[data-toggle-pin]').forEach((button) => {
    button.addEventListener('click', () => {
      const id = button.dataset.togglePin;
      const isPinned = button.dataset.pinned === '1';
      const updated = currentAgents.map((agent) => (agent.id === id ? { ...agent, pinned: !isPinned } : agent));
      const pinned = updated.filter((agent) => agent.pinned);
      const rest = updated.filter((agent) => !agent.pinned);
      paintProfileLists({ pinned, rest });
      bindProfileForm(updated);
    });
  });
}

const PROFILE_KEY = 'hub_user_profiles';
function loadProfile(userId) {
  try {
    const all = JSON.parse(localStorage.getItem(PROFILE_KEY)) || {};
    return all[userId] || {};
  } catch {
    return {};
  }
}
function saveProfile(userId, profile) {
  const all = (() => { try { return JSON.parse(localStorage.getItem(PROFILE_KEY)) || {}; } catch { return {}; } })();
  all[userId] = profile;
  localStorage.setItem(PROFILE_KEY, JSON.stringify(all));
}
function clearProfile(userId) {
  const all = (() => { try { return JSON.parse(localStorage.getItem(PROFILE_KEY)) || {}; } catch { return {}; } })();
  delete all[userId];
  localStorage.setItem(PROFILE_KEY, JSON.stringify(all));
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = () => reject(reader.error || new Error('读取文件失败'));
    reader.readAsDataURL(file);
  });
}

function loadImage(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('图片加载失败'));
    img.src = src;
  });
}

/**
 * 头像裁剪弹窗：圆形遮罩 + 拖动平移 + 滚轮缩放，确认后导出 256×256 方形 DataURL。
 */
function openAvatarCropper(imageSrc) {
  return new Promise((resolve, reject) => {
    loadImage(imageSrc).then((img) => {
      const overlay = document.createElement('div');
      overlay.className = 'cropper-overlay';

      const stage = document.createElement('div');
      stage.className = 'cropper-stage';

      const canvas = document.createElement('canvas');
      canvas.className = 'cropper-canvas';
      const ctx = canvas.getContext('2d');
      const STAGE = 320;
      const RADIUS = 120;
      canvas.width = STAGE;
      canvas.height = STAGE;

      const ring = document.createElement('div');
      ring.className = 'cropper-ring';

      const hint = document.createElement('p');
      hint.className = 'cropper-hint';
      hint.textContent = '拖动调整位置 · 滚轮缩放';

      const actions = document.createElement('div');
      actions.className = 'cropper-actions';
      const cancelBtn = document.createElement('button');
      cancelBtn.type = 'button';
      cancelBtn.className = 'ghost-button';
      cancelBtn.textContent = '取消';
      const confirmBtn = document.createElement('button');
      confirmBtn.type = 'button';
      confirmBtn.className = 'button';
      confirmBtn.textContent = '使用此头像';
      actions.append(cancelBtn, confirmBtn);

      stage.append(canvas, ring, hint);
      overlay.append(stage, actions);
      document.body.append(overlay);

      // 视图状态：图片中心相对画布中心，缩放
      const natural = { w: img.naturalWidth || img.width, h: img.naturalHeight || img.height };
      const fitScale = Math.max(STAGE / natural.w, STAGE / natural.h);
      const view = {
        cx: STAGE / 2,
        cy: STAGE / 2,
        scale: fitScale,
        minScale: fitScale * 0.5,
        maxScale: fitScale * 4,
      };

      function clamp() {
        // 至少保证圆形遮罩内不露出图片外
        const halfW = (natural.w * view.scale) / 2;
        const halfH = (natural.h * view.scale) / 2;
        const minX = STAGE / 2 - halfW + RADIUS;
        const maxX = STAGE / 2 + halfW - RADIUS;
        const minY = STAGE / 2 - halfH + RADIUS;
        const maxY = STAGE / 2 + halfH - RADIUS;
        view.cx = Math.min(Math.max(view.cx, Math.min(minX, maxX)), Math.max(minX, maxX));
        view.cy = Math.min(Math.max(view.cy, Math.min(minY, maxY)), Math.max(minY, maxY));
      }

      function draw() {
        ctx.clearRect(0, 0, STAGE, STAGE);
        ctx.save();
        // 圆形裁剪
        ctx.beginPath();
        ctx.arc(STAGE / 2, STAGE / 2, RADIUS, 0, Math.PI * 2);
        ctx.clip();
        ctx.drawImage(
          img,
          view.cx - (natural.w * view.scale) / 2,
          view.cy - (natural.h * view.scale) / 2,
          natural.w * view.scale,
          natural.h * view.scale,
        );
        ctx.restore();
        // 遮罩外暗化
        ctx.save();
        ctx.fillStyle = 'rgba(8, 10, 18, 0.62)';
        ctx.beginPath();
        ctx.rect(0, 0, STAGE, STAGE);
        ctx.arc(STAGE / 2, STAGE / 2, RADIUS, 0, Math.PI * 2, true);
        ctx.fill('evenodd');
        ctx.restore();
      }

      clamp();
      draw();

      // 拖动平移
      let dragging = false;
      let lastX = 0;
      let lastY = 0;
      function onPointerDown(event) {
        dragging = true;
        lastX = event.clientX;
        lastY = event.clientY;
        canvas.setPointerCapture?.(event.pointerId);
      }
      function onPointerMove(event) {
        if (!dragging) return;
        view.cx += event.clientX - lastX;
        view.cy += event.clientY - lastY;
        lastX = event.clientX;
        lastY = event.clientY;
        clamp();
        draw();
      }
      function onPointerUp(event) {
        dragging = false;
        canvas.releasePointerCapture?.(event.pointerId);
      }
      canvas.addEventListener('pointerdown', onPointerDown);
      canvas.addEventListener('pointermove', onPointerMove);
      canvas.addEventListener('pointerup', onPointerUp);
      canvas.addEventListener('pointercancel', onPointerUp);

      // 滚轮缩放
      function onWheel(event) {
        event.preventDefault();
        const factor = event.deltaY > 0 ? 0.92 : 1.08;
        view.scale = Math.min(Math.max(view.scale * factor, view.minScale), view.maxScale);
        clamp();
        draw();
      }
      canvas.addEventListener('wheel', onWheel, { passive: false });

      function cleanup() {
        canvas.removeEventListener('pointerdown', onPointerDown);
        canvas.removeEventListener('pointermove', onPointerMove);
        canvas.removeEventListener('pointerup', onPointerUp);
        canvas.removeEventListener('pointercancel', onPointerUp);
        canvas.removeEventListener('wheel', onWheel);
        overlay.remove();
      }

      cancelBtn.addEventListener('click', () => {
        cleanup();
        resolve(null);
      });
      confirmBtn.addEventListener('click', () => {
        const out = document.createElement('canvas');
        out.width = 256;
        out.height = 256;
        const octx = out.getContext('2d');
        const crop = RADIUS * 2;
        const sourceLeft = view.cx - RADIUS;
        const sourceTop = view.cy - RADIUS;
        octx.drawImage(canvas, sourceLeft, sourceTop, crop, crop, 0, 0, 256, 256);
        cleanup();
        resolve(out.toDataURL('image/png'));
      });
      overlay.addEventListener('click', (event) => {
        if (event.target === overlay) {
          cleanup();
          resolve(null);
        }
      });
    }).catch(reject);
  });
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
  node.querySelector('button').addEventListener('click', () => {
    node.remove();
    retry();
  });
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
  localStorage.removeItem(STORAGE.recent);
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
