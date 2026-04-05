from __future__ import annotations

import base64
import gzip
import json

from src.api_table import build_payload_from_sheet_values, gzip_base64_json


def test_gzip_base64_json_roundtrip() -> None:
    obj = {"a": 1, "b": "中文", "c": [1, 2, 3], "d": {"x": "y"}}
    wrap = gzip_base64_json(obj)
    assert wrap["encoding"] == "gzip_base64"
    raw = gzip.decompress(base64.b64decode(wrap["gzip_b64"]))
    got = json.loads(raw.decode("utf-8"))
    assert got == obj


def test_build_payload_symbol_query_facts() -> None:
    # 模拟公开表结构：banner/meta/header/data
    values = [
        ["广告位\\nline2"],
        ["币种，SOL，导出时间(UTC+8)，2026-03-08T18:04:51+08:00，语言，zh_CN"],
        ["面板", "指标组", "指标", "5m", "15m"],
        ["📊 基础指标", "布林带扫描器", "带宽", "0.93", "1.53"],
        ["", "", "%B", "0.74", "0.80"],
    ]
    schema, payload = build_payload_from_sheet_values(values)
    assert schema == "symbol_query_v2"
    assert payload["facts_schema"] == "symbol_query_v2"
    assert payload["symbol"] == "SOLUSDT"
    assert payload["base_symbol"] == "SOL"
    assert payload["fact_count"] == 2
    assert payload["facts"][0]["dims"]["metric"] == "带宽"
    assert payload["facts"][1]["dims"]["metric"] == "%B"
    # carry-forward panel/group
    assert payload["facts"][1]["dims"]["panel"] == "📊 基础指标"
    assert payload["facts"][1]["dims"]["group"] == "布林带扫描器"
