# Agent Gateway 协议调度层设计

## 1. 背景

本项目面向中国科大“一〇七”杯智能体赛道，第一版定位为一个标准兼容的校园 Agent 统筹层：平台不试图替代所有 Agent，而是负责接入、发现、路由、任务状态管理、权限隔离、观测和演示编排。

MVP 允许白名单第三方 Agent 按协议真实接入，同时保留两个内部 Demo Agent 用于稳定演示：

- 课程评价 Deep Research Agent：聚合课程信息、点评和允许访问的附件元数据，生成带来源的选课调研报告。
- 课程资料与复习 Agent：围绕课程公共资料和用户私有资料提供带引用的检索与复习辅助。

`v0.3` 在该控制面上增加个人 Agent Harness：用户通过 `AgentProfile` 选择版本化模板、模型、知识空间、预算和执行偏好；Gateway 负责 Profile 解析入口、策略快照和本地 Runner lease，Harness 负责模板运行、模型适配、缓存和用量。两个内部 Demo 仍按服务粒度发布 Agent Card 和 A2A endpoint，并在内部复用 Harness Core；个人用户/Profile 不单独发布 Agent Card，外部独立 Agent 的 A2A 接入边界保持不变。

推荐技术栈：

- 后端：Python、FastAPI。
- Agent 互联：官方 A2A Python SDK。
- 工具与数据上下文：官方 MCP Python SDK。
- 数据库：PostgreSQL + pgvector。
- 事件与缓存：MVP 先用 PostgreSQL 事件表和进程内通知，出现明确瓶颈后再加入 Redis。
- 观测：MVP 使用结构化 JSON 日志和 correlation ID，保留 OpenTelemetry instrumentation 接口。
- 平台 REST 接口：OpenAPI 3.1 + JSON Schema 2020-12。

### 1.1 协议基线

- Agent-to-Agent 互联锁定为 A2A Protocol 1.0，MVP 只采用 `HTTP+JSON` binding，不跟随 `latest` 自动升级。
- Gateway 每个 A2A 请求都发送 `A2A-Version: 1.0`。空版本会被解释为旧版 0.3，因此平台拒绝缺失版本头的接入，版本不支持时按规范处理 `VersionNotSupportedError`。
- Agent Card 的 `supportedInterfaces[].protocolVersion` 必须为 `1.0`，`protocolBinding` 必须为 `HTTP+JSON`；若原型验证后官方 SDK 只能支持其他 binding，必须统一修改 ADR 和全部文档，不能混用。
- 平台数据库和 UI 可以使用内部状态名，但协议边界只收发 A2A 1.0 标准 `TaskState`，映射由 A2A Adapter 集中维护。
- MCP HTTP 授权语义锁定到 2025-06-18 规范。实施原型选定支持该规范的官方 SDK 后，必须将版本写入依赖锁文件和 ADR。
- 2026-07-19 调研时，A2A Python SDK 最新 release 为 `v1.1.1`，MCP Python SDK 最新 release 为 `v1.28.1`。它们只是 spike 候选，不代表已经完成兼容验证；实施后以锁文件和测试结果为准。

## 2. 目标

- 接入白名单内的独立第三方 Agent，并通过 A2A 与其交换任务。
- 为平台前端、管理脚本和演示程序提供稳定 REST API。
- 用 Agent Card 记录第三方 Agent 的身份、能力、端点、认证方式和流式能力。
- 根据能力描述、任务类型、上下文和健康状态进行路由。
- 统一管理 task、context、message、artifact、event 的生命周期。
- 同时支持 SSE 流式返回和轮询查询，便于不同前端或脚本接入。
- 将 MCP 限定在 Agent 内部工具、数据源和能力扩展边界，不把 MCP 当作 Agent 间任务协议。
- 对外部 Agent 凭据、平台用户凭据、内部工具凭据进行隔离。
- 覆盖超时、失败、取消、追问、人工可读错误和可观测性。
- 提供最小 SDK，让第三方 Agent 可以用少量代码完成注册、健康检查和 A2A 接入。
- 解析 AgentProfile、ModelProfile、CachePolicy 与 RuntimePolicy，向 Harness 签发最小执行票据。
- 支持平台托管与只出站本地 Runner 两种执行位置，且不静默切换模型、密钥归属或运行位置。

## 3. 非目标

- 不做开放 Agent 市场、公开上架、评分排行或收益分成。
- 不做多租户计费、复杂组织管理和合同级权限模型。
- 不做复杂审批流；MVP 只支持仓库配置或管理员手工录入白名单。
- 不承诺兼容任意未知 Agent，只支持按文档完成 Agent Card 和 A2A 端点的白名单 Agent。
- 不在平台内保存第三方 Agent 的真实 API key 示例或用户个人敏感数据。
- 不强制第三方 Agent 暴露内部工具；工具接入由各 Agent 自己通过 MCP 管理。
- 不把每个个人 Agent 变成独立公网 A2A 服务，不让 Gateway 直接访问用户 `localhost`。
- 不在比赛 MVP 收集普通用户真实托管 BYOK，也不开放任意用户自定义模型 `base_url`。
- 不把模型 provider 包装成 A2A Agent 或 MCP Tool。

## 4. 核心组件

```text
frontend / demo script
        |
        v
FastAPI REST API  ---- OpenAPI 3.1 / JSON Schema 2020-12
        |
        v
Agent Gateway
  - registry
  - planner / router
  - task manager
  - profile resolver / policy guard
  - runner lease manager
  - event stream
  - credential broker
  - observability middleware
        |
        +---- A2A client ---- external agent A
        +---- A2A client ---- external agent B
        +---- A2A client ---- internal demo agents

        +---- Harness ---- managed model adapter
        +---- Runner lease ---- outbound local runner

internal demo agent
        |
        +---- MCP client ---- MCP server for tools / data
```

组件职责：

- API 层：提供任务创建、任务查询、流式事件、取消、Agent 列表、Agent 注册草稿校验等接口。
- Registry：保存白名单 Agent、Agent Card 摘要、认证配置引用、健康状态和最近一次探测结果。
- Router：根据能力标签、输入模态、输出模态、任务类型、优先级、健康状态和演示策略选择 Agent。
- Task Manager：维护统一任务模型，跟踪 A2A task 与平台 task 的映射。
- Event Stream：把平台事件和 A2A 流式事件统一为 SSE 事件，同时落库供轮询读取。
- Credential Broker：按 Agent 维度读取凭据引用，运行时注入请求，不把密钥暴露给前端或日志。
- Profile Resolver：把 AgentTemplate、AgentProfile、ModelProfile、知识空间和预算解析为不可变执行快照；只把短期 `execution_ticket_id` 交给 Harness。
- Runner Lease Manager：设备配对后向只出站 Runner 签发短期、单用户、单任务租约，校验 nonce、序号、过期和幂等。
- Observability：基线用结构化日志关联 `task_id`、`agent_id`、`context_id`；核心链路稳定后用 OpenTelemetry 扩展 trace、metric 和 log。

## 5. Agent Card 与白名单注册

### 5.1 标准 Agent Card 与平台注册记录

A2A Agent 在 `/.well-known/agent-card.json` 发布标准 Agent Card，其中包含 Agent 名称、说明、版本、接口、能力、安全声明、输入输出模式和 skills。平台不修改该标准结构，而是在 Registry 中保存经过校验的 Agent Card 快照和平台治理字段。

