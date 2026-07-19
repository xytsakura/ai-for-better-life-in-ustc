# Personal Main Agent 与 Specialist 单跳编排设计规格

## 1. 状态

- 状态：已获队长批准，三路独立审计与修复复审通过
- 日期：2026-07-19
- 所属版本：`v0.4`
- 详细设计：[Personal Main Agent 与 Specialist 单跳编排设计](../../designs/05-personal-main-agent-orchestration.md)
- 决策记录：[ADR-0004](../../decisions/0004-personal-main-agent-single-hop-orchestration.md)

## 2. 产品命题

用户只与 Personal Main Agent 交互。平台将已验收的 Specialist 以渐进式能力摘要披露给 Main Agent；Main Agent 发现当前问题适合某项专业能力时，编写 query 和字段级个人上下文授权，通过确定性 Gateway 创建一个 depth=1 child task。Specialist 使用领域 pipeline、Harness、知识库和公共 QA 完成工作，返回可审计 Artifact；最终回答仍由 Main Agent 生成。

## 3. 硬性架构约束

1. Personal Main Agent 是普通用户唯一入口和最终回答者；
2. Gateway 是控制面，不进行自由推理和自然语言综合；
3. 只有 accepted + enabled Specialist 可以被披露和调用；
4. 每个用户回合最多一个 child task，`depth=1`；
5. Specialist `can_delegate=false`，没有 `create_child_task` 或 `specialist.invoke` 权限；
6. Main Agent 不直接访问 A2A endpoint，Specialist 不被包装成 MCP Tool；
7. 第三方 Specialist 不接收 ModelProfile、API key、Cookie、完整 Profile、完整会话或私人记忆正文；
8. 所有 SpecialistArtifact 先经 Gateway 校验，再进入 Main Agent；
9. 最终 MainAnswerArtifact 保留引用、数据范围、限制、缓存和 child lineage；
10. 不允许静默 Agent/model/provider/key/runtime fallback。

## 4. 组件边界

| 组件 | 职责 | 明确不负责 |
|---|---|---|
| Personal Main Agent | 对话、意图、能力选择、query、上下文请求、最终综合 | 直接 endpoint、凭据管理、绕过权限 |
| Specialist Registry | 冻结快照、能力摘要、Schema、健康和验收状态 | 开放市场、未经复审自动更新 |
| Gateway | 披露过滤、授权、任务、A2A、预算、限深、校验和审计 | 自由 planner、最终自然语言回答 |
| Specialist | 领域 pipeline、工具、知识库、公共 QA、专业 Artifact | 继续委派、完整个人档案、最终用户答复 |
| Harness Core | 模型、MCP、缓存、预算、egress、输出校验 | 跨 Agent 自治协商 |

## 5. 渐进式披露

- 第一层 `CatalogSummary`：名称、一句话用途、skill、输出类型、data scope、授权提示、健康和验收版本；
- 第二层 `CapabilityDetail`：输入/输出 Schema、适用边界、允许上下文字段、示例、错误、缓存、延迟和预算；
- 第三层 `GovernanceDetail`：完整 Card、endpoint、认证、安全审计和评测，仅管理员可见；
- 摘要由平台基于冻结快照生成，不把第三方长描述原样注入 Main Agent；
- Card、endpoint、Schema 或权限变化后立即进入 `review_required`，停止新任务披露。

## 6. 单跳任务契约

RootTask 属于 Main Agent。Main 提交的 `SpecialistInvokeRequest` 只包含 Specialist/version/skill、目标、query、期望 Artifact、所需字段/引用、data scopes、预算和截止时间，不包含服务端 task/grant/budget ID。Gateway 原子生成 ChildTaskRecord、ContextGrant、AuthorizedContextBundle 和投递给 Specialist 的 `SpecialistTaskEnvelope`；ChildTask 固定包含 root/parent/child ID、Main caller、`depth=1` 与 `can_delegate=false`。

Gateway 必须验证：

