# HUB 框架规格说明书 — 插件式 Agent 接入与展示框架（Agent Hub）

> 文档类型：面向机器可读的产品 + 实现规格说明书
> 目标读者：实现或审查本代码的 LLM / AI 编程智能体
> 语言：中文为主体，自由文本字段（如 name/description）允许中文，代码/标识符用英文
> 状态：已确认的设计基线
> 仓库根目录：/Users/bilibili/107杯/107杯/ai-for-better-life-in-ustc
> 关联文档：项目产品文档.md（产品基线）、report.md（调研依据）、apps/course-agent/（第一个 Agent）

---

## 0. 概要（给实现智能体看）

你要构建一个**插件式 Agent Hub**。它不是多 Agent 编排器。
**没有 supervisor、没有主 Agent、没有 Agent 之间的互调（无 A2A）。**

Hub 只做四件事：
1. 定义统一接入契约（manifest + AG-UI 协议）。
2. 维护 Agent manifest 的注册中心（Registry），带管理员审核/审批。
3. 提供反向代理网关（Gateway），只把用户请求**转发**给被选中的 Agent（不做调度）。
4. 提供前端门户（Portal）：已上线 Agent 的卡片列表 + 一个能与所选 Agent 用 AG-UI 对话的聊天界面。

第一个要接入的 Agent 是 `apps/course-agent`（课程资料学习助手），它需要增加一个 **AG-UI 适配层**。

---

## 1. 硬性决策（不要再讨论）

| 编号 | 决策项 | 取值 |
|------|--------|------|
| D1 | 交互协议 | **AG-UI**（Agent↔用户的事件流，基于 SSE）。不用 A2A。MCP 仅作为 `protocol` 的可选枚举值。 |
| D2 | course-agent 适配方式 | 在 course-agent 中**新增 AG-UI 适配端点**，不重写其内部逻辑。 |
| D3 | 网关模式 | **后端反向代理**，路径 `/agents/{agent_id}/chat` 及 AG-UI 事件子路径。只转发，不调度。 |
| D4 | 代码位置 | 本仓库内新增子应用 **`apps/hub`**，复用 course-agent 已有的 `db.py`/鉴权/会话基础设施。 |
| D5 | 注册方式 | **自注册 API + 管理员审核**。`POST /registry` → 状态 `pending` → 管理员审批 → `active`。 |
| D6 | LLM 网关 | **v1 不统一**。各 Agent 自带 API Key。Hub 只做注册/转发/展示。 |
| D7 | 持久化存储 | SQLite，复用现有 `runtime_dir/course-agent.sqlite3` 的扩展表方式（独立新增表）。 |

---

## 2. 架构（数据流）

```
终端用户（浏览器）
  │
  ├─ GET  /hub                  → 从 GET /api/hub/agents（仅 active）渲染 Agent 卡片
  └─ 点击卡片 → 打开聊天界面（AG-UI 客户端），指向 /api/hub/agents/{id}/chat
                                                     │
                                                     │ SSE（AG-UI 事件流）
                                                     ▼
你的后端（apps/hub）                        Agent 提供方（第三方）
  ┌──────────────────────────────────┐       ┌──────────────────────────┐
  │ Registry（SQLite: hub_agents）    │       │ 任意技术栈（LangGraph、  │
  │ Gateway（只转发）                 │ ──代理──► │ FastAPI、MCP server…） │
  │ 管理员审核 UI / API              │       │ 暴露 AG-UI 端点           │
  │ 鉴权（复用 course-agent 登录）    │       └──────────────────────────┘
  └──────────────────────────────────┘
        ▲
        │ POST /api/hub/registry（提交 manifest，status=pending）
   第三方 Agent 提供方
```

