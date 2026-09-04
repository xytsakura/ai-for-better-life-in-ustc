# Campus Agent Hub 更新日志

## 2026-09-04

### `fix(model-gateway)`：兼容 CC switch 根地址

- 模型 Profile 的 Base URL 可以填写 CC switch 使用的服务根地址，Hub 在最终请求时自动补 `/v1`。
- 已带 `/v1` 或其他自定义路径的地址保持原行为，不会重复添加路径；模型发现与实际生成请求使用同一规则。
- 新增网关端点拼接回归测试。

## 2026-09-01

- 修复合并后 Agent 广场在宽屏重新变成三列的视觉回归，恢复瀚海行位于左上角的两列布局；移动端继续使用单列，并补充布局契约测试。
- Windows 干净环境部署验证补齐课程 Agent 的 Python 模块路径，避免首次初始化时报找不到 `course_agent`。

### `fix(hub)`：补齐根入口缓存收口

- `/`、`/hub`、`/hub/...` 与顶层 JS/CSS 统一返回 `Cache-Control: no-store, no-cache, must-revalidate, max-age=0`，从比赛常用根入口进入时不再遗漏缓存保护。
- 回归测试同时确认带指纹的 `/assets` 和 Agent 图标继续保留原有缓存策略，不扩大禁缓存范围。

## 2026-08-31

### `fix(hub)`：修复配置页面被浏览器缓存导致卡住

- 模型配置页 `/hub/settings` 的 JS/CSS 资源由 FastAPI `FileResponse` 原样返回，只带 `Last-Modified`/`ETag`，未设置 `Cache-Control`。Safari 会基于 ETag 强缓存带查询参数的脚本，导致旧版 `app.js` 滞留，页面卡在骨架屏。
- 为 `/app.js`、`/hub-core.js`、`/styles.css` 等静态资源以及 `/hub` SPA 响应统一添加 `Cache-Control: no-store, no-cache, must-revalidate, max-age=0`。
- 在 `index.html` 中增加 `<meta http-equiv="Cache-Control">` 作为双重保险。
- 为 `paintModelSettings` 增加错误边界：若渲染阶段抛出异常，不再停留在骨架屏，而是显示带重试按钮的错误状态并输出 `console.error`。
- **修复恢复 `renderModelSettingsOverview` 时误删 `renderBindingText` 导致的运行时错误**：该函数在概览步骤和模型列表摘要中均被调用，缺失后页面直接报错「Can't find variable: renderBindingText」并进入错误状态；已重新声明并验证概览页加载。
- **移除首页模型状态条**：用户反馈首页不需要显示 DeepSeek API Key 掩码、全局默认、瀚海行模型绑定等配置摘要。已删除 `portal-model-status` 区块、`paintPortalModelStatus` 函数、首页对 `loadModelProfiles()` 的调用，并清理对应 CSS；首页初始化不再请求模型配置，渲染更快更干净。
- **左侧对话存档支持删除**：每条历史对话右侧显示一个 `×` 删除按钮，hover/focus 时出现；删除后从 `localStorage` 中移除并同步左侧列表。若删除的是当前活跃对话，则自动清空画布新建一个空对话。
- **修复编辑 Profile 时保存失败（HTTP 400 / 422）**：后端 PATCH 只要 body 里出现 `base_url` 就要求同时提供 `api_key`；前端原本每次编辑都会把 `base_url` 和 `default_model_id` 等字段全部发回去，导致只改名称也会触发该校验。现在 `saveModelProfile` 会对当前 Profile 做差分，只发送真正变化的字段；若用户确实改了 `base_url` 但未填 `api_key`，则在提交前用 toast 提示并聚焦输入框。
- **改进错误提示**：将 `api_key_required_for_base_url_change` 加入 `ERROR_MESSAGES`；`normalizeHttpError` 现在能正确解析 FastAPI 数组型验证错误（如 `api_key` 长度不足），并拼接字段名与消息。
- 前端 JavaScript 测试 39/39 通过。

