# 课程评价 Specialist Sub Agent 设计

## 1. 背景

参赛作品面向中国科大选课场景：用户向平台 Main Agent 描述自己关心的课程、教师、上课负担、给分、先修要求或个人偏好，Main Agent 在确有必要时把问题改写为专业查询，并通过 Gateway 委派给课程评价 Specialist Sub Agent。Specialist 从课程字段、公开点评、登录后用户允许访问的点评与附件中提取证据，返回带来源和时间标注的专业研究材料；它不是用户入口，也不直接完成面向用户的最终措辞。报告不替代学生自己的判断，也不替代评课社区原站内容，而是把分散信息整理成可追溯、可复核的选课研究结论。

评课社区是主要外部来源。公开页面提供课程信息和部分点评，源码显示不同登录身份会影响点评可见范围；登录限定内容必须遵守原站账号、隐私、版权和社区规则边界。产品设计默认把评课社区视为第三方内容平台，不把开源代码许可等同于用户点评或附件内容的转载许可。

## 2. 目标

- 支持 Main Agent 把用户的自然语言问题改写为课程、教师或组合条件的专业查询，例如“计院机器学习哪个老师作业少但收获大”。
- 自动完成课程候选检索、同名课程消歧、教师维度聚合和证据抽取。
- 生成中文 Deep Research 报告，明确列出结论、证据、时间、新旧点评差异、分歧观点和不确定性。
- 汇总课程字段、评分维度、点评观点、课程要求、教师信息、课程主页和允许访问的附件元数据。
- 对课程级摘要建立缓存，并在出现新点评或可见性变化时做增量刷新。
- 在统一 Harness 下使用平台批准的模型、MCP 工具、知识库与缓存，并复用公共证据或经校验的公共标准答案；模型与工具都只是 Specialist 域内流水线能力，不是可继续委派的 Agent。
- 只接收 Gateway 校验后的一级 `ChildTask`，返回统一 `SpecialistArtifact`，由 Main Agent 综合证据并完成最终用户措辞。
- 必须支持公开模式；登录增强模式是获得平台授权且安全测试通过后的条件能力，登录凭据不离开用户设备，不共享队长或任何成员的 Cookie。
- 为演示和评测提供可重复的数据集、指标和错误处理路径。

## 3. 非目标

- 不批量复制、公开再分发评课社区全量点评或附件内容。
- 不绕过登录、CSRF、访问控制、反滥用机制或原站内容过滤。
- 不把 `/course/<id>/reviews/` 当作官方稳定 API。
- 不保存、展示或转发登录 Cookie、CSRF token、会话值、用户邮箱、学号、真实姓名等敏感信息。
- 不做自动选课、代抢课、刷赞、写点评或任何会改变评课社区状态的操作。
- 不在 MVP 阶段训练独立推荐模型；先采用检索、证据聚合、规则约束和大模型报告生成。
- 不作为聊天入口、公开 A2A 入口或可由用户直接选择的 Agent；不接受根任务、二级及更深子任务。
- 不拥有 `create_child_task` 权限，不调用其他 Agent，也不把 MCP 工具、模型 adapter、知识库或缓存包装成下一级 Agent 调用。

## 4. 用户故事

1. 作为正在选课的学生，我向 Main Agent 提问“算法基础 A 和 B 哪个更适合数学基础一般的人”，希望 Main Agent 能基于 Specialist 返回的课程难度、作业量、收获、给分和近几年点评变化给出答复。
2. 作为对教师风格敏感的学生，我向 Main Agent 提问“某老师的线代课适合自学能力弱的人吗”，希望系统区分同名课程、教师和学期差异。
3. 作为队伍演示者，我希望系统能展示公开模式与登录增强模式的差别，但不暴露任何 Cookie 或登录专属原文。
4. 作为内容维护者，我希望每条结论都有来源链接、采集时间和可见范围说明，便于排查幻觉或过期信息。
5. 作为隐私关注者，我希望系统只给摘要和证据定位，不把用户长点评或附件全文复制到报告中。

