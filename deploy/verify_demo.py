from __future__ import annotations

import argparse
import json
import re
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


def _agent_id(agent: dict[str, Any]) -> str:
    return str(agent.get("agent_id") or agent.get("id") or "")


def _agent_manifest(agent: dict[str, Any]) -> dict[str, Any]:
    active = agent.get("active_version")
    if isinstance(active, dict) and isinstance(active.get("manifest"), dict):
        return active["manifest"]
    manifest = agent.get("manifest")
    return manifest if isinstance(manifest, dict) else agent


def _binding_is_configured(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    binding = value.get("binding") if isinstance(value.get("binding"), dict) else value
    return bool(binding.get("profile_id") and binding.get("model_id"))


def _assert_no_plain_model_secret(value: Any, *, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            lowered = str(key).lower()
            if lowered in {"api_key", "apikey", "encrypted_api_key", "encryptedapikey"}:
                raise RuntimeError(f"model profile endpoint leaked secret field at {path}.{key}")
            _assert_no_plain_model_secret(nested, path=f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, nested in enumerate(value):
            _assert_no_plain_model_secret(nested, path=f"{path}[{index}]")
        return
    if isinstance(value, str) and re.search(r"(?i)\bsk-[A-Za-z0-9_-]{16,}\b", value):
        raise RuntimeError(f"model profile endpoint leaked key-looking value at {path}")


def verify_model_configuration(base_url: str, *, user: str, agents: list[dict[str, Any]]) -> dict[str, Any]:
    started = time.perf_counter()
    hanhai = next((agent for agent in agents if _agent_id(agent) == "hanhai-course-agent"), None)
    if not hanhai:
        raise RuntimeError("hanhai-course-agent missing from agent list")
    manifest = _agent_manifest(hanhai)
    capabilities = manifest.get("capabilities") if isinstance(manifest.get("capabilities"), list) else []
    model_runtime = manifest.get("model_runtime") if isinstance(manifest.get("model_runtime"), dict) else {}
    if "platform-model-gateway" not in capabilities:
        raise RuntimeError("hanhai-course-agent does not declare platform-model-gateway")
    if model_runtime.get("mode") not in {"platform_optional", "platform_required"}:
        raise RuntimeError(f"hanhai-course-agent model runtime not platform compatible: {model_runtime}")

    profiles_payload = request_json(f"{base_url}/api/model-profiles", user=user)
    bindings_payload = request_json(f"{base_url}/api/model-bindings", user=user)
    _assert_no_plain_model_secret(profiles_payload)
    _assert_no_plain_model_secret(bindings_payload)

    if isinstance(profiles_payload, dict):
        profile_summaries = profiles_payload.get("profiles", [])
    elif isinstance(profiles_payload, list):
        profile_summaries = profiles_payload
    else:
        profile_summaries = []
    if not isinstance(profile_summaries, list):
        raise RuntimeError("model profile endpoint returned non-list profiles")

    profiles: list[dict[str, Any]] = []
    for summary in profile_summaries:
        if not isinstance(summary, dict):
            continue
        profile_id = str(summary.get("profile_id") or summary.get("id") or "")
        if not profile_id:
            continue
        detail = request_json(f"{base_url}/api/model-profiles/{profile_id}", user=user)
        _assert_no_plain_model_secret(detail)
        profiles.append(detail)

    chat_model_count = 0
    for profile in profiles:
        models = profile.get("models") if isinstance(profile.get("models"), list) else []
        chat_model_count += sum(1 for model in models if isinstance(model, dict) and model.get("chat_eligible") is not False)

    bindings = bindings_payload if isinstance(bindings_payload, dict) else {}
    agent_bindings = bindings.get("agents")
    if isinstance(agent_bindings, dict):
        hanhai_binding = agent_bindings.get("hanhai-course-agent")
    elif isinstance(agent_bindings, list):
        hanhai_binding = next(
            (
                item
                for item in agent_bindings
                if isinstance(item, dict)
                and str(item.get("agent_id") or item.get("scope_id") or "") == "hanhai-course-agent"
            ),
            None,
        )
    else:
        hanhai_binding = None
    global_binding = bindings.get("global") if isinstance(bindings.get("global"), dict) else None

    return {
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "hanhai_declares_model_gateway": True,
        "profiles": len(profiles),
        "chat_eligible_models": chat_model_count,
        "global_binding": _binding_is_configured(global_binding),
        "hanhai_binding": _binding_is_configured(hanhai_binding),
        "status": "configured" if profiles else "no_profiles_configured",
    }


def run_iteration(base_url: str, *, user: str) -> dict[str, Any]:
    agents = request_json(f"{base_url}/api/agents", user=user).get("agents", [])
    visible = sorted(_agent_id(item) for item in agents)
    required = {
        "campus-helper-demo",
        "hanhai-course-agent",
        "course-review-demo",
        "campus-public-service-demo",
    }
    if not required.issubset(visible):
        raise RuntimeError(f"required Agents not visible: {visible}")
    return {
        "visible_agents": visible,
        "model_config": verify_model_configuration(base_url, user=user, agents=agents),
        "demo": gateway_run(base_url, "campus-helper-demo", user=user, prompt="图书馆在哪里？"),
        "hanhai": gateway_run(base_url, "hanhai-course-agent", user=user, prompt="请用一句话说明极限的直觉。"),
        "course_review": gateway_run(base_url, "course-review-demo", user=user, prompt="请介绍课程评价和老师评价的演示能力。"),
        "public_service": gateway_run(base_url, "campus-public-service-demo", user=user, prompt="我需要签字盖章，应该去哪里？"),
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
