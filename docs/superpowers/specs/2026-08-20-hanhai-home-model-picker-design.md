# 瀚海行首页与模型切换微调设计

## 目标

在不改变问答、RAG、身份权限和 Hub Model Gateway 安全边界的前提下，完成四项首页微调：适度放大产品图标；将标语显示为“真理如瀚海 求索亦行舟”；让同一平台 Model Profile 中的 `gpt-5.6-luna`、`gpt-5.6-sol`、`gpt-5.6-terra` 可见且真实可切换；把模型与思考强度控件改成更柔和的圆角选择器。

## 方案

### 视觉

- 首页 Logo 从 72 px 提升到约 88 px，保留圆角、边框和响应式约束。
- 标语使用两个不可拆散的词组，中间保留一个普通视觉空格，小屏允许在词组边界换行。
- 模型输入框改为原生 `select`，使用胶囊式圆角、弱边框和低对比背景；思考强度选择器沿用同一视觉语言。

### 平台模型目录

- Hub 创建 Featured 工作台模型委托时，从用户当前 Agent 专属绑定或全局绑定中解析 Profile，并只返回该 Profile 内 `chat_eligible = 1` 的模型摘要。
- 摘要使用专用安全 DTO，仅包含 `id`、`display_name`、`chat_eligible`（以及由瀚海行本地推导的能力字段）；不包含 `profile_id`、`owner_user_id`、`provider`、`base_url`、`api_style`、指纹、元数据、API Key、加密密文或服务端内部凭据。
- 瀚海行把摘要保存在现有服务端委托存储中，浏览器通过 `/api/models` 获得合并后的安全模型目录。

### 真实切换

- 瀚海行在兑换单次 Grant 时携带可选的 `requested_model_id`；不接受 `profile_id`、`base_url`、`provider` 或任何密钥字段。Connected 旧客户端省略该字段时继续沿用默认行为。
- Hub 只允许在当前绑定 Profile 内选择仍然可聊天的模型；非法、过期或跨 Profile 模型返回 `model_not_allowed`。
- 未选择模型时保持当前绑定模型行为；独立运行时继续使用瀚海行本地模型配置。

### 兼容与权限边界

- Featured 与 Connected 共用同一兑换接口；模型选择始终在 Hub 当前用户、目标 Agent 的已绑定 Profile 内完成，不能跨 Profile。
- Grant 消费时重新检查 Profile 状态、模型仍可聊天和 Grant/委托生命周期；模型的临时选择不等同于修改默认绑定，绑定变更仍由 Hub 的撤销逻辑使旧 Grant 失效。
- 平台目录、瀚海行 `/api/models`、页面、Cookie、前端存储和日志均不得出现委托 token、Grant、Profile 标识、Base URL、指纹或任何密钥。

## 验收

- 首页图标更大，标语准确显示“真理如瀚海 求索亦行舟”。
- 模型选择器至少出现 Luna、Sol、Terra，选择后真实回答返回对应模型 ID。
- API Key 不进入瀚海行响应、Cookie、页面或日志。
- Hub、瀚海行和前端测试通过；桌面与 390 px 移动端无横向溢出，控制台无错误。
- 负例必须覆盖跨 Profile 模型、不可聊天模型、额外 `profile_id` 字段、旧 Connected 请求兼容和 `xhigh` 思考强度；Luna、Sol、Terra 选择后要在 Hub 上游请求和最终模型字段中真实变化。
- 根级与课程 Agent 更新日志同步更新。

## 涉及文件与测试

- Hub：`apps/hub/hub/model_gateway.py`、`apps/hub/hub/main.py`、`apps/hub/tests/test_model_gateway.py`。
- 瀚海行：`apps/course-agent/course_agent/hub.py`、`main.py`、`llm.py`、`model_catalog.py`、`web/index.html`、`web/app.js`、`web/styles.css`。
- 瀚海行回归：`tests/test_llm.py`、`tests/test_hub_adapter.py`、`tests/test_course_agent.py`、`tests/test_avatar_preview.py`。
