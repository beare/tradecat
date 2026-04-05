from __future__ import annotations

"""中央契约读取器。

说明：
- `assets/contracts/*` 是唯一运行时真相源。
- `assets/catalog/*` 仅保留导出/兼容投影，不再参与运行时解析。
- 本模块只负责读取中央契约，不负责兜底 `.env` 或旧 catalog。
- 读取失败仍保持 import-safe；真正依赖中央契约的调用方应在缺失时显式失败。
"""

from functools import lru_cache
from pathlib import Path
from typing import Any


try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None


PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS_ROOT = PROJECT_ROOT / "assets" / "contracts"
RESOURCES_PATH = CONTRACTS_ROOT / "resources.v1.yaml"
PROBES_PATH = CONTRACTS_ROOT / "probes.v1.yaml"
BINDINGS_PATH = CONTRACTS_ROOT / "bindings.v1.yaml"


def _load_yaml(path: Path) -> Any:
    if yaml is None:
        raise RuntimeError("missing_dep:PyYAML")
    return yaml.safe_load(path.read_text(encoding="utf-8", errors="ignore"))


def _load_contract_list(path: Path, key: str) -> list[dict[str, Any]]:
    if not path.exists() or yaml is None:
        return []
    data = _load_yaml(path)
    if not isinstance(data, dict):
        return []
    rows = data.get(key)
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


@lru_cache(maxsize=1)
def load_contract_resources() -> list[dict[str, Any]]:
    return _load_contract_list(RESOURCES_PATH, "resources")


@lru_cache(maxsize=1)
def load_contract_probes() -> list[dict[str, Any]]:
    return _load_contract_list(PROBES_PATH, "probes")


@lru_cache(maxsize=1)
def load_contract_bindings() -> list[dict[str, Any]]:
    return _load_contract_list(BINDINGS_PATH, "bindings")


def get_contract_resource(resource_id: str) -> dict[str, Any] | None:
    normalized = str(resource_id or "").strip()
    if not normalized:
        return None
    for resource in load_contract_resources():
        if str(resource.get("resource_id") or "").strip() == normalized:
            return resource
    return None


def get_contract_physical(resource_id: str) -> dict[str, Any] | None:
    resource = get_contract_resource(resource_id)
    if not isinstance(resource, dict):
        return None
    physical = resource.get("physical")
    if not isinstance(physical, dict):
        return None
    return physical
