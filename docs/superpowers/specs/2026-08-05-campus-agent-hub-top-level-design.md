# Campus Agent Hub 顶层设计

> 文档状态：顶层设计审计通过，等待团队最终复核
>
> 首次创建：2026 年 8 月 5 日
>
> 当前阶段：完整顶层设计已通过三轮独立审计，尚未进入实现
>
> 参赛项目：AI for better life In ustc
>
> 设计权威性：本文档获团队最终确认后，取代 `HUB_FRAMEWORK_SPEC.md`、`README.md` 和 `项目产品文档.md` 中与本文冲突的 Hub 设计；在完成同步更新前不得启动实现

## 1. 文档目的

本文档定义本项目从已基本成型的“瀚海行”课程 Agent 演进为校园 Agent 集成平台的顶层方案，并作为后续实现、审计、演示和答辩的共同依据。

本文档在设计过程中持续更新。已经得到团队确认的决策标记为“已确认”；尚未讨论完成的内容标记为“待确认”，不得直接作为实现基线。

## 2. 核心目标

本项目参加中国科学技术大学“一〇七”杯算力与智能体开发大赛本科生组智能体赛道。参赛作品的核心不是单独完成一个专职 Agent，而是建设一个面向校园场景的 Agent 应用广场与统一接入平台。

平台允许不同技术栈、不同完成度的校园 Agent 先被发现，再通过标准协议逐步获得统一对话、网关治理和平台能力。

“瀚海行”是平台中的首个标杆 Agent，用于证明团队既能把一个真实校园 Agent 做深，也能把它的接入方式抽象成其他 Agent 可以复用的公共标准。

## 3. 已确认的产品边界

### 3.1 平台是什么

平台负责：

- 展示和发现校园 Agent；
- 接收 Agent 注册信息并执行审核、上架、下架和版本治理；
- 为标准接入 Agent 提供统一聊天入口；
- 按用户明确选择的 `agent_id` 转发请求；
- 提供身份传递、健康检查、超时、限流和审计等公共能力；
- 为接入方定义稳定、版本化的接入契约。

### 3.2 平台不是什么

比赛版本明确不做：

- Main Agent 或 Supervisor；
- 根据自然语言自动选择 Agent；
- Agent 之间互相调用、递归委派或协作编排；
- 在平台内运行未经审核的第三方代码；
- 生产级商业结算、计费和开放式托管。

用户在应用广场中主动选择 Agent。Hub 只执行确定性访问和治理，不理解课程、选课等具体业务。

### 3.3 Agent 的运行边界

- Hub、瀚海行和第三方 Agent 分别作为独立服务运行；
- Hub 只能通过公开 HTTP 契约调用 Agent，不得直接导入 Agent 的内部业务代码；
- 每个 Agent 独立维护自己的模型、提示词、知识库、工具和业务数据；
- 比赛演示中的“第三方 Agent”可以由本团队实现，但必须作为独立进程运行，并按与真实第三方相同的流程注册和接入。

## 4. 渐进式接入模式

平台同时支持快速外链接入和标准协议接入，不要求所有 Agent 在第一次注册时完成相同程度的改造。

### 4.1 Link App：基础接入

接入方提交 Manifest 和 `launch_url`。审核通过后，Agent 以卡片形式出现在应用广场，用户点击后进入它自己的完整页面。

该模式适合时间紧张、暂时无法适配统一协议的团队。平台必须将其明确标记为“外部应用”，不能把网址收录描述成完整协议接入。

### 4.2 Connected Agent：标准接入

Agent 在基础信息之外实现统一聊天协议和健康检查。用户可以留在 Hub 的统一聊天界面内与其交互，所有请求经过 Gateway。

### 4.3 Featured Agent：标杆接入

Agent 通过完整验收，同时提供统一对话能力和自己的完整工作台。用户既可以在 Hub 内直接对话，也可以进入完整应用使用复杂功能。

“瀚海行”的目标接入等级是 Featured Agent：

- Hub 内可以发起标准问答；
- 完整工作台继续提供知识库、资料选择、PDF 阅读器、模型配置、引用分支和虚拟形象等能力。

Manifest 中可由开发者声明的 `integration.mode` 只有 `link` 和 `connected`。`Featured` 是 Hub 在 Connected 与完整工作台验收通过后授予的治理标识，开发者不能自行声明。

## 5. 顶层分层

```text
第一层：Agent 应用广场
负责搜索、分类、卡片展示、Agent 详情和接入等级标识。

第二层：平台控制层
负责 Manifest、注册、审核、上下架、版本、健康状态和审计记录。

第三层：统一访问层
负责身份传递、Gateway 转发、SSE 流式响应、超时、限流和协议适配。

第四层：独立 Agent
├── 瀚海行：标准协议 + 完整工作台
├── 选课评价分析 Agent：第二示范位，暂不实现
└── 其他队伍 Agent：外链或标准协议接入
```

## 6. Campus Agent Hub Contract v1

### 6.1 定位

`Campus Agent Hub Contract v1` 是平台与 Agent 之间的接入标准，不是新的 Agent 开发框架。

Agent 内部可以使用 FastAPI、LangGraph、MCP、WeKnora 或其他技术。Hub 只依赖 Agent 对外暴露的 Manifest、交互端点和健康检查。

### 6.2 Manifest

每个 Agent 必须提交版本化 Manifest。示例：

```json
{
  "schema_version": "1.0",
  "id": "hanhai-course-agent",
  "name": "瀚海行",
  "description": "课程资料整理、知识库问答与期末复习助手",
  "version": "0.8.0",
  "owner": "AI for better life In ustc",
  "category": "学习助手",
  "tags": ["课程资料", "知识库", "期末复习"],
  "integration": {
    "mode": "connected",
    "launch_url": "https://hanhai.example.edu.cn/",
    "chat_endpoint": "https://hanhai.example.edu.cn/api/hub/chat",
    "health_endpoint": "https://hanhai.example.edu.cn/api/health"
  },
  "capabilities": [
    "streaming",
    "citations",
    "knowledge-base",
    "file-preview",
    "full-workspace"
  ]
}
```

Manifest 至少需要覆盖：

