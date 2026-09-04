from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import socket
import sqlite3
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
import jwt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import Settings
from .identity import IdentityService, authenticate_client_secret_basic
from .registry import get_active_version
from .security import _host_is_private
from .utils import new_id, now_iso, random_token, sha256_text


API_STYLES = {"responses", "chat_completions"}
MODEL_AUDIENCE = "hub-model-gateway"


class ModelProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=80)
    label: str | None = Field(default=None, min_length=1, max_length=80)
    provider: str = Field(default="openai-compatible", min_length=1, max_length=80)
    base_url: str = Field(min_length=8, max_length=2048)
    api_key: str = Field(min_length=1, max_length=4096)
    api_style: Literal["responses", "chat_completions"] = "responses"
    status: Literal["active", "disabled"] = "active"
    default_model_id: str | None = Field(default=None, max_length=128)
    default_model: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def require_name(self) -> "ModelProfileCreate":
        if not (self.name or self.label):
            raise ValueError("name is required")
        return self


class ModelProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=80)
    label: str | None = Field(default=None, min_length=1, max_length=80)
    provider: str | None = Field(default=None, min_length=1, max_length=80)
    base_url: str | None = Field(default=None, min_length=8, max_length=2048)
    api_key: str | None = Field(default=None, min_length=1, max_length=4096)
    api_style: Literal["responses", "chat_completions"] | None = None
    default_model_id: str | None = Field(default=None, max_length=128)
    default_model: str | None = Field(default=None, max_length=128)
    status: Literal["active", "disabled"] | None = None


class ModelBindingRequest(BaseModel):
    profile_id: str
    model_id: str


class ModelGrantRequest(BaseModel):
    profile_id: str | None = None
    model_id: str | None = None


class ModelGrantExchangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_delegation_token: str = Field(min_length=20, max_length=4096)
    request_id: str = Field(min_length=8, max_length=128)
    requested_model_id: str | None = Field(default=None, min_length=1, max_length=128)


class ModelDelegationRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=20, max_length=4096)


class ModelMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=0, max_length=64_000)


class ModelGenerateRequest(BaseModel):
    instructions: str | None = Field(default=None, max_length=64_000)
    messages: list[ModelMessage] = Field(default_factory=list, max_length=64)
    reasoning_effort: Literal["minimal", "low", "medium", "high", "xhigh", "max"] | None = None
    max_output_tokens: int | None = Field(default=None, ge=1, le=16_384)
    stream: bool = False

    @field_validator("messages")
    @classmethod
    def non_empty_generation_context(cls, value: list[ModelMessage]) -> list[ModelMessage]:
        if not value:
            raise ValueError("messages must not be empty")
        return value


@dataclass(frozen=True)
class PlatformModelResult:
    output_text: str
    usage: dict[str, int]
    model: str


@dataclass(frozen=True)
class ModelProfileService:
    settings: Settings
    identity: IdentityService
    master_key: bytes | None
    current_key_version: int = 1
    keyring: dict[int, bytes] | None = None

    @classmethod
    def from_settings(cls, settings: Settings, identity: IdentityService) -> "ModelProfileService":
        if not settings.model_profiles_enabled:
            return cls(settings=settings, identity=identity, master_key=None)
        current_key = _load_master_key(settings.model_profile_master_key_file)
        keyring = _load_previous_keys(settings)
        keyring[settings.model_profile_master_key_version] = current_key
        return cls(
            settings=settings,
            identity=identity,
            master_key=current_key,
            current_key_version=settings.model_profile_master_key_version,
            keyring=keyring,
        )

    def require_enabled(self) -> None:
        if not self.settings.model_profiles_enabled or self.master_key is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail={"error": "model_profiles_disabled"},
            )

    def encrypt_key(
        self,
        *,
        profile_id: str,
        owner_user_id: str,
        key_version: int,
        api_key: str,
    ) -> tuple[bytes, bytes, str]:
        self.require_enabled()
        assert self.master_key is not None
        aad = _aad(profile_id, owner_user_id, key_version)
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(self.master_key).encrypt(nonce, api_key.encode("utf-8"), aad)
        envelope = {
            "format_version": 1,
            "key_version": key_version,
            "algorithm": "AES-256-GCM",
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }
        return json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode("utf-8"), nonce, _fingerprint(api_key)

    def decrypt_key(self, profile: sqlite3.Row | dict[str, Any]) -> str:
        self.require_enabled()
        assert self.keyring is not None
        encrypted = bytes(profile["encrypted_api_key"])
        nonce = bytes(profile["encrypted_api_key_nonce"])
        ciphertext = encrypted
        key_version = int(profile["key_version"])
        if encrypted.startswith(b"{"):
            try:
                envelope = json.loads(encrypted.decode("utf-8"))
                key_version = int(envelope["key_version"])
                nonce = base64.b64decode(envelope["nonce"])
                ciphertext = base64.b64decode(envelope["ciphertext"])
            except Exception as exc:
                raise HTTPException(
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail={"error": "model_profile_key_unreadable"},
                ) from exc
        key = self.keyring.get(key_version)
        if key is None:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": "model_profile_key_version_unavailable"},
            )
        aad = _aad(profile["profile_id"], profile["owner_user_id"], key_version)
        try:
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, aad)
        except Exception as exc:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": "model_profile_key_unreadable"},
            ) from exc
        return plaintext.decode("utf-8")

    def sign_gateway_grant(
        self,
        conn: sqlite3.Connection,
        *,
        user_id: str,
        agent_id: str,
        profile_id: str,
        model_id: str,
        delegation_id: str,
        request_id: str,
    ) -> str:
        self.require_enabled()
        now = datetime.now(UTC)
        ttl = max(1, min(120, self.settings.model_gateway_grant_ttl_seconds))
        jti = new_id("mgj")
        payload = {
            "iss": self.settings.issuer,
            "sub": user_id,
            "aud": MODEL_AUDIENCE,
            "iat": int(now.timestamp()),
            "nbf": int((now - timedelta(seconds=30)).timestamp()),
            "exp": int((now + timedelta(seconds=ttl)).timestamp()),
            "jti": jti,
            "scope": "model:invoke",
            "user_id": user_id,
            "agent_id": agent_id,
            "profile_id": profile_id,
            "model_id": model_id,
            "delegation_id": delegation_id,
            "request_id": request_id,
        }
        token = jwt.encode(
            payload,
            self.identity._private_key,  # noqa: SLF001 - Hub owns both signing APIs.
            algorithm="EdDSA",
            headers={"typ": "JWT", "kid": self.settings.jwt_kid},
        )
        conn.execute(
            """
            INSERT INTO hub_model_gateway_grants (
              jti, delegation_id, user_id, agent_id, profile_id, model_id, request_id,
              status, issued_at, expires_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'issued', ?, ?, ?)
            """,
            (
                jti,
                delegation_id,
                user_id,
                agent_id,
                profile_id,
                model_id,
                request_id,
                now_iso(),
                (now + timedelta(seconds=ttl)).isoformat(timespec="seconds").replace("+00:00", "Z"),
                now_iso(),
            ),
        )
        return token

    def decode_workspace_token(self, token: str, *, audience: str) -> dict[str, Any]:
        try:
            payload = jwt.decode(
                token,
                self.identity._public_key,  # noqa: SLF001 - companion verifier for Hub-issued tokens.
                algorithms=["EdDSA"],
                audience=audience,
                issuer=self.settings.issuer,
            )
        except Exception as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"error": "invalid_access_token"}) from exc
        scopes = set(str(payload.get("scope", "")).split())
        if not scopes.intersection({"workspace:enter", "chat:invoke"}):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"error": "insufficient_scope"})
        return payload


