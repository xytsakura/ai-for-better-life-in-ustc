# 课程 Agent 流式输出设计

## 1. 目标

把课程 Agent 当前“等待完整 JSON 后一次显示”的模型回答改为真正的流式输出。主问答与 GPT-5.6 独立解释分支都应在服务端收到上游模型文本增量后立即传给浏览器，浏览器持续追加并渲染回答。

本迭代只流式传输最终回答正文，不展示或伪造“正在思考”“正在调用工具”等内部状态。知识库引用、模型信息、token usage 和最终错误状态在结束事件中统一确认。

## 2. 已确认边界

1. 主问答和独立解释分支同时支持流式输出。
2. 正文实时追加；知识库引用在回答完成并经过服务端校验后才变为可点击来源。
3. 继续保留现有 `/api/query` 与 `/api/branch-query` JSON 接口，避免破坏已有测试和潜在调用方。
4. 新增 POST 流式接口，浏览器使用 `fetch` 和 `ReadableStream` 读取 SSE，不使用只能发送 GET 的原生 `EventSource`。
5. 用户切换身份、开始新请求或取消当前请求后，旧流不得继续写入新界面、会话历史或 token 统计。
6. 不把“打字机动画”当作流式输出；文本增量必须来自模型上游流。
7. 不向浏览器暴露模型 provider、API key、`base_url`、原始上游事件或内部推理内容。

## 3. 方案选择

### 方案 A：直接把现有接口改成 SSE

改动最少，但会破坏所有依赖 JSON 响应的测试和调用方，不采用。

### 方案 B：新增 NDJSON 流式接口

解析简单，但事件语义和后续 Agent Contract 的兼容性较弱，不采用。

### 方案 C：新增 POST + SSE 流式接口

新增 `/api/query/stream` 和 `/api/branch-query/stream`，保留旧接口。SSE 事件具备明确类型，适合后续 Gateway 转发和统一 Agent 流协议。采用此方案。

## 4. 流式协议

响应类型为 `text/event-stream; charset=utf-8`，并设置禁止代理缓冲和缓存的响应头。每个事件包含一行 JSON `data`，事件之间以空行分隔。

### 4.1 `start`

服务端完成请求校验、权限过滤和知识库检索后发送：

```text
event: start
data: {"mode":"retrieval","scope":"knowledge_base","retrieval_count":5}
```

`start` 不包含检索正文、私人文件信息或未确认引用。

### 4.2 `delta`

每次收到上游最终回答文本增量时发送：

```text
event: delta
data: {"text":"一致连续的核心是"}
```

仅转发最终回答的文本增量。上游 reasoning、工具参数、调试日志和未知事件全部忽略。

### 4.3 `complete`

上游正常结束后发送：

```text
event: complete
data: {"answer":"完整且已校验的回答","citations":[],"usage":null,"model":"gpt-5.6-sol","degraded":false,"model_error":null}
```

浏览器以 `complete.answer` 覆盖流式累计文本，消除分块边界、Markdown 临时状态和无效引用标记造成的差异。知识库回答仅在此时绑定已校验引用。

### 4.4 `error`

流已经建立后发生错误时发送：

```text
event: error
data: {"code":"llm_http_503","message":"模型服务暂不可用","retryable":true,"partial":false}
```

请求校验、身份、权限和 Schema 错误发生在建立流之前，继续使用标准 HTTP `4xx` JSON 错误。流建立后的模型或网络错误使用 `error` 事件，然后结束连接。

## 5. 后端设计

### 5.1 模型适配器

在 `LLMAdapter` 中增加流式方法，并复用现有消息清洗、个性化偏好、不可信引用包装、模型选择和错误脱敏逻辑。

上游请求仍调用 Responses-compatible `/responses`，但增加 `stream: true`。解析器以 SSE `data` 中 JSON 对象的 `type` 字段为准，不依赖可选的 SSE `event:` 行。只识别以下可信事件：

