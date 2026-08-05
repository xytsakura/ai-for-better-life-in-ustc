# 校园助手 Demo Agent

这是一个独立进程，用于演示第三方应用从 `Link App` 升级为 `Connected Agent`。它不导入 Hub 或瀚海行的内部代码。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\campus-helper-demo.exe
```

默认地址为 `http://127.0.0.1:8101/`，健康检查为 `/api/health`，标准聊天端点为 `/api/chat`。

设置 `DEMO_AGENT_REQUIRE_HUB_TOKEN=1` 后，聊天端点只接受 Hub 签发的 EdDSA JWT。通过 `DEMO_AGENT_HUB_JWKS_URL`、`DEMO_AGENT_HUB_AUDIENCE` 和 `DEMO_AGENT_HUB_ISSUER` 配置验证参数。