## 5. 输入输出

### 5.1 输入

- 只接收 Main Agent 编写、Gateway 校验并签发的 `depth = 1` `ChildTask`，不接收用户原始消息或前端直连请求。
- `query`：完成消歧、检索和证据组织所需的专业查询，不包含完整会话历史。
- `expected_artifact_type` 与 `output_schema_ref`：Main Agent 指定的材料目标、必需维度和期望引用粒度，不能要求 Specialist 直接向用户定稿。
- `ContextGrant`：短期、可撤销、绑定 `child_task_id`、Specialist、skill、主体与数据范围的授权；Gateway 在执行前解析并校验，Specialist 不信任调用方自报的 scope。
- 允许的最小上下文字段仅限课程筛选，以及用户明确同意提供的专业/院系、年级、已修课程引用、课程基础偏好和工作量偏好；字段缺失时不从 Profile、记忆或完整会话自行补全。
- 访问模式仅为公开模式或登录增强模式。后者只传本地 Connector 的不透明授权/证据引用及允许的脱敏字段，服务器端和 ChildTask 都不接收 Cookie。
- 禁止输入完整 `Profile`、`AgentProfile`、`ModelProfile`、API key、Cookie、CSRF token、完整会话、个人记忆正文，以及与专业查询无关的身份字段。

### 5.2 输出

- 统一返回 `SpecialistArtifact`，由 Gateway 做 Schema、引用、scope、敏感字段和状态校验后交回 Main Agent；Specialist 不直接向用户发送消息。
- `answer` 中可包含课程候选和消歧说明，以及摘要结论、适合/不适合人群、课程负担、评分与给分、学习收获、教师风格、课程要求、资料可用性、风险和建议等专业材料。
- `evidence` 与 `citations` 记录来源页面、引用位置、可见范围、采集时间、点评发布时间或更新时间。
- `limitations` 记录样本数量、时间分布、极端评价、匿名/仅学生可见内容限制、可见性差异、未验证事实和建议补充的输入。
- 附件信息只能作为 `answer`/`evidence` 中的清单字段展示文件名、类型、来源页面、是否需要登录和摘要状态；不公开镜像下载链接，不上传附件全文到公共服务。

### 5.3 与 Gateway 的最小契约

课程评价 Specialist 只登记在平台内置 Specialist Registry，不出现在用户 Agent 列表或公开 Agent Card 中。Main 到 Gateway 使用 `SpecialistInvokeRequest`；Gateway 分配 child/Grant/Bundle 并完成校验后，才向 Specialist 投递下面的 `SpecialistTaskEnvelope`。Gateway 只在父任务属于 Main Agent、`depth` 精确为 `1`、目标 `specialist_id`/`skill_id` 匹配、预算与 `ContextGrant` 有效且未取消时投递：

```json
{
  "schema_version": "1.0",
  "child_task_id": "task_child_example",
  "root_task_id": "task_example",
  "parent_task_id": "task_example",
  "attempt": 1,
  "depth": 1,
  "specialist_id": "course-research",
  "specialist_version": "0.4.0",
  "skill_id": "course.research",
  "query": "比较目标课程在 2024 秋至 2026 春的工作量和学习收获，并区分教师差异",
  "expected_artifact_type": "course_research_material",
  "output_schema_ref": "schema://specialists/course-research/material/1.0",
  "authorized_context": {
    "bundle_id": "bundle_example",
    "context_grant_id": "grant_example",
    "root_task_id": "task_example",
    "child_task_id": "task_child_example",
    "attempt": 1,
    "specialist_id": "course-research",
    "specialist_version": "0.4.0",
    "skill_id": "course.research",
    "anonymous_subject_ref": "anon_example",
    "data_scope": ["public"],
    "profile_fields": {
      "major": "计算机类",
      "grade": "本科二年级",
      "completed_courses": ["course_ref_linear_algebra"],
      "workload_preference": "中等"
    },
    "knowledge_refs": [],
    "purpose": "按用户偏好比较课程工作量与学习收获",
    "egress_policy": {
      "execution_location": "platform",
      "allowed_provider_ids": ["platform-demo-provider"],
      "requires_confirmation": false,
      "confirmation_ref": null
    },
    "policy_version": 1,
    "nonce": "nonce_example_1234567890",
    "issued_at": "2026-07-19T00:00:00Z",
    "expires_at": "2026-07-19T00:05:00Z"
  },
  "deadline_at": "2026-07-19T00:05:00Z"
}
```

