# 校园 Agent 集成平台设计规格

## 1. 状态

- 设计日期：2026-07-19
- 版本：0.4（Personal Main Agent 单跳编排版）
- 状态：已获队长批准，v0.4 三路独立审计与修复复审通过
- 项目：AI for better life In ustc
- 赛事：一〇七杯，本科生组，智能体赛道

## 2. 背景与问题

团队希望建设校园 Agent 的统筹层，而不是只实现一个校园问答功能。每位用户只与自己的 Personal Main Agent 交互；平台把已验收的专业能力像 skill 一样渐进披露，Main Agent 在需要时通过确定性 Gateway 单跳调用一个 Specialist，再综合专业 Artifact 完成最终回答。平台同时提供共享 Harness，让用户按经济能力与隐私需求选择模型、知识空间和执行位置。比赛版本仍只有课程评价 Deep Research 和课程资料复习两个业务 Demo；外部通知 Specialist 只证明标准接入，不作为第三个完整业务 Demo。

## 3. 交付范围

本轮仅交付可供团队评审和分工的设计文档，不创建业务代码脚手架：

1. 总体架构说明；
2. 协议调度层独立设计；
3. 课程评价 Deep Research Specialist 独立设计；
4. 课程资料与复习 Specialist 独立设计；
5. Agent 协议调研；
6. 评课社区公开/登录能力、认证和内容边界调研；
7. 会议记录与项目路线图；
8. 个人 Agent Harness 与混合模型运行时独立设计；
9. 多模型、BYOK、本地 Runner 技术调研和 ADR-0003；
10. 全局 README、威胁模型、审计和路线图同步更新。
11. Personal Main Agent、渐进式 Specialist Catalog 与单跳编排独立设计、ADR-0004 和正式规格。

## 4. 核心架构决策

- 第一版支持白名单第三方 Agent 真实接入；
- A2A Protocol 1.0 `HTTP+JSON` binding 用于独立 Agent 间通信，请求必须携带 `A2A-Version: 1.0`；
- A2A wire 状态只使用标准 `TASK_STATE_*` 枚举，平台内部状态通过 Adapter 显式映射；
- MCP 2025-06-18 授权语义用于 Agent 调用工具和数据源；
- OpenAPI 3.1 与 JSON Schema 2020-12 用于平台接口；
- PostgreSQL/pgvector 提供元数据和向量检索；
- 设计兼容 Redis 缓存/事件协调，但 MVP 先使用 PostgreSQL 事件表和进程内通知，出现明确瓶颈后再引入；
- 设计兼容 OpenTelemetry，MVP 先交付结构化日志和 correlation ID，核心链路稳定后再部署 Collector；
- Gateway 不承载具体课程业务逻辑；
- Personal Main Agent 是普通用户唯一入口和最终回答者，负责语义判断、专业 query 和最终综合；
- Gateway 是确定性控制面，不使用模型决定意图、选择 Specialist 或改写最终答案；
- 平台只披露 `accepted + enabled` Specialist 的 `CatalogSummary`，按需加载 `CapabilityDetail`，完整治理信息仅管理员可见；
- 每个用户回合最多创建一个 `depth=1` ChildTask，Specialist `can_delegate=false`，禁止递归、fan-out、并行和 Specialist 间调用；
- Personal Main Agent 通过字段级 `ContextGrant` 申请最小个人上下文，Gateway 生成短期 `AuthorizedContextBundle`；
- Specialist 只返回 `SpecialistArtifact`，Gateway 校验后由 Main Agent 生成 `MainAnswerArtifact`；
- 登录凭据由 Connector 管理，不跨 Agent 透传。
- Gateway 使用版本化 `TaskRequest`、`TaskEvent`、`ArtifactEnvelope` 和 `FileRef` 连接平台组件；文件先进入受控文件区，A2A 任务只传安全引用，部署需要时再升级为 S3/MinIO。
- 比赛 MVP 采用纵向闭环优先：PostgreSQL/pgvector、受控本地文件区和结构化日志是基线；Redis、S3/MinIO、完整 OpenTelemetry、LangGraph 和登录增强 Connector 均为条件采用。
- 外部示例 Specialist 固定实现 `campus.notice.lookup`，用独立进程、标准 Agent Card 和固定任务证明 Gateway 无业务代码改动接入。
- Personal Main Agent 采用 `AgentTemplate + AgentProfile + ModelProfile`，不为每个用户部署公网 A2A endpoint；
- 模型 provider 是 Harness 内部 adapter，A2A 与 MCP 边界不变；
- 硬 MVP 使用平台/团队托管测试模型；只出站本地 Runner 保留契约并作为条件增强，普通用户真实 BYOK 不进入比赛版服务器；
- 数据 scope 先于模型路由，禁止静默 provider/key/runtime fallback，MVP 只允许白名单 `base_url`；
- 缓存分为来源、解析/索引、证据/检索、公共 AnswerArtifact 和私人生成五层，不同模型共享公共证据而不共享私人回答。

