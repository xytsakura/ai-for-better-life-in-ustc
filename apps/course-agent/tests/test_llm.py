from __future__ import annotations

import httpx

from course_agent.config import Settings
from course_agent.llm import LLMAdapter
from course_agent.retrieval import SearchResult


class _Response:
    status_code = 200

    @staticmethod
    def json() -> dict:
        return {"output_text": "这是一个没有引用的模型回答。"}


class _CitationResponse:
    status_code = 200

    @staticmethod
    def json() -> dict:
        return {"output_text": "资料中的定义如下。[S1]"}


class _MalformedResponse:
    status_code = 200

    @staticmethod
    def json() -> list[str]:
        return ["unexpected", "response"]


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


class _CapturingClient:
    last_url: str | None = None
    last_payload: dict | None = None

    def __init__(self, **_: object):
        pass

    def __enter__(self) -> "_CapturingClient":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def post(self, *args: object, **kwargs: object) -> _Response:
        type(self).last_url = str(args[0]) if args else None
        json_payload = kwargs.get("json")
        if json_payload is None and len(args) >= 3:
            json_payload = args[2]
        type(self).last_payload = json_payload
        return _Response()


class _CitationCapturingClient(_CapturingClient):
    def post(self, *args: object, **kwargs: object) -> _CitationResponse:
        super().post(*args, **kwargs)
        return _CitationResponse()


class _MalformedClient(_Client):
    @staticmethod
    def post(*_: object, **__: object) -> _MalformedResponse:
        return _MalformedResponse()


class _HttpErrorClient:
    def __init__(self, **_: object):
        pass

    def __enter__(self) -> "_HttpErrorClient":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    @staticmethod
    def post(*_: object, **__: object) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "error": {
                    "message": "unauthorized; Authorization: Bearer test-secret-key"
                }
            },
        )


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


def test_direct_mode_forwards_conversation_history(monkeypatch, tmp_path):
    settings = Settings(
        runtime_dir=tmp_path,
        llm_api_key="test-key",
        llm_base_url="https://example.invalid",
        llm_model="test-model",
    )
    _CapturingClient.last_url = None
    _CapturingClient.last_payload = None
    monkeypatch.setattr("course_agent.llm.httpx.Client", _CapturingClient)

    history = [
        {"role": "user", "content": "什么是函数连续性？"},
        {"role": "assistant", "content": "函数连续性是指…"},
        {"role": "user", "content": "能详细一点吗？"},
        {"role": "assistant", "content": "更详细地说…"},
    ]
    LLMAdapter(settings).generate_direct("再举一个例子", history=history)

    payload = _CapturingClient.last_payload
    assert payload is not None
    assert _CapturingClient.last_url == "https://example.invalid/responses"
    assert "instructions" in payload
    assert "messages" not in payload
    assert "max_tokens" not in payload
    assert payload["max_output_tokens"] == 1200
    input_messages = payload["input"]
    assert input_messages[0] == history[0]
    assert input_messages[1] == history[1]
    assert input_messages[2] == history[2]
    assert input_messages[3] == history[3]
    assert input_messages[-1]["role"] == "user"
    assert "再举一个例子" in input_messages[-1]["content"]


def test_direct_mode_drops_invalid_history_entries(monkeypatch, tmp_path):
    settings = Settings(
        runtime_dir=tmp_path,
        llm_api_key="test-key",
        llm_base_url="https://example.invalid",
        llm_model="test-model",
    )
    _CapturingClient.last_url = None
    _CapturingClient.last_payload = None
    monkeypatch.setattr("course_agent.llm.httpx.Client", _CapturingClient)

    LLMAdapter(settings).generate_direct(
        "现在呢？",
        history=[
            {"role": "system", "content": "不该出现"},
            {"role": "user", "content": "  "},
            {"role": "user", "content": "上文提问"},
            {"role": "assistant", "content": "上文回答"},
            {"role": "tool", "content": "不该出现"},
        ],
    )
    payload = _CapturingClient.last_payload
    roles = [m["role"] for m in payload["input"]]
    assert roles == ["user", "assistant", "user"]
    assert all(message["role"] != "system" for message in payload["input"])


def test_retrieval_mode_uses_responses_payload_and_preserves_citations(monkeypatch, tmp_path):
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
        content="函数极限使用 epsilon-delta 语言定义。",
        score=-1.0,
        space_id="space-1",
    )
    _CitationCapturingClient.last_url = None
    _CitationCapturingClient.last_payload = None
    monkeypatch.setattr("course_agent.llm.httpx.Client", _CitationCapturingClient)

    result = LLMAdapter(settings).generate(
        "什么是函数极限？",
        [source],
        history=[{"role": "user", "content": "请使用所选资料。"}],
    )

    payload = _CitationCapturingClient.last_payload
    assert result.degraded is False
    assert result.citation_ids == ["S1"]
    assert _CitationCapturingClient.last_url == "https://example.invalid/responses"
    assert "instructions" in payload
    assert "messages" not in payload
    assert [message["role"] for message in payload["input"]] == ["user", "user"]
    assert "<source id=\"S1\"" in payload["input"][-1]["content"]


def test_http_error_is_degraded_without_exposing_request_credentials(monkeypatch, tmp_path):
    settings = Settings(
        runtime_dir=tmp_path,
        llm_api_key="test-secret-key",
        llm_base_url="https://example.invalid",
        llm_model="test-model",
    )
    monkeypatch.setattr("course_agent.llm.httpx.Client", _HttpErrorClient)

    result = LLMAdapter(settings).generate_direct("测试错误处理")

    assert result.degraded is True
    assert result.error_code == "llm_http_401"
    assert result.error_message == "unauthorized; Authorization: Bearer [REDACTED]"
    assert "test-secret-key" not in repr(result)


def test_malformed_success_response_degrades_instead_of_raising(monkeypatch, tmp_path):
    settings = Settings(
        runtime_dir=tmp_path,
        llm_api_key="test-key",
        llm_base_url="https://example.invalid",
        llm_model="test-model",
    )
    monkeypatch.setattr("course_agent.llm.httpx.Client", _MalformedClient)

    result = LLMAdapter(settings).generate_direct("测试异常响应")

    assert result.degraded is True
    assert result.error_code == "llm_empty_response"
