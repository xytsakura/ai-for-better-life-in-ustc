from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from fastapi import HTTPException, Request, status

from .config import Settings


async def read_json_limited(request: Request, settings: Settings) -> dict[str, Any]:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > settings.max_request_bytes:
                raise HTTPException(
                    status.HTTP_413_CONTENT_TOO_LARGE,
                    detail={"error": "request_too_large"},
                )
        except ValueError:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail={"error": "invalid_content_length"},
            ) from None
    body = await request.body()
    if len(body) > settings.max_request_bytes:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            detail={"error": "request_too_large"},
        )
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_json"},
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "invalid_run_input"},
        )
    return payload


def enforce_rate_limit(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    agent_id: str,
    settings: Settings,
) -> None:
    if settings.rate_limit_requests <= 0:
        return
    now = int(time.time())
    window = max(1, settings.rate_limit_window_seconds)
    window_start = now - (now % window)
    conn.execute(
        "DELETE FROM hub_rate_limits WHERE window_start < ?",
        (window_start - window,),
    )
    row = conn.execute(
        """
        SELECT request_count FROM hub_rate_limits
        WHERE user_id = ? AND agent_id = ? AND window_start = ?
        """,
        (user_id, agent_id, window_start),
    ).fetchone()
    if row is not None and row["request_count"] >= settings.rate_limit_requests:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error": "rate_limited", "retry_after": window_start + window - now},
            headers={"Retry-After": str(max(1, window_start + window - now))},
        )
    conn.execute(
        """
        INSERT INTO hub_rate_limits (user_id, agent_id, window_start, request_count)
        VALUES (?, ?, ?, 1)
        ON CONFLICT(user_id, agent_id, window_start)
        DO UPDATE SET request_count = request_count + 1
        """,
        (user_id, agent_id, window_start),
    )
