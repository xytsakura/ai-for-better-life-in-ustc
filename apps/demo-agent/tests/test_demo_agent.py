import base64
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import HTTPException
from fastapi.testclient import TestClient

from demo_agent.main import HubTokenVerifier, answer_for, app, token_verifier


client = TestClient(app)


def setup_function() -> None:
    token_verifier.cache_clear()


def _encoded_token(*, scopes: list[str], ttl_seconds: int = 120, audience: str | None = None) -> tuple[HubTokenVerifier, str]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    encoded_public_key = base64.urlsafe_b64encode(public_key).rstrip(b"=").decode("ascii")
    verifier = HubTokenVerifier()
    verifier.required = True
    verifier._jwks = {
        "keys": [
            {
                "kty": "OKP",
                "crv": "Ed25519",
                "x": encoded_public_key,
                "use": "sig",
                "alg": "EdDSA",
                "kid": "test-key",
            }
        ]
    }
    verifier._jwks_loaded_at = time.monotonic()
    issued_at = int(time.time())
    token = jwt.encode(
        {
            "iss": verifier.issuer,
            "aud": audience or verifier.audience,
            "sub": "demo-c",
            "scope": scopes,
            "iat": issued_at,
            "exp": issued_at + ttl_seconds,
        },
        private_key,
        algorithm="EdDSA",
        headers={"kid": "test-key"},
    )
    return verifier, token


def test_health_matches_contract() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": "1.1.0",
        "contract_version": "1.0",
        "capabilities": ["simple-chat", "external-workspace"],
    }


def test_simple_chat_returns_assistant_message() -> None:
    response = client.post(
        "/api/chat",
        json={
            "thread_id": "thread-1",
            "run_id": "run-1",
            "messages": [{"id": "message-1", "role": "user", "content": "图书馆哪里适合自习？"}],
            "context": {"locale": "zh-CN"},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["message"]["role"] == "assistant"
    assert "图书馆" in body["message"]["content"]
    assert body["citations"][0]["label"] == "S1"


def test_chat_rejects_missing_user_message() -> None:
    response = client.post(
        "/api/chat",
        json={
            "thread_id": "thread-1",
            "run_id": "run-1",
            "messages": [{"id": "message-1", "role": "assistant", "content": "hello"}],
            "context": {},
        },
    )
    assert response.status_code == 422


def test_chat_requires_hub_token_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("DEMO_AGENT_REQUIRE_HUB_TOKEN", "1")
    token_verifier.cache_clear()
    response = client.post(
        "/api/chat",
        json={
            "thread_id": "thread-1",
            "run_id": "run-1",
            "messages": [{"id": "message-1", "role": "user", "content": "hello"}],
            "context": {},
        },
    )
    assert response.status_code == 401
    monkeypatch.delenv("DEMO_AGENT_REQUIRE_HUB_TOKEN")
    token_verifier.cache_clear()


def test_hub_token_requires_chat_invoke_scope() -> None:
    verifier, token = _encoded_token(scopes=["workspace:enter"])

    with pytest.raises(HTTPException) as exc_info:
        verifier.verify(f"Bearer {token}", "chat:invoke")

    assert exc_info.value.status_code == 403


def test_hub_token_rejects_overlong_lifetime() -> None:
    verifier, token = _encoded_token(scopes=["chat:invoke"], ttl_seconds=121)

    with pytest.raises(HTTPException) as exc_info:
        verifier.verify(f"Bearer {token}", "chat:invoke")

    assert exc_info.value.status_code == 401


def test_hub_token_refreshes_jwks_once_when_same_kid_rotates(monkeypatch) -> None:
    old_key = Ed25519PrivateKey.generate()
    current_key = Ed25519PrivateKey.generate()
    verifier = HubTokenVerifier()
    verifier.required = True

    def jwk_for(private_key: Ed25519PrivateKey) -> dict:
        public_key = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return {
            "kty": "OKP",
            "crv": "Ed25519",
            "x": base64.urlsafe_b64encode(public_key).rstrip(b"=").decode("ascii"),
            "use": "sig",
            "alg": "EdDSA",
            "kid": "test-key",
        }

    verifier._jwks = {"keys": [jwk_for(old_key)]}
    verifier._jwks_loaded_at = time.monotonic()
    refresh_count = 0

    def load_rotating_jwks() -> dict:
        nonlocal refresh_count
        if verifier._jwks is None:
            verifier._jwks = {"keys": [jwk_for(current_key)]}
            verifier._jwks_loaded_at = time.monotonic()
            refresh_count += 1
        return verifier._jwks

    monkeypatch.setattr(verifier, "_load_jwks", load_rotating_jwks)
    issued_at = int(time.time())
    token = jwt.encode(
        {
            "iss": verifier.issuer,
            "aud": verifier.audience,
            "sub": "demo-c",
            "scope": ["chat:invoke"],
            "iat": issued_at,
            "exp": issued_at + 120,
        },
        current_key,
        algorithm="EdDSA",
        headers={"kid": "test-key"},
    )

    claims = verifier.verify(f"Bearer {token}", "chat:invoke")

    assert claims["sub"] == "demo-c"
    assert refresh_count == 1


def test_hub_token_accepts_registered_future_work_audiences(monkeypatch) -> None:
    monkeypatch.setenv(
        "DEMO_AGENT_HUB_AUDIENCES",
        "campus-helper-demo,course-review-demo,campus-public-service-demo",
    )
    verifier, token = _encoded_token(scopes=["chat:invoke"], audience="course-review-demo")

    claims = verifier.verify(f"Bearer {token}", "chat:invoke")

    assert claims["aud"] == "course-review-demo"
    assert "course-review-demo" in verifier.audiences


def test_future_work_demo_answers_are_explicitly_labeled() -> None:
    review, _ = answer_for("请给我课程评分和老师评价", "course-review-demo")
    public_service, _ = answer_for("我需要签字盖章，应该去哪个楼？", "campus-public-service-demo")

    assert "Future Work Demo" in review
    assert "评课社区" in review
    assert "Future Work Demo" in public_service
    assert "校园公共服务" in public_service