- `response.output_text.delta`：读取事件的 `delta` 字段作为最终回答文本增量；
- `response.completed`：提取最终响应、usage 和模型信息；
- `response.incomplete`：按非正常终止处理，并读取脱敏后的 `incomplete_details`；
- `response.failed`：按模型生成失败处理；
- `error`：按平台或请求错误处理；
- HTTP 错误、JSON 格式错误或 EOF 前未出现任何终止事件：转换为脱敏后的 `stream_incomplete` 或对应内部错误。

`response.error` 不是本实现认可的 Responses 事件名。未知事件忽略，但未知事件不能代替明确的终止事件。

适配器必须累计完整正文。知识库模式结束后继续使用现有 `[S1]` 白名单逻辑校验引用；如果没有任何有效引用，沿用现有检索降级行为，并通过最终 `complete` 或 `error` 语义明确结果。

同步 `generate()` 与 `generate_direct()` 保持不变，测试设置页和旧 JSON 接口继续使用它们。

### 5.2 API 层

抽取主问答共有的“请求校验、权限过滤、检索和 prompt 构建”逻辑，避免 JSON 与 SSE 端点产生两套权限规则。尤其是知识库文档权限必须在创建 `StreamingResponse` 和调用模型之前完成。

新增：

- `POST /api/query/stream`
- `POST /api/branch-query/stream`

两者返回 FastAPI `StreamingResponse`。服务端使用 async generator 和 `httpx.AsyncClient.stream()`，按 `start -> delta* -> complete` 或 `start -> delta* -> error` 输出。生成器在 `finally` 中关闭上游响应和客户端；ASGI 取消或 `Request.is_disconnected()` 为真时立即停止读取，不继续占用模型连接。

独立分支继续固定使用服务端 `COURSE_AGENT_BRANCH_LLM_MODEL`，不接受浏览器传入模型、provider、`base_url` 或检索参数。

## 6. 前端设计

新增一个通用 `streamApi()`：

1. 使用 `fetch` POST JSON；
2. 对非 `2xx` 响应沿用现有结构化错误解析；
3. 使用 `response.body.getReader()` 和 `TextDecoder` 处理跨网络分块的 SSE；
4. 正确处理 `\r\n`、一个网络块包含多个事件、一个事件跨多个网络块以及流结束时的残余缓冲区；
5. 将 `start`、`delta`、`complete`、`error` 分发给调用方；未知事件忽略。

主问答收到第一个 `delta` 后移除“思考中”。回答内容写入当前 assistant message，并以合并后的完整 Markdown 节流重渲染，避免每个 token 都触发昂贵的 DOM 与 KaTeX 更新。公式和引用按钮只在 `complete` 后执行最终渲染与绑定。

独立分支在提交问题时先插入一个空的 assistant message，收到 `delta` 后持续更新该消息；完成后持久化。失败时保留已经收到的文本并显示结构化错误，但把该消息标记为未完成，避免它在后续历史中伪装成完整回答。

每个请求使用 `AbortController`。前端状态新增：

- `state.activeQueryController`：当前主问答控制器；
- `state.branchControllers`：以 `messageId:branchId` 为键的分支控制器 Map；
- `cancelActiveStreams(reason)`：取消主问答和全部分支流，并清理控制器引用。

调用规则：

- 新请求开始时取消同一交互面的旧请求；
- 切换身份、退出登录、清空/切换会话和页面销毁时调用 `cancelActiveStreams()`；
- `queryRequestId` 与现有 auth generation guard 继续负责丢弃晚到事件；
- 被取消的请求不显示“模型失败”，也不写入新身份历史。

## 7. 错误与兼容策略

- 上游不支持 Responses streaming 时返回明确的可重试错误，不把完整答案切片后伪装成流式输出。
- 如果流在收到部分正文后中断，界面显示“回答中断，可重试”，保留部分内容用于用户阅读，但不记录为正常完成回答。
- `complete.answer` 是最终可信正文；前端累计文本仅用于即时展示。
- SSE 数据全部使用 `JSON.stringify`，禁止直接拼接未经编码的模型文本，避免换行破坏协议。
- 代理部署需关闭响应缓冲；部署文档补充 Nginx 等反向代理的相关要求。
- 旧 JSON API、测试设置接口和 FakeLLM 测试能力继续保留。

