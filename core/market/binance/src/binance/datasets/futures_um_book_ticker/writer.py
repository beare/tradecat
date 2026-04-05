"""UM bookTicker dataset 写入契约。"""

TARGET_TABLE = "market.binance_futures_um_book_ticker"

from binance.storage.raw_futures_um_book_ticker import RawFuturesUmBookTickerRow, RawFuturesUmBookTickerWriter

__all__ = ["RawFuturesUmBookTickerRow", "RawFuturesUmBookTickerWriter", "TARGET_TABLE"]
