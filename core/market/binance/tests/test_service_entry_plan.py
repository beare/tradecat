import sys

from binance import service_entry


def test_service_entry_plan_renders_dataset_matrix(monkeypatch, capsys) -> None:
    monkeypatch.setenv("BINANCE_STACK_ENABLE_LF", "1")
    monkeypatch.setenv("BINANCE_STACK_ENABLE_HF", "0")
    monkeypatch.setattr(sys, "argv", ["binance.service_entry", "plan"])

    rc = service_entry.main()
    captured = capsys.readouterr()

    assert rc == 0
    assert "=== Binance Stack: plan ===" in captured.out
    assert "futures_um_candles_1m" in captured.out
    assert "futures_um_metrics_snapshot_5m" in captured.out
    assert "spot_candles_1m" in captured.out
    assert "futures_um_trades" in captured.out
