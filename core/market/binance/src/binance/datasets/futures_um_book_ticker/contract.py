"""UM bookTicker dataset 契约。"""

DATASET_KEY = "futures_um_book_ticker"
COLLECT_CLI = "crypto.data.futures.um.bookTicker"
BACKFILL_CLI = "crypto.archive.futures.um.bookTicker"
TARGET_TABLE = "market.binance_futures_um_book_ticker"

from .collect import CSV_HEADER, UmBookTicker, parse_um_book_ticker_from_ccxt, um_book_ticker_to_csv_row

__all__ = [
    "BACKFILL_CLI",
    "COLLECT_CLI",
    "CSV_HEADER",
    "DATASET_KEY",
    "TARGET_TABLE",
    "UmBookTicker",
    "parse_um_book_ticker_from_ccxt",
    "um_book_ticker_to_csv_row",
]