- 身份：`id`、名称、描述、版本和维护者；
- 展示：图标、分类和标签；
- 接入：接入等级、完整应用入口、聊天端点和健康检查端点；
- 能力：流式输出、引用、知识库、文件预览等可选能力；
- 治理：由 Hub 维护的审核状态、审核记录和上下架状态。

`endpoint`、认证信息和其他内部连接信息不得出现在普通用户可读取的公开响应中。

### 6.3 统一问答协议

Connected Agent 的协议边界使用固定版本的 AG-UI 请求和事件契约。

Hub 通过 HTTP `POST` 发送 `RunAgentInput`。需要保留的核心字段包括：

```text
threadId
runId
parentRunId（可选）
state
messages
tools
context
forwardedProps
```

短期签名身份通过受控请求头传递，不把认证令牌混入 `RunAgentInput` 的普通业务字段。

Agent 使用 SSE 返回 AG-UI `BaseEvent`。标准帧的最小线格式为：

```text
data: {JSON 序列化的 BaseEvent}

```

不把 SSE `event:` 或 `id:` 行设为兼容性必需条件。

比赛版本采用 AG-UI 的核心事件语义：

```text
RUN_STARTED
TEXT_MESSAGE_START
TEXT_MESSAGE_CONTENT
TEXT_MESSAGE_END
TOOL_CALL_START
TOOL_CALL_ARGS
TOOL_CALL_END
TOOL_CALL_RESULT
RUN_FINISHED
RUN_ERROR
```

协议关系：

- AG-UI：Hub 对 Connected Agent 暴露的标准流式交互边界；
- `simple-chat`：由 middleware adapter 翻译为 AG-UI 的低成本后端协议；
- MCP：Agent 内部连接工具和知识源时可以自行使用，Hub 不强制；
- A2A：Agent 间协作协议，比赛版本不实现。

只有同时满足以下条件时，平台或 Agent 才能标记为“AG-UI 兼容”：

- `POST` 接收符合固定版本的 `RunAgentInput`；
- SSE 输出符合该版本的 `BaseEvent`；
- 保留运行、消息和工具调用的关联字段；
- 不丢弃 `state`、`tools`、`context`、`forwardedProps` 等输入语义；
- 不把事件流降级为仅保留文本 token 的私有格式。

旧协议或普通 JSON 后端只能称为“通过 adapter 接入 AG-UI”，不能冒充原生 AG-UI Server。

本决策取代仓库中“自定义 `platform-chat-v1`”与“AG-UI 是唯一协议”并存的冲突口径。实现阶段必须固定 AG-UI 版本及其官方字段和事件 Schema。

### 6.4 身份和权限上下文

Hub 不把浏览器 Cookie、模型 API Key 或其他长期凭据直接交给第三方 Agent。

标准调用只携带短期、签名且权限受限的用户上下文，至少包含：

- 用户标识；
- 显示名称；
- 本次允许的权限范围；
- 请求编号；
- 签发时间和过期时间。

第三方 Agent 只能获得当前请求所必需的信息。瀚海行适配器负责把 Hub 用户映射到课程 Agent 自己的知识空间权限。

比赛版本的身份参数基线为：

- 使用成熟 JWT 库和非对称 Ed25519/EdDSA 签名；
- JWT Header 必须包含 `kid`；
- Hub 在 `/.well-known/jwks.json` 发布只读公钥集；
- 调用令牌有效期为 120 秒，允许最多 30 秒时钟偏差；
- 每个令牌必须绑定唯一 `aud=agent_id` 和 `jti`；
- 私钥只保存在 Hub 服务端 Secret，不进入仓库、数据库或日志；
- Agent 按 `kid` 缓存公钥，遇到未知 `kid` 时刷新 JWKS 后只重试一次；
- 密钥轮换期间同时发布当前和上一把公钥，超过最长令牌寿命后移除旧公钥。

若实现依赖不能安全支持 EdDSA，必须在阶段 0 通过 ADR 改为另一种成熟的非对称 JWT 算法并重新执行身份验收，不得静默降级为共享 HMAC 密钥。

### 6.5 健康检查

Connected Agent 必须提供健康检查，返回自身状态、版本、Contract 版本和实际能力。

```json
{
  "status": "ok",
  "version": "0.8.0",
  "contract_version": "1.0",
  "capabilities": ["streaming", "citations"]
}
```

Hub 根据健康状态展示“可用”“暂时离线”或“协议异常”，但不会因为一次瞬时失败直接删除 Agent。

## 7. 瀚海行接入原则

瀚海行不重写现有业务，只增加薄适配层：

```text
Hub 标准请求
→ 瀚海行 Hub 适配器
→ 现有 direct / retrieval / query stream
→ 将现有流式事件转换为 AG-UI 事件
→ Hub 统一聊天界面
```

继续保留在瀚海行内部的能力包括：

- 课程知识库和权限隔离；
- OCR、解析、分块和检索；
- 模型配置、提示词和回答组织；
- 资料勾选、引用和 PDF 预览；
- 知识库投稿、审核、订阅和版本治理；
- 完整工作台前端和虚拟形象。

Hub 不复制这些能力，也不直接读写瀚海行的数据库。

## 8. 第二示范应用预留

第二示范应用暂定为“选课评价分析 Agent”，本阶段只预留，不立即实现。

它用于展示 AI 对评课社区公开课程信息和公开评价的结构化分析，帮助学生更直观地了解课程和教师。后续实现必须遵守既有调研中的合规边界：

- 不共享或持久化任何成员的登录 Cookie、CSRF token 或其他会话凭据；
- 不把搜索 token 当作登录凭据；
- 不依赖非稳定的 `/course/<id>/reviews/` 路由作为正式 API；
- 不批量复制或公开再分发全量点评和附件；
- 报告保留来源、采集时间和可见范围；
- 比赛主流程优先使用少量、脱敏、预先缓存的公开评价快照，实时访问只作为可选增强。

## 9. Hub 内部架构与核心数据流

### 9.1 独立服务和部署边界

Hub 是独立于所有 Agent 的平台服务。比赛版本的逻辑拓扑为：

