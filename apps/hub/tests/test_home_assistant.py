from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from hub.config import Settings
from hub.db import database
from hub.main import create_app


def settings_for_home_assistant(tmp_path: Path) -> Settings:
    tmp_path.mkdir(parents=True, exist_ok=True)
    key_file = tmp_path / "master.key"
    key_file.write_text("x" * 32, encoding="utf-8")
    return Settings(
        database_path=tmp_path / "hub.sqlite3",
        demo_mode=True,
        automatic_checks_enabled=False,
        require_passing_checks=False,
        internal_url_allowlist=("http://127.0.0.1:9101",),
        model_profiles_enabled=True,
        model_profile_master_key_file=key_file,
        allow_local_model_providers=True,
        model_provider_origin_allowlist=("http://127.0.0.1:18080",),
    )


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(settings=settings_for_home_assistant(tmp_path)))


def agent_manifest(
    *,
    agent_id: str = "hanhai-course-agent",
    name: str = "瀚海行 Agent",
    description: str = "课程资料整理与期末复习助手",
    version: str = "1.0.0",
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "id": agent_id,
        "name": name,
        "description": description,
        "version": version,
        "owner": "AI for better life In ustc",
        "category": "学习助手",
        "tags": ["demo"],
        "integration": {
            "mode": "link",
            "launch_url": "http://127.0.0.1:9101/app",
        },
        "capabilities": ["streaming"],
        "data_policy": {
            "receives_user_identity": False,
            "receives_files": False,
            "stores_conversation": False,
        },
    }


def submit_and_approve(
    client: TestClient,
    *,
    agent_id: str = "hanhai-course-agent",
    name: str = "瀚海行 Agent",
    description: str = "课程资料整理与期末复习助手",
    featured: bool = False,
) -> str:
    submitted = client.post(
        "/api/registry/agents",
        json={
            "manifest": agent_manifest(agent_id=agent_id, name=name, description=description),
            "trust_level": "first_party_internal",
        },
        headers={"X-Hub-User": "demo-a"},
    )
    assert submitted.status_code == 201, submitted.text
    version_id = submitted.json()["versions"][0]["version_id"]
    approved = client.post(
        f"/api/admin/agents/{agent_id}/versions/{version_id}/review",
        json={"decision": "approved", "notes": "home assistant test", "featured": featured},
        headers={"X-Hub-User": "demo-a"},
    )
    assert approved.status_code == 200, approved.text
    return version_id


def configure_global_model(client: TestClient, tmp_path: Path) -> str:
    created = client.post(
        "/api/model-profiles",
        json={
            "name": "home profile",
            "provider": "openai-compatible",
            "base_url": "http://127.0.0.1:18080/v1",
            "api_key": "sk-home-secret",
            "api_style": "responses",
            "status": "active",
        },
    )
    assert created.status_code == 201, created.text
    profile_id = created.json()["profile_id"]
    with database(tmp_path / "hub.sqlite3") as conn:
        conn.execute(
            """
            INSERT INTO hub_model_profile_models (
              profile_id, model_id, display_name, api_style, chat_eligible, discovered_at, metadata_json
            ) VALUES (?, 'gpt-5.6-sol', 'GPT 5.6 Sol', 'responses', 1, '2026-08-20T00:00:00Z', '{}')
            """,
            (profile_id,),
        )
    bound = client.put(
        "/api/model-bindings/global",
        json={"profile_id": profile_id, "model_id": "gpt-5.6-sol"},
    )
    assert bound.status_code == 200, bound.text
    return profile_id