### `fix(hub)`：本地启动脚本启用 Model Profile 与 provider 白名单

- `start-local.sh` 启用 `HUB_MODEL_PROFILES_ENABLED` 并自动生成 master key（`var/hub-secrets/model-profile-master.key`，权限 600，仅生成一次），`/api/model-profiles` 从 404 变为可用，配置页面不再显示「服务暂不可用」。
- 新增 `HUB_MODEL_PROVIDER_ORIGIN_ALLOWLIST`：部分网络（Clash/Surge fake-ip 模式）会把公网模型域名解析到 `198.18.0.0/15` 等保留段，被 SSRF 防护正确拦截。白名单仅放行指定的可信服务商来源，其余地址仍受防护。
- 修复脚本中一处会静默丢环境变量的问题：反斜杠续行的 env 前缀内不能插入注释行，否则续行会把注释吞掉并截断后续变量赋值。

### `test(hub)`：DeepSeek 配置端到端打通

- 新建 Profile → 测试连接（277ms）→ 发现 3 个模型（`deepseek-v4-flash` / `deepseek-v4-flash-vision-exp` / `deepseek-v4-pro`）→ 设置默认 → 绑定全局与 Agent。
- 首页助手两种模式均通：`instant` 流式返回正常；`route_stream` 对「期末复习」正确推荐瀚海行。
- Agent 委派链路全通：签发 workspace delegation → 换取 grant → Gateway 生成成功返回。
- 重启后 Profile、加密 API Key 与绑定关系完整保留（API Key 仅以掩码 `••••2403` 回显，不落明文）。
- 前端 JavaScript 测试 39/39 通过；后端 pytest 57 通过 2 失败，两处失败均为本机 fake-ip DNS 导致 `private_url_not_allowed`，与本次改动无关（未修改任何 Python 文件）。

### `refactor(hub)`：简化多模型配置中心界面

- 本地启动时启用 Model Profile 功能（`HUB_MODEL_PROFILES_ENABLED=true` + 32 字节 master key），`/api/model-profiles` 从 404 变为可用，配置页面不再显示「服务暂不可用」空状态。
- 精简页面文案：
  - 页面副标题由「集中维护多套模型配置，可设置全局默认或按 Agent 单独绑定」移除冗余，保留核心说明
  - 数据安全提醒从两行长文改为「请求会发送到你配置的 Base URL；请只添加你信任的服务商」
  - 进度概览标题由「MODEL ROUTING FLOW / 从配置到 Agent 调用的闭环」改为「PROGRESS / 配置到 Agent 调用」，删除解释段落和底部统计行
- 重构 Profile 编辑器表单：
  - 标签缩短：配置名称→名称，Provider→服务商，API 协议→协议，Base URL 保持，API Key（留空表示不替换）→API Key，启用状态→状态，默认模型保持
  - 字段分组为两列（服务商/协议、状态/默认模型）和通栏（名称、Base URL、API Key），减少垂直高度
  - 操作按钮缩短：创建 Profile→创建，保存修改→保存，测试连接→测试，发现模型保持；新增 `.action-row--compact` 缩小按钮尺寸
  - API Key placeholder 根据是否已保存动态显示，去掉头部冗余说明
  - 空模型列表文案从长句改为「未发现模型」
- 配置档案列表头移除「已保存 N 套/还没有服务端 Profile」的二级说明，空状态文案精简。
- 旧版本地配置迁移横幅文案精简：「发现旧版本地配置」→「旧配置待迁移」，按钮「暂不迁移/确认迁移」→「忽略/迁移」。
- 更新 T4 测试断言以匹配新的文案；前端测试 39/39 通过。

### `refactor(hub)`：移除 Agent 广场卡片的能力小标签

