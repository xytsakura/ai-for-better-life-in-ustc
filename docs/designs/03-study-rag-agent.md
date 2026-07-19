# 03 课程资料 RAG Specialist Sub Agent 设计

## 1. 定位

课程资料 RAG Specialist Sub Agent 是校园 Agent 调度层的第二个演示场景。它面向学生的真实复习流程：把课程主页、公开许可或书面授权资料、经确认可入库的课程附件和个人上传材料整理成可引用、可更新、可隔离的知识库，再通过 RAG 问答、资料定位和复习计划提供专业材料。它是平台内置 Specialist，不是用户入口；用户只与 Main Agent 交互，Main Agent 编写专业查询并通过 Gateway 委派，最终用户措辞仍由 Main Agent 完成。仅“账号可以打开”不等于允许上传到服务器或共享索引。

本模块不是独立的大而全网盘，而是统一 Harness 下受 Gateway 约束的课程知识 Specialist。它需要证明四件事：

- Main Agent 可以把用户问题改写为“课程资料检索、解析、引用、复习建议”等专业 query，并通过一级 `ChildTask` 路由到本 Specialist。
- 公共课程知识和个人私有资料可以安全共存，检索时按权限边界合并。
- 回答能够追溯到原始片段，便于学生核验，不把模型生成内容伪装成官方材料。
- Specialist 只能在本领域 pipeline 内使用 MCP、模型 adapter、知识库和缓存，不拥有 `create_child_task` 权限，也不能调用其他 Agent。

## 2. 目标

- 架构预留 PDF、PPTX、DOCX、图片和 OCR 文本；比赛 MVP 固定支持文本型 PDF、PPTX 基础文本和 PNG/JPEG 图片 OCR 三类输入，DOCX 与扫描版 PDF 整体 OCR 延后。
- 建立公共课程知识库、课程共享空间和个人私有空间三层资料域。
- 对资料做来源准入、内容指纹、去重、版本记录和增量更新。
- 支持结构化解析、分块、元数据入库和向量化索引。
- 使用 Postgres、pgvector 和全文索引完成混合检索。
- 支持查询改写、重排、引用片段、缓存和个性化资料合并。
- 提供删除、失效、权限隔离、错误恢复和可观测日志。
- 建立小规模评测集，用引用正确率、检索命中率、答案忠实度和端到端延迟衡量效果。
- 在比赛演示中展示“公共资料 + 个人笔记”共同辅助复习，但不泄露私有资料。
- 只接收 Gateway 校验后的 `depth = 1` `ChildTask`，统一返回 `SpecialistArtifact`，由 Gateway 校验后交回 Main Agent。

## 3. 非目标

- 不自动抓取或再分发未经许可的教材、课件、试卷、作业答案和评课社区受限附件。
- 不承诺覆盖全校所有课程，MVP 只选择 1 到 2 门资料来源清楚、授权边界清楚的课程。
- 不替代课程教师、助教或学校官方通知。
- 不把用户上传资料默认共享给他人。
- 不做完整 LMS、网盘、笔记软件或题库系统。
- 不在早期实现复杂的版权审批流；MVP 使用白名单来源和人工确认。
- 不作为聊天入口、公开 A2A 入口或用户可直接选择的 Agent，不接受根任务、二级及更深子任务。
- 不接收完整 `Profile`、`AgentProfile`、`ModelProfile`、API key、Cookie、完整会话或个人记忆正文，也不自行发现或调用其他 Agent。

## 4. 用户故事

- 作为学生，我可以通过 Main Agent 上传自己的课件、课堂笔记和复习截图；Main Agent 基于 Specialist 的专业材料回答“这章重点是什么”并引用到具体页码或段落。
- 作为学生，我可以让 Main Agent 选择一门课程，只授权 Specialist 检索这门课的公共资料和我自己的私有资料，避免其他课程内容干扰。
- 作为学生，我可以问 Main Agent“期中前需要复习哪些章节”，Main Agent 会基于 Specialist 对课程大纲、课件目录和我的笔记的检索结果给出复习顺序。
- 作为团队演示者，我可以展示公共课程资料被多名用户复用，而个人上传资料只在本人会话里参与检索。
- 作为维护者，我可以看到某个文档的来源、导入状态、解析错误、版本变化和是否已从索引中失效。

