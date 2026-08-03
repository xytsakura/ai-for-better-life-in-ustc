from __future__ import annotations

from pathlib import Path

import fitz

from course_agent.config import Settings
from course_agent.db import database, init_database
from course_agent.ingestion import DocumentMetadata, ingest_pdf
from course_agent.ingestion import PyMuPDFParser
from course_agent.ocr import (
    OcrPage,
    PdfDocumentRef,
    active_pdf_documents,
    clean_ocr_markdown,
    file_sha256 as ocr_file_sha256,
    ocr_pdf_document,
    read_ocr_sidecar,
    sidecar_path_for,
    write_ocr_sidecar,
)
from scripts.ocr_backfill import main as ocr_backfill_main


def make_pdf(path: Path, texts: list[str]) -> None:
    document = fitz.open()
    for text in texts:
        page = document.new_page()
        if text:
            page.insert_text((72, 72), text)
    document.save(path)
    document.close()


class _OcrResponse:
    status_code = 200

    @staticmethod
    def json() -> dict:
        return {
            "markdown": "Alpha <|ref|>box<|/ref|><|det|>1,2,3,4<|/det|>\n\\[x^2\\]",
        }


class _BlankOcrResponse:
    status_code = 200

    @staticmethod
    def json() -> dict:
        return {"markdown": ""}


class _FailingOcrResponse:
    status_code = 503
    text = "temporary"

    @staticmethod
    def json() -> dict:
        return {"error": {"message": "temporary"}}


class _OcrClient:
    calls = 0

    def __init__(self, **_: object):
        pass

    def __enter__(self) -> "_OcrClient":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def post(self, *args: object, **kwargs: object) -> _OcrResponse:
        type(self).calls += 1
        assert str(args[0]) == "http://127.0.0.1:18082/v1/ocr"
        assert kwargs["data"] == {"mode": "markdown"}
        image_file = kwargs["files"]["image"]
        assert image_file[0].endswith(".png")
        assert image_file[2] == "image/png"
        return _OcrResponse()


class _MultiEndpointOcrClient:
    urls: list[str] = []

    def __init__(self, **_: object):
        pass

    def __enter__(self) -> "_MultiEndpointOcrClient":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def post(self, *args: object, **kwargs: object) -> _OcrResponse:
        type(self).urls.append(str(args[0]))
        return _OcrResponse()


class _BlankOcrClient(_OcrClient):
    def post(self, *args: object, **kwargs: object) -> _BlankOcrResponse:
        type(self).calls += 1
        return _BlankOcrResponse()


class _FailingOcrClient(_OcrClient):
    def post(self, *args: object, **kwargs: object) -> _FailingOcrResponse:
        type(self).calls += 1
        return _FailingOcrResponse()


def test_clean_ocr_markdown_removes_deepseek_location_tags() -> None:
    assert clean_ocr_markdown("A <|ref|>ignore<|/ref|><|det|>1<|/det|> \\(x\\)") == "A  \\(x\\)"


def test_ocr_pdf_document_writes_valid_sidecar_and_reuses_success_cache(monkeypatch, tmp_path: Path) -> None:
    pdf_path = tmp_path / "source.pdf"
    make_pdf(pdf_path, ["first page"])
    document = PdfDocumentRef("doc-1", "Source", pdf_path, 1)
    cache_dir = tmp_path / "ocr-cache"
    _OcrClient.calls = 0
    monkeypatch.setattr("course_agent.ocr.httpx.Client", _OcrClient)

    first = ocr_pdf_document(
        document,
        endpoint="http://127.0.0.1:18082/v1/ocr",
        cache_dir=cache_dir,
        dpi=72,
    )
    second = ocr_pdf_document(
        document,
        endpoint="http://127.0.0.1:18082/v1/ocr",
        cache_dir=cache_dir,
        dpi=72,
    )

    assert first.succeeded is True
    assert first.processed_pages == 1
    assert second.cached_pages == 1
    assert _OcrClient.calls == 1
    sidecar = read_ocr_sidecar(pdf_path)
    assert sidecar is not None
    assert sidecar.page_count == 1
    assert sidecar.model == "deepseek-ocr"
    assert sidecar.mode == "markdown"
    assert sidecar.dpi == 72
    assert sidecar.pages == {1: "Alpha\n\\[x^2\\]"}