代码必须遵守的不变量（INVARIANTS）：
- 网关**绝不**决定调用哪个 Agent。Agent id 来自 URL 路径，由用户在 Portal 中点选决定。
- 网关**绝不**修改 Agent 响应的语义内容；以字节流方式透传（对 SSE 至关重要）。
- Agent 之间**绝不**通过 Hub 互相调用。
- 只有 `active` 状态的 Agent 出现在 Portal 卡片列表中。

---

## 3. 接入契约

### 3.1 AgentManifest（"统一形式"）

对 Hub 而言，每个 Agent 就等价于"一份 manifest + 一个 endpoint"。Schema（权威定义）：

```json
{
  "id": "pdf-summarizer",            // 必填，全局唯一，[a-z0-9-]{3,64}
  "name": "PDF 摘要助手",            // 必填，展示名，1..64 字符
  "description": "上传 PDF，自动生成摘要。", // 必填，1..512 字符
  "icon": "https://cdn.example.com/icons/pdf.svg", // 选填，URL 或 null
  "category": "文档处理",            // 选填，默认 "未分类"
  "tags": ["pdf", "摘要"],           // 选填，字符串列表
  "endpoint": "https://agents.alice.dev/pdf-summarizer", // 必填，绝对 URL（推荐 https）
  "protocol": "ag-ui",               // 必填，枚举："ag-ui" | "mcp" | "simple-chat"
  "auth": { "type": "none" },        // 必填结构；type: "none" | "apikey" | "oauth2"
  "capabilities": ["file-upload", "streaming"], // 选填列表
  "owner": "alice@external.dev",     // 必填，联系标识
  "version": "1.0.0",                // 选填，默认 "1.0.0"
  "status": "pending"                // 由 Hub 管理，提交时客户端填了也忽略
}
```

校验规则（实现为 pydantic 模型 `AgentManifest`）：
- `id`：正则 `^[a-z0-9-]{3,64}$`；若 Registry 中已存在（大小写不敏感）则拒绝。
- `endpoint`：必须是绝对 URL；若 `auth.type == "none"` 且 scheme 为 `http`（非 `https`），除非开发标志允许 localhost（复用现有 allow-local 模式），否则拒绝。
- `protocol == "ag-ui"` 是 v1 唯一完整支持路径；`mcp` / `simple-chat` 在 schema 中接受，但 Gateway 需经人工核验备注后才置为 `active`。
- `status` 由服务端控制；客户端提交时忽略其传入的 `status` 字段。

### 3.2 交互协议 = AG-UI

AG-UI = Agent↔用户的交互协议（基于 SSE/WebSocket 的事件流）。
- Hub 前端对**所有 Agent 共用同一套 AG-UI 客户端组件**，不为每个 Agent 单独写 UI。
- 聊天界面在 v1 至少要支持的 AG-UI 事件类型：
  `RUN_STARTED`、`TEXT_MESSAGE_START`、`TEXT_MESSAGE_CONTENT`、`TEXT_MESSAGE_END`、
  `TOOL_CALL_START`、`TOOL_CALL_END`、`RUN_FINISHED`、`RUN_ERROR`。
- 传输方式：SSE（`text/event-stream`）。线格式：AG-UI 标准 `event:` / `data:` 帧。
- 网关在 v1 逐字节透传 SSE 流，服务端**不解析** AG-UI 事件（未来可观测钩子除外）。

### 3.3 第三方如何接入（三步，写入接入指南）

1. 用任意技术栈实现 Agent，并暴露一个 **AG-UI 端点**（SSE）。
2. 通过 `POST /api/hub/registry` 提交一份 `AgentManifest`（或提 PR 增加一个 manifest JSON —— 两种方式都接受）。
3. 管理员审批通过后，该 Agent 卡片自动出现在 Portal；用户即可点选使用。

---

## 4. 后端规格（apps/hub）

复用 course-agent 的：`db.py` 连接辅助、`current_user`/`require_admin` 会话鉴权、`Settings`、静态资源服务、错误封装 `_error(...)`。

### 4.1 新增数据库表（扩展现有 SQLite，同一 `runtime_dir`）

