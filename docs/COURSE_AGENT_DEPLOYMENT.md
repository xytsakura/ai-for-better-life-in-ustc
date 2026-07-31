# 课程复习 Agent 部署与审计指南

本文档面向第一次接触仓库的队友、Codex/Claude Code 等代码 Agent，以及负责复现和审计 Demo 的开发者。目标是在一台没有本项目运行状态的新电脑上，仅依赖 GitHub 私有仓库和一组独立提供的模型 API 配置，完整运行当前课程复习 Agent。

## 1. 部署完成后能得到什么

当前可运行产品是“数学分析 B1 课程复习 Agent Demo v0.1”，不是完整的校园 Agent 插件平台。成功部署后可以：

- 在 `demo-a` 与 `demo-b` 两个演示身份之间切换；
- 访问彼此隔离的个人知识空间，以及两人都可访问的邀请制共享空间；
- 浏览仓库预置的 25 份唯一数学分析 B1 资料及其解析状态；
- 向可写知识空间上传不超过 50 MiB 的 PDF，进行页级解析、分块与 SQLite FTS5 全文索引；
- 对文档执行重复检测、重新解析与删除，删除后关联索引立即失效；
- 在“直接问答”模式中只调用模型，不检索课程资料；
- 从已保存的 Responses-compatible 服务自动发现多个模型，并区分可用文本模型与不适用模型；
- 为每个会话独立选择模型与思考强度，并显示本轮真实上下文 token 用量；
- 在“使用课程资料”模式中按“日常学习”“备考刷题”“全部资料”预设或逐份勾选本轮资料；
- 仅在用户有权访问且本轮勾选的文档中检索，并在回答中展示文件名、页码和 `[S1]` 形式的引用；
- 本地渲染 Markdown、行内公式和块级公式，KaTeX 资源不依赖外部 CDN；
- 模型不可用时保留资料检索能力，并明确显示降级状态。

完整插件化 Agent 平台仍属于后续阶段，不能把本 Demo 描述成已经实现 Agent Portal、Registry 或 Gateway。

后端已经提供带权限检查的页级文本接口，但当前网页没有“逐页打开原文”的阅读器入口；资料列表、检索引用和回答展示才是当前可见界面的交付范围。

## 2. GitHub 交付物清单

以下内容必须来自仓库，不能依赖队长电脑上的隐藏文件：

| 组件 | 仓库位置 | 用途 |
|---|---|---|
| FastAPI 后端 | `apps/course-agent/course_agent/*.py` | 会话、权限、上传、解析、检索、模型调用与 API |
| 浏览器前端 | `apps/course-agent/course_agent/web/` | 身份、空间、资料选择、问答和引用界面 |
| KaTeX 资源与许可证 | `apps/course-agent/course_agent/web/vendor/katex/` | 离线数学公式渲染 |
| Python 依赖 | `apps/course-agent/pyproject.toml` | 安装运行和测试依赖 |
| 配置模板 | `apps/course-agent/.env.example` | 创建每台电脑自己的 `.env` |
| 资料 Manifest | `data/manifests/math-analysis-b1.yaml` | 定义预置资料、类型和来源信息 |
| 数学分析资料 | `学习资料/数学分析B1/` | Manifest 引用的 26 个 PDF 文件 |
| 自动化测试 | `apps/course-agent/tests/` | 验证权限隔离、检索边界、删除失效和模型适配 |
| 部署 smoke test | `apps/course-agent/scripts/smoke_test.py` | 检查运行服务和 25 份唯一资料，不调用模型 |
| 容器配置 | `apps/course-agent/Dockerfile`、`.dockerignore` | 可选 Docker 部署 |

Manifest 有 26 条记录，其中两份 PDF 内容重复。首次导入的正确结果是 25 份导入、1 份跳过、0 份失败，网页应显示 25 份唯一资料。

下列内容必须由每台电脑本地生成，因此不会上传：

- `apps/course-agent/.env`：API Key、模型地址和会话密钥；
- `var/course-agent/course-agent.sqlite3`：本机数据库和全文索引；
- `var/course-agent/uploads/`：本机上传文件；
- `apps/course-agent/.venv/`：本机 Python 虚拟环境；
- 缓存、日志和测试临时文件。

不要把上述运行状态补交到 Git。仓库交付的是可重复构建的源码与资料，而不是队长电脑的密钥和私人状态快照。

## 3. 前置条件

推荐环境：

