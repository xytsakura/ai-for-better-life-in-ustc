from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


PACKAGE_DIR = Path(__file__).resolve().parent
APP_DIR = PACKAGE_DIR.parent
REPO_ROOT = PACKAGE_DIR.parents[2]
load_dotenv(APP_DIR / ".env", override=False)


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _csv_set(value: str | None) -> set[str]:
    if not value:
        return set()
    return {item.strip() for item in value.split(",") if item.strip()}


def _csv_mapping(value: str | None) -> dict[str, str]:
    result: dict[str, str] = {}
    if not value:
        return result
    for item in value.split(","):
        if ":" not in item:
            continue
        source, target = item.split(":", 1)
        source = source.strip()
        target = target.strip()
        if source and target:
            result[source] = target
    return result


def _runtime_dir() -> Path:
    configured = os.getenv("COURSE_AGENT_RUNTIME_DIR")
    if not configured:
        return (REPO_ROOT / "var" / "course-agent").resolve()
    path = Path(configured)
    if not path.is_absolute():
        path = APP_DIR / path
    return path.resolve()


def _secret_from_env_or_file(value_name: str, file_name: str) -> str:
    direct = os.getenv(value_name, "")
    if direct:
        return direct
    configured_path = os.getenv(file_name, "")
    if not configured_path:
        return ""
    path = Path(configured_path)
    if not path.is_absolute():
        path = APP_DIR / path
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _secret_file_path(value_name: str, file_name: str) -> Path | None:
    if os.getenv(value_name, ""):
        return None
    configured_path = os.getenv(file_name, "").strip()
    if not configured_path:
        return None
    path = Path(configured_path)
    if not path.is_absolute():
        path = APP_DIR / path
    return path.resolve()


