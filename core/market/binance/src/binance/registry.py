"""Binance dataset 真相矩阵。"""

from __future__ import annotations

from dataclasses import dataclass

from assets.common.contracts.semantic_contracts import get_contract_physical
from binance.common.env import first_bool_env


@dataclass(frozen=True, slots=True)
class DatasetSpec:
    dataset_key: str
    group: str
    runtime_status: str
    source_kind: str
    collect_cli: str | None
    backfill_cli: str | None
    repair_cli: str | None
    resource_id: str | None
    module_path: str
    enabled_env: str
    default_enabled: bool
    enabled_env_aliases: tuple[str, ...] = ()

    @property
    def physical_table(self) -> str | None:
        return _physical_table_from_resource(self.resource_id)


def dataset_env_name(dataset_key: str) -> str:
    return f"BINANCE_DATASET_{dataset_key.upper()}_ENABLED"

def _physical_table_from_resource(resource_id: str | None) -> str | None:
    normalized = str(resource_id or "").strip()
    if not normalized:
        return None
    physical = get_contract_physical(normalized)
    if isinstance(physical, dict):
        kind = str(physical.get("kind") or "").strip()
        schema = str(physical.get("schema") or "").strip()
        object_name = str(physical.get("object_name") or "").strip()
        if kind in {"postgres_table", "postgres_view"} and schema and object_name:
            return f"{schema}.{object_name}"
    raise RuntimeError(f"missing_contract_physical:{normalized}")