def _load_master_key(path: Any) -> bytes:
    if path is None:
        raise RuntimeError("HUB_MODEL_PROFILE_MASTER_KEY_FILE is required when model profiles are enabled")
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise RuntimeError("HUB_MODEL_PROFILE_MASTER_KEY_FILE does not exist") from exc
    except OSError as exc:
        raise RuntimeError("HUB_MODEL_PROFILE_MASTER_KEY_FILE cannot be read") from exc
    candidates: list[bytes] = []
    if raw:
        try:
            candidates.append(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        except Exception:
            pass
        try:
            candidates.append(bytes.fromhex(raw))
        except ValueError:
            pass
        candidates.append(raw.encode("utf-8"))
    for candidate in candidates:
        if len(candidate) == 32:
            return candidate
    raise RuntimeError("HUB_MODEL_PROFILE_MASTER_KEY_FILE must contain exactly 32 bytes")


def _load_previous_keys(settings: Settings) -> dict[int, bytes]:
    result: dict[int, bytes] = {}
    for item in settings.model_profile_previous_key_files:
        try:
            version_text, path_text = item.split("=", 1)
            version = int(version_text)
        except ValueError as exc:
            raise RuntimeError(
                "HUB_MODEL_PROFILE_PREVIOUS_KEY_FILES entries must use version=path"
            ) from exc
        if version == settings.model_profile_master_key_version:
            raise RuntimeError("previous model profile key versions must differ from current version")
        result[version] = _load_master_key(Path(path_text))
    return result


def _aad(profile_id: str, owner_user_id: str, key_version: int) -> bytes:
    return f"profile_id={profile_id};owner={owner_user_id};key_version={key_version}".encode("utf-8")


def _fingerprint(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


def _mask_profile(row: sqlite3.Row | dict[str, Any], *, include_models: bool = False, models: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    fingerprint = row["key_fingerprint"]
    result = {
        "id": row["profile_id"],
        "profile_id": row["profile_id"],
        "owner_user_id": row["owner_user_id"],
        "name": row["label"],
        "label": row["label"],
        "provider": row["provider"],
        "base_url": row["base_url"],
        "api_style": row["api_style"],
        "has_api_key": True,
        "api_key_mask": f"••••{fingerprint[-4:]}",
        "api_key_fingerprint": fingerprint,
        "key_fingerprint": fingerprint,
        "status": row["status"],
        "default_model_id": row["default_model"],
        "default_model": row["default_model"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if include_models:
        result["models"] = models or []
    return result


def _profile_name(body: ModelProfileCreate | ModelProfilePatch, fallback: str | None = None) -> str:
    return body.name or body.label or fallback or ""


def _default_model(body: ModelProfileCreate | ModelProfilePatch, fallback: str | None = None) -> str | None:
    if body.default_model_id is not None:
        return body.default_model_id
    if body.default_model is not None:
        return body.default_model
    return fallback


def _model_to_dict(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return {
        "profile_id": row["profile_id"],
        "id": row["model_id"],
        "display_name": row["display_name"],
        "api_style": row["api_style"],
        "chat_eligible": bool(row["chat_eligible"]),
        "discovered_at": row["discovered_at"],
        "metadata": json.loads(row["metadata_json"] or "{}"),
    }


def _delegated_model_to_dict(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    """Return only fields that are safe to expose to a delegated workspace."""
    return {
        "id": row["model_id"],
        "display_name": row["display_name"],
        "chat_eligible": True,
    }


def _origin(url: str) -> str:
    parsed = urlparse(url)
    port = ""
    if parsed.port is not None:
        port = f":{parsed.port}"
    return f"{parsed.scheme}://{parsed.hostname}{port}"


def normalize_provider_base_url(url: str, settings: Settings) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"error": "invalid_base_url"})
    normalized = urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            "",
            "",
            "",
        )
    )
    origin = _origin(normalized)
    if parsed.scheme != "https":
        allowed = (
            settings.allow_local_model_providers
            and origin in set(settings.model_provider_origin_allowlist)
        )
        if not allowed:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"error": "model_provider_requires_https"})
    if _host_is_private(parsed.hostname):
        allowed = (
            settings.allow_local_model_providers
            and origin in set(settings.model_provider_origin_allowlist)
        )
        if not allowed:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"error": "model_provider_private_url_not_allowed"})
    validate_provider_dns_safety(normalized, settings)
    return normalized


def validate_provider_dns_safety(url: str, settings: Settings) -> None:
    parsed = urlparse(url)
    if not parsed.hostname:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"error": "invalid_base_url"})
    origin = _origin(url)
    if settings.allow_local_model_providers and origin in set(settings.model_provider_origin_allowlist):
        return
    try:
        addresses = socket.getaddrinfo(
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"error": "model_provider_host_unresolvable"}) from exc
    if not addresses or any(_host_is_private(item[4][0]) for item in addresses):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"error": "model_provider_private_url_not_allowed"})


def _provider_endpoint(base_url: str, path: str) -> str:
    """Build an OpenAI-compatible endpoint from either a root or API base URL.

    CC switch (and the Codex client it configures) accepts a provider root such
    as ``https://host`` and adds the conventional ``/v1`` prefix internally.
    The Hub settings page accepts both that form and an explicit
    ``https://host/v1``.  Keep the value stored by the user intact, but apply
    the same prefix rule at the final request boundary so both forms behave
    identically.
    """
    parsed = urlparse(base_url.strip())
    if not parsed.path.strip("/"):
        base_url = urlunparse(
            (parsed.scheme, parsed.netloc, "/v1", "", "", "")
        )
    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def _profile_by_owner(conn: sqlite3.Connection, profile_id: str, owner_user_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM hub_model_profiles WHERE profile_id = ? AND owner_user_id = ?",
        (profile_id, owner_user_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"error": "model_profile_not_found"})
    return row


def _active_profile(conn: sqlite3.Connection, profile_id: str, owner_user_id: str) -> sqlite3.Row:
    row = _profile_by_owner(conn, profile_id, owner_user_id)
    if row["status"] != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "model_profile_disabled"})
    return row


def create_profile(
    conn: sqlite3.Connection,
    service: ModelProfileService,
    *,
    owner_user_id: str,
    body: ModelProfileCreate,
) -> dict[str, Any]:
    service.require_enabled()
    profile_id = new_id("mp")
    base_url = normalize_provider_base_url(body.base_url, service.settings)
    encrypted, nonce, fingerprint = service.encrypt_key(
        profile_id=profile_id,
        owner_user_id=owner_user_id,
        key_version=service.current_key_version,
        api_key=body.api_key,
    )
    now = now_iso()
    conn.execute(
        """
        INSERT INTO hub_model_profiles (
          profile_id, owner_user_id, label, provider, base_url, api_style,
          encrypted_api_key, encrypted_api_key_nonce, key_version, key_fingerprint,
          status, default_model, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            profile_id,
            owner_user_id,
            _profile_name(body),
            body.provider,
            base_url,
            body.api_style,
            encrypted,
            nonce,
            service.current_key_version,
            fingerprint,
            body.status,
            _default_model(body),
            now,
            now,
        ),
    )
    record_model_audit(
        conn,
        "model_profile_created",
        actor=owner_user_id,
        profile_id=profile_id,
        safe_detail={"provider": body.provider, "api_style": body.api_style, "origin": _origin(base_url)},
    )
    return _mask_profile(conn.execute("SELECT * FROM hub_model_profiles WHERE profile_id = ?", (profile_id,)).fetchone())


def list_profiles(conn: sqlite3.Connection, owner_user_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM hub_model_profiles
        WHERE owner_user_id = ?
        ORDER BY updated_at DESC, rowid DESC
        """,
        (owner_user_id,),
    ).fetchall()
    return [_mask_profile(row) for row in rows]


