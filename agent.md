# 当前工作目录约定

## 项目

本目录用于筹备参加中国科大“一〇七”杯算力与智能体开发大赛，并支持组队协作。

## 已确认偏好

- 给人看的文档默认使用中文 Markdown，优先保证在 Codex 中可正常渲染。
- 比赛规则以官方公众号原文和组委会最新通知为准；本地笔记用于整理，不替代官方通知。
- 重要日期、报名状态、赛道和队伍资格在推进任务前先核对。
- 使用外部数据、代码、模型和服务时记录来源，并检查合法合规与隐私风险。
- 不在仓库中保存 API key、密码、个人敏感信息或未脱敏数据。

## 当前工作经验

- 官方文章链接已验证可访问：<https://mp.weixin.qq.com/s/AZKK8QSrTQWR3u0yO4d_kA>
- 队伍名称为 `AI for better life In ustc`，队长负责主要协调工作。
- 队伍共有 3 名本科生，已完成报名，参加本科生组的智能体赛道。
- GitHub 私有仓库为 `xytsakura/ai-for-better-life-in-ustc`。
- 两名队友的 GitHub 用户名为 `wml-wml294` 和 `ysyzynx`，邀请时只使用 GitHub 用户名，不记录个人邮箱。
- 官方文章发布时间为 2026 年 6 月 24 日；原文报名截止为 2026 年 7 月 12 日，本队已在截止流程中完成报名。
- 赛道分为“算力平台赛道”和“智能体赛道”，每队最多 4 人且至少 1 名本科生，每队只能报一个赛道。
- 2026 年 7 月 19 日会议确定：核心产品是校园 Agent 协议调度与集成框架，课程评价和课程资料助手是两个验证 Demo。
- 第一版采用白名单第三方 Agent 真实接入；A2A 负责 Agent 间通信，MCP 负责工具与数据连接，OpenAPI/JSON Schema 负责平台接口契约。
- 评课社区使用 Flask 会话 Cookie 和 CSRF，不存在可共享的通用用户 API token。搜索 URL 的短期 token 不是登录凭据。
- 评课社区登录凭据不得提取到聊天、写入文档或提交仓库；登录限定内容优先通过用户本地 Connector 访问。
- 评课社区代码的 AGPLv3 不覆盖用户点评和课程附件的内容版权。未经授权不得批量复制或公开再分发。
- 文档目录按 `architecture/`、`designs/`、`research/`、`security/`、`audits/`、`meetings/`、`decisions/` 分类；代码目录只在实施计划批准且确有内容时创建。
- 发布前技术审计新增 `audits/` 与 `security/`：重要方案必须记录发现、修复状态、残余风险和威胁模型，不能只写理想架构。
- 外部 Agent 固定用 `campus.notice.lookup` 做第一周真实接入验收；接入只允许配置/Schema 变化，Gateway 业务代码改动为 0。
- 比赛 MVP 采用纵向闭环优先：PostgreSQL、受控文件区、结构化日志为基线；Redis、MinIO、完整 OpenTelemetry、LangGraph 和登录增强 Connector 条件采用。
- 未获评课社区明确授权时，登录受限点评和附件不进入服务器事件、对象存储、哈希、向量、Artifact 或共享缓存；公开模式使用短 TTL。
- 网页、点评、外部 Agent 输出和用户文件均按不可信输入处理，实施必须覆盖 SSRF、提示注入、恶意文件、FileRef 越权和任务重放。
- 2026 年 7 月 19 日新增产品方向：平台提供统一校园 Agent Harness，用户通过 `AgentTemplate + AgentProfile` 维护个人 Agent，可选择不同模型、知识空间、记忆、预算和执行位置。
- 模型提供方属于 Harness 内部 adapter，不是 A2A Agent 或 MCP Tool；外部独立 Agent 继续使用 A2A，Agent 内部工具继续使用 MCP。
- 个人模型支持 `platform_sponsored`、`managed_byok`、`local_runner` 三类设计；比赛 MVP 不收集普通用户真实托管 BYOK，真实用户 key 优先保留在只出站本地 Runner。
- 平台模型 provider 和 `base_url` 必须白名单；不允许静默切换 provider、密钥归属或本地/托管执行，私人数据远端发送必须逐次确认。
- 缓存按公开来源、解析/索引、证据/检索、公共 AnswerArtifact、私人生成五层治理；不同模型可复用公共证据，私人回答、记忆和用户级 usage 不跨用户命中。
- `v0.3` 已形成 ADR-0003、个人 Agent Harness、模型/BYOK/Runner 调研和历史审计基线。
- `v0.4` 冻结 Personal Main Agent 单跳架构：Main Agent 是唯一用户入口和语义选择者，Gateway 是确定性控制面，Specialist 只允许 `depth=1` 且 `can_delegate=false`。
- Specialist Catalog 采用三级渐进披露：Main 可见 `CatalogSummary`，候选确定后按需读取 `CapabilityDetail`，完整 `GovernanceDetail` 仅管理员验收使用。
- 每个用户回合最多一个 Specialist ChildTask；禁止第二 child、递归、fan-out、并行 Specialist 和 Specialist 相互调用。补充输入继续同一个 child。
- 个人上下文通过字段级 `ContextGrant` 和短期 `AuthorizedContextBundle` 下发；不得传完整 Profile、ModelProfile、API key、Cookie、完整会话或完整私人记忆。
- Specialist 返回 `SpecialistArtifact`，经 Gateway 校验后由 Main Agent 生成最终 `MainAnswerArtifact`；Specialist 不直接答复用户或写长期记忆。
- 内置 Specialist 共享 Harness Core，但各自维护领域 pipeline、工具、知识库、业务 Schema、评测集和公共 QA namespace；第三方 Specialist 可以有独立运行时。
- Main 到单 Specialist 的闭环是比赛硬 MVP；完整 CLI Runner 改为满足主链稳定、安全基线和排期条件后最多两天的增强 spike。
- 当前仍处于设计与任务文档阶段，不创建空代码脚手架；v0.4 必须经独立架构、契约、安全和范围审计后才能作为当前发布版推送。
