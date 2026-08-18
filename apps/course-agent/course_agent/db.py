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

CREATE TABLE IF NOT EXISTS published_libraries (
    id TEXT PRIMARY KEY,
    space_id TEXT NOT NULL UNIQUE REFERENCES spaces(id),
    author_id TEXT NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    course TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL CHECK (status IN ('pending', 'published', 'suspended', 'withdrawn')),
    current_version_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS publication_versions (
    id TEXT PRIMARY KEY,
    library_id TEXT NOT NULL REFERENCES published_libraries(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'changes_requested', 'rejected', 'withdrawn', 'published', 'superseded')
    ),
    name TEXT NOT NULL DEFAULT '',
    course TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    submitted_by TEXT NOT NULL REFERENCES users(id),
    base_version_id TEXT,
    reviewed_by TEXT,
    review_note TEXT,
    submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TEXT,
    published_at TEXT,
    UNIQUE(library_id, version_number)
);

CREATE TABLE IF NOT EXISTS publication_documents (
    version_id TEXT NOT NULL REFERENCES publication_versions(id) ON DELETE CASCADE,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    source_document_id TEXT NOT NULL REFERENCES documents(id),
    use_in_rag INTEGER NOT NULL DEFAULT 1,
    can_preview INTEGER NOT NULL DEFAULT 1,
    can_download INTEGER NOT NULL DEFAULT 0,
    review_status TEXT NOT NULL DEFAULT 'pending',
    review_note TEXT,
    PRIMARY KEY(version_id, document_id)
);

CREATE TABLE IF NOT EXISTS library_subscriptions (
    library_id TEXT NOT NULL REFERENCES published_libraries(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('active', 'cancelled')),
    subscribed_at TEXT,
    cancelled_at TEXT,
    PRIMARY KEY(library_id, user_id)
);

CREATE TABLE IF NOT EXISTS marketplace_course_metadata (
    library_id TEXT PRIMARY KEY REFERENCES published_libraries(id) ON DELETE CASCADE,
    slug TEXT NOT NULL UNIQUE,
    demo_kind TEXT NOT NULL DEFAULT 'demo-placeholder'
        CHECK (demo_kind IN ('real', 'demo-placeholder')),
    cover_icon TEXT NOT NULL DEFAULT '◇',
    cover_theme TEXT NOT NULL DEFAULT 'indigo',
    short_description TEXT NOT NULL DEFAULT '',
    empty_state TEXT NOT NULL DEFAULT '资料待补充',
    sort_order INTEGER NOT NULL DEFAULT 100,
    seed_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    actor_id TEXT NOT NULL REFERENCES users(id),
    event_type TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
CREATE INDEX IF NOT EXISTS idx_published_libraries_status ON published_libraries(status);
CREATE INDEX IF NOT EXISTS idx_publication_versions_status ON publication_versions(status);
CREATE INDEX IF NOT EXISTS idx_publication_documents_document ON publication_documents(document_id);
CREATE INDEX IF NOT EXISTS idx_library_subscriptions_user_status ON library_subscriptions(user_id, status);
CREATE INDEX IF NOT EXISTS idx_marketplace_course_metadata_sort ON marketplace_course_metadata(sort_order, slug);
CREATE INDEX IF NOT EXISTS idx_audit_events_target ON audit_events(target_type, target_id, created_at);
"""


DEMO_USERS = (
    ("demo-a", "谢同学", 1),
    ("demo-b", "队友演示", 1),
    ("demo-c", "访客演示", 1),
)

DEMO_SPACES = (
    ("personal-demo-a", "谢同学的资料", "personal", "demo-a", "private"),
    ("personal-demo-b", "队友演示的资料", "personal", "demo-b", "private"),
    ("personal-demo-c", "访客演示的资料", "personal", "demo-c", "private"),
    ("math-b1-shared", "数学分析 B1 学习小组", "shared", "demo-a", "invited"),
)

DEMO_MEMBERSHIPS = (
    ("personal-demo-a", "demo-a", "owner", "active"),
    ("personal-demo-b", "demo-b", "owner", "active"),
    ("personal-demo-c", "demo-c", "owner", "active"),
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
        version_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(publication_versions)").fetchall()
        }
        for column, ddl in {
            "name": "ALTER TABLE publication_versions ADD COLUMN name TEXT NOT NULL DEFAULT ''",
            "course": "ALTER TABLE publication_versions ADD COLUMN course TEXT NOT NULL DEFAULT ''",
            "description": "ALTER TABLE publication_versions ADD COLUMN description TEXT NOT NULL DEFAULT ''",
            "tags_json": "ALTER TABLE publication_versions ADD COLUMN tags_json TEXT NOT NULL DEFAULT '[]'",
        }.items():
            if column not in version_columns:
                conn.execute(ddl)
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
