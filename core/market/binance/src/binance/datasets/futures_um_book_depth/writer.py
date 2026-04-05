"""UM bookDepth dataset 写入契约。"""

TARGET_TABLE = "market.binance_futures_um_book_depth"

from binance.storage.raw_futures_um_book_depth import RawFuturesUmBookDepthRow, RawFuturesUmBookDepthWriter

__all__ = ["RawFuturesUmBookDepthRow", "RawFuturesUmBookDepthWriter", "TARGET_TABLE"]