def get_profile(conn: sqlite3.Connection, owner_user_id: str, profile_id: str) -> dict[str, Any]:
    row = _profile_by_owner(conn, profile_id, owner_user_id)
    models = [
        _model_to_dict(model)
        for model in conn.execute(
            """
            SELECT * FROM hub_model_profile_models
            WHERE profile_id = ?
            ORDER BY chat_eligible DESC, display_name COLLATE NOCASE
            """,
            (profile_id,),
        ).fetchall()
    ]
    return _mask_profile(row, include_models=True, models=models)


def patch_profile(
    conn: sqlite3.Connection,
    service: ModelProfileService,
    *,
    owner_user_id: str,
    profile_id: str,
    body: ModelProfilePatch,
) -> dict[str, Any]:
    row = _profile_by_owner(conn, profile_id, owner_user_id)
    data = body.model_dump(exclude_unset=True)
    if not data:
        return get_profile(conn, owner_user_id, profile_id)
    new_base = normalize_provider_base_url(data["base_url"], service.settings) if "base_url" in data else row["base_url"]
    if "base_url" in data and "api_key" not in data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"error": "api_key_required_for_base_url_change"})
    new_key_version = int(row["key_version"])
    encrypted = row["encrypted_api_key"]
    nonce = row["encrypted_api_key_nonce"]
    fingerprint = row["key_fingerprint"]
    if "api_key" in data:
        new_key_version = service.current_key_version
        encrypted, nonce, fingerprint = service.encrypt_key(
            profile_id=profile_id,
            owner_user_id=owner_user_id,
            key_version=new_key_version,
            api_key=data["api_key"],
        )
    old_status = row["status"]
    conn.execute(
        """
        UPDATE hub_model_profiles
        SET label = ?, provider = ?, base_url = ?, api_style = ?,
            encrypted_api_key = ?, encrypted_api_key_nonce = ?, key_version = ?,
            key_fingerprint = ?, status = ?, default_model = ?, updated_at = ?
        WHERE profile_id = ? AND owner_user_id = ?
        """,
        (
            data.get("name") or data.get("label") or row["label"],
            data.get("provider", row["provider"]),
            new_base,
            data.get("api_style", row["api_style"]),
            encrypted,
            nonce,
            new_key_version,
            fingerprint,
            data.get("status", row["status"]),
            data.get("default_model_id", data.get("default_model", row["default_model"])),
            now_iso(),
            profile_id,
            owner_user_id,
        ),
    )
    new_status = data.get("status", row["status"])
    runtime_fields_changed = bool({"provider", "base_url", "api_key", "api_style"}.intersection(data))
    if old_status == "active" and new_status == "disabled":
        revoke_model_runtime_state(
            conn,
            owner_user_id=owner_user_id,
            profile_id=profile_id,
            reason="profile_disabled",
        )
    elif runtime_fields_changed:
        revoke_model_runtime_state(
            conn,
            owner_user_id=owner_user_id,
            profile_id=profile_id,
            reason="profile_runtime_changed",
        )
    record_model_audit(
        conn,
        "model_profile_updated",
        actor=owner_user_id,
        profile_id=profile_id,
        safe_detail={"changed": sorted(data), "origin": _origin(new_base)},
    )
    return get_profile(conn, owner_user_id, profile_id)


def delete_profile(conn: sqlite3.Connection, owner_user_id: str, profile_id: str) -> None:
    _profile_by_owner(conn, profile_id, owner_user_id)
    binding = conn.execute(
        "SELECT binding_id FROM hub_model_bindings WHERE owner_user_id = ? AND profile_id = ? LIMIT 1",
        (owner_user_id, profile_id),
    ).fetchone()
    if binding is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "model_profile_still_bound"})
    revoke_model_runtime_state(
        conn,
        owner_user_id=owner_user_id,
        profile_id=profile_id,
        reason="profile_deleted",
        delete_grants=True,
    )
    conn.execute(
        "DELETE FROM hub_model_profiles WHERE profile_id = ? AND owner_user_id = ?",
        (profile_id, owner_user_id),
    )
    record_model_audit(
        conn,
        "model_profile_deleted",
        actor=owner_user_id,
        profile_id=profile_id,
    )


async def test_profile_connection(
    conn: sqlite3.Connection,
    service: ModelProfileService,
    *,
    owner_user_id: str,
    profile_id: str,
) -> dict[str, Any]:
    profile = _active_profile(conn, profile_id, owner_user_id)
    api_key = service.decrypt_key(profile)
    started = time.perf_counter()
    try:
        payload = await _fetch_models(profile["base_url"], api_key, service.settings)
    except HTTPException:
        raise
    except Exception as exc:
        record_model_audit(
            conn,
            "model_profile_test_failed",
            actor=owner_user_id,
            profile_id=profile_id,
            error_code="provider_unreachable",
        )
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail={"error": "provider_unreachable"}) from exc
    latency_ms = int((time.perf_counter() - started) * 1000)
    count = len(_models_from_payload(payload, profile["api_style"]))
    record_model_audit(
        conn,
        "model_profile_tested",
        actor=owner_user_id,
        profile_id=profile_id,
        safe_detail={"latency_ms": latency_ms, "model_count": count},
    )
    return {"status": "ok", "latency_ms": latency_ms, "model_count": count}


async def discover_profile_models(
    conn: sqlite3.Connection,
    service: ModelProfileService,
    *,
    owner_user_id: str,
    profile_id: str,
) -> dict[str, Any]:
    profile = _active_profile(conn, profile_id, owner_user_id)
    api_key = service.decrypt_key(profile)
    try:
        payload = await _fetch_models(profile["base_url"], api_key, service.settings)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail={"error": "provider_unreachable"}) from exc
    models = _models_from_payload(payload, profile["api_style"])
    now = now_iso()
    for model in models:
        conn.execute(
            """
            INSERT INTO hub_model_profile_models (
              profile_id, model_id, display_name, api_style, chat_eligible, discovered_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile_id, model_id) DO UPDATE SET
              display_name = excluded.display_name,
              api_style = excluded.api_style,
              chat_eligible = excluded.chat_eligible,
              discovered_at = excluded.discovered_at,
              metadata_json = excluded.metadata_json
            """,
            (
                profile_id,
                model["id"],
                model["display_name"],
                profile["api_style"],
                1 if model["chat_eligible"] else 0,
                now,
                json.dumps(model.get("metadata", {}), ensure_ascii=False, sort_keys=True),
            ),
        )
    record_model_audit(
        conn,
        "model_profile_discovered",
        actor=owner_user_id,
        profile_id=profile_id,
        safe_detail={"model_count": len(models)},
    )
    return {
        "models": [
            _model_to_dict(row)
            for row in conn.execute(
                "SELECT * FROM hub_model_profile_models WHERE profile_id = ? ORDER BY display_name COLLATE NOCASE",
                (profile_id,),
            ).fetchall()
        ]
    }


