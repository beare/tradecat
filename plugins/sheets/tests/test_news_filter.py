from __future__ import annotations

import src.news_exporter as ne


class _DummyWriter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def read_values_a1(self, *, title: str, a1_range: str):  # type: ignore[no-untyped-def]
        self.calls.append((str(title), str(a1_range)))
        raise RuntimeError("dummy_no_sheet")


def _reset_filter_cache() -> None:
    ne._FILTER_CFG_CACHE["ts"] = 0.0
    ne._FILTER_CFG_CACHE["cfg"] = None
    ne._FILTER_CFG_CACHE["source"] = None


def test_enforce_mandatory_terms() -> None:
    cfg = ne.NewsFilterConfig(min_length=15, max_length=2000, blacklist=[], trim=[])
    out = ne._enforce_mandatory_news_filter_terms(cfg)
    assert "金十数据" in out.trim
    assert "金十图示" in out.blacklist
    assert "金十数据中心工具" in out.blacklist
    assert "点击观看" in out.blacklist
    assert "立即观看" in out.blacklist


def test_get_news_filter_config_forced_public(monkeypatch) -> None:
    _reset_filter_cache()
    monkeypatch.setenv("SHEETS_PUBLIC_READ", "1")
    monkeypatch.setenv("SHEETS_NEWS_FILTER_ENABLE", "0")
    monkeypatch.setenv("SHEETS_NEWS_FILTER_CONFIG_FROM_SHEET", "0")

    monkeypatch.setattr(
        ne,
        "_load_news_filter_config_from_repo",
        lambda: ne.NewsFilterConfig(min_length=15, max_length=2000, blacklist=[], trim=[]),
    )

    cfg, source = ne._get_news_filter_config(_DummyWriter())
    assert "forced_public" in source
    assert "金十数据" in cfg.trim


def test_get_news_filter_config_disabled_private(monkeypatch) -> None:
    _reset_filter_cache()
    monkeypatch.setenv("SHEETS_PUBLIC_READ", "0")
    monkeypatch.setenv("SHEETS_NEWS_FILTER_ENABLE", "0")
    monkeypatch.setenv("SHEETS_NEWS_FILTER_CONFIG_FROM_SHEET", "0")

    cfg, source = ne._get_news_filter_config(_DummyWriter())
    assert source == "disabled"
    assert cfg.blacklist == []
    assert cfg.trim == []


def test_normalize_trims_jinshi_with_optional_space() -> None:
    cfg = ne._enforce_mandatory_news_filter_terms(ne.NewsFilterConfig(min_length=0, max_length=2000, blacklist=[], trim=[]))
    got = ne._normalize_news_text("【标题】金十 数据3月25日讯，测试。", cfg=cfg)
    assert got is not None
    assert "金十数据" not in got
    assert "金十 数据" not in got


def test_normalize_drops_blacklisted_jinshi_tushi() -> None:
    cfg = ne._enforce_mandatory_news_filter_terms(ne.NewsFilterConfig(min_length=0, max_length=2000, blacklist=[], trim=[]))
    assert ne._normalize_news_text("金十图示：2026年03月17日（周二）测试", cfg=cfg) is None