## 5. 评课社区约束

- 公开课程和点评优先使用公共连接器访问；
- 搜索 token 是短期防滥用参数，不是登录 token；
- 登录使用 Flask 会话 Cookie 和 CSRF，没有可共享的通用用户 API token；
- 登录限定内容优先由用户本地 Connector 读取，Cookie 不离开设备；
- 不依赖未声明为稳定公共 API 的内部路由；
- 代码 AGPLv3 不等于用户点评和附件内容开源；
- 未获授权前不批量复制或公开再分发完整点评和课程附件。
- 队长在 2026-07-21 前联系维护者，2026-07-27 前无明确授权则采用保守降级：仅公开模式、单并发低频访问；搜索缓存 5 至 15 分钟，课程元数据/摘要最长 24 小时；不保存原始 HTML 或全量点评，不处理登录受限附件，不公开登录增强报告。

## 6. 核心子系统的接口边界

### Personal Main Agent

输入是用户消息和按需读取的个人上下文；输出是直接回答，或一个不含服务端 ID 的 `SpecialistInvokeRequest`，以及最终 `MainAnswerArtifact`。它负责语义判断、能力选择、专业 query、最小上下文请求和最终综合，不直接访问 Specialist endpoint，也不绕过 Gateway。

### Specialist Registry

输入是管理员验收的 Agent Card、Schema、评测和安全材料；输出是版本化 `CatalogSummary`、按需 `CapabilityDetail` 和管理员 `GovernanceDetail`。未经验收或发生契约变化的版本进入 `review_required`，不得向 Main Agent 披露。

### Gateway

输入是 Main Agent 的目录请求、`SpecialistInvokeRequest` 和 A2A 状态/Artifact；输出是经过授权的目录、ChildTask/ContextGrant、`SpecialistTaskEnvelope`、`AuthorizedContextBundle` 和经校验的 SpecialistArtifact。它依赖 Registry、Profile Resolver、Policy、Task Store、Context Broker、Runner Lease Manager 和 A2A Client，不依赖评课社区页面结构，也不负责语义路由或最终措辞。

### 个人 Agent Harness

输入是已验证的 RootTask 或 ChildTask 与相应执行快照；输出是 TaskEvent、SpecialistArtifact/MainAnswerArtifact 和 UsageRecord。它负责 Template/Profile/Model 执行快照、模型 adapter、MCP 工具、L1-L5 缓存、预算和输出校验。内置 Specialist 可复用 Harness Core，但拥有独立领域 pipeline、工具、知识库、业务 Schema、评测集和公共 QA namespace。Profile、密钥、预算与 Runner 治理字段不进入第三方 A2A payload。

### 课程评价 Specialist

输入是 Gateway 校验后的 `depth=1` 课程调研 ChildTask 和最小 AuthorizedContextBundle；输出是统一 SpecialistArtifact。它通过评课社区 MCP Connector 获取标准化课程数据，不直接管理用户登录 Cookie，不继续委派，也不直接答复用户。

