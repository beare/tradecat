from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from src.public_meta import format_public_meta_row_text


# ==================== schema v1（对齐 CoinGlass 风格 envelope） ====================


def _now_iso_local() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _banner_text_from_env() -> str:
    raw = (os.environ.get("SHEETS_TOP_BANNER_TEXT", "") or "").strip()
    if raw:
        return raw
    return (os.environ.get("SHEETS_DASHBOARD_BANNER_TEXT", "") or "").strip()


def _banner_lines(text: str) -> list[str]:
    s = (text or "").strip()
    if not s:
        return []
    s = s.replace("\\n", "\n").replace("\r\n", "\n").replace("\r", "\n")
    return [ln.strip() for ln in s.split("\n") if ln.strip()]


def _canonical_json_bytes(obj: object) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def gzip_base64_json(obj: object) -> dict[str, Any]:
    raw = _canonical_json_bytes(obj)
    sha = hashlib.sha256(raw).hexdigest()
    comp = gzip.compress(raw, compresslevel=9)
    b64 = base64.b64encode(comp).decode("ascii")
    return {"encoding": "gzip_base64", "gzip_b64": b64, "raw_sha256": sha, "raw_bytes": len(raw)}


@dataclass(frozen=True)
class SheetRef:
    spreadsheet_id: str
    gid: int
    title: str


def build_envelope_cell_json(
    *,
    sheet: SheetRef,
    banner_text: str,
    payload_obj: dict[str, Any],
    payload_schema: str,
    lang: str,
    max_chars: int | None = None,
    truncated: bool = False,
    truncate_reason: str = "",
) -> str:
    """
    返回：可直接写入单元格的“单行 JSON”（外层对齐 CoinGlass 风格）。

    设计原则：
    - data.banner 必选，并尽量保持为 data 的第一个 key（人眼阅读稳定）
    - payload 默认 gzip+base64：单格可承载“全量”结构化数据
    """
    if max_chars is None:
        try:
            max_chars = int(os.environ.get("SHEETS_API_MAX_CHARS", "48000") or "48000")
        except Exception:
            max_chars = 48000

    banner_lines = _banner_lines(banner_text)
    payload_wrap = {"format": "facts", "facts_schema": payload_schema, **gzip_base64_json(payload_obj)}

    data: dict[str, Any] = {}
    data["banner"] = {
        "text": "\\n".join(banner_lines),
        "lines": banner_lines,
        "source": "SHEETS_TOP_BANNER_TEXT|SHEETS_DASHBOARD_BANNER_TEXT",
    }
    data["meta"] = {
        "schema_version": 1,
        "generated_at": _now_iso_local(),
        "producer": "tradecat.sheets-service",
        "lang": (lang or "zh_CN").strip() or "zh_CN",
    }
    data["sheet"] = {"spreadsheet_id": sheet.spreadsheet_id, "gid": int(sheet.gid), "title": sheet.title}
    data["payload"] = payload_wrap
    data["integrity"] = {"payload_sha256": payload_wrap["raw_sha256"], "payload_bytes": payload_wrap["raw_bytes"]}

    limits: dict[str, Any] = {"max_chars": int(max_chars), "truncated": bool(truncated)}
    if truncated and truncate_reason:
        limits["reason"] = str(truncate_reason)[:120]
    data["limits"] = limits

    envelope = {"code": "0", "msg": "success", "data": data, "success": True}
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))


def build_error_envelope_cell_json(*, sheet: SheetRef, banner_text: str, lang: str, error: str) -> str:
    banner_lines = _banner_lines(banner_text)
    data: dict[str, Any] = {}
    data["banner"] = {
        "text": "\\n".join(banner_lines),
        "lines": banner_lines,
        "source": "SHEETS_TOP_BANNER_TEXT|SHEETS_DASHBOARD_BANNER_TEXT",
    }
    data["meta"] = {
        "schema_version": 1,
        "generated_at": _now_iso_local(),
        "producer": "tradecat.sheets-service",
        "lang": (lang or "zh_CN").strip() or "zh_CN",
    }
    data["sheet"] = {"spreadsheet_id": sheet.spreadsheet_id, "gid": int(sheet.gid), "title": sheet.title}
    data["payload"] = {"format": "error", "error": str(error)[:2000]}
    envelope = {"code": "1", "msg": "error", "data": data, "success": False}
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))


