from __future__ import annotations

from pathlib import Path

from binance.config import BinanceStackConfig
from binance.runtime.hf_canary import render_hf_canary_summary, rollback_hf_canary


def _build_config(tmp_path: Path, *, enable_hf: bool = True, mode: str = "collect") -> BinanceStackConfig:
    return BinanceStackConfig(
        project_root=tmp_path,
        stack_root=tmp_path,
        env_file=None,
        var_root=tmp_path / "var",
        lf_runtime_dir=tmp_path / "var" / "lf",
        hf_runtime_dir=tmp_path / "var" / "hf",
        enable_lf=False,
        enable_hf=enable_hf,
        dry_run=False,
        hf_service_mode=mode,
    )


def test_validate_start_rejects_multiple_hf_datasets(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BINANCE_HF_SERVICE_MODE", "collect")
    monkeypatch.setenv("BINANCE_HF_COLLECT_DATASET", "crypto.data.futures.um.trades")
    monkeypatch.setenv("BINANCE_HF_COLLECT_SYMBOLS", "BTCUSDT")
    monkeypatch.setenv("BINANCE_HF_DATABASE_URL", "postgresql://example")
    monkeypatch.setenv("BINANCE_DATASET_FUTURES_UM_TRADES_ENABLED", "1")
    monkeypatch.setenv("BINANCE_DATASET_FUTURES_UM_BOOK_TICKER_ENABLED", "1")

    errors = _build_config(tmp_path).validate_start()

    assert any("只允许启用 1 个 dataset" in item for item in errors)


def test_validate_start_rejects_multi_symbol_canary(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BINANCE_HF_SERVICE_MODE", "collect")
    monkeypatch.setenv("BINANCE_HF_COLLECT_DATASET", "crypto.data.futures.um.trades")
    monkeypatch.setenv("BINANCE_HF_COLLECT_SYMBOLS", "BTCUSDT,ETHUSDT")
    monkeypatch.setenv("BINANCE_HF_DATABASE_URL", "postgresql://example")
    monkeypatch.setenv("BINANCE_DATASET_FUTURES_UM_TRADES_ENABLED", "1")

    errors = _build_config(tmp_path).validate_start()

    assert any("只允许单 symbol smoke" in item for item in errors)


def test_hf_canary_summary_reports_ready(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BINANCE_HF_SERVICE_MODE", "collect")
    monkeypatch.setenv("BINANCE_HF_COLLECT_DATASET", "crypto.data.futures.um.trades")
    monkeypatch.setenv("BINANCE_HF_COLLECT_SYMBOLS", "BTCUSDT")
    monkeypatch.setenv("BINANCE_HF_DATABASE_URL", "postgresql://example")
    monkeypatch.setenv("BINANCE_DATASET_FUTURES_UM_TRADES_ENABLED", "1")

    rendered = render_hf_canary_summary(_build_config(tmp_path))

    assert "hf_canary_ready=1" in rendered
    assert "hf_canary_dataset=futures_um_trades" in rendered
    assert "hf_canary_symbols=BTCUSDT" in rendered


def test_hf_canary_rollback_archives_log_and_meta(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BINANCE_HF_SERVICE_MODE", "collect")
    monkeypatch.setenv("BINANCE_HF_COLLECT_DATASET", "crypto.data.futures.um.trades")
    monkeypatch.setenv("BINANCE_HF_COLLECT_SYMBOLS", "BTCUSDT")
    monkeypatch.setenv("BINANCE_HF_DATABASE_URL", "postgresql://example")
    monkeypatch.setenv("BINANCE_DATASET_FUTURES_UM_TRADES_ENABLED", "1")
    monkeypatch.setenv("BINANCE_HF_CANARY_EVIDENCE_DIR", str(tmp_path / "evidence"))

    config = _build_config(tmp_path)
    runtime_dir = config.hf_runtime_dir
    log_file = runtime_dir / "logs" / "service.log"
    meta_file = runtime_dir / "run" / "service.meta"
    pid_file = runtime_dir / "pids" / "service.pid"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    meta_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("HF log\n", encoding="utf-8")
    meta_file.write_text("mode=collect\n", encoding="utf-8")
    pid_file.write_text("999999\n", encoding="utf-8")

    rc, rendered = rollback_hf_canary(config)

    assert rc == 0
    assert "hf_canary_rollback=ok" in rendered
    evidence_root = Path(str(tmp_path / "evidence"))
    archived = sorted(evidence_root.iterdir())
    assert archived
    copied = archived[0]
    assert (copied / "service.log").exists()
    assert (copied / "service.meta").exists()
    assert (copied / "service.pid").exists()
