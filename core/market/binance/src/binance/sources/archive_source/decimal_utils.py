"""Decimal 格式化工具。

# 目标
# - 输出尽量对齐 官方历史归档源 CSV 的数值表现（尤其是 quote_qty）
# - 不使用 float，避免精度漂移

# 官方历史归档源 样本事实（UM trades）：
# - quote_qty 会去掉尾随 0，但会保留至少 1 位小数，例如：1125.0、7028.0
"""

from __future__ import annotations

from decimal import Decimal


def format_decimal_for_archive_csv(value: Decimal) -> str:
    """格式化 Decimal：去掉尾随 0，但至少保留 1 位小数。

    示例：
    - 1125.0000 -> 1125.0
    - 7028.00   -> 7028.0
    - 492.0174  -> 492.0174
    - 0         -> 0.0
    """

    s = format(value, "f")
    if "." in s:
        s = s.rstrip("0")
        if s.endswith("."):
            s += "0"
        return s

    return s + ".0"
