# 评课社区集成调研

## 1. 调研结论

评课社区适合作为课程评价 Deep Research Agent 的主要外部来源，但接入方式应采用“公开 HTML + 搜索 token + 用户本地登录连接器”的组合，而不是把原站当作开放数据 API。

关键边界如下：

- 公开页面提供课程信息、评分、公开点评和部分站点说明，适合公开模式采集。
- 源码显示登录身份会影响点评可见范围；公开模式无法代表登录学生看到的完整信息。
- 站点认证基于 Flask 登录会话和 CSRF；没有发现可供第三方长期调用的通用 bearer API token。
- 搜索 token 由 `POST /api/search/token` 生成，是防滥用的一次性/限时搜索令牌，不是登录凭据。
- `/course/<id>/reviews/` 返回拼接 HTML 片段，缺少鉴权、过滤和稳定契约，不应当作官方稳定 API。
- 附件上传接口需要登录；登录后课程页中的真实 `/uploads/files/...` 链接只应在用户授权范围内本地处理。
- 评课社区代码 AGPLv3 不等于用户点评、回复、头像、课件或附件获得开源许可；产品必须避免批量复制或公开再分发全量内容。
- 应联系 `service@icourse.club` 争取只读 API、速率限制、数据授权和比赛演示许可。

## 2. 主要来源

| 来源 | 用途 |
| --- | --- |
| <https://icourse.club/about/> | 站点定位、愿景、开源仓库、联系方式 |
| <https://icourse.club/community-rules/> | 可访问性、账号注册边界、隐私与内容规则 |
| <https://github.com/USTC-iCourse/ustc-course> | 源码仓库、AGPLv3 许可、项目性质 |
| <https://github.com/USTC-iCourse/ustc-course/blob/master/app/views/course.py> | 课程页、点评过滤、课程调试式 reviews 路由 |
| <https://github.com/USTC-iCourse/ustc-course/blob/master/app/views/home.py> | 登录流程、全站最新点评、搜索路由与 search token 校验 |
| <https://github.com/USTC-iCourse/ustc-course/blob/master/app/views/api.py> | 搜索 token 生成、上传文件接口、需要登录的 API |
| <https://github.com/USTC-iCourse/ustc-course/blob/master/app/models/utils.py> | `SearchToken` 模型、5 分钟有效期、同 IP 复用逻辑 |
| <https://github.com/USTC-iCourse/ustc-course/blob/master/SEARCH_TOKEN_README.md> | 搜索 token 设计说明 |

## 3. 站点定位与内容政策

`about` 页面说明评课社区服务于科大师生，目标是点评课程、获取课程信息，并促进校内课程信息公开、帮助学生找到更适合自己的课程。页面也说明评课社区代码以 GNU AGPL v3 协议开源，并链接到 `USTC-iCourse/ustc-course` 仓库。

`community-rules` 页面说明评课社区面向中国科大学生，课程信息和点评可公开访问，但只有登录账号后才可以发布新内容；只有拥有科大邮箱的学生或教师可以注册账号。社区规则还强调不会公开用户邮箱和学号，但用户公开账号信息和言论仍可能泄露真实身份，因此需要注意隐私保护。

对本项目的影响：

- 可以把公开页面作为选课信息聚合来源，但要保留来源链接和采集时间。
- 登录增强模式只能使用用户自己账号可见内容，不能共享队长或队友 Cookie。
- 报告应避免展示用户名、个人主页、邮箱、学号、点赞用户列表等可识别信息。
- 社区规则禁止侵犯隐私和侵犯知识产权内容；产品必须支持内容最小化、投诉和删除。

## 4. 源码许可与内容许可边界

GitHub 仓库元数据和 `about` 页面均显示源码以 AGPLv3 开源。AGPLv3 授权对象是软件代码，不自动覆盖站内用户点评、回复、头像、教师材料、课件、附件或其他上传内容。

因此集成策略应当是：

- 源码可用于理解接口、路由、权限和数据结构。
- 用户内容只做摘要、证据定位和最小必要短摘录。
- 不把全量点评或附件复制进仓库、公开演示站或共享数据集。
- 对附件内容采用本地处理优先，公开报告只展示元数据和来源。
- 若要做批量数据分析、公开榜单或共享语料，先获得维护者授权。

## 5. 认证与登录边界

`home.py` 中登录路由是 `/signin/`，使用表单提交、`User.authenticate`、`login_user` 和 CSRF 保护。`layout.html` 中 AJAX POST 会设置 `X-CSRFToken`。这说明站点主认证是浏览器会话 Cookie + CSRF，而不是第三方长期 bearer token。

`api.py` 中存在第三方登录相关路由 `/signin-3rdparty/`，但该流程基于 challenge、邮箱、日期和 token 给第三方站点完成一次登录验证，不是通用数据访问 API token。它不适合作为课程评价抓取凭据。

