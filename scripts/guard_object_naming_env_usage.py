#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对象命名 env 使用守护。

目标：
- 阻止新的运行时代码继续裸读 `DB_TABLE_*` / `DB_SCHEMA_*`
- 允许极少数迁移期兼容入口暂时存在，但必须显式列入 allowlist

说明：
- 本守护只检查“代码里的直接读取”，不检查 `.env.example` 本身。
- 当前中央真相源目标是 `assets/contracts/*`；`.env` 中对象命名键只允许作为兼容层存在。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = [
    REPO_ROOT / "core",
    REPO_ROOT / "plugins",
    REPO_ROOT / "scripts",
]

PATTERNS = [
    re.compile(r'os\.getenv\("DB_[A-Z0-9_]+"\)'),
    re.compile(r"process\.env\.DB_[A-Z0-9_]+"),
]

ALLOWED_PATHS = {
    "assets/common/contracts/db_contracts.py",
    "core/market/binance/tests/test_db_contracts_market_surface.py",
    "core/query/tests/test_catalog_resources_v1.py",
}

SKIP_PARTS = {
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "dist",
    "build",
    "libs/external",
}

EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx"}


def _should_skip(path: Path) -> bool:
    rel = path.relative_to(REPO_ROOT).as_posix()
    if rel in ALLOWED_PATHS:
        return True
    return any(part in rel for part in SKIP_PARTS)


def _scan_file(path: Path) -> list[str]:
    if path.suffix not in EXTENSIONS:
        return []
    if _should_skip(path):
        return []
    text = path.read_text(encoding="utf-8", errors="ignore")
    hits: list[str] = []
    for idx, line in enumerate(text.splitlines(), start=1):
        if "DB_" not in line:
            continue
        for pattern in PATTERNS:
            if pattern.search(line):
                hits.append(f"{path.relative_to(REPO_ROOT)}:{idx}: {line.strip()}")
                break
    return hits


def main() -> int:
    findings: list[str] = []
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            findings.extend(_scan_file(path))

    if findings:
        print("✗ 发现未经允许的对象命名 env 直接读取：")
        for hit in findings[:200]:
            print(f"  - {hit}")
        if len(findings) > 200:
            print(f"  ... 其余 {len(findings) - 200} 条省略")
        return 1

    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
