from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import fitz
import httpx

SIDECAR_VERSION = "course-agent-ocr-sidecar-v1"
PAGE_START_RE = re.compile(
    r"<!-- OCR_PAGE_START (?P<attrs>.*?) -->\n?(?P<body>.*?)\n?<!-- OCR_PAGE_END page=(?P<end_page>\d+) -->",
    re.DOTALL,
)
LOCATION_TAG_RE = re.compile(
    r"<\|(?:ref|det)\|>.*?<\|/(?:ref|det)\|>",
    re.DOTALL,
)


class OcrError(Exception):
    pass


@dataclass(frozen=True)
class PdfDocumentRef:
    document_id: str
    title: str
    file_path: Path
    page_count: int


@dataclass(frozen=True)
class OcrPage:
    page_number: int
    status: str
    markdown: str


@dataclass(frozen=True)
class OcrSidecar:
    source_sha256: str
    page_count: int
    model: str
    mode: str
    dpi: int
    pages: dict[int, str]
    page_statuses: dict[int, str] = field(default_factory=dict)
    sidecar_path: Path | None = None


@dataclass(frozen=True)
class OcrDocumentResult:
    document_id: str
    file_path: Path
    page_count: int
    succeeded: bool
    sidecar_path: Path | None
    processed_pages: int
    cached_pages: int
    failed_pages: dict[int, str]


ProgressCallback = Callable[[dict[str, int | float | None]], None]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def bytes_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sidecar_path_for(pdf_path: Path) -> Path:
    return pdf_path.with_name(f"{pdf_path.name}.ocr.md")


def clean_ocr_markdown(text: str) -> str:
    cleaned = LOCATION_TAG_RE.sub("", text)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    return cleaned.strip()


def redact_error_message(message: str) -> str:
    redacted = re.sub(
        r"(?i)\bAuthorization\s*:\s*Bearer\s+[^\s,;]+",
        "Authorization: Bearer [REDACTED]",
        message,
    )
    redacted = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer [REDACTED]", redacted)
    redacted = re.sub(
        r"(?i)\b(api[_-]?key|token|password|secret)=([^&\s]+)",
        r"\1=[REDACTED]",
        redacted,
    )
    return redacted[:500]


