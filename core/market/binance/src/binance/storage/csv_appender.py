"""CSV 追加写入器（幂等/对齐 header）。

# 约束
# - 运行时落盘目录位于服务根：core/market/binance/var/hf/data/**
# - 对齐 官方历史归档源：需要 header（UM trades 有 header）
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, Sequence


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def append_csv_rows(path: Path, header: Sequence[str], rows: Iterable[Sequence[object]]) -> None:
    """追加写入 CSV。

    - 文件不存在：创建并写 header
    - 文件存在但为空：补写 header
    - 其他情况：直接 append
    """

    ensure_parent_dir(path)

    write_header = False
    if not path.exists():
        write_header = True
    else:
        try:
            if path.stat().st_size == 0:
                write_header = True
        except OSError:
            write_header = True

    with path.open("a", newline="") as f:
        writer = csv.writer(f)
        if write_header and header:
            writer.writerow(list(header))
        for row in rows:
            writer.writerow(list(row))
