"""HF 统一工作进程。"""

from __future__ import annotations

import asyncio
from datetime import date
import logging
import os
from pathlib import Path

from binance.common.env import build_layout, load_env_defaults
from binance.common.hf_env import hf_bool_env, hf_env, primary_hf_env_name
from binance.datasets.futures_um_book_depth.backfill import download_and_ingest as backfill_futures_um_book_depth
from binance.datasets.futures_um_book_depth.collect import collect_realtime as collect_futures_um_book_depth
from binance.datasets.futures_um_book_ticker.backfill import download_and_ingest as backfill_futures_um_book_ticker
from binance.datasets.futures_um_book_ticker.collect import collect_realtime as collect_futures_um_book_ticker
from binance.datasets.futures_um_trades.backfill import download_and_ingest as backfill_futures_um_trades
from binance.datasets.futures_um_trades.collect import collect_realtime as collect_futures_um_trades
from binance.datasets.futures_um_trades.repair import repair_open_gaps as repair_futures_um_trades
from binance.datasets.spot_trades.backfill import download_and_ingest as backfill_spot_trades
from binance.datasets.spot_trades.collect import collect_realtime as collect_spot_trades
from binance.datasets.spot_trades.repair import repair_open_gaps as repair_spot_trades
from binance.registry import find_dataset_by_cli, is_dataset_enabled


logger = logging.getLogger("binance.runtime.hf_worker")
STACK_ROOT = Path(__file__).resolve().parents[3]
LAYOUT = build_layout(STACK_ROOT)
load_env_defaults(LAYOUT.env_file)
HF_RUNTIME_DIR = STACK_ROOT / "var" / "hf"



def _required(key: str) -> str:
    value = hf_env(key)
    if not value:
        raise ValueError(f"缺少必填环境变量：{primary_hf_env_name(key)}")
    return value



def _optional(key: str) -> str | None:
    value = hf_env(key)
    return value or None



def _parse_symbols(raw: str) -> list[str]:
    symbols = [item.strip().upper() for item in (raw or "").split(",") if item.strip()]
    if not symbols:
        raise ValueError("symbols 不能为空")
    return symbols



