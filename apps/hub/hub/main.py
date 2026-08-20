from __future__ import annotations

import asyncio
import hashlib
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import uvicorn
from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .audit import list_audit, record_audit
from .config import DEMO_USERS, Settings
from .conformance import resolve_safe_launch_url, run_version_checks
from .db import database, init_db
from .gateway import gateway_stream
from .health import check_agent_health
from .home_assistant import HomeAssistantChatRequest, home_assistant_chat
from .identity import (
    IdentityService,
    authenticate_client_secret_basic,
    consume_auth_code,
    create_agent_credential,
    create_auth_code,
    update_agent_credential_status,
)
from .limits import read_json_limited
from .model_gateway import (
    ModelBindingRequest,
    ModelDelegationRevokeRequest,
    ModelGenerateRequest,
    ModelGrantExchangeRequest,
    ModelProfileCreate,
    ModelProfilePatch,
    ModelProfileService,
    bind_model,
    create_model_delegation_if_supported,
    create_profile,
    delete_profile,
    discover_profile_models,
    exchange_model_grant,
    get_binding,
    get_profile,
    list_profiles,
    model_generate_stream,
    patch_profile,
    revoke_model_delegation,
    test_profile_connection,
)
from .registry import (
    deprecate_agent,
    get_active_version,
    get_agent,
    list_agents,
    list_submitted_agents,
    restore_agent,
    review_version,
    rollback_agent,
    submit_manifest,
    suspend_agent,
)
from .schemas import (
    CredentialStatusRequest,
    ReviewRequest,
    RollbackRequest,
    StatusChangeRequest,
    WorkspaceStartRequest,
)