MVP 校验器至少检查 `name`、`description`、`version`、`supportedInterfaces`、`capabilities`、`defaultInputModes`、`defaultOutputModes` 和 `skills`。每个 `supportedInterfaces` 项必须包含 `url`、`protocolBinding: "HTTP+JSON"` 和 `protocolVersion: "1.0"`；生产/公网端点必须使用 HTTPS，本地开发只允许显式配置的 `http://localhost` 或容器内测试网络。每个 skill 至少包含 `id`、`name`、`description` 和 `tags`。需要认证时，`securitySchemes` 与 `securityRequirements` 的引用必须一致且不得包含密钥明文。

标准 A2A 1.0 Agent Card 最小示例：

```json
{
  "name": "Course Research Demo Agent",
  "description": "Generate an evidence-based course research report",
  "version": "0.1.0",
  "supportedInterfaces": [
    {
      "url": "https://course-research.example.edu/a2a",
      "protocolBinding": "HTTP+JSON",
      "protocolVersion": "1.0"
    }
  ],
  "capabilities": {"streaming": true},
  "defaultInputModes": ["text/plain", "application/json"],
  "defaultOutputModes": ["text/markdown", "application/json"],
  "skills": [
    {
      "id": "course_research",
      "name": "Course Research",
      "description": "Research a course with evidence and timestamps",
      "tags": ["course", "research"]
    }
  ]
}
```

以下是平台注册记录的关键字段，不是对 A2A Agent Card 的重新定义：

- `agent_id`：平台内唯一标识，例如 `ustc-campus-qa`.
- `display_name`：展示名。
- `owner`：负责团队或联系人，不写个人敏感信息。
- `a2a_endpoint`：A2A 服务入口。
- `card_url`：Agent Card 发现地址，可选。
- `skills_index`：从标准 Agent Card `skills[].id` 和 `skills[].tags` 生成的平台注册索引，例如 `course_research`、`study_rag`、`campus_service`。
- `input_modes`：支持输入，例如 `text/plain`、`application/json`.
- `output_modes`：支持输出，例如 `text/plain`、`application/json`.
- `supports_streaming`：是否支持 A2A 流式事件。
- `supports_async`：是否支持异步任务。
- `auth_profile_ref`：凭据引用，不是密钥明文。
- `status`：`draft`、`enabled`、`disabled`、`blocked`.

### 5.2 白名单注册流程

1. 第三方团队提交 Agent Card URL、A2A endpoint、能力说明和测试账号需求。
2. 管理员用注册校验脚本拉取 Agent Card，验证 JSON 结构、端点可达性、认证方式和能力字段；拉取前后都要阻止 loopback、私网、链路本地、保留地址、跨域重定向和 DNS rebinding。
3. 平台创建 `agent_id`，写入白名单配置或数据库。
4. 凭据只写入本地密钥管理环境或部署平台 Secret，不进入 Git 仓库。
5. 平台从已审核的 Agent Card 快照选择 endpoint，执行健康检查和最小任务握手，不信任提交表单中与 Card 不一致的 `a2a_endpoint`。
6. 通过后将 `status` 置为 `enabled`，Router 才允许真实路由。

MVP 可以先用一个受版本控制的 `agents.allowlist.example.json` 说明字段，但真实凭据和真实内网地址放在部署环境中。

### 5.3 平台 Registry 记录示例

```json
{
  "agent_id": "course-research-demo",
  "display_name": "Course Research Demo Agent",
  "owner": "AI for better life In ustc",
  "a2a_endpoint": "https://course-research.example.edu/a2a",
  "card_url": "https://course-research.example.edu/.well-known/agent-card.json",
  "skills_index": [
    {
      "id": "course_research",
      "description": "Generate an evidence-based course research report",
      "input_modes": ["text/plain"],
      "output_modes": ["text/markdown", "application/json"],
      "tags": ["course", "research", "demo"]
    }
  ],
  "supports_streaming": true,
  "supports_async": true,
  "auth_profile_ref": "secret://agents/course-research-demo/token",
  "status": "enabled"
}
```

示例中的 `secret://...` 只是凭据引用格式，不代表真实密钥。

## 6. 能力发现与路由

### 6.1 发现来源

能力发现来自三类信息：

- 静态白名单：管理员认可的 Agent Card 摘要。
- 主动探测：定期安全拉取 `card_url` 并检查版本、能力和健康状态；Card、endpoint 或权限声明变化后先进入待复审状态，不自动覆盖已批准快照。
- 运行反馈：任务成功率、超时率、取消率、平均首 token 时间、平均完成时间。

MVP 不做复杂语义市场匹配，优先使用可解释的规则路由。

### 6.2 路由输入

以下是 `TaskRequest` 经身份校验后的内部归一化路由输入；`execution_preferences` 与 10.4 节的 REST Schema 保持同一形态：

```json
{
  "task_type": "course_research",
  "user_message": "请调研人工智能与机器学习基础的工作量和学习收获。",
  "agent_profile_id": "profile_example",
  "required_input_mode": "text/plain",
  "preferred_output_mode": "text/plain",
  "execution_preferences": {
    "runtime_preference": "local_first",
    "cache_preference": "shared_first",
    "fallback_policy": "ask_before_switch"
  },
  "context_id": "ctx_01JZEXAMPLE0000000000000",
  "allow_streaming": true
}
```

### 6.3 路由规则

1. 过滤 `status != enabled` 的 Agent。
2. 过滤不支持输入或输出模态的 Agent。
3. 根据 `task_type` 和平台注册的 `skills_index[].id`/`tags` 匹配；`task_type` 是可扩展 skill hint，不是 Gateway 硬编码枚举。
4. 若多个 Agent 匹配，优先选择健康状态正常、最近失败率低、支持流式返回的 Agent。
5. 若仍有多个候选，MVP 采用配置优先级；后续再加入 embedding 召回或学习型策略。
6. 如果没有候选，返回可解释错误，并建议用户换一种任务类型或启用内部 Demo。
7. 若使用个人 Agent，先校验 Profile 所属用户、模板能力、知识空间、模型 capability、预算和数据 egress；这些治理字段不参与第三方 Agent Card 匹配，也不进入第三方 A2A payload。
8. MVP 不自动切换 Agent。当前 Agent 不可用时重试一次后失败；只有用户明确确认后，才可创建新任务并选择同 skill、同数据 scope、预批准且用户可见的另一 Agent。

### 6.4 路由结果

```json
{
  "selected_agent_id": "course-research-demo",
  "reason": "matched capability course_research and text/plain input",
  "streaming": true
}
```

## 7. A2A Task 与 Context 生命周期

平台内部 task 与 A2A task 一一映射或一对多映射。MVP 先采用一对一；需要多 Agent 协作时再扩展为父子任务。

### 7.1 Context

`context_id` 表示一次用户会话或一组相关任务的上下文。它保存：

- 用户可见的对话摘要。
- 任务历史引用。
- 被允许传给外部 Agent 的最小必要上下文。
- 隐私与授权边界。

上下文默认不把完整历史无脑转发给第三方 Agent。Router 和 Task Manager 需要按任务构造最小上下文。

### 7.2 Task 状态

平台内部状态采用小写下划线命名，A2A Adapter 显式映射到 A2A 1.0 线级枚举。业务代码不得在 A2A 请求或响应中使用内部状态字符串。

