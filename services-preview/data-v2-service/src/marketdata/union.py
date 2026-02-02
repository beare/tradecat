"""全字段并集：定义 + 观测 + 合并策略（唯一真相源）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Set

from marketdata.types import UnionEvent


def _flatten_keys(obj: Any, prefix: str = "", out: Set[str] | None = None) -> Set[str]:
    """将 dict 的 key 递归展开成 key-path（用于构造字段并集）。"""
    if out is None:
        out = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            out.add(key)
            _flatten_keys(v, key, out)
    return out


@dataclass
class UnionBuilder:
    """运行时观测字段并集：按 kind 维护 key-path 集合。"""

    observed: Dict[str, Set[str]] = field(default_factory=dict)

    def observe(self, e: UnionEvent) -> None:
        keys = _flatten_keys(e.fields)
        bucket = self.observed.setdefault(e.kind, set())
        bucket.update(keys)

    def summary(self) -> Dict[str, int]:
        return {k: len(v) for k, v in sorted(self.observed.items(), key=lambda kv: kv[0])}

    def keys(self, kind: str) -> list[str]:
        return sorted(self.observed.get(kind, set()))


def to_union_event(*, transport: str, tool: str, exchange: str, symbol: str, kind: str, raw: Any) -> UnionEvent:
    """
    将 raw 事件归一化为 UnionEvent。

    约定：
    - fields 尽量保留 ccxt 原始结构的 key（不做强行统一），目标是“并集”而非“规范化”。
    - raw 保留原始对象，便于后续补齐/回放。
    """
    fields: dict[str, Any]
    if isinstance(raw, dict):
        fields = dict(raw)
    else:
        fields = {"value": raw}
    return UnionEvent(
        transport=transport, tool=tool, exchange=exchange, symbol=symbol, kind=kind, fields=fields, raw=raw
    )

