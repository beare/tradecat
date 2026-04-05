from __future__ import annotations

from binance.registry import DATASET_REGISTRY


def test_reserved_datasets_stay_reserved() -> None:
    reserved = {spec.dataset_key for spec in DATASET_REGISTRY.values() if spec.runtime_status == "reserved"}
    assert reserved == {
        "futures_cm_book_ticker",
        "futures_cm_book_depth",
        "option_bvol_index",
        "option_eoh_summary",
    }
