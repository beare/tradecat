from binance.registry import list_datasets


def test_registry_contains_core_dataset_keys() -> None:
    keys = {spec.dataset_key for spec in list_datasets(include_reserved=True)}
    assert {
        "futures_um_candles_1m",
        "futures_um_metrics_snapshot_5m",
        "futures_um_trades",
        "spot_trades",
        "spot_candles_1m",
    }.issubset(keys)
    assert "futures_um_metrics_atomic" not in keys


def test_registry_excludes_reserved_when_requested() -> None:
    runtime_statuses = {spec.dataset_key: spec.runtime_status for spec in list_datasets(include_reserved=False)}
    assert "futures_cm_book_ticker" not in runtime_statuses
    assert runtime_statuses["spot_candles_1m"] == "materialized_view"


def test_hf_datasets_default_disabled() -> None:
    rows = {spec.dataset_key: spec for spec in list_datasets(include_reserved=False) if spec.group == "hf"}
    assert rows["futures_um_trades"].default_enabled is False
    assert rows["futures_um_book_ticker"].default_enabled is False
    assert rows["futures_um_book_depth"].default_enabled is False
    assert rows["spot_trades"].default_enabled is False


def test_passive_dataset_default_disabled() -> None:
    rows = {spec.dataset_key: spec for spec in list_datasets(include_reserved=False)}
    assert rows["spot_candles_1m"].group == "passive"
    assert rows["spot_candles_1m"].default_enabled is False


def test_lf_physical_tables_follow_public_surface() -> None:
    rows = {spec.dataset_key: spec for spec in list_datasets(include_reserved=False)}
    assert rows["futures_um_candles_1m"].physical_table == "market.binance_futures_um_candles_1m"
    assert rows["futures_um_metrics_snapshot_5m"].physical_table == "market.binance_futures_um_metrics_snapshot_5m"


def test_reserved_datasets_keep_no_physical_table() -> None:
    rows = {spec.dataset_key: spec for spec in list_datasets(include_reserved=True)}
    assert rows["option_bvol_index"].physical_table is None
    assert rows["option_eoh_summary"].physical_table is None