# ==================== exporters: values -> facts（结构化，适合 AI） ====================


_NUM_SUFFIX = {"K": 1_000.0, "M": 1_000_000.0, "B": 1_000_000_000.0, "T": 1_000_000_000_000.0}
_DEFAULT_NULL_MARKERS = {"-", "/", "\\", "／", "＼", "—", "–", "NA", "N/A"}
_NULL_MARKERS_CACHE_RAW: str | None = None
_NULL_MARKERS_CACHE: tuple[set[str], set[str]] = (set(_DEFAULT_NULL_MARKERS), {m.upper() for m in _DEFAULT_NULL_MARKERS})


def _null_marker_sets() -> tuple[set[str], set[str]]:
    """
    返回 (markers, markers_upper)，用于把常见占位符视为 None -> JSON null。
    - markers_upper 用于大小写不敏感匹配（如 'na'/'N/A'）
    """
    global _NULL_MARKERS_CACHE_RAW
    global _NULL_MARKERS_CACHE

    raw = (os.environ.get("SHEETS_API_NULL_MARKERS", "") or "").strip()
    if raw == (_NULL_MARKERS_CACHE_RAW or ""):
        return _NULL_MARKERS_CACHE

    if not raw:
        markers = set(_DEFAULT_NULL_MARKERS)
    else:
        s = raw.replace("，", ",")
        parts = [p.strip() for p in s.split(",") if p.strip()]
        markers = {str(p) for p in parts} or set(_DEFAULT_NULL_MARKERS)

    markers_upper = {m.upper() for m in markers}
    _NULL_MARKERS_CACHE_RAW = raw
    _NULL_MARKERS_CACHE = (markers, markers_upper)
    return _NULL_MARKERS_CACHE


def _is_null_marker(v: object) -> bool:
    if v is None:
        return True
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return False
    s = str(v).strip()
    if not s:
        return True
    markers, markers_upper = _null_marker_sets()
    if s in markers:
        return True
    return s.upper() in markers_upper


def _coerce_text(v: object) -> str | None:
    """
    将单元格值规范为文本（或空值 None）。
    - 空白 / 常见占位符（如 '-', '/', '—'）统一视为 None -> JSON null
    """
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
    else:
        s = str(v).strip()
    if _is_null_marker(s):
        return None
    return s


def _coerce_number(v: object) -> float | int | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return v
    if not isinstance(v, str):
        return None

    s = v.strip()
    if _is_null_marker(s):
        return None

    sign = 1.0
    if s[0] == "+":
        s = s[1:].strip()
    elif s[0] == "-":
        sign = -1.0
        s = s[1:].strip()

    is_percent = s.endswith("%")
    if is_percent:
        s = s[:-1].strip()

    mult = 1.0
    if s and s[-1].upper() in _NUM_SUFFIX:
        mult = _NUM_SUFFIX[s[-1].upper()]
        s = s[:-1].strip()

    s = s.replace(",", "")
    try:
        x = float(s)
    except Exception:
        return None

    x = sign * x * mult
    if is_percent:
        return int(x) if float(int(x)) == x else x
    return int(x) if float(int(x)) == x else x


def _parse_kv_csv_zh(line: str) -> dict[str, str]:
    raw = str(line or "").replace("，", ",")
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    out: dict[str, str] = {}
    for i in range(0, len(parts) - 1, 2):
        out[str(parts[i])] = str(parts[i + 1])
    return out


def _parse_list_csv_zh(raw: str) -> list[str]:
    s = str(raw or "").replace("，", ",")
    return [p.strip() for p in s.split(",") if p.strip()]


def _match_title_patterns(title: str, patterns: list[str]) -> bool:
    """
    patterns:
    - 精确匹配：'币种查询_BTCUSDT'
    - 前缀通配：'看板_*'（只支持末尾 '*'）
    """
    t = str(title or "").strip()
    if not t:
        return False
    for p0 in patterns:
        p = str(p0 or "").strip()
        if not p:
            continue
        if p.endswith("*"):
            if t.startswith(p[:-1]):
                return True
            continue
        if t == p:
            return True
    return False


