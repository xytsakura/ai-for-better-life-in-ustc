from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

from .config import Settings
from .retrieval import SearchResult


@dataclass(frozen=True)
class LLMResult:
    answer: str
    citation_ids: list[str]
    degraded: bool
    model: str
    error_code: str | None = None


class LLMAdapter:
    def __init__(self, settings: Settings):
        self.settings = settings

    def generate(self, question: str, sources: list[SearchResult]) -> LLMResult:
        if not sources:
            return LLMResult(
                answer="当前可访问的数学分析资料中没有找到足够依据。",
                citation_ids=[],
                degraded=False,
                model=self.settings.llm_model,
            )
        if not self.settings.llm_configured:
            return self._degraded(sources, "llm_not_configured")
        source_text = "\n\n".join(
            f'<source id="{source.citation_id}" document="{source.document_title}" page="{source.page}">\n'
            f"{source.content[:1800]}\n</source>"
            for source in sources[:8]
        )
        instructions = (
            "你是中国科学技术大学数学分析 B1 复习助手。只能根据用户问题和给定资料回答，"
            "不要补写资料中没有的结论。用简洁中文 Markdown 回答，并在事实后使用 [S1] 形式引用。"
            "如果资料不足，明确说明当前资料中没有找到依据。"
        )
        payload = {
            "model": self.settings.llm_model,
            "instructions": instructions,
            "input": f"用户问题：\n{question}\n\n可用资料：\n{source_text}",
            "max_output_tokens": 1200,
        }
        url = self.settings.llm_base_url.rstrip("/") + "/responses"
        last_code = "llm_request_failed"
        for attempt in range(2):
            try:
                with httpx.Client(timeout=self.settings.llm_timeout_seconds) as client:
                    response = client.post(
                        url,
                        headers={
                            "Authorization": f"Bearer {self.settings.llm_api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
                if response.status_code in {429, 502, 503, 504} and attempt == 0:
                    continue
                if response.status_code >= 400:
                    last_code = f"llm_http_{response.status_code}"
                    break
                data = response.json()
                text = self._extract_text(data).strip()
                if not text:
                    last_code = "llm_empty_response"
                    break
                valid = {source.citation_id for source in sources}
                citation_ids: list[str] = []

                def keep(match: re.Match[str]) -> str:
                    citation = f"S{match.group(1)}"
                    if citation in valid:
                        citation_ids.append(citation)
                        return f"[{citation}]"
                    return ""

                answer = re.sub(r"\[S(\d+)\]", keep, text)
                if not citation_ids:
                    return self._degraded(sources, "llm_missing_citations")
                return LLMResult(
                    answer=answer,
                    citation_ids=list(dict.fromkeys(citation_ids)),
                    degraded=False,
                    model=self.settings.llm_model,
                )
            except (httpx.HTTPError, ValueError):
                last_code = "llm_network_or_parse_error"
                if attempt == 0:
                    continue
        return self._degraded(sources, last_code)

    @staticmethod
    def _extract_text(data: dict) -> str:
        if isinstance(data.get("output_text"), str):
            return data["output_text"]
        parts: list[str] = []
        for item in data.get("output", []) or []:
            for content in item.get("content", []) or []:
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    parts.append(content["text"])
        if isinstance(data.get("text"), str):
            parts.append(data["text"])
        return "\n".join(parts)

    def _degraded(self, sources: list[SearchResult], error_code: str) -> LLMResult:
        citations = [source.citation_id for source in sources]
        lines = ["模型暂时不可用，以下是检索到的相关资料："]
        for source in sources:
            lines.append(f"- {source.document_title}，第 {source.page} 页 [{source.citation_id}]")
        return LLMResult(
            answer="\n".join(lines),
            citation_ids=citations,
            degraded=True,
            model=self.settings.llm_model,
            error_code=error_code,
        )


class FakeLLMAdapter(LLMAdapter):
    def __init__(self, settings: Settings, answer: str | None = None):
        super().__init__(settings)
        self.answer = answer

    def generate(self, question: str, sources: list[SearchResult]) -> LLMResult:
        if not sources:
            return super().generate(question, sources)
        answer = self.answer or f"根据检索到的资料，可以从相关页面继续复习。[{sources[0].citation_id}]"
        return LLMResult(
            answer=answer,
            citation_ids=[sources[0].citation_id],
            degraded=False,
            model="fake-test-model",
        )
