"""数据结构：Raw/Union 的最小封装。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Transport = Literal["REST", "WS"]


@dataclass(frozen=True, slots=True)
class RawEvent:
    """采集到的原始事件（尽量无损保留）。"""

    transport: Transport
    tool: str
    exchange: str
    symbol: str
    kind: str
    raw: Any


@dataclass(frozen=True, slots=True)
class UnionEvent:
    """归一化后的事件：fields 是“并集字段空间”的一个样本。"""

    transport: Transport
    tool: str
    exchange: str
    symbol: str
    kind: str
    fields: dict[str, Any]
    raw: Any

