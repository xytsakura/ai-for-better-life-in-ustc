from __future__ import annotations

import socket
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from course_agent.config import Settings
from course_agent.llm import FakeLLMAdapter
from course_agent.main import create_app
from course_agent.model_catalog import (
    ModelCatalog,
    ModelCatalogError,
    invalidate_model_catalog,
)


class _DiscoverClient:
    urls: list[str] = []

    def __init__(self, **_: object):
        pass

    def __enter__(self) -> "_DiscoverClient":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def get(self, url: str, **_: object) -> httpx.Response:
        type(self).urls.append(url)
        if url.endswith("/models") and not url.endswith("/v1/models"):
            return httpx.Response(200, text="<html>not a model list</html>")
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "gpt-5.6-luna"},
                    {"id": "gpt-5.6-sol"},
                    {"id": "gpt-5.6-sol"},
                    {"id": "gpt-5.6-terra"},
                    {"id": "voice-audio-model"},
                    {"id": "unknown-specialized-model"},
                ]
            },
        )


def _public_dns(*_: object) -> list[tuple]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


def test_model_discovery_falls_back_from_html_models_to_v1_and_classifies(monkeypatch, tmp_path: Path):
    invalidate_model_catalog()
    _DiscoverClient.urls = []
    monkeypatch.setattr("course_agent.model_catalog.socket.getaddrinfo", _public_dns)
    monkeypatch.setattr("course_agent.model_catalog.httpx.Client", _DiscoverClient)
    settings = Settings(
        runtime_dir=tmp_path,
        llm_api_key="secret-key",
        llm_base_url="https://models.example.com/api",
        llm_model="gpt-5.6-sol",
    )

    result = ModelCatalog(settings).discover(force=True)

    assert _DiscoverClient.urls == [
        "https://models.example.com/api/models",
        "https://models.example.com/v1/models",
    ]
    assert result.discovery_source == "/v1/models"
    assert [model.id for model in result.models] == [
        "gpt-5.6-luna",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "voice-audio-model",
        "unknown-specialized-model",
    ]
    eligible = {model.id: model for model in result.models}
    assert eligible["gpt-5.6-sol"].chat_eligible is True
    assert eligible["gpt-5.6-sol"].supported_reasoning_efforts == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]
    assert eligible["gpt-5.6-sol"].context_window_tokens == 272000
    assert eligible["voice-audio-model"].chat_eligible is False
    assert eligible["voice-audio-model"].disabled_reason == "audio_model_not_supported"
    assert eligible["unknown-specialized-model"].chat_eligible is True
    assert eligible["unknown-specialized-model"].disabled_reason is None


def test_model_discovery_cache_is_bound_to_generation(monkeypatch, tmp_path: Path):
    invalidate_model_catalog()
    _DiscoverClient.urls = []
    monkeypatch.setattr("course_agent.model_catalog.socket.getaddrinfo", _public_dns)
    monkeypatch.setattr("course_agent.model_catalog.httpx.Client", _DiscoverClient)
    settings = Settings(
        runtime_dir=tmp_path,
        llm_api_key="secret-key",
        llm_base_url="https://models.example.com",
        llm_model="gpt-5.6-sol",
    )
    catalog = ModelCatalog(settings)

    first = catalog.discover()
    second = catalog.discover()
    settings.llm_config_generation += 1
    third = catalog.discover()

    assert first.cached is False
    assert second.cached is True
    assert third.cached is False
    assert _DiscoverClient.urls.count("https://models.example.com/v1/models") == 2


def test_discovery_rejects_unapproved_local_and_private_targets(tmp_path: Path):
    invalidate_model_catalog()
    local = Settings(
        runtime_dir=tmp_path,
        llm_api_key="secret-key",
        llm_base_url="http://127.0.0.1:11434",
        llm_allow_local_base_urls=False,
    )
    private = Settings(
        runtime_dir=tmp_path,
        llm_api_key="secret-key",
        llm_base_url="https://private.example.com",
    )

    with pytest.raises(ModelCatalogError) as local_error:
        ModelCatalog(local).discover(force=True)
    assert local_error.value.code == "unsafe_llm_base_url"

    def private_dns(*_: object) -> list[tuple]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("course_agent.model_catalog.socket.getaddrinfo", private_dns)
        with pytest.raises(ModelCatalogError) as private_error:
            ModelCatalog(private).discover(force=True)
    assert private_error.value.code == "unsafe_llm_base_url"