def test_read_ocr_sidecar_rejects_changed_source_pdf(monkeypatch, tmp_path: Path) -> None:
    pdf_path = tmp_path / "source.pdf"
    make_pdf(pdf_path, ["first page"])
    document = PdfDocumentRef("doc-1", "Source", pdf_path, 1)
    monkeypatch.setattr("course_agent.ocr.httpx.Client", _OcrClient)

    result = ocr_pdf_document(
        document,
        endpoint="http://127.0.0.1:18082/v1/ocr",
        cache_dir=tmp_path / "ocr-cache",
        dpi=72,
    )
    assert result.succeeded is True

    sidecar_text = sidecar_path_for(pdf_path).read_text(encoding="utf-8")
    make_pdf(pdf_path, ["changed page"])
    sidecar_path_for(pdf_path).write_text(sidecar_text, encoding="utf-8")

    assert read_ocr_sidecar(pdf_path) is None


def test_blank_page_succeeds_with_blank_status(monkeypatch, tmp_path: Path) -> None:
    pdf_path = tmp_path / "blank.pdf"
    make_pdf(pdf_path, [""])
    document = PdfDocumentRef("doc-blank", "Blank", pdf_path, 1)
    monkeypatch.setattr("course_agent.ocr.httpx.Client", _BlankOcrClient)

    result = ocr_pdf_document(
        document,
        endpoint="http://127.0.0.1:18082/v1/ocr",
        cache_dir=tmp_path / "ocr-cache",
        dpi=72,
    )

    assert result.succeeded is True
    sidecar = read_ocr_sidecar(pdf_path)
    assert sidecar is not None
    assert sidecar.pages == {1: ""}
    assert sidecar.page_statuses == {1: "blank"}


def test_failed_page_does_not_publish_sidecar(monkeypatch, tmp_path: Path) -> None:
    pdf_path = tmp_path / "source.pdf"
    make_pdf(pdf_path, ["first page"])
    document = PdfDocumentRef("doc-1", "Source", pdf_path, 1)
    monkeypatch.setattr("course_agent.ocr.httpx.Client", _FailingOcrClient)
    monkeypatch.setattr("course_agent.ocr.time.sleep", lambda _: None)

    result = ocr_pdf_document(
        document,
        endpoint="http://127.0.0.1:18082/v1/ocr",
        cache_dir=tmp_path / "ocr-cache",
        dpi=72,
        max_retries=1,
    )

    assert result.succeeded is False
    assert 1 in result.failed_pages
    assert not sidecar_path_for(pdf_path).exists()


def test_multiple_endpoints_are_used_as_serial_workers(monkeypatch, tmp_path: Path) -> None:
    pdf_path = tmp_path / "multi.pdf"
    make_pdf(pdf_path, ["page one", "page two"])
    document = PdfDocumentRef("doc-1", "Multi", pdf_path, 2)
    _MultiEndpointOcrClient.urls = []
    progress: list[dict[str, int | float | None]] = []
    monkeypatch.setattr("course_agent.ocr.httpx.Client", _MultiEndpointOcrClient)

    result = ocr_pdf_document(
        document,
        endpoints=[
            "http://127.0.0.1:18082/v1/ocr",
            "http://127.0.0.1:18083/v1/ocr",
        ],
        cache_dir=tmp_path / "ocr-cache",
        dpi=72,
        progress_callback=progress.append,
        progress_interval=0.1,
    )

    assert result.succeeded is True
    assert sorted(_MultiEndpointOcrClient.urls) == [
        "http://127.0.0.1:18082/v1/ocr",
        "http://127.0.0.1:18083/v1/ocr",
    ]
    assert progress[-1]["completed"] == 2
    assert progress[-1]["total"] == 2
    assert progress[-1]["failed"] == 0


def test_active_pdf_documents_and_cli_backfill(monkeypatch, tmp_path: Path) -> None:
    settings = Settings(runtime_dir=tmp_path, session_secret="test-secret")
    init_database(settings)
    source = tmp_path / "db-source.pdf"
    make_pdf(source, ["database page"])
    with database(settings) as conn:
        result = ingest_pdf(
            conn,
            settings,
            source,
            "math-b1-shared",
            DocumentMetadata("DB Source", "notes", "private"),
        )
    document_id = result["id"]
    documents = active_pdf_documents(settings.database_path, document_ids=[document_id])
    assert documents[0].document_id == document_id
    assert documents[0].file_path == source.resolve()
    monkeypatch.setattr("course_agent.ocr.httpx.Client", _OcrClient)

    exit_code = ocr_backfill_main(
        [
            "--database",
            str(settings.database_path),
            "--base-url",
            "http://127.0.0.1:18082",
            "--document-id",
            document_id,
            "--dpi",
            "72",
            "--limit",
            "1",
        ]
    )

    assert exit_code == 0
    assert read_ocr_sidecar(source.resolve()) is not None


