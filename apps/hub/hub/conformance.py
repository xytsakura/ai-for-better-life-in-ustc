from __future__ import annotations

import json
import hashlib
import sqlite3
import time
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

from .config import Settings
from .identity import IdentityService
from .security import validate_url_safety
from .utils import new_id, now_iso


def _icon_content_matches(media_type: str, content: bytes) -> bool:
    if media_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if media_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff") and content.endswith(b"\xff\xd9")
    if media_type == "image/gif":
        return content.startswith((b"GIF87a", b"GIF89a"))
    if media_type == "image/webp":
        return len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    return False


def _result(
    name: str,
    started: float,
    *,
    passed: bool,
    error_code: str | None = None,
    detail: str = "",
) -> dict[str, Any]:
    return {
        "name": name,
        "status": "passed" if passed else "failed",
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "error_code": error_code,
        "safe_detail": detail[:160],
    }


def _parse_sse(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    normalized = text.replace("\r\n", "\n")
    for frame in normalized.split("\n\n"):
        data = "\n".join(
            line[5:].lstrip()
            for line in frame.split("\n")
            if line.startswith("data:")
        )
        if not data:
            continue
        payload = json.loads(data)
        if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
            raise ValueError("SSE event must be a JSON object with type")
        events.append(payload)
    return events


def _validate_event_order(events: list[dict[str, Any]]) -> None:
    if not events or events[0].get("type") != "RUN_STARTED":
        raise ValueError("RUN_STARTED must be the first event")
    terminal = [index for index, event in enumerate(events) if event.get("type") in {"RUN_FINISHED", "RUN_ERROR"}]
    if terminal != [len(events) - 1]:
        raise ValueError("exactly one terminal event must be last")

    open_messages: set[str] = set()
    known_tools: set[str] = set()
    for event in events:
        event_type = event.get("type")
        if event_type == "TEXT_MESSAGE_START":
            message_id = event.get("messageId")
            if not isinstance(message_id, str) or not message_id:
                raise ValueError("TEXT_MESSAGE_START requires messageId")
            open_messages.add(message_id)
        elif event_type in {"TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END"}:
            message_id = event.get("messageId")
            if message_id not in open_messages:
                raise ValueError(f"{event_type} references an unopened message")
            if event_type == "TEXT_MESSAGE_CONTENT" and not isinstance(event.get("delta"), str):
                raise ValueError("TEXT_MESSAGE_CONTENT requires string delta")
            if event_type == "TEXT_MESSAGE_END":
                open_messages.remove(message_id)
        elif event_type == "TOOL_CALL_START":
            tool_id = event.get("toolCallId")
            if not isinstance(tool_id, str) or not tool_id:
                raise ValueError("TOOL_CALL_START requires toolCallId")
            known_tools.add(tool_id)
        elif event_type in {"TOOL_CALL_ARGS", "TOOL_CALL_END", "TOOL_CALL_RESULT"}:
            if event.get("toolCallId") not in known_tools:
                raise ValueError(f"{event_type} references an unknown tool call")
    if open_messages:
        raise ValueError("message stream ended before TEXT_MESSAGE_END")


async def _request_with_safe_redirects(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    trust_level: str,
    settings: Settings,
    max_redirects: int = 3,
    **kwargs: Any,
) -> httpx.Response:
    current = url
    for redirect_count in range(max_redirects + 1):
        validate_url_safety(current, trust_level, settings)
        response = await client.request(method, current, follow_redirects=False, **kwargs)
        if response.status_code not in {301, 302, 303, 307, 308}:
            return response
        location = response.headers.get("location")
        if not location or redirect_count == max_redirects:
            raise ValueError("unsafe_or_excessive_redirect")
        current = urljoin(current, location)
        method = "GET" if response.status_code == 303 else method
    raise ValueError("unsafe_or_excessive_redirect")


async def resolve_safe_launch_url(
    url: str,
    *,
    trust_level: str,
    settings: Settings,
) -> str:
    timeout = httpx.Timeout(
        settings.health_timeout_seconds,
        connect=settings.connect_timeout_seconds,
    )
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        response = await _request_with_safe_redirects(
            client,
            "GET",
            url,
            trust_level=trust_level,
            settings=settings,
        )
    if response.status_code < 200 or response.status_code >= 400:
        raise ValueError("launch_unavailable")
    return str(response.url)


async def run_version_checks(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
    version_id: str,
    settings: Settings,
    identity: IdentityService,
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT manifest_json, trust_level FROM hub_agent_versions WHERE agent_id = ? AND version_id = ?",
        (agent_id, version_id),
    ).fetchone()
    if row is None:
        raise ValueError("unknown agent version")
    manifest = json.loads(row["manifest_json"])
    integration = manifest["integration"]
    trust_level = row["trust_level"]
    run_id = new_id("check")
    started_at = now_iso()
    checks: list[dict[str, Any]] = []

    started = time.perf_counter()
    try:
        for key in ("launch_url", "chat_endpoint", "health_endpoint"):
            if integration.get(key):
                validate_url_safety(integration[key], trust_level, settings)
        checks.append(_result("url_safety", started, passed=True))
    except Exception:
        checks.append(_result("url_safety", started, passed=False, error_code="unsafe_endpoint"))

    timeout = httpx.Timeout(
        settings.request_timeout_seconds,
        connect=settings.connect_timeout_seconds,
        read=settings.request_timeout_seconds,
    )
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        icon = manifest.get("icon")
        if isinstance(icon, str) and urlparse(icon).scheme in {"http", "https"}:
            started = time.perf_counter()
            try:
                response = await _request_with_safe_redirects(
                    client,
                    "GET",
                    icon,
                    trust_level=trust_level,
                    settings=settings,
                )
                media_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                extensions = {
                    "image/png": ".png",
                    "image/jpeg": ".jpg",
                    "image/webp": ".webp",
                    "image/gif": ".gif",
                }
                if response.status_code != 200 or media_type not in extensions:
                    raise ValueError("unsupported icon response")
                if not response.content or len(response.content) > 524_288:
                    raise ValueError("invalid icon size")
                if not _icon_content_matches(media_type, response.content):
                    raise ValueError("icon content does not match declared media type")
                cache_dir = settings.asset_cache_dir or settings.database_path.parent / "assets"
                cache_dir.mkdir(parents=True, exist_ok=True)
                path = cache_dir / f"{version_id}{extensions[media_type]}"
                path.write_bytes(response.content)
                digest = hashlib.sha256(response.content).hexdigest()
                conn.execute(
                    """
                    INSERT INTO hub_version_assets (
                      version_id, asset_type, file_path, media_type, sha256, created_at
                    ) VALUES (?, 'icon', ?, ?, ?, ?)
                    ON CONFLICT(version_id, asset_type) DO UPDATE SET
                      file_path = excluded.file_path,
                      media_type = excluded.media_type,
                      sha256 = excluded.sha256,
                      created_at = excluded.created_at
                    """,
                    (version_id, str(path), media_type, digest, now_iso()),
                )
                checks.append(_result("icon_cache", started, passed=True))
            except Exception:
                checks.append(
                    _result(
                        "icon_cache",
                        started,
                        passed=False,
                        error_code="unsafe_asset",
                        detail="icon download or validation failed",
                    )
                )

        started = time.perf_counter()
        try:
            launch_url = integration["launch_url"]
            if trust_level == "first_party_internal" and integration.get("chat_endpoint"):
                public = urlparse(launch_url)
                internal = urlparse(integration["chat_endpoint"])
                launch_url = urlunparse(
                    (internal.scheme, internal.netloc, public.path or "/", "", public.query, "")
                )
            response = await _request_with_safe_redirects(
                client,
                "GET",
                launch_url,
                trust_level=trust_level,
                settings=settings,
            )
            if response.status_code < 200 or response.status_code >= 400:
                raise ValueError(f"HTTP {response.status_code}")
            checks.append(_result("launch_url", started, passed=True))
        except httpx.TimeoutException:
            checks.append(_result("launch_url", started, passed=False, error_code="agent_timeout"))
        except Exception as exc:
            detail = str(exc) if str(exc).startswith("HTTP ") else "launch check failed"
            checks.append(_result("launch_url", started, passed=False, error_code="agent_unavailable", detail=detail))

        if integration["mode"] == "connected":
            started = time.perf_counter()
            try:
                validate_url_safety(integration["health_endpoint"], trust_level, settings)
                response = await client.get(integration["health_endpoint"], headers={"accept": "application/json"})
                if response.status_code != 200:
                    raise ValueError(f"HTTP {response.status_code}")
                if len(response.content) > settings.max_response_bytes:
                    raise ValueError("health response too large")
                payload = response.json()
                if (
                    not isinstance(payload, dict)
                    or payload.get("status") != "ok"
                    or payload.get("contract_version") != "1.0"
                    or not isinstance(payload.get("capabilities"), list)
                ):
                    raise ValueError("invalid health contract")
                checks.append(_result("health_contract", started, passed=True))
            except httpx.TimeoutException:
                checks.append(_result("health_contract", started, passed=False, error_code="agent_timeout"))
            except Exception as exc:
                detail = str(exc) if str(exc).startswith("HTTP ") else "health contract invalid"
                checks.append(_result("health_contract", started, passed=False, error_code="protocol_error", detail=detail))

            started = time.perf_counter()
            try:
                request_id = new_id("conformance")
                token = identity.sign_agent_token(
                    agent_id=agent_id,
                    version_id=version_id,
                    user_id="hub-conformance",
                    display_name="Hub Conformance",
                    scopes=["chat:invoke"],
                    request_id=request_id,
                )
                common_headers = {
                    "authorization": f"Bearer {token}",
                    "x-hub-request-id": request_id,
                    "content-type": "application/json",
                }
                validate_url_safety(integration["chat_endpoint"], trust_level, settings)
                if integration.get("protocol") == "simple-chat":
                    payload = {
                        "thread_id": f"check-{run_id}",
                        "run_id": run_id,
                        "messages": [{"id": "user-1", "role": "user", "content": "协议自检"}],
                        "context": {"conformance": True},
                    }
                    response = await client.post(
                        integration["chat_endpoint"],
                        headers={**common_headers, "accept": "application/json"},
                        json=payload,
                    )
                    if response.status_code != 200:
                        raise ValueError(f"HTTP {response.status_code}")
                    if len(response.content) > settings.max_response_bytes:
                        raise ValueError("response too large")
                    body = response.json()
                    message = body.get("message") if isinstance(body, dict) else None
                    if not isinstance(message, dict) or message.get("role") != "assistant" or not isinstance(message.get("content"), str):
                        raise ValueError("invalid simple-chat response")
                else:
                    payload = {
                        "threadId": f"check-{run_id}",
                        "runId": run_id,
                        "state": {},
                        "messages": [{"id": "user-1", "role": "user", "content": "协议自检"}],
                        "tools": [],
                        "context": [{"description": "conformance", "value": "campus-agent-hub-v1"}],
                        "forwardedProps": {"conformance": True},
                    }
                    response = await client.post(
                        integration["chat_endpoint"],
                        headers={**common_headers, "accept": "text/event-stream"},
                        json=payload,
                    )
                    if response.status_code != 200:
                        raise ValueError(f"HTTP {response.status_code}")
                    if "text/event-stream" not in response.headers.get("content-type", "").lower():
                        raise ValueError("invalid SSE content type")
                    if len(response.content) > settings.max_response_bytes:
                        raise ValueError("response too large")
                    _validate_event_order(_parse_sse(response.text))
                checks.append(_result("chat_contract", started, passed=True))
            except httpx.TimeoutException:
                checks.append(_result("chat_contract", started, passed=False, error_code="agent_timeout"))
            except Exception as exc:
                detail = str(exc) if str(exc).startswith("HTTP ") else "chat contract invalid"
                checks.append(_result("chat_contract", started, passed=False, error_code="protocol_error", detail=detail))

    completed_at = now_iso()
    overall_status = "passed" if checks and all(item["status"] == "passed" for item in checks) else "failed"
    conn.execute(
        """
        INSERT INTO hub_conformance_runs (
          run_id, agent_id, version_id, overall_status, checks_json, started_at, completed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            agent_id,
            version_id,
            overall_status,
            json.dumps(checks, ensure_ascii=False, sort_keys=True),
            started_at,
            completed_at,
        ),
    )
    return {
        "run_id": run_id,
        "agent_id": agent_id,
        "version_id": version_id,
        "contract_version": "1.0",
        "overall_status": overall_status,
        "checks": checks,
        "started_at": started_at,
        "completed_at": completed_at,
    }
