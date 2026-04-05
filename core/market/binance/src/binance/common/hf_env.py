"""HF 运行环境契约。"""

from __future__ import annotations

from typing import Final

from binance.common.env import first_bool_env, first_env


HF_ENV_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "database_url": ("BINANCE_HF_DATABASE_URL", "DATABASE_URL"),
    "service_mode": ("BINANCE_HF_SERVICE_MODE",),
    "canary_smoke_seconds": ("BINANCE_HF_CANARY_SMOKE_SECONDS",),
    "canary_evidence_dir": ("BINANCE_HF_CANARY_EVIDENCE_DIR",),
    "collect_dataset": ("BINANCE_HF_COLLECT_DATASET",),
    "collect_symbols": ("BINANCE_HF_COLLECT_SYMBOLS",),
    "collect_no_csv": ("BINANCE_HF_COLLECT_NO_CSV",),
    "collect_no_db": ("BINANCE_HF_COLLECT_NO_DB",),
    "collect_flush_max_rows": ("BINANCE_HF_COLLECT_FLUSH_MAX_ROWS",),
    "collect_flush_interval": ("BINANCE_HF_COLLECT_FLUSH_INTERVAL",),
    "collect_window_seconds": ("BINANCE_HF_COLLECT_WINDOW_SECONDS",),
    "collect_rest_overlap_multiplier": ("BINANCE_HF_COLLECT_REST_OVERLAP_MULTIPLIER",),
    "collect_gap_threshold_seconds": ("BINANCE_HF_COLLECT_GAP_THRESHOLD_SECONDS",),
    "collect_gap_check_interval": ("BINANCE_HF_COLLECT_GAP_CHECK_INTERVAL",),
    "collect_orderbook_limit": ("BINANCE_HF_COLLECT_ORDERBOOK_LIMIT",),
    "collect_emit_interval": ("BINANCE_HF_COLLECT_EMIT_INTERVAL",),
    "backfill_dataset": ("BINANCE_HF_BACKFILL_DATASET",),
    "backfill_symbols": ("BINANCE_HF_BACKFILL_SYMBOLS",),
    "backfill_start_date": ("BINANCE_HF_BACKFILL_START_DATE",),
    "backfill_end_date": ("BINANCE_HF_BACKFILL_END_DATE",),
    "backfill_no_files": ("BINANCE_HF_BACKFILL_NO_FILES",),
    "backfill_no_db": ("BINANCE_HF_BACKFILL_NO_DB",),
    "backfill_no_prefer_monthly": ("BINANCE_HF_BACKFILL_NO_PREFER_MONTHLY",),
    "backfill_allow_no_checksum": ("BINANCE_HF_BACKFILL_ALLOW_NO_CHECKSUM",),
    "backfill_local_only": ("BINANCE_HF_BACKFILL_LOCAL_ONLY",),
    "backfill_force_update": ("BINANCE_HF_BACKFILL_FORCE_UPDATE",),
    "backfill_workers": ("BINANCE_HF_BACKFILL_WORKERS",),
    "repair_dataset": ("BINANCE_HF_REPAIR_DATASET",),
    "repair_symbols": ("BINANCE_HF_REPAIR_SYMBOLS",),
    "repair_max_jobs": ("BINANCE_HF_REPAIR_MAX_JOBS",),
    "repair_no_files": ("BINANCE_HF_REPAIR_NO_FILES",),
    "repair_no_prefer_monthly": ("BINANCE_HF_REPAIR_NO_PREFER_MONTHLY",),
    "repair_allow_no_checksum": ("BINANCE_HF_REPAIR_ALLOW_NO_CHECKSUM",),
}


def primary_hf_env_name(key: str) -> str:
    return HF_ENV_ALIASES[key][0]


def hf_env(key: str, default: str = "") -> str:
    return first_env(HF_ENV_ALIASES[key], default=default)


def hf_bool_env(key: str, default: bool = False) -> bool:
    return first_bool_env(HF_ENV_ALIASES[key], default=default)
