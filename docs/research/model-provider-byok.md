# 多模型接入、BYOK 与本地 Runner 技术调研

> 调研日期：2026-07-19
> 结论用途：支撑个人 Agent Harness 的模型适配、凭据、成本和混合执行设计，不代表相关能力已经实现。

## 1. 结论

平台应把 GPT、DeepSeek、学校模型等视为可替换的模型提供方，由统一 `ModelAdapter` 处理协议差异；不应在课程 Agent 业务流程中散落 provider 判断。个人 Agent 采用 `AgentTemplate + AgentProfile + ModelProfile`，模型 API 只是 Profile 的运行配置，不是 A2A Agent 或 MCP Tool。

比赛 MVP 推荐：

- 先实现一个 OpenAI-compatible adapter，但为每个 provider 维护独立能力快照；
- 只允许管理员维护的 provider 和 `base_url` 白名单，不接受任意用户 URL；
- 平台托管模式只使用比赛额度或团队受控测试密钥；
- 普通用户真实 BYOK 通过只出站的本地 Runner 演示，API key 不离开设备；
- 建立平台自己的 Usage Ledger，不把 provider 账单页当作实时预算控制；
- 不同模型共享公开数据、解析、索引和证据缓存，最终私人回答相互隔离；
- 模型不可用时返回明确错误或请求用户确认，不静默切换 provider、密钥归属或执行位置。

## 2. 一手事实

### 2.1 OpenAI SDK 可作为兼容客户端外壳

