from __future__ import annotations

import argparse
import json
import re
import uuid
from pathlib import Path
from typing import Any

import yaml

from .config import Settings
from .db import database, init_database
from .ingestion import (
    DocumentMetadata,
    DuplicateDocument,
    IngestionError,
    cleanup_prepared_pdf_ingestion,
    ingest_pdf,
    prepare_pdf_ingestion,
    write_prepared_pdf_ingestion,
)


def resolve_repo_path(settings: Settings, value: str) -> Path:
    path = (settings.repo_root / value).resolve()
    try:
        path.relative_to(settings.repo_root)
    except ValueError as exc:
        raise ValueError("manifest path escapes repository root") from exc
    return path


def import_manifest(settings: Settings, manifest_path: Path) -> dict:
    init_database(settings)
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    space_id = payload.get("space_id", "math-b1-shared")
    defaults = payload.get("defaults", {})
    documents = payload.get("documents", [])
    imported = 0
    skipped = 0
    failed: list[dict] = []
    with database(settings) as conn:
        for item in documents:
            relative_path = str(item["path"])
            path = resolve_repo_path(settings, relative_path)
            title = item.get("title") or path.stem
            material_type = item.get("material_type") or infer_material_type(path)
            metadata = DocumentMetadata(
                title=title,
                material_type=material_type,
                license_status=item.get("license_status", defaults.get("license_status", "private-team-use")),
                semester=item.get("semester", defaults.get("semester")),
                source_url=item.get("source_url", defaults.get("source_url")),
                course=item.get("course", defaults.get("course", "数学分析 B1")),
                source_type=item.get("source_type", defaults.get("source_type", "team-material")),
            )
            try:
                ingest_pdf(conn, settings, path, item.get("space_id", space_id), metadata)
                imported += 1
            except DuplicateDocument:
                skipped += 1
            except (IngestionError, KeyError, ValueError) as exc:
                failed.append({"path": relative_path, "error": str(exc)})
    return {"imported": imported, "skipped": skipped, "failed": failed}


def infer_material_type(path: Path) -> str:
    name = path.name
    if "真题" in name or re.search(r"20\d{2}", name):
        return "历年真题与答案"
    if "提纲" in name:
        return "复习提纲"
    if "习题" in name:
        return "习题课讲义"
    if "笔记" in name:
        return "课程笔记"
    return "课程资料"


def stable_marketplace_id(prefix: str, slug: str) -> str:
    normalized = re.sub(r"[^a-z0-9-]+", "-", slug.lower()).strip("-")
    if not normalized:
        normalized = uuid.uuid5(uuid.NAMESPACE_URL, slug).hex[:12]
    return f"{prefix}-{normalized}"


def marketplace_cover_asset(item: dict[str, Any], slug: str) -> str:
    normalized_slug = re.sub(r"[^a-z0-9-]+", "-", slug.lower()).strip("-")
    if not normalized_slug:
        normalized_slug = uuid.uuid5(uuid.NAMESPACE_URL, slug).hex[:12]
    raw = str(item.get("cover_asset") or f"/assets/course-covers/{normalized_slug}.png").strip()
    asset = raw.replace("\\", "/")
    if not asset:
        return ""
    if not asset.startswith("/assets/course-covers/") or not asset.endswith(".png"):
        raise ValueError("marketplace cover_asset must be a local /assets/course-covers/*.png path")
    relative_parts = asset[len("/assets/course-covers/") :].split("/")
    if any(part in {"", ".", ".."} for part in relative_parts):
        raise ValueError("marketplace cover_asset must not contain empty or parent path segments")
    if not re.fullmatch(r"/assets/course-covers/[A-Za-z0-9._/-]+\.png", asset):
        raise ValueError("marketplace cover_asset contains unsupported characters")
    return asset


def marketplace_document_count(conn, version_id: str | None) -> int:
    if not version_id:
        return 0
    return int(
        conn.execute(
            "SELECT count(*) AS count FROM publication_documents WHERE version_id = ?",
            (version_id,),
        ).fetchone()["count"]
        or 0
    )


def source_documents_for_seed(conn, source_space_id: str) -> list[Any]:
    return conn.execute(
        """SELECT d.*, s.source_url, s.license_status, s.source_type
           FROM documents d
           JOIN sources s ON s.id = d.source_id
           JOIN revisions r ON r.document_id = d.id AND r.status = 'active'
           WHERE d.space_id = ? AND d.status = 'active'
           ORDER BY d.created_at ASC, d.id ASC""",
        (source_space_id,),
    ).fetchall()