```sql
CREATE TABLE IF NOT EXISTS hub_agents (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  description   TEXT NOT NULL,
  icon          TEXT,
  category      TEXT NOT NULL DEFAULT '未分类',
  tags          TEXT NOT NULL DEFAULT '[]',        -- JSON 数组
  endpoint      TEXT NOT NULL,
  protocol      TEXT NOT NULL DEFAULT 'ag-ui',
  auth          TEXT NOT NULL DEFAULT '{"type":"none"}', -- JSON
  capabilities  TEXT NOT NULL DEFAULT '[]',        -- JSON 数组
  owner         TEXT NOT NULL,
  version       TEXT NOT NULL DEFAULT '1.0.0',
  status        TEXT NOT NULL DEFAULT 'pending',   -- pending|active|rejected|deprecated
  reviewer_id   TEXT,
  review_note   TEXT,
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
```

状态机：
`pending` →（`active` | `rejected`）；`active` →（`deprecated` | `pending`）；`deprecated`/`rejected` 在展示上为终态（不出现在 Portal）。

### 4.2 端点（全部位于 `/api/hub` 下）

| 方法 | 路径 | 鉴权 | 用途 |
|------|------|------|------|
| POST | `/api/hub/registry` | 任意已登录用户 | 提交 manifest → `pending`。返回 `{ok, id}`。 |
| GET | `/api/hub/agents` | 任意已登录用户 | 仅列出 `active` Agent（Portal 卡片）。响应中剔除 `endpoint` 与 `auth` 密钥。 |
| GET | `/api/hub/agents/{id}` | 任意已登录用户 | 单个 Agent 公开元信息（仅 active）。 |
| POST | `/api/hub/agents/{id}/review` | 仅管理员 | 请求体 `{action: "approve"|"reject"|"deprecate", note?}`。转换状态。 |
| GET | `/api/hub/admin/agents` | 仅管理员 | 列出全部 Agent（含 pending/rejected）。 |
| ANY | `/api/hub/agents/{id}/chat` | 任意已登录用户 | **网关**：代理到该 Agent 的 AG-UI 端点。 |
| ANY | `/api/hub/agents/{id}/{path:path}` | 任意已登录用户 | **网关**：AG-UI 事件/资源子路径的通用代理。 |

网关行为（必须）：
- 按 `id` 解析 Agent；若不存在或不为 `active` → 404。
- 目标 URL = `{agent.endpoint}` + 剩余路径（对 `/chat` 使用 endpoint 根或 Agent 声明的 `/chat`）。
- 转发方法、请求头（剔除逐跳头：`host`、`content-length`、`connection`、`transfer-encoding`）、查询参数与请求体。
- 以 `StreamingResponse` 流式返回响应（含 SSE），保持 `content-type` 一致；保留 `text/event-stream`。
- 占位鉴权：若 `agent.auth.type == "apikey"`，仅从 Hub 存储的密钥映射中注入 `Authorization: Bearer <key>`（v1：环境变量 `HUB_AGENT_APIKEYS` 为 JSON `{id: key}`；**禁止**记录密钥到日志）。
- 不调度、不变更响应内容、不允许 Agent 发现其他 Agent。

### 4.3 安全约束（强制）

- 对非管理员调用的 `GET /api/hub/agents` **绝不**返回 `auth` 密钥或原始 `endpoint` 内部信息。
- 提交时校验 `endpoint` 的 URL scheme/host（拦截内网私有 IP 段 / localhost，除非开发标志放开 —— 复用现有 `validate_base_url_for_saved_config` 逻辑）。
- 在网关层按用户限流（v1 用内存或 SQLite 计数器；后续接 OTel/Langfuse）。
- 所有注册中心变更写入现有 `audit_events` 表，`target_type='hub_agent'`。

---

## 5. 前端规格（apps/hub/web）

