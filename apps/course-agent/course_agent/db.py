from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import Settings


SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    is_demo INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS spaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    space_type TEXT NOT NULL CHECK (space_type IN ('personal', 'shared', 'subscribed')),
    owner_id TEXT NOT NULL REFERENCES users(id),
    visibility TEXT NOT NULL DEFAULT 'private'
);

CREATE TABLE IF NOT EXISTS memberships (
    space_id TEXT NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('owner', 'maintainer', 'member', 'reader')),
    status TEXT NOT NULL DEFAULT 'active',
    PRIMARY KEY (space_id, user_id)
);

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_url TEXT,
    license_status TEXT NOT NULL,
    access_mode TEXT NOT NULL DEFAULT 'private-team-use'
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    space_id TEXT NOT NULL REFERENCES spaces(id),
    source_id TEXT NOT NULL REFERENCES sources(id),
    title TEXT NOT NULL,
    course TEXT NOT NULL,
    semester TEXT,
    material_type TEXT NOT NULL,
    file_path TEXT NOT NULL,
    is_repo_source INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS revisions (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    content_hash TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    page_count INTEGER NOT NULL,
    searchable_pages INTEGER NOT NULL,
    needs_ocr_pages INTEGER NOT NULL,
    needs_review_pages INTEGER NOT NULL,
    failed_pages INTEGER NOT NULL,
    parse_status TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

DROP INDEX IF EXISTS uq_active_hash_per_space;

CREATE UNIQUE INDEX IF NOT EXISTS uq_active_revision_per_document
ON revisions(document_id) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS pages (
    id TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL REFERENCES revisions(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    status TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    UNIQUE(revision_id, page_number)
);

CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL REFERENCES revisions(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    content TEXT NOT NULL,
    search_text TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
    chunk_id UNINDEXED,
    search_text,
    tokenize='unicode61 remove_diacritics 0'
);

CREATE INDEX IF NOT EXISTS idx_documents_space_status ON documents(space_id, status);
CREATE INDEX IF NOT EXISTS idx_revisions_document_status ON revisions(document_id, status);
CREATE INDEX IF NOT EXISTS idx_chunks_revision ON chunks(revision_id);
"""


DEMO_USERS = (
    ("demo-a", "谢同学", 1),
    ("demo-b", "队友演示", 1),
)

DEMO_SPACES = (
    ("personal-demo-a", "谢同学的资料", "personal", "demo-a", "private"),
    ("personal-demo-b", "队友演示的资料", "personal", "demo-b", "private"),
    ("math-b1-shared", "数学分析 B1 学习小组", "shared", "demo-a", "invited"),
)

DEMO_MEMBERSHIPS = (
    ("personal-demo-a", "demo-a", "owner", "active"),
    ("personal-demo-b", "demo-b", "owner", "active"),
    ("math-b1-shared", "demo-a", "owner", "active"),
    ("math-b1-shared", "demo-b", "member", "active"),
)


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def database(settings: Settings) -> Iterator[sqlite3.Connection]:
    settings.ensure_directories()
    conn = connect(settings.database_path)
    try:
        yield conn
    finally:
        conn.close()


def init_database(settings: Settings) -> None:
    settings.ensure_directories()
    with database(settings) as conn:
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT OR IGNORE INTO users(id, display_name, is_demo) VALUES (?, ?, ?)",
            DEMO_USERS,
        )
        conn.executemany(
            """INSERT OR IGNORE INTO spaces
               (id, name, space_type, owner_id, visibility) VALUES (?, ?, ?, ?, ?)""",
            DEMO_SPACES,
        )
        conn.executemany(
            """INSERT OR IGNORE INTO memberships
               (space_id, user_id, role, status) VALUES (?, ?, ?, ?)""",
            DEMO_MEMBERSHIPS,
        )
        conn.commit()


def healthcheck(settings: Settings) -> dict[str, bool]:
    try:
        with database(settings) as conn:
            conn.execute("CREATE TEMP TABLE IF NOT EXISTS healthcheck(value INTEGER)")
            conn.execute("SELECT count(*) FROM chunk_fts WHERE chunk_fts MATCH 'healthcheck'")
        return {"database": True, "search": True}
    except sqlite3.Error:
        return {"database": False, "search": False}
