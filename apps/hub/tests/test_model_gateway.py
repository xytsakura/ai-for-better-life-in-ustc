from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hub.config import Settings
from hub.db import database
from hub.gateway import _record_invocation_end
from hub.main import create_app


def settings_for_models(tmp_path: Path, *, enabled: bool = True, allow_local: bool = True) -> Settings:
    tmp_path.mkdir(parents=True, exist_ok=True)
    key_file = tmp_path / "master.key"
    key_file.write_text("x" * 32, encoding="utf-8")
    return Settings(
        database_path=tmp_path / "hub.sqlite3",
        demo_mode=True,
        automatic_checks_enabled=False,
        require_passing_checks=False,
        internal_url_allowlist=("http://127.0.0.1:9101",),
        model_profiles_enabled=enabled,
        model_profile_master_key_file=key_file if enabled else None,
        allow_local_model_providers=allow_local,
        model_provider_origin_allowlist=("http://127.0.0.1:18080",) if allow_local else (),
    )


def model_client(tmp_path: Path, *, enabled: bool = True, allow_local: bool = True) -> TestClient:
    return TestClient(create_app(settings=settings_for_models(tmp_path, enabled=enabled, allow_local=allow_local)))


def platform_manifest(*, agent_id: str = "hanhai-agent", api_style: str = "responses") -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "id": agent_id,
        "name": "瀚海行 Agent",
        "description": "支持平台模型网关的测试 Agent",
        "version": "1.0.0",
        "owner": "AI for better life In ustc",
        "category": "学习助手",
        "tags": ["demo"],
        "integration": {
            "mode": "connected",
            "protocol": "simple-chat",
            "launch_url": "http://127.0.0.1:9101/app",
            "chat_endpoint": "http://127.0.0.1:9101/chat",
            "health_endpoint": "http://127.0.0.1:9101/health",
            "callback_urls": ["http://127.0.0.1:9101/callback"],
        },
        "capabilities": ["streaming", "full-workspace", "platform-model-gateway"],
        "model_runtime": {
            "mode": "platform_optional",
            "gateway_contract": "campus-model-gateway-v1",
            "supported_api_styles": [api_style],
        },
        "data_policy": {
            "receives_user_identity": True,
            "receives_files": False,
            "stores_conversation": False,
        },
    }


def submit_and_approve(client: TestClient, manifest: dict[str, Any]) -> None:
    submitted = client.post(
        "/api/registry/agents",
        json={"manifest": manifest, "trust_level": "first_party_internal"},
        headers={"X-Hub-User": "demo-a"},
    )
    assert submitted.status_code == 201, submitted.text
    version_id = submitted.json()["versions"][0]["version_id"]
    approved = client.post(
        f"/api/admin/agents/{manifest['id']}/versions/{version_id}/review",
        json={"decision": "approved", "notes": "model gateway test", "featured": True},
        headers={"X-Hub-User": "demo-a"},
    )
    assert approved.status_code == 200, approved.text