| 平台状态 | A2A 1.0 wire `TaskState` | 含义 |
|---|---|---|
| `submitted` | `TASK_STATE_SUBMITTED` | 平台已收到并创建外部任务 |
| `routed` | 无，仅平台内部 | 已选定 Agent、尚未创建外部任务 |
| `working` | `TASK_STATE_WORKING` | Agent 已接收并处理中 |
| `input_required` | `TASK_STATE_INPUT_REQUIRED` | Agent 需要用户补充信息 |
| `auth_required` | `TASK_STATE_AUTH_REQUIRED` | Agent 需要用户完成授权 |
| `completed` | `TASK_STATE_COMPLETED` | Agent 完成并返回最终结果 |
| `failed` | `TASK_STATE_FAILED` | Agent 或平台处理失败 |
| `rejected` | `TASK_STATE_REJECTED` | Agent 拒绝当前任务 |
| `cancel_requested` | 无，仅平台内部 | 用户请求取消，等待 Agent 确认 |
| `cancelled` | `TASK_STATE_CANCELED` | A2A 任务已取消 |
| `timeout` | 无，仅平台内部 | 超过平台时限并发出 best-effort 取消 |

`TASK_STATE_UNSPECIFIED` 一律视为协议错误，不映射为正常业务状态。

状态流转：

```text
submitted -> routed -> working -> completed
                         |  |  |
                         |  |  +-> failed
                         |  +----> input_required -> working
                         |  +----> auth_required -> working
                         |  +----> rejected
                         +-------> cancel_requested -> cancelled
                         +-------> timeout
```

### 7.3 Message 与 Artifact

- Message：用户、平台和 Agent 之间的文本或结构化输入输出。
- Artifact：Agent 生成的稳定结果，例如 JSON 结果、Markdown 答案、文件引用、结构化计划。
- Event：状态变化、流式片段、错误、追问请求和取消确认。

平台存储 Artifact 摘要和安全引用；大文件先进入受控本地文件区，部署确需跨容器共享时再接对象存储。

## 8. SSE 与轮询

### 8.1 SSE

用于演示和交互式前端，接口：

```http
GET /v1/tasks/{task_id}/events
Accept: text/event-stream
Authorization: Bearer <platform-user-token>
```

事件示例：

```text
id: 1
event: task.status
data: {"task_id":"task_01JZEXAMPLE","seq":1,"event_type":"task.status","occurred_at":"2026-07-19T09:20:01Z","payload":{"status":"working","agent_id":"course-research-demo"}}

id: 2
event: task.delta
data: {"task_id":"task_01JZEXAMPLE","seq":2,"event_type":"task.delta","occurred_at":"2026-07-19T09:20:03Z","payload":{"delta":"正在聚合不同学期的课程评价"}}

id: 3
event: task.artifact
data: {"task_id":"task_01JZEXAMPLE","seq":3,"event_type":"task.artifact","occurred_at":"2026-07-19T09:20:08Z","payload":{"artifact":{"artifact_id":"art_01JZEXAMPLE","artifact_type":"course_report","schema_version":"1.0","data":{},"files":[],"citations":[]}}}

id: 4
event: task.status
data: {"task_id":"task_01JZEXAMPLE","seq":4,"event_type":"task.status","occurred_at":"2026-07-19T09:20:08Z","payload":{"status":"completed","artifact_ids":["art_01JZEXAMPLE"]}}
```

SSE 的 `data` 始终是完整 `TaskEvent`，轮询接口读取同一事件源。连接断开时，前端可以带 `Last-Event-ID` 重连；平台按 `seq` 去重和补发，不得因为重连重复生成最终 Artifact。MVP 先把事件追加写入 PostgreSQL 事件表；只有单库方案无法满足时再增加 Redis Stream。

### 8.2 轮询

用于脚本、非流式 Agent 或网络不稳定场景：

```http
GET /v1/tasks/{task_id}
Authorization: Bearer <platform-user-token>
```

响应示例：

```json
{
  "task_id": "task_01JZEXAMPLE",
  "status": "completed",
  "agent_id": "campus-qa-demo",
  "context_id": "ctx_01JZEXAMPLE0000000000000",
  "created_at": "2026-07-19T09:20:00Z",
  "updated_at": "2026-07-19T09:20:08Z",
  "artifacts": [
    {
      "artifact_id": "art_01JZEXAMPLE",
      "artifact_type": "course_report",
      "schema_version": "1.0",
      "data": {"summary": "基于当前来源生成的课程调研摘要。"},
      "files": [],
      "citations": []
    }
  ],
  "last_event_seq": 4
}
```

## 9. MCP 边界

MCP 用于 Agent 内部连接工具、数据库、文件、向量检索、浏览器或校园系统适配器。平台边界如下：

- Gateway 与独立 Agent 之间使用 A2A，不使用 MCP 传递 Agent 任务。
- 内部 Demo Agent 可以作为 MCP client 调用内部 MCP server。
- 外部第三方 Agent 的 MCP server 不直接暴露给平台用户。
- 平台不透传用户 token 给 MCP server；由 Agent 根据自己的授权模型访问工具。
- MCP 工具返回结果必须经过 Agent 汇总和脱敏后再作为 A2A artifact 返回。
- 点评、网页、文件和工具返回都属于不可信证据，不能覆盖系统指令、扩大权限或自行触发额外工具调用。

这样划分可以避免把工具协议误用为跨 Agent 编排协议，也减少凭据扩散。

## 10. 统一任务数据模型

### 10.1 表结构草案

```text
agents
  id
  display_name
  owner
  a2a_endpoint
  card_url
  capabilities_json
  auth_profile_ref
  supports_streaming
  supports_async
  status
  health_status
  created_at
  updated_at

agent_profiles
  id
  subject_id
  template_id
  template_version
  model_profile_id
  knowledge_space_refs
  memory_policy
  cache_preference
  fallback_policy
  status

model_profiles
  id
  subject_id
  provider_id
  model_id
  execution_mode
  secret_ref
  capability_snapshot_id
  budget_policy_id
  status

contexts
  id
  subject_id
  title
  summary
  policy_json
  data_scope
  retention_until
  created_at
  updated_at

tasks
  id
  context_id
  subject_id
  selected_agent_id
  external_task_id
  task_type
  agent_profile_id
  execution_ticket_id
  runtime_mode
  cache_policy_id
  fallback_policy
  cost_budget_ref
  runner_lease_id
  status
  input_json
  route_reason
  timeout_at
  data_scope
  retention_until
  created_at
  updated_at

task_events
  id
  task_id
  seq
  event_type
  payload_json
  data_scope
  retention_until
  created_at

artifacts
  id
  task_id
  content_type
  content_json
  content_text
  data_scope
  retention_until
  created_at
```

`subject_id` 使用平台生成的随机、不透明标识或演示账号 UUID，不由学号、邮箱、手机号等直接哈希得到，避免可枚举标识被反查。真实身份映射若未来需要，由独立身份层保存，不进入任务/事件正文。
`local_authenticated` 和 `private` 的 context/task/event/Artifact 默认不持久化正文。`contexts.summary` 与 `tasks.input_json` 只保存脱敏 envelope、scope、计数和安全引用，不保存完整 `message`、`query` 或本地证据摘要；过期清理同时覆盖上下文、任务、事件、Artifact、对象和缓存。
`model_profiles.secret_ref` 只允许空值或用户拥有的随机引用，不能保存 key 明文。比赛 MVP 的普通用户 `local_runner` Profile 令 `secret_ref=null`，key 只存在用户设备。执行票据、Runner lease 和预算引用都有独立短 TTL，不作为长期用户配置复用。

### 10.2 共享 Envelope 与文件引用

Gateway、两个 Demo Agent 和外部示例 Agent 共享四个版本化契约：

