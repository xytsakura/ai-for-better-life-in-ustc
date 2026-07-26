"""Extension point protocols for the course agent platform.

Each protocol defines a pluggable component. Current implementations ship as default
backends; future upgrades (vector search, GraphRAG, OCR, alternative parsers, etc.)
can satisfy the same protocol and be swapped in via configuration without touching
business logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


# ---- Shared data types ------------------------------------------------

@dataclass
class ParseCounts:
    """Parsing quality statistics for a single document revision."""

    text_ok: int = 0
    needs_ocr: int = 0
    needs_review: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "text_ok": self.text_ok,
            "needs_ocr": self.needs_ocr,
            "needs_review": self.needs_review,
            "failed": self.failed,
        }

    @property
    def total(self) -> int:
        return self.text_ok + self.needs_ocr + self.needs_review + self.failed


@dataclass(frozen=True)
class PageRecord:
    """A single page extracted from a document, decoupled from storage format."""

    page_id: str
    revision_id: str
    page_number: int
    status: str  # text_ok | needs_ocr | needs_review | failed
    content: str


@dataclass(frozen=True)
class ChunkRecord:
    """A text chunk ready for indexing, decoupled from storage format."""

    chunk_id: str
    revision_id: str
    page_number: int
    ordinal: int
    content: str
    search_text: str


@dataclass
class ParseOutput:
    """Output from a DocumentParser, ready to be written by an IndexWriter."""

    pages: list[PageRecord] = field(default_factory=list)
    chunks: list[ChunkRecord] = field(default_factory=list)
    counts: ParseCounts = field(default_factory=ParseCounts)

    def as_db_rows(self) -> tuple[list[tuple], list[tuple], dict[str, int]]:
        """Legacy tuple format for backward compatibility with existing DB code."""
        page_rows = [
            (p.page_id, p.revision_id, p.page_number, p.status, p.content)
            for p in self.pages
        ]
        chunk_rows = [
            (c.chunk_id, c.revision_id, c.page_number, c.ordinal, c.content, c.search_text)
            for c in self.chunks
        ]
        return page_rows, chunk_rows, self.counts.as_dict()


# ---- Search protocols ------------------------------------------------

# Re-exported at module level; actual definition lives in retrieval.py to
# avoid circular imports. This Protocol documents the expected interface.

class SearchBackendProto(Protocol):
    """Search backend capable of full-text, vector, or hybrid retrieval.

    Each backend is responsible for knowing how to query its own index.
    """

    def search(
        self,
        conn: Any,
        user_id: str,
        question: str,
        document_ids: list[str],
        top_k: int = 5,
    ) -> list[Any]:  # returns list[SearchResult]
        ...


# ---- Parser protocols -------------------------------------------------

@runtime_checkable
class DocumentParserProto(Protocol):
    """Pluggable document parser.

    Future implementations could include:
    - MinerU / Markitdown (for complex PDF layouts)
    - PaddleOCR-backed parser (for scanned pages)
    - Plain text / Markdown / HTML parsers
    """

    @property
    def version(self) -> str:
        """Semantic version or label of this parser implementation."""
        ...

    def validate(self, path: Path, max_bytes: int) -> None:
        """Raise IngestionError if the file is not supported or malformed."""
        ...

    def extract(self, path: Path, revision_id: str) -> ParseOutput:
        """Parse a document into pages and chunks ready for indexing."""
        ...


# ---- Chunking protocols -----------------------------------------------

@runtime_checkable
class ChunkingStrategyProto(Protocol):
    """Pluggable text chunking.

    Future implementations could include:
    - Recursive character splitting (langchain-style)
    - Semantic chunking (split on embedding distance)
    - Markdown-aware splitting (respect headers)
    """

    def chunk(self, text: str, max_chars: int = 1800, overlap: int = 180) -> list[str]:
        """Split text into overlapping chunks with sentence-boundary awareness."""
        ...


# ---- Index writer protocols -------------------------------------------

@runtime_checkable
class IndexWriterProto(Protocol):
    """Pluggable index writer for storing parsed chunks.

    Future implementations could include:
    - Vector DB writer (Qdrant, pgvector, Milvus)
    - Elasticsearch writer
    - GraphRAG writer (entity extraction + graph DB)
    - Multi-writer (fan-out to FTS5 + vector + graph)
    """

    def write_chunks(self, conn: Any, chunk_rows: list[tuple]) -> None:
        """Persist chunk rows into the search index."""
        ...

    def delete_chunks(self, conn: Any, chunk_ids: list[str]) -> None:
        """Remove chunk entries from the search index."""
        ...


# ---- Tokenizer protocol -----------------------------------------------

@runtime_checkable
class TokenizerProto(Protocol):
    """Pluggable tokenizer for search query construction.

    Future implementations could include:
    - BERT / LLM-based tokenizer
    - English-optimized tokenizer
    - Domain-specific tokenizer (legal, medical)
    """

    def normalize(self, text: str) -> str:
        """Normalize text (NFKC, strip control chars, collapse whitespace)."""
        ...

    def tokenize(self, text: str) -> str:
        """Tokenize text into space-separated tokens for index/search."""
        ...

    def build_query(self, text: str) -> str:
        """Build a backend-specific query string from user input."""
        ...


# ---- Composite protocol (what main.py wires) --------------------------

@runtime_checkable
class CourseAgentComponents(Protocol):
    """All pluggable components that make up a course agent instance.

    This is what create_app() expects on app.state.components.
    """

    search_backend: SearchBackendProto
    document_parser: DocumentParserProto
    chunking_strategy: ChunkingStrategyProto
    index_writer: IndexWriterProto
    tokenizer: TokenizerProto
