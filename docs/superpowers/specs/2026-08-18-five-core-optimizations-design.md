# 五项核心功能优化设计与实施验收方案

> 文档状态：待队长审核
>
> 创建日期：2026 年 8 月 18 日
>
> 设计基线：提交 `23b890c` 上的 Campus Agent Hub、瀚海行和三服务 Demo
>
> 参赛项目：AI for better life In ustc
>
> 实施状态：本文档只描述相对 `23b890c` 的待实施增量，不把工作区中的实验性或未提交代码视为已交付；通过团队审核后再进入目标模式实施

## 1. 文档目的

本文档把本轮五项核心优化收束为可以逐项开发、验证、审计和回退的执行基线：

1. 保持深色默认，在 Hub 与瀚海行中统一字体系统；
2. 简化 Agent 广场交互，使用户直接进入目标 Agent；
3. 明确管理员、开发者和普通用户三层身份界面与权限；
4. 将 Hub 的单套模型配置升级为安全的多配置、多模型能力，并向接入 Agent 提供兼容接口；
5. 为瀚海行知识广场补充课程演示内容，并改为课程集市式呈现。

本文档只定义增量更新，不重写现有产品。当前已经能够运行的 Hub Registry、Gateway、Featured 工作台授权、瀚海行问答、RAG、知识广场、知识库权限和三服务演示闭环必须完整保留。

## 2. 已确认的产品决策

以下决策已经由队长确认，实施时不再作为开放问题：

- Hub 与瀚海行继续以深色主题作为首次访问的默认主题；浅色主题保留，但不在本轮重新设计。
- 字体任务只调整字体资产、字体角色和字重，不借机改动整体布局、颜色系统、星空或业务交互。
- 瀚海行作为 Featured Agent，用户点击卡片或“立即对话”后直接进入其正式工作台，不经过 Hub 统一聊天中间页。
- 本轮身份体系使用现有 Demo 身份完成真实的界面差异、路由保护和后端权限校验；不扩展为生产级注册、登录、找回密码和账号审核系统。
- Hub 支持多套用户模型配置，但第三方 Agent 不得接触用户的明文 API Key。
- 瀚海行保留原有独立模型配置作为单独部署时的回退能力，不因接入 Hub 而失去独立运行能力。
- 知识广场可以使用课程演示数据，但界面必须明确标记“演示知识库”，不得把虚构内容或未授权资料描述为真实公开资源。
- 每个 To-do 独立实施、独立测试、独立浏览器验收并独立记录更新日志；上一项没有通过验收门禁时，不进入下一项。

## 3. 增量更新与运行保护原则

### 3.1 保护当前基线

开始任何实现前，以本文档的设计基线 `23b890c` 为参照，先把最新 `main` 的新增提交纳入评估，再建立可复现实施基线并记录：

- 当前短提交号和工作区状态；
- Hub、瀚海行、独立 Demo Agent 的健康状态；
- Hub 首页、广场、瀚海行工作台和知识广场的基线截图；
- 当前 Python、JavaScript、Contract 和固定 Demo 验收结果；
- 当前数据库与运行时目录的备份位置。

不得通过删除数据库、重置用户数据、重写整个前端或绕过鉴权来完成本轮任务。新增数据库结构必须使用向前兼容的幂等迁移；新增前端行为必须保留现有路由和深链接；新增模型能力不得改变未选择 Hub 配置时的原有模型调用行为。

### 3.2 小步提交与回退

五项任务分别形成独立提交，不混入无关重构。每项提交必须：

- 只修改该任务所需模块和测试；
- 同步更新根级 `CHANGELOG.md`，涉及瀚海行时同步更新 `apps/course-agent/CHANGELOG.md`；
- 在提交前执行该任务的专项测试和全量回归；
- 记录浏览器验收结果和截图位置；
- 可以通过回退单个提交恢复到上一项已经通过验收的状态。

模型配置数据库迁移等不可简单回退的数据变化，必须保留旧字段和读取回退路径，直到新链路经过完整验收。

### 3.3 每项任务的交付门禁

每个 To-do 都必须依次通过以下五道门禁：

1. **代码检查**：检查改动范围、权限边界、敏感信息和异常路径。
2. **自动测试**：专项测试与现有全量测试全部通过。
3. **真实部署**：重新启动三服务，确认不是只在测试替身中成立。
4. **浏览器验收**：以真实交互完成桌面端和移动端关键流程，并读取控制台错误。
5. **截图审计**：保存改动前后截图，对照本任务验收标准逐项确认后再更新日志。

任何一项失败，都应先修复当前任务，不把问题带到下一个 To-do。

## 4. 总体 To-do 清单

| 编号 | 任务 | 当前基础 | 本轮交付 | 主要风险 |
| --- | --- | --- | --- | --- |
| T1 | 统一字体体系 | 两端已默认深色，但字体不统一，Hub 依赖远程字体 | 本地字体资产、统一字体角色、断网可用 | 字体体积、中文缺字、换行回归 |
| T2 | Agent 直接进入 | Featured 部分入口已能进入工作台，但详情页和卡片行为不一致 | 三类 Agent 统一启动规则，瀚海行一键进入 | 授权码流程回归、外链安全 |
| T3 | 三层身份权限 | 已有 `demo-a/b/c`，管理员局部隐藏 | 三种界面、路由保护、后端权限矩阵 | 只隐藏按钮而接口仍可调用 |
| T4 | 多模型配置与兼容 | Hub 只有一套浏览器本地配置，瀚海行另有单套服务端配置 | 多配置中心、模型发现、绑定、Hub Model Gateway、瀚海行适配 | API Key 泄露、SSRF、范围最大 |
| T5 | 课程知识广场 | 已有投稿、审核、订阅和版本治理，但演示内容不足 | 课程集市卡片、幂等演示数据、课程详情 | 演示数据冒充真实资源、重复 seed |

## 5. T1：统一字体体系

### 5.1 目标

