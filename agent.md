# 当前工作目录约定

## 项目背景

- 本目录用于筹备中国科学技术大学“一〇七”杯算力与智能体开发大赛。
- 队伍名称为 `AI for better life In ustc`，共 3 名本科生，已报名本科生组智能体赛道。
- GitHub 私有仓库为 `xytsakura/ai-for-better-life-in-ustc`。
- 两名队友的 GitHub 用户名为 `wml-wml294` 和 `ysyzynx`；协作记录只使用 GitHub 用户名，不保存个人邮箱。
- 官方比赛要求以公众号原文和组委会最新通知为准，本地文档只用于整理。

## 用户偏好

- 给人看的文档默认使用中文 Markdown，优先保证在 Codex 中正常渲染。
- 未明确要求时优先生成 Markdown，不生成 PDF。
- 复杂方案、代码理解、多文件实现和审计优先使用 `codex-subagent-workflow`；子 Agent 提供证据，主 Agent 负责取舍、验证与交付。
- 对目标明确、可回滚的修复和小迭代直接实施、测试并交付，不要求用户先审阅设计文档；只有产品方向、数据迁移、权限边界或其他高影响决策需要先确认。
- 仓库保持轻量协作，不为了形式引入任务看板或复杂流程。
- 不创建没有实际内容的代码目录或空脚手架。
- Windows 本地文件路径包含中文或其他非 ASCII 字符、导致 Codex 链接无法打开时，应创建并持续复用纯 ASCII junction，再返回该别名路径。

## 当前产品基线

当前实施依据是 2026 年 8 月 5 日更新后的 `HUB_FRAMEWORK_SPEC.md` 和 Hub 顶层设计。产品由平台与标杆 Agent 两部分组成，当前比赛交付以平台闭环为主：

1. **课程资料整理与复习 Agent**：把个人、共享和订阅第三方三类知识空间做成权限隔离、来源可追踪、可更新和可删除的课程知识库闭环。
2. **插件化 Agent 接入平台**：用 Contract、Registry、Review、Gateway、Agent Portal 和统一交互容器接入独立校园 Agent。

当前方案的关键边界：

- 用户在 Agent Portal 主动选择 Agent；
- Gateway 根据已选择的 `agent_id` 做确定性代理和治理，不做自然语言意图识别；
- 平台不设置 Main Agent、Supervisor 或自动任务路由器；
- Agent 之间相互独立，不互相发现、调用、协作或递归委派；
- 课程资料 Agent 是首个第一方参考实现；
- 另接一个独立进程的极简第三方 Agent，证明接入新 Agent 时 Gateway 业务路由代码修改为 0 行；
- Contract v1 锁定 `@ag-ui/core@0.0.57`；原生 Connected Agent 使用 AG-UI，轻量 Agent 可使用 `simple-chat` 通用适配器；
- Campus Agent Hub、瀚海行与独立校园助手 Demo 已形成三服务闭环，代码分别位于 `apps/hub`、`apps/course-agent` 和 `apps/demo-agent`；
- Hub 使用独立 `hub.sqlite3`，不 import 瀚海行业务代码，也不复用其数据库和 Session；
- Featured 工作台使用 60 秒一次性授权码、`client_secret_basic` 和 `workspace:enter` EdDSA JWT；Gateway 调用使用 `chat:invoke`；
- 标准一键演示入口为 `deploy/run-demo.ps1`，运行时自动 seed 两个 Agent 并幂等导入 25 份数学分析资料。
- Demo 使用 FastAPI、SQLite FTS5、PyMuPDF、jieba 和服务端 Responses-compatible 模型配置；运行时数据库和上传目录只放在 `var/`。
- 当前演示语料为 25 份唯一 PDF、510 页；2026-08-03 已完成 DeepSeek-OCR-2 Markdown 全量回填，500 页可检索、10 个空白页不入索引、686 个分块。

## 知识库与课程 Agent 约定

- 三类知识空间复用统一数据对象，但必须按 `space_type`、用户身份和访问策略隔离。
- 权限过滤必须发生在检索和模型调用之前，不能先把私人内容交给模型再做删除。
- 个人内容不自动转为共享内容，也不得进入跨用户公共缓存。
- 所有检索结果至少保留知识空间、文档版本、定位信息和访问范围。
- 删除资料后，全文索引、向量索引和关联缓存必须失效。
- MVP 优先支持文本型 PDF、PPTX 基础文本、Markdown/TXT 和一种简单图片 OCR。
- PDF 使用相邻 `<原文件名>.ocr.md` 作为 RAG 文本；原 PDF 保留为引用和页面预览来源。Markdown/TXT 属于原生文本，后续接入时不走 OCR。
- OCR sidecar 必须校验源 PDF SHA-256、页数和连续页集合；校验失败时回退 PyMuPDF，不允许部分 sidecar 进入索引。
- 首个演示课程已冻结为数学分析 B1，共享知识库采用邀请制学习小组；免费订阅知识库广场已在课程 Agent `v0.7.0` 实现，外部网站自动抓取型来源仍在后续阶段再冻结。