```text
浏览器
  │
  ▼
Hub Service
  ├── Portal：应用广场与统一聊天
  ├── Registry：Agent 注册信息
  ├── Review：审核、上下架
  ├── Gateway：请求转发
  ├── Identity：短期身份凭证
  ├── Health：健康检查
  └── Audit：操作和调用记录
  │
  ├── HTTP → 瀚海行
  ├── HTTP → 选课评价分析 Agent（后续）
  └── Redirect → Link App
```

Hub 使用自己的 `hub.sqlite3`，不复用瀚海行的 `course-agent.sqlite3`。两个服务分别管理自己的数据和生命周期。

该隔离保证：

- Hub 不依赖任何一个 Agent 的数据库结构；
- 瀚海行可以独立部署、升级或暂停，不影响平台运行；
- 第三方 Agent 无法接触瀚海行的知识库、用户数据和模型配置；
- 代码和部署结构可以直接证明“瀚海行只是平台插件”。

### 9.2 Hub 模块

| 模块 | 职责 | 不负责 |
|---|---|---|
| Portal | 应用发现、详情、统一聊天和接入入口 | Agent 业务逻辑 |
| Registry | Manifest、版本、接入等级和状态 | 执行 Agent 代码 |
| Review | 自动检查、人工审核、上下架 | 修改 Agent 输出 |
| Gateway | 鉴权、查表、协议选择、保真转发、超时和取消 | 语义路由和业务分支 |
| Identity | 签发短期、最小权限的调用上下文 | 共享浏览器 Cookie 或长期密钥 |
| Health | 检查端点可达性、协议和能力 | 因单次失败永久删除 Agent |
| Audit | 注册变更、审核操作和调用结果 | 保存聊天全文或明文凭据 |

### 9.3 注册和上架数据流

```text
提交 Manifest
→ Schema 校验
→ 状态 pending
→ 自动执行端点、协议和健康检查
→ 管理员复核能力与风险
→ 批准为 active
→ 自动出现在应用广场
```

Link App 不要求聊天端点，但必须验证 `launch_url` 的 URL 格式、可访问性和跳转安全。Connected Agent 还必须通过统一聊天协议和健康检查验收。

### 9.4 标准 Agent 调用数据流

```text
用户主动选择 Agent
→ Hub 查询 Registry
→ 确认 Agent 为 active 且符合调用条件
→ Identity 签发短期受限上下文
→ Gateway 按 Manifest 选择通用协议适配器
→ 调用 Agent Endpoint
→ Agent 返回流式事件
→ Gateway 转发到 Hub 统一聊天界面
→ Audit 记录不含正文的调用结果
```

健康检查用于状态展示和审核，不应在每一条用户消息前同步阻塞调用。Gateway 以 Registry 的当前状态和最近健康结果做准入；真实调用失败时返回统一错误。

### 9.5 Link App 打开数据流

```text
用户点击 Link App
→ Hub 确认 Agent 仍为 active
→ Audit 记录一次启动事件
→ 通过受控跳转打开 launch_url
```

Link App 默认不接收 Hub 用户身份、Cookie 或其他个人上下文。

### 9.6 协议适配分派

Gateway 只按 Manifest 声明的接入模式和协议选择通用适配器：

```text
protocol = ag-ui       → 保持完整请求、事件、顺序和扩展字段并流式转发
protocol = simple-chat → 将普通 JSON 响应转换为统一 SSE 事件
integration = link     → 执行受控跳转
```

AG-UI 透明代理必须按官方事件 Schema 保留该事件实际拥有的字段：

- 完整 `RunAgentInput` 语义；
- 所有事件的 `type`、时间戳和 `rawEvent`；
- 生命周期事件实际包含的 `threadId`、`runId`、`parentRunId`、`input` 和结果字段；
- 消息和工具事件实际包含的 `messageId`、`toolCallId`、`parentMessageId`、参数和结果字段；
- 事件顺序、未知事件、扩展字段和 `rawEvent`；
- 完整运行结果和错误字段。

除身份头注入、逐跳请求头清理和明确的安全限制外，Gateway 不重写原生 AG-UI 事件语义。

Gateway 中不得出现按具体 Agent 编写的业务分支，例如：

```text
if agent_id == "hanhai":
    执行课程逻辑
```

新增 Agent 时只能增加或更新 Manifest、Registry 记录和该 Agent 自己的实现，不修改 Gateway 业务路由。

### 9.7 Agent 与版本状态机

Agent 级状态和版本级状态分开维护。

Agent 级状态：

```text
pending   → active
pending   → rejected
active    → suspended
suspended → active
active    → deprecated
```

- `pending`：等待自动检查和管理员审核；
- `active`：普通用户可见且允许调用；
- `rejected`：审核未通过，保留原因和记录；
- `suspended`：临时停用，可重新审核恢复；
- `deprecated`：整个 Agent 已废弃，只保留审计记录。

版本同时维护审核状态和部署状态：

```text
review_status: pending → approved | rejected

deployment_status:
staged → active → superseded → deprecated
             ▲         │
             └─────────┘  允许回滚已批准且未废弃的版本
```

- `hub_agents.active_version_id` 指向当前接受新调用的版本；
- `previous_active_version_id` 记录最近一次切换前的版本，便于快速回滚；
- 批准新版本时，在一个数据库事务中更新两个版本状态和活动版本指针；
- 每次调用开始时固定 `agent_version_id`，版本切换不改变已经进行中的调用；
- 回滚只能指向 `review_status=approved` 且未 `deprecated` 的版本；
- 版本批准、切换和回滚分别写入审计事件。

只有 Agent 级状态为 `active` 且存在有效 `active_version_id` 时，Agent 才出现在普通用户的应用广场中。状态变更必须记录操作者、时间和原因。

## 10. 前端信息架构

Hub 前端围绕“应用发现、使用、接入、治理”四类任务设计，不以单一聊天页面代替平台。

### 10.1 应用广场 `/hub`

应用广场是产品第一屏，也是比赛视频的起点。

左侧导航包含：

- Agent 广场；
- 我的最近使用；
- 开发者接入；
- 管理审核，仅管理员可见。

主区域提供搜索、分类和接入等级筛选。Agent 卡片显示图标、名称、简介、维护者、分类、标签、版本、接入等级和最近健康状态。

不同接入等级使用不同的真实操作：