所有状态统一返回 `SpecialistArtifact`。其中 `status` 取 `succeeded`、`input_required`、`failed` 或 `cancelled`；`input_required` 通过问题列表让 Main Agent 向用户补问，并继续同一个 child。即使失败或取消，也保留已知 scope、限制和实际 usage，但不得返回未通过校验的部分答案：

```json
{
  "artifact_id": "artifact_example",
  "artifact_type": "course_research_material",
  "schema_version": "1.0",
  "child_task_id": "task_child_example",
  "status": "succeeded",
  "specialist_id": "course-research",
  "specialist_version": "0.4.0",
  "skill_id": "course.research",
  "answer": {
    "candidates": [],
    "summary": "基于当前可见来源生成的专业摘要。",
    "dimensions": {}
  },
  "evidence": [],
  "citations": [],
  "confidence": "medium",
  "limitations": [],
  "data_scope": ["public"],
  "cache_level": "L3",
  "usage": {
    "model_input_tokens": 0,
    "model_output_tokens": 0,
    "mcp_calls": 0,
    "cache_hits": 1
  },
  "generated_at": "2026-07-19T00:04:30Z"
}
```

该契约是严格 one-hop：Specialist 没有 `create_child_task` 权限，不能调用其他 Agent，也不能接受 `depth = 0` 或 `depth > 1` 的任务。它只能在本领域 pipeline/Harness 内调用策略批准的 MCP、模型 adapter、知识库和缓存。Gateway 校验 `SpecialistArtifact` 后交回原 Main Agent，由 Main Agent 结合用户上下文、其他材料和交互状态决定最终措辞；`access_mode = local_authenticated` 仅表示本地 Connector 已生成获授权引用，不表示 Gateway 或 Specialist 获得用户 Cookie。

## 6. 课程消歧

自然语言查询先进入候选召回，再进行消歧排序。候选来源包括课程名、课程号、教师名、院系、课程类型和点评内容搜索。系统应把“同名课程不同教师”“同一课程不同学期”“课程号前缀相同但课程不同”分开呈现。

消歧排序使用以下信号：

- 精确匹配优先于模糊匹配：课程号、完整课程名、教师名完全匹配权重最高。
- 教师与课程组合优先：用户同时提到教师和课程时，必须优先返回两者共同匹配的课程。
- 课程活跃度和可信度：点评数、最近更新时间、开课学期覆盖范围作为辅助信号。
- 用户上下文：若用户指定院系、课程类型或学期，只在候选解释中保留被过滤掉的近似结果，不混入主报告。

当最高候选无法明显胜出时，系统返回 2 到 5 个候选并请用户确认；演示模式可默认选择“评分/点评数/匹配度综合最高”的候选，同时在报告开头标注“自动消歧”。

## 7. 采集与标准化

### 7.1 数据源

- 评课社区公开课程页和搜索页。
- 登录增强模式下用户本地浏览器可见的课程页、点评和附件链接。
- 课程主页、教师主页、培养方案页面等由课程页显式链接到的公开资源。
- 队伍自建的授权缓存和演示数据集。

### 7.2 标准化字段

课程实体字段：

- `course_id`：评课社区课程页面 ID。
- `name`、`teachers`、`department`、`course_number`、`terms`。
- `course_type`、`join_type`、`teaching_type`、`course_level`、`credit`。
- `homepage`、`introduction`、`admin_announcement`、`source_url`、`fetched_at`。

点评实体字段：