DATASET_REGISTRY: dict[str, DatasetSpec] = {
    "futures_um_candles_1m": DatasetSpec(
        dataset_key="futures_um_candles_1m",
        group="lf",
        runtime_status="active",
        source_kind="ws+archive_zip+rest_gap_fill",
        collect_cli="lf.collect.futures_um_candles_1m",
        backfill_cli="lf.backfill.futures_um_candles_1m",
        repair_cli=None,
        resource_id="facts/market/futures_um_candles_1m",
        module_path="binance.datasets.futures_um_candles_1m",
        enabled_env=dataset_env_name("futures_um_candles_1m"),
        default_enabled=True,
        enabled_env_aliases=(dataset_env_name("candles_1m"),),
    ),
    "futures_um_metrics_snapshot_5m": DatasetSpec(
        dataset_key="futures_um_metrics_snapshot_5m",
        group="lf",
        runtime_status="active",
        source_kind="rest_poll+archive_zip+rest_gap_fill",
        collect_cli="lf.collect.futures_um_metrics_snapshot_5m",
        backfill_cli="lf.backfill.futures_um_metrics_snapshot_5m",
        repair_cli=None,
        resource_id="facts/market/futures_um_metrics_snapshot_5m",
        module_path="binance.datasets.futures_um_metrics_snapshot_5m",
        enabled_env=dataset_env_name("futures_um_metrics_snapshot_5m"),
        default_enabled=True,
        enabled_env_aliases=(dataset_env_name("futures_metrics_5m"),),
    ),
    "futures_um_trades": DatasetSpec(
        dataset_key="futures_um_trades",
        group="hf",
        runtime_status="active",
        source_kind="ws+rest_gap_fill",
        collect_cli="crypto.data.futures.um.trades",
        backfill_cli="crypto.archive.futures.um.trades",
        repair_cli="crypto.repair.futures.um.trades",
        resource_id="facts/market/futures_um_trades",
        module_path="binance.datasets.futures_um_trades",
        enabled_env=dataset_env_name("futures_um_trades"),
        default_enabled=False,
    ),
    "futures_um_book_ticker": DatasetSpec(
        dataset_key="futures_um_book_ticker",
        group="hf",
        runtime_status="active",
        source_kind="ws+rest_gap_fill",
        collect_cli="crypto.data.futures.um.bookTicker",
        backfill_cli="crypto.archive.futures.um.bookTicker",
        repair_cli=None,
        resource_id="facts/market/futures_um_book_ticker",
        module_path="binance.datasets.futures_um_book_ticker",
        enabled_env=dataset_env_name("futures_um_book_ticker"),
        default_enabled=False,
    ),
    "futures_um_book_depth": DatasetSpec(
        dataset_key="futures_um_book_depth",
        group="hf",
        runtime_status="active",
        source_kind="ws+rest_gap_fill",
        collect_cli="crypto.data.futures.um.bookDepth",
        backfill_cli="crypto.archive.futures.um.bookDepth",
        repair_cli=None,
        resource_id="facts/market/futures_um_book_depth",
        module_path="binance.datasets.futures_um_book_depth",
        enabled_env=dataset_env_name("futures_um_book_depth"),
        default_enabled=False,
    ),
    "spot_trades": DatasetSpec(
        dataset_key="spot_trades",
        group="hf",
        runtime_status="active",
        source_kind="ws+rest_gap_fill",
        collect_cli="crypto.data.spot.trades",
        backfill_cli="crypto.archive.spot.trades",
        repair_cli="crypto.repair.spot.trades",
        resource_id="facts/market/spot_trades",
        module_path="binance.datasets.spot_trades",
        enabled_env=dataset_env_name("spot_trades"),
        default_enabled=False,
    ),
    "spot_candles_1m": DatasetSpec(
        dataset_key="spot_candles_1m",
        group="passive",
        runtime_status="materialized_view",
        source_kind="cagg_from_spot_trades",
        collect_cli=None,
        backfill_cli=None,
        repair_cli=None,
        resource_id="facts/market/spot_candles_1m",
        module_path="binance.datasets.spot_candles_1m",
        enabled_env=dataset_env_name("spot_candles_1m"),
        default_enabled=False,
    ),
    "futures_cm_book_ticker": DatasetSpec(
        dataset_key="futures_cm_book_ticker",
        group="hf",
        runtime_status="reserved",
        source_kind="reserved",
        collect_cli=None,
        backfill_cli=None,
        repair_cli=None,
        resource_id=None,
        module_path="binance.datasets._reserved.futures_cm_book_ticker",
        enabled_env=dataset_env_name("futures_cm_book_ticker"),
        default_enabled=False,
    ),
    "futures_cm_book_depth": DatasetSpec(
        dataset_key="futures_cm_book_depth",
        group="hf",
        runtime_status="reserved",
        source_kind="reserved",
        collect_cli=None,
        backfill_cli=None,
        repair_cli=None,
        resource_id=None,
        module_path="binance.datasets._reserved.futures_cm_book_depth",
        enabled_env=dataset_env_name("futures_cm_book_depth"),
        default_enabled=False,
    ),
    "option_bvol_index": DatasetSpec(
        dataset_key="option_bvol_index",
        group="hf",
        runtime_status="reserved",
        source_kind="reserved",
        collect_cli=None,
        backfill_cli=None,
        repair_cli=None,
        resource_id=None,
        module_path="binance.datasets._reserved.option_bvol_index",
        enabled_env=dataset_env_name("option_bvol_index"),
        default_enabled=False,
    ),
    "option_eoh_summary": DatasetSpec(
        dataset_key="option_eoh_summary",
        group="hf",
        runtime_status="reserved",
        source_kind="reserved",
        collect_cli=None,
        backfill_cli=None,
        repair_cli=None,
        resource_id=None,
        module_path="binance.datasets._reserved.option_eoh_summary",
        enabled_env=dataset_env_name("option_eoh_summary"),
        default_enabled=False,
    ),
}


MODE_ATTR = {
    "collect": "collect_cli",
    "backfill": "backfill_cli",
    "repair": "repair_cli",
}


def is_dataset_enabled(spec: DatasetSpec) -> bool:
    return first_bool_env((spec.enabled_env, *spec.enabled_env_aliases), default=spec.default_enabled)


def list_datasets(*, include_reserved: bool = True, enabled_only: bool = False, group: str | None = None) -> list[DatasetSpec]:
    rows = list(DATASET_REGISTRY.values())
    if group is not None:
        rows = [row for row in rows if row.group == group]
    if not include_reserved:
        rows = [row for row in rows if row.runtime_status != "reserved"]
    if enabled_only:
        rows = [row for row in rows if is_dataset_enabled(row)]
    return rows


def enabled_dataset_keys(*, group: str | None = None, include_reserved: bool = False) -> tuple[str, ...]:
    return tuple(spec.dataset_key for spec in list_datasets(group=group, include_reserved=include_reserved, enabled_only=True))


def find_dataset_by_key(dataset_key: str) -> DatasetSpec | None:
    return DATASET_REGISTRY.get(dataset_key)


def find_dataset_by_cli(cli_name: str, mode: str) -> DatasetSpec | None:
    attr = MODE_ATTR[mode]
    for spec in DATASET_REGISTRY.values():
        if getattr(spec, attr) == cli_name:
            return spec
    return None
