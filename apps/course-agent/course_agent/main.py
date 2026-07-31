from __future__ import annotations

import json
import tempfile
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from threading import BoundedSemaphore, Lock
from typing import Any, Iterator, Literal, Optional

import fitz
import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.sessions import SessionMiddleware

from .config import Settings
from .db import database, healthcheck, init_database
from .ingestion import (
    DocumentMetadata,
    DuplicateDocument,
    FTS5IndexWriter,
    IngestionError,
    PyMuPDFParser,
    SentenceChunking,
    delete_document,
    document_details,
    ingest_pdf,
    reparse_document,
)
from .llm import LLMAdapter
from .model_catalog import (
    ModelCatalog,
    ModelCatalogError,
    SUPPORTED_REASONING_EFFORTS,
    invalidate_model_catalog,
    validate_base_url_for_saved_config,
)
from .retrieval import FTS5SearchBackend, SearchResult, accessible_document_ids, search
from .tokenizer import JiebaTokenizer


OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
USTC_LATITUDE = 31.8206
USTC_LONGITUDE = 117.2272
WEATHER_REQUEST_TIMEOUT = httpx.Timeout(8.0, connect=3.0)
OPEN_METEO_WEATHER_PARAMS: dict[str, str | float | int] = {
    "latitude": USTC_LATITUDE,
    "longitude": USTC_LONGITUDE,
    "current": (
        "temperature_2m,relative_humidity_2m,apparent_temperature,"
        "wind_speed_10m,wind_direction_10m"
    ),
    "daily": (
        "weather_code,temperature_2m_max,temperature_2m_min,"
        "precipitation_probability_max,sunrise,sunset"
    ),
    "temperature_unit": "celsius",
    "wind_speed_unit": "kmh",
    "precipitation_unit": "mm",
    "timezone": "Asia/Shanghai",
    "forecast_days": 1,
}

_WMO_WEATHER_DESCRIPTIONS = {
    0: "晴",
    1: "大部晴朗",
    2: "局部多云",
    3: "阴",
    45: "有雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "较强毛毛雨",
    56: "轻微冻毛毛雨",
    57: "冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "轻微冻雨",
    67: "冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "米雪",
    80: "小阵雨",
    81: "阵雨",
    82: "强阵雨",
    85: "小阵雪",
    86: "强阵雪",
    95: "雷暴",
    96: "雷暴伴小冰雹",
    99: "雷暴伴强冰雹",
}


class SessionRequest(BaseModel):
    user_id: str


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant", "system"]
    content: str = Field(min_length=1, max_length=20000)


class AssistantPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tone: Literal["friendly", "pragmatic"] = "friendly"
    detail: Literal["concise", "balanced", "detailed"] = "balanced"
    custom_instructions: str = Field(default="", max_length=2000)


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    model: Optional[str] = Field(default=None, max_length=200)
    reasoning_effort: Optional[Literal["low", "medium", "high", "xhigh", "max"]] = None
    mode: Literal["direct", "retrieval"] = "direct"
    document_ids: list[str] = Field(default_factory=list, max_length=100)
    top_k: int = Field(default=5, ge=1, le=8)
    messages: list[ChatMessage] = Field(default_factory=list, max_length=40)
    scope: Optional[Literal["general", "knowledge_base"]] = None
    space_id: Optional[str] = Field(default=None, max_length=100)
    assistant_preferences: AssistantPreferences = Field(default_factory=AssistantPreferences)


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm_base_url: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_model: Optional[str] = None
    llm_timeout_seconds: Optional[float] = Field(default=None, ge=5, le=300)
    search_backend: Optional[str] = None
    parser_backend: Optional[str] = None
    chunking_backend: Optional[str] = None
    tokenizer_backend: Optional[str] = None


def _error(code: str, message: str, retryable: bool = False) -> dict:
    return {"error": {"code": code, "message": message, "retryable": retryable}}


TONE_PROMPTS = {
    "friendly": "语气亲和、自然、有耐心，像可靠的同学或学长学姐；避免生硬的客服套话。",
    "pragmatic": "语气务实、专注、直接，优先给出可执行结论；避免寒暄和重复用户问题。",
}