- `review_id`、`course_id`、`term`、`rate`。
- `difficulty`、`homework`、`grading`、`gain`。
- `content_excerpt`、`content_summary`、`publish_time`、`update_time`。
- `visibility`：公开、登录用户可见、仅学生可见、作者/管理员可见等。
- `source_url`、`anchor`、`fetched_at`。

附件实体字段：

- `attachment_id`、`course_id`、`file_name`、`file_type`、`source_context`。
- `visibility`、`source_page_url`、`source_anchor`、`fetched_at`、`local_processed`。
- 不记录文件哈希、会话化下载地址、上传者昵称或 Cookie。

### 7.3 采集流程

1. 查询理解：抽取课程名、教师、院系、课程号、时间范围和比较对象。
2. 搜索召回：公开模式由服务器请求公开 HTML 和官方搜索 token；登录增强模式由本地浏览器连接器读取用户当前可见页面。
3. 页面解析：用结构化 HTML 解析器抽取课程字段、评分、点评、时间、标签、附件链接和页面锚点。
4. 去重合并：按课程 ID、点评 ID、附件文件名和来源上下文合并；同一点评更新时保留最新版本和历史更新时间。
5. 内容规整：移除脚本、样式、导航噪声和评论区交互元素；保留必要上下文和来源定位。
6. 不可信证据隔离：点评、HTML、附件文本和 OCR 内容只能作为证据，不能覆盖系统指令、扩大访问范围、改变工具列表或触发新的工具调用。
7. 安全过滤：丢弃 Cookie、CSRF token、邮箱、学号、隐藏表单字段和受限下载 URL；登录增强模式只保留用户逐次授权范围内的摘要和元数据。

## 8. 观点聚类、时间维度与偏差控制

观点聚类按“结论主题”而不是按原文顺序组织。首批主题包括课程难度、作业负担、考试/给分、课堂体验、学习收获、先修要求、资料质量、教师风格和适合人群。

每个主题输出：

- 主流观点：被多条独立点评支持的结论。
- 少数观点：与主流冲突但有明确证据的评价。
- 时间变化：按学期或点评更新时间标注“早期评价”“近两年评价”“最近新增评价”。
- 置信度：基于样本量、时间跨度、点评一致性和来源可见范围给出高/中/低。

偏差控制规则：

- 不把单条高情绪点评直接升级为总体结论。
- 不把旧学期经验直接套用到最新授课教师或课程大纲。
- 点评数过少时优先输出“不确定”，而不是强行推荐。
- 对“给分好坏”“作业多少”等主观维度同时展示评分统计和文本证据。
- 登录增强模式生成的结论必须标注“包含用户本地登录可见信息”，公开分享时自动降级为公开证据版本。

## 9. 来源引用

报告中的每个事实级结论都必须能追溯到来源：

- 课程字段引用课程页 URL 和采集时间。
- 点评观点引用课程页、点评锚点、点评发布时间/更新时间和可见范围。
- 附件引用所在课程页和文件类型，不公开可会话化的真实下载地址。
- 源码或技术边界引用评课社区 GitHub 文件或官方页面。

引用格式示例：

```text
来源：评课社区课程页 /course/<id>/，点评 #review-<id>，公开可见，发布于 YYYY-MM-DD，采集于 YYYY-MM-DD HH:mm。
```

报告末尾必须列出“未能验证的信息”，例如课程主页失效、附件未授权读取、搜索结果需要用户进一步确认。

## 10. 缓存键与增量更新

### 10.1 缓存层级

本 Specialist 使用 Harness 的五层缓存语义：

- L1 来源快照：自然语言查询到课程候选、公开课程字段和公开页面版本；搜索 TTL 5 到 15 分钟，其余公开快照无额外授权时最长 24 小时。
- L2 解析与索引：清洗后的允许字段、短证据、内容版本和结构化索引，不持久化原始 HTML 或完整点评正文。
- L3 证据与检索：规范化公共专业查询到 `EvidenceBundle`，记录 Specialist、skill、版本、课程、教师、可见范围、查询和检索策略版本；含私人输入的查询只能进入主体隔离的私有层。
- L4 公共标准答案：只基于公共证据和不含个人字段的公共 QA，经引用、事实性和安全校验后保存 `SpecialistArtifact`，记录 Specialist、skill、版本、模板、提示、生成模型、证据与策略版本和时间。
- L5 私人生成：结合用户个人偏好、登录证据或个人记忆的报告，只进入用户私有空间或本地加密缓存。