def _find_header_row(values: list[list[Any]]) -> int | None:
    for i, row in enumerate(values):
        cells = [str(c or "").strip() for c in (row or [])]
        if cells[:3] == ["面板", "指标组", "指标"]:
            return i

    # generic 表：尽量选择“看起来像表头”的行
    # - 常见形态：row0 是 banner（单格长文本/合并单元格），row1 才是真正 header
    # - 策略：在前 N 行里找“非空列最多”的那一行；若都 <=1，则回退到第一个非空行
    best_i: int | None = None
    best_score = -1
    n = min(len(values), 30)
    for i in range(0, n):
        row = values[i] or []
        score = sum(1 for c in row if _coerce_text(c) is not None)
        if score > best_score:
            best_score = score
            best_i = i
    if best_i is not None and best_score >= 2:
        return best_i

    for i, row in enumerate(values):
        if any(_coerce_text(c) is not None for c in (row or [])):
            return i
    return None


def export_symbol_query_facts_from_values(values: list[list[Any]]) -> dict[str, Any] | None:
    """
    识别并导出“币种查询”facts（长表）。

    期望结构（与公开表一致）：
    - row0: banner（可选）
    - row1: meta（含 “币种，SOL，导出时间...”）
    - row2: header: 面板/指标组/指标/5m/15m/...
    - row3+: data rows（面板/指标组允许空，沿用上一行）
    """
    if not values or len(values) < 3:
        return None

    header_idx = _find_header_row(values)
    if header_idx is None:
        return None
    header = [str(c or "").strip() for c in (values[header_idx] or [])]
    if len(header) < 4 or header[:3] != ["面板", "指标组", "指标"]:
        return None

    meta_row = values[header_idx - 1][0] if header_idx >= 1 and values[header_idx - 1] else ""
    kv = _parse_kv_csv_zh(str(meta_row))
    raw_base = (kv.get("币种") or kv.get("symbol") or "").strip().upper()
    export_time = (kv.get("导出时间(UTC+8)") or kv.get("导出时间") or "").strip()
    lang = (kv.get("语言") or "zh_CN").strip() or "zh_CN"

    base_symbol = raw_base.replace("USDT", "") if raw_base else ""
    symbol = raw_base if raw_base.endswith("USDT") else ((base_symbol + "USDT") if base_symbol else "")

    periods = [c for c in header[3:] if c]
    facts: list[dict[str, Any]] = []

    last_panel = ""
    last_group = ""
    for row in values[header_idx + 1 :]:
        if not row:
            continue
        panel = _coerce_text(row[0] if len(row) > 0 else None)
        group = _coerce_text(row[1] if len(row) > 1 else None)
        metric = _coerce_text(row[2] if len(row) > 2 else None)
        if panel:
            last_panel = panel
        else:
            panel = last_panel
        if group:
            last_group = group
        else:
            group = last_group
        if not metric:
            continue

        values_text: dict[str, str | None] = {}
        values_num: dict[str, float | int | None] = {}
        for j, p in enumerate(periods):
            col_i = 3 + j
            vt = _coerce_text(row[col_i] if col_i < len(row) else None)
            values_text[p] = vt
            values_num[p] = _coerce_number(vt) if vt is not None else None

        facts.append(
            {
                "dims": {
                    "symbol": symbol,
                    "base_symbol": base_symbol,
                    "panel": panel,
                    "group": group,
                    "metric": metric,
                },
                "values_text": values_text,
                "values_num": values_num,
            }
        )

    return {
        "format": "facts",
        "facts_schema": "symbol_query_v2",
        "symbol": symbol,
        "base_symbol": base_symbol,
        "export_time": export_time,
        "lang": lang,
        "periods": periods,
        "facts": facts,
        "fact_count": len(facts),
    }


