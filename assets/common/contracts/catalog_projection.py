from __future__ import annotations

"""中央 contracts -> legacy catalog 兼容投影。

目标：
- `assets/contracts/*` 作为中央真相源
- `assets/catalog/*` 退为兼容投影
- 对于已迁入中央 contracts 的资源，legacy catalog 不再手写维护
"""

from copy import deepcopy
from pathlib import Path
import re
from typing import Any

from assets.common.contracts.semantic_contracts import (
    BINDINGS_PATH,
    CONTRACTS_ROOT,
    PROBES_PATH,
    PROJECT_ROOT,
    RESOURCES_PATH,
)


try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None


CATALOG_ROOT = PROJECT_ROOT / "assets" / "catalog"
LEGACY_RESOURCES_PATH = CATALOG_ROOT / "resources.v1.yaml"
LEGACY_DATA_SOURCES_PATH = CATALOG_ROOT / "data_sources.v1.yaml"

_SQL_TABLE_RE = re.compile(r"(?i)\bfrom\s+([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)\b")

_DATABASE_REF_TO_ENV = {
    "facts": "FACTS_DATABASE_URL",
    "derived": "DERIVED_DATABASE_URL",
}

RESOURCE_HEADER = """schema_version: "1.0"
catalog_id: "tradecat_resources"

# 注意：本文件已退为兼容期 projection。
# - 中央真相源：`assets/contracts/resources.v1.yaml`
# - 本文件只承接兼容期资源投影与 legacy-only 资源
# - 若条目已迁入中央 contracts，不允许继续在这里手写维护 canonical physical mapping
"""

DATA_SOURCES_HEADER = """schema_version: "1.0"
catalog_id: "tradecat_data_sources"

# 注意：本文件已退为兼容期 projection。
# - 中央真相源：`assets/contracts/probes.v1.yaml`
# - 本文件只承接兼容期 probe 投影与 legacy-only source
# - 若条目已迁入中央 contracts，不允许继续在这里手写维护 probe/query_hint 真相
"""


def _require_yaml() -> Any:
    if yaml is None:
        raise RuntimeError("missing_dep:PyYAML")
    return yaml


def _load_yaml(path: Path) -> Any:
    y = _require_yaml()
    return y.safe_load(path.read_text(encoding="utf-8", errors="ignore"))


def _safe_list(value: Any) -> list[Any]:
    return deepcopy(value) if isinstance(value, list) else []


def _safe_dict(value: Any) -> dict[str, Any]:
    return deepcopy(value) if isinstance(value, dict) else {}


def _load_doc(path: Path, key: str, catalog_id: str) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": "1.0", "catalog_id": catalog_id, key: []}
    data = _load_yaml(path)
    if not isinstance(data, dict):
        return {"schema_version": "1.0", "catalog_id": catalog_id, key: []}
    rows = data.get(key)
    if not isinstance(rows, list):
        rows = []
    return {
        "schema_version": str(data.get("schema_version") or "1.0"),
        "catalog_id": str(data.get("catalog_id") or catalog_id),
        key: [deepcopy(row) for row in rows if isinstance(row, dict)],
    }


def _qualified_table_from_physical(physical: dict[str, Any]) -> str | None:
    schema = str(physical.get("schema") or "").strip()
    object_name = str(physical.get("object_name") or "").strip()
    if not schema or not object_name:
        return None
    return f"{schema}.{object_name}"


def _query_hint_to_table(query_hint: str) -> str | None:
    text = str(query_hint or "").strip()
    if not text:
        return None
    match = _SQL_TABLE_RE.search(text)
    if not match:
        return None
    return f"{match.group(1)}.{match.group(2)}"


def _resource_projection_name(resource: dict[str, Any]) -> str:
    base = str(resource.get("name_zh") or "").strip()
    physical = _safe_dict(resource.get("physical"))
    qualified = _qualified_table_from_physical(physical)
    if base and qualified:
        return f"{base}（{qualified}）"
    return base or qualified or str(resource.get("resource_id") or "").strip()


