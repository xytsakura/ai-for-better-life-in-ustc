# 竞品与参考实现技术调研

> 调研日期：2026-07-19  
> 范围：Agent 互操作、编排平台、Deep Research、校园 RAG 与企业治理。  
> 证据原则：优先使用官方文档和项目仓库；产品宣传、自报指标与本项目推断分别标注。

## 1. 结论

市场上已经存在大量 Agent 编排平台、工作流框架和校园问答助手，因此本项目不能把“统一聊天入口”“使用 RAG”或“支持多个 Agent”本身当作创新点。

本项目更有说服力的差异化是：

1. 使用 A2A 1.0 真实接入由不同团队独立部署的校园 Agent，而不是把所有能力写进同一应用；
2. 用可验证的校园能力契约、权限范围和来源证据治理 Agent，而不是只做模型路由；
3. 用两个课程 Demo 证明同一调度层既能承载 Deep Research，也能承载权限隔离 RAG；
4. 通过“第三方仅新增 Agent Card 和白名单配置、Gateway 业务代码零修改”的验收证明平台是上游能力；
5. 给出固定接入脚本、协议兼容测试和端到端 trace，让扩展性能够被复现，而不是只存在于架构图中。

## 2. 竞品分组

### 2.1 协议与基础设施

| 项目 | 一手事实 | 可借鉴 | 不应照搬 |
|---|---|---|---|
| A2A | Agent Card 用于发现；task/context 表达长任务；支持流式、轮询和认证声明 | 跨团队 Agent 的发现、任务生命周期和 HTTP 协议边界 | 不把内部每个函数调用都改成 A2A |
| MCP | 标准化 tools、resources、prompts 和传输授权 | Agent 对数据源、检索和文件工具的稳定接口 | 不把完整远端 Agent 降格为 MCP Tool |
| OpenAPI / JSON Schema | 描述普通 HTTP API 和结构化载荷 | Gateway REST 契约、测试和生成文档 | 不替代 A2A 的任务语义 |
| OpenTelemetry | 统一 traces、metrics、logs 和上下文传播 | 串联 Gateway、Agent、MCP 工具和数据库 | 比赛 MVP 不必先部署完整观测平台 |

结论：这些是应直接借用的标准，不是本项目需要重新发明的产品层。

### 2.2 编排框架与开发 SDK

| 项目 | 一手事实 | 对本项目的判断 |
|---|---|---|
| LangGraph | 用 graph、state、node、edge 表达工作流，支持 persistence、streaming 和 human-in-the-loop | 适合课程 Deep Research 内部的可恢复研究流程；先做两天 spike，只有确实用到 checkpoint/人工确认才纳入 MVP |
| Open Deep Research | MIT 开源；支持多模型、多搜索工具和 MCP；使用 LangGraph server，并提供 Deep Research Bench 评测路径 | 可借鉴“问题拆解、并行检索、证据压缩、报告生成、固定评测”的流水线，不直接复制其通用网页搜索产品形态 |
| OpenAI Agents SDK | 提供 agent loop、handoff、tracing 和 MCP 集成 | 适合快速做单一厂商模型原型，但它是运行时 SDK，不是跨团队互操作标准；本项目不以其替代 A2A |

主 Agent 的取舍：不把 LangGraph 设为全平台硬依赖。Gateway 使用明确的应用状态机；课程研究 Agent 可以在原型证明能减少代码和提高可恢复性后采用 LangGraph，课程 RAG 的导入与检索无需为“多 Agent”标签强行图化。

### 2.3 Agent 平台与市场

| 产品 | 一手事实 | 可借鉴 | 不采用为底座的原因 |
|---|---|---|---|
| Dify | 支持 workspace、应用/知识库 API、workflow/chatflow/agent 流式事件和 Docker Compose 自托管 | 工作区隔离、知识库产品交互、流式事件展示 | 中心化平台模型不能直接证明 A2A 第三方 Agent 互操作，且引入整个平台会削弱原创实现边界 |
| Coze | 产品架构覆盖 bot、plugin、workflow、knowledge base 和多渠道发布 | “Agent + 工具 + 工作流 + 知识库”的用户心智 | 官方平台依赖强，协议层细节和独立部署接入不是本项目要证明的重点 |
| Copilot Studio | 支持知识源、工具/连接器、用户级认证、安全治理和测试 | 用户权限、连接器授权、测试面板 | 绑定微软生态和企业身份，不适合作为开放校园 Agent 网关底座 |
| Agentforce | 官方资料覆盖 data library、retrieval quality、Testing Center 和权限配置 | 数据源治理、离线评测和最小权限思路 | Salesforce 平台绑定过重，比赛无法复现其企业基础设施 |