与 course-agent 共用会话/登录（同一 cookie 域）、同一套 `styles.css` 设计变量（`--bg-*`、`--surface-*`、`--text-*`、`--muted-*`、`--accent`、`--accent-soft`、`--radius-*`、`--shadow-*`）。所有页面支持 dark / light 双主题（跟随系统 `prefers-color-scheme`，并允许手动 toggle 覆盖）。

前端为纯静态 SPA，放在 `apps/hub/web/` 下，由 FastAPI 以静态文件服务。禁止引入 React/Vue 等框架依赖；用原生 HTML + CSS + 少量 vanilla JS 实现。布局与视觉**强参考 Coze 技能商店页面**（见 §5.1），做到卡片网格 + 左侧导航 + 顶部 Tab 的三段式门户。

### 5.1 门户（Portal）— 路由 `/hub`（参考 Coze 技能商店）

**整体三段式布局（写死，不可改）**：
```
┌─────────────┬──────────────────────────────────────────────┐
│             │  顶部条：Logo + 搜索框（"搜索技能"） + 用户头像  │
│  左侧导航    ├──────────────────────────────────────────────┤
│  (固定 220px)│  标题区："技能商店" 大标题 + 副标题说明         │
│             ├──────────────────────────────────────────────┤
│ - 工作空间   │  Tab 栏：全部 | 行业技能包 | 效率工具 | 内容创作 │
│ - 技能商店★  │          | 数据分析 | 开发辅助                 │
│ - 我的技能   │  筛选项行（chips）：技能包 | 数据集 | 插件       │
│ - 我的智能体  │          | 工作流 | 图像 …（可横向滚动）        │
│ - 模板       ├──────────────────────────────────────────────┤
│             │  卡片网格（2 列 / 响应式 1~4 列）               │
│             │  ┌────────┐  ┌────────┐  ┌────────┐           │
│             │  │ 卡片    │  │ 卡片    │  │ 卡片    │           │
│             │  └────────┘  └────────┘  └────────┘           │
│             │  ┌────────┐  ┌────────┐  ┌────────┐           │
│             │  │ 卡片    │  │ 卡片    │  │ 卡片    │           │
│             │  └────────┘  └────────┘  └────────┘           │
└─────────────┴──────────────────────────────────────────────┘
```

**左侧导航（`.hub-sidebar`）**：
- 宽度固定 `220px`，背景 `--surface`，右侧 `1px` 分隔线 `--border`。
- 顶部放 Hub Logo（文字"AI for better Life" + 小图标）。
- 菜单项：工作空间、技能商店（当前激活态，`--accent` 文字 + 左侧 `3px` 高亮条 + 浅色背景）、我的技能、我的智能体、模板。
- 菜单项 hover：背景 `--surface-hover`，圆角 `--radius-md`，过渡 `120ms`。
- 底部放主题切换按钮（🌙/☀️）。

**顶部条（`.hub-topbar`）**：
- 高度 `56px`，左留白，右侧一个圆角搜索框（占位文字"搜索技能"，带 🔍 图标）+ 用户头像（圆形，`36px`，首字母占位）。
- 搜索框聚焦时边框变 `--accent`，阴影 `--shadow-sm`。

**标题区**：
- 左对齐大标题"技能商店"，字号 `28px`，字重 `700`，颜色 `--text`。
- 下方副标题（可选）："发现并添加由社区与官方维护的 AI Agent"，颜色 `--muted`。

**Tab 栏（`.hub-tabs`）**：
- 横向排列，每项是一个 `<button>`，当前项底部 `2px` `--accent` 下划线 + `--text` 字色，非当前项 `--muted`。
- Tab 点击只做前端筛选/分类切换（对应 manifest 的 `category`），默认"全部"。
- 切换时卡片网格做淡入动画（`opacity 0→1`，`160ms`）。

**筛选 chips 行（`.hub-filters`）**：
- 可横向滚动的标签胶囊（`pill`）：圆角全圆，背景 `--surface`，边框 `--border`，选中态背景 `--accent-soft`、文字 `--accent`。
- 对应 manifest 的 `tags` / `capabilities` 做二次过滤。