- root owner 与 Main Agent ticket；
- child_count 为 0；
- Specialist accepted version、skill 和 Schema；
- ContextGrant、数据 egress 和预算；
- 幂等键、超时和取消传播；
- child ticket `can_delegate=false`。

任何 self-route、depth=2、fan-out、第二 child 或 Specialist 发起委派都必须拒绝。

## 7. 个人上下文

Main Agent 对个人上下文拥有逻辑访问入口，但物理读取由 Context Broker/Gateway 按需完成。Main 只提出白名单字段、知识引用、purpose、scope 和 egress 意图；Gateway 生成的 ContextGrant/AuthorizedContextBundle 绑定 root/child、attempt、Specialist/version/skill、匿名 subject、policy version、nonce、egress 和 TTL。

MVP 字段白名单：专业、年级、校区、已修课程、工作量偏好和学习目标。私人资料只传经校验的 FileRef/knowledge ref。API key、Cookie、ModelProfile、完整会话和完整记忆不进入 Specialist payload。

## 8. Specialist 与缓存

- 内置 Specialist 复用 Harness Core，但拥有独特领域 pipeline 配置、工具、知识库、业务 Schema、评测集和缓存 namespace；
- 第三方 Specialist 可使用自己的内部实现，只要 A2A、Schema 和 Artifact 验收通过；
- 公共 QA 键包含 specialist、skill、accepted version、question、evidence、prompt 和 policy 版本；
- 私人字段或登录证据参与的结果只能进入用户 L5，不能写公共 QA；
- Specialist 不能直接写长期记忆，只能返回候选。

## 9. 输出闭环

SpecialistArtifact 至少包含 Specialist/version、child task、专业答案、原始证据引用、置信度、限制、data scope、cache level、usage 和时间。

Gateway 校验后，Main Agent 生成 MainAnswerArtifact：最终答案、引用、data scope、最多一个 specialist call lineage、cache level 和时间。Main Agent 可以改善表达，但不能删除关键反例、限制或把低置信结果表述为确定事实。

## 10. MVP 范围

硬 MVP：

- 一个 Personal Main Agent；
- 三个静态白名单 Specialist 摘要，其中两个内置、一个极简外部；
- search、describe、invoke 三个结构化能力，以及 SpecialistInvokeRequest/TaskEnvelope 的双边契约；
- 每回合一个 child、depth=1、无递归；
- ContextGrant、SpecialistArtifact、MainAnswerArtifact；
- A2A 状态、SSE/轮询、取消、追问、超时、幂等与审计；
- 公共证据、公共 QA 和私人最终结果三类关键缓存；
- 固定意图、安全和引用评测。

条件增强：CLI 本地 Runner、完整个人档案 UI、embedding 目录搜索、记忆确认 UI。

明确后置：多个/并行/递归 Specialist、开放市场、自动长期记忆、生产级 BYOK、多设备 Runner、自动 fallback 和计费。

## 11. 验收

- 所有普通用户消息只进入 Main Agent；
- 不需要 Specialist 的固定问题不创建 child；
- 三类固定意图正确选择对应 Specialist，准确率不低于 90%；
- 不必要 Specialist 调用率不高于 10%；
- 未验收 Agent 披露 0 次；
- depth/fan-out/第二 child/递归拒绝率 100%；
- 跨用户 ContextGrant、私人知识和 L5 命中 0 次；
- Specialist 关键结论的引用和限制在 MainAnswerArtifact 中 100% 保留；
- Main 最终答案忠实度不低于 90%；
- 第三方 Specialist 接入时 Gateway 业务代码改动为 0；
- 日志、trace、Artifact 和前端响应中密钥、Cookie、私人值泄露 0 次。

## 12. 文档交付

- 新增 ADR-0004、独立设计和本规格；
- README、总体架构、Gateway、个人 Harness、两个 Specialist、威胁模型和路线图统一为 v0.4；
- v0.3 审计保留为历史，不再代表当前全部范围；
- 独立架构、契约、安全和范围审计通过后才允许推送；
- 当前不创建代码目录，不声称功能已经实现。
