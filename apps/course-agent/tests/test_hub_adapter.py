from __future__ import annotations

import asyncio
import base64
import json
import time
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from course_agent.config import Settings, _secret_from_env_or_file
from course_agent.hub import HubJwtVerifier, map_hub_subject_to_demo_user
from course_agent.llm import FakeLLMAdapter
from course_agent.main import create_app


def make_client(
    tmp_path: Path,
    *,
    jwks_json: str = "",
    auth_required: bool = False,
    token_endpoint: str = "",
    client_secret: str = "",
    user_map: dict[str, str] | None = None,
) -> tuple[TestClient, FakeLLMAdapter]:
    settings = Settings(
        runtime_dir=tmp_path,
        session_secret="test-secret",
        hub_jwks_json=jwks_json,
        hub_auth_required=auth_required,
        hub_token_endpoint=token_endpoint,
        hub_client_secret=client_secret,
        hub_user_mapping=user_map or {},
    )
    adapter = FakeLLMAdapter(settings, answer="Hub adapter answer")
    adapter.stream_chunks = ["Hub ", "adapter ", "answer"]
    app = create_app(settings, adapter)
    return TestClient(app), adapter


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def test_hub_client_secret_can_be_read_from_runtime_file(tmp_path: Path, monkeypatch) -> None:
    secret_path = tmp_path / "course-agent.secret"
    secret_path.write_text("runtime-only-secret\n", encoding="utf-8")
    monkeypatch.delenv("TEST_HUB_SECRET", raising=False)
    monkeypatch.setenv("TEST_HUB_SECRET_FILE", str(secret_path))

    assert _secret_from_env_or_file("TEST_HUB_SECRET", "TEST_HUB_SECRET_FILE") == "runtime-only-secret"

    monkeypatch.setenv("TEST_HUB_SECRET", "direct-secret")
    assert _secret_from_env_or_file("TEST_HUB_SECRET", "TEST_HUB_SECRET_FILE") == "direct-secret"


def jwks_for(private_key: Ed25519PrivateKey, kid: str = "test-kid") -> str:
    public_key = private_key.public_key()
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return json.dumps(
        {
            "keys": [
                {
                    "kty": "OKP",
                    "crv": "Ed25519",
                    "kid": kid,
                    "use": "sig",
                    "alg": "EdDSA",
                    "x": b64url(raw),
                }
            ]
        }
    )