async def _fetch_models(base_url: str, api_key: str, settings: Settings) -> dict[str, Any]:
    validate_provider_dns_safety(base_url, settings)
    timeout = httpx.Timeout(
        settings.request_timeout_seconds,
        connect=settings.connect_timeout_seconds,
        read=settings.request_timeout_seconds,
    )
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        response = await client.get(
            _provider_endpoint(base_url, "models"),
            headers={"authorization": f"Bearer {api_key}", "accept": "application/json"},
        )
    if response.status_code in {401, 403}:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"error": "provider_auth_failed"})
    if response.status_code == 429:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail={"error": "provider_rate_limited"})
    if response.status_code < 200 or response.status_code >= 300:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail={"error": "provider_unreachable"})
    try:
        payload = response.json()
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail={"error": "provider_protocol_error"}) from exc
    if not isinstance(payload, dict):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail={"error": "provider_protocol_error"})
    return payload


def _models_from_payload(payload: dict[str, Any], api_style: str) -> list[dict[str, Any]]:
    raw_models = payload.get("data")
    if not isinstance(raw_models, list):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail={"error": "provider_protocol_error"})
    result: list[dict[str, Any]] = []
    for item in raw_models:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        model_id = item["id"]
        lowered = model_id.lower()
        chat_eligible = lowered != "codex-auto-review" and not any(
            marker in lowered
            for marker in (
                "embedding",
                "tts",
                "whisper",
                "audio",
                "moderation",
                "rerank",
                "realtime",
                "transcription",
                "image",
                "dall-e",
                "sora",
            )
        )
        result.append(
            {
                "id": model_id,
                "display_name": item.get("name") if isinstance(item.get("name"), str) else model_id,
                "api_style": api_style,
                "chat_eligible": chat_eligible,
                "metadata": {"owned_by": item.get("owned_by")} if item.get("owned_by") else {},
            }
        )
    return result


def bind_model(
    conn: sqlite3.Connection,
    *,
    owner_user_id: str,
    agent_id: str,
    body: ModelBindingRequest,
) -> dict[str, Any]:
    profile = _active_profile(conn, body.profile_id, owner_user_id)
    model = conn.execute(
        """
        SELECT * FROM hub_model_profile_models
        WHERE profile_id = ? AND model_id = ? AND chat_eligible = 1
        """,
        (body.profile_id, body.model_id),
    ).fetchone()
    if model is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "model_not_allowed"})
    if agent_id:
        ensure_agent_accepts_platform_model(conn, agent_id, api_style=profile["api_style"])
    revoke_model_runtime_state(
        conn,
        owner_user_id=owner_user_id,
        agent_id=agent_id or None,
        reason="model_binding_changed",
    )
    now = now_iso()
    binding_id = new_id("mb")
    conn.execute(
        """
        INSERT INTO hub_model_bindings (
          binding_id, owner_user_id, agent_id, profile_id, model_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(owner_user_id, agent_id) DO UPDATE SET
          profile_id = excluded.profile_id,
          model_id = excluded.model_id,
          updated_at = excluded.updated_at
        """,
        (binding_id, owner_user_id, agent_id, body.profile_id, body.model_id, now, now),
    )
    record_model_audit(
        conn,
        "model_binding_updated",
        actor=owner_user_id,
        agent_id=agent_id or None,
        profile_id=body.profile_id,
        model_id=body.model_id,
    )
    return get_binding(conn, owner_user_id, agent_id)


def get_binding(conn: sqlite3.Connection, owner_user_id: str, agent_id: str = "") -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT b.*, p.label, p.provider, p.base_url, p.api_style, p.status
        FROM hub_model_bindings b
        JOIN hub_model_profiles p ON p.profile_id = b.profile_id
        WHERE b.owner_user_id = ? AND b.agent_id = ?
        """,
        (owner_user_id, agent_id),
    ).fetchone()
    if row is None:
        return {
            "scope_type": "agent" if agent_id else "global",
            "scope_id": agent_id or "global",
            "agent_id": agent_id,
            "binding": None,
        }
    return {
        "scope_type": "agent" if agent_id else "global",
        "scope_id": agent_id or "global",
        "agent_id": agent_id,
        "binding": {
            "profile_id": row["profile_id"],
            "model_id": row["model_id"],
            "profile_label": row["label"],
            "provider": row["provider"],
            "api_style": row["api_style"],
            "status": row["status"],
            "updated_at": row["updated_at"],
        },
    }


def ensure_agent_accepts_platform_model(
    conn: sqlite3.Connection,
    agent_id: str,
    *,
    api_style: str,
) -> dict[str, Any]:
    _, version = get_active_version(conn, agent_id)
    manifest = version["manifest"]
    capabilities = set(manifest.get("capabilities", []))
    runtime = manifest.get("model_runtime") or {"mode": "agent_managed"}
    mode = runtime.get("mode", "agent_managed")
    supported = set(runtime.get("supported_api_styles") or [])
    if "platform-model-gateway" not in capabilities or mode not in {"platform_optional", "platform_required"}:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "agent_model_gateway_not_supported"})
    if supported and api_style not in supported:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "model_api_style_not_supported"})
    return version


def resolve_binding_for_grant(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    agent_id: str,
    profile_id: str | None = None,
    model_id: str | None = None,
) -> tuple[str, str, sqlite3.Row]:
    row = conn.execute(
        """
        SELECT b.profile_id, b.model_id, p.*, m.model_id AS eligible_model_id
        FROM hub_model_bindings b
        JOIN hub_model_profiles p ON p.profile_id = b.profile_id
        LEFT JOIN hub_model_profile_models m
          ON m.profile_id = b.profile_id
         AND m.model_id = b.model_id
         AND m.chat_eligible = 1
        WHERE b.owner_user_id = ? AND b.agent_id IN (?, '')
        ORDER BY CASE WHEN b.agent_id = ? THEN 0 ELSE 1 END
        LIMIT 1
        """,
        (user_id, agent_id, agent_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"error": "model_binding_not_found"})
    if row["status"] != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "model_profile_disabled"})
    if row["eligible_model_id"] is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "model_not_allowed"})
    if profile_id is not None and profile_id != row["profile_id"]:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "model_not_allowed"})
    resolved_model_id = model_id or row["model_id"]
    eligible = conn.execute(
        """
        SELECT model_id FROM hub_model_profile_models
        WHERE profile_id = ? AND model_id = ? AND chat_eligible = 1
        """,
        (row["profile_id"], resolved_model_id),
    ).fetchone()
    if eligible is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "model_not_allowed"})
    ensure_agent_accepts_platform_model(conn, agent_id, api_style=row["api_style"])
    return row["profile_id"], resolved_model_id, row


def delegated_model_catalog(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    agent_id: str,
) -> dict[str, Any]:
    profile_id, default_model_id, _ = resolve_binding_for_grant(
        conn,
        user_id=user_id,
        agent_id=agent_id,
    )
    rows = conn.execute(
        """
        SELECT model_id, display_name
        FROM hub_model_profile_models
        WHERE profile_id = ? AND chat_eligible = 1
        ORDER BY lower(display_name), lower(model_id)
        """,
        (profile_id,),
    ).fetchall()
    return {
        "default_model_id": default_model_id,
        "models": [_delegated_model_to_dict(row) for row in rows],
    }


def _resolve_user_global_binding(
    conn: sqlite3.Connection,
    service: ModelProfileService,
    *,
    user_id: str,
) -> tuple[str, str, sqlite3.Row]:
    service.require_enabled()
    row = conn.execute(
        """
        SELECT b.profile_id, b.model_id, p.*, m.model_id AS eligible_model_id
        FROM hub_model_bindings b
        JOIN hub_model_profiles p ON p.profile_id = b.profile_id
        LEFT JOIN hub_model_profile_models m
          ON m.profile_id = b.profile_id
         AND m.model_id = b.model_id
         AND m.chat_eligible = 1
        WHERE b.owner_user_id = ? AND b.agent_id = ''
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"error": "model_binding_not_found"})
    if row["status"] != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "model_profile_disabled"})
    if row["eligible_model_id"] is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "model_not_allowed"})
    return row["profile_id"], row["model_id"], row