def create_app(settings: Settings | None = None, identity: IdentityService | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    init_db(settings.database_path)
    identity = identity or IdentityService(settings)
    model_service = ModelProfileService.from_settings(settings, identity)

    async def monitor_health() -> None:
        while True:
            with database(settings.database_path) as conn:
                agent_ids = [
                    row["agent_id"]
                    for row in conn.execute(
                        """
                        SELECT a.agent_id
                        FROM hub_agents a
                        JOIN hub_agent_versions v ON v.version_id = a.active_version_id
                        WHERE a.status = 'active'
                          AND json_extract(v.manifest_json, '$.integration.mode') = 'connected'
                        """
                    ).fetchall()
                ]
            for agent_id in agent_ids:
                try:
                    with database(settings.database_path) as conn:
                        await check_agent_health(conn, agent_id=agent_id, settings=settings)
                except Exception:
                    continue
            await asyncio.sleep(settings.health_poll_interval_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        if settings.health_poll_interval_seconds > 0:
            task = asyncio.create_task(monitor_health())
            app.state.health_monitor_task = task
        try:
            yield
        finally:
            if task is None:
                return
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="Campus Agent Hub", version="0.2.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.identity = identity
    app.state.model_service = model_service
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_allow_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
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

    def require_developer_or_admin(user: dict[str, str] = Depends(current_user)) -> dict[str, str]:
        if user["role"] not in {"developer", "admin"}:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"error": "developer_or_admin_required"})
        return user

    async def run_checks_background(agent_id: str, version_id: str) -> None:
        try:
            with database(settings.database_path) as conn:
                result = await run_version_checks(
                    conn,
                    agent_id=agent_id,
                    version_id=version_id,
                    settings=settings,
                    identity=identity,
                )
                record_audit(
                    conn,
                    "agent_conformance_checked",
                    actor="hub-automatic-checker",
                    agent_id=agent_id,
                    version_id=version_id,
                    safe_detail={
                        "run_id": result["run_id"],
                        "overall_status": result["overall_status"],
                    },
                )
        except Exception:
            return

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/session")
    def session(user: dict[str, str] = Depends(current_user)) -> dict[str, Any]:
        return {"user": user, "available_demo_users": list(DEMO_USERS)}

    @app.get("/.well-known/jwks.json")
    def jwks() -> dict[str, Any]:
        return identity.jwks()

    @app.get("/api/assets/agent-icons/{version_id}", include_in_schema=False)
    def agent_icon(version_id: str) -> FileResponse:
        with database(settings.database_path) as conn:
            row = conn.execute(
                """
                SELECT file_path, media_type FROM hub_version_assets
                WHERE version_id = ? AND asset_type = 'icon'
                """,
                (version_id,),
            ).fetchone()
        if row is None or not Path(row["file_path"]).is_file():
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        return FileResponse(
            row["file_path"],
            media_type=row["media_type"],
            headers={"Cache-Control": "public, max-age=86400, immutable"},
        )

    @app.post("/api/registry/agents", status_code=201)
    async def submit_agent(
        manifest: dict[str, Any],
        background_tasks: BackgroundTasks,
        user: dict[str, str] = Depends(require_developer_or_admin),
    ) -> dict[str, Any]:
        if manifest.get("trust_level") == "first_party_internal" and user["role"] != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"error": "admin_required"})
        with database(settings.database_path) as conn:
            record = submit_manifest(
                conn,
                raw_manifest=manifest,
                submitted_by=user["user_id"],
                settings=settings,
            )
            version_id = record["versions"][0]["version_id"]
            if settings.automatic_checks_enabled:
                background_tasks.add_task(
                    run_checks_background,
                    record["agent_id"],
                    version_id,
                )
            return get_agent(conn, record["agent_id"], include_private=True)

    @app.get("/api/developer/submissions")
    def developer_submissions(user: dict[str, str] = Depends(require_developer_or_admin)) -> dict[str, Any]:
        with database(settings.database_path) as conn:
            submitted_by = None if user["role"] == "admin" else user["user_id"]
            return {"agents": list_submitted_agents(conn, submitted_by=submitted_by)}

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
            conformance = conn.execute(
                """
                SELECT run_id, overall_status FROM hub_conformance_runs
                WHERE version_id = ?
                ORDER BY completed_at DESC, rowid DESC
                LIMIT 1
                """,
                (version_id,),
            ).fetchone()
            review_checks = {"manual_review": review.decision}
            if conformance:
                review_checks.update(
                    {
                        "conformance_run_id": conformance["run_id"],
                        "conformance_status": conformance["overall_status"],
                    }
                )
            return review_version(
                conn,
                agent_id=agent_id,
                version_id=version_id,
                reviewer=user["user_id"],
                decision=review.decision,
                notes=review.notes,
                settings=settings,
                featured=review.featured,
                checks=review_checks,
            )

    @app.post("/api/admin/agents/{agent_id}/versions/{version_id}/checks")
    async def admin_run_checks(
        agent_id: str,
        version_id: str,
        user: dict[str, str] = Depends(require_admin),
    ) -> dict[str, Any]:
        with database(settings.database_path) as conn:
            result = await run_version_checks(
                conn,
                agent_id=agent_id,
                version_id=version_id,
                settings=settings,
                identity=identity,
            )
            record_audit(
                conn,
                "agent_conformance_checked",
                actor=user["user_id"],
                agent_id=agent_id,
                version_id=version_id,
                safe_detail={"run_id": result["run_id"], "overall_status": result["overall_status"]},
            )
            return result

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

    @app.post("/api/admin/agents/{agent_id}/deprecate")
    def admin_deprecate(
        agent_id: str,
        body: StatusChangeRequest,
        user: dict[str, str] = Depends(require_admin),
    ) -> dict[str, Any]:
        with database(settings.database_path) as conn:
            return deprecate_agent(conn, agent_id=agent_id, actor=user["user_id"], reason=body.reason)

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
            result = create_agent_credential(
                conn,
                agent_id,
                rotation_window_seconds=settings.credential_rotation_window_seconds,
            )
            record_audit(
                conn,
                "agent_credential_created",
                actor=user["user_id"],
                agent_id=agent_id,
                safe_detail={"credential_id": result["credential_id"]},
            )
            return result

    @app.post("/api/admin/agents/{agent_id}/credentials/{credential_id}/status")
    def admin_credential_status(
        agent_id: str,
        credential_id: str,
        body: CredentialStatusRequest,
        user: dict[str, str] = Depends(require_admin),
    ) -> dict[str, str]:
        with database(settings.database_path) as conn:
            update_agent_credential_status(
                conn,
                agent_id=agent_id,
                credential_id=credential_id,
                new_status=body.status,
                rotation_window_seconds=settings.credential_rotation_window_seconds,
            )
            record_audit(
                conn,
                "agent_credential_status_changed",
                actor=user["user_id"],
                agent_id=agent_id,
                reason=body.reason,
                safe_detail={"credential_id": credential_id, "status": body.status},
            )
            return {"credential_id": credential_id, "status": body.status}

    @app.get("/api/admin/audit")
    def admin_audit(
        agent_id: str | None = None,
        _: dict[str, str] = Depends(require_admin),
    ) -> dict[str, Any]:
        with database(settings.database_path) as conn:
            return {"events": list_audit(conn, agent_id)}

    @app.get("/api/model-profiles")
    def api_model_profiles(user: dict[str, str] = Depends(current_user)) -> dict[str, Any]:
        model_service.require_enabled()
        with database(settings.database_path) as conn:
            return {"profiles": list_profiles(conn, user["user_id"])}

    @app.post("/api/model-profiles", status_code=201)
    def api_create_model_profile(
        body: ModelProfileCreate,
        user: dict[str, str] = Depends(current_user),
    ) -> dict[str, Any]:
        with database(settings.database_path) as conn:
            return create_profile(
                conn,
                model_service,
                owner_user_id=user["user_id"],
                body=body,
            )

    @app.get("/api/model-profiles/{profile_id}")
    def api_get_model_profile(
        profile_id: str,
        user: dict[str, str] = Depends(current_user),
    ) -> dict[str, Any]:
        model_service.require_enabled()
        with database(settings.database_path) as conn:
            return get_profile(conn, user["user_id"], profile_id)

    @app.patch("/api/model-profiles/{profile_id}")
    def api_patch_model_profile(
        profile_id: str,
        body: ModelProfilePatch,
        user: dict[str, str] = Depends(current_user),
    ) -> dict[str, Any]:
        with database(settings.database_path) as conn:
            return patch_profile(
                conn,
                model_service,
                owner_user_id=user["user_id"],
                profile_id=profile_id,
                body=body,
            )

    @app.delete("/api/model-profiles/{profile_id}", status_code=204)
    def api_delete_model_profile(
        profile_id: str,
        user: dict[str, str] = Depends(current_user),
    ) -> None:
        model_service.require_enabled()
        with database(settings.database_path) as conn:
            delete_profile(conn, user["user_id"], profile_id)

    @app.post("/api/model-profiles/{profile_id}/test")
    async def api_test_model_profile(
        profile_id: str,
        user: dict[str, str] = Depends(current_user),
    ) -> dict[str, Any]:
        with database(settings.database_path) as conn:
            return await test_profile_connection(
                conn,
                model_service,
                owner_user_id=user["user_id"],
                profile_id=profile_id,
            )

    @app.post("/api/model-profiles/{profile_id}/discover")
    async def api_discover_model_profile(
        profile_id: str,
        user: dict[str, str] = Depends(current_user),
    ) -> dict[str, Any]:
        with database(settings.database_path) as conn:
            return await discover_profile_models(
                conn,
                model_service,
                owner_user_id=user["user_id"],
                profile_id=profile_id,
            )

    @app.put("/api/model-bindings/global")
    def api_bind_global_model(
        body: ModelBindingRequest,
        user: dict[str, str] = Depends(current_user),
    ) -> dict[str, Any]:
        model_service.require_enabled()
        with database(settings.database_path) as conn:
            return bind_model(conn, owner_user_id=user["user_id"], agent_id="", body=body)

    @app.get("/api/model-bindings")
    def api_get_model_bindings(user: dict[str, str] = Depends(current_user)) -> dict[str, Any]:
        model_service.require_enabled()
        with database(settings.database_path) as conn:
            rows = conn.execute(
                """
                SELECT agent_id FROM hub_model_bindings
                WHERE owner_user_id = ?
                ORDER BY agent_id
                """,
                (user["user_id"],),
            ).fetchall()
            result = {
                "global": get_binding(conn, user["user_id"], ""),
                "agents": [
                    get_binding(conn, user["user_id"], row["agent_id"])
                    for row in rows
                    if row["agent_id"]
                ],
            }
            return result

    @app.post("/api/home-assistant/chat")
    async def api_home_assistant_chat(
        body: HomeAssistantChatRequest,
        user: dict[str, str] = Depends(current_user),
    ):
        with database(settings.database_path) as conn:
            return await home_assistant_chat(conn, model_service, user=user, body=body)

    @app.put("/api/model-bindings/agents/{agent_id}")
    def api_bind_agent_model(
        agent_id: str,
        body: ModelBindingRequest,
        user: dict[str, str] = Depends(current_user),
    ) -> dict[str, Any]:
        model_service.require_enabled()
        with database(settings.database_path) as conn:
            return bind_model(conn, owner_user_id=user["user_id"], agent_id=agent_id, body=body)

    @app.post("/api/model-gateway/grants/exchange")
    def api_exchange_model_grant(
        request: Request,
        body: ModelGrantExchangeRequest,
    ) -> dict[str, Any]:
        model_service.require_enabled()
        with database(settings.database_path) as conn:
            return exchange_model_grant(conn, model_service, request=request, body=body)

    @app.post("/api/model-gateway/delegations/revoke")
    def api_revoke_model_delegation(
        request: Request,
        body: ModelDelegationRevokeRequest,
    ) -> dict[str, Any]:
        model_service.require_enabled()
        with database(settings.database_path) as conn:
            client_id = authenticate_client_secret_basic(conn, request)
            return revoke_model_delegation(conn, agent_id=client_id, token=body.token)

    @app.post("/api/model-gateway/v1/generate")
    async def api_model_gateway_generate(
        request: Request,
        body: ModelGenerateRequest,
    ):
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"error": "model_grant_invalid"})
        token = auth.split(" ", 1)[1].strip()
        with database(settings.database_path) as conn:
            return await model_generate_stream(conn, model_service, token=token, body=body)

    @app.get("/api/agents/{agent_id}/launch")
    async def launch_link_app(
        agent_id: str,
        user: dict[str, str] = Depends(current_user),
    ) -> RedirectResponse:
        with database(settings.database_path) as conn:
            _, version = get_active_version(conn, agent_id)
            manifest = version["manifest"]
            if manifest["integration"]["mode"] != "link":
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail={"error": "agent_not_link_app"},
                )
            url = manifest["integration"]["launch_url"]
            trust_level = version.get("trust_level", "third_party_external")
        try:
            target = await resolve_safe_launch_url(
                url,
                trust_level=trust_level,
                settings=settings,
            )
        except Exception as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error": "agent_unavailable"},
            ) from exc
        with database(settings.database_path) as conn:
            record_audit(
                conn,
                "agent_launch",
                actor=user["user_id"],
                agent_id=agent_id,
                version_id=version["version_id"],
                safe_detail={"mode": manifest["integration"]["mode"]},
            )
        return RedirectResponse(target, status_code=302)

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
            response = {
                "access_token": token,
                "token_type": "Bearer",
                "expires_in": settings.jwt_ttl_seconds,
                "scope": " ".join(code_record["scopes"]),
            }
            delegation = create_model_delegation_if_supported(
                conn,
                model_service,
                agent_id=client_id,
                version_id=code_record["version_id"],
                user_id=code_record["user_id"],
                display_name=code_record["display_name"],
            )
            if delegation:
                response.update(delegation)
            return response

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
        payload = await read_json_limited(request, settings)
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
        payload = await read_json_limited(request, settings)
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
        if filename not in {"app.js", "hub-core.js", "hub-theme.js", "splash.js", "starfield.js", "styles.css"}:
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
