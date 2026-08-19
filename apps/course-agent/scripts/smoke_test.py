from __future__ import annotations

import argparse
import sys

import httpx


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify a running USTC course-agent deployment without calling the LLM."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--expected-documents", type=int, default=25)
    parser.add_argument("--require-llm", action="store_true")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    with httpx.Client(base_url=base_url, timeout=15, follow_redirects=True) as client:
        index_response = client.get("/")
        require(index_response.status_code == 200, "课程 Agent 首页不可用")
        require("瀚海行Agent" in index_response.text, "首页内容不是当前瀚海行Agent")
        require("真理如瀚海求索亦行舟" in index_response.text, "首页缺少当前品牌标语")
        for asset in (
            "/assets/app.js",
            "/assets/styles.css",
            "/assets/vendor/katex/katex.min.js",
            "/assets/vendor/katex/katex.min.css",
        ):
            asset_response = client.get(asset)
            require(asset_response.status_code == 200, f"前端静态资源不可用：{asset}")
            require(bool(asset_response.content), f"前端静态资源为空：{asset}")

        health_response = client.get("/api/health")
        require(health_response.status_code == 200, "健康检查接口不可用")
        health = health_response.json()
        require(health.get("database") is True, "SQLite 数据库健康检查失败")
        require(health.get("search") is True, "FTS5 检索健康检查失败")
        if args.require_llm:
            require(health.get("llm_configured") is True, "模型 API 尚未正确配置")

        users_response = client.get("/api/users")
        require(users_response.status_code == 200, "演示身份接口不可用")
        user_ids = {item["id"] for item in users_response.json().get("items", [])}
        require({"demo-a", "demo-b", "demo-c"}.issubset(user_ids), "缺少三个预置演示身份")

        session_response = client.post("/api/session", json={"user_id": "demo-a"})
        require(session_response.status_code == 200, "无法创建 demo-a 演示会话")

        spaces_response = client.get("/api/spaces")
        require(spaces_response.status_code == 200, "知识空间接口不可用")
        spaces = spaces_response.json().get("items", [])
        space_types = {item["space_type"] for item in spaces}
        require({"personal", "shared"}.issubset(space_types), "个人或共享知识空间缺失")
        shared = next((item for item in spaces if item["id"] == "math-b1-shared"), None)
        require(shared is not None, "数学分析 B1 共享空间缺失")

        documents_response = client.get(
            "/api/spaces/math-b1-shared/documents", params={"page_size": 100}
        )
        require(documents_response.status_code == 200, "共享课程资料接口不可用")
        documents = documents_response.json()
        require(
            documents.get("total") == args.expected_documents,
            f"课程资料数量异常：预期 {args.expected_documents}，实际 {documents.get('total')}",
        )
        require(len(documents.get("items", [])) == args.expected_documents, "资料列表不完整")

    llm_status = "已配置" if health.get("llm_configured") else "未配置（离线检索仍可用）"
    print("部署 smoke test 通过")
    print(f"- 服务地址：{base_url}")
    print("- 首页 / 前端 / KaTeX：正常")
    print("- SQLite / FTS5：正常")
    print("- 演示身份：demo-a、demo-b、demo-c")
    print(f"- 数学分析 B1 资料：{args.expected_documents} 份")
    print(f"- 模型 API：{llm_status}")


if __name__ == "__main__":
    try:
        main()
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        print(f"部署 smoke test 失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc
