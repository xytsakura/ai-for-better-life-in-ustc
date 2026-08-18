export const HUB_API = Object.freeze({
  session: '/api/session',
  agents: '/api/agents',
  agent: (id) => `/api/agents/${encodeURIComponent(id)}`,
  chat: (id) => `/api/agents/${encodeURIComponent(id)}/chat`,
  gatewayRun: (id) => `/api/gateway/agents/${encodeURIComponent(id)}/runs`,
  registry: '/api/registry/agents',
  adminAgents: '/api/admin/agents',
  adminAgent: (id) => `/api/admin/agents/${encodeURIComponent(id)}`,
  reviewVersion: (id, versionId) => `/api/admin/agents/${encodeURIComponent(id)}/versions/${encodeURIComponent(versionId)}/review`,
  checkVersion: (id, versionId) => `/api/admin/agents/${encodeURIComponent(id)}/versions/${encodeURIComponent(versionId)}/checks`,
  suspend: (id) => `/api/admin/agents/${encodeURIComponent(id)}/suspend`,
  restore: (id) => `/api/admin/agents/${encodeURIComponent(id)}/restore`,
  deprecate: (id) => `/api/admin/agents/${encodeURIComponent(id)}/deprecate`,
  rollback: (id) => `/api/admin/agents/${encodeURIComponent(id)}/rollback`,
  launch: (id) => `/api/agents/${encodeURIComponent(id)}/launch`,
  workspaceStart: (id) => `/api/agents/${encodeURIComponent(id)}/workspace/start`,
  modelProfiles: '/api/model-profiles',
  modelProfile: (id) => `/api/model-profiles/${encodeURIComponent(id)}`,
  modelProfileTest: (id) => `/api/model-profiles/${encodeURIComponent(id)}/test`,
  modelProfileDiscover: (id) => `/api/model-profiles/${encodeURIComponent(id)}/discover`,
  modelBindings: '/api/model-bindings',
  modelBindingGlobal: '/api/model-bindings/global',
  modelBindingAgent: (id) => `/api/model-bindings/agents/${encodeURIComponent(id)}`,
});

export const ACCESS_LEVELS = Object.freeze({
  link: {
    label: 'Link App',
    tone: 'neutral',
    primary: '打开应用',
    secondary: '查看详情',
    chatEnabled: false,
    workspaceEnabled: false,
  },
  connected: {
    label: 'Connected Agent',
    tone: 'blue',
    primary: '立即对话',
    secondary: '查看详情',
    chatEnabled: true,
    workspaceEnabled: false,
  },
  featured: {
    label: 'Featured Agent',
    tone: 'gold',
    primary: '立即对话',
    secondary: '查看详情',
    chatEnabled: true,
    workspaceEnabled: true,
  },
});

export const ERROR_MESSAGES = Object.freeze({
  agent_not_found: '没有找到这个 Agent。',
  agent_not_active: '该 Agent 当前未上线，暂时不能调用。',
  agent_unavailable: '该 Agent 暂时离线，请稍后再试。',
  agent_timeout: 'Agent 响应超时，请稍后重试。',
  protocol_error: 'Agent 返回的协议事件不完整或格式不正确。',
  rate_limited: '请求过于频繁，请稍后再试。',
  upstream_error: 'Agent 服务返回异常，请稍后重试。',
  conformance_checks_not_passed: '机器验收尚未通过，不能批准这个版本。',
  request_too_large: '本次请求超过平台允许的大小。',
  response_too_large: 'Agent 返回内容超过平台允许的大小。',
});

export const DEMO_USERS = Object.freeze([
  { id: 'demo-a', name: '管理员 demo-a', role: 'admin', initials: '管' },
  { id: 'demo-b', name: '开发者 demo-b', role: 'developer', initials: '开' },
  { id: 'demo-c', name: '学生 demo-c', role: 'user', initials: '学' },
]);

export function normalizeAccessLevel(agent) {
  const raw = String(agent?.access_level || agent?.integration?.mode || agent?.active_version?.manifest?.integration?.mode || 'link').toLowerCase();
  if (agent?.featured === true || raw === 'featured') return 'featured';
  if (raw === 'connected' || raw === 'ag-ui' || raw === 'agui') return 'connected';
  return 'link';
}

export function accessMeta(agent) {
  return ACCESS_LEVELS[normalizeAccessLevel(agent)] || ACCESS_LEVELS.link;
}