def create_profile(client: TestClient, *, api_style: str = "responses", user: str = "demo-c") -> str:
    response = client.post(
        "/api/model-profiles",
        json={
            "name": f"{api_style} profile",
            "provider": "openai",
            "base_url": "http://127.0.0.1:18080/v1",
            "api_key": "sk-test-secret",
            "api_style": api_style,
            "status": "active",
            "default_model_id": None,
        },
        headers={"X-Hub-User": user},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["name"] == payload["label"]
    assert payload["has_api_key"] is True
    assert payload["api_key_mask"].startswith("••••")
    assert payload["api_key_fingerprint"] == payload["key_fingerprint"]
    assert "default_model_id" in payload
    assert "api_key" not in payload
    return payload["profile_id"]


class FakeModelResponse:
    def __init__(self, payload: dict[str, Any], *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeStreamResponse:
    status_code = 200

    async def aiter_lines(self):
        yield 'data: {"choices":[{"delta":{"content":"你"}}]}'
        yield ""
        yield 'data: {"choices":[{"delta":{"content":"好"}}]}'
        yield 'data: {"usage":{"prompt_tokens":4,"completion_tokens":2,"total_tokens":6}}'
        yield "data: [DONE]"


class FakeStreamContext:
    async def __aenter__(self) -> FakeStreamResponse:
        return FakeStreamResponse()

    async def __aexit__(self, *args: Any) -> None:
        return None


class FakeProviderClient:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> "FakeProviderClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def get(self, url: str, **kwargs: Any) -> FakeModelResponse:
        assert url == "http://127.0.0.1:18080/v1/models"
        assert kwargs["headers"]["authorization"] == "Bearer sk-test-secret"
        return FakeModelResponse(
            {
                "data": [
                    {"id": "gpt-5.6", "owned_by": "fixture"},
                    {"id": "text-embedding-3-large"},
                    {"id": "gpt-image-1"},
                    {"id": "gpt-4o-realtime-preview"},
                    {"id": "codex-auto-review"},
                ]
            }
        )

    async def post(self, url: str, **kwargs: Any) -> FakeModelResponse:
        assert kwargs["headers"]["authorization"] == "Bearer sk-test-secret"
        body = kwargs["json"]
        if url.endswith("/responses"):
            assert body["model"] == "gpt-5.6"
            assert "profile_id" not in body
            return FakeModelResponse(
                {
                    "output_text": "模型网关响应",
                    "usage": {
                        "input_tokens": 3,
                        "output_tokens": 4,
                        "output_tokens_details": {"reasoning_tokens": 1},
                        "input_tokens_details": {"cached_tokens": 2},
                        "total_tokens": 7,
                    },
                }
            )
        raise AssertionError(url)

    def stream(self, method: str, url: str, **kwargs: Any) -> FakeStreamContext:
        assert method == "POST"
        assert url.endswith("/chat/completions")
        assert kwargs["json"]["model"] == "gpt-5.6"
        return FakeStreamContext()


def discover_and_bind(
    client: TestClient,
    profile_id: str,
    *,
    agent_id: str = "hanhai-agent",
    user: str = "demo-c",
) -> None:
    discovered = client.post(
        f"/api/model-profiles/{profile_id}/discover",
        headers={"X-Hub-User": user},
    )
    assert discovered.status_code == 200, discovered.text
    models = discovered.json()["models"]
    by_id = {model["id"]: model for model in models}
    assert set(by_id) == {
        "gpt-5.6",
        "text-embedding-3-large",
        "gpt-image-1",
        "gpt-4o-realtime-preview",
        "codex-auto-review",
    }
    assert by_id["gpt-5.6"]["chat_eligible"] is True
    assert all(
        by_id[model_id]["chat_eligible"] is False
        for model_id in set(by_id) - {"gpt-5.6"}
    )
    bound = client.put(
        f"/api/model-bindings/agents/{agent_id}",
        json={"profile_id": profile_id, "model_id": "gpt-5.6"},
        headers={"X-Hub-User": user},
    )
    assert bound.status_code == 200, bound.text
    assert bound.json()["scope_type"] == "agent"
    assert bound.json()["binding"]["model_id"] == "gpt-5.6"


def issue_workspace_delegation(client: TestClient, *, agent_id: str = "hanhai-agent") -> tuple[str, str, str]:
    credential = client.post(
        f"/api/admin/agents/{agent_id}/credentials",
        headers={"X-Hub-User": "demo-a"},
    ).json()
    start = client.post(
        f"/api/agents/{agent_id}/workspace/start",
        json={"state": "state-model-gateway-1234"},
    ).json()
    code = start["launch_url"].split("code=", 1)[1].split("&", 1)[0]
    basic = "Basic " + base64.b64encode(
        f"{agent_id}:{credential['client_secret']}".encode()
    ).decode()
    payload = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://127.0.0.1:9101/callback",
            "state": "state-model-gateway-1234",
        },
        headers={"Authorization": basic},
    ).json()
    return basic, payload["model_delegation_token"], payload["access_token"]


