from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import HTTPException, Request, status
from fastapi.responses import StreamingResponse

from .audit import record_audit
from .config import Settings
from .identity import IdentityService
from .limits import enforce_rate_limit
from .registry import ensure_health_allows_invocation, get_active_version
from .schemas import RunAgentInput
from .security import validate_url_safety
from .utils import new_id, now_iso


TERMINAL_EVENTS = {"RUN_FINISHED", "RUN_ERROR"}


def sse_event(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n".encode("utf-8")


def agui_run_error(run_id: str | None, code: str) -> bytes:
    payload = {"type": "RUN_ERROR", "message": code, "code": code}
    if run_id is not None:
        payload["runId"] = run_id
    return sse_event(payload)


def agui_text_response(
    thread_id: str,
    run_id: str,
    content: str,
    *,
    citations: list[dict[str, Any]] | None = None,
    usage: dict[str, Any] | None = None,
) -> list[bytes]:
    message_id = new_id("msg")
    return [
        sse_event({"type": "RUN_STARTED", "threadId": thread_id, "runId": run_id}),
        sse_event({"type": "TEXT_MESSAGE_START", "messageId": message_id, "role": "assistant"}),
        sse_event({"type": "TEXT_MESSAGE_CONTENT", "messageId": message_id, "delta": content}),
        sse_event({"type": "TEXT_MESSAGE_END", "messageId": message_id}),
        sse_event(
            {
                "type": "RUN_FINISHED",
                "threadId": thread_id,
                "runId": run_id,
                "citations": citations or [],
                "usage": usage or {},
            }
        ),
    ]


def _record_invocation_start(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
    version_id: str,
    user_id: str,
    run_id: str,
) -> str:
    invocation_id = new_id("inv")
    conn.execute(
        """
        INSERT INTO hub_invocations (
          invocation_id, agent_id, version_id, user_id, run_id, status, started_at
        ) VALUES (?, ?, ?, ?, ?, 'started', ?)
        """,
        (invocation_id, agent_id, version_id, user_id, run_id, now_iso()),
    )
    conn.commit()
    return invocation_id


def _record_invocation_end(
    db_path,
    *,
    invocation_id: str,
    status_value: str,
    error_code: str | None,
    duration_ms: int,
    usage: dict[str, Any] | None = None,
) -> None:
    from .db import database

    with database(db_path) as conn:
        now = now_iso()
        conn.execute(
            """
            UPDATE hub_invocations
            SET status = ?, error_code = ?, duration_ms = ?, usage_json = ?, completed_at = ?
            WHERE invocation_id = ?
            """,
            (
                status_value,
                error_code,
                duration_ms,
                json.dumps(usage or {}, ensure_ascii=False, sort_keys=True),
                now,
                invocation_id,
            ),
        )
        delegation_rows = conn.execute(
            """
            SELECT delegation_id
            FROM hub_model_delegations
            WHERE scope_type = 'connected_run' AND scope_id = ?
            """,
            (invocation_id,),
        ).fetchall()
        delegation_ids = [row["delegation_id"] for row in delegation_rows if row["delegation_id"]]
        if delegation_ids:
            placeholders = ",".join("?" for _ in delegation_ids)
            conn.execute(
                f"""
                UPDATE hub_model_delegations
                SET status = 'revoked', revoked_at = ?
                WHERE delegation_id IN ({placeholders}) AND status != 'revoked'
                """,
                (now, *delegation_ids),
            )
            conn.execute(
                f"""
                UPDATE hub_model_gateway_grants
                SET status = 'revoked', revoked_at = ?
                WHERE delegation_id IN ({placeholders}) AND status = 'issued'
                """,
                (now, *delegation_ids),
            )


async def gateway_stream(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
    payload: dict[str, Any],
    user: dict[str, str],
    request: Request,
    settings: Settings,
    identity: IdentityService,
) -> StreamingResponse:
    _, version = get_active_version(conn, agent_id)
    ensure_health_allows_invocation(
        conn,
        agent_id=agent_id,
        version_id=version["version_id"],
        settings=settings,
    )
    enforce_rate_limit(
        conn,
        user_id=user["user_id"],
        agent_id=agent_id,
        settings=settings,
    )
    manifest = version["manifest"]
    integration = manifest["integration"]
    if integration["mode"] != "connected":
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "agent_not_active"})

    try:
        run_input = RunAgentInput.model_validate(payload)
    except Exception as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail={"error": "invalid_run_input"}) from exc

    chat_endpoint = integration.get("chat_endpoint")
    if not chat_endpoint:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "agent_not_active"})
    validate_url_safety(chat_endpoint, version.get("trust_level", "third_party_external"), settings)

    invocation_id = _record_invocation_start(
        conn,
        agent_id=agent_id,
        version_id=version["version_id"],
        user_id=user["user_id"],
        run_id=run_input.runId,
    )
    request_id = invocation_id
    token = identity.sign_agent_token(
        agent_id=agent_id,
        version_id=version["version_id"],
        user_id=user["user_id"],
        display_name=user["display_name"],
        scopes=["chat:invoke"],
        request_id=request_id,
    )

    protocol = integration.get("protocol", "ag-ui")
    if protocol == "simple-chat":
        iterator = _simple_chat_stream(
            settings=settings,
            endpoint=chat_endpoint,
            payload=payload,
            run_input=run_input,
            token=token,
            request_id=request_id,
            request=request,
            invocation_id=invocation_id,
        )
    else:
        iterator = _agui_proxy_stream(
            settings=settings,
            endpoint=chat_endpoint,
            payload=payload,
            run_input=run_input,
            token=token,
            request_id=request_id,
            request=request,
            invocation_id=invocation_id,
        )

    headers = {
        "cache-control": "no-cache",
        "x-accel-buffering": "no",
    }
    record_audit(
        conn,
        "agent_invocation_started",
        actor=user["user_id"],
        agent_id=agent_id,
        version_id=version["version_id"],
        safe_detail={"invocation_id": invocation_id, "protocol": protocol},
    )
    conn.commit()
    return StreamingResponse(iterator, media_type="text/event-stream", headers=headers)