def export_table_rows_facts_from_values(values: list[list[Any]]) -> dict[str, Any]:
    """
    兜底：把任意二维表格导出为“按行”的 facts。
    - header: 第一行（或第一个非空行）
    - facts: 每行一个对象：fields_text/fields_num（按列名）
    """
    header_idx = _find_header_row(values)
    header_idx = 0 if header_idx is None else int(header_idx)
    header = [str(c or "").strip() for c in (values[header_idx] or [])]
    header = [h if h else f"col_{i+1}" for i, h in enumerate(header)]

    facts: list[dict[str, Any]] = []
    for i, row in enumerate(values[header_idx + 1 :], start=header_idx + 2):
        if not row:
            continue
        # NOTE: 以“规范化后的有效值”判断空行（把 '-', '/' 这种占位符视为无值）
        if not any(_coerce_text(c) is not None for c in row):
            continue
        fields_text: dict[str, str | None] = {}
        fields_num: dict[str, float | int | None] = {}
        for j, col in enumerate(header):
            vt = _coerce_text(row[j] if j < len(row) else None)
            fields_text[col] = vt
            fields_num[col] = _coerce_number(vt) if vt is not None else None
        facts.append({"dims": {"row": i}, "fields_text": fields_text, "fields_num": fields_num})

    return {"format": "facts", "facts_schema": "table_rows_v2", "header": header, "facts": facts, "fact_count": len(facts)}


def build_payload_from_sheet_values(values: list[list[Any]]) -> tuple[str, dict[str, Any]]:
    sq = export_symbol_query_facts_from_values(values)
    if sq is not None:
        schema = str(sq.get("facts_schema") or "symbol_query_v2").strip() or "symbol_query_v2"
        return schema, sq
    t = export_table_rows_facts_from_values(values)
    schema2 = str(t.get("facts_schema") or "table_rows_v2").strip() or "table_rows_v2"
    return schema2, t


def short_desc_for_payload(payload_schema: str, payload_obj: dict[str, Any]) -> str:
    fact_count = int(payload_obj.get("fact_count") or 0)
    sym = str(payload_obj.get("symbol") or "").strip()
    extras = f"facts={fact_count}"
    if sym:
        extras = f"symbol={sym} {extras}"
    return f"schema={payload_schema} {extras}"


def _endpoint_desc_for_title(title: str) -> tuple[str, str]:
    """
    返回 (是什么, 有什么用)。
    - 说明列是给人看的：保持短、明确、可复制给外部读者
    """
    t = str(title or "").strip()
    if not t:
        return "表格快照", "AI/脚本消费"

    if t == "加密货币看板":
        return "全市场加密货币多周期指标总览快照", "策略总览/风险监控/AI 总结"
    if t == "宏观大宗看板":
        return "宏观/大宗/外汇/加密基准与比值快照", "跨资产风险偏好/比值监控/AI 分析"
    if t == "实时新闻":
        return "滚动实时新闻流（时间+正文）", "事件驱动分析/摘要/检索"

    if t.startswith("币种查询_"):
        sym = t.split("_", 1)[1].strip() if "_" in t else ""
        if sym:
            return f"单币种 {sym} 指标明细快照", "币种画像/信号诊断/AI 分析"
        return "单币种指标明细快照", "币种画像/信号诊断/AI 分析"

    if t.startswith("Polymarket"):
        if "Top15" in t:
            return "预测市场 Polymarket 信号 Top15 统计（滚动窗口）", "热点/资金偏好监控"
        if "时段分布" in t:
            return "Polymarket 信号按小时分布统计（滚动窗口）", "活跃时段/异常波动识别"
        if "类别偏好" in t:
            return "Polymarket 信号按类别偏好统计（滚动窗口）", "主题轮动/关注方向识别"
        return "预测市场 Polymarket 信号统计快照", "事件与资金偏好监控"

    return "表格快照", "AI/脚本消费"


def _structure_desc(*, payload_schema: str, payload_obj: dict[str, Any]) -> str:
    schema = str(payload_schema or "").strip()
    if schema == "symbol_query_v2":
        return "facts[]: dims(symbol,panel,group,metric)+values_text/values_num(period->value)"
    if schema == "table_rows_v2":
        return "facts[]: dims(row)+fields_text/fields_num(表头->value)"
    return f"facts_schema={schema or '-'}"


