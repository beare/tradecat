"""WS 采集接口（插件协议）。"""
from __future__ import annotations

from typing import AsyncIterator, Protocol

from marketdata.types import UnionEvent


class WsCollector(Protocol):
    async def collect(self) -> AsyncIterator[UnionEvent]: ...

