from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from src.sa_sheets_writer import SaSheetsWriter


def _index_to_col(idx_1: int) -> str:
    n = int(idx_1)
    if n <= 0:
        return "A"
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


def _tz_bj() -> timezone:
    return timezone(timedelta(hours=8))


def _now_bj() -> datetime:
    return datetime.now(timezone.utc).astimezone(_tz_bj()).replace(microsecond=0)


def _format_bj(dt: datetime) -> str:
    return dt.astimezone(_tz_bj()).replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


_RE_URL = re.compile(r"(https?://\S+)")
_RE_TG_LINK = re.compile(r"(?:(?:https?://)?(?:t\.me|telegram\.me|telegram\.dog)/\S+)")
_RE_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_RE_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d\- ]{7,}\d)(?!\d)")
_RE_HANDLE = re.compile(r"(?<!\w)@[A-Za-z0-9_]{3,}(?!\w)")


def _redact_text(text: str, *, redact: bool) -> str:
    if not redact:
        return text
    s = str(text or "")
    if not s:
        return s
    s = _RE_TG_LINK.sub("[URL]", s)
    s = _RE_URL.sub("[URL]", s)
    s = _RE_EMAIL.sub("[EMAIL]", s)
    s = _RE_PHONE.sub("[PHONE]", s)
    s = _RE_HANDLE.sub("[HANDLE]", s)
    return s