def endpoint_desc_for_payload(
    *,
    title: str,
    payload_schema: str,
    payload_obj: dict[str, Any],
    gid: int,
    truncated: bool,
    truncate_reason: str,
) -> str:
    """
    给 API 表 B 列使用的“人类可读说明”。
    目标：一句话让外部消费者知道这行数据是什么、为什么存在、有什么用、怎么用。
    """
    what, use = _endpoint_desc_for_title(title)
    schema = str(payload_schema or "").strip() or "unknown"
    try:
        fact_count = int(payload_obj.get("fact_count") or 0)
    except Exception:
        fact_count = 0

    trunc = ""
    if truncated:
        reason = str(truncate_reason or "").strip()
        trunc = f"；已截断=1({reason or 'unknown'})"

    struct = _structure_desc(payload_schema=schema, payload_obj=payload_obj)
    # 说明列不作为机器解析目标，但仍保持稳定格式，方便复制给外部读者。
    return (
        f"是什么：{what}；为什么：把公开表格变成可版本化的机器接口（外部可直读）；"
        f"有什么用：{use}；结构：{struct}；空值=null；"
        f"使用：复制右侧命令执行（自动解 gzip/base64）；schema={schema} facts={fact_count} gid={int(gid)}{trunc}"
    )


def api_cell_command(*, spreadsheet_id: str, api_gid: int, row: int) -> str:
    """
    生成“公开读取 + 解码”的单行命令（不含 token）。
    - 使用 gviz Query Language 精确读取指定行的 A 列（避免 range= 参数不稳定）
    """
    offset = max(int(row) - 1, 0)
    return (
        "curl -fsSL "
        f"\"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/gviz/tq?tqx=out:csv&gid={int(api_gid)}&tq=select%20A%20limit%201%20offset%20{offset}\""
        " | python3 -c 'import sys,csv,json,gzip,base64; "
        "cell=next(csv.reader(sys.stdin))[0]; "
        "obj=json.loads(cell); "
        "p=obj.get(\"data\",{}).get(\"payload\",{}); "
        "enc=str(p.get(\"encoding\") or \"\"); "
        "b64=str(p.get(\"gzip_b64\") or \"\"); "
        "raw=gzip.decompress(base64.b64decode(b64)) if enc==\"gzip_base64\" else json.dumps(p,ensure_ascii=False).encode(); "
        "sys.stdout.write(raw.decode(\"utf-8\"))'"
    )


def _truncate_payload_facts(payload_obj: dict[str, Any], *, max_facts: int) -> tuple[dict[str, Any], bool]:
    facts = payload_obj.get("facts")
    if not isinstance(facts, list):
        return payload_obj, False
    if max_facts <= 0:
        max_facts = 1
    total = len(facts)
    if total <= max_facts:
        return payload_obj, False
    new_obj = dict(payload_obj)
    new_obj["facts_total"] = int(total)
    new_obj["facts_truncated"] = True
    new_facts = facts[: int(max_facts)]
    new_obj["facts"] = new_facts
    new_obj["fact_count"] = int(len(new_facts))
    return new_obj, True


