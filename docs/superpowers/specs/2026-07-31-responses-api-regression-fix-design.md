# Responses API 协议回归修复设计

> 状态：方案 A 已实现并完成本地真实模型验证
>
> 日期：2026 年 7 月 31 日

## 1. 问题与目标

提交 `2d95552` 将课程 Agent 的模型请求从已验证可用的 Responses API 改为 Chat Completions API，导致当前网关请求失败并被前端显示为“模型暂时不可用”。同一模型、Key 与网关下，`POST /responses` 已实测返回 `200`，并能正确处理多轮消息。

本次修复的目标是恢复 Responses API，同时保留合并版本新增的多轮对话、通用问答、知识库问答、引用校验和降级错误展示。

## 2. 实现范围

仅修改以下两个边界，路径均相对于仓库根目录：

- `apps/course-agent/course_agent/llm.py`：把内部请求恢复为 `POST {base_url}/responses`，将系统提示放入 `instructions`，将经过清理的历史消息和本轮问题放入 `input` 消息数组，并使用 `max_output_tokens`。
- `apps/course-agent/tests/test_llm.py`：让测试客户端记录请求 URL 与请求体，断言 Responses 端点、字段名称、多轮历史顺序、输出解析和错误降级。

不增加协议自动回退，不增加新的前端配置项，不修改 API Key 保存方式，不调整检索、权限或界面结构。

## 3. 请求与响应契约

直接问答与知识库问答统一使用：

```json
{
  "model": "<configured-model>",
  "instructions": "<system-prompt>",
  "input": [
    {"role": "user", "content": "<history>"},
    {"role": "assistant", "content": "<history>"},
    {"role": "user", "content": "<current-question>"}
  ],
  "max_output_tokens": 1200
}
```

模型文本继续兼容 `output_text` 与 `output[].content[].text`。上游网络错误、解析错误和非成功状态仍转换为现有 `LLMResult` 降级结果，不向浏览器或日志暴露 API Key。

`input` 中只包含经过清理的 `user` 与 `assistant` 消息，不包含 `system` 角色；系统提示只能通过顶层 `instructions` 发送。

## 4. 验证标准

修复完成必须同时满足：

1. 单元测试断言请求 URL 以 `/responses` 结尾，并确认不再发送 `messages` 与 `max_tokens`。
2. 多轮历史按原顺序进入 `input`，非法角色与空消息继续被过滤。
3. 直接问答、知识库回答、引用校验与模型错误降级测试全部通过；错误响应断言不得包含测试 API Key 或 `Authorization` 请求头内容。
4. 在 `apps/course-agent` 目录运行 `python -m pytest`，完整测试集通过。
5. 从 `apps/course-agent` 启动 `python -m uvicorn course_agent.main:app --host 127.0.0.1 --port 8002`；若已有旧进程，先停止旧进程再启动新版本。
6. 访问 `http://127.0.0.1:8002/`，登录任一演示用户，在“个人设置 -> 模型服务”点击“测试连接”，确认显示连接成功。
7. 返回“问问 Agent”，保持“直接问答”，发送一个最小测试问题，确认返回真实模型回答且不再显示“模型不可用”。
8. 使用同一界面的多轮对话验证第二轮能够引用第一轮给出的测试标记。
9. 推送前用 `git status --short` 和 `git diff --cached --name-only` 确认 `.env`、API Key、Cookie 与运行时数据未进入 Git 变更。

## 5. 回滚方式

本次改动不涉及数据库迁移或持久化格式。若目标网关行为发生变化，可回滚单个修复提交；现有知识库、会话与上传资料不会受影响。
