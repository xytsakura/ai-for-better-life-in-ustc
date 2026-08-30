from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _split_csv(value: str) -> list[str]:
    return [item.strip().rstrip("/") for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    database_path: Path
    asset_cache_dir: Path | None = None
    demo_mode: bool = False
    issuer: str = "campus-agent-hub"
    public_base_url: str = "http://127.0.0.1:8100"
    jwt_kid: str = "hub-dev-ed25519"
    jwt_private_key_pem: str | None = None
    jwt_private_key_file: Path | None = None
    jwt_previous_public_jwk_json: str | None = None
    jwt_ttl_seconds: int = 120
    auth_code_ttl_seconds: int = 60
    request_timeout_seconds: float = 30.0
    connect_timeout_seconds: float = 5.0
    health_timeout_seconds: float = 5.0
    max_request_bytes: int = 262_144
    max_response_bytes: int = 1_048_576
    rate_limit_requests: int = 20
    rate_limit_window_seconds: int = 60
    health_failure_threshold: int = 3
    health_poll_interval_seconds: float = 30.0
    automatic_checks_enabled: bool = True
    require_passing_checks: bool = True
    credential_rotation_window_seconds: int = 300
    internal_url_allowlist: tuple[str, ...] = (
        "http://127.0.0.1:8002",
        "http://127.0.0.1:8101",
        "http://localhost:8002",
        "http://localhost:8101",
    )
    cors_allow_origins: tuple[str, ...] = ("http://127.0.0.1:8100", "http://localhost:8100")
    model_profiles_enabled: bool = False
    model_profile_master_key_file: Path | None = None
    model_profile_master_key_version: int = 1
    model_profile_previous_key_files: tuple[str, ...] = ()
    model_provider_origin_allowlist: tuple[str, ...] = ()
    allow_local_model_providers: bool = False
    model_gateway_grant_ttl_seconds: int = 120
    model_delegation_ttl_seconds: int = 14_400
    model_gateway_timeout_seconds: float = 60.0

    @classmethod
    def from_env(cls) -> "Settings":
        runtime_dir = Path(os.getenv("HUB_RUNTIME_DIR", "var/hub"))
        database_path = Path(os.getenv("HUB_DATABASE_PATH", runtime_dir / "hub.sqlite3"))
        allowlist = os.getenv("HUB_INTERNAL_URL_ALLOWLIST")
        cors = os.getenv("HUB_CORS_ALLOW_ORIGINS")
        provider_allowlist = os.getenv("HUB_MODEL_PROVIDER_ORIGIN_ALLOWLIST")
        return cls(
            database_path=database_path,
            asset_cache_dir=Path(
                os.getenv("HUB_ASSET_CACHE_DIR", str(runtime_dir / "assets"))
            ),
            demo_mode=os.getenv("HUB_DEMO_MODE", "false").strip().lower() in {"1", "true", "yes", "on"},
            issuer=os.getenv("HUB_ISSUER", cls.issuer),
            public_base_url=os.getenv("HUB_PUBLIC_BASE_URL", cls.public_base_url).rstrip("/"),
            jwt_kid=os.getenv("HUB_JWT_KID", cls.jwt_kid),
            jwt_private_key_pem=os.getenv("HUB_JWT_PRIVATE_KEY_PEM"),
            jwt_private_key_file=Path(
                os.getenv("HUB_JWT_PRIVATE_KEY_FILE", str(runtime_dir / "jwt-ed25519.pem"))
            ),
            jwt_previous_public_jwk_json=os.getenv("HUB_JWT_PREVIOUS_PUBLIC_JWK_JSON"),
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
            max_request_bytes=int(os.getenv("HUB_MAX_REQUEST_BYTES", str(cls.max_request_bytes))),
            max_response_bytes=int(os.getenv("HUB_MAX_RESPONSE_BYTES", str(cls.max_response_bytes))),
            rate_limit_requests=int(
                os.getenv("HUB_RATE_LIMIT_REQUESTS", str(cls.rate_limit_requests))
            ),
            rate_limit_window_seconds=int(
                os.getenv("HUB_RATE_LIMIT_WINDOW_SECONDS", str(cls.rate_limit_window_seconds))
            ),
            health_failure_threshold=int(
                os.getenv("HUB_HEALTH_FAILURE_THRESHOLD", str(cls.health_failure_threshold))
            ),
            health_poll_interval_seconds=float(
                os.getenv("HUB_HEALTH_POLL_INTERVAL_SECONDS", str(cls.health_poll_interval_seconds))
            ),
            automatic_checks_enabled=os.getenv("HUB_AUTOMATIC_CHECKS_ENABLED", "true")
            .strip()
            .lower()
            in {"1", "true", "yes", "on"},
            require_passing_checks=os.getenv("HUB_REQUIRE_PASSING_CHECKS", "true")
            .strip()
            .lower()
            in {"1", "true", "yes", "on"},
            credential_rotation_window_seconds=int(
                os.getenv(
                    "HUB_CREDENTIAL_ROTATION_WINDOW_SECONDS",
                    str(cls.credential_rotation_window_seconds),
                )
            ),
            internal_url_allowlist=tuple(_split_csv(allowlist))
            if allowlist is not None
            else cls.internal_url_allowlist,
            cors_allow_origins=tuple(_split_csv(cors)) if cors is not None else cls.cors_allow_origins,
            model_profiles_enabled=os.getenv("HUB_MODEL_PROFILES_ENABLED", "false")
            .strip()
            .lower()
            in {"1", "true", "yes", "on"},
            model_profile_master_key_file=Path(os.getenv("HUB_MODEL_PROFILE_MASTER_KEY_FILE"))
            if os.getenv("HUB_MODEL_PROFILE_MASTER_KEY_FILE")
            else None,
            model_profile_master_key_version=int(
                os.getenv("HUB_MODEL_PROFILE_MASTER_KEY_VERSION", str(cls.model_profile_master_key_version))
            ),
            model_profile_previous_key_files=tuple(
                _split_csv(os.getenv("HUB_MODEL_PROFILE_PREVIOUS_KEY_FILES", ""))
            ),
            model_provider_origin_allowlist=tuple(_split_csv(provider_allowlist))
            if provider_allowlist is not None
            else cls.model_provider_origin_allowlist,
            allow_local_model_providers=os.getenv("HUB_ALLOW_LOCAL_MODEL_PROVIDERS", "false")
            .strip()
            .lower()
            in {"1", "true", "yes", "on"},
            model_gateway_grant_ttl_seconds=min(
                120,
                int(
                    os.getenv(
                        "HUB_MODEL_GATEWAY_GRANT_TTL_SECONDS",
                        str(cls.model_gateway_grant_ttl_seconds),
                    )
                ),
            ),
            model_delegation_ttl_seconds=int(
                os.getenv(
                    "HUB_MODEL_DELEGATION_TTL_SECONDS",
                    str(cls.model_delegation_ttl_seconds),
                )
            ),
            model_gateway_timeout_seconds=float(
                os.getenv(
                    "HUB_MODEL_GATEWAY_TIMEOUT_SECONDS",
                    str(cls.model_gateway_timeout_seconds),
                )
            ),
        )


DEMO_USERS = {
    "demo-a": {"user_id": "demo-a", "display_name": "Demo Admin", "role": "admin"},
    "demo-b": {"user_id": "demo-b", "display_name": "Demo Developer", "role": "developer"},
    "demo-c": {"user_id": "demo-c", "display_name": "Demo User", "role": "user"},
}
