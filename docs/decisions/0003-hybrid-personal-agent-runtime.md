# ADR-0003：采用模板化个人 Agent 与混合模型运行时

- 状态：已接受
- 日期：2026-07-19
- 决策者：AI for better life In ustc 团队
- 最新补充：[ADR-0004：采用 Personal Main Agent 与单跳 Specialist 编排](./0004-personal-main-agent-single-hop-orchestration.md)

## 背景

平台原设计聚焦白名单独立 Agent、两个内部 Demo 和统一 Gateway。团队进一步明确了更长期的产品形态：平台不仅接入外部校园 Agent，还应提供一套共享校园 Agent Harness，让每个用户可以选择不同模型、绑定公共或私人知识、维护自己的 Personal Main Agent，并通过缓存复用大家已经完成的公开调研。

这一变化涉及个人 Agent 的身份、模型凭据、运行位置、缓存、成本和数据外发，超出 ADR-0002 原先冻结的比赛 MVP。必须在不破坏 A2A/MCP 边界、不过度扩张三人团队实现范围的前提下重新收敛。

## 候选方案

### 每个用户部署独立 Agent 服务

用户拥有完整代码、Agent Card 和公网 A2A endpoint，独立性最强。但每个用户都需要部署、注册、健康检查、SSRF 防护和升级，比赛 MVP 的运维与审核规模不可控。

### 只有平台托管的固定 Agent

实现最简单，但无法满足用户自选模型、私人知识留在本地和“每个人养一个 Agent”的产品方向。

### AgentTemplate + AgentProfile，平台托管与本地 Runner 混合执行

平台维护版本化 Harness 与模板，用户 Profile 只保存模型、知识空间、记忆、预算和执行偏好。公开任务可使用平台运行时，真实个人 BYOK 和登录/私人数据优先在只出站的本地 Runner 执行。

## 决策

采用第三种方案，并明确以下边界：

1. `AgentTemplate` 定义共享 skill、workflow、tools、Schema、prompt、policy 和 cache rule；`AgentProfile` 定义用户模型、知识空间、记忆、预算和执行偏好。
2. `ModelProfile` 支持 `platform_sponsored`、`managed_byok` 和 `local_runner`；比赛 MVP 不收集普通用户真实托管 BYOK。
3. 外部独立 Agent 仍通过 A2A 1.0 接入；MCP 仍用于 Agent 内部工具和数据；模型 provider 使用内部 adapter，不成为 A2A Agent 或 MCP Tool。
4. 本地 Runner 主动出站领取短租约，Gateway 不访问用户 `localhost`，也不把本地 Runner 注册为任意 A2A endpoint。
5. 数据 scope 在模型路由前检查。`private`、`local_authenticated` 默认使用本地 Runner；发送到明确的远端 provider 必须逐次确认。
6. 不允许静默模型 fallback。切换 provider、模型、密钥归属或本地/云执行都要求用户确认并生成新执行票据。
7. MVP 只允许 provider 和 `base_url` 白名单；任意用户自定义 URL 后置。
8. 缓存按来源、解析/索引、证据/检索、公共 AnswerArtifact 和私人生成五层治理。不同模型可以共享公共证据，不共享私人回答。
9. 平台建立独立 Usage Ledger 和预算限制，不代收用户模型费用。

ADR-0004 补充确定：`AgentProfile` 所配置的是用户唯一对话入口 Personal Main Agent；课程评价、课程资料与外部能力作为验收后的 Specialist 被 Main 单跳调用。Main Agent 的语义选择与 Gateway 的确定性控制职责不得互换。

## 对 ADR-0002 的影响

ADR-0002 的纵向闭环原则继续有效。原计划中的双运行时证明不再与主从闭环同列为硬 MVP：比赛首先证明同一 Personal Main Agent 通过 Gateway 单跳调用一个 Specialist，并正确完成上下文授权、Artifact 校验和最终综合。

为控制范围：

- 新增 Harness/Profile/Adapter/Runner 的最小契约；
- 平台托管只使用团队受控测试凭据；
- 硬 MVP 只实现一个 OpenAI-compatible adapter 和白名单 provider 配置；
- CLI Runner 仅在 Main 到 Specialist 闭环稳定且满足路线图前置条件时做最多两天的条件增强；
- 只演示 L3 证据、L4 公共答案和 L5 私人生成三类关键缓存；
- 自动路由、任意 provider、多设备、真实托管 BYOK、市场和计费全部后置。

若到 2026-07-27 仍无法完成 Profile 解析、Main 到 Specialist 单跳闭环和缓存/安全最小 spike，则先保住 Main Agent + 课程评价 Specialist 主链；Runner 继续只保留规格、mock provider 和接口级验证，不以收集真实用户 key 的方式赶进度。

## 结果

正面结果：平台定位从固定 Agent 聚合器提升为可复用校园 Agent 运行框架，用户可以按经济能力和隐私需求选择模型；公共知识劳动可以复用，同时保留个性化生成与本地数据边界。

代价：需要新增 Profile、model adapter、usage、Runner lease 和分层缓存契约，并增加密钥、成本、数据外发、缓存污染和 Runner 重放的安全测试。

## 复审条件

出现以下任一情况时必须复审：

- 比赛提供统一模型网关或禁止外部模型 API；
- 学校模型不支持当前 adapter 契约；
- 评课社区或资料来源授权限制私人/本地处理；
- 团队希望收集普通用户真实托管 BYOK；
- 希望开放任意 `base_url`、自动 fallback、模型市场或付费结算；
- 团队决定把 CLI Runner 从条件增强重新提升为硬 MVP。

## 相关文档

- [个人 Agent Harness 与混合模型运行时设计](../designs/04-personal-agent-harness.md)
- [Personal Main Agent 与 Specialist 单跳编排设计](../designs/05-personal-main-agent-orchestration.md)
- [多模型接入、BYOK 与本地 Runner 技术调研](../research/model-provider-byok.md)
- [平台威胁模型](../security/threat-model.md)
- [ADR-0002：采用纵向闭环优先的比赛 MVP](./0002-competition-mvp-scope.md)
- [ADR-0004：采用 Personal Main Agent 与单跳 Specialist 编排](./0004-personal-main-agent-single-hop-orchestration.md)