def test_models_endpoint_requires_admin_and_query_validates_catalog_model(monkeypatch, tmp_path: Path):
    invalidate_model_catalog()
    _DiscoverClient.urls = []
    monkeypatch.setattr("course_agent.model_catalog.socket.getaddrinfo", _public_dns)
    monkeypatch.setattr("course_agent.model_catalog.httpx.Client", _DiscoverClient)
    settings = Settings(
        runtime_dir=tmp_path,
        session_secret="test-secret",
        llm_api_key="secret-key",
        llm_base_url="https://models.example.com",
        llm_model="gpt-5.6-sol",
    )
    adapter = FakeLLMAdapter(settings)
    app = create_app(settings, adapter)
    client = TestClient(app)

    client.post("/api/session", json={"user_id": "demo-b"})
    forbidden = client.post("/api/models/discover")
    assert forbidden.status_code == 403

    client.post("/api/session", json={"user_id": "demo-a"})
    discovered = client.post("/api/models/discover")
    assert discovered.status_code == 200
    assert discovered.json()["discovery_source"] == "/v1/models"

    response = client.post(
        "/api/query",
        json={
            "question": "测试模型选择",
            "model": "gpt-5.6-terra",
            "reasoning_effort": "high",
        },
    )
    assert response.status_code == 200
    assert response.json()["model"] == "gpt-5.6-terra"
    assert response.json()["usage"] is None
    assert adapter.last_direct_model == "gpt-5.6-terra"
    assert adapter.last_direct_reasoning_effort == "high"

    compatible_model = client.post(
        "/api/query",
        json={"question": "测试模型选择", "model": "unknown-specialized-model"},
    )
    assert compatible_model.status_code == 200
    assert compatible_model.json()["model"] == "unknown-specialized-model"


def test_models_endpoint_auto_discovers_catalog_for_standalone_users(monkeypatch, tmp_path: Path):
    invalidate_model_catalog()
    _DiscoverClient.urls = []
    monkeypatch.setattr("course_agent.model_catalog.socket.getaddrinfo", _public_dns)
    monkeypatch.setattr("course_agent.model_catalog.httpx.Client", _DiscoverClient)
    settings = Settings(
        runtime_dir=tmp_path,
        session_secret="test-secret",
        llm_api_key="secret-key",
        llm_base_url="https://models.example.com",
        llm_model="gpt-5.6-sol",
    )
    client = TestClient(create_app(settings, FakeLLMAdapter(settings)))
    client.post("/api/session", json={"user_id": "demo-b"})

    response = client.get("/api/models")

    assert response.status_code == 200
    assert response.json()["discovery_source"] == "/v1/models"
    assert [model["id"] for model in response.json()["models"][:3]] == [
        "gpt-5.6-luna",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
    ]


def test_models_endpoint_falls_back_to_default_when_auto_discovery_fails(monkeypatch, tmp_path: Path):
    invalidate_model_catalog()
    monkeypatch.setattr(
        "course_agent.model_catalog.ModelCatalog.discover",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ModelCatalogError("model_discovery_failed", "未能发现可用模型", True)
        ),
    )
    settings = Settings(
        runtime_dir=tmp_path,
        session_secret="test-secret",
        llm_api_key="secret-key",
        llm_base_url="https://models.example.com",
        llm_model="gpt-5.6-sol",
    )
    client = TestClient(create_app(settings, FakeLLMAdapter(settings)))
    client.post("/api/session", json={"user_id": "demo-b"})

    response = client.get("/api/models")

    assert response.status_code == 200
    assert response.json()["discovery_source"] is None
    assert [model["id"] for model in response.json()["models"]] == ["gpt-5.6-sol"]


def test_query_rejects_unsupported_reasoning_for_default_model(tmp_path: Path):
    settings = Settings(
        runtime_dir=tmp_path,
        session_secret="test-secret",
        llm_model="test-model-without-reasoning",
    )
    client = TestClient(create_app(settings, FakeLLMAdapter(settings)))
    client.post("/api/session", json={"user_id": "demo-a"})

    response = client.post(
        "/api/query",
        json={"question": "测试思考强度", "reasoning_effort": "low"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "reasoning_effort_not_supported"


def test_settings_responses_expose_only_current_user_is_admin(tmp_path: Path):
    settings = Settings(
        runtime_dir=tmp_path,
        session_secret="test-secret",
        _env_file=tmp_path / "course-agent.env",
    )
    client = TestClient(create_app(settings, FakeLLMAdapter(settings)))

    client.post("/api/session", json={"user_id": "demo-b"})
    reader_settings = client.get("/api/settings")
    assert reader_settings.status_code == 200
    reader_body = reader_settings.json()
    assert reader_body["is_admin"] is False
    assert "admin_user_ids" not in reader_body

    forbidden_update = client.post("/api/settings", json={"llm_model": "gpt-5.6-sol"})
    assert forbidden_update.status_code == 403

    client.post("/api/session", json={"user_id": "demo-a"})
    admin_settings = client.get("/api/settings")
    assert admin_settings.status_code == 200
    admin_body = admin_settings.json()
    assert admin_body["is_admin"] is True
    assert "admin_user_ids" not in admin_body

    updated = client.post(
        "/api/settings",
        json={
            "llm_model": "gpt-5.6-terra",
            "llm_api_style": "chat_completions",
        },
    )
    assert updated.status_code == 200
    updated_body = updated.json()
    assert updated_body["is_admin"] is True
    assert "admin_user_ids" not in updated_body
    assert updated_body["llm_model"] == "gpt-5.6-terra"
    assert updated_body["llm_api_style"] == "chat_completions"
    assert "COURSE_AGENT_LLM_API_STYLE=chat_completions" in (
        tmp_path / "course-agent.env"
    ).read_text(encoding="utf-8")

    invalid_style = client.post("/api/settings", json={"llm_api_style": "auto"})
    assert invalid_style.status_code == 422