def fetch_tg_messages_recent(*, lang: str = "zh_CN") -> dict[str, Any]:
    """
    获取 Telegram 监听消息（窗口化，Query Service Only）。

    单读出口硬约束：
    - 本服务（consumption）禁止直连 sqlite/ssh 旁路读取
    - 必须通过 Query Service（/api/v1/events）读取
    """
    _ = lang
    window_hours = int((os.environ.get("SHEETS_TG_WINDOW_HOURS", "24") or "24").strip() or "24")
    limit = int((os.environ.get("SHEETS_TG_LIMIT", "200") or "200").strip() or "200")
    timeout_seconds = int((os.environ.get("SHEETS_TG_TIMEOUT_SECONDS", "30") or "30").strip() or "30")
    # 安全默认：强制脱敏（用户要求）
    redact = (os.environ.get("SHEETS_TG_REDACT", "1") or "1").strip() == "1"
    max_text_chars = int((os.environ.get("SHEETS_TG_MAX_TEXT_CHARS", "4000") or "4000").strip() or "4000")

    base = (os.environ.get("QUERY_SERVICE_BASE_URL") or "http://127.0.0.1:8088").strip().rstrip("/")
    token = (os.environ.get("QUERY_SERVICE_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("missing_env:QUERY_SERVICE_TOKEN（事件域端点 fail-closed，必须配置）")

    now_bj = _now_bj()
    threshold = now_bj - timedelta(hours=int(window_hours))
    threshold_s = _format_bj(threshold)

    params = {
        "hours": int(window_hours),
        "limit": int(max(int(limit) * 3, 50)),
        "offset": 0,
        # 兼容：Query Service 未实现 types 过滤时会忽略该参数；客户端仍会二次过滤。
        "types": "telegram",
    }
    resp = requests.get(
        f"{base}/api/v1/events",
        params=params,
        headers={"X-Internal-Token": token},
        timeout=max(int(timeout_seconds), 1),
    )
    resp.raise_for_status()
    payload = resp.json() if resp.text else {}
    if not isinstance(payload, dict) or not payload.get("success"):
        raise RuntimeError(f"query_failed:{str((payload or {}).get('msg') or 'unknown')}")
    data = payload.get("data") or {}
    events = data.get("list") or []

    out_rows: list[dict[str, Any]] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if str(ev.get("type") or "").strip().lower() != "telegram":
            continue

        ts_str = str(ev.get("ingested_at") or ev.get("ts") or "").strip()
        dt = None
        if ts_str:
            try:
                dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except Exception:
                dt = None
        if dt is not None and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        captured_at_bj = _format_bj(dt.astimezone(_tz_bj())) if dt else None
        if captured_at_bj and captured_at_bj < threshold_s:
            continue

        redacted_text = _redact_text(str(ev.get("content") or ""), redact=bool(redact))
        if max_text_chars > 0 and len(redacted_text) > int(max_text_chars):
            redacted_text = redacted_text[: int(max_text_chars)] + "…"

        out_rows.append({"captured_at_bj": captured_at_bj, "text": redacted_text or None})
        if len(out_rows) >= int(limit):
            break

    return {
        "ok": True,
        "generated_at_bj": _format_bj(now_bj),
        "window_hours": int(window_hours),
        "threshold_bj": threshold_s,
        "limit": int(limit),
        "rows": out_rows,
        "mode": "query",
        "source": f"{base}/api/v1/events",
    }


def write_tg_messages_tab(
    writer: SaSheetsWriter,
    *,
    tab_messages: str = "Telegram消息",
    hide_tab: bool = False,
    lang: str = "zh_CN",
) -> dict[str, Any]:
    """
    写入 Telegram消息（窗口化消息明细，强制脱敏）。

    重要约束（用户硬要求）：
    - 不导出频道列表（频道列表属于核心资产）
    - 只允许输出脱敏后的消息内容
    """
    now_bj = _now_bj().isoformat()

    res_msg = fetch_tg_messages_recent(lang=lang)
    # 历史遗留：如果工作簿里曾经生成过 Telegram频道，则主动删除（可逆：未来需要可重建，但默认不输出）
    deleted_channels = 0
    try:
        deleted_channels = int(writer.delete_tab_if_exists(title="Telegram频道").get("deleted") or 0)
    except Exception:
        deleted_channels = 0

    # -------------------- Telegram消息 --------------------
    msg_rows = list(res_msg.get("rows") or [])
    # 仅保留“时间 + 脱敏内容”，避免泄露频道资产（名称/username/chat_id/url 等）
    msg_headers = ["时间(北京)", "内容（已脱敏）"]
    msg_values: list[list[Any]] = []
    meta = (
        f"数据源，Telegram监听，导出时间(UTC+8)，{now_bj}，窗口，滚动{int(res_msg.get('window_hours') or 0)}h，"
        f"条数，{len(msg_rows)}，模式，{res_msg.get('mode')}，source，{res_msg.get('source')}"
    )
    msg_values.append([meta] + [""] * (len(msg_headers) - 1))
    msg_values.append(msg_headers)
    for r in msg_rows:
        # 双保险：即使上游/远端没脱敏，这里也强制再脱敏一次。
        text = _redact_text(str(r.get("text") or ""), redact=True)
        msg_values.append(
            [
                (r.get("captured_at_bj") or ""),
                (text or ""),
            ]
        )

    msg_cols = len(msg_headers)
    writer.reset_sheet_display(
        title=tab_messages,
        col_l="A",
        col_r=_index_to_col(msg_cols),
        compact=True,
        frozen_row_count=2,
        frozen_column_count=1,  # 时间
    )
    writer.write_values_matrix(title=tab_messages, values=msg_values, value_input_option="USER_ENTERED")

    sh_id_msg = writer.sheet_id(tab_messages)
    reqs_msg: list[dict[str, Any]] = []
    reqs_msg.append(
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": int(sh_id_msg),
                    "gridProperties": {"hideGridlines": True},
                },
                "fields": "gridProperties.hideGridlines",
            }
        }
    )
    # meta row / header styles
    def repeat_cell(r0: int, r1: int, c0: int, c1: int, fmt: dict[str, Any], fields: str) -> None:
        reqs_msg.append(
            {
                "repeatCell": {
                    "range": {
                        "sheetId": int(sh_id_msg),
                        "startRowIndex": int(r0),
                        "endRowIndex": int(r1),
                        "startColumnIndex": int(c0),
                        "endColumnIndex": int(c1),
                    },
                    "cell": {"userEnteredFormat": fmt},
                    "fields": fields,
                }
            }
        )

    repeat_cell(
        0,
        1,
        0,
        msg_cols,
        {
            "backgroundColor": {"red": 1.0, "green": 0.97, "blue": 0.86},
            "textFormat": {"fontFamily": "Arial", "fontSize": 11, "bold": True},
            "horizontalAlignment": "LEFT",
            "verticalAlignment": "MIDDLE",
            "wrapStrategy": "OVERFLOW_CELL",
        },
        "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)",
    )
    repeat_cell(
        1,
        2,
        0,
        msg_cols,
        {
            "backgroundColor": {"red": 0.95, "green": 0.95, "blue": 0.95},
            "textFormat": {"fontFamily": "Arial", "fontSize": 10, "bold": True},
            "horizontalAlignment": "CENTER",
            "verticalAlignment": "MIDDLE",
            "wrapStrategy": "CLIP",
        },
        "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)",
    )
    # 内容列（C）自动换行
    repeat_cell(
        2,
        max(len(msg_values), 3),
        1,
        2,
        {"wrapStrategy": "WRAP"},
        "userEnteredFormat.wrapStrategy",
    )
    # 去边框
    reqs_msg.append(
        {
            "updateBorders": {
                "range": {
                    "sheetId": int(sh_id_msg),
                    "startRowIndex": 0,
                    "endRowIndex": int(len(msg_values)),
                    "startColumnIndex": 0,
                    "endColumnIndex": int(msg_cols),
                },
                "top": {"style": "NONE"},
                "bottom": {"style": "NONE"},
                "left": {"style": "NONE"},
                "right": {"style": "NONE"},
                "innerHorizontal": {"style": "NONE"},
                "innerVertical": {"style": "NONE"},
            }
        }
    )
    # 列宽
    col_widths = [170, 980]
    for ci, px in enumerate(col_widths[:msg_cols]):
        reqs_msg.append(
            {
                "updateDimensionProperties": {
                    "range": {"sheetId": int(sh_id_msg), "dimension": "COLUMNS", "startIndex": int(ci), "endIndex": int(ci + 1)},
                    "properties": {"pixelSize": int(px)},
                    "fields": "pixelSize",
                }
            }
        )

    if reqs_msg:
        writer.batch_update(requests=reqs_msg)

    # 隐藏 tab（可选；注意：隐藏不等于“外部不可访问”）
    if hide_tab:
        try:
            sid = int(writer.sheet_id(tab_messages))
            writer.batch_update(
                requests=[{"updateSheetProperties": {"properties": {"sheetId": int(sid), "hidden": True}, "fields": "hidden"}}]
            )
        except Exception:
            pass

    return {
        "ok": True,
        "tab": tab_messages,
        "messages": int(len(msg_rows)),
        "deleted_tabs": {"Telegram频道": int(deleted_channels)},
        "mode": res_msg.get("mode"),
    }
