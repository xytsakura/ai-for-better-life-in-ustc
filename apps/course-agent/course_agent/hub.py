from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal

import httpx
import jwt
from fastapi import HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import Settings


DEMO_USER_IDS = ("demo-a", "demo-b", "demo-c")
JWT_CLOCK_SKEW_SECONDS = 30


class HubChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: Literal["system", "user", "assistant", "tool"]
    content: Any = ""


class RunAgentInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    threadId: str = Field(min_length=1, max_length=200)
    runId: str = Field(min_length=1, max_length=200)
    parentRunId: str | None = Field(default=None, max_length=200)
    state: dict[str, Any] = Field(default_factory=dict)
    messages: list[HubChatMessage] = Field(default_factory=list, max_length=80)
    tools: list[dict[str, Any]] = Field(default_factory=list, max_length=80)
    context: Any = None
    forwardedProps: dict[str, Any] = Field(default_factory=dict)


class HubWorkspaceExchangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=16, max_length=4096)
    state: str = Field(min_length=16, max_length=512)
    redirect_uri: str | None = Field(default=None, max_length=1000)


@dataclass(frozen=True)
class VerifiedHubIdentity:
    hub_sub: str
    course_user_id: str
    display_name: str
    scopes: set[str]
    jti: str
    claims: dict[str, Any]


class HubJwtVerifier:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._jwks_cache: dict[str, Any] | None = None

    async def verify_request(
        self,
        request: Request,
        *,
        required_scope: str,
    ) -> VerifiedHubIdentity | None:
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            if self.settings.hub_auth_required:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={"error": {"code": "missing_hub_token", "message": "Hub token is required"}},
                )
            return None
        request_id = request.headers.get("x-hub-request-id", "").strip()
        if self.settings.hub_auth_required and not request_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": {"code": "missing_hub_request_id", "message": "Hub request id is required"}},
            )
        token = auth.split(" ", 1)[1].strip()
        identity = await self.verify_token(token, required_scope=required_scope)
        token_request_id = str(identity.claims.get("request_id") or "")
        if token_request_id and token_request_id != request_id:
            raise self._invalid("hub_request_id_mismatch")
        return identity

    async def verify_token(self, token: str, *, required_scope: str) -> VerifiedHubIdentity:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise self._invalid("malformed_hub_token") from exc
        if header.get("alg") != "EdDSA" or not isinstance(header.get("kid"), str):
            raise self._invalid("invalid_hub_token_header")
        try:
            payload = await self._decode_with_jwks_refresh(token, header["kid"])
        except jwt.InvalidAudienceError as exc:
            raise self._invalid("invalid_hub_audience") from exc
        except jwt.InvalidIssuerError as exc:
            raise self._invalid("invalid_hub_issuer") from exc
        except jwt.ExpiredSignatureError as exc:
            raise self._invalid("hub_token_expired") from exc
        except jwt.PyJWTError as exc:
            raise self._invalid("invalid_hub_token") from exc
        self._validate_claims(payload)
        scopes = set(str(payload.get("scope") or "").split())
        if required_scope not in scopes:
            raise self._invalid("insufficient_hub_scope", status.HTTP_403_FORBIDDEN)
        sub = str(payload["sub"])
        return VerifiedHubIdentity(
            hub_sub=sub,
            course_user_id=map_hub_subject_to_demo_user(sub, self.settings),
            display_name=str(payload.get("name") or sub),
            scopes=scopes,
            jti=str(payload["jti"]),
            claims=payload,
        )

    async def _decode_with_jwks_refresh(self, token: str, kid: str) -> dict[str, Any]:
        key = await self._public_key_for_kid(kid)
        try:
            return self._decode_token(token, key)
        except jwt.InvalidSignatureError:
            # A Hub restart or emergency rotation may replace a key while a legacy
            # deployment keeps the same kid. Refresh once before rejecting it.
            self._jwks_cache = None
            refreshed_key = await self._public_key_for_kid(kid)
            return self._decode_token(token, refreshed_key)

    def _decode_token(self, token: str, key: Any) -> dict[str, Any]:
        return jwt.decode(
            token,
            key=key,
            algorithms=["EdDSA"],
            audience=self.settings.hub_agent_id,
            issuer=self.settings.hub_issuer,
            leeway=JWT_CLOCK_SKEW_SECONDS,
            options={"require": ["iss", "aud", "sub", "iat", "exp", "jti"]},
        )

    def _validate_claims(self, payload: dict[str, Any]) -> None:
        now = int(time.time())
        for key in ("iss", "aud", "sub", "iat", "exp", "jti"):
            if key not in payload:
                raise self._invalid(f"missing_hub_claim_{key}")
        try:
            iat = int(payload["iat"])
            exp = int(payload["exp"])
        except (TypeError, ValueError) as exc:
            raise self._invalid("invalid_hub_token_time") from exc
        if iat > now + JWT_CLOCK_SKEW_SECONDS:
            raise self._invalid("hub_token_from_future")
        if exp < now - JWT_CLOCK_SKEW_SECONDS:
            raise self._invalid("hub_token_expired")
        if exp <= iat or exp - iat > 120 + JWT_CLOCK_SKEW_SECONDS:
            raise self._invalid("invalid_hub_token_lifetime")
        if not str(payload.get("sub") or "").strip() or not str(payload.get("jti") or "").strip():
            raise self._invalid("invalid_hub_token_subject")

    async def _public_key_for_kid(self, kid: str) -> Any:
        jwks = await self._load_jwks()
        key = _find_jwk(jwks, kid)
        if key is None:
            self._jwks_cache = None
            jwks = await self._load_jwks()
            key = _find_jwk(jwks, kid)
        if key is None:
            raise self._invalid("unknown_hub_key")
        try:
            return jwt.PyJWK.from_dict(key, algorithm="EdDSA").key
        except (jwt.PyJWTError, ValueError) as exc:
            raise self._invalid("invalid_hub_public_key") from exc

    async def _load_jwks(self) -> dict[str, Any]:
        if self._jwks_cache is not None:
            return self._jwks_cache
        if self.settings.hub_jwks_json:
            jwks = json.loads(self.settings.hub_jwks_json)
        elif self.settings.hub_jwks_url:
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
                response = await client.get(self.settings.hub_jwks_url)
                response.raise_for_status()
                jwks = response.json()
        else:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error": {"code": "hub_jwks_not_configured", "message": "Hub JWKS is not configured"}},
            )
        if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list):
            raise self._invalid("invalid_hub_jwks")
        self._jwks_cache = jwks
        return jwks

    @staticmethod
    def _invalid(code: str, status_code: int = status.HTTP_401_UNAUTHORIZED) -> HTTPException:
        return HTTPException(
            status_code=status_code,
            detail={"error": {"code": code, "message": "Invalid Hub token"}},
        )