## 5. 三层资料空间

### 5.1 公共课程知识库

公共课程知识库保存来源合法、可被所有演示用户访问的课程信息，例如课程官方主页公开说明、教师公开发布且允许访问的课程介绍、公开许可证下的 GitHub 仓库资料。

要求：

- 每个来源记录 URL、访问时间、许可说明或人工确认记录。
- 默认只保存用于演示的少量课程，不做全站镜像。
- 公共库内容只包含允许公开访问和展示的材料。

### 5.2 课程共享空间

课程共享空间面向同一课程内的授权用户，例如团队自建的演示班级或测试小组。它可以存放经许可上传、且明确允许课程内共享的讲义、实验说明、复习提纲或助教资料。

要求：

- 绑定 `course_id` 和 `tenant_id`。
- 访问者必须拥有该课程空间的成员身份。
- 共享资料需要记录上传者、授权范围和失效时间。
- 删除或撤回后必须从检索结果中消失。

### 5.3 个人私有知识库

个人私有知识库只属于上传者本人，例如个人笔记、批注、错题截图、自己整理的复习表。

要求：

- 所有检索必须带 `user_id` 过滤。
- 默认不参与共享，不被管理员外的普通用户看到。
- 与公共资料合并时只在检索结果层临时融合，不改变原空间归属。
- 导出演示日志时必须脱敏或使用模拟用户。
- `AgentProfile` 只在 Main Agent/Harness 侧保存知识空间引用；Profile 必须属于当前用户，且只能绑定该用户有权访问的公共、课程共享和私人空间。Gateway 只向 Specialist 下发经解析、短期有效的 `space_ref`，不下发完整 Profile。
- Profile 删除时撤销其知识空间授权与私人生成缓存，但不自动删除用户文件；文件、记忆和 Profile 分别提供删除入口，已签发的相关 `ContextGrant` 与 `space_ref` 同步失效。

## 6. 来源准入与版权

资料进入系统前必须满足以下任一条件：

- 课程官方主页公开发布，且使用方式限于检索、引用和学习辅助。
- GitHub 仓库具有明确许可，或仓库所有者允许在演示中使用。
- 评课社区或课程平台附件具有明确的入库/处理授权；仅登录可见但未获入库授权的附件只能在用户本地处理，不进入本系统对象、哈希、向量或共享缓存。
- 用户本人上传，且确认其拥有学习使用权，不要求系统对外分发。

禁止事项：

- 不绕过登录、验证码、访问控制或反爬限制。
- 不批量抓取受限平台内容。
- 不把未经许可的教材、课件、真题或答案打包再分发。
- 不在公开演示材料中展示受版权或隐私限制的原文大段内容。

MVP 中建议采用“白名单来源 + 手动导入”的方式：团队先准备 1 到 2 门课程的合规样例资料，并在文档中说明来源和授权边界。

## 7. 文档导入流水线

导入流水线分为七步：

1. 接收上传或登记来源，先写入不可检索的隔离区。
2. 校验来源准入、真实 MIME、扩展名、大小、页数、压缩比、病毒扫描和重复提交。
3. 为允许服务器处理的文件计算内容指纹；登录受限且未获授权附件不进入本流水线。
4. 在无网络、受 CPU/内存/超时限制的解析沙箱中禁用 PDF JavaScript、宏、外链和主动内容，提取文本、页码、标题、图片说明和表格。
5. 对解析文本执行敏感字段检查和不可信内容标记，生成结构化文档对象和分块结果。
6. 通过准入与解析检查后，才写入受控文件区、Postgres 元数据表和 pgvector 向量索引。
7. 产出导入报告，包括成功片段数、失败页、OCR 置信度、隔离原因和可检索状态。

建议将导入实现为异步任务，前端只显示“排队中、解析中、索引中、可检索、失败”五类状态。失败任务可以重试，但重试不应重复创建可见文档。

## 8. 内容指纹、去重与版本

系统只对允许进入服务器存储的资料维护三类指纹：

- 文件指纹：对原始二进制文件计算哈希，用于发现完全相同的上传。
- 文本指纹：对解析后规范化文本计算哈希，用于发现格式不同但内容相同的资料。
- 分块指纹：对每个 chunk 的规范化内容计算哈希，用于增量更新时复用已有向量。