| 接入等级 | 主要操作 | 次要操作 |
|---|---|---|
| Link App | 打开应用 | 查看详情 |
| Connected Agent | 立即对话 | 查看详情 |
| Featured Agent | 立即对话 | 进入完整工作台 |

不提供没有真实后端语义的“一键添加”按钮。

### 10.2 Agent 详情 `/hub/agents/{id}`

详情页同时承担产品介绍和使用前风险告知：

- Agent 的主要能力和适用场景；
- 维护者、版本和更新时间；
- 接入等级与协议版本；
- 支持的能力；
- 是否会接收用户身份、文件或其他数据；
- 最近健康状态；
- 与接入等级对应的“立即对话”“打开应用”或“进入完整工作台”。

### 10.3 统一聊天 `/hub/agents/{id}/chat`

Connected Agent 和 Featured Agent 共用统一聊天容器，至少支持：

- SSE 流式输出；
- 安全 Markdown 和 KaTeX 数学公式；
- 引用和来源展示；
- 工具调用的折叠状态；
- 工具调用参数和结果的安全展示；
- 取消、重试和统一错误提示；
- 按 Agent capability 条件展示的附件入口。

瀚海行的统一聊天页额外提供“进入完整工作台”，但统一容器不复制资料勾选、知识库管理和 PDF 阅读器等 Agent 私有能力。

对话正文原则上由 Agent 自己负责。Hub 只持久化线程标识、Agent、用户、时间、状态和用量等最小元数据，不默认把第三方聊天全文复制到平台数据库。

### 10.4 开发者接入 `/hub/submit`

接入流程首先选择：

```text
快速入驻：提供应用网址
标准接入：提供聊天端点和健康检查
```

页面根据接入方式显示对应字段，并支持：

- 预览应用卡片；
- 校验 Manifest；
- 测试网址或 Endpoint；
- 展示协议验收项目；
- 提交后查看 `pending` 状态和审核意见。

### 10.5 管理审核 `/hub/admin`

管理员页面采用紧凑列表和详情面板，不建设与比赛 MVP 无关的数据大屏。

审核流程为：

```text
选择待审核 Agent
→ 查看 Manifest
→ 查看 URL 安全检查
→ 查看健康检查
→ 查看协议测试结果
→ 批准或拒绝
```

已上线 Agent 可以暂停、恢复或标记废弃。所有操作要求填写原因并写入审计记录。

### 10.6 启动、视觉和响应式原则

- 保留已经加入仓库的中国科大校徽启动动画；
- 每次页面会话只在首次打开时播放，SPA 内部切换不重复播放；
- 尊重 `prefers-reduced-motion`，减少动态效果时只做短暂淡入；
- 沿用瀚海行的基础设计变量，保持品牌一致；
- Hub 采用安静、清晰、适合扫描的应用目录布局；
- 卡片只用于 Agent 列表等重复对象，不嵌套装饰性卡片；
- 桌面端、平板和手机端均需保证搜索、卡片操作、聊天输入和审核操作不重叠。

## 11. Registry、自动验收与 Gateway 治理

### 11.1 Agent 身份与版本分离

Registry 不使用“一个 Agent 一行、更新时直接覆盖”的简单模型。Agent 的稳定身份与每次提交的不可变版本分离：

```text
hub_agents
保存 Agent 的稳定身份、所有者、当前状态和当前生效版本。

hub_agent_versions
保存每一次提交的不可变 Manifest、版本号和 Endpoint 配置。

hub_reviews
保存自动检查结果、管理员决定、意见和时间。

hub_health_checks
保存最近健康状态、延迟、协议版本和错误原因。

hub_invocations
保存请求编号、Agent、版本、用户、耗时、用量和结果码，不保存聊天正文。

hub_audit_events
保存注册、审核、上架、暂停、恢复和版本切换记录。
```

Agent 更新采用先审核、后原子切换的方式：

```text
当前 v1.0 继续在线
→ 开发者提交 v1.1
→ v1.1 单独进入 pending
→ 自动检查和人工审核
→ 审核通过后原子切换 active_version
→ v1.0 保留用于审计和回滚
```

未通过审核的新版本不影响当前生效版本。

### 11.2 凭据边界

- API Key、OAuth Secret 和其他凭据不进入公开 Manifest；
- Registry 只保存 `secret_ref` 等间接引用；
- 比赛 MVP 的真实凭据由服务端环境变量管理；
- 普通 Agent 列表、详情 API、日志和错误响应不得返回 Endpoint 内部信息或凭据；
- 后续如引入 Secret Store，保持 `secret_ref` 契约不变。

### 11.3 自动验收

提交 Manifest 后，平台根据接入等级执行自动检查：

1. 字段、Schema 版本、Agent ID 和语义化版本是否合法；
2. `launch_url`、聊天端点和健康端点是否为允许的 URL；
3. DNS 解析、私有地址、localhost 和重定向链是否存在 SSRF 风险；
4. 健康检查是否返回声明的 Contract 版本和能力；
5. Connected Agent 是否返回正确的 SSE Content-Type；
6. 流式事件是否具有合法的开始、正文和终止顺序；
7. 超时、断连、错误和取消能否正常处理；
8. 请求、响应、附件和图标是否符合大小与类型限制。

外部图标不由用户浏览器直接加载。Hub 在审核过程中下载、校验并保存安全副本，再使用平台自己的静态资源地址。

自动检查只产生证据，不直接决定上架。比赛版本仍由管理员最终批准。

### 11.4 Endpoint 信任等级

Endpoint 按来源使用两类服务端信任等级。该等级由 Hub 管理员或仓库 seed 决定，接入方不能在 Manifest 中自行声明：

```text
first_party_internal
third_party_external
```

- `first_party_internal`：仅用于本仓库部署的瀚海行等第一方服务；允许命中管理员配置的精确 Docker 服务名或内网地址 allowlist；
- `third_party_external`：开发者提交的默认等级；要求公网 HTTPS，禁止 localhost、私网、链路本地地址和危险重定向；
- 本地开发模式可以为明确的 loopback 端口启用单独 allowlist，但该开关默认关闭，不能在比赛部署或生产配置中继承；
- `launch_url` 必须是浏览器可访问地址；Gateway 内部连接地址可以通过服务端 connection override 替换，不能把 Docker 服务名暴露给浏览器；
- Conformance Runner 和真实 Gateway 使用相同的 URL 解析、DNS 和重定向安全函数，避免审核通过后调用规则不一致。