def sign_hub_token(
    private_key: Ed25519PrivateKey,
    *,
    sub: str = "hub-user-1",
    aud: str = "hanhai-course-agent",
    scope: str = "chat:invoke",
    kid: str = "test-kid",
    request_id: str = "req-test-1",
) -> str:
    now = int(time.time())
    header = {"alg": "EdDSA", "typ": "JWT", "kid": kid}
    payload = {
        "iss": "campus-agent-hub",
        "sub": sub,
        "aud": aud,
        "iat": now,
        "exp": now + 120,
        "jti": f"jti-{now}",
        "name": "Hub User",
        "scope": scope,
        "request_id": request_id,
    }
    signing_input = (
        b64url(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        + "."
        + b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    )
    signature = private_key.sign(signing_input.encode("ascii"))
    return signing_input + "." + b64url(signature)


def test_hub_jwks_refreshes_once_when_same_kid_rotates(tmp_path: Path, monkeypatch) -> None:
    old_key = Ed25519PrivateKey.generate()
    current_key = Ed25519PrivateKey.generate()
    verifier = HubJwtVerifier(Settings(runtime_dir=tmp_path, hub_jwks_url="http://hub.test/jwks"))
    jwks_versions = [json.loads(jwks_for(old_key)), json.loads(jwks_for(current_key))]
    load_count = 0

    async def load_rotating_jwks() -> dict:
        nonlocal load_count
        if verifier._jwks_cache is not None:
            return verifier._jwks_cache
        verifier._jwks_cache = jwks_versions[min(load_count, len(jwks_versions) - 1)]
        load_count += 1
        return verifier._jwks_cache

    monkeypatch.setattr(verifier, "_load_jwks", load_rotating_jwks)
    token = sign_hub_token(current_key, sub="rotated-user")

    identity = asyncio.run(verifier.verify_token(token, required_scope="chat:invoke"))

    assert identity.hub_sub == "rotated-user"
    assert load_count == 2


def parse_agui_events(text: str) -> list[dict]:
    events: list[dict] = []
    for block in text.replace("\r\n", "\n").strip().split("\n\n"):
        data_lines = [
            line.split(":", 1)[1].strip()
            for line in block.splitlines()
            if line.startswith("data:")
        ]
        if data_lines:
            events.append(json.loads("\n".join(data_lines)))
    return events


def test_health_declares_hub_contract_and_capabilities(tmp_path: Path):
    client, _adapter = make_client(tmp_path)

    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["contract_version"] == "1.0"
    assert body["hub"]["agent_id"] == "hanhai-course-agent"
    assert body["hub"]["protocol"] == "ag-ui"
    assert body["hub"]["chat_path"] == "/api/hub/chat"
    assert body["hub"]["workspace_callback_path"] == "/api/hub/callback"
    assert {"streaming", "citations", "knowledge-base", "full-workspace"}.issubset(
        set(body["capabilities"])
    )


def test_hub_chat_requires_bearer_token_when_configured(tmp_path: Path):
    private_key = Ed25519PrivateKey.generate()
    client, _adapter = make_client(tmp_path, jwks_json=jwks_for(private_key), auth_required=True)

    response = client.post(
        "/api/hub/chat",
        json={
            "threadId": "thread-1",
            "runId": "run-1",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "missing_hub_token"


def test_hub_chat_verifies_eddsa_jwt_and_streams_agui_events(tmp_path: Path):
    private_key = Ed25519PrivateKey.generate()
    token = sign_hub_token(private_key, sub="hub-student", scope="chat:invoke")
    client, adapter = make_client(
        tmp_path,
        jwks_json=jwks_for(private_key),
        auth_required=True,
        user_map={"hub-student": "demo-b"},
    )

    response = client.post(
        "/api/hub/chat",
        headers={"Authorization": f"Bearer {token}", "x-hub-request-id": "req-test-1"},
        json={
            "threadId": "thread-1",
            "runId": "run-1",
            "state": {},
            "messages": [
                {"role": "user", "content": "请介绍瀚海行"},
            ],
            "tools": [],
            "context": {},
            "forwardedProps": {"course_agent": {"mode": "direct"}},
        },
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_agui_events(response.text)
    assert [event["type"] for event in events] == [
        "RUN_STARTED",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "RUN_FINISHED",
    ]
    assert "".join(event.get("delta", "") for event in events) == "Hub adapter answer"
    assert events[-1]["result"]["answer"] == "Hub adapter answer"
    assert adapter.last_direct_question == "请介绍瀚海行"


def test_hub_chat_rejects_wrong_audience(tmp_path: Path):
    private_key = Ed25519PrivateKey.generate()
    token = sign_hub_token(private_key, aud="other-agent", scope="chat:invoke")
    client, _adapter = make_client(tmp_path, jwks_json=jwks_for(private_key), auth_required=True)

    response = client.post(
        "/api/hub/chat",
        headers={"Authorization": f"Bearer {token}", "x-hub-request-id": "req-test-1"},
        json={
            "threadId": "thread-1",
            "runId": "run-1",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_hub_audience"


def test_hub_chat_requires_request_id_with_authenticated_gateway_call(tmp_path: Path):
    private_key = Ed25519PrivateKey.generate()
    token = sign_hub_token(private_key, scope="chat:invoke")
    client, _adapter = make_client(tmp_path, jwks_json=jwks_for(private_key), auth_required=True)

    response = client.post(
        "/api/hub/chat",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "threadId": "thread-1",
            "runId": "run-1",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "missing_hub_request_id"


def test_hub_subject_mapping_uses_config_exact_demo_and_deterministic_fallback(tmp_path: Path):
    settings = Settings(
        runtime_dir=tmp_path,
        session_secret="test-secret",
        hub_user_mapping={"external-user": "demo-b"},
    )

    assert map_hub_subject_to_demo_user("external-user", settings) == "demo-b"
    assert map_hub_subject_to_demo_user("demo-a", settings) == "demo-a"
    assert map_hub_subject_to_demo_user("ustc-demo-c", settings) == "demo-c"
    assert map_hub_subject_to_demo_user("unknown-user", settings) in {"demo-a", "demo-b", "demo-c"}


def test_workspace_exchange_consumes_hub_code_and_creates_course_session(
    monkeypatch,
    tmp_path: Path,
):
    private_key = Ed25519PrivateKey.generate()
    workspace_token = sign_hub_token(private_key, sub="workspace-user", scope="workspace:enter")
    client, _adapter = make_client(
        tmp_path,
        jwks_json=jwks_for(private_key),
        token_endpoint="http://hub.example.test/api/hub/oauth/token",
        client_secret="secret",
        user_map={"workspace-user": "demo-c"},
    )

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, headers=None, data=None):
            assert url == "http://hub.example.test/api/hub/oauth/token"
            assert headers["Authorization"].startswith("Basic ")
            assert data["grant_type"] == "authorization_code"
            assert data["code"] == "x" * 32
            assert data["state"] == "state-1234567890"
            return httpx.Response(200, json={"access_token": workspace_token, "scope": "workspace"})

    monkeypatch.setattr("course_agent.hub.httpx.AsyncClient", FakeAsyncClient)

    response = client.post(
        "/api/hub/workspace/exchange",
        json={"code": "x" * 32, "state": "state-1234567890"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["hub"]["mapped_user_id"] == "demo-c"
    assert client.get("/api/session").json()["user"]["id"] == "demo-c"
