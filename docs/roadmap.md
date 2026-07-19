# 项目路线图

> 架构基线：v0.4
>
> 计划周期：2026-07-19 至 2026-09-06

## 1. 时间与团队边界

本路线图从 2026 年 7 月 19 日开始，到 2026 年 9 月 6 日线上提交窗口结束。根据已整理的比赛信息，线上提交窗口为 2026 年 8 月 1 日至 2026 年 9 月 6 日，9 月上旬进行线上初评。最终材料必须在 2026 年 9 月 6 日前处于可提交状态，不能把关键功能压到最后一天。

团队规模固定按 3 名本科生规划。路线图只划分角色责任，不绑定具体队员，也不假设额外后端、算法、设计或运维人力。

## 2. v0.4 项目主线

作品主线从“Gateway 自动选择多个 Agent”收敛为“个人 Main Agent 调用一个 Specialist 的受控一跳闭环”：

1. `Personal Main Agent` 是唯一用户入口和唯一语义决策者。它理解用户问题、选择一个 Specialist、决定需要申请的个人上下文字段，并生成最终 `MainAnswerArtifact`。
2. `Gateway` 是确定性控制面。它维护 Specialist Catalog，只渐进披露 `accepted + enabled` 版本的 `CatalogSummary` 与按需 `CapabilityDetail`，执行身份、父子任务、深度、幂等、预算、取消、ContextGrant 和 Artifact 校验；它不使用模型做意图路由或回答综合。
3. 每个 Main turn 最多创建一个 `depth=1` 的 `ChildTask`。只有 Main 拥有 `create_child_task`；Specialist 不得创建子任务、递归、自调用或直接向用户返回最终答案。
4. Main 通过 `ContextGrant` 申请必要字段，Gateway 物化目标绑定、字段级、短 TTL 的 `AuthorizedContextBundle`。Specialist 只能看到该 Bundle，不能读取整份 Profile、记忆或其他个人数据。
5. Specialist 复用领域 pipeline、Harness、知识库和公共 QA，返回 `SpecialistArtifact`。内置 Specialist 共享 Harness Core，第三方 Specialist 可使用自己的运行时但必须遵守同一 A2A/Artifact 契约。Gateway 验证后交给 Main，由 Main 生成最终 `MainAnswerArtifact`。

三个领域增量依次接入同一闭环：

1. 课程评价 Specialist：先替换 mock，证明领域 pipeline、公共 QA、引用与结构化 Artifact。
2. 课程资料 RAG Specialist：复用同一 ChildTask/ContextGrant/Artifact 契约，证明公共与个人知识隔离。
3. 外部通知 Specialist：最后以独立进程接入 `campus.notice.lookup`，证明 Agent Card 经过验收后可零 Gateway 业务代码改动接入。

## 3. 硬 MVP 与条件增强

### 3.1 不可降级的硬 MVP

比赛 MVP 必须完整跑通以下纵向闭环：

```text
用户
  -> Personal Main Agent
  -> 已验收 CatalogSummary / CapabilityDetail 渐进披露
  -> 一个 depth=1 ChildTask
  -> ContextGrant / AuthorizedContextBundle
  -> 一个 Specialist
  -> SpecialistArtifact
  -> Gateway 校验
  -> MainAnswerArtifact
  -> 用户
```

这里的“单 Specialist”表示每个用户回合最多调用一个 Specialist，不是 Catalog 全局只能注册一个；Main 可以直接回答并创建 0 个 ChildTask，最终 Catalog 仍承载两个内置 Specialist 与一个极简外部 Specialist。

硬 MVP 的验收定义：

- 同一用户轮次只能成功创建一个 ChildTask，重试幂等，第二个不同任务被拒绝；
- Specialist 身份没有 `create_child_task`，递归、自调用和 `depth>1` 创建成功数为 0；
- Main 只能看到 `accepted + enabled`、版本固定的 `CatalogSummary`/`CapabilityDetail`；`review_required` 和未验收 Agent 零披露，原始 Agent Card 与管理员 `GovernanceDetail` 不进入 Main；
- `AuthorizedContextBundle` 的实际字段与 `ContextGrant` 批准字段完全一致，错用户、错目标、过期和多余字段均拒绝；
- Specialist 只返回结构化 `SpecialistArtifact`，恶意或错 Schema 产物不能进入 Main；
- 所有用户可见最终答案均由 Main 生成 `MainAnswerArtifact`，并保留引用、data scope、cache level、限制和最多一个 child lineage；
- 父任务取消/超时会吊销 ChildTask 与 Bundle，迟到或重放结果不能生成新答案或写记忆；
- 预算、token、工具步数、重试和超时都有 Gateway 硬限制与可解释日志。

