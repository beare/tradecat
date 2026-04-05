from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from binance.config import BinanceStackConfig
from binance.runtime.audit import audit_exit_code, doctor_exit_code, render_audit_summary, render_doctor_health


def _build_config(runtime_dir: Path) -> BinanceStackConfig:
    return BinanceStackConfig(
        project_root=runtime_dir.parent.parent,
        stack_root=runtime_dir.parent.parent,
        env_file=None,
        var_root=runtime_dir.parent,
        lf_runtime_dir=runtime_dir,
        hf_runtime_dir=runtime_dir.parent / "hf",
        enable_lf=True,
        enable_hf=False,
        dry_run=False,
        hf_service_mode="",
    )


def test_render_doctor_health_reports_active_lf_datasets(tmp_path) -> None:
    now_utc = datetime.now(UTC)
    now_local = now_utc.astimezone()
    runtime_dir = tmp_path / "var" / "lf"
    logs_dir = runtime_dir / "logs"
    logs_dir.mkdir(parents=True)
    (logs_dir / "ws.log").write_text(
        f"{now_local.strftime('%Y-%m-%d %H:%M:%S,%f')[:-3]} - INFO - ws.collector - WS写入: 4 条 | bucket_ts_max={(now_utc - timedelta(minutes=1)).isoformat()}\n",
        encoding="utf-8",
    )
    (logs_dir / "metrics.log").write_text(
        f"{now_local.strftime('%Y-%m-%d %H:%M:%S,%f')[:-3]} - INFO - binance.datasets.futures_um_metrics_snapshot_5m.collect - 保存 4 条 | requests_total=1280 | requests_failed=8 | rows_written=252 | last_collect_duration=0.923 | last_collect_time=1775258451.7987669\n",
        encoding="utf-8",
    )

    rows, rendered = render_doctor_health(_build_config(runtime_dir), now=now_utc)

    assert len(rows) == 2
    assert {row.dataset_key for row in rows} == {"futures_um_candles_1m", "futures_um_metrics_snapshot_5m"}
    assert "freshness_s" in rendered
    assert "rows_written" in rendered
    assert doctor_exit_code(rows) == 0


def test_render_audit_summary_marks_missing_backfill_log(tmp_path) -> None:
    now_utc = datetime.now(UTC)
    now_local = now_utc.astimezone()
    runtime_dir = tmp_path / "var" / "lf"
    logs_dir = runtime_dir / "logs"
    logs_dir.mkdir(parents=True)
    (logs_dir / "ws.log").write_text(
        f"{now_local.strftime('%Y-%m-%d %H:%M:%S,%f')[:-3]} - INFO - ws.collector - WS写入: 4 条 | bucket_ts_max={(now_utc - timedelta(minutes=1)).isoformat()}\n",
        encoding="utf-8",
    )
    (logs_dir / "metrics.log").write_text(
        f"{now_local.strftime('%Y-%m-%d %H:%M:%S,%f')[:-3]} - INFO - binance.datasets.futures_um_metrics_snapshot_5m.collect - 保存 4 条 | requests_total=1280 | requests_failed=8 | rows_written=252 | last_collect_duration=0.923 | last_collect_time=1775258451.7987669\n",
        encoding="utf-8",
    )

    rows, rendered = render_audit_summary(_build_config(runtime_dir), now=now_utc)

    assert "lf_ws" in rendered
    assert "lf_metrics" in rendered
    assert "lf_backfill" in rendered
    assert any(row.component == "lf_backfill" and row.state == "missing" for row in rows)
    assert audit_exit_code(rows) == 1
