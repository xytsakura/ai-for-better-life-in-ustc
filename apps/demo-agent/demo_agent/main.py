from __future__ import annotations

import os
import secrets
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import httpx
import jwt
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from . import __version__


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    role: Literal["system", "user", "assistant"]
    content: str = Field(max_length=100_000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    messages: list[Message] = Field(min_length=1, max_length=256)
    context: dict[str, Any]


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    title: str
    url: str | None = None


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: Message
    citations: list[Citation] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=dict)


class HubTokenVerifier:
    def __init__(self) -> None:
        self.required = os.getenv("DEMO_AGENT_REQUIRE_HUB_TOKEN", "0") == "1"
        self.jwks_url = os.getenv("DEMO_AGENT_HUB_JWKS_URL", "http://127.0.0.1:8100/.well-known/jwks.json")
        self.audience = os.getenv("DEMO_AGENT_HUB_AUDIENCE", "campus-helper-demo")
        self.issuer = os.getenv("DEMO_AGENT_HUB_ISSUER", "campus-agent-hub")
        self.max_token_ttl_seconds = int(os.getenv("DEMO_AGENT_HUB_MAX_TOKEN_TTL_SECONDS", "120"))
        self._jwks: dict[str, Any] | None = None
        self._jwks_loaded_at = 0.0

    def _load_jwks(self) -> dict[str, Any]:
        if self._jwks is not None and time.monotonic() - self._jwks_loaded_at < 300:
            return self._jwks
        with httpx.Client(timeout=3.0) as client:
            response = client.get(self.jwks_url)
            response.raise_for_status()
        self._jwks = response.json()
        self._jwks_loaded_at = time.monotonic()
        return self._jwks

    def verify(self, authorization: str | None, required_scope: str) -> dict[str, Any]:
        if not authorization and not self.required:
            return {"sub": "direct-demo-user", "scope": [required_scope]}
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Hub access token is required")

        token = authorization.removeprefix("Bearer ").strip()
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            jwk = next(item for item in self._load_jwks().get("keys", []) if item.get("kid") == kid)
            key = jwt.PyJWK.from_dict(jwk).key
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["EdDSA"],
                audience=self.audience,
                issuer=self.issuer,
                leeway=30,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except (StopIteration, jwt.PyJWTError, httpx.HTTPError, ValueError) as error:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Hub access token") from error
        scopes = claims.get("scope", [])
        if isinstance(scopes, str):
            scopes = scopes.split()
        if not isinstance(scopes, list) or required_scope not in scopes:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Required Hub scope is missing")
        issued_at = claims.get("iat")
        expires_at = claims.get("exp")
        if (
            not isinstance(issued_at, (int, float))
            or not isinstance(expires_at, (int, float))
            or expires_at - issued_at > self.max_token_ttl_seconds
        ):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Hub token lifetime")
        return claims


@lru_cache(maxsize=1)
def token_verifier() -> HubTokenVerifier:
    return HubTokenVerifier()


def require_hub_identity(
    authorization: str | None = Header(default=None),
    verifier: HubTokenVerifier = Depends(token_verifier),
) -> dict[str, Any]:
    return verifier.verify(authorization, "chat:invoke")


def answer_for(question: str) -> tuple[str, list[Citation]]:
    normalized = question.casefold()
    if any(keyword in normalized for keyword in ("图书馆", "自习", "座位")):
        return (
            "可以先查看图书馆座位与开放安排，再根据校区和空闲时段选择自习地点。正式使用时，这个 Agent 可以接入校园开放数据；当前演示回答用于验证第三方 Agent 的标准接入流程。",
            [Citation(label="S1", title="中国科学技术大学图书馆", url="https://lib.ustc.edu.cn/")],
        )
    if any(keyword in normalized for keyword in ("校车", "班车", "通勤")):
        return (
            "建议先确认出发校区、目标校区和希望到达的时间，再查询当天班车安排。演示版不生成未经核实的具体班次，避免把过期时刻表当成实时信息。",
            [],
        )
    if any(keyword in normalized for keyword in ("活动", "讲座", "社团")):
        return (
            "我可以按时间、校区和活动类型整理校园活动。当前是独立 Demo Agent，重点用于展示它从外链应用升级为平台内 Connected Agent 后，仍可复用同一个服务。",
            [],
        )
    return (
        "这是校园助手 Demo Agent 的标准协议回复。它与瀚海行是两个独立服务，Hub 只根据注册版本和协议转发请求，因此接入它不需要在 Gateway 中增加业务分支。",
        [],
    )


app = FastAPI(title="Campus Helper Demo Agent", version=__version__)
WEB_ROOT = Path(__file__).with_name("web")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(WEB_ROOT / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": __version__,
        "contract_version": "1.0",
        "capabilities": ["simple-chat", "external-workspace"],
    }


@app.post("/api/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, _identity: dict[str, Any] = Depends(require_hub_identity)) -> ChatResponse:
    user_messages = [message for message in payload.messages if message.role == "user"]
    if not user_messages:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="At least one user message is required")

    content, citations = answer_for(user_messages[-1].content)
    return ChatResponse(
        message=Message(id=f"assistant-{secrets.token_hex(8)}", role="assistant", content=content),
        citations=citations,
        usage={
            "input_tokens": max(1, len(user_messages[-1].content) // 2),
            "output_tokens": max(1, len(content) // 2),
        },
    )


def run() -> None:
    uvicorn.run(
        "demo_agent.main:app",
        host=os.getenv("DEMO_AGENT_HOST", "127.0.0.1"),
        port=int(os.getenv("DEMO_AGENT_PORT", "8101")),
    )


if __name__ == "__main__":
    run()