### 课程资料 Specialist

输入是 Gateway 校验后的 `depth=1` 课程资料或复习 ChildTask 和获授权 FileRef/知识空间引用；输出是带引用、限制和 data scope 的 SpecialistArtifact。它依赖资料索引和权限过滤，不访问 Gateway 内部路由表，不继续委派，也不直接答复用户。

## 7. 错误和降级

- 不需要 Specialist：Main Agent 直接回答，不创建 ChildTask；确需专业能力但无匹配项时返回可解释的 unsupported 结果；
- Specialist 不可用：重试策略耗尽后失败，不静默改用另一个 Specialist；
- Specialist 需要补充输入：进入 `input_required`，由 Main Agent 询问用户并继续同一个 child，不计作第二次调用；
- 任务超时：保留 taskId 和已产生进度，允许用户取消或重新提交；
- 数据源未登录：退化到公开模式，并明确缺失范围；
- 数据源限流或页面变化：返回部分结果和来源状态，不生成无证据结论；
- 附件解析失败：只保留文件类型、课程页来源和处理状态，不保存或返回受限下载地址，不阻塞其他证据；
- 私有资料无权访问：在检索前拒绝，不将内容交给模型后再过滤。
- 模型能力不足、预算超限或 Runner 离线：返回专用错误；可用公共缓存仍可展示，但不静默调用其他模型；
- 用户请求切换 provider、key 归属或执行位置：显示数据范围与成本影响，确认后生成新执行票据；
- key 撤销或 lease 过期：停止排队、重试和结果提交，不复用旧授权。

## 8. 评测与验收

### 协议层

- 所有普通用户消息只进入 Personal Main Agent，最终用户答复只由 Main Agent 生成；
- 两个内部 Specialist 和一个外部示例 Specialist 能通过 Agent Card 注册并完成平台验收；
- 只有 `accepted + enabled` 版本可被披露和调用，Card/Schema/权限变化后停止披露；
- 外部示例 Specialist 必须是独立部署/独立进程的真实 A2A 1.0 服务，不能用 Gateway 进程内 mock 替代；
- 每回合至多一个 `depth=1` ChildTask；self-route、第二 child、`depth=2`、fan-out 和 Specialist 委派 100% 被拒绝；
- ContextGrant 只下发白名单字段或受控引用，完整 Profile、ModelProfile、密钥、Cookie、会话和记忆正文泄露为 0；
- `TASK_STATE_WORKING`、`TASK_STATE_INPUT_REQUIRED`、`TASK_STATE_AUTH_REQUIRED`、`TASK_STATE_COMPLETED`、`TASK_STATE_FAILED`、`TASK_STATE_CANCELED` 和 `TASK_STATE_REJECTED` 的 A2A 1.0 映射可复现；
- SSE 中断后可通过 taskId 轮询恢复；
- 外部 Specialist 接入不需要修改 Gateway 业务代码；
- 日志无密钥和 Cookie。
- Agent Card SSRF、任务重放、FileRef 越权、恶意 Artifact 和提示注入用例被拒绝。

### 课程评价 Specialist 验收

- 课程消歧准确；
- 报告覆盖课程信息、工作量、难度、给分、收获、考核方式和时效；
- 关键结论有来源和样本说明；
- 重复查询命中缓存，新点评触发增量更新；
- 登录不可用时能够公开模式降级。

### 课程资料 Specialist 验收

- MVP 文件解析清单固定为文本型 PDF、PPTX 基础文本和 PNG/JPEG 图片 OCR；网页仅作为来源 URL/元数据，DOCX 和扫描版 PDF 整体 OCR 延后；
- 公共和私人资料权限隔离；
- 回答引用正确且能够回到来源；
- 重复文件通过内容指纹去重；
- 删除资料后索引和缓存同步失效。
- 登录受限且未获入库授权的附件不进入服务器文件区、哈希、向量、Artifact 或共享缓存。

