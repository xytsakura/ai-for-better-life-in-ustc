const state = { user: null, users: [], spaces: [], space: null, documents: [], documentTotal: 0 };
const $ = (selector) => document.querySelector(selector);

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

function renderMath(container) {
  const renderer = window.katex;
  container.querySelectorAll('.math-inline, .math-block').forEach(element => {
    const source = element.textContent;
    if (!renderer) {
      element.classList.add('math-render-error');
      return;
    }
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

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: 'same-origin', ...options });
  const text = await response.text();
  let payload = null;
  try { payload = text ? JSON.parse(text) : null; } catch { payload = { error: { message: text } }; }
  if (!response.ok) throw new Error(payload?.error?.message || '请求失败');
  return payload;
}

function notice(message, error = false) {
  const element = $('#notice');
  element.textContent = message;
  element.classList.toggle('error', error);
  element.classList.remove('hidden');
  window.clearTimeout(notice.timer);
  notice.timer = window.setTimeout(() => element.classList.add('hidden'), 5000);
}

function renderUsers() {
  $('#user-list').innerHTML = state.users.map(user => `
    <button class="identity-button ${state.user?.id === user.id ? 'active' : ''}" data-user="${user.id}">
      <span class="identity-name">${escapeHtml(user.display_name)}</span>
      <span class="identity-id">${escapeHtml(user.id)}</span>
    </button>`).join('');
  document.querySelectorAll('[data-user]').forEach(button => button.addEventListener('click', () => login(button.dataset.user)));
}

function renderSpaces() {
  $('#space-count').textContent = state.spaces.length;
  $('#space-list').innerHTML = state.spaces.map(space => `
    <button class="space-button ${state.space?.id === space.id ? 'active' : ''}" data-space="${space.id}">
      <span class="space-dot ${space.space_type === 'personal' ? 'personal' : ''}"></span>
      <span><span class="space-name">${escapeHtml(space.name)}</span><span class="space-detail">${space.document_count} 份 · ${escapeHtml(space.role)}</span></span>
    </button>`).join('');
  document.querySelectorAll('[data-space]').forEach(button => button.addEventListener('click', () => selectSpace(button.dataset.space)));
}

function renderDocuments() {
  $('#document-count').textContent = `${state.documentTotal} 份资料`;
  const writeable = state.space && state.space.role !== 'reader';
  $('#document-list').innerHTML = state.documents.length ? state.documents.map(doc => {
    const warning = doc.needs_ocr_pages || doc.needs_review_pages || doc.failed_pages;
    return `<div class="document-row">
      <div>
        <div class="document-title" title="${escapeHtml(doc.title)}">${escapeHtml(doc.title)}</div>
        <div class="document-meta"><span>${escapeHtml(doc.material_type)}</span><span>${doc.page_count} 页</span><span>${doc.searchable_pages} 页可检索</span></div>
        <span class="parse-badge ${warning ? 'warn' : ''}">${warning ? `需关注 ${doc.needs_ocr_pages + doc.needs_review_pages + doc.failed_pages} 页` : '解析完成'}</span>
      </div>
      <div class="doc-actions">${writeable ? `<button class="icon-text" data-reparse="${doc.id}">重解析</button><button class="icon-text" data-delete="${doc.id}">删除</button>` : ''}</div>
    </div>`;
  }).join('') : '<div class="empty-list">当前空间还没有资料</div>';
  document.querySelectorAll('[data-delete]').forEach(button => button.addEventListener('click', () => removeDocument(button.dataset.delete)));
  document.querySelectorAll('[data-reparse]').forEach(button => button.addEventListener('click', () => reparse(button.dataset.reparse)));
}

async function loadHealth() {
  try {
    const health = await api('/api/health');
    const badge = $('#health-badge');
    badge.textContent = health.database && health.search ? '服务正常' : '检索待检查';
    badge.classList.add(health.database && health.search ? 'ok' : 'warn');
    $('#model-label').textContent = health.llm_configured ? 'gpt-5.6-sol' : '模型未配置 · 可离线检索';
  } catch { $('#health-badge').textContent = '服务不可用'; }
}

async function loadBase() {
  const [users, session] = await Promise.all([api('/api/users'), api('/api/session')]);
  state.users = users.items;
  state.user = session.user;
  renderUsers();
  if (state.user) await loadSpaces();
  updateView();
}

