from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PACKAGE_DIR = Path(__file__).resolve().parent
APP_DIR = PACKAGE_DIR.parent
REPO_ROOT = PACKAGE_DIR.parents[2]
load_dotenv(APP_DIR / ".env", override=False)


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _runtime_dir() -> Path:
    configured = os.getenv("COURSE_AGENT_RUNTIME_DIR")
    if not configured:
        return (REPO_ROOT / "var" / "course-agent").resolve()
    path = Path(configured)
    if not path.is_absolute():
        path = APP_DIR / path
    return path.resolve()


@dataclass(frozen=True)
class Settings:
    repo_root: Path = REPO_ROOT
    runtime_dir: Path = _runtime_dir()
    demo_mode: bool = _as_bool(os.getenv("COURSE_AGENT_DEMO_MODE"), True)
    session_secret: str = os.getenv(
        "COURSE_AGENT_SESSION_SECRET", "dev-only-change-before-shared-deployment"
    )
    llm_api_key: str = os.getenv("COURSE_AGENT_LLM_API_KEY", "")
    llm_base_url: str = os.getenv("COURSE_AGENT_LLM_BASE_URL", "")
    llm_model: str = os.getenv("COURSE_AGENT_LLM_MODEL", "gpt-5.6-sol")
    llm_timeout_seconds: float = float(
        os.getenv("COURSE_AGENT_LLM_TIMEOUT_SECONDS", "45")
    )
    max_upload_bytes: int = 50 * 1024 * 1024

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