def exchange_grant(
    client: TestClient,
    *,
    basic: str,
    token: str,
    request_id: str = "request-model-0001",
) -> str:
    response = client.post(
        "/api/model-gateway/grants/exchange",
        json={"model_delegation_token": token, "request_id": request_id},
        headers={"Authorization": basic},
    )
    assert response.status_code == 200, response.text
    assert response.json()["access_token"] == response.json()["grant"]
    return response.json()["access_token"]


def test_model_profiles_are_feature_gated_and_key_file_validated(tmp_path: Path) -> None:
    disabled = model_client(tmp_path / "disabled", enabled=False)
    assert disabled.get("/healthz").status_code == 200
    assert disabled.get("/api/model-profiles").status_code == 404

    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(RuntimeError, match="MASTER_KEY_FILE"):
        create_app(
            settings=Settings(
                database_path=missing,
                demo_mode=True,
                model_profiles_enabled=True,
                model_profile_master_key_file=tmp_path / "missing.key",
            )
        )

    invalid_key = tmp_path / "invalid.key"
    invalid_key.write_text("too-short", encoding="utf-8")
    with pytest.raises(RuntimeError, match="32 bytes"):
        create_app(
            settings=Settings(
                database_path=tmp_path / "invalid.sqlite3",
                demo_mode=True,
                model_profiles_enabled=True,
                model_profile_master_key_file=invalid_key,
            )
        )