## 订阅知识库广场约定

- 订阅是只读引用，不复制到每个用户的个人空间；个人投稿必须明确勾选资料并生成独立不可变快照。
- 发布资料逐份维护 `use_in_rag`、`can_preview`、`can_download`，服务端在页面、文件和模型调用前分别鉴权。
- 未订阅登录用户只能预览明确允许预览的当前公开资料；下载和 RAG 需要有效订阅。作者与管理员的审阅访问是独立权限，不写入 `library_subscriptions` 或 `memberships`。
- 新版批准后订阅者自动使用新版，旧版只用于审计与回滚；取消订阅、暂停、下架、换版后，旧文档 ID 必须在下一次请求立即失效。
- `library_subscriptions` 与邀请制 `memberships` 永久分离；订阅文档的 FTS 查询不得再次硬连接 `memberships`。
- 多文档投稿必须在一个数据库事务内写入文档、版本、页面、分块和 FTS；任一失败时同时清理数据库、索引和复制文件。
- 保存资料到个人库时，文档、页面、分块、FTS 和审计事件必须处于同一事务；复制 OCR-backed PDF 时必须同时复制已校验的 `.ocr.md` sidecar，回滚和删除时也必须同时清理 PDF 与 sidecar。
- 演示角色固定为：`demo-a` 管理员、`demo-b` 投稿作者、`demo-c` 普通订阅者，用于验证权限矩阵。

## 平台与 Contract 约定

- Agent 内部可以使用任意框架，平台只依赖公开且版本化的接入契约。
- Contract 至少覆盖 Manifest、请求、流式事件、错误、文件引用、认证声明、健康检查和兼容规则。
- 只有 `active` 且存在活动版本的 Agent 才能在 Portal 展示。
- Gateway 只做身份验证、Schema 校验、状态检查、文件鉴权、限流、超时、取消、事件转发和审计。
- 第三方 endpoint、Agent 输出、网页和文件都按不可信输入处理。
- 第三方 Agent 不得获得其他 Agent 的数据、状态或凭据。
- 第三方 Agent 接入验收必须包含 Schema、连通性、SSRF、危险重定向、恶意响应、限流和超时测试。
- 第三方外部 Endpoint 必须使用公网 HTTPS；第一方本地或内部 Endpoint 只允许管理员提交，并使用服务端精确 origin 白名单。
- 公共 Registry 输出必须隐藏聊天、健康、回调 Endpoint 和私有治理元数据。
- Hub JWT 固定使用 Ed25519/EdDSA、`kid` 和 JWKS；有效期不超过 120 秒，时钟偏差不超过 30 秒。

## 数据、安全与合规经验

- 不在仓库中保存 API key、密码、Cookie、CSRF token、个人敏感信息或未脱敏数据。
- Hub 的 Demo 身份模式默认关闭；`X-Hub-User` 只在显式启用 Demo 模式时生效，不能作为生产认证方案。
- 容器存活检查使用无需身份的 `/healthz`，不能依赖 `/api/session` 等业务认证接口。
- 外部 Endpoint 在注册和调用前都要解析 DNS，并拒绝任一私网、回环、链路本地、保留或未指定地址；生产部署还应通过出站代理或网络策略绑定允许的目的地址，闭合 DNS rebinding 的解析与连接时序风险。
- Agent 必须校验 Hub JWT 的 issuer、audience、签名、`chat:invoke`/`workspace:enter` scope 和最长 120 秒有效期，不能只校验签名。
- Featured Agent 的客户端密钥通过运行时只读 secret 文件注入，不写入命令行、普通环境变量、日志或仓库。
- Agent 健康响应必须严格满足 Contract（包括 `status: "ok"` 与 `contract_version: "1.0"`），不能只以 HTTP 200 判定兼容。
- 文档不得把尚未实现的能力写成现状；Hub 已实现 SQLite 持久限流、大小限制、异步健康轮询和连续失败准入，公开多实例部署仍需评估共享数据库或独立限流基础设施。
- Hub Ed25519 私钥必须持久化在忽略的运行时文件或 Secret Store 中；不能在每次进程启动时静默生成新身份，否则 Agent 的 JWKS 缓存会导致重启后的短期 401。
- 固定比赛验收使用 `deploy/verify_demo.py`，至少 10 轮中成功 9 轮；结果只记录状态、耗时和事件数量，不保存问答正文、授权码、JWT 或 Cookie。
- 本地或服务器首次启动三服务后必须执行 `deploy/bootstrap_demo.py`（或使用包含同等 bootstrap 步骤的标准编排），完成两个 Agent 注册、Contract 检查、审核、健康检查和 Featured 凭据生成；只看到三个健康接口为 200 不能证明 Hub 已经可用。
- 日志、事件和 Manifest 不记录明文密钥、完整私人文件正文或完整私人聊天正文。
- 平台模型 provider 和 `base_url` 必须受控；不允许普通用户向任意 URL 发送私人资料。
- `file_ref` 必须由服务端按当前用户和目标 Agent 重新鉴权。
- 用户文件、网页、OCR 文本、第三方知识源和 Agent 输出均按不可信输入处理，实施时覆盖 SSRF、提示注入、恶意文件和越权访问。
- 外部课程资料必须记录来源、许可状态和可使用范围；未授权内容不得批量复制或公开再分发。
- 评课社区使用会话 Cookie 和 CSRF，不存在可共享的通用用户 API token；搜索 URL 中的短期 token 不是登录凭据。
- 不提取、共享或持久化任何成员的评课社区登录凭据。
- 评课社区源码的 AGPLv3 许可不覆盖用户点评和课程附件的内容版权。
- 未获得明确授权时，登录限定点评、附件、教材、真题和讲义不得进入公共知识库或共享缓存。

