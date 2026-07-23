from __future__ import annotations

import argparse
import re
from pathlib import Path

import yaml

from .config import Settings
from .db import database, init_database
from .ingestion import DocumentMetadata, DuplicateDocument, IngestionError, ingest_pdf


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


def main() -> None:
    parser = argparse.ArgumentParser(description="USTC course agent CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-db")
    import_parser = subparsers.add_parser("import-manifest")
    import_parser.add_argument("manifest", type=Path)
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


if __name__ == "__main__":
    main()