在上述闭环稳定前，不开始完整 Runner、多模型、复杂 RAG 格式、登录增强或外部通知扩展。不能把 Specialist 改成 Main 进程内普通函数、让 Specialist 直接回答用户，或取消父子任务/ContextGrant 来“简化演示”。

### 3.2 条件增强

以下能力不进入硬 MVP：

- 完整 CLI Runner：真实设备配对、lease、离线恢复、本地 key、完整 Usage Ledger 与多 provider 适配；
- 普通用户真实托管 BYOK、任意 provider/base URL、自动模型路由或静默 fallback；
- Redis、MinIO/S3、完整 OpenTelemetry、LangGraph、多 Specialist、并行或递归编排；
- 评课社区登录增强、未授权附件、自动增量抓取和生产级恶意文件隔离；
- 重排模型、复杂表格/公式/扫描版 PDF、开放 Specialist 市场。

完整 CLI Runner 只有在以下条件全部满足后才启动最多两天的增强 spike：

1. 硬 MVP 全部固定测试连续两次通过；
2. 课程评价 Specialist 已按演示脚本稳定跑通；
3. 三人当周无超过两天无人负责的主线阻塞；
4. Runner 能直接证明隐私或模型可替换性，且不会改变 ChildTask、ContextGrant 和 Artifact 契约。

未满足条件时只保留 Runner 接口 Schema、mock provider 与安全测试，不实现完整 CLI。Runner 成败不得阻塞课程评价、RAG、外部通知和提交材料。

## 4. 里程碑

| 日期 | 里程碑 | 必须达到的状态 |
| --- | --- | --- |
| 2026-07-21 | v0.4 边界冻结 | Main、Gateway、Specialist 职责；单 ChildTask；ContextGrant；双 Artifact 契约与两个 Demo 范围确认 |
| 2026-07-27 | Main 到 mock Specialist 一跳闭环 | Catalog + 已验收能力摘要、父子状态机、单 `depth=1` ChildTask、mock SpecialistArtifact、Gateway 校验和 MainAnswerArtifact 全链通过 |
| 2026-08-03 | 课程评价 Specialist E2E | mock 被课程评价领域 pipeline 替换；合规公开样例、公共 QA、引用与演示页面稳定 |
| 2026-08-10 | RAG Specialist MVP | 合规样例可导入、检索和引用；公共资料与个人私有资料通过字段级 Grant 隔离 |
| 2026-08-17 | 双 Demo 可演示 | 课程评价与 RAG 都复用同一一跳契约，取消、越权、恶意 Artifact 和记忆写回测试通过 |
| 2026-08-24 | 外部通知与评测闭环 | 独立 `campus.notice.lookup` Specialist 通过 Agent Card 验收接入；Gateway 业务代码零改动；基础评测完成 |
| 2026-08-31 | 提交材料初版 | 设计文档、演示视频脚本、部署说明、安全证据和答辩稿初版完成 |
| 2026-09-04 | 冻结版本 | 只修严重 bug、安全问题和材料，不再加功能 |
| 2026-09-06 | 完成线上提交 | 提交代码、文档、视频和相关材料 |

## 5. 每周计划

### 第 0 周：2026-07-19 至 2026-07-21

交付物：

- 项目 README、基本协作规范和 v0.4 术语表；
- Main、Gateway、Specialist、Catalog、ContextGrant 与双 Artifact 的职责和边界图；
- `ChildTask`、`ContextGrant`、`AuthorizedContextBundle`、`SpecialistArtifact`、`MainAnswerArtifact` 最小 Schema 草案；
- 两个课程 Demo 与外部通知 Specialist 的数据/许可边界；
- v0.4 威胁模型、硬 MVP 取舍与停止规则；
- 由队长暂代数据合规联系人，在 2026-07-21 前向 `service@icourse.club` 发送比赛用途、只读接口、缓存期限、速率限制、附件摘要和演示授权咨询，并记录邮件内容。

重点：

