# 数学分析 B1 课程复习 Agent

这是 `AI for better life In ustc` 的首个可运行课程 Agent Demo。它提供个人空间、邀请制共享空间、PDF 导入、页级解析、模型直接问答、可选择资料的检索问答和删除失效验证。

## 本地启动

建议使用 Python 3.10 或更新版本。Windows 可以使用 Codex 工作区附带的 Python 3.12。

```powershell
cd apps/course-agent
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
Copy-Item .env.example .env
```

在 `.env` 中填写服务端模型配置。API Key 只保存在本地 `.env`，不要提交到 Git：

```text
COURSE_AGENT_LLM_API_KEY=本机密钥
COURSE_AGENT_LLM_BASE_URL=兼容 Responses API 的地址
COURSE_AGENT_LLM_MODEL=gpt-5.6-sol
```

初始化并导入课程资料：

```powershell
.\.venv\Scripts\python.exe -m course_agent.cli init-db
.\.venv\Scripts\python.exe -m course_agent.cli import-manifest ..\..\data\manifests\math-analysis-b1.yaml
.\.venv\Scripts\python.exe -m uvicorn course_agent.main:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000>，选择演示身份。没有模型配置时仍可使用全文检索，并会显示降级结果。

## 演示流程

1. 选择 `谢同学 demo-a`，进入“数学分析 B1 学习小组”；该空间预置课程资料。
2. 保持“直接问答”，输入“一致连续和连续有什么区别？”，确认模型不检索资料即可回答。
3. 切换到“使用课程资料”：选择“日常学习”会勾选教材、讲义、笔记和提纲；选择“备考刷题”会勾选真题、试卷、答案和解析，也可以逐份调整复选框。
4. 提交资料问题，确认回答只引用当前勾选的文件，并用 `[S1]` 标记精确到页码的来源；资料模式没有勾选文件时不会发起请求。
5. 切换到 `队友演示 demo-b`，确认“队友演示的资料”仍为空，再进入共享学习小组确认可以读取共享资料。
6. 在可写空间上传一份 PDF，重复上传会提示重复；删除后该文件会从可选来源中消失，旧 ID 也不能继续参与检索。

问答采用两条明确分离的链路：`direct` 模式只调用模型，不访问课程索引；`retrieval` 模式必须提交非空 `document_ids`，服务端会重新校验每份文档的当前用户权限与有效状态，并严格把检索范围限制在所选文件内。来源勾选会在当前空间内保留，便于针对同一组资料连续追问；切换身份或知识空间时会清空。

当前演示数据库导入 25 份唯一 PDF，共 510 页、493 个检索分块；其中 484 页可检索，14 页标记为需 OCR，12 页标记为需人工检查。解析状态会在资料列表中明确展示，不会把扫描页伪装成可检索文本。

模型回答中的 Markdown 标题、列表、行内公式和块级公式由应用本地渲染。数学排版使用仓库内置的 KaTeX 0.18.1，不依赖外部 CDN；第三方许可证保存在 `course_agent/web/vendor/katex/LICENSE`。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest
```

测试使用假模型，不消耗真实 API。真实模型 smoke test 只需在本地 `.env` 配置后，在网页中提交一个问题。

已验证：自动化测试覆盖直接问答不触发检索、资料必选、文档粒度检索、越权拦截、双用户个人空间隔离、共享空间访问、重复导入和删除失效；具体通过数量以当前 `pytest` 输出为准。

## Docker

从仓库根目录构建，避免丢失 `学习资料` 和 manifest：

```powershell
docker build -f apps/course-agent/Dockerfile -t ustc-course-agent .
docker run --rm -p 8000:8000 `
  -e COURSE_AGENT_LLM_API_KEY=本机密钥 `
  -e COURSE_AGENT_LLM_BASE_URL=兼容 Responses API 的地址 `
  -e COURSE_AGENT_LLM_MODEL=gpt-5.6-sol `
  -v course-agent-data:/app/var `
  ustc-course-agent
```

## 数据边界

原始资料属于团队私有仓库内容，来源页面声明仅限学习使用；本 Demo 不自动公开或再分发资料。`var/` 中的 SQLite、索引、上传文件和日志均不应提交到 Git。