def _default_query_hint(resource: dict[str, Any]) -> str | None:
    physical = _safe_dict(resource.get("physical"))
    qualified = _qualified_table_from_physical(physical)
    kind = str(physical.get("kind") or "").strip()
    if kind in {"postgres_table", "postgres_view"} and qualified:
        return f"SELECT COUNT(*) FROM {qualified};"
    return None


def _dsn_env_for_resource(resource: dict[str, Any], existing_probe: dict[str, Any] | None) -> str | None:
    if isinstance(existing_probe, dict):
        existing_env = str(existing_probe.get("dsn_env") or "").strip()
        if existing_env:
            return existing_env
    physical = _safe_dict(resource.get("physical"))
    database_ref = str(physical.get("database_ref") or "").strip().lower()
    return _DATABASE_REF_TO_ENV.get(database_ref)


def _build_projected_resource_row(resource: dict[str, Any], existing: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = deepcopy(existing) if isinstance(existing, dict) else {}
    for key, value in resource.items():
        if key == "physical":
            continue
        out[key] = deepcopy(value)

    out["name_zh"] = _resource_projection_name(resource)

    physical = _safe_dict(resource.get("physical"))
    existing_physical = _safe_dict(existing.get("physical")) if isinstance(existing, dict) else {}
    physical_out: dict[str, Any] = {}
    physical_out.update(existing_physical)
    physical_out.update(physical)

    kind = str(physical.get("kind") or physical_out.get("kind") or "").strip()
    physical_out["kind"] = kind
    dsn_env = _DATABASE_REF_TO_ENV.get(str(physical.get("database_ref") or "").strip().lower())
    if dsn_env:
        physical_out["database_env"] = dsn_env
    for obsolete_key in ("schema_env", "table_env", "view_env"):
        physical_out.pop(obsolete_key, None)
    out["physical"] = physical_out
    return out


def _probe_database_ref(probe: dict[str, Any], resource: dict[str, Any] | None) -> str | None:
    database_ref = str(probe.get("database_ref") or "").strip()
    if database_ref:
        return database_ref
    if isinstance(resource, dict):
        physical = _safe_dict(resource.get("physical"))
        resource_ref = str(physical.get("database_ref") or "").strip()
        if resource_ref:
            return resource_ref
    dsn_env = str(probe.get("dsn_env") or "").strip()
    if dsn_env in {"FACTS_DATABASE_URL", "DATA_SERVICE_DATABASE_URL", "BINANCE_HF_DATABASE_URL"}:
        return "facts"
    if dsn_env in {"DERIVED_DATABASE_URL", "QUERY_PG_INDICATORS_URL"}:
        return "derived"
    return None


def _build_contract_probe_row(
    contract_probe: dict[str, Any],
    existing_source: dict[str, Any] | None,
    resource: dict[str, Any] | None,
) -> dict[str, Any] | None:
    source_id = str(contract_probe.get("source_id") or "").strip()
    if not source_id:
        return None

    out: dict[str, Any] = deepcopy(existing_source) if isinstance(existing_source, dict) else {}
    for key in ("source_id", "resource_id", "name_zh", "impl_status", "ttl_s", "evidence"):
        if key in contract_probe:
            out[key] = deepcopy(contract_probe.get(key))

    if "name_zh" not in out and isinstance(resource, dict):
        out["name_zh"] = _resource_projection_name(resource)
    if "impl_status" not in out:
        out["impl_status"] = "scattered"
    if "ttl_s" not in out:
        out["ttl_s"] = 300

    probe_contract = _safe_dict(contract_probe.get("probe"))
    probe_existing = _safe_dict(existing_source.get("probe")) if isinstance(existing_source, dict) else {}
    probe_out: dict[str, Any] = {}
    probe_out.update(probe_existing)
    probe_out.update(probe_contract)

    if str(probe_out.get("kind") or "").strip() == "sql":
        database_ref = _probe_database_ref(probe_out, resource)
        if database_ref:
            probe_out["database_ref"] = database_ref
        query_hint = str(probe_out.get("query_hint") or "").strip()
        if not query_hint and isinstance(resource, dict):
            default_query_hint = _default_query_hint(resource)
            if default_query_hint:
                probe_out["query_hint"] = default_query_hint

    out["probe"] = probe_out
    return out


def build_resources_projection(
    contract_resources: list[dict[str, Any]],
    legacy_resources_doc: dict[str, Any],
) -> dict[str, Any]:
    legacy_rows = [deepcopy(row) for row in legacy_resources_doc.get("resources", []) if isinstance(row, dict)]
    by_resource_id = {
        str(row.get("resource_id") or "").strip(): idx for idx, row in enumerate(legacy_rows) if str(row.get("resource_id") or "").strip()
    }

    for resource in contract_resources:
        resource_id = str(resource.get("resource_id") or "").strip()
        if not resource_id:
            continue
        row = _build_projected_resource_row(resource, legacy_rows[by_resource_id[resource_id]] if resource_id in by_resource_id else None)
        if resource_id in by_resource_id:
            legacy_rows[by_resource_id[resource_id]] = row
        else:
            by_resource_id[resource_id] = len(legacy_rows)
            legacy_rows.append(row)

    return {"schema_version": "1.0", "catalog_id": "tradecat_resources", "resources": legacy_rows}


def build_data_sources_projection(
    contract_resources: list[dict[str, Any]],
    contract_probes: list[dict[str, Any]],
    legacy_sources_doc: dict[str, Any],
) -> dict[str, Any]:
    legacy_rows = [deepcopy(row) for row in legacy_sources_doc.get("sources", []) if isinstance(row, dict)]
    by_source_id = {
        str(row.get("source_id") or "").strip(): idx for idx, row in enumerate(legacy_rows) if str(row.get("source_id") or "").strip()
    }
    by_table: dict[str, dict[str, Any]] = {}
    for row in legacy_rows:
        probe = _safe_dict(row.get("probe"))
        table = _query_hint_to_table(str(probe.get("query_hint") or "").strip())
        if table:
            by_table[table] = row

    resource_by_id = {
        str(row.get("resource_id") or "").strip(): row for row in contract_resources if str(row.get("resource_id") or "").strip()
    }

    probes_by_resource = {
        str(row.get("resource_id") or "").strip(): row for row in contract_probes if str(row.get("resource_id") or "").strip()
    }

    for contract_probe in contract_probes:
        source_id = str(contract_probe.get("source_id") or "").strip()
        if not source_id:
            continue
        resource_id = str(contract_probe.get("resource_id") or "").strip()
        resource = resource_by_id.get(resource_id) if resource_id else None
        existing_source = legacy_rows[by_source_id[source_id]] if source_id in by_source_id else None
        row = _build_contract_probe_row(contract_probe, existing_source, resource)
        if row is None:
            continue
        if source_id in by_source_id:
            legacy_rows[by_source_id[source_id]] = row
        else:
            by_source_id[source_id] = len(legacy_rows)
            legacy_rows.append(row)

    for resource in contract_resources:
        physical = _safe_dict(resource.get("physical"))
        if str(physical.get("kind") or "").strip() not in {"postgres_table", "postgres_view"}:
            continue
        table = _qualified_table_from_physical(physical)
        if not table:
            continue
        contract_probe = probes_by_resource.get(str(resource.get("resource_id") or "").strip())
        if contract_probe is not None:
            continue
        existing_source = by_table.get(table)
        row = _build_contract_probe_row(
            {
                "source_id": str(existing_source.get("source_id") or "").strip()
                if isinstance(existing_source, dict)
                else f"contracts/{str(resource.get('resource_id') or '').replace('/', '_')}",
                "resource_id": str(resource.get("resource_id") or "").strip(),
                "name_zh": _resource_projection_name(resource),
                "impl_status": str(existing_source.get("impl_status") or "").strip() if isinstance(existing_source, dict) else "scattered",
                "ttl_s": int(existing_source.get("ttl_s")) if isinstance(existing_source, dict) and existing_source.get("ttl_s") is not None else 300,
                "probe": {
                    "kind": "sql",
                    "query_hint": _default_query_hint(resource),
                    "dsn_env": _dsn_env_for_resource(resource, _safe_dict(existing_source.get("probe")) if isinstance(existing_source, dict) else {}),
                    "note": str(_safe_dict(existing_source.get("probe")).get("note") or "").strip()
                    if isinstance(existing_source, dict)
                    else "兼容期 projection：canonical probe 由中央 contracts 派生。",
                },
                "evidence": _safe_list(resource.get("evidence")),
            },
            existing_source,
            resource,
        )
        if row is None:
            continue
        source_id = str(row.get("source_id") or "").strip()
        if source_id in by_source_id:
            legacy_rows[by_source_id[source_id]] = row
        else:
            by_source_id[source_id] = len(legacy_rows)
            legacy_rows.append(row)

    return {"schema_version": "1.0", "catalog_id": "tradecat_data_sources", "sources": legacy_rows}


def load_projection_inputs() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    resources_doc = _load_doc(RESOURCES_PATH, "resources", "tradecat_contract_resources")
    probes_doc = _load_doc(PROBES_PATH, "probes", "tradecat_contract_probes")
    legacy_resources_doc = _load_doc(LEGACY_RESOURCES_PATH, "resources", "tradecat_resources")
    legacy_sources_doc = _load_doc(LEGACY_DATA_SOURCES_PATH, "sources", "tradecat_data_sources")
    return (
        [deepcopy(row) for row in resources_doc["resources"]],
        [deepcopy(row) for row in probes_doc["probes"]],
        legacy_resources_doc,
        legacy_sources_doc,
    )


def render_yaml_document(doc: dict[str, Any], *, header: str) -> str:
    y = _require_yaml()

    class _Dumper(y.SafeDumper):
        pass

    def _represent_str(dumper: Any, value: str) -> Any:
        style = "|" if "\n" in value else None
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)

    _Dumper.add_representer(str, _represent_str)
    body = y.dump(doc, sort_keys=False, allow_unicode=True, Dumper=_Dumper).strip()
    if body.startswith("schema_version:"):
        parts = body.split("\n", 2)
        body = parts[2] if len(parts) == 3 else body
    return f"{header.strip()}\n\n{body}\n"


