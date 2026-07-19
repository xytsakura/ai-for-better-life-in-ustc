# Agent Gateway 协议调度层设计

## 1. 背景

本项目面向中国科大“一〇七”杯智能体赛道，第一版定位为一个标准兼容的校园 Agent 统筹层：平台不试图替代所有 Agent，而是负责接入、发现、路由、任务状态管理、权限隔离、观测和演示编排。

MVP 允许白名单第三方 Agent 按协议真实接入，同时保留两个内部 Demo Agent 用于稳定演示：

- 课程评价 Deep Research Agent：聚合课程信息、点评和允许访问的附件元数据，生成带来源的选课调研报告。
- 课程资料与复习 Agent：围绕课程公共资料和用户私有资料提供带引用的检索与复习辅助。

推荐技术栈：

- 后端：Python、FastAPI。
- Agent 互联：官方 A2A Python SDK。
- 工具与数据上下文：官方 MCP Python SDK。
- 数据库：PostgreSQL + pgvector。
- 缓存与异步协调：Redis。
- 观测：OpenTelemetry。
- 平台 REST 接口：OpenAPI 3.1 + JSON Schema 2020-12。

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

## 3. 非目标

- 不做开放 Agent 市场、公开上架、评分排行或收益分成。
- 不做多租户计费、复杂组织管理和合同级权限模型。
- 不做复杂审批流；MVP 只支持仓库配置或管理员手工录入白名单。
- 不承诺兼容任意未知 Agent，只支持按文档完成 Agent Card 和 A2A 端点的白名单 Agent。
- 不在平台内保存第三方 Agent 的真实 API key 示例或用户个人敏感数据。
- 不强制第三方 Agent 暴露内部工具；工具接入由各 Agent 自己通过 MCP 管理。

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
  - event stream
  - credential broker
  - observability middleware
        |
        +---- A2A client ---- external agent A
        +---- A2A client ---- external agent B
        +---- A2A client ---- internal demo agents

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
- Observability：通过 OpenTelemetry 记录 trace、span、metric、log，并关联 `task_id`、`agent_id`、`context_id`。

## 5. Agent Card 与白名单注册

### 5.1 标准 Agent Card 与平台注册记录

A2A Agent 在 `/.well-known/agent-card.json` 发布标准 Agent Card，其中包含 Agent 名称、说明、版本、接口、能力、安全声明、输入输出模式和 skills。平台不修改该标准结构，而是在 Registry 中保存经过校验的 Agent Card 快照和平台治理字段。

以下是平台注册记录的关键字段，不是对 A2A Agent Card 的重新定义：

- `agent_id`：平台内唯一标识，例如 `ustc-campus-qa`.
- `display_name`：展示名。
- `owner`：负责团队或联系人，不写个人敏感信息。
- `a2a_endpoint`：A2A 服务入口。
- `card_url`：Agent Card 发现地址，可选。
- `capabilities`：能力列表，例如 `course_research`、`study_rag`、`campus_service`。
- `input_modes`：支持输入，例如 `text/plain`、`application/json`.
- `output_modes`：支持输出，例如 `text/plain`、`application/json`.
- `supports_streaming`：是否支持 A2A 流式事件。
- `supports_async`：是否支持异步任务。
- `auth_profile_ref`：凭据引用，不是密钥明文。
- `status`：`draft`、`enabled`、`disabled`、`blocked`.

### 5.2 白名单注册流程