| 契约 | 必填字段 | 用途 |
|---|---|---|
| `TaskRequest` | `task_type`、`message`、`inputs`、`schema_version` | 创建可扩展任务 |
| `TaskEvent` | `task_id`、`seq`、`event_type`、`occurred_at`、`payload` | 保证 SSE 与轮询使用同一事件源 |
| `ArtifactEnvelope` | `artifact_id`、`artifact_type`、`schema_version`、`data` | 承载课程报告、导入回执、检索结果和回答 |
| `FileRef` | `file_id`、`owner_scope`、`media_type`、`size_bytes` | 引用平台暂存文件，不传原始凭据或任意 URL |

`ArtifactEnvelope` 的最小 JSON Schema：

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://example.edu/schemas/artifact-envelope-v1.json",
  "type": "object",
  "required": ["artifact_id", "artifact_type", "schema_version", "data"],
  "properties": {
    "artifact_id": {"type": "string"},
    "artifact_type": {
      "type": "string",
      "enum": ["course_report", "ingestion_receipt", "retrieval_result", "study_answer", "deletion_receipt", "source_list", "campus_notice"]
    },
    "schema_version": {"const": "1.0"},
    "data": {"type": "object"},
    "files": {"type": "array", "items": {"$ref": "file-ref-v1.json"}, "default": []},
    "citations": {"type": "array", "items": {"type": "object"}, "default": []}
  },
  "additionalProperties": false
}
```

`TaskEvent` 的最小 JSON Schema：

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://example.edu/schemas/task-event-v1.json",
  "type": "object",
  "required": ["task_id", "seq", "event_type", "occurred_at", "payload"],
  "properties": {
    "task_id": {"type": "string"},
    "seq": {"type": "integer", "minimum": 1},
    "event_type": {
      "type": "string",
      "enum": ["task.status", "task.delta", "task.artifact", "task.question", "task.error"]
    },
    "occurred_at": {"type": "string", "format": "date-time"},
    "payload": {"type": "object"}
  },
  "additionalProperties": false
}
```

`task.status.payload.status` 只使用平台状态表中的值；`task.artifact.payload.artifact` 必须是完整 `ArtifactEnvelope`；`task.question` 必须给出问题和允许的输入模态；`task.error` 必须使用统一错误码，不能携带堆栈、请求头或凭据。

`FileRef` 的最小 JSON Schema：

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://example.edu/schemas/file-ref-v1.json",
  "type": "object",
  "required": ["file_id", "owner_scope", "media_type", "size_bytes"],
  "properties": {
    "file_id": {"type": "string"},
    "owner_scope": {"type": "string", "enum": ["public", "course_shared", "private"]},
    "media_type": {"type": "string"},
    "size_bytes": {"type": "integer", "minimum": 1},
    "display_name": {"type": "string"}
  },
  "additionalProperties": false
}
```

原始文件先通过 Gateway 文件暂存接口进入受控文件区，再把 `FileRef` 放入 A2A 结构化 Data Part。MVP 不通过 A2A 传输原始二进制，也不允许 Agent 接收任意外部 URL 后自行下载。Gateway 只生成短期、限定对象、限定 Agent 的内部读取授权，授权值不进入 Artifact、日志或数据库正文；部署确需跨容器共享时再把受控文件区替换为 S3/MinIO。

`FileRef` 是不透明引用，不是授权证明。客户端提交的 `owner_scope`、`media_type` 和 `size_bytes` 只用于一致性检查，Gateway 必须根据当前用户身份在服务端文件记录中重新解析所有权、真实 MIME、大小、状态和有效期。伪造 scope、复用他人的 `file_id`、过期或已删除引用必须被拒绝。

### 10.3 三模块操作契约

| `task_type` | Request `data` 最小字段 | Artifact 类型与最小字段 |
|---|---|---|
| `course_research` | `query`、`access_mode`、可选 `course_filters` | `course_report`：`candidates`、`summary`、`dimensions`、`evidence`、`data_scope`、`generated_at` |
| `study.ingest` | `course_id`、`knowledge_space`、`source_policy`；文件放在 `TaskRequest.inputs` | `ingestion_receipt`：`job_id`、`status`、`accepted_files` |
| `study.ingestion_status` | `job_id` | `ingestion_receipt`：`job_id`、`status`、`progress`、`errors` |
| `study.search` | `course_id`、`query`、`top_k` | `retrieval_result`：`chunks`、`citations`、`data_scope` |
| `study.answer` | `course_id`、`question` | `study_answer`：`answer`、`citations`、`confidence` |
| `study.delete` | `document_id` | `deletion_receipt`：`document_id`、`status`、`invalidated_at` |
| `study.list_sources` | `course_id`、可选 `knowledge_space` | `source_list`：`sources`、`data_scope`、`generated_at` |
| `campus.notice.lookup` | `query`、可选 `category`、可选 `date_range` | `campus_notice`：`items`、`sources`、`generated_at` |

具体业务 Schema 由各 Agent 文档定义，Gateway 只校验版本化 envelope 和已注册 skill 的输入/输出 Schema 引用。

`campus.notice.lookup` 是外部示例 Agent 的固定验收能力，只读取团队自制或明确公开的通知样例。它必须作为独立进程发布标准 Agent Card，至少用 10 条固定任务验证正常查询、无结果、追问、取消和不可达；接入时只增加 Agent Card/白名单配置和 Schema，不修改 Gateway 路由业务代码。

外部 Agent Card 的 `skills` 至少包含：

```json
{
  "id": "campus.notice.lookup",
  "name": "Campus Notice Lookup",
  "description": "Find public campus notices from an approved sample source",
  "tags": ["campus", "notice", "public-data"]
}
```

固定请求 `data` 示例：

```json
{
  "query": "查找下周图书馆开放时间通知",
  "category": "library",
  "date_range": ["2026-07-20", "2026-07-27"]
}
```

`campus_notice` Artifact 的 `data` 至少包含 `items[]`、`sources[]` 和 `generated_at`；每个 item 包含标题、日期、摘要和来源引用，不返回任意文件下载 URL。

实施时将以下用例固化到 `tests/fixtures/external-agent/campus-notice-cases.json`；文档阶段先冻结语义：

| # | 场景 | 输入摘要 | 预期状态/结果 |
|---:|---|---|---|
| 1 | 精确查询 | 图书馆开放时间 + 明确日期 | `completed`，至少 1 条带来源 item |
| 2 | 类别过滤 | `category=library` | `completed`，结果类别全部匹配 |
| 3 | 日期过滤 | 明确 `date_range` | `completed`，结果日期不越界 |
| 4 | 信息不足 | “查下周通知”但当前日期上下文缺失 | `input_required`，补充后 `completed` |
| 5 | 无结果 | 合法但不存在的通知 | `completed`，`items=[]`，不是幻觉结果 |
| 6 | 不支持类别 | 超出 Agent Card 声明范围 | `rejected`，给出可解释原因 |
| 7 | 取消 | working 期间请求取消 | `cancelled`，不产生最终业务 Artifact |
| 8 | 非法载荷 | 缺少 `query` | Gateway 校验失败，不创建外部 task |
| 9 | Agent 不可达 | 停止外部进程后提交 | 平台 `failed/agent_unreachable`，健康状态更新 |
| 10 | SSE 中断 | 中途断开后轮询同一 task | `completed`，最终 Artifact 只生成一次 |

### 10.4 TaskRequest JSON Schema 草案

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://example.edu/schemas/task-create.json",
  "type": "object",
  "required": ["schema_version", "task_type", "message", "inputs", "data"],
  "properties": {
    "schema_version": {"const": "1.0"},
    "task_type": {
      "type": "string",
      "minLength": 2,
      "maxLength": 64,
      "pattern": "^[a-z][a-z0-9_.-]+$"
    },
    "message": {
      "type": "string",
      "minLength": 1,
      "maxLength": 4000
    },
    "inputs": {
      "type": "array",
      "items": {"$ref": "file-ref-v1.json"},
      "default": []
    },
    "data": {
      "type": "object",
      "default": {}
    },
    "agent_profile_id": {
      "type": "string",
      "pattern": "^profile_[A-Za-z0-9_]+$"
    },
    "execution_preferences": {
      "type": "object",
      "properties": {
        "runtime_preference": {
          "type": "string",
          "enum": ["managed_first", "local_first", "managed_only", "local_only"]
        },
        "cache_preference": {
          "type": "string",
          "enum": ["shared_first", "regenerate"]
        },
        "fallback_policy": {
          "type": "string",
          "enum": ["none", "ask_before_switch"]
        }
      },
      "additionalProperties": false
    },
    "context_id": {
      "type": "string",
      "pattern": "^ctx_[A-Za-z0-9_]+$"
    },
    "stream": {
      "type": "boolean",
      "default": true
    },
    "metadata": {
      "type": "object",
      "properties": {
        "demo_scene": {"type": "string", "maxLength": 64},
        "client_request_id": {"type": "string", "maxLength": 128}
      },
      "additionalProperties": false
    }
  },
  "additionalProperties": false
}
```

