const state = {
  user: null,
  users: [],
  spaces: [],
  currentSpace: null,
  documents: [],
  selectedDocumentIds: new Set(),
  settings: {},
  modelName: '',
  apiKeyTouched: false,
  isQuerying: false,
  queryRequestId: 0,
  currentView: 'home',
  homeMode: 'direct',
  homeConversation: [],
  history: [],
  activeHistoryId: null,
};


const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const SOURCE_GROUPS = [
  { id: 'daily', title: '日常学习', keywords: ['教材', '讲义', '笔记', '提纲', '教辅'] },
  { id: 'exam', title: '备考刷题', keywords: ['真题', '试卷', '答案', '解析'] },
  { id: 'other', title: '其他资料', keywords: [] },
];

const HISTORY_KEY = 'course-agent-history-v1';

// ---------- Helpers ----------
function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"]/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'
  })[character]);
}

function renderInlineMarkdown(value) {
  return escapeHtml(value)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\\\((.+?)\\\)/g, '<span class="math-inline">$1</span>');
}

function renderMarkdown(value) {
  const lines = String(value ?? '').split(/\r?\n/);
  const output = [];
  let listType = null;
  let mathLines = null;
  let mathDelimiter = null;

  const closeList = () => {
    if (listType) output.push(`</${listType}>`);
    listType = null;
  };

  for (const line of lines) {
    const trimmed = line.trim();
    const singleLineMath = trimmed.match(/^\\\[(.+)\\\]$/) || trimmed.match(/^\$\$(.+)\$\$$/);
    if (!mathLines && singleLineMath) {
      closeList();
      output.push(`<div class="math-block">${escapeHtml(singleLineMath[1].trim())}</div>`);
      continue;
    }

    if (mathLines) {
      if (trimmed === mathDelimiter) {
        output.push(`<div class="math-block">${escapeHtml(mathLines.join(' '))}</div>`);
        mathLines = null;
        mathDelimiter = null;
      } else {
        mathLines.push(trimmed);
      }
      continue;
    }

    if (trimmed === '\\[' || trimmed === '$$') {
      closeList();
      mathLines = [];
      mathDelimiter = trimmed === '$$' ? '$$' : '\\]';
      continue;
    }

    if (!trimmed) {
      closeList();
      continue;
    }

    const heading = trimmed.match(/^(#{2,4})\s+(.+)$/);
    if (heading) {
      closeList();
      const level = Math.min(4, heading[1].length);
      output.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }

    const unordered = trimmed.match(/^[-*]\s+(.+)$/);
    const ordered = trimmed.match(/^\d+[.)]\s+(.+)$/);
    if (unordered || ordered) {
      const nextType = unordered ? 'ul' : 'ol';
      if (listType !== nextType) {
        closeList();
        output.push(`<${nextType}>`);
        listType = nextType;
      }
      output.push(`<li>${renderInlineMarkdown((unordered || ordered)[1])}</li>`);
      continue;
    }

    closeList();
    if (trimmed.startsWith('> ')) {
      output.push(`<blockquote>${renderInlineMarkdown(trimmed.slice(2))}</blockquote>`);
    } else {
      output.push(`<p>${renderInlineMarkdown(trimmed)}</p>`);
    }
  }

  closeList();
  if (mathLines) output.push(`<div class="math-block">${escapeHtml(mathLines.join(' '))}</div>`);
  return output.join('');
}

function renderMath(container) {
  const renderer = window.katex;
  if (!renderer) return;
  container.querySelectorAll('.math-inline, .math-block').forEach(element => {
    const source = element.textContent;
    try {
      renderer.render(source, element, {
        displayMode: element.classList.contains('math-block'),
        throwOnError: false,
        strict: 'ignore',
        trust: false,
      });
    } catch {
      element.textContent = source;
      element.classList.add('math-render-error');
    }
  });
}

async function api(path, options = {}, timeoutMs = 150000) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(path, {
      credentials: 'same-origin',
      ...options,
      signal: controller.signal,
    });
    const text = await response.text();
    let payload = null;
    try { payload = text ? JSON.parse(text) : null; } catch { payload = { error: { message: text } }; }
    if (response.ok) return payload;
    let message =
      payload?.error?.message ||
      (Array.isArray(payload?.detail)
        ? payload.detail.map((d) => d.msg || d.message || String(d)).join('；')
        : payload?.detail) ||
      `请求失败（HTTP ${response.status}）`;
    throw new Error(message);
  } catch (error) {
    if (error.name === 'AbortError') {
      throw new Error('请求超时，请稍后重试');
    }
    throw error;
  } finally {
    clearTimeout(timeoutId);
  }
}

function toast(message, type = '') {
  const el = $('#toast');
  el.textContent = message;
  el.className = `toast ${type}`;
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => el.classList.add('hidden'), 4000);
}

function autoResize(textarea) {
  textarea.style.height = 'auto';
  const max = parseInt(textarea.style.maxHeight, 10) || 220;
  textarea.style.height = Math.min(textarea.scrollHeight, max) + 'px';
}

function setLoading(isLoading) {
  state.isQuerying = isLoading;
  const send = $('#home-send-button');
  const libSubmit = $('#library-query-submit');
  if (send) send.disabled = isLoading;
  if (libSubmit) {
    libSubmit.disabled = isLoading;
    libSubmit.textContent = isLoading ? '生成中…' : '回答';
  }
  updateHomeModeLabel();
}