async def _agui_proxy_stream(
    *,
    settings: Settings,
    endpoint: str,
    payload: dict[str, Any],
    run_input: RunAgentInput,
    token: str,
    request_id: str,
    request: Request,
    invocation_id: str,
) -> AsyncIterator[bytes]:
    start = time.perf_counter()
    terminal_seen = False
    event_count = 0
    open_messages: set[str] = set()
    known_tools: set[str] = set()
    usage: dict[str, Any] = {}
    error_code: str | None = None
    status_value = "finished"
    try:
        timeout = httpx.Timeout(
            settings.request_timeout_seconds,
            connect=settings.connect_timeout_seconds,
            read=settings.request_timeout_seconds,
        )
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            async with client.stream(
                "POST",
                endpoint,
                json=payload,
                headers={
                    "accept": "text/event-stream",
                    "content-type": "application/json",
                    "authorization": f"Bearer {token}",
                    "x-hub-request-id": request_id,
                },
            ) as response:
                content_type = response.headers.get("content-type", "")
                if response.status_code < 200 or response.status_code >= 300:
                    error_code = "upstream_error"
                    status_value = "error"
                    yield agui_run_error(run_input.runId, error_code)
                    return
                if "text/event-stream" not in content_type.lower():
                    error_code = "protocol_error"
                    status_value = "error"
                    yield agui_run_error(run_input.runId, error_code)
                    return
                response_bytes = 0
                async for line in response.aiter_lines():
                    response_bytes += len(line.encode("utf-8")) + 1
                    if response_bytes > settings.max_response_bytes:
                        error_code = "protocol_error"
                        status_value = "error"
                        yield agui_run_error(run_input.runId, "response_too_large")
                        return
                    if await request.is_disconnected():
                        status_value = "cancelled"
                        error_code = "client_cancelled"
                        break
                    if line.startswith("data:"):
                        data = line[5:].strip()
                        if data:
                            try:
                                event = json.loads(data)
                                event_type = event.get("type") if isinstance(event, dict) else None
                                if not isinstance(event_type, str):
                                    raise ValueError("missing event type")
                                if event_count == 0 and event_type != "RUN_STARTED":
                                    raise ValueError("RUN_STARTED must be first")
                                if event_count > 0 and event_type == "RUN_STARTED":
                                    raise ValueError("RUN_STARTED may only occur once")
                                if terminal_seen:
                                    raise ValueError("event received after terminal event")
                                if event_type == "TEXT_MESSAGE_START":
                                    message_id = event.get("messageId")
                                    if not isinstance(message_id, str) or not message_id:
                                        raise ValueError("missing messageId")
                                    if message_id in open_messages:
                                        raise ValueError("message already open")
                                    open_messages.add(message_id)
                                elif event_type in {"TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END"}:
                                    message_id = event.get("messageId")
                                    if message_id not in open_messages:
                                        raise ValueError("unknown messageId")
                                    if event_type == "TEXT_MESSAGE_CONTENT" and not isinstance(event.get("delta"), str):
                                        raise ValueError("missing delta")
                                    if event_type == "TEXT_MESSAGE_END":
                                        open_messages.remove(message_id)
                                elif event_type == "TOOL_CALL_START":
                                    tool_id = event.get("toolCallId")
                                    if not isinstance(tool_id, str) or not tool_id or tool_id in known_tools:
                                        raise ValueError("invalid toolCallId")
                                    known_tools.add(tool_id)
                                elif event_type in {"TOOL_CALL_ARGS", "TOOL_CALL_END", "TOOL_CALL_RESULT"}:
                                    if event.get("toolCallId") not in known_tools:
                                        raise ValueError("unknown toolCallId")
                                if event_type in TERMINAL_EVENTS:
                                    if open_messages:
                                        raise ValueError("message not closed")
                                    terminal_seen = True
                                    if isinstance(event.get("usage"), dict):
                                        usage = event["usage"]
                                event_count += 1
                            except (json.JSONDecodeError, ValueError):
                                error_code = "protocol_error"
                                status_value = "error"
                                yield agui_run_error(run_input.runId, error_code)
                                return
                    yield (line + "\n").encode("utf-8")
                    if line == "":
                        yield b""
                        if terminal_seen:
                            return
                if status_value == "finished" and not terminal_seen:
                    error_code = "protocol_error"
                    status_value = "error"
                    yield agui_run_error(run_input.runId, error_code)
    except httpx.TimeoutException:
        error_code = "agent_timeout"
        status_value = "error"
        yield agui_run_error(run_input.runId, error_code)
    except httpx.HTTPError:
        error_code = "agent_unavailable"
        status_value = "error"
        yield agui_run_error(run_input.runId, error_code)
    except asyncio.CancelledError:
        error_code = "client_cancelled"
        status_value = "cancelled"
        raise
    finally:
        duration_ms = int((time.perf_counter() - start) * 1000)
        _record_invocation_end(
            settings.database_path,
            invocation_id=invocation_id,
            status_value=status_value,
            error_code=error_code,
            duration_ms=duration_ms,
            usage=usage,
        )


