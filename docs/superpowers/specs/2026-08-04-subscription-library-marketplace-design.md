# 订阅知识库广场设计

## 1. 目标

在现有课程资料 Agent 中增加一个免费的订阅知识库广场。用户可以浏览经审核的公开课程知识库、订阅后在独立知识空间中检索问答，也可以从自己的个人知识库明确选择资料，生成不可变投稿快照并交由管理员审核发布。

本功能不实现付费、结算、自动混合检索或无需审核的公开发布。

## 2. 已确认的产品边界

1. 订阅采用只读引用，不把公开资料复制到每个用户的个人空间。
2. 投稿时生成独立发布快照，个人知识库后续变化不会自动进入公开版本。
3. 用户必须明确勾选投稿资料，未选择的个人内容永远不进入快照。
4. 资料默认允许平台内检索与受限预览，是否允许下载按单份资料策略决定。
5. 新版审核通过后，所有订阅者自动使用最新版本；旧版本仅用于审计和回滚。
6. 订阅库在侧栏中作为独立 `subscribed` 知识空间使用，MVP 不跨空间自动混合检索。
7. 广场免费，但登录用户需要点击订阅后才能执行完整阅读、检索和问答。

## 3. 核心对象

### 3.1 公开知识库

`published_libraries` 表示广场中的逻辑知识库：

- `id`
- `space_id`：唯一的 `space_type=subscribed` 内容空间
- `author_id`
- `name`
- `course`
- `description`
- `tags_json`
- `status`：`pending`、`published`、`suspended`、`withdrawn`
- `current_version_id`
- `created_at`、`updated_at`

### 3.2 发布版本

`publication_versions` 表示不可变投稿快照：

- `id`
- `library_id`
- `version_number`
- `status`：`pending`、`changes_requested`、`rejected`、`withdrawn`、`published`、`superseded`
- `submitted_by`
- `base_version_id`：提交新版时记录当时的线上版本，审批时做 compare-and-set
- `reviewed_by`
- `review_note`
- `submitted_at`、`reviewed_at`、`published_at`

### 3.3 版本资料

`publication_documents` 把快照版本与独立复制的公开文档关联：

- `version_id`
- `document_id`
- `source_document_id`：只用于作者和管理员审计，不向订阅者暴露私人空间路径
- `use_in_rag`
- `can_preview`
- `can_download`
- `review_status`
- `review_note`

投稿时，每份被选中的个人文档只复制一次到公开知识库空间。新文档在审核期间使用 `staged` 状态，不参与订阅者检索；版本批准时才原子切换为 `active`。旧版文档切换为 `superseded`。

发布版本始终不可变。`changes_requested` 后作者不能修改原快照，只能提交一个新的、版本号递增的 `pending` 快照；旧快照保留审核记录。管理员回滚同样不修改旧快照，只原子切换当前版本和文档状态。

### 3.4 订阅关系

`library_subscriptions` 保存：

- `library_id`
- `user_id`
- `status`：`active`、`cancelled`
- `subscribed_at`、`cancelled_at`

订阅关系与邀请制 `memberships` 分开。共享空间成员和公开订阅者不是同一种权限主体。

## 4. 主流程

### 4.1 投稿

1. 用户在自己的 `personal` 空间点击“投稿到广场”。
2. 填写名称、课程、简介和标签，明确勾选资料。
3. 服务端在写入前一次性验证所有文档均属于当前用户的个人空间且处于有效状态，拒绝重复 ID。
4. 预生成 `library_id`、`version_id`、文档 ID 和目标文件清单；把源文件复制到不可公开访问的最终存储路径，但此时不建立任何公开权限。
5. 在内存或临时结果中完成解析、OCR 读取和分块准备，不调用会自行提交事务的单文档写入函数。
6. 使用 `BEGIN IMMEDIATE`，一次写入 `documents(status='staged')`、`revisions`、`pages`、`chunks`、`chunk_fts`、`publication_versions` 和 `publication_documents`，成功后统一提交。
7. 任意复制、解析、FTS 或数据库步骤失败时回滚事务，并删除本次目标文件清单中的所有文件；旧线上版本保持不变。
8. 审批阶段只执行数据库状态切换，不再移动或重新解析文件。

### 4.2 审核与发布

1. 管理员查看待审核版本及每份资料的来源、许可、解析状态和访问策略。
2. 管理员可批准、要求修改或拒绝。
3. 批准时在单一事务内 compare-and-set `base_version_id`：旧版文档失效、新版文档激活、旧版标记 `superseded`、新版标记 `published`、更新 `current_version_id`。线上版本在投稿后发生变化时返回 `409 publication_base_changed`，不得覆盖新版本。
4. 管理员可暂停整个公开库；暂停后订阅检索、预览和下载立即失效。