### 个人 Agent Harness 验收

- Personal Main Agent 使用用户 ModelProfile 完成能力选择和最终综合；
- 两个内置 Specialist 复用 Harness Core，但领域 pipeline、工具、知识库、Schema、评测和公共 QA namespace 相互独立；
- L3 公共证据可跨 Profile 复用，L4 命中不调用用户模型，主动重新生成写入本人 L5；
- 用户 A 的 ModelProfile、私人记忆和 L5 对用户 B 零可见；
- 模型 capability 不匹配、预算超限、Runner 离线和 key 撤销可解释失败；
- provider/base URL allowlist、密钥 DLP 和禁止静默 fallback 测试通过；Runner lease 测试只在条件增强启用时成为发布门槛。

### 量化通过阈值

| 模块 | 指标 | 通过阈值 |
|---|---|---:|
| Gateway | 三个独立 Agent 固定脚本端到端完成率 | 不低于 90% |
| Gateway | A2A 版本/状态映射、权限和脱敏测试 | 100% 通过 |
| Main 编排 | 固定意图选择准确率 / 不必要 Specialist 调用率 | 不低于 90% / 不高于 10% |
| Main 编排 | 深度、第二 child、fan-out、递归拒绝率 | 100% |
| Main 编排 | Specialist 关键引用和限制保留率 | 100% |
| 课程评价 | 候选 Recall@5 / Top-1 消歧 | 不低于 95% / 85% |
| 课程评价 | 证据覆盖率 / 忠实度 | 不低于 95% / 90% |
| 课程评价 | 凭据、身份和受限地址泄露 | 0 次 |
| 课程资料 | Recall@5 / 引用正确率 / 忠实度 | 不低于 80% / 90% / 90% |
| 课程资料 | 权限隔离 | 100% 通过 |
| 课程资料 | 删除后残留命中 | 0 条，60 秒内失效 |
| Harness | L4 命中时用户模型调用 | 0 次 |
| Harness | 跨用户 ModelProfile/记忆/L5 泄漏 | 0 次 |
| Harness | Runner 重放、任意 base URL、静默 fallback、成本滥用 | 100% 被拒绝 |
| 安全 | SSRF、FileRef 越权、提示注入、恶意文件/Artifact 固定用例 | 100% 被拒绝 |

## 9. 非目标

- 开放市场、计费、多租户运营后台；
- 自动接入任意不可信 Agent；
- 一个回合多个或并行 Specialist、递归委派和 Specialist 相互调用；
- 绕过第三方站点认证或反滥用机制；
- 全量镜像评课社区；
- 自动收集或公开再分发版权不明确的教材、往年题和附件；
- 在文档阶段锁定未经原型验证的所有依赖版本。
- 收集普通用户真实托管 BYOK、任意模型 `base_url`、自动模型竞价/failover、多设备 Runner、开放模板市场和费用代收；完整 CLI Runner 只作为条件增强。

## 10. 文档验收

- 五份独立设计的目标、边界、数据流、错误处理、测试和 MVP 完整；
- 总体架构与五份设计没有协议或职责冲突；
- 评课社区结论有公开页面或开源代码证据；
- README 准确反映“设计阶段”，不声称代码已经实现；
- 路线图能够在 2026-09-06 前形成稳定演示；
- 仓库中不存在账号密码、Cookie、搜索 token、文件哈希样例或私人资料。
- 竞品、技术选型、威胁模型、审计发现和修复状态有独立文档，可区分一手事实、项目推断与未决风险。
- 个人 Agent Harness 的正式规格单列于[个人 Agent Harness 与混合模型运行时设计规格](./2026-07-19-personal-agent-harness-design.md)，主规格只维护跨模块边界与总体验收。
- Main Agent 单跳编排的正式规格单列于[Personal Main Agent 与 Specialist 单跳编排设计规格](./2026-07-19-personal-main-agent-orchestration-design.md)。