DETAIL_PROMPTS = {
    "concise": "回答尽量简短，只保留结论、必要步骤和关键限制。",
    "balanced": "回答保持适中篇幅，先给结论，再补充足够的解释和步骤。",
    "detailed": "回答尽可能完整，展开背景、步骤、例子、边界条件和容易出错之处，但避免无关重复。",
}


def _assistant_identity_prompt() -> str:
    return (
        "你是「瀚海行 Agent」，由 AI for better life In ustc 团队为中国科学技术大学学生打造的"
        "校园学习与生活助手。你可以帮助用户学习课程、整理知识、制定复习计划，并处理一般问题。"
        "当用户询问你的身份时，应明确介绍自己是瀚海行 Agent；当前配置的大语言模型为你提供推理和表达能力，"
        "但不要把自己仅描述为一个通过 API 提供服务的通用助手。"
    )


def _assistant_preference_prompt(preferences: AssistantPreferences) -> str:
    sections = [
        "请遵循以下用户回答偏好：",
        f"- 语气：{TONE_PROMPTS[preferences.tone]}",
        f"- 详略：{DETAIL_PROMPTS[preferences.detail]}",
    ]
    return "\n".join(sections)


def _assistant_custom_preference(preferences: AssistantPreferences) -> str | None:
    custom = preferences.custom_instructions.strip()
    return custom or None


def _frontend_output_prompt() -> str:
    return (
        "前端输出格式约束：回答会由受限 Markdown 与 KaTeX 渲染器展示。"
        "可以使用二至四级标题、普通段落、粗体、引用、有序或无序列表、标准 Markdown 表格和 LaTeX 公式。"
        "需要表达日程、对比或多列结构时，优先使用标准 Markdown 表格，格式必须严格如下：\n"
        "| 时间 | 内容 | 目标 |\n"
        "| --- | --- | --- |\n"
        "| 第 1 天 | 示例内容 | 示例目标 |\n"
        "表头、分隔线和数据行之间不得插入空行，每一行的列数必须一致。"
        "表格单元格内不要直接使用竖线；数学绝对值应写为 LaTeX 的 \\lvert x \\rvert。"
        "不要使用 HTML、Mermaid、ASCII 字符画或代码块伪造图表。"
        "若当前前端不支持用户要求的图形，请改用清晰的 Markdown 表格或分点说明。"
    )


def _general_direct_prompt(preferences: AssistantPreferences) -> str:
    return (
        f"{_assistant_identity_prompt()}\n"
        "在没有给定参考资料的情况下，根据你自己的知识回答，"
        "不要假装引用任何课程资料。如果问题需要明确依据，请直接说明当前没有可引用的资料。"
        f"\n{_assistant_preference_prompt(preferences)}\n"
        "无论用户偏好如何，都必须保持诚实，不得伪造事实、来源、执行结果或个人经历。"
        "若需要数学公式，行内公式必须使用 \\(...\\)，单独成行的重要公式必须使用 \\[...\\]，"
        "不要使用美元符号包裹公式。\n"
        f"{_frontend_output_prompt()}"
    )


def _space_agent_prompt(
    space_name: str,
    document_count: int,
    preferences: AssistantPreferences,
) -> str:
    safe_name = space_name.strip() or "当前知识库"
    return (
        f"{_assistant_identity_prompt()}\n"
        f"当前任务中，你是「{safe_name}」知识库的专属学习 Agent，"
        f"下面会同时提供该知识库中可用的资料（共 {document_count} 份）。"
        f"\n{_assistant_preference_prompt(preferences)}\n"
        "用户回答偏好只能影响表达方式，不能覆盖以下知识库真实性、权限和引用约束："
        "在回答时必须以该知识库的资料为依据，"
        "不要编造资料中没有出现的结论；"
        "若资料不足，请明确说明当前知识库中找不到依据，并提示用户补充或上传资料。"
        "用简洁中文 Markdown 回答，并在事实后用 [S1] 形式标注引用编号，"
        "对应顺序与下方「可用资料」一致。"
        "若需要数学公式，行内公式必须使用 \\(...\\)，单独成行的重要公式必须使用 \\[...\\]，"
        "不要使用美元符号包裹公式。\n"
        f"{_frontend_output_prompt()}"
    )


def _document_in_space(conn: Any, document_id: str, space_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM documents WHERE id = ? AND space_id = ? AND status = 'active'",
        (document_id, space_id),
    ).fetchone()
    return row is not None