对产品设计的约束：

- 不要求用户复制 Cookie、CSRF token 或登录响应。
- 不在日志、报告、缓存、错误信息里打印任何会话值。
- 服务端公共连接器只访问公开页面和公开搜索 token。
- 登录限定内容用本地浏览器连接器读取当前页面可见结构化信息，Cookie 不离开用户设备。
- 演示中不得共享队长 Cookie，也不得把登录增强报告直接作为公开版本导出。

## 6. 搜索 token 机制

`SEARCH_TOKEN_README.md` 说明搜索 token 是为了搜索 API 防 DoS：用户搜索前先获取 token，token 限时 5 分钟，并有一次性使用语义和旧 token 清理。

源码细节：

- `api.py` 定义 `POST /api/search/token`，CSRF exempt，无需登录；服务端记录请求 IP，调用 `SearchToken.generate(ip_address)` 并返回 JSON。
- `utils.py` 中 `SearchToken.generate` 生成 urlsafe token 并保存 IP。
- `SearchToken.validate_and_use` 校验 token 是否存在、是否超过 5 分钟；首次使用会标记 `used=True`。
- 同一 IP 在有效期内可复用已使用 token；不同 IP 复用会失败。
- `home.py` 中 `/search/` 和 `/search-reviews/` 都从 query string 读取 `token`，无 token 或校验失败会返回 403。
- `layout.html` 中搜索表单会先 `POST` 获取 token，再跳转到带 `q` 和 `token` 的搜索 URL。

结论：

- 搜索 token 可以在公开模式下按原站前端逻辑使用。
- 搜索 token 不是登录凭据，不授予登录态，不应被命名为 access token。
- 搜索 token 短期有效，不适合进入长期缓存键、日志或报告。
- 若搜索 token 获取失败，产品应退回手动课程 URL 或稍后重试，而不是绕过校验。

## 7. 课程页与点评可见性

`course.py` 的 `view_course(course_id)` 会读取课程、教师、同名课程、同教师课程、排序条件、学期条件和评分筛选条件，然后查询该课程点评。点评查询按登录状态过滤：

- 未登录用户：只保留 `is_hidden=False`。
- 已登录但非管理员用户：保留未隐藏点评，或当前用户本人隐藏的点评。
- 课程模板层面还会按 `is_blocked`、`is_hidden`、`only_visible_to_student`、作者身份等条件决定是否渲染。

`home.py` 的全站最新点评和 `search_reviews` 也有类似可见性控制：被屏蔽或隐藏的点评过滤掉；非学生或未登录用户不能看到仅学生可见点评，除非是作者自己的点评。

对报告的影响：

- 公开模式报告必须标注“仅基于公开可见点评”。
- 登录增强模式报告必须标注“包含用户本地登录可见信息”，并隔离缓存。
- 同一课程在公开模式和登录增强模式下的点评数、观点比例、最新点评时间可能不同。
- 摘要增量刷新必须按可见范围分别计算，不能用登录态结论污染公开缓存。

## 8. `/course/<id>/reviews/` 不是稳定 API

`course.py` 中存在 `/course/<id>/reviews/` 路由。该路由分页读取 `course.reviews`，把点评正文和编辑链接拼成字符串返回；源码中没有像主课程页和搜索页那样完整执行隐藏、屏蔽、仅学生可见等过滤逻辑，也没有 JSON schema、版本号、错误结构或公开接口说明。

因此：

- 不把 `/course/<id>/reviews/` 作为官方稳定 API。
- 不以该路由返回结果作为权限边界判断。
- 不围绕该路由设计批量点评抓取。
- 课程内容采集优先解析主课程页，或等待维护者授权的只读 API。

## 9. 附件与上传边界

`api.py` 中 `/api/upload/file` 使用 `@login_required`，说明文件上传需要登录。`generic_upload` 成功后返回 `/uploads/files/...` 路径。实际登录后课程页中可能出现真实文件附件链接；一次抽样课程页观察到 4 个文件附件和 7 张内嵌 PNG，但这些观察只用于判断产品能力边界，不进入文档作为可复用数据资产。

附件处理原则：

- 不记录文件哈希、上传者昵称、Cookie、CSRF token 或任何会话值。
- 不把登录后的真实文件地址公开到报告或共享缓存。
- 公开模式只展示公开页面可见的附件元数据。
- 登录增强模式下，附件内容解析优先在本地设备完成。
- 附件摘要需要标注文件类型、来源页面、处理时间和授权范围。
- 课件、试卷、作业等材料可能受版权保护，默认不得公开再分发。

## 10. 推荐接入架构

### 10.1 公开连接器

运行位置：项目服务端。

职责：

- `POST /api/search/token` 获取短期搜索 token。
- 访问 `/search/?q=...&token=...` 或 `/search-reviews/?q=...&token=...`。
- 解析公开课程页 `/course/<id>/`。
- 抽取公开课程字段、评分、公开点评、公开附件元数据和来源链接。
- 遵守低频请求、缓存和失败退避。

