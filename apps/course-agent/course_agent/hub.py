from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse, urlunparse

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


@dataclass(frozen=True)
class HubModelDelegation:
    delegation_id: str
    access_token: str
    hub_sub: str
    course_user_id: str
    display_name: str
    expires_at: float


@dataclass(frozen=True)
class HubModelContext:
    hub_sub: str
    course_user_id: str
    display_name: str
    delegate_token: str
    request_id: str


@dataclass(frozen=True)
class HubModelGrant:
    access_token: str
    expires_in: int
    model: str | None = None
    request_id: str | None = None


@dataclass(frozen=True)
class HubModelGatewayResult:
    text: str
    model: str
    usage: dict[str, Any] | None = None


class HubModelGatewayError(Exception):
    def __init__(
        self,
        code: str,
        message: str = "",
        *,
        retryable: bool = False,
        allow_fallback: bool = False,
    ):
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.retryable = retryable
        self.allow_fallback = allow_fallback


class HubModelDelegationStore:
    """Server-side storage for Hub user delegation tokens.

    Starlette's default session middleware stores signed data in the browser
    cookie. Hub workspace/model tokens therefore must not be written into the
    session itself. The session only keeps an opaque handle; the token stays in
    this process-local store and naturally expires.
    """

    def __init__(self) -> None:
        self._items: dict[str, HubModelDelegation] = {}

    def put(
        self,
        *,
        access_token: str,
        identity: VerifiedHubIdentity,
        expires_in: int,
        max_ttl_seconds: int,
    ) -> str:
        self.prune()
        ttl = max(1, min(int(expires_in or max_ttl_seconds), max_ttl_seconds))
        delegation_id = f"hubdlg_{uuid.uuid4().hex}"
        self._items[delegation_id] = HubModelDelegation(
            delegation_id=delegation_id,
            access_token=access_token,
            hub_sub=identity.hub_sub,
            course_user_id=identity.course_user_id,
            display_name=identity.display_name,
            expires_at=time.time() + ttl,
        )
        return delegation_id

    def get(self, delegation_id: str | None) -> HubModelDelegation | None:
        if not delegation_id:
            return None
        item = self._items.get(delegation_id)
        if item is None:
            return None
        if item.expires_at <= time.time():
            self._items.pop(delegation_id, None)
            return None
        return item

    def pop(self, delegation_id: str | None) -> HubModelDelegation | None:
        if not delegation_id:
            return None
        item = self._items.pop(delegation_id, None)
        if item is None or item.expires_at <= time.time():
            return None
        return item

    def remove(self, delegation_id: str | None) -> None:
        self._items.pop(delegation_id or "", None)

    def prune(self) -> None:
        now = time.time()
        expired = [key for key, item in self._items.items() if item.expires_at <= now]
        for key in expired:
            self._items.pop(key, None)


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
    client_secret = settings.current_hub_client_secret()
    if not settings.hub_token_endpoint or not client_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"code": "hub_workspace_exchange_not_configured", "message": "Hub token exchange is not configured"}},
        )
    redirect_uri = payload.redirect_uri or settings.hub_workspace_redirect_uri
    auth = base64.b64encode(
        f"{settings.hub_client_id}:{client_secret}".encode("utf-8")
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


class HubModelGatewayClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def is_configured(self) -> bool:
        return self.settings.hub_model_gateway_configured

    def revoke_delegation(self, token: str) -> bool:
        value = str(token or "").strip()
        if not value or not self.settings.current_hub_client_secret():
            return False
        try:
            with httpx.Client(timeout=8.0, follow_redirects=False) as client:
                response = client.post(
                    self._delegation_revoke_url(),
                    headers=self._client_secret_basic_headers(),
                    json={"token": value},
                )
            return 200 <= response.status_code < 300
        except Exception:
            return False

    async def revoke_delegation_async(self, token: str) -> bool:
        value = str(token or "").strip()
        if not value or not self.settings.current_hub_client_secret():
            return False
        try:
            async with httpx.AsyncClient(timeout=8.0, follow_redirects=False) as client:
                response = await client.post(
                    self._delegation_revoke_url(),
                    headers=self._client_secret_basic_headers(),
                    json={"token": value},
                )
            return 200 <= response.status_code < 300
        except Exception:
            return False

    def generate(
        self,
        *,
        context: HubModelContext,
        instructions: str,
        messages: list[dict[str, Any]],
        reasoning_effort: str | None,
        max_output_tokens: int,
    ) -> HubModelGatewayResult:
        grant = self._exchange_grant(context)
        payload = self._gateway_payload(
            instructions=instructions,
            messages=messages,
            reasoning_effort=reasoning_effort,
            max_output_tokens=max_output_tokens,
            stream=False,
        )
        try:
            with httpx.Client(
                timeout=self.settings.hub_model_gateway_timeout_seconds,
                follow_redirects=False,
            ) as client:
                response = client.post(
                    self.settings.hub_model_gateway_url,
                    headers={
                        "Authorization": f"Bearer {grant.access_token}",
                        "Content-Type": "application/json",
                        "X-Course-Agent-Request-Id": context.request_id,
                    },
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise HubModelGatewayError(
                "model_gateway_unreachable",
                "Hub Model Gateway 暂不可用",
                retryable=True,
                allow_fallback=True,
            ) from exc
        if response.status_code >= 400:
            raise self._http_error(response, default_code="model_gateway_request_failed")
        data = self._json_response(response)
        text = _model_text(data)
        if not text:
            raise HubModelGatewayError("model_gateway_empty_response", "平台模型返回为空", retryable=True)
        model_value = data.get("model") or grant.model or "platform-model"
        if isinstance(model_value, dict):
            model_value = model_value.get("id") or model_value.get("model_id") or "platform-model"
        return HubModelGatewayResult(
            text=text,
            model=str(model_value),
            usage=data.get("usage") if isinstance(data.get("usage"), dict) else None,
        )

    async def stream_generate(
        self,
        *,
        context: HubModelContext,
        instructions: str,
        messages: list[dict[str, Any]],
        reasoning_effort: str | None,
        max_output_tokens: int,
    ):
        grant = await self._exchange_grant_async(context)
        payload = self._gateway_payload(
            instructions=instructions,
            messages=messages,
            reasoning_effort=reasoning_effort,
            max_output_tokens=max_output_tokens,
            stream=True,
        )
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.hub_model_gateway_timeout_seconds,
                follow_redirects=False,
            ) as client:
                async with client.stream(
                    "POST",
                    self.settings.hub_model_gateway_url,
                    headers={
                        "Authorization": f"Bearer {grant.access_token}",
                        "Content-Type": "application/json",
                        "X-Course-Agent-Request-Id": context.request_id,
                    },
                    json=payload,
                ) as response:
                    if response.status_code >= 400:
                        await response.aread()
                        raise self._http_error(
                            response,
                            default_code="model_gateway_request_failed",
                        )
                    async for event in _iter_gateway_sse(response):
                        yield event, grant
        except HubModelGatewayError:
            raise
        except httpx.HTTPError as exc:
            raise HubModelGatewayError(
                "model_gateway_unreachable",
                "Hub Model Gateway 暂不可用",
                retryable=True,
                allow_fallback=True,
            ) from exc

    def _exchange_grant(self, context: HubModelContext) -> HubModelGrant:
        if not self.is_configured():
            raise HubModelGatewayError(
                "model_gateway_not_configured",
                "Hub Model Gateway 未配置",
                allow_fallback=True,
            )
        payload = self._grant_payload(context)
        try:
            with httpx.Client(timeout=8.0, follow_redirects=False) as client:
                response = client.post(
                    self.settings.hub_model_grant_endpoint,
                    headers=self._grant_headers(context),
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise HubModelGatewayError(
                "model_grant_unreachable",
                "Hub 模型授权暂不可用",
                retryable=True,
                allow_fallback=True,
            ) from exc
        if response.status_code >= 400:
            raise self._http_error(response, default_code="model_grant_failed")
        return self._parse_grant(response)

    async def _exchange_grant_async(self, context: HubModelContext) -> HubModelGrant:
        if not self.is_configured():
            raise HubModelGatewayError(
                "model_gateway_not_configured",
                "Hub Model Gateway 未配置",
                allow_fallback=True,
            )
        payload = self._grant_payload(context)
        try:
            async with httpx.AsyncClient(timeout=8.0, follow_redirects=False) as client:
                response = await client.post(
                    self.settings.hub_model_grant_endpoint,
                    headers=self._grant_headers(context),
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise HubModelGatewayError(
                "model_grant_unreachable",
                "Hub 模型授权暂不可用",
                retryable=True,
                allow_fallback=True,
            ) from exc
        if response.status_code >= 400:
            raise self._http_error(response, default_code="model_grant_failed")
        return self._parse_grant(response)

    def _client_secret_basic_headers(self) -> dict[str, str]:
        auth = base64.b64encode(
            f"{self.settings.hub_client_id}:{self.settings.current_hub_client_secret()}".encode("utf-8")
        ).decode("ascii")
        return {
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/json",
        }

    def _grant_headers(self, context: HubModelContext) -> dict[str, str]:
        return self._client_secret_basic_headers()

    def _grant_payload(self, context: HubModelContext) -> dict[str, Any]:
        return {
            "model_delegation_token": context.delegate_token,
            "request_id": context.request_id,
        }

    def _delegation_revoke_url(self) -> str:
        parsed = urlparse(self.settings.hub_model_grant_endpoint)
        path = parsed.path or ""
        marker = "/api/model-gateway/"
        if marker in path:
            prefix = path.split(marker, 1)[0]
            revoke_path = f"{prefix}{marker}delegations/revoke"
        else:
            revoke_path = "/api/model-gateway/delegations/revoke"
        return urlunparse((parsed.scheme, parsed.netloc, revoke_path, "", "", ""))

    @staticmethod
    def _gateway_payload(
        *,
        instructions: str,
        messages: list[dict[str, Any]],
        reasoning_effort: str | None,
        max_output_tokens: int,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "instructions": instructions,
            "messages": messages,
            "max_output_tokens": max_output_tokens,
            "stream": stream,
        }
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort
        return payload

    @staticmethod
    def _json_response(response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise HubModelGatewayError(
                "model_gateway_protocol_error",
                "Hub Model Gateway 返回了无法解析的响应",
                retryable=True,
            ) from exc
        if not isinstance(data, dict):
            raise HubModelGatewayError(
                "model_gateway_protocol_error",
                "Hub Model Gateway 响应格式无效",
                retryable=True,
            )
        return data

    def _parse_grant(self, response: httpx.Response) -> HubModelGrant:
        data = self._json_response(response)
        token = data.get("access_token") or data.get("grant") or data.get("grant_token") or data.get("token")
        if not isinstance(token, str) or not token.strip():
            raise HubModelGatewayError(
                "model_grant_protocol_error",
                "Hub 模型授权响应缺少 grant token",
                retryable=True,
            )
        expires_in = data.get("expires_in", 120)
        try:
            expires = max(1, min(120, int(expires_in)))
        except (TypeError, ValueError):
            expires = 120
        model_value = data.get("model") or data.get("model_id")
        if isinstance(model_value, dict):
            model_value = model_value.get("id") or model_value.get("model_id")
        return HubModelGrant(
            access_token=token.strip(),
            expires_in=expires,
            model=str(model_value or "") or None,
            request_id=str(data.get("request_id") or "") or None,
        )

    def _http_error(self, response: httpx.Response, *, default_code: str) -> HubModelGatewayError:
        data: Any = None
        try:
            data = response.json()
        except ValueError:
            data = None
        code = default_code
        message = ""
        if isinstance(data, dict):
            error = data.get("error") or data.get("detail")
            if isinstance(error, dict):
                if isinstance(error.get("code"), str):
                    code = error["code"]
                if isinstance(error.get("message"), str):
                    message = error["message"]
            elif isinstance(error, str):
                message = error
        if not message:
            message = f"Hub Model Gateway returned HTTP {response.status_code}"
        allow_fallback = response.status_code in {404, 409, 429, 502, 503, 504}
        if code in {"model_binding_not_found", "model_profile_not_found", "model_profile_disabled"}:
            allow_fallback = True
        if response.status_code in {401, 403} or code in {
            "model_grant_invalid",
            "model_grant_expired",
            "model_not_allowed",
        }:
            allow_fallback = False
        return HubModelGatewayError(
            _normalize_error_code(code, fallback=default_code),
            _redact_bearer(message),
            retryable=response.status_code in {429, 502, 503, 504},
            allow_fallback=allow_fallback,
        )


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


def _model_text(data: dict[str, Any]) -> str:
    for key in ("answer", "text", "output_text"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


async def _iter_gateway_sse(response: httpx.Response):
    data_lines: list[str] = []
    event_name: str | None = None
    async for raw_line in response.aiter_lines():
        line = raw_line.rstrip("\r")
        if not line:
            event = _parse_gateway_sse_data(data_lines, event_name=event_name)
            data_lines = []
            event_name = None
            if event is not None:
                yield event
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[6:].strip() or None
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    event = _parse_gateway_sse_data(data_lines, event_name=event_name)
    if event is not None:
        yield event


def _parse_gateway_sse_data(
    data_lines: list[str],
    *,
    event_name: str | None = None,
) -> dict[str, Any] | None:
    if not data_lines:
        return None
    data = "\n".join(data_lines).strip()
    if not data or data == "[DONE]":
        return None
    parsed = json.loads(data)
    if not isinstance(parsed, dict):
        raise HubModelGatewayError(
            "model_gateway_protocol_error",
            "Hub Model Gateway SSE 数据格式无效",
            retryable=True,
        )
    if event_name and "type" not in parsed:
        parsed["type"] = event_name
    return parsed


def _normalize_error_code(raw: str, *, fallback: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in raw.strip()).strip("_")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned[:80] or fallback


def _redact_bearer(message: str) -> str:
    parts = str(message or "").split()
    redacted: list[str] = []
    skip_next = False
    for index, part in enumerate(parts):
        if skip_next:
            redacted.append("[REDACTED]")
            skip_next = False
            continue
        redacted.append(part)
        if part.lower() == "bearer" and index + 1 < len(parts):
            skip_next = True
    return " ".join(redacted)[:500]
