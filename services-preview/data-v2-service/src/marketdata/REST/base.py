"""REST 采集接口（插件协议）。"""
from __future__ import annotations

from typing import Protocol

from marketdata.types import UnionEvent


class RestCollector(Protocol):
    def collect_once(self) -> list[UnionEvent]: ...