版本策略：

- 同一空间、同一来源、同一文件指纹直接复用，不重复入库。
- 同一来源但内容变化时创建新版本，旧版本标记为 `superseded`。
- 用户可以查询当前版本，也可以在维护页查看历史版本。
- 删除操作先写 tombstone 并立即阻断检索，后台任务在约定保留期内清理文件区、向量、缓存和孤立版本。

## 9. 解析与 OCR

不同文件类型使用统一的解析接口：

- PDF：MVP 提取文本型 PDF 的文本、页码和基础标题结构；禁用 JavaScript、嵌入文件和外链拉取，扫描版 PDF 整体 OCR 延后。
- PPTX：按幻灯片提取标题、正文和图片 OCR 文本；备注、宏、嵌入对象和外部资源不进入 MVP。
- DOCX（MVP 后）：提取标题层级、段落、表格和页内结构。
- 图片：直接 OCR，保留图片尺寸、置信度和版面区域。
- OCR 文本：记录来源文件和 OCR 引擎版本，低置信度片段参与检索但降低权重。

解析结果需要保留 `page_number`、`slide_number`、`section_path` 和 `bbox` 等定位信息，保证回答引用可以回到原文位置。MVP 必须实现文本型 PDF、PPTX 基础文本和 PNG/JPEG 图片 OCR；DOCX、扫描版 PDF 整体 OCR、复杂表格与公式版面解析属于增强项。

## 10. 分块策略

分块遵循“课程结构优先，token 长度兜底”的原则：

- 优先按标题、章节、页码、幻灯片和小节边界切分。
- 单个 chunk 建议控制在 300 到 800 个中文字符或等价 token 范围内。
- 相邻 chunk 保留 10% 到 20% overlap，避免定义和解释被切断。
- 公式、表格和代码块尽量保持完整。
- 对课程大纲、目录、复习提纲这类结构化内容额外生成摘要 chunk。
- 对个人笔记保留较小 chunk，提升“我自己的记录里怎么写的”这类查询命中率。

每个 chunk 必须携带文档 ID、版本、空间、课程、页码、标题路径和权限标签。

## 11. 元数据模型

核心表建议如下：

| 表 | 作用 |
| --- | --- |
| `courses` | 课程基础信息，如课程名、学期、教师、标签 |
| `knowledge_spaces` | 公共、课程共享、个人私有三类空间 |
| `documents` | 文档元数据、来源、授权说明、状态 |
| `document_versions` | 文件指纹、文本指纹、解析器版本、版本状态 |
| `chunks` | 分块文本、定位信息、分块指纹、可见状态 |
| `chunk_embeddings` | chunk 向量和 embedding 模型版本 |
| `access_grants` | 用户、课程、空间之间的访问授权 |
| `ingestion_jobs` | 导入任务状态、错误、重试次数 |
| `source_audits` | 来源准入、授权记录、人工备注 |
| `retrieval_logs` | 查询、命中片段、引用、延迟、反馈 |

关键字段：

- `space_type`：`public`、`course_shared`、`private`。
- `tenant_id`：演示或部署租户，用于隔离不同班级或团队。
- `course_id`：课程粒度过滤。
- `owner_user_id`：私有资料所有者。
- `license_note`：来源许可和使用限制说明。
- `visibility_status`：`active`、`deleted`、`expired`、`superseded`。

## 12. Postgres + pgvector 混合检索

检索层采用 Postgres 统一管理结构化元数据、权限过滤、全文索引和向量索引，减少早期系统复杂度。

流程：

1. 根据用户身份计算可访问空间集合。
2. 用课程、学期、资料类型、状态和权限做硬过滤。
3. 对查询做 PostgreSQL `tsvector`/`tsquery` 全文检索，并用 `ts_rank`/`ts_rank_cd` 排序；只有固定评测证明需要且团队采用相应扩展时才引入 BM25。
4. 对查询生成 embedding，使用 pgvector 做语义近邻检索。
5. 合并关键词和向量结果，按课程匹配、来源可信度、新版本、OCR 置信度和个人资料优先级打分。
6. 送入重排模型或轻量 cross-encoder 重排。
7. 返回 top-k chunk 及引用元数据。

MVP 可以先实现全文检索与向量检索的加权融合，重排模型作为可插拔增强项。

