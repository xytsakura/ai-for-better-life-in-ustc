# Demo deployment

一键演示编排位于 `deploy/compose.yaml`，用于新克隆后启动：

- Campus Agent Hub；
- 瀚海行 course-agent；
- 独立 campus-helper demo-agent；
- 运行时 bootstrap/seed。

该编排会显式设置 `HUB_DEMO_MODE=true`，允许使用 `demo-a/b/c` 演示身份；它只适合本地比赛 Demo，不是公网生产认证方案。

启动：

```powershell
docker compose --env-file .env -f deploy/compose.yaml up --build
```

如果没有 `.env`，先复制根目录 `.env.example`：

```powershell
Copy-Item .env.example .env
```

要启用真实模型回答，在本地 `.env` 中填写
`COURSE_AGENT_LLM_API_KEY`、`COURSE_AGENT_LLM_BASE_URL` 和模型名。该文件已被
Git 忽略，Compose 只把这些值注入瀚海行容器。

Windows 下也可以直接运行：

```powershell
.\deploy\run-demo.ps1
```

bootstrap 行为：

1. 等待 Hub API 可用；
2. 读取 `contracts/campus-agent-hub/v1/examples` 中的四个 Manifest；
3. 将容器内部调用 URL 和浏览器公开 URL 按 Compose 环境变量重写；
4. 以管理员演示身份提交并审核：
   - `hanhai-course-agent`：Featured；
   - `campus-helper-demo`：Connected；
   - `course-review-demo`：Connected Future Work Demo；
   - `campus-public-service-demo`：Connected Future Work Demo；
5. 为 `hanhai-course-agent` 生成 Hub client credential；
6. 只把 client secret 以权限收紧的原始 secret 文件写入 Docker runtime volume `hub-secrets`，瀚海行通过 `COURSE_AGENT_HUB_CLIENT_SECRET_FILE` 读取；命令行和普通环境变量不承载明文密钥；
7. 瀚海行启动时幂等初始化数据库，并导入 `math-analysis-b1.yaml` 中的 25 份 OCR 课程资料；
8. 等待 Demo Agent 服务就绪并自动执行四个 Agent 的健康检查，应用广场首次打开即可显示当前状态。

Hub 的 Ed25519 签名私钥默认保存在 `hub-data` volume 中，容器重启后继续使用同一签名身份；Featured client secret 仍单独保存在 `hub-secrets` volume。两者都不进入 Git。

启动完成后，从仓库根目录运行固定比赛流程验收：

```powershell
python .\deploy\verify_demo.py `
  --iterations 10 `
  --minimum-success 9 `
  --output .\var\demo-acceptance.json
```

每轮会验证广场同时显示四个 Agent、三个 `simple-chat` Demo 与原生 AG-UI 均正常终止、Featured 工作台可进入且同一授权码不可重放。结果只保存状态、耗时和事件数量，不保存聊天正文、JWT、授权码或 Cookie。

凭据不会写入 Git 文件、Compose 文件或日志。若重复运行，Hub 会保留旧 active credential 并生成新的运行时 secret；这适合本地演示，正式共享部署前应清理 volume 或实现显式轮换策略。
