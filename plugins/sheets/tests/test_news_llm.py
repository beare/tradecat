from __future__ import annotations

from pathlib import Path

from src.news_exporter import (
    NewsLlmRuntime,
    _derive_level_from_score,
    _normalize_llm_score_payload,
    _parse_json_object_text,
    _score_news_rows_with_llm,
)


def _runtime() -> NewsLlmRuntime:
    return NewsLlmRuntime(
        enabled=True,
        reason="enabled",
        base_url="https://example.com/v1",
        api_key="k",
        model_id="qwen3.5-plus",
        prompt_version="news_score_v1",
        timeout_seconds=20,
        max_new_per_run=10,
        max_reason_chars=40,
        cache_path=Path("/tmp/news_llm_cache_test.sqlite3"),
        max_tokens=256,
    )


def test_parse_json_object_text_from_wrapped_text() -> None:
    raw = '```json\n{"importance_score":78,"importance_level":"HIGH"}\n```'
    got = _parse_json_object_text(raw)
    assert isinstance(got, dict)
    assert got["importance_score"] == 78


def test_normalize_llm_score_payload_clamp_and_cleanup() -> None:
    rt = _runtime()
    got = _normalize_llm_score_payload(
        {
            "importance_score": "123",
            "importance_level": "bad_level",
            "tags": [" macro ", "Rates", "", "macro"],
            "reason": "第一行\n第二行",
            "confidence": "-9",
            "scored_at_bj": "2026-03-18 20:00:00",
        },
        rt=rt,
    )
    assert got["importance_score"] == 100
    assert got["importance_level"] == "CRITICAL"
    assert got["tags"] == "macro,rates"
    assert got["reason"] == "第一行 第二行"
    assert got["confidence"] == 0
    assert got["scored_at_bj"] == "2026-03-18 20:00:00"
    assert got["model_id"] == "qwen3.5-plus"


def test_derive_level_from_score_thresholds() -> None:
    assert _derive_level_from_score(92) == "CRITICAL"
    assert _derive_level_from_score(70) == "HIGH"
    assert _derive_level_from_score(50) == "MID"
    assert _derive_level_from_score(10) == "LOW"


def test_score_rows_with_llm_disabled(monkeypatch) -> None:
    monkeypatch.setenv("SHEETS_NEWS_LLM_ENABLE", "0")
    rows = [{"created_at_bj": "2026-03-18 20:00:00", "content": "测试新闻"}]
    scored, stats = _score_news_rows_with_llm(rows, values_only=True)
    assert stats["enabled"] is False
    assert len(scored) == 1
    assert scored[0]["importance_score"] == "null"
    assert scored[0]["importance_level"] == "null"
