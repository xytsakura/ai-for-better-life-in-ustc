from __future__ import annotations

import base64
import json
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from hub.config import Settings
from hub.db import database
from hub.gateway import _simple_chat_stream
from hub.identity import IdentityService
from hub.main import create_app
from hub.schemas import RunAgentInput


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(
        database_path=tmp_path / "hub.sqlite3",
        demo_mode=True,
        automatic_checks_enabled=False,
        require_passing_checks=False,
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

    source = manifest(mode="connected")
    source["icon"] = "https://example.com/agent.png"
    submitted = submit(client, source)
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
    assert "icon" not in public["active_version"]["manifest"]
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


def test_agui_gateway_rejects_malformed_message_sequence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = make_client(tmp_path)
    submitted = submit(client, manifest(mode="connected", protocol="ag-ui"))
    approve(client, "demo-agent", submitted["versions"][0]["version_id"])

    class FakeStreamResponse:
        status_code = 200
        headers = {"content-type": "text/event-stream"}

        async def aiter_lines(self):
            yield 'data: {"type":"RUN_STARTED","threadId":"thread-1","runId":"run-1"}'
            yield ""
            yield 'data: {"type":"TEXT_MESSAGE_CONTENT","messageId":"missing","delta":"unsafe"}'
            yield ""

    class FakeStreamContext:
        async def __aenter__(self) -> FakeStreamResponse:
            return FakeStreamResponse()

        async def __aexit__(self, *args: Any) -> None:
            return None

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        def stream(self, *args: Any, **kwargs: Any) -> FakeStreamContext:
            return FakeStreamContext()

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
    assert response.status_code == 200
    assert '"code":"protocol_error"' in response.text
    assert "unsafe" not in response.text
    with database(client.app.state.settings.database_path) as conn:
        invocation = conn.execute(
            "SELECT status, error_code FROM hub_invocations ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    assert dict(invocation) == {"status": "error", "error_code": "protocol_error"}


@pytest.mark.asyncio
async def test_client_disconnect_is_recorded_as_cancelled(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    submitted = submit(client, manifest(mode="connected", protocol="simple-chat"))
    version_id = submitted["versions"][0]["version_id"]
    approve(client, "demo-agent", version_id)
    invocation_id = "inv-cancelled"
    with database(client.app.state.settings.database_path) as conn:
        conn.execute(
            """
            INSERT INTO hub_invocations (
              invocation_id, agent_id, version_id, user_id, run_id, status, started_at
            ) VALUES (?, 'demo-agent', ?, 'demo-c', 'run-cancelled', 'started', '2026-08-06T00:00:00Z')
            """,
            (invocation_id, version_id),
        )

    class DisconnectedRequest:
        async def is_disconnected(self) -> bool:
            return True

    run_input = RunAgentInput.model_validate(
        {
            "threadId": "thread-cancelled",
            "runId": "run-cancelled",
            "messages": [{"id": "user-1", "role": "user", "content": "hello"}],
            "tools": [],
            "context": [],
        }
    )
    chunks = [
        chunk
        async for chunk in _simple_chat_stream(
            settings=client.app.state.settings,
            endpoint="http://127.0.0.1:9101/chat",
            payload={},
            run_input=run_input,
            token="unused",
            request_id="request-cancelled",
            request=DisconnectedRequest(),
            invocation_id=invocation_id,
        )
    ]
    assert chunks == []
    with database(client.app.state.settings.database_path) as conn:
        invocation = conn.execute(
            "SELECT status, error_code FROM hub_invocations WHERE invocation_id = ?",
            (invocation_id,),
        ).fetchone()
    assert dict(invocation) == {"status": "cancelled", "error_code": "client_cancelled"}


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
    root = client.get("/")
    assert root.status_code == 200
    assert root.headers["cache-control"] == "no-store, no-cache, must-revalidate, max-age=0"
    hub_root = client.get("/hub")
    assert hub_root.status_code == 200
    assert hub_root.headers["cache-control"] == "no-store, no-cache, must-revalidate, max-age=0"
    deep_link = client.get("/hub/agents/demo/chat")
    assert deep_link.status_code == 200
    assert deep_link.headers["cache-control"] == "no-store, no-cache, must-revalidate, max-age=0"
    assert "Campus Agent Hub" in deep_link.text
    assert 'href="/styles.css?' in deep_link.text
    assert 'src="/assets/ustc-emblem.jpg"' in deep_link.text
    assert 'src="/splash.js?' in deep_link.text
    assert 'src="/hub-theme.js?' in deep_link.text
    assert 'src="/app.js?' in deep_link.text
    assert 'href="./styles.css?' not in deep_link.text
    assert 'src="./' not in deep_link.text
    app_script = client.get("/app.js")
    assert app_script.status_code == 200
    assert app_script.headers["cache-control"] == "no-store, no-cache, must-revalidate, max-age=0"
    assert "from './hub-core.js?v=" in app_script.text
    assert "data-model-settings" in app_script.text
    assert "多模型配置中心" in app_script.text
    assert "data-new-profile" in app_script.text
    assert "发现模型" in app_script.text
    assert 'name="api_key" type="password" value=""' in app_script.text
    assert "clearSettings();" in app_script.text
    for asset_path in ("/hub-core.js", "/hub-theme.js", "/splash.js", "/starfield.js", "/styles.css"):
        asset = client.get(asset_path)
        assert asset.status_code == 200
        assert asset.headers["cache-control"] == "no-store, no-cache, must-revalidate, max-age=0"
    assert "/api/model-profiles" in client.get("/hub-core.js").text

    # Fingerprinted and mounted assets keep their own cache behavior; only the
    # mutable SPA entry points and top-level bundles are forced to revalidate.
    assert client.get("/assets/ustc-emblem.jpg").status_code == 200


class ConformanceResponse:
    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        *,
        content_type: str = "application/json",
        content: bytes | None = None,
    ) -> None:
        self.status_code = 200
        self.headers = {"content-type": content_type}
        self._payload = payload or {}
        self.content = content if content is not None else json.dumps(self._payload, ensure_ascii=False).encode("utf-8")
        self.text = self.content.decode("utf-8", errors="replace")

    def json(self) -> dict[str, Any]:
        return self._payload


class PassingConformanceClient:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> "PassingConformanceClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def request(self, method: str, url: str, **kwargs: Any) -> ConformanceResponse:
        assert method == "GET"
        return ConformanceResponse({"page": "ok"}, content_type="text/html")

    async def get(self, url: str, **kwargs: Any) -> ConformanceResponse:
        return ConformanceResponse(
            {"status": "ok", "version": "1.0.0", "contract_version": "1.0", "capabilities": ["streaming"]}
        )

    async def post(self, url: str, **kwargs: Any) -> ConformanceResponse:
        return ConformanceResponse(
            {"message": {"id": "assistant-1", "role": "assistant", "content": "ok"}, "citations": [], "usage": {}}
        )


def test_approval_requires_passing_machine_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        database_path=tmp_path / "hub.sqlite3",
        demo_mode=True,
        automatic_checks_enabled=False,
        require_passing_checks=True,
        internal_url_allowlist=("http://127.0.0.1:9101",),
    )
    client = TestClient(create_app(settings=settings))
    submitted = client.post(
        "/api/registry/agents",
        json={"manifest": manifest(mode="connected", protocol="simple-chat"), "trust_level": "first_party_internal"},
        headers={"X-Hub-User": "demo-a"},
    ).json()
    version_id = submitted["versions"][0]["version_id"]

    rejected = client.post(
        f"/api/admin/agents/demo-agent/versions/{version_id}/review",
        json={"decision": "approved", "notes": "not checked", "featured": False},
        headers={"X-Hub-User": "demo-a"},
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["error"] == "conformance_checks_not_passed"

    monkeypatch.setattr("hub.conformance.httpx.AsyncClient", PassingConformanceClient)
    checked = client.post(
        f"/api/admin/agents/demo-agent/versions/{version_id}/checks",
        json={},
        headers={"X-Hub-User": "demo-a"},
    )
    assert checked.status_code == 200
    assert checked.json()["overall_status"] == "passed"

    approved = client.post(
        f"/api/admin/agents/demo-agent/versions/{version_id}/review",
        json={"decision": "approved", "notes": "checks passed", "featured": False},
        headers={"X-Hub-User": "demo-a"},
    )
    assert approved.status_code == 200
    version = approved.json()["versions"][0]
    assert version["check_status"] == "passed"
    assert {item["name"] for item in version["checks"]} == {
        "url_safety",
        "launch_url",
        "health_contract",
        "chat_contract",
    }


def test_external_icon_is_validated_cached_and_served_by_hub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        database_path=tmp_path / "hub.sqlite3",
        asset_cache_dir=tmp_path / "assets",
        demo_mode=True,
        automatic_checks_enabled=False,
        require_passing_checks=True,
        internal_url_allowlist=("http://127.0.0.1:9101",),
    )
    client = TestClient(create_app(settings=settings))
    source = manifest(mode="connected", protocol="simple-chat")
    source["icon"] = "http://127.0.0.1:9101/icon.png"
    submitted = submit(client, source)
    version_id = submitted["versions"][0]["version_id"]
    png = b"\x89PNG\r\n\x1a\n" + b"safe-fixture"

    class IconConformanceClient(PassingConformanceClient):
        async def request(self, method: str, url: str, **kwargs: Any) -> ConformanceResponse:
            if url.endswith("/icon.png"):
                return ConformanceResponse(content_type="image/png", content=png)
            return ConformanceResponse({"page": "ok"}, content_type="text/html")

    monkeypatch.setattr("hub.conformance.httpx.AsyncClient", IconConformanceClient)
    checked = client.post(
        f"/api/admin/agents/demo-agent/versions/{version_id}/checks",
        json={},
        headers={"X-Hub-User": "demo-a"},
    )
    assert checked.status_code == 200
    assert checked.json()["overall_status"] == "passed"
    assert any(item["name"] == "icon_cache" for item in checked.json()["checks"])
    approve(client, "demo-agent", version_id)

    public = client.get("/api/agents/demo-agent").json()
    assert public["active_version"]["manifest"]["icon"] == f"/api/assets/agent-icons/{version_id}"
    icon = client.get(f"/api/assets/agent-icons/{version_id}")
    assert icon.status_code == 200
    assert icon.headers["content-type"] == "image/png"
    assert icon.content == png


def test_icon_cache_rejects_content_type_spoofing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        database_path=tmp_path / "hub.sqlite3",
        asset_cache_dir=tmp_path / "assets",
        demo_mode=True,
        automatic_checks_enabled=False,
        require_passing_checks=False,
        internal_url_allowlist=("http://127.0.0.1:9101",),
    )
    client = TestClient(create_app(settings=settings))
    source = manifest(mode="connected", protocol="simple-chat")
    source["icon"] = "http://127.0.0.1:9101/icon.png"
    submitted = submit(client, source)
    version_id = submitted["versions"][0]["version_id"]

    class SpoofedIconClient(PassingConformanceClient):
        async def request(self, method: str, url: str, **kwargs: Any) -> ConformanceResponse:
            if url.endswith("/icon.png"):
                return ConformanceResponse(content_type="image/png", content=b"<script>alert(1)</script>")
            return ConformanceResponse({"page": "ok"}, content_type="text/html")

    monkeypatch.setattr("hub.conformance.httpx.AsyncClient", SpoofedIconClient)
    checked = client.post(
        f"/api/admin/agents/demo-agent/versions/{version_id}/checks",
        json={},
        headers={"X-Hub-User": "demo-a"},
    )
    assert checked.status_code == 200
    assert checked.json()["overall_status"] == "failed"
    assert next(item for item in checked.json()["checks"] if item["name"] == "icon_cache")["error_code"] == "unsafe_asset"


