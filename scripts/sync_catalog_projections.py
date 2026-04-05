#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""同步 legacy catalog 兼容投影。

用途：
- 从 `assets/contracts/*` 生成 `assets/catalog/*` 的兼容投影
- `--check` 模式下用于 verify 守护，阻止中央 contracts 与 legacy projection 漂移
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from assets.common.contracts.catalog_projection import (
    LEGACY_DATA_SOURCES_PATH,
    LEGACY_RESOURCES_PATH,
    build_projection_documents,
    current_projection_documents,
    write_projection_documents,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="同步或校验 legacy catalog 兼容投影")
    parser.add_argument("--check", action="store_true", help="只校验，不写文件")
    args = parser.parse_args()

    projected_resources, projected_sources = build_projection_documents()

    if args.check:
        current_resources, current_sources = current_projection_documents()
        if current_resources != projected_resources or current_sources != projected_sources:
            print("✗ legacy catalog projection 已漂移，请执行：python3 scripts/sync_catalog_projections.py")
            if current_resources != projected_resources:
                print(f"  - resources drift: {LEGACY_RESOURCES_PATH}")
            if current_sources != projected_sources:
                print(f"  - data_sources drift: {LEGACY_DATA_SOURCES_PATH}")
            return 1
        print("ok")
        return 0

    write_projection_documents()
    print(f"ok: synced {LEGACY_RESOURCES_PATH}")
    print(f"ok: synced {LEGACY_DATA_SOURCES_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
