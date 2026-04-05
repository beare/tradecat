from binance.config import load_stack_config
from binance.runtime.lf_runner import build_lf_runner



def test_lf_runner_components_follow_dataset_switches(monkeypatch) -> None:
    monkeypatch.setenv("BINANCE_DATASET_FUTURES_UM_CANDLES_1M_ENABLED", "0")
    monkeypatch.setenv("BINANCE_DATASET_FUTURES_UM_METRICS_SNAPSHOT_5M_ENABLED", "1")

    runner = build_lf_runner(load_stack_config())

    assert runner.component_names == ("metrics", "backfill")


def test_lf_runner_supports_legacy_env_aliases(monkeypatch) -> None:
    monkeypatch.delenv("BINANCE_DATASET_FUTURES_UM_CANDLES_1M_ENABLED", raising=False)
    monkeypatch.delenv("BINANCE_DATASET_FUTURES_UM_METRICS_SNAPSHOT_5M_ENABLED", raising=False)
    monkeypatch.setenv("BINANCE_DATASET_CANDLES_1M_ENABLED", "0")
    monkeypatch.setenv("BINANCE_DATASET_FUTURES_METRICS_5M_ENABLED", "1")

    runner = build_lf_runner(load_stack_config())

    assert runner.component_names == ("metrics", "backfill")