## 13. 查询改写、重排与引用

查询改写用于处理学生自然语言提问，例如“这个知识点”和“上次课讲的那个定理”。改写时只补充课程名、章节名、同义词和用户上下文，不应扩大权限范围。

回答生成规则：

- 每个关键结论至少关联一个引用片段。
- 引用显示课程名、资料名、页码或幻灯片号、空间类型。
- 当证据不足时明确说“不确定”或“当前资料中没有找到”。
- 公共资料和个人资料冲突时，优先说明来源差异，不直接替用户判定官方结论。
- 对可能涉及考试真题、答案流通或版权限制的请求，返回合规提示和可用的复习建议。
- 检索到的文档、OCR 文本和元数据全部是“不可信证据”；其中要求忽略系统提示、读取其他用户资料、泄露密钥或调用工具的文本不得执行。
- 工具调用只由应用代码和已批准策略决定，模型不能依据资料正文动态增加工具、URL 或权限范围。
- 检索出的 `private` chunk 和 `local_authenticated` 证据默认只能交给本地 Runner；完整个人记忆不得进入 `ChildTask`。逐次确认远端 provider 和数据范围后才允许例外，且比赛 MVP 不远端发送登录限定内容。
- Runner 离线或当前模型失败时不得静默切到云模型或平台 key。

## 14. 缓存

本 Specialist 对齐 Harness 的五层缓存：

- L1 来源快照：公开/获授权的文档版本和来源元数据。
- L2 解析与索引：相同文件指纹复用解析结果，相同分块指纹与 embedding 模型版本复用向量。
- L3 证据与检索：规范化公共专业查询到 `EvidenceBundle`；公共或相同共享 scope 可复用，私人查询按用户隔离，并记录 Specialist、skill 与版本。
- L4 公共标准答案：只使用公共证据和不含个人字段的公共 QA，经引用和安全校验后发布统一 `SpecialistArtifact`，记录 Specialist、skill、版本、generator、model、prompt、evidence 与 policy 版本。
- L5 私人生成：结合个人资料、记忆或用户模型的回答，只存在用户私有空间或本地加密缓存。

不同生成模型可以共享不含私人输入的 L1-L3，不能跨用户共享 L5。L4 命中不消耗用户模型额度；Main Agent 请求“用我的模型重新生成”时复用允许共享的 L3，并把 usage 与结果写入本人 L5。用户 BYOK 回答默认不进入 L4。

公共 QA 缓存必须至少按 `specialist_id`、`skill`、`specialist_version`、`evidence_version` 和 `policy_version` 分区，并继续包含模板、提示、generator provider/model 与规范化公共问题。示例键为 `specialist_qa:v1:specialist=study-rag@<specialist_version>:skill=study.answer:question=<public_question_hash>:evidence=<evidence_version>:policy=<policy_version>:template=<template_version>:prompt=<prompt_version>:generator=<provider_id>/<model_id>`。

L5 私人缓存键还必须包含 `subject_id`、`agent_profile_id`、provider、model、私人资料与记忆版本。任何资料删除、失效、授权变化或新版本发布后，相关缓存立即失效。`private`、`local_authenticated`、私人或受限 `FileRef`/`space_ref`、完整或派生的私人 query/evidence/answer/usage 均不得进入或影响 L1-L4，也不得命中公共 QA 缓存；公开 `space_ref` 只能按其公共证据版本参与分区后的公共缓存。

## 15. 个性化资料合并

个性化检索采用“先隔离、后合并”的策略：

1. 公共课程知识库检索得到通用结果。
2. 课程共享空间检索得到班级或小组结果。
3. 个人私有知识库检索得到用户自己的笔记和截图。
4. 在重排阶段按问题意图调整权重，例如“我的笔记”优先私有资料，“课程大纲”优先公共资料。
5. 生成回答时按来源分组引用，避免混淆。
6. Main Agent/Harness 侧 Profile 选择的模型只影响生成步骤，不改变检索 SQL 的权限过滤；Specialist 不接收完整 Profile 或 `ModelProfile`，模型 adapter 不能自行扩大空间范围。

这样既能让用户获得个性化帮助，也能证明系统没有把私有资料写回公共知识库。

## 16. 权限隔离

权限隔离必须在检索 SQL 层完成，不能只靠 Agent 提示词约束。