def current_projection_documents() -> tuple[dict[str, Any], dict[str, Any]]:
    _, _, legacy_resources_doc, legacy_sources_doc = load_projection_inputs()
    return legacy_resources_doc, legacy_sources_doc


def build_projection_documents() -> tuple[dict[str, Any], dict[str, Any]]:
    contract_resources, contract_probes, legacy_resources_doc, legacy_sources_doc = load_projection_inputs()
    resources_doc = build_resources_projection(contract_resources, legacy_resources_doc)
    data_sources_doc = build_data_sources_projection(contract_resources, contract_probes, legacy_sources_doc)
    return resources_doc, data_sources_doc


def write_projection_documents() -> None:
    resources_doc, data_sources_doc = build_projection_documents()
    LEGACY_RESOURCES_PATH.write_text(render_yaml_document(resources_doc, header=RESOURCE_HEADER), encoding="utf-8")
    LEGACY_DATA_SOURCES_PATH.write_text(render_yaml_document(data_sources_doc, header=DATA_SOURCES_HEADER), encoding="utf-8")


__all__ = [
    "BINDINGS_PATH",
    "CONTRACTS_ROOT",
    "LEGACY_DATA_SOURCES_PATH",
    "LEGACY_RESOURCES_PATH",
    "PROBES_PATH",
    "PROJECT_ROOT",
    "RESOURCES_PATH",
    "build_data_sources_projection",
    "build_projection_documents",
    "build_resources_projection",
    "current_projection_documents",
    "write_projection_documents",
]