**卡片网格（`.hub-grid`）**：
- `display: grid`，`grid-template-columns: repeat(auto-fill, minmax(320px, 1fr))`，`gap: 16px`。
- 容器最大宽度 `1200px`，居中，左右 `padding: 24px`。
- 响应式断点：`≤640px` 单列，`≤1024px` 两列。

**数据来源**：`GET /api/hub/agents`（仅 active）→ 渲染卡片。加载中显示骨架屏，失败显示错误提示 + 重试按钮。空状态：插画 + "暂无已上线的 Agent，去提交一个？" + 跳转 `/hub/registry` 的按钮。

### 5.2 Agent 卡片组件（`.hub-card`）

**DOM 结构（强约定，AI 必须按此生成）**：
```html
<article class="hub-card" data-id="{agent.id}" tabindex="0">
  <div class="hub-card__head">
    <div class="hub-card__icon">              <!-- 圆形图标，48px -->
      <img src="{agent.icon}" alt="" onerror="this.replaceWith(emojiFallback())">
      <!-- 无 icon 时回退到 emoji（📘 等）或首字母方块 -->
    </div>
    <div class="hub-card__titleblock">
      <h3 class="hub-card__name">{agent.name}</h3>
      <span class="hub-card__category">{agent.category}</span>
    </div>
    <button class="hub-card__add" aria-label="添加">＋ 一键添加</button>
  </div>
  <p class="hub-card__desc">{agent.description}</p>   <!-- 截断 2 行，line-clamp:2 -->
  <div class="hub-card__subskills">                  <!-- 子技能 tag 行，Coze 风格 -->
    <span class="tag">简历解析</span>
    <span class="tag">面试题库</span>
    <span class="tag">+3</span>
  </div>
  <div class="hub-card__foot">
    <span class="hub-card__uses">🔥 1.2k 次使用</span>   <!-- 来自 manifest 扩展字段或占位 -->
    <span class="hub-card__tags">{agent.tags 前 2 个}</span>
  </div>
</article>
```

**样式要点**：
- 卡片：背景 `--surface`，圆角 `--radius-lg`，边框 `--border`，`padding: 16px`，阴影 `--shadow-sm`。
- hover：`transform: translateY(-2px)`，阴影 `--shadow-md`，边框 `--accent-soft`，过渡 `140ms`；图标轻微放大 `scale(1.04)`。
- focus-visible（键盘）：`outline: 2px solid var(--accent)`，`outline-offset: 2px`。
- "一键添加"按钮（`.hub-card__add`）：背景 `--accent`，文字白，圆角 `--radius-md`，`padding: 6px 12px`，字号 `13px`；hover 加深；点击后变为"✓ 已添加"禁用态（占位的本地状态，真实接入需调用 `/registry` 或收藏 API，v1 可仅本地标记）。
- 子技能 tag（`.tag`）：背景 `--surface-hover`，圆角全圆，`padding: 2px 8px`，字号 `12px`，颜色 `--muted`。
- 描述截断：`display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;`。

**交互**：
- 点击卡片任意处（除按钮）→ 跳转 `/hub/agent/{id}` 聊天页。
- "一键添加"按钮 `stopPropagation`，不触发跳转。

### 5.3 Agent 聊天 — 路由 `/hub/agent/{id}`