class FakeResponse:
    def __init__(self, payload: dict[str, Any], *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeStreamResponse:
    status_code = 200

    async def aiter_lines(self):
        yield 'data: {"type":"response.output_text.delta","delta":"你好"}'
        yield 'data: {"type":"response.output_text.delta","delta":"，这里是 Hub 助手。"}'
        yield 'data: {"type":"response.completed","response":{"output_text":"你好，这里是 Hub 助手。","usage":{"input_tokens":5,"output_tokens":4,"total_tokens":9}}}'
        yield "data: [DONE]"


class FakeStreamContext:
    async def __aenter__(self) -> FakeStreamResponse:
        return FakeStreamResponse()

    async def __aexit__(self, *args: Any) -> None:
        return None


class FakeProviderClient:
    next_text = '{"recommend":true,"agent_id":"hanhai-course-agent","reason":"它能处理课程资料和期末复习问题。"}'
    last_payload: dict[str, Any] | None = None
    calls: list[dict[str, Any]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> "FakeProviderClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        assert url == "http://127.0.0.1:18080/v1/responses"
        assert kwargs["headers"]["authorization"] == "Bearer sk-home-secret"
        payload = kwargs["json"]
        assert payload["model"] == "gpt-5.6-sol"
        assert "profile_id" not in payload
        FakeProviderClient.last_payload = payload
        FakeProviderClient.calls.append(payload)
        return FakeResponse(
            {
                "output_text": FakeProviderClient.next_text,
                "usage": {"input_tokens": 10, "output_tokens": 8, "total_tokens": 18},
            }
        )

    def stream(self, method: str, url: str, **kwargs: Any) -> FakeStreamContext:
        assert method == "POST"
        assert url == "http://127.0.0.1:18080/v1/responses"
        assert kwargs["headers"]["authorization"] == "Bearer sk-home-secret"
        payload = kwargs["json"]
        assert payload["model"] == "gpt-5.6-sol"
        FakeProviderClient.last_payload = payload
        FakeProviderClient.calls.append(payload)
        return FakeStreamContext()


def test_instant_home_assistant_streams_with_short_platform_prompt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("hub.model_gateway.httpx.AsyncClient", FakeProviderClient)
    FakeProviderClient.calls = []
    client = make_client(tmp_path)
    configure_global_model(client, tmp_path)

    response = client.post(
        "/api/home-assistant/chat",
        json={
            "mode": "instant",
            "messages": [{"role": "user", "content": "你好"}],
        },
    )

    assert response.status_code == 200, response.text
    assert "text/event-stream" in response.headers["content-type"]
    assert "event: model.started" in response.text
    assert "event: model.output_text.delta" in response.text
    assert "event: model.completed" in response.text
    payload = FakeProviderClient.last_payload
    assert payload is not None
    assert len(payload["instructions"]) <= 40
    assert "Agent Hub" in payload["instructions"]
    assert "AGENT_CATALOG" not in payload["instructions"]


def test_route_recommends_only_active_catalog_agent_without_urls_or_secrets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("hub.model_gateway.httpx.AsyncClient", FakeProviderClient)
    FakeProviderClient.next_text = '{"recommend":true,"agent_id":"hanhai-course-agent","reason":"它能读取课程知识库并辅助复习。"}'
    client = make_client(tmp_path)
    configure_global_model(client, tmp_path)
    submit_and_approve(client)

    response = client.post(
        "/api/home-assistant/chat",
        json={
            "mode": "route",
            "messages": [{"role": "user", "content": "我想复习数学分析并让 AI 讲解试卷。"}],
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "route"
    assert body["model"] == "gpt-5.6-sol"
    assert body["recommendation"] == {
        "agent_id": "hanhai-course-agent",
        "name": "瀚海行 Agent",
        "description": "课程资料整理与期末复习助手",
        "reason": "它能读取课程知识库并辅助复习。",
    }
    encoded = json.dumps(body, ensure_ascii=False)
    assert "http://" not in encoded
    assert "sk-home-secret" not in encoded
    assert "127.0.0.1" not in encoded


def test_route_rejects_forged_or_inactive_model_agent_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("hub.model_gateway.httpx.AsyncClient", FakeProviderClient)
    client = make_client(tmp_path)
    configure_global_model(client, tmp_path)
    submit_and_approve(client)

    FakeProviderClient.next_text = '{"recommend":true,"agent_id":"forged-agent","reason":"伪造推荐"}'
    forged = client.post(
        "/api/home-assistant/chat",
        json={"mode": "route", "messages": [{"role": "user", "content": "帮我找校园服务"}]},
    )
    assert forged.status_code == 200, forged.text
    assert forged.json()["recommendation"] is None

    FakeProviderClient.next_text = '{"recommend":true,"agent_id":"campus-helper-demo","reason":"下线推荐"}'
    inactive = client.post(
        "/api/home-assistant/chat",
        json={"mode": "route", "messages": [{"role": "user", "content": "图书馆在哪里"}]},
    )
    assert inactive.status_code == 200, inactive.text
    assert inactive.json()["recommendation"] is None


def test_route_filters_catalog_to_runtime_active_agents(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("hub.model_gateway.httpx.AsyncClient", FakeProviderClient)
    FakeProviderClient.calls = []
    FakeProviderClient.next_text = '{"recommend":false,"agent_id":null,"reason":"无匹配"}'
    client = make_client(tmp_path)
    configure_global_model(client, tmp_path)
    submit_and_approve(client, agent_id="campus-helper-demo", name="校园助手 Demo", description="校园生活服务咨询")

    response = client.post(
        "/api/home-assistant/chat",
        json={"mode": "route", "messages": [{"role": "user", "content": "我该去哪个教学楼？"}]},
    )

    assert response.status_code == 200, response.text
    payload = FakeProviderClient.last_payload
    assert payload is not None
    instructions = payload["instructions"]
    assert "campus-helper-demo" in instructions
    assert "hanhai-course-agent" not in instructions


def test_home_assistant_missing_model_binding_is_safe(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    submit_and_approve(client)

    instant = client.post(
        "/api/home-assistant/chat",
        json={"mode": "instant", "messages": [{"role": "user", "content": "你好"}]},
    )
    assert instant.status_code == 200, instant.text
    assert "event: model.error" in instant.text
    assert "model_binding_not_found" in instant.text

    route = client.post(
        "/api/home-assistant/chat",
        json={"mode": "route", "messages": [{"role": "user", "content": "我要复习"}]},
    )
    assert route.status_code == 404
    assert route.json()["detail"]["error"] == "model_binding_not_found"