- 已获邀访问私有仓库 `xytsakura/ai-for-better-life-in-ustc`；
- Git；
- 64 位 Python 3.10 至 3.12，推荐 Python 3.12；
- 能访问 Python 包源；
- 若要使用模型回答，需要一个兼容 OpenAI Responses API 的模型服务；
- 首次导入 510 页资料需要几分钟，具体取决于 CPU 与磁盘速度。

先确认 Python 版本：

```powershell
py -3.12 --version
git --version
```

Windows 主流程使用 Python Launcher 明确选择 3.12，避免 Anaconda 或旧项目修改了默认 `python`。如果没有 `py` 命令，但 `python --version` 已确认是 3.10 至 3.12，可以把后续的 `py -3.12` 换成 `python`。如果两条命令都找不到合适版本，请先安装 64 位 Python 3.12，并在安装器中启用 Python Launcher。

## 4. Windows 原生部署

以下命令均在 PowerShell 中执行。

### 第一步：克隆私有仓库

```powershell
git clone https://github.com/xytsakura/ai-for-better-life-in-ustc.git
cd ai-for-better-life-in-ustc
git status --short --branch
```

预期位于 `main` 分支，工作区没有未提交文件。如果 GitHub 要求登录，请使用自己的 GitHub 账号或 GitHub CLI；不要让队长发送 GitHub 密码。

### 第二步：创建隔离的 Python 环境

```powershell
cd apps/course-agent
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
```

安装完成后验证命令入口：

```powershell
.\.venv\Scripts\python.exe -m course_agent.cli --help
```

### 第三步：创建本机配置

```powershell
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
```

把第二条命令输出的随机字符串填入 `.env` 的 `COURSE_AGENT_SESSION_SECRET`。然后填写模型配置：

```text
COURSE_AGENT_DEMO_MODE=true
COURSE_AGENT_SESSION_SECRET=本机生成的随机字符串
COURSE_AGENT_RUNTIME_DIR=../../var/course-agent
COURSE_AGENT_LLM_API_KEY=本机模型密钥
COURSE_AGENT_LLM_BASE_URL=兼容Responses API的基础地址
COURSE_AGENT_LLM_MODEL=gpt-5.6-sol
COURSE_AGENT_LLM_TIMEOUT_SECONDS=45
COURSE_AGENT_ADMIN_USER_IDS=demo-a
COURSE_AGENT_ALLOW_LOCAL_LLM_BASE_URLS=false
```

`COURSE_AGENT_LLM_BASE_URL` 应填写基础地址，例如服务商要求的地址通常以 `/v1` 结尾。应用会自行追加 `/responses`，不要把 `/responses` 重复写入基础地址。`COURSE_AGENT_ADMIN_USER_IDS` 指定可以保存模型配置和执行模型发现的演示身份，多个 ID 用英文逗号分隔。外部服务默认必须使用 HTTPS；只有明确测试本机模型服务时才把 `COURSE_AGENT_ALLOW_LOCAL_LLM_BASE_URLS` 改为 `true`。

首次启动后，以管理员身份进入“个人设置 → 模型服务”，点击“发现模型”。应用会使用服务端已保存的 Base URL 和 API Key 探测模型目录，不会把密钥发送到浏览器；发现结果可用于设置新会话默认模型和当前会话模型。

API Key 只能通过私聊、密码管理工具或其他安全渠道提供，不能写入 Issue、聊天截图、部署报告或 Git 提交。

### 第四步：初始化数据库并导入课程资料

仍在 `apps/course-agent` 目录执行：

```powershell
.\.venv\Scripts\python.exe -m course_agent.cli init-db
.\.venv\Scripts\python.exe -m course_agent.cli import-manifest ..\..\data\manifests\math-analysis-b1.yaml
```

第一次导入的预期摘要为：

```text
{'imported': 25, 'skipped': 1, 'failed': []}
```

再次运行导入命令是安全的。所有资料已经存在时，通常会显示 0 份导入、26 份跳过、0 份失败。

若 `failed` 不是空列表，不要继续假设部署成功。先根据其中的路径检查 PDF 是否完整拉取，以及当前终端是否仍位于 `apps/course-agent`。

### 第五步：运行自动化测试

```powershell
.\.venv\Scripts\python.exe -m pytest
```

测试不使用真实 API Key，也不会消耗模型额度。测试必须以退出码 0 结束；通过数量以当前仓库输出为准，不在文档中固定一个容易过期的数字。

### 第六步：启动服务