def map_hub_subject_to_demo_user(subject: str, settings: Settings) -> str:
    mapping = settings.hub_user_mapping or {}
    if subject in mapping and mapping[subject] in DEMO_USER_IDS:
        return mapping[subject]
    if subject in DEMO_USER_IDS:
        return subject
    normalized = subject.lower()
    for user_id in DEMO_USER_IDS:
        if normalized.endswith(user_id):
            return user_id
    digest = hashlib.sha256(subject.encode("utf-8")).digest()
    return DEMO_USER_IDS[digest[0] % len(DEMO_USER_IDS)]


def extract_question_and_history(payload: RunAgentInput) -> tuple[str, list[dict[str, str]]]:
    history: list[dict[str, str]] = []
    question = ""
    for message in payload.messages:
        role = message.role
        content = _message_text(message.content).strip()
        if not content or role not in {"user", "assistant", "system"}:
            continue
        if role == "user":
            question = content
        history.append({"role": role, "content": content})
    if not question:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": {"code": "missing_user_message", "message": "RunAgentInput requires a user message"}},
        )
    return question, history[:-1]


def course_query_options(payload: RunAgentInput) -> dict[str, Any]:
    source = _deep_get(payload.forwardedProps, "course_agent")
    if not isinstance(source, dict):
        source = _deep_get(payload.state, "course_agent")
    if not isinstance(source, dict):
        source = {}
    return {
        "mode": source.get("mode") or "direct",
        "scope": source.get("scope"),
        "space_id": source.get("space_id"),
        "document_ids": source.get("document_ids") if isinstance(source.get("document_ids"), list) else [],
        "top_k": source.get("top_k") or 5,
        "model": source.get("model"),
        "reasoning_effort": source.get("reasoning_effort"),
    }


def agui_sse(data: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n".encode("utf-8")


def agui_event(event_type: str, **fields: Any) -> bytes:
    return agui_sse({"type": event_type, **{key: value for key, value in fields.items() if value is not None}})


def new_message_id() -> str:
    return f"msg_{uuid.uuid4().hex}"


async def exchange_workspace_code(settings: Settings, payload: HubWorkspaceExchangeRequest) -> dict[str, Any]:
    if not settings.hub_token_endpoint or not settings.hub_client_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"code": "hub_workspace_exchange_not_configured", "message": "Hub token exchange is not configured"}},
        )
    redirect_uri = payload.redirect_uri or settings.hub_workspace_redirect_uri
    auth = base64.b64encode(
        f"{settings.hub_client_id}:{settings.hub_client_secret}".encode("utf-8")
    ).decode("ascii")
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=False) as client:
        response = await client.post(
            settings.hub_token_endpoint,
            headers={
                "Authorization": f"Basic {auth}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "authorization_code",
                "code": payload.code,
                "redirect_uri": redirect_uri,
                "state": payload.state,
            },
        )
    if response.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "hub_workspace_exchange_failed", "message": "Hub authorization code exchange failed"}},
        )
    data = response.json()
    if not isinstance(data, dict) or not isinstance(data.get("access_token"), str):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"code": "hub_workspace_exchange_protocol_error", "message": "Hub token endpoint returned an invalid response"}},
        )
    return data


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    if isinstance(content, dict):
        text = content.get("text") or content.get("content")
        if isinstance(text, str):
            return text
    return ""


def _deep_get(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else None


def _find_jwk(jwks: dict[str, Any], kid: str) -> dict[str, Any] | None:
    for key in jwks.get("keys") or []:
        if isinstance(key, dict) and key.get("kid") == kid:
            return key
    return None