### 11.5 Gateway 请求安全

- 目标 URL 只能由服务端根据 Registry 解析，浏览器不能传入任意目标；
- DNS 解析和每一次重定向都要重新执行 SSRF 检查；
- 不向 Agent 转发浏览器 Cookie、原始 `Authorization` 或逐跳请求头；
- 只注入平台签发的短期身份和该 Agent 自己的服务凭据；
- 限制请求体、响应头和非流式响应体大小；
- 第三方 Markdown、HTML、链接、工具调用、引用和附件全部按不可信内容处理；
- 用户断开或主动取消后，Gateway 必须取消上游请求；
- POST 调用失败后不自动重试，避免重复执行有副作用的操作。

### 11.6 超时和流式错误

Gateway 区分：

- 连接超时：无法建立到 Agent 的连接；
- 首事件超时：连接成功但 Agent 长时间没有开始响应；
- 流式空闲超时：已开始响应但长时间没有新事件；
- 总运行上限：防止无界任务永久占用连接。

具体秒数由实现阶段根据演示任务确定并集中配置，不散落在路由代码中。

统一错误码至少包括：

```text
agent_not_found
agent_not_active
agent_unavailable
agent_timeout
protocol_error
rate_limited
upstream_error
```

流开始前失败时返回结构化 HTTP 错误；流开始后失败时发送统一 `RUN_ERROR` 终止事件。任何响应都不得暴露堆栈、内部 Endpoint 或凭据。

### 11.7 健康状态策略

健康检查异步执行并缓存结果，不在每条用户消息前同步阻塞调用。

- 单次失败：显示“暂时异常”，不改变 Agent 的审核状态；
- 连续失败：标记为“离线”，阻止新的标准调用；
- 恢复成功：更新为“可用”；
- 是否将 Agent 从 `active` 转为 `suspended` 由管理员决定并记录原因。

健康检查、真实调用状态和 Registry 审核状态是三个不同维度，不得混为一个字段。

## 12. 身份衔接与完整工作台启动

### 12.1 身份所有权

Hub 维护平台登录会话；每个 Agent 维护自己的本地用户、业务权限和会话。两者通过稳定的外部身份映射关联，不共享数据库或浏览器 Cookie。

```text
Hub User ID
→ Agent 内的 external_identity(provider="campus-hub", subject=Hub User ID)
→ Agent Local User ID
```

显示名称、邮箱等可变化字段不得作为账号映射主键。

### 12.2 统一聊天的短期身份

用户在 Hub 统一聊天中发送消息时：

1. 浏览器只把 Hub 的 HttpOnly 会话 Cookie 发送给 Hub；
2. Hub 确认用户和目标 Agent 权限；
3. Identity 模块签发面向该 Agent 的短期身份令牌；
4. Gateway 将令牌发送给 Agent；
5. Agent 验证签名、签发方、目标、有效期和权限范围后处理请求。

令牌采用成熟库实现的非对称签名 JWT，并通过 `kid` 与 JWKS 支持密钥轮换。最小 claims 为：

```text
iss   平台签发方
aud   唯一目标 agent_id
sub   稳定 Hub User ID
scope 本次允许的权限
iat   签发时间
exp   短有效期
jti   唯一请求或令牌编号
```

令牌不携带模型密钥、浏览器 Cookie、私人文件正文或无关个人档案。第三方 Agent 只能获得其 Manifest 和审核策略允许的 scope。

### 12.3 Featured Agent 完整工作台启动

完整工作台不使用 iframe，不把 Hub Cookie 共享给 Agent，也不在 URL 中携带长期身份令牌。采用类似 OAuth 授权码的单次启动流程：

```text
用户点击“进入完整工作台”
→ Hub 创建绑定用户、Agent 和回跳地址的一次性短期 code
→ 浏览器跳转到 Agent 的 Hub callback
→ Agent 后端通过服务器到服务器请求兑换 code
→ Hub 返回面向该 Agent 的签名身份
→ Agent 映射或创建本地用户并设置自己的 HttpOnly 会话
→ Agent 跳转到完整工作台
```

授权码必须单次使用、短期有效，并绑定目标 Agent 和允许的回跳地址。兑换端点需要验证 Agent 身份，防止其他服务冒领。

比赛版本参数基线为：

- 授权码有效期 60 秒；
- 服务端仅保存授权码哈希，成功兑换后在同一事务中标记已使用；
- 回调地址必须与 Agent 已审核版本中的精确 allowlist 匹配，不允许通配域名；
- 启动请求携带并校验随机 `state`，防止登录 CSRF；
- 兑换端点使用标准 `client_secret_basic` 验证 Agent 服务身份，`client_id=agent_id`；
- Hub 为需要完整工作台的 Agent 生成至少 256 bit 随机 client secret，只在创建或轮换时展示一次；
- Hub 只保存 client secret 的 Argon2id 哈希和凭据状态，Agent 将原始 secret 保存在自己的服务端环境变量或 Secret Store；
- 凭据记录绑定 `agent_id`，授权码同时绑定发起时的 `agent_version_id`；兑换时两者必须与当前请求一致；
- secret 支持轮换和撤销，旧 secret 只在明确的短轮换窗口内有效；
- 授权码仅凭自身内容不能兑换，缺少、错误或属于其他 Agent 的服务凭据一律失败；
- 除本机 loopback 开发模式外，Hub、回调和兑换端点都必须使用 HTTPS；
- 授权码和 `state` 不写入访问日志正文，回调完成后立即从浏览器地址栏移除。

瀚海行工作台提供明确的“返回 Agent 广场”入口，保证用户能顺畅往返。Featured Agent 的完整工作台在当前窗口打开；普通 Link App 使用带外部链接标记的新窗口打开，并设置 `noopener`，且默认不传递 Hub 身份。

### 12.4 瀚海行用户映射

瀚海行增加 Hub 外部身份映射层，但继续由自己的权限系统决定用户能访问哪些个人、共享和订阅知识空间。

