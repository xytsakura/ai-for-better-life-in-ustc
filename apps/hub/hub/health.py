from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

import httpx
from fastapi import HTTPException, status

from .config import Settings
from .registry import get_active_version
from .security import validate_url_safety
from .utils import new_id, now_iso


def _safe_detail(exc: Exception | str) -> str:
    text = str(exc)
    return text[:160] if text else ""


async def check_agent_health(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
    settings: Settings,
) -> dict[str, Any]:
    _, version = get_active_version(conn, agent_id)
    manifest = version["manifest"]
    endpoint = manifest["integration"].get("health_endpoint")
    if not endpoint:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "agent_has_no_health_endpoint"})

    validate_url_safety(endpoint, version.get("trust_level", "third_party_external"), settings)
    start = time.perf_counter()
    status_value = "unknown"
    latency_ms: int | None = None
    contract_version: str | None = None
    capabilities: list[str] = []
    error_code: str | None = None
    safe_detail = ""
    try:
        timeout = httpx.Timeout(settings.health_timeout_seconds, connect=settings.connect_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.get(endpoint, headers={"accept": "application/json"})
        latency_ms = int((time.perf_counter() - start) * 1000)
        if response.status_code != 200:
            status_value = "offline"
            error_code = "agent_unavailable"
        else:
            payload = response.json()
            contract_version = payload.get("contract_version")
            capabilities = payload.get("capabilities") or []
            if payload.get("status") == "ok" and contract_version == "1.0":
                status_value = "ok"
            else:
                status_value = "protocol_error"
                error_code = "protocol_error"
    except (httpx.TimeoutException, httpx.ConnectError) as exc:
        latency_ms = int((time.perf_counter() - start) * 1000)
        status_value = "offline"
        error_code = "agent_timeout" if isinstance(exc, httpx.TimeoutException) else "agent_unavailable"
        safe_detail = _safe_detail(exc)
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        latency_ms = int((time.perf_counter() - start) * 1000)
        status_value = "protocol_error"
        error_code = "protocol_error"
        safe_detail = _safe_detail(exc)

    health_id = new_id("health")
    checked_at = now_iso()
    conn.execute(
        """
        INSERT INTO hub_health_checks (
          health_id, agent_id, version_id, status, latency_ms, contract_version,
          capabilities_json, error_code, safe_detail, checked_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            health_id,
            agent_id,
            version["version_id"],
            status_value,
            latency_ms,
            contract_version,
            json.dumps(capabilities, ensure_ascii=False),
            error_code,
            safe_detail,
            checked_at,
        ),
    )
    return {
        "health_id": health_id,
        "agent_id": agent_id,
        "version_id": version["version_id"],
        "status": status_value,
        "latency_ms": latency_ms,
        "contract_version": contract_version,
        "capabilities": capabilities,
        "error_code": error_code,
        "checked_at": checked_at,
    }
