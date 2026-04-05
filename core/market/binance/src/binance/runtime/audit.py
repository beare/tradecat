"""LF 运行审计与判活工具。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
import re

from binance.config import BinanceStackConfig


_LOCAL_TZ = datetime.now().astimezone().tzinfo
_LOG_TS_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})")
_WS_RE = re.compile(r"WS写入: (?P<rows>\d+) 条 \| bucket_ts_max=(?P<bucket_ts>.+)$")
_METRICS_RE = re.compile(r"保存 (?P<rows>\d+) 条 \| (?P<rest>.+)$")
_ROWS_TOTAL_RE = re.compile(r"rows_written=(?P<rows_total>\d+)")
_REQUESTS_FAILED_RE = re.compile(r"requests_failed=(?P<requests_failed>\d+)")
_BACKFILL_SCAN_RE = re.compile(
    r"扫描 (?P<kind>K 线|Metrics)缺口: \d+ 个符号, (?P<start>\d{4}-\d{2}-\d{2}) ~ (?P<end>\d{4}-\d{2}-\d{2})"
)
_BACKFILL_DONE_RE = re.compile(r"巡检完成: K线填充 (?P<klines>\d+) 条, Metrics填充 (?P<metrics>\d+) 条")


@dataclass(frozen=True, slots=True)
class DatasetHealth:
    dataset_key: str
    state: str
    observed_at: datetime | None
    freshness_seconds: int | None
    lag_seconds: int | None
    rows_written: int | None
    detail: str


@dataclass(frozen=True, slots=True)
class AuditFinding:
    component: str
    state: str
    observed_at: datetime | None
    freshness_seconds: int | None
    detail: str


def _read_lines(path: Path, *, tail: int = 400) -> list[str]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    if tail <= 0 or len(lines) <= tail:
        return lines
    return lines[-tail:]


def _parse_log_timestamp(line: str) -> datetime | None:
    match = _LOG_TS_RE.match(line)
    if not match:
        return None
    try:
        ts = datetime.strptime(match.group("ts"), "%Y-%m-%d %H:%M:%S,%f")
    except ValueError:
        return None
    return ts.replace(tzinfo=_LOCAL_TZ)


def _to_seconds(delta: timedelta) -> int:
    return max(int(delta.total_seconds()), 0)


def _state_from_age(age_seconds: int | None, *, warn_after: int, fail_after: int) -> str:
    if age_seconds is None:
        return "missing"
    if age_seconds > fail_after:
        return "stale"
    if age_seconds > warn_after:
        return "warn"
    return "ok"


def _render_dt(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _render_int(value: int | None) -> str:
    return "-" if value is None else str(int(value))


def _render_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))

    def border(sep: str = "-") -> str:
        return "+" + "+".join(sep * (width + 2) for width in widths) + "+"

    def render_row(values: list[str]) -> str:
        return "| " + " | ".join(value.ljust(widths[index]) for index, value in enumerate(values)) + " |"

    parts = [border(), render_row(headers), border("=")]
    parts.extend(render_row(row) for row in rows)
    parts.append(border())
    return "\n".join(parts)


def _build_doctor_rows(config: BinanceStackConfig, *, now: datetime | None = None) -> list[DatasetHealth]:
    current = now or datetime.now(UTC)
    logs_dir = config.lf_runtime_dir / "logs"
    rows: list[DatasetHealth] = []

    if "futures_um_candles_1m" in config.lf_enabled_dataset_keys:
        ws_lines = _read_lines(logs_dir / "ws.log")
        observed_at = None
        bucket_ts = None
        last_rows = None
        for line in reversed(ws_lines):
            match = _WS_RE.search(line)
            if not match:
                continue
            observed_at = _parse_log_timestamp(line)
            try:
                bucket_ts = datetime.fromisoformat(match.group("bucket_ts"))
            except ValueError:
                bucket_ts = None
            last_rows = int(match.group("rows"))
            break
        freshness = _to_seconds(current - observed_at.astimezone(UTC)) if observed_at else None
        lag = _to_seconds(current - bucket_ts.astimezone(UTC)) if bucket_ts else None
        state = _state_from_age(freshness, warn_after=180, fail_after=600)
        detail = f"source=ws bucket_ts_max={_render_dt(bucket_ts)}"
        rows.append(
            DatasetHealth(
                dataset_key="futures_um_candles_1m",
                state=state,
                observed_at=observed_at,
                freshness_seconds=freshness,
                lag_seconds=lag,
                rows_written=last_rows,
                detail=detail,
            )
        )

    if "futures_um_metrics_snapshot_5m" in config.lf_enabled_dataset_keys:
        metrics_lines = _read_lines(logs_dir / "metrics.log")
        observed_at = None
        last_rows = None
        rows_total = None
        requests_failed = None
        data_time = None
        for line in reversed(metrics_lines):
            match = _METRICS_RE.search(line)
            if not match:
                continue
            observed_at = _parse_log_timestamp(line)
            last_rows = int(match.group("rows"))
            rest = match.group("rest")
            total_match = _ROWS_TOTAL_RE.search(rest)
            if total_match:
                rows_total = int(total_match.group("rows_total"))
            failed_match = _REQUESTS_FAILED_RE.search(rest)
            if failed_match:
                requests_failed = int(failed_match.group("requests_failed"))
            if observed_at:
                observed_utc = observed_at.astimezone(UTC)
                data_time = observed_utc.replace(
                    minute=(observed_utc.minute // 5) * 5,
                    second=0,
                    microsecond=0,
                )
            break
        freshness = _to_seconds(current - observed_at.astimezone(UTC)) if observed_at else None
        lag = _to_seconds(current - data_time) if data_time else None
        state = _state_from_age(freshness, warn_after=420, fail_after=1200)
        detail_parts = [f"source=rest_poll snapshot_ts={_render_dt(data_time)}"]
        if rows_total is not None:
            detail_parts.append(f"rows_total={rows_total}")
        if requests_failed is not None:
            detail_parts.append(f"requests_failed={requests_failed}")
        rows.append(
            DatasetHealth(
                dataset_key="futures_um_metrics_snapshot_5m",
                state=state,
                observed_at=observed_at,
                freshness_seconds=freshness,
                lag_seconds=lag,
                rows_written=last_rows,
                detail=" ".join(detail_parts),
            )
        )

    return rows


def _build_audit_rows(config: BinanceStackConfig, *, now: datetime | None = None) -> list[AuditFinding]:
    current = now or datetime.now(UTC)
    logs_dir = config.lf_runtime_dir / "logs"
    findings: list[AuditFinding] = []

    ws_rows = _build_doctor_rows(config, now=current)
    for row in ws_rows:
        component = row.dataset_key
        if row.dataset_key == "futures_um_candles_1m":
            component = "lf_ws"
        elif row.dataset_key == "futures_um_metrics_snapshot_5m":
            component = "lf_metrics"
        findings.append(
            AuditFinding(
                component=component,
                state=row.state,
                observed_at=row.observed_at,
                freshness_seconds=row.freshness_seconds,
                detail=f"rows_written={_render_int(row.rows_written)} lag_s={_render_int(row.lag_seconds)} {row.detail}",
            )
        )

    backfill_lines = _read_lines(logs_dir / "backfill.log")
    last_scan = None
    last_done = None
    repeated_fill_count = 0
    for line in backfill_lines:
        scan_match = _BACKFILL_SCAN_RE.search(line)
        if scan_match and scan_match.group("kind") == "K 线":
            last_scan = (scan_match.group("start"), scan_match.group("end"))
        done_match = _BACKFILL_DONE_RE.search(line)
        if done_match:
            kline_filled = int(done_match.group("klines"))
            metrics_filled = int(done_match.group("metrics"))
            ts = _parse_log_timestamp(line)
            if kline_filled > 0 or metrics_filled > 0:
                repeated_fill_count += 1
            else:
                repeated_fill_count = 0
            last_done = (ts, kline_filled, metrics_filled)

    if last_done:
        observed_at, kline_filled, metrics_filled = last_done
        freshness = _to_seconds(current - observed_at.astimezone(UTC)) if observed_at else None
        state = _state_from_age(freshness, warn_after=600, fail_after=1800)
        if repeated_fill_count >= 2:
            state = "anomaly"
        range_text = "-" if last_scan is None else f"{last_scan[0]}..{last_scan[1]}"
        findings.append(
            AuditFinding(
                component="lf_backfill",
                state=state,
                observed_at=observed_at,
                freshness_seconds=freshness,
                detail=(
                    f"range={range_text} kline_filled={kline_filled} metrics_filled={metrics_filled} "
                    f"repeated_fill_detected={'1' if repeated_fill_count >= 2 else '0'}"
                ),
            )
        )
    else:
        findings.append(
            AuditFinding(
                component="lf_backfill",
                state="missing",
                observed_at=None,
                freshness_seconds=None,
                detail="backfill.log 缺少最近巡检完成证据",
            )
        )

    return findings


def render_doctor_health(config: BinanceStackConfig, *, now: datetime | None = None) -> tuple[list[DatasetHealth], str]:
    rows = _build_doctor_rows(config, now=now)
    table_rows = [
        [
            row.dataset_key,
            row.state,
            _render_dt(row.observed_at),
            _render_int(row.freshness_seconds),
            _render_int(row.lag_seconds),
            _render_int(row.rows_written),
            row.detail,
        ]
        for row in rows
    ]
    headers = ["dataset_key", "state", "observed_at", "freshness_s", "lag_s", "rows_written", "detail"]
    return rows, _render_table(headers, table_rows)


def render_audit_summary(config: BinanceStackConfig, *, now: datetime | None = None) -> tuple[list[AuditFinding], str]:
    rows = _build_audit_rows(config, now=now)
    table_rows = [
        [
            row.component,
            row.state,
            _render_dt(row.observed_at),
            _render_int(row.freshness_seconds),
            row.detail,
        ]
        for row in rows
    ]
    headers = ["component", "state", "observed_at", "freshness_s", "detail"]
    return rows, _render_table(headers, table_rows)


def doctor_exit_code(rows: list[DatasetHealth]) -> int:
    if any(row.state in {"missing", "stale"} for row in rows):
        return 1
    return 0


def audit_exit_code(rows: list[AuditFinding]) -> int:
    if any(row.state in {"missing", "stale", "anomaly"} for row in rows):
        return 1
    return 0