def _platform_internal_grant(
    *,
    user_id: str,
    agent_id: str,
    profile_id: str,
    model_id: str,
    request_id: str,
) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "agent_id": agent_id,
        "profile_id": profile_id,
        "model_id": model_id,
        "delegation_id": "platform-internal",
        "request_id": request_id,
    }


async def call_user_global_model(
    conn: sqlite3.Connection,
    service: ModelProfileService,
    *,
    user_id: str,
    request_id: str,
    instructions: str,
    messages: list[ModelMessage],
    max_output_tokens: int | None = None,
) -> PlatformModelResult:
    profile_id, model_id, profile = _resolve_user_global_binding(conn, service, user_id=user_id)
    api_key = service.decrypt_key(profile)
    body = ModelGenerateRequest(
        instructions=instructions,
        messages=messages,
        max_output_tokens=max_output_tokens,
        stream=False,
    )
    grant = _platform_internal_grant(
        user_id=user_id,
        agent_id="hub-home-assistant",
        profile_id=profile_id,
        model_id=model_id,
        request_id=request_id,
    )
    started = time.perf_counter()
    try:
        text, usage = await _call_provider_non_stream(service, grant, dict(profile), api_key, body)
    except HTTPException as exc:
        record_model_audit(
            conn,
            "home_assistant_model_error",
            actor=user_id,
            agent_id="hub-home-assistant",
            profile_id=profile_id,
            model_id=model_id,
            request_id=request_id,
            error_code=_error_code(exc),
        )
        raise
    record_model_audit(
        conn,
        "home_assistant_model_completed",
        actor=user_id,
        agent_id="hub-home-assistant",
        profile_id=profile_id,
        model_id=model_id,
        request_id=request_id,
        safe_detail={"duration_ms": int((time.perf_counter() - started) * 1000), "usage": usage},
    )
    return PlatformModelResult(output_text=text, usage=usage, model=model_id)


async def stream_user_global_model(
    conn: sqlite3.Connection,
    service: ModelProfileService,
    *,
    user_id: str,
    request_id: str,
    instructions: str,
    messages: list[ModelMessage],
    max_output_tokens: int | None = None,
) -> StreamingResponse:
    profile_id, model_id, profile = _resolve_user_global_binding(conn, service, user_id=user_id)
    api_key = service.decrypt_key(profile)
    body = ModelGenerateRequest(
        instructions=instructions,
        messages=messages,
        max_output_tokens=max_output_tokens,
        stream=True,
    )
    grant = _platform_internal_grant(
        user_id=user_id,
        agent_id="hub-home-assistant",
        profile_id=profile_id,
        model_id=model_id,
        request_id=request_id,
    )
    return StreamingResponse(
        _stream_provider(
            conn_path=service.settings.database_path,
            service=service,
            grant=grant,
            profile=dict(profile),
            api_key=api_key,
            body=body,
        ),
        media_type="text/event-stream",
        headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
    )


def create_gateway_grant(
    conn: sqlite3.Connection,
    service: ModelProfileService,
    *,
    user_id: str,
    agent_id: str,
    delegation_id: str,
    request_id: str,
    model_id: str | None = None,
) -> dict[str, Any]:
    resolved_profile_id, resolved_model_id, profile = resolve_binding_for_grant(
        conn,
        user_id=user_id,
        agent_id=agent_id,
        model_id=model_id,
    )
    try:
        token = service.sign_gateway_grant(
            conn,
            user_id=user_id,
            agent_id=agent_id,
            profile_id=resolved_profile_id,
            model_id=resolved_model_id,
            delegation_id=delegation_id,
            request_id=request_id,
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "model_request_replayed"}) from exc
    record_model_audit(
        conn,
        "model_gateway_grant_issued",
        actor=user_id,
        agent_id=agent_id,
        profile_id=resolved_profile_id,
        model_id=resolved_model_id,
    )
    return {
        "model_gateway_url": "/api/model-gateway/v1/generate",
        "access_token": token,
        "grant": token,
        "token_type": "Bearer",
        "expires_in": max(1, min(120, service.settings.model_gateway_grant_ttl_seconds)),
        "model_id": resolved_model_id,
        "profile": _mask_profile(profile),
        "model": {"id": resolved_model_id},
    }


def exchange_model_grant(
    conn: sqlite3.Connection,
    service: ModelProfileService,
    *,
    request: Request,
    body: ModelGrantExchangeRequest,
) -> dict[str, Any]:
    agent_id = authenticate_client_secret_basic(conn, request)
    try:
        delegation = consume_model_delegation_for_exchange(
            conn,
            service,
            agent_id=agent_id,
            token=body.model_delegation_token,
        )
        user_id = delegation["user_id"]
        delegation_id = delegation["delegation_id"]
    except HTTPException as exc:
        if _error_code(exc) != "model_delegation_not_found":
            raise
        payload = service.decode_workspace_token(body.model_delegation_token, audience=agent_id)
        delegation = materialize_connected_delegation_for_exchange(
            conn,
            service,
            agent_id=agent_id,
            token=body.model_delegation_token,
            payload=payload,
        )
        user_id = payload["sub"]
        delegation_id = delegation["delegation_id"]
    return create_gateway_grant(
        conn,
        service,
        user_id=user_id,
        agent_id=agent_id,
        delegation_id=delegation_id,
        request_id=body.request_id,
        model_id=body.requested_model_id,
    )


def create_model_delegation_if_supported(
    conn: sqlite3.Connection,
    service: ModelProfileService,
    *,
    agent_id: str,
    version_id: str,
    user_id: str,
    display_name: str,
) -> dict[str, Any] | None:
    if not service.settings.model_profiles_enabled:
        return None
    try:
        _, version = get_active_version(conn, agent_id)
        if version["version_id"] != version_id:
            return None
        ensure_agent_accepts_platform_model(conn, agent_id, api_style="responses")
    except HTTPException:
        try:
            ensure_agent_accepts_platform_model(conn, agent_id, api_style="chat_completions")
        except HTTPException:
            return None
    ttl = max(60, int(service.settings.model_delegation_ttl_seconds))
    token = random_token(48)
    expires = datetime.now(UTC) + timedelta(seconds=ttl)
    conn.execute(
        """
        INSERT INTO hub_model_delegations (
          token_hash, delegation_id, user_id, display_name, agent_id, version_id,
          scope_type, scope_id, status, scopes_json, expires_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'featured_workspace', ?, 'active', ?, ?, ?)
        """,
        (
            sha256_text(token),
            new_id("mdel"),
            user_id,
            display_name,
            agent_id,
            version_id,
            f"workspace:{version_id}:{user_id}",
            json.dumps(["model:delegate"], ensure_ascii=False),
            expires.isoformat(timespec="seconds").replace("+00:00", "Z"),
            now_iso(),
        ),
    )
    record_model_audit(
        conn,
        "model_delegation_created",
        actor=user_id,
        agent_id=agent_id,
        safe_detail={"version_id": version_id, "ttl_seconds": ttl},
    )
    try:
        catalog = delegated_model_catalog(conn, user_id=user_id, agent_id=agent_id)
    except HTTPException:
        catalog = {"default_model_id": None, "models": []}
    return {
        "model_delegation_token": token,
        "model_delegation_expires_in": ttl,
        "model_delegation_default_model_id": catalog["default_model_id"],
        "model_delegation_models": catalog["models"],
    }