- 顶部返回栏：← 返回技能商店 + Agent 图标/名称 + "由 {owner} 提供"小字。
- 主体为**单一 AG-UI 客户端组件**（SSE），指向 `/api/hub/agents/{id}/chat`，处理 §3.2 事件（`RUN_STARTED`、`TEXT_MESSAGE_*`、`TOOL_CALL_*`、`RUN_FINISHED`、`RUN_ERROR`）。
- 消息气泡复用 course-agent 现有样式变量；用户消息右对齐（`--accent-soft` 背景），Agent 消息左对齐（`--surface`）。
- 流式文本：`TEXT_MESSAGE_CONTENT` 增量追加，打字机无额外动画（原生追加即可）。
- 工具调用：`TOOL_CALL_*` 渲染为可折叠的"🔧 调用了 xxx"卡片。
- 错误：`RUN_ERROR` 渲染为红色提示条，带重试按钮。
- 底部输入框：圆角 `--radius-lg`，支持 Enter 发送 / Shift+Enter 换行，右侧发送按钮（纸飞机图标）。
- 复用 `apps/course-agent/course_agent/web/styles.css` 的样式变量，保持视觉一致。
- **不为每个 Agent 写定制 UI**。

### 5.4 管理员审核 UI — 路由 `/hub/admin`

- 表格视图列出全部 Agent（调用 `GET /api/hub/admin/agents`），列：`ID` / `名称` / `分类` / `状态(badge)` / `提交时间` / `操作`。
- 状态 badge 颜色：pending=琥珀、active=绿、rejected=红、deprecated=灰。
- 操作按钮：批准（approve）、拒绝（reject，弹 note 输入）、废弃（deprecate）。调用 `POST /api/hub/agents/{id}/review`。
- 操作后原地刷新该行状态（乐观更新 + 失败回滚提示）。

### 5.5 启动动画（Splash Screen）— 必做

应用**首次加载 / 整页刷新**时，在 Portal（或任意 Hub 路由）之上叠加一个全屏启动动画，完成后淡出进入主界面。

**视觉与时序（Coze 风格 minimal 动效）**：
1. 全屏遮罩 `.splash`，`position: fixed; inset: 0; z-index: 9999;`，背景 `var(--bg)`，flex 居中。
2. 中心内容 `.splash__inner` 纵向排列：
   - **中国科大校徽**（`.splash__logo`）：使用校徽图片 `assets/ustc-emblem.jpg`（放在 `apps/hub/web/assets/`）。
   - 校徽尺寸 `120px`，初始 `opacity:0; transform: scale(0.6)`。
   - 校徽下方文字 **"AI for better Life"**（`.splash__title`）：字号 `22px`，字重 `600`，颜色 `var(--text)`，`letter-spacing: 1px`，初始 `opacity:0; transform: translateY(8px)`。
3. 动画序列（总时长约 `2600ms`）：
   - `0ms`：校徽 `scale(0.6)→1` 弹入（`cubic-bezier(.2,.8,.2,1)`，时长 `700ms`），同时 `opacity 0→1`。
   - `500ms`：标题 `translateY(8px)→0` + `opacity 0→1`（时长 `500ms`）。
   - `1600ms`：整体停留（可加校徽轻微 `pulse` 呼吸 `scale(1)→1.03→1`，时长 `600ms`）。
   - `2200ms`：`.splash` 整体 `opacity 1→0`（时长 `400ms`），`pointer-events:none`。
   - `2600ms`：从 DOM 移除（JS `setTimeout` 或 `animationend` 监听）。
4. `@keyframes` 约定（AI 必须实现）：
```css
@keyframes splashLogoIn {
  0%   { opacity: 0; transform: scale(0.6); }
  100% { opacity: 1; transform: scale(1); }
}
@keyframes splashTitleIn {
  0%   { opacity: 0; transform: translateY(8px); }
  100% { opacity: 1; transform: translateY(0); }
}
@keyframes splashPulse {
  0%,100% { transform: scale(1); }
  50%     { transform: scale(1.03); }
}
@keyframes splashFadeOut {
  0%   { opacity: 1; }
  100% { opacity: 0; }
}
```
5. **降级方案（必须）**：
   - 尊重 `prefers-reduced-motion: reduce`：跳过缩放/位移，仅做 `opacity` 淡入淡出（总时长压缩到 `800ms`）。
   - 若校徽图片加载失败：`onerror` 切换到 CSS 圆徽占位，不影响动画。
   - 动画期间禁止交互（`.splash` 覆盖全屏，`pointer-events` 在淡出前为 `auto` 以阻挡误触，淡出时改 `none`）。
