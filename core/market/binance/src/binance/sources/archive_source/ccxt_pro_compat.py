"""ccxt.pro 运行时兼容补丁（最小侵入）。

背景（P0）：
- 当前运行环境 aiohttp=3.13+ 时，ccxt.pro 的 FastClient 会尝试 monkeypatch aiohttp websocket parser 的 parse_frame，
  但 aiohttp 3.13 的实现已不再暴露该属性，导致 WebSocket 直接崩溃。

策略：
- 若检测到 parse_frame 不存在，则让 FastClient 退化为标准 receive_loop（不走快路径 patch）。
- 这是“止血”级别补丁：不改依赖版本、不引入新依赖。
"""

from __future__ import annotations


def patch_ccxt_fast_client_for_aiohttp_313() -> bool:
    try:
        from ccxt.async_support.base.ws.client import Client as WsClient  # type: ignore
        from ccxt.async_support.base.ws.fast_client import FastClient  # type: ignore
    except Exception:
        return False

    original = getattr(FastClient, "receive_loop", None)
    if original is None:
        return False

    def receive_loop(self):  # type: ignore[no-untyped-def]
        try:
            connection = self.connection._conn  # noqa: SLF001
            ws_reader = connection.protocol._payload_parser  # noqa: SLF001
            if not hasattr(ws_reader, "parse_frame"):
                return WsClient.receive_loop(self)
        except Exception:
            return WsClient.receive_loop(self)
        return original(self)

    FastClient.receive_loop = receive_loop  # type: ignore[assignment]
    return True


__all__ = ["patch_ccxt_fast_client_for_aiohttp_313"]

