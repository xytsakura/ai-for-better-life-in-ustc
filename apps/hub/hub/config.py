from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _split_csv(value: str) -> list[str]:
    return [item.strip().rstrip("/") for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    database_path: Path
    demo_mode: bool = False
    issuer: str = "campus-agent-hub"
    public_base_url: str = "http://127.0.0.1:8100"
    jwt_kid: str = "hub-dev-ed25519"
    jwt_private_key_pem: str | None = None
    jwt_ttl_seconds: int = 120
    auth_code_ttl_seconds: int = 60
    request_timeout_seconds: float = 30.0
    connect_timeout_seconds: float = 5.0
    health_timeout_seconds: float = 5.0
    internal_url_allowlist: tuple[str, ...] = (
        "http://127.0.0.1:8002",
        "http://127.0.0.1:8101",
        "http://localhost:8002",
        "http://localhost:8101",
    )
    cors_allow_origins: tuple[str, ...] = ("http://127.0.0.1:8100", "http://localhost:8100")

    @classmethod
    def from_env(cls) -> "Settings":
        runtime_dir = Path(os.getenv("HUB_RUNTIME_DIR", "var/hub"))
        database_path = Path(os.getenv("HUB_DATABASE_PATH", runtime_dir / "hub.sqlite3"))
        allowlist = os.getenv("HUB_INTERNAL_URL_ALLOWLIST")
        cors = os.getenv("HUB_CORS_ALLOW_ORIGINS")
        return cls(
            database_path=database_path,
            demo_mode=os.getenv("HUB_DEMO_MODE", "false").strip().lower() in {"1", "true", "yes", "on"},
            issuer=os.getenv("HUB_ISSUER", cls.issuer),
            public_base_url=os.getenv("HUB_PUBLIC_BASE_URL", cls.public_base_url).rstrip("/"),
            jwt_kid=os.getenv("HUB_JWT_KID", cls.jwt_kid),
            jwt_private_key_pem=os.getenv("HUB_JWT_PRIVATE_KEY_PEM"),
            jwt_ttl_seconds=int(os.getenv("HUB_JWT_TTL_SECONDS", str(cls.jwt_ttl_seconds))),
            auth_code_ttl_seconds=int(
                os.getenv("HUB_AUTH_CODE_TTL_SECONDS", str(cls.auth_code_ttl_seconds))
            ),
            request_timeout_seconds=float(
                os.getenv("HUB_REQUEST_TIMEOUT_SECONDS", str(cls.request_timeout_seconds))
            ),
            connect_timeout_seconds=float(
                os.getenv("HUB_CONNECT_TIMEOUT_SECONDS", str(cls.connect_timeout_seconds))
            ),
            health_timeout_seconds=float(
                os.getenv("HUB_HEALTH_TIMEOUT_SECONDS", str(cls.health_timeout_seconds))
            ),
            internal_url_allowlist=tuple(_split_csv(allowlist))
            if allowlist is not None
            else cls.internal_url_allowlist,
            cors_allow_origins=tuple(_split_csv(cors)) if cors is not None else cls.cors_allow_origins,
        )


DEMO_USERS = {
    "demo-a": {"user_id": "demo-a", "display_name": "Demo Admin", "role": "admin"},
    "demo-b": {"user_id": "demo-b", "display_name": "Demo Developer", "role": "developer"},
    "demo-c": {"user_id": "demo-c", "display_name": "Demo User", "role": "user"},
}