```powershell
.\.venv\Scripts\python.exe -m uvicorn course_agent.main:app --host 127.0.0.1 --port 8000
```

保持该终端运行，然后在浏览器打开 <http://127.0.0.1:8000>。`127.0.0.1` 只允许本机访问，适合队友各自在电脑上部署和审计。

如果 8000 端口已经被占用，可以改用：

```powershell
.\.venv\Scripts\python.exe -m uvicorn course_agent.main:app --host 127.0.0.1 --port 8001
```

此时浏览器和 smoke test 地址也要改为 `http://127.0.0.1:8001`。

### 第七步：运行无模型调用的部署检查

另开一个 PowerShell 窗口，回到 `apps/course-agent`：

```powershell
.\.venv\Scripts\python.exe scripts\smoke_test.py --base-url http://127.0.0.1:8000 --require-llm
```

该脚本验证首页、前端脚本、样式、KaTeX、数据库、FTS5、两个演示身份、个人/共享空间、模型配置状态和 25 份唯一课程资料，但不会向模型发送问题。如果暂时没有 API Key，去掉 `--require-llm` 即可验证离线部分。

## 5. 页面验收流程

部署者应按下面顺序人工验收，不能只看到首页就宣布成功。

1. 页头显示“服务正常”和当前模型名。
2. 选择“谢同学 `demo-a`”，确认能看到个人空间和“数学分析 B1 学习小组”。
3. 进入共享空间，确认资料列表显示 25 份，且每份都有页数、可检索页数和解析状态。
4. 在“个人设置 → 模型服务”点击“发现模型”，确认目录列出多个模型；选择新会话默认模型并保存。
5. 回到聊天页，切换当前会话模型和思考强度，确认新对话与已有会话互不覆盖。
6. 保持“直接问答”，询问“一致连续和连续有什么区别？”。回答不应出现课程资料引用，完成后上下文圈应显示本轮真实用量。
7. 切换“使用课程资料”，确认页面展示可以逐份勾选的资料列表。
8. 点击“日常学习”，应优先选中讲义、笔记和提纲；点击“备考刷题”，应优先选中真题、答案和解析。
9. 选择一份或多份资料后提问，确认回答仅引用本轮选中的文件，并显示文件名和页码。
10. 在回答中检查普通 Markdown 列表、行内公式和块级公式是否正常渲染，不应看到未渲染的 `\\(`、`\\[` 或美元符号公式源码。
11. 在个人空间上传一份测试 PDF，确认重复上传会被拦截，并可以重新解析和删除。
12. 切换到 `demo-b`，确认无法看到 `demo-a` 的个人资料，但仍能访问共享学习小组。

直接问答和资料问答都会调用模型，人工验收这两步可能产生少量 API 用量。

## 6. Linux 或 macOS 部署

仓库代码本身不依赖 Windows。主要差异是虚拟环境命令：

```bash
git clone https://github.com/xytsakura/ai-for-better-life-in-ustc.git
cd ai-for-better-life-in-ustc/apps/course-agent
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[test]"
cp .env.example .env
.venv/bin/python -m course_agent.cli init-db
.venv/bin/python -m course_agent.cli import-manifest ../../data/manifests/math-analysis-b1.yaml
.venv/bin/python -m pytest
.venv/bin/python -m uvicorn course_agent.main:app --host 127.0.0.1 --port 8000
```

启动前同样需要编辑 `.env`。服务启动后，在另一个终端运行：

```bash
.venv/bin/python scripts/smoke_test.py --require-llm
```

## 7. Docker 部署

Docker 是可选路径。必须从仓库根目录构建，否则镜像拿不到 Manifest 和课程 PDF。

```powershell
docker build -f apps/course-agent/Dockerfile -t ustc-course-agent .
docker volume create course-agent-data
```

首次运行前，先在持久卷中初始化数据库并导入资料：

```powershell
docker run --rm -v course-agent-data:/app/var ustc-course-agent course-agent init-db
docker run --rm -v course-agent-data:/app/var ustc-course-agent course-agent import-manifest /app/repository/data/manifests/math-analysis-b1.yaml
```

然后启动网页服务：

```powershell
docker run --rm --name ustc-course-agent -p 8000:8000 `
  -e COURSE_AGENT_SESSION_SECRET=本机随机字符串 `
  -e COURSE_AGENT_LLM_API_KEY=本机模型密钥 `
  -e COURSE_AGENT_LLM_BASE_URL=兼容Responses API的基础地址 `
  -e COURSE_AGENT_LLM_MODEL=gpt-5.6-sol `
  -v course-agent-data:/app/var `
  ustc-course-agent
```