[OpenAI Python SDK](https://github.com/openai/openai-python) 支持覆盖 `base_url` 和自定义 HTTP client，也支持 provider 适配扩展。它同时明确建议从环境或安全身份机制加载 API key，而不是写入源码。

这说明统一客户端外壳可行，但只解决请求发送和响应解析的部分差异，不能证明兼容服务在 tools、structured output、reasoning、缓存和 usage 上语义一致。

### 2.2 DeepSeek 提供兼容格式，但存在独立语义

[DeepSeek API 文档](https://api-docs.deepseek.com/) 说明其接口兼容 OpenAI 格式；[Chat Completion 文档](https://api-docs.deepseek.com/api/create-chat-completion/) 单独定义 thinking、reasoning、stream usage 等行为；[模型列表接口](https://api-docs.deepseek.com/api/list-models/) 可返回当前可用模型；[Context Caching 文档](https://api-docs.deepseek.com/guides/kv_cache) 说明缓存命中具有独立计费与 usage 语义。

因此“OpenAI-compatible”只能作为 adapter 类型，不能作为完整能力声明。

### 2.3 兼容层不是原生能力等价证明

[Anthropic OpenAI SDK compatibility](https://docs.anthropic.com/en/api/openai-sdk) 同样把 OpenAI SDK 支持描述为兼容层。不同 provider 可能接受同一字段，却忽略、转换或拒绝部分参数。Harness 必须根据 capability snapshot 决定模板是否可运行。

### 2.4 A2A 与 MCP 不承担模型 provider 抽象

[A2A 与 MCP 的官方边界说明](https://a2a-protocol.org/latest/topics/a2a-and-mcp/) 将 A2A 定位为 Agent-to-Agent，将 MCP 定位为 Agent-to-tool/data。模型调用属于 Agent 运行时内部能力：

- 外部独立 Agent 仍通过 A2A 被 Gateway 调度；
- Agent 内部通过 MCP 使用课程搜索、文件、检索等工具；
- ModelAdapter 直接调用已批准 provider，不发布虚假的 Agent Card，也不把模型伪装成 MCP Tool。

### 2.5 长任务适合出站 Runner

[A2A 流式与异步任务文档](https://a2a-protocol.org/latest/topics/streaming-and-async/) 支持 SSE、轮询和异步生命周期，但平台无法直接访问用户电脑上的 `localhost`。个人本地执行应采用 Runner 主动连接控制面的 worker 模式：设备配对后长轮询或订阅任务，通过短租约认领和提交结果，不向公网开放用户端口。

## 3. 推荐抽象

### 3.1 ModelProfile

ModelProfile 至少包含：

- owner；
- provider 与 model；
- `platform_sponsored`、`managed_byok` 或 `local_runner` 执行模式；
- `secret_ref`，不能保存密钥明文；
- capability snapshot；
- budget policy；
- 状态、撤销和轮换时间。

Profile 引用模型配置，不复制密钥。外部 A2A payload 只收到业务所需的最小上下文，不收到 ModelProfile 或 `secret_ref`。

### 3.2 CapabilitySnapshot

每个 provider/model 需要独立记录：

- streaming；
- tools/function calling；
- structured output/JSON；
- vision；
- reasoning/thinking；
- context window；
- usage、缓存命中和已知计费字段；
- 探测时间、adapter 版本和不兼容参数。

模型列表接口只能证明模型被 provider 列出，不能代替真实能力探测。比赛前需以固定小请求验证模板真正依赖的能力。

### 3.3 Usage Ledger

每次模型调用独立记录 `task_id`、用户、provider、model、执行模式、输入/输出 token、provider cache hit、估算费用和错误码。费用信息未知或可能过期时只展示 token，不伪造精确价格。

预算至少限制每任务 token、每日额度、并发、RPM/TPM、重试次数和工具步数。平台账本用于即时控制，provider 后台账单用于对账。

## 4. 缓存与多模型关系

缓存应按数据处理阶段分层，而不是笼统称为“回答缓存”：

1. 公开来源快照；
2. 解析、OCR、chunk、embedding 与索引；
3. 证据与检索结果；
4. 经过质量门槛的公共 `AnswerArtifact`；
5. 用户私人生成结果。

不同模型可以共享第 1 至 3 层。第 4 层必须展示生成模型、模板、提示和证据版本，用户可以零模型调用直接使用，也可以选择自己的模型重新生成。第 5 层的缓存键必须包含用户、Profile、provider、model、提示、私人数据和权限版本，且不能跨用户命中。

Provider 自身的 prompt/context cache 是供应商内部优化，不能替代平台的证据版本、权限失效和 AnswerArtifact 治理。

## 5. BYOK 安全边界

生产级托管 BYOK 至少要求：

- 独立加密 Secret Store；
- 业务数据库只保存用户拥有的 `secret_ref`；
- API 不回显密钥，日志、trace、错误和 Artifact 不出现密钥；
- provider 与 `base_url` 白名单，默认禁止任意 URL；
- 运行时最小权限解封装，用后释放；
- 撤销或轮换后停止排队、重试和已租赁任务；
- 每用户/provider/key 的预算和并发限制；
- 覆盖数据库导出与前端响应的 DLP 测试。

三人团队不应在比赛周期内收集普通用户的真实托管 key。MVP 用团队受控测试凭据验证 `secret_ref` 生命周期，用本地 Runner 证明真实用户可使用自己的 key。

## 6. 本地 Runner 契约

推荐流程：

1. 用户使用短期配对码绑定设备；
2. Runner 主动连接 Gateway，领取绑定用户、任务和数据 scope 的短租约；
3. Runner 在本地解析 Profile，使用本地 key 调用模型；
4. Runner 只提交事件、usage 和结构化结果；
5. Gateway 把结果当作不可信输入，再做 Schema、权限、大小和安全渲染校验；
6. nonce、序号、过期时间和幂等键阻止跨用户认领、重放和重复提交。

MVP 只支持一个演示设备、一个并发任务和一个 CLI Runner。不实现公网入站端口、任意隧道、多设备同步和后台常驻 Agent。

## 7. 方案取舍

| 方案 | 优点 | 风险 | 结论 |
|---|---|---|---|
| 全部平台托管 | 用户体验简单、随时在线 | 收集真实 key 的安全和运营成本过高 | 仅团队测试凭据 |
| 全部本地运行 | 私密数据和 key 不离开设备 | 在线性、安装和演示复杂 | 作为真实 BYOK 主路径 |
| 平台托管 + 本地 Runner | 公共任务易用，私人任务可控 | 需要两种执行路径和租约协议 | 采用 |
| 每人部署一个公网 Agent | 独立性强 | 注册、SSRF、运维和版本矩阵失控 | 不用于个人 Agent |
| 自动模型 fallback | 表面可用性高 | 费用、数据外发和结果语义不透明 | MVP 禁止 |

## 8. 待 spike 验证

- 选定 SDK 版本下 OpenAI 与 DeepSeek adapter 的 streaming、structured output 和 usage 一致性；
- 学校模型是否真的兼容 OpenAI API，以及允许的数据类型和额度；
- CLI Runner 在断线、租约超时、重复提交和 key 撤销时的行为；
- L3/L4 缓存对延迟和模型调用次数的真实改善；
- provider 数据政策、价格与速率限制，以实施当天官方文档为准。

## 9. 相关文档

- [个人 Agent Harness 与混合模型运行时设计](../designs/04-personal-agent-harness.md)
- [协议调度层设计](../designs/01-agent-gateway.md)
- [平台威胁模型](../security/threat-model.md)
- [ADR-0003：采用模板化个人 Agent 与混合模型运行时](../decisions/0003-hybrid-personal-agent-runtime.md)