- 冻结“Main 唯一语义入口、Gateway 确定性控制面、每轮一个 depth=1 ChildTask”；
- Gateway 只提供已验收能力摘要，不把原始 Agent Card 文本直接注入 Main；
- 维护者回复等待到 2026-07-27。若届时无明确授权，课程评价只用公开、低频、短 TTL 或自制脱敏样例；不保存原始 HTML/全量点评，不处理登录受限附件。

### 第 1 周：2026-07-22 至 2026-07-27

目标：先完成 Catalog + parent-child + mock Specialist，不接真实课程业务。

交付物：

- Specialist Catalog：管理员录入、Agent Card 校验、`accepted + enabled` 版本冻结、规范化 `CatalogSummary`/`CapabilityDetail` 与渐进披露接口；完整 `GovernanceDetail` 仅管理员可见；
- Main turn 与 ChildTask 父子状态机：每轮一个、`depth=1`、幂等、取消、超时、迟到结果隔离；
- Main 专属 `create_child_task` 能力；Specialist 身份、客户端和普通工具调用均明确拒绝；
- `ContextGrant` 到 `AuthorizedContextBundle` 的字段 allowlist、目标/任务/TTL 绑定和审计字段；
- 一个独立 mock Specialist：固定领域 pipeline，返回正常、失败、超时、恶意和乱序 `SpecialistArtifact` 样例；
- Gateway Artifact 校验与 Main 生成 `MainAnswerArtifact` 的完整链路；
- 最小演示入口：输入问题、显示能力摘要、父子任务状态、授权字段、Artifact 验证结果和最终回答；
- 固定回归：正常、第二 ChildTask、递归、自调用、错目标 Bundle、取消/重放、恶意 Artifact、记忆写回污染至少各 2 条。

重点：

- 7 月 24 日先跑通纯 mock 主路径，再补异常路径；不等待 RAG、真实数据或完整模型配置；
- PostgreSQL 事件/状态表、受控本地样例区和结构化日志先行；不部署 Redis、MinIO 和完整 OTel；
- 能力摘要、任务计数、深度、授权和 Artifact 校验必须是确定性代码路径，不能依赖提示词承诺；
- 7 月 27 日硬 MVP 未通过时，立即停止 Runner、外部 Agent、UI 美化和业务扩展，下一周继续修闭环。

### 第 2 周：2026-07-28 至 2026-08-03

目标：用课程评价 Specialist 替换 mock，闭环契约不变。

交付物：

- 课程评价领域 pipeline/Harness、公开知识库和版本化公共 QA；
- 合规公开或自制脱敏样例集，支持检索、筛选、总结和引用；
- 课程评价 `CatalogSummary`/`CapabilityDetail`、ContextGrant 字段模板和 `SpecialistArtifact` Schema；
- 30 至 50 条固定问答与安全夹具：来源、许可、数据版本、人工答案、模型/提示版本和评审人；
- 第一版演示脚本；展示 Main 选择、一个 ChildTask、领域 Artifact、Gateway 校验和 Main 最终综合；
- 公共 QA 发布、双人复核、版本哈希、撤回和级联失效流程。

重点：

- Specialist 不接管用户会话，不读取完整 Profile，不直接生成最终用户答案；
- 只使用允许访问、可展示或模拟脱敏的数据；真实登录增强不是本周交付；
- 2026 年 8 月 1 日提交窗口开启后，仓库和材料进入可持续提交状态。

### 第 3 周：2026-08-04 至 2026-08-10

目标：接入课程资料 RAG Specialist，复用同一控制面契约。

交付物：

- 课程资料导入任务、元数据、分块和 Postgres + pgvector 基础索引；
- 文本型 PDF、PPTX 基础文本和清晰 PNG/JPEG 印刷体 OCR 三类演示候选；
- 公共课程知识库与个人私有知识库隔离；
- RAG `CatalogSummary`/`CapabilityDetail`、字段级 ContextGrant、受控 `FileRef` 与带引用 `SpecialistArtifact`；
- 40 至 60 条固定 RAG 评测夹具，以及恶意文件、提示注入、FileRef 越权和删除失效样例；
- 私有资料删除后，Grant 立即撤销且 60 秒内检索零命中。

重点：

- 手动导入合规样例，不做未授权自动抓取；
- 文件先进入隔离区，在无网络、受资源限制的解析环境处理；
- RAG 不得绕过 Main 直接回答，也不得把检索到的私人内容晋升公共 QA 或公共缓存；
- 格式不稳定时按停止规则收缩解析深度，不改变一跳闭环。

