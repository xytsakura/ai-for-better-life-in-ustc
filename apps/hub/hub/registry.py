from __future__ import annotations

import json
import sqlite3
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException, status
from pydantic import ValidationError

from .audit import record_audit
from .config import Settings
from .schemas import AgentManifest, AgentSubmission
from .security import ensure_no_public_endpoint_leak, validate_url_safety
from .utils import canonical_json, new_id, now_iso, sha256_text


def _dump_model(model: AgentManifest) -> dict[str, Any]:
    return model.model_dump(mode="json")


def manifest_urls(manifest: AgentManifest) -> list[str]:
    integration = manifest.integration
    urls = [str(integration.launch_url)]
    if getattr(integration, "chat_endpoint", None):
        urls.append(str(integration.chat_endpoint))
    if getattr(integration, "health_endpoint", None):
        urls.append(str(integration.health_endpoint))
    urls.extend(str(url) for url in getattr(integration, "callback_urls", []))
    if manifest.icon and urlparse(manifest.icon).scheme in {"http", "https"}:
        urls.append(str(manifest.icon))
    if manifest.data_policy.privacy_url:
        urls.append(str(manifest.data_policy.privacy_url))
    return urls


def validate_manifest_urls(manifest: AgentManifest, trust_level: str, settings: Settings) -> None:
    for url in manifest_urls(manifest):
        validate_url_safety(url, trust_level, settings)


def parse_submission(raw_submission: dict[str, Any], settings: Settings) -> tuple[AgentManifest, str]:
    if "manifest" in raw_submission:
        try:
            submission = AgentSubmission.model_validate(raw_submission)
        except ValidationError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=exc.errors(include_context=False),
            ) from exc
        manifest = submission.manifest
        trust_level = submission.trust_level
    else:
        try:
            manifest = AgentManifest.model_validate(raw_submission)
        except ValidationError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=exc.errors(include_context=False),
            ) from exc
        trust_level = "third_party_external"
    validate_manifest_urls(manifest, trust_level, settings)
    return manifest, trust_level


