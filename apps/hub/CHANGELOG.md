# Campus Agent Hub 更新日志

## 2026-08-21

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
