# Campus Agent Hub 更新日志

## 2026-08-20

### 首页即时对话与需求路由

- 新增 `POST /api/home-assistant/chat`，支持 `instant` 真流式回答与 `route` 结构化推荐两种模式。
- 首页改为双模式会话区，具备按身份隔离的有界历史、加载、取消、错误和 Agent 推荐卡片。
- 路由规则维护在 `skills/agent-routing/SKILL.md`，已登记能力维护在 `skills/agent-routing/AGENT_CATALOG.md`。
- 路由结果必须通过功能表与运行时 active Registry 双重校验；前端只消费 `agent_id` 并复用既有激活流程，不信任模型生成的 URL。
- 新增缺少模型绑定、伪造或下线 Agent、敏感信息隔离、SSE 和响应式前端契约测试。
