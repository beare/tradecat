"""HF canary 控制与证据保全。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from binance.common.hf_env import hf_bool_env, hf_env
from binance.config import BinanceStackConfig
from binance.registry import DatasetSpec, find_dataset_by_cli
from binance.runtime.hf_runner import build_hf_runner
from binance.runtime.process_utils import is_running, read_pid
from binance.storage.pg import connect, cursor


@dataclass(frozen=True, slots=True)
class HFCanarySpec:
    mode: str
    dataset_cli: str
    dataset_spec: DatasetSpec | None
    symbols: tuple[str, ...]
    smoke_seconds: int
    evidence_dir: Path
    errors: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.errors


COLLECT_DB_SQL = {
    "futures_um_trades": "SELECT to_timestamp(MAX(time) / 1000.0) FROM market.binance_futures_um_trades",
    "futures_um_book_ticker": "SELECT to_timestamp(MAX(event_time) / 1000.0) FROM market.binance_futures_um_book_ticker",
    "futures_um_book_depth": "SELECT to_timestamp(MAX(timestamp) / 1000.0) FROM market.binance_futures_um_book_depth",
    "spot_trades": "SELECT to_timestamp(MAX(time) / 1000000.0) FROM market.binance_spot_trades",
}

SUCCESS_MARKERS = {
    "collect": {
        "futures_um_trades": ("UM trades WS订阅:", "UM trades 入库 inserted="),
        "futures_um_book_ticker": ("UM bookTicker 入库 inserted=",),
        "futures_um_book_depth": ("UM bookDepth 入库 inserted=",),
        "spot_trades": ("Spot trades WS订阅:", "Spot trades 入库 inserted="),
    },
    "backfill": {
        "futures_um_trades": ("回填计划:", "入库完成:"),
        "futures_um_book_ticker": ("回填计划:", "入库完成:"),
        "futures_um_book_depth": ("回填计划:", "入库完成:"),
        "spot_trades": ("回填计划:", "入库完成:"),
    },
    "repair": {
        "futures_um_trades": ("repair gap_id=", "repair: 没有可认领的 open gaps"),
        "spot_trades": ("repair gap_id=", "repair: 没有可认领的 open gaps"),
    },
}

FAIL_MARKERS = ("Traceback", "回填失败:", "实时采集异常:", "repair 失败", "ValueError:", "RuntimeError:")


def resolve_hf_canary_spec(config: BinanceStackConfig) -> HFCanarySpec:
    mode = config.hf_service_mode.strip().lower()
    dataset_cli = hf_env(f"{mode}_dataset") if mode in {"collect", "backfill", "repair"} else ""
    dataset_spec = find_dataset_by_cli(dataset_cli, mode) if dataset_cli and mode in {"collect", "backfill", "repair"} else None
    symbols_raw = hf_env(f"{mode}_symbols") if mode in {"collect", "backfill", "repair"} else ""
    symbols = tuple(item.strip().upper() for item in symbols_raw.split(",") if item.strip())
    try:
        smoke_seconds = int(hf_env("canary_smoke_seconds", "120") or "120")
    except ValueError:
        smoke_seconds = 120
    evidence_dir = build_hf_runner(config).evidence_root
    errors = tuple(config.validate_hf_canary())
    return HFCanarySpec(
        mode=mode,
        dataset_cli=dataset_cli,
        dataset_spec=dataset_spec,
        symbols=symbols,
        smoke_seconds=smoke_seconds,
        evidence_dir=evidence_dir,
        errors=errors,
    )


def render_hf_canary_summary(config: BinanceStackConfig) -> str:
    spec = resolve_hf_canary_spec(config)
    dataset_key = spec.dataset_spec.dataset_key if spec.dataset_spec else "-"
    validation = "ok" if spec.ready else "; ".join(spec.errors)
    lines = [
        f"  hf_canary_ready={'1' if spec.ready else '0'}",
        f"  hf_canary_mode={spec.mode or '-'}",
        f"  hf_canary_dataset={dataset_key}",
        f"  hf_canary_symbols={','.join(spec.symbols) or '-'}",
        f"  hf_canary_smoke_seconds={spec.smoke_seconds}",
        f"  hf_canary_evidence_dir={spec.evidence_dir}",
        f"  hf_canary_validation={validation}",
    ]
    return "\n".join(lines)


def render_hf_canary_plan(config: BinanceStackConfig) -> str:
    spec = resolve_hf_canary_spec(config)
    runner = build_hf_runner(config)
    dataset_key = spec.dataset_spec.dataset_key if spec.dataset_spec else "-"
    enabled_env = spec.dataset_spec.enabled_env if spec.dataset_spec else "BINANCE_DATASET_<HF_DATASET>_ENABLED"
    lines = ["=== Binance Stack: canary ===", render_hf_canary_summary(config)]
    if not spec.ready:
        lines.append("  canary_plan=blocked")
        lines.append("  next_step=先修正 hf_canary_validation 后再启动 HF")
        return "\n".join(lines)

    lines.extend(
        [
            "  canary_plan=ready",
            f"  start_cmd=BINANCE_STACK_ENABLE_HF=1 {enabled_env}=1 ./scripts/start.sh start",
            f"  verify_cmd=PYTHONPATH={config.stack_root / 'src'} python3 -m binance.service_entry canary verify",
            f"  rollback_cmd=PYTHONPATH={config.stack_root / 'src'} python3 -m binance.service_entry canary rollback",
            f"  status_cmd=PYTHONPATH={config.stack_root / 'src'} python3 -m binance.service_entry doctor",
            f"  hf_runtime_log={runner.log_file}",
            f"  hf_runtime_meta={runner.meta_file}",
            f"  hf_dataset={dataset_key}",
        ]
    )
    return "\n".join(lines)


def _read_meta(meta_file: Path) -> dict[str, str]:
    if not meta_file.exists():
        return {}
    payload: dict[str, str] = {}
    for line in meta_file.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        payload[key.strip()] = value.strip()
    return payload


def _read_log_tail(log_file: Path, *, max_bytes: int = 32768) -> str:
    if not log_file.exists():
        return ""
    data = log_file.read_bytes()
    return data[-max_bytes:].decode("utf-8", errors="ignore")


def _query_collect_latest_utc(spec: DatasetSpec, database_url: str) -> datetime | None:
    query = COLLECT_DB_SQL.get(spec.dataset_key)
    if not query:
        return None
    with connect(database_url) as conn:
        with cursor(conn) as cur:
            cur.execute(query)
            row = cur.fetchone()
    value = row[0] if row else None
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def verify_hf_canary(config: BinanceStackConfig) -> tuple[int, str]:
    spec = resolve_hf_canary_spec(config)
    runner = build_hf_runner(config)
    lines = ["=== Binance Stack: canary verify ===", render_hf_canary_summary(config)]
    if not spec.ready:
        return 2, "\n".join(lines)

    errors: list[str] = []
    meta = _read_meta(runner.meta_file)
    log_tail = _read_log_tail(runner.log_file)
    pid = read_pid(runner.pid_file)

    lines.append(f"  hf_canary_pid={pid or '-'}")
    lines.append(f"  hf_canary_log_file={runner.log_file}")
    lines.append(f"  hf_canary_meta_file={runner.meta_file}")

    if spec.mode == "collect" and not is_running(pid):
        errors.append("collect canary 进程未运行")

    if not runner.log_file.exists():
        errors.append("HF canary 日志不存在")

    if meta.get("dataset") and meta.get("dataset") != spec.dataset_cli:
        errors.append(f"HF canary meta dataset 漂移：{meta.get('dataset')} != {spec.dataset_cli}")

    markers = SUCCESS_MARKERS.get(spec.mode, {}).get(spec.dataset_spec.dataset_key if spec.dataset_spec else "", ())
    if markers and not any(marker in log_tail for marker in markers):
        errors.append(f"HF canary 缺少成功日志信号：{markers[0]}")
    if any(marker in log_tail for marker in FAIL_MARKERS):
        errors.append("HF canary 日志包含失败信号")

    if spec.mode == "collect" and spec.dataset_spec and not hf_bool_env("collect_no_db", default=False):
        latest = _query_collect_latest_utc(spec.dataset_spec, hf_env("database_url"))
        lines.append(f"  hf_canary_db_latest_utc={latest.isoformat() if latest else '-'}")
        started_at_raw = meta.get("started_at_utc")
        if latest is None:
            errors.append("HF canary collect 未观测到数据库最新时间")
        elif started_at_raw:
            started_at = datetime.fromisoformat(started_at_raw.replace("Z", "+00:00")).astimezone(UTC)
            if latest < (started_at - timedelta(seconds=max(spec.smoke_seconds, 60))):
                errors.append("HF canary collect 数据库最新时间未进入 smoke 窗口")
    elif spec.mode != "collect":
        lines.append("  hf_canary_db_latest_utc=manual")

    lines.append(f"  hf_canary_verify={'ok' if not errors else 'failed'}")
    if errors:
        for item in errors:
            lines.append(f"  - {item}")
        lines.append(f"  rollback_cmd=PYTHONPATH={config.stack_root / 'src'} python3 -m binance.service_entry canary rollback")
        return 1, "\n".join(lines)
    return 0, "\n".join(lines)


def rollback_hf_canary(config: BinanceStackConfig) -> tuple[int, str]:
    runner = build_hf_runner(config)
    evidence_dir = runner.archive_evidence("rollback")
    stop_rc = runner.stop(dry_run=config.dry_run)
    lines = [
        "=== Binance Stack: canary rollback ===",
        f"  hf_canary_evidence_saved={evidence_dir}",
        f"  hf_canary_rollback={'ok' if stop_rc == 0 else 'failed'}",
    ]
    return stop_rc, "\n".join(lines)