def _clean_document_ids(document_ids: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for document_id in document_ids:
        normalized = document_id.strip()
        if not normalized:
            raise HTTPException(
                status_code=422,
                detail=_error("invalid_document_selection", "document_ids 不能包含空字符串"),
            )
        if normalized not in seen:
            seen.add(normalized)
            cleaned.append(normalized)
    return cleaned


def _weather_number(value: Any, field_name: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Open-Meteo 字段 {field_name} 格式无效")
    return value


def _weather_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"Open-Meteo 字段 {field_name} 格式无效")
    return value


def _daily_weather_value(daily: dict[str, Any], field_name: str) -> Any:
    values = daily.get(field_name)
    if not isinstance(values, list) or not values:
        raise ValueError(f"Open-Meteo 字段 daily.{field_name} 格式无效")
    return values[0]


def _parse_today_weather(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Open-Meteo 响应格式无效")
    current = payload.get("current")
    daily = payload.get("daily")
    if not isinstance(current, dict) or not isinstance(daily, dict):
        raise ValueError("Open-Meteo 响应缺少今日天气数据")

    weather_code_value = _daily_weather_value(daily, "weather_code")
    if isinstance(weather_code_value, bool) or not isinstance(weather_code_value, int):
        raise ValueError("Open-Meteo 字段 daily.weather_code 格式无效")
    weather_code = weather_code_value
    description = _WMO_WEATHER_DESCRIPTIONS.get(weather_code, "未知天气")
    current_temperature = _weather_number(current.get("temperature_2m"), "current.temperature_2m")
    apparent_temperature = _weather_number(current.get("apparent_temperature"), "current.apparent_temperature")
    minimum_temperature = _weather_number(
        _daily_weather_value(daily, "temperature_2m_min"),
        "daily.temperature_2m_min",
    )
    maximum_temperature = _weather_number(
        _daily_weather_value(daily, "temperature_2m_max"),
        "daily.temperature_2m_max",
    )

    return {
        "location": {
            "name": "中国科学技术大学",
            "city": "合肥",
            "latitude": USTC_LATITUDE,
            "longitude": USTC_LONGITUDE,
            "timezone": "Asia/Shanghai",
        },
        "date": _weather_text(_daily_weather_value(daily, "time"), "daily.time"),
        "weather": {"code": weather_code, "description": description},
        "temperature": {
            "current_c": current_temperature,
            "apparent_c": apparent_temperature,
            "min_c": minimum_temperature,
            "max_c": maximum_temperature,
        },
        "humidity_percent": _weather_number(
            current.get("relative_humidity_2m"),
            "current.relative_humidity_2m",
        ),
        "precipitation_probability_max_percent": _weather_number(
            _daily_weather_value(daily, "precipitation_probability_max"),
            "daily.precipitation_probability_max",
        ),
        "wind": {
            "speed_kmh": _weather_number(current.get("wind_speed_10m"), "current.wind_speed_10m"),
            "direction_degrees": _weather_number(
                current.get("wind_direction_10m"),
                "current.wind_direction_10m",
            ),
        },
        "sunrise": _weather_text(_daily_weather_value(daily, "sunrise"), "daily.sunrise"),
        "sunset": _weather_text(_daily_weather_value(daily, "sunset"), "daily.sunset"),
        "updated_at": _weather_text(current.get("time"), "current.time"),
        "summary": (
            f"合肥今日{description}，{minimum_temperature}℃～{maximum_temperature}℃，"
            f"当前{current_temperature}℃"
        ),
    }


def create_app(settings: Settings | None = None, llm_adapter: LLMAdapter | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.ensure_directories()
    init_database(settings)
    app = FastAPI(title="瀚海行agent", version="0.6.0")
    app.state.settings = settings
    app.state.llm = llm_adapter or LLMAdapter(settings)
    app.state.model_catalog = ModelCatalog(settings)
    page_image_cache: OrderedDict[tuple[str, int, int, int], bytes] = OrderedDict()
    page_image_cache_lock = Lock()
    page_image_render_slots = BoundedSemaphore(value=2)

    # ---- Pluggable components (swap via Settings in the future) ---------
    app.state.search_backend = FTS5SearchBackend()
    app.state.document_parser = PyMuPDFParser()
    app.state.chunking_strategy = SentenceChunking()
    app.state.index_writer = FTS5IndexWriter()
    app.state.tokenizer = JiebaTokenizer()
    # Future upgrades: replace any of the above with alternative
    # implementations that satisfy their respective protocols from .types
    # --------------------------------------------------------------------
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="course_agent_session",
        https_only=False,
        same_site="lax",
    )

    static_dir = Path(__file__).resolve().parent / "web"
    if static_dir.exists():
        app.mount("/assets", StaticFiles(directory=static_dir), name="assets")

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, dict) else {
            "code": "request_failed",
            "message": str(exc.detail),
            "retryable": False,
        }
        if "error" in detail:
            return JSONResponse(status_code=exc.status_code, content=detail)
        return JSONResponse(status_code=exc.status_code, content={"error": detail})

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_error("validation_error", str(exc.errors()), False),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
        # Ensure any unexpected server error is returned as JSON (not an HTML
        # traceback) so the frontend can surface a readable message.
        return JSONResponse(
            status_code=500,
            content=_error("internal_error", "服务器内部错误，请稍后重试", False),
        )

    @contextmanager
    def get_db() -> Iterator[Any]:
        with database(settings) as conn:
            yield conn

    def current_user(request: Request) -> str:
        user_id = request.session.get("user_id")
        if not user_id:
            raise HTTPException(status_code=401, detail=_error("not_authenticated", "请先选择演示身份"))
        return str(user_id)

    def require_admin(user_id: str) -> None:
        if user_id not in (app.state.settings.admin_user_ids or set()):
            raise HTTPException(status_code=403, detail=_error("admin_required", "需要管理员权限"))

    def catalog_error(exc: ModelCatalogError, status_code: int = 422) -> HTTPException:
        return HTTPException(
            status_code=status_code,
            detail=_error(exc.code, exc.message, exc.retryable),
        )

    def validate_query_model(payload: QueryRequest) -> tuple[str, str | None]:
        selected = (payload.model or app.state.settings.llm_model or "").strip()
        try:
            model_info = app.state.model_catalog.model_for_query(selected)
            app.state.model_catalog.validate_reasoning(model_info, payload.reasoning_effort)
        except ModelCatalogError as exc:
            raise catalog_error(exc, 422) from exc
        return model_info.id, payload.reasoning_effort

    def settings_response(user_id: str) -> dict:
        return {
            **app.state.settings.to_safe_dict(),
            "is_admin": user_id in (app.state.settings.admin_user_ids or set()),
        }

    def require_space(conn: Any, user_id: str, space_id: str) -> Any:
        row = conn.execute(
            """SELECT s.*, m.role, m.status AS membership_status
               FROM spaces s JOIN memberships m ON m.space_id = s.id
               WHERE s.id = ? AND m.user_id = ? AND m.status = 'active'""",
            (space_id, user_id),
        ).fetchone()
        if not row:
            raise HTTPException(
                status_code=404,
                detail=_error("space_not_found", "知识空间不存在或当前用户不可访问"),
            )
        return row

    def require_document(conn: Any, user_id: str, document_id: str) -> Any:
        row = conn.execute(
            """SELECT d.*, m.role, r.id AS revision_id, r.page_count, r.parse_status,
                      r.searchable_pages, r.needs_ocr_pages, r.needs_review_pages, r.failed_pages
               FROM documents d
               JOIN memberships m ON m.space_id = d.space_id AND m.user_id = ?
                 AND m.status = 'active'
               LEFT JOIN revisions r ON r.document_id = d.id AND r.status = 'active'
               WHERE d.id = ? AND d.status = 'active'""",
            (user_id, document_id),
        ).fetchone()
        if not row:
            raise HTTPException(
                status_code=404,
                detail=_error("document_not_found", "资料不存在或当前用户不可访问"),
            )
        return row

    @app.get("/api/health")
    def api_health() -> dict:
        from . import __version__

        return {
            **healthcheck(settings),
            "llm_configured": settings.llm_configured,
            "version": __version__,
        }

    @app.get("/api/users")
    def users() -> dict:
        with get_db() as conn:
            rows = conn.execute("SELECT id, display_name, is_demo FROM users ORDER BY id").fetchall()
        return {"items": [dict(row) for row in rows]}

    @app.post("/api/session")
    def create_session(payload: SessionRequest, request: Request) -> dict:
        with get_db() as conn:
            row = conn.execute("SELECT id, display_name, is_demo FROM users WHERE id = ?", (payload.user_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=_error("user_not_found", "演示身份不存在"))
        request.session["user_id"] = row["id"]
        return {"user": dict(row)}

    @app.get("/api/session")
    def get_session(request: Request) -> dict:
        user_id = request.session.get("user_id")
        if not user_id:
            return {"user": None}
        with get_db() as conn:
            row = conn.execute("SELECT id, display_name, is_demo FROM users WHERE id = ?", (user_id,)).fetchone()
        return {"user": dict(row) if row else None}

    @app.delete("/api/session", status_code=204)
    def clear_session(request: Request) -> None:
        request.session.clear()

    @app.get("/api/weather/today")
    async def today_weather(_user_id: str = Depends(current_user)) -> dict:
        try:
            async with httpx.AsyncClient(
                timeout=WEATHER_REQUEST_TIMEOUT,
                follow_redirects=False,
            ) as client:
                response = await client.get(
                    OPEN_METEO_FORECAST_URL,
                    params=OPEN_METEO_WEATHER_PARAMS,
                )
                response.raise_for_status()
                return _parse_today_weather(response.json())
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=502,
                detail=_error(
                    "weather_upstream_unavailable",
                    "天气服务暂时不可用，请稍后重试",
                    True,
                ),
            ) from exc

    @app.get("/api/spaces")
    def spaces(user_id: str = Depends(current_user)) -> dict:
        with get_db() as conn:
            rows = conn.execute(
                """SELECT s.id, s.name, s.space_type, s.owner_id, s.visibility, m.role,
                          (SELECT count(*) FROM documents d WHERE d.space_id = s.id AND d.status = 'active') AS document_count
                   FROM spaces s JOIN memberships m ON m.space_id = s.id
                   WHERE m.user_id = ? AND m.status = 'active' ORDER BY s.space_type, s.name""",
                (user_id,),
            ).fetchall()
        return {"items": [dict(row) for row in rows]}

    @app.get("/api/spaces/{space_id}/documents")
    def documents(space_id: str, page: int = 1, page_size: int = 20, user_id: str = Depends(current_user)) -> dict:
        if page < 1 or not 1 <= page_size <= 100:
            raise HTTPException(status_code=422, detail=_error("invalid_pagination", "页码或每页数量无效"))
        with get_db() as conn:
            require_space(conn, user_id, space_id)
            total = conn.execute(
                "SELECT count(*) AS count FROM documents WHERE space_id = ? AND status = 'active'",
                (space_id,),
            ).fetchone()["count"]
            rows = conn.execute(
                """SELECT d.id, d.title, d.course, d.semester, d.material_type, d.created_at,
                          r.page_count, r.searchable_pages, r.needs_ocr_pages, r.needs_review_pages,
                          r.failed_pages, r.parse_status, s.license_status, s.source_url
                   FROM documents d JOIN revisions r ON r.document_id = d.id AND r.status = 'active'
                   JOIN sources s ON s.id = d.source_id
                   WHERE d.space_id = ? AND d.status = 'active'
                   ORDER BY d.created_at DESC LIMIT ? OFFSET ?""",
                (space_id, page_size, (page - 1) * page_size),
            ).fetchall()
        return {"items": [dict(row) for row in rows], "page": page, "page_size": page_size, "total": total}

    @app.post("/api/spaces/{space_id}/documents")
    async def upload_document(
        space_id: str,
        file: UploadFile = File(...),
        title: str = Form(...),
        material_type: str = Form("课程资料"),
        license_status: str = Form("private-team-use"),
        semester: Optional[str] = Form(None),
        source_url: Optional[str] = Form(None),
        user_id: str = Depends(current_user),
    ) -> dict:
        with get_db() as conn:
            space = require_space(conn, user_id, space_id)
            if space["role"] == "reader":
                raise HTTPException(status_code=403, detail=_error("write_forbidden", "当前成员只有阅读权限"))
        suffix = Path(file.filename or "upload.pdf").suffix.lower()
        if suffix != ".pdf":
            raise HTTPException(status_code=422, detail=_error("invalid_file_type", "只支持 PDF"))
        temp_path = settings.temp_dir / f"{uuid.uuid4()}.pdf"
        size = 0
        try:
            with temp_path.open("wb") as output:
                while block := await file.read(1024 * 1024):
                    size += len(block)
                    if size > settings.max_upload_bytes:
                        raise HTTPException(status_code=422, detail=_error("file_too_large", "文件超过 50 MiB"))
                    output.write(block)
            metadata = DocumentMetadata(
                title=title.strip() or Path(file.filename or "资料.pdf").stem,
                material_type=material_type.strip() or "课程资料",
                license_status=license_status.strip() or "private-team-use",
                semester=semester,
                source_url=source_url or None,
            )
            with get_db() as conn:
                try:
                    result = ingest_pdf(conn, settings, temp_path, space_id, metadata, copy_to_uploads=True)
                except DuplicateDocument as exc:
                    raise HTTPException(
                        status_code=409,
                        detail=_error("duplicate_document", f"资料已存在：{exc.document_id}"),
                    ) from exc
                except IngestionError as exc:
                    raise HTTPException(status_code=422, detail=_error("ingestion_failed", str(exc))) from exc
            return {"document": result}
        finally:
            temp_path.unlink(missing_ok=True)

    @app.delete("/api/documents/{document_id}", status_code=204)
    def remove_document(document_id: str, user_id: str = Depends(current_user)) -> None:
        with get_db() as conn:
            row = require_document(conn, user_id, document_id)
            if row["role"] == "reader":
                raise HTTPException(status_code=403, detail=_error("write_forbidden", "当前成员没有删除权限"))
            try:
                delete_document(conn, document_id)
            except IngestionError as exc:
                raise HTTPException(status_code=404, detail=_error("document_not_found", str(exc))) from exc

    @app.post("/api/documents/{document_id}/reparse")
    def reparse(document_id: str, user_id: str = Depends(current_user)) -> dict:
        with get_db() as conn:
            row = require_document(conn, user_id, document_id)
            if row["role"] == "reader":
                raise HTTPException(status_code=403, detail=_error("write_forbidden", "当前成员没有重新解析权限"))
            try:
                result = reparse_document(conn, settings, document_id)
            except IngestionError as exc:
                raise HTTPException(status_code=422, detail=_error("reparse_failed", str(exc))) from exc
        return {"document": result}

    @app.get("/api/documents/{document_id}/pages/{page_number}")
    def document_page(document_id: str, page_number: int, user_id: str = Depends(current_user)) -> dict:
        if page_number < 1:
            raise HTTPException(status_code=404, detail=_error("page_not_found", "页面不存在"))
        with get_db() as conn:
            row = require_document(conn, user_id, document_id)
            page = conn.execute(
                """SELECT p.page_number, p.status, p.content, d.title
                   FROM pages p JOIN revisions r ON r.id = p.revision_id AND r.status = 'active'
                   JOIN documents d ON d.id = r.document_id
                   WHERE d.id = ? AND p.page_number = ?""",
                (document_id, page_number),
            ).fetchone()
        if not page:
            raise HTTPException(status_code=404, detail=_error("page_not_found", "页面不存在"))
        result = dict(page)
        result["page_count"] = int(row["page_count"] or 0)
        return result

    @app.get("/api/documents/{document_id}/file")
    def document_file(document_id: str, user_id: str = Depends(current_user)) -> FileResponse:
        with get_db() as conn:
            row = require_document(conn, user_id, document_id)
        file_path = Path(row["file_path"])
        if not file_path.is_file():
            raise HTTPException(
                status_code=404,
                detail=_error("document_file_not_found", "资料文件不存在"),
            )
        title = str(row["title"]).strip() or document_id
        suffix = file_path.suffix or ".pdf"
        filename = title if title.lower().endswith(suffix.lower()) else f"{title}{suffix}"
        return FileResponse(
            file_path,
            media_type="application/pdf",
            filename=filename,
            content_disposition_type="inline",
            headers={"Cache-Control": "private, no-store"},
        )

    @app.get("/api/documents/{document_id}/pages/{page_number}/image")
    def document_page_image(
        document_id: str,
        page_number: int,
        user_id: str = Depends(current_user),
    ) -> Response:
        if page_number < 1:
            raise HTTPException(status_code=404, detail=_error("page_not_found", "页面不存在"))
        with get_db() as conn:
            row = require_document(conn, user_id, document_id)
        file_path = Path(row["file_path"])
        if not file_path.is_file():
            raise HTTPException(
                status_code=404,
                detail=_error("document_file_not_found", "资料文件不存在"),
            )
        file_stat = file_path.stat()
        cache_key = (str(file_path), file_stat.st_mtime_ns, file_stat.st_size, page_number)
        with page_image_cache_lock:
            image = page_image_cache.get(cache_key)
            if image is not None:
                page_image_cache.move_to_end(cache_key)
        if image is None:
            if not page_image_render_slots.acquire(timeout=2.0):
                raise HTTPException(
                    status_code=429,
                    detail=_error("page_render_busy", "原始页面渲染繁忙，请稍后重试"),
                )
            try:
                with fitz.open(file_path) as document:
                    if page_number > document.page_count:
                        raise HTTPException(status_code=404, detail=_error("page_not_found", "页面不存在"))
                    page = document.load_page(page_number - 1)
                    max_dimension = max(float(page.rect.width), float(page.rect.height), 1.0)
                    scale = min(1.5, 1800.0 / max_dimension)
                    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                    image = pixmap.tobytes("png")
            finally:
                page_image_render_slots.release()
            with page_image_cache_lock:
                page_image_cache[cache_key] = image
                page_image_cache.move_to_end(cache_key)
                while len(page_image_cache) > 8:
                    page_image_cache.popitem(last=False)
        return Response(
            content=image,
            media_type="image/png",
            headers={"Cache-Control": "private, no-store"},
        )

    @app.get("/api/settings")
    def get_settings(user_id: str = Depends(current_user)) -> dict:
        return settings_response(user_id)

    @app.post("/api/settings")
    def update_settings(payload: SettingsUpdate, user_id: str = Depends(current_user)) -> dict:
        require_admin(user_id)
        incoming = payload.model_dump(exclude_unset=True)
        old_base_url = app.state.settings.llm_base_url
        new_base_url = str(incoming.get("llm_base_url", old_base_url) or "").strip()
        base_changed = bool(new_base_url) and new_base_url.rstrip("/") != old_base_url.rstrip("/")
        if base_changed and not incoming.get("llm_api_key"):
            raise HTTPException(
                status_code=422,
                detail=_error(
                    "llm_api_key_required_for_base_url_change",
                    "Base URL 变化时必须同时提供该服务的新 API key",
                ),
            )
        if "llm_base_url" in incoming and new_base_url:
            try:
                incoming["llm_base_url"] = validate_base_url_for_saved_config(
                    app.state.settings,
                    new_base_url,
                )
            except ModelCatalogError as exc:
                raise catalog_error(exc, 422) from exc
        try:
            app.state.settings.update_from_dict(incoming)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=_error("bad_settings", str(exc)))
        try:
            app.state.settings.save()
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=_error("settings_save_failed", f"无法写入配置文件：{exc}"),
            )
        app.state.settings.llm_config_generation += 1
        invalidate_model_catalog()
        app.state.llm = LLMAdapter(app.state.settings)
        app.state.model_catalog = ModelCatalog(app.state.settings)
        return settings_response(user_id)

    @app.get("/api/models")
    def get_models(user_id: str = Depends(current_user)) -> dict:
        cached = app.state.model_catalog.get_cached()
        if cached:
            return cached.as_dict()
        try:
            default_info = app.state.model_catalog.model_for_query(app.state.settings.llm_model)
        except ModelCatalogError as exc:
            raise catalog_error(exc, 422) from exc
        return {
            "models": [default_info.as_dict()],
            "discovery_source": None,
            "cached": False,
            "reasoning_efforts": list(SUPPORTED_REASONING_EFFORTS),
        }

    @app.post("/api/models/discover")
    def discover_models(user_id: str = Depends(current_user)) -> dict:
        require_admin(user_id)
        try:
            return app.state.model_catalog.discover(force=True).as_dict()
        except ModelCatalogError as exc:
            raise catalog_error(exc, 502 if exc.retryable else 422) from exc

    @app.post("/api/settings/test")
    def test_settings(user_id: str = Depends(current_user)) -> dict:
        require_admin(user_id)
        adapter = LLMAdapter(app.state.settings)
        result = adapter.generate_direct("你好，请回复“配置测试成功”即可。")
        return {
            "ok": not result.degraded,
            "model": result.model,
            "degraded": result.degraded,
            "model_error": (
                {"code": result.error_code, "message": result.error_message}
                if result.error_code
                else None
            ),
        }

    @app.post("/api/query")
    def query(payload: QueryRequest, user_id: str = Depends(current_user)) -> dict:
        history = [m.model_dump() for m in payload.messages]
        selected_model, selected_reasoning = validate_query_model(payload)
        scope = payload.scope or ("knowledge_base" if payload.mode == "retrieval" else "general")
        if payload.mode == "direct":
            if scope != "general":
                raise HTTPException(
                    status_code=422,
                    detail=_error("invalid_scope", "direct 模式仅适用于通用模型，请进入知识库后提问"),
                )
            system = _general_direct_prompt(payload.assistant_preferences)
            llm_result = app.state.llm.generate_direct(
                payload.question,
                history=history,
                system=system,
                preference_context=_assistant_custom_preference(payload.assistant_preferences),
                model=selected_model,
                reasoning_effort=selected_reasoning,
            )
            return {
                "answer": llm_result.answer,
                "mode": "direct",
                "scope": "general",
                "degraded": llm_result.degraded,
                "model": llm_result.model,
                "usage": llm_result.usage.as_dict() if llm_result.usage else None,
                "retrieval_count": 0,
                "citations": [],
                "model_error": (
                    {"code": llm_result.error_code, "message": llm_result.error_message}
                    if llm_result.error_code
                    else None
                ),
            }

        if scope != "knowledge_base":
            raise HTTPException(
                status_code=422,
                detail=_error("invalid_scope", "知识库提问必须进入对应知识空间后发起"),
            )
        if not payload.space_id:
            raise HTTPException(
                status_code=422,
                detail=_error("missing_space_id", "请先选择一个知识库空间再提问"),
            )

        document_ids = _clean_document_ids(payload.document_ids)
        if not document_ids:
            raise HTTPException(
                status_code=422,
                detail=_error("missing_document_ids", "知识库提问必须基于至少一份资料"),
            )
        with get_db() as conn:
            space = require_space(conn, user_id, payload.space_id)
            allowed_documents = accessible_document_ids(conn, user_id, document_ids)
            if set(document_ids) != allowed_documents:
                raise HTTPException(
                    status_code=404,
                    detail=_error("document_not_found", "资料不存在、已删除或当前用户不可访问"),
                )
            if not all(_document_in_space(conn, doc_id, payload.space_id) for doc_id in document_ids):
                raise HTTPException(
                    status_code=404,
                    detail=_error("document_not_in_space", "所选资料不属于当前知识库"),
                )
            results = search(conn, user_id, payload.question, document_ids, payload.top_k)
        system = _space_agent_prompt(
            space["name"], len(allowed_documents), payload.assistant_preferences
        )
        llm_result = app.state.llm.generate(
            payload.question,
            results,
            history=history,
            system=system,
            preference_context=_assistant_custom_preference(payload.assistant_preferences),
            model=selected_model,
            reasoning_effort=selected_reasoning,
        )
        source_map = {item.citation_id: item.as_dict() for item in results}
        citation_ids = llm_result.citation_ids or ([] if not llm_result.degraded else list(source_map))
        citations = [source_map[item] for item in citation_ids if item in source_map]
        return {
            "answer": llm_result.answer,
            "mode": "retrieval",
            "scope": "knowledge_base",
            "space_id": payload.space_id,
            "degraded": llm_result.degraded,
            "model": llm_result.model,
            "usage": llm_result.usage.as_dict() if llm_result.usage else None,
            "retrieval_count": len(results),
            "citations": citations,
            "model_error": (
                {"code": llm_result.error_code, "message": llm_result.error_message}
                if llm_result.error_code
                else None
            ),
        }

    @app.get("/", response_class=FileResponse)
    def index() -> Path:
        return static_dir / "index.html"

    return app


app = create_app()
