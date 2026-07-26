from __future__ import annotations

import json
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Literal, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
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
from .retrieval import FTS5SearchBackend, SearchResult, accessible_document_ids, search
from .tokenizer import JiebaTokenizer


class SessionRequest(BaseModel):
    user_id: str


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant", "system"]
    content: str = Field(min_length=1, max_length=20000)


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    mode: Literal["direct", "retrieval"] = "direct"
    document_ids: list[str] = Field(default_factory=list, max_length=100)
    top_k: int = Field(default=5, ge=1, le=8)
    messages: list[ChatMessage] = Field(default_factory=list, max_length=40)
    scope: Optional[Literal["general", "knowledge_base"]] = None
    space_id: Optional[str] = Field(default=None, max_length=100)


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


def _general_direct_prompt() -> str:
    return (
        "你是一个通用大模型助手，可以回答任何学科的一般问题。"
        "在没有给定参考资料的情况下，根据你自己的知识回答，"
        "不要假装引用任何课程资料。如果问题需要明确依据，请直接说明当前没有可引用的资料。"
        "若需要数学公式，行内公式必须使用 \\(...\\)，单独成行的重要公式必须使用 \\[...\\]，"
        "不要使用美元符号包裹公式。"
    )


def _space_agent_prompt(space_name: str, document_count: int) -> str:
    safe_name = space_name.strip() or "当前知识库"
    return (
        f"你是「{safe_name}」知识库的专属 Agent，下面会同时提供该知识库中可用的资料（共 {document_count} 份）。"
        "在回答时必须以该知识库的资料为依据，"
        "不要编造资料中没有出现的结论；"
        "若资料不足，请明确说明当前知识库中找不到依据，并提示用户补充或上传资料。"
        "用简洁中文 Markdown 回答，并在事实后用 [S1] 形式标注引用编号，"
        "对应顺序与下方「可用资料」一致。"
        "若需要数学公式，行内公式必须使用 \\(...\\)，单独成行的重要公式必须使用 \\[...\\]，"
        "不要使用美元符号包裹公式。"
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


def create_app(settings: Settings | None = None, llm_adapter: LLMAdapter | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.ensure_directories()
    init_database(settings)
    app = FastAPI(title="USTC Course Agent", version="0.6.0")
    app.state.settings = settings
    app.state.llm = llm_adapter or LLMAdapter(settings)

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
            content=_error("internal_error", str(exc) or exc.__class__.__name__, False),
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
        return dict(page)

    @app.get("/api/settings")
    def get_settings(user_id: str = Depends(current_user)) -> dict:
        return app.state.settings.to_safe_dict()

    @app.post("/api/settings")
    def update_settings(payload: SettingsUpdate, user_id: str = Depends(current_user)) -> dict:
        try:
            app.state.settings.update_from_dict(payload.model_dump(exclude_unset=True))
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=_error("bad_settings", str(exc)))
        try:
            app.state.settings.save()
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=_error("settings_save_failed", f"无法写入配置文件：{exc}"),
            )
        app.state.llm = LLMAdapter(app.state.settings)
        return app.state.settings.to_safe_dict()

    @app.post("/api/settings/test")
    def test_settings(user_id: str = Depends(current_user)) -> dict:
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
        scope = payload.scope or ("knowledge_base" if payload.mode == "retrieval" else "general")
        if payload.mode == "direct":
            if scope != "general":
                raise HTTPException(
                    status_code=422,
                    detail=_error("invalid_scope", "direct 模式仅适用于通用模型，请进入知识库后提问"),
                )
            system = _general_direct_prompt()
            llm_result = app.state.llm.generate_direct(
                payload.question, history=history, system=system
            )
            return {
                "answer": llm_result.answer,
                "mode": "direct",
                "scope": "general",
                "degraded": llm_result.degraded,
                "model": llm_result.model,
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
        system = _space_agent_prompt(space["name"], len(allowed_documents))
        llm_result = app.state.llm.generate(
            payload.question, results, history=history, system=system
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