### 10.5 平台契约到 A2A 的映射

- 前端向 Gateway REST API 提交 `TaskRequest`；它不是直接发给外部端点的自定义 A2A 请求。
- A2A Adapter 创建标准 A2A Message：`message` 进入文本 Part，`schema_version`、`task_type`、`data` 和经过服务端验证的 `inputs` 进入结构化 Data Part。
- `agent_profile_id` 与 `execution_preferences` 只在 Gateway/Harness 控制面使用。Gateway 解析出的 ModelProfile、secret_ref、预算、缓存与 Runner 字段不得进入第三方 A2A Data Part；团队控制的内部 Harness Agent 只接收短期 `execution_ticket_id` 和最小 data scope，不接收原始 Profile 或密钥。第三方业务 Agent 只接收完成任务所需、不可反推用户配置的 scope。
- `task_type` 必须匹配已批准 Agent Card 的 skill ID 或平台注册映射，但不添加 A2A 规范之外的顶层字段。
- 外部 Agent 返回标准 A2A Task、Message 和 Artifact；业务结果放在 Artifact 的结构化 Data Part 中，并校验为 `ArtifactEnvelope`。
- A2A wire `TaskState`、状态更新和 Artifact 更新由 Adapter 转换为平台 `TaskEvent`。`TaskEvent` 是 Gateway 对前端的统一事件契约，不冒充 A2A wire event。
- 平台保存内部 `task_id` 与外部 A2A task/context ID 映射，取消、追问、轮询和 SSE 重连都通过该映射继续同一任务。

因此，外部 Agent 仍然只需要实现标准 A2A endpoint；平台业务 Schema 作为 A2A Data Part 的内容协商，不形成第二套传输协议。

## 11. REST API 草案

OpenAPI 3.1 文档由 FastAPI 自动生成后人工补充描述。核心接口：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/v1/tasks` | 创建任务并触发路由 |
| `GET` | `/v1/tasks/{task_id}` | 查询任务当前状态和最终结果 |
| `GET` | `/v1/tasks/{task_id}/events` | 订阅 SSE 事件 |
| `POST` | `/v1/tasks/{task_id}/cancel` | 请求取消任务 |
| `POST` | `/v1/tasks/{task_id}/messages` | 回复 Agent 的追问 |
| `POST` | `/v1/files` | 暂存用户授权文件并返回 `FileRef` |
| `DELETE` | `/v1/files/{file_id}` | 删除未入库或已撤回的暂存文件 |
| `GET` | `/v1/agents` | 查看可用白名单 Agent |
| `GET` | `/v1/agent-templates` | 查看可用 AgentTemplate 及所需模型能力 |
| `GET` / `POST` | `/v1/agent-profiles` | 查看或创建本人 AgentProfile |
| `PATCH` | `/v1/agent-profiles/{profile_id}` | 修改本人 Profile 的模型、空间与偏好绑定 |
| `GET` / `POST` | `/v1/model-profiles` | 查看或创建本人 ModelProfile；请求不接受 key 明文 |
| `PATCH` | `/v1/model-profiles/{model_profile_id}` | 修改/停用本人模型配置；切换需显式确认 |
| `GET` | `/v1/tasks/{task_id}/execution` | 查看脱敏 provider、runtime、cache 与 usage 元数据 |
| `POST` | `/v1/runners/pairing-codes` | 已登录用户创建一次性短期设备配对码 |
| `POST` | `/v1/runners/pair` | Runner 用配对码换取短期、绑定设备的 runner token |
| `GET` | `/v1/runners/leases/next` | Runner 长轮询本人下一条 lease offer |
| `POST` | `/v1/runners/leases/{lease_id}/claim` | 原子认领 lease，校验 nonce 与序号 |
| `POST` | `/v1/runners/leases/{lease_id}/heartbeat` | 在最大任务时限内短期续租 |
| `POST` | `/v1/runners/leases/{lease_id}/events` | 按序提交允许的 TaskEvent |
| `POST` | `/v1/runners/leases/{lease_id}/complete` | 幂等提交 RunnerResult、Artifact 与 UsageRecord |
| `POST` | `/v1/admin/agents/validate` | 校验 Agent Card 草稿 |
| `POST` | `/v1/admin/agents` | 管理员启用白名单 Agent |
| `GET` | `/healthz` | 平台健康检查 |

Profile 接口只允许当前用户访问本人对象。ModelProfile 创建接口只接收 provider、model、执行模式、能力快照和预算引用，不接受 `api_key`、任意 `base_url` 或密钥明文；比赛 MVP 的普通用户 `local_runner` Profile 固定 `secret_ref=null`，平台/团队测试凭据由部署配置绑定。

### 11.1 RunnerLease

`GET /v1/runners/leases/next` 只返回与 runner token 的 `subject_id`、`runner_id` 和设备绑定一致的 offer。`claim` 必须原子完成，成功后才返回最小数据引用；offer 未认领、已过期或 credential/policy/profile version 已变化时不得执行。

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://example.edu/schemas/runner-lease-v1.json",
  "type": "object",
  "required": [
    "lease_id",
    "runner_id",
    "subject_id",
    "task_id",
    "execution_ticket_id",
    "data_scope",
    "nonce",
    "lease_seq",
    "policy_version",
    "profile_version",
    "expires_at"
  ],
  "properties": {
    "lease_id": {"type": "string", "pattern": "^lease_[A-Za-z0-9_]+$"},
    "runner_id": {"type": "string", "pattern": "^runner_[A-Za-z0-9_]+$"},
    "subject_id": {"type": "string", "pattern": "^subject_[A-Za-z0-9_]+$"},
    "task_id": {"type": "string", "pattern": "^task_[A-Za-z0-9_]+$"},
    "execution_ticket_id": {"type": "string", "pattern": "^ticket_[A-Za-z0-9_]+$"},
    "data_scope": {"type": "string", "enum": ["public", "course_shared", "private", "local_authenticated"]},
    "allowed_data_refs": {"type": "array", "items": {"type": "string"}, "maxItems": 32, "default": []},
    "nonce": {"type": "string", "minLength": 24, "maxLength": 128},
    "lease_seq": {"type": "integer", "minimum": 1},
    "policy_version": {"type": "integer", "minimum": 1},
    "profile_version": {"type": "integer", "minimum": 1},
    "expires_at": {"type": "string", "format": "date-time"}
  },
  "additionalProperties": false
}
```