### 第 4 周：2026-08-11 至 2026-08-17

目标：稳定双 Demo，完成上下文、Artifact 与记忆写回安全闭环。

交付物：

- 课程评价与 RAG 均通过同一 Catalog、ChildTask、ContextGrant 和双 Artifact 契约；
- 前端展示当前 Specialist、能力摘要版本、授权字段、预算、来源与取消状态；
- 学生 A 的私有资料、Profile、记忆和私人 Artifact 对学生 B 零可见；
- 恶意 `SpecialistArtifact`、公共 QA 污染、错误引用和提示注入回归；
- 只有 Main 可产生 `MemoryWriteProposal`，敏感/长期写回需用户确认，可追溯、撤销和删除；
- 小规模端到端成功率、引用正确率、权限隔离和延迟指标表。

条件增强：

- 只有满足第 3.2 节四项前置条件，才做最多两天 CLI Runner spike；
- spike 只验证一个设备、一个并发、一个固定任务和只出站 lease，不扩多设备、离线队列或普通用户托管 BYOK；
- 未达到条件时保持 mock provider 与 Runner 接口测试，不影响双 Demo 验收。

### 第 5 周：2026-08-18 至 2026-08-24

目标：最后接入外部通知 Specialist，证明扩展性。

交付物：

- 独立进程 `campus.notice.lookup`，只读取团队自制或明确公开的通知样例；
- 标准 A2A 1.0 Agent Card，经平台注册、摘要规范化、人工验收和版本冻结；
- 10 条固定接入测试：正常、无结果、追问边界、取消、超时、重放、不可达、恶意 Card、恶意 Artifact 与超预算；
- Gateway 只新增 Agent Card/白名单配置与 Schema，业务路由代码改动为 0；
- 外部 Specialist 接入文档、SDK 薄封装、兼容回归和降级说明；
- 三个 Specialist 的统一评测脚本与基础指标表。

重点：

- 外部通知是扩展性证明，不抢课程评价和 RAG 的稳定性时间；
- 外部 Agent 已验收仍按不可信网络输入处理；
- 不实现通知推送、订阅、写操作或真实校园账号登录，只做确定性只读查询。

### 第 6 周：2026-08-25 至 2026-08-31

交付物：

- 提交版设计文档初稿；
- 部署说明、环境变量清单、威胁模型与安全验收证据；
- 演示视频脚本、镜头清单、样例账号和样例问题；
- 答辩 PPT 或讲稿初稿；
- 代码冻结候选版本和一键演示/备用录屏。

重点：

- 材料开始占主线时间，不再扩 Specialist 数量、Runner 能力或文件格式；
- 所有截图和视频使用合规样例；
- 评审应能看清 Main 的语义职责、Gateway 的确定性控制、字段级上下文和不可递归的一跳边界。

### 第 7 周：2026-09-01 至 2026-09-06

交付物：

- 最终代码包或仓库提交状态；
- 最终设计文档、安全/评测证据和演示视频；
- 答辩材料、备用演示方案与线上提交确认记录。

重点：

- 2026 年 9 月 4 日后冻结功能，只修严重 bug、安全问题、材料错误和必要视频；
- 2026 年 9 月 5 日完成一次完整提交预演；
- 2026 年 9 月 6 日只做最终核对和提交，不安排新增功能。

## 6. 三人角色建议

### 角色 A：Gateway 控制面与协议

职责：

- Specialist Catalog、Agent Card 验收、`CatalogSummary` 与 `CapabilityDetail`；
- Main/ChildTask 父子状态机、幂等、取消、重放、预算和结构化日志；
- ContextGrant/AuthorizedContextBundle、Artifact Schema 与权限校验；
- 外部 A2A 接入、部署和基础回归。

优先交付：

- 第 1 周 mock 一跳闭环；
- 确定性安全边界；
- 外部通知零业务代码改动接入。

### 角色 B：Specialist Pipeline、数据与评测

职责：

- 课程评价 pipeline、公共 QA、合规样例和引用；
- RAG 导入、解析、分块、索引、检索和 `FileRef` 隔离；
- `SpecialistArtifact` 领域 Schema、固定评测集和失败案例；
- QA/KB 版本、撤回、缓存失效与数据删除。

优先交付：

- 第 2 周课程评价 Specialist；
- 第 3 周 RAG Specialist；
- 权限、引用和污染测试。

### 角色 C：Personal Main、前端、演示与材料

职责：