@dataclass
class Settings:
    repo_root: Path = REPO_ROOT
    runtime_dir: Path = _runtime_dir()
    demo_mode: bool = _as_bool(os.getenv("COURSE_AGENT_DEMO_MODE"), True)
    session_secret: str = os.getenv(
        "COURSE_AGENT_SESSION_SECRET", "dev-only-change-before-shared-deployment"
    )
    session_https_only: bool = _as_bool(os.getenv("COURSE_AGENT_SESSION_HTTPS_ONLY"), False)
    llm_api_key: str = os.getenv("COURSE_AGENT_LLM_API_KEY", "")
    llm_base_url: str = os.getenv("COURSE_AGENT_LLM_BASE_URL", "")
    llm_model: str = os.getenv("COURSE_AGENT_LLM_MODEL", "gpt-5.6-sol")
    llm_api_style: str = os.getenv("COURSE_AGENT_LLM_API_STYLE", "responses")
    branch_llm_model: str = os.getenv(
        "COURSE_AGENT_BRANCH_LLM_MODEL", "gpt-5.6-sol"
    )
    llm_timeout_seconds: float = float(
        os.getenv("COURSE_AGENT_LLM_TIMEOUT_SECONDS", "45")
    )
    admin_user_ids: set[str] | None = None
    llm_allow_local_base_urls: bool = _as_bool(
        os.getenv("COURSE_AGENT_ALLOW_LOCAL_LLM_BASE_URLS"), False
    )
    hub_agent_id: str = os.getenv("COURSE_AGENT_HUB_AGENT_ID", "hanhai-course-agent")
    hub_contract_version: str = os.getenv("COURSE_AGENT_HUB_CONTRACT_VERSION", "1.0")
    hub_issuer: str = os.getenv("COURSE_AGENT_HUB_ISSUER", "campus-agent-hub")
    hub_jwks_url: str = os.getenv(
        "COURSE_AGENT_HUB_JWKS_URL",
        "http://127.0.0.1:8100/.well-known/jwks.json",
    )
    hub_jwks_json: str = os.getenv("COURSE_AGENT_HUB_JWKS_JSON", "")
    hub_auth_required: bool = _as_bool(os.getenv("COURSE_AGENT_HUB_AUTH_REQUIRED"), True)
    hub_token_endpoint: str = os.getenv("COURSE_AGENT_HUB_TOKEN_ENDPOINT", "")
    hub_client_id: str = os.getenv("COURSE_AGENT_HUB_CLIENT_ID", "hanhai-course-agent")
    hub_client_secret: str = _secret_from_env_or_file(
        "COURSE_AGENT_HUB_CLIENT_SECRET",
        "COURSE_AGENT_HUB_CLIENT_SECRET_FILE",
    )
    hub_client_secret_file: Path | None = _secret_file_path(
        "COURSE_AGENT_HUB_CLIENT_SECRET",
        "COURSE_AGENT_HUB_CLIENT_SECRET_FILE",
    )
    hub_workspace_redirect_uri: str = os.getenv(
        "COURSE_AGENT_HUB_WORKSPACE_REDIRECT_URI",
        "http://127.0.0.1:8002/api/hub/callback",
    )
    hub_return_url: str = os.getenv("COURSE_AGENT_HUB_RETURN_URL", "http://127.0.0.1:8100/")
    hub_user_mapping: dict[str, str] | None = None
    llm_config_generation: int = 0
    max_upload_bytes: int = 50 * 1024 * 1024

    # ---- Pluggable backend selection (reserved for future upgrades) -----
    # Set these environment variables to swap implementations:
    #   COURSE_AGENT_SEARCH_BACKEND    -- "fts5" (default) | "vector" | "hybrid"
    #   COURSE_AGENT_PARSER_BACKEND    -- "pymupdf" (default) | "mineru" | "markitdown"
    #   COURSE_AGENT_CHUNKING_BACKEND  -- "sentence" (default) | "recursive" | "semantic"
    #   COURSE_AGENT_TOKENIZER_BACKEND -- "jieba" (default) | "simple"
    search_backend: str = os.getenv("COURSE_AGENT_SEARCH_BACKEND", "fts5")
    parser_backend: str = os.getenv("COURSE_AGENT_PARSER_BACKEND", "pymupdf")
    chunking_backend: str = os.getenv("COURSE_AGENT_CHUNKING_BACKEND", "sentence")
    tokenizer_backend: str = os.getenv("COURSE_AGENT_TOKENIZER_BACKEND", "jieba")

    _env_file: Path = APP_DIR / ".env"

    def __post_init__(self) -> None:
        if self.admin_user_ids is None:
            configured = _csv_set(os.getenv("COURSE_AGENT_ADMIN_USER_IDS"))
            if not configured and self.demo_mode:
                configured = {"demo-a"}
            self.admin_user_ids = configured
        if self.hub_user_mapping is None:
            self.hub_user_mapping = _csv_mapping(os.getenv("COURSE_AGENT_HUB_USER_MAP"))

    def to_safe_dict(self) -> dict[str, Any]:
        """Return settings suitable for the frontend.

        The API key is never sent back to the browser (the field is rendered
        empty and left untouched unless the user explicitly types a new one),
        which avoids the previous bug where a masked placeholder was echoed
        back and saved as the real key.
        """
        return {
            "llm_base_url": self.llm_base_url,
            "llm_api_key": "",
            "llm_model": self.llm_model,
            "llm_api_style": self.llm_api_style,
            "llm_timeout_seconds": self.llm_timeout_seconds,
            "llm_configured": self.llm_configured,
            "search_backend": self.search_backend,
            "parser_backend": self.parser_backend,
            "chunking_backend": self.chunking_backend,
            "tokenizer_backend": self.tokenizer_backend,
        }

    def current_hub_client_secret(self) -> str:
        if self.hub_client_secret_file is None:
            return self.hub_client_secret
        try:
            return self.hub_client_secret_file.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def update_from_dict(self, data: dict[str, Any]) -> None:
        """Apply runtime updates from the frontend."""
        if "llm_base_url" in data:
            self.llm_base_url = str(data["llm_base_url"] or "").strip()
        if "llm_api_key" in data:
            value = data["llm_api_key"]
            # Only overwrite when the frontend actually sent a (non-empty) key.
            # An empty/absent value means "keep the existing key".
            if value:
                self.llm_api_key = str(value).strip()
        if "llm_model" in data:
            self.llm_model = str(data["llm_model"] or "").strip() or "gpt-5.6-sol"
        if "llm_api_style" in data:
            self.llm_api_style = str(data["llm_api_style"] or "responses").strip()
        if "llm_timeout_seconds" in data:
            self.llm_timeout_seconds = float(data["llm_timeout_seconds"])
        for key in ("search_backend", "parser_backend", "chunking_backend", "tokenizer_backend"):
            if key in data:
                setattr(self, key, str(data[key] or "").strip())

    def save(self) -> None:
        """Persist current settings to the .env file."""
        lines: list[str] = []
        if self._env_file.exists():
            lines = self._env_file.read_text(encoding="utf-8").splitlines()

        keys = [
            "COURSE_AGENT_DEMO_MODE",
            "COURSE_AGENT_SESSION_SECRET",
            "COURSE_AGENT_LLM_API_KEY",
            "COURSE_AGENT_LLM_BASE_URL",
            "COURSE_AGENT_LLM_MODEL",
            "COURSE_AGENT_LLM_API_STYLE",
            "COURSE_AGENT_LLM_TIMEOUT_SECONDS",
            "COURSE_AGENT_SEARCH_BACKEND",
            "COURSE_AGENT_PARSER_BACKEND",
            "COURSE_AGENT_CHUNKING_BACKEND",
            "COURSE_AGENT_TOKENIZER_BACKEND",
        ]
        values = {
            "COURSE_AGENT_DEMO_MODE": "true" if self.demo_mode else "false",
            "COURSE_AGENT_SESSION_SECRET": self.session_secret,
            "COURSE_AGENT_LLM_API_KEY": self.llm_api_key,
            "COURSE_AGENT_LLM_BASE_URL": self.llm_base_url,
            "COURSE_AGENT_LLM_MODEL": self.llm_model,
            "COURSE_AGENT_LLM_API_STYLE": self.llm_api_style,
            "COURSE_AGENT_LLM_TIMEOUT_SECONDS": str(self.llm_timeout_seconds),
            "COURSE_AGENT_SEARCH_BACKEND": self.search_backend,
            "COURSE_AGENT_PARSER_BACKEND": self.parser_backend,
            "COURSE_AGENT_CHUNKING_BACKEND": self.chunking_backend,
            "COURSE_AGENT_TOKENIZER_BACKEND": self.tokenizer_backend,
        }
        seen: set[str] = set()
        new_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                new_lines.append(line)
                continue
            key = stripped.split("=", 1)[0]
            if key in values:
                new_lines.append(f'{key}={values[key]}')
                seen.add(key)
            else:
                new_lines.append(line)
        for key in keys:
            if key not in seen:
                new_lines.append(f'{key}={values[key]}')
        self._env_file.parent.mkdir(parents=True, exist_ok=True)
        self._env_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    @property
    def database_path(self) -> Path:
        return self.runtime_dir / "course-agent.sqlite3"

    @property
    def uploads_dir(self) -> Path:
        return self.runtime_dir / "uploads"

    @property
    def temp_dir(self) -> Path:
        return self.runtime_dir / "tmp"

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key and self.llm_base_url and self.llm_model)

    def ensure_directories(self) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)