def submit_manifest(
    conn: sqlite3.Connection,
    *,
    raw_manifest: dict[str, Any],
    submitted_by: str,
    settings: Settings,
) -> dict[str, Any]:
    manifest, trust_level = parse_submission(raw_manifest, settings)

    manifest_dict = _dump_model(manifest)
    manifest_json = canonical_json(manifest_dict)
    manifest_hash = sha256_text(manifest_json)
    now = now_iso()
    version_id = new_id("ver")

    existing = conn.execute(
        "SELECT agent_id FROM hub_agents WHERE agent_id = ?", (manifest.id,)
    ).fetchone()
    if existing is None:
        conn.execute(
            """
            INSERT INTO hub_agents (
              agent_id, name, owner, category, summary, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                manifest.id,
                manifest.name,
                manifest.owner,
                manifest.category,
                manifest.description,
                now,
                now,
            ),
        )
    else:
        conn.execute(
            """
            UPDATE hub_agents
            SET name = ?, owner = ?, category = ?, summary = ?, updated_at = ?
            WHERE agent_id = ?
            """,
            (
                manifest.name,
                manifest.owner,
                manifest.category,
                manifest.description,
                now,
                manifest.id,
            ),
        )

    try:
        conn.execute(
            """
            INSERT INTO hub_agent_versions (
              version_id, agent_id, version, manifest_json, manifest_hash, review_status,
              deployment_status, trust_level, submitted_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'pending', 'staged', ?, ?, ?, ?)
            """,
            (
                version_id,
                manifest.id,
                manifest.version,
                manifest_json,
                manifest_hash,
                trust_level,
                submitted_by,
                now,
                now,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "version_already_exists"}) from exc

    record_audit(
        conn,
        "agent_version_submitted",
        actor=submitted_by,
        agent_id=manifest.id,
        version_id=version_id,
        safe_detail={"manifest_hash": manifest_hash, "mode": manifest.integration.mode, "trust_level": trust_level},
    )
    return get_agent(conn, manifest.id, include_private=True)


def _version_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["manifest"] = json.loads(item.pop("manifest_json"))
    item["featured_approved"] = bool(item.get("featured_approved"))
    return item


def get_agent(conn: sqlite3.Connection, agent_id: str, *, include_private: bool = False) -> dict[str, Any]:
    agent = conn.execute("SELECT * FROM hub_agents WHERE agent_id = ?", (agent_id,)).fetchone()
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"error": "agent_not_found"})
    versions = [
        _version_to_dict(row)
        for row in conn.execute(
            """
            SELECT * FROM hub_agent_versions
            WHERE agent_id = ?
            ORDER BY created_at DESC, rowid DESC
            """,
            (agent_id,),
        ).fetchall()
    ]
    if include_private:
        for version in versions:
            run = conn.execute(
                """
                SELECT run_id, overall_status, checks_json, started_at, completed_at
                FROM hub_conformance_runs
                WHERE version_id = ?
                ORDER BY completed_at DESC, rowid DESC
                LIMIT 1
                """,
                (version["version_id"],),
            ).fetchone()
            if run:
                version["check_run_id"] = run["run_id"]
                version["check_status"] = run["overall_status"]
                version["checks"] = json.loads(run["checks_json"] or "[]")
                version["checks_started_at"] = run["started_at"]
                version["checks_completed_at"] = run["completed_at"]
    active_version = next(
        (version for version in versions if version["version_id"] == agent["active_version_id"]), None
    )
    if active_version and not include_private:
        icon_asset = conn.execute(
            """
            SELECT file_path FROM hub_version_assets
            WHERE version_id = ? AND asset_type = 'icon'
            """,
            (active_version["version_id"],),
        ).fetchone()
        if icon_asset:
            active_version["manifest"]["icon"] = (
                f"/api/assets/agent-icons/{active_version['version_id']}"
            )
    latest_health = None
    if active_version:
        health = conn.execute(
            """
            SELECT * FROM hub_health_checks
            WHERE agent_id = ? AND version_id = ?
            ORDER BY checked_at DESC
            LIMIT 1
            """,
            (agent_id, active_version["version_id"]),
        ).fetchone()
        if health:
            latest_health = dict(health)
            latest_health["capabilities"] = json.loads(latest_health.pop("capabilities_json") or "[]")

    record = dict(agent)
    record["featured"] = bool(record["featured"])
    record["versions"] = versions if include_private else []
    record["active_version"] = active_version
    record["latest_health"] = latest_health
    if not include_private and active_version:
        record = ensure_no_public_endpoint_leak(record)
    return record


def list_agents(
    conn: sqlite3.Connection,
    *,
    include_private: bool = False,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT agent_id FROM hub_agents WHERE (? = 0 OR status = 'active') ORDER BY updated_at DESC",
        (1 if active_only else 0,),
    ).fetchall()
    result = []
    for row in rows:
        record = get_agent(conn, row["agent_id"], include_private=include_private)
        if active_only and record["active_version"] is None:
            continue
        result.append(record)
    return result


def list_submitted_agents(
    conn: sqlite3.Connection,
    *,
    submitted_by: str | None = None,
) -> list[dict[str, Any]]:
    if submitted_by:
        rows = conn.execute(
            """
            SELECT DISTINCT agent_id
            FROM hub_agent_versions
            WHERE submitted_by = ?
            ORDER BY updated_at DESC, rowid DESC
            """,
            (submitted_by,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT DISTINCT agent_id
            FROM hub_agent_versions
            ORDER BY updated_at DESC, rowid DESC
            """
        ).fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        record = get_agent(conn, row["agent_id"], include_private=True)
        if submitted_by:
            visible_versions = [
                version for version in record["versions"] if version.get("submitted_by") == submitted_by
            ]
            if not visible_versions:
                continue
            visible_ids = {version["version_id"] for version in visible_versions}
            record["versions"] = visible_versions
            if record.get("active_version") and record["active_version"]["version_id"] not in visible_ids:
                record["active_version"] = None
                record["latest_health"] = None
                record["active_version_id"] = None
                record["previous_active_version_id"] = None
                record["featured"] = False
            record["submitted_versions_count"] = len(visible_versions)
        else:
            record["submitted_versions_count"] = len(record.get("versions", []))
        result.append(record)
    return result