def materialize_connected_delegation_for_exchange(
    conn: sqlite3.Connection,
    service: ModelProfileService,
    *,
    agent_id: str,
    token: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    service.require_enabled()
    scopes = set(str(payload.get("scope") or "").split())
    if "chat:invoke" not in scopes:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"error": "model_delegation_invalid"})
    scope_id = str(payload.get("request_id") or payload.get("jti") or "")
    if not scope_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"error": "model_delegation_invalid"})
    agent = conn.execute(
        "SELECT active_version_id, status FROM hub_agents WHERE agent_id = ?",
        (agent_id,),
    ).fetchone()
    version_id = str(payload.get("agent_version_id") or "")
    if agent is None or agent["status"] != "active" or agent["active_version_id"] != version_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"error": "model_delegation_invalid"})
    invocation = conn.execute(
        """
        SELECT agent_id, version_id, user_id, status
        FROM hub_invocations
        WHERE invocation_id = ?
        """,
        (scope_id,),
    ).fetchone()
    if (
        invocation is None
        or invocation["status"] != "started"
        or invocation["agent_id"] != agent_id
        or invocation["version_id"] != version_id
        or invocation["user_id"] != payload["sub"]
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"error": "model_delegation_invalid"})
    delegation_id = new_id("mdel")
    now = now_iso()
    try:
        conn.execute(
            """
            INSERT INTO hub_model_delegations (
              token_hash, delegation_id, user_id, display_name, agent_id, version_id,
              scope_type, scope_id, status, scopes_json, expires_at, consumed_at,
              last_used_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'connected_run', ?, 'consumed', ?, ?, ?, ?, ?)
            """,
            (
                sha256_text(token),
                delegation_id,
                payload["sub"],
                str(payload.get("name") or payload["sub"]),
                agent_id,
                version_id,
                scope_id,
                json.dumps(["model:delegate"], ensure_ascii=False),
                datetime.fromtimestamp(int(payload["exp"]), UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
                now,
                now,
                now,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"error": "model_delegation_consumed"}) from exc
    record_model_audit(
        conn,
        "model_delegation_connected_consumed",
        actor=payload["sub"],
        agent_id=agent_id,
        safe_detail={"scope_id": scope_id},
    )
    return {
        "delegation_id": delegation_id,
        "user_id": payload["sub"],
        "agent_id": agent_id,
        "version_id": version_id,
        "scope_type": "connected_run",
        "scope_id": scope_id,
    }


def consume_model_delegation_for_exchange(
    conn: sqlite3.Connection,
    service: ModelProfileService,
    *,
    agent_id: str,
    token: str,
) -> dict[str, Any]:
    service.require_enabled()
    expired = conn.execute(
        """
        UPDATE hub_model_delegations
        SET status = 'expired'
        WHERE status = 'active' AND expires_at < ?
        """,
        (now_iso(),),
    ).rowcount
    if expired:
        conn.commit()
    row = conn.execute(
        "SELECT * FROM hub_model_delegations WHERE token_hash = ?",
        (sha256_text(token),),
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"error": "model_delegation_not_found"})
    if row["agent_id"] != agent_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"error": "model_delegation_invalid"})
    if row["revoked_at"] is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"error": "model_delegation_revoked"})
    if row["status"] == "revoked":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"error": "model_delegation_revoked"})
    if row["scope_type"] == "connected_run" or row["status"] == "consumed":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"error": "model_delegation_consumed"})
    if row["status"] == "expired":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"error": "model_delegation_expired"})
    if datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00")) < datetime.now(UTC):
        conn.execute(
            "UPDATE hub_model_delegations SET status = 'expired' WHERE token_hash = ? AND status = 'active'",
            (sha256_text(token),),
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"error": "model_delegation_expired"})
    agent = conn.execute(
        "SELECT active_version_id, status FROM hub_agents WHERE agent_id = ?",
        (agent_id,),
    ).fetchone()
    if agent is None or agent["status"] != "active" or agent["active_version_id"] != row["version_id"]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"error": "model_delegation_invalid"})
    conn.execute(
        """
        UPDATE hub_model_delegations
        SET last_used_at = ?
        WHERE token_hash = ? AND status = 'active'
        """,
        (now_iso(), sha256_text(token)),
    )
    return dict(row)


def revoke_model_delegation(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
    token: str,
) -> dict[str, Any]:
    now = now_iso()
    row = conn.execute(
        "SELECT delegation_id, user_id FROM hub_model_delegations WHERE token_hash = ? AND agent_id = ?",
        (sha256_text(token), agent_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"error": "model_delegation_not_found"})
    conn.execute(
        """
        UPDATE hub_model_delegations
        SET status = 'revoked', revoked_at = ?
        WHERE delegation_id = ? AND status != 'revoked'
        """,
        (now, row["delegation_id"]),
    )
    conn.execute(
        """
        UPDATE hub_model_gateway_grants
        SET status = 'revoked', revoked_at = ?
        WHERE delegation_id = ? AND status = 'issued'
        """,
        (now, row["delegation_id"]),
    )
    record_model_audit(
        conn,
        "model_delegation_revoked",
        actor=agent_id,
        agent_id=agent_id,
        safe_detail={"delegation_id": row["delegation_id"]},
    )
    return {"status": "revoked"}


def revoke_model_runtime_state(
    conn: sqlite3.Connection,
    *,
    owner_user_id: str,
    agent_id: str | None = None,
    profile_id: str | None = None,
    reason: str,
    delete_grants: bool = False,
) -> None:
    now = now_iso()
    delegation_rows = conn.execute(
        """
        SELECT DISTINCT d.delegation_id
        FROM hub_model_delegations d
        WHERE d.user_id = ?
          AND (? IS NULL OR d.agent_id = ?)
        """,
        (owner_user_id, agent_id, agent_id),
    ).fetchall()
    delegation_ids = [row["delegation_id"] for row in delegation_rows if row["delegation_id"]]
    if delegation_ids:
        placeholders = ",".join("?" for _ in delegation_ids)
        conn.execute(
            f"""
            UPDATE hub_model_delegations
            SET status = 'revoked', revoked_at = ?
            WHERE delegation_id IN ({placeholders}) AND status = 'active'
            """,
            (now, *delegation_ids),
        )
    if profile_id:
        if delete_grants:
            conn.execute("DELETE FROM hub_model_gateway_grants WHERE profile_id = ?", (profile_id,))
        else:
            conn.execute(
                """
                UPDATE hub_model_gateway_grants
                SET status = 'revoked', revoked_at = ?
                WHERE profile_id = ? AND status = 'issued'
                """,
                (now, profile_id),
            )
    if delegation_ids and not delete_grants:
        placeholders = ",".join("?" for _ in delegation_ids)
        conn.execute(
            f"""
            UPDATE hub_model_gateway_grants
            SET status = 'revoked', revoked_at = ?
            WHERE delegation_id IN ({placeholders}) AND status = 'issued'
            """,
            (now, *delegation_ids),
        )
    record_model_audit(
        conn,
        "model_runtime_state_revoked",
        actor=owner_user_id,
        agent_id=agent_id,
        profile_id=profile_id,
        safe_detail={"reason": reason, "delegation_count": len(delegation_ids)},
    )