### 11.2 UsageRecord

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://example.edu/schemas/usage-record-v1.json",
  "type": "object",
  "required": [
    "task_id",
    "subject_id",
    "provider_id",
    "model_id",
    "runtime_mode",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "occurred_at"
  ],
  "properties": {
    "task_id": {"type": "string", "pattern": "^task_[A-Za-z0-9_]+$"},
    "subject_id": {"type": "string", "pattern": "^subject_[A-Za-z0-9_]+$"},
    "provider_id": {"type": "string", "pattern": "^[a-z][a-z0-9_-]{1,63}$"},
    "model_id": {"type": "string", "minLength": 1, "maxLength": 128},
    "runtime_mode": {"type": "string", "enum": ["platform_sponsored", "managed_byok", "local_runner"]},
    "input_tokens": {"type": "integer", "minimum": 0},
    "output_tokens": {"type": "integer", "minimum": 0},
    "cache_read_tokens": {"type": "integer", "minimum": 0},
    "estimated_cost": {"type": ["number", "null"], "minimum": 0},
    "currency": {"type": ["string", "null"], "maxLength": 8},
    "error_code": {"type": ["string", "null"], "maxLength": 64},
    "occurred_at": {"type": "string", "format": "date-time"}
  },
  "additionalProperties": false
}
```

未知或可能过期的价格令 `estimated_cost` 与 `currency` 为 `null`，不能伪造精确费用。服务端用 budget ticket 校验 token 和并发，并把 Runner 上报 usage 视为待校验数据。

### 11.3 RunnerResult

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://example.edu/schemas/runner-result-v1.json",
  "type": "object",
  "required": ["lease_id", "runner_id", "task_id", "nonce", "lease_seq", "result_seq", "status", "usage"],
  "properties": {
    "lease_id": {"type": "string", "pattern": "^lease_[A-Za-z0-9_]+$"},
    "runner_id": {"type": "string", "pattern": "^runner_[A-Za-z0-9_]+$"},
    "task_id": {"type": "string", "pattern": "^task_[A-Za-z0-9_]+$"},
    "nonce": {"type": "string", "minLength": 24, "maxLength": 128},
    "lease_seq": {"type": "integer", "minimum": 1},
    "result_seq": {"type": "integer", "minimum": 1},
    "status": {"type": "string", "enum": ["completed", "failed", "cancelled"]},
    "events": {"type": "array", "items": {"$ref": "task-event-v1.json"}, "maxItems": 256, "default": []},
    "artifact": {"oneOf": [{"$ref": "artifact-envelope-v1.json"}, {"type": "null"}]},
    "usage": {"$ref": "usage-record-v1.json"},
    "error_code": {"type": ["string", "null"], "maxLength": 64}
  },
  "additionalProperties": false
}
```

配对码单次使用且默认 5 分钟失效；runner token 绑定用户、设备和 `runner` audience。offer/claim/heartbeat/events/complete 都校验 `runner_id`、`subject_id`、nonce、lease_seq、过期时间和 credential/policy/profile version，其中 `subject_id` 是平台随机不透明标识，必须与 runner token 和任务 owner 一致。所有改变状态的 Runner POST 接口要求 `Idempotency-Key`；重复正文返回原结果，key 相同正文不同返回冲突。`events` 严格递增，`complete` 只能成功一次；Gateway 对 Artifact、引用、大小和安全渲染重新校验，不信任 Runner 结果。

创建任务请求：

```http
POST /v1/tasks
Content-Type: application/json
Authorization: Bearer <platform-user-token>
Idempotency-Key: <client-generated-unique-key>
```

```json
{
  "schema_version": "1.0",
  "task_type": "course_research",
  "message": "请比较这门课近两年的工作量、给分和学习收获。",
  "inputs": [],
  "data": {
    "query": "请比较这门课近两年的工作量、给分和学习收获。",
    "access_mode": "public"
  },
  "stream": true,
  "metadata": {
    "demo_scene": "course-selection"
  }
}
```

所有改变状态的 POST/PATCH 接口都必须支持幂等：任务、文件、Profile、ModelProfile、Runner 配对、lease claim/heartbeat/events/complete、取消和追问均接受 `Idempotency-Key`。同一主体、同一 key 和同一请求正文返回原结果；key 相同但正文不同返回冲突。取消请求还携带 `cancel_request_id`，重复取消返回当前终态；追问回答携带 `message_id`，同一消息只能映射到外部 task 一次。Gateway 在调用 A2A Agent 或签发 lease 前持久化幂等记录，避免网络重试生成重复任务、消息、租约或 Artifact。

响应：

```json
{
  "task_id": "task_01JZEXAMPLE",
  "context_id": "ctx_01JZEXAMPLE0000000000000",
  "status": "submitted",
  "events_url": "/v1/tasks/task_01JZEXAMPLE/events",
  "poll_url": "/v1/tasks/task_01JZEXAMPLE"
}
```

取消任务：

```json
{
  "cancel_request_id": "cancel_01JZEXAMPLE",
  "reason": "user_cancelled_from_demo_ui"
}
```

追问回复：

```json
{
  "message_id": "msg_01JZEXAMPLE",
  "message": "我想查东区到西区方向，今天下午的班次。",
  "content_type": "text/plain"
}
```

## 12. 认证授权与凭据隔离

### 12.1 认证对象

- 平台用户：访问 Gateway REST API 的前端用户或演示脚本。
- 外部 Agent：被 Gateway 调用的 A2A 服务。
- 内部工具：被内部 Demo Agent 通过 MCP 调用的工具和数据服务。
- 管理员：维护白名单和运行健康检查的人。
- 个人 Agent 用户：拥有 AgentProfile、ModelProfile、知识空间、记忆和预算的人。
- 本地 Runner：与单一用户和设备绑定、主动出站领取任务的执行进程。

### 12.2 MVP 策略

- 平台 API 使用短期签名会话或 Bearer token；演示环境使用两个可重置的模拟账号，不在前端代码中硬编码共享管理员 token。
- 外部 Agent 凭据按 `agent_id` 单独保存，使用环境变量、Secret Manager 或本机安全配置。
- 日志、trace、事件表不得记录 Authorization header、API key、cookie 或原始密钥。
- Gateway 不把平台用户 token 直接转发给第三方 Agent。
- 每个外部 Agent 只能收到当前任务需要的最小上下文。
- 管理接口与普通任务接口分离，MVP 可以通过内网访问限制加管理员 token 实现。
- 外部 Agent 和 MCP HTTP token 必须绑定目标资源/受众，禁止 token passthrough；不同 Agent 不共享同一凭据。
- 授权判断使用任务创建时的 `policy_version` 快照；权限撤销后，新事件和 Artifact 在持久化前再次校验，缓存同步失效。
- 模型 provider 与 `base_url` 只来自管理员 allowlist；MVP 不允许用户提交任意 URL。
- 平台托管 BYOK 只保存用户拥有的加密 `secret_ref`，比赛 MVP 仅用团队受控测试凭据验证；普通用户真实 key 由本地 Runner 保存。
- 数据 scope 在模型路由前校验：`private`、`local_authenticated` 默认只走本地 Runner，逐次授权才允许发送到明确的远端 provider。
- key 撤销或轮换立即使排队、重试和未完成 Runner lease 失效；不得继续使用旧执行票据。
- provider、model、密钥归属或执行位置切换必须由用户确认，生成新执行票据和审计事件，不允许静默 fallback。

### 12.3 凭据引用示例

```json
{
  "agent_id": "course-research-demo",
  "auth_profile": {
    "type": "bearer",
    "token_ref": "secret://agents/course-research-demo/token"
  }
}
```