### 7.1 终止语义

| 场景 | 协议结果 | 界面与持久化 |
|---|---|---|
| 身份、Schema、空间或文档权限校验失败 | 建流前返回 HTTP `4xx` JSON | 不创建完整 assistant 历史；主问答恢复本轮引用 |
| 主问答模型未配置或模型在首个增量前失败 | `complete`，`degraded=true`，携带现有降级答案和 `model_error` | 展示并保存明确标注的降级答案 |
| retrieval 正常结束但没有有效引用 | `complete`，`degraded=true`，以检索来源摘要替换流式草稿 | 保存最终降级答案，不保存被替换草稿 |
| branch 在首个增量前失败 | `error`，`partial=false` | 显示可重试错误，不追加 assistant 历史 |
| 任一流在已经收到正文后出现 `response.incomplete`、`response.failed`、`error`、解析错误或异常 EOF | `error`，`partial=true` | 保留部分正文供阅读，标记“回答中断”，不进入后续模型历史，不作为正常回答持久化 |
| 客户端主动取消、切换身份或断开连接 | 连接结束，不保证发送终止事件 | 不提示模型失败，不写入新身份或正常历史 |
| 正常 `response.completed` | `complete`，`degraded=false` | 用最终正文覆盖草稿，绑定引用、usage 并持久化 |

流式端点最多发送一个终止事件。浏览器收到 `complete` 或 `error` 后忽略后续数据；连接在没有终止事件时 EOF，客户端也生成本地 `stream_incomplete` 中断状态。

## 8. 测试与验收

### 后端自动化

- 上游 SSE 在任意网络分块下都能正确解析文本增量；
- direct 流按 `start/delta/complete` 顺序输出；
- retrieval 在调用模型前完成空间和文档权限过滤；
- retrieval 完成事件只包含白名单引用；
- HTTP 错误、上游 error、格式错误和中途断流会产生脱敏错误；
- branch 流固定服务端模型并拒绝越权字段；
- 客户端断开后上游流被关闭；
- 旧 JSON 接口行为不变。

### 前端自动化与浏览器验收

- SSE parser 覆盖事件跨块、同块多事件、Unicode 跨字节和残余缓冲区；
- 主回答和独立分支都能看到逐步增长的正文；
- 完成后 Markdown、公式、引用和 token usage 正确；
- 切换身份或取消请求后旧流不再写入；
- 中途失败能保留部分正文并明确标记未完成；
- 桌面端和移动端没有因为流式更新产生布局抖动、溢出或按钮错位。

前端解析器提取到独立的 `course_agent/web/streaming.js`，通过 `globalThis.CourseAgentStreaming` 暴露给现有非模块化 `app.js`。不引入 npm 依赖；使用 Node 内置测试运行器执行：

```powershell
cd apps/course-agent
node --test tests-js/streaming.test.mjs
```

后端与静态资源契约继续通过：

```powershell
cd apps/course-agent
.\.venv\Scripts\python.exe -m pytest -q
node --check course_agent\web\streaming.js
node --check course_agent\web\app.js
```

真实增量到达、取消和布局由本地浏览器 smoke test 验收，不宣称进入当前 CI。验收时使用一个至少分三次、每次间隔可观察时间返回文本的 fake streaming adapter，避免把网络偶然分块误认为功能通过。

## 9. 不在本迭代范围

- 展示模型隐藏推理内容；
- 伪造“思考、规划、调用工具”等状态；
- 多 Agent 工具调用事件；
- WebSocket 双向协议；
- 跨设备同步未完成回答；
- 修改平台级 `platform-chat-v1` 正式 Contract。

## 10. 官方协议依据

- [OpenAI Streaming API responses](https://developers.openai.com/api/docs/guides/streaming-responses)：Responses 使用 `stream=true` 的 SSE，并按事件对象的 `type` 分发。
- [OpenAI Responses streaming events](https://developers.openai.com/api/reference/resources/responses/streaming-events)：流事件 Schema 与 `error` 终止事件定义。
