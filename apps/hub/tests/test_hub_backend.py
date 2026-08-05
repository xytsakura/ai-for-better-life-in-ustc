from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hub.config import Settings
from hub.main import create_app


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(
        database_path=tmp_path / "hub.sqlite3",
        demo_mode=True,
        public_base_url="http://127.0.0.1:8100",
        internal_url_allowlist=("http://127.0.0.1:9101", "http://agent.internal"),
    )
    return TestClient(create_app(settings=settings))


def test_hub_fails_closed_when_demo_identity_mode_is_disabled(tmp_path: Path) -> None:
    client = TestClient(create_app(settings=Settings(database_path=tmp_path / "hub.sqlite3")))

    health = client.get("/healthz")
    response = client.get("/api/session", headers={"X-Hub-User": "demo-a"})

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "authentication_required"


def manifest(
    *,
    agent_id: str = "demo-agent",
    version: str = "1.0.0",
    mode: str = "link",
    protocol: str = "ag-ui",
    full_workspace: bool = False,
) -> dict[str, Any]:
    integration: dict[str, Any] = {
        "mode": mode,
        "launch_url": "http://127.0.0.1:9101/app",
    }
    if mode == "connected":
        integration["protocol"] = protocol
        integration["chat_endpoint"] = "http://127.0.0.1:9101/chat"
        integration["health_endpoint"] = "http://127.0.0.1:9101/health"
        integration["callback_urls"] = ["http://127.0.0.1:9101/callback"]
    return {
        "schema_version": "1.0",
        "id": agent_id,
        "name": "演示 Agent",
        "description": "用于测试 Hub 接入的演示 Agent",
        "version": version,
        "owner": "AI for better life In ustc",
        "category": "学习助手",
        "tags": ["demo"],
        "integration": integration,
        "capabilities": ["streaming"] + (["full-workspace"] if full_workspace else []),
        "data_policy": {
            "receives_user_identity": mode == "connected",
            "receives_files": False,
            "stores_conversation": False,
        },
    }