这些产品说明“市场、治理、知识库、工作流”都已有成熟实现。比赛版不建设开放市场或企业级后台，只借鉴最能证明可信性的权限、评测和观测机制。

### 2.4 校园助手与校园 RAG

| 项目 | 可验证事实 | 局限与启发 |
|---|---|---|
| MustangsAI | 仓库描述为 MSU Texas 校园助手；README 展示官方网页来源、引用、反馈、会话记忆、Streamlit 与 FAISS | 证明“官方来源 + 引用 + 反馈”比单纯聊天更重要；README 中效果数据为作者自报，且 2026-07-19 访问仓库元数据时未见明确许可证，只作产品参考 |
| UB ISS Chatbot | MIT 开源；以大学公开文档为上下文，使用 Mistral 7B、OpenAI embeddings、LlamaIndex 和 Streamlit | 是低复杂度校园 RAG 基线；缺少跨 Agent 接入、细粒度权限和来源增量治理 |
| RAG-Based University Assistant | 仓库说明以大学 PDF、Gemini、LangChain 和 RAG 回答学生问题 | 可参考最小导入问答流程；未发现明确许可证，不直接复用代码 |

校园项目的共同经验是：用户真正感知的是来源可信、答案可核验、信息是否过期和反馈能否闭环。它们也构成本项目的基线：如果两个 Demo 只有普通 RAG 效果，而不能展示统一协议和权限治理，就不足以证明平台价值。

## 3. 技术路线取舍

### 3.1 比赛 MVP 必选

| 能力 | 方案 | 原因 |
|---|---|---|
| Gateway 与 API | Python、FastAPI、Pydantic | 团队统一语言，自动生成 OpenAPI，适合协议原型 |
| Agent 互操作 | MVP 采用 A2A v1.0 的 `HTTP+JSON` binding，候选 A2A Python SDK `v1.1.1` | A2A 还支持其他 binding；本项目只借用标准任务和 Agent Card，不自造协议 |
| 工具边界 | MCP 规范基线为 2025-06-18，候选 MCP Python SDK `v1.28.1`；仅用于真正跨模块复用的 Connector | 规范版本、SDK 包版本和文档分支分别记录，spike 后写入锁文件 |
| 数据与检索 | PostgreSQL + pgvector + PostgreSQL 全文检索 | 一套存储覆盖任务、元数据、权限和小规模混合检索 |
| 前端 | React + Vite 或团队已熟悉的等价方案 | 比 Next.js 更少服务端框架负担；最终以两天内可交付为准 |
| 文件 | 开发期受控本地目录 + 元数据表 | 先满足上传、删除和隔离；不因 MinIO 阻塞主线 |
| 观测 | 结构化 JSON 日志、trace ID、固定调用轨迹页 | 先证明可追踪；保留 OTel instrumentation 接口 |
| 部署 | Docker Compose | 统一三人本地环境和提交复现方式 |

### 3.2 条件采用

| 能力 | 采用条件 | 失败时降级 |
|---|---|---|
| LangGraph | 两天 spike 跑通课程研究状态、恢复和测试，并比普通异步流水线更简单 | 显式 Python pipeline + 数据库 checkpoint |
| Redis | 单进程事件表无法满足 SSE 重连或缓存需要 | PostgreSQL 事件表 + 进程内通知 |
| S3/MinIO | 部署环境需要跨容器文件共享或对象级短期授权 | 受控本地目录，不开放任意 URL |
| OpenTelemetry Collector | 基础 trace 已稳定且有时间制作观测界面 | 结构化日志 + correlation ID |
| 登录增强 Connector | 获得平台授权，且公开模式已稳定、威胁测试通过 | 只演示公开模式或合成授权页面 |
| 重排模型 | 固定评测集证明它显著提高引用正确率 | 全文 + 向量加权融合 |

