from __future__ import annotations

from binance.sources.archive_source.download_utils import parse_checksum_text


def test_parse_checksum_text_maps_filename_to_sha256() -> None:
    checksum = "26e7c804864bed33bf470f01c4fb46d2caed1bfb064e2535831dade12168b786  BTCUSDT-trades-2026-02-12.zip\n"
    mapping = parse_checksum_text(checksum)
    assert mapping["BTCUSDT-trades-2026-02-12.zip"] == "26e7c804864bed33bf470f01c4fb46d2caed1bfb064e2535831dade12168b786"


def test_parse_checksum_text_ignores_invalid_lines() -> None:
    checksum = "\ninvalid\nshort  file.zip\n"
    mapping = parse_checksum_text(checksum)
    assert mapping == {}