def submit(client: TestClient, data: dict[str, Any]) -> dict[str, Any]:
    response = client.post(
        "/api/registry/agents",
        json={"manifest": data, "trust_level": "first_party_internal"},
        headers={"X-Hub-User": "demo-a"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def approve(
    client: TestClient,
    agent_id: str,
    version_id: str,
    *,
    featured: bool = False,
) -> dict[str, Any]:
    response = client.post(
        f"/api/admin/agents/{agent_id}/versions/{version_id}/review",
        json={"decision": "approved", "notes": "ok", "featured": featured},
        headers={"X-Hub-User": "demo-a"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_registry_review_public_sanitizes_private_endpoints(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    submitted = submit(client, manifest(mode="connected"))
    version_id = submitted["versions"][0]["version_id"]

    assert client.get("/api/agents").json()["agents"] == []

    approve(client, "demo-agent", version_id)
    response = client.get("/api/agents/demo-agent")
    assert response.status_code == 200
    public = response.json()
    assert public["status"] == "active"
    assert public["active_version"]["manifest"]["integration"]["mode"] == "connected"
    assert "chat_endpoint" not in public["active_version"]["manifest"]["integration"]
    assert "health_endpoint" not in public["active_version"]["manifest"]["integration"]
    assert "callback_urls" not in public["active_version"]["manifest"]["integration"]
    assert "trust_level" not in public["active_version"]

    admin = client.get("/api/admin/agents/demo-agent", headers={"X-Hub-User": "demo-a"}).json()
    assert "chat_endpoint" in admin["active_version"]["manifest"]["integration"]


def test_suspend_restore_and_rollback_state_machine(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    first = submit(client, manifest(version="1.0.0"))
    first_version = first["versions"][0]["version_id"]
    approve(client, "demo-agent", first_version)

    second = submit(client, manifest(version="1.1.0"))
    second_version = second["versions"][0]["version_id"]
    approved = approve(client, "demo-agent", second_version)
    assert approved["active_version_id"] == second_version
    assert approved["previous_active_version_id"] == first_version

    suspended = client.post(
        "/api/admin/agents/demo-agent/suspend",
        json={"reason": "maintenance"},
        headers={"X-Hub-User": "demo-a"},
    )
    assert suspended.status_code == 200
    assert client.get("/api/agents").json()["agents"] == []

    restored = client.post(
        "/api/admin/agents/demo-agent/restore",
        json={"reason": "fixed"},
        headers={"X-Hub-User": "demo-a"},
    )
    assert restored.status_code == 200

    rolled_back = client.post(
        "/api/admin/agents/demo-agent/rollback",
        json={"reason": "regression"},
        headers={"X-Hub-User": "demo-a"},
    )
    assert rolled_back.status_code == 200
    assert rolled_back.json()["active_version_id"] == first_version


def test_third_party_external_rejects_private_or_plain_http(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    bad = manifest()
    response = client.post(
        "/api/registry/agents",
        json={"manifest": bad, "trust_level": "third_party_external"},
        headers={"X-Hub-User": "demo-b"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] in {"url_requires_https", "private_url_not_allowed"}

    self_trusted = client.post(
        "/api/registry/agents",
        json={"manifest": bad, "trust_level": "first_party_internal"},
        headers={"X-Hub-User": "demo-b"},
    )
    assert self_trusted.status_code == 403


def test_third_party_external_rejects_hostname_resolving_to_private_ip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = make_client(tmp_path)
    private_dns = manifest()
    private_dns["integration"]["launch_url"] = "https://agent.example/app"
    monkeypatch.setattr(
        "hub.security.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("127.0.0.1", 443))],
    )

    response = client.post(
        "/api/registry/agents",
        json=private_dns,
        headers={"X-Hub-User": "demo-b"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "private_url_not_allowed"


def test_manifest_validation_error_is_json_serializable(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    invalid = manifest()
    invalid["tags"] = ["duplicate", "duplicate"]

    response = client.post(
        "/api/registry/agents",
        json={"manifest": invalid, "trust_level": "first_party_internal"},
        headers={"X-Hub-User": "demo-a"},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail[0]["loc"] == ["manifest", "tags"]
    assert detail[0]["msg"] == "Value error, tags must be unique"
    assert "ctx" not in detail[0]


def test_workspace_code_exchange_requires_client_secret_and_is_single_use(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    submitted = submit(client, manifest(mode="connected", full_workspace=True))
    version_id = submitted["versions"][0]["version_id"]
    approve(client, "demo-agent", version_id, featured=True)
    secret_response = client.post(
        "/api/admin/agents/demo-agent/credentials",
        headers={"X-Hub-User": "demo-a"},
    )
    assert secret_response.status_code == 201
    secret_body = secret_response.json()

    start = client.post(
        "/api/agents/demo-agent/workspace/start",
        json={"state": "state-1234567890"},
        headers={"X-Hub-User": "demo-c"},
    )
    assert start.status_code == 200, start.text
    launch_url = start.json()["launch_url"]
    code = launch_url.split("code=", 1)[1].split("&", 1)[0]

    bad_auth = "Basic " + base64.b64encode(b"demo-agent:wrong").decode()
    bad = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://127.0.0.1:9101/callback",
            "state": "state-1234567890",
        },
        headers={"Authorization": bad_auth},
    )
    assert bad.status_code == 401

    good_auth = "Basic " + base64.b64encode(
        f"demo-agent:{secret_body['client_secret']}".encode()
    ).decode()
    wrong_state = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://127.0.0.1:9101/callback",
            "state": "state-wrong-123456",
        },
        headers={"Authorization": good_auth},
    )
    assert wrong_state.status_code == 400

    good = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://127.0.0.1:9101/callback",
            "state": "state-1234567890",
        },
        headers={"Authorization": good_auth},
    )
    assert good.status_code == 200, good.text
    access_token = good.json()["access_token"]
    header = json.loads(base64.urlsafe_b64decode(access_token.split(".")[0] + "=="))
    assert header["alg"] == "EdDSA"
    assert header["kid"] == "hub-dev-ed25519"
    assert client.get("/.well-known/jwks.json").json()["keys"][0]["crv"] == "Ed25519"

    replay = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://127.0.0.1:9101/callback",
            "state": "state-1234567890",
        },
        headers={"Authorization": good_auth},
    )
    assert replay.status_code == 400


def test_simple_chat_gateway_adapts_json_to_agui_sse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client(tmp_path)
    submitted = submit(client, manifest(mode="connected", protocol="simple-chat"))
    approve(client, "demo-agent", submitted["versions"][0]["version_id"])

    class FakeResponse:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "message": {
                    "id": "assistant-1",
                    "role": "assistant",
                    "content": "你好，这是 simple-chat 响应",
                },
                "citations": [{"label": "S1", "title": "来源"}],
                "usage": {"input_tokens": 1, "output_tokens": 2},
            }

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(self, url: str, **kwargs: Any) -> FakeResponse:
            assert url == "http://127.0.0.1:9101/chat"
            assert kwargs["headers"]["authorization"].startswith("Bearer ")
            assert "x-hub-identity" not in kwargs["headers"]
            assert set(kwargs["json"]) == {"thread_id", "run_id", "messages", "context"}
            assert kwargs["json"]["messages"][0] == {
                "id": "user-1",
                "role": "user",
                "content": "hello",
            }
            return FakeResponse()

    monkeypatch.setattr("hub.gateway.httpx.AsyncClient", FakeAsyncClient)
    response = client.post(
        "/api/gateway/agents/demo-agent/runs",
        json={
            "threadId": "thread-1",
            "runId": "run-1",
            "messages": [{"id": "user-1", "role": "user", "content": "hello"}],
            "state": {},
            "tools": [],
            "context": [],
            "forwardedProps": {},
        },
    )
    assert response.status_code == 200, response.text
    assert "text/event-stream" in response.headers["content-type"]
    assert "RUN_STARTED" in response.text
    assert "TEXT_MESSAGE_CONTENT" in response.text
    assert "RUN_FINISHED" in response.text
    assert "citations" in response.text


def test_workspace_start_requires_featured_agent(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    submitted = submit(client, manifest(mode="connected", full_workspace=True))
    approve(client, "demo-agent", submitted["versions"][0]["version_id"], featured=False)

    response = client.post(
        "/api/agents/demo-agent/workspace/start",
        json={"state": "state-1234567890"},
        headers={"X-Hub-User": "demo-c"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "agent_not_featured"


def test_hub_serves_spa_and_static_assets(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    assert client.get("/").status_code == 200
    deep_link = client.get("/hub/agents/demo/chat")
    assert deep_link.status_code == 200
    assert "Campus Agent Hub" in deep_link.text
    assert 'href="/styles.css?' in deep_link.text
    assert 'src="/assets/ustc-emblem.jpg"' in deep_link.text
    assert 'src="/splash.js?' in deep_link.text
    assert 'src="/app.js?' in deep_link.text
    assert 'href="./styles.css?' not in deep_link.text
    assert 'src="./' not in deep_link.text
    assert client.get("/app.js").status_code == 200
    assert client.get("/assets/ustc-emblem.jpg").status_code == 200