- Personal Main Agent 的语义选择、最终综合和 `MainAnswerArtifact`；
- Main 唯一用户入口、能力渐进披露、父子轨迹和授权字段展示；
- `MemoryWriteProposal` 交互、用户确认与撤销展示；
- 视频、截图、答辩材料、演示数据和提交检查。

优先交付：

- Main 到 mock Specialist 的用户闭环；
- 双 Demo 统一入口与可解释轨迹；
- 8 月底提交材料初版和稳定录屏。

三人可以互相 code review，但不得让多人同时无边界地改 Gateway 状态机、Schema 或演示主流程。每周必须明确一个主责任人和一个复核人。

## 7. 关键路径

1. 冻结 v0.4 契约与角色边界；
2. Catalog + parent-child + mock Specialist 跑通硬 MVP；
3. 课程评价 Specialist 替换 mock，验证领域 pipeline 与公共 QA；
4. RAG Specialist 复用同一契约，验证字段级私人上下文和引用；
5. 双 Demo 安全与评测稳定后，再接外部通知 Specialist；
6. 条件满足时才做完整 CLI Runner spike；
7. 8 月 25 日起材料与稳定性优先，9 月 4 日冻结。

任何功能如果不能服务这条路径，都应推迟或砍掉。硬 MVP 不能被 UI 美化、Runner、多模型、更多 Specialist 或基础设施替代。

## 8. 风险与缓冲

| 风险 | 影响 | 缓冲策略 |
| --- | --- | --- |
| Main/Gateway 职责混淆 | Gateway 变成第二个语义 Agent，架构不可解释 | 固定测试验证 Gateway 只执行目录、策略、状态和 Schema；语义选择与最终综合只在 Main |
| Specialist 递归或成本放大 | 任务失控、预算耗尽 | Specialist 无 `create_child_task`；单轮一个 depth=1 ChildTask；硬预算、步数、重试和超时 |
| 上下文过度披露 | 私人 Profile、记忆或文件泄露 | 字段级 Grant、目标/任务/TTL 绑定、deny-by-default、逐次 egress 确认与两个用户零命中测试 |
| 能力摘要 / Agent Card 注入 | Main 被诱导错误选择或泄密 | 只披露 `accepted + enabled` 的规范化 `CatalogSummary`/`CapabilityDetail`；原始 Card 不进提示；版本哈希与变更复审 |
| 恶意 Artifact 或公共 QA 污染 | 错误/注入扩散到最终答案和记忆 | Gateway Schema/引用/大小校验；QA 双人复核和版本撤回；Specialist 无记忆写权限 |
| 课程数据许可不清 | 无法公开演示 | 使用公开许可、书面授权或自制脱敏样例；未获授权不做登录增强 |
| RAG 格式过多 | 导入链路不稳定 | 优先文本 PDF；PPTX/OCR 按停止规则保留最浅演示；不做复杂格式 |
| 三人时间不足 | 多条支线半成品 | 每周一个可演示增量；硬 MVP 未通过即停止全部增强；8 月 24 日后不扩范围 |
| 完整 Runner 抢主线 | 设备、lease、BYOK 与 provider 排障拖垮 Demo | Runner 改为条件增强，前置条件不满足就只留 Schema 和 mock provider |
| 外部通知接入过晚 | 扩展性证明不足 | 第 1 周先完成真实 Catalog/Card 校验与 mock 独立进程模式；第 5 周只替换领域实现 |
| 基础设施堆叠 | Redis/MinIO/OTel/LangGraph 排障占用时间 | 默认 PostgreSQL、受控文件区和结构化日志；两天 spike 未证明价值即停止 |
| 视频或部署失败 | 评审无法复现 | 8 月 31 日前录初版；准备本地演示、截图、录屏和一键启动说明 |

## 9. 明确停止条件

### 9.1 主线门禁

- **2026-07-24**：若 Main 到 mock Specialist 的正常路径尚未跑通，停止 LangGraph、Runner、多模型、真实数据和 UI 美化，只保留显式 pipeline、PostgreSQL 状态和最小页面。
- **2026-07-27**：若硬 MVP 任一验收失败，课程评价、RAG 和外部通知不得并行开工；全员优先修 Catalog、父子状态、单 ChildTask、ContextGrant、Artifact 与取消/重放，直到固定测试连续两次通过。
- **硬 MVP 不允许的降级**：不得改成 Main 直接调用领域函数、Specialist 直接回答用户、Gateway 用模型做路由、移除字段级 Grant，或允许 Specialist 递归。若到提交仍不稳定，必须停止宣称 v0.4 已完成并继续优先修复闭环，不能用无父子任务的 mock 或伪造“多 Agent 协作”掩盖失败。