要求：

- 所有 `documents`、`chunks` 和 `retrieval_logs` 都带空间和用户上下文字段。
- 私有资料查询必须满足 `owner_user_id = current_user_id`。
- 课程共享资料必须满足用户拥有该课程空间授权。
- 公共资料可以被所有演示用户访问，但仍需遵守来源许可。
- 检索日志只保存文档/分块标识、分数和脱敏查询摘要，不保存私有原文、完整用户问题或生成提示词。
- 演示环境使用模拟账号和合规样例资料。
- 管理员读取私有资料必须形成审计事件，记录操作者、原因和时间；默认管理界面不提供私有原文浏览。
- 自动化测试使用两个相互隔离的演示账号验证跨用户检索为零命中。
- 权限过滤在 Gateway 解析 `ContextGrant`、Harness 解析 `ModelProfile` 和模型 egress 之前完成；Specialist 只得到已批准的 `FileRef`/`space_ref` 与执行参数，不得到完整 `ModelProfile`。执行快照记录 `subject_id`、data scope、provider、model、runtime 和 policy 版本，但审计日志不保存私人 chunk、完整问题或记忆正文。
- 用户 A 的 ModelProfile、L5 生成缓存和私人记忆对用户 B 必须零可见，不能只验证文档检索隔离。

## 17. 删除、失效与增量更新

- 用户删除私有文档后，文档、chunk 和向量立即标记为不可见。
- 课程共享资料授权到期后，状态改为 `expired`，不再参与检索。
- 公共来源更新时创建新版本，旧版本保留审计但默认不检索。
- 后台清理任务定期删除软删除对象和孤立向量。
- 导入失败的临时文件设置短期保留时间，避免堆积。
- 删除任务生成 `deletion_receipt`，记录检索阻断时间和异步清理状态；备份或审计副本不得再次进入检索。

增量更新只重新解析和嵌入变化的 chunk，降低比赛演示环境的算力消耗。

## 18. 错误处理

常见错误与处理：

- 非 Gateway 来源、根任务、`depth > 1`、目标 Specialist/skill 不匹配、`ContextGrant` 过期或 `FileRef`/`space_ref` 越权：Gateway 拒绝投递并记录脱敏审计事件，Specialist 不开始执行。
- 输入不足：当专业 query、`expected_artifact_type`/`output_schema_ref`、课程范围或完成任务必需的授权引用缺失时，返回 `status = input_required` 的 `SpecialistArtifact`，在 `questions` 中列出最少需要补充的问题或授权引用；Main Agent 补问后继续同一个 child，不自行读取完整 Profile、记忆或会话补全。
- 文件格式不支持：返回可支持格式列表。
- 文件过大：提示压缩或拆分上传。
- 文件真实 MIME、页数、压缩比或主动内容异常：隔离并返回安全错误，不调用解析器。
- 解析超时、内存超限或沙箱异常：终止任务并清理临时文件，不保留部分索引。
- OCR 置信度低：允许检索，但引用处提示“OCR 可能不准确”。
- 来源未通过准入：拒绝导入，并记录原因。
- embedding 服务失败：任务进入重试队列，不影响已有可检索版本。
- 检索结果为空：返回可尝试的课程范围、关键词和资料上传建议。
- 权限不足：只说明无权访问，不泄露资料是否存在。
- 域内 pipeline、MCP、模型、知识库或缓存发生不可恢复失败：停止后续调用，返回 `status = failed`，`limitations` 给出可公开的失败阶段和可重试性；不得泄露提示词、凭据、私有证据或内部堆栈。
- Gateway 取消父任务或 `ChildTask`：立即传播取消信号，终止排队、解析、索引、重试、MCP 与模型调用，清理临时文件并丢弃未校验的部分答案；在可行时返回 `status = cancelled` 和实际 `usage`，Main Agent 不把取消前草稿当作结论。

## 19. 评测集与指标

MVP 评测集建议手工构造 40 到 60 条问题，覆盖：

- 课程大纲定位。
- 课件定义和概念解释。
- PPT 页码查找。
- 个人笔记补充。
- 公共资料与个人资料同时命中。
- 无答案问题。
- 权限越界问题。
- OCR 噪声问题。

核心指标：

