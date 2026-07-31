# 聊天模型、思考强度与上下文用量设计

## 1. 背景与结论

当前课程 Agent 只能在服务端配置一个固定模型，聊天请求不会携带模型或思考强度；LLM 适配层只提取回答文本，没有透传 Responses API 的 `usage`。本次迭代把三个能力放到同一条按对话管理的链路中：

1. 从已配置的 Base URL 和 API key 自动发现可用模型；
2. 每个对话独立选择模型和思考强度；
3. 在聊天输入区显示最近一次真实请求的上下文用量圈。

交互采用已确认的 A 方案：模型、思考强度、上下文圈和发送按钮位于输入框底部同一行。

## 2. 目标与非目标

### 2.1 目标

- 设置页可发现上游暴露的模型并保存新对话默认模型；
- 当前对话可随时切换模型和思考强度，其他对话不受影响；
- 思考强度提供“快速、均衡、深度、极深”四档，`max` 放在高级选项；
- 后端解析并返回真实 token usage；
- 上下文圈可解释、可降级，不伪造未知模型的上下文比例；
- API key 不进入响应、日志、浏览器存储或 Git 仓库。

### 2.2 非目标

- 不实现自动模型路由或根据问题替用户切换模型；
- 不批量调用所有模型来测试回答质量；
- 不在个人回答偏好中保存思考强度；
- 不实现跨设备会话同步；现有会话仍保存在当前浏览器；
- 不引入独立模型供应商 SDK，继续使用 Responses-compatible HTTP 接口。

## 3. 已验证的上游能力

- 当前 Base URL 的 `/models` 返回网页 HTML，不能作为模型列表；
- 当前 Base URL 的 `/v1/models` 返回标准模型集合，共发现 20 个模型；
- 当前 `/responses` 返回 `usage.input_tokens`、`output_tokens`、`total_tokens`、缓存 token 和推理 token；
- `gpt-5.6-sol` 已实测接受 `reasoning.effort: low` 并在响应中回显有效配置；
- 模型列表没有提供上下文窗口上限，因此不能只靠模型发现响应计算百分比。

## 4. 状态与所有权

### 4.1 服务端全局设置

继续由现有设置配置保存：

- `llm_base_url`
- `llm_api_key`
- `llm_model`：作为新对话默认模型

模型发现不会自动保存设置。只有用户显式保存模型服务配置时才写入 `.env`。

模型服务配置属于部署级管理能力，不属于普通用户个人偏好。只有 `COURSE_AGENT_ADMIN_USER_IDS` 指定的管理员可以修改 Base URL/API key、执行模型发现和保存默认模型；普通用户只能从服务端验收后的模型清单中为自己的对话选择模型。开发示例可以把 `demo-a` 配为管理员，但生产部署必须显式配置。

### 4.2 当前对话状态

每个会话历史项新增：

- `model`
- `reasoningEffort`
- `usage`

新对话读取服务端默认模型。若该模型的能力清单包含 `medium`，思考强度初始化为 `medium`；否则不发送 `reasoning` 参数，并在界面中禁用思考强度选择。切换模型或思考强度只更新当前对话和当前浏览器中的会话记录。

### 4.3 个人回答偏好

个人设置继续保留：

- 语气；
- 回答详略；
- 自定义指令。

回答详略只控制最终输出长度。思考强度控制模型内部推理预算，两者相互独立。

## 5. 后端接口

### 5.1 模型发现

新增 `POST /api/models/discover`。接口不接收 Base URL 或 API key，只使用服务端已经保存并通过安全校验的模型服务配置。这样不会把已保存密钥发送到用户临时提交的目标。

管理员修改模型服务配置时遵守以下规则：

- Base URL 规范化后发生变化，必须同时显式提供该服务对应的新 API key；不得把旧服务的密钥沿用到新主机；
- 只允许 `https`，本地开发地址只能来自服务端显式开发白名单；
- DNS 解析后拒绝 loopback、private、link-local、multicast、保留地址和云 metadata 地址；
- 禁止自动跟随重定向；候选模型 URL 必须保持同源；
- 保存配置和模型发现均受管理员权限检查。

服务端按保存配置生成规范化候选 URL：

1. `base_url + /models`；
2. 当第一项不是有效 JSON 模型集合时，探测同源 `/v1/models`；
3. URL 去重，避免 Base URL 已包含 `/v1` 时重复请求。

