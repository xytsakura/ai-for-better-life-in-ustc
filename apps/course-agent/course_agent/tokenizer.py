from __future__ import annotations

import re
import unicodedata

import jieba


CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
IDENTIFIER_RE = re.compile(r"[a-z0-9]+(?:[._+-][a-z0-9]+)*|[α-ωΑ-ΩεδεΔλΛμΜσΣπΠ]+")


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = CONTROL_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokenize_for_search(text: str) -> str:
    normalized = normalize_text(text)
    if not normalized:
        return ""
    tokens: list[str] = []
    tokens.extend(token.strip().lower() for token in jieba.cut_for_search(normalized) if token.strip())
    for run in CJK_RUN_RE.findall(normalized):
        tokens.extend(run[index : index + 2] for index in range(max(0, len(run) - 1)))
    tokens.extend(match.group(0).lower() for match in IDENTIFIER_RE.finditer(normalized))
    return " ".join(tokens)


def fts_query(text: str) -> str:
    tokenized = tokenize_for_search(text)
    tokens = [token.replace('"', '""') for token in tokenized.split() if token]
    unique = list(dict.fromkeys(tokens))[:32]
    return " OR ".join(f'"{token}"' for token in unique)