### 9.2 领域与增强停止条件

- **2026-08-03**：若真实课程评价数据或访问许可不稳定，切换到自制脱敏公开样例和固定公共 QA；保留课程评价 Specialist 契约与引用演示，不实现登录增强。
- **2026-08-10**：若 RAG 多格式不稳定，先只保文本型 PDF；PPTX 只取基础文本，PNG/JPEG 只做清晰印刷体 OCR。复杂表格、备注、公式、扫描 PDF 和 DOCX 全部停止。
- **2026-08-17**：若课程共享空间权限复杂，降为公共库 + 单用户私有库；若记忆写回不稳定，关闭持久化，只展示待确认 `MemoryWriteProposal`，不能允许自动写回。
- **2026-08-17**：若完整 CLI Runner 的四项前置条件未全部满足，取消本次比赛的 Runner 实现，只保接口 Schema、mock provider、安全说明和未来工作；已开始的 spike 超过两天仍未通过也立即停止。
- **2026-08-20**：若重排模型或复杂混合检索不能稳定提高固定评测，停止该增强，保留全文检索 + 向量检索加权融合。
- **2026-08-24**：若真实外部通知源、认证或网络稳定性阻塞，改用独立进程读取团队自制通知样例；仍须发布并验收标准 Agent Card，Gateway 业务代码零改动，不能退化为 Gateway 内部 mock。
- **2026-08-24**：停止新增 Specialist、provider、文件格式和基础设施；后续只做评测、安全、材料和稳定性。
- **2026-08-27**：若自动增量更新不稳定，改为手动重新导入并保留数据版本；不再排查增量同步。
- **2026-08-31**：停止全部新增功能，进入材料与稳定性优先；必须已有可提交代码、设计文档初稿和录屏初版。
- **2026-09-04**：冻结代码；除严重 bug、安全问题和提交材料错误外不再修改。

### 9.3 最小提交作品

无论条件增强是否完成，最终必须保留：

- 一个 Personal Main Agent 唯一入口；
- 一个确定性 Gateway 与已验收 Specialist Catalog；
- 一个每轮至多一个、`depth=1`、不可递归的父子任务闭环；
- 一套字段级 ContextGrant/AuthorizedContextBundle；
- 至少一个独立 Specialist 完成 `SpecialistArtifact -> Gateway 校验 -> MainAnswerArtifact`；
- 一个端到端课程评价 Demo；
- 一个课程资料 RAG Demo，至少证明公共资料与个人私有资料隔离；
- 一个独立进程的 `campus.notice.lookup` 外部通知 Specialist；若真实数据源不可用，使用自制通知样例；
- 一组覆盖能力注入、越权、递归、上下文披露、恶意 Artifact、取消/重放、公共 QA 与记忆写回的安全验收证据；
- 一份能讲清楚架构、合规、评测与演示流程的设计文档和一段稳定录屏。

完整 CLI Runner 不属于最小提交作品。

## 10. 每周检查清单

每周至少检查一次：

- 用户是否只通过 Personal Main Agent 进入，最终回答是否只由 Main 生成；
- Gateway 是否仍为确定性控制面，而没有承担语义路由或答案综合；
- 同一 Main turn 是否最多一个 ChildTask，Specialist 是否仍无 `create_child_task`；
- 能力摘要是否来自已验收、版本固定的 Agent Card，是否有注入或未审核变更；
- Bundle 字段是否与 ContextGrant 完全一致，是否有跨用户、错目标、过期或多余字段；
- 父取消/超时后 ChildTask、Grant、迟到 Artifact 和重放是否均无副作用；
- SpecialistArtifact、公共 QA、知识库和文件是否按不可信输入校验；
- 是否发生 Specialist/公共 QA 自动写个人记忆，或未经确认的敏感持久化；
- 是否有 API key、Cookie、CSRF、私人正文或受限 URL 进入仓库、日志、Artifact 或公共缓存；
- 当前功能能否从零启动并按脚本演示；
- 是否有超过 2 天无人负责的阻塞项；
- 本周是否让项目更接近 2026 年 9 月 6 日提交，而不是只增加复杂度。