def test_gateway_enforces_persistent_rate_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        database_path=tmp_path / "hub.sqlite3",
        demo_mode=True,
        automatic_checks_enabled=False,
        require_passing_checks=False,
        rate_limit_requests=1,
        internal_url_allowlist=("http://127.0.0.1:9101",),
    )
    client = TestClient(create_app(settings=settings))
    submitted = client.post(
        "/api/registry/agents",
        json={"manifest": manifest(mode="connected", protocol="simple-chat"), "trust_level": "first_party_internal"},
        headers={"X-Hub-User": "demo-a"},
    ).json()
    approve(client, "demo-agent", submitted["versions"][0]["version_id"])

    class GatewayClient(PassingConformanceClient):
        async def post(self, url: str, **kwargs: Any) -> ConformanceResponse:
            return await PassingConformanceClient().post(url, **kwargs)

    monkeypatch.setattr("hub.gateway.httpx.AsyncClient", GatewayClient)
    body = {
        "threadId": "thread-1",
        "runId": "run-1",
        "messages": [{"id": "user-1", "role": "user", "content": "hello"}],
        "state": {},
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }
    first = client.post("/api/gateway/agents/demo-agent/runs", json=body)
    second = client.post("/api/gateway/agents/demo-agent/runs", json={**body, "runId": "run-2"})
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["detail"]["error"] == "rate_limited"


