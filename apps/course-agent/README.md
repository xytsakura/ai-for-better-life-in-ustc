# 数学分析 B1 课程复习 Agent

这是 `AI for better life In ustc` 的首个可运行课程 Agent Demo。它提供个人空间、邀请制共享空间、免费订阅知识库广场、PDF 导入、页级解析、模型直接问答、可选择资料的检索问答和删除失效验证。模型服务支持自动发现多个文本模型；每个会话可独立选择模型与思考强度，并显示真实的上下文 token 用量。主问答与 GPT-5.6 独立解释分支都会实时流式追加模型正文；用户还可以选中回答中的一段或多段文字，把它们作为结构化引用加入下一轮主对话或独立分支。

第一次在新电脑部署或交给代码 Agent 审计时，请使用仓库级的[完整部署与审计指南](../../docs/COURSE_AGENT_DEPLOYMENT.md)。本 README 只保留日常开发所需的最短命令。

## 本地启动

建议使用 Python 3.10 或更新版本。Windows 可以使用 Codex 工作区附带的 Python 3.12。

```powershell
cd apps/course-agent
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
Copy-Item .env.example .env
```

如果电脑没有 Windows Python Launcher，但 `python --version` 已确认是 3.10 或更新版本，可以把 `py -3.12` 换成 `python`。

在 `.env` 中填写服务端模型配置。API Key 只保存在本地 `.env`，不要提交到 Git：

```text
COURSE_AGENT_LLM_API_KEY=本机密钥
COURSE_AGENT_LLM_BASE_URL=兼容 Responses API 的地址
COURSE_AGENT_LLM_MODEL=gpt-5.6-sol
COURSE_AGENT_BRANCH_LLM_MODEL=gpt-5.6-sol
COURSE_AGENT_ADMIN_USER_IDS=demo-a
```

`COURSE_AGENT_BRANCH_LLM_MODEL` 是选中文字后“单独提问”所使用的服务端固定模型，默认是 `gpt-5.6-sol`。`COURSE_AGENT_ADMIN_USER_IDS` 是允许保存模型服务配置和执行模型发现的演示身份列表，多个 ID 用英文逗号分隔。默认演示配置使用 `demo-a`；API Key 不会返回浏览器，也不会写入会话历史。

初始化并导入课程资料：

```powershell
.\.venv\Scripts\python.exe -m course_agent.cli init-db
.\.venv\Scripts\python.exe -m course_agent.cli import-manifest ..\..\data\manifests\math-analysis-b1.yaml
.\.venv\Scripts\python.exe -m uvicorn course_agent.main:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000>，选择演示身份。没有模型配置时仍可使用全文检索，并会显示降级结果。

## 演示流程

1. 选择 `谢同学 demo-a`，进入“数学分析 B1 学习小组”；该空间预置课程资料。
2. 在“个人设置 → 模型服务”点击“发现模型”，确认可用文本模型与不可用模型原因均被列出，并选择新对话默认模型。
3. 回到聊天页，为当前会话选择模型和“快速 / 均衡 / 深度 / 极深 / 最高（高级）”思考强度；这些选择只影响当前会话。
4. 保持“直接问答”，输入“一致连续和连续有什么区别？”，确认模型不检索资料即可回答；回答完成后，上下文圈应显示本轮真实 token 用量。
5. 选中助手回答中的文字，点击“加入引用”；可以继续选中同一回答中的其他片段，并在输入框上方删除或调整引用顺序。提交下一轮问题后，确认引用作为独立上下文参与回答，而不是被粘贴进可编辑问题正文。
6. 再选中文字并点击“单独问 GPT-5.6”，在原回答下方的分支中提问；确认分支可以折叠、继续追问，并能把分支回答手动加入主对话引用。刷新或重新打开该历史会话后，分支仍可恢复。
7. 切换到“使用课程资料”：选择“日常学习”会勾选教材、讲义、笔记和提纲；选择“备考刷题”会勾选真题、试卷、答案和解析，也可以逐份调整复选框。
8. 提交资料问题，确认回答只引用当前勾选的文件，并用 `[S1]` 标记精确到页码的来源；资料模式没有勾选文件时不会发起请求。
9. 切换到 `队友演示 demo-b`，确认“队友演示的资料”仍为空，再进入共享学习小组确认可以读取共享资料。
10. 在 `demo-b` 的个人空间上传一份 PDF，点击“投稿到广场”，明确勾选资料，并分别设置“用于问答 / 可预览 / 可下载”后提交审核。
11. 切换到管理员 `demo-a`，进入“知识广场 → 待审核”，查看快照并批准；待审资料在批准前不会进入普通用户检索。
12. 切换到普通用户 `demo-c`，在知识广场订阅该知识库并点击“进入知识库”；确认它以独立只读空间出现，可按审核后的策略预览、下载和问答。
13. 取消订阅后，订阅空间和 RAG 权限立即失效；允许公开预览的资料仍可从广场详情查看。
14. 在可写空间重复上传同一 PDF 会提示重复；删除后该文件会从可选来源中消失，旧 ID 也不能继续参与检索。

问答采用两条明确分离的链路：`direct` 模式只调用模型，不访问课程索引；`retrieval` 模式必须提交非空 `document_ids`，服务端会重新校验每份文档的当前用户权限与有效状态，并严格把检索范围限制在所选文件内。来源勾选会在当前空间内保留，便于针对同一组资料连续追问；切换身份或知识空间时会清空。独立解释分支使用专用的 `/api/branch-query` direct-only 接口，模型由服务端固定，不能请求知识库检索或由浏览器指定其他模型；选中文字和完整源回答均按不可信引用内容处理。

当前演示数据库导入 25 份唯一 PDF，共 510 页。2026-08-03 已使用 DeepSeek-OCR-2 的 Markdown 模式完成全量 OCR 回填：500 页可检索、10 页为空白页不进入索引，共生成 686 个检索分块。PDF 旁的 `<原文件名>.ocr.md` 保存按页分隔的 OCR 文本，检索使用该文本，但引用、页码和阅读器始终指向原 PDF；sidecar 缺失、哈希过期或页数不完整时会自动回退到 PyMuPDF 文本层。

模型回答中的 Markdown 标题、列表、行内公式和块级公式由应用本地渲染。数学排版使用仓库内置的 KaTeX 0.18.1，不依赖外部 CDN；第三方许可证保存在 `course_agent/web/vendor/katex/LICENSE`。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest
```

