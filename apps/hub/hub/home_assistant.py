from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .model_gateway import (
    ModelMessage,
    ModelProfileService,
    call_user_global_model,
    stream_user_global_model,
)
from .registry import list_agents
from .utils import new_id


INSTANT_INSTRUCTIONS = "你是 Campus Agent Hub 首页助手。简洁、友好、务实回答。"
AUTO_INSTRUCTIONS = (
    "你是 Campus Agent Hub 首页助手。先判断用户问题是否适合已注册的校园 Agent；"
    "如果高度匹配，给出简短说明并推荐一个 Agent；如果没有高度匹配，就直接回答用户问题。"
    "不要声称访问了不存在的数据，不要输出 URL、token 或凭据。"
    "只输出一个 JSON 对象，格式为："
    '{"answer":"给用户的直接回答或下一步说明","recommend":true|false,'
    '"agent_id":null|"清单中的 agent_id","reason":"一句话推荐理由"}。'
)
MAX_HISTORY_MESSAGES = 12
MAX_ROUTE_CATALOG_ITEMS = 20


class HomeAssistantMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=0, max_length=8_000)


class HomeAssistantChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["instant", "route", "auto"]
    messages: list[HomeAssistantMessage] = Field(min_length=1, max_length=32)

    @field_validator("messages")
    @classmethod
    def require_user_message(cls, value: list[HomeAssistantMessage]) -> list[HomeAssistantMessage]:
        if not any(item.role == "user" and item.content.strip() for item in value):
            raise ValueError("at least one non-empty user message is required")
        return value


class CatalogAgent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=240)
    keywords: list[str] = Field(default_factory=list, max_length=16)


class AgentCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agents: list[CatalogAgent] = Field(default_factory=list, max_length=64)


class RouteModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommend: bool = False
    agent_id: str | None = Field(default=None, max_length=80)
    reason: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def normalize_empty_recommendation(self) -> "RouteModelOutput":
        if not self.recommend:
            self.agent_id = None
        return self


class AutoModelOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    answer: str = Field(default="", max_length=8_000)
    recommend: bool = False
    agent_id: str | None = Field(default=None, max_length=80)
    reason: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def normalize_empty_recommendation(self) -> "AutoModelOutput":
        if not self.recommend:
            self.agent_id = None
        return self


def skills_root() -> Path:
    return Path(__file__).resolve().parents[1] / "skills" / "agent-routing"


def _load_text(name: str) -> str:
    path = skills_root() / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "agent_routing_docs_unavailable"},
        ) from exc


def _json_from_fenced_block(markdown: str) -> Any:
    match = re.search(r"```json\s*(.*?)\s*```", markdown, flags=re.DOTALL | re.IGNORECASE)
    if not match:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "agent_catalog_unreadable"},
        )
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "agent_catalog_unreadable"},
        ) from exc


def load_agent_routing_skill() -> str:
    return _load_text("SKILL.md")


def load_agent_catalog() -> AgentCatalog:
    return AgentCatalog.model_validate(_json_from_fenced_block(_load_text("AGENT_CATALOG.md")))


def _bounded_messages(source: list[HomeAssistantMessage]) -> list[ModelMessage]:
    bounded = source[-MAX_HISTORY_MESSAGES:]
    return [
        ModelMessage(role=item.role, content=item.content.strip())
        for item in bounded
        if item.content.strip()
    ]


def _safe_model_error_event(code: str) -> bytes:
    payload = {"type": "model.error", "error": code}
    return (
        "event: model.error\n"
        f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
    ).encode("utf-8")


async def _single_error_stream(code: str) -> StreamingResponse:
    async def iterator():
        yield _safe_model_error_event(code)

    return StreamingResponse(
        iterator(),
        media_type="text/event-stream",
        headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
    )


def _error_code(exc: HTTPException) -> str:
    detail = exc.detail
    if isinstance(detail, dict) and isinstance(detail.get("error"), str):
        return detail["error"]
    return "home_assistant_error"