### 4.3 浏览和订阅

1. 登录用户可浏览广场元数据、资料清单和允许公开的预览。
2. 点击订阅后创建或恢复 `active` 订阅关系。
3. `/api/spaces` 将有效订阅库作为 `subscribed` 分组返回。
4. 用户进入该空间后沿用当前单空间资料选择和 RAG 问答流程。
5. 取消订阅后侧栏入口、文件访问和检索权限立即失效。

## 5. 权限规则

- `personal`：仅 owner 可读写，不能被广场直接引用。
- `shared`：继续使用邀请制 `memberships`。
- `subscribed`：作者和管理员可管理；普通用户必须同时满足有效订阅、知识库为 `published`、文档属于当前发布版本。
- 检索权限过滤必须发生在搜索和模型调用之前。
- `use_in_rag=false` 的资料不得进入检索结果。
- `can_preview=false` 时拒绝页文本和页图片接口。
- `can_download=false` 时拒绝原文件接口，但作者和管理员可以在审核流程中访问。
- 浏览器按钮隐藏不构成权限控制，所有接口都要服务端复核。

### 5.1 统一有效权限

所有空间列表、文档列表、页文本、页图片、原文件和检索必须复用同一套 `effective_space_access` / `effective_document_access` 规则：

- `personal/shared`：有效 `memberships`；
- `subscribed`：有效 `library_subscriptions`、公开库状态为 `published`、请求版本等于 `current_version_id`、文档存在于该版本；
- 作者和管理员的审核访问走独立 `manage/review` 权限，不伪装成普通订阅者；
- FTS 最终查询也必须使用已经鉴权的文档 ID，不能再次硬连接 `memberships`；
- 取消订阅、暂停、下架和版本切换后，旧文档 ID 在下一次请求中立即失效。

### 5.2 操作权限矩阵

| 主体 | 广场元数据 | 页文本/页图片 | 原文件 `/file` | RAG | 管理 |
|---|---|---|---|---|---|
| 未登录 | 不开放 | 不开放 | 不开放 | 不开放 | 不开放 |
| 已登录未订阅 | 可见 | 仅 `can_preview=true` 的公开预览 | 不开放 | 不开放 | 不开放 |
| 已订阅 | 可见 | `can_preview=true` | `can_download=true` | `use_in_rag=true` | 不开放 |
| 作者 | 自己的投稿均可见 | 可审核访问 | 可审核访问 | 仅线上版本按普通规则 | 提交新版本、查看审核意见、撤回未发布版本、申请下架 |
| 管理员 | 全部可见 | 可审核访问 | 可审核访问 | 不以管理员身份绕过线上 RAG | 审批、调整资料策略、暂停、恢复和回滚 |

`/api/documents/{id}/pages/{n}` 与 `/pages/{n}/image` 属于预览；`/api/documents/{id}/file` 属于原文件下载。即使客户端传入文档 ID，`use_in_rag=false` 的文档也必须在模型调用前被拒绝。

作者不能审批自己的投稿，也不能暂停、恢复或管理他人投稿。管理员审核响应不得暴露个人空间文件路径；`source_document_id` 只用于服务端审计。

## 6. API

### 广场与订阅

- `GET /api/marketplace/libraries?q=&course=`
- `GET /api/marketplace/libraries/{library_id}`
- `POST /api/marketplace/libraries/{library_id}/subscribe`
- `DELETE /api/marketplace/libraries/{library_id}/subscription`

### 投稿者

- `GET /api/publications/mine`
- `POST /api/publications`
- `POST /api/publications/{library_id}/versions`
- `POST /api/publication-versions/{version_id}/withdraw`
- `POST /api/publications/{library_id}/withdraw`

### 管理员

- `GET /api/admin/publication-versions?status=pending`
- `GET /api/admin/publication-versions/{version_id}`
- `PATCH /api/admin/publication-versions/{version_id}`：`approve`、`changes_requested`、`reject`
- `POST /api/admin/publications/{library_id}/suspend`
- `POST /api/admin/publications/{library_id}/restore`
- `POST /api/admin/publications/{library_id}/rollback`

### 6.1 最小请求契约

`POST /api/publications`：

```json
{
  "name": "数学分析期末复习",
  "course": "数学分析 B1",
  "description": "课程重点和例题整理",
  "tags": ["期末", "例题"],
  "documents": [
    {
      "document_id": "personal-document-id",
      "use_in_rag": true,
      "can_preview": true,
      "can_download": false
    }
  ]
}
```

成功返回 `201`，包含 `library`、`version` 和不含私人路径的文档清单。`POST /api/publications/{id}/versions` 使用同一 body，但名称、课程、简介和标签作为该库的新元数据候选；仅作者可调用。