def seed_marketplace(settings: Settings, manifest_path: Path) -> dict:
    init_database(settings)
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    courses = payload.get("courses", [])
    if not isinstance(courses, list):
        raise ValueError("marketplace manifest field 'courses' must be a list")

    created = 0
    skipped = 0
    populated_documents = 0
    failed: list[dict[str, str]] = []

    with database(settings) as conn:
        for item in courses:
            try:
                if not isinstance(item, dict):
                    raise ValueError("course item must be a mapping")
                slug = str(item["slug"]).strip()
                name = str(item.get("name") or item.get("course") or slug).strip()
                course = str(item.get("course") or name).strip()
                description = str(item.get("description") or item.get("short_description") or "").strip()
                tags = [str(tag).strip() for tag in item.get("tags", []) if str(tag).strip()]
                author_id = str(item.get("author_id", "demo-a")).strip() or "demo-a"
                library_id = str(item.get("library_id") or stable_marketplace_id("marketplace-library", slug))
                space_id = str(item.get("space_id") or stable_marketplace_id("marketplace-space", slug))
                version_id = str(item.get("version_id") or stable_marketplace_id("marketplace-version", slug))
                seed_version = int(payload.get("version", item.get("seed_version", 1)) or 1)
                demo_kind = str(item.get("demo_kind", "demo-placeholder")).strip() or "demo-placeholder"
                if demo_kind not in {"real", "demo-placeholder"}:
                    raise ValueError(f"unsupported demo_kind: {demo_kind}")
                cover_asset = marketplace_cover_asset(item, slug)

                existing_by_slug = conn.execute(
                    """SELECT pl.*
                       FROM marketplace_course_metadata m
                       JOIN published_libraries pl ON pl.id = m.library_id
                       WHERE m.slug = ?""",
                    (slug,),
                ).fetchone()
                existing = existing_by_slug or conn.execute(
                    "SELECT * FROM published_libraries WHERE id = ?",
                    (library_id,),
                ).fetchone()

                if not existing:
                    conn.execute("BEGIN IMMEDIATE")
                    conn.execute(
                        """INSERT OR IGNORE INTO spaces(id, name, space_type, owner_id, visibility)
                           VALUES (?, ?, 'subscribed', ?, 'public-subscription')""",
                        (space_id, name, author_id),
                    )
                    conn.execute(
                        """INSERT INTO published_libraries
                           (id, space_id, author_id, name, course, description, tags_json,
                            status, current_version_id)
                           VALUES (?, ?, ?, ?, ?, ?, ?, 'published', ?)""",
                        (
                            library_id,
                            space_id,
                            author_id,
                            name,
                            course,
                            description,
                            json.dumps(tags, ensure_ascii=False),
                            version_id,
                        ),
                    )
                    conn.execute(
                        """INSERT INTO publication_versions
                           (id, library_id, version_number, status, name, course, description,
                            tags_json, submitted_by, reviewed_by, review_note, reviewed_at,
                            published_at)
                           VALUES (?, ?, 1, 'published', ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP,
                            CURRENT_TIMESTAMP)""",
                        (
                            version_id,
                            library_id,
                            name,
                            course,
                            description,
                            json.dumps(tags, ensure_ascii=False),
                            author_id,
                            author_id,
                            "演示课程 seed 自动发布；无资料课程仅作演示元数据。",
                        ),
                    )
                    conn.execute(
                        """INSERT INTO marketplace_course_metadata
                           (library_id, slug, demo_kind, cover_icon, cover_theme, cover_asset,
                            short_description, empty_state, sort_order, seed_version)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            library_id,
                            slug,
                            demo_kind,
                            str(item.get("cover_icon", "◇")),
                            str(item.get("cover_theme", "indigo")),
                            cover_asset,
                            str(item.get("short_description", "")),
                            str(item.get("empty_state", "资料待补充")),
                            int(item.get("sort_order", 100)),
                            seed_version,
                        ),
                    )
                    conn.execute(
                        """INSERT INTO audit_events
                           (id, actor_id, event_type, target_type, target_id, metadata_json)
                           VALUES (?, ?, 'marketplace_demo_seeded', 'published_library', ?, ?)""",
                        (
                            str(uuid.uuid4()),
                            author_id,
                            library_id,
                            json.dumps({"slug": slug, "demo_kind": demo_kind}, ensure_ascii=False),
                        ),
                    )
                    conn.commit()
                    created += 1
                    existing = conn.execute(
                        "SELECT * FROM published_libraries WHERE id = ?", (library_id,)
                    ).fetchone()
                else:
                    library_id = str(existing["id"])
                    space_id = str(existing["space_id"])
                    version_id = str(existing["current_version_id"] or version_id)
                    conn.execute(
                        """INSERT OR IGNORE INTO marketplace_course_metadata
                           (library_id, slug, demo_kind, cover_icon, cover_theme, cover_asset,
                            short_description, empty_state, sort_order, seed_version)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            library_id,
                            slug,
                            demo_kind,
                            str(item.get("cover_icon", "◇")),
                            str(item.get("cover_theme", "indigo")),
                            cover_asset,
                            str(item.get("short_description", "")),
                            str(item.get("empty_state", "资料待补充")),
                            int(item.get("sort_order", 100)),
                            seed_version,
                        ),
                    )
                    conn.execute(
                        """UPDATE marketplace_course_metadata
                           SET updated_at = CASE
                                 WHEN cover_asset <> ? OR seed_version < ? THEN CURRENT_TIMESTAMP
                                 ELSE updated_at
                               END,
                               cover_asset = ?,
                               seed_version = MAX(seed_version, ?)
                           WHERE library_id = ?""",
                        (cover_asset, seed_version, cover_asset, seed_version, library_id),
                    )
                    conn.commit()
                    skipped += 1

                source_space_id = str(item.get("source_space_id", "")).strip()
                if source_space_id and demo_kind == "real":
                    library = conn.execute(
                        "SELECT * FROM published_libraries WHERE id = ?", (library_id,)
                    ).fetchone()
                    current_version_id = str(library["current_version_id"] or version_id)
                    if marketplace_document_count(conn, current_version_id) == 0:
                        source_rows = source_documents_for_seed(conn, source_space_id)
                        prepared_items = []
                        try:
                            for source_row in source_rows:
                                prepared = prepare_pdf_ingestion(
                                    settings,
                                    Path(str(source_row["file_path"])),
                                    copy_to_uploads=True,
                                )
                                prepared_items.append((source_row, prepared))
                            if prepared_items:
                                conn.execute("BEGIN IMMEDIATE")
                                for source_row, prepared in prepared_items:
                                    metadata = DocumentMetadata(
                                        title=str(source_row["title"]),
                                        course=str(source_row["course"]),
                                        semester=source_row["semester"],
                                        material_type=str(source_row["material_type"]),
                                        source_url=source_row["source_url"],
                                        license_status=str(source_row["license_status"]),
                                        source_type="marketplace-demo-snapshot",
                                    )
                                    write_prepared_pdf_ingestion(
                                        conn,
                                        metadata,
                                        space_id,
                                        prepared,
                                        source_access_mode="public-subscription-demo",
                                        manage_transaction=False,
                                    )
                                    conn.execute(
                                        """INSERT INTO publication_documents
                                           (version_id, document_id, source_document_id, use_in_rag,
                                            can_preview, can_download, review_status, review_note)
                                           VALUES (?, ?, ?, 1, 1, 0, 'approved', ?)""",
                                        (
                                            current_version_id,
                                            prepared.document_id,
                                            source_row["id"],
                                            "演示种子从已导入数学分析资料生成。",
                                        ),
                                    )
                                conn.execute(
                                    "UPDATE published_libraries SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                                    (library_id,),
                                )
                                conn.commit()
                                populated_documents += len(prepared_items)
                        except Exception:
                            conn.rollback()
                            for _source_row, prepared in prepared_items:
                                cleanup_prepared_pdf_ingestion(prepared)
                            raise

                # Subscribe only after this manifest item has been validated and
                # its real marketplace library has been created or resolved.
                if demo_kind == "real":
                    conn.execute(
                        """INSERT OR IGNORE INTO library_subscriptions
                           (library_id, user_id, status, subscribed_at)
                           VALUES (?, ?, 'active', CURRENT_TIMESTAMP)""",
                        (library_id, author_id),
                    )
                    conn.commit()
            except Exception as exc:
                failed.append({"slug": str(item.get("slug", "<unknown>")) if isinstance(item, dict) else "<invalid>", "error": str(exc)})

    return {
        "created": created,
        "skipped": skipped,
        "populated_documents": populated_documents,
        "failed": failed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="USTC course agent CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-db")
    import_parser = subparsers.add_parser("import-manifest")
    import_parser.add_argument("manifest", type=Path)
    seed_parser = subparsers.add_parser("seed-marketplace")
    seed_parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    settings = Settings()
    if args.command == "init-db":
        init_database(settings)
        print(settings.database_path)
    elif args.command == "import-manifest":
        result = import_manifest(settings, args.manifest.resolve())
        print(result)
        if result["failed"]:
            raise SystemExit(1)
    elif args.command == "seed-marketplace":
        result = seed_marketplace(settings, args.manifest.resolve())
        print(result)
        if result["failed"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