比赛演示阶段可以把固定 Hub 演示身份映射到现有 `demo-a`、`demo-b` 和 `demo-c`，但映射必须保存在服务端配置或数据表中，不能由浏览器传入任意本地用户 ID。

### 12.5 会话安全

- Hub 与 Agent 的会话 Cookie 均为 HttpOnly、SameSite，并在 HTTPS 部署时启用 Secure；
- 登录、授权码兑换和状态变更接口执行 CSRF 防护；
- 授权码、JWT 和服务凭据不得进入 URL 日志、前端存储或审计正文；
- Agent 只能信任已配置的 Hub 签发方和公钥；
- 身份验证失败不得降级为匿名高权限用户；
- Link App 默认完全独立登录，除非未来通过单独审核的标准身份协议升级。

## 13. Contract 一致性测试与接入验收

### 13.1 可执行契约

Contract 不能只停留在说明文档。仓库需要保存并版本化：

- Manifest JSON Schema；
- Connected Agent 请求 Schema；
- 固定官方版本的 `RunAgentInput`、`BaseEvent`、事件和扩展字段约束；
- `simple-chat` 请求与响应 Schema；
- 健康检查 Schema；
- 统一错误码；
- 合法和非法样例；
- Contract 版本兼容规则。

对 AG-UI 的支持必须基于固定版本的官方字段和事件 Schema，并优先使用官方 SDK 编解码测试。只有通过相应一致性测试的 Agent 才能标记为“AG-UI 兼容”；不能仅因事件名称相似而使用该标识。

### 13.2 Conformance Runner

平台提供可以独立运行的 Contract 测试工具。接入方输入 Manifest 后，工具至少验证：

```text
Manifest Schema
URL 和重定向安全
健康检查
身份令牌拒绝与接受规则
授权码兑换的 client_secret_basic、Agent/版本绑定和重放规则
聊天请求格式
SSE Content-Type 与帧解析
RunAgentInput 字段保真
事件顺序与终止状态
未知事件、扩展字段和关联 ID 保真
取消、超时和错误
大小限制
危险响应内容
```

测试结果使用机器可读 JSON 和适合管理员查看的摘要表示，并随具体 Agent 版本进入审核记录。

仓库中的最小执行入口固定为：

```powershell
.\contracts\campus-agent-hub\v1\conformance\run.ps1 `
  -Manifest .\contracts\campus-agent-hub\v1\examples\connected-agent.json `
  -BaseUrl http://127.0.0.1:8101 `
  -Output .\var\conformance-result.json
