from __future__ import annotations

import asyncio
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
MAX_HISTORY_MESSAGES = 12
MAX_ROUTE_CATALOG_ITEMS = 20
ROUTE_REQUEST_MARKERS = (
    "推荐",
    "查询",
    "查找",
    "寻找",
    "检索",
    "搜集",
    "汇总",
    "对比",
    "比较",
    "帮我",
    "请帮",
    "给我",
    "我需要",
    "我要",
    "我想要",
    "想找",
    "想查",
    "需要找",
    "需要查",
    "想复习",
    "要复习",
    "需要复习",
    "想选课",
    "要选课",
    "需要选课",
    "想办理",
    "要办理",
    "需要办理",
    "在哪",
    "哪里",
    "怎么走",
    "怎么办理",
    "如何办理",
    "整理",
    "规划",
    "怎么申请",
    "如何申请",
    "怎么样",
)

HIGH_CONFIDENCE_ROUTES = (
    {
        "agent_id": "campus-public-service-demo",
        "all_groups": (
            ("签字", "盖章", "签章", "公章"),
            (
                "在哪",
                "哪里",
                "地点",
                "位置",
                "找谁",
                "行政老师",
                "窗口",
                "办理",
                "办事",
                "流程",
                "材料",
                "手续",
                "申请",
            ),
        ),
        "reason": "校园公共服务 Agent 适合查询签字盖章、行政窗口和具体办事地点。",
    },
    {
        "agent_id": "hanhai-course-agent",
        "all_groups": (
            ("复习", "备考", "考前"),
            (
                "期末",
                "考试",
                "数学分析",
                "数分",
                "线性代数",
                "线代",
                "概率论",
                "概统",
                "高等数学",
                "高数",
            ),
        ),
        "reason": "瀚海行适合使用课程资料、知识库和试卷辅助期末复习。",
    },
    {
        "agent_id": "hanhai-course-agent",
        "any_terms": ("复习资料", "课程资料", "真题", "往年题", "试卷讲解", "讲解试卷"),
        "reason": "瀚海行适合整理课程资料、检索真题并讲解试卷。",
    },
)


class HomeAssistantMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=0, max_length=8_000)


class HomeAssistantChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["instant", "route", "auto", "route_stream"]
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


def _home_sse(event: str, payload: dict[str, Any]) -> bytes:
    data = {"type": event, **payload}
    return (
        f"event: {event}\n"
        f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
    ).encode("utf-8")


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


def _should_attempt_route(
    messages: list[ModelMessage],
    active_catalog: dict[str, dict[str, Any]],
) -> bool:
    if not active_catalog:
        return False
    latest_user = next((item.content for item in reversed(messages) if item.role == "user"), "")
    normalized = re.sub(r"\s+", "", latest_user).casefold()
    if not normalized:
        return False
    keywords = {
        re.sub(r"\s+", "", keyword).casefold()
        for item in active_catalog.values()
        for keyword in item.get("keywords", [])
        if isinstance(keyword, str) and len(re.sub(r"\s+", "", keyword)) >= 2
    }
    has_domain_signal = any(keyword in normalized for keyword in keywords)
    has_request_signal = any(marker in normalized for marker in ROUTE_REQUEST_MARKERS)
    return has_domain_signal and has_request_signal


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