`PATCH /api/admin/publication-versions/{id}`：

```json
{
  "action": "approve",
  "review_note": "来源与许可已确认",
  "document_reviews": [
    {
      "document_id": "snapshot-document-id",
      "use_in_rag": true,
      "can_preview": true,
      "can_download": false,
      "review_note": "仅限平台内使用"
    }
  ]
}
```

`action` 仅允许 `approve`、`changes_requested`、`reject`。只有管理员可调用，且不能审批自己提交的版本。批准返回更新后的公开库和版本；状态冲突返回 `409`。

列表接口统一使用 `page`、`page_size`，返回 `items`、`page`、`page_size`、`total`。广场详情只返回公开元数据和策略，不返回 `source_document_id`、私人 `space_id` 或文件路径。

订阅和取消订阅使用 upsert，重复操作返回当前状态，不创建重复关系。主要错误码包括：`publication_document_forbidden`、`publication_not_found`、`publication_not_published`、`already_pending_review`、`review_forbidden`、`invalid_review_transition`、`publication_base_changed`。

### 6.2 状态迁移

| 当前状态 | 操作者 | 动作 | 目标状态 |
|---|---|---|---|
| `pending` | 管理员 | 要求修改 | `changes_requested` |
| `pending` | 管理员 | 拒绝 | `rejected` |
| `pending` | 管理员 | 批准 | `published`，旧线上版本变为 `superseded` |
| `pending/changes_requested` | 作者 | 撤回 | `withdrawn` |
| `changes_requested/rejected` | 作者 | 重新投稿 | 创建新的递增版本，原版本不变 |
| `published` 库 | 管理员 | 暂停 | 库变为 `suspended`，当前版本不变 |
| `suspended` 库 | 管理员 | 恢复 | 库变为 `published` |
| `published/suspended` 库 | 管理员 | 回滚 | 目标旧版本变为 `published`，原当前版本变为 `superseded` |
| `published` 库 | 作者申请或管理员 | 下架 | 库变为 `withdrawn` |

## 7. 前端

新增“知识广场”一级视图：

- 公开知识库搜索、课程筛选和列表；
- 详情区展示作者、课程、简介、标签、资料清单、许可和订阅状态；
- 订阅/取消订阅按钮；
- “我的投稿”视图；
- 管理员可见的“待审核”视图。

个人知识库工具栏新增“投稿到广场”。投稿对话框包含元数据表单、文档复选框和逐份资料的预览/下载授权。管理员审核详情显示解析状态并支持逐份调整访问策略。

订阅成功后不离开广场强制跳转，提供“进入知识库”命令。订阅空间沿用现有知识库三栏界面和问答流程。

## 8. 错误与一致性

- 重复订阅幂等成功。
- 重复取消订阅幂等成功。
- 投稿包含越权、已删除或非个人空间文档时整体拒绝。
- 审核版本不是 `pending` 时拒绝重复审批。
- 发布切换失败时保持旧版继续在线。
- 删除个人原文档不影响已经生成的公开快照。
- 公开库暂停、下架或取消订阅后，旧 `document_id` 也不能继续访问或检索。
- 投稿、审核和订阅操作记录结构化审计事件，不记录私人正文和密钥。

## 9. 测试与验收

至少覆盖：

1. 用户只能投稿自己个人空间的有效文档。
2. 未勾选文档不进入快照。
3. 待审核文档对普通用户不可见、不可检索。
4. 非管理员不能审批。
5. 审核通过后广场可见，未订阅不能完整访问。
6. 两个用户订阅同一库时共享同一公开文档和索引，不复制文件。
7. 取消订阅后文件、页面和检索全部失效。
8. `can_preview`、`can_download`、`use_in_rag` 分别生效。
9. 新版批准后订阅者自动使用新版，旧文档 ID 失效。
10. 暂停知识库后所有订阅访问立即失效，恢复后重新可用。
11. 投稿来源删除或私人空间后续修改不影响已发布快照。
12. 桌面和移动端完成“投稿、审核、订阅、进入知识库、问答、取消订阅”浏览器流程。
13. 多文档投稿中途失败时，数据库、FTS 和文件系统均无残留。
14. 订阅权限不依赖 `memberships`，搜索 SQL 不会因旧 membership 连接漏掉订阅文档。
15. 未订阅、已订阅、作者和管理员分别符合预览/下载/RAG 权限矩阵。
16. 作者不能审批、暂停或恢复知识库，管理员响应不泄露私人路径。
17. `changes_requested`、撤回、拒绝、暂停、恢复、回滚和 base-version 冲突均按状态机处理。
