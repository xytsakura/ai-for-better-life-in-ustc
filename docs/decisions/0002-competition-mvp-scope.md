# ADR-0002：采用纵向闭环优先的比赛 MVP

- 状态：已接受
- 日期：2026-07-19
- 决策者：AI for better life In ustc 团队
- 补充决策：[ADR-0003：采用模板化个人 Agent 与混合模型运行时](./0003-hybrid-personal-agent-runtime.md)
- 最新补充：[ADR-0004：采用 Personal Main Agent 与单跳 Specialist 编排](./0004-personal-main-agent-single-hop-orchestration.md)

## 背景

现有方案同时涉及 A2A、MCP、Gateway、两个业务 Agent、RAG、多格式解析、登录 Connector、Redis、对象存储、OpenTelemetry 和前端。三名本科生成员在约七周内还需要完成评测、视频、文档和答辩，若把所有基础设施都列为硬依赖，会显著增加出现多个半成品的概率。

## 决策

比赛 MVP 只把能直接证明核心命题的能力设为必选：

1. Personal Main Agent 作为唯一用户入口，通过 Gateway 每回合至多创建一个 `depth=1` Specialist ChildTask；
2. Gateway 通过 A2A 1.0 接入两个内部 Specialist 和一个独立外部 Specialist；
3. 至少一个内部 Specialist 通过 MCP 调用可复用工具；
4. 课程评价公开模式和课程资料权限隔离 RAG 各有一条稳定演示；
5. PostgreSQL/pgvector 承载任务、元数据、权限和检索；
6. 结构化日志、固定 trace ID、协议测试和场景评测可复现；
7. 第三方 Specialist 接入不修改 Gateway 业务代码；
8. 数据来源、许可、缓存、删除和安全边界在演示中可说明。

ADR-0003 保留个人模型、Profile、缓存和本地 Runner 契约。ADR-0004 进一步冻结产品主链：Main Agent 唯一入口、Catalog 渐进披露、每回合一个 ChildTask、字段级 ContextGrant 和 Main 最终综合成为硬 MVP；完整本地 Runner 改为条件增强。发生冲突时以 ADR-0004 为准。

以下能力改为条件采用：

- LangGraph：只在课程研究 spike 证明能简化状态恢复后采用；
- Redis：只在 PostgreSQL 事件表和单进程通知无法满足 SSE/缓存后采用；
- S3/MinIO：只在部署需要跨容器对象共享后采用；
- OpenTelemetry Collector：只在结构化日志链路稳定后采用；
- 评课社区登录增强 Connector：只在获得授权、公开模式稳定且威胁测试通过后采用；
- 重排模型、课程共享空间 UI、扫描 PDF OCR 等全部后置。
- 完整 CLI 本地 Runner：只在 Main 到 Specialist 单跳闭环稳定、剩余排期充足且安全测试已有基线后做最多两天的增强 spike；
- 真实用户 managed BYOK、任意 provider/base URL、自动模型路由、多设备 Runner、开放模板市场和计费结算全部后置。

## 结果

正面结果：平台协议价值、两个 Demo 和外部接入仍可完整证明，同时降低部署和排障面。

代价：比赛版本不会展示完整的生产级横向扩展、对象存储、企业观测或登录增强云服务。文档必须明确这些是演进方向，不得把条件能力写成已实现或硬验收。

## 停止条件

- A2A SDK spike 两天内无法稳定跑通 Agent Card、任务和流式/轮询时，先记录兼容问题，保留一个 binding 和最小功能，不自造完整协议；
- 课程评价公开模式未稳定前，不实现登录增强 Connector；
- 双 Demo 未稳定前，不引入 Redis、MinIO 或完整观测后台；
- Main 到第一个 Specialist 的父子任务、授权、取消和 Artifact 校验未稳定前，不实现完整 CLI Runner；
- 外部 Agent 必须保留，但可缩减为确定性公开校园通知查询能力；
- 任何可选框架若不能减少代码、提高可测性或改善演示，立即移除。

## 复审条件

当基础闭环连续通过固定测试、团队剩余时间超过两周且材料初版已完成时，才允许把条件能力提升为 MVP 必选，并用新的 ADR 记录原因和验收标准。