不同模型可以共享不含私人输入的 L1-L3。L4 命中时不调用用户模型，并明确记录生成模型与时间；Main Agent 请求使用用户模型重新生成时复用允许共享的 L3，把新回答和 usage 写入本人 L5。用户 BYOK 结果默认不能晋升为 L4。专业/院系、年级、已修课程、工作量偏好、登录增强证据和任何完整或派生的私人查询都不得写入 L1-L4，也不得影响公共 QA 缓存键或公共命中结果。

### 10.2 缓存键

公开证据缓存键：

```text
course_evidence:v1:public:icourse:<course_id>:data=<data_version>:retriever=<retriever_version>:policy=<policy_version>
```

公共 QA 缓存必须至少按 `specialist_id`、`skill`、`specialist_version`、`evidence_version` 和 `policy_version` 分区；模板、提示、模型和规范化公共问题也继续参与键生成：

```text
specialist_qa:v1:specialist=course-research@<specialist_version>:skill=course.research:question=<public_question_hash>:evidence=<evidence_version>:policy=<policy_version>:template=<template_version>:prompt=<prompt_version>:generator=<provider_id>/<model_id>
```

私人生成缓存键至少包含 `subject_id`、`agent_profile_id`、`provider_id`、`model_id`、`private_data_version`、`prompt_version` 和 `policy_version`。`local_scope_id` 是每次本地授权随机生成的不透明标识，不由 Cookie、用户名、邮箱或学号哈希得到。附件处理、登录增强生成、用户偏好和任何由私人输入派生的 query、evidence、answer 与 usage 必须绑定主体、本地设备和授权版本，只进入 L5 或本地加密缓存，不进入 L1-L4 或公共共享缓存。

### 10.3 增量刷新

增量刷新以“课程页最后采集时间、点评更新时间、点评数量、附件列表元数据变化”为触发信号：

1. 搜索命中课程时先读取缓存摘要。
2. 后台轻量抓取课程页元数据，比较点评数量、最新更新时间和附件元数据。
3. 若无变化，直接返回缓存并标注缓存时间。
4. 若有新点评，只对新增或变化点评做解析、聚类和摘要合并。
5. 若点评可见范围变化，重新计算对应可见范围的摘要，不污染公开缓存。
6. 若点评被删除、隐藏、屏蔽或从公开变为受限，立即在对应快照写入 tombstone，并从摘要、证据表和搜索索引移除；不能只处理新增内容。
7. 若附件变化，只更新附件元数据；附件内容摘要需用户本地授权后再处理。
8. Template、Prompt、Model capability 或 Policy 版本变化时按所在层精确失效；更换用户模型不应强制重建 L1-L3。

缓存不是内容所有权。来源页面消失、维护者提出删除、授权到期或可见范围收紧时，即使 TTL 未到也必须立即失效并重建；所有缓存结果展示来源和采集时间。

## 11. 公开/登录双模式

### 11.1 公开模式

公开模式由服务端访问评课社区公开 HTML 和官方搜索 token 流程。该模式不需要用户登录，也不接触 Cookie。适合演示、普通查询和可分享报告。

公开模式限制：

- 只能看到公开课程字段和公开点评。
- 不能读取仅登录学生可见、作者可见或管理员可见内容。
- 附件只显示公开页面可见元数据；需要登录的文件不下载、不镜像。

### 11.2 登录增强模式

登录增强模式优先使用用户本地浏览器连接器。用户在自己的浏览器中登录评课社区，连接器只把当前授权页面的结构化摘要交给本地 Runner 中的 Specialist 域内 pipeline，Cookie 不离开设备。该模式不是比赛 MVP 的硬依赖，无明确授权时不对真实登录页面做公开演示。