def _high_confidence_recommendation(
    messages: list[ModelMessage],
    active_catalog: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    latest_user = next((item.content for item in reversed(messages) if item.role == "user"), "")
    normalized = re.sub(r"\s+", "", latest_user).casefold()
    if not normalized:
        return None

    for rule in HIGH_CONFIDENCE_ROUTES:
        agent_id = rule["agent_id"]
        safe = active_catalog.get(agent_id)
        if not safe:
            continue
        all_groups = rule.get("all_groups", ())
        matches_groups = bool(all_groups) and all(
            any(term.casefold() in normalized for term in group)
            for group in all_groups
        )
        any_terms = rule.get("any_terms", ())
        matches_terms = bool(any_terms) and any(term.casefold() in normalized for term in any_terms)
        if not matches_groups and not matches_terms:
            continue
        return {
            "agent_id": safe["agent_id"],
            "name": safe["name"],
            "description": safe["description"],
            "reason": rule["reason"],
        }
    return None


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


async def _route_auto_request(
    model_service: ModelProfileService,
    *,
    user_id: str,
    request_id: str,
    messages: list[ModelMessage],
    active_catalog: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    from .db import database

    if not active_catalog:
        return None
    deterministic = _high_confidence_recommendation(messages, active_catalog)
    if deterministic:
        return deterministic
    try:
        with database(model_service.settings.database_path) as route_conn:
            result = await call_user_global_model(
                route_conn,
                model_service,
                user_id=user_id,
                request_id=f"{request_id}-route",
                instructions=_route_instructions(load_agent_routing_skill(), active_catalog),
                messages=messages,
                max_output_tokens=300,
            )
    except (HTTPException, sqlite3.Error):
        # Routing is advisory. A routing failure must not discard a valid direct answer.
        return None

    routed = _parse_route_output(result.output_text)
    if not routed.recommend or routed.agent_id not in active_catalog:
        return None
    safe = active_catalog[routed.agent_id]
    return {
        "agent_id": safe["agent_id"],
        "name": safe["name"],
        "description": safe["description"],
        "reason": routed.reason or "这个 Agent 与当前需求最匹配。",
    }


async def _stream_auto_response(
    model_service: ModelProfileService,
    *,
    user_id: str,
    request_id: str,
    messages: list[ModelMessage],
    active_catalog: dict[str, dict[str, Any]],
) -> StreamingResponse:
    async def iterator():
        from .db import database

        try:
            recommendation = None
            if _should_attempt_route(messages, active_catalog):
                try:
                    recommendation = await asyncio.wait_for(
                        _route_auto_request(
                            model_service,
                            user_id=user_id,
                            request_id=request_id,
                            messages=messages,
                            active_catalog=active_catalog,
                        ),
                        timeout=5,
                    )
                except Exception:
                    recommendation = None
            if recommendation:
                yield _home_sse("home.recommendation", {"recommendation": recommendation})
                yield _home_sse("home.completed", {})
                return

            try:
                with database(model_service.settings.database_path) as answer_conn:
                    answer_response = await stream_user_global_model(
                        answer_conn,
                        model_service,
                        user_id=user_id,
                        request_id=request_id,
                        instructions=INSTANT_INSTRUCTIONS,
                        messages=messages,
                        max_output_tokens=900,
                    )
            except HTTPException as exc:
                yield _safe_model_error_event(_error_code(exc))
                return

            async for chunk in answer_response.body_iterator:
                yield chunk
            yield _home_sse("home.recommendation", {"recommendation": None})
            yield _home_sse("home.completed", {})
        except asyncio.CancelledError:
            raise

    return StreamingResponse(
        iterator(),
        media_type="text/event-stream",
        headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
    )


async def _stream_direct_response(
    model_service: ModelProfileService,
    *,
    user_id: str,
    request_id: str,
    messages: list[ModelMessage],
) -> StreamingResponse:
    async def iterator():
        from .db import database

        try:
            with database(model_service.settings.database_path) as answer_conn:
                answer_response = await stream_user_global_model(
                    answer_conn,
                    model_service,
                    user_id=user_id,
                    request_id=request_id,
                    instructions=INSTANT_INSTRUCTIONS,
                    messages=messages,
                    max_output_tokens=900,
                )
        except HTTPException as exc:
            yield _safe_model_error_event(_error_code(exc))
            return

        try:
            async for chunk in answer_response.body_iterator:
                yield chunk
            yield _home_sse("home.completed", {})
        except asyncio.CancelledError:
            raise

    return StreamingResponse(
        iterator(),
        media_type="text/event-stream",
        headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
    )


async def _stream_route_response(
    model_service: ModelProfileService,
    *,
    user_id: str,
    request_id: str,
    messages: list[ModelMessage],
    active_catalog: dict[str, dict[str, Any]],
) -> StreamingResponse:
    async def iterator():
        recommendation = None
        if active_catalog:
            try:
                recommendation = await asyncio.wait_for(
                    _route_auto_request(
                        model_service,
                        user_id=user_id,
                        request_id=request_id,
                        messages=messages,
                        active_catalog=active_catalog,
                    ),
                    timeout=8,
                )
            except Exception:
                # Routing is advisory. Analysis failure falls back to the direct assistant.
                recommendation = None

        if recommendation:
            yield _home_sse("home.recommendation", {"recommendation": recommendation})
            yield _home_sse("home.completed", {})
            return

        from .db import database

        try:
            with database(model_service.settings.database_path) as answer_conn:
                answer_response = await stream_user_global_model(
                    answer_conn,
                    model_service,
                    user_id=user_id,
                    request_id=request_id,
                    instructions=INSTANT_INSTRUCTIONS,
                    messages=messages,
                    max_output_tokens=900,
                )
        except HTTPException as exc:
            yield _safe_model_error_event(_error_code(exc))
            return

        try:
            async for chunk in answer_response.body_iterator:
                yield chunk
            yield _home_sse("home.recommendation", {"recommendation": None})
            yield _home_sse("home.completed", {})
        except asyncio.CancelledError:
            raise

    return StreamingResponse(
        iterator(),
        media_type="text/event-stream",
        headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
    )


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
        return await _stream_direct_response(
            model_service,
            user_id=user["user_id"],
            request_id=request_id,
            messages=messages,
        )

    if body.mode == "route_stream":
        try:
            active_catalog = _active_catalog_by_id(conn)
        except HTTPException:
            active_catalog = {}
        return await _stream_route_response(
            model_service,
            user_id=user["user_id"],
            request_id=request_id,
            messages=messages,
            active_catalog=active_catalog,
        )

    if body.mode == "auto":
        active_catalog = _active_catalog_by_id(conn)
        return await _stream_auto_response(
            model_service,
            user_id=user["user_id"],
            request_id=request_id,
            messages=messages,
            active_catalog=active_catalog,
        )

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