def test_gateway_converts_upstream_timeout_to_terminal_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = make_client(tmp_path)
    submitted = submit(client, manifest(mode="connected", protocol="simple-chat"))
    approve(client, "demo-agent", submitted["versions"][0]["version_id"])

    class TimeoutClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "TimeoutClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(self, *args: Any, **kwargs: Any):
            raise httpx.ReadTimeout("fixture timeout")

    monkeypatch.setattr("hub.gateway.httpx.AsyncClient", TimeoutClient)
    response = client.post(
        "/api/gateway/agents/demo-agent/runs",
        json={
            "threadId": "thread-timeout",
            "runId": "run-timeout",
            "messages": [{"id": "user-1", "role": "user", "content": "hello"}],
            "tools": [],
            "context": [],
        },
    )
    assert response.status_code == 200
    assert '"type":"RUN_ERROR"' in response.text
    assert '"code":"agent_timeout"' in response.text
    with database(client.app.state.settings.database_path) as conn:
        invocation = conn.execute(
            "SELECT status, error_code FROM hub_invocations ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    assert dict(invocation) == {"status": "error", "error_code": "agent_timeout"}


def test_gateway_rejects_oversize_upstream_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        database_path=tmp_path / "hub.sqlite3",
        demo_mode=True,
        automatic_checks_enabled=False,
        require_passing_checks=False,
        max_response_bytes=128,
        internal_url_allowlist=("http://127.0.0.1:9101",),
    )
    client = TestClient(create_app(settings=settings))
    submitted = submit(client, manifest(mode="connected", protocol="simple-chat"))
    approve(client, "demo-agent", submitted["versions"][0]["version_id"])

    class OversizeClient(PassingConformanceClient):
        async def post(self, *args: Any, **kwargs: Any) -> ConformanceResponse:
            return ConformanceResponse(
                {"message": {"id": "assistant-1", "role": "assistant", "content": "x" * 256}}
            )

    monkeypatch.setattr("hub.gateway.httpx.AsyncClient", OversizeClient)
    response = client.post(
        "/api/gateway/agents/demo-agent/runs",
        json={
            "threadId": "thread-large",
            "runId": "run-large",
            "messages": [{"id": "user-1", "role": "user", "content": "hello"}],
            "tools": [],
            "context": [],
        },
    )
    assert response.status_code == 200
    assert '"code":"response_too_large"' in response.text
    assert "x" * 64 not in response.text


