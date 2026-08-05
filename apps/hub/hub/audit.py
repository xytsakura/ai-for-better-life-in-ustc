from __future__ import annotations

import json
import sqlite3
from typing import Any

from .utils import new_id, now_iso


def record_audit(
    conn: sqlite3.Connection,
    event_type: str,
    *,
    actor: str,
    agent_id: str | None = None,
    version_id: str | None = None,
    reason: str = "",
    safe_detail: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO hub_audit_events (
          event_id, event_type, agent_id, version_id, actor, reason, safe_detail_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_id("audit"),
            event_type,
            agent_id,
            version_id,
            actor,
            reason,
            json.dumps(safe_detail or {}, ensure_ascii=False, sort_keys=True),
            now_iso(),
        ),
    )


def list_audit(conn: sqlite3.Connection, agent_id: str | None = None) -> list[dict[str, Any]]:
    if agent_id:
        rows = conn.execute(
            "SELECT * FROM hub_audit_events WHERE agent_id = ? ORDER BY created_at DESC",
            (agent_id,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM hub_audit_events ORDER BY created_at DESC").fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["safe_detail"] = json.loads(item.pop("safe_detail_json") or "{}")
        result.append(item)
    return result
