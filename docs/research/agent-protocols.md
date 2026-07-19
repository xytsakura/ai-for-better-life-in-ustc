# Agent 协议选型研究

## 1. 结论

本项目第一版应采用组合协议，而不是单一协议：

- A2A 用于 Gateway 与独立 Agent 之间的任务委派、状态同步、流式事件、异步任务和 Agent Card 发现。
- MCP 用于单个 Agent 内部连接工具、数据源、检索服务和上下文能力，不作为跨 Agent 任务协议。
- OpenAPI 3.1 + JSON Schema 2020-12 用于定义平台 REST API，服务前端、演示脚本、管理接口和自动化测试。
- OpenTelemetry 用于统一观测 API、路由、A2A 调用、MCP 工具调用和数据库访问。

`v0.4` 的产品入口是 Personal Main Agent。Main Agent 负责语义选择，Gateway 只做确定性目录过滤、授权、父子任务和协议代理；平台到 Specialist 的跨进程调用仍使用 A2A，因此新增单跳父子关系不改变 A2A/MCP 的职责边界。

不建议自定义完整 Agent 协议，也不建议 MCP-only。自定义协议短期看似快，但会把发现、生命周期、流式、取消、追问、兼容性和文档成本都压回团队；MCP-only 会混淆“Agent 间任务协作”和“Agent 内部工具访问”两个边界。

版本基线：MVP 锁定 A2A Protocol 1.0 的 `HTTP+JSON` binding，请求统一携带 `A2A-Version: 1.0`，不接受空版本静默回退到 0.3。线级状态只使用 `TASK_STATE_UNSPECIFIED`、`TASK_STATE_SUBMITTED`、`TASK_STATE_WORKING`、`TASK_STATE_COMPLETED`、`TASK_STATE_FAILED`、`TASK_STATE_CANCELED`、`TASK_STATE_INPUT_REQUIRED`、`TASK_STATE_REJECTED`、`TASK_STATE_AUTH_REQUIRED`。Agent Card 按 1.0 的 `supportedInterfaces`、`capabilities`、`defaultInputModes`、`defaultOutputModes`、`skills` 和安全字段校验。MCP HTTP 授权语义锁定到 2025-06-18 规范，并在实施原型后锁定官方 SDK 版本。

