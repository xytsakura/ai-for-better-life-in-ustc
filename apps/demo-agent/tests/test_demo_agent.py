import base64
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import HTTPException
from fastapi.testclient import TestClient

from demo_agent.main import HubTokenVerifier, app, token_verifier


client = TestClient(app)


def setup_function() -> None:
    token_verifier.cache_clear()


def _encoded_token(*, scopes: list[str], ttl_seconds: int = 120) -> tuple[HubTokenVerifier, str]:
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
            "aud": verifier.audience,
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
