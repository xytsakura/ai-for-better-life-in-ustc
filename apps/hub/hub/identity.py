from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from fastapi import HTTPException, Request, status

from .config import Settings
from .utils import b64url, new_id, now_iso, random_token, sha256_text


_PASSWORD_HASHER = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, hash_len=32, salt_len=16)


class IdentityService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._private_key = self._load_private_key(
            settings.jwt_private_key_pem,
            settings.jwt_private_key_file,
        )
        self._public_key = self._private_key.public_key()

    @staticmethod
    def _load_private_key(pem: str | None, key_file: Path | None) -> Ed25519PrivateKey:
        if pem:
            key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
            if not isinstance(key, Ed25519PrivateKey):
                raise RuntimeError("HUB_JWT_PRIVATE_KEY_PEM must be an Ed25519 private key")
            return key
        if key_file is None:
            return Ed25519PrivateKey.generate()
        key_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            encoded = key_file.read_bytes()
        except FileNotFoundError:
            generated = Ed25519PrivateKey.generate()
            encoded = generated.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
            try:
                with key_file.open("xb") as handle:
                    handle.write(encoded)
                key_file.chmod(0o600)
            except FileExistsError:
                encoded = key_file.read_bytes()
            except OSError:
                if not key_file.exists():
                    raise
        key = serialization.load_pem_private_key(encoded, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise RuntimeError("HUB_JWT_PRIVATE_KEY_FILE must contain an Ed25519 private key")
        return key

    def jwks(self) -> dict[str, Any]:
        raw_public = self._public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        keys = [
            {
                "kty": "OKP",
                "crv": "Ed25519",
                "kid": self.settings.jwt_kid,
                "use": "sig",
                "alg": "EdDSA",
                "x": b64url(raw_public),
            }
        ]
        if self.settings.jwt_previous_public_jwk_json:
            previous = json.loads(self.settings.jwt_previous_public_jwk_json)
            if (
                not isinstance(previous, dict)
                or previous.get("kty") != "OKP"
                or previous.get("crv") != "Ed25519"
                or previous.get("alg") != "EdDSA"
                or not isinstance(previous.get("kid"), str)
                or not isinstance(previous.get("x"), str)
                or "d" in previous
                or previous["kid"] == self.settings.jwt_kid
            ):
                raise RuntimeError("HUB_JWT_PREVIOUS_PUBLIC_JWK_JSON must be a distinct public Ed25519 JWK")
            keys.append(previous)
        return {"keys": keys}

    def sign_agent_token(
        self,
        *,
        agent_id: str,
        version_id: str,
        user_id: str,
        display_name: str,
        scopes: list[str],
        request_id: str,
    ) -> str:
        now = datetime.now(UTC)
        payload = {
            "iss": self.settings.issuer,
            "sub": user_id,
            "aud": agent_id,
            "iat": int(now.timestamp()),
            "nbf": int((now - timedelta(seconds=30)).timestamp()),
            "exp": int((now + timedelta(seconds=self.settings.jwt_ttl_seconds)).timestamp()),
            "jti": new_id("jti"),
            "name": display_name,
            "scope": " ".join(scopes),
            "request_id": request_id,
            "agent_version_id": version_id,
        }
        return jwt.encode(
            payload,
            self._private_key,
            algorithm="EdDSA",
            headers={"typ": "JWT", "kid": self.settings.jwt_kid},
        )


def hash_secret(secret: str) -> str:
    return _PASSWORD_HASHER.hash(secret)


def verify_secret(secret_hash: str, candidate: str) -> bool:
    try:
        return _PASSWORD_HASHER.verify(secret_hash, candidate)
    except VerifyMismatchError:
        return False


def hash_code(code: str) -> str:
    return sha256_text(code)


def state_hash(state: str) -> str:
    return sha256_text(state)


def create_agent_credential(
    conn: sqlite3.Connection,
    agent_id: str,
    *,
    rotation_window_seconds: int = 300,
) -> dict[str, str]:
    rotation_deadline = datetime.now(UTC) + timedelta(seconds=max(0, rotation_window_seconds))
    conn.execute(
        """
        UPDATE hub_agent_credentials
        SET status = 'rotating', rotates_at = ?
        WHERE agent_id = ? AND status = 'active'
        """,
        (
            rotation_deadline.isoformat(timespec="seconds").replace("+00:00", "Z"),
            agent_id,
        ),
    )
    secret = random_token(32)
    credential_id = new_id("cred")
    conn.execute(
        """
        INSERT INTO hub_agent_credentials (
          credential_id, agent_id, secret_hash, status, created_at
        ) VALUES (?, ?, ?, 'active', ?)
        """,
        (credential_id, agent_id, hash_secret(secret), now_iso()),
    )
    return {
        "client_id": agent_id,
        "client_secret": secret,
        "credential_id": credential_id,
        "secret_notice": "store this once; Hub only keeps an Argon2id hash",
    }


def authenticate_client_secret_basic(conn: sqlite3.Connection, request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("basic "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"error": "invalid_client"})
    import base64

    try:
        raw = base64.b64decode(auth.split(" ", 1)[1]).decode("utf-8")
        client_id, client_secret = raw.split(":", 1)
    except Exception as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"error": "invalid_client"}) from exc

    rows = conn.execute(
        """
        SELECT secret_hash, status, rotates_at FROM hub_agent_credentials
        WHERE agent_id = ? AND status IN ('active','rotating')
        """,
        (client_id,),
    ).fetchall()
    now = datetime.now(UTC)
    valid_rows = []
    for row in rows:
        if row["status"] == "active":
            valid_rows.append(row)
            continue
        rotates_at = row["rotates_at"]
        if rotates_at and datetime.fromisoformat(rotates_at.replace("Z", "+00:00")) >= now:
            valid_rows.append(row)
    if not any(verify_secret(row["secret_hash"], client_secret) for row in valid_rows):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"error": "invalid_client"})
    return client_id


