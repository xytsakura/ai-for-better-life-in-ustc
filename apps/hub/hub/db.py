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
  featured_approved INTEGER NOT NULL DEFAULT 0,
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
  usage_json TEXT NOT NULL DEFAULT '{}',
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

CREATE TABLE IF NOT EXISTS hub_conformance_runs (
  run_id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL,
  version_id TEXT NOT NULL,
  overall_status TEXT NOT NULL CHECK (overall_status IN ('passed','failed')),
  checks_json TEXT NOT NULL,
  started_at TEXT NOT NULL,
  completed_at TEXT NOT NULL,
  FOREIGN KEY (agent_id) REFERENCES hub_agents(agent_id),
  FOREIGN KEY (version_id) REFERENCES hub_agent_versions(version_id)
);

CREATE TABLE IF NOT EXISTS hub_rate_limits (
  user_id TEXT NOT NULL,
  agent_id TEXT NOT NULL,
  window_start INTEGER NOT NULL,
  request_count INTEGER NOT NULL,
  PRIMARY KEY (user_id, agent_id, window_start),
  FOREIGN KEY (agent_id) REFERENCES hub_agents(agent_id)
);

CREATE TABLE IF NOT EXISTS hub_version_assets (
  version_id TEXT NOT NULL,
  asset_type TEXT NOT NULL CHECK (asset_type IN ('icon')),
  file_path TEXT NOT NULL,
  media_type TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (version_id, asset_type),
  FOREIGN KEY (version_id) REFERENCES hub_agent_versions(version_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS hub_model_profiles (
  profile_id TEXT PRIMARY KEY,
  owner_user_id TEXT NOT NULL,
  label TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT 'openai-compatible',
  base_url TEXT NOT NULL,
  api_style TEXT NOT NULL CHECK (api_style IN ('responses','chat_completions')),
  encrypted_api_key BLOB NOT NULL,
  encrypted_api_key_nonce BLOB NOT NULL,
  key_version INTEGER NOT NULL DEFAULT 1,
  key_fingerprint TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active','disabled')),
  default_model TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hub_model_profile_models (
  profile_id TEXT NOT NULL,
  model_id TEXT NOT NULL,
  display_name TEXT NOT NULL,
  api_style TEXT NOT NULL CHECK (api_style IN ('responses','chat_completions')),
  chat_eligible INTEGER NOT NULL DEFAULT 1,
  discovered_at TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (profile_id, model_id),
  FOREIGN KEY (profile_id) REFERENCES hub_model_profiles(profile_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS hub_model_bindings (
  binding_id TEXT PRIMARY KEY,
  owner_user_id TEXT NOT NULL,
  agent_id TEXT NOT NULL DEFAULT '',
  profile_id TEXT NOT NULL,
  model_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (owner_user_id, agent_id),
  FOREIGN KEY (profile_id) REFERENCES hub_model_profiles(profile_id)
);

CREATE TABLE IF NOT EXISTS hub_model_gateway_grants (
  jti TEXT PRIMARY KEY,
  delegation_id TEXT,
  user_id TEXT NOT NULL,
  agent_id TEXT NOT NULL,
  profile_id TEXT NOT NULL,
  model_id TEXT NOT NULL,
  request_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'issued' CHECK (status IN ('issued','consumed','expired','revoked')),
  issued_at TEXT,
  expires_at TEXT NOT NULL,
  consumed_at TEXT,
  revoked_at TEXT,
  used_at TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (profile_id) REFERENCES hub_model_profiles(profile_id)
);

CREATE TABLE IF NOT EXISTS hub_model_delegations (
  token_hash TEXT PRIMARY KEY,
  delegation_id TEXT UNIQUE,
  user_id TEXT NOT NULL,
  display_name TEXT NOT NULL,
  agent_id TEXT NOT NULL,
  version_id TEXT NOT NULL,
  scope_type TEXT NOT NULL DEFAULT 'featured_workspace' CHECK (scope_type IN ('featured_workspace','connected_run')),
  scope_id TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','consumed','expired','revoked')),
  scopes_json TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  consumed_at TEXT,
  revoked_at TEXT,
  last_used_at TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (agent_id) REFERENCES hub_agents(agent_id),
  FOREIGN KEY (version_id) REFERENCES hub_agent_versions(version_id)
);

CREATE TABLE IF NOT EXISTS hub_model_gateway_audit (
  event_id TEXT PRIMARY KEY,
  event_type TEXT NOT NULL,
  actor TEXT NOT NULL,
  agent_id TEXT,
  profile_id TEXT,
  model_id TEXT,
  request_id TEXT,
  error_code TEXT,
  safe_detail_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

"""

INDEX_SCHEMA = """
CREATE INDEX IF NOT EXISTS idx_versions_agent ON hub_agent_versions(agent_id);
CREATE INDEX IF NOT EXISTS idx_health_agent_version ON hub_health_checks(agent_id, version_id, checked_at);
CREATE INDEX IF NOT EXISTS idx_audit_agent ON hub_audit_events(agent_id, created_at);
CREATE INDEX IF NOT EXISTS idx_invocations_agent ON hub_invocations(agent_id, started_at);
CREATE INDEX IF NOT EXISTS idx_conformance_version ON hub_conformance_runs(version_id, completed_at);
CREATE INDEX IF NOT EXISTS idx_rate_limit_window ON hub_rate_limits(window_start);
CREATE INDEX IF NOT EXISTS idx_model_profiles_owner ON hub_model_profiles(owner_user_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_model_bindings_owner ON hub_model_bindings(owner_user_id, agent_id);
CREATE INDEX IF NOT EXISTS idx_model_gateway_audit ON hub_model_gateway_audit(actor, created_at);
CREATE INDEX IF NOT EXISTS idx_model_delegations_agent ON hub_model_delegations(agent_id, expires_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_model_grant_request_once
  ON hub_model_gateway_grants(delegation_id, request_id)
  WHERE delegation_id IS NOT NULL;
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _migrate_model_runtime_constraints(conn: sqlite3.Connection) -> None:
    """Upgrade early T4 tables whose SQLite CHECK constraints cannot be altered in place."""
    grants_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'hub_model_gateway_grants'"
    ).fetchone()["sql"]
    if "'expired'" not in grants_sql:
        conn.executescript(
            """
            DROP TABLE IF EXISTS hub_model_gateway_grants_v2;
            CREATE TABLE hub_model_gateway_grants_v2 (
              jti TEXT PRIMARY KEY,
              delegation_id TEXT,
              user_id TEXT NOT NULL,
              agent_id TEXT NOT NULL,
              profile_id TEXT NOT NULL,
              model_id TEXT NOT NULL,
              request_id TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'issued'
                CHECK (status IN ('issued','consumed','expired','revoked')),
              issued_at TEXT,
              expires_at TEXT NOT NULL,
              consumed_at TEXT,
              revoked_at TEXT,
              used_at TEXT,
              created_at TEXT NOT NULL,
              FOREIGN KEY (profile_id) REFERENCES hub_model_profiles(profile_id)
            );
            INSERT INTO hub_model_gateway_grants_v2 (
              jti, delegation_id, user_id, agent_id, profile_id, model_id, request_id,
              status, issued_at, expires_at, consumed_at, revoked_at, used_at, created_at
            )
            SELECT
              jti, delegation_id, user_id, agent_id, profile_id, model_id, request_id,
              CASE
                WHEN status IN ('consumed','revoked') THEN status
                WHEN datetime(expires_at) <= datetime('now') THEN 'expired'
                ELSE 'issued'
              END,
              issued_at, expires_at, consumed_at, revoked_at, used_at, created_at
            FROM hub_model_gateway_grants;
            DROP TABLE hub_model_gateway_grants;
            ALTER TABLE hub_model_gateway_grants_v2 RENAME TO hub_model_gateway_grants;
            """
        )

    delegations_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'hub_model_delegations'"
    ).fetchone()["sql"]
    if "featured_workspace" not in delegations_sql or "'expired'" not in delegations_sql:
        conn.executescript(
            """
            DROP TABLE IF EXISTS hub_model_delegations_v2;
            CREATE TABLE hub_model_delegations_v2 (
              token_hash TEXT PRIMARY KEY,
              delegation_id TEXT UNIQUE,
              user_id TEXT NOT NULL,
              display_name TEXT NOT NULL,
              agent_id TEXT NOT NULL,
              version_id TEXT NOT NULL,
              scope_type TEXT NOT NULL DEFAULT 'featured_workspace'
                CHECK (scope_type IN ('featured_workspace','connected_run')),
              scope_id TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active','consumed','expired','revoked')),
              scopes_json TEXT NOT NULL,
              expires_at TEXT NOT NULL,
              consumed_at TEXT,
              revoked_at TEXT,
              last_used_at TEXT,
              created_at TEXT NOT NULL,
              FOREIGN KEY (agent_id) REFERENCES hub_agents(agent_id),
              FOREIGN KEY (version_id) REFERENCES hub_agent_versions(version_id)
            );
            INSERT INTO hub_model_delegations_v2 (
              token_hash, delegation_id, user_id, display_name, agent_id, version_id,
              scope_type, scope_id, status, scopes_json, expires_at, consumed_at,
              revoked_at, last_used_at, created_at
            )
            SELECT
              token_hash, delegation_id, user_id, display_name, agent_id, version_id,
              CASE
                WHEN scope_type = 'connected_run' THEN 'connected_run'
                ELSE 'featured_workspace'
              END,
              scope_id,
              CASE
                WHEN status IN ('consumed','revoked') THEN status
                WHEN datetime(expires_at) <= datetime('now') THEN 'expired'
                ELSE 'active'
              END,
              scopes_json, expires_at, consumed_at, revoked_at, last_used_at, created_at
            FROM hub_model_delegations;
            DROP TABLE hub_model_delegations;
            ALTER TABLE hub_model_delegations_v2 RENAME TO hub_model_delegations;
            """
        )


def init_db(path: Path) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        invocation_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(hub_invocations)").fetchall()
        }
        if "usage_json" not in invocation_columns:
            conn.execute("ALTER TABLE hub_invocations ADD COLUMN usage_json TEXT NOT NULL DEFAULT '{}'")
        version_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(hub_agent_versions)").fetchall()
        }
        if "featured_approved" not in version_columns:
            conn.execute(
                "ALTER TABLE hub_agent_versions ADD COLUMN featured_approved INTEGER NOT NULL DEFAULT 0"
            )
        for table, additions in {
            "hub_model_gateway_grants": {
                "delegation_id": "TEXT",
                "status": "TEXT NOT NULL DEFAULT 'issued'",
                "issued_at": "TEXT",
                "consumed_at": "TEXT",
                "revoked_at": "TEXT",
                "used_at": "TEXT",
            },
            "hub_model_delegations": {
                "delegation_id": "TEXT",
                "scope_type": "TEXT NOT NULL DEFAULT 'featured_workspace'",
                "scope_id": "TEXT NOT NULL DEFAULT ''",
                "status": "TEXT NOT NULL DEFAULT 'active'",
                "consumed_at": "TEXT",
                "revoked_at": "TEXT",
                "last_used_at": "TEXT",
            },
        }.items():
            columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            for name, ddl in additions.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
            if table == "hub_model_delegations":
                conn.execute(
                    """
                    UPDATE hub_model_delegations
                    SET delegation_id = 'legacy_' || substr(token_hash, 1, 24)
                    WHERE delegation_id IS NULL
                    """
                )
            if table == "hub_model_gateway_grants":
                conn.execute(
                    """
                    UPDATE hub_model_gateway_grants
                    SET issued_at = COALESCE(issued_at, created_at)
                    """
                )
        _migrate_model_runtime_constraints(conn)
        conn.executescript(INDEX_SCHEMA)
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
