"""Document ingestion pipeline: parse, chunk, deduplicate, and index.

Public API (stable):
    DocumentMetadata   -- metadata for a document being ingested.
    IngestionError     -- base error for ingestion failures.
    DuplicateDocument  -- raised when a SHA-256 duplicate is detected.

    PyMuPDFParser      -- default PDF parser, satisfies DocumentParserProto.
    SentenceChunking   -- default chunking strategy, satisfies ChunkingStrategyProto.
    FTS5IndexWriter    -- default index writer, satisfies IndexWriterProto.

    ingest_pdf         -- backward-compatible wrapper.
    delete_document    -- backward-compatible wrapper.
    reparse_document   -- backward-compatible wrapper.
    document_details   -- backward-compatible wrapper.
    validate_pdf / file_sha256 / classify_page / chunk_page / extract_pdf
                       -- legacy module-level functions, kept for test compatibility.

To swap parsers, provide a DocumentParserProto implementation.
To swap chunking, provide a ChunkingStrategyProto implementation.
To swap index writing (e.g. vector DB), provide an IndexWriterProto implementation.
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path

import fitz

from .config import Settings
from .ocr import read_ocr_sidecar
from .tokenizer import normalize_text, tokenize_for_search
from .types import (
    ChunkRecord,
    ChunkingStrategyProto,
    DocumentParserProto,
    IndexWriterProto,
    PageRecord,
    ParseCounts,
    ParseOutput,
)


# ---- Errors -----------------------------------------------------------

class IngestionError(Exception):
    pass


class DuplicateDocument(IngestionError):
    def __init__(self, document_id: str):
        super().__init__("duplicate document")
        self.document_id = document_id


# ---- Document metadata ------------------------------------------------

@dataclass(frozen=True)
class DocumentMetadata:
    title: str
    material_type: str
    license_status: str
    semester: str | None = None
    source_url: str | None = None
    course: str = "数学分析 B1"
    source_type: str = "team-material"


# ---- Utilities --------------------------------------------------------

def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def classify_page(text: str) -> str:
    """Classify a parsed page as text_ok, needs_ocr, needs_review, or failed."""
    normalized = normalize_text(text)
    if len(normalized.replace(" ", "")) < 30:
        return "needs_ocr"
    bad = sum(
        1
        for char in text
        if char == "\ufffd"
        or (unicodedata.category(char) in {"Cc", "Cs"} and char not in "\n\r\t")
    )
    if text and bad / len(text) > 0.10:
        return "needs_review"
    return "text_ok"


# ---- Chunking strategy ------------------------------------------------

class SentenceChunking:
    """Default chunking: split at sentence boundaries within character limits.

    Satisfies ChunkingStrategyProto.
    """

    @staticmethod
    def chunk(text: str, max_chars: int = 1800, overlap: int = 180) -> list[str]:
        normalized = normalize_text(text)
        if not normalized:
            return []
        chunks: list[str] = []
        start = 0
        while start < len(normalized):
            end = min(len(normalized), start + max_chars)
            if end < len(normalized):
                boundary = max(
                    normalized.rfind(mark, start, end) for mark in ("。", "；", "\n", " ")
                )
                if boundary > start + max_chars // 2:
                    end = boundary + 1
            chunks.append(normalized[start:end].strip())
            if end >= len(normalized):
                break
            start = max(start + 1, end - overlap)
        return [c for c in chunks if c]


def chunk_page(text: str, max_chars: int = 1800, overlap: int = 180) -> list[str]:
    """Backward-compatible wrapper."""
    return SentenceChunking.chunk(text, max_chars, overlap)


# ---- PDF parser -------------------------------------------------------

class PyMuPDFParser:
    """PDF parser using a validated OCR sidecar or PyMuPDF as fallback.

    Satisfies DocumentParserProto.
    """

    PARSER_VERSION = "ocr-markdown-sidecar-or-pymupdf-v2"

    @property
    def version(self) -> str:
        return self.PARSER_VERSION

    @staticmethod
    def validate(path: Path, max_bytes: int) -> None:
        if not path.is_file():
            raise IngestionError("file not found")
        if path.suffix.lower() != ".pdf":
            raise IngestionError("only PDF files are supported")
        if path.stat().st_size > max_bytes:
            raise IngestionError("file exceeds 50 MiB limit")
        with path.open("rb") as handle:
            if handle.read(5) != b"%PDF-":
                raise IngestionError("file content is not a PDF")

    @staticmethod
    def extract(path: Path, revision_id: str) -> ParseOutput:
        pages: list[PageRecord] = []
        chunks: list[ChunkRecord] = []
        counts = ParseCounts()
        sidecar = read_ocr_sidecar(path)
        pdf = fitz.open(path)
        for page_index in range(len(pdf)):
            page_number = page_index + 1
            page_id = str(uuid.uuid4())
            try:
                if sidecar is not None:
                    text = sidecar.pages[page_number]
                    # A completed OCR pass may legitimately produce a short page.
                    # Only blank OCR pages are kept out of the search index.
                    status = "text_ok" if normalize_text(text) else "needs_ocr"
                else:
                    text = pdf[page_index].get_text("text")
                    status = classify_page(text)
            except Exception:
                text = ""
                status = "failed"
            setattr(counts, status, getattr(counts, status) + 1)
            pages.append(PageRecord(page_id, revision_id, page_number, status, text))
            if status != "text_ok":
                continue
            for ordinal, content in enumerate(SentenceChunking.chunk(text)):
                chunk_id = str(uuid.uuid4())
                chunks.append(
                    ChunkRecord(
                        chunk_id, revision_id, page_number,
                        ordinal, content, tokenize_for_search(content),
                    )
                )
        pdf.close()
        return ParseOutput(pages=pages, chunks=chunks, counts=counts)


# ---- FTS5 index writer ------------------------------------------------

class FTS5IndexWriter:
    """Write parsed chunks into SQLite FTS5 virtual table.

    Satisfies IndexWriterProto.
    """

    @staticmethod
    def write_chunks(conn: sqlite3.Connection, chunk_rows: list[tuple]) -> None:
        conn.executemany(
            "INSERT INTO chunk_fts(chunk_id, search_text) VALUES (?, ?)",
            ((row[0], row[5]) for row in chunk_rows),
        )

    @staticmethod
    def delete_chunks(conn: sqlite3.Connection, chunk_ids: list[str]) -> None:
        if chunk_ids:
            conn.executemany(
                "DELETE FROM chunk_fts WHERE chunk_id = ?",
                ((item,) for item in chunk_ids),
            )


# ---- Legacy module-level references -----------------------------------

PARSER_VERSION = PyMuPDFParser.PARSER_VERSION

_default_parser = PyMuPDFParser()
_default_chunker = SentenceChunking()
_default_index_writer = FTS5IndexWriter()


# ---- Document ingestion -----------------------------------------------

def _active_duplicate(conn: sqlite3.Connection, space_id: str, content_hash: str) -> str | None:
    row = conn.execute(
        """SELECT d.id FROM documents d
           JOIN revisions r ON r.document_id = d.id AND r.status = 'active'
           WHERE d.space_id = ? AND d.status = 'active' AND r.content_hash = ?
           LIMIT 1""",
        (space_id, content_hash),
    ).fetchone()
    return str(row["id"]) if row else None


def extract_pdf(path: Path, revision_id: str) -> tuple[list[tuple], list[tuple], dict[str, int]]:
    """Backward-compatible wrapper; delegates to PyMuPDFParser."""
    output = _default_parser.extract(path, revision_id)
    return output.as_db_rows()


def _write_ingestion_records(
    conn: sqlite3.Connection,
    metadata: DocumentMetadata,
    document_id: str,
    source_id: str,
    space_id: str,
    stored_path: Path,
    content_hash: str,
    parse_output: ParseOutput,
    copy_to_uploads: bool,
    revision_id: str,
) -> None:
    """Write all ingestion records inside a single transaction."""
    page_rows, chunk_rows, counts = parse_output.as_db_rows()
    parse_status = "ready" if counts["text_ok"] else "needs_ocr"
    conn.execute("BEGIN")
    try:
        conn.execute(
            """INSERT INTO sources
               (id, source_type, source_url, license_status, access_mode)
               VALUES (?, ?, ?, ?, 'private-team-use')""",
            (source_id, metadata.source_type, metadata.source_url, metadata.license_status),
        )
        conn.execute(
            """INSERT INTO documents
               (id, space_id, source_id, title, course, semester, material_type,
                file_path, is_repo_source, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')""",
            (
                document_id, space_id, source_id, metadata.title, metadata.course,
                metadata.semester, metadata.material_type, str(stored_path),
                0 if copy_to_uploads else 1,
            ),
        )
        conn.execute(
            """INSERT INTO revisions
               (id, document_id, content_hash, parser_version, page_count,
                searchable_pages, needs_ocr_pages, needs_review_pages, failed_pages,
                parse_status, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')""",
            (
                revision_id, document_id, content_hash, _default_parser.version,
                ParseCounts(**counts).total, counts["text_ok"],
                counts["needs_ocr"], counts["needs_review"], counts["failed"],
                parse_status,
            ),
        )
        conn.executemany(
            "INSERT INTO pages(id, revision_id, page_number, status, content) VALUES (?, ?, ?, ?, ?)",
            page_rows,
        )
        conn.executemany(
            """INSERT INTO chunks
               (id, revision_id, page_number, ordinal, content, search_text)
               VALUES (?, ?, ?, ?, ?, ?)""",
            chunk_rows,
        )
        _default_index_writer.write_chunks(conn, chunk_rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def ingest_pdf(
    conn: sqlite3.Connection,
    settings: Settings,
    path: Path,
    space_id: str,
    metadata: DocumentMetadata,
    *,
    copy_to_uploads: bool = False,
) -> dict:
    """Backward-compatible ingestion entry point."""
    path = path.resolve()
    _default_parser.validate(path, settings.max_upload_bytes)
    content_hash = file_sha256(path)
    duplicate = _active_duplicate(conn, space_id, content_hash)
    if duplicate:
        raise DuplicateDocument(duplicate)

    document_id = str(uuid.uuid4())
    revision_id = str(uuid.uuid4())
    source_id = str(uuid.uuid4())
    stored_path = path
    if copy_to_uploads:
        stored_path = settings.uploads_dir / f"{document_id}.pdf"
        shutil.copy2(path, stored_path)

    try:
        parse_output = _default_parser.extract(stored_path, revision_id)
    except Exception as exc:
        if copy_to_uploads and stored_path.exists():
            stored_path.unlink(missing_ok=True)
        raise IngestionError(f"PDF parse failed: {exc}") from exc

    try:
        _write_ingestion_records(
            conn, metadata, document_id, source_id, space_id, stored_path,
            content_hash, parse_output, copy_to_uploads, revision_id,
        )
    except Exception:
        if copy_to_uploads and stored_path.exists():
            stored_path.unlink(missing_ok=True)
        raise

    return document_details(conn, document_id)


def document_details(conn: sqlite3.Connection, document_id: str) -> dict:
    row = conn.execute(
        """SELECT d.*, r.id AS revision_id, r.page_count, r.searchable_pages,
                  r.needs_ocr_pages, r.needs_review_pages, r.failed_pages, r.parse_status,
                  s.source_url, s.license_status
           FROM documents d
           JOIN revisions r ON r.document_id = d.id AND r.status = 'active'
           JOIN sources s ON s.id = d.source_id
           WHERE d.id = ? AND d.status = 'active'""",
        (document_id,),
    ).fetchone()
    if not row:
        raise IngestionError("document not found")
    return dict(row)


def delete_document(conn: sqlite3.Connection, document_id: str) -> None:
    row = conn.execute(
        "SELECT file_path, is_repo_source FROM documents WHERE id = ? AND status = 'active'",
        (document_id,),
    ).fetchone()
    if not row:
        raise IngestionError("document not found")
    chunk_ids = [
        item["id"]
        for item in conn.execute(
            """SELECT c.id FROM chunks c JOIN revisions r ON r.id = c.revision_id
               WHERE r.document_id = ? AND r.status = 'active'""",
            (document_id,),
        )
    ]
    conn.execute("BEGIN")
    _default_index_writer.delete_chunks(conn, chunk_ids)
    conn.execute(
        "DELETE FROM chunks WHERE revision_id IN (SELECT id FROM revisions WHERE document_id = ?)",
        (document_id,),
    )
    conn.execute(
        "DELETE FROM pages WHERE revision_id IN (SELECT id FROM revisions WHERE document_id = ?)",
        (document_id,),
    )
    conn.execute(
        "UPDATE revisions SET status = 'deleted' WHERE document_id = ? AND status = 'active'",
        (document_id,),
    )
    conn.execute(
        "UPDATE documents SET status = 'deleted', deleted_at = CURRENT_TIMESTAMP WHERE id = ?",
        (document_id,),
    )
    conn.commit()
    if not row["is_repo_source"]:
        Path(row["file_path"]).unlink(missing_ok=True)


def reparse_document(conn: sqlite3.Connection, settings: Settings, document_id: str) -> dict:
    row = conn.execute(
        """SELECT d.*, s.source_url, s.license_status, s.source_type
           FROM documents d JOIN sources s ON s.id = d.source_id
           WHERE d.id = ? AND d.status = 'active'""",
        (document_id,),
    ).fetchone()
    if not row:
        raise IngestionError("document not found")
    path = Path(row["file_path"])
    _default_parser.validate(path, settings.max_upload_bytes)
    new_revision_id = str(uuid.uuid4())
    content_hash = file_sha256(path)
    parse_output = _default_parser.extract(path, new_revision_id)
    page_rows, chunk_rows, counts = parse_output.as_db_rows()
    parse_status = "ready" if counts["text_ok"] else "needs_ocr"

    old_revision_ids = [
        str(item["id"])
        for item in conn.execute(
            "SELECT id FROM revisions WHERE document_id = ? AND status = 'active'", (document_id,)
        )
    ]
    old_chunk_ids = []
    if old_revision_ids:
        marks = ",".join("?" for _ in old_revision_ids)
        old_chunk_ids = [
            str(item["id"])
            for item in conn.execute(
                f"SELECT id FROM chunks WHERE revision_id IN ({marks})", old_revision_ids
            )
        ]

    conn.execute("BEGIN")
    _default_index_writer.delete_chunks(conn, old_chunk_ids)
    conn.execute(
        "DELETE FROM chunks WHERE revision_id IN (SELECT id FROM revisions WHERE document_id = ?)",
        (document_id,),
    )
    conn.execute(
        "DELETE FROM pages WHERE revision_id IN (SELECT id FROM revisions WHERE document_id = ?)",
        (document_id,),
    )
    conn.execute(
        "UPDATE revisions SET status = 'superseded' WHERE document_id = ? AND status = 'active'",
        (document_id,),
    )
    conn.execute(
        """INSERT INTO revisions
           (id, document_id, content_hash, parser_version, page_count,
            searchable_pages, needs_ocr_pages, needs_review_pages, failed_pages,
            parse_status, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')""",
        (
            new_revision_id, document_id, content_hash, _default_parser.version,
            ParseCounts(**counts).total, counts["text_ok"],
            counts["needs_ocr"], counts["needs_review"], counts["failed"],
            parse_status,
        ),
    )
    conn.executemany(
        "INSERT INTO pages(id, revision_id, page_number, status, content) VALUES (?, ?, ?, ?, ?)",
        page_rows,
    )
    conn.executemany(
        """INSERT INTO chunks
           (id, revision_id, page_number, ordinal, content, search_text)
           VALUES (?, ?, ?, ?, ?, ?)""",
        chunk_rows,
    )
    _default_index_writer.write_chunks(conn, chunk_rows)
    conn.commit()
    return document_details(conn, document_id)