- 广场卡片不再渲染能力标签列表（`hub-card__subskills`），卡片结构简化为：图标 + 名称 + 等级徽章 / 描述 / 健康点 + 使用次数 / 操作按钮。相关 CSS 规则（`.hub-card__subskills`、`.tag--count`）同步删除，卡片更紧凑、底部操作行仍自动对齐。

### `refactor(hub)`：统一前端设计标尺

- `styles.css` 原先存在四层叠加样式，其中两层 teal 主题被后面的深色主题整体覆盖，导致生效的圆角只有 4/6/8/8px、`--shadow-sm` 为 `none`。已删除失效主题层，只保留一份 token 定义。
- 新增几何与排版标尺：圆角 6 级（6/8/12/16/20/999px）、阴影 4 级（深浅主题各一套）、间距 7 级（4px 基准）、字号 5 级，并全量替换 40 余处硬编码数值。
- 硬编码颜色（`#5269c8`、`#334b93`、`#2e3857`、`#0f766e`、`#8f5b16`、`#343945` 等）改为 `color-mix()` 派生的主题变量，深浅主题不再依赖逐条补丁覆盖。
- 修复失效选择器：`.agent-card` 实际类名应为 `.hub-card`，此前卡片 hover 上浮从未生效；侧边导航 `aria-current` 高亮被后续规则覆盖，已恢复并改用 accent 配色。
- 删除死代码：`.portal-search` 整套（79 行，HTML 中已无对应结构）、`.filter-panel`、`.hub-tabs`、`.tab`。

### `refactor(hub)`：首页视觉重构

- 首屏英文 kicker 由 36px 衬线大字降级为 12px 字距标签加两侧渐隐细线，色值改用主题 accent，消除与全站配色的冲突。
- 主标题由「中文衬线 + 英文无衬线」混排改为统一无衬线，字号改为 `clamp(30px, 4.4vw, 44px)`，中英文之间补充空格。
- 品牌区与对话区收窄到同一条 780px 列，消除原先三段各自居中导致的错位；移除固定高度与 `overflow: hidden`，对话态不再挤压消息区。
- 模型状态条由 5 列 grid 悬浮胶囊改为输入框下方的居中脚注，正文字号从 10px 提升到 11/12px，窄屏隐藏次要项。
- 输入框新增 `focus-within` 聚焦环；品牌区与对话区加入 320ms 入场动画，自动遵守 `prefers-reduced-motion`。

### `refactor(hub)`：Agent 卡片与筛选区重构

- 卡片信息由 6 层精简为 4 层：移除分类、维护者和版本号（详情页「接入信息」已完整展示这三项），能力标签由 4 个改为 3 个加 `+N` 计数。
- 健康状态由完整徽章改为色点加文字，与接入等级徽章在视觉上分离；卡名改无衬线并单行省略。
- 底栏改为 `margin-top: auto` 吸底，同一行卡片的操作按钮始终对齐，不再因描述长短而参差。
- 卡片图标由正圆形改为 12px 圆角方形，避免裁切 Agent logo；移除 `style="margin-top:14px"` 内联样式。
- 网格由固定两列改为 `repeat(auto-fill, minmax(300px, 1fr))`，宽屏自动增加列数。
- 修复首页「为你推荐」快捷卡片竖向堆叠的问题：`.hub-card--quick` 未声明 `flex-direction`，被后写的 `.hub-card` 覆盖为 `column`，现改回横向布局。
- 筛选区由默认折叠的 `<details>` 面板改为常驻三行结构（分类 / 接入 / 标签），标签改用 `flex-wrap` 换行而非横向滚动，超过 8 个折叠为 `+N`，且选中项始终可见。

### `fix(hub)`：恢复多模型配置进度概览

- 恢复误删的 `renderModelSettingsOverview`：该函数在未提交的视觉改动中被移除，但 14 条配套 CSS 规则全部保留，属于不完整删除，导致模型配置中心缺少配置进度引导。
- 恢复后同步将区块内残留的间距、圆角、字号硬编码纳入新标尺。

### `test(hub)`：模块缓存版本断言改为动态比对