def get_active_version(conn: sqlite3.Connection, agent_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    agent = conn.execute("SELECT * FROM hub_agents WHERE agent_id = ?", (agent_id,)).fetchone()
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"error": "agent_not_found"})
    if agent["status"] != "active" or not agent["active_version_id"]:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "agent_not_active"})
    version = conn.execute(
        "SELECT * FROM hub_agent_versions WHERE version_id = ?",
        (agent["active_version_id"],),
    ).fetchone()
    if version is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "agent_not_active"})
    return dict(agent), _version_to_dict(version)


def review_version(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
    version_id: str,
    reviewer: str,
    decision: str,
    notes: str,
    checks: dict[str, Any],
    settings: Settings,
    featured: bool = False,
) -> dict[str, Any]:
    agent = conn.execute("SELECT * FROM hub_agents WHERE agent_id = ?", (agent_id,)).fetchone()
    version = conn.execute(
        "SELECT * FROM hub_agent_versions WHERE agent_id = ? AND version_id = ?",
        (agent_id, version_id),
    ).fetchone()
    if agent is None or version is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"error": "agent_not_found"})
    if version["review_status"] != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "version_already_reviewed"})
    if decision == "approved" and settings.require_passing_checks:
        conformance = conn.execute(
            """
            SELECT overall_status FROM hub_conformance_runs
            WHERE version_id = ?
            ORDER BY completed_at DESC, rowid DESC
            LIMIT 1
            """,
            (version_id,),
        ).fetchone()
        if conformance is None or conformance["overall_status"] != "passed":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={"error": "conformance_checks_not_passed"},
            )
    manifest = json.loads(version["manifest_json"])
    integration = manifest.get("integration", {})
    if featured and (
        decision != "approved"
        or integration.get("mode") != "connected"
        or not integration.get("callback_urls")
        or "full-workspace" not in manifest.get("capabilities", [])
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "featured_requirements_not_met"})

    now = now_iso()
    checks_json = json.dumps(checks, ensure_ascii=False, sort_keys=True)
    conn.execute(
        """
        INSERT INTO hub_reviews (
          review_id, agent_id, version_id, reviewer, decision, notes, checks_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (new_id("review"), agent_id, version_id, reviewer, decision, notes, checks_json, now),
    )
    if decision == "rejected":
        conn.execute(
            """
            UPDATE hub_agent_versions
            SET review_status = 'rejected', deployment_status = 'deprecated', updated_at = ?
            WHERE version_id = ?
            """,
            (now, version_id),
        )
        if not agent["active_version_id"]:
            conn.execute(
                "UPDATE hub_agents SET status = 'rejected', updated_at = ? WHERE agent_id = ?",
                (now, agent_id),
            )
        event_type = "agent_version_rejected"
    else:
        current_active = agent["active_version_id"]
        if current_active:
            conn.execute(
                """
                UPDATE hub_agent_versions
                SET deployment_status = 'superseded', updated_at = ?
                WHERE version_id = ?
                """,
                (now, current_active),
            )
        conn.execute(
            """
            UPDATE hub_agent_versions
            SET review_status = 'approved', deployment_status = 'active', featured_approved = ?, updated_at = ?
            WHERE version_id = ?
            """,
            (1 if featured else 0, now, version_id),
        )
        conn.execute(
            """
            UPDATE hub_agents
            SET status = 'active', active_version_id = ?, previous_active_version_id = ?,
                featured = ?, updated_at = ?
            WHERE agent_id = ?
            """,
            (version_id, current_active, 1 if featured else 0, now, agent_id),
        )
        event_type = "agent_version_approved"

    record_audit(
        conn,
        event_type,
        actor=reviewer,
        agent_id=agent_id,
        version_id=version_id,
        reason=notes,
        safe_detail={"checks": checks},
    )
    return get_agent(conn, agent_id, include_private=True)


def suspend_agent(conn: sqlite3.Connection, *, agent_id: str, actor: str, reason: str) -> dict[str, Any]:
    updated = conn.execute(
        "UPDATE hub_agents SET status = 'suspended', updated_at = ? WHERE agent_id = ? AND status = 'active'",
        (now_iso(), agent_id),
    ).rowcount
    if not updated:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "agent_not_active"})
    record_audit(conn, "agent_suspended", actor=actor, agent_id=agent_id, reason=reason)
    return get_agent(conn, agent_id, include_private=True)


def restore_agent(conn: sqlite3.Connection, *, agent_id: str, actor: str, reason: str) -> dict[str, Any]:
    updated = conn.execute(
        """
        UPDATE hub_agents SET status = 'active', updated_at = ?
        WHERE agent_id = ? AND status = 'suspended' AND active_version_id IS NOT NULL
        """,
        (now_iso(), agent_id),
    ).rowcount
    if not updated:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "agent_not_restorable"})
    record_audit(conn, "agent_restored", actor=actor, agent_id=agent_id, reason=reason)
    return get_agent(conn, agent_id, include_private=True)


def deprecate_agent(conn: sqlite3.Connection, *, agent_id: str, actor: str, reason: str) -> dict[str, Any]:
    updated = conn.execute(
        """
        UPDATE hub_agents SET status = 'deprecated', updated_at = ?
        WHERE agent_id = ? AND status IN ('active','suspended')
        """,
        (now_iso(), agent_id),
    ).rowcount
    if not updated:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "agent_not_deprecatable"})
    record_audit(conn, "agent_deprecated", actor=actor, agent_id=agent_id, reason=reason)
    return get_agent(conn, agent_id, include_private=True)


def ensure_health_allows_invocation(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
    version_id: str,
    settings: Settings,
) -> None:
    threshold = max(1, settings.health_failure_threshold)
    rows = conn.execute(
        """
        SELECT status FROM hub_health_checks
        WHERE agent_id = ? AND version_id = ?
        ORDER BY checked_at DESC, rowid DESC
        LIMIT ?
        """,
        (agent_id, version_id, threshold),
    ).fetchall()
    if len(rows) >= threshold and all(row["status"] != "ok" for row in rows):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "agent_unavailable"},
        )


def rollback_agent(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
    version_id: str | None,
    actor: str,
    reason: str,
) -> dict[str, Any]:
    agent = conn.execute("SELECT * FROM hub_agents WHERE agent_id = ?", (agent_id,)).fetchone()
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"error": "agent_not_found"})
    target = version_id or agent["previous_active_version_id"]
    if not target or target == agent["active_version_id"]:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "rollback_target_unavailable"})
    version = conn.execute(
        """
        SELECT * FROM hub_agent_versions
        WHERE agent_id = ? AND version_id = ? AND review_status = 'approved'
          AND deployment_status IN ('active','superseded')
        """,
        (agent_id, target),
    ).fetchone()
    if version is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "rollback_target_unavailable"})
    now = now_iso()
    current = agent["active_version_id"]
    if current:
        conn.execute(
            "UPDATE hub_agent_versions SET deployment_status = 'superseded', updated_at = ? WHERE version_id = ?",
            (now, current),
        )
    conn.execute(
        "UPDATE hub_agent_versions SET deployment_status = 'active', updated_at = ? WHERE version_id = ?",
        (now, target),
    )
    conn.execute(
        """
        UPDATE hub_agents
        SET status = 'active', active_version_id = ?, previous_active_version_id = ?, featured = ?, updated_at = ?
        WHERE agent_id = ?
        """,
        (target, current, version["featured_approved"], now, agent_id),
    )
    record_audit(conn, "agent_rollback", actor=actor, agent_id=agent_id, version_id=target, reason=reason)
    return get_agent(conn, agent_id, include_private=True)
