"""LF Metrics 子进程。"""

from __future__ import annotations

import logging
import signal
import time

from binance.datasets.futures_um_metrics_snapshot_5m.collect import MetricsCollector


logger = logging.getLogger('binance.runtime.lf_metrics_worker')
_running = True



def _stop(*_: object) -> None:
    global _running
    _running = False



def main() -> None:
    global _running
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    collector = MetricsCollector()
    try:
        while _running:
            collector.run_once()
            for _ in range(300):
                if not _running:
                    break
                time.sleep(1)
    finally:
        collector.close()
        logger.info('LF metrics worker 已退出')


if __name__ == '__main__':
    main()