def test_profile_crud_encrypts_key_and_enforces_ownership_and_ssrf(tmp_path: Path) -> None:
    client = model_client(tmp_path)
    profile_id = create_profile(client)
    with database(client.app.state.settings.database_path) as conn:
        row = conn.execute("SELECT * FROM hub_model_profiles WHERE profile_id = ?", (profile_id,)).fetchone()
        assert row["encrypted_api_key"] != b"sk-test-secret"
        assert b"sk-test-secret" not in bytes(row["encrypted_api_key"])
        assert row["key_fingerprint"] == "d34c09fe275706e8"

    cross = client.get(f"/api/model-profiles/{profile_id}", headers={"X-Hub-User": "demo-b"})
    assert cross.status_code == 404

    patched = client.patch(
        f"/api/model-profiles/{profile_id}",
        json={"status": "disabled", "name": "disabled profile", "default_model_id": "gpt-5.6"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["status"] == "disabled"
    assert patched.json()["name"] == "disabled profile"
    assert patched.json()["default_model_id"] == "gpt-5.6"
    assert "sk-test-secret" not in patched.text
    assert "encrypted_api_key" not in patched.text

    unsafe = model_client(tmp_path / "unsafe", allow_local=False)
    rejected = unsafe.post(
        "/api/model-profiles",
        json={
            "label": "unsafe",
            "base_url": "http://127.0.0.1:18080/v1",
            "api_key": "sk-test-secret",
            "api_style": "responses",
        },
    )
    assert rejected.status_code == 400
    assert rejected.json()["detail"]["error"] == "model_provider_requires_https"


def test_provider_dns_safety_is_enforced_on_save_and_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = model_client(tmp_path, allow_local=False)
    monkeypatch.setattr(
        "hub.model_gateway.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    created = client.post(
        "/api/model-profiles",
        json={
            "name": "public provider",
            "provider": "custom",
            "base_url": "https://api.example/v1",
            "api_key": "sk-test-secret",
            "api_style": "responses",
            "status": "active",
        },
    )
    assert created.status_code == 201, created.text

    monkeypatch.setattr(
        "hub.model_gateway.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("127.0.0.1", 443))],
    )
    runtime_rejected = client.post(f"/api/model-profiles/{created.json()['profile_id']}/test")
    assert runtime_rejected.status_code == 400
    assert runtime_rejected.json()["detail"]["error"] == "model_provider_private_url_not_allowed"

    save_rejected = client.post(
        "/api/model-profiles",
        json={
            "name": "dns private",
            "provider": "custom",
            "base_url": "https://private.example/v1",
            "api_key": "sk-test-secret",
            "api_style": "responses",
        },
    )
    assert save_rejected.status_code == 400
    assert save_rejected.json()["detail"]["error"] == "model_provider_private_url_not_allowed"


def test_discover_binding_and_delete_guards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("hub.model_gateway.httpx.AsyncClient", FakeProviderClient)
    client = model_client(tmp_path)
    submit_and_approve(client, platform_manifest())
    profile_id = create_profile(client)

    tested = client.post(f"/api/model-profiles/{profile_id}/test")
    assert tested.status_code == 200, tested.text
    discover_and_bind(client, profile_id)

    delete_bound = client.delete(f"/api/model-profiles/{profile_id}")
    assert delete_bound.status_code == 409
    assert delete_bound.json()["detail"]["error"] == "model_profile_still_bound"

    legacy = platform_manifest(agent_id="legacy-agent")
    legacy.pop("model_runtime")
    legacy["capabilities"] = ["streaming", "full-workspace"]
    submit_and_approve(client, legacy)
    rejected = client.put(
        "/api/model-bindings/agents/legacy-agent",
        json={"profile_id": profile_id, "model_id": "gpt-5.6"},
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["error"] == "agent_model_gateway_not_supported"


def test_user_grant_responses_generate_and_replay_are_governed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("hub.model_gateway.httpx.AsyncClient", FakeProviderClient)
    client = model_client(tmp_path)
    submit_and_approve(client, platform_manifest(api_style="responses"))
    profile_id = create_profile(client, api_style="responses")
    discover_and_bind(client, profile_id)

    direct = client.post("/api/agents/hanhai-agent/model-grants", json={})
    assert direct.status_code == 404
    basic, delegation_token, _ = issue_workspace_delegation(client)
    grant = exchange_grant(client, basic=basic, token=delegation_token, request_id="request-generate-0001")
    generated = client.post(
        "/api/model-gateway/v1/generate",
        json={
            "instructions": "回答要简洁",
            "messages": [{"role": "user", "content": "你好"}],
            "stream": False,
        },
        headers={"Authorization": f"Bearer {grant}"},
    )
    assert generated.status_code == 200, generated.text
    assert generated.json()["output_text"] == "模型网关响应"
    assert generated.json()["usage"] == {
        "input_tokens": 3,
        "output_tokens": 4,
        "reasoning_tokens": 1,
        "cached_tokens": 2,
        "total_tokens": 7,
    }

    replay = client.post(
        "/api/model-gateway/v1/generate",
        json={"messages": [{"role": "user", "content": "重放"}]},
        headers={"Authorization": f"Bearer {grant}"},
    )
    assert replay.status_code == 401
    assert replay.json()["detail"]["error"] == "model_grant_replayed"


def test_featured_agent_can_exchange_workspace_token_for_model_grant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("hub.model_gateway.httpx.AsyncClient", FakeProviderClient)
    client = model_client(tmp_path)
    submit_and_approve(client, platform_manifest())
    profile_id = create_profile(client)
    discover_and_bind(client, profile_id)
    basic, token, access_token = issue_workspace_delegation(client)
    with database(client.app.state.settings.database_path) as conn:
        stored = conn.execute("SELECT token_hash FROM hub_model_delegations").fetchone()
        assert stored is not None
        assert stored["token_hash"] != token
    exchanged = client.post(
        "/api/model-gateway/grants/exchange",
        json={"model_delegation_token": token, "request_id": "workspace-request-1"},
        headers={"Authorization": basic},
    )
    assert exchanged.status_code == 200, exchanged.text
    body = exchanged.json()
    assert body["model_gateway_url"] == "/api/model-gateway/v1/generate"
    assert body["token_type"] == "Bearer"
    assert body["access_token"] == body["grant"]
    assert body["model_id"] == "gpt-5.6"
    assert body["model"]["id"] == "gpt-5.6"
    encoded = json.dumps(body)
    assert "sk-test-secret" not in encoded
    assert "encrypted_api_key" not in encoded

    workspace_access_token_rejected = client.post(
        "/api/model-gateway/grants/exchange",
        json={"model_delegation_token": access_token, "request_id": "connected-request-1"},
        headers={"Authorization": basic},
    )
    assert workspace_access_token_rejected.status_code == 401
    assert workspace_access_token_rejected.json()["detail"]["error"] == "model_delegation_invalid"

    forged = client.post(
        "/api/model-gateway/grants/exchange",
        json={
            "model_delegation_token": token,
            "request_id": "forged-request-1",
            "agent_id": "forged-agent",
            "user_id": "demo-a",
        },
        headers={"Authorization": basic},
    )
    assert forged.status_code == 422


def test_chat_completions_stream_is_normalized_to_model_sse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("hub.model_gateway.httpx.AsyncClient", FakeProviderClient)
    client = model_client(tmp_path)
    submit_and_approve(client, platform_manifest(api_style="chat_completions"))
    profile_id = create_profile(client, api_style="chat_completions")
    discover_and_bind(client, profile_id)
    basic, delegation_token, _ = issue_workspace_delegation(client)
    grant = exchange_grant(client, basic=basic, token=delegation_token, request_id="request-stream-0001")
    response = client.post(
        "/api/model-gateway/v1/generate",
        json={
            "messages": [{"role": "user", "content": "你好"}],
            "stream": True,
        },
        headers={"Authorization": f"Bearer {grant}"},
    )
    assert response.status_code == 200, response.text
    assert "event: model.started" in response.text
    assert "event: model.output_text.delta" in response.text
    assert '"delta":"你"' in response.text
    assert '"delta":"好"' in response.text
    assert "event: model.usage" in response.text
    assert "event: model.completed" in response.text


def test_featured_workspace_delegation_allows_distinct_requests_but_rejects_duplicate_request_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("hub.model_gateway.httpx.AsyncClient", FakeProviderClient)
    client = model_client(tmp_path)
    submit_and_approve(client, platform_manifest())
    profile_id = create_profile(client)
    discover_and_bind(client, profile_id)
    basic, delegation_token, _ = issue_workspace_delegation(client)

    first = client.post(
        "/api/model-gateway/grants/exchange",
        json={"model_delegation_token": delegation_token, "request_id": "workspace-request-a"},
        headers={"Authorization": basic},
    )
    second = client.post(
        "/api/model-gateway/grants/exchange",
        json={"model_delegation_token": delegation_token, "request_id": "workspace-request-b"},
        headers={"Authorization": basic},
    )
    replay = client.post(
        "/api/model-gateway/grants/exchange",
        json={"model_delegation_token": delegation_token, "request_id": "workspace-request-a"},
        headers={"Authorization": basic},
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert replay.status_code == 409
    assert replay.json()["detail"]["error"] == "model_request_replayed"


def test_featured_workspace_and_grant_expiration_are_persisted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("hub.model_gateway.httpx.AsyncClient", FakeProviderClient)
    client = model_client(tmp_path)
    submit_and_approve(client, platform_manifest())
    profile_id = create_profile(client)
    discover_and_bind(client, profile_id)

    basic, delegation_token, _ = issue_workspace_delegation(client)
    with database(tmp_path / "hub.sqlite3") as conn:
        row = conn.execute("SELECT scope_type FROM hub_model_delegations").fetchone()
        assert row["scope_type"] == "featured_workspace"
        conn.execute(
            "UPDATE hub_model_delegations SET expires_at = '2000-01-01T00:00:00Z'"
        )
    expired_delegation = client.post(
        "/api/model-gateway/grants/exchange",
        json={"model_delegation_token": delegation_token, "request_id": "expired-delegation"},
        headers={"Authorization": basic},
    )
    assert expired_delegation.status_code == 401
    assert expired_delegation.json()["detail"]["error"] == "model_delegation_expired"
    with database(tmp_path / "hub.sqlite3") as conn:
        row = conn.execute("SELECT status FROM hub_model_delegations").fetchone()
        assert row["status"] == "expired"

    basic, delegation_token, _ = issue_workspace_delegation(client)
    grant = exchange_grant(
        client,
        basic=basic,
        token=delegation_token,
        request_id="expired-grant-request",
    )
    with database(tmp_path / "hub.sqlite3") as conn:
        conn.execute(
            "UPDATE hub_model_gateway_grants SET expires_at = '2000-01-01T00:00:00Z'"
        )
    expired_grant = client.post(
        "/api/model-gateway/v1/generate",
        json={"messages": [{"role": "user", "content": "过期授权"}]},
        headers={"Authorization": f"Bearer {grant}"},
    )
    assert expired_grant.status_code == 401
    assert expired_grant.json()["detail"]["error"] == "model_grant_expired"
    with database(tmp_path / "hub.sqlite3") as conn:
        row = conn.execute("SELECT status FROM hub_model_gateway_grants").fetchone()
        assert row["status"] == "expired"


def test_connected_run_jwt_exchange_can_only_materialize_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("hub.model_gateway.httpx.AsyncClient", FakeProviderClient)
    client = model_client(tmp_path)
    submit_and_approve(client, platform_manifest())
    profile_id = create_profile(client)
    discover_and_bind(client, profile_id)
    basic, _, _ = issue_workspace_delegation(client)
    invocation_id = "inv-connected-exchange"
    with database(tmp_path / "hub.sqlite3") as conn:
        version_id = conn.execute(
            "SELECT active_version_id FROM hub_agents WHERE agent_id = 'hanhai-agent'"
        ).fetchone()["active_version_id"]
        conn.execute(
            """
            INSERT INTO hub_invocations (
              invocation_id, agent_id, version_id, user_id, run_id, status, started_at
            ) VALUES (?, 'hanhai-agent', ?, 'demo-c', 'run-connected', 'started', '2026-08-18T00:00:00Z')
            """,
            (invocation_id, version_id),
        )
    access_token = client.app.state.identity.sign_agent_token(
        agent_id="hanhai-agent",
        version_id=version_id,
        user_id="demo-c",
        display_name="普通用户",
        scopes=["chat:invoke"],
        request_id=invocation_id,
    )

    first = client.post(
        "/api/model-gateway/grants/exchange",
        json={"model_delegation_token": access_token, "request_id": "connected-request-a"},
        headers={"Authorization": basic},
    )
    second = client.post(
        "/api/model-gateway/grants/exchange",
        json={"model_delegation_token": access_token, "request_id": "connected-request-b"},
        headers={"Authorization": basic},
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 401
    assert second.json()["detail"]["error"] == "model_delegation_consumed"


def test_connected_run_end_revokes_issued_grant_and_rejects_late_exchange(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("hub.model_gateway.httpx.AsyncClient", FakeProviderClient)
    client = model_client(tmp_path)
    submit_and_approve(client, platform_manifest())
    profile_id = create_profile(client)
    discover_and_bind(client, profile_id)
    basic, _, _ = issue_workspace_delegation(client)

    with database(tmp_path / "hub.sqlite3") as conn:
        version_id = conn.execute(
            "SELECT active_version_id FROM hub_agents WHERE agent_id = 'hanhai-agent'"
        ).fetchone()["active_version_id"]
        for invocation_id in ("inv-active-run", "inv-ended-run"):
            conn.execute(
                """
                INSERT INTO hub_invocations (
                  invocation_id, agent_id, version_id, user_id, run_id, status, started_at
                ) VALUES (?, 'hanhai-agent', ?, 'demo-c', ?, 'started', '2026-08-18T00:00:00Z')
                """,
                (invocation_id, version_id, invocation_id.replace("inv-", "run-")),
            )

    active_token = client.app.state.identity.sign_agent_token(
        agent_id="hanhai-agent",
        version_id=version_id,
        user_id="demo-c",
        display_name="普通用户",
        scopes=["chat:invoke"],
        request_id="inv-active-run",
    )
    grant = exchange_grant(
        client,
        basic=basic,
        token=active_token,
        request_id="connected-grant-before-end",
    )
    _record_invocation_end(
        tmp_path / "hub.sqlite3",
        invocation_id="inv-active-run",
        status_value="finished",
        error_code=None,
        duration_ms=5,
    )
    after_end = client.post(
        "/api/model-gateway/v1/generate",
        json={"messages": [{"role": "user", "content": "run 已结束"}]},
        headers={"Authorization": f"Bearer {grant}"},
    )
    assert after_end.status_code == 401
    assert after_end.json()["detail"]["error"] == "model_grant_revoked"

    ended_token = client.app.state.identity.sign_agent_token(
        agent_id="hanhai-agent",
        version_id=version_id,
        user_id="demo-c",
        display_name="普通用户",
        scopes=["chat:invoke"],
        request_id="inv-ended-run",
    )
    _record_invocation_end(
        tmp_path / "hub.sqlite3",
        invocation_id="inv-ended-run",
        status_value="finished",
        error_code=None,
        duration_ms=5,
    )
    late_exchange = client.post(
        "/api/model-gateway/grants/exchange",
        json={"model_delegation_token": ended_token, "request_id": "late-connected-exchange"},
        headers={"Authorization": basic},
    )
    assert late_exchange.status_code == 401
    assert late_exchange.json()["detail"]["error"] == "model_delegation_invalid"


def test_revoke_disable_and_binding_change_invalidate_delegations_and_grants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("hub.model_gateway.httpx.AsyncClient", FakeProviderClient)
    client = model_client(tmp_path)
    submit_and_approve(client, platform_manifest())
    profile_id = create_profile(client)
    discover_and_bind(client, profile_id)

    basic, delegation_token, _ = issue_workspace_delegation(client)
    revoked = client.post(
        "/api/model-gateway/delegations/revoke",
        json={"token": delegation_token},
        headers={"Authorization": basic},
    )
    assert revoked.status_code == 200, revoked.text
    after_revoke = client.post(
        "/api/model-gateway/grants/exchange",
        json={"model_delegation_token": delegation_token, "request_id": "after-revoke-1"},
        headers={"Authorization": basic},
    )
    assert after_revoke.status_code == 401
    assert after_revoke.json()["detail"]["error"] == "model_delegation_revoked"

    basic2, delegation_token2, _ = issue_workspace_delegation(client)
    grant = exchange_grant(client, basic=basic2, token=delegation_token2, request_id="before-disable-1")
    disabled = client.patch(f"/api/model-profiles/{profile_id}", json={"status": "disabled"})
    assert disabled.status_code == 200, disabled.text
    generate_after_disable = client.post(
        "/api/model-gateway/v1/generate",
        json={"messages": [{"role": "user", "content": "禁用后"}]},
        headers={"Authorization": f"Bearer {grant}"},
    )
    assert generate_after_disable.status_code == 401
    assert generate_after_disable.json()["detail"]["error"] == "model_grant_revoked"

    enabled = client.patch(f"/api/model-profiles/{profile_id}", json={"status": "active"})
    assert enabled.status_code == 200, enabled.text
    basic3, delegation_token3, _ = issue_workspace_delegation(client)
    grant2 = exchange_grant(client, basic=basic3, token=delegation_token3, request_id="before-rebind-1")
    rebound = client.put(
        "/api/model-bindings/agents/hanhai-agent",
        json={"profile_id": profile_id, "model_id": "gpt-5.6"},
    )
    assert rebound.status_code == 200, rebound.text
    generate_after_rebind = client.post(
        "/api/model-gateway/v1/generate",
        json={"messages": [{"role": "user", "content": "换绑后"}]},
        headers={"Authorization": f"Bearer {grant2}"},
    )
    assert generate_after_rebind.status_code == 401
    assert generate_after_rebind.json()["detail"]["error"] == "model_grant_revoked"

    basic4, delegation_token4, _ = issue_workspace_delegation(client)
    grant3 = exchange_grant(
        client,
        basic=basic4,
        token=delegation_token4,
        request_id="before-key-rotation-1",
    )
    rotated = client.patch(
        f"/api/model-profiles/{profile_id}",
        json={"api_key": "sk-rotated-test-secret"},
    )
    assert rotated.status_code == 200, rotated.text
    generate_after_key_rotation = client.post(
        "/api/model-gateway/v1/generate",
        json={"messages": [{"role": "user", "content": "密钥轮换后"}]},
        headers={"Authorization": f"Bearer {grant3}"},
    )
    assert generate_after_key_rotation.status_code == 401
    assert generate_after_key_rotation.json()["detail"]["error"] == "model_grant_revoked"


def test_grant_rechecks_that_bound_model_is_still_chat_eligible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("hub.model_gateway.httpx.AsyncClient", FakeProviderClient)
    client = model_client(tmp_path)
    submit_and_approve(client, platform_manifest())
    profile_id = create_profile(client)
    discover_and_bind(client, profile_id)
    basic, delegation_token, _ = issue_workspace_delegation(client)

    with database(tmp_path / "hub.sqlite3") as conn:
        conn.execute(
            """
            UPDATE hub_model_profile_models
            SET chat_eligible = 0
            WHERE profile_id = ? AND model_id = 'gpt-5.6'
            """,
            (profile_id,),
        )

    rejected = client.post(
        "/api/model-gateway/grants/exchange",
        json={"model_delegation_token": delegation_token, "request_id": "stale-model-binding"},
        headers={"Authorization": basic},
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["error"] == "model_not_allowed"


def test_profile_key_rotation_reads_previous_key_and_writes_current_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("hub.model_gateway.httpx.AsyncClient", FakeProviderClient)
    old_key = tmp_path / "old.key"
    new_key = tmp_path / "new.key"
    old_key.write_text("a" * 32, encoding="utf-8")
    new_key.write_text("b" * 32, encoding="utf-8")
    db_path = tmp_path / "hub.sqlite3"
    first_client = TestClient(
        create_app(
            settings=Settings(
                database_path=db_path,
                demo_mode=True,
                model_profiles_enabled=True,
                model_profile_master_key_file=old_key,
                model_profile_master_key_version=1,
                allow_local_model_providers=True,
                model_provider_origin_allowlist=("http://127.0.0.1:18080",),
            )
        )
    )
    profile_id = create_profile(first_client)
    with database(db_path) as conn:
        first_row = conn.execute("SELECT encrypted_api_key, key_version FROM hub_model_profiles").fetchone()
        first_envelope = json.loads(bytes(first_row["encrypted_api_key"]).decode("utf-8"))
        assert first_row["key_version"] == 1
        assert first_envelope["format_version"] == 1
        assert first_envelope["key_version"] == 1
        assert first_envelope["nonce"]
        assert first_envelope["ciphertext"]

    rotated_client = TestClient(
        create_app(
            settings=Settings(
                database_path=db_path,
                demo_mode=True,
                model_profiles_enabled=True,
                model_profile_master_key_file=new_key,
                model_profile_master_key_version=2,
                model_profile_previous_key_files=(f"1={old_key}",),
                allow_local_model_providers=True,
                model_provider_origin_allowlist=("http://127.0.0.1:18080",),
            )
        )
    )
    tested = rotated_client.post(f"/api/model-profiles/{profile_id}/test")
    assert tested.status_code == 200, tested.text
    patched = rotated_client.patch(
        f"/api/model-profiles/{profile_id}",
        json={"api_key": "sk-test-secret"},
    )
    assert patched.status_code == 200, patched.text
    with database(db_path) as conn:
        second_row = conn.execute("SELECT encrypted_api_key, key_version FROM hub_model_profiles").fetchone()
        second_envelope = json.loads(bytes(second_row["encrypted_api_key"]).decode("utf-8"))
        assert second_row["key_version"] == 2
        assert second_envelope["key_version"] == 2
