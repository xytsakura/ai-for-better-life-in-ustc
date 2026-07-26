"""Chinese text tokenizer for course material search.

Public API (stable):
    JiebaTokenizer   -- class-based implementation satisfying TokenizerProto.
    normalize_text   -- module-level wrapper, kept for backward compatibility.
    tokenize_for_search -- module-level wrapper, kept for backward compatibility.
    fts_query        -- module-level wrapper, kept for backward compatibility.

To swap in a different tokenizer (e.g. for English-only courses), provide a
class that satisfies the TokenizerProto from .types and pass it to create_app().
"""

from __future__ import annotations

import re
import unicodedata

import jieba

from .types import TokenizerProto

CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
IDENTIFIER_RE = re.compile(r"[a-z0-9]+(?:[._+-][a-z0-9]+)*|[α-ωΑ-ΩεδεΔλΛμΜσΣπΠ]+")


# ---- Tokenizer implementation -----------------------------------------

class JiebaTokenizer:
    """Default tokenizer using jieba for Chinese + bigram fallback + identifier extraction.

    Satisfies TokenizerProto.
    """

    @staticmethod
    def normalize(text: str) -> str:
        return _normalize(text)

    @staticmethod
    def tokenize(text: str) -> str:
        return _tokenize(text)

    @staticmethod
    def build_query(text: str) -> str:
        return _build_query(text)


# ---- Internal helpers -------------------------------------------------

def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = CONTROL_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokenize(text: str) -> str:
    """Tokenize into space-separated tokens for indexing and search."""
    normalized = _normalize(text)
    if not normalized:
        return ""
    tokens: list[str] = []
    tokens.extend(
        token.strip().lower()
        for token in jieba.cut_for_search(normalized)
        if token.strip()
    )
    for run in CJK_RUN_RE.findall(normalized):
        tokens.extend(run[index : index + 2] for index in range(max(0, len(run) - 1)))
    tokens.extend(
        match.group(0).lower() for match in IDENTIFIER_RE.finditer(normalized)
    )
    return " ".join(tokens)


def _build_query(text: str) -> str:
    """Build an FTS5-compatible query string from user input."""
    tokenized = _tokenize(text)
    tokens = [token.replace('"', '""') for token in tokenized.split() if token]
    unique = list(dict.fromkeys(tokens))[:32]
    return " OR ".join(f'"{token}"' for token in unique)


# ---- Backward-compatible module-level functions -----------------------

def normalize_text(text: str) -> str:
    return _normalize(text)


def tokenize_for_search(text: str) -> str:
    return _tokenize(text)


def fts_query(text: str) -> str:
    return _build_query(text)
