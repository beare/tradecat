"""UM trades dataset 契约。"""

DATASET_KEY = "futures_um_trades"
COLLECT_CLI = "crypto.data.futures.um.trades"
BACKFILL_CLI = "crypto.archive.futures.um.trades"
REPAIR_CLI = "crypto.repair.futures.um.trades"
TARGET_TABLE = "market.binance_futures_um_trades"

from .collect import CSV_HEADER, UmTrade, parse_um_trade_from_ccxt, um_trade_to_csv_row

__all__ = [
    "BACKFILL_CLI",
    "COLLECT_CLI",
    "CSV_HEADER",
    "DATASET_KEY",
    "REPAIR_CLI",
    "TARGET_TABLE",
    "UmTrade",
    "parse_um_trade_from_ccxt",
    "um_trade_to_csv_row",
]