// ---------- Views ----------
function showView(viewName) {
  state.currentView = viewName;
  $$('.view').forEach(v => v.classList.add('hidden'));
  $(`#view-${viewName}`).classList.remove('hidden');
  $$('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.view === viewName));
  window.location.hash = `#/${viewName}`;
  if (viewName === 'settings') loadSettings();
  if (viewName === 'library' && state.currentSpace && !state.documents.length) loadDocuments();
}

function initRouting() {
  const hash = window.location.hash.replace(/^#\//, '') || 'home';
  const valid = ['home', 'library', 'settings'];
  showView(valid.includes(hash) ? hash : 'home');
  window.addEventListener('hashchange', () => {
    const h = window.location.hash.replace(/^#\//, '') || 'home';
    if (valid.includes(h) && h !== state.currentView) showView(h);
  });
}

// ---------- Auth ----------
async function loadBase() {
  const [users, session, health] = await Promise.all([
    api('/api/users'),
    api('/api/session'),
    api('/api/health').catch(() => ({ database: false, search: false, llm_configured: false })),
  ]);
  state.users = users.items;
  state.user = session.user;
  updateUserCard();
  updateAbout(health);
  if (state.user) {
    await loadSpaces();
    await loadSettings();
    renderLoginUsers();
  } else {
    openLoginModal();
  }
}

function updateUserCard() {
  const name = $('#user-name');
  const status = $('#user-status');
  const avatar = $('#user-avatar');
  if (state.user) {
    name.textContent = state.user.display_name;
    status.textContent = state.user.id;
    avatar.textContent = state.user.display_name.slice(0, 1);
  } else {
    name.textContent = '未选择身份';
    status.textContent = '点击选择演示身份';
    avatar.textContent = '?';
  }
}

function openLoginModal() {
  renderLoginUsers();
  $('#login-modal').classList.remove('hidden');
}

function closeLoginModal() {
  $('#login-modal').classList.add('hidden');
}

function renderLoginUsers() {
  $('#login-user-list').innerHTML = state.users.map(user => `
    <button class="login-user-button" data-user="${escapeHtml(user.id)}" type="button">
      <div class="login-user-avatar">${escapeHtml(user.display_name.slice(0, 1))}</div>
      <div>
        <div class="login-user-name">${escapeHtml(user.display_name)}</div>
        <div class="login-user-id">${escapeHtml(user.id)}</div>
      </div>
    </button>
  `).join('');
  $$('#login-user-list [data-user]').forEach(btn => {
    btn.addEventListener('click', () => login(btn.dataset.user));
  });
}

async function login(userId) {
  try {
    const result = await api('/api/session', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: userId }),
    });
    state.user = result.user;
    state.selectedDocumentIds.clear();
    updateUserCard();
    closeLoginModal();
    await loadSpaces();
    await loadSettings();
    toast(`已以 ${state.user.display_name} 身份登录`, 'success');
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function logout() {
  await api('/api/session', { method: 'DELETE' });
  state.user = null;
  state.spaces = [];
  state.currentSpace = null;
  state.documents = [];
  state.selectedDocumentIds.clear();
  state.settings = {};
  state.modelName = '';
  updateHomeModelLabel();
  updateUserCard();
  openLoginModal();
}

// ---------- Spaces ----------
async function loadSpaces() {
  const result = await api('/api/spaces');
  state.spaces = result.items;
  const previousId = state.currentSpace?.id;
  state.currentSpace = state.spaces.find(s => s.id === previousId)
    || state.spaces.find(s => s.id === 'math-b1-shared')
    || state.spaces[0];
  if (state.currentSpace?.id !== previousId) state.selectedDocumentIds.clear();
  renderSpaces();
  if (state.currentSpace) await loadDocuments();
}

function renderSpaces() {
  const groups = { personal: [], shared: [], subscribed: [] };
  for (const space of state.spaces) {
    (groups[space.space_type] || groups.subscribed).push(space);
  }
  const renderList = (list, containerId) => {
    const container = $(`#${containerId}`);
    if (!container) return;
    container.innerHTML = list.map(space => `
      <div class="space-tree-item ${state.currentSpace?.id === space.id ? 'active' : ''}" data-space="${escapeHtml(space.id)}">
        <span class="space-tree-dot ${space.space_type}"></span>
        <div>
          <div class="space-tree-name">${escapeHtml(space.name)}</div>
          <div class="space-tree-meta">${space.document_count} 份 · ${escapeHtml(space.role)}</div>
        </div>
      </div>
    `).join('');
  };
  renderList(groups.personal, 'personal-space-list');
  renderList(groups.shared, 'shared-space-list');
  renderList(groups.subscribed, 'subscribed-space-list');

  $$('[data-space]').forEach(item => {
    item.addEventListener('click', () => selectSpace(item.dataset.space));
  });
}

async function selectSpace(spaceId) {
  if (state.currentSpace?.id !== spaceId) state.selectedDocumentIds.clear();
  state.currentSpace = state.spaces.find(s => s.id === spaceId);
  renderSpaces();
  await loadDocuments();
}

// ---------- Documents ----------
async function loadDocuments() {
  if (!state.currentSpace) return;
  const result = await api(`/api/spaces/${encodeURIComponent(state.currentSpace.id)}/documents?page_size=100`);
  state.documents = result.items;
  pruneDocumentSelection();
  renderDocuments();
  renderSourceSelector();
  renderHomeSourceSelector();
  updateQueryStatus();
}

function pruneDocumentSelection() {
  const available = new Set(state.documents.map(d => d.id));
  state.selectedDocumentIds = new Set([...state.selectedDocumentIds].filter(id => available.has(id)));
}

function documentText(doc) {
  return `${doc.material_type || ''} ${doc.title || ''}`.toLowerCase();
}

function documentMatches(doc, keywords) {
  return keywords.some(k => documentText(doc).includes(k.toLowerCase()));
}

function groupForDocument(doc) {
  return SOURCE_GROUPS.find(g => g.keywords.length && documentMatches(doc, g.keywords)) || SOURCE_GROUPS[SOURCE_GROUPS.length - 1];
}

function renderDocuments() {
  const count = $('#library-document-count');
  const list = $('#library-document-list');
  const title = $('#library-space-title');
  const type = $('#library-space-type');
  const role = $('#library-space-role');

  if (!state.currentSpace) {
    if (count) count.textContent = '0 份资料';
    if (title) title.textContent = '未选择空间';
    if (type) type.textContent = '';
    if (role) role.textContent = '';
    return;
  }

  if (count) count.textContent = `${state.documents.length} 份资料`;
  if (title) title.textContent = state.currentSpace.name;
  if (type) type.textContent = {
    personal: '个人知识库', shared: '共享知识库', subscribed: '订阅知识库'
  }[state.currentSpace.space_type] || '知识库';
  if (role) role.textContent = `角色：${state.currentSpace.role}`;

  if (!list) return;
  const writeable = state.currentSpace.role !== 'reader';
  list.innerHTML = state.documents.length ? state.documents.map(doc => {
    const warning = doc.needs_ocr_pages || doc.needs_review_pages || doc.failed_pages;
    return `
      <div class="document-row">
        <div>
          <div class="document-title" title="${escapeHtml(doc.title)}">${escapeHtml(doc.title)}</div>
          <div class="document-meta">
            <span>${escapeHtml(doc.material_type)}</span>
            <span>${doc.page_count} 页</span>
            <span>${doc.searchable_pages} 页可检索</span>
          </div>
          <span class="parse-badge ${warning ? 'warn' : ''}">${warning ? `需关注 ${doc.needs_ocr_pages + doc.needs_review_pages + doc.failed_pages} 页` : '解析完成'}</span>
        </div>
        <div class="doc-actions">
          ${writeable ? `<button class="icon-text" data-reparse="${doc.id}" type="button">重解析</button><button class="icon-text" data-delete="${doc.id}" type="button">删除</button>` : ''}
        </div>
      </div>
    `;
  }).join('') : `
    <div class="empty-state-library">
      <div class="empty-icon">▤</div>
      <p>当前空间还没有资料</p>
    </div>
  `;

  $$('[data-delete]').forEach(btn => btn.addEventListener('click', () => removeDocument(btn.dataset.delete)));
  $$('[data-reparse]').forEach(btn => btn.addEventListener('click', () => reparse(btn.dataset.reparse)));
}

async function removeDocument(documentId) {
  if (!window.confirm('确认删除这份资料？删除后不会再参与检索。')) return;
  try {
    await api(`/api/documents/${documentId}`, { method: 'DELETE' });
    toast('资料已删除，索引已失效', 'success');
    await loadSpaces();
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function reparse(documentId) {
  try {
    await api(`/api/documents/${documentId}/reparse`, { method: 'POST' });
    toast('资料已重新解析', 'success');
    await loadSpaces();
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function upload(file) {
  if (!state.currentSpace) {
    toast('请先选择一个知识库空间', 'error');
    return;
  }
  const form = new FormData();
  form.append('file', file);
  form.append('title', file.name.replace(/\.pdf$/i, ''));
  form.append('material_type', '用户上传资料');
  form.append('license_status', 'private-team-use');
  try {
    await api(`/api/spaces/${encodeURIComponent(state.currentSpace.id)}/documents`, { method: 'POST', body: form });
    toast('资料已导入', 'success');
    await loadSpaces();
  } catch (error) {
    toast(error.message, 'error');
  }
}

// ---------- Source selector ----------
function renderSourceList(listId, countId, onChange) {
  const count = $(`#${countId}`);
  const list = $(`#${listId}`);
  if (count) count.textContent = `已选 ${state.selectedDocumentIds.size} 份`;
  if (!list) return;

  const grouped = SOURCE_GROUPS.map(group => ({
    ...group,
    documents: state.documents.filter(doc => groupForDocument(doc).id === group.id),
  })).filter(group => group.documents.length);

  list.innerHTML = grouped.length ? grouped.map(group => `
    <div>
      <div class="source-group-label">${escapeHtml(group.title)}</div>
      <div class="source-group-list">
        ${group.documents.map(doc => `
          <label class="source-checkbox">
            <input type="checkbox" value="${escapeHtml(doc.id)}" ${state.selectedDocumentIds.has(doc.id) ? 'checked' : ''}>
            <div>
              <div class="source-title" title="${escapeHtml(doc.title)}">${escapeHtml(doc.title)}</div>
              <div class="source-meta">${escapeHtml(doc.material_type)} · ${doc.page_count} 页</div>
            </div>
          </label>
        `).join('')}
      </div>
    </div>
  `).join('') : '<div class="muted" style="font-size:.78rem;padding:8px 0">当前空间还没有可选资料</div>';

  $$(`#${listId} input[type="checkbox"]`).forEach(input => {
    input.addEventListener('change', () => {
      if (input.checked) state.selectedDocumentIds.add(input.value);
      else state.selectedDocumentIds.delete(input.value);
      onChange();
    });
  });
}

function renderSourceSelector() {
  renderSourceList('source-list', 'source-count', () => {
    clearAnswer('library');
    renderSourceSelector();
    renderHomeSourceSelector();
    updateQueryStatus();
  });
}

function renderHomeSourceSelector() {
  const panel = $('#home-source-selector');
  if (!panel) return;
  panel.classList.toggle('hidden', state.homeMode !== 'retrieval');
  renderSourceList('home-source-list', 'home-source-count', () => {
    renderSourceSelector();
    renderHomeSourceSelector();
    updateQueryStatus();
  });
}

function selectDocumentsByAction(action, context = 'library') {
  if (context !== 'home') clearAnswer('library');
  if (action === 'clear') {
    state.selectedDocumentIds.clear();
  } else if (action === 'all') {
    state.selectedDocumentIds = new Set(state.documents.map(doc => doc.id));
  } else {
    const group = SOURCE_GROUPS.find(item => item.id === action);
    state.selectedDocumentIds = new Set(
      state.documents.filter(doc => group && documentMatches(doc, group.keywords)).map(doc => doc.id)
    );
  }
  renderSourceSelector();
  renderHomeSourceSelector();
  updateQueryStatus();
}

function updateQueryStatus() {
  const el = $('#library-query-status');
  if (!el) return;
  el.textContent = state.selectedDocumentIds.size
    ? `将基于 ${state.selectedDocumentIds.size} 份已选资料回答`
    : (state.documents.length ? `将基于当前知识库全部 ${state.documents.length} 份资料回答` : '当前知识库还没有可用资料');
}

// ---------- Query ----------
function clearAnswer(prefix) {
  state.queryRequestId += 1;
  $(`#${prefix}-answer-area`).classList.add('hidden');
  $(`#${prefix}-answer-text`).replaceChildren();
  const citation = $(`#${prefix}-citation-section`);
  if (citation) citation.classList.add('hidden');
  const citationList = $(`#${prefix}-citation-list`);
  if (citationList) citationList.replaceChildren();
}

function scrollHomeToBottom() {
  const convo = $('#home-conversation');
  if (convo) convo.scrollTop = convo.scrollHeight;
}

function hideHomeGreeting() {
  const greeting = $('#home-greeting');
  if (greeting) greeting.style.display = 'none';
}

function resetHomeConversation() {
  state.homeConversation = [];
  state.activeHistoryId = null;
  const convo = $('#home-conversation');
  if (convo) {
    convo.querySelectorAll('.chat-row').forEach(el => el.remove());
    const greeting = $('#home-greeting');
    if (greeting) greeting.style.display = '';
  }
  renderHistoryActive();
  scrollHomeToBottom();
}

function renderHistoryActive() {
  $$('.history-item').forEach(el => {
    const idx = Number(el.dataset.historyIndex);
    const time = Number(state.history[idx]?.time);
    el.classList.toggle('active', time === state.activeHistoryId);
  });
}

function appendHomeUserMessage(question) {
  const convo = $('#home-conversation');
  if (!convo) return;
  hideHomeGreeting();
  const row = document.createElement('div');
  row.className = 'chat-row chat-row-user';
  const bubble = document.createElement('div');
  bubble.className = 'chat-bubble-user';
  bubble.textContent = question;
  row.appendChild(bubble);
  convo.appendChild(row);
  state.homeConversation.push({ role: 'user', content: question });
  scrollHomeToBottom();
}

function beginHomeAssistantMessage(mode) {
  const convo = $('#home-conversation');
  if (!convo) return null;
  hideHomeGreeting();
  const row = document.createElement('div');
  row.className = 'chat-row chat-row-assistant';

  const meta = document.createElement('div');
  meta.className = 'chat-meta';
  meta.textContent = mode === 'retrieval' ? '资料检索' : '直接问答';

  const bubble = document.createElement('div');
  bubble.className = 'chat-bubble-assistant';
  bubble.innerHTML = '<p class="muted">思考中…</p>';

  const cite = document.createElement('div');
  cite.className = 'chat-citation citation-section hidden';
  const citeHeading = document.createElement('div');
  citeHeading.className = 'citation-heading';
  citeHeading.textContent = '引用来源';
  const citeList = document.createElement('div');
  citeList.className = 'citation-list';
  cite.appendChild(citeHeading);
  cite.appendChild(citeList);

  row.appendChild(meta);
  row.appendChild(bubble);
  row.appendChild(cite);
  convo.appendChild(row);
  scrollHomeToBottom();
  return { textEl: bubble, modeEl: meta, citationSection: cite, citationList: citeList };
}

function renderHomeAnswer(result, mode, ctx) {
  ctx.modeEl.textContent = result.degraded
    ? (mode === 'direct' ? '模型不可用' : '检索降级')
    : (mode === 'direct' ? '直接回答' : '资料回答');
  ctx.modeEl.className = `chat-meta${result.degraded ? ' warn' : ''}`;
  ctx.textEl.innerHTML = renderMarkdown(result.answer);
  renderMath(ctx.textEl);

  const citations = result.citations || [];
  const showCitations = mode !== 'direct' && citations.length > 0;
  if (ctx.citationSection) ctx.citationSection.classList.toggle('hidden', !showCitations);
  if (ctx.citationList) {
    ctx.citationList.innerHTML = citations.length ? citations.map(source => `
      <div class="citation-item">
        <strong>[${escapeHtml(source.id)}] ${escapeHtml(source.document_title)} · 第 ${source.page} 页</strong>
        <div class="citation-excerpt">${escapeHtml(source.excerpt)}</div>
      </div>
    `).join('') : '';
  }
  const answerText = String(result.answer || '').trim();
  if (answerText) state.homeConversation.push({ role: 'assistant', content: answerText, mode });
  scrollHomeToBottom();
}

function renderAnswer(result, mode, prefix) {
  const area = $(`#${prefix}-answer-area`);
  const textEl = $(`#${prefix}-answer-text`);
  const modeEl = $(`#${prefix}-answer-mode`);
  const citationSection = $(`#${prefix}-citation-section`);
  const citationList = $(`#${prefix}-citation-list`);

  area.classList.remove('hidden');
  modeEl.textContent = result.degraded
    ? (mode === 'direct' ? '模型不可用' : '检索降级')
    : (mode === 'direct' ? '直接回答' : '资料回答');
  modeEl.className = `mode-pill ${result.degraded ? 'warn' : ''}`;

  textEl.innerHTML = renderMarkdown(result.answer);
  renderMath(textEl);

  const citations = result.citations || [];
  if (citationSection) citationSection.classList.toggle('hidden', mode === 'direct');
  if (citationList) {
    citationList.innerHTML = citations.length ? citations.map(source => `
      <div class="citation-item">
        <strong>[${escapeHtml(source.id)}] ${escapeHtml(source.document_title)} · 第 ${source.page} 页</strong>
        <div class="citation-excerpt">${escapeHtml(source.excerpt)}</div>
      </div>
    `).join('') : '<div class="muted" style="font-size:.78rem">本次回答没有可验证引用</div>';
  }
}

async function query(question, mode, prefix) {
  if (state.isQuerying || !question.trim()) return;
  if (!state.user) {
    toast('请先选择身份', 'error');
    return;
  }

  let documentIds = [];
  if (mode === 'retrieval') {
    documentIds = [...state.selectedDocumentIds];
    if (!documentIds.length && state.documents.length) {
      documentIds = state.documents.map(doc => doc.id);
      state.selectedDocumentIds = new Set(documentIds);
      renderSourceSelector();
      renderHomeSourceSelector();
    }
    if (!documentIds.length) {
      toast('资料模式需要至少一份资料，请先上传或选择', 'error');
      showView('library');
      return;
    }
  }

  setLoading(true);
  const isHome = prefix === 'home';
  const isFirstHomeQuestion = isHome && state.homeConversation.length === 0;
  const requestId = ++state.queryRequestId;

  let ctx;
  if (isHome) {
    appendHomeUserMessage(question);
    if (isFirstHomeQuestion) addHistory(question, '');
    ctx = beginHomeAssistantMessage(mode);
  } else {
    clearAnswer(prefix);
    ctx = {
      textEl: $(`#${prefix}-answer-text`),
      modeEl: $(`#${prefix}-answer-mode`),
      citationSection: $(`#${prefix}-citation-section`),
      citationList: $(`#${prefix}-citation-list`),
    };
    $(`#${prefix}-answer-area`).classList.remove('hidden');
  }
  const textEl = ctx.textEl;

  let waitingMessageTimer = null;
  waitingMessageTimer = setTimeout(() => {
    if (requestId === state.queryRequestId && textEl.innerHTML.includes('思考中')) {
      textEl.innerHTML = '<p class="muted">仍在思考，请稍候…</p>';
    }
  }, 8000);

  try {
    const messages = isHome
      ? state.homeConversation.slice(0, -1).map(({ role, content }) => ({ role, content }))
      : [];
    const payload = mode === 'direct'
      ? { question, mode: 'direct', scope: 'general', messages }
      : {
          question,
          mode: 'retrieval',
          scope: 'knowledge_base',
          space_id: state.currentSpace?.id || null,
          document_ids: documentIds,
          top_k: 5,
          messages,
        };
    const result = await api('/api/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (requestId !== state.queryRequestId) return;
    if (result.model) {
      state.modelName = result.model;
      updateHomeModelLabel();
    }
    if (isHome) {
      renderHomeAnswer(result, mode, ctx);
      if (state.activeHistoryId === null) addHistory(question, result.answer);
      else updateActiveHistoryPreview(result.answer, mode);
    } else {
      renderAnswer(result, mode, prefix);
    }
  } catch (error) {
    if (requestId !== state.queryRequestId) return;
    textEl.innerHTML = `<p class="math-render-error">请求失败：${escapeHtml(error.message)}</p>`;
    if (isHome) {
      const errorText = String(error.message || '未知错误').slice(0, 200);
      state.homeConversation.push({ role: 'assistant', content: `(请求失败：${errorText})`, mode });
    }
  } finally {
    clearTimeout(waitingMessageTimer);
    if (requestId === state.queryRequestId) setLoading(false);
  }
}

// ---------- Home ----------
function updateHomeModeLabel() {
  const label = $('#home-mode-label');
  if (label) label.textContent = state.homeMode === 'retrieval' ? '知识检索' : '直接问答';
  $$('.home-mode-button').forEach(button => {
    button.classList.toggle('active', button.dataset.homeMode === state.homeMode);
    button.setAttribute('aria-pressed', button.dataset.homeMode === state.homeMode ? 'true' : 'false');
  });
  renderHomeSourceSelector();
}

function updateHomeModelLabel() {
  const label = $('#home-current-model');
  if (label) label.textContent = `当前模型：${state.modelName || '未配置'}`;
}

function setHomeMode(mode) {
  if (!['direct', 'retrieval'].includes(mode) || state.isQuerying) return;
  state.homeMode = mode;
  updateHomeModeLabel();
  if (mode === 'retrieval' && !state.currentSpace) {
    toast('请先选择一个知识库空间', 'error');
    showView('library');
  }
}

function handleHomeSubmit(event) {
  event.preventDefault();
  const textarea = $('#home-question');
  const question = textarea.value.trim();
  if (!question) return;
  query(question, state.homeMode, 'home');
  textarea.value = '';
  textarea.style.height = 'auto';
}

function handleHomeShortcuts(shortcut) {
  const textarea = $('#home-question');
  if (shortcut === 'explain') {
    setHomeMode('retrieval');
    showView('library');
    toast('已切换到知识检索，请选择资料后提问', 'success');
  } else if (shortcut === 'image') {
    toast('图像生成功能即将推出', '');
  } else if (shortcut === 'record') {
    toast('录音纪要功能即将推出', '');
  }
}

// ---------- History ----------
function sortHistory() {
  state.history.sort((a, b) =>
    Number(Boolean(b.pinned)) - Number(Boolean(a.pinned))
    || Number(b.time || 0) - Number(a.time || 0)
  );
}

function loadHistory() {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    state.history = Array.isArray(parsed) ? parsed
      .filter(item => item && typeof item.question === 'string' && item.question.trim())
      .map((item, index) => ({
        ...item,
        question: item.question.trim(),
        preview: String(item.preview || ''),
        time: Number(item.time) || Date.now() - index,
        pinned: Boolean(item.pinned),
        conversation: Array.isArray(item.conversation) ? item.conversation : [],
      })) : [];
    sortHistory();
  } catch {
    state.history = [];
  }
  renderHistory();
}

function saveHistory() {
  sortHistory();
  state.history = state.history.slice(0, 30);
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(state.history));
  } catch {}
  renderHistory();
}

function addHistory(question, answer) {
  const answerText = String(answer || '');
  const preview = answerText.replace(/\s+/g, ' ').slice(0, 60) + (answerText.length > 60 ? '…' : '');
  state.history.unshift({
    question,
    preview,
    time: Date.now(),
    pinned: false,
    mode: state.homeMode,
    conversation: state.homeConversation.slice(),
  });
  state.activeHistoryId = state.history[0].time;
  saveHistory();
}

function updateActiveHistoryPreview(answer, mode) {
  if (state.activeHistoryId === null) return;
  const item = state.history.find(entry => entry.time === state.activeHistoryId);
  if (!item) return;
  const answerText = String(answer || '');
  item.preview = answerText.replace(/\s+/g, ' ').slice(0, 60) + (answerText.length > 60 ? '…' : '');
  item.mode = mode === 'retrieval' ? 'retrieval' : 'direct';
  item.conversation = state.homeConversation.slice();
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(state.history.slice(0, 30)));
  } catch {}
}

function openHistory(index) {
  const item = state.history[index];
  if (!item) return;
  resetHomeConversation();
  state.homeMode = item.mode === 'retrieval' ? 'retrieval' : 'direct';
  updateHomeModeLabel();
  state.activeHistoryId = item.time;
  state.homeConversation = Array.isArray(item.conversation) ? item.conversation.slice() : [];
  renderHomeConversation();
  showView('home');
}

function appendHomeMessageBubble(entry) {
  const convo = $('#home-conversation');
  if (!convo || !entry || !entry.role) return;
  hideHomeGreeting();
  if (entry.role === 'user') {
    const row = document.createElement('div');
    row.className = 'chat-row chat-row-user';
    const bubble = document.createElement('div');
    bubble.className = 'chat-bubble-user';
    bubble.textContent = entry.content;
    row.appendChild(bubble);
    convo.appendChild(row);
    return;
  }
  if (entry.role === 'assistant') {
    const row = document.createElement('div');
    row.className = 'chat-row chat-row-assistant';
    const meta = document.createElement('div');
    meta.className = 'chat-meta';
    meta.textContent = entry.mode === 'retrieval' ? '资料回答' : '直接回答';
    const bubble = document.createElement('div');
    bubble.className = 'chat-bubble-assistant';
    bubble.innerHTML = renderMarkdown(entry.content || '');
    renderMath(bubble);
    row.appendChild(meta);
    row.appendChild(bubble);
    convo.appendChild(row);
  }
}

function renderHomeConversation() {
  const convo = $('#home-conversation');
  if (!convo) return;
  convo.querySelectorAll('.chat-row').forEach(el => el.remove());
  if (!state.homeConversation.length) {
    const greeting = $('#home-greeting');
    if (greeting) greeting.style.display = '';
    scrollHomeToBottom();
    return;
  }
  hideHomeGreeting();
  const snapshot = state.homeConversation.slice();
  for (const entry of snapshot) {
    appendHomeMessageBubble(entry);
  }
  scrollHomeToBottom();
}

function closeHistoryMenus() {
  $$('.history-menu').forEach(menu => menu.classList.add('hidden'));
  $$('.history-menu-button').forEach(button => button.setAttribute('aria-expanded', 'false'));
}

function handleHistoryAction(action, index) {
  const item = state.history[index];
  if (!item) return;

  if (action === 'pin') {
    item.pinned = !item.pinned;
    saveHistory();
    toast(item.pinned ? '会话已置顶' : '已取消置顶', 'success');
    return;
  }

  if (action === 'rename') {
    const nextName = window.prompt('重命名会话', item.question);
    if (nextName === null) return;
    const title = nextName.trim();
    if (!title) {
      toast('会话名称不能为空', 'error');
      return;
    }
    item.question = title.slice(0, 2000);
    saveHistory();
    toast('会话已重命名', 'success');
    return;
  }

  if (action === 'delete' && window.confirm(`确认删除会话“${item.question}”？`)) {
    state.history.splice(index, 1);
    if (state.activeHistoryId === item.time) {
      state.activeHistoryId = null;
      resetHomeConversation();
    }
    saveHistory();
    toast('会话已删除', 'success');
  }
}

function renderHistory() {
  const list = $('#history-list');
  if (!list) return;
  list.innerHTML = state.history.length ? state.history.map((item, index) => `
    <div class="history-item${item.pinned ? ' pinned' : ''}" data-history-index="${index}">
      <button class="history-open" data-history-open="${index}" type="button" title="${escapeHtml(item.question)}">
        ${item.pinned ? '<span class="history-pin-indicator" aria-label="已置顶"></span>' : ''}
        <span class="history-title">${escapeHtml(item.question)}</span>
      </button>
      <button class="history-menu-button" data-history-menu="${index}" type="button" aria-label="会话操作" aria-expanded="false">⋯</button>
      <div class="history-menu hidden" role="menu">
        <button type="button" role="menuitem" data-history-action="pin" data-history-index="${index}">${item.pinned ? '取消置顶' : '置顶'}</button>
        <button type="button" role="menuitem" data-history-action="rename" data-history-index="${index}">重命名</button>
        <button type="button" role="menuitem" class="danger" data-history-action="delete" data-history-index="${index}">删除</button>
      </div>
    </div>
  `).join('') : '<div class="muted" style="font-size:.72rem;padding:8px 10px">还没有问答记录</div>';

  $$('[data-history-open]').forEach(button => {
    button.addEventListener('click', () => openHistory(Number(button.dataset.historyOpen)));
  });

  $$('[data-history-menu]').forEach(button => {
    button.addEventListener('click', event => {
      event.stopPropagation();
      const menu = button.parentElement.querySelector('.history-menu');
      const shouldOpen = menu.classList.contains('hidden');
      closeHistoryMenus();
      if (shouldOpen) {
        menu.classList.remove('hidden');
        button.setAttribute('aria-expanded', 'true');
        window.requestAnimationFrame(() => menu.scrollIntoView({ block: 'nearest' }));
      }
    });
  });

  $$('[data-history-action]').forEach(button => {
    button.addEventListener('click', event => {
      event.stopPropagation();
      handleHistoryAction(button.dataset.historyAction, Number(button.dataset.historyIndex));
    });
  });
}

// ---------- Settings ----------
async function loadSettings() {
  if (!state.user) return;
  try {
    state.settings = await api('/api/settings');
    state.modelName = state.settings.llm_model || '';
    updateHomeModelLabel();
    renderSettings();
  } catch (error) {
    toast(error.message, 'error');
  }
}

function renderSettings() {
  $('#setting-base-url').value = state.settings.llm_base_url || '';
  $('#setting-api-key').value = '';
  $('#setting-api-key').placeholder = state.settings.llm_configured ? '已配置，留空表示保持不变' : 'sk-...';
  state.apiKeyTouched = false;
  $('#setting-model').value = state.settings.llm_model || '';
  $('#setting-timeout').value = state.settings.llm_timeout_seconds || 45;
  const versionEl = $('#about-version');
  if (versionEl && state.settings?.version) versionEl.textContent = `v${state.settings.version}`;
  $('#about-model-status').textContent = state.settings.llm_configured ? '已配置' : '未配置';
}

async function saveSettings(event) {
  event.preventDefault();
  const payload = {
    llm_base_url: $('#setting-base-url').value.trim() || null,
    llm_model: $('#setting-model').value.trim() || null,
    llm_timeout_seconds: Number($('#setting-timeout').value) || 45,
  };
  // 仅当用户真正改过密钥字段时才发送，避免把空值/掩码误存为真实 key。
  if (state.apiKeyTouched) {
    payload.llm_api_key = $('#setting-api-key').value.trim();
  }
  try {
    state.settings = await api('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    state.modelName = state.settings.llm_model || '';
    updateHomeModelLabel();
    renderSettings();
    toast('设置已保存', 'success');
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function testSettings() {
  const btn = $('#settings-test');
  btn.disabled = true;
  btn.textContent = '测试中…';
  try {
    const result = await api('/api/settings/test', { method: 'POST' });
    if (result.ok) {
        toast('连接测试成功', 'success');
    } else {
        const code = result.model_error?.code || '未知错误';
        const detail = result.model_error?.message ? `：${result.model_error.message}` : '';
        toast(`连接失败：${code}${detail}`, 'error');
    }
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '测试连接';
  }
}

function updateAbout(health) {
  const healthEl = $('#about-health');
  const versionEl = $('#about-version');
  if (versionEl && health?.version) versionEl.textContent = `v${health.version}`;
  if (!healthEl) return;
  if (!health) {
    healthEl.textContent = '检查中';
    return;
  }
  healthEl.textContent = health.database && health.search ? '服务正常' : '检索待检查';
  healthEl.style.color = health.database && health.search ? 'var(--accent)' : 'var(--warning)';
}

// ---------- Init ----------
function initEventListeners() {
  // Navigation
  $$('.nav-item').forEach(item => {
    item.addEventListener('click', (e) => {
      e.preventDefault();
      showView(item.dataset.view);
    });
  });

  // Auth
  $('#logout-button').addEventListener('click', logout);
  $('#user-card').addEventListener('click', openLoginModal);

  // Home
  $('#home-query-form').addEventListener('submit', handleHomeSubmit);
  $$('.home-mode-button').forEach(button => {
    button.addEventListener('click', () => setHomeMode(button.dataset.homeMode));
  });
  $$('[data-home-source-action]').forEach(button => {
    button.addEventListener('click', () => selectDocumentsByAction(button.dataset.homeSourceAction, 'home'));
  });
  $('#home-question').addEventListener('input', () => autoResize($('#home-question')));
  $('#home-question').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleHomeSubmit(e);
    }
  });
  $('#home-new-chat').addEventListener('click', () => {
    if (state.isQuerying) {
      toast('请等待当前回答完成', '');
      return;
    }
    if (state.homeConversation.length === 0) {
      toast('当前已是新对话', '');
      return;
    }
    resetHomeConversation();
    toast('已开启新对话', 'success');
  });
  $$('[data-shortcut]').forEach(btn => {
    btn.addEventListener('click', () => handleHomeShortcuts(btn.dataset.shortcut));
  });

  // Library
  $('#refresh-spaces').addEventListener('click', () => loadSpaces().catch(e => toast(e.message, 'error')));
  $('#library-query-form').addEventListener('submit', (e) => {
    e.preventDefault();
    query($('#library-question').value.trim(), 'retrieval', 'library');
  });
  $('#library-question').addEventListener('input', () => autoResize($('#library-question')));
  $('#library-question').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      query($('#library-question').value.trim(), 'retrieval', 'library');
    }
  });
  $$('[data-source-action]').forEach(btn => {
    btn.addEventListener('click', () => selectDocumentsByAction(btn.dataset.sourceAction));
  });
  $('#library-upload-input').addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (file) upload(file);
    e.target.value = '';
  });

  // Settings tabs
  $$('[data-settings-tab]').forEach(tab => {
    tab.addEventListener('click', (e) => {
      e.preventDefault();
      const target = tab.dataset.settingsTab;
      $$('[data-settings-tab]').forEach(t => t.classList.toggle('active', t.dataset.settingsTab === target));
      $$('.settings-tab').forEach(t => t.classList.toggle('hidden', t.id !== `settings-tab-${target}`));
    });
  });
  $('#settings-form').addEventListener('submit', saveSettings);
  $('#settings-test').addEventListener('click', testSettings);
  $('#setting-api-key').addEventListener('input', () => { state.apiKeyTouched = true; });

  // History
  $('#clear-history').addEventListener('click', () => {
    state.history = [];
    saveHistory();
  });
  document.addEventListener('click', closeHistoryMenus);
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') closeHistoryMenus();
  });
}

