from __future__ import annotations

from course_agent.config import Settings
from course_agent.llm import LLMAdapter
from course_agent.retrieval import SearchResult


class _Response:
    status_code = 200

    @staticmethod
    def json() -> dict:
        return {"output_text": "这是一个没有引用的模型回答。"}


class _Client:
    def __init__(self, **_: object):
        pass

    def __enter__(self) -> "_Client":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    @staticmethod
    def post(*_: object, **__: object) -> _Response:
        return _Response()


def test_model_answer_without_valid_citations_falls_back(monkeypatch, tmp_path):
    settings = Settings(
        runtime_dir=tmp_path,
        llm_api_key="test-key",
        llm_base_url="https://example.invalid",
        llm_model="test-model",
    )
    source = SearchResult(
        citation_id="S1",
        chunk_id="chunk-1",
        document_id="document-1",
        document_title="测试讲义",
        page=3,
        content="一致连续要求同一个 delta 对区间内所有点成立。",
        score=-1.0,
        space_id="space-1",
    )
    monkeypatch.setattr("course_agent.llm.httpx.Client", _Client)

    result = LLMAdapter(settings).generate("什么是一致连续？", [source])

    assert result.degraded is True
    assert result.error_code == "llm_missing_citations"
    assert result.citation_ids == ["S1"]
    assert "第 3 页 [S1]" in result.answer


def test_direct_mode_degrades_when_model_is_not_configured(tmp_path):
    settings = Settings(runtime_dir=tmp_path, llm_api_key="", llm_base_url="")

    result = LLMAdapter(settings).generate_direct("解释一致连续。")

    assert result.degraded is True
    assert result.error_code == "llm_not_configured"
    assert result.citation_ids == []


def test_direct_mode_does_not_require_citations(monkeypatch, tmp_path):
    settings = Settings(
        runtime_dir=tmp_path,
        llm_api_key="test-key",
        llm_base_url="https://example.invalid",
        llm_model="test-model",
    )
    monkeypatch.setattr("course_agent.llm.httpx.Client", _Client)

    result = LLMAdapter(settings).generate_direct("解释一致连续。")

    assert result.degraded is False
    assert result.citation_ids == []
    assert result.error_code is None
