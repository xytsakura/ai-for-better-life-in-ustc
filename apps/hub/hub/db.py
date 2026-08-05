from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS hub_agents (
  agent_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  owner TEXT NOT NULL,
  category TEXT NOT NULL,
  summary TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending','active','rejected','suspended','deprecated')),
  active_version_id TEXT,
  previous_active_version_id TEXT,
  featured INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (active_version_id) REFERENCES hub_agent_versions(version_id),
  FOREIGN KEY (previous_active_version_id) REFERENCES hub_agent_versions(version_id)
);

CREATE TABLE IF NOT EXISTS hub_agent_versions (
  version_id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL,
  version TEXT NOT NULL,
  manifest_json TEXT NOT NULL,
  manifest_hash TEXT NOT NULL,
  review_status TEXT NOT NULL CHECK (review_status IN ('pending','approved','rejected')),
  deployment_status TEXT NOT NULL CHECK (deployment_status IN ('staged','active','superseded','deprecated')),
  trust_level TEXT NOT NULL CHECK (trust_level IN ('third_party_external','first_party_internal')),
  submitted_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (agent_id, version),
  FOREIGN KEY (agent_id) REFERENCES hub_agents(agent_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS hub_reviews (
  review_id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL,
  version_id TEXT NOT NULL,
  reviewer TEXT NOT NULL,
  decision TEXT NOT NULL CHECK (decision IN ('approved','rejected')),
  notes TEXT NOT NULL DEFAULT '',
  checks_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (agent_id) REFERENCES hub_agents(agent_id),
  FOREIGN KEY (version_id) REFERENCES hub_agent_versions(version_id)
);

CREATE TABLE IF NOT EXISTS hub_health_checks (
  health_id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL,
  version_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('ok','offline','protocol_error','unknown')),
  latency_ms INTEGER,
  contract_version TEXT,
  capabilities_json TEXT NOT NULL DEFAULT '[]',
  error_code TEXT,
  safe_detail TEXT NOT NULL DEFAULT '',
  checked_at TEXT NOT NULL,
  FOREIGN KEY (agent_id) REFERENCES hub_agents(agent_id),
  FOREIGN KEY (version_id) REFERENCES hub_agent_versions(version_id)
);

CREATE TABLE IF NOT EXISTS hub_invocations (
  invocation_id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL,
  version_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  run_id TEXT,
  status TEXT NOT NULL,
  error_code TEXT,
  duration_ms INTEGER,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  FOREIGN KEY (agent_id) REFERENCES hub_agents(agent_id),
  FOREIGN KEY (version_id) REFERENCES hub_agent_versions(version_id)
);

CREATE TABLE IF NOT EXISTS hub_audit_events (
  event_id TEXT PRIMARY KEY,
  event_type TEXT NOT NULL,
  agent_id TEXT,
  version_id TEXT,
  actor TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  safe_detail_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hub_auth_codes (
  code_hash TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL,
  version_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  display_name TEXT NOT NULL,
  redirect_uri TEXT NOT NULL,
  state_hash TEXT NOT NULL,
  scopes_json TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used_at TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (agent_id) REFERENCES hub_agents(agent_id),
  FOREIGN KEY (version_id) REFERENCES hub_agent_versions(version_id)
);

CREATE TABLE IF NOT EXISTS hub_agent_credentials (
  credential_id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL,
  secret_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active','rotating','revoked')),
  created_at TEXT NOT NULL,
  rotates_at TEXT,
  revoked_at TEXT,
  FOREIGN KEY (agent_id) REFERENCES hub_agents(agent_id)
);

CREATE INDEX IF NOT EXISTS idx_versions_agent ON hub_agent_versions(agent_id);
CREATE INDEX IF NOT EXISTS idx_health_agent_version ON hub_health_checks(agent_id, version_id, checked_at);
CREATE INDEX IF NOT EXISTS idx_audit_agent ON hub_audit_events(agent_id, created_at);
CREATE INDEX IF NOT EXISTS idx_invocations_agent ON hub_invocations(agent_id, started_at);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(path: Path) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


@contextmanager
def database(path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