export function normalizeAgent(raw) {
  const manifest = raw?.active_version?.manifest || raw?.manifest || raw || {};
  const integration = manifest.integration || raw?.integration || {};
  const access_level = normalizeAccessLevel({
    ...manifest,
    active_version: raw?.active_version,
    featured: raw?.featured,
    integration,
    access_level: raw?.access_level || manifest.access_level,
  });
  return {
    ...manifest,
    ...raw,
    id: raw?.agent_id || raw?.id || manifest.id || '',
    name: raw?.name || manifest.name || raw?.id || '未命名 Agent',
    description: raw?.summary || raw?.description || manifest.description || '暂无简介',
    owner: raw?.owner || manifest.owner || '未知维护者',
    category: raw?.category || manifest.category || '未分类',
    tags: Array.isArray(raw?.tags || manifest.tags) ? raw?.tags || manifest.tags : [],
    version: raw?.version || raw?.active_version?.version || manifest.version || '0.0.0',
    access_level,
    integration,
    capabilities: Array.isArray(raw?.capabilities || manifest.capabilities) ? raw?.capabilities || manifest.capabilities : [],
    health: normalizeHealth(raw?.health || raw?.latest_health || raw?.health_check || manifest.health),
    data_policy: raw?.data_policy || manifest.data_policy || '平台按最小必要原则传递身份和请求上下文。',
    icon: raw?.icon || manifest.icon || manifest.icon_url || '',
    usage_count: Number(raw?.usage_count || raw?.uses || 0),
    status: raw?.status || 'active',
    active_version: raw?.active_version,
    versions: raw?.versions || [],
  };
}

export function normalizeHealth(health) {
  if (!health) return { status: 'unknown', label: '未检查' };
  const status = String(health.status || health.state || 'unknown').toLowerCase();
  const label = health.label || ({
    ok: '可用',
    healthy: '可用',
    degraded: '暂时异常',
    offline: '离线',
    error: '协议异常',
    unknown: '未检查',
  }[status] || '未检查');
  return { ...health, status, label };
}

export function normalizeModelProfile(raw) {
  const {
    api_key: _apiKey,
    apiKey: _apiKeyCamel,
    encrypted_api_key: _encryptedApiKey,
    encryptedApiKey: _encryptedApiKeyCamel,
    ...source
  } = raw || {};
  const models = normalizeProfileModels(source.models || source.available_models || source.model_catalog || []);
  const defaultModel = source.default_model_id || source.default_model || source.model_id || source.model || models.find((item) => item.default)?.id || '';
  return {
    ...source,
    id: String(source.profile_id || source.id || ''),
    name: source.name || source.label || '未命名配置',
    provider: source.provider || 'openai',
    api_style: source.api_style || source.apiStyle || 'responses',
    base_url: source.base_url || source.baseUrl || '',
    status: source.status || (source.enabled === false ? 'disabled' : 'active'),
    has_api_key: Boolean(source.has_api_key ?? source.hasApiKey ?? source.api_key_fingerprint ?? source.apiKeyFingerprint ?? source.api_key_mask ?? source.mask ?? source.masked_api_key),
    api_key_mask: source.api_key_mask || source.mask || source.masked_api_key || '',
    api_key_fingerprint: source.api_key_fingerprint || source.fingerprint || source.apiKeyFingerprint || '',
    default_model_id: defaultModel,
    models,
    last_tested_at: source.last_tested_at || source.lastTestedAt || '',
    last_error: source.last_error || source.error || '',
  };
}

export function normalizeProfileModels(rawModels) {
  const list = Array.isArray(rawModels)
    ? rawModels
    : Array.isArray(rawModels?.models)
      ? rawModels.models
      : Array.isArray(rawModels?.data)
        ? rawModels.data
        : [];
  return list.map((item) => {
    const value = typeof item === 'string' ? { id: item } : item || {};
    const id = String(value.model_id || value.id || value.name || '');
    return {
      ...value,
      id,
      model_id: id,
      display_name: value.display_name || value.displayName || value.name || id,
      chat_eligible: value.chat_eligible ?? value.chatEligible ?? true,
      default: Boolean(value.default),
    };
  }).filter((item) => item.id);
}

export function normalizeModelProfilesPayload(payload) {
  const root = payload?.data && typeof payload.data === 'object' && !Array.isArray(payload.data) ? payload.data : payload;
  const profilesRaw = Array.isArray(root) ? root : root?.profiles || root?.items || root?.model_profiles || [];
  const bindingsRaw = Array.isArray(root) ? [] : root?.bindings || root?.model_bindings || {};
  return {
    profiles: (Array.isArray(profilesRaw) ? profilesRaw : []).map(normalizeModelProfile).filter((profile) => profile.id),
    bindings: normalizeModelBindings(bindingsRaw),
  };
}