只有满足以下条件才算成功：HTTP 成功、响应是受支持的 JSON 结构，并且能提取至少一个模型 ID。返回 HTML、空数组或未知结构均进入下一候选或返回受控错误。请求超时为 10 秒，响应体上限为 1 MiB，规范化后最多保留 200 个模型。

规范化响应示例：

```json
{
  "models": [
    {
      "id": "gpt-5.6-sol",
      "display_name": "gpt-5.6-sol",
      "chat_eligible": true,
      "supported_reasoning_efforts": ["low", "medium", "high", "xhigh", "max"],
      "disabled_reason": null
    }
  ],
  "discovery_source": "/v1/models"
}
```

模型能力使用服务端版本化注册表判定，判定顺序固定且可测试：

| 规则 | `chat_eligible` | `disabled_reason` |
| --- | --- | --- |
| ID 包含 `image` | `false` | `image_model_not_supported` |
| ID 包含 `audio` | `false` | `audio_model_not_supported` |
| ID 包含 `realtime` | `false` | `realtime_model_not_supported` |
| ID 等于 `codex-auto-review` | `false` | `specialized_review_model` |
| 已登记的 GPT/Codex 文本模型族 | `true` | `null` |
| 未知模型 | `false` | `unknown_model_capability` |

思考能力也由同一注册表返回精确的 `supported_reasoning_efforts`，不使用模糊布尔值。首版至少登记当前已经验证的 GPT-5.6 模型族；其他模型只有在官方能力说明或真实兼容性测试确认后才增加档位。未知模型返回空数组。

发现结果保存在服务端内存缓存中，并满足：

- 缓存属于当前部署的全局模型服务配置，不按普通用户分别复制；
- 缓存键绑定规范化 Base URL、API key 的单向指纹和配置 generation，不保存明文 key；
- TTL 为 10 分钟，最大 200 个模型；
- Base URL、API key 或默认模型保存后立即使旧缓存失效；
- 服务重启后缓存为空；查询使用非默认模型且缓存为空或过期时，服务端先按受控配置刷新一次；刷新失败返回 `model_catalog_unavailable`，不放行客户端模型字符串。

### 5.2 查询请求

`QueryRequest` 新增：

- `model`：本轮选择的模型 ID；
- `reasoning_effort`：`low | medium | high | xhigh | max | null`。

服务端只允许使用当前有效模型目录中的可用模型，或已经保存并验证的默认模型，防止用户提交任意模型字符串。所选思考强度必须属于该模型的 `supported_reasoning_efforts`；否则返回 `reasoning_effort_not_supported` 和允许值。空值表示不发送 `reasoning` 参数。LLM 适配层将有效配置写入 Responses 请求：

```json
{
  "model": "gpt-5.6-sol",
  "reasoning": {
    "effort": "medium"
  }
}
```

### 5.3 查询响应与 usage

`LLMResult` 和 `/api/query` 响应新增结构化 `usage`：

```json
{
  "input_tokens": 4388,
  "output_tokens": 13,
  "reasoning_tokens": 0,
  "cached_tokens": 3840,
  "cache_write_tokens": 0,
  "total_tokens": 4401,
  "context_window_tokens": 272000,
  "context_usage_percent": 1.61,
  "context_window_source": "registry"
}
```

字段缺失时使用 `null`，不将缺失字段伪装为 `0`。上下文百分比只在服务端能确认模型窗口时计算：

Responses 原始字段映射如下：

| 归一化字段 | Responses 原始路径 |
| --- | --- |
| `input_tokens` | `usage.input_tokens` |
| `output_tokens` | `usage.output_tokens` |
| `reasoning_tokens` | `usage.output_tokens_details.reasoning_tokens` |
| `cached_tokens` | `usage.input_tokens_details.cached_tokens` |
| `cache_write_tokens` | `usage.input_tokens_details.cache_write_tokens` |
| `total_tokens` | `usage.total_tokens` |

\[
p
=
\min\!\left(
100,
\frac{\text{input\_tokens}}{\text{context\_window\_tokens}}\times 100
\right)
\]