async function init() {
  loadHistory();
  initEventListeners();
  initResizeHandles();
  initRouting();
  updateHomeModeLabel();
  await loadBase();
}

document.addEventListener('DOMContentLoaded', init);

// ---------- Resize handles ----------
const PANEL_LIMITS = {
  sidebar:       { min: 200, max: 400, default: 260 },
  librarySpaces: { min: 200, max: 380, default: 260 },
  libraryChat:   { min: 320, max: 720, default: 420 },
};

const PANEL_SELECTORS = {
  sidebar:       '.app-sidebar',
  librarySpaces: '.library-spaces',
  libraryChat:   '.library-chat',
};

const PANEL_CSS_VARS = {
  sidebar:       '--sidebar-width',
  librarySpaces: '--library-spaces-width',
  libraryChat:   '--library-chat-width',
};

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function loadPanelWidth(name) {
  const limits = PANEL_LIMITS[name];
  if (!limits) return 0;
  try {
    const raw = localStorage.getItem(`course-agent:panel-width:${name}`);
    const value = Number(raw);
    return Number.isFinite(value) ? clamp(value, limits.min, limits.max) : limits.default;
  } catch {
    return limits.default;
  }
}

function savePanelWidth(name, value) {
  try { localStorage.setItem(`course-agent:panel-width:${name}`, String(Math.round(value))); } catch {}
}

