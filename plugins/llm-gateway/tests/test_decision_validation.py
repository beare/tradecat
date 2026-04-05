from __future__ import annotations

from src.gateway import (
    _extract_decisions_json,
    _normalize_decisions,
    _validate_decisions_schema,
    _validate_nofx_json_format,
)


def test_validate_nofx_json_format_ok():
    assert _validate_nofx_json_format('[{"symbol":"ALL","action":"wait","reasoning":"x"}]') is None


def test_validate_nofx_json_format_reject_range():
    assert _validate_nofx_json_format('[{"x":"1~2"}]') is not None


def test_extract_decisions_json_from_tag():
    text = "<reasoning>r</reasoning><decision>[{\"symbol\":\"ALL\",\"action\":\"wait\",\"reasoning\":\"x\"}]</decision>"
    assert _extract_decisions_json(text) == '[{"symbol":"ALL","action":"wait","reasoning":"x"}]'


def test_validate_decisions_schema_reject_missing_symbol():
    err = _validate_decisions_schema([{"action": "wait", "reasoning": "x"}])
    assert err is not None


def test_validate_decisions_schema_reject_invalid_action():
    err = _validate_decisions_schema([{"symbol": "BTCUSDT", "action": "buy", "reasoning": "x"}])
    assert err is not None


def test_validate_decisions_schema_reject_open_long_missing_fields():
    err = _validate_decisions_schema([{"symbol": "BTCUSDT", "action": "open_long", "reasoning": "x"}])
    assert err is not None


def test_normalize_decisions_upper_symbol_lower_action():
    decisions = _normalize_decisions([{"symbol": " btcusdt ", "action": " OPEN_LONG ", "reasoning": " x "}])
    assert decisions[0]["symbol"] == "BTCUSDT"
    assert decisions[0]["action"] == "open_long"
    assert decisions[0]["reasoning"] == "x"