def _parse_optional_symbols(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    values = [item.strip().upper() for item in raw.split(",") if item.strip()]
    return values or None



def _database_url() -> str:
    return hf_env("database_url")



def _binance_data_base() -> str:
    return os.getenv("BINANCE_DATA_BASE", "https://data.binance.vision").rstrip("/")



def _resolve_spec(mode: str, cli_name: str):
    spec = find_dataset_by_cli(cli_name, mode)
    if spec is None:
        raise ValueError(f"未知 {mode} dataset: {cli_name}")
    if not is_dataset_enabled(spec):
        raise ValueError(f"dataset 已禁用：{spec.dataset_key}（需设置 {spec.enabled_env}=1）")
    return spec



def _run_collect() -> None:
    dataset = _required("collect_dataset")
    spec = _resolve_spec("collect", dataset)
    symbols = _parse_symbols(_required("collect_symbols"))
    write_csv = not hf_bool_env("collect_no_csv", default=False)
    write_db = not hf_bool_env("collect_no_db", default=False)
    flush_max_rows = int(hf_env("collect_flush_max_rows", "2000") or "2000")
    flush_interval = float(hf_env("collect_flush_interval", "1.0") or "1.0")
    window_seconds = int(hf_env("collect_window_seconds", "300") or "300")
    rest_overlap_multiplier = int(hf_env("collect_rest_overlap_multiplier", "3") or "3")
    gap_threshold = int(hf_env("collect_gap_threshold_seconds", "30") or "30")
    gap_interval = float(hf_env("collect_gap_check_interval", "10.0") or "10.0")
    orderbook_limit = int(hf_env("collect_orderbook_limit", "1000") or "1000")
    emit_interval = float(hf_env("collect_emit_interval", "5.0") or "5.0")
    database_url = _database_url()

    if spec.dataset_key == "futures_um_trades":
        asyncio.run(
            collect_futures_um_trades(
                symbols=symbols,
                service_root=HF_RUNTIME_DIR,
                database_url=database_url,
                write_csv=write_csv,
                write_db=write_db,
                flush_max_rows=flush_max_rows,
                flush_interval_seconds=flush_interval,
                window_seconds=window_seconds,
                rest_overlap_multiplier=rest_overlap_multiplier,
                gap_threshold_seconds=gap_threshold,
                gap_check_interval_seconds=gap_interval,
            )
        )
        return

    if spec.dataset_key == "futures_um_book_ticker":
        asyncio.run(
            collect_futures_um_book_ticker(
                symbols=symbols,
                service_root=HF_RUNTIME_DIR,
                database_url=database_url,
                write_csv=write_csv,
                write_db=write_db,
                flush_max_rows=flush_max_rows,
                flush_interval_seconds=flush_interval,
                gap_threshold_seconds=gap_threshold,
                gap_check_interval_seconds=gap_interval,
            )
        )
        return

    if spec.dataset_key == "futures_um_book_depth":
        asyncio.run(
            collect_futures_um_book_depth(
                symbols=symbols,
                service_root=HF_RUNTIME_DIR,
                database_url=database_url,
                write_csv=write_csv,
                write_db=write_db,
                flush_max_rows=flush_max_rows,
                flush_interval_seconds=flush_interval,
                gap_threshold_seconds=gap_threshold,
                gap_check_interval_seconds=gap_interval,
                orderbook_limit=orderbook_limit,
                emit_interval_seconds=emit_interval,
            )
        )
        return

    if spec.dataset_key == "spot_trades":
        asyncio.run(
            collect_spot_trades(
                symbols=symbols,
                service_root=HF_RUNTIME_DIR,
                database_url=database_url,
                write_csv=write_csv,
                write_db=write_db,
                flush_max_rows=flush_max_rows,
                flush_interval_seconds=flush_interval,
                window_seconds=window_seconds,
                rest_overlap_multiplier=rest_overlap_multiplier,
                gap_threshold_seconds=gap_threshold,
                gap_check_interval_seconds=gap_interval,
            )
        )
        return

    raise ValueError(f"不支持 collect 的 dataset: {spec.dataset_key}")



def _run_backfill() -> None:
    dataset = _required("backfill_dataset")
    spec = _resolve_spec("backfill", dataset)
    symbols = _parse_symbols(_required("backfill_symbols"))
    start_date = date.fromisoformat(_required("backfill_start_date"))
    end_date = date.fromisoformat(_required("backfill_end_date"))
    write_files = not hf_bool_env("backfill_no_files", default=False)
    write_db = not hf_bool_env("backfill_no_db", default=False)
    prefer_monthly = not hf_bool_env("backfill_no_prefer_monthly", default=False)
    allow_no_checksum = hf_bool_env("backfill_allow_no_checksum", default=False)
    local_only = hf_bool_env("backfill_local_only", default=False)
    force_update = hf_bool_env("backfill_force_update", default=False)
    workers = int(hf_env("backfill_workers", "1") or "1")
    database_url = _database_url()
    binance_data_base = _binance_data_base()

    common = dict(
        symbols=symbols,
        start_date=start_date,
        end_date=end_date,
        service_root=HF_RUNTIME_DIR,
        database_url=database_url,
        binance_data_base=binance_data_base,
        write_files=write_files,
        write_db=write_db,
        prefer_monthly=prefer_monthly,
        allow_no_checksum=allow_no_checksum,
    )

    if spec.dataset_key == "futures_um_trades":
        statuses = backfill_futures_um_trades(local_only=local_only, force_update=force_update, workers=workers, **common)
        logger.info("HF backfill 完成: dataset=%s statuses=%s", spec.dataset_key, statuses)
        return
    if spec.dataset_key == "spot_trades":
        statuses = backfill_spot_trades(local_only=local_only, force_update=force_update, workers=workers, **common)
        logger.info("HF backfill 完成: dataset=%s statuses=%s", spec.dataset_key, statuses)
        return
    if spec.dataset_key == "futures_um_book_ticker":
        backfill_futures_um_book_ticker(**common)
        logger.info("HF backfill 完成: dataset=%s", spec.dataset_key)
        return
    if spec.dataset_key == "futures_um_book_depth":
        backfill_futures_um_book_depth(**common)
        logger.info("HF backfill 完成: dataset=%s", spec.dataset_key)
        return
    raise ValueError(f"不支持 backfill 的 dataset: {spec.dataset_key}")



def _run_repair() -> None:
    dataset = _required("repair_dataset")
    spec = _resolve_spec("repair", dataset)
    symbols = _parse_optional_symbols(_optional("repair_symbols"))
    max_jobs = int(hf_env("repair_max_jobs", "10") or "10")
    write_files = not hf_bool_env("repair_no_files", default=False)
    prefer_monthly = not hf_bool_env("repair_no_prefer_monthly", default=False)
    allow_no_checksum = hf_bool_env("repair_allow_no_checksum", default=False)
    database_url = _database_url()
    binance_data_base = _binance_data_base()

    common = dict(
        service_root=HF_RUNTIME_DIR,
        database_url=database_url,
        binance_data_base=binance_data_base,
        symbols=symbols,
        max_jobs=max_jobs,
        write_files=write_files,
        prefer_monthly=prefer_monthly,
        allow_no_checksum=allow_no_checksum,
    )

    if spec.dataset_key == "futures_um_trades":
        result = repair_futures_um_trades(**common)
        logger.info(
            "HF repair 完成: dataset=%s claimed=%d closed=%d reopened=%d",
            spec.dataset_key,
            result.claimed,
            result.closed,
            result.reopened,
        )
        return
    if spec.dataset_key == "spot_trades":
        result = repair_spot_trades(**common)
        logger.info(
            "HF repair 完成: dataset=%s claimed=%d closed=%d reopened=%d",
            spec.dataset_key,
            result.claimed,
            result.closed,
            result.reopened,
        )
        return
    raise ValueError(f"不支持 repair 的 dataset: {spec.dataset_key}")



def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    mode = _required("service_mode").strip().lower()
    logger.info("HF unified worker 启动: mode=%s", mode)
    if mode == "collect":
        _run_collect()
        return
    if mode == "backfill":
        _run_backfill()
        return
    if mode == "repair":
        _run_repair()
        return
    raise ValueError(f"{primary_hf_env_name('service_mode')}={mode} 不支持（只允许 collect|backfill|repair）")


if __name__ == "__main__":
    main()