不要直接把原生部署使用的 `.env` 作为 Docker `--env-file`，其中的相对 `COURSE_AGENT_RUNTIME_DIR` 会覆盖镜像内预设的 `/app/var/course-agent`。上面的命令保留镜像默认运行目录，并把整个 `/app/var` 持久化。

容器运行后可以从另一终端执行：

```powershell
docker exec ustc-course-agent python scripts/smoke_test.py --base-url http://127.0.0.1:8000 --require-llm
```

## 8. 更新代码与保留本机数据

拉取队友的新提交前，先停止 Uvicorn，然后从仓库根目录执行：

```powershell
git status
git pull --ff-only
cd apps/course-agent
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m course_agent.cli init-db
.\.venv\Scripts\python.exe -m course_agent.cli import-manifest ..\..\data\manifests\math-analysis-b1.yaml
.\.venv\Scripts\python.exe -m pytest
```

`var/course-agent` 不受 Git 管理，因此正常拉取代码不会覆盖本机上传资料。涉及数据库结构变化时，应先复制整个 `var/course-agent` 作为备份，再按对应版本迁移说明操作。

## 9. 常见问题

### 页面显示“模型未配置”

检查 `.env` 是否位于 `apps/course-agent/.env`，并确认 API Key、基础地址和模型名三项都不是空值。修改 `.env` 后必须重启 Uvicorn。

### 模型返回 404

最常见原因是基础地址写错，或把 `/responses` 重复写进 `COURSE_AGENT_LLM_BASE_URL`。应用实际请求地址为：

```text
COURSE_AGENT_LLM_BASE_URL + /responses
```

还要确认服务商确实支持 Responses API，而不只是 Chat Completions API。

### 模型返回 401 或 403

API Key 无效、没有对应模型权限，或代理服务需要不同的认证方式。不要把真实密钥贴进公开日志；只报告 HTTP 状态码和已脱敏的基础地址。

### 导入结果出现失败项

确认仓库完整拉取、Manifest 引用的 PDF 存在，并从 `apps/course-agent` 目录运行命令。Git LFS 当前不是本仓库的必要条件；正常克隆应直接得到 PDF 文件。

### 页面能打开但资料为 0

服务会自动建表和演示身份，但不会自动导入课程资料。重新执行 `import-manifest` 命令，并确保启动服务与导入命令使用相同的 `COURSE_AGENT_RUNTIME_DIR`。

### 端口被占用

换用 8001 等空闲端口，或者停止旧 Uvicorn 进程。不要同时让两个进程写同一个 SQLite 数据库来做压力测试。

### 部分页面显示“需 OCR”或“需人工检查”

这是当前解析器对扫描页或低文本页的真实状态，不是部署失败。Demo 不会把无法可靠提取的页面伪装成可检索内容。

## 10. 审计者检查清单

队友审计时至少记录以下证据：

- Git 提交哈希和分支名；
- Python 版本与操作系统；
- `pip install -e ".[test]"` 是否成功；
- Manifest 首次导入结果是否为 25/1/0；
- `pytest` 是否以退出码 0 结束；
- `scripts/smoke_test.py --require-llm` 是否通过；
- 页面十步验收中每一步的结果；
- direct 模式是否保持 0 次检索；
- retrieval 模式是否只使用已勾选资料；
- `demo-a` 的个人资料是否对 `demo-b` 不可见；
- 删除资料后，旧文档 ID 是否无法继续检索；
- 浏览器控制台是否存在阻断功能的错误；
- 仓库中是否误提交 `.env`、API Key、SQLite、上传文件或日志。

审计发现问题时，请提供复现命令、预期行为、实际行为、错误状态码和脱敏日志，不要只写“不能运行”。

## 11. 安全边界

- 当前是本地比赛 Demo，演示身份选择不等于生产级登录认证；
- `--host 127.0.0.1` 只对本机开放，适合个人审计；
- 若改为 `--host 0.0.0.0` 并暴露给局域网或公网，必须先补充真实认证、HTTPS、强会话密钥、上传隔离、速率限制和部署防火墙；
- 不上传 API Key、Cookie、CSRF token、个人聊天、私人数据库或未授权课程资料；
- 当前课程资料仅用于团队私有学习和比赛演示，不应公开再分发。
