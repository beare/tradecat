from __future__ import annotations

from marketdata.types import UnionEvent
from marketdata.union import UnionBuilder


def test_union_builder_collects_keys():
    b = UnionBuilder()
    e1 = UnionEvent(
        transport="REST",
        tool="ccxt",
        exchange="binanceusdm",
        symbol="BTC/USDT:USDT",
        kind="ticker",
        fields={"a": 1, "b": {"c": 2}},
        raw={"a": 1, "b": {"c": 2}},
    )
    e2 = UnionEvent(
        transport="WS",
        tool="ccxt",
        exchange="binanceusdm",
        symbol="BTC/USDT:USDT",
        kind="ticker",
        fields={"d": 3},
        raw={"d": 3},
    )
    b.observe(e1)
    b.observe(e2)
    assert b.summary()["ticker"] >= 3
    keys = b.keys("ticker")
    assert "a" in keys
    assert "b" in keys
    assert "b.c" in keys
    assert "d" in keys

