# 数学分析 B1 课程复习 Agent

这是 `AI for better life In ustc` 的首个可运行课程 Agent Demo。它提供个人空间、邀请制共享空间、PDF 导入、页级解析、中文全文检索、带来源问答和删除失效验证。

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

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest
```

测试使用假模型，不消耗真实 API。真实模型 smoke test 只需在本地 `.env` 配置后，在网页中提交一个问题。

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