参考 [Hyperknow Agent](https://agent.hyperknow.io/) 的文字层级审美，在不改变现有深色视觉结构的前提下，让 Hub 与瀚海行拥有一致、稳定、适合中文学习产品的字体表现。

参考对象只用于提炼字体层级，不复制其品牌、布局或私有素材。

### 5.2 字体角色

建议建立以下字体变量：

| 角色 | 建议字体 | 使用位置 | 字重 |
| --- | --- | --- | --- |
| UI 与正文 | `Inter` + `Noto Sans SC` | 导航、按钮、正文、表单 | 400 / 500 |
| 页面标题 | `Inter` + `Noto Sans SC` | 首页标题、页面一级标题 | 600 / 700 |
| 辅助文字 | `Inter` + `Noto Sans SC` | 描述、时间、状态说明 | 400 |
| 课程标题 | `Noto Serif SC` | 知识广场课程卡片标题 | 600 |

中文字体承担中文字符，`Inter` 主要承担英文、数字和模型名。字体以授权允许的 WOFF2 文件自托管，并保留系统字体回退链。现有依赖 Google Fonts 的展示字体应移除运行时网络依赖。

本轮不改变既有文字颜色变量。参考图中“主标题更深、正文适中、辅助文字更浅”的颜色层级只作为后续视觉规范记录，不在 T1 中扩大改动范围。

### 5.3 实施范围

- Hub 字体资产、全局字体变量和标题层级；
- 瀚海行字体资产、全局字体变量和知识广场课程标题；
- 首页品牌、导航、按钮、表单、聊天正文和课程卡片的字体映射；
- 字体加载失败时的本地系统回退；
- 字体许可证和来源记录。

不得修改星空算法、卡片结构、页面间距、主题切换规则和业务文案。

字体资产固定放在以下随应用发布的静态目录，避免两个服务跨目录取文件：

- Hub：`apps/hub/web/assets/fonts/`；
- 瀚海行：`apps/course-agent/course_agent/web/assets/fonts/`；
- 每套字体在相邻目录保留许可证和来源说明，至少记录字体名称、版本、来源 URL、许可证和实际使用的字重。

只引入本轮实际使用的 WOFF2 字重或经过许可证允许的子集，不提交完整桌面字体包或重复的 TTF/OTF 文件。

### 5.4 验收标准

1. Hub 与瀚海行首次访问仍直接进入深色主题。
2. 断开外网后，两端字体仍能正确显示，不依赖 Google Fonts。
3. 中文、英文、数字、模型名和数学符号无缺字或错误回退。
4. 1440×900、1280×800 和 390×844 下无新增截断、重叠和横向滚动。
5. 长 Agent 名、长课程名、长按钮文字不会因字体变化破坏布局。
6. 浅色主题仍能使用，现有主题偏好和首屏防闪烁测试不回归。

### 5.5 截图审计

- Hub 首页：桌面深色、移动深色；
- Hub 广场：包含多个 Agent 卡片；
- 瀚海行首页：包含导航、聊天输入区和正文；
- 瀚海行知识广场：包含课程标题、辅助文字和按钮；
- 浏览器网络面板确认没有外部字体请求。

## 6. T2：Agent 广场直接进入

### 6.1 目标

用户不需要理解接入等级，只需点击一次就进入该 Agent 最合适的使用界面。瀚海行必须直接进入正式工作台，不经过 Hub 统一聊天页。

### 6.2 统一启动规则

| Agent 类型 | 卡片主体 | 主按钮 | 实际行为 |
| --- | --- | --- | --- |
| Featured | 直接启动 | “立即对话” | 调用 `workspace/start`，完成一次性授权后进入完整工作台 |
| Connected | 直接启动 | “立即对话” | 进入 Hub 统一聊天 |
| Link App | 直接启动 | “打开应用” | 经过 Hub 受控启动记录后打开外部网址 |

详情页不再是默认中间路径。需要了解能力、维护者和数据政策的用户仍可通过次要的“查看详情”入口进入详情页。

Featured Agent 不允许为了省事直接拼接瀚海行 URL。必须继续使用当前一次性授权码和工作台令牌兑换链路，保证身份映射、过期、重放拒绝和审计仍然有效。

Link App 继续按不可信外链处理：启动前记录脱敏审计事件，拒绝 `javascript:`、`data:`、私网、回环和非允许协议 URL，服务端检查危险重定向；新窗口必须使用 `noopener noreferrer`，不能把 Hub 的 Cookie、授权码或来源页面对象暴露给外部站点。

### 6.3 一致性范围

以下入口必须共享同一个主操作决策函数：

- Hub 首页推荐卡；
- Agent 广场卡片；
- 最近使用；
- Agent 详情页主按钮；
- 搜索结果和钉选 Agent。

启动失败时保留在 Hub 当前页面，显示可重试的明确错误；不得悄悄回退到与用户预期不同的聊天页。

主操作类型与目标 URL 的纯决策逻辑放在 `apps/hub/web/hub-core.js`，DOM 绑定和 `workspace/start` 调用保留在 `apps/hub/web/app.js`。所有入口调用同一个决策函数，并在 `apps/hub/tests-js/hub-core.test.mjs` 覆盖 Featured、Connected 和 Link 三种结果，避免页面之间再次出现分叉。

### 6.4 验收标准

1. 管理员、开发者和普通用户分别点击瀚海行，均一次进入瀚海行正式工作台。
2. 首页卡片、广场卡片、详情页主按钮和最近使用的结果一致。
3. Connected Agent 继续进入 Hub 统一聊天，Link App 继续使用安全外链。
4. Featured 授权码只能使用一次，过期或重放继续被拒绝。
5. Agent 离线、启动接口失败和弹窗被阻止时均有清晰错误提示。
6. Hub 的搜索、筛选、详情页和统一聊天没有回归。
7. Link App 的危险协议、私网目标和危险重定向被拒绝，合法外链仍可通过受控入口打开并留下审计记录。

### 6.5 截图与流程审计

截图本身不能证明跳转正确，因此本项同时保存流程记录：

- 点击前的 Hub 瀚海行卡片；
- 点击后的瀚海行正式工作台；
- 浏览器地址和页面品牌确认不经过 `/hub/agents/{id}/chat`；
- Connected Agent 的 Hub 聊天页；
- Link App 的受控打开结果；
- 浏览器控制台无未处理异常。

## 7. T3：三层身份界面与权限

### 7.1 本轮边界

本轮继续使用现有演示身份：

- `demo-a`：管理员；
- `demo-b`：开发者；
- `demo-c`：普通用户。

本轮完成的是可以真实演示和真实阻止越权的角色系统，不实现生产级账号注册。未来接入统一身份认证时，沿用相同角色值和权限矩阵，不重写业务判断。

### 7.2 导航与界面矩阵

| 功能 | 管理员 | 开发者 | 普通用户 |
| --- | --- | --- | --- |
| 平台首页与 Agent 广场 | 可见 | 可见 | 可见 |
| Agent 使用与最近使用 | 可用 | 可用 | 可用 |
| 模型配置 | 可用 | 可用 | 可用 |
| 个人主页 | 可用 | 可用 | 可用 |
| Agent 提交 | 可查看，必要时用于第一方管理 | 可用 | 不显示、不可访问 |
| 我的提交 | 可查看管理范围 | 只看自己的提交 | 不显示、不可访问 |
| 管理审核 | 可用 | 不显示、不可访问 | 不显示、不可访问 |

普通用户只保留使用平台所需的基本模块。开发者右侧重点突出“提交 Agent”和“我的提交”。管理员右侧重点突出“审核管理”，不把普通用户不需要的治理操作混入广场。

### 7.3 后端权限矩阵

前端隐藏只负责减少干扰，不能作为安全边界。后端至少执行以下规则：

- 审核、批准、拒绝、暂停、恢复、回滚和第一方内部 Endpoint 只允许管理员；
- Manifest 提交和查看自己的提交只允许开发者与管理员；
- 开发者只能读取和操作自己提交的 Agent 版本，不能读取其他开发者的私有草稿或审核意见；
- 普通用户只可读取 active Agent 的公开字段并启动允许的 Agent；
- 直接访问无权限路由时，前端显示受控的 403 页面或返回广场，API 固定返回 403；
- 身份切换时取消旧身份的异步请求，旧响应不能写入新身份页面。

设计基线 `23b890c` 中存在两处必须在 T3 明确消除的风险，不能只通过新增菜单完成角色演示：

- `apps/hub/web/app.js` 的部分请求会根据 `options.admin` 把 `X-Hub-User` 强制替换为 `demo-a`。实现时必须移除这种前端管理员身份替换；所有请求默认只能使用当前选中的身份，管理员资格由后端判断。
- 当前 `POST /api/registry/agents` 只对 `first_party_internal` 做管理员限制。实现时新增 `require_developer_or_admin`，普通用户提交任何 Manifest 都固定返回 403。

“我的提交”使用 `hub_agent_versions.submitted_by` 作为所有权依据。开发者列表接口只返回当前用户提交的版本及可公开的审核状态，不返回其他开发者的私有 Manifest、Endpoint、检查详情或审核意见。

权限自动测试至少逐一覆盖以下接口族：

- `POST /api/registry/agents`；
- 开发者“我的提交”查询接口；
- `GET /api/admin/agents` 与 `GET /api/admin/agents/{agent_id}`；
- `review`、`checks`、`suspend`、`restore`、`deprecate`、`rollback`；
- Agent `credentials` 创建与状态变更；
- `POST /api/agents/{agent_id}/health/check`；
- `GET /api/admin/audit`。

每个接口分别检查缺省 `X-Hub-User`、`demo-c`、`demo-b` 和 `demo-a`。当前 Demo 模式下缺省请求按 `demo-c` 普通用户处理；管理接口只有 `demo-a` 成功，开发者提交接口只有 `demo-b` 与 `demo-a` 成功。生产级“无登录返回 401”留到真实账号系统实现。

### 7.4 验收标准

1. 三种身份切换后，侧栏和右侧功能模块立即符合权限矩阵。
2. 普通用户直接输入 `/hub/submit` 或 `/hub/admin` 不能使用对应能力。
3. 开发者可以提交和查看自己的记录，但不能审核、暂停或回滚。
4. 管理员可以进入审核模块，原有审核闭环保持正常。
5. 对所有敏感 API 分别验证无身份、普通用户、开发者和管理员四种情况。
6. 角色差异有一页中文说明，可直接用于比赛文档和视频口播。

### 7.5 截图审计

在相同桌面视口分别保存三种身份的 Hub 首页、侧栏和个人操作区截图；另外保存开发者提交页、管理员审核页，以及普通用户访问受限页面的结果。截图中不得出现真实 API Key、JWT、Cookie 或私人聊天正文。

## 8. T4：多模型配置与 Agent 兼容

### 8.1 目标与定位

Hub 从“浏览器里保存一套模型表单”升级为平台级模型配置中心。用户可以维护多套 API 配置、发现每套服务的模型、选择默认配置，并让声明兼容能力的 Agent 通过 Hub 安全调用这些模型。

本项改变了旧顶层设计中“每个 Agent 只能独立维护自己的模型配置”的绝对边界。新的边界为：

- Agent 仍可独立维护自己的模型服务；
- Agent 可以选择接入 Hub Model Gateway，共享用户明确授权的模型配置；
- 是否接入由 Agent Manifest 能力声明和平台验收决定，Hub 不强迫所有第三方 Agent 改造。

### 8.2 用户模型配置档案

一个用户可以创建多个 Model Profile。每个档案包含：

- 用户自定义名称，例如“GPT 主力”“DeepSeek 经济版”；
- provider 类型；
- API 协议类型，例如 Responses 或 Chat Completions；
- 规范化 Base URL；
- 加密保存的 API Key；
- 发现并验收过的模型目录；
- 默认模型；
- 启用状态、最近测试时间和受控错误状态。

前端只显示 API Key 掩码。API Key 不进入 `localStorage`、日志、聊天记录、Manifest、Agent 响应或浏览器网络响应。

配置层级为：

1. 用户全局默认 Model Profile；
2. 用户为某个 Agent 设置的默认 Profile 和模型；
3. 当前会话在允许范围内临时选择的 Profile 和模型；
4. 没有 Hub Profile 或 Hub 不可用时，瀚海行回退到自身原有服务端配置。

### 8.3 数据模型

Hub 使用自己的 `hub.sqlite3` 保存 Profile 元数据和密文，不写入瀚海行数据库。首版新增以下表：

| 表 | 关键字段 | 约束 |
| --- | --- | --- |
| `hub_model_profiles` | `profile_id`、`owner_user_id`、`name`、`provider`、`api_style`、`base_url`、`encrypted_api_key`、`key_version`、`api_key_fingerprint`、`status`、`created_at`、`updated_at` | Profile 只能由所有者读取和修改；API Key 只保存 AES-GCM 密文 |
| `hub_model_profile_models` | `profile_id`、`model_id`、`display_name`、`chat_eligible`、`capabilities_json`、`discovered_at` | `(profile_id, model_id)` 唯一；只允许绑定已发现且可聊天的模型 |
| `hub_model_bindings` | `binding_id`、`user_id`、`scope_type`、`scope_id`、`profile_id`、`model_id`、`updated_at` | `scope_type` 仅为 `global` 或 `agent`；同一用户每个作用域只允许一个活动绑定 |
| `hub_model_gateway_delegations` | `delegation_id`、`token_hash`、`user_id`、`agent_id`、`profile_id`、`model_id`、`scope_type`、`scope_id`、`status`、`expires_at`、`last_used_at` | `scope_type` 仅为 `connected_run` 或 `featured_workspace`；未过期 `token_hash` 唯一；只保存 token 哈希 |
| `hub_model_gateway_grants` | `grant_id`、`jti`、`delegation_id`、`request_id`、`user_id`、`agent_id`、`profile_id`、`model_id`、`status`、`expires_at`、`issued_at`、`consumed_at` | `jti` 唯一，`(delegation_id, request_id)` 唯一；每个 Grant 只能原子消费一次 |
| `hub_model_gateway_audit` | `request_id`、`user_id`、`agent_id`、`profile_id`、`model_id`、`status`、`error_code`、`usage_json`、`started_at`、`completed_at` | 不保存提示词、回答正文、明文 URL 或密钥 |

外键删除规则必须阻止仍被绑定的 Profile 直接删除。用户先通过界面切换或解除全局与 Agent 绑定；删除接口确认无活动绑定后，原子撤销该 Profile 的未过期授权，再删除模型目录与密文。接口不得静默解除仍在使用的默认绑定。审计记录只保留不可逆的 Profile 标识和脱敏结果。

委托状态固定为 `active`、`expired`、`revoked`、`consumed`；`consumed` 只用于已经兑换的 Connected run 级委托，Featured workspace 级委托在多次合法兑换后仍保持 `active`，直到会话结束或触发撤销。Grant 状态固定为 `issued`、`consumed`、`expired`、`revoked`。

数据库初始化继续使用当前 `init_db()` 的幂等迁移风格。不得删除或重命名现有表和字段；旧版本二进制读取同一数据库时至少不因新增表而失败。

### 8.4 Profile API

首版接口固定为：

| 方法与路径 | 用途 | 权限 |
| --- | --- | --- |
| `GET /api/model-profiles` | 返回当前用户 Profile 摘要与绑定，不返回密钥 | 当前用户 |
| `POST /api/model-profiles` | 创建 Profile | 当前用户 |
| `PATCH /api/model-profiles/{profile_id}` | 修改名称、连接和状态 | Profile 所有者 |
| `DELETE /api/model-profiles/{profile_id}` | 在无活动绑定时删除 | Profile 所有者 |
| `POST /api/model-profiles/{profile_id}/test` | 使用保存配置做轻量连通测试 | Profile 所有者 |
| `POST /api/model-profiles/{profile_id}/discover` | 发现并保存可用模型目录 | Profile 所有者 |
| `PUT /api/model-bindings/global` | 设置当前用户全局默认 | 当前用户 |
| `PUT /api/model-bindings/agents/{agent_id}` | 设置当前用户对某 Agent 的默认配置 | 当前用户，且 Agent 已声明兼容 |

创建和更新响应只返回 `has_api_key`、末尾掩码或不可逆指纹等安全状态，不回传 `encrypted_api_key`。连接测试和模型发现只能使用服务端已保存且已通过 URL 安全校验的配置，不能接受临时 Base URL 作为探测目标。

### 8.5 Hub Model Gateway

不把 API Key 注入第三方 Agent。兼容 Agent 使用短期、签名且绑定范围的 Hub 模型调用令牌访问 Model Gateway：

```text
用户在 Hub 选择 Model Profile
→ Hub 验证用户拥有该配置并确认 Agent 已声明兼容
→ Hub 生成绑定 user_id、agent_id、profile_id、model_id 和当前 run/workspace 的上下文委托
→ Agent 使用自身客户端凭据兑换短期、单次使用的 Gateway Grant
→ Agent 向 Hub Model Gateway 发起模型请求
→ Gateway 读取服务端加密密钥并调用对应上游
→ Gateway 返回标准化的流式文本、错误和 usage
```

令牌不能用于读取 Profile、导出密钥、切换任意模型或调用其他用户配置。Agent 只能获得它完成当前请求所需的模型结果。

Model Gateway 对 Agent 暴露固定的文本生成接口 `POST /api/model-gateway/v1/generate`。请求只包含经过限制的 `instructions`、`messages`、`reasoning_effort`、`max_output_tokens` 和 `stream`；Profile 与模型来自授权令牌，Agent 不能在请求体中替换。

流式响应使用 SSE，并归一化为以下事件：

```text
model.started
model.output_text.delta
model.usage
model.completed
model.error
```

Gateway 内部负责把该契约转换为 Profile 对应的 Responses 或 Chat Completions 请求，并把 token usage 归一化为 `input_tokens`、`output_tokens`、`reasoning_tokens`、`cached_tokens` 和 `total_tokens`。首版只代理文本生成；工具调用、知识库检索和 Agent harness 继续由各 Agent 自己执行。

受控错误码至少包括：

- `model_profile_not_found`、`model_profile_disabled`；
- `model_not_allowed`、`model_grant_invalid`、`model_grant_expired`；
- `provider_auth_failed`、`provider_rate_limited`、`provider_unreachable`；
- `provider_protocol_error`、`model_gateway_timeout`。

上游原始响应体、响应头和完整 URL 不直接返回 Agent 或浏览器。

### 8.6 Grant 签发与兑换契约

浏览器不直接获取 Model Gateway Grant。Hub 先生成只在 Agent 服务端保存的 `model_delegation_token`，再由已注册 Agent 使用自身客户端凭据进行服务端兑换。委托按执行上下文分为两类：Connected 委托只允许当前 run 兑换一次；Featured 委托绑定当前工作台会话，可在会话有效期内为不同 `request_id` 兑换多个单次 Grant。

```text
Hub 解析当前用户与 Agent 的有效模型绑定
→ Hub 生成绑定 user_id、agent_id、profile_id、model_id 和 run/workspace 的服务端委托
→ Connected Agent 在当前 run 请求中收到 run 级委托；Featured Agent 在服务端工作台令牌兑换结果中收到 workspace 级委托
→ Agent 使用 client_secret_basic 调用 Grant 兑换接口
→ Hub 验证 Agent 身份、委托归属、有效期和唯一 request_id
→ Hub 返回短期、单次使用的 Gateway Grant
→ Agent 使用 Grant 调用 generate；Hub 原子标记 Grant 已消费
```

兑换接口固定为 `POST /api/model-gateway/grants/exchange`：

- 认证：`Authorization: Basic base64(agent_id:client_secret)`；
- 请求体只允许 `model_delegation_token` 和由 Agent 生成的唯一 `request_id`，禁止 Agent 自报 `user_id`、`profile_id`、`model_id` 或其他越权字段；
- 成功响应包含 `access_token`、`token_type: "Bearer"`、`expires_in`、`model_id` 和 `model_gateway_url`；
- 服务端按 8.3 的两张状态表记录委托哈希、`jti`、`request_id`、状态、过期时间、签发时间与消费时间，不保存明文令牌；
- Connected 委托重复兑换、Featured 委托重复使用同一 `request_id`、Grant 重复消费、身份不匹配或任一凭据过期时分别返回稳定的受控错误，不回传内部 claims。

Connected Agent 的委托随当前 Hub run 结束而失效。Featured 瀚海行的委托随工作台会话过期、退出登录、身份切换、Profile 禁用或绑定变更而失效；每轮模型调用生成新的 `request_id` 并兑换新的 Grant。Agent 长期凭据只能证明 Agent 身份，不能单独选择用户、Profile 或模型。浏览器 Cookie、用户 API Key、Agent `client_secret`、委托和完整 Grant 均不得出现在页面、URL、日志或前端存储中。

### 8.7 授权与密钥契约

Profile API Key 使用 AES-256-GCM 加密，附加认证数据至少绑定 `profile_id`、`owner_user_id` 和 `key_version`。每次加密使用密码学安全随机 nonce；密文字段采用带版本的 envelope，至少完整保存 `format_version`、`key_version`、`nonce`、`ciphertext` 和认证 tag。密钥轮换期间只允许受控的旧 key 解密窗口，新写入一律使用当前 key；备份恢复必须同时恢复相应 key version，不能只备份数据库。

主密钥从运行时只读文件 `HUB_MODEL_PROFILE_MASTER_KEY_FILE` 加载。新增功能开关 `HUB_MODEL_PROFILES_ENABLED`，在设计基线升级期间默认 `false`：

- 功能关闭时，Hub 必须正常启动，现有广场、Gateway、三服务 Demo 和瀚海行独立模型配置保持可用；
- 显式启用 Model Profile 后，主密钥文件缺失、权限不当或长度非法必须拒绝启动，不能静默生成新的生产密钥；
- 标准比赛 Demo 启用 T4 时，`deploy/run-demo.ps1` 必须在 Git 已忽略的 `var/secrets/` 中首次生成开发专用主密钥，后续启动复用同一文件；
- 生产环境只接受部署系统预置的只读密钥文件，不允许启动脚本临时生成；
- 主密钥文件、备份和轮换材料均不得进入 Git。

Gateway Grant 使用 Hub 现有非对称身份设施签发，固定：

- `aud=hub-model-gateway`；
- `scope=model:invoke`；
- 携带 `user_id`、`agent_id`、`profile_id`、`model_id`、`jti`；
- 有效期最多 120 秒；
- 只能调用一次或绑定一个请求编号，重放必须拒绝。

Connected Agent 调用时，Hub 随当前 run 下发 run 级委托，Agent 再用自身客户端凭据兑换一次 Grant。Featured 工作台模式下，瀚海行保留服务端 workspace 级委托，并在每次模型调用前使用新的 `request_id` 向 Hub 换取 120 秒 Grant；浏览器 Cookie、用户 API Key 和 Agent 长期凭据都不进入该 Grant。

本地开发私网放行由 `HUB_MODEL_PROVIDER_ORIGIN_ALLOWLIST` 精确列出 origin，默认空；`HUB_ALLOW_LOCAL_MODEL_PROVIDERS` 默认 `false`。生产模式不得通过通配符允许任意内网地址。

### 8.8 Agent Contract 扩展

Manifest 增加可选能力声明，概念字段如下：

```json
{
  "capabilities": ["platform-model-gateway"],
  "model_runtime": {
    "mode": "platform_optional",
    "gateway_contract": "campus-model-gateway-v1",
    "supported_api_styles": ["responses", "chat_completions"]
  }
}
```

建议支持三种模式：

| 模式 | 含义 |
| --- | --- |
| `agent_managed` | Agent 只使用自身模型配置，保持现有第三方行为 |
| `platform_optional` | 用户可在 Hub Profile 与 Agent 自身配置之间选择 |
| `platform_required` | Agent 必须使用平台 Gateway，适合平台托管的轻量 Agent |

Featured 瀚海行首期使用 `platform_optional`。这保证 Hub 演示时可以无缝选择多模型，同时保留瀚海行独立启动和原有设置页。

该字段是现有 Campus Agent Hub Contract v1 的可选、向后兼容扩展，不替换当前 Manifest、AG-UI、simple-chat、健康检查或身份契约。没有该字段的 Agent 一律按 `agent_managed` 处理。

### 8.9 安全边界

- Base URL 只允许 HTTPS；本地开发地址只能由管理员显式白名单放行。
- 服务端执行 DNS/IP 检查，拒绝 loopback、private、link-local、metadata、危险重定向和跨源凭据复用。
- Base URL 变更时必须重新提供对应 API Key。
- 密钥使用部署时注入的独立主密钥加密；主密钥不进入数据库或仓库。
- 删除 Profile 前必须先解除 Agent 绑定；删除接口撤销未过期授权后再删除密文和模型目录，历史审计只保留 Profile ID 和脱敏状态。
- 用户选择外部模型服务前显示数据将发送到该服务商的明确说明。
- Gateway 设置请求大小、响应大小、超时、并发和频率限制。
- 上游错误只返回脱敏错误码，不把响应头、完整 URL 或密钥片段返回浏览器。

### 8.10 旧配置迁移与回退

Hub 当前浏览器 `localStorage` 中的 `hub_user_model_settings` 可能包含旧单套配置。迁移时：

1. 模型配置页检测到旧记录后显示一次性迁移提示，不静默上传；
2. 用户明确确认后，通过 Profile API 创建“旧版导入”Profile；
3. 服务端保存成功并返回安全摘要后，浏览器删除旧记录中的 API Key；
4. 迁移失败时保留旧记录并给出重试，不产生半套 Profile；
5. 生产部署只有在 HTTPS 下允许迁移包含 API Key 的记录。

瀚海行现有 `/api/settings` 与服务端 `.env` 不自动复制到 Hub，继续作为 Agent 独立运行回退。新链路验收通过前，瀚海行优先级为“本轮明确授权的 Hub Profile；否则使用原有服务端配置”。

### 8.11 分阶段实现

T4 内部仍分为五个可验证子阶段：

1. 功能开关、运行时主密钥准备和“关闭功能不破坏基线”的启动回归；
2. Model Profile 数据模型、加密存储和多配置界面；
3. 连接测试、模型发现、默认 Profile 与按 Agent 绑定；
4. Hub Model Gateway、委托和短期 Grant；
5. 瀚海行 `platform_optional` 适配、独立配置回退和端到端测试。

不得先删除 Hub 当前表单或瀚海行现有设置，再等待新链路补齐。迁移期间只有经过用户确认的旧浏览器配置才回填为默认 Profile；新链路验收通过前保留旧读取回退。

### 8.12 验收标准

1. 同一用户可以保存至少两套服务，例如 OpenAI-compatible 和 DeepSeek-compatible。
2. 每套配置可以独立测试连接、发现模型并选择默认模型。
3. 用户可以设置全局默认和瀚海行专属配置，切换后完成真实问答。
4. 瀚海行经 Hub 启动时使用所选 Profile；瀚海行单独启动时仍能使用自身配置。
5. 第三方未声明 Gateway 能力时保持原行为，不接收任何用户模型信息。
6. API Key 不出现在页面源码、网络响应、日志、数据库明文字段或 Agent 进程环境中。
7. 普通用户不能使用他人的 Profile，开发者和 Agent 不能扩大令牌范围。
8. 删除、禁用或轮换 Profile 后，旧短期凭据在限定时间后失效。
9. Responses 与 Chat Completions 两条协议路径均有真实或受控兼容测试。
10. 模型服务不可用时错误可恢复，不破坏瀚海行原有知识库和直接问答。
11. 功能开关关闭且没有主密钥文件时，Hub 和原有三服务仍能正常启动。
12. 模型发现可以展示服务端返回的完整目录，但默认模型和 Agent 绑定下拉只允许选择已确认可文本聊天的模型；图片、音频、Realtime 和自动审查等非聊天模型不可绑定。
13. Profile 禁用、Agent 绑定切换、工作台退出或身份切换后，旧 workspace 委托不能再兑换新的 Grant；已经过期或被撤销的 Grant 不能调用 Gateway。

### 8.13 浏览器与安全审计

- 模型配置列表、添加、编辑、测试、发现模型和删除流程；
- 两个 Profile 切换后的瀚海行问答和模型标识；
- Profile 禁用后的受控错误与回退；
- 浏览器开发者工具检查所有响应无明文 API Key；
- 服务端日志脱敏检查；
- 恶意 Base URL、跨用户 Profile ID、越权 Agent 和过期令牌测试；
- 桌面和移动端配置页面截图，密钥字段必须为掩码。

## 9. T5：瀚海行课程知识广场

### 9.1 目标

保留现有知识广场投稿、审核、订阅、暂停、恢复和版本治理能力，在其上增加适合比赛演示的课程内容与课程集市式浏览入口，使页面饱满、真实且能继续进入现有知识库详情和 RAG 流程。

### 9.2 首批演示课程

| 课程 | 建议内容分类 | 首批数据边界 |
| --- | --- | --- |
| 数学分析 B1 | 教材、复习提纲、习题课、历年卷 | 第一版真实可检索；只关联现有 `math-analysis-b1.yaml` 中许可允许且解析成功的资料 |
| 线性代数 B1 | 矩阵、线性空间、特征值、往年题 | 第一版仅演示元数据，显示“资料待补充” |
| 概率论与数理统计 | 分布、极限定理、参数估计、复习笔记 | 通过来源与许可审计并新增 manifest 后才可真实检索；否则仅演示元数据 |
| 大学物理 | 力学、电磁学、复习提纲 | 第一版仅演示元数据，显示“资料待补充” |
| 数据结构 | 算法专题、实验、考试题型 | 第一版仅演示元数据，显示“资料待补充” |
| 程序设计基础 | 语法、实验、常见题型 | 第一版仅演示元数据，显示“资料待补充” |

本轮最低交付口径固定为“数学分析真实可检索 + 线性代数、概率论与数理统计、大学物理、数据结构、程序设计基础五门演示课程卡片”。演示元数据不等于可以公开下载的课程资料。没有明确许可的课程只展示课程卡片和演示说明，不生成虚假的文档数量、订阅人数、评价或更新时间。

如果本轮把当前概率论笔记纳入真实 RAG，必须新增 `data/manifests/probability-statistics.yaml`，记录来源、许可、文件哈希和 Markdown 解析方式，并通过与数学分析相同的导入、权限、检索和删除失效测试。未完成这些条件时，概率论卡片不得显示“可检索”。

### 9.3 课程卡片

广场主视图使用响应式课程网格。单张卡片包含：

- 本地、授权清晰的课程封面或图案；
- 课程名称；
- 一句话简介；
- 真实资料数量；
- 最近更新时间；
- 内容标签；
- “演示知识库”状态；
- 订阅或进入按钮。

封面优先使用团队生成或拥有授权的本地位图，不使用远程热链、来源不明图片或纯装饰渐变。课程卡片圆角不超过 8 像素，固定封面比例和内容区最小高度，避免长标题导致同排跳动。

点击卡片进入现有知识库详情，不新建第二套订阅、预览或 RAG 逻辑。已订阅、作者和管理员继续使用当前权限矩阵。

### 9.4 幂等演示数据

演示元数据保存在 `data/manifests/marketplace-demo.yaml`，新增 `course-agent seed-marketplace <manifest>` CLI 入口。开发环境从仓库根目录使用：

```powershell
Push-Location .\apps\course-agent
.\.venv\Scripts\python.exe -m course_agent.cli seed-marketplace ..\..\data\manifests\marketplace-demo.yaml
Pop-Location
```

Docker 演示在瀚海行初始化完成后运行同一子命令，路径映射到容器内仓库目录。seed 使用稳定 ID 或唯一 slug 判断是否已经创建：

- 首次运行创建缺失的演示知识库；
- 重复运行只补缺失项，不重复创建、不覆盖用户修改；
- 不直接编辑 SQLite 文件；
- 数学分析和概统只关联已经进入正式解析链路且许可允许的资料；
- 演示课程与用户投稿使用不同的来源类型和审计记录；
- 生产部署可以通过配置关闭演示 seed。

建议固定 slug 为 `math-analysis-b1`、`linear-algebra-b1`、`probability-statistics`、`college-physics`、`data-structures` 和 `programming-fundamentals`。seed 不覆盖用户已经修改的标题、简介、封面或发布状态；需要更新官方 seed 时通过显式版本号和审计记录完成。

### 9.5 验收标准

1. 全新演示环境首次启动后至少出现六张课程卡片。
2. 重复执行 seed 后课程、文档和订阅记录不重复。
3. 卡片显示的资料数和更新时间来自数据库真实值，不写死在前端。
4. 点击卡片、查看详情、订阅、进入知识库、勾选资料和 RAG 问答继续使用现有链路。
5. 未订阅用户、订阅者、作者和管理员的预览、下载、RAG 权限没有变化。
6. 课程封面断网可用，图片加载失败有稳定占位，不改变卡片尺寸。
7. 1440×900、1280×800、768×1024 和 390×844 下无重叠、截断错误和横向滚动。
8. 所有演示库具有清晰标识，不把模拟内容描述为真实第三方资源。

### 9.6 截图审计

- 桌面端六门课程首屏；
- 移动端单列或双列课程卡片；
- 数学分析课程详情与资料列表；
- 演示元数据课程的空资料说明；
- 普通用户订阅前后状态；
- 管理员审核页和原有知识库治理能力。

## 10. 执行顺序与依赖

正式实施按以下顺序进行：

### 阶段 0：冻结并验证当前基线

部署最新 `main`，记录测试结果、三服务健康状态、固定 Demo 结果和基线截图。此阶段不改变产品。

### 阶段 1：T2 Agent 直接进入

改动小、用户收益高，先建立顺畅的演示主路径。完成后立即执行一次完整 Featured 授权和三类 Agent 启动回归。

### 阶段 2：T1 统一字体

在行为稳定后统一视觉基线。字体变更必须单独提交，便于发现换行和布局回归。

### 阶段 3：T3 三层身份权限

角色矩阵为开发者提交、管理员审核以及后续模型配置所有权提供边界，应在 T4 前完成。

### 阶段 4：T5 课程知识广场

利用已经稳定的角色权限填充课程市场，完成比赛视频最直观的内容层展示。

### 阶段 5：T4 多模型配置与兼容

该任务涉及密钥、数据库、Gateway、Contract 和瀚海行适配，风险和测试量最大，单独实施。按 8.11 的五个子阶段逐段启用，不与视觉任务混做。

### 阶段 6：整体验收

五项全部完成后，从干净环境重新部署并执行一次跨任务验收，确认组合后的行为没有产生局部测试无法发现的回归。

## 11. 自动化与真实浏览器验收基线

### 11.1 固定命令

以下命令均从仓库根目录执行。后续若测试入口因本轮实现发生变化，必须先更新本文档和 README，再以新命令作为验收依据。队友新电脑需要先安装 Python 3.12 与当前 LTS Node.js；命令不可用时应停止并给出依赖提示，不得跳过对应测试。

首次准备或依赖变化后，分别创建三个应用的隔离环境并安装测试依赖。虚拟环境不提交 Git：

```powershell
Push-Location .\apps\hub
if (-not (Test-Path .\.venv\Scripts\python.exe)) { py -3.12 -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
Pop-Location

Push-Location .\apps\course-agent
if (-not (Test-Path .\.venv\Scripts\python.exe)) { py -3.12 -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
Pop-Location

Push-Location .\apps\demo-agent
if (-not (Test-Path .\.venv\Scripts\python.exe)) { py -3.12 -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
Pop-Location
```

```powershell
# Hub Python
Push-Location .\apps\hub
.\.venv\Scripts\python.exe -m pytest -q
Pop-Location

# Hub JavaScript
node --test .\apps\hub\tests-js\hub-core.test.mjs .\apps\hub\tests-js\starfield.test.mjs .\apps\hub\tests-js\theme.test.mjs

# 瀚海行 Python
Push-Location .\apps\course-agent
.\.venv\Scripts\python.exe -m pytest -q
Pop-Location

# 瀚海行 JavaScript
node --test .\apps\course-agent\tests-js\streaming.test.mjs .\apps\course-agent\tests-js\theme.test.mjs

# 独立 Demo Agent
Push-Location .\apps\demo-agent
.\.venv\Scripts\python.exe -m pytest -q
Pop-Location

# Campus Agent Hub Contract v1
npm --prefix .\contracts\campus-agent-hub\v1 test

# 启动真实三服务
powershell -ExecutionPolicy Bypass -File .\deploy\run-demo.ps1

# 固定 Demo 10 轮验收并保存机器可读证据
py -3.12 .\deploy\verify_demo.py --iterations 10 --minimum-success 9 --output .\var\audits\five-core-optimizations\final\demo-acceptance.json
```

固定 Demo 验收至少成功 9 轮才通过。测试输出和截图不得包含 API Key、授权码、JWT、Cookie、完整私人聊天正文或完整私人资料正文。

### 11.2 每项必须新增或更新的测试

| 任务 | 最低专项测试要求 |
| --- | --- |
| T1 | 字体 CSS 与本地资源存在性、无远程字体引用、主题首次加载和长文本布局浏览器检查 |
| T2 | `hub-core` 三类主操作决策、所有入口一致性、Featured 授权码成功与重放拒绝 |
| T3 | 前端不替换管理员身份、角色导航矩阵、提交/审核/凭据/健康/审计接口四身份权限矩阵 |
| T4 | Profile CRUD 与所有权、加密存储、SSRF、防重放 Grant、禁用/换绑后的委托撤销、模型发现、两种上游协议、SSE 归一化、瀚海行回退 |
| T5 | seed 幂等、真实计数、无资料状态、订阅/RAG 权限回归、课程网格响应式浏览器检查 |

### 11.3 审计证据目录

每项证据保存在 Git 已忽略的目录：

```text
var/audits/five-core-optimizations/
├── baseline/
├── T1-fonts/
├── T2-direct-launch/
├── T3-roles/
├── T4-model-profiles/
├── T5-marketplace/
└── final/
```

每个目录至少包含 `test-summary.txt`、`browser-checklist.md`、桌面截图、移动端截图和必要的脱敏 JSON。证据不默认提交 Git；更新日志记录结论、视口和相对证据位置。

截图统一使用 `<页面>-<身份>-<视口>-<状态>.png`，例如 `hub-home-demo-c-1440x900-dark.png`、`hanhai-workspace-demo-c-390x844-loaded.png`。每项另保存 `console.json` 和 `network-summary.json`，只记录错误数量、脱敏 URL path、状态码和时间，不保存请求正文、响应正文或凭据。

### 11.4 浏览器验收

浏览器验收使用真实运行服务，不使用静态 HTML 替代。固定视口建议为：

- 1440×900：比赛演示桌面主视口；
- 1280×800：普通笔记本；
- 390×844：移动端；
- T5 额外检查 768×1024 的平板布局。

每个页面检查：

- 首屏是否非空、是否完成加载；
- 是否存在文字、按钮、虚拟形象或卡片重叠；
- 关键文字是否截断或溢出；
- 点击、键盘操作和移动端交互是否可用；
- 浏览器控制台未处理异常数量是否为 0；
- 网络请求是否存在 4xx/5xx、重复请求或外部字体依赖；
- 截图与该 To-do 的预期结果是否一致。

每项 `browser-checklist.md` 固定记录：起始 URL、测试身份、视口、操作步骤、目标 URL、截图文件、控制台错误数、失败网络请求数和 PASS/FAIL。至少覆盖该任务列出的桌面主流程与 390×844 移动主流程；只有截图而没有可复现操作记录不能判定通过。

截图属于验收证据，默认保存在被 Git 忽略的审计目录，不提交含用户信息的图片。设计文档和更新日志只记录视口、页面、结论和证据位置；比赛需要的公开截图再经过脱敏后进入正式材料。

## 12. 五项完成后的跨任务验收场景

最终必须完整走通以下流程：

1. 清除浏览器主题偏好，打开 Hub，确认深色主题和本地字体立即生效。
2. 以普通用户浏览课程集市，订阅数学分析知识库并进入资料详情。
3. 返回 Hub，点击瀚海行卡片，一次进入正式工作台。
4. 选择 Hub 中的两个不同 Profile，或同一兼容 Base URL 下两个不同模型配置，分别完成一次直接问答和一次课程资料问答。
5. 关闭 Hub Profile 或单独启动瀚海行，确认瀚海行原有模型配置仍可回退使用。
6. 切换开发者，确认能看到提交入口和自己的提交，不能进入审核页。
7. 切换管理员，确认能看到审核入口，并完成一次独立 Demo Agent 的审核或状态查看。
8. 切回普通用户，确认开发者和管理员模块消失，直接访问对应 API 返回 403。
9. 执行固定三服务验收，确认 Registry、Gateway、JWT、Featured 工作台和两个 Agent 均无回归。

## 13. 完成定义

单个 To-do 只有同时满足以下条件，才能标记完成：

- 实现与本文档一致，没有扩大范围；
- 专项测试和全量回归全部通过；
- 真实部署和浏览器流程通过；
- 桌面、移动端截图已检查，控制台无未处理异常；
- 安全与权限审计没有高优先级未解决问题；
- 更新日志已记录用户可感知变化和验证结果；
- 没有破坏上一阶段已经通过的功能；
- 可以说明失败时如何回退。

五项全部满足上述条件，并通过第 12 节的跨任务验收后，本轮核心功能优化才算整体交付。

## 14. 非目标

本轮不实现：

- 生产级账号注册、统一登录、找回密码和真实学生认证；
- Main Agent、自然语言自动选择 Agent、Agent 间递归调用；
- 商业计费、模型额度结算和开放式第三方代码托管；
- 浅色主题整体视觉重做；
- 未经授权的课程资料抓取或公开再分发；
- 为了多模型能力而强制重写所有第三方 Agent；
- 与五项任务无关的大规模前端框架迁移或后端重构。

## 15. 审核结论记录

队长审核后在此补充：

- 审核日期；
- 审核结论；
- 需要修改的条目；
- 是否批准进入目标模式实施；
- 批准后的实施起点提交号。
