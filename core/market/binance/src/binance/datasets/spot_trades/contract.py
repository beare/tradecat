"""Spot trades dataset 契约。"""

DATASET_KEY = "spot_trades"
COLLECT_CLI = "crypto.data.spot.trades"
BACKFILL_CLI = "crypto.archive.spot.trades"
REPAIR_CLI = "crypto.repair.spot.trades"
TARGET_TABLE = "market.binance_spot_trades"

from .collect import CSV_HEADER, SpotTrade, parse_spot_trade_from_ccxt, spot_trade_to_csv_row

__all__ = [
    "BACKFILL_CLI",
    "COLLECT_CLI",
    "CSV_HEADER",
    "DATASET_KEY",
    "REPAIR_CLI",
    "TARGET_TABLE",
    "SpotTrade",
    "parse_spot_trade_from_ccxt",
    "spot_trade_to_csv_row",
]
