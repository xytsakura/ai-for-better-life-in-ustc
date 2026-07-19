# ADR-0004：采用 Personal Main Agent 与单跳 Specialist 编排

- 状态：已接受
- 日期：2026-07-19
- 决策者：AI for better life In ustc 团队
- 补充决策：[ADR-0003：采用模板化个人 Agent 与混合模型运行时](./0003-hybrid-personal-agent-runtime.md)

## 背景

`v0.3` 已经定义 Agent Registry、A2A、Gateway、AgentProfile、ModelProfile、Harness、知识空间、分层缓存和本地 Runner，但主要交互仍是“用户请求进入 Gateway，由 Gateway 选择一个 Agent”。这使个人 Agent 更像配置容器，而不是用户长期交互的主 Agent。

团队进一步确认产品形态：用户无论处理通用问题还是校园专业问题，都只与自己的 Personal Main Agent 交互。平台把已经验收的专业 Agent 以类似 skill catalog 的摘要渐进披露给 Main Agent；Main Agent 发现合适能力后，编写专业 query 和最小上下文授权，通过 Gateway 调用一个 Specialist Sub Agent。Specialist 完成领域工作后返回结构化结果，由 Main Agent 最终提炼并答复用户。

## 候选方案

### 保持 Gateway 主动路由

实现风险最低，但 Main Agent 仍不是一等交互主体，无法体现“每个人养一个 Agent”的核心产品价值。

### Main Agent 直接调用 Specialist endpoint

最接近自由多 Agent，但会绕过注册、权限、预算、任务状态、取消、Artifact 校验和审计，不适合作为比赛基线。

### Main Agent 语义决策 + Gateway 确定性控制

Main Agent 负责理解、选择、写 query 和最终综合；Gateway 负责白名单披露、授权、父子任务、A2A、预算、限深和审计。产品体验与安全治理职责清晰。

## 决策

采用第三种方案，并冻结以下约束：

1. Personal Main Agent 是普通用户唯一对话入口；Specialist 不直接向用户产生最终答复。
2. Main Agent 负责语义判断，但不能直接访问 A2A endpoint。所有 Specialist 调用都经 Gateway。
3. Gateway 只向 Main Agent 披露 `accepted + enabled` 的 Specialist，摘要来自冻结并经过平台整理的 Agent Card 快照。
4. 能力采用三级披露：CatalogSummary、CapabilityDetail、管理员 GovernanceDetail。完整 endpoint、凭据和内部实现不进入 Main Agent 上下文。
5. 比赛 MVP 每个用户回合最多创建一个 child task，`depth=1`；Specialist 的 token audience 不包含 `create_child_task`，禁止递归、自调用、并行和 fan-out。
6. Main Agent 只提出所需字段、引用和用途；Gateway 生成绑定 child、Specialist/version/skill、attempt、egress 与 TTL 的 `ContextGrant` 和短期 `AuthorizedContextBundle`。完整 Profile、ModelProfile、API key、Cookie、私人记忆正文和完整会话不传给第三方 Specialist。
7. 平台内置 Specialist 复用共享 Harness Core，但可以拥有独特的领域 pipeline 配置、工具、知识库和缓存 namespace；第三方 Specialist 可使用自己的内部运行时，但必须遵守 A2A 和 Artifact 契约。
8. Specialist 只返回 `SpecialistArtifact`；Gateway 做 Schema、权限、引用、大小、DLP 和安全渲染校验后，Main Agent 才能读取。
9. 最终回答由 Main Agent 生成 `MainAnswerArtifact`，必须保留 Specialist 来源、引用、data scope、缓存和限制信息。
10. Specialist 不能直接写入个人长期记忆。它只能提出候选信息，由 Main Agent 和记忆策略决定是否写回。
11. 不允许静默切换 Specialist、模型、provider、key 归属或本地/云执行位置；新选择必须得到用户确认并创建新任务或执行票据。
12. Main Agent 使用用户的 ModelProfile。第三方 Specialist 可以使用自己的模型，平台不得把用户模型 key 透传给它。

## 对现有范围的影响

以下能力提升为比赛硬 MVP：

- Main Agent 唯一入口；
- Specialist Catalog 渐进式披露；
- Main task 到一个 Specialist child task 的单跳闭环；
- ContextGrant、RecursionGuard、SpecialistArtifact 和 MainAnswerArtifact；
- 两个内置 Specialist 与一个极简外部 Specialist 的统一调用证明。

为控制三人团队范围，以下能力改为条件增强或后置：

- 完整 CLI 本地 Runner：保留契约，只有主从闭环稳定后才进入主演示；
- 一个回合调用多个 Specialist、并行、递归和子 Agent 相互调用；
- 开放注册市场、自助审核 UI、复杂语义目录搜索；
- 自动长期记忆学习、多设备 Runner、生产级 managed BYOK；
- 自动 Agent/model fallback、竞价路由和计费结算。

课程评价和课程资料 RAG 仍是两个业务 Demo；`campus.notice.lookup` 只证明第三方接入，不成为第三个完整产品。

## 结果

正面结果：产品入口统一，个人 Agent 成为真正的长期主 Agent；专业能力可以像 skill 一样渐进发现，同时保留独立 Agent 的协议和部署边界；父子关系、权限、引用和成本可以被演示和审计。

代价：需要新增 CatalogSummary、CapabilityDetail、ChildTask、ContextGrant、任务 lineage 和最终综合契约；原先 Gateway 主动路由的文档和演示流程必须更新。

## 停止条件

- 第一个内置 Specialist 未稳定前，不接第二个 Specialist；
- one-hop mock 闭环未通过权限、取消、超时、重放和递归拒绝测试前，不接真实私人数据；
- 2026-07-27 前主从闭环未稳定时，Runner 保持条件增强，优先保住 Main Agent + 课程评价 Specialist；
- 任何实现若要求把完整个人上下文、API key 或 Cookie 传给 Specialist，立即停止并重新设计；
- 任何实现若让 Gateway 自由推理或让 Specialist 继续委派，立即回退到确定性控制面和单跳约束。

## 复审条件

- 希望每回合调用多个 Specialist；
- 希望允许 Specialist 调用其他 Agent；
- 希望开放未知第三方自助注册；
- 希望第三方 Specialist 访问私人或登录限定数据；
- 希望 Main Agent 自动写长期记忆或自动切换 provider；
- 比赛规则、模型资源或第三方数据授权发生变化。

## 相关文档

- [Personal Main Agent 与 Specialist 单跳编排设计](../designs/05-personal-main-agent-orchestration.md)
- [Agent Gateway 设计](../designs/01-agent-gateway.md)
- [个人 Agent Harness 与混合模型运行时设计](../designs/04-personal-agent-harness.md)
- [平台威胁模型](../security/threat-model.md)
