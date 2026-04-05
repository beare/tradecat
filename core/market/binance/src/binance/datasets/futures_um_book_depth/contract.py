"""UM bookDepth dataset 契约。"""

DATASET_KEY = "futures_um_book_depth"
COLLECT_CLI = "crypto.data.futures.um.bookDepth"
BACKFILL_CLI = "crypto.archive.futures.um.bookDepth"
TARGET_TABLE = "market.binance_futures_um_book_depth"

from .collect import CSV_HEADER, UmBookDepthPoint, um_book_depth_to_csv_row

__all__ = [
    "BACKFILL_CLI",
    "COLLECT_CLI",
    "CSV_HEADER",
    "DATASET_KEY",
    "TARGET_TABLE",
    "UmBookDepthPoint",
    "um_book_depth_to_csv_row",
]
