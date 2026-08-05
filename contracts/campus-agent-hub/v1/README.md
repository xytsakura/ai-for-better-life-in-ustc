# Campus Agent Hub Contract v1

本目录是 Campus Agent Hub 的可执行接入契约。Manifest 和健康检查使用 JSON Schema；Connected Agent 的标准边界固定为 `@ag-ui/core@0.0.57`；普通 JSON Agent 可以通过 `simple-chat` middleware adapter 接入。

## 安装与测试

```powershell
cd contracts\campus-agent-hub\v1
npm ci
npm test
```

## 一致性检查

```powershell
.\conformance\run.ps1 `
  -Manifest .\examples\demo-connected.json `
  -BaseUrl http://127.0.0.1:8101 `
  -Output ..\..\..\..\var\conformance-result.json
```

`-BaseUrl` 只用于本地测试，将 Manifest Endpoint 的 origin 替换为指定地址。生产审核必须使用 Registry 中保存并通过 SSRF 校验的真实 Endpoint。

Runner 会限制响应大小并检查 AG-UI 的开始、消息、工具和唯一终止顺序。提供 `-Token` 时还会验证缺少身份令牌必须被 Agent 拒绝；不提供时该项明确标记为 `skipped`，不伪装成已验证。

## 接入等级

- `integration.mode=link`：只要求 `launch_url`。
- `integration.mode=connected`：还要求 `protocol`、`chat_endpoint` 和 `health_endpoint`。
- `Featured` 不是 Manifest 可声明字段，而是 Hub 在 Connected 与完整工作台验收后授予的治理标识。