登录增强模式规则：

- 不要求用户把 Cookie 粘贴给系统。
- 不在服务器保存、转发、打印或展示 Cookie 与 CSRF token。
- 不共享队长 Cookie 给队友或服务端。
- Connector 固定允许 `https://icourse.club/`，默认只读当前页面 DOM，不跟随跨站跳转、不提交表单、不改变原站状态。
- 每次读取前显示页面、字段、附件和输出范围；剥离请求头、隐藏字段、用户名和 `/uploads/files/...` 真实地址。
- 报告默认仅供本地使用；若用户导出可分享版本，系统必须重新生成公开模式报告。
- 对附件内容处理采用本地解析优先；使用远端模型前必须明确告知哪些脱敏片段将离开设备并再次确认，默认不上传登录受限内容。
- 登录增强证据使用短期本地引用，会话结束可一键清除，不进入 Gateway 公共事件、公共缓存或共享 RAG。
- 登录增强、私人偏好和个人记忆默认由本地 Runner 处理。不能因 Runner 离线或模型失败静默切换到平台或第三方云模型。

## 12. 隐私、版权与授权

- 评课社区代码采用 AGPLv3，不代表点评、回复、头像、附件、课件或上传材料自动获得开源许可。
- 不批量复制或公开再分发全量用户内容；默认保留摘要、短证据片段、来源链接和时间。
- 附件可能包含课件、作业、试卷或教师/学生材料，必须按最小必要原则处理。
- 产品应联系 `service@icourse.club`，争取只读 API、数据使用授权、速率限制建议和演示许可。
- 对可识别用户信息做最小化处理：报告不展示用户名、个人主页、邮箱、学号、点赞用户列表或上传者身份。
- 对不当内容、隐私泄露或版权材料保留投诉/下架路径，并在缓存中支持快速删除。
- `robots.txt` 在 2026-07-19 仅解释 content signals 语义，未观察到对 search、AI input 或 AI training 的明确许可值；这既不是禁止结论，也不构成授权，仍以站点规则和维护者书面回复为准。

## 13. 错误处理

- 非 Gateway 来源、根任务、`depth > 1`、目标 Specialist/skill 不匹配、`ContextGrant` 过期或 scope 越界：Gateway 拒绝投递并记录脱敏审计事件，Specialist 不开始执行。
- 输入不足：当课程/教师无法消歧、`expected_artifact_type`/`output_schema_ref` 不明确或完成任务所需的最小字段缺失时，返回 `status = input_required` 的 `SpecialistArtifact`，在 `questions` 中列出最少需要补充的问题；Main Agent 向用户询问后继续同一个 child，不自行读取完整 Profile、记忆或会话补全。
- 搜索 token 失败：向 Main Agent 返回稍后重试、课程 URL 直接解析或手动输入课程链接等可选恢复路径。
- 搜索结果过多：返回候选与 `input_required`，由 Main Agent 请求用户选择并继续同一个 child，不生成混淆报告。
- 页面结构变化：返回“解析失败但页面可打开”，记录选择器失败点，保留来源 URL。
- 登录态失效：只通过 Main Agent 提示用户在本地浏览器重新登录，不要求粘贴 Cookie。
- 权限不足：标注“未授权访问”，不尝试绕过。
- 附件无法解析：保留文件名、类型和来源，正文摘要标记为未处理。
- 内容冲突：把冲突结论列入“分歧观点”，不强行合并成单一判断。
- 速率限制或站点不可用：指数退避，使用旧缓存并明确缓存时间。
- 域内 pipeline、MCP、模型或缓存发生不可恢复失败：停止后续调用，返回 `status = failed`，`limitations` 给出可公开的失败阶段和可重试性；不得泄露提示词、凭据、私有证据或内部堆栈。
- Gateway 取消父任务或 `ChildTask`：立即传播取消信号，终止排队、重试、MCP 与模型调用，丢弃未校验的部分答案；在可行时返回 `status = cancelled` 和实际 `usage`，Main Agent 不把取消前的草稿当作结论。

