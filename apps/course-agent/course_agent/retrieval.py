from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .tokenizer import fts_query


@dataclass(frozen=True)
class SearchResult:
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


def accessible_space_ids(conn: sqlite3.Connection, user_id: str) -> set[str]:
    rows = conn.execute(
        """SELECT s.id FROM spaces s
           JOIN memberships m ON m.space_id = s.id
           WHERE m.user_id = ? AND m.status = 'active'""",
        (user_id,),
    )
    return {str(row["id"]) for row in rows}


def search(
    conn: sqlite3.Connection,
    user_id: str,
    question: str,
    requested_space_ids: list[str] | None = None,
    top_k: int = 5,
) -> list[SearchResult]:
    query = fts_query(question)
    if not query:
        return []
    allowed = accessible_space_ids(conn, user_id)
    if requested_space_ids is not None:
        if not set(requested_space_ids).issubset(allowed):
            return []
        allowed = set(requested_space_ids)
    if not allowed:
        return []
    placeholders = ",".join("?" for _ in allowed)
    params: list[object] = [query, user_id, *sorted(allowed), max(1, min(top_k * 4, 32))]
    rows = conn.execute(
        f"""SELECT c.id AS chunk_id, d.id AS document_id, d.title AS document_title,
                   c.page_number, c.content, d.space_id, bm25(chunk_fts) AS rank
            FROM chunk_fts
            JOIN chunks c ON c.id = chunk_fts.chunk_id
            JOIN revisions r ON r.id = c.revision_id AND r.status = 'active'
            JOIN documents d ON d.id = r.document_id AND d.status = 'active'
            JOIN memberships m ON m.space_id = d.space_id
              AND m.user_id = ? AND m.status = 'active'
            WHERE chunk_fts MATCH ? AND d.space_id IN ({placeholders})
            ORDER BY rank ASC
            LIMIT ?""",
        [user_id, query, *sorted(allowed), max(1, min(top_k * 4, 32))],
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