## 版本与归档约定

- `main` 当前只呈现插件化 Agent 平台方案，不混入旧架构的现行说明。
- Personal Main Agent + Specialist 单跳编排方案完整归档在提交 `c49719e`。
- 描述性归档标签为 `archive-v0.4-personal-main-agent`，原有标签 `V0.0` 继续保留。
- 归档版只用于回溯设计演进，不再作为当前开发依据。
- 不把旧版 24 份文档重新复制回 `main`；需要研究或恢复时从归档标签创建独立分支。
- 方向变化后必须同步检查 README、产品文档和 `agent.md`，避免仓库同时出现两套互相矛盾的当前方案。

## Git 协作经验

- 开始工作前先运行 `git pull --ff-only`，理解远程变化后再编辑。
- 一次提交尽量只处理一个主题，提交信息清楚说明变化。
- 工作区可能包含队友修改；不得回滚或覆盖不属于当前任务的改动。
- 推送前检查远程是否继续前进，并验证 Markdown 链接、格式与敏感信息。
- 旧方案需要退出主线时，优先使用描述性 Git 标签归档，不在当前目录保留两套冲突文档。

## 课程 Agent 引用交互约定

- 助手回答支持同一条消息内的多段选区；引用篮最多 8 段、单段最多 2000 字、选中文字合计最多 4000 字。
- 主输入框只展示结构化引用条目，不把引用粘贴进用户可编辑正文；发送失败时必须把本轮引用恢复到引用篮。
- 独立解释分支挂在源回答下方，支持多轮、折叠和浏览器本地历史恢复；分支内容不会自动进入主对话，只能由用户手动回引。
- 分支请求固定走服务端 `/api/branch-query` 和 `COURSE_AGENT_BRANCH_LLM_MODEL`，不得接收浏览器传入的模型、provider、`base_url` 或检索参数。
- 选中文字、完整源回答和分支历史都按不可信数据处理；课程检索模式下不得把模型旧回答冒充为 `[S1]` 课程证据。
- 分支异步请求必须使用身份 generation guard；用户切换身份后，旧请求响应不得写回新身份界面或历史。
- 当前持久化边界是每个演示身份独立的 `localStorage`，不提供跨设备同步。

## 课程 Agent 流式输出约定

- 主问答和独立解释分支使用新增的 POST + SSE 端点；旧 JSON 端点继续保留兼容性，不直接改协议。
- 只流式传输最终回答正文，不展示隐藏推理或伪造“正在思考、调用工具”等状态。
- Responses 上游事件必须按 JSON `data.type` 分发，覆盖 `response.output_text.delta`、`response.completed`、`response.incomplete`、`response.failed`、`error` 和异常 EOF。
- 知识库权限与检索发生在建立模型流之前；引用、usage、模型信息和最终正文只在 `complete` 中统一确认。
- 中途失败可保留部分正文供阅读，但不能进入后续模型历史；取消或切换身份后不得显示模型错误或写入新身份状态。
- 前端通过 `AbortController` 管理主流和分支流；反向代理必须关闭 SSE 响应缓冲，不能用本地打字机动画冒充上游真流式。

## Hub 统一聊天故障收口经验

- Connected Agent 缓存 JWKS 后，如果 Hub 在保留同一 `kid` 的情况下轮换了 Ed25519 密钥，应只在 `InvalidSignatureError` 时清空缓存、重新拉取 JWKS 并重试一次；issuer、audience、scope、有效期和签名校验不得放宽。
- Hub 统一聊天同一页面只允许一个活动 run；发送期间禁用发送，取消、`RUN_ERROR`、异常 EOF 和身份切换都必须收尾当前占位消息并恢复控件。
- 重试按钮必须绑定失败当轮的 Agent 和原问题，点击后先移除旧错误卡片，避免错误卡片与等待气泡累计。
- 浏览器原生 ES Module 会分别缓存入口和依赖；`app.js` 新增 `hub-core.js` 导出依赖时，两者的 URL 版本必须同步更新，并用真实浏览器检查控制台，防止入口更新但依赖仍命中旧缓存导致整页空白。