async function login(userId) {
  try {
    const result = await api('/api/session', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ user_id: userId }) });
    state.user = result.user;
    await loadSpaces();
    renderUsers();
    updateView();
  } catch (error) { notice(error.message, true); }
}

async function logout() {
  await api('/api/session', { method: 'DELETE' });
  state.user = null; state.spaces = []; state.space = null; state.documents = []; state.documentTotal = 0;
  renderUsers(); updateView();
}

async function loadSpaces() {
  const result = await api('/api/spaces');
  state.spaces = result.items;
  state.space = state.spaces.find(item => item.id === state.space?.id) || state.spaces.find(item => item.id === 'math-b1-shared') || state.spaces[0];
  renderSpaces();
  if (state.space) await loadDocuments();
}

async function selectSpace(spaceId) {
  state.space = state.spaces.find(item => item.id === spaceId);
  renderSpaces();
  await loadDocuments();
}

async function loadDocuments() {
  if (!state.space) return;
  const result = await api(`/api/spaces/${encodeURIComponent(state.space.id)}/documents?page_size=100`);
  state.documents = result.items;
  state.documentTotal = result.total;
  $('#space-title').textContent = state.space.name;
  $('#space-role').textContent = `当前角色：${state.space.role}`;
  renderDocuments();
}

async function removeDocument(documentId) {
  if (!window.confirm('确认删除这份资料？删除后不会再参与检索。')) return;
  try { await api(`/api/documents/${documentId}`, { method: 'DELETE' }); notice('资料已删除，索引已失效'); await loadSpaces(); } catch (error) { notice(error.message, true); }
}

async function reparse(documentId) {
  try { await api(`/api/documents/${documentId}/reparse`, { method: 'POST' }); notice('资料已重新解析'); await loadSpaces(); } catch (error) { notice(error.message, true); }
}

async function upload(file) {
  if (!state.space) return;
  const form = new FormData();
  form.append('file', file); form.append('title', file.name.replace(/\.pdf$/i, '')); form.append('material_type', '用户上传资料'); form.append('license_status', 'private-team-use');
  try { await api(`/api/spaces/${encodeURIComponent(state.space.id)}/documents`, { method: 'POST', body: form }); notice('资料已导入'); await loadSpaces(); } catch (error) { notice(error.message, true); }
}

function renderAnswer(result) {
  $('#answer-card').classList.remove('hidden');
  $('#answer-mode').textContent = result.degraded ? '检索降级' : '模型回答';
  $('#answer-mode').className = `status-pill ${result.degraded ? 'warn' : 'ok'}`;
  const answerElement = $('#answer-text');
  answerElement.innerHTML = renderMarkdown(result.answer);
  renderMath(answerElement);
  $('#citation-list').innerHTML = result.citations.length ? result.citations.map(source => `<div class="citation-item"><strong>[${escapeHtml(source.id)}] ${escapeHtml(source.document_title)} · 第 ${source.page} 页</strong><div class="citation-excerpt">${escapeHtml(source.excerpt)}</div></div>`).join('') : '<div class="empty-list">本次回答没有可验证引用</div>';
}

async function query(event) {
  event.preventDefault();
  const question = $('#question').value.trim();
  if (!question) return;
  $('#query-status').textContent = '检索与生成中…';
  try {
    const result = await api('/api/query', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question, space_ids: [state.space.id], top_k: 5 }) });
    renderAnswer(result); $('#query-status').textContent = result.degraded ? '已返回检索降级结果' : '回答完成';
  } catch (error) { $('#query-status').textContent = '请求失败'; notice(error.message, true); }
}

function updateView() {
  $('#login-empty').classList.toggle('hidden', Boolean(state.user));
  $('#app-view').classList.toggle('hidden', !state.user);
  $('#current-user').textContent = state.user?.display_name || '未选择';
}

$('#query-form').addEventListener('submit', query);
$('#upload-input').addEventListener('change', event => { const file = event.target.files[0]; if (file) upload(file); event.target.value = ''; });
$('#refresh-button').addEventListener('click', () => loadSpaces().catch(error => notice(error.message, true)));
$('#logout-button').addEventListener('click', logout);
loadHealth();
loadBase().catch(error => notice(error.message, true));