不得做的事：

- 保存搜索 token。
- 尝试登录、复用他人 Cookie 或绕过 CSRF。
- 批量抓取全站点评。

### 10.2 本地登录连接器

运行位置：用户设备或本地浏览器自动化环境。

职责：

- 用户自行在浏览器登录评课社区。
- 连接器读取当前页面 DOM 中用户可见的课程字段、点评和附件元数据。
- 本地处理附件解析和摘要。
- 只向报告生成模块传递脱敏结构化数据。

安全边界：

- Cookie 和 CSRF token 不离开本地。
- 日志中屏蔽请求头、表单隐藏字段和下载 URL 中的会话参数。
- 用户导出公开报告时重新执行公开模式采集。

### 10.3 授权 API 方向

应联系维护者申请只读 API 或授权数据接口。建议诉求：

- 课程搜索：课程 ID、课程名、教师、课程号、院系、学期、评分摘要。
- 单课程详情：课程字段、评分维度、公开点评摘要或分页点评。
- 增量同步：按课程更新时间或点评更新时间查询。
- 附件元数据：文件名、类型、可见范围、来源课程；附件正文不默认开放。
- 速率限制和署名规范：请求频率、缓存期限、展示来源和删除请求。

## 11. 数据模型建议

课程快照：

```text
CourseSnapshot {
  source: "icourse",
  course_id,
  source_url,
  visibility,
  fetched_at,
  name,
  teachers[],
  department,
  course_number,
  terms[],
  course_type,
  join_type,
  teaching_type,
  course_level,
  credit,
  homepage,
  introduction_summary,
  rating_summary,
  reviews[],
  attachments[]
}
```

点评快照：

```text
ReviewSnapshot {
  review_id,
  source_url,
  anchor,
  visibility,
  fetched_at,
  term,
  publish_time,
  update_time,
  rate,
  difficulty,
  homework,
  grading,
  gain,
  content_summary,
  evidence_excerpt
}
```

附件快照：

```text
AttachmentSnapshot {
  attachment_id,
  source_url,
  visibility,
  fetched_at,
  file_name,
  file_type,
  source_context,
  local_processed
}
```

## 12. 风险清单

| 风险 | 表现 | 处理 |
| --- | --- | --- |
| 页面结构变化 | 选择器失效、字段缺失 | 保留源 URL，降级为人工确认，增加解析回归测试 |
| 权限混淆 | 登录态内容进入公开报告 | 按 visibility 分离缓存和导出路径 |
| Cookie 泄露 | 日志、错误页、报告出现会话值 | 请求头脱敏、日志扫描、安全测试 |
| 版权争议 | 附件或长点评被公开复制 | 只保存摘要和来源，附件本地处理，申请授权 |
| 站点压力 | 高频搜索或课程页请求 | 缓存、退避、限速，联系维护者确认频率 |
| 结论偏差 | 样本少或旧点评被过度概括 | 输出置信度、时间维度和分歧观点 |
| 非稳定接口依赖 | `/course/<id>/reviews/` 行为变化 | 不依赖该路由作为正式接口 |

## 13. 待确认问题

- 维护者是否愿意提供比赛用途只读 API 或数据授权。
- 允许缓存公开课程摘要的期限和展示署名方式。
- 对附件元数据和附件内容摘要的授权边界。
- 是否允许针对少量课程做演示级自动化访问，以及建议速率限制。
- 登录增强模式下，本地生成的报告能否在团队内部共享摘要，还是必须重新基于公开证据生成。

### 13.1 联系计划与默认降级

- 负责人：队长暂代，下一次会议可以移交数据/合规角色。
- 首次联系截止：2026-07-21，使用 `service@icourse.club`，说明比赛背景、两项 Demo、拟访问页面、缓存内容和演示方式。
- 回复等待截止：2026-07-27；无回复不代表授权。
- 无明确授权时的默认方案：仅使用公开模式；单并发、低频请求并积极缓存；不保存全量点评正文；不处理登录受限附件；不公开登录增强报告；演示资料使用公开许可、自制或明确授权样例。
- 任何扩大抓取范围、提高频率、共享附件摘要或持久化登录内容的决定，都需要新的书面依据和 ADR。

## 14. 对实现文档的约束

课程评价 Deep Research Agent 的实现方案应遵守以下硬约束：

- 公开模式只用公开 HTML、公开搜索 token 和公开来源链接。
- 登录增强模式只通过用户本地浏览器读取当前可见内容。
- 不展示或抽取登录 Cookie、CSRF token、会话值。
- 不共享队长 Cookie。
- 不把 `/course/<id>/reviews/` 作为正式 API。
- 不批量复制或公开再分发全量点评/附件。
- 所有报告结论都要带来源、时间和可见范围。
