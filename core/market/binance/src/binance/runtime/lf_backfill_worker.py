"""LF Backfill 子进程。"""

from __future__ import annotations

import logging
import signal
import time

from binance.registry import enabled_dataset_keys
from binance.sources.binance_api.backfill_shared import DataBackfiller, compute_lookback, get_backfill_config


logger = logging.getLogger("binance.runtime.lf_backfill_worker")
_running = True



def _stop(*_: object) -> None:
    global _running
    _running = False



def _run_enabled_backfill(backfiller: DataBackfiller) -> tuple[dict[str, int], dict[str, int]]:
    enabled = set(enabled_dataset_keys(group="lf"))
    empty = {"scanned": 0, "gaps": 0, "filled": 0, "remaining": 0}
    if not enabled:
        return empty, empty
    if enabled == {"futures_um_candles_1m", "futures_um_metrics_snapshot_5m"}:
        result = backfiller.run_all()
        return result["klines"], result["metrics"]
    if enabled == {"futures_um_candles_1m"}:
        return backfiller.run_klines(), empty
    if enabled == {"futures_um_metrics_snapshot_5m"}:
        return empty, backfiller.run_metrics()
    return backfiller.run_klines(), backfiller.run_metrics()



def main() -> None:
    global _running
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    mode, env_days, on_start, start_date = get_backfill_config()
    lookback = compute_lookback(mode, env_days, start_date)
    if lookback <= 0:
        logger.info("BACKFILL_MODE=none，LF backfill worker 直接退出")
        return

    logger.info("LF backfill worker 启动: mode=%s lookback=%d days", mode, lookback)
    backfiller = DataBackfiller(lookback_days=lookback)

    def run_once(stage: str) -> None:
        klines, metrics = _run_enabled_backfill(backfiller)
        logger.info(
            "%s完成: K线填充 %d 条, Metrics填充 %d 条",
            stage,
            int(klines.get("filled", 0)),
            int(metrics.get("filled", 0)),
        )

    if on_start and _running:
        try:
            logger.info("启动时执行一次全量补齐")
            run_once("启动补齐")
        except Exception as exc:  # noqa: BLE001
            logger.error("启动补齐异常: %s", exc, exc_info=True)

    while _running:
        try:
            logger.info("开始缺口巡检")
            run_once("巡检")
        except Exception as exc:  # noqa: BLE001
            logger.error("巡检异常: %s", exc, exc_info=True)

        for _ in range(300):
            if not _running:
                break
            time.sleep(1)

    logger.info("LF backfill worker 已退出")


if __name__ == "__main__":
    main()