`token_ref` 只用于运行时解析，不允许被 API 原样返回给普通用户。

ModelProfile 只使用同类引用：

```json
{
  "model_profile_id": "model_profile_example",
  "owner_subject_id": "subject_example",
  "provider_id": "deepseek",
  "model_id": "provider-model-id",
  "execution_mode": "local_runner",
  "secret_ref": null
}
```

托管模式的 `secret_ref` 由独立 Secret Store 解析，不允许写入 Agent Card、TaskRequest、TaskEvent、Artifact 或第三方 A2A payload。

## 13. 失败、超时、取消与追问

### 13.1 失败分类

| 类型 | 示例 | 处理 |
| --- | --- | --- |
| `validation_error` | 请求缺少 `task_type` | 返回 4xx，不创建外部任务 |
| `no_route` | 无匹配 Agent | 返回可解释错误 |
| `agent_unreachable` | A2A endpoint 连接失败 | 标记 Agent 健康异常，重试一次后失败；切换 Agent 必须由用户确认并创建新任务 |
| `agent_error` | Agent 返回错误事件 | 记录错误摘要，任务置为 `failed` |
| `timeout` | 超过平台限制 | 请求取消外部任务，任务置为 `timeout` |
| `model_capability_mismatch` | Profile 模型缺少模板所需能力 | 拒绝调用并列出缺失能力 |
| `model_switch_confirmation_required` | 当前模型不可用且策略要求确认 | 保持原任务不继续，等待用户显式选择 |
| `model_budget_exceeded` | token、费用、并发或重试超限 | 停止模型调用，仍可提供符合策略的公共缓存 |
| `runner_offline` / `runner_lease_expired` | Runner 不在线、租约过期或重放 | 拒绝结果，不切到云模型 |
| `policy_denied` | 请求需要未授权数据 | 拒绝并记录审计事件 |
| `unsafe_agent_output` | Artifact 类型、大小、Schema、URL 或渲染内容不安全 | 隔离输出并失败，不交给前端或下游 Agent |

错误响应：

```json
{
  "error": {
    "code": "no_route",
    "message": "当前没有启用支持 campus_finance 的 Agent。",
    "retryable": false
  }
}
```

### 13.2 超时

- 默认首事件超时：10 秒。
- 默认总任务超时：120 秒。
- Demo 场景可按任务类型调整，但必须在配置中显式声明。
- 超时后 Gateway 向 A2A Agent 发送取消请求；如果 Agent 无响应，平台任务仍置为 `timeout`。

### 13.3 取消

用户取消时：

1. 平台将任务置为 `cancel_requested`。
2. Gateway 调用外部 Agent 的取消能力。
3. 收到确认后置为 `cancelled`。
4. SSE 发送 `task.status` 事件，`payload.status` 为 `cancelled`。

如果外部 Agent 不支持取消，平台记录 `cancel_not_supported`，并停止向前端推送后续增量。此后到达的事件只进入受限审计记录，不得恢复已取消任务、覆盖最终 Artifact 或触发下游副作用。

### 13.4 追问

当 Agent 需要补充信息时，任务进入 `input_required`。前端展示追问，用户通过 `/v1/tasks/{task_id}/messages` 追加回答。平台把回答映射回同一个 A2A task 或同一 context 下的新消息。

追问事件示例：

```json
{
  "task_id": "task_01JZEXAMPLE",
  "status": "input_required",
  "prompt": "请说明你要查询哪个校区和日期。",
  "expected_input_modes": ["text/plain"]
}
```

## 14. 可观测性

OpenTelemetry 贯穿平台 API、路由、A2A 调用、MCP 工具调用和数据库访问。

### 14.1 Trace

每个用户任务生成一个 trace：

- `http.request`: REST API 入口。
- `gateway.route`: 能力匹配和 Agent 选择。
- `a2a.send_task`: 调用外部 Agent。
- `a2a.stream_events`: 接收流式事件。
- `task.persist_event`: 写入事件表。
- `mcp.tool_call`: 仅在内部 Demo Agent 调用 MCP 工具时记录。
- `harness.resolve_profile`: 解析模板、模型与策略快照。
- `model.invoke`: 调用 allowlist provider 或创建本地 lease。
- `runner.lease`: 配对、认领、续租、完成或过期。

关键属性：

- `task.id`
- `context.id`
- `agent.id`
- `task.type`
- `route.reason`
- `error.code`
- `profile.id`
- `provider.id`
- `model.id`
- `runtime.mode`
- `cache.level`
- `runner.id`

不记录原始用户隐私数据、完整模型提示词、私人 chunk/记忆正文、点评/文件正文、受限下载 URL、密钥、secret_ref 或 Runner 执行票据。`local_authenticated`、`private` 任务只记录 ID、状态、计数、耗时和错误码；日志与事件按数据范围设置短保留期并执行抽样 DLP 扫描。

### 14.2 Metrics

- `gateway_task_created_total`
- `gateway_task_completed_total`
- `gateway_task_failed_total`
- `gateway_task_timeout_total`
- `gateway_task_cancelled_total`
- `gateway_task_duration_seconds`
- `gateway_first_event_latency_seconds`
- `gateway_agent_health`
- `gateway_route_no_match_total`
- `model_request_total`、`model_input_tokens_total`、`model_output_tokens_total`
- `model_budget_rejected_total`、`model_switch_confirmation_total`
- `harness_cache_hit_total{level}`
- `runner_lease_created_total`、`runner_lease_rejected_total`

### 14.3 Logs

日志使用结构化 JSON，至少包含 `timestamp`、`level`、`trace_id`、`task_id`、`agent_id`、`profile_id`、`provider_id`、`model_id`、`runtime_mode`、`cache_level`、`event`、`error_code`。字段可以为空；日志写入前做字段脱敏，`key_ref_hash` 和 `base_url_hash` 只在确有审计需要时保存不可逆值。

## 15. 外部 Agent 接入流程

1. 阅读接入文档，确认 Agent 能提供 A2A endpoint 和 Agent Card。
2. 实现健康检查和最小任务处理。
3. 提供测试 endpoint、Agent Card URL、能力说明和是否支持 SSE。
4. 平台管理员执行校验脚本：
   - 拉取 Agent Card。
   - 校验 JSON Schema。
   - 发送 ping 或示例 task。
   - 检查响应状态、artifact 和错误格式。
5. 管理员把 Agent 加入白名单，并配置凭据引用。
6. Gateway 定期健康检查。
7. 演示前冻结 Agent Card 快照，避免现场能力字段变化导致路由不稳定。

## 16. 最小 SDK

MVP SDK 目标不是覆盖全部 A2A 功能，而是降低白名单 Agent 接入成本。建议提供 Python 包或示例目录：

```text
gateway_sdk/
  __init__.py
  card.py
  auth.py
  client.py
  validators.py
examples/
  minimal_a2a_agent.py
  validate_agent_card.py
```

最小能力：

- 生成标准 A2A 1.0 Agent Card 模板，不重新定义 Card 字段。
- 校验 Agent Card 必填字段。
- 封装平台示例任务请求。
- 提供 FastAPI + A2A SDK 的最小 Agent 示例。
- 提供 SSE 和非流式两种处理示例。

SDK 伪代码只接受标准 Agent Card 文件；`gateway_sdk` 是校验和启动薄封装，不提供 `agent_id`、`display_name`、`capabilities=[...]` 等替代 A2A Card 的自定义模型：

