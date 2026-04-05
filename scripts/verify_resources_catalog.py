#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
语义层资源目录 / 中央契约 resources 校验器。

目标：
- resource_id 唯一
- 必填字段齐全（最小可用语义模型）
- 兼容旧 catalog：物理映射中的 *_env 引用必须存在于 assets/config/.env.example
- 新中央 contracts：允许直接使用 schema/object_name 表达 canonical physical mapping
- 目录文件不允许出现明文密钥/Token/私钥/DSN 密码（只允许 env key 名）

退出码：
- 0: ok
- 1: 校验失败
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE_PATH = REPO_ROOT / "assets" / "config" / ".env.example"

RESOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_/\\-]*[a-z0-9]$")
ENV_KEY_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=")


try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None


@dataclass(frozen=True)
class Problem:
    message: str
    path_hint: str | None = None


def _load_yaml(path: Path) -> Any:
    if yaml is None:
        raise RuntimeError("缺少依赖：PyYAML（import yaml 失败）")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data


def _parse_env_keys(env_example_path: Path) -> set[str]:
    keys: set[str] = set()
    for line in env_example_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = ENV_KEY_RE.match(line)
        if m:
            keys.add(m.group(1))
    return keys


def _walk_env_refs(node: Any, current_path: str) -> list[tuple[str, str]]:
    """递归遍历，抽取所有 `*_env: SOME_KEY` 引用。返回 (path, env_key)。"""
    refs: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for k, v in node.items():
            next_path = f"{current_path}.{k}" if current_path else str(k)
            if isinstance(k, str) and k.endswith("_env"):
                if isinstance(v, str):
                    refs.append((next_path, v))
                else:
                    refs.append((next_path, "<non-string>"))
            refs.extend(_walk_env_refs(v, next_path))
    elif isinstance(node, list):
        for idx, item in enumerate(node):
            refs.extend(_walk_env_refs(item, f"{current_path}[{idx}]"))
    return refs