def test_gateway_rejects_oversize_body_and_consecutive_unhealthy_agent(tmp_path: Path) -> None:
    settings = Settings(
        database_path=tmp_path / "hub.sqlite3",
        demo_mode=True,
        automatic_checks_enabled=False,
        require_passing_checks=False,
        max_request_bytes=128,
        health_failure_threshold=2,
        internal_url_allowlist=("http://127.0.0.1:9101",),
    )
    client = TestClient(create_app(settings=settings))
    submitted = client.post(
        "/api/registry/agents",
        json={"manifest": manifest(mode="connected", protocol="simple-chat"), "trust_level": "first_party_internal"},
        headers={"X-Hub-User": "demo-a"},
    ).json()
    version_id = submitted["versions"][0]["version_id"]
    approve(client, "demo-agent", version_id)

    oversized = client.post(
        "/api/gateway/agents/demo-agent/runs",
        content=json.dumps({"payload": "x" * 200}),
        headers={"content-type": "application/json", "X-Hub-User": "demo-c"},
    )
    assert oversized.status_code == 413

    with database(settings.database_path) as conn:
        for index in range(2):
            conn.execute(
                """
                INSERT INTO hub_health_checks (
                  health_id, agent_id, version_id, status, capabilities_json, checked_at
                ) VALUES (?, 'demo-agent', ?, 'offline', '[]', ?)
                """,
                (f"health-{index}", version_id, f"2026-08-06T00:00:0{index}Z"),
            )
    unavailable = client.post(
        "/api/gateway/agents/demo-agent/runs",
        json={"threadId": "t", "runId": "r", "messages": [], "tools": [], "context": []},
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"]["error"] == "agent_unavailable"


def test_conformance_revalidates_dangerous_redirect_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        database_path=tmp_path / "hub.sqlite3",
        demo_mode=True,
        automatic_checks_enabled=False,
        require_passing_checks=False,
        internal_url_allowlist=("http://127.0.0.1:9101",),
    )
    client = TestClient(create_app(settings=settings))
    submitted = submit(client, manifest(mode="connected", protocol="simple-chat"))
    version_id = submitted["versions"][0]["version_id"]

    class RedirectClient(PassingConformanceClient):
        async def request(self, method: str, url: str, **kwargs: Any) -> ConformanceResponse:
            response = ConformanceResponse()
            response.status_code = 302
            response.headers = {"location": "http://169.254.169.254/latest/meta-data"}
            return response

    monkeypatch.setattr("hub.conformance.httpx.AsyncClient", RedirectClient)
    checked = client.post(
        f"/api/admin/agents/demo-agent/versions/{version_id}/checks",
        json={},
        headers={"X-Hub-User": "demo-a"},
    )
    assert checked.status_code == 200
    assert checked.json()["overall_status"] == "failed"
    launch = next(item for item in checked.json()["checks"] if item["name"] == "launch_url")
    assert launch["status"] == "failed"
    assert launch["error_code"] == "agent_unavailable"