```

底层测试同时可以通过以下命令在 CI 和本地运行：

```powershell
python -m pytest contracts/campus-agent-hub/v1/conformance/tests
```

结果 JSON 至少包含：

```text
contract_version
manifest_hash
agent_id
agent_version
started_at
completed_at
checks[]: name, status, duration_ms, error_code, safe_detail
overall_status: passed | failed
```

`safe_detail` 不得包含凭据、内部响应正文或未脱敏 Endpoint。

### 13.3 关键验收矩阵

| 场景 | 预期结果 |
|---|---|
| `pending` Agent 查询或调用 | 普通用户不可见、不可调用 |
| `suspended` Agent 调用 | 返回 `agent_not_active` |
| Link App 缺少聊天端点 | 可以按 Link App 审核，不伪装为 Connected |
| Connected Agent 非 SSE 响应 | 协议检查失败 |
| 流缺少终止事件 | 返回或记录 `protocol_error` |
| 用户取消 | Hub 和 Agent 上游均停止处理 |
| 修改请求中的目标 URL | Hub 忽略并使用 Registry 目标 |
| `third_party_external` 使用私网、localhost、链路本地地址或危险重定向 | 注册或调用被拒绝 |
| `first_party_internal` 命中精确内网 allowlist | 允许；非 allowlist 内网目标仍被拒绝 |
| 令牌 `aud` 不匹配 | Agent 拒绝调用 |
| 一次性启动 code 重放 | 第二次兑换失败 |
| 缺少、错误或属于其他 Agent 的服务凭据兑换 code | 兑换失败且不消耗合法 code |
| code 绑定的 `agent_id/agent_version_id` 与兑换方不匹配 | 兑换失败 |
| 第三方输出含脚本或危险链接 | 前端安全渲染，不执行脚本 |
| 新 Agent 注册 | Gateway 业务代码修改为 0 行 |

### 13.4 测试夹具与第二 Agent 的关系

Contract 测试可以先使用极简协议夹具，不等于现在就实现第二个完整产品。极简夹具只用于自动化验证成功、错误、超时、断流和恶意响应。

“选课评价分析 Agent”后续作为真实第二示范应用接入，负责产品演示；协议夹具负责稳定回归测试，二者不混为一个模块。

## 14. 比赛演示闭环

### 14.1 演示要证明的命题

比赛视频和答辩必须证明：

1. 产品入口是 Agent 应用广场，而不是单独的瀚海行；
2. 瀚海行是通过标准 Contract 接入的 Featured Agent；
3. Hub 内统一聊天可以真实调用瀚海行并流式返回；
4. 瀚海行完整工作台保留知识库、RAG、OCR、引用和资料阅读能力；
5. 一个独立 Agent 可以先以 Link App 快速入驻，再升级为 Connected Agent；
6. 上架、升级、暂停和健康状态都由 Registry 与审核流程治理；
7. 新 Agent 接入时 Gateway 不增加任何 Agent 专用业务分支。

### 14.2 推荐视频脚本

```text
1. 打开 Hub，播放一次科大校徽启动动画，进入 Agent 广场。
2. 展示瀚海行 Featured Agent 卡片、能力、版本和健康状态。
3. 在 Hub 统一聊天中询问一个课程问题，展示真实 SSE、公式和引用。
4. 点击“进入完整工作台”，无重复登录进入瀚海行。
5. 展示资料勾选、RAG、OCR 后引用、PDF 阅读器和虚拟形象。
6. 回到 Hub 开发者接入页，提交一个独立极简演示 Agent 的 Link App 版本。
7. 管理员查看自动检查并批准，应用卡片自动出现。
8. 提交该极简 Agent 的 Connected 新版本，协议测试通过后批准切换。
9. 卡片从“外部应用”升级为“标准接入”，在 Hub 内完成一次独立 Agent 问答。
10. 暂停该 Agent，展示它立即不可调用；瀚海行和 Hub 仍正常运行。
```

选课评价分析 Agent 不属于本脚本的完成前置条件。它在实现完成后可以替换极简演示 Agent，使比赛故事更贴近校园选课场景，但替换与否不改变平台验收标准。

### 14.3 可量化验收指标

| 维度 | 比赛版目标 |
|---|---:|
| Manifest、健康和交互 Contract 固定测试 | 100% 通过 |
| `pending/suspended/rejected` Agent 对普通用户曝光 | 0 次 |
| 新 Agent 接入所需 Gateway 业务代码修改 | 0 行 |
| 跨 Agent 身份令牌误用成功 | 0 次 |
| 一次性启动 code 重放成功 | 0 次 |
| 密钥、内部 Endpoint、Cookie 和堆栈泄漏 | 0 次 |
| 流式事件顺序和终止状态保真 | 100% |
| 用户取消后上游继续生成 | 0 次 |
| 固定演示脚本连续成功 | 10 次中至少 9 次 |

模型回答质量和瀚海行 RAG 指标继续由瀚海行自己的评测负责，不混入 Hub Contract 指标。

### 14.4 演示降级

- 某 Agent 离线：卡片显示离线，其他 Agent 和平台继续可用；
- Hub 统一聊天调用失败：展示统一错误和重试，不暴露内部信息；
- 模型服务不可用：瀚海行使用其已有降级路径，Hub 不伪造回答；
- 评课社区不可访问：第二示范 Agent 使用带来源和时间的脱敏公开快照；
- 健康检查服务瞬时失败：使用最近状态并明确标记检查时间；
- 启动动画或图标失败：直接进入广场并使用平台托管占位图，不阻断主流程。

## 15. 分阶段实施路线

### 阶段 0：冻结 Contract 和仓库边界

- 完成本设计文档审计；
- 固定 AG-UI 官方 SDK/包版本，并写入依赖锁文件；
- 固定 Manifest、`RunAgentInput`、事件、健康和错误 Schema；
- 固定 JSON 字段命名策略，原生 AG-UI 代理不得在 camelCase 与 snake_case 之间擅自转换；
- 固定 JWT 算法、`kid`、JWKS 地址、令牌有效期、授权码参数、`client_secret_basic` 和回调 allowlist；
- 固定本地演示 URL、内部服务 allowlist、公开 URL 和 HTTPS 规则；
- 将本文档标记为权威基线，并同步修改 README、现有 Hub 规格和产品文档中的协议、数据库和身份口径；
- 确认 Hub、瀚海行和示范 Agent 的独立进程边界。

阶段 0 是硬门槛。上述项目未形成可执行 Schema、配置和锁定依赖前，不得启动阶段 1 或阶段 2。

验收：文档不存在 `platform-chat-v1` 与 AG-UI、共享 course-agent 数据库与独立 Hub 数据库等互相冲突的当前方案。

### 阶段 1：Hub 控制面最小闭环

- 建立独立 Hub FastAPI 应用和 `hub.sqlite3`；
- 实现版本化 Registry、注册、审核和状态机；
- 实现应用广场、详情、开发者接入和管理员审核；
- 先以 Link App 方式临时注册瀚海行，卡片不得提前标记为 Featured。

验收：Manifest 提交、审核、出现、暂停和恢复端到端可用。

### 阶段 2：Gateway 与瀚海行标准接入

- 实现 AG-UI 代理和 `simple-chat` 适配器；
- 实现短期身份、JWKS 和审计元数据；
- 给瀚海行增加薄适配端点；
- 实现统一聊天；
- 实现一次性授权码进入完整工作台。

验收：用户从 Hub 对话并进入瀚海行完整工作台，原有瀚海行功能不回归；通过 Connected、完整工作台启动和治理验收后，瀚海行才升级为 Featured。

### 阶段 3：第三方接入证明

- 完成 Conformance Runner 和协议夹具；
- 接入一个独立 Link App；
- 将其通过新版本升级为 Connected Agent；
- 如时间允许，实现选课评价分析 Agent 的脱敏公开数据 Demo。

验收：独立极简 Agent 可以从 Link App 升级为 Connected，新增 Agent 时 Gateway 业务代码修改为 0 行。选课评价分析 Agent 仍是可选产品增强，不作为本阶段硬依赖。

### 阶段 4：安全、可靠性和比赛材料

- 完成 SSRF、身份、权限、流式异常、取消和危险输出测试；
- 固化健康检查、审计和失败降级；
- 完成 Docker Compose 或等价的一键演示部署；
- 连续执行演示脚本；
- 完成答辩文档、架构说明和演示视频。

验收：固定演示脚本 10 次中至少 9 次成功，关键安全指标为 0 次失败。

## 16. 建议仓库结构和责任边界

```text
apps/
├── hub/
│   ├── hub/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── db.py
│   │   ├── registry.py
│   │   ├── review.py
│   │   ├── gateway.py
│   │   ├── identity.py
│   │   ├── health.py
│   │   ├── audit.py
│   │   └── protocols/
│   ├── web/
│   └── tests/
├── course-agent/
│   └── 现有瀚海行 + Hub 适配层
└── course-review-agent/
    └── 后续选课评价分析 Agent

contracts/campus-agent-hub/v1/
├── manifest.schema.json
├── health.schema.json
├── simple-chat.schema.json
├── examples/
└── conformance/