```python
from gateway_sdk import load_standard_agent_card, run_minimal_agent

card = load_standard_agent_card("agent-card.json")


async def handle_task(message, context):
    return "这是一个演示回答，不包含真实密钥或敏感数据。"


run_minimal_agent(agent_card=card, handler=handle_task)
```

`agent-card.json` 必须与 5.1 节的 A2A 1.0 结构一致。实施时示例代码直接使用锁定版本官方 SDK 的类型和服务器组件，并由兼容测试验证，避免文档伪代码成为第二套协议。

## 17. 测试与验收

### 17.1 单元测试

- Agent Card Schema 校验。
- 白名单状态过滤。
- 路由规则：能力匹配、模态过滤、健康状态优先级。
- 任务状态机合法流转。
- 错误分类和用户可见错误格式。
- 凭据字段脱敏。
- Agent Card URL 的 SSRF、重定向、DNS rebinding 和 Card 变更复审。
- FileRef 伪造、跨用户复用、过期和删除后的拒绝。
- `Idempotency-Key` 重放和冲突。
- 不可信 Artifact、脚本 Markdown、超大/乱序事件和提示注入隔离。
- Profile 越权、模型能力不匹配、预算上限与禁止静默 fallback。
- provider/base URL allowlist、key 撤销/轮换和 secret DLP。
- Runner 跨用户认领、lease 重放、重复/过期提交和断线。
- `metadata` 注入 fake API key、Cookie、CSRF、任意 `base_url` 和受限下载 URL 时请求被拒绝，且持久化、日志与响应 DLP 为零命中。

### 17.2 集成测试

- FastAPI 创建任务后成功路由到内部 Demo Agent。
- 非流式 A2A task 完成并生成 artifact。
- 流式 A2A task 通过 SSE 推送 delta 和 completed。
- 前端断开 SSE 后通过轮询恢复状态。
- Agent 返回 `input_required` 后，用户补充消息并完成任务。
- 用户取消任务后，外部 Agent 收到取消请求。
- 外部 Agent 不可达时，任务失败且健康状态更新。
- 同一 AgentTemplate 使用平台测试模型和本地 Runner 模型完成同一固定任务。
- L4 公共 AnswerArtifact 命中不产生用户模型调用，主动重生成复用 L3 并写入本人 L5。

### 17.3 验收标准

- 至少两个内部 Demo Agent 可稳定完成演示任务。
- 至少一个白名单第三方 Agent 可通过 A2A 真实接入测试环境。
- OpenAPI 文档能描述核心 REST API，并包含 JSON Schema。
- SSE 与轮询两种方式都能看到同一个任务的状态变化。
- 任务、事件、artifact 能在数据库中查询。
- 日志和 trace 能通过 `task_id` 关联一次完整调用。
- 仓库、配置和日志中没有真实密钥。
- 三个独立 Agent 各执行 20 条固定脚本任务，端到端完成率不低于 90%。
- A2A 版本协商、状态映射、权限拒绝和凭据脱敏自动化测试通过率为 100%。
- SSE 断开重连测试 10 次均能通过同一 `task_id` 恢复，且不重复生成最终 Artifact。
- 外部 `campus.notice.lookup` Agent 用 10 条固定任务完成接入，Gateway 业务代码改动为 0。
- SSRF、FileRef 越权、恶意 Artifact、提示注入和任务重放安全用例 100% 通过。
- 仓库、日志、trace、事件、Artifact、数据库导出和浏览器响应的密钥 DLP 为零命中。
- 用户 A 的 ModelProfile、私人记忆和 L5 缓存对用户 B 零可见。
- Runner 重放/越权、任意 `base_url`、静默模型切换和成本滥用用例 100% 被拒绝。

## 18. MVP 与后续边界

### 18.1 MVP

- 白名单 Agent 管理。
- Agent Card 校验和健康检查。
- 单 Agent 路由。
- A2A task 创建、状态同步、结果读取、取消和追问。
- SSE 与轮询。
- 内部 Demo Agent 使用 MCP 连接工具或数据。
- PostgreSQL 持久化任务与事件。
- 结构化 JSON 日志和 correlation ID 串联调用链。
- AgentTemplate、AgentProfile、ModelProfile 和 CapabilitySnapshot 最小 Schema。
- 一个 OpenAI-compatible adapter、两个 allowlist provider 配置和独立 Usage Ledger。
- 平台/团队受控测试模型与一个只出站 CLI Runner；不收集普通用户真实托管 BYOK。
- L3 证据、L4 公共 AnswerArtifact、L5 私人生成的演示与隔离测试。

条件采用：

- Redis：仅在 PostgreSQL 事件表和单进程通知无法满足 SSE/缓存时加入；
- OpenTelemetry Collector：仅在基础调用链稳定后加入，instrumentation 接口可以预留；
- S3/MinIO：仅在部署需要跨容器文件共享时替换受控本地文件区。

### 18.2 后续

- 多 Agent 协作与父子任务。
- embedding 语义路由和历史表现学习。
- 更细粒度的数据授权策略。
- 面向真实校园系统的身份认证集成。
- Agent Card 版本管理和兼容性测试。
- 更完整的前端管理台。
- 完整 OpenTelemetry 后端、Redis 横向扩展和对象存储部署。
- 生产级真实用户 managed BYOK、多 provider 原生 adapter、多设备 Runner 和常驻个人 Agent。

不进入 MVP 的事项：

- 开放市场。
- 多租户计费。
- 复杂审批流。
- 公网未知 Agent 自助接入。
- 任意用户自定义 `base_url`、自动模型竞价/路由、自动 failover、模型费用代收和开放模板市场。

## 19. 比赛演示流程

建议演示控制在 5 到 8 分钟：

1. 展示平台首页或演示脚本，说明 Gateway 是标准兼容的校园 Agent 统筹层。
2. 打开 Agent 列表，展示两个内部 Demo Agent 和一个白名单第三方 Agent。
3. 提交选课调研任务，Router 选择 `course_research` Agent，并展示来源和缓存状态。
4. 前端通过 SSE 展示状态变化和流式回答。
5. 提交一个需要补充信息的任务，展示 `input_required` 和追问回复。
6. 提交课程复习问题，展示 `study_rag` Agent 通过 MCP 调用公共和私有知识库工具。
7. 提交一个轻量校园服务请求，展示白名单外部示例 Agent 无需修改 Gateway 即可接入。
8. 断开流式连接，改用轮询查看同一个任务结果。
9. 打开观测面板或日志片段，用 `task_id` 展示 API、路由、A2A 调用和 MCP 工具调用的 trace。
10. 用同一课程模板切换平台模型与本地 Runner，展示公共证据缓存、个人重生成、usage 和不静默 fallback。
11. 简要说明 MVP 边界：白名单真实接入、标准协议兼容、凭据隔离，不收集普通用户托管 key，不做开放市场和计费。

## 20. 关键决策

- A2A 负责独立 Agent 之间的任务互联，MCP 负责 Agent 内部工具和数据上下文。
- 平台 REST API 使用 OpenAPI 3.1 + JSON Schema 2020-12，保证前后端和测试脚本有明确契约。
- MVP 采用白名单接入和规则路由，优先保证演示稳定性和可解释性。
- 所有密钥只以引用形式出现在配置中，不进入 API 响应、日志或仓库。
- SSE 和轮询同时支持，避免演示依赖单一网络模式。
- 个人 Agent 使用 AgentTemplate + AgentProfile；模型 provider 是内部 adapter，不改变 A2A/MCP 边界。
- 比赛 MVP 采用平台/团队测试 managed 模式 + 真实 BYOK 本地 Runner，不开放任意 `base_url` 或静默 fallback。
