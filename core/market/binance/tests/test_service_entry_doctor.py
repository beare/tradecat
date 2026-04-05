from datetime import UTC, datetime, timedelta
import sys

from binance.config import BinanceStackConfig
from binance import service_entry


def test_service_entry_doctor_reports_enabled_datasets(monkeypatch, tmp_path, capsys) -> None:
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
    monkeypatch.setenv("BINANCE_STACK_ENABLE_LF", "1")
    monkeypatch.setenv("BINANCE_STACK_ENABLE_HF", "1")
    monkeypatch.setenv("BINANCE_HF_SERVICE_MODE", "collect")
    monkeypatch.setenv("BINANCE_HF_COLLECT_DATASET", "crypto.data.futures.um.trades")
    monkeypatch.setenv("BINANCE_HF_COLLECT_SYMBOLS", "BTCUSDT")
    monkeypatch.setenv("BINANCE_DATASET_FUTURES_UM_TRADES_ENABLED", "1")
    config = BinanceStackConfig(
        project_root=tmp_path,
        stack_root=tmp_path,
        env_file=None,
        var_root=tmp_path / "var",
        lf_runtime_dir=runtime_dir,
        hf_runtime_dir=tmp_path / "var" / "hf",
        enable_lf=True,
        enable_hf=True,
        dry_run=False,
        hf_service_mode="collect",
    )
    monkeypatch.setattr(service_entry, "load_stack_config", lambda: config)
    monkeypatch.setattr(sys, "argv", ["binance.service_entry", "doctor"])

    rc = service_entry.main()
    captured = capsys.readouterr()

    assert rc == 0
    assert "=== Binance Stack: doctor ===" in captured.out
    assert "hf_enabled_datasets=futures_um_trades" in captured.out
    assert "hf_canary_ready=1" in captured.out
    assert "hf_canary_dataset=futures_um_trades" in captured.out
    assert "freshness_s" in captured.out
    assert "lag_s" in captured.out
    assert "rows_written" in captured.out
    assert "validation: ok" in captured.out


def test_service_entry_audit_renders_lf_component_summary(monkeypatch, tmp_path, capsys) -> None:
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
    (logs_dir / "backfill.log").write_text(
        f"{now_local.strftime('%Y-%m-%d %H:%M:%S,%f')[:-3]} - INFO - binance.sources.binance_api.backfill_shared - 扫描 K 线缺口: 4 个符号, 2026-03-03 ~ 2026-04-02\n"
        f"{now_local.strftime('%Y-%m-%d %H:%M:%S,%f')[:-3]} - INFO - binance.runtime.lf_backfill_worker - 巡检完成: K线填充 0 条, Metrics填充 0 条\n",
        encoding="utf-8",
    )
    config = BinanceStackConfig(
        project_root=tmp_path,
        stack_root=tmp_path,
        env_file=None,
        var_root=tmp_path / "var",
        lf_runtime_dir=runtime_dir,
        hf_runtime_dir=tmp_path / "var" / "hf",
        enable_lf=True,
        enable_hf=False,
        dry_run=False,
        hf_service_mode="",
    )
    monkeypatch.setattr(service_entry, "load_stack_config", lambda: config)
    monkeypatch.setattr(sys, "argv", ["binance.service_entry", "audit"])

    rc = service_entry.main()
    captured = capsys.readouterr()

    assert rc == 0
    assert "=== Binance Stack: audit ===" in captured.out
    assert "lf_ws" in captured.out
    assert "lf_metrics" in captured.out
    assert "lf_backfill" in captured.out