async def model_generate_stream(
    conn: sqlite3.Connection,
    service: ModelProfileService,
    *,
    token: str,
    body: ModelGenerateRequest,
) -> StreamingResponse | JSONResponse:
    grant = consume_gateway_grant(conn, service, token)
    profile = conn.execute(
        "SELECT * FROM hub_model_profiles WHERE profile_id = ?",
        (grant["profile_id"],),
    ).fetchone()
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"error": "model_profile_not_found"})
    if profile["status"] != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "model_profile_disabled"})
    api_key = service.decrypt_key(profile)
    if body.stream:
        return StreamingResponse(
            _stream_provider(
                conn_path=service.settings.database_path,
                service=service,
                grant=grant,
                profile=dict(profile),
                api_key=api_key,
                body=body,
            ),
            media_type="text/event-stream",
            headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
        )
    started = time.perf_counter()
    try:
        text, usage = await _call_provider_non_stream(service, grant, dict(profile), api_key, body)
    except HTTPException as exc:
        record_model_audit(
            conn,
            "model_gateway_error",
            actor=grant["user_id"],
            agent_id=grant["agent_id"],
            profile_id=grant["profile_id"],
            model_id=grant["model_id"],
            request_id=grant["request_id"],
            error_code=_error_code(exc),
        )
        raise
    record_model_audit(
        conn,
        "model_gateway_completed",
        actor=grant["user_id"],
        agent_id=grant["agent_id"],
        profile_id=grant["profile_id"],
        model_id=grant["model_id"],
        request_id=grant["request_id"],
        safe_detail={"duration_ms": int((time.perf_counter() - started) * 1000), "usage": usage},
    )
    return JSONResponse({"output_text": text, "usage": usage, "model": grant["model_id"]})


def consume_gateway_grant(
    conn: sqlite3.Connection,
    service: ModelProfileService,
    token: str,
) -> dict[str, Any]:
    service.require_enabled()
    expired = conn.execute(
        """
        UPDATE hub_model_gateway_grants
        SET status = 'expired'
        WHERE status = 'issued' AND expires_at < ?
        """,
        (now_iso(),),
    ).rowcount
    if expired:
        conn.commit()
    try:
        payload = jwt.decode(
            token,
            service.identity._public_key,  # noqa: SLF001
            algorithms=["EdDSA"],
            audience=MODEL_AUDIENCE,
            issuer=service.settings.issuer,
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"error": "model_grant_expired"}) from exc
    except Exception as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"error": "model_grant_invalid"}) from exc
    if payload.get("scope") != "model:invoke":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"error": "model_grant_invalid"})
    row = conn.execute(
        "SELECT * FROM hub_model_gateway_grants WHERE jti = ?",
        (payload.get("jti"),),
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"error": "model_grant_invalid"})
    if row["status"] == "revoked":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"error": "model_grant_revoked"})
    if row["status"] == "expired":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"error": "model_grant_expired"})
    if row["used_at"] is not None or row["status"] == "consumed":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"error": "model_grant_replayed"})
    if datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00")) < datetime.now(UTC):
        conn.execute(
            "UPDATE hub_model_gateway_grants SET status = 'expired' WHERE jti = ? AND status = 'issued'",
            (row["jti"],),
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"error": "model_grant_expired"})
    for key in ("user_id", "agent_id", "profile_id", "model_id", "delegation_id", "request_id"):
        if row[key] != payload[key]:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"error": "model_grant_invalid"})
    binding_profile_id, binding_model_id, _ = resolve_binding_for_grant(
        conn,
        user_id=row["user_id"],
        agent_id=row["agent_id"],
        profile_id=row["profile_id"],
        model_id=row["model_id"],
    )
    if binding_profile_id != row["profile_id"] or binding_model_id != row["model_id"]:
        conn.execute(
            """
            UPDATE hub_model_gateway_grants
            SET status = 'revoked', revoked_at = ?
            WHERE jti = ?
            """,
            (now_iso(), row["jti"]),
        )
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "model_binding_changed"})
    now = now_iso()
    updated = conn.execute(
        """
        UPDATE hub_model_gateway_grants
        SET status = 'consumed', used_at = ?, consumed_at = ?
        WHERE jti = ? AND status = 'issued' AND used_at IS NULL
        """,
        (now, now, row["jti"]),
    ).rowcount
    if not updated:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"error": "model_grant_replayed"})
    return dict(row)


def _provider_request(profile: dict[str, Any], grant: dict[str, Any], body: ModelGenerateRequest) -> tuple[str, dict[str, Any]]:
    messages = [message.model_dump() for message in body.messages]
    if profile["api_style"] == "responses":
        payload: dict[str, Any] = {
            "model": grant["model_id"],
            "input": messages,
            "stream": body.stream,
        }
        if body.instructions:
            payload["instructions"] = body.instructions
        if body.reasoning_effort:
            payload["reasoning"] = {"effort": body.reasoning_effort}
        if body.max_output_tokens:
            payload["max_output_tokens"] = body.max_output_tokens
        return _provider_endpoint(profile["base_url"], "responses"), payload
    payload = {
        "model": grant["model_id"],
        "messages": ([{"role": "system", "content": body.instructions}] if body.instructions else [])
        + messages,
        "stream": body.stream,
    }
    if body.reasoning_effort:
        payload["reasoning_effort"] = body.reasoning_effort
    if body.max_output_tokens:
        payload["max_tokens"] = body.max_output_tokens
    return _provider_endpoint(profile["base_url"], "chat/completions"), payload


async def _call_provider_non_stream(
    service: ModelProfileService,
    grant: dict[str, Any],
    profile: dict[str, Any],
    api_key: str,
    body: ModelGenerateRequest,
) -> tuple[str, dict[str, int]]:
    url, payload = _provider_request(profile, grant, body)
    validate_provider_dns_safety(profile["base_url"], service.settings)
    timeout = httpx.Timeout(
        service.settings.model_gateway_timeout_seconds,
        connect=service.settings.connect_timeout_seconds,
        read=service.settings.model_gateway_timeout_seconds,
    )
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.post(
                url,
                json=payload,
                headers={
                    "authorization": f"Bearer {api_key}",
                    "content-type": "application/json",
                    "accept": "application/json",
                },
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, detail={"error": "model_gateway_timeout"}) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail={"error": "provider_unreachable"}) from exc
    _raise_provider_status(response)
    try:
        data = response.json()
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail={"error": "provider_protocol_error"}) from exc
    return _extract_text_and_usage(profile["api_style"], data)


