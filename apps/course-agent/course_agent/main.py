from __future__ import annotations

import json
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Literal

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
    IngestionError,
    delete_document,
    document_details,
    ingest_pdf,
    reparse_document,
)
from .llm import LLMAdapter
from .retrieval import accessible_document_ids, search


class SessionRequest(BaseModel):
    user_id: str


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    mode: Literal["direct", "retrieval"] = "direct"
    document_ids: list[str] = Field(default_factory=list, max_length=100)
    top_k: int = Field(default=5, ge=1, le=8)


def _error(code: str, message: str, retryable: bool = False) -> dict:
    return {"error": {"code": code, "message": message, "retryable": retryable}}


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
    app = FastAPI(title="USTC Course Agent", version="0.1.0")
    app.state.settings = settings
    app.state.llm = llm_adapter or LLMAdapter(settings)
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
        return {**healthcheck(settings), "llm_configured": settings.llm_configured}

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
        semester: str | None = Form(None),
        source_url: str | None = Form(None),
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

    @app.post("/api/query")
    def query(payload: QueryRequest, user_id: str = Depends(current_user)) -> dict:
        if payload.mode == "direct":
            llm_result = app.state.llm.generate_direct(payload.question)
            return {
                "answer": llm_result.answer,
                "mode": "direct",
                "degraded": llm_result.degraded,
                "model": llm_result.model,
                "retrieval_count": 0,
                "citations": [],
                "model_error": {"code": llm_result.error_code} if llm_result.error_code else None,
            }

        document_ids = _clean_document_ids(payload.document_ids)
        if not document_ids:
            raise HTTPException(
                status_code=422,
                detail=_error("missing_document_ids", "retrieval 模式必须选择至少一个文档"),
            )
        with get_db() as conn:
            allowed_documents = accessible_document_ids(conn, user_id, document_ids)
            if set(document_ids) != allowed_documents:
                raise HTTPException(
                    status_code=404,
                    detail=_error("document_not_found", "资料不存在、已删除或当前用户不可访问"),
                )
            results = search(conn, user_id, payload.question, document_ids, payload.top_k)
        llm_result = app.state.llm.generate(payload.question, results)
        source_map = {item.citation_id: item.as_dict() for item in results}
        citation_ids = llm_result.citation_ids or ([] if not llm_result.degraded else list(source_map))
        citations = [source_map[item] for item in citation_ids if item in source_map]
        return {
            "answer": llm_result.answer,
            "mode": "retrieval",
            "degraded": llm_result.degraded,
            "model": llm_result.model,
            "retrieval_count": len(results),
            "citations": citations,
            "model_error": {"code": llm_result.error_code} if llm_result.error_code else None,
        }

    @app.get("/", response_class=FileResponse)
    def index() -> Path:
        return static_dir / "index.html"

    return app


app = create_app()
