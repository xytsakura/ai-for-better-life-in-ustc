# Campus Agent Hub Backend

后端是独立 FastAPI 服务，运行时数据库默认写入 `var/hub/hub.sqlite3`，不复用 `apps/course-agent` 的数据库。

启动：

```powershell
cd apps/hub
python -m pip install -e ".[test]"
python -m uvicorn hub.main:app --host 127.0.0.1 --port 8100
```

单独启动 Hub 时 Registry 初始为空。完整演示请从仓库根目录运行：

```powershell
Copy-Item .env.example .env
.\deploy\run-demo.ps1
```

Compose 会 seed 瀚海行、校园助手 Demo、评课社区 Agent Demo 和校园公共服务 Agent Demo，完成审核、创建 Featured 运行时凭据，并启动三个独立服务。

演示身份通过请求头 `X-Hub-User` 切换：

- `demo-a`：管理员；
- `demo-b`：开发者；
- `demo-c`：普通用户。

这套请求头身份仅用于本地比赛 Demo 和权限状态机验收，任何客户端都可以构造该请求头，因此不能作为生产认证。公开部署前必须替换为可信登录会话或统一身份提供方，并关闭前端身份切换器。

核心接口：

- `POST /api/registry/agents`：提交版本化 Manifest。请求体可以是纯 Contract Manifest，也可以是 `{ "manifest": {...}, "trust_level": "first_party_internal" }`，其中 `trust_level` 是 Hub 私有注册元数据，不写入 Manifest；
- `GET /api/agents`：普通用户可见的 active Agent 广场数据；
- `POST /api/admin/agents/{agent_id}/versions/{version_id}/review`：管理员审核并原子切换活动版本；
- `POST /api/admin/agents/{agent_id}/versions/{version_id}/checks`：重跑并保存版本级自动验收；
- `POST /api/admin/agents/{agent_id}/suspend|restore|rollback|deprecate`：治理状态操作；
- `POST /api/admin/agents/{agent_id}/credentials` 与凭据状态接口：创建、轮换或撤销 Featured 服务凭据；
- `GET /.well-known/jwks.json`：Ed25519/EdDSA JWT 公钥；
- `POST /api/home-assistant/chat`：首页助手；`auto` 在一次对话中直接回答普通问题，并在高度匹配时从受控功能表推荐当前 active Agent。旧的 `instant` / `route` 模式继续保留兼容；
- `POST /api/gateway/agents/{agent_id}/runs`：按 Registry 目标转发 AG-UI 或 simple-chat；
- `POST /api/agents/{agent_id}/workspace/start` 与 `POST /oauth/token`：Featured Agent 工作台一次性授权码启动。

首页需求路由规则维护在 [`skills/agent-routing/SKILL.md`](./skills/agent-routing/SKILL.md)，简短能力清单维护在 [`skills/agent-routing/AGENT_CATALOG.md`](./skills/agent-routing/AGENT_CATALOG.md)。模型输出的 URL 不可信且不会使用；服务端只接受功能表与运行时 Registry 交集中仍为 active 的 `agent_id`，最终入口由前端复用平台既有激活流程生成。

第三方外部 Agent 默认只允许公网 HTTPS Endpoint。内网或本地演示 Endpoint 需要在提交请求外层将 `trust_level` 设为 `first_party_internal`，并通过 `HUB_INTERNAL_URL_ALLOWLIST` 精确放行；Manifest 本身保持与 `contracts/campus-agent-hub/v1/manifest.schema.json` 完全一致。

常用环境变量：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `HUB_DEMO_MODE` | `false` | 仅比赛 Demo 设为 `true`，启用 `X-Hub-User` 演示身份 |
| `HUB_DATABASE_PATH` | `var/hub/hub.sqlite3` | 独立 Registry 数据库 |
| `HUB_ASSET_CACHE_DIR` | `var/hub/assets` | 审核通过的外部图标安全副本 |
| `HUB_PUBLIC_BASE_URL` | `http://127.0.0.1:8100` | JWT issuer 相关公共入口 |
| `HUB_INTERNAL_URL_ALLOWLIST` | 本地瀚海行和 Demo 精确 origin | 第一方内部 Endpoint 白名单 |
| `HUB_JWT_TTL_SECONDS` | `120` | Gateway / workspace JWT 有效期 |
| `HUB_AUTH_CODE_TTL_SECONDS` | `60` | Featured 一次性授权码有效期 |
| `HUB_JWT_PRIVATE_KEY_FILE` | `var/hub/jwt-ed25519.pem` | 默认持久化的 Ed25519 私钥文件，不得提交 Git |
| `HUB_JWT_PRIVATE_KEY_PEM` | 空 | 可选的外部 Secret 注入，设置后优先于私钥文件 |
| `HUB_JWT_PREVIOUS_PUBLIC_JWK_JSON` | 空 | 轮换窗口内同时发布的上一把公钥 |
| `HUB_AUTOMATIC_CHECKS_ENABLED` | `true` | Manifest 提交后异步执行版本验收 |
| `HUB_REQUIRE_PASSING_CHECKS` | `true` | 管理员批准前强制要求最新验收通过 |
| `HUB_MAX_REQUEST_BYTES` | `262144` | Gateway JSON 请求体上限 |
| `HUB_MAX_RESPONSE_BYTES` | `1048576` | Agent 响应上限 |
| `HUB_RATE_LIMIT_REQUESTS` | `20` | 每用户、每 Agent、每窗口请求数 |
| `HUB_RATE_LIMIT_WINDOW_SECONDS` | `60` | 持久限流窗口秒数 |
| `HUB_HEALTH_FAILURE_THRESHOLD` | `3` | 连续失败达到该值后阻止新调用 |
| `HUB_HEALTH_POLL_INTERVAL_SECONDS` | `30` | 后台健康轮询间隔；小于等于 0 时关闭 |

运行时 Ed25519 私钥、SQLite、图标缓存、日志和 Agent client secret 都必须放在忽略目录或 Secret/volume 中。正式公网部署仍需用真实认证替换 Demo 身份，并通过出站代理或网络策略闭合 DNS 解析与连接之间的 rebinding 风险。

测试：

```powershell
cd apps/hub
python -m pytest
node --test tests-js/*.test.mjs

cd ../../contracts/campus-agent-hub/v1
npm install
npm test
```

Contract Conformance Runner 的用法见 [`contracts/campus-agent-hub/v1/README.md`](../../contracts/campus-agent-hub/v1/README.md)。
