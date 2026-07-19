# 2026-07-19 发布前技术审计

> 历史版本：本文对应 `v0.2`，保留用于追踪原始审计。当前发布判定见 [v0.3 个人 Agent Harness 集成审计](./2026-07-19-v0.3-personal-agent-audit.md)。

## 1. 审计范围

本轮将现有方案视为交给队友评审前的“出版版”，审计范围包括：

- 比赛要求、README、总架构、三份独立设计、两份技术研究、ADR 和路线图之间的一致性；
- A2A 1.0、MCP、OpenAPI/JSON Schema、任务事件和 Artifact 契约；
- 三名本科生在约七周内的实现可行性；
- 评课社区公开/登录内容、附件、缓存、版权和撤权边界；
- Agent Card、外部 Agent、文件解析、RAG 和本地 Connector 的安全风险；
- 类似平台、编排框架、Deep Research 与校园 RAG 的技术借鉴价值；
- 比赛“创新性、实用性、技术难度、完成度”四个维度的证据链。

本轮由三个独立子 Agent 分别执行规范/可行性审计、竞品技术调研、数据安全与合规审计；主 Agent 对证据进行了交叉核对、取舍和文档回写。

## 2. 审计结论

- P0 阻塞问题：0 项；
- P1 高优先级问题：9 组，已在本轮文档中修复；
- P2 中优先级问题：10 组，已修复或转化为明确实施门槛；
- P3 文档维护问题：1 组，已修复；
- 仍有 5 项外部/实施风险，不能仅靠文档关闭，见第 6 节。

协议主方向不需要推翻：A2A 负责跨进程 Agent 互操作，MCP 负责 Agent 内部工具和数据，OpenAPI/JSON Schema 负责 Gateway REST 契约。需要调整的是契约完整性、安全模型和 MVP 负载，而不是再换一套框架。

## 3. 主要发现与修复

| 严重级 | 发现 | 处理结果 | 证据文档 |
|---|---|---|---|
| P1 | 最小 SDK 伪代码使用自定义旧字段，与 A2A 1.0 Agent Card 不一致 | 改为加载标准 Agent Card 的薄封装，明确官方 SDK 类型是唯一实现基线 | [Gateway 设计](../designs/01-agent-gateway.md) |
| P1 | SSE、轮询和 Artifact 示例使用不同结构，`TaskEvent` 无 Schema | SSE 数据统一为 `TaskEvent`，轮询统一返回 `ArtifactEnvelope[]`，补事件枚举、序号和载荷规则 | [Gateway 设计](../designs/01-agent-gateway.md) |
| P1 | 外部 Agent 没有固定 skill、Schema 和验收脚本 | 固定 `campus.notice.lookup`、公开样例数据和 10 条任务，要求 Gateway 业务代码零改动 | [Gateway 设计](../designs/01-agent-gateway.md) |
| P1 | `study.ingest` 对 FileRef 的位置在表格和示例中矛盾 | 统一为 `TaskRequest.inputs`，`data` 只放课程、空间和来源策略 | [Gateway 设计](../designs/01-agent-gateway.md) |
| P1 | 受限附件可能进入通用 RAG 的对象、哈希和向量链路 | 未获明确入库授权时彻底排除服务器文件区、哈希、向量、Artifact 和共享缓存 | [RAG 设计](../designs/03-study-rag-agent.md)、[评课社区调研](../research/icourse-integration.md) |
| P1 | 点评、网页和文件的间接提示注入未建模 | 定义“不可信证据”通道，内容不得改变系统指令、工具、URL 或权限，增加恶意样例验收 | [威胁模型](../security/threat-model.md) |
| P1 | PDF/PPTX/OCR 解析仅写病毒扫描，缺少沙箱与资源控制 | 增加隔离区、真实 MIME、页数/压缩比、无网络沙箱、主动内容禁用、CPU/内存/超时控制 | [RAG 设计](../designs/03-study-rag-agent.md) |
| P1 | 取消流程使用的 `task.cancelled` 不在 `TaskEvent` 枚举 | 统一使用 `task.status` 和 `payload.status=cancelled` | [Gateway 设计](../designs/01-agent-gateway.md) |
| P1 | 竞品表把无直接来源的框架/企业产品概括写成一手事实 | 删除证据不足条目，收窄 Agentforce 表述，规范版本、binding 和 SDK 包版本分开引用 | [竞品调研](../research/competitive-landscape.md) |
| P2 | `FileRef.owner_scope` 可能被客户端当作授权声明 | 明确 FileRef 是不透明引用，所有权和 MIME 由 Gateway 重新解析，伪造/跨用户/过期引用必须拒绝 | [Gateway 设计](../designs/01-agent-gateway.md) |
| P2 | Agent Card URL 和外部 Artifact 仍可形成 SSRF/XSS/资源消耗面 | 增加私网/重定向/DNS rebinding 阻断、Schema/MIME/大小校验和安全渲染 | [威胁模型](../security/threat-model.md) |
| P2 | 登录 Connector 只有“Cookie 不离开本地”原则，没有可执行边界 | 增加 origin pinning、只读 DOM、逐次确认、字段/URL 剥离、本地日志和公开导出重采集 | [评课社区调研](../research/icourse-integration.md) |
| P2 | 缓存没有无授权 TTL、撤回和隐藏同步 | 无授权时搜索 5-15 分钟、结构化摘要最长 24 小时；不存原始 HTML/长正文；删除/隐藏立即 tombstone | [课程研究设计](../designs/02-course-research-agent.md) |
| P2 | 三人七周的基础设施栈过满 | PostgreSQL、受控文件区和结构化日志设为基线；Redis、MinIO、OTel Collector、LangGraph、登录 Connector 条件采用 | [ADR-0002](../decisions/0002-competition-mvp-scope.md) |
| P2 | 指标有阈值但缺少冻结数据、版本和评审人 | 路线图前置课程/RAG 评测夹具，记录来源、许可、人工答案、模型/提示/embedding 版本和评审人 | [路线图](../roadmap.md) |
| P2 | context/task 可能绕过 Artifact 保留策略保存私有正文 | 增加 `data_scope`、`retention_until`，私有输入只存脱敏 envelope、计数和引用 | [Gateway 设计](../designs/01-agent-gateway.md) |
| P2 | 取消和追问重试可能重复改变外部任务 | 所有状态变更 POST 使用幂等键，并增加 `cancel_request_id` 与 `message_id` | [Gateway 设计](../designs/01-agent-gateway.md) |
| P2 | 外部 Agent 只写“10 条任务”，语义不可复现 | 冻结 10 类输入、预期状态/Artifact 和未来 fixture 路径 | [Gateway 设计](../designs/01-agent-gateway.md) |
| P2 | 私有文件、任务和日志保留期仍不可测试 | 增加演示环境默认 TTL、15 分钟清理频率和删除回执字段 | [威胁模型](../security/threat-model.md) |
| P3 | 历史会议待办与当前路线图可能被误读 | 会议记录增加“后续以 roadmap/ADR 为准”提示 | [会议记录](../meetings/2026-07-19-project-direction.md) |