function applyPanelWidth(name, value) {
  const cssVar = PANEL_CSS_VARS[name];
  if (cssVar) document.documentElement.style.setProperty(cssVar, `${Math.round(value)}px`);
}

function getPanelWidth(name) {
  const selector = PANEL_SELECTORS[name];
  const el = selector ? $(selector) : null;
  if (el && el.getBoundingClientRect().width) {
    return el.getBoundingClientRect().width;
  }
  return PANEL_LIMITS[name]?.default ?? 0;
}

function initResizeHandles() {
  $$('.resize-handle').forEach(handle => {
    const name = handle.dataset.resize;
    const limits = PANEL_LIMITS[name];
    if (!limits) return;

    applyPanelWidth(name, loadPanelWidth(name));

    const beginDrag = (event) => {
      if (event.button !== undefined && event.button !== 0) return;
      event.preventDefault();
      handle.focus({ preventScroll: true });
      const startX = event.clientX;
      const startWidth = getPanelWidth(name);

      const onMove = (moveEvent) => {
        const next = clamp(startWidth + (moveEvent.clientX - startX), limits.min, limits.max);
        applyPanelWidth(name, next);
      };
      const onUp = () => {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        document.body.classList.remove('resizing');
        handle.classList.remove('dragging');
        savePanelWidth(name, getPanelWidth(name));
      };

      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
      document.body.classList.add('resizing');
      handle.classList.add('dragging');
    };

    handle.addEventListener('mousedown', beginDrag);

    handle.addEventListener('touchstart', (event) => {
      if (!event.touches.length) return;
      const touch = event.touches[0];
      beginDrag({ preventDefault: () => event.preventDefault(), clientX: touch.clientX, button: 0 });
    }, { passive: false });

    handle.addEventListener('keydown', (event) => {
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
      event.preventDefault();
      const step = event.shiftKey ? 24 : 8;
      const delta = event.key === 'ArrowRight' ? step : -step;
      const next = clamp(getPanelWidth(name) + delta, limits.min, limits.max);
      applyPanelWidth(name, next);
      savePanelWidth(name, next);
    });
  });
}