export function normalizeModelBindings(raw) {
  const result = { global: null, agents: {} };
  if (!raw) return result;
  if (Array.isArray(raw)) {
    for (const item of raw) {
      const binding = normalizeModelBinding(item);
      if (binding.scope_type === 'global') result.global = binding;
      if (binding.scope_type === 'agent' && binding.scope_id) result.agents[binding.scope_id] = binding;
    }
    return result;
  }
  if (raw.global) result.global = normalizeModelBinding({ ...raw.global, scope_type: 'global' });
  const agents = raw.agents || raw.agent || {};
  if (Array.isArray(agents)) {
    for (const item of agents) {
      const binding = normalizeModelBinding({ ...item, scope_type: 'agent' });
      if (binding.scope_id) result.agents[binding.scope_id] = binding;
    }
    return result;
  }
  for (const [agentId, binding] of Object.entries(agents)) {
    result.agents[agentId] = normalizeModelBinding({ ...binding, scope_type: 'agent', scope_id: agentId });
  }
  return result;
}

function normalizeModelBinding(raw) {
  const wrapper = raw || {};
  const source = wrapper.binding && typeof wrapper.binding === 'object'
    ? { ...wrapper, ...wrapper.binding }
    : wrapper;
  return {
    ...source,
    scope_type: source.scope_type || source.scopeType || 'global',
    scope_id: source.scope_id || source.scopeId || source.agent_id || source.agentId || '',
    profile_id: source.profile_id || source.profileId || '',
    model_id: source.model_id || source.modelId || source.default_model_id || '',
  };
}

export function getAgentPrimaryAction(raw) {
  const agent = normalizeAgent(raw);
  const level = normalizeAccessLevel(agent);
  if (level === 'link') {
    return {
      kind: 'launch',
      href: HUB_API.launch(agent.id),
      label: ACCESS_LEVELS.link.primary,
      external: true,
    };
  }
  if (level === 'featured') {
    return {
      kind: 'workspace',
      href: '',
      label: ACCESS_LEVELS.featured.primary,
      external: false,
    };
  }
  return {
    kind: 'chat',
    href: `/hub/agents/${encodeURIComponent(agent.id)}/chat`,
    label: ACCESS_LEVELS.connected.primary,
    external: false,
  };
}

export function getAgentPrimaryHref(agent) {
  return getAgentPrimaryAction(agent).href;
}

export function getAgentSecondaryHref(agent) {
  const normalized = normalizeAgent(agent);
  return `/hub/agents/${encodeURIComponent(normalized.id)}`;
}

export function filterAgents(agents, { query = '', category = '全部', level = '全部', chip = '全部' } = {}) {
  const q = query.trim().toLowerCase();
  return agents.filter((agent) => {
    const normalized = normalizeAgent(agent);
    const searchable = [
      normalized.id,
      normalized.name,
      normalized.description,
      normalized.owner,
      normalized.category,
      ...normalized.tags,
      ...normalized.capabilities,
    ].join(' ').toLowerCase();
    const matchesQuery = !q || searchable.includes(q);
    const matchesCategory = category === '全部' || normalized.category === category;
    const matchesLevel = level === '全部' || accessMeta(normalized).label === level;
    const terms = new Set([...normalized.tags, ...normalized.capabilities, normalized.category]);
    const matchesChip = chip === '全部' || terms.has(chip);
    return matchesQuery && matchesCategory && matchesLevel && matchesChip;
  });
}

export function safeUrl(url) {
  if (!String(url || '').trim()) return '';
  try {
    const origin = typeof window !== 'undefined' && window.location?.origin
      ? window.location.origin
      : 'http://localhost';
    const parsed = new URL(url, origin);
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:' || parsed.pathname.startsWith('/')) {
      return parsed.href;
    }
  } catch {
    return '';
  }
  return '';
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function renderInlineMarkdown(text) {
  let out = escapeHtml(text);
  out = out.replace(/`([^`]+)`/g, '<code>$1</code>');
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  out = out.replace(/\[([^\]]+)]\(([^)]+)\)/g, (_match, label, href) => {
    const safe = safeUrl(href);
    if (!safe) return escapeHtml(label);
    return `<a href="${escapeHtml(safe)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>`;
  });
  out = out.replace(/\\\((.+?)\\\)/g, (_match, formula) => renderMath(formula, false));
  return out;
}

function renderMath(formula, displayMode) {
  const source = String(formula || '').trim();
  if (!source) return '';
  if (typeof window !== 'undefined' && window.katex?.renderToString) {
    try {
      return window.katex.renderToString(source, { displayMode, throwOnError: false, strict: 'ignore' });
    } catch {
      // Fall through to safe text fallback.
    }
  }
  const className = displayMode ? 'math math--block' : 'math';
  return `<span class="${className}">${escapeHtml(source)}</span>`;
}