def test_background_health_monitor_polls_active_connected_agents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    polled = threading.Event()

    async def fake_health_check(conn, *, agent_id: str, settings: Settings):
        assert agent_id == "demo-agent"
        polled.set()
        return {"status": "ok"}

    monkeypatch.setattr("hub.main.check_agent_health", fake_health_check)
    settings = Settings(
        database_path=tmp_path / "hub.sqlite3",
        demo_mode=True,
        automatic_checks_enabled=False,
        require_passing_checks=False,
        health_poll_interval_seconds=0.02,
        internal_url_allowlist=("http://127.0.0.1:9101",),
    )
    with TestClient(create_app(settings=settings)) as client:
        submitted = submit(client, manifest(mode="connected", protocol="simple-chat"))
        approve(client, "demo-agent", submitted["versions"][0]["version_id"])
        assert polled.wait(1.0)


def test_deprecate_and_credential_rotation_are_governed(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    submitted = submit(client, manifest(mode="connected", protocol="simple-chat"))
    version_id = submitted["versions"][0]["version_id"]
    approve(client, "demo-agent", version_id)

    first = client.post(
        "/api/admin/agents/demo-agent/credentials",
        headers={"X-Hub-User": "demo-a"},
    ).json()
    second = client.post(
        "/api/admin/agents/demo-agent/credentials",
        headers={"X-Hub-User": "demo-a"},
    ).json()
    with database(client.app.state.settings.database_path) as conn:
        statuses = {
            row["credential_id"]: row["status"]
            for row in conn.execute(
                "SELECT credential_id, status FROM hub_agent_credentials WHERE agent_id = 'demo-agent'"
            ).fetchall()
        }
    assert statuses[first["credential_id"]] == "rotating"
    assert statuses[second["credential_id"]] == "active"

    revoked = client.post(
        f"/api/admin/agents/demo-agent/credentials/{second['credential_id']}/status",
        json={"status": "revoked", "reason": "rotation complete"},
        headers={"X-Hub-User": "demo-a"},
    )
    assert revoked.status_code == 200
    deprecated = client.post(
        "/api/admin/agents/demo-agent/deprecate",
        json={"reason": "end of service"},
        headers={"X-Hub-User": "demo-a"},
    )
    assert deprecated.status_code == 200
    assert deprecated.json()["status"] == "deprecated"
    assert client.get("/api/agents").json()["agents"] == []


def test_workspace_code_is_invalid_after_active_version_switch(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    first = submit(client, manifest(mode="connected", full_workspace=True, version="1.0.0"))
    approve(client, "demo-agent", first["versions"][0]["version_id"], featured=True)
    credential = client.post(
        "/api/admin/agents/demo-agent/credentials",
        headers={"X-Hub-User": "demo-a"},
    ).json()
    start = client.post(
        "/api/agents/demo-agent/workspace/start",
        json={"state": "state-version-switch-1234"},
        headers={"X-Hub-User": "demo-c"},
    ).json()
    code = start["launch_url"].split("code=", 1)[1].split("&", 1)[0]

    second = submit(client, manifest(mode="connected", full_workspace=True, version="1.1.0"))
    approve(client, "demo-agent", second["versions"][0]["version_id"], featured=True)
    basic = "Basic " + base64.b64encode(
        f"demo-agent:{credential['client_secret']}".encode()
    ).decode()
    exchange = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://127.0.0.1:9101/callback",
            "state": "state-version-switch-1234",
        },
        headers={"Authorization": basic},
    )
    assert exchange.status_code == 400
    assert exchange.json()["detail"]["error"] == "invalid_grant"


