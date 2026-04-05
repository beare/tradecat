#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""血缘 resource_id 使用守护（CI/verify 门禁）。

目标：
- 任何调用 `emit_lineage_event(... resource_id=...)` 或定义 `LINEAGE_RESOURCE_*` 的代码文件中，
  出现的 resource_id 字面量必须存在于 `assets/contracts/resources.v1.yaml`。

说明：
- 只校验“血缘写入侧”文件，避免对其它文档/示例造成误伤。
- 对动态拼接的 resource_id（非字面量）不做强行推断；但一旦出现字面量就必须合法。

退出码：
- 0：通过
- 1：发现未知 resource_id 或 catalog 解析失败
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_RESOURCES_PATH = REPO_ROOT / "assets" / "contracts" / "resources.v1.yaml"

# 只关心血缘侧：出现这些关键字的文件才进入校验
CANDIDATE_MARKERS = (
    "emit_lineage_event",
    "LINEAGE_RESOURCE",
)

# 允许的 resource_id 前缀（和 resources.v1.yaml 的用途对齐）
RID_PREFIX_RE = re.compile(r"^(facts|derived|api|archive)/")

# 捕获资源 ID 字面量（单/双引号）
RID_LITERAL_RE = re.compile(
    r"(?P<q>['\"])(?P<rid>(?:facts|derived|api|archive)/[a-z0-9][a-z0-9_/\\-]*[a-z0-9])(?P=q)"
)


try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None


@dataclass(frozen=True)
class Hit:
    path: Path
    line_no: int
    rid: str


def _load_catalog_ids(path: Path) -> set[str]:
    if yaml is None:
        raise RuntimeError("缺少依赖：PyYAML（import yaml 失败）")
    if not path.exists():
        raise RuntimeError(f"resources catalog 不存在: {path}")

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("resources.v1.yaml 顶层必须为 dict")

    resources = data.get("resources")
    if not isinstance(resources, list):
        raise RuntimeError("resources.v1.yaml 顶层字段 resources 必须为 list")

    ids: set[str] = set()
    for res in resources:
        if not isinstance(res, dict):
            continue
        rid = res.get("resource_id")
        if isinstance(rid, str) and rid.strip():
            ids.add(rid.strip())
    return ids


def _iter_code_files(root: Path) -> Iterable[Path]:
    # 只扫常见源码文件
    exts = {".py", ".ts", ".js", ".mjs", ".cjs"}

    def _skip_dir(p: Path) -> bool:
        parts = set(p.parts)
        if ".venv" in parts or "node_modules" in parts or "__pycache__" in parts:
            return True
        # 外部/镜像仓库不纳入主链门禁
        if "TradingAgents-main" in parts or "nofx-dev" in parts:
            return True
        if "assets" in parts and "repo" in parts:
            return True
        return False

    for path in root.rglob("*"):
        if path.is_dir():
            if _skip_dir(path):
                # prune by skipping traversal would be better, but rglob 无法直接 prune；靠过滤即可
                continue
            continue

        if path.suffix not in exts:
            continue
        if _skip_dir(path.parent):
            continue
        yield path


def _is_candidate_file(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False
    return any(m in text for m in CANDIDATE_MARKERS)


def _scan_file(path: Path) -> list[Hit]:
    hits: list[Hit] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return hits

    for idx, line in enumerate(lines, start=1):
        for m in RID_LITERAL_RE.finditer(line):
            rid = (m.group("rid") or "").strip()
            if rid and RID_PREFIX_RE.match(rid):
                hits.append(Hit(path=path, line_no=idx, rid=rid))
    return hits


def main() -> int:
    try:
        known = _load_catalog_ids(CONTRACT_RESOURCES_PATH)
    except Exception as exc:
        print(f"❌ 无法加载 resources catalog: {type(exc).__name__}: {exc}")
        return 1

    all_hits: list[Hit] = []
    for f in _iter_code_files(REPO_ROOT):
        if not _is_candidate_file(f):
            continue
        all_hits.extend(_scan_file(f))

    unknown = [h for h in all_hits if h.rid not in known]
    if unknown:
        print("❌ 发现未登记到 resources.v1.yaml 的 lineage resource_id：")
        for h in unknown[:200]:
            rel = h.path.relative_to(REPO_ROOT)
            print(f"  - {rel}:{h.line_no}: {h.rid}")
        if len(unknown) > 200:
            print(f"  ... 其余 {len(unknown) - 200} 条已省略")
        return 1

    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