2026-07-19 复核时，[A2A Python SDK v1.1.1](https://github.com/a2aproject/a2a-python/releases/tag/v1.1.1) 发布于 2026-07-16，[MCP Python SDK v1.28.1](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v1.28.1) 发布于 2026-06-26。两者是实施 spike 的候选版本，不代表已通过本项目兼容测试；测试通过后必须写入锁文件并记录实际 binding、事件和取消行为。

## 2. 项目约束

团队由三名本科生组成，比赛目标需要可演示、可说明、能体现技术路线，同时开发时间有限。因此协议选择要满足：

- 标准兼容：有官方规范和主来源，方便答辩解释。
- MVP 可落地：能用 Python、FastAPI 和官方 SDK 较快实现。
- 真实接入：允许白名单第三方 Agent 按协议接入，而不是只做假数据演示。
- 边界清晰：平台负责统筹和调度，Agent 自己负责工具调用和具体能力。
- 可观测：演示时能看到任务从入口到 Agent 和工具调用的完整链路。
- 不过度设计：不做开放市场、多租户计费、复杂审批流。

## 3. A2A

### 3.1 官方定位

A2A 文档把 A2A 和 MCP 明确区分为互补关系：A2A 面向 Agent 到 Agent 的通信，MCP 面向 Agent 到工具或上下文的连接。官方文档也指出，A2A 的 Agent Card 用于描述 Agent 身份、能力、端点和交互方式；任务生命周期文档描述了 task 的状态变化；流式和异步文档覆盖长任务、推送、轮询和实时更新。

主要来源：

- [A2A and MCP](https://a2a-protocol.org/latest/topics/a2a-and-mcp/)
- [A2A 1.0 Specification（TaskState、AgentCard、Versioning）](https://a2a-protocol.org/latest/specification/)
- [Agent Discovery](https://a2a-protocol.org/latest/topics/agent-discovery/)
- [Life of a Task](https://a2a-protocol.org/latest/topics/life-of-a-task/)
- [Streaming and Asynchronous Operations](https://a2a-protocol.org/latest/topics/streaming-and-async/)

### 3.2 适合本项目的能力

- Agent Card：适合白名单注册、能力发现、端点记录和演示说明。
- Task 生命周期：A2A 1.0 线级使用标准 `TASK_STATE_*` 枚举；平台 UI 可映射为 `submitted`、`working`、`input_required`、`completed`、`failed`、`cancelled` 等内部状态。
- Context：适合把同一次用户会话或相关任务串起来，并控制可转发上下文。
- Streaming：适合演示中实时展示 Agent 输出。
- Async 与 polling：适合较慢任务和非流式前端。
- 取消和追问：适合真实交互，避免所有任务都被简化成一次性问答。

### 3.3 风险

- 需要正确理解 A2A 状态机，避免平台状态和外部 Agent 状态不一致。
- 第三方 Agent 的协议实现质量可能不一，需要白名单校验和健康检查。
- 若过早设计多 Agent 协作，会超出 MVP 范围。

### 3.4 MVP 取舍

MVP 只实现严格两层的一对一委派：每个用户回合创建一个 Main Agent RootTask，最多再创建一个 `depth=1` Specialist ChildTask。Gateway 维护 parent/child lineage、取消、超时和幂等，Specialist `can_delegate=false`。多个/并行 Specialist、fan-out、`depth=2`、Specialist 相互调用和复杂 planner 暂不实现。A2A 只放在 Gateway 到跨进程/跨团队 Specialist 的边界，Agent 内部普通函数调用不为追求“协议化”而强制改成 A2A。

## 4. MCP

### 4.1 官方定位

MCP 官方架构文档将 MCP 描述为应用连接上下文、工具和数据源的协议。授权规范说明 MCP 需要明确资源服务器、客户端、授权服务器等安全边界，并强调授权流程不能被随意简化。

主要来源：

- [MCP Architecture](https://modelcontextprotocol.io/docs/learn/architecture)
- [MCP Authorization Specification, 2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization)

### 4.2 适合本项目的能力

- 内置 Specialist 通过 MCP 访问课程资料、校园问答知识库、检索服务或小工具。
- 每个 Agent 可以独立管理自己的工具列表和授权边界。
- Gateway 不需要知道每个工具的细节，只关心 Agent 返回的任务结果。
- 后续如果接真实校园系统，MCP server 可以作为工具适配层逐步扩展。

### 4.3 风险

- 如果让 Gateway 直接暴露外部 MCP server 给用户，会扩大权限和隐私风险。
- 如果把 MCP 当作 Agent 间协议，会缺少 Agent Card、任务生命周期、跨 Agent 状态同步等关键语义。
- MCP 授权不能只靠“内网可信”一笔带过，后续接真实系统时要单独设计。

### 4.4 MVP 取舍

MVP 中 MCP 只用于 Main Agent 和内置 Specialist 的工具与数据访问。外部白名单 Specialist 是否使用 MCP 是它自己的内部实现细节，Gateway 不做强制要求。

## 5. OpenAPI 3.1 + JSON Schema 2020-12

### 5.1 官方定位

OpenAPI 3.1.1 规范用于描述 HTTP API 的接口、路径、操作、请求、响应和安全要求。OpenAPI 3.1 与 JSON Schema 2020-12 对齐程度更高，适合作为 REST API 契约。JSON Schema 官方规范提供了 JSON 数据结构校验、注解和约束表达能力。

主要来源：

- [OpenAPI Specification v3.1.1](https://spec.openapis.org/oas/v3.1.1.html)
- [JSON Schema Specification](https://json-schema.org/specification)

### 5.2 适合本项目的能力

- 明确前端、演示脚本和后端之间的接口契约。
- 自动生成 FastAPI 文档，方便团队调试。
- 用 JSON Schema 校验任务创建、Agent Card 摘要、错误响应和 artifact。
- 在答辩中清楚说明平台接口是标准 REST API，不是临时脚本接口。

### 5.3 风险

- OpenAPI 只能描述平台 HTTP API，不能替代 A2A 的 Agent 任务语义。
- Schema 过细会拖慢 MVP，过松又会降低测试价值。

### 5.4 MVP 取舍

只为核心接口写清楚 Schema：Main session、CatalogSummary 搜索、CapabilityDetail 查看、ChildTask 创建/查询、SSE 事件、取消、追问、ContextGrant、SpecialistArtifact、MainAnswerArtifact 和 Agent Card 验收。管理台和复杂配置接口后续再补。

## 6. OpenTelemetry

### 6.1 官方定位

OpenTelemetry 官方文档将其定位为可观测性框架，覆盖 trace、metric 和 log，适合把跨服务调用串联起来。

主要来源：

- [OpenTelemetry Documentation](https://opentelemetry.io/docs/)

### 6.2 适合本项目的能力

- 演示一个任务从 Main Agent、Gateway 父子任务、A2A 调用、Specialist 处理、MCP 工具调用到 Main 最终综合的完整链路。
- 用 `task_id`、`agent_id`、`context_id` 关联日志、指标和 trace。
- 快速定位外部 Agent 超时、失败或返回慢的问题。

### 6.3 风险

- 如果采集原始消息、密钥或个人信息，会带来隐私风险。
- 观测系统部署过重会分散开发精力。

### 6.4 MVP 取舍

MVP 先做结构化 JSON 日志、correlation ID 和基础 instrumentation。只有核心链路稳定后才部署 OpenTelemetry Collector；指标先覆盖任务数量、成功率、失败率、超时率、首事件延迟和总耗时。

## 7. 自定义协议

### 7.1 优点

- 短期实现简单，可以完全按 Demo 需求定义字段。
- 不需要等待第三方 SDK 或规范理解。
- 对非常固定的内部 Demo 足够快。

### 7.2 缺点

- 需要自己设计 Agent 发现、能力描述、任务生命周期、流式事件、异步任务、取消、追问、错误格式和兼容性。
- 第三方 Agent 接入成本高，因为对方需要适配项目私有协议。
- 答辩时难以说明标准兼容性。
- 后续迁移到标准协议时会产生重复工作。

### 7.3 结论

不采用自定义完整 Agent 协议。可以只在平台内部保留少量私有字段，例如 `route_reason`、`demo_scene`、随机不透明的 `subject_id`，但外部 Agent 互联应走 A2A。

## 8. MCP-only 方案

### 8.1 优点

- 工具生态清晰，适合把数据库、文件、检索、浏览器等能力接给 Agent。
- 内置 Specialist 调工具会比较自然。
- 对单 Agent 应用来说架构简单。

### 8.2 缺点

- MCP 的核心边界是工具和上下文，不是独立 Agent 之间的任务委派。
- 缺少 Agent Card 式的跨 Agent 发现和能力描述语义。
- 不天然覆盖跨 Agent task 生命周期、追问、取消、异步和流式任务协调。
- Gateway 如果直接管理所有 MCP 工具，会变成工具中枢而不是 Agent 统筹层，权限边界更重。

### 8.3 结论

不采用 MCP-only。MCP 应作为 Agent 内部能力层，和 A2A 搭配使用。

## 9. 对比表

| 方案 | 适合做什么 | 不适合做什么 | MVP 采用方式 |
| --- | --- | --- | --- |
| A2A | 独立 Agent 互联、Agent Card、任务生命周期、流式和异步 | Agent 内部工具连接、数据库适配 | 作为 Gateway 到 Specialist 的主协议 |
| MCP | Agent 内部工具、数据源、上下文能力 | 跨 Agent 任务协调和发现 | Main/内置 Specialist 使用，外部 Specialist 自行决定 |
| OpenAPI 3.1 | 平台 REST API 契约、前后端协作、管理接口 | Agent 间语义协议 | 定义 Gateway API |
| JSON Schema 2020-12 | 请求、响应、配置和事件校验 | 调度策略本身 | 与 OpenAPI 3.1 一起使用 |
| OpenTelemetry | trace、metric、log、故障定位 | 业务协议或授权模型 | 做基础观测 |
| 自定义协议 | 极短期内部 Demo | 标准兼容和第三方接入 | 不采用，只保留内部扩展字段 |
| MCP-only | 单 Agent 工具型应用 | 白名单第三方 Agent 统筹层 | 不采用 |

## 10. 推荐架构

```text
user / frontend
        |
        | REST, OpenAPI 3.1
        v
Personal Main Agent
        |
        | search / describe / invoke
        v
Deterministic Gateway
        |
        | A2A, one depth=1 child
        v
accepted external / internal Specialists
        |
        | MCP, inside each agent when needed
        v
tools / data / retrieval / campus adapters
```

平台职责：

- 维护验收状态、冻结 Agent Card 快照和三级披露内容。
- 只向 Main Agent 披露 `accepted + enabled` Specialist。
- 校验 Main Agent 发起的唯一 ChildTask、ContextGrant、预算和深度。
- 维护 root/child task、context、message、artifact、event。
- 提供 SSE 和轮询。
- 记录观测数据。
- 隔离凭据。

Main Agent 职责：

- 作为用户唯一入口，判断直接回答或调用一个 Specialist；
- 基于平台摘要选择能力、编写专业 query 和最小 ContextGrant；
- 综合已验证 SpecialistArtifact，生成最终 MainAnswerArtifact。

Specialist 职责：

- 暴露 A2A endpoint。
- 声明能力和交互方式。
- 执行任务并返回消息、状态和 artifact。
- 如需工具或数据，自行通过 MCP 连接。
- 不直接答复用户，不创建子任务，不调用其他 Agent。

## 11. MVP 决策

1. 协议组合：A2A + MCP + OpenAPI 3.1 + JSON Schema 2020-12 + OpenTelemetry。
2. 接入方式：只允许白名单第三方 Agent，不做开放市场。
3. 选择方式：Main Agent 根据平台整理的 CatalogSummary/CapabilityDetail 做语义选择；Gateway 只做确定性过滤和校验。
4. 任务关系：每个 Main RootTask 最多映射一个 `depth=1` A2A ChildTask，禁止第二 child 和递归。
5. 流式能力：SSE 是优先演示路径，轮询是兼容路径。
6. MCP 边界：只在 Agent 内部使用，Gateway 不直接代理外部 MCP 工具。
7. 观测边界：记录任务链路和性能，不记录密钥、原始敏感信息或完整隐私上下文。

## 12. 后续可扩展方向

- Agent Card 版本管理和兼容性测试。
- 一个回合多个 Specialist、并行/fan-out 和多层父子任务。
- 基于 embedding 的语义路由。
- 更严格的授权策略和校园统一身份认证。
- 更完整的管理台和健康面板。
- 面向真实校园系统的 MCP server 适配器。

这些方向不阻塞 MVP。比赛第一版应优先完成 Main Agent -> 一个 Specialist -> Main Agent 的标准协议闭环、真实白名单接入和两个内置 Specialist 的稳定演示。

Main Agent 单跳职责和渐进式披露的冻结决策见 [ADR-0004](../decisions/0004-personal-main-agent-single-hop-orchestration.md)。

更完整的竞品、编排框架和校园 RAG 对比见[竞品与参考实现技术调研](./competitive-landscape.md)。其中 LangGraph 只作为课程研究 Agent 的条件运行时：两天 spike 证明 checkpoint、人工确认和测试确实更简单后才采用；它不进入跨 Agent 协议层，也不与 Gateway 维护第二套共享任务状态。
