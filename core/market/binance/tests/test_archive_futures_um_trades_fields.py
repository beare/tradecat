from __future__ import annotations

from datetime import date
from decimal import Decimal

from binance.datasets.futures_um_trades.collect import parse_um_trade_from_ccxt, um_trade_to_csv_row
from binance.sources.archive_source.decimal_utils import format_decimal_for_archive_csv
from binance.sources.archive_source.paths import relpath_futures_um_trades_daily


def test_format_decimal_for_archive_csv_trims_trailing_zeros_and_keeps_one_decimal() -> None:
    assert format_decimal_for_archive_csv(Decimal("1125.0000")) == "1125.0"
    assert format_decimal_for_archive_csv(Decimal("7028.00")) == "7028.0"
    assert format_decimal_for_archive_csv(Decimal("492.0174")) == "492.0174"
    assert format_decimal_for_archive_csv(Decimal("0")) == "0.0"


def test_relpath_futures_um_trades_daily() -> None:
    assert relpath_futures_um_trades_daily("BTCUSDT", date(2026, 2, 9)) == "data/futures/um/daily/trades/BTCUSDT/BTCUSDT-trades-2026-02-09.csv"


def test_parse_um_trade_from_ccxt_and_csv_row_aligns_official_fields() -> None:
    trade = {
        "info": {
            "s": "BTCUSDT",
            "t": 7252042528,
            "p": "70280.0",
            "q": "0.1",
            "T": 1770595201730,
            "m": True,
        }
    }

    parsed = parse_um_trade_from_ccxt(trade, fallback_symbol="BTCUSDT")
    assert parsed.symbol == "BTCUSDT"
    assert parsed.id == 7252042528
    assert parsed.price == Decimal("70280.0")
    assert parsed.qty == Decimal("0.1")
    assert parsed.quote_qty == Decimal("7028.00")
    assert parsed.time == 1770595201730
    assert parsed.is_buyer_maker is True

    row = um_trade_to_csv_row(parsed)
    assert row[0] == "7252042528"
    assert row[1] == "70280.0"
    assert row[2] == "0.1"
    assert row[3] == "7028.0"
    assert row[4] == "1770595201730"
    assert row[5] == "true"