- 检索 Recall@5。
- 引用正确率。
- 答案忠实度。
- 无答案拒答率。
- 权限隔离通过率。
- P95 端到端延迟。
- 导入成功率。

MVP 在固定数据、模型版本和测试环境下采用以下通过阈值：

| 指标 | 通过阈值 |
|---|---:|
| 固定评测集 Recall@5 | 不低于 80% |
| 引用正确率 | 不低于 90% |
| 人工核验的答案忠实度 | 不低于 90% |
| 无答案问题正确拒答率 | 不低于 80% |
| 权限隔离测试 | 100% 通过 |
| 私有资料删除后残留命中 | 0 条，60 秒内完成失效 |
| 已完成索引查询的 P95 响应 | 不高于 15 秒 |
| 三类 MVP 样例文件导入成功率 | 不低于 90% |

比赛演示中优先展示可解释指标，不追求大规模 benchmark。

## 20. 测试

测试分层：

- 单元测试：指纹、分块、元数据过滤、权限判断、引用格式。
- 集成测试：导入文本型 PDF、PPTX 和 PNG/JPEG 图片后可以检索到目标片段。
- 权限测试：用户不能检索到其他用户私有资料。
- 安全测试：伪造 FileRef、恶意 PDF/PPTX、压缩炸弹、外链和解析器超时被隔离。
- 提示注入测试：恶意文档/OCR 指令不能触发工具、扩大 scope、改变系统提示或泄露其他资料。
- 模型 egress 测试：私人 chunk 未经逐次确认不能发送到远端 provider，Runner 离线不发生静默 fallback。
- 缓存隔离测试：公共 L3/L4 可按策略复用，两个用户的 L5、ModelProfile 与私人记忆零交叉命中。
- 调度契约测试：只接受 Gateway 校验后的 `depth = 1` `ChildTask`；用户直连、Main Agent 以外父任务、根任务和二级委派均被拒绝，`create_child_task` 与其他 Agent 调用能力不存在。
- 引用授权测试：RAG Specialist 只接受 Gateway 授权的 `FileRef`/`space_ref`；伪造、过期、跨用户或扩大 scope 的引用为零命中，完整 Profile、`ModelProfile`、API key、Cookie 和完整会话不能进入执行输入或日志。
- 状态测试：分别验证 `input_required`、`failed`、`cancelled` 的 `SpecialistArtifact` 字段完整、scope 不扩大、usage 可核验；补充输入继续原 child，取消后无继续调用或公共缓存写入。
- 出站测试：Gateway 拒绝缺少 `specialist_id`/`specialist_version`、answer、evidence、citations、confidence、limitations、data_scope、cache_level 或 usage 的结果，以及引用越权或含敏感字段的结果。
- 公共 QA 分区测试：缓存按 Specialist、skill、版本、证据与策略分区；加入私人 `FileRef`/`space_ref` 或由私人资料派生的 query 后不得命中或写入公共缓存。
- 回归测试：删除、失效、版本更新后旧内容不再命中。
- 质量测试：固定评测集的引用正确率和无答案拒答率。
- 演示测试：按脚本从上传到问答完整跑通。

每次演示前至少运行导入样例、权限隔离和固定问答集。

## 21. MVP 范围

必须完成：

- 手动导入合规样例资料。
- 公共课程知识库和个人私有知识库隔离。
- 文本型 PDF、PPTX 基础文本和 PNG/JPEG 图片 OCR 三类链路均可演示。
- 内容指纹、重复文件跳过、文档状态。
- Postgres + pgvector 混合检索。
- 带引用的 RAG 回答。
- 删除后不可检索。
- 小规模评测集和演示脚本。
- 冻结 40 到 60 条评测夹具，记录资料版本、许可、人工答案、模型/embedding/提示版本和评审人。
- 通过统一 Harness 在平台/团队测试模型上完成一级 ChildTask 并返回统一 SpecialistArtifact；若本地 Runner 条件增强已启用，再用同一契约做双运行时对比，不能让 Runner 阻塞硬 MVP。

可选增强：

- DOCX 完整解析。
- 扫描版 PDF OCR。
- 重排模型。
- 课程共享空间 UI。
- 自动检测课程主页更新。
- 更细粒度的引用定位截图。
- 登录受限附件本地 Connector；只有获得明确授权后才评估是否进入个人本地索引。

## 22. 演示脚本