def _scan_for_secrets(text: str) -> list[str]:
    hits: list[str] = []
    patterns: list[tuple[str, str]] = [
        ("postgres_dsn", r"(?i)\b(postgresql|postgres)://"),
        ("mysql_dsn", r"(?i)\bmysql://"),
        ("mongo_dsn", r"(?i)\bmongodb(\+srv)?://"),
        ("redis_dsn", r"(?i)\bredis://"),
        ("amqp_dsn", r"(?i)\bamqp://"),
        ("private_key", r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
        ("openai_key", r"\bsk-[A-Za-z0-9]{16,}\b"),
        ("github_pat", r"\bghp_[A-Za-z0-9]{20,}\b"),
        ("slack_bot", r"\bxoxb-[0-9A-Za-z-]{10,}\b"),
    ]
    for name, pat in patterns:
        if re.search(pat, text):
            hits.append(name)
    return hits


def _require_str(obj: dict[str, Any], key: str, problems: list[Problem], obj_path: str) -> str | None:
    val = obj.get(key)
    if not isinstance(val, str) or not val.strip():
        problems.append(Problem(f"缺少或非法字段：{key}（必须为非空字符串）", path_hint=obj_path))
        return None
    return val


def _require_list_str(
    obj: dict[str, Any], key: str, problems: list[Problem], obj_path: str, *, min_len: int = 1
) -> list[str] | None:
    val = obj.get(key)
    if not isinstance(val, list) or len(val) < min_len or any((not isinstance(x, str) or not x.strip()) for x in val):
        problems.append(Problem(f"缺少或非法字段：{key}（必须为字符串数组，长度≥{min_len}）", path_hint=obj_path))
        return None
    return [x.strip() for x in val]


def _validate_resources(
    resources_path: Path,
    *,
    env_keys: set[str],
    min_resources: int | None,
    require_ids: list[str],
    check_unique: bool,
    check_no_secrets: bool,
    check_required_fields: bool,
    check_consistency: bool,
    data_sources_path: Path | None,
) -> list[Problem]:
    problems: list[Problem] = []

    if not resources_path.exists():
        return [Problem("resources 文件不存在", path_hint=str(resources_path))]

    text = resources_path.read_text(encoding="utf-8", errors="ignore")
    if check_no_secrets:
        hits = _scan_for_secrets(text)
        if hits:
            problems.append(Problem(f"resources 文件疑似包含敏感信息/DSN：{', '.join(sorted(set(hits)))}", path_hint=str(resources_path)))

    data = _load_yaml(resources_path)
    if not isinstance(data, dict):
        return [Problem("resources YAML 顶层必须为 object/dict", path_hint=str(resources_path))]

    schema_version = data.get("schema_version")
    if schema_version != "1.0":
        problems.append(Problem(f"schema_version 必须为 '1.0'（当前={schema_version!r}）", path_hint=str(resources_path)))

    resources = data.get("resources")
    if not isinstance(resources, list):
        return [Problem("顶层字段 resources 必须为数组", path_hint=str(resources_path))]

    if min_resources is not None and len(resources) < min_resources:
        problems.append(Problem(f"resources 数量不足：{len(resources)} < {min_resources}", path_hint=str(resources_path)))

    ids: list[str] = []
    for idx, res in enumerate(resources):
        res_path = f"resources[{idx}]"
        if not isinstance(res, dict):
            problems.append(Problem("资源条目必须为 object/dict", path_hint=res_path))
            continue

        rid = _require_str(res, "resource_id", problems, res_path)
        if rid:
            ids.append(rid)
            if not RESOURCE_ID_RE.match(rid):
                problems.append(Problem(f"resource_id 命名不合法：{rid!r}", path_hint=res_path))

        if check_required_fields:
            rtype = _require_str(res, "type", problems, res_path)
            if rtype and rtype not in {"dataset", "api", "job"}:
                problems.append(Problem(f"type 非法：{rtype!r}（必须为 dataset/api/job）", path_hint=res_path))
            _require_str(res, "name_zh", problems, res_path)
            _require_str(res, "layer", problems, res_path)
            _require_str(res, "domain", problems, res_path)
            sens = _require_str(res, "sensitivity", problems, res_path)
            if sens and sens not in {"public", "internal", "secret"}:
                problems.append(Problem(f"sensitivity 非法：{sens!r}（必须为 public/internal/secret）", path_hint=res_path))
            _require_list_str(res, "owners", problems, res_path, min_len=1)
            _require_list_str(res, "producers", problems, res_path, min_len=1)
            runtime_status = res.get("runtime_status")
            if runtime_status is not None and runtime_status not in {"active", "backfill_only", "reserved"}:
                problems.append(
                    Problem(
                        f"runtime_status 非法：{runtime_status!r}（必须为 active/backfill_only/reserved）",
                        path_hint=res_path,
                    )
                )
            phys = res.get("physical")
            if not isinstance(phys, dict):
                problems.append(Problem("缺少或非法字段：physical（必须为 object/dict）", path_hint=res_path))
            else:
                kind = phys.get("kind")
                if not isinstance(kind, str) or not kind.strip():
                    problems.append(Problem("physical.kind 必须为非空字符串", path_hint=f"{res_path}.physical"))
                if kind in {"postgres_table", "postgres_view"}:
                    schema_direct = phys.get("schema")
                    object_name_direct = phys.get("object_name")
                    schema_env = phys.get("schema_env")
                    table_env = phys.get("table_env") or phys.get("view_env")
                    has_direct = isinstance(schema_direct, str) and bool(schema_direct.strip()) and isinstance(
                        object_name_direct, str
                    ) and bool(object_name_direct.strip())
                    has_env = isinstance(schema_env, str) and bool(schema_env.strip()) and isinstance(
                        table_env, str
                    ) and bool(table_env.strip())
                    if not has_direct and not has_env:
                        problems.append(
                            Problem(
                                "postgres 物理映射必须提供 `schema+object_name` 或 `schema_env+table_env/view_env`",
                                path_hint=f"{res_path}.physical",
                            )
                        )

    if check_unique:
        seen: set[str] = set()
        dup: set[str] = set()
        for rid in ids:
            if rid in seen:
                dup.add(rid)
            seen.add(rid)
        if dup:
            problems.append(Problem(f"发现重复 resource_id：{', '.join(sorted(dup))}", path_hint=str(resources_path)))

    # env 引用检查：所有 *_env 值必须在 .env.example 中存在
    for ref_path, env_key in _walk_env_refs(data, ""):
        if env_key == "<non-string>":
            problems.append(Problem("env 引用必须为字符串（env key 名）", path_hint=ref_path))
            continue
        if env_key not in env_keys:
            problems.append(Problem(f"引用了不存在的 env key：{env_key}", path_hint=ref_path))

    # require 校验：必须存在指定 resource_id
    if require_ids:
        id_set = set(ids)
        missing = [r for r in require_ids if r not in id_set]
        if missing:
            problems.append(Problem(f"缺少 required resource_id：{', '.join(missing)}", path_hint=str(resources_path)))

    # 可选一致性检查（P1）：resources 中的 PG 表必须至少在 data_sources 的 sql probe 中可观测到
    if check_consistency:
        if data_sources_path is None:
            problems.append(Problem("启用 --check-consistency 时必须提供 data_sources.v1.yaml 路径", path_hint=str(resources_path)))
        else:
            ds_data = _load_yaml(data_sources_path)
            sql_tables: set[str] = set()
            if isinstance(ds_data, dict) and isinstance(ds_data.get("sources"), list):
                for s in ds_data["sources"]:
                    if not isinstance(s, dict):
                        continue
                    probe = s.get("probe")
                    if not isinstance(probe, dict) or probe.get("kind") != "sql":
                        continue
                    qh = probe.get("query_hint")
                    if not isinstance(qh, str):
                        continue
                    m = re.search(r"(?i)\bfrom\s+([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)\b", qh)
                    if m:
                        sql_tables.add(f"{m.group(1)}.{m.group(2)}")

            # 从 resources 里抽取物理表名（优先 direct mapping，其次 env key -> env.example 值）
            env_map: dict[str, str] = {}
            for line in ENV_EXAMPLE_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env_map[k] = v

            missing_tables: list[str] = []
            for res in data.get("resources", []):
                if not isinstance(res, dict) or res.get("type") != "dataset":
                    continue
                phys = res.get("physical")
                if not isinstance(phys, dict):
                    continue
                if phys.get("kind") not in {"postgres_table", "postgres_view"}:
                    continue
                schema = phys.get("schema")
                table = phys.get("object_name")
                if not isinstance(schema, str) or not schema.strip() or not isinstance(table, str) or not table.strip():
                    schema_env = phys.get("schema_env")
                    table_env = phys.get("table_env") or phys.get("view_env")
                    if not isinstance(schema_env, str) or not isinstance(table_env, str):
                        continue
                    schema = env_map.get(schema_env)
                    table = env_map.get(table_env)
                if not schema or not table:
                    continue
                fq = f"{schema}.{table}"
                if fq not in sql_tables:
                    rid = res.get("resource_id")
                    missing_tables.append(f"{rid} -> {fq}")
            if missing_tables:
                problems.append(
                    Problem(
                        "resources 中存在未被 data_sources.sql probe 覆盖的 PG 对象（可能导致观测与治理漂移）:\n  - "
                        + "\n  - ".join(missing_tables[:50]),
                        path_hint=str(resources_path),
                    )
                )

    return problems


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("resources_yaml", type=str, help="resources.v1.yaml 路径")
    p.add_argument("data_sources_yaml", type=str, nargs="?", help="data_sources.v1.yaml 路径（仅在 --check-consistency 时需要）")
    p.add_argument("--min-resources", type=int, default=None)
    p.add_argument("--require", dest="require_ids", action="append", default=[], help="必须存在的 resource_id（可多次传入）")
    p.add_argument("--check-unique", action="store_true", help="只做唯一性检查（仍会做 env/no-secrets 基础校验）")
    p.add_argument("--check-no-secrets", action="store_true", help="只做敏感信息扫描（仍会做 env/no-secrets 基础校验）")
    p.add_argument("--check-required-fields", action="store_true", help="只做必填字段检查（仍会做 env/no-secrets 基础校验）")
    p.add_argument("--check-consistency", action="store_true", help="检查与 data_sources.v1.yaml 的一致性（P1，可选）")
    args = p.parse_args()

    resources_path = (REPO_ROOT / args.resources_yaml).resolve() if not Path(args.resources_yaml).is_absolute() else Path(args.resources_yaml)
    data_sources_path = None
    if args.data_sources_yaml:
        data_sources_path = (
            (REPO_ROOT / args.data_sources_yaml).resolve()
            if not Path(args.data_sources_yaml).is_absolute()
            else Path(args.data_sources_yaml)
        )

    if not ENV_EXAMPLE_PATH.exists():
        print(f"❌ 缺少 .env.example：{ENV_EXAMPLE_PATH}")
        return 1

    env_keys = _parse_env_keys(ENV_EXAMPLE_PATH)

    # 默认：全量校验（唯一性/必填字段/敏感信息/env 引用）
    only_unique = bool(args.check_unique)
    only_no_secrets = bool(args.check_no_secrets)
    only_required_fields = bool(args.check_required_fields)
    if any((only_unique, only_no_secrets, only_required_fields)):
        check_unique = only_unique
        check_no_secrets = only_no_secrets
        check_required_fields = only_required_fields
    else:
        check_unique = True
        check_no_secrets = True
        check_required_fields = True

    problems = _validate_resources(
        resources_path,
        env_keys=env_keys,
        min_resources=args.min_resources,
        require_ids=list(args.require_ids or []),
        check_unique=check_unique,
        check_no_secrets=check_no_secrets,
        check_required_fields=check_required_fields,
        check_consistency=bool(args.check_consistency),
        data_sources_path=data_sources_path,
    )

    if problems:
        print("❌ resources 校验失败：")
        for pr in problems:
            if pr.path_hint:
                print(f"  - {pr.path_hint}: {pr.message}")
            else:
                print(f"  - {pr.message}")
        return 1

    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
