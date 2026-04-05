"""现货 1m K线 dataset 回填说明。

当前对象不单独执行 archive backfill。
如果需要补历史，应优先补 `spot_trades`；数据库连续聚合会自动追平 `spot_candles_1m`。
"""

TARGET_TABLE = "market.binance_cagg_spot_klines_1m"


def main() -> None:
    raise RuntimeError("spot_candles_1m 由 spot_trades 连续聚合维护，不支持独立 backfill CLI")


__all__ = ["TARGET_TABLE", "main"]