建议 5 分钟演示流程：

1. 选择“数据结构”或另一门合规样例课程，展示公共课程资料已入库。
2. 使用学生 A 通过 Main Agent 上传个人复习笔记或截图；Gateway 生成授权 `FileRef`，Main Agent 编写专业 query、expected artifact 与最小 `ContextGrant`，只投递 `depth = 1` `ChildTask`。
3. Specialist 在域内调用导入 MCP，任务从“解析中”变为“可检索”；演示用户直连、伪造引用和二级委派被拒绝。
4. 向 Main Agent 提问“这门课期中前需要复习哪些章节”，Specialist 只根据授权 `space_ref` 返回带课程大纲和课件引用的 `SpecialistArtifact`，Gateway 校验后由 Main Agent 组织最终答案。
5. 提问“我的笔记里提到的容易混淆点是什么”，Specialist 只引用学生 A 获授权的私有资料，并展示 confidence、limitations、data_scope、cache_level 与 usage；私人输入不命中公共 QA 缓存。
6. 切换学生 B，重复同一问题，证明无法看到学生 A 的私有资料。
7. 删除学生 A 的私有资料，再次提问，证明授权引用、索引和相关私人缓存失效。
8. 演示输入不足，以及一次取消或可控失败：Gateway 收到 `input_required`、`cancelled` 或 `failed` Artifact；前者由 Main Agent 补问并继续原 child，后两者说明失败且不展示部分草稿。

演示材料必须使用合规样例资料；仅涉及具有明确演示和处理授权的真实课程附件时才展示必要片段和引用，且不公开分发原文件。

演示验收要求：任务链路只有 `Main Agent -> Gateway -> 课程资料 RAG Specialist -> Gateway -> Main Agent` 一跳；Gateway 入站与出站校验均可见；Specialist 无用户入口、无 `create_child_task`、无其他 Agent 调用；成功、输入不足、失败和取消四态均能由 Main Agent 正确收束。原有第 19 节量化指标继续作为检索、引用、隔离与性能验收阈值。

## 23. 与调度层的接口

课程资料 RAG Specialist 只登记在平台内置 Specialist Registry，不出现在用户 Agent 列表或公开 Agent Card 中。Main Agent 可请求 `study.ingest`、`study.ingestion_status`、`study.search`、`study.answer`、`study.delete` 或 `study.list_sources` 等 skill，但 Gateway 必须把请求封装为统一一级 `ChildTask`；这些 skill 是同一 Specialist 域内 pipeline 入口，不是可独立发现的 Agent，也不能继续创建子任务。

### 23.1 入站 `SpecialistTaskEnvelope`

Main 到 Gateway 使用 `SpecialistInvokeRequest`；Gateway 分配 child/Grant/Bundle 并校验后才生成 `SpecialistTaskEnvelope`。Gateway 只在父任务属于 Main Agent、`depth` 精确为 `1`、目标 `specialist_id`/`skill_id` 匹配、预算与 `ContextGrant` 有效且任务未取消时投递。文件不作为 A2A raw part 直接传输，RAG Specialist 只接收 Gateway 已解析并授权的 `FileRef`/`space_ref`；前端不能直连 Specialist，也不能用客户端自报的 `owner_scope`、MIME、大小或空间权限扩大范围。

Main Agent 必须提供专业 `query`、`expected_artifact_type` 和 `output_schema_ref`。Gateway 生成的 `ContextGrant` 必须短期、可撤销，并绑定 `child_task_id`、Specialist/version/skill、主体、tenant、允许的 `FileRef`/`space_ref`、数据范围和用途。ChildTask 不得携带完整 `Profile`、`AgentProfile`、`ModelProfile`、API key、Cookie、CSRF token、完整会话、个人记忆正文或未经授权的原始文件内容。

示例：

