from __future__ import annotations

import argparse
import json
import time
import uuid
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any
from urllib import error, request


def request_json(
    url: str,
    *,
    user: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 90,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        method="GET" if payload is None else "POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Hub-User": user,
        },
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc


def gateway_run(base_url: str, agent_id: str, *, user: str, prompt: str) -> dict[str, Any]:
    run_id = str(uuid.uuid4())
    started = time.perf_counter()
    payload = {
        "threadId": f"accept-{uuid.uuid4()}",
        "runId": run_id,
        "state": {},
        "messages": [{"id": str(uuid.uuid4()), "role": "user", "content": prompt}],
        "tools": [],
        "context": [],
        "forwardedProps": {"acceptance": True},
    }
    req = request.Request(
        f"{base_url}/api/gateway/agents/{agent_id}/runs",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "X-Hub-User": user,
        },
    )
    try:
        with request.urlopen(req, timeout=90) as response:
            content_type = response.headers.get("Content-Type", "").lower()
            if "text/event-stream" not in content_type:
                raise RuntimeError(f"unexpected content type: {content_type}")
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"gateway HTTP {exc.code}: {detail}") from exc

    events: list[dict[str, Any]] = []
    for frame in raw.replace("\r\n", "\n").split("\n\n"):
        data = "\n".join(line[5:].lstrip() for line in frame.splitlines() if line.startswith("data:"))
        if data:
            events.append(json.loads(data))
    types = [event.get("type") for event in events]
    terminal = [item for item in types if item in {"RUN_FINISHED", "RUN_ERROR"}]
    if not events or types[0] != "RUN_STARTED" or terminal != ["RUN_FINISHED"] or types[-1] != "RUN_FINISHED":
        raise RuntimeError(f"invalid AG-UI lifecycle: {types}")
    return {
        "agent_id": agent_id,
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "event_count": len(events),
        "terminal": types[-1],
    }


def verify_workspace(base_url: str, *, user: str) -> dict[str, Any]:
    state = f"accept-{uuid.uuid4()}"
    launch = request_json(
        f"{base_url}/api/agents/hanhai-course-agent/workspace/start",
        user=user,
        payload={"state": state},
    )["launch_url"]
    opener = request.build_opener(request.HTTPCookieProcessor(CookieJar()))
    started = time.perf_counter()
    with opener.open(launch, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"workspace final HTTP {response.status}")
        final_url = response.geturl()
    replay_status = None
    try:
        request.build_opener().open(launch, timeout=30)
    except error.HTTPError as exc:
        replay_status = exc.code
    if replay_status not in {400, 401}:
        raise RuntimeError(f"workspace code replay was not rejected: {replay_status}")
    return {
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "final_origin": final_url.split("/", 3)[:3],
        "replay_rejected": True,
    }


def run_iteration(base_url: str, *, user: str) -> dict[str, Any]:
    agents = request_json(f"{base_url}/api/agents", user=user).get("agents", [])
    visible = sorted(item.get("agent_id") for item in agents)
    required = {"campus-helper-demo", "hanhai-course-agent"}
    if not required.issubset(visible):
        raise RuntimeError(f"required Agents not visible: {visible}")
    return {
        "visible_agents": visible,
        "demo": gateway_run(base_url, "campus-helper-demo", user=user, prompt="图书馆在哪里？"),
        "hanhai": gateway_run(base_url, "hanhai-course-agent", user=user, prompt="请用一句话说明极限的直觉。"),
        "workspace": verify_workspace(base_url, user=user),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the fixed Campus Agent Hub competition Demo acceptance loop.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8100")
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--minimum-success", type=int, default=9)
    parser.add_argument("--user", help="Use one fixed Demo identity instead of rotating the configured users.")
    parser.add_argument("--users", default="demo-a,demo-b,demo-c")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.iterations < 1 or not 1 <= args.minimum_success <= args.iterations:
        parser.error("minimum-success must be between 1 and iterations")

    base_url = args.base_url.rstrip("/")
    users = [args.user] if args.user else [item.strip() for item in args.users.split(",") if item.strip()]
    if not users:
        parser.error("at least one Demo user is required")
    result: dict[str, Any] = {
        "base_url": base_url,
        "iterations": args.iterations,
        "minimum_success": args.minimum_success,
        "runs": [],
    }
    for index in range(1, args.iterations + 1):
        started = time.perf_counter()
        user = users[(index - 1) % len(users)]
        try:
            evidence = run_iteration(base_url, user=user)
            run = {
                "iteration": index,
                "user": user,
                "status": "passed",
                "duration_ms": int((time.perf_counter() - started) * 1000),
                **evidence,
            }
        except Exception as exc:
            run = {
                "iteration": index,
                "user": user,
                "status": "failed",
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "safe_error": str(exc)[:300],
            }
        result["runs"].append(run)
        print(f"[{index}/{args.iterations}] {run['status']} ({run['duration_ms']} ms)")

    passed = sum(run["status"] == "passed" for run in result["runs"])
    result["passed"] = passed
    result["failed"] = args.iterations - passed
    result["overall_status"] = "passed" if passed >= args.minimum_success else "failed"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("passed", "failed", "overall_status")}, ensure_ascii=False))
    return 0 if result["overall_status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