1. 第三方团队提交 Agent Card URL、A2A endpoint、能力说明和测试账号需求。
2. 管理员用注册校验脚本拉取 Agent Card，验证 JSON 结构、端点可达性、认证方式和能力字段。
3. 平台创建 `agent_id`，写入白名单配置或数据库。
4. 凭据只写入本地密钥管理环境或部署平台 Secret，不进入 Git 仓库。
5. 平台执行健康检查和最小任务握手。
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
  "capabilities": [
    {
      "name": "course_research",
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
- 主动探测：定期拉取 `card_url` 并检查版本、能力和健康状态。
- 运行反馈：任务成功率、超时率、取消率、平均首 token 时间、平均完成时间。

MVP 不做复杂语义市场匹配，优先使用可解释的规则路由。

### 6.2 路由输入

```json
{
  "task_type": "course_research",
  "user_message": "请调研人工智能与机器学习基础的工作量和学习收获。",
  "required_input_mode": "text/plain",
  "preferred_output_mode": "text/plain",
  "context_id": "ctx_01JZEXAMPLE0000000000000",
  "allow_streaming": true
}
```

### 6.3 路由规则

1. 过滤 `status != enabled` 的 Agent。
2. 过滤不支持输入或输出模态的 Agent。
3. 根据 `task_type` 和 `capabilities.name` 精确匹配。
4. 若多个 Agent 匹配，优先选择健康状态正常、最近失败率低、支持流式返回的 Agent。
5. 若仍有多个候选，MVP 采用配置优先级；后续再加入 embedding 召回或学习型策略。
6. 如果没有候选，返回可解释错误，并建议用户换一种任务类型或启用内部 Demo。

### 6.4 路由结果

```json
{
  "selected_agent_id": "course-research-demo",
  "reason": "matched capability course_research and text/plain input",
  "streaming": true,
  "fallback_agent_ids": []
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

平台内部状态采用下划线命名，并映射到 A2A 的标准状态。A2A `input-required`、`auth-required` 和 `canceled` 在平台内分别表示为 `input_required`、`auth_required` 和 `cancelled`；平台额外保留路由和取消请求的中间状态。

| 平台状态 | 含义 | 典型来源 |
| --- | --- | --- |
| `submitted` | 平台已收到请求，尚未发送给 Agent | REST API |
| `routed` | 已选定 Agent | Router |
| `working` | Agent 已接收并处理中 | A2A task event |
| `input_required` | Agent 需要用户追问补充 | A2A task state |
| `auth_required` | Agent 需要用户完成授权 | A2A task state |
| `completed` | Agent 完成并返回最终结果 | A2A artifact / final event |
| `failed` | Agent 或平台处理失败 | A2A error / gateway error |
| `rejected` | Agent 拒绝当前任务 | A2A task state |
| `cancel_requested` | 用户请求取消，等待 Agent 确认 | REST API |
| `cancelled` | 任务已取消 | A2A `canceled` result |
| `timeout` | 超过平台或 Agent 时间限制 | gateway timer |

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

平台存储 artifact 摘要和安全引用；大文件后续再接对象存储。

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
event: task.status
data: {"task_id":"task_01JZEXAMPLE","status":"working","agent_id":"campus-qa-demo"}

event: task.delta
data: {"task_id":"task_01JZEXAMPLE","delta":"可以通过图书馆官网的通知栏目查询"}

event: task.completed
data: {"task_id":"task_01JZEXAMPLE","artifact_id":"art_01JZEXAMPLE"}
```

SSE 连接断开时，前端可以带 `Last-Event-ID` 重连。平台需要把事件追加写入 Redis Stream 或数据库事件表，至少保留到任务完成后一段时间。

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
  "artifact": {
    "artifact_id": "art_01JZEXAMPLE",
    "content_type": "text/markdown",
    "content": "可以通过图书馆官网通知栏目或公众号查询暑假开放安排。"
  }
}
```

## 9. MCP 边界

MCP 用于 Agent 内部连接工具、数据库、文件、向量检索、浏览器或校园系统适配器。平台边界如下：

- Gateway 与独立 Agent 之间使用 A2A，不使用 MCP 传递 Agent 任务。
- 内部 Demo Agent 可以作为 MCP client 调用内部 MCP server。
- 外部第三方 Agent 的 MCP server 不直接暴露给平台用户。
- 平台不透传用户 token 给 MCP server；由 Agent 根据自己的授权模型访问工具。
- MCP 工具返回结果必须经过 Agent 汇总和脱敏后再作为 A2A artifact 返回。

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

contexts
  id
  user_id_hash
  title
  summary
  policy_json
  created_at
  updated_at

tasks
  id
  context_id
  user_id_hash
  selected_agent_id
  external_task_id
  task_type
  status
  input_json
  route_reason
  timeout_at
  created_at
  updated_at

task_events
  id
  task_id
  seq
  event_type
  payload_json
  created_at

artifacts
  id
  task_id
  content_type
  content_json
  content_text
  created_at
```

`user_id_hash` 使用稳定哈希或演示账号标识，不在 MVP 中保存真实学号、手机号、邮箱等敏感信息。

### 10.2 JSON Schema 草案

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://example.edu/schemas/task-create.json",
  "type": "object",
  "required": ["task_type", "message"],
  "properties": {
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
      "additionalProperties": true
    }
  },
  "additionalProperties": false
}
```

## 11. REST API 草案

OpenAPI 3.1 文档由 FastAPI 自动生成后人工补充描述。核心接口：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/v1/tasks` | 创建任务并触发路由 |
| `GET` | `/v1/tasks/{task_id}` | 查询任务当前状态和最终结果 |
| `GET` | `/v1/tasks/{task_id}/events` | 订阅 SSE 事件 |
| `POST` | `/v1/tasks/{task_id}/cancel` | 请求取消任务 |
| `POST` | `/v1/tasks/{task_id}/messages` | 回复 Agent 的追问 |
| `GET` | `/v1/agents` | 查看可用白名单 Agent |
| `POST` | `/v1/admin/agents/validate` | 校验 Agent Card 草稿 |
| `POST` | `/v1/admin/agents` | 管理员启用白名单 Agent |
| `GET` | `/healthz` | 平台健康检查 |

创建任务请求：

```http
POST /v1/tasks
Content-Type: application/json
Authorization: Bearer <platform-user-token>
```

```json
{
  "task_type": "course_research",
  "message": "请比较这门课近两年的工作量、给分和学习收获。",
  "stream": true,
  "metadata": {
    "demo_scene": "course-selection"
  }
}
```

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
  "reason": "user_cancelled_from_demo_ui"
}
```

追问回复：

```json
{
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

### 12.2 MVP 策略

- 平台 API 使用短期 Bearer token 或演示环境固定登录态；生产化前再接统一身份认证。
- 外部 Agent 凭据按 `agent_id` 单独保存，使用环境变量、Secret Manager 或本机安全配置。
- 日志、trace、事件表不得记录 Authorization header、API key、cookie 或原始密钥。
- Gateway 不把平台用户 token 直接转发给第三方 Agent。
- 每个外部 Agent 只能收到当前任务需要的最小上下文。
- 管理接口与普通任务接口分离，MVP 可以通过内网访问限制加管理员 token 实现。

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

## 13. 失败、超时、取消与追问

### 13.1 失败分类

| 类型 | 示例 | 处理 |
| --- | --- | --- |
| `validation_error` | 请求缺少 `task_type` | 返回 4xx，不创建外部任务 |
| `no_route` | 无匹配 Agent | 返回可解释错误 |
| `agent_unreachable` | A2A endpoint 连接失败 | 标记 Agent 健康异常，可尝试 fallback |
| `agent_error` | Agent 返回错误事件 | 记录错误摘要，任务置为 `failed` |
| `timeout` | 超过平台限制 | 请求取消外部任务，任务置为 `timeout` |
| `policy_denied` | 请求需要未授权数据 | 拒绝并记录审计事件 |

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
4. SSE 发送 `task.cancelled` 事件。

如果外部 Agent 不支持取消，平台记录 `cancel_not_supported`，并停止向前端推送后续增量。

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

关键属性：

- `task.id`
- `context.id`
- `agent.id`
- `task.type`
- `route.reason`
- `error.code`

不记录原始用户隐私数据和密钥。

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

### 14.3 Logs

日志使用结构化 JSON，至少包含 `timestamp`、`level`、`trace_id`、`task_id`、`agent_id`、`event`、`error_code`。日志写入前做字段脱敏。

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

- 生成 Agent Card 模板。
- 校验 Agent Card 必填字段。
- 封装平台示例任务请求。
- 提供 FastAPI + A2A SDK 的最小 Agent 示例。
- 提供 SSE 和非流式两种处理示例。

SDK 伪代码：

```python
from gateway_sdk import AgentCard, run_minimal_agent

card = AgentCard(
    agent_id="campus-service-example",
    display_name="Campus Service Example Agent",
    capabilities=["campus_service"],
    supports_streaming=True,
)


async def handle_task(message, context):
    return "这是一个演示回答，不包含真实密钥或敏感数据。"


run_minimal_agent(card=card, handler=handle_task)
```

## 17. 测试与验收

### 17.1 单元测试

- Agent Card Schema 校验。
- 白名单状态过滤。
- 路由规则：能力匹配、模态过滤、健康状态优先级。
- 任务状态机合法流转。
- 错误分类和用户可见错误格式。
- 凭据字段脱敏。

### 17.2 集成测试

- FastAPI 创建任务后成功路由到内部 Demo Agent。
- 非流式 A2A task 完成并生成 artifact。
- 流式 A2A task 通过 SSE 推送 delta 和 completed。
- 前端断开 SSE 后通过轮询恢复状态。
- Agent 返回 `input_required` 后，用户补充消息并完成任务。
- 用户取消任务后，外部 Agent 收到取消请求。
- 外部 Agent 不可达时，任务失败且健康状态更新。

### 17.3 验收标准

- 至少两个内部 Demo Agent 可稳定完成演示任务。
- 至少一个白名单第三方 Agent 可通过 A2A 真实接入测试环境。
- OpenAPI 文档能描述核心 REST API，并包含 JSON Schema。
- SSE 与轮询两种方式都能看到同一个任务的状态变化。
- 任务、事件、artifact 能在数据库中查询。
- 日志和 trace 能通过 `task_id` 关联一次完整调用。
- 仓库、配置和日志中没有真实密钥。

## 18. MVP 与后续边界

### 18.1 MVP

- 白名单 Agent 管理。
- Agent Card 校验和健康检查。
- 单 Agent 路由。
- A2A task 创建、状态同步、结果读取、取消和追问。
- SSE 与轮询。
- 内部 Demo Agent 使用 MCP 连接工具或数据。
- PostgreSQL 持久化任务与事件。
- Redis 支撑 SSE 事件分发和短期状态缓存。
- OpenTelemetry 基础 trace、metric、log。

### 18.2 后续

- 多 Agent 协作与父子任务。
- embedding 语义路由和历史表现学习。
- 更细粒度的数据授权策略。
- 面向真实校园系统的身份认证集成。
- Agent Card 版本管理和兼容性测试。
- 更完整的前端管理台。

不进入 MVP 的事项：

- 开放市场。
- 多租户计费。
- 复杂审批流。
- 公网未知 Agent 自助接入。

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
10. 简要说明 MVP 边界：白名单真实接入、标准协议兼容、凭据隔离，不做开放市场和计费。

## 20. 关键决策

- A2A 负责独立 Agent 之间的任务互联，MCP 负责 Agent 内部工具和数据上下文。
- 平台 REST API 使用 OpenAPI 3.1 + JSON Schema 2020-12，保证前后端和测试脚本有明确契约。
- MVP 采用白名单接入和规则路由，优先保证演示稳定性和可解释性。
- 所有密钥只以引用形式出现在配置中，不进入 API 响应、日志或仓库。
- SSE 和轮询同时支持，避免演示依赖单一网络模式。
