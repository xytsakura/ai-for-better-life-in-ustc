"""Full-text search and access control for course documents.

Public API (stable):
    SearchResult      -- unified search result dataclass.
    FTS5SearchBackend -- default search backend, satisfies SearchBackendProto.
    accessible_document_ids -- module-level utility, kept for backward compatibility.
    search            -- module-level wrapper, kept for backward compatibility.

To swap in a vector or hybrid search backend, provide a class satisfying
SearchBackendProto from .types and pass it to create_app().
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .tokenizer import fts_query
from .types import SearchBackendProto


@dataclass(frozen=True)
class SearchResult:
    """A single ranked search result from any search backend."""

    citation_id: str
    chunk_id: str
    document_id: str
    document_title: str
    page: int
    content: str
    score: float
    space_id: str

    def as_dict(self) -> dict:
        return {
            "id": self.citation_id,
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "document_title": self.document_title,
            "page": self.page,
            "excerpt": self.content[:800],
            "score": round(self.score, 4),
            "space_id": self.space_id,
        }


# ---- Access control (orthogonal to search backend) --------------------

def accessible_document_ids(
    conn: sqlite3.Connection,
    user_id: str,
    document_ids: list[str],
) -> set[str]:
    """Return the subset of document_ids the user has active membership access to."""
    if not document_ids:
        return set()
    placeholders = ",".join("?" for _ in document_ids)
    rows = conn.execute(
        f"""SELECT d.id FROM documents d
            JOIN memberships m ON m.space_id = d.space_id
              AND m.user_id = ? AND m.status = 'active'
            JOIN revisions r ON r.document_id = d.id AND r.status = 'active'
            WHERE d.status = 'active' AND d.id IN ({placeholders})""",
        [user_id, *document_ids],
    ).fetchall()
    return {str(row["id"]) for row in rows}


# ---- FTS5 implementation ----------------------------------------------

class FTS5SearchBackend:
    """SQLite FTS5 full-text search with BM25 ranking and page deduplication.

    Satisfies SearchBackendProto.
    """

    def search(
        self,
        conn: sqlite3.Connection,
        user_id: str,
        question: str,
        document_ids: list[str],
        top_k: int = 5,
    ) -> list[SearchResult]:
        query = fts_query(question)
        if not query or not document_ids:
            return []
        allowed = accessible_document_ids(conn, user_id, document_ids)
        if not set(document_ids).issubset(allowed):
            return []
        placeholders = ",".join("?" for _ in document_ids)
        rows = conn.execute(
            f"""SELECT c.id AS chunk_id, d.id AS document_id, d.title AS document_title,
                       c.page_number, c.content, d.space_id, bm25(chunk_fts) AS rank
                FROM chunk_fts
                JOIN chunks c ON c.id = chunk_fts.chunk_id
                JOIN revisions r ON r.id = c.revision_id AND r.status = 'active'
                JOIN documents d ON d.id = r.document_id AND d.status = 'active'
                JOIN memberships m ON m.space_id = d.space_id
                  AND m.user_id = ? AND m.status = 'active'
                WHERE chunk_fts MATCH ? AND d.id IN ({placeholders})
                ORDER BY rank ASC
                LIMIT ?""",
            [user_id, query, *document_ids, max(1, min(top_k * 4, 32))],
        ).fetchall()
        results: list[SearchResult] = []
        seen: set[tuple[str, int]] = set()
        for row in rows:
            key = (str(row["document_id"]), int(row["page_number"]))
            if key in seen:
                continue
            seen.add(key)
            results.append(
                SearchResult(
                    citation_id=f"S{len(results) + 1}",
                    chunk_id=str(row["chunk_id"]),
                    document_id=str(row["document_id"]),
                    document_title=str(row["document_title"]),
                    page=int(row["page_number"]),
                    content=str(row["content"]),
                    score=float(row["rank"]),
                    space_id=str(row["space_id"]),
                )
            )
            if len(results) >= top_k:
                break
        return results


# ---- Module-level default instance ------------------------------------

_default_backend = FTS5SearchBackend()


def search(
    conn: sqlite3.Connection,
    user_id: str,
    question: str,
    document_ids: list[str],
    top_k: int = 5,
) -> list[SearchResult]:
    """Backward-compatible wrapper; delegates to the default FTS5 backend."""
    return _default_backend.search(conn, user_id, question, document_ids, top_k)
