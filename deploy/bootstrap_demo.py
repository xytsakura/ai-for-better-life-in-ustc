from __future__ import annotations

import json
import os
import stat
import sys
import time
from pathlib import Path
from typing import Any
from urllib import error, request


HUB_URL = os.getenv("HUB_URL", "http://127.0.0.1:8100").rstrip("/")
ADMIN_USER = os.getenv("HUB_ADMIN_USER", "demo-a")
CONTRACT_ROOT = Path(os.getenv("CONTRACT_ROOT", "/workspace/contracts/campus-agent-hub/v1"))
SECRET_PATH = Path(os.getenv("COURSE_AGENT_SECRET_PATH", "/run/hub-secrets/course-agent.secret"))


def main() -> None:
    wait_for_hub()
    hanhai = load_manifest("hanhai-connected.json")
    demo = load_manifest("demo-connected.json")
    patch_hanhai_manifest(hanhai)
    patch_demo_manifest(demo)

    hanhai_version = submit_manifest(hanhai)
    review_version("hanhai-course-agent", hanhai_version, featured=True)
    credential = create_credential("hanhai-course-agent")
    write_course_agent_secret(credential["client_secret"])

    demo_version = submit_manifest(demo)
    review_version("campus-helper-demo", demo_version, featured=False)
    skip_health_check = os.getenv("HUB_BOOTSTRAP_SKIP_HEALTH_CHECK", "0") == "1"
    if not skip_health_check:
        wait_for_agent_health("hanhai-course-agent")
        wait_for_agent_health("campus-helper-demo")
    suffix = "seeded both Agents" if skip_health_check else "seeded and health-checked both Agents"
    print(f"Hub demo bootstrap completed: {suffix}.")


def wait_for_hub(timeout_seconds: int = 90) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            api_get("/api/session")
            return
        except RuntimeError:
            time.sleep(1)
    raise RuntimeError("Hub did not become ready in time")


def wait_for_agent_health(agent_id: str, timeout_seconds: int = 120) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            result = api_post(f"/api/agents/{agent_id}/health/check", {})
            if result.get("status") == "ok":
                return
        except RuntimeError:
            pass
        time.sleep(1)
    raise RuntimeError(f"Agent did not become healthy in time: {agent_id}")


def load_manifest(name: str) -> dict[str, Any]:
    path = CONTRACT_ROOT / "examples" / name
    return json.loads(path.read_text(encoding="utf-8"))


def patch_hanhai_manifest(manifest: dict[str, Any]) -> None:
    public_url = os.getenv("COURSE_AGENT_PUBLIC_URL", "http://127.0.0.1:8002").rstrip("/")
    internal_url = os.getenv("COURSE_AGENT_INTERNAL_URL", "http://course-agent:8000").rstrip("/")
    integration = manifest["integration"]
    integration["launch_url"] = f"{public_url}/"
    integration["chat_endpoint"] = f"{internal_url}/api/hub/chat"
    integration["health_endpoint"] = f"{internal_url}/api/health"
    integration["callback_urls"] = [f"{public_url}/api/hub/callback"]


def patch_demo_manifest(manifest: dict[str, Any]) -> None:
    public_url = os.getenv("DEMO_AGENT_PUBLIC_URL", "http://127.0.0.1:8101").rstrip("/")
    internal_url = os.getenv("DEMO_AGENT_INTERNAL_URL", "http://demo-agent:8101").rstrip("/")
    integration = manifest["integration"]
    integration["launch_url"] = f"{public_url}/"
    integration["chat_endpoint"] = f"{internal_url}/api/chat"
    integration["health_endpoint"] = f"{internal_url}/api/health"
    integration.pop("callback_urls", None)


def submit_manifest(manifest: dict[str, Any]) -> str:
    response = api_post(
        "/api/registry/agents",
        {"manifest": manifest, "trust_level": "first_party_internal"},
        tolerate_status={409},
    )
    if response.get("detail", {}).get("error") == "version_already_exists":
        response = api_get(f"/api/admin/agents/{manifest['id']}")
    if "versions" not in response:
        raise RuntimeError(f"Unexpected submit response for {manifest['id']}")
    version = next(
        item for item in response["versions"] if item["version"] == manifest["version"]
    )
    return version["version_id"]


def review_version(agent_id: str, version_id: str, *, featured: bool) -> None:
    response = api_post(
        f"/api/admin/agents/{agent_id}/versions/{version_id}/review",
        {"decision": "approved", "notes": "compose bootstrap", "featured": featured},
        tolerate_status={409},
    )
    if response.get("detail", {}).get("error") == "version_already_reviewed":
        return


def create_credential(agent_id: str) -> dict[str, str]:
    response = api_post(f"/api/admin/agents/{agent_id}/credentials", {})
    secret = response.get("client_secret")
    if not isinstance(secret, str) or not secret:
        raise RuntimeError("Hub did not return a one-time client secret")
    print("Created runtime-only Featured credential for hanhai-course-agent.")
    return response


def write_course_agent_secret(client_secret: str) -> None:
    SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    SECRET_PATH.write_text(client_secret + "\n", encoding="utf-8")
    try:
        SECRET_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def api_get(path: str) -> dict[str, Any]:
    req = request.Request(
        HUB_URL + path,
        method="GET",
        headers={"X-Hub-User": ADMIN_USER, "Accept": "application/json"},
    )
    return send(req)


def api_post(path: str, payload: dict[str, Any], tolerate_status: set[int] | None = None) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        HUB_URL + path,
        data=body,
        method="POST",
        headers={
            "X-Hub-User": ADMIN_USER,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    return send(req, tolerate_status=tolerate_status or set())


def send(req: request.Request, tolerate_status: set[int] | None = None) -> dict[str, Any]:
    tolerate_status = tolerate_status or set()
    try:
        with request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        data = exc.read().decode("utf-8")
        if exc.code in tolerate_status:
            return json.loads(data) if data else {"status_code": exc.code}
        raise RuntimeError(f"Hub API failed with HTTP {exc.code}: {redact(data)}") from exc
    except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Hub API request failed: {exc}") from exc


def redact(value: str) -> str:
    for key in ("client_secret", "code", "state"):
        value = value.replace(key, f"{key[0]}***")
    return value[:500]


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Hub demo bootstrap failed: {exc}", file=sys.stderr)
        raise