def _shrink_payload_to_fit_cell(
    *,
    sheet: SheetRef,
    banner_text: str,
    payload_schema: str,
    payload_obj: dict[str, Any],
    lang: str,
    max_chars: int,
) -> tuple[str, dict[str, Any], bool, str]:
    cell_json = build_envelope_cell_json(
        sheet=sheet,
        banner_text=banner_text,
        payload_obj=payload_obj,
        payload_schema=payload_schema,
        lang=lang,
        max_chars=max_chars,
        truncated=False,
    )
    if len(cell_json) <= max_chars:
        return cell_json, payload_obj, False, ""

    raw_max = (os.environ.get("SHEETS_API_TRUNCATE_MAX_FACTS", "") or "").strip()
    if not raw_max:
        raw_max = (os.environ.get("SHEETS_API_TRUNCATE_MAX_ROWS", "") or "").strip()
    try:
        max_facts = int(raw_max or "1200")
    except Exception:
        max_facts = 1200
    payload_obj2, did = _truncate_payload_facts(payload_obj, max_facts=max_facts)
    if did:
        cell_json2 = build_envelope_cell_json(
            sheet=sheet,
            banner_text=banner_text,
            payload_obj=payload_obj2,
            payload_schema=payload_schema,
            lang=lang,
            max_chars=max_chars,
            truncated=True,
            truncate_reason="facts_truncated",
        )
        if len(cell_json2) <= max_chars:
            return cell_json2, payload_obj2, True, "facts_truncated"

    raw_preview = (os.environ.get("SHEETS_API_TRUNCATE_PREVIEW_FACTS", "") or "").strip()
    if not raw_preview:
        raw_preview = (os.environ.get("SHEETS_API_TRUNCATE_PREVIEW_ROWS", "") or "").strip()
    try:
        preview = int(raw_preview or "50")
    except Exception:
        preview = 50
    payload_obj3, _ = _truncate_payload_facts(payload_obj, max_facts=preview)
    payload_obj3 = dict(payload_obj3)
    payload_obj3["facts_preview_only"] = True
    cell_json3 = build_envelope_cell_json(
        sheet=sheet,
        banner_text=banner_text,
        payload_obj=payload_obj3,
        payload_schema=payload_schema,
        lang=lang,
        max_chars=max_chars,
        truncated=True,
        truncate_reason="preview_only",
    )
    if len(cell_json3) <= max_chars:
        return cell_json3, payload_obj3, True, "preview_only"

    cell_json4 = build_error_envelope_cell_json(
        sheet=sheet,
        banner_text=banner_text,
        lang=lang,
        error=f"payload_too_large:chars={len(cell_json3)} max_chars={max_chars}",
    )
    return cell_json4, {"format": "error", "error": "payload_too_large"}, True, "payload_too_large"