async def _stream_provider(
    *,
    conn_path: Any,
    service: ModelProfileService,
    grant: dict[str, Any],
    profile: dict[str, Any],
    api_key: str,
    body: ModelGenerateRequest,
) -> AsyncIterator[bytes]:
    from .db import database

    started = time.perf_counter()
    yield _model_sse("model.started", {"model": grant["model_id"], "request_id": grant["request_id"]})
    usage: dict[str, int] = {}
    emitted_text: list[str] = []
    error_code: str | None = None
    try:
        url, payload = _provider_request(profile, grant, body)
        validate_provider_dns_safety(profile["base_url"], service.settings)
        timeout = httpx.Timeout(
            service.settings.model_gateway_timeout_seconds,
            connect=service.settings.connect_timeout_seconds,
            read=service.settings.model_gateway_timeout_seconds,
        )
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            async with client.stream(
                "POST",
                url,
                json=payload,
                headers={
                    "authorization": f"Bearer {api_key}",
                    "content-type": "application/json",
                    "accept": "text/event-stream",
                },
            ) as response:
                _raise_provider_status(response)
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    try:
                        item = json.loads(raw)
                    except json.JSONDecodeError:
                        error_code = "provider_protocol_error"
                        yield _model_sse("model.error", {"error": error_code})
                        return
                    delta = _stream_delta(profile["api_style"], item)
                    if delta:
                        emitted_text.append(delta)
                        yield _model_sse("model.output_text.delta", {"delta": delta})
                    final_text = _stream_final_text(profile["api_style"], item)
                    if final_text:
                        emitted = "".join(emitted_text)
                        recovered = final_text[len(emitted) :] if final_text.startswith(emitted) else ""
                        if recovered:
                            emitted_text.append(recovered)
                            yield _model_sse("model.output_text.delta", {"delta": recovered})
                    item_usage = _stream_usage(profile["api_style"], item)
                    if item_usage:
                        usage = item_usage
                        yield _model_sse("model.usage", usage)
        yield _model_sse("model.completed", {"usage": usage})
    except HTTPException as exc:
        error_code = _error_code(exc)
        yield _model_sse("model.error", {"error": error_code})
    except (httpx.TimeoutException, asyncio.TimeoutError):
        error_code = "model_gateway_timeout"
        yield _model_sse("model.error", {"error": error_code})
    except httpx.HTTPError:
        error_code = "provider_unreachable"
        yield _model_sse("model.error", {"error": error_code})
    finally:
        with database(conn_path) as conn:
            record_model_audit(
                conn,
                "model_gateway_completed" if error_code is None else "model_gateway_error",
                actor=grant["user_id"],
                agent_id=grant["agent_id"],
                profile_id=grant["profile_id"],
                model_id=grant["model_id"],
                request_id=grant["request_id"],
                error_code=error_code,
                safe_detail={"duration_ms": int((time.perf_counter() - started) * 1000), "usage": usage},
            )


def _model_sse(event: str, payload: dict[str, Any]) -> bytes:
    data = {"type": event, **payload}
    return (
        f"event: {event}\n"
        f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
    ).encode("utf-8")


def _raise_provider_status(response: Any) -> None:
    status_code = int(response.status_code)
    if 200 <= status_code < 300:
        return
    if status_code in {401, 403}:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"error": "provider_auth_failed"})
    if status_code == 429:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail={"error": "provider_rate_limited"})
    raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail={"error": "provider_unreachable"})


def _extract_text_and_usage(api_style: str, data: dict[str, Any]) -> tuple[str, dict[str, int]]:
    if api_style == "responses":
        text = data.get("output_text")
        if not isinstance(text, str):
            chunks: list[str] = []
            for item in data.get("output", []) if isinstance(data.get("output"), list) else []:
                for content in item.get("content", []) if isinstance(item, dict) else []:
                    if isinstance(content, dict) and content.get("type") in {"output_text", "text"}:
                        if isinstance(content.get("text"), str):
                            chunks.append(content["text"])
            text = "".join(chunks)
        if not isinstance(text, str):
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail={"error": "provider_protocol_error"})
        return text, _normalize_responses_usage(data.get("usage") or {})
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail={"error": "provider_protocol_error"})
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    text = message.get("content") if isinstance(message, dict) else None
    if not isinstance(text, str):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail={"error": "provider_protocol_error"})
    return text, _normalize_chat_usage(data.get("usage") or {})


def _stream_delta(api_style: str, item: dict[str, Any]) -> str:
    if api_style == "responses":
        if item.get("type") == "response.output_text.delta" and isinstance(item.get("delta"), str):
            return item["delta"]
        if isinstance(item.get("delta"), str):
            return item["delta"]
        return ""
    choices = item.get("choices")
    if isinstance(choices, list) and choices:
        delta = choices[0].get("delta") if isinstance(choices[0], dict) else None
        content = delta.get("content") if isinstance(delta, dict) else None
        return content if isinstance(content, str) else ""
    return ""


def _stream_final_text(api_style: str, item: dict[str, Any]) -> str:
    if api_style != "responses":
        return ""
    if item.get("type") == "response.output_text.done" and isinstance(item.get("text"), str):
        return item["text"]
    response = item.get("response")
    if item.get("type") != "response.completed" or not isinstance(response, dict):
        return ""
    text, _ = _extract_text_and_usage("responses", response)
    return text


def _stream_usage(api_style: str, item: dict[str, Any]) -> dict[str, int]:
    usage = item.get("usage")
    if api_style == "responses" and not isinstance(usage, dict):
        response = item.get("response")
        usage = response.get("usage") if isinstance(response, dict) else None
    if not isinstance(usage, dict):
        return {}
    return _normalize_responses_usage(usage) if api_style == "responses" else _normalize_chat_usage(usage)


def _normalize_responses_usage(usage: dict[str, Any]) -> dict[str, int]:
    output_details = usage.get("output_tokens_details") if isinstance(usage.get("output_tokens_details"), dict) else {}
    input_details = usage.get("input_tokens_details") if isinstance(usage.get("input_tokens_details"), dict) else {}
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": int(output_details.get("reasoning_tokens") or 0),
        "cached_tokens": int(input_details.get("cached_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or (input_tokens + output_tokens)),
    }


def _normalize_chat_usage(usage: dict[str, Any]) -> dict[str, int]:
    completion_details = (
        usage.get("completion_tokens_details")
        if isinstance(usage.get("completion_tokens_details"), dict)
        else {}
    )
    prompt_details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
    input_tokens = int(usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or 0)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": int(completion_details.get("reasoning_tokens") or 0),
        "cached_tokens": int(prompt_details.get("cached_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or (input_tokens + output_tokens)),
    }


def _error_code(exc: HTTPException) -> str:
    detail = exc.detail
    if isinstance(detail, dict) and isinstance(detail.get("error"), str):
        return detail["error"]
    return "model_gateway_error"


def record_model_audit(
    conn: sqlite3.Connection,
    event_type: str,
    *,
    actor: str,
    agent_id: str | None = None,
    profile_id: str | None = None,
    model_id: str | None = None,
    request_id: str | None = None,
    error_code: str | None = None,
    safe_detail: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO hub_model_gateway_audit (
          event_id, event_type, actor, agent_id, profile_id, model_id,
          request_id, error_code, safe_detail_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_id("mga"),
            event_type,
            actor,
            agent_id,
            profile_id,
            model_id,
            request_id,
            error_code,
            json.dumps(safe_detail or {}, ensure_ascii=False, sort_keys=True),
            now_iso(),
        ),
    )