export function renderMarkdownSafe(markdown) {
  const input = String(markdown || '').replace(/\r\n/g, '\n');
  const blocks = [];
  let working = input.replace(/```([\s\S]*?)```/g, (_match, code) => {
    const token = `\u0000CODE${blocks.length}\u0000`;
    blocks.push(`<pre><code>${escapeHtml(code.trim())}</code></pre>`);
    return token;
  });
  working = working.replace(/\\\[([\s\S]+?)\\\]/g, (_match, formula) => {
    const token = `\u0000CODE${blocks.length}\u0000`;
    blocks.push(renderMath(formula, true));
    return token;
  });

  const lines = working.split('\n');
  const html = [];
  let listOpen = false;

  function closeList() {
    if (listOpen) {
      html.push('</ul>');
      listOpen = false;
    }
  }

  for (const line of lines) {
    const trimmed = line.trim();
    const codeIndex = trimmed.match(/^\u0000CODE(\d+)\u0000$/);
    if (codeIndex) {
      closeList();
      html.push(blocks[Number(codeIndex[1])] || '');
      continue;
    }
    if (!trimmed) {
      closeList();
      continue;
    }
    const heading = trimmed.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      closeList();
      const level = heading[1].length + 2;
      html.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }
    const item = trimmed.match(/^[-*]\s+(.+)$/);
    if (item) {
      if (!listOpen) {
        html.push('<ul>');
        listOpen = true;
      }
      html.push(`<li>${renderInlineMarkdown(item[1])}</li>`);
      continue;
    }
    closeList();
    html.push(`<p>${renderInlineMarkdown(trimmed)}</p>`);
  }
  closeList();

  return html.join('');
}

export function parseSseBuffer(buffer) {
  const events = [];
  const parts = String(buffer).split(/\n\n/);
  const rest = parts.pop() || '';
  for (const part of parts) {
    const dataLines = part
      .split(/\n/)
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).trimStart());
    if (!dataLines.length) continue;
    const payload = dataLines.join('\n');
    if (payload === '[DONE]') {
      events.push({ type: 'RUN_FINISHED' });
      continue;
    }
    try {
      events.push(JSON.parse(payload));
    } catch {
      events.push({ type: 'RUN_ERROR', error: { code: 'protocol_error', message: '无法解析 SSE data JSON' } });
    }
  }
  return { events, rest };
}

export function errorFromAguiEvent(event) {
  const nested = event?.error;
  const detail = nested && typeof nested === 'object' ? nested : event || {};
  const code = detail.code || event?.code || 'upstream_error';
  const message = detail.message || event?.message || ERROR_MESSAGES[code] || ERROR_MESSAGES.upstream_error;
  const error = new Error(message);
  error.code = code;
  return error;
}

export function buildRunAgentInput({ agentId, user, threadId, runId, messages }) {
  return {
    threadId,
    runId,
    state: {},
    messages: messages.map((message, index) => ({
      id: message.id || `${runId}-message-${index + 1}`,
      role: message.role,
      content: message.content,
    })),
    tools: [],
    context: [
      {
        description: 'Campus Hub minimal request context',
        value: JSON.stringify({
          agent_id: agentId,
          user_id: user?.id,
          display_name: user?.name,
          role: user?.role,
        }),
      },
    ],
    forwardedProps: {
      source: 'campus-agent-hub-web',
      contract_version: '1.0',
    },
  };
}

export function validateManifest(manifest) {
  const errors = [];
  const value = manifest || {};
  const idPattern = /^[a-z0-9][a-z0-9-]{2,62}$/;
  if (!idPattern.test(String(value.id || ''))) errors.push('id 必须为 3-63 位小写字母、数字或连字符。');
  if (!value.name || String(value.name).trim().length < 2) errors.push('name 至少需要 2 个字符。');
  if (!value.description || String(value.description).trim().length < 8) errors.push('description 需要说明主要能力。');
  if (!/^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$/.test(String(value.version || ''))) errors.push('version 必须使用语义化版本，例如 0.1.0。');
  const mode = value.integration?.mode;
  if (!['link', 'connected'].includes(mode)) errors.push('integration.mode 只能是 link 或 connected；Featured 由平台审核授予。');
  if (mode === 'link' && !safeUrl(value.integration?.launch_url || '')) errors.push('Link App 必须提供安全的 launch_url。');
  if (mode === 'connected') {
    if (!safeUrl(value.integration?.chat_endpoint || '')) errors.push('Connected Agent 必须提供 chat_endpoint。');
    if (!safeUrl(value.integration?.health_endpoint || '')) errors.push('Connected Agent 必须提供 health_endpoint。');
  }
  return { ok: errors.length === 0, errors };
}

export function formatUsage(value) {
  const number = Number(value || 0);
  if (number >= 10000) return `${(number / 10000).toFixed(1)}w`;
  if (number >= 1000) return `${(number / 1000).toFixed(1)}k`;
  return String(number);
}