## 14. 评测集与指标

### 14.1 评测集

构建 30 到 50 条中文查询，覆盖：

- 精确课程名查询。
- 课程号查询。
- 教师名查询。
- 同名课程和多教师消歧。
- 课程比较。
- “作业少”“给分好”“适合零基础”等主观条件查询。
- 近年变化查询。
- 附件和课程要求查询。
- 公开模式与登录增强模式差异。
- 页面缺失、候选过多、样本过少等失败场景。

评测样本只保存来源链接、人工标注答案、必要短摘录和可公开元数据，不保存全量点评或附件。

### 14.2 指标

- 候选召回率：目标课程出现在前 5 个候选的比例。
- 消歧准确率：自动选择正确课程/教师组合的比例。
- 证据覆盖率：报告关键结论中带可追溯来源的比例。
- 忠实度：人工检查报告是否被来源支持。
- 新鲜度：新增点评进入摘要的延迟。
- 偏差处理质量：是否正确标注样本少、时间旧、观点冲突和登录可见范围。
- 合规性：是否泄露 Cookie、用户身份、长篇原文或附件内容。
- 可用性：从输入查询到首屏报告的耗时和失败可恢复率。

MVP 在固定数据、模型版本和测试环境下采用以下通过阈值：

| 指标 | 通过阈值 |
|---|---:|
| 课程候选 Recall@5 | 不低于 95% |
| 自动消歧 Top-1 准确率 | 不低于 85% |
| 关键结论证据覆盖率 | 不低于 95% |
| 人工核验的结论忠实度 | 不低于 90% |
| 登录凭据、用户身份和受限下载地址泄露 | 0 次 |
| 相同公开课程重复查询缓存命中率 | 不低于 90% |
| 缓存命中后的 P95 首屏响应 | 不高于 5 秒 |
| 未缓存公开调研的 P95 完成时间 | 不高于 60 秒 |

## 15. 测试

- 单元测试：查询解析、课程候选归并、HTML 清洗、字段标准化、缓存键生成、可见范围隔离。
- 集成测试：公开搜索 token 获取、公开课程页解析、课程页结构变化降级、缓存命中和增量刷新。
- 本地登录测试：用测试账号或人工授权浏览器验证 Cookie 不离开设备，日志中不出现会话值。
- 安全测试：扫描日志、缓存、报告和错误页面，确保不包含 Cookie、CSRF token、邮箱、学号、文件哈希或会话化下载 URL。
- 对抗测试：恶意点评或 HTML 中的提示注入不能改变工具调用、数据范围、系统提示和引用；受限附件 URL 不能进入 Artifact。
- 调度契约测试：只接受 Gateway 校验后的 `depth = 1` `ChildTask`；用户直连、Main Agent 以外父任务、根任务和二级委派均被拒绝，`create_child_task` 与其他 Agent 调用能力不存在。
- 状态测试：分别验证 `input_required`、`failed`、`cancelled` 的 `SpecialistArtifact` 字段完整、scope 不扩大、usage 可核验；补充输入继续原 child，取消后无继续调用或缓存写入。
- 出站测试：Gateway 拒绝缺少 `specialist_id`/`specialist_version`、answer、evidence、citations、confidence、limitations、data_scope、cache_level 或 usage 的结果，以及引用越权或含敏感字段的结果。
- 缓存分区测试：公共 QA 按 Specialist、skill、版本、证据与策略分区；加入年级、已修课程、工作量偏好或登录证据后不得命中或写入公共缓存。
- 回归测试：固定评测集对比摘要结论、引用数量、消歧结果和错误提示。
- 人工评审：抽样检查报告是否准确表达原点评分歧，没有把推测写成事实。

## 16. MVP

MVP 聚焦公开模式和可演示闭环：

