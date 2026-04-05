from pathlib import Path

from src.publishers import runner


def _clear_exporter_env(monkeypatch):
    for key in [
        "TG_PUBLISHER_CHANNEL_ID",
        "TG_PUBLISHER_BOT_TOKENS",
        "TG_PUBLISHER_EXCLUDE_TAGS",
        "TG_PUBLISHER_POLL_INTERVAL",
        "TG_SIGNAL_CHANNEL_ID",
        "TG_SIGNAL_BOT_TOKENS",
        "TG_SIGNAL_INCLUDE_TAGS",
        "TG_SIGNAL_POLL_INTERVAL",
        "TG_CHANNEL_ID",
        "TG_BOT_TOKENS",
        "BOT_TOKEN",
        "TELEGRAM_BOT_TOKEN",
        "QUERY_SERVICE_BASE_URL",
        "QUERY_SERVICE_TOKEN",
        "QUERY_SERVICE_AUTH_MODE",
        "POLL_INTERVAL",
        "EXCLUDE_TAGS",
        "INCLUDE_TAGS",
    ]:
        monkeypatch.delenv(key, raising=False)


def test_build_publisher_mode_uses_explicit_env(monkeypatch):
    _clear_exporter_env(monkeypatch)
    monkeypatch.setenv("TG_PUBLISHER_CHANNEL_ID", "@publisher")
    monkeypatch.setenv("TG_PUBLISHER_BOT_TOKENS", "token-a, token-b")
    monkeypatch.setenv("TG_PUBLISHER_EXCLUDE_TAGS", "日历,交易")
    monkeypatch.setenv("TG_PUBLISHER_POLL_INTERVAL", "2")

    mode = runner._build_mode("publisher")

    assert mode.name == "publisher"
    assert mode.channel_id == "@publisher"
    assert mode.bot_tokens == ["token-a", "token-b"]
    assert mode.exclude_tags == {"日历", "交易"}
    assert mode.poll_interval == 2.0
    assert mode.cursor_file.name == "publisher_cursor.json"


def test_build_signal_mode_falls_back_to_shared_keys(monkeypatch):
    _clear_exporter_env(monkeypatch)
    monkeypatch.setenv("TG_CHANNEL_ID", "@shared")
    monkeypatch.setenv("BOT_TOKEN", "bot-token")
    monkeypatch.setenv("INCLUDE_TAGS", "信号,交易")
    monkeypatch.setenv("POLL_INTERVAL", "3")

    mode = runner._build_mode("signal")

    assert mode.name == "signal"
    assert mode.channel_id == "@shared"
    assert mode.bot_tokens == ["bot-token"]
    assert mode.include_tags == {"信号", "交易"}
    assert mode.poll_interval == 3.0
    assert mode.cursor_file.name == "signal_cursor.json"


def test_validate_mode_requires_query_token_when_auth_enabled(monkeypatch):
    _clear_exporter_env(monkeypatch)
    monkeypatch.setenv("QUERY_SERVICE_BASE_URL", "http://127.0.0.1:8088")
    monkeypatch.setenv("QUERY_SERVICE_AUTH_MODE", "required")
    monkeypatch.setenv("TG_CHANNEL_ID", "@shared")
    monkeypatch.setenv("BOT_TOKEN", "bot-token")

    mode = runner._build_mode("publisher")

    try:
        runner._validate_mode(mode)
    except RuntimeError as exc:
        assert "QUERY_SERVICE_TOKEN" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected QUERY_SERVICE_TOKEN validation failure")