def _active_catalog_by_id(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    public_agents = {
        item["agent_id"]: item
        for item in list_agents(conn, include_private=False, active_only=True)
        if item.get("status") == "active" and item.get("active_version")
    }
    catalog = load_agent_catalog()
    result: dict[str, dict[str, Any]] = {}
    for entry in catalog.agents[:MAX_ROUTE_CATALOG_ITEMS]:
        public = public_agents.get(entry.agent_id)
        if not public:
            continue
        result[entry.agent_id] = {
            "agent_id": entry.agent_id,
            "name": public.get("name") or entry.name,
            "description": public.get("summary") or entry.summary,
            "catalog_summary": entry.summary,
            "keywords": entry.keywords,
        }
    return result


def _route_instructions(skill_text: str, active_catalog: dict[str, dict[str, Any]]) -> str:
    compact_catalog = [
        {
            "agent_id": item["agent_id"],
            "name": item["name"],
            "summary": item["catalog_summary"],
            "keywords": item["keywords"],
        }
        for item in active_catalog.values()
    ]
    return "\n\n".join(
        [
            "你是 Campus Agent Hub 的需求路由助手。",
            skill_text,
            "当前已通过平台验收且用户可见的 Agent 清单如下：",
            json.dumps({"agents": compact_catalog}, ensure_ascii=False, separators=(",", ":")),
            (
                "只输出一个 JSON 对象，不要输出 Markdown。格式："
                '{"recommend":true|false,"agent_id":null|"清单中的 agent_id","reason":"一句话推荐理由"}。'
                "如果没有高度匹配的 Agent，recommend=false 且 agent_id=null。"
            ),
        ]
    )


def _parse_route_output(text: str) -> RouteModelOutput:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        stripped = fenced.group(1).strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return RouteModelOutput(recommend=False, reason="暂时无法可靠解析路由结果。")
    try:
        return RouteModelOutput.model_validate(payload)
    except Exception:
        return RouteModelOutput(recommend=False, reason="暂时无法可靠解析路由结果。")


def _parse_auto_output(text: str) -> AutoModelOutput:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        stripped = fenced.group(1).strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return AutoModelOutput(answer=stripped[:8_000])
    try:
        return AutoModelOutput.model_validate(payload)
    except Exception:
        return AutoModelOutput(answer=stripped[:8_000])


async def home_assistant_chat(
    conn: sqlite3.Connection,
    model_service: ModelProfileService,
    *,
    user: dict[str, str],
    body: HomeAssistantChatRequest,
) -> StreamingResponse | dict[str, Any]:
    messages = _bounded_messages(body.messages)
    request_id = new_id("home")
    if body.mode == "instant":
        try:
            return await stream_user_global_model(
                conn,
                model_service,
                user_id=user["user_id"],
                request_id=request_id,
                instructions=INSTANT_INSTRUCTIONS,
                messages=messages,
                max_output_tokens=900,
            )
        except HTTPException as exc:
            return await _single_error_stream(_error_code(exc))

    if body.mode == "auto":
        active_catalog = _active_catalog_by_id(conn)
        if active_catalog:
            instructions = "\n\n".join(
                [
                    AUTO_INSTRUCTIONS,
                    "当前已通过平台验收且用户可见的 Agent 清单如下：",
                    json.dumps(
                        {
                            "agents": [
                                {
                                    "agent_id": item["agent_id"],
                                    "name": item["name"],
                                    "summary": item["catalog_summary"],
                                    "keywords": item["keywords"],
                                }
                                for item in active_catalog.values()
                            ]
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ]
            )
        else:
            instructions = INSTANT_INSTRUCTIONS
        try:
            result = await call_user_global_model(
                conn,
                model_service,
                user_id=user["user_id"],
                request_id=request_id,
                instructions=instructions,
                messages=messages,
                max_output_tokens=900,
            )
        except HTTPException as exc:
            raise HTTPException(exc.status_code, detail={"error": _error_code(exc)}) from exc

        parsed = _parse_auto_output(result.output_text)
        recommendation = None
        if parsed.recommend and parsed.agent_id in active_catalog:
            safe = active_catalog[parsed.agent_id]
            recommendation = {
                "agent_id": safe["agent_id"],
                "name": safe["name"],
                "description": safe["description"],
                "reason": parsed.reason or "这个 Agent 与当前需求最匹配。",
            }
        return {
            "mode": "auto",
            "message": parsed.answer or "我暂时没有足够信息回答这个问题。",
            "recommendation": recommendation,
            "model": result.model,
            "usage": result.usage,
        }

    active_catalog = _active_catalog_by_id(conn)
    if not active_catalog:
        return {
            "mode": "route",
            "message": "当前还没有可推荐的已上线 Agent。你可以先用普通对话描述需求，或稍后再试。",
            "recommendation": None,
            "model": None,
        }
    try:
        result = await call_user_global_model(
            conn,
            model_service,
            user_id=user["user_id"],
            request_id=request_id,
            instructions=_route_instructions(load_agent_routing_skill(), active_catalog),
            messages=messages,
            max_output_tokens=400,
        )
    except HTTPException as exc:
        raise HTTPException(
            exc.status_code,
            detail={"error": _error_code(exc)},
        ) from exc

    routed = _parse_route_output(result.output_text)
    recommendation = None
    if routed.recommend and routed.agent_id in active_catalog:
        safe = active_catalog[routed.agent_id]
        recommendation = {
            "agent_id": safe["agent_id"],
            "name": safe["name"],
            "description": safe["description"],
            "reason": routed.reason or "这个 Agent 与当前需求最匹配。",
        }
    message = (
        "我找到一个更适合处理这个需求的 Agent，可以直接进入。"
        if recommendation
        else "我暂时没有找到足够匹配的专门 Agent，可以先在这里继续普通对话。"
    )
    return {
        "mode": "route",
        "message": message,
        "recommendation": recommendation,
        "model": result.model,
        "usage": result.usage,
    }