def ocr_endpoint_from_base_url(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    if not base:
        raise OcrError("base URL is required")
    if base.endswith("/v1/ocr"):
        return base
    return f"{base}/v1/ocr"


def ocr_endpoints_from_base_urls(base_urls: str | Iterable[str]) -> list[str]:
    if isinstance(base_urls, str):
        raw_items = base_urls.split(",")
    else:
        raw_items = list(base_urls)
    endpoints: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        endpoint = ocr_endpoint_from_base_url(str(item))
        if endpoint not in seen:
            seen.add(endpoint)
            endpoints.append(endpoint)
    if not endpoints:
        raise OcrError("at least one OCR base URL is required")
    return endpoints


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    _atomic_write_text(
        path,
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _attrs_to_text(attrs: dict[str, object]) -> str:
    return " ".join(f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in attrs.items())


def _parse_attrs(text: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in re.finditer(r"([A-Za-z0-9_-]+)=((?:\".*?\")|(?:\S+))", text):
        value = match.group(2)
        if value.startswith('"'):
            try:
                attrs[match.group(1)] = str(json.loads(value))
            except json.JSONDecodeError:
                attrs[match.group(1)] = value.strip('"')
        else:
            attrs[match.group(1)] = value
    return attrs


def write_ocr_sidecar(
    pdf_path: Path,
    *,
    source_sha256: str,
    page_count: int,
    model: str,
    mode: str,
    dpi: int,
    pages: list[OcrPage],
) -> Path:
    if len(pages) != page_count:
        raise OcrError("cannot publish sidecar before every page succeeds")
    ordered = sorted(pages, key=lambda item: item.page_number)
    expected = list(range(1, page_count + 1))
    if [item.page_number for item in ordered] != expected:
        raise OcrError("sidecar pages are incomplete or not contiguous")

    sidecar_path = sidecar_path_for(pdf_path)
    metadata = {
        "version": SIDECAR_VERSION,
        "source_sha256": source_sha256,
        "page_count": page_count,
        "model": model,
        "mode": mode,
        "dpi": dpi,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    lines = [
        f"<!-- {SIDECAR_VERSION}",
        json.dumps(metadata, ensure_ascii=False, sort_keys=True),
        "-->",
        "",
        "# OCR Sidecar",
        "",
    ]
    for page in ordered:
        attrs = _attrs_to_text({"page": page.page_number, "status": page.status})
        lines.append(f"<!-- OCR_PAGE_START {attrs} -->")
        lines.append(page.markdown)
        lines.append(f"<!-- OCR_PAGE_END page={page.page_number} -->")
        lines.append("")
    _atomic_write_text(sidecar_path, "\n".join(lines).rstrip() + "\n")
    return sidecar_path


def _extract_sidecar_metadata(text: str) -> dict[str, Any] | None:
    prefix = f"<!-- {SIDECAR_VERSION}"
    if not text.startswith(prefix):
        return None
    end = text.find("-->")
    if end < 0:
        return None
    raw = text[len(prefix):end].strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _pdf_page_count(path: Path) -> int:
    with fitz.open(path) as document:
        return int(document.page_count)


def read_ocr_sidecar(pdf_path: Path) -> OcrSidecar | None:
    sidecar_path = sidecar_path_for(pdf_path)
    if not sidecar_path.is_file() or not pdf_path.is_file():
        return None
    text = sidecar_path.read_text(encoding="utf-8")
    metadata = _extract_sidecar_metadata(text)
    if not metadata:
        return None
    try:
        source_sha = str(metadata["source_sha256"])
        page_count = int(metadata["page_count"])
        model = str(metadata["model"])
        mode = str(metadata["mode"])
        dpi = int(metadata["dpi"])
    except (KeyError, TypeError, ValueError):
        return None
    if source_sha != file_sha256(pdf_path):
        return None
    if page_count != _pdf_page_count(pdf_path):
        return None

    pages: dict[int, str] = {}
    statuses: dict[int, str] = {}
    for match in PAGE_START_RE.finditer(text):
        attrs = _parse_attrs(match.group("attrs"))
        try:
            page_number = int(attrs["page"])
            end_page = int(match.group("end_page"))
        except (KeyError, TypeError, ValueError):
            return None
        if page_number != end_page:
            return None
        pages[page_number] = match.group("body").strip()
        statuses[page_number] = attrs.get("status", "success")
    if sorted(pages) != list(range(1, page_count + 1)):
        return None
    return OcrSidecar(
        source_sha256=source_sha,
        page_count=page_count,
        model=model,
        mode=mode,
        dpi=dpi,
        pages=pages,
        page_statuses=statuses,
        sidecar_path=sidecar_path,
    )


def render_pdf_page_png(pdf_path: Path, page_number: int, dpi: int) -> bytes:
    if page_number < 1:
        raise OcrError("page number must be positive")
    scale = dpi / 72.0
    with fitz.open(pdf_path) as document:
        if page_number > document.page_count:
            raise OcrError(f"page {page_number} is outside PDF page count")
        page = document.load_page(page_number - 1)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        return pixmap.tobytes("png")


def _cache_file_for(
    cache_dir: Path,
    *,
    source_sha256: str,
    page_number: int,
    render_sha256: str,
    mode: str,
    model: str,
    dpi: int,
) -> Path:
    key = hashlib.sha256(
        f"{source_sha256}:{page_number}:{render_sha256}:{mode}:{model}:{dpi}".encode("utf-8")
    ).hexdigest()
    return cache_dir / source_sha256[:16] / f"page-{page_number:04d}-{key[:16]}.json"


def _matching_success_cache(
    path: Path,
    *,
    source_sha256: str,
    page_number: int,
    render_sha256: str,
    mode: str,
    model: str,
    dpi: int,
) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    metadata = cached.get("metadata") if isinstance(cached, dict) else None
    if not isinstance(metadata, dict):
        return None
    expected = {
        "source_sha256": source_sha256,
        "page_number": page_number,
        "render_sha256": render_sha256,
        "mode": mode,
        "model": model,
        "dpi": dpi,
        "status": "success",
    }
    if all(metadata.get(key) == value for key, value in expected.items()):
        return cached
    return None


def extract_ocr_text(response_data: Any) -> str:
    if not isinstance(response_data, dict):
        return ""
    for key in ("markdown", "text", "content", "output_text"):
        value = response_data.get(key)
        if isinstance(value, str) and value.strip():
            return value
    data = response_data.get("data")
    if isinstance(data, dict):
        nested = extract_ocr_text(data)
        if nested:
            return nested
    for choice in response_data.get("choices", []) or []:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content
    parts: list[str] = []
    for output in response_data.get("output", []) or []:
        if not isinstance(output, dict):
            continue
        for content in output.get("content", []) or []:
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                parts.append(content["text"])
    return "\n".join(parts).strip()


class OcrHttpClient:
    def __init__(
        self,
        endpoint: str,
        *,
        mode: str = "markdown",
        timeout_seconds: float = 120.0,
        max_retries: int = 2,
    ):
        self.endpoint = endpoint
        self.mode = mode
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, max_retries)

    def recognize(self, image: bytes, *, filename: str) -> dict[str, Any]:
        last_error = "OCR request failed"
        for attempt in range(self.max_retries + 1):
            try:
                timeout = httpx.Timeout(self.timeout_seconds, connect=min(10.0, self.timeout_seconds))
                with httpx.Client(timeout=timeout) as client:
                    response = client.post(
                        self.endpoint,
                        data={"mode": self.mode},
                        files={"image": (filename, image, "image/png")},
                    )
                if response.status_code in {408, 425, 429, 500, 502, 503, 504} and attempt < self.max_retries:
                    time.sleep(min(2.0, 0.25 * (2 ** attempt)))
                    continue
                if response.status_code >= 400:
                    raise OcrError(
                        f"OCR HTTP {response.status_code}: {redact_error_message(_response_error_text(response))}"
                    )
                data = response.json()
                if not isinstance(data, dict):
                    raise OcrError("OCR response was not a JSON object")
                return data
            except (httpx.HTTPError, ValueError, OcrError) as exc:
                last_error = redact_error_message(str(exc))
                if attempt < self.max_retries:
                    time.sleep(min(2.0, 0.25 * (2 ** attempt)))
                    continue
                raise OcrError(last_error) from exc
        raise OcrError(last_error)


def _response_error_text(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text.strip()
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
        if isinstance(error, str):
            return error
    return response.text.strip()


def _ocr_pdf_page(
    *,
    pdf_path: Path,
    page_number: int,
    endpoint: str,
    cache_dir: Path,
    source_sha: str,
    page_count: int,
    dpi: int,
    mode: str,
    model: str,
    resume_cache: bool,
    timeout_seconds: float,
    max_retries: int,
) -> tuple[OcrPage, bool, bool]:
    image = render_pdf_page_png(pdf_path, page_number, dpi)
    render_sha = bytes_sha256(image)
    cache_path = _cache_file_for(
        cache_dir,
        source_sha256=source_sha,
        page_number=page_number,
        render_sha256=render_sha,
        mode=mode,
        model=model,
        dpi=dpi,
    )
    cached = (
        _matching_success_cache(
            cache_path,
            source_sha256=source_sha,
            page_number=page_number,
            render_sha256=render_sha,
            mode=mode,
            model=model,
            dpi=dpi,
        )
        if resume_cache
        else None
    )
    processed = False
    cache_hit = cached is not None
    if cached is None:
        client = OcrHttpClient(endpoint, mode=mode, timeout_seconds=timeout_seconds, max_retries=max_retries)
        response_data = client.recognize(image, filename=f"{pdf_path.stem}-page-{page_number}.png")
        wrapper = {
            "metadata": {
                "source_sha256": source_sha,
                "page_number": page_number,
                "page_count": page_count,
                "render_sha256": render_sha,
                "mode": mode,
                "model": model,
                "dpi": dpi,
                "endpoint": endpoint,
                "status": "success",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            "response": response_data,
        }
        _atomic_write_json(cache_path, wrapper)
        processed = True
    else:
        wrapper = cached
    text = clean_ocr_markdown(extract_ocr_text(wrapper.get("response")))
    return OcrPage(
        page_number=page_number,
        status="blank" if not text else "success",
        markdown=text,
    ), processed, cache_hit


def _progress_payload(
    *,
    completed: int,
    total: int,
    failed: int,
    started_at: float,
) -> dict[str, int | float | None]:
    elapsed = max(0.0, time.monotonic() - started_at)
    eta = None
    if completed > 0 and completed < total:
        eta = elapsed * (total - completed) / completed
    elif completed >= total:
        eta = 0.0
    return {
        "completed": completed,
        "total": total,
        "failed": failed,
        "elapsed": round(elapsed, 1),
        "eta": round(eta, 1) if eta is not None else None,
    }


def ocr_pdf_document(
    document: PdfDocumentRef,
    *,
    endpoint: str | None = None,
    endpoints: list[str] | None = None,
    cache_dir: Path,
    dpi: int = 200,
    mode: str = "markdown",
    model: str = "deepseek-ocr",
    resume_cache: bool = True,
    output_sidecar: bool = True,
    timeout_seconds: float = 120.0,
    max_retries: int = 2,
    progress_callback: ProgressCallback | None = None,
    progress_interval: float = 5.0,
) -> OcrDocumentResult:
    pdf_path = document.file_path
    if not pdf_path.is_file():
        return OcrDocumentResult(
            document.document_id, pdf_path, document.page_count, False, None, 0, 0,
            {0: "PDF file not found"},
        )
    source_sha = file_sha256(pdf_path)
    page_count = _pdf_page_count(pdf_path)
    endpoint_list = endpoints or ([endpoint] if endpoint else [])
    endpoint_list = ocr_endpoints_from_base_urls(endpoint_list)
    pages: list[OcrPage] = []
    failures: dict[int, str] = {}
    processed_pages = 0
    cached_pages = 0
    started_at = time.monotonic()
    completed = 0

    executors = [
        ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"ocr-endpoint-{index}")
        for index, _ in enumerate(endpoint_list, start=1)
    ]
    future_to_page: dict[Future[tuple[OcrPage, bool, bool]], int] = {}
    try:
        for page_number in range(1, page_count + 1):
            endpoint_index = (page_number - 1) % len(endpoint_list)
            future = executors[endpoint_index].submit(
                _ocr_pdf_page,
                pdf_path=pdf_path,
                page_number=page_number,
                endpoint=endpoint_list[endpoint_index],
                cache_dir=cache_dir,
                source_sha=source_sha,
                page_count=page_count,
                dpi=dpi,
                mode=mode,
                model=model,
                resume_cache=resume_cache,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
            )
            future_to_page[future] = page_number
        pending: set[Future[tuple[OcrPage, bool, bool]]] = set(future_to_page)
        while pending:
            done, pending = wait(
                pending,
                timeout=max(0.1, progress_interval),
                return_when=FIRST_COMPLETED,
            )
            if not done:
                if progress_callback:
                    progress_callback(
                        _progress_payload(
                            completed=completed,
                            total=page_count,
                            failed=len(failures),
                            started_at=started_at,
                        )
                    )
                continue
            for future in done:
                page_number = future_to_page[future]
                try:
                    page, processed, cache_hit = future.result()
                    pages.append(page)
                    processed_pages += 1 if processed else 0
                    cached_pages += 1 if cache_hit else 0
                except OcrError as exc:
                    failures[page_number] = str(exc)
                completed += 1
            if progress_callback:
                progress_callback(
                    _progress_payload(
                        completed=completed,
                        total=page_count,
                        failed=len(failures),
                        started_at=started_at,
                    )
                )
    finally:
        for executor in executors:
            executor.shutdown(wait=True)

    sidecar_path: Path | None = None
    succeeded = not failures and len(pages) == page_count
    if succeeded and output_sidecar:
        sidecar_path = write_ocr_sidecar(
            pdf_path,
            source_sha256=source_sha,
            page_count=page_count,
            model=model,
            mode=mode,
            dpi=dpi,
            pages=pages,
        )
    return OcrDocumentResult(
        document.document_id,
        pdf_path,
        page_count,
        succeeded,
        sidecar_path,
        processed_pages,
        cached_pages,
        failures,
    )


def active_pdf_documents(
    database_path: Path,
    *,
    document_ids: Iterable[str] | None = None,
    limit: int | None = None,
) -> list[PdfDocumentRef]:
    if not database_path.is_file():
        raise OcrError(f"database not found: {database_path}")
    requested = [item.strip() for item in (document_ids or []) if item.strip()]
    sql = [
        """SELECT d.id, d.title, d.file_path, r.page_count
           FROM documents d
           JOIN revisions r ON r.document_id = d.id AND r.status = 'active'
           WHERE d.status = 'active'""",
    ]
    params: list[object] = []
    if requested:
        marks = ",".join("?" for _ in requested)
        sql.append(f"AND d.id IN ({marks})")
        params.extend(requested)
    sql.append("ORDER BY d.created_at, d.id")
    if limit is not None:
        sql.append("LIMIT ?")
        params.append(limit)
    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("\n".join(sql), params).fetchall()
    finally:
        conn.close()
    documents = [
        PdfDocumentRef(
            document_id=str(row["id"]),
            title=str(row["title"]),
            file_path=Path(str(row["file_path"])),
            page_count=int(row["page_count"]),
        )
        for row in rows
        if Path(str(row["file_path"])).suffix.lower() == ".pdf"
    ]
    if requested:
        found = {item.document_id for item in documents}
        missing = [item for item in requested if item not in found]
        if missing:
            raise OcrError(f"active PDF document not found: {', '.join(missing)}")
    return documents


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backfill OCR markdown sidecars for active PDF documents.")
    parser.add_argument("--database", type=Path, required=True, help="Path to course-agent SQLite database.")
    parser.add_argument("--base-url", default="http://127.0.0.1:18082", help="OCR service root or /v1/ocr URL.")
    parser.add_argument(
        "--base-urls",
        default=None,
        help="Comma-separated OCR service roots or /v1/ocr URLs. Uses one serial worker per endpoint.",
    )
    parser.add_argument("--dpi", type=int, default=200, help="PDF render DPI.")
    parser.add_argument("--document-id", action="append", default=[], help="Active document id to process. Repeatable.")
    parser.add_argument("--all", action="store_true", help="Process every active PDF document.")
    parser.add_argument("--resume-cache", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--output-sidecars", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of documents to process.")
    parser.add_argument("--mode", default="markdown", help="OCR service mode form field.")
    parser.add_argument("--model", default="deepseek-ocr", help="Model label written to cache and sidecar metadata.")
    parser.add_argument("--timeout", type=float, default=120.0, help="Per-request timeout in seconds.")
    parser.add_argument("--max-retries", type=int, default=2, help="Retries per page after the first attempt.")
    parser.add_argument("--progress-interval", type=float, default=5.0, help="Progress print interval in seconds.")
    return parser


def run_backfill(args: argparse.Namespace) -> int:
    if not args.all and not args.document_id:
        raise OcrError("choose --all or at least one --document-id")
    if args.all and args.document_id:
        raise OcrError("choose either --all or --document-id, not both")
    if args.dpi <= 0:
        raise OcrError("--dpi must be positive")
    if args.limit is not None and args.limit <= 0:
        raise OcrError("--limit must be positive")
    endpoints = ocr_endpoints_from_base_urls(args.base_urls or args.base_url)
    database_path = args.database.resolve()
    documents = active_pdf_documents(
        database_path,
        document_ids=None if args.all else args.document_id,
        limit=args.limit,
    )
    cache_dir = database_path.parent / "ocr-cache"
    failures = 0
    total_pages = sum(document.page_count for document in documents)
    completed_before = 0
    failed_before = 0
    run_started_at = time.monotonic()

    def print_progress(payload: dict[str, int | float | None]) -> None:
        completed = completed_before + int(payload["completed"] or 0)
        failed = failed_before + int(payload["failed"] or 0)
        elapsed = max(0.0, time.monotonic() - run_started_at)
        eta = None
        if completed > 0 and completed < total_pages:
            eta = elapsed * (total_pages - completed) / completed
        elif completed >= total_pages:
            eta = 0.0
        print(
            "PROGRESS "
            f"completed={completed}/{total_pages} "
            f"failed={failed} "
            f"elapsed={elapsed:.1f}s "
            f"ETA={(f'{eta:.1f}s' if eta is not None else 'unknown')}"
        )

    for document in documents:
        result = ocr_pdf_document(
            document,
            endpoints=endpoints,
            cache_dir=cache_dir,
            dpi=args.dpi,
            mode=args.mode,
            model=args.model,
            resume_cache=args.resume_cache,
            output_sidecar=args.output_sidecars,
            timeout_seconds=args.timeout,
            max_retries=args.max_retries,
            progress_callback=print_progress,
            progress_interval=args.progress_interval,
        )
        completed_before += result.page_count
        failed_before += len(result.failed_pages)
        if result.succeeded:
            location = str(result.sidecar_path) if result.sidecar_path else "(sidecar disabled)"
            print(
                f"OK {result.document_id} pages={result.page_count} "
                f"processed={result.processed_pages} cached={result.cached_pages} sidecar={location}"
            )
        else:
            failures += 1
            print(
                f"FAIL {result.document_id} pages={result.page_count} failures={result.failed_pages}",
            )
    return 1 if failures else 0
