from course_agent.tokenizer import fts_query, normalize_text, tokenize_for_search


def test_math_and_chinese_tokens_are_stable():
    text = "一致连续性：设 ε > 0，存在 δ > 0。"
    normalized = normalize_text(text)
    tokens = tokenize_for_search(text)
    assert "一致" in tokens
    assert "连续" in tokens
    assert "epsilon" not in normalized.lower()
    assert "ε" in tokens
    assert '"一致"' in fts_query("一致连续")