def test_rollback_restores_featured_approval_of_target_version(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    first = submit(client, manifest(mode="connected", full_workspace=True, version="1.0.0"))
    first_version = first["versions"][0]["version_id"]
    approve(client, "demo-agent", first_version, featured=True)
    second = submit(client, manifest(mode="connected", full_workspace=True, version="1.1.0"))
    second_version = second["versions"][0]["version_id"]
    approve(client, "demo-agent", second_version, featured=False)

    assert client.get("/api/agents/demo-agent").json()["featured"] is False
    rolled_back = client.post(
        "/api/admin/agents/demo-agent/rollback",
        json={"version_id": first_version, "reason": "restore featured release"},
        headers={"X-Hub-User": "demo-a"},
    )
    assert rolled_back.status_code == 200
    assert rolled_back.json()["active_version_id"] == first_version
    assert rolled_back.json()["featured"] is True
    workspace = client.post(
        "/api/agents/demo-agent/workspace/start",
        json={"state": "state-after-featured-rollback"},
        headers={"X-Hub-User": "demo-c"},
    )
    assert workspace.status_code == 200


def test_jwks_can_publish_previous_public_key_during_rotation(tmp_path: Path) -> None:
    first = IdentityService(Settings(database_path=tmp_path / "first.sqlite3", jwt_kid="old-key"))
    previous = first.jwks()["keys"][0]
    rotated = IdentityService(
        Settings(
            database_path=tmp_path / "second.sqlite3",
            jwt_kid="new-key",
            jwt_previous_public_jwk_json=json.dumps(previous),
        )
    )
    keys = rotated.jwks()["keys"]
    assert [key["kid"] for key in keys] == ["new-key", "old-key"]
    assert all("d" not in key for key in keys)


def test_identity_signing_key_survives_hub_restart(tmp_path: Path) -> None:
    key_file = tmp_path / "runtime" / "jwt-ed25519.pem"
    first = IdentityService(
        Settings(
            database_path=tmp_path / "first.sqlite3",
            jwt_private_key_file=key_file,
        )
    )
    second = IdentityService(
        Settings(
            database_path=tmp_path / "second.sqlite3",
            jwt_private_key_file=key_file,
        )
    )
    assert key_file.is_file()
    assert first.jwks() == second.jwks()


def external_link_manifest(*, agent_id: str, version: str = "1.0.0") -> dict[str, Any]:
    data = manifest(agent_id=agent_id, version=version, mode="link")
    data["integration"]["launch_url"] = f"https://example.com/{agent_id}"
    return data


def test_t3_registry_and_developer_submission_permissions(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    for headers in ({}, {"X-Hub-User": "demo-c"}):
        denied = client.post(
            "/api/registry/agents",
            json=external_link_manifest(agent_id="denied-agent"),
            headers=headers,
        )
        assert denied.status_code == 403
        assert denied.json()["detail"]["error"] == "developer_or_admin_required"

        denied_submissions = client.get("/api/developer/submissions", headers=headers)
        assert denied_submissions.status_code == 403
        assert denied_submissions.json()["detail"]["error"] == "developer_or_admin_required"

    developer_submission = client.post(
        "/api/registry/agents",
        json=external_link_manifest(agent_id="developer-agent"),
        headers={"X-Hub-User": "demo-b"},
    )
    assert developer_submission.status_code == 201, developer_submission.text
    developer_version = developer_submission.json()["versions"][0]
    assert developer_version["submitted_by"] == "demo-b"

    admin_submission = client.post(
        "/api/registry/agents",
        json={"manifest": manifest(agent_id="admin-agent"), "trust_level": "first_party_internal"},
        headers={"X-Hub-User": "demo-a"},
    )
    assert admin_submission.status_code == 201, admin_submission.text
    assert admin_submission.json()["versions"][0]["submitted_by"] == "demo-a"

    admin_replacement = client.post(
        "/api/registry/agents",
        json={
            "manifest": manifest(agent_id="developer-agent", version="1.1.0"),
            "trust_level": "first_party_internal",
        },
        headers={"X-Hub-User": "demo-a"},
    )
    assert admin_replacement.status_code == 201, admin_replacement.text
    approve(client, "developer-agent", admin_replacement.json()["versions"][0]["version_id"])

    developer_view = client.get(
        "/api/developer/submissions",
        headers={"X-Hub-User": "demo-b"},
    )
    assert developer_view.status_code == 200
    developer_agents = developer_view.json()["agents"]
    assert [agent["agent_id"] for agent in developer_agents] == ["developer-agent"]
    assert all(
        version["submitted_by"] == "demo-b"
        for agent in developer_agents
        for version in agent["versions"]
    )
    assert developer_agents[0]["active_version"] is None
    assert developer_agents[0]["active_version_id"] is None

    admin_view = client.get("/api/developer/submissions", headers={"X-Hub-User": "demo-a"})
    assert admin_view.status_code == 200
    assert {agent["agent_id"] for agent in admin_view.json()["agents"]} == {
        "developer-agent",
        "admin-agent",
    }


def test_t3_admin_endpoint_permission_matrix(tmp_path: Path) -> None:
    def prepare(endpoint_name: str) -> tuple[TestClient, str, str, str | None]:
        client = make_client(tmp_path / endpoint_name)
        submitted = submit(client, manifest(mode="connected", protocol="simple-chat", version="1.0.0"))
        first_version = submitted["versions"][0]["version_id"]
        credential_id: str | None = None

        if endpoint_name in {"detail", "review", "checks", "audit"}:
            return client, "demo-agent", first_version, credential_id

        approve(client, "demo-agent", first_version)

        if endpoint_name == "restore":
            suspended = client.post(
                "/api/admin/agents/demo-agent/suspend",
                json={"reason": "prepare restore"},
                headers={"X-Hub-User": "demo-a"},
            )
            assert suspended.status_code == 200

        if endpoint_name == "rollback":
            second = submit(client, manifest(mode="connected", protocol="simple-chat", version="1.1.0"))
            approve(client, "demo-agent", second["versions"][0]["version_id"])

        if endpoint_name == "credential_status":
            credential = client.post(
                "/api/admin/agents/demo-agent/credentials",
                headers={"X-Hub-User": "demo-a"},
            )
            assert credential.status_code == 201
            credential_id = credential.json()["credential_id"]

        return client, "demo-agent", first_version, credential_id

    cases = {
        "list": ("GET", "/api/admin/agents", None),
        "detail": ("GET", "/api/admin/agents/{agent_id}", None),
        "review": (
            "POST",
            "/api/admin/agents/{agent_id}/versions/{version_id}/review",
            {"decision": "approved", "notes": "t3 matrix"},
        ),
        "checks": ("POST", "/api/admin/agents/{agent_id}/versions/{version_id}/checks", {}),
        "suspend": ("POST", "/api/admin/agents/{agent_id}/suspend", {"reason": "t3 matrix"}),
        "restore": ("POST", "/api/admin/agents/{agent_id}/restore", {"reason": "t3 matrix"}),
        "deprecate": ("POST", "/api/admin/agents/{agent_id}/deprecate", {"reason": "t3 matrix"}),
        "rollback": ("POST", "/api/admin/agents/{agent_id}/rollback", {"version_id": None, "reason": "t3 matrix"}),
        "credentials": ("POST", "/api/admin/agents/{agent_id}/credentials", None),
        "credential_status": (
            "POST",
            "/api/admin/agents/{agent_id}/credentials/{credential_id}/status",
            {"status": "revoked", "reason": "t3 matrix"},
        ),
        "health": ("POST", "/api/agents/{agent_id}/health/check", None),
        "audit": ("GET", "/api/admin/audit", None),
    }

    for name, (method, template, body) in cases.items():
        client, agent_id, version_id, credential_id = prepare(name)
        path = template.format(
            agent_id=agent_id,
            version_id=version_id,
            credential_id=credential_id or "missing-credential",
        )
        for headers in ({}, {"X-Hub-User": "demo-c"}, {"X-Hub-User": "demo-b"}):
            response = client.request(method, path, json=body, headers=headers)
            assert response.status_code == 403, (name, headers, response.status_code, response.text)
            assert response.json()["detail"]["error"] == "admin_required"

        admin_response = client.request(
            method,
            path,
            json=body,
            headers={"X-Hub-User": "demo-a"},
        )
        assert admin_response.status_code < 400, (name, admin_response.status_code, admin_response.text)