def publish_api_table(
    sa_writer: Any,
    *,
    api_title: str,
    include_hidden: bool,
    max_tabs: int,
    read_col_r: str,
    read_max_rows: int,
    lang: str,
) -> dict[str, Any]:
    """
    SA 模式：从工作簿现有 tab 读取 values，生成 API 目录表并覆盖写入 A/B/C。
    """
    # banner：优先从工作簿兜底读取（避免运维未配置 env 时，API JSON 里 banner 为空）
    banner_text = ""
    try:
        if hasattr(sa_writer, "public_top_banner_text"):
            banner_text, _ = sa_writer.public_top_banner_text()
    except Exception:
        banner_text = ""
    if not banner_text:
        banner_raw = _banner_text_from_env()
        banner_lines = _banner_lines(banner_raw)
        # 统一：API envelope / API tab 的 banner 文本都使用“真实换行符”
        banner_text = "\n".join(banner_lines)

    sa_writer.ensure_sheet(title=api_title)
    sheets = sa_writer.list_sheet_properties()

    api_gid = 0
    for s in sheets:
        if str(s.get("title") or "").strip() == str(api_title or "").strip():
            api_gid = int(s.get("gid") or 0)
            break
    if api_gid <= 0:
        raise RuntimeError(f"missing_api_sheet_gid:{api_title}")

    spreadsheet_id = str(getattr(sa_writer, "_spreadsheet_id", "") or "")
    if not spreadsheet_id:
        raise RuntimeError("missing_spreadsheet_id")

    try:
        max_chars = int(os.environ.get("SHEETS_API_MAX_CHARS", "48000") or "48000")
    except Exception:
        max_chars = 48000

    only_titles = _parse_list_csv_zh(os.environ.get("SHEETS_API_ONLY_TITLES", ""))
    skip_titles = _parse_list_csv_zh(os.environ.get("SHEETS_API_SKIP_TITLES", ""))

    rows: list[list[str]] = []
    # 首行：广告位（强制；与其它 tab 对齐）
    if banner_text:
        rows.append([banner_text, "", ""])
    # 第 2 行：元信息（单单元格）
    tz8 = timezone(timedelta(hours=8))
    now_bj = datetime.now(timezone.utc).astimezone(tz8).replace(microsecond=0).isoformat()
    interval_s: float | None = None
    try:
        raw_interval = (os.environ.get("SHEETS_API_INTERVAL_SECONDS", "") or "").strip()
        if raw_interval:
            interval_s = float(raw_interval)
    except Exception:
        interval_s = None
    meta_row_idx = len(rows)
    rows.append(
        [
            format_public_meta_row_text(
                dataset=str(api_title or "API").strip() or "API",
                exported_at_utc8=str(now_bj),
                interval_seconds=interval_s,
                window=None,
                row_count=None,  # 回填
                lang=str(lang or "zh_CN").strip() or "zh_CN",
                mode=(os.environ.get("SHEETS_WRITE_MODE", "") or "").strip().lower() or "sa",
                schema="api_table_v1",
            ),
            "",
            "",
        ]
    )
    # 第 3 行：表头（jsonl / 说明 / 请求命令）
    rows.append(["jsonl", "说明", "请求命令"])
    errors: list[str] = []
    published = 0

    for sh in sheets:
        title = str(sh.get("title") or "").strip()
        gid = int(sh.get("gid") or 0)
        hidden = bool(sh.get("hidden") or False)
        if not title or gid <= 0:
            continue
        if title == api_title:
            continue
        if only_titles and (not _match_title_patterns(title, only_titles)):
            continue
        if skip_titles and _match_title_patterns(title, skip_titles):
            continue
        if hidden and (not include_hidden):
            continue
        if max_tabs > 0 and published >= max_tabs:
            break

        sheet_ref = SheetRef(spreadsheet_id=spreadsheet_id, gid=gid, title=title)
        row_num = len(rows) + 1  # 当前行数 + 1 -> 下一个写入行号（1-based；含 banner/meta/header）
        cmd = api_cell_command(spreadsheet_id=spreadsheet_id, api_gid=api_gid, row=row_num)

        try:
            col_r = (read_col_r or "BS").strip().upper()
            a1 = f"A1:{col_r}{int(read_max_rows)}"
            values = sa_writer.read_values_a1(title=title, a1_range=a1)
            payload_schema, payload_obj = build_payload_from_sheet_values(values)

            cell_json, payload_final, truncated, reason = _shrink_payload_to_fit_cell(
                sheet=sheet_ref,
                banner_text=banner_text,
                payload_schema=payload_schema,
                payload_obj=payload_obj,
                lang=lang,
                max_chars=max_chars,
            )
            desc = endpoint_desc_for_payload(
                title=title,
                payload_schema=payload_schema,
                payload_obj=payload_final,
                gid=gid,
                truncated=truncated,
                truncate_reason=reason,
            )
            rows.append([cell_json, desc, cmd])
            published += 1
        except Exception as exc:
            errors.append(f"{title}:{type(exc).__name__}:{exc}")
            cell_json = build_error_envelope_cell_json(
                sheet=sheet_ref,
                banner_text=banner_text,
                lang=lang,
                error=f"{type(exc).__name__}:{exc}",
            )
            desc = (
                "是什么：该表 API 导出失败；为什么：上游表格解析/读取失败；有什么用：用于排障定位；"
                f"使用：先看右侧命令是否可读，再检查表格内容；gid={gid}；"
                f"error={type(exc).__name__}:{str(exc)[:160]}"
            )
            rows.append([cell_json, desc, cmd])
            published += 1

    # 回填：API tab 元信息行条数
    try:
        rows[meta_row_idx][0] = format_public_meta_row_text(
            dataset=str(api_title or "API").strip() or "API",
            exported_at_utc8=str(now_bj),
            interval_seconds=interval_s,
            window=None,
            row_count=int(published),
            lang=str(lang or "zh_CN").strip() or "zh_CN",
            mode=(os.environ.get("SHEETS_WRITE_MODE", "") or "").strip().lower() or "sa",
            schema="api_table_v1",
        )
    except Exception:
        pass

    sa_writer.write_api_table_values(api_title=api_title, rows=rows)
    return {"ok": True, "api_title": api_title, "api_gid": api_gid, "published": published, "errors": errors}