async def _simple_chat_stream(
    *,
    settings: Settings,
    endpoint: str,
    payload: dict[str, Any],
    run_input: RunAgentInput,
    token: str,
    request_id: str,
    request: Request,
    invocation_id: str,
) -> AsyncIterator[bytes]:
    start = time.perf_counter()
    status_value = "finished"
    error_code: str | None = None
    usage: dict[str, Any] = {}
    try:
        if await request.is_disconnected():
            status_value = "cancelled"
            error_code = "client_cancelled"
            return
        simple_messages: list[dict[str, str]] = []
        for index, item in enumerate(run_input.messages):
            role = item.get("role")
            content = item.get("content")
            if role not in {"system", "user", "assistant"} or not isinstance(content, str):
                continue
            simple_messages.append(
                {
                    "id": str(item.get("id") or item.get("messageId") or f"msg-{index + 1}"),
                    "role": role,
                    "content": content,
                }
            )
        if not simple_messages:
            status_value = "error"
            error_code = "protocol_error"
            yield agui_run_error(run_input.runId, error_code)
            return
        timeout = httpx.Timeout(settings.request_timeout_seconds, connect=settings.connect_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.post(
                endpoint,
                json={
                    "thread_id": run_input.threadId,
                    "run_id": run_input.runId,
                    "messages": simple_messages,
                    "context": {
                        "state": run_input.state or {},
                        "context": run_input.context or [],
                        "forwardedProps": run_input.forwardedProps or {},
                    },
                },
                headers={
                    "accept": "application/json",
                    "content-type": "application/json",
                    "authorization": f"Bearer {token}",
                    "x-hub-request-id": request_id,
                },
            )
        if response.status_code < 200 or response.status_code >= 300:
            status_value = "error"
            error_code = "upstream_error"
            yield agui_run_error(run_input.runId, error_code)
            return
        body = response.json()
        if len(json.dumps(body, ensure_ascii=False).encode("utf-8")) > settings.max_response_bytes:
            status_value = "error"
            error_code = "protocol_error"
            yield agui_run_error(run_input.runId, "response_too_large")
            return
        message = body.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        role = message.get("role") if isinstance(message, dict) else None
        if not isinstance(content, str):
            status_value = "error"
            error_code = "protocol_error"
            yield agui_run_error(run_input.runId, error_code)
            return
        if role != "assistant":
            status_value = "error"
            error_code = "protocol_error"
            yield agui_run_error(run_input.runId, error_code)
            return
        citations = body.get("citations") if isinstance(body.get("citations"), list) else []
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        for chunk in agui_text_response(
            run_input.threadId,
            run_input.runId,
            content,
            citations=citations,
            usage=usage,
        ):
            yield chunk
    except httpx.TimeoutException:
        status_value = "error"
        error_code = "agent_timeout"
        yield agui_run_error(run_input.runId, error_code)
    except (httpx.HTTPError, ValueError, TypeError):
        status_value = "error"
        error_code = "agent_unavailable"
        yield agui_run_error(run_input.runId, error_code)
    except asyncio.CancelledError:
        status_value = "cancelled"
        error_code = "client_cancelled"
        raise
    finally:
        _record_invocation_end(
            settings.database_path,
            invocation_id=invocation_id,
            status_value=status_value,
            error_code=error_code,
            duration_ms=int((time.perf_counter() - start) * 1000),
            usage=usage,
        )