分子使用 `input_tokens`，因为它代表当前请求真正占用的输入上下文；输出、推理和缓存 token 在悬停详情中分别展示，不重复计入上下文圈。服务端维护小型、可测试的已知模型窗口注册表，并同时返回窗口来源。若使用官方标称值而非上游实时元数据，提示文案必须写明“按模型标称窗口估算”；未命中的模型返回 `context_window_tokens: null` 和 `context_usage_percent: null`。

## 6. 前端交互

### 6.1 设置页

- 模型输入改为可搜索选择器；
- 管理员先保存通过安全校验的连接配置，再点击“发现模型”；发现操作只使用已保存配置；
- 列表显示可用文本模型和被禁用模型的原因；
- 默认模型只能从当前目录中 `chat_eligible: true` 的项目选择并保存；
- 保存后更新新对话默认模型，不修改已有对话。

### 6.2 聊天输入区

同一行从左到右为：

1. 当前模型选择器；
2. 思考强度选择器；
3. 弹性空白；
4. 上下文用量圈；
5. 发送按钮。

思考强度映射：

| 界面文案 | API 值 | 使用建议 |
| --- | --- | --- |
| 快速 | `low` | 日常问答、低延迟 |
| 均衡 | `medium` | 支持该档位时的新对话默认 |
| 深度 | `high` | 复杂讲解与推导 |
| 极深 | `xhigh` | 高难度分析 |
| 最高（高级） | `max` | 质量优先且可接受明显等待 |

上下文圈状态：

- 新对话：空圈，提示“尚无用量”；
- 回答完成：显示最近一次真实输入上下文比例；
- 切换模型：灰色待定圈，提示“发送下一条消息后重新计算”；
- 未知窗口：显示 token 数而非百分比；
- 低、中、高用量使用绿色、黄色、红色，但不能只依赖颜色表达状态。

移动端保持同一功能，但允许模型名截断，思考强度显示短标签；控件不得挤压发送按钮或产生横向滚动。

## 7. 错误处理与安全

- 模型发现失败时保留当前模型并提供可重试提示；
- 上游错误只返回状态、受控错误码和脱敏摘要；
- API key 不写入日志、响应、`localStorage`、会话历史或模型列表缓存；
- Base URL 变化时必须提供新 key，禁止跨主机复用旧 key；
- 上游请求执行 DNS/IP 安全校验、同源约束并禁用重定向；
- 非 JSON 的 200 响应按发现失败处理；
- 模型不支持所选思考强度时明确提示用户调整，不静默降级；
- 查询失败不覆盖上一轮有效 usage；
- 上游模型 ID 和显示名作为不可信文本转义后渲染；
- 模型发现设置超时、数量上限和响应体大小上限；
- 模型服务设置与发现接口仅允许部署管理员调用。

## 8. 测试与验收

### 8.1 自动测试

- `/models` 返回 HTML、`/v1/models` 成功的回退路径；
- 标准列表、空列表、未知结构、401、超时和超大响应；
- 模型 ID 去重、分类和转义；
- Base URL 变化但未提供新 key 时拒绝保存；
- loopback、private、link-local、metadata IP、危险 DNS 解析和重定向被拒绝；
- 模型目录缓存绑定配置指纹/generation，TTL、配置变更和重启后正确失效；
- 普通用户不能修改模型服务设置或执行发现；
- `supported_reasoning_efforts` 控制前端档位和服务端校验，默认模型不支持 `medium` 时不发送 reasoning；
- direct/retrieval 请求均透传模型和思考强度；
- Responses usage 完整、部分缺失和完全缺失；
- 上下文百分比与未知窗口降级；
- 会话 A/B 的模型、思考强度和 usage 相互隔离；
- 新对话继承默认模型但思考强度初始化为均衡；
- API key 与上游原始错误不会出现在响应和日志中。

### 8.2 浏览器验收

- 桌面端与 390 px 移动端无重叠、截断错误或横向溢出；
- 模型发现、默认模型保存、对话内切换和历史恢复形成闭环；
- 上下文圈、悬停详情和非颜色提示均可读取；
- 使用当前真实 API 验证模型发现、`reasoning.effort` 和 usage，但不在测试产物中保存密钥。

## 9. 交付边界

本次实现只修改 `apps/course-agent` 的模型适配、查询 API、设置页、聊天输入区和相关测试。插件化 Agent 平台、跨设备账号同步、自动模型路由和计费统计不在本次范围内。
