from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import uvicorn
from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .audit import list_audit, record_audit
from .config import DEMO_USERS, Settings
from .db import database, init_db
from .gateway import gateway_stream
from .health import check_agent_health
from .identity import (
    IdentityService,
    authenticate_client_secret_basic,
    consume_auth_code,
    create_agent_credential,
    create_auth_code,
)
from .registry import (
    get_active_version,
    get_agent,
    list_agents,
    restore_agent,
    review_version,
    rollback_agent,
    submit_manifest,
    suspend_agent,
)
from .schemas import ReviewRequest, RollbackRequest, StatusChangeRequest, WorkspaceStartRequest


def create_app(settings: Settings | None = None, identity: IdentityService | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    init_db(settings.database_path)
    identity = identity or IdentityService(settings)

    app = FastAPI(title="Campus Agent Hub", version="0.1.0")
    app.state.settings = settings
    app.state.identity = identity
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_allow_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    web_root = Path(__file__).resolve().parents[1] / "web"
    app.mount("/assets", StaticFiles(directory=web_root / "assets"), name="hub-assets")

    def current_user(request: Request) -> dict[str, str]:
        if not settings.demo_mode:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                detail={"error": "authentication_required"},
            )
        user_id = request.headers.get("x-hub-user", "demo-c")
        user = DEMO_USERS.get(user_id)
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"error": "unknown_demo_user"})
        return user

    def require_admin(user: dict[str, str] = Depends(current_user)) -> dict[str, str]:
        if user["role"] != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"error": "admin_required"})
        return user

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/session")
    def session(user: dict[str, str] = Depends(current_user)) -> dict[str, Any]:
        return {"user": user, "available_demo_users": list(DEMO_USERS)}

    @app.get("/.well-known/jwks.json")
    def jwks() -> dict[str, Any]:
        return identity.jwks()

    @app.post("/api/registry/agents", status_code=201)
    def submit_agent(
        manifest: dict[str, Any],
        user: dict[str, str] = Depends(current_user),
    ) -> dict[str, Any]:
        if manifest.get("trust_level") == "first_party_internal" and user["role"] != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"error": "admin_required"})
        with database(settings.database_path) as conn:
            return submit_manifest(
                conn,
                raw_manifest=manifest,
                submitted_by=user["user_id"],
                settings=settings,
            )

    @app.get("/api/agents")
    def public_agents() -> dict[str, Any]:
        with database(settings.database_path) as conn:
            return {"agents": list_agents(conn, include_private=False, active_only=True)}

    @app.get("/api/agents/{agent_id}")
    def public_agent(agent_id: str) -> dict[str, Any]:
        with database(settings.database_path) as conn:
            record = get_agent(conn, agent_id, include_private=False)
            if record["status"] != "active" or record["active_version"] is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"error": "agent_not_found"})
            return record

    @app.get("/api/admin/agents")
    def admin_agents(_: dict[str, str] = Depends(require_admin)) -> dict[str, Any]:
        with database(settings.database_path) as conn:
            return {"agents": list_agents(conn, include_private=True, active_only=False)}

    @app.get("/api/admin/agents/{agent_id}")
    def admin_agent(agent_id: str, _: dict[str, str] = Depends(require_admin)) -> dict[str, Any]:
        with database(settings.database_path) as conn:
            return get_agent(conn, agent_id, include_private=True)

    @app.post("/api/admin/agents/{agent_id}/versions/{version_id}/review")
    def admin_review(
        agent_id: str,
        version_id: str,
        review: ReviewRequest,
        user: dict[str, str] = Depends(require_admin),
    ) -> dict[str, Any]:
        with database(settings.database_path) as conn:
            return review_version(
                conn,
                agent_id=agent_id,
                version_id=version_id,
                reviewer=user["user_id"],
                decision=review.decision,
                notes=review.notes,
                featured=review.featured,
                checks={"manual_review": review.decision},
            )

    @app.post("/api/admin/agents/{agent_id}/suspend")
    def admin_suspend(
        agent_id: str,
        body: StatusChangeRequest,
        user: dict[str, str] = Depends(require_admin),
    ) -> dict[str, Any]:
        with database(settings.database_path) as conn:
            return suspend_agent(conn, agent_id=agent_id, actor=user["user_id"], reason=body.reason)

    @app.post("/api/admin/agents/{agent_id}/restore")
    def admin_restore(
        agent_id: str,
        body: StatusChangeRequest,
        user: dict[str, str] = Depends(require_admin),
    ) -> dict[str, Any]:
        with database(settings.database_path) as conn:
            return restore_agent(conn, agent_id=agent_id, actor=user["user_id"], reason=body.reason)

    @app.post("/api/admin/agents/{agent_id}/rollback")
    def admin_rollback(
        agent_id: str,
        body: RollbackRequest,
        user: dict[str, str] = Depends(require_admin),
    ) -> dict[str, Any]:
        with database(settings.database_path) as conn:
            return rollback_agent(
                conn,
                agent_id=agent_id,
                version_id=body.version_id,
                actor=user["user_id"],
                reason=body.reason,
            )

    @app.post("/api/admin/agents/{agent_id}/credentials", status_code=201)
    def admin_credentials(
        agent_id: str,
        user: dict[str, str] = Depends(require_admin),
    ) -> dict[str, str]:
        with database(settings.database_path) as conn:
            get_agent(conn, agent_id, include_private=True)
            result = create_agent_credential(conn, agent_id)
            record_audit(
                conn,
                "agent_credential_created",
                actor=user["user_id"],
                agent_id=agent_id,
                safe_detail={"credential_id": result["credential_id"]},
            )
            return result

    @app.get("/api/admin/audit")
    def admin_audit(
        agent_id: str | None = None,
        _: dict[str, str] = Depends(require_admin),
    ) -> dict[str, Any]:
        with database(settings.database_path) as conn:
            return {"events": list_audit(conn, agent_id)}

    @app.get("/api/agents/{agent_id}/launch")
    def launch_link_app(
        agent_id: str,
        user: dict[str, str] = Depends(current_user),
    ) -> RedirectResponse:
        with database(settings.database_path) as conn:
            _, version = get_active_version(conn, agent_id)
            manifest = version["manifest"]
            url = manifest["integration"]["launch_url"]
            record_audit(
                conn,
                "agent_launch",
                actor=user["user_id"],
                agent_id=agent_id,
                version_id=version["version_id"],
                safe_detail={"mode": manifest["integration"]["mode"]},
            )
            return RedirectResponse(url, status_code=302)

    @app.post("/api/agents/{agent_id}/workspace/start")
    def start_workspace(
        agent_id: str,
        body: WorkspaceStartRequest,
        user: dict[str, str] = Depends(current_user),
    ) -> dict[str, str]:
        with database(settings.database_path) as conn:
            agent, version = get_active_version(conn, agent_id)
            if not bool(agent.get("featured")):
                raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "agent_not_featured"})
            manifest = version["manifest"]
            integration = manifest["integration"]
            allowed = [str(item) for item in integration.get("callback_urls", [])]
            if not allowed:
                raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "workspace_callback_unavailable"})
            redirect_uri = allowed[0]
            code = create_auth_code(
                conn,
                agent_id=agent_id,
                version_id=version["version_id"],
                user_id=user["user_id"],
                display_name=user["display_name"],
                redirect_uri=redirect_uri,
                state=body.state,
                scopes=["workspace:enter"],
                ttl_seconds=settings.auth_code_ttl_seconds,
            )
            record_audit(
                conn,
                "workspace_auth_code_created",
                actor=user["user_id"],
                agent_id=agent_id,
                version_id=version["version_id"],
                safe_detail={"redirect_uri_hash": hashlib.sha256(redirect_uri.encode()).hexdigest()},
            )
            callback = redirect_uri
            query = urlencode({"code": code, "state": body.state})
            return {"launch_url": f"{callback}?{query}", "expires_in": str(settings.auth_code_ttl_seconds)}

    @app.post("/oauth/token")
    def exchange_code(
        request: Request,
        grant_type: str = Form(...),
        code: str = Form(...),
        redirect_uri: str = Form(...),
        state: str = Form(...),
    ) -> dict[str, Any]:
        if grant_type != "authorization_code":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"error": "unsupported_grant_type"})
        with database(settings.database_path) as conn:
            client_id = authenticate_client_secret_basic(conn, request)
            code_record = consume_auth_code(
                conn,
                code=code,
                client_id=client_id,
                redirect_uri=redirect_uri,
                state=state,
            )
            token = identity.sign_agent_token(
                agent_id=client_id,
                version_id=code_record["version_id"],
                user_id=code_record["user_id"],
                display_name=code_record["display_name"],
                scopes=code_record["scopes"],
                request_id=f"workspace:{code_record['version_id']}",
            )
            record_audit(
                conn,
                "workspace_auth_code_exchanged",
                actor=client_id,
                agent_id=client_id,
                version_id=code_record["version_id"],
            )
            return {
                "access_token": token,
                "token_type": "Bearer",
                "expires_in": settings.jwt_ttl_seconds,
                "scope": " ".join(code_record["scopes"]),
            }

    @app.post("/api/agents/{agent_id}/health/check")
    async def run_health_check(
        agent_id: str,
        _: dict[str, str] = Depends(require_admin),
    ) -> dict[str, Any]:
        with database(settings.database_path) as conn:
            return await check_agent_health(conn, agent_id=agent_id, settings=settings)

    @app.post("/api/gateway/agents/{agent_id}/runs")
    async def run_agent(
        agent_id: str,
        request: Request,
        user: dict[str, str] = Depends(current_user),
    ):
        payload = await request.json()
        with database(settings.database_path) as conn:
            return await gateway_stream(
                conn,
                agent_id=agent_id,
                payload=payload,
                user=user,
                request=request,
                settings=settings,
                identity=identity,
            )

    @app.post("/api/agents/{agent_id}/chat")
    async def chat_alias(
        agent_id: str,
        request: Request,
        user: dict[str, str] = Depends(current_user),
    ):
        payload = await request.json()
        with database(settings.database_path) as conn:
            return await gateway_stream(
                conn,
                agent_id=agent_id,
                payload=payload,
                user=user,
                request=request,
                settings=settings,
                identity=identity,
            )

    @app.get("/", include_in_schema=False)
    @app.get("/hub", include_in_schema=False)
    @app.get("/hub/{path:path}", include_in_schema=False)
    def hub_spa(path: str = "") -> FileResponse:
        return FileResponse(web_root / "index.html")

    @app.get("/{filename}", include_in_schema=False)
    def hub_static_file(filename: str) -> FileResponse:
        if filename not in {"app.js", "hub-core.js", "splash.js", "styles.css"}:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        return FileResponse(web_root / filename)

    return app


app = create_app()


def run() -> None:
    uvicorn.run(
        "hub.main:app",
        host=os.getenv("HUB_HOST", "127.0.0.1"),
        port=int(os.getenv("HUB_PORT", "8100")),
        reload=False,
    )


if __name__ == "__main__":
    run()