def test_parser_uses_valid_ocr_markdown_and_preserves_pdf_page_numbers(tmp_path: Path) -> None:
    pdf_path = tmp_path / "broken-native.pdf"
    make_pdf(pdf_path, ["native mojibake", "second native page"])
    document = PdfDocumentRef("doc-1", "Source", pdf_path, 2)
    write_ocr_sidecar(
        pdf_path,
        source_sha256=ocr_file_sha256(pdf_path),
        page_count=2,
        model="deepseek-ocr-2",
        mode="markdown",
        dpi=200,
        pages=[
            OcrPage(1, "success", "# 第一页\n\n极限与连续性"),
            OcrPage(2, "success", "# 第二页\n\n\\[f'(x)=2x\\]"),
        ],
    )

    output = PyMuPDFParser.extract(document.file_path, "revision-1")

    assert output.counts.text_ok == 2
    assert [page.page_number for page in output.pages] == [1, 2]
    assert output.pages[0].content == "# 第一页\n\n极限与连续性"
    assert "native mojibake" not in output.pages[0].content
    assert {chunk.page_number for chunk in output.chunks} == {1, 2}


def test_parser_rejects_stale_sidecar_and_falls_back_to_native_text(tmp_path: Path) -> None:
    pdf_path = tmp_path / "stale.pdf"
    make_pdf(pdf_path, ["original native text long enough for indexing"])
    write_ocr_sidecar(
        pdf_path,
        source_sha256=ocr_file_sha256(pdf_path),
        page_count=1,
        model="deepseek-ocr-2",
        mode="markdown",
        dpi=200,
        pages=[OcrPage(1, "success", "stale OCR text")],
    )
    sidecar_text = sidecar_path_for(pdf_path).read_text(encoding="utf-8")
    make_pdf(pdf_path, ["changed native text long enough for indexing"])
    sidecar_path_for(pdf_path).write_text(sidecar_text, encoding="utf-8")

    output = PyMuPDFParser.extract(pdf_path, "revision-1")

    assert output.pages[0].content.startswith("changed native text")
    assert "stale OCR text" not in output.pages[0].content


def test_parser_rejects_incomplete_sidecar_and_keeps_blank_ocr_page_unsearchable(tmp_path: Path) -> None:
    incomplete_pdf = tmp_path / "incomplete.pdf"
    make_pdf(incomplete_pdf, ["native first page long enough", "native second page long enough"])
    valid_sidecar_pdf = tmp_path / "blank.pdf"
    make_pdf(valid_sidecar_pdf, ["native page should be overridden"])
    write_ocr_sidecar(
        valid_sidecar_pdf,
        source_sha256=ocr_file_sha256(valid_sidecar_pdf),
        page_count=1,
        model="deepseek-ocr-2",
        mode="markdown",
        dpi=200,
        pages=[OcrPage(1, "blank", "")],
    )
    # A syntactically present but incomplete sidecar must never be partially indexed.
    sidecar_path_for(incomplete_pdf).write_text(
        "<!-- course-agent-ocr-sidecar-v1\n"
        f"{{\"source_sha256\": \"{ocr_file_sha256(incomplete_pdf)}\", \"page_count\": 2, \"model\": \"x\", \"mode\": \"markdown\", \"dpi\": 200}}\n"
        "-->\n<!-- OCR_PAGE_START page=1 status=success -->\npartial\n<!-- OCR_PAGE_END page=1 -->\n",
        encoding="utf-8",
    )

    incomplete = PyMuPDFParser.extract(incomplete_pdf, "revision-1")
    blank = PyMuPDFParser.extract(valid_sidecar_pdf, "revision-2")

    assert incomplete.pages[0].content.startswith("native first page")
    assert blank.counts.needs_ocr == 1
    assert blank.pages[0].content == ""
    assert blank.chunks == []