- `t4-model-settings-contract.test.mjs` 原先硬编码 `20260822-8`，每次更新缓存版本号都会失败。现改为从 `index.html` 读取入口版本，再断言 `app.js`、`hub-core.js`、`styles.css` 三处一致，保留防缓存错位的守卫能力。
- 前端 JavaScript 测试 39 项全部通过。

## 2026-08-22

- `route_stream` 与兼容 `auto` 路由新增 active Registry 约束下的高置信匹配，保证期末复习、数学分析复习和签字盖章地点等核心演示需求稳定生成正确 Agent 卡片。
- 扩充瀚海行与校园公共服务 Agent 的公开检索关键词，并补充正例、反例及 inactive Agent 测试；签字盖章规则只接受明确地点、窗口、办理、流程、材料、手续或申请语义，避免图案设计与书写审美误匹配。
- 三组指定演示话术实机重复验证均为 10/10；Hub Python 59 项、前端 JavaScript 39 项通过，浏览器确认推荐卡片与两个 Agent 入口可用。

## 2026-08-21

- 首页新增普通对话与需求分析路由双模式，分别请求 `instant` 与 `route_stream`，不再依赖统一 `auto` 的关键词门控。
- 两个模式的对话、活动会话和左侧存档按身份独立保存；模式切换会中止旧流并恢复目标模式最近会话。
- `route_stream` 只接受功能表与 active Registry 交集中经校验的 `agent_id`，未命中或路由失败时自动回退普通流式回答。
- 恢复本地四 Agent Demo 的完整 bootstrap 与在线状态：Featured 瀚海行、校园助手、评课社区和校园公共服务均通过契约检查并进入 active Registry。
- Hub 首页 `auto` 模式新增本地前置判定：简单问候、通用知识问答和概念解释直接普通回答，只有明确的 Agent 需求才触发路由模型。


### 首页统一对话真实流式化

- `auto` 模式改为 `text/event-stream`：直接回答通过 `model.output_text.delta` 增量展示，Agent 推荐通过 `home.recommendation` 追加，`home.completed` 明确标记完整结束。
- 回答和路由并行执行；路由使用独立数据库连接并设置有限等待时间，失败或超时只取消推荐，不影响正常回答。
- 前端在错误、取消、身份切换或异常 EOF 时主动结束读取，防止残留请求继续写入当前会话。

## 2026-08-20

### 首页统一全屏对话

- 去掉首页即时对话 / 需求路由双标签和封闭式对话框，改为单一 `auto` 对话入口。
- 初始状态只展示品牌标识与扁平输入条；首条消息后进入全屏会话，模型可直接回答或展示已验收 Agent 的直达推荐。

### Agent 广场两列布局

- 桌面端 Agent 卡片调整为两列网格，每行两个，Featured 瀚海行固定优先展示。
- 首页“为你推荐”快捷卡片同步使用相同排序；移动端继续单列显示。

### 首页即时对话与需求路由

- 新增 `POST /api/home-assistant/chat`，支持 `instant` 真流式回答与 `route` 结构化推荐两种模式。
- 首页改为双模式会话区，具备按身份隔离的有界历史、加载、取消、错误和 Agent 推荐卡片。
- 路由规则维护在 `skills/agent-routing/SKILL.md`，已登记能力维护在 `skills/agent-routing/AGENT_CATALOG.md`。
- 路由结果必须通过功能表与运行时 active Registry 双重校验；前端只消费 `agent_id` 并复用既有激活流程，不信任模型生成的 URL。
- 新增缺少模型绑定、伪造或下线 Agent、敏感信息隔离、SSE 和响应式前端契约测试。

## Future Work Demo Agent

- Registry 与路由清单新增 `course-review-demo` 和 `campus-public-service-demo`，均需通过 active Registry 校验后才会被推荐。
- 两个 Agent 复用统一聊天容器，后端不信任模型生成的 URL 或 Agent ID。