## 4. 竞品审计后的定位调整

调研确认 Dify、Coze、Copilot Studio、Agentforce 等已经覆盖 Agent、工具、工作流、知识库、权限或市场中的大部分通用能力；多个校园开源项目也已经能完成带引用的普通 RAG。

因此，以下说法不足以作为创新：

- “我们有一个统一聊天入口”；
- “我们能调用多个 Agent”；
- “我们用了 RAG、工作流或多智能体”；
- “我们能缓存课程摘要”。

审计后保留的核心命题是：

> 面向校园场景，建立一个标准兼容、权限和证据可随任务流动、能够真实接入独立 Agent 的治理与调度层；两个课程 Agent 和一个外部通知 Agent共同证明其通用性。

完整竞品证据、技术选型和 Build vs Borrow 见[竞品与参考实现技术调研](../research/competitive-landscape.md)。

## 5. 子 Agent 建议的取舍

已接受：

- 保留 A2A/MCP/OpenAPI/OpenTelemetry 的职责分层；
- 借鉴 Open Deep Research 的问题拆解、证据压缩和评测方法；
- 借鉴企业平台的权限、知识源治理和测试中心思想；
- 用固定外部 Agent、固定评测和安全测试证明上游平台价值。

条件接受：

- LangGraph 只用于课程研究 Agent 的两天 spike，不成为 Gateway 硬依赖；
- Redis、S3/MinIO 和 OpenTelemetry Collector 只有出现明确需求后加入；
- 登录增强 Connector 只有在维护者授权、公开模式稳定且安全测试通过后演示。

未接受：

- 没有采用“把完整 Dify/Coze 等平台作为底座”的路径，因为会引入过重控制面并削弱协议创新证明；
- 没有同时使用多套编排框架和状态模型；
- 没有把竞品宣传或开源项目 README 自报指标当作经过独立验证的效果证据；
- 没有因 Codex 额度充足而扩大 MVP，团队集成、调试、演示和答辩时间仍是硬约束。

## 6. 尚未关闭的风险

| 风险 | 为什么文档不能关闭 | 下一步门槛 |
|---|---|---|
| 评课社区授权 | 公开可访问、robots 无明确禁令都不等于允许 AI 摘要、embedding 或附件处理 | 2026-07-21 前联系维护者，2026-07-27 无明确回复则执行公开短缓存降级 |
| A2A/MCP SDK 实际兼容 | 规范和 release 存在不代表组合实现一定稳定 | 两天 spike 跑通 Card、task、SSE/轮询、追问和取消，再锁版本 |
| 比赛提交格式和资源 | 官方后续通知可能改变部署与材料要求 | 每周核对组委会通知并记录 |
| 演示数据许可 | 课程资料、真题和附件的访问权不自动包含再处理/展示权 | 冻结 1-2 门明确许可、自制或书面授权资料及许可记录 |
| 模型与成本/延迟 | 比赛模型资源和 SDK 尚未在真实链路评测 | 固定主模型和降级模型，记录版本、成本、P95 和失败率 |

## 7. 发布判定

当前文档已经达到“可以交给队友进行技术评审和分工讨论”的状态，但不等于代码方案已经经过原型验证。技术范围已由 ADR 和路线图冻结，团队审阅时只需完成以下分工与执行确认：

1. 确认 `campus.notice.lookup` 第一周外部接入验收的负责人；
2. 确认登录增强退出默认 MVP，并指定后续授权跟进人；
3. 确认 Redis/MinIO/OTel/LangGraph 只有满足 ADR 条件才可加入；
4. 谁负责评课社区授权沟通和合规样例；
5. 谁负责两天 A2A/MCP spike 和最终 SDK 锁定。

团队确认后再编写实施计划和代码脚手架。