```json
{
  "schema_version": "1.0",
  "child_task_id": "task_child_example",
  "root_task_id": "task_example",
  "parent_task_id": "task_example",
  "attempt": 1,
  "depth": 1,
  "specialist_id": "study-rag",
  "specialist_version": "0.4.0",
  "skill_id": "study.answer",
  "query": "根据课程大纲和我的已授权笔记，定位期中前应复习的章节并逐项给出页码证据",
  "expected_artifact_type": "study_rag_material",
  "output_schema_ref": "schema://specialists/study-rag/material/1.0",
  "authorized_context": {
    "bundle_id": "bundle_example",
    "context_grant_id": "grant_example",
    "root_task_id": "task_example",
    "child_task_id": "task_child_example",
    "attempt": 1,
    "specialist_id": "study-rag",
    "specialist_version": "0.4.0",
    "skill_id": "study.answer",
    "anonymous_subject_ref": "anon_example",
    "profile_fields": {},
    "knowledge_refs": ["file_ref_note_example", "space_ref_course_public", "space_ref_user_private"],
    "data_scope": ["public", "private"],
    "purpose": "基于已授权资料生成期中复习材料",
    "egress_policy": {
      "execution_location": "platform",
      "allowed_provider_ids": ["platform-demo-provider"],
      "requires_confirmation": true,
      "confirmation_ref": "confirm_example"
    },
    "policy_version": 1,
    "nonce": "nonce_example_1234567890",
    "issued_at": "2026-07-19T00:00:00Z",
    "expires_at": "2026-07-19T00:05:00Z"
  },
  "deadline_at": "2026-07-19T00:05:00Z"
}
```

`FileRef`/`space_ref` 都是不透明引用。Gateway 用当前主体解析服务端记录并签发短期、单对象/单空间、单 Specialist、单 skill 授权；Specialist 在每次检索、导入、删除或列举来源前重新校验 grant 与引用状态。引用过期、撤回、跨用户、跨 tenant 或超出 `expected_artifact_type`/`output_schema_ref` 用途时必须零命中。

### 23.2 出站 `SpecialistArtifact`

所有 skill 和所有状态统一返回 `SpecialistArtifact`。其中 `status` 取 `succeeded`、`input_required`、`failed` 或 `cancelled`；`input_required` 由 Main Agent 向用户补问并继续同一个 child。导入回执、检索结果、删除回执等都放在 `answer` 的结构化字段中，不再返回另一套顶层 Artifact。即使失败或取消，也保留已知 scope、限制和实际 usage，但不得返回未通过校验的部分答案：

```json
{
  "artifact_id": "artifact_example",
  "artifact_type": "study_rag_material",
  "schema_version": "1.0",
  "child_task_id": "task_child_example",
  "status": "succeeded",
  "specialist_id": "study-rag",
  "specialist_version": "0.4.0",
  "skill_id": "study.answer",
  "answer": {
    "summary": "根据已授权资料生成的专业材料。",
    "study_sequence": []
  },
  "evidence": [],
  "citations": [
    {
      "document_id": "document_example",
      "page_number": 12,
      "space": "private"
    }
  ],
  "confidence": "medium",
  "limitations": [],
  "data_scope": ["public", "private"],
  "cache_level": "L5",
  "usage": {
    "model_input_tokens": 0,
    "model_output_tokens": 0,
    "mcp_calls": 1,
    "cache_hits": 0
  },
  "generated_at": "2026-07-19T00:04:30Z"
}
```

Gateway 对出站 Artifact 做 Schema、Specialist/skill/version、引用归属、scope、敏感字段、缓存层级、usage 和状态一致性校验，通过后只交回发起委派的 Main Agent。Main Agent 结合用户上下文、其他材料和当前交互状态完成最终用户措辞；Specialist 不直接发送用户消息，也不把 `answer` 视为最终定稿。

### 23.3 One-hop 与能力边界

任务链路严格为 `Main Agent -> Gateway -> 课程资料 RAG Specialist -> Gateway -> Main Agent`。Specialist 没有 `create_child_task` 权限，不能调用其他 Agent，也拒绝 `depth = 0` 或 `depth > 1` 的任务。它可以在本领域 pipeline/Harness 内调用策略批准的 MCP 文件/解析/检索工具、模型 adapter、知识库和缓存；这些调用不改变 ChildTask 深度，不产生新 Agent 身份，也不能扩大 `ContextGrant`。

当专业 query、`expected_artifact_type`/`output_schema_ref` 或授权引用不足时，Specialist 返回 `input_required`，由 Main Agent 向用户补问并继续同一个 ChildTask；该续传不计作第二次调用。发生失败或取消时，Gateway 与 Main Agent 按第 18 节处理，不把部分索引、部分回答或取消前草稿作为成功结果。