deploy/
└── demo compose 与环境变量模板
```

实现责任按边界划分，而不是多人同时修改同一模块：

- 平台后端负责人：Contract、Registry、Gateway、Identity 和治理测试；
- Agent 负责人：瀚海行适配层、原功能回归和后续选课评价 Agent；
- 前端与演示负责人：Portal、统一聊天、审核交互、响应式验收和比赛材料；
- 队长：冻结跨模块契约、协调版本、组织端到端验收和答辩叙事。

## 17. 当前设计结论

本项目的最终产品是 Campus Agent Hub，而不是瀚海行本身。瀚海行承担“第一个真实、复杂、可用的标杆 Agent”角色；选课评价分析 Agent 承担后续第二示范应用角色；其他队伍的 Agent 可以从 Link App 开始，在不改变自身完整产品的前提下逐步升级为 Connected Agent。

平台的核心竞争力由四件事共同构成：

1. 渐进式接入，降低其他团队进入广场的门槛；
2. 版本化统一 Contract，使标准接入可执行、可测试；
3. Registry、Gateway、Identity 和 Review 形成真实治理闭环；
4. 瀚海行证明平台能够承载复杂校园 Agent，而不是只有空框架。

## 18. 待审计事项

1. 阶段 0 选择并锁定的 AG-UI 官方版本和 SDK 组合；
2. 本地演示与最终部署下的实际公开 URL、回调地址和证书；
3. 选课评价分析 Agent 的数据授权、快照范围和演示内容；
4. 比赛最终提交格式和部署环境限制。

### 18.1 与旧 Hub 规格的取代关系

本文档获最终确认后，明确取代 `HUB_FRAMEWORK_SPEC.md` 中以下旧决策：

| 旧规格 | 新基线 |
|---|---|
| Hub 复用 course-agent 登录和会话基础设施 | Hub 独立登录，通过短期身份和一次性授权码衔接 Agent |
| Hub 表扩展在 `course-agent.sqlite3` | Hub 使用独立 `hub.sqlite3` |
| 所有接入围绕单一聊天入口 | 支持 Link App、Connected Agent 和 Featured Agent 渐进式接入 |
| 单表覆盖 Agent 当前记录 | Agent 身份与不可变版本分离，活动版本指针原子切换 |

保留不变的旧决策包括：Hub 不是多 Agent 编排器、用户主动选择 Agent、Registry 需要管理员审核、Gateway 不做业务调度、AG-UI 是 Connected Agent 标准协议。

AG-UI 规范核对使用的官方来源：

- [AG-UI Introduction](https://docs.ag-ui.com/introduction)
- [AG-UI Events](https://docs.ag-ui.com/concepts/events)
- [AG-UI Serialization](https://docs.ag-ui.com/concepts/serialization)
- [AG-UI Middleware](https://docs.ag-ui.com/quickstart/middleware)
- [AG-UI JavaScript Types](https://docs.ag-ui.com/sdk/js/core/types)
- [AG-UI Python Types](https://docs.ag-ui.com/sdk/python/core/types)
- [AG-UI Python Event Encoder](https://docs.ag-ui.com/sdk/python/encoder/overview)

## 19. 决策记录

| 日期 | 决策 | 状态 |
|---|---|---|
| 2026-08-05 | Hub 是最终产品，瀚海行是首个标杆 Agent | 已确认 |
| 2026-08-05 | 比赛版本不做 Main Agent、自动路由和 Agent 间调用 | 已确认 |
| 2026-08-05 | 第三方 Agent 作为独立服务，通过 Endpoint 接入 | 已确认 |
| 2026-08-05 | 同时支持 Link App、Connected Agent 和 Featured Agent | 已确认 |
| 2026-08-05 | Connected Agent 采用统一流式聊天协议，复杂 Agent 可保留完整工作台 | 已确认 |
| 2026-08-05 | 使用 Campus Agent Hub Contract v1，交互语义对齐 AG-UI 核心事件 | 已确认 |
| 2026-08-05 | 选课评价分析 Agent 作为第二示范应用预留，本阶段不实现 | 已确认 |
| 2026-08-05 | Hub 和所有 Agent 独立运行，Hub 使用独立的 `hub.sqlite3` | 已确认 |
| 2026-08-05 | Gateway 只按 Manifest 选择通用适配器，不包含具体 Agent 业务分支 | 已确认 |
| 2026-08-05 | Registry 分离 Agent 级状态、版本审核状态和版本部署状态，活动版本通过指针原子切换 | 已确认 |
| 2026-08-05 | 前端包含应用广场、Agent 详情、统一聊天、开发者接入和管理员审核五类核心页面 | 已确认 |
| 2026-08-05 | Hub 仅保留最小会话元数据，不默认复制第三方 Agent 聊天正文 | 已确认 |
| 2026-08-05 | Registry 将 Agent 稳定身份与不可变版本分离，新版本审核通过后原子切换 | 已确认 |
| 2026-08-05 | 自动验收提供证据，最终上架仍需管理员批准 | 已确认 |
| 2026-08-05 | 凭据不进入公开 Manifest，Gateway 目标只从服务端 Registry 解析 | 已确认 |
| 2026-08-05 | Endpoint 分为第一方内部与第三方外部信任等级，第三方默认只允许公网 HTTPS | 已确认 |
| 2026-08-05 | Hub 与 Agent 会话和数据库分离，统一聊天使用面向单个 Agent 的短期签名身份 | 已确认 |
| 2026-08-05 | Featured Agent 通过一次性授权码兑换建立自己的本地会话，不共享 Hub Cookie | 已确认 |
| 2026-08-05 | Contract 必须提供可执行 Schema、Conformance Runner 和按版本保存的验收结果 | 已确认 |
| 2026-08-05 | 比赛演示按“广场→瀚海行→完整工作台→第三方注册→协议升级→暂停治理”证明平台价值 | 已确认 |
| 2026-08-05 | 实施顺序为 Contract 冻结、Hub 控制面、瀚海行标准接入、第三方证明、安全与材料 | 已确认 |

## 20. 审计记录

- 现状审计：确认 `apps/hub` 目前只有启动动画和校徽素材，瀚海行具备作为首个标杆 Agent 的真实能力；
- 架构审计：确认 Hub 与 Agent 独立运行、渐进式接入和确定性 Gateway 符合比赛范围；
- AG-UI 官方核对：补齐 `RunAgentInput`、SSE `data:` 线格式、工具调用事件和透明代理保真边界；
- 首轮规格审计：发现并修复主演示依赖暂缓 Agent、协议冻结门槛、内网 SSRF、事件字段、旧文档冲突、版本状态和验收入口等问题；
- 第二轮规格审计：补齐授权码兑换的 Agent 服务认证和分级 SSRF 验收；
- 最终复审：未发现剩余 Blocker、High 或 Medium 问题，状态为 Approved。