1. 用户向 Main Agent 输入自然语言查询；Main Agent 决定是否需要课程评价专业材料。
2. Main Agent 编写 `query`、`expected_artifact_type` 和 `output_schema_ref`，Gateway 生成并校验 `depth = 1` `ChildTask` 与最小 `ContextGrant`。
3. Specialist 调用公开搜索 token，打开搜索结果并召回课程候选；用户确认需求只能由 Main Agent 发起后续交互，Specialist 不直接追问用户。
4. Specialist 解析课程页公开字段、评分、点评和附件元数据。
5. Specialist 返回统一 `SpecialistArtifact`，包含摘要、观点聚类、时间维度、证据、引用、不确定性、scope、缓存层级与 usage；Gateway 校验后交回 Main Agent。
6. Main Agent 结合用户上下文完成最终中文答复；Specialist 的 answer 不作为未经复核的最终用户措辞。
7. 建立分区后的公共课程证据/QA 缓存，并支持新点评增量刷新，保证私人输入不进入公共缓存。
8. 冻结 30 到 50 条评测夹具：课程 ID、来源快照时间、许可说明、人工标注、Specialist/skill/模型/提示/证据/策略版本和评审人。

登录增强模式仅在维护者明确授权、公开模式已稳定且本地 Connector 安全测试通过后作为可选演示：用户打开授权页面，连接器读取当前可见内容并生成仅本地报告。

MVP 不实现站点级批量爬取、不下载全量附件、不训练推荐模型、不做团队共享登录态，也不把登录受限附件送入云端或共享 RAG。

## 17. 演示脚本

1. 展示输入：“我想了解某门课程是否适合基础一般、希望作业不要太多的人。”
2. 展示 Main Agent 生成专业 query、expected artifact 与最小 `ContextGrant`，Gateway 仅投递 `depth = 1` `ChildTask`；同时演示直连和二级委派被拒绝。
3. Specialist 返回 3 个课程候选的 `SpecialistArtifact`；若候选无法消歧，则返回 `input_required`，由 Main Agent 向用户确认后继续原 ChildTask。
4. 选择一个课程，展示 Specialist 域内采集进度：课程字段、点评、时间分布、附件元数据；不出现其他 Agent 调用。
5. Gateway 校验 Artifact 后交回 Main Agent，展示由 Main Agent 完成的报告首屏：结论、适合/不适合人群、主要风险。
6. 展开“证据与来源”：每条结论显示课程页、点评锚点、发布时间/更新时间和采集时间，并展示 confidence、limitations、data_scope、cache_level 与 usage。
7. 展示“观点分歧”：例如作业负担在不同教师或不同学期之间的差异。
8. 新增一条模拟点评变化，再模拟一条点评被隐藏，证明增量刷新同时处理新增与撤回；带私人偏好的任务不命中公共 QA 缓存。
9. 展示恶意点评中的提示注入被当作普通证据文本，不触发工具调用。
10. 演示一次取消或可控失败：调用停止，Gateway 收到 `cancelled`/`failed` Artifact，Main Agent 不把部分草稿作为答案。
11. 可选：仅在授权和安全条件满足时展示本地登录增强；导出公开分享版时重新基于公开可见证据生成报告。

演示验收要求：任务链路只有 `Main Agent -> Gateway -> 课程评价 Specialist -> Gateway -> Main Agent` 一跳；Gateway 入站与出站校验均可见；Specialist 无用户入口、无 `create_child_task`、无其他 Agent 调用；成功、输入不足、失败和取消四态均能由 Main Agent 正确收束。原有第 14 节量化指标继续作为内容质量与性能验收阈值。

## 18. 关键外部边界

- 评课社区公开说明：<https://icourse.club/about/>
- 评课社区社区规范：<https://icourse.club/community-rules/>
- 评课社区源码仓库：<https://github.com/USTC-iCourse/ustc-course>
- 搜索 token 说明：<https://github.com/USTC-iCourse/ustc-course/blob/master/SEARCH_TOKEN_README.md>
- 技术边界详见：[评课社区集成调研](../research/icourse-integration.md)。
