from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.public_meta import format_public_meta_row_text
from src.repo import find_repo_root
from src.sa_sheets_writer import SaSheetsWriter


@dataclass(frozen=True)
class NewsFilterConfig:
    min_length: int
    max_length: int
    blacklist: list[str]
    trim: list[str]


def _load_default_config_from_repo() -> NewsFilterConfig:
    """
    默认值从 repo 内的 core/alternative/news/configs/filter.json 读取，避免双处维护。
    若读取失败则回退到硬编码默认值（与当前 filter.json 对齐）。
    """
    fallback = NewsFilterConfig(
        min_length=15,
        max_length=2000,
        blacklist=[
            "广告",
            "点击查看",
            "点击观看",
            "立即下载",
            "立即观看",
            "关注我们",
            "Copyright",
            "版权所有",
            "金十数据中心工具",
            "金十图示",
        ],
        trim=[
            "金十数据",
            "分享收藏详情复制",
            "历史数据",
            "【金十数据APP下载】",
            "【点击查看详情】",
            "下载金十APP",
            "更多精彩内容请关注",
            "扫码下载",
            "立即体验",
        ],
    )

    try:
        repo_root = find_repo_root(Path(__file__).resolve())
        path = repo_root / "core" / "alternative" / "news" / "configs" / "filter.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        min_len = int(raw.get("minLength") or fallback.min_length)
        max_len = int(raw.get("maxLength") or fallback.max_length)
        blacklist = [str(x).strip() for x in (raw.get("blacklist") or []) if str(x).strip()]
        trim = [str(x).strip() for x in (raw.get("trim") or []) if str(x).strip()]
        if min_len <= 0 or max_len <= 0 or min_len > max_len:
            return fallback
        return NewsFilterConfig(min_length=min_len, max_length=max_len, blacklist=blacklist, trim=trim)
    except Exception:
        return fallback


def publish_news_filter_config_tab(
    writer: SaSheetsWriter,
    *,
    tab_title: str = "新闻过滤配置",
    force: bool = False,
    lang: str = "zh_CN",
) -> dict[str, Any]:
    """
    发布“新闻过滤配置”表（可编辑的配置模板）：
    - 仅在 tab 不存在或 force=1 时覆盖写入
    - 目的：让用户在 Google Sheets 中编辑过滤规则，并让上游 news-services 以“表格”为配置源

    表结构（A:D）：
    - 键(key)：minLength|maxLength|blacklist|trim
    - 值(value)：数值或关键词
    - 启用(enabled)：1/0（0 表示忽略该行）
    - 说明(note)：可选
    """
    title = str(tab_title or "").strip() or "新闻过滤配置"
    sheets = writer.list_sheet_properties()
    exists = any(str(s.get("title") or "") == title for s in sheets)
    if exists and not force:
        return {"ok": True, "op": "publish_news_filter_config_tab", "skipped": True, "title": title}

    cfg = _load_default_config_from_repo()
    headers = ["键(key)", "值(value)", "启用", "说明"]
    n_cols = len(headers)

    # banner + 元信息：保持与公开表契约一致（A1/A2 单格）
    banner_text, banner_line_count = writer.public_top_banner_text()
    banner_rows = 1 if banner_text else 0

    now_bj = datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()
    meta = format_public_meta_row_text(
        dataset=title,
        exported_at_utc8=now_bj,
        interval_seconds=None,
        window=None,
        row_count=2 + len(cfg.blacklist) + len(cfg.trim),
        lang=str(lang or "zh_CN").strip() or "zh_CN",
        mode="sheet_config",
        schema="news_filter_config_v1",
    )

    values: list[list[Any]] = []
    if banner_rows > 0:
        values.append([banner_text] + [""] * (n_cols - 1))
    values.append([meta] + [""] * (n_cols - 1))
    values.append(headers)

    def row(key: str, value: object, enabled: int = 1, note: str = "") -> list[Any]:
        return [str(key), str(value), int(enabled), str(note)]

    values.append(row("minLength", cfg.min_length, 1, "最短长度（字符数）；小于则丢弃"))
    values.append(row("maxLength", cfg.max_length, 1, "最长长度（字符数）；大于则丢弃"))

    # blacklist / trim：每行一个词；命中 blacklist 则整条丢弃；trim 会先从正文删除再判断长度/黑名单
    if cfg.blacklist:
        values.append(row("blacklist", cfg.blacklist[0], 1, "黑名单：命中任意子串→整条丢弃（大小写不敏感）"))
        for w in cfg.blacklist[1:]:
            values.append(row("blacklist", w, 1, ""))
    if cfg.trim:
        values.append(row("trim", cfg.trim[0], 1, "删词：先从正文中删除这些片段，再做长度/黑名单判断"))
        for w in cfg.trim[1:]:
            values.append(row("trim", w, 1, ""))

    writer.reset_sheet_display(
        title=title,
        col_l="A",
        col_r="D",
        compact=True,
        frozen_row_count=int(banner_rows) + 2,  # banner+meta+header
        frozen_column_count=1,
    )
    writer.write_values_matrix(title=title, values=values, value_input_option="USER_ENTERED")

    # 仅做最小样式：隐藏网格线 + 表头加粗（避免覆盖用户自定义格式）
    sh_id = writer.sheet_id(title)
    row_hdr = int(banner_rows) + 1  # meta 在 banner 后一行；header 在其后
    reqs: list[dict[str, Any]] = []
    reqs.append(
        {
            "updateSheetProperties": {
                "properties": {"sheetId": int(sh_id), "gridProperties": {"hideGridlines": True}},
                "fields": "gridProperties.hideGridlines",
            }
        }
    )
    reqs.append(
        {
            "repeatCell": {
                "range": {
                    "sheetId": int(sh_id),
                    "startRowIndex": int(row_hdr),
                    "endRowIndex": int(row_hdr) + 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": int(n_cols),
                },
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                "fields": "userEnteredFormat.textFormat.bold",
            }
        }
    )
    writer.batch_update(requests=reqs)

    return {"ok": True, "op": "publish_news_filter_config_tab", "skipped": False, "title": title, "banner_lines": banner_line_count}