6. **实现位置**：
   - 静态 HTML 片段直接写在 `index.html`（或各 Hub 页面 `<body>` 顶部），由一个共用的 `splash.js`（`apps/hub/web/splash.js`）控制时序与移除。
   - 该动画**只在整页加载时触发一次**（用 `sessionStorage` 标记 `hub_splash_shown`，SPA 内部路由切换不重复播放；刷新页面重新播放）。

---

## 6. 第一个 Agent：course-agent 的 AG-UI 适配层（参考接入范例）

这是验证契约的实例。步骤：

1. 在 `apps/course-agent/course_agent/main.py` 新增端点：
   `POST /api/ag-ui`（和/或 `/api/ag-ui/chat`），接收 AG-UI 的 `RunAgentInput`，返回
   AG-UI 事件的 SSE 流，内部包装现有的 `llm.py` 直接/检索生成逻辑。
2. 内部事件 → AG-UI 事件映射：
   - 流式 token → `TEXT_MESSAGE_CONTENT`
   - 检索引用 → `TOOL_CALL_*` 或内嵌引用文本
   - 最终 usage/引用 → `RUN_FINISHED`
   - 错误 → `RUN_ERROR`
3. 编写它的 manifest（示例）：
```json
{
  "id": "course-agent",
  "name": "课程资料学习助手",
  "description": "中科大课程资料整理、检索与复习助手（数学分析 B1 已冻结）。",
  "category": "学习助手",
  "tags": ["课程", "复习", "检索"],
  "endpoint": "http://127.0.0.1:8000/api/ag-ui",
  "protocol": "ag-ui",
  "auth": { "type": "none" },
  "capabilities": ["streaming", "retrieval", "file-upload"],
  "owner": "team@ai-for-better-life-in-ustc",
  "version": "0.8.0"
}
```
4. 注册它（管理员审批）→ 成为 Portal 中的第一张卡片。

这份 manifest 就是**第三方复制使用的模板**。

---

## 7. 实现顺序（建议给编程智能体）

1. `apps/hub/db.py` 建表 + `AgentManifest` pydantic 模型。
2. 注册中心端点（`POST /registry`、`GET /agents`、审核端点）。
3. 网关代理端点（支持 SSE 透传）。
4. 前端 Portal + AG-UI 聊天视图。
5. 管理员审核 UI。
6. course-agent 的 AG-UI 适配层 + 其 manifest 注册。
7. 冒烟测试：经 Hub 网关对 course-agent 端到端对话打通。

---

## 8. 范围外（v1）

- 统一 LLM 网关 / 集中管 Key 与计费（见 D6）。
- Agent 互调、编排、supervisor（明确禁止）。
- 服务发现 / 自动注册广播。
- 完整 OTel/Langfuse 可观测（仅预留钩子，非必需）。
- 沙箱隔离第三方 Agent 运行时（仅作治理说明；Hub 只转发，不执行他人代码）。

---

## 9. 术语表（消歧义）

- **Agent Hub** = 本框架。自身不是 Agent。
- **Agent 提供方** = 实现并注册某个 Agent 的第三方。
- **终端用户** = 在 Portal 中点选 Agent 的最终使用者。
- **网关（Gateway）** = apps/hub 内的反向代理；只转发。
- **注册中心（Registry）** = 存储 AgentManifest + 状态的 SQLite 表。
- **AG-UI** = Agent↔用户交互协议（基于 SSE 的事件流）。唯一必需的交互协议。
- **MCP** = 工具/数据层协议；可作为 `protocol` 枚举值，但 v1 非必需。

---

*本规格书是 Hub 的权威实现基线。偏离必须对照 §1 硬性决策并说明理由并重新确认。*