### 3.3 明确不采用

- 不用 Dify、Coze、Copilot Studio 或 Agentforce 承载核心 Gateway；
- 不同时引入多套内部编排框架和状态模型；
- 不自建 Agent 市场、计费、自动上架和复杂租户后台；
- 不把未声明许可证的校园项目代码复制进仓库；
- 不以“框架数量多”代替可复现的第三方接入和评测结果。

## 4. Build vs Borrow

| 能力 | 决策 | 交付证明 |
|---|---|---|
| A2A / MCP / OpenAPI / OTel 语义 | Borrow | 官方 SDK/规范、兼容测试、锁定版本 |
| Specialist Registry、Gateway 确定性控制和 Main Agent 选择 | Build | 渐进披露、单跳调用、第三方 Specialist 零业务代码改动接入 |
| 校园 skill 契约 | Build | 版本化 Schema 与固定测试任务 |
| 课程证据聚合与可见范围 | Build | 时间、样本、分歧、权限和来源评测 |
| RAG 来源准入与权限过滤 | Build | SQL 级隔离、删除失效、恶意样例测试 |
| Deep Research 流程骨架 | Borrow + 定制 | 参考 Open Deep Research，只保留课程场景需要的节点 |
| UI 组件和图表 | Borrow | 使用成熟组件库，不复制竞品品牌界面 |
| 评测集与演示数据 | Build | 记录来源、许可、版本和人工标注 |

## 5. 对比赛叙事的影响

按照评审维度，本项目应这样证明价值：

| 评审维度 | 需要展示的证据 |
|---|---|
| 创新性 | Personal Main Agent 统一入口、渐进披露、单跳 Specialist 契约，以及权限和证据随任务跨边界传递 |
| 实用性 | 选课调研和课程复习两条完整用户流程，来源可核验，数据可删除 |
| 技术难度 | A2A/MCP 边界、父子任务限深、字段级授权、SSE/轮询恢复、提示注入防护、固定评测 |
| 完成度 | 一键启动、Main + 三 Specialist 固定脚本、失败降级、录屏、文档和可复现指标 |

## 6. 来源

以下链接均于 2026-07-19 访问：

- [A2A Agent Discovery](https://a2a-protocol.org/latest/topics/agent-discovery/)
- [A2A 1.0 Specification](https://a2a-protocol.org/latest/specification/)
- [A2A Life of a Task](https://a2a-protocol.org/latest/topics/life-of-a-task/)
- [A2A Streaming and Async](https://a2a-protocol.org/latest/topics/streaming-and-async/)
- [A2A and MCP](https://a2a-protocol.org/latest/topics/a2a-and-mcp/)
- [MCP Architecture](https://modelcontextprotocol.io/docs/learn/architecture)
- [MCP Authorization 2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization)
- [A2A Python SDK v1.1.1](https://github.com/a2aproject/a2a-python/releases/tag/v1.1.1)
- [MCP Python SDK v1.28.1](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v1.28.1)
- [OpenAPI 3.1.1](https://spec.openapis.org/oas/v3.1.1.html)
- [JSON Schema 2020-12](https://json-schema.org/draft/2020-12)
- [OpenTelemetry Documentation](https://opentelemetry.io/docs/)
- [LangGraph Overview](https://docs.langchain.com/oss/python/langgraph/overview)
- [Open Deep Research](https://github.com/langchain-ai/open_deep_research)
- [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/)
- [Dify Documentation](https://docs.dify.ai/)
- [Coze Architecture](https://docs.coze.com/guides/architecture)
- [Microsoft Copilot Studio Security and Governance](https://learn.microsoft.com/en-us/microsoft-copilot-studio/security-and-governance)
- [Agentforce Testing Center](https://help.salesforce.com/s/articleView?id=005228642&language=en_US&type=1)
- [MustangsAI](https://github.com/Saimudragada/MustangsAI)
- [UB ISS Chatbot](https://github.com/billodalroy/ub-iss-chatbot)
- [RAG-Based University Assistant Chatbot](https://github.com/SohaibBazaz/RAG-Based-University-Assistant-Chatbot)