def update_agent_credential_status(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
    credential_id: str,
    new_status: str,
    rotation_window_seconds: int = 300,
) -> None:
    if new_status not in {"rotating", "revoked"}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail={"error": "invalid_credential_status"})
    now = now_iso()
    if new_status == "revoked":
        updated = conn.execute(
            """
            UPDATE hub_agent_credentials
            SET status = 'revoked', revoked_at = ?
            WHERE agent_id = ? AND credential_id = ? AND status IN ('active','rotating')
            """,
            (now, agent_id, credential_id),
        ).rowcount
    else:
        rotation_deadline = datetime.now(UTC) + timedelta(seconds=max(0, rotation_window_seconds))
        updated = conn.execute(
            """
            UPDATE hub_agent_credentials
            SET status = 'rotating', rotates_at = ?
            WHERE agent_id = ? AND credential_id = ? AND status = 'active'
            """,
            (
                rotation_deadline.isoformat(timespec="seconds").replace("+00:00", "Z"),
                agent_id,
                credential_id,
            ),
        ).rowcount
    if not updated:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "credential_not_active"})


def create_auth_code(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
    version_id: str,
    user_id: str,
    display_name: str,
    redirect_uri: str,
    state: str,
    scopes: list[str],
    ttl_seconds: int,
) -> str:
    code = random_token(32)
    expires = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
    conn.execute(
        """
        INSERT INTO hub_auth_codes (
          code_hash, agent_id, version_id, user_id, display_name, redirect_uri,
          state_hash, scopes_json, expires_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            hash_code(code),
            agent_id,
            version_id,
            user_id,
            display_name,
            redirect_uri,
            state_hash(state),
            json.dumps(scopes, ensure_ascii=False),
            expires.isoformat(timespec="seconds").replace("+00:00", "Z"),
            now_iso(),
        ),
    )
    return code


def consume_auth_code(
    conn: sqlite3.Connection,
    *,
    code: str,
    client_id: str,
    redirect_uri: str,
    state: str,
) -> dict[str, Any]:
    digest = hash_code(code)
    row = conn.execute("SELECT * FROM hub_auth_codes WHERE code_hash = ?", (digest,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"error": "invalid_grant"})
    if (
        row["agent_id"] != client_id
        or row["redirect_uri"] != redirect_uri
        or not secrets.compare_digest(row["state_hash"], state_hash(state))
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"error": "invalid_grant"})
    if row["used_at"] is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"error": "invalid_grant"})
    expires_at = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
    if expires_at < datetime.now(UTC):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"error": "invalid_grant"})
    agent = conn.execute(
        "SELECT status, active_version_id FROM hub_agents WHERE agent_id = ?",
        (client_id,),
    ).fetchone()
    if (
        agent is None
        or agent["status"] != "active"
        or agent["active_version_id"] != row["version_id"]
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"error": "invalid_grant"})

    conn.execute("UPDATE hub_auth_codes SET used_at = ? WHERE code_hash = ?", (now_iso(), digest))
    result = dict(row)
    result["scopes"] = json.loads(result.pop("scopes_json") or "[]")
    return result
