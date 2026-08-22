# Campus Agent Hub 更新日志

## 2026-08-22

- `route_stream` 与兼容 `auto` 路由新增 active Registry 约束下的高置信匹配，保证期末复习、数学分析复习和签字盖章地点等核心演示需求稳定生成正确 Agent 卡片。
- 扩充瀚海行与校园公共服务 Agent 的公开检索关键词，并补充正例、反例及 inactive Agent 测试；签字盖章规则只接受明确地点、窗口、办理、流程、材料、手续或申请语义，避免图案设计与书写审美误匹配。
- 三组指定演示话术实机重复验证均为 10/10；Hub Python 59 项、前端 JavaScript 39 项通过，浏览器确认推荐卡片与两个 Agent 入口可用。

## 2026-08-21

- 首页新增普通对话与需求分析路由双模式，分别请求 `instant` 与 `route_stream`，不再依赖统一 `auto` 的关键词门控。
- 两个模式的对话、活动会话和左侧存档按身份独立保存；模式切换会中止旧流并恢复目标模式最近会话。
- `route_stream` 只接受功能表与 active Registry 交集中经校验的 `agent_id`，未命中或路由失败时自动回退普通流式回答。
- 恢复本地四 Agent Demo 的完整 bootstrap 与在线状态：Featured 瀚海行、校园助手、评课社区和校园公共服务均通过契约检查并进入 active Registry。
- Hub 首页 `auto` 模式新增本地前置判定：简单问候、通用知识问答和概念解释直接普通回答，只有明确的 Agent 需求才触发路由模型。


### 首页统一对话真实流式化

- `auto` 模式改为 `text/event-stream`：直接回答通过 `model.output_text.delta` 增量展示，Agent 推荐通过 `home.recommendation` 追加，`home.completed` 明确标记完整结束。
- 回答和路由并行执行；路由使用独立数据库连接并设置有限等待时间，失败或超时只取消推荐，不影响正常回答。
- 前端在错误、取消、身份切换或异常 EOF 时主动结束读取，防止残留请求继续写入当前会话。

## 2026-08-20

### 首页统一全屏对话

- 去掉首页即时对话 / 需求路由双标签和封闭式对话框，改为单一 `auto` 对话入口。
- 初始状态只展示品牌标识与扁平输入条；首条消息后进入全屏会话，模型可直接回答或展示已验收 Agent 的直达推荐。

### Agent 广场两列布局

- 桌面端 Agent 卡片调整为两列网格，每行两个，Featured 瀚海行固定优先展示。
- 首页“为你推荐”快捷卡片同步使用相同排序；移动端继续单列显示。

### 首页即时对话与需求路由

- 新增 `POST /api/home-assistant/chat`，支持 `instant` 真流式回答与 `route` 结构化推荐两种模式。
- 首页改为双模式会话区，具备按身份隔离的有界历史、加载、取消、错误和 Agent 推荐卡片。
- 路由规则维护在 `skills/agent-routing/SKILL.md`，已登记能力维护在 `skills/agent-routing/AGENT_CATALOG.md`。
- 路由结果必须通过功能表与运行时 active Registry 双重校验；前端只消费 `agent_id` 并复用既有激活流程，不信任模型生成的 URL。
- 新增缺少模型绑定、伪造或下线 Agent、敏感信息隔离、SSE 和响应式前端契约测试。

## Future Work Demo Agent

- Registry 与路由清单新增 `course-review-demo` 和 `campus-public-service-demo`，均需通过 active Registry 校验后才会被推荐。
- 两个 Agent 复用统一聊天容器，后端不信任模型生成的 URL 或 Agent ID。