测试使用假模型，不消耗真实 API。真实模型 smoke test 只需在本地 `.env` 配置后，在网页中提交一个问题。

已验证：自动化测试覆盖直接问答不触发检索、资料必选、文档粒度检索、越权拦截、个人空间隔离、共享空间访问、重复导入、删除失效、模型发现回退、管理员权限、会话级模型/思考强度透传和 Responses usage 解析；知识广场另外覆盖投稿权限、不可变快照、普通预览、订阅、逐份下载/RAG 策略、取消订阅、暂停恢复、换版、回滚、基础版本冲突、作者/管理员审阅和多文档失败清理。具体通过数量以当前 `pytest` 输出为准。

服务启动后，可以另开终端运行不调用模型的部署 smoke test：

```powershell
.\.venv\Scripts\python.exe scripts\smoke_test.py --base-url http://127.0.0.1:8000 --require-llm
```

暂时不配置模型时去掉 `--require-llm`。脚本会核对首页、前端与 KaTeX 静态资源、SQLite、FTS5、演示身份、知识空间和 25 份唯一课程资料。

## Docker

从仓库根目录构建，避免丢失 `学习资料` 和 manifest：

```powershell
docker build -f apps/course-agent/Dockerfile -t ustc-course-agent .
docker volume create course-agent-data
docker run --rm -v course-agent-data:/app/var ustc-course-agent course-agent init-db
docker run --rm -v course-agent-data:/app/var ustc-course-agent course-agent import-manifest /app/repository/data/manifests/math-analysis-b1.yaml
docker run --rm -p 8000:8000 `
  -e COURSE_AGENT_LLM_API_KEY=本机密钥 `
  -e COURSE_AGENT_LLM_BASE_URL=兼容 Responses API 的地址 `
  -e COURSE_AGENT_LLM_MODEL=gpt-5.6-sol `
  -v course-agent-data:/app/var `
  ustc-course-agent
```

前两条一次性容器命令负责初始化持久卷并导入资料；只运行最后一条服务命令会得到空资料库。完整说明和验收步骤见[部署与审计指南](../../docs/COURSE_AGENT_DEPLOYMENT.md)。

## 数据边界

原始资料属于团队私有仓库内容，来源页面声明仅限学习使用；本 Demo 不自动公开或再分发资料。`var/` 中的 SQLite、索引、上传文件和日志均不应提交到 Git。
