#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""服务侧 canonical physical mapping 守护。

目标：
- 阻止 service runtime 继续裸写 alternative / derived 域 canonical physical mapping
- 允许中央 contracts / db_contracts 保留真相，服务侧只保留 resource_id 或 helper 绑定
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = [
    REPO_ROOT / "core" / "alternative",
    REPO_ROOT / "plugins" / "trading",
    REPO_ROOT / "plugins" / "signal",
    REPO_ROOT / "plugins" / "ai",
]
EXTENSIONS = {".py", ".sh", ".ts", ".tsx", ".js", ".jsx"}
SKIP_PARTS = {
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "dist",
    "build",
    "docs",
    "logs",
    "data",
    "configs",
    "tests",
}
ALLOWLIST = {
    "core/alternative/news/src/lib/contracts.ts",
}
PATTERNS = [
    re.compile(r'write_events_batch\([^\\n]*table\s*=\s*["\'][A-Za-z_]+["\']'),
    re.compile(
        r"\b(?:FROM|INTO|UPDATE|JOIN|TABLE IF NOT EXISTS|ON)\s+alternative\.(?:news|telegram|x|investing_calendar_snapshots|hyperliquid_address_snapshots)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:FROM|INTO|UPDATE|JOIN|TABLE IF NOT EXISTS|ON)\s+(?:market|indicator_snapshots|signal_runtime)\.[A-Za-z_][A-Za-z0-9_]*\b",
        re.IGNORECASE,
    ),
    re.compile(
        r'["\'](?:market|alternative|indicator_snapshots|signal_runtime)\.[A-Za-z_][A-Za-z0-9_]*["\']'
    ),
]


def _should_skip(path: Path) -> bool:
    rel = path.relative_to(REPO_ROOT).as_posix()
    if rel in ALLOWLIST:
        return True
    return any(part in rel.split("/") for part in SKIP_PARTS)


def _scan_file(path: Path) -> list[str]:
    if path.suffix not in EXTENSIONS:
        return []
    if _should_skip(path):
        return []
    text = path.read_text(encoding="utf-8", errors="ignore")
    hits: list[str] = []
    for idx, line in enumerate(text.splitlines(), start=1):
        candidate = line.strip()
        if not candidate:
            continue
        for pattern in PATTERNS:
            if pattern.search(candidate):
                hits.append(f"{path.relative_to(REPO_ROOT)}:{idx}: {candidate}")
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
        print("✗ 发现服务侧 canonical physical mapping 旁路：")
        for hit in findings[:200]:
            print(f"  - {hit}")
        if len(findings) > 200:
            print(f"  ... 其余 {len(findings) - 200} 条省略")
        return 1

    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
