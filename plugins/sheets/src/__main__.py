from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from src.config import Settings
from src.dashboard_dedup import inject_base_card_and_dedup
from src.dashboard_variants import field_rows_period_columns
from src.idempotency import IdempotencyStore
from src.mock_webhook_server import serve_mock_webhook
from src.outbox import JsonlOutbox
from src.api_table import publish_api_table
from src.news_exporter import style_news_tab_only, write_news_tab, write_news_values_only
from src.polymarket_facts_exporter import export_polymarket_facts_events_sheet
from src.polymarket_exporter import export_polymarket_stats_sheet
from src.repo import find_repo_root
from src.sa_sheets_writer import SaSheetsWriter
from src.symbol_query_exporter import export_symbol_query_sheet, normalize_symbol_tab_title
from src.tg_cards_exporter import TgCardsExporter
from src.webhook_client import SheetsWebhookClient

try:
    from src.macro_display import publish_macro_display
except Exception:
    publish_macro_display = None

try:
    from src.macro_snapshot import refresh_macro_snapshot_stooq
except Exception:
    refresh_macro_snapshot_stooq = None

try:
    from src.news_filter_config_tab import publish_news_filter_config_tab
except Exception:
    publish_news_filter_config_tab = None

try:
    from src.public_audit import audit_public_tab_names, audit_sensitive_text_cells
except Exception:
    audit_public_tab_names = None
    audit_sensitive_text_cells = None

try:
    from src.public_style_audit import audit_public_styles
except Exception:
    audit_public_styles = None


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sync local TG cards to Google Sheets")
    p.add_argument("--once", action="store_true", help="只执行一次：导出卡片 -> 写 outbox -> flush")
    p.add_argument("--daemon", action="store_true", help="守护模式：按间隔循环执行")
    p.add_argument("--daemon-news", action="store_true", help="守护模式：仅高频刷新 实时新闻 tab（SA 模式）")
    p.add_argument(
        "--daemon-news-interval-seconds",
        type=float,
        default=0.0,
        help="--daemon-news 刷新间隔秒（优先于 env；默认读 SHEETS_NEWS_DAEMON_INTERVAL_SECONDS）",
    )
    p.add_argument("--mock-webhook", action="store_true", help="启动本地 mock webhook（用于离线验收）")
    p.add_argument("--dry-run", action="store_true", help="不发网络请求，只打印 payload 概要")
    p.add_argument("--force", action="store_true", help="强制重渲染：忽略幂等（用于版式/样式大改后刷新）")
    p.add_argument("--write-mode", default="", help="写入模式：webhook|sa（默认读 env: SHEETS_WRITE_MODE）")
    p.add_argument("--bootstrap", action="store_true", help="SA 模式：创建/初始化工作簿并配置权限（全 CLI）")
    p.add_argument("--bootstrap-title", default="TradeCat TG Cards Dashboard", help="SA 模式：新建工作簿标题")
    p.add_argument("--reset-dashboard", action="store_true", help="SA 模式：清空看板并重置 y 指针/列区间")
    p.add_argument("--rebuild-dashboard", action="store_true", help="SA 模式：从事实表重建看板（可能较慢）")
    p.add_argument("--delete-tab", default="", help="SA 模式：删除指定 tab（精确匹配 title）")
    p.add_argument(
        "--prune-tabs",
        action="store_true",
        help="SA 模式：删除非必要 tab（仅保留 加密货币看板 + 配置的币种查询子表）",
    )
    p.add_argument("--dashboard-variants", action="store_true", help="SA 模式：生成 3 套高密度看板变体（新建 tab）")
    p.add_argument(
        "--dashboard-variants-only",
        action="store_true",
        help="SA 模式：仅生成看板变体（不重绘主看板；用于避免写入配额压力）",
    )
    p.add_argument(
        "--snapshot-col-widths",
        action="store_true",
        help="SA 模式：读取 加密货币看板/币种查询/Polymarket 三表 当前列宽并输出 env 配置行（只读，不写入）",
    )
    p.add_argument(
        "--snapshot-polymarket-col-widths",
        action="store_true",
        help="SA 模式：读取 Polymarket 三表当前列宽并输出 env 配置行（只读，不写入）",
    )
    p.add_argument(
        "--publish-api-table",
        action="store_true",
        help="SA 模式：仅发布 API tab（只写 API 目录表，不刷新看板）",
    )
    p.add_argument(
        "--publish-news-filter-config",
        action="store_true",
        help="SA 模式：创建/刷新 新闻过滤配置 tab（用于上游 news-services 读取）",
    )
    p.add_argument(
        "--publish-macro-display",
        action="store_true",
        help="SA 模式：创建/刷新宏观展示页（宏观大宗看板/10_SNAPSHOT_WIDE/90_SERIES_MAP/98_REFRESH_LOG）",
    )
    p.add_argument(
        "--refresh-macro-snapshot",
        action="store_true",
        help="SA 模式：刷新宏观快照（从公开数据源抓取并覆盖写入 10_SNAPSHOT_WIDE 第2行）",
    )
    p.add_argument(
        "--style-borderless-all",
        action="store_true",
        help="SA 模式：对整个工作簿所有 tabs 应用无边框样式（隐藏网格线 + 清边框，覆盖用户手动边框）",
    )
    p.add_argument(
        "--audit-grid",
        action="store_true",
        help="SA 模式：审计所有 tabs 网格大小 vs 最大有效行列（只读）",
    )
    p.add_argument(
        "--audit-top-rows",
        action="store_true",
        help="SA 模式：审计各 tab 的 A1/A2 是否为 banner+元信息（只读）",
    )
    p.add_argument(
        "--audit-sensitive-public",
        action="store_true",
        help="SA 模式：审计可见 tabs 是否包含敏感信息（只读；命中则返回非 0）",
    )
    p.add_argument(
        "--audit-public-styles",
        action="store_true",
        help="SA 模式：审计可见 tabs 的公开样式契约（只读；命中则返回非 0）",
    )
    p.add_argument(
        "--audit-tab-names-public",
        action="store_true",
        help="SA 模式：审计可见 tabs 命名（禁止 '工作表6/Sheet1' 等随机名）",
    )
    p.add_argument(
        "--fix-top-rows",
        action="store_true",
        help="SA 模式：统一所有 tabs 的首行广告位 + 第二行元信息（必要时插入行；覆盖样式/行高）",
    )
    p.add_argument(
        "--reorder-tabs-public",
        action="store_true",
        help="SA 模式：按固定顺序重排可见 tabs（不影响 hidden tabs）",
    )
    p.add_argument(
        "--compact-grid-public",
        action="store_true",
        help="SA 模式：只裁剪可见 tabs 的行/列到最大有效区域（危险：会删除网格外单元格）",
    )
    p.add_argument(
        "--compact-grid-all",
        action="store_true",
        help="SA 模式：按最大有效行列裁剪所有 tabs 的行/列（危险：会删除网格外单元格）",
    )
    p.add_argument(
        "--refresh-external-tabs",
        action="store_true",
        help="SA 模式：刷新外部数据 tabs（实时新闻；会删除 Telegram消息/Telegram频道）",
    )
    p.add_argument("--rebuild-max-cards", type=int, default=200, help="重建：只取最后 N 张卡片（默认 200）")
    p.add_argument("--cards", default="", help="逗号分隔 card_id 白名单；空=全部")
    p.add_argument("--lang", default="", help="导出语言（默认 zh_CN）")
    p.add_argument("--mock-port", type=int, default=18080, help="mock webhook 监听端口（默认 18080）")
    return p.parse_args()


def _should_retry(status: int) -> bool:
    # 约定：
    # - status=0：网络层错误（URLError）
    # - 429/5xx：上游限流/临时错误
    return status == 0 or status == 429 or (500 <= status <= 599)


def _env_bool(key: str, default: str = "0") -> bool:
    raw = (os.environ.get(key, default) or default).strip().lower()
    return raw not in {"0", "off", "false", "no", ""}


def _log_level() -> str:
    return (os.environ.get("SHEETS_LOG_LEVEL", "info") or "info").strip().lower()


def _debug_enabled() -> bool:
    return _log_level() in {"debug", "trace"}


def _debug_print(msg: str) -> None:
    if _debug_enabled():
        print(str(msg))


def _env_text(key: str, default: str) -> str:
    v = (os.environ.get(key, "") or "").strip()
    return v or default


def _env_int(key: str, default: int) -> int:
    try:
        return int((os.environ.get(key, str(default)) or str(default)).strip() or str(default))
    except Exception:
        return int(default)


def _env_float(key: str, default: float) -> float:
    try:
        return float((os.environ.get(key, str(default)) or str(default)).strip() or str(default))
    except Exception:
        return float(default)


def _publish_guard_enabled() -> bool:
    # 发布门禁（可选）：写入完成后自动执行公开审计，失败则返回非 0
    return _env_bool("SHEETS_PUBLIC_PUBLISH_GUARD", "0")


def _validate_write_target(settings: Settings) -> str | None:
    if settings.write_mode not in {"webhook", "sa"}:
        return f"❌ 不支持的 SHEETS_WRITE_MODE={settings.write_mode}（仅支持 webhook|sa）"

    if settings.dry_run:
        return None

    if settings.write_mode == "webhook":
        if not settings.webhook_url or not settings.webhook_secret:
            return "❌ 缺少 SHEETS_WEBHOOK_URL / SHEETS_WEBHOOK_SECRET，无法发送（可先用 --dry-run 或 SHEETS_SYNC_DRY_RUN=1）"
        return None

    if not settings.sa_credentials_path:
        return "❌ 缺少 GOOGLE_APPLICATION_CREDENTIALS / SHEETS_SA_CREDENTIALS_PATH，无法使用 SA 写入"
    if not settings.spreadsheet_id:
        return "❌ 缺少 SHEETS_SPREADSHEET_ID（可先运行 --bootstrap 创建工作簿）"
    return None


def _run_public_publish_guard(settings: Settings, *, lang: str) -> dict[str, Any]:
    """
    发布门禁：对“可见 tabs”执行公开审计（只读）。

    覆盖：
    - top_rows: A1=广告位 / A2=元信息
    - grid: 网格大小裁剪是否合理（不应存在异常膨胀/冻结越界）
    - sensitive: 敏感信息（内网 IP / token / DSN / 绝对路径等）
    - styles: hideGridlines / 顶部两行行高底色 / API&新闻列宽等
    """
    if settings.write_mode != "sa":
        return {"ok": False, "error": "publish_guard_requires_sa_mode"}
    if audit_sensitive_text_cells is None or audit_public_styles is None:
        return {"ok": False, "error": "publish_guard_modules_unavailable"}

    writer = SaSheetsWriter(
        spreadsheet_id=settings.spreadsheet_id,
        credentials_path=settings.sa_credentials_path,
        dashboard_col_l=settings.dashboard_col_l,
        dashboard_col_r=settings.dashboard_col_r,
        dashboard_mode=settings.dashboard_mode,
        dashboard_slot_height=settings.dashboard_slot_height,
        facts_mode=settings.facts_mode,
        share_email=settings.share_email,
        public_read=settings.public_read,
        drive_folder_id=settings.drive_folder_id,
        blob_threshold_chars=settings.blob_threshold_chars,
        timeout_seconds=settings.webhook_timeout_seconds,
        schema_mode=settings.schema_mode,
        local_meta_path=settings.local_meta_path,
    )

    api_title = _env_text("SHEETS_API_TAB_TITLE", "API")
    tab_news = _env_text("SHEETS_TAB_NEWS", "实时新闻")

    # 1) top rows
    expected_banner, _expected_lines = writer.public_top_banner_text()
    sheets = writer.list_sheet_properties()
    top_bad: list[str] = []
    for sh in sheets:
        title = str(sh.get("title") or "").strip()
        hidden = bool(sh.get("hidden") or False)
        gid = int(sh.get("gid") or 0)
        if not title or gid <= 0 or hidden:
            continue
        try:
            got = writer.read_values_a1(title=title, a1_range="A1:A2")
            a1 = str(got[0][0] or "") if got and got[0] else ""
            a2 = str(got[1][0] or "") if len(got) >= 2 and got[1] else ""
        except Exception:
            top_bad.append(title)
            continue
        ok_banner = bool(expected_banner) and (a1.strip() == expected_banner.strip())
        ok_meta = ("数据源" in a2) and ("导出时间(UTC+8)" in a2)
        if not (ok_banner and ok_meta):
            top_bad.append(title)

    top_ok = len(top_bad) == 0

    # 2) grid
    grid_rows = writer.audit_grid_used_range_all(include_hidden=False, value_render_option="FORMULA")
    grid_bad = [r for r in grid_rows if str(r.get("status") or "") != "ok"]
    grid_ok = len(grid_bad) == 0

    # 3) sensitive
    max_api_rows = _env_int("SHEETS_AUDIT_API_ROWS", 200)
    max_news_rows = _env_int("SHEETS_AUDIT_NEWS_ROWS", 260)
    sensitive_hits: list[dict[str, Any]] = []
    for sh in sheets:
        title = str(sh.get("title") or "").strip()
        hidden = bool(sh.get("hidden") or False)
        gid = int(sh.get("gid") or 0)
        if not title or gid <= 0 or hidden:
            continue

        ranges: list[str] = ["A1:A2"]
        if title == str(api_title or "").strip():
            ranges.append(f"A1:C{int(max_api_rows)}")
        if title == str(tab_news or "").strip():
            ranges.append(f"A1:B{int(max_news_rows)}")

        for a1_range in ranges:
            try:
                vals = writer.read_values_a1(title=title, a1_range=a1_range)
            except Exception as exc:
                sensitive_hits.append(
                    {
                        "sheet": title,
                        "cell": a1_range,
                        "rule": "read_failed",
                        "excerpt": f"{type(exc).__name__}:{exc}"[:160],
                    }
                )
                continue
            for h in audit_sensitive_text_cells(sheet_title=title, a1_range=a1_range, values=vals):
                sensitive_hits.append({"sheet": h.sheet, "cell": h.cell, "rule": h.rule, "excerpt": h.excerpt})

    sensitive_ok = len(sensitive_hits) == 0

    # 4) styles
    style_res = audit_public_styles(
        writer,
        include_hidden=False,
        a1_range=_env_text("SHEETS_AUDIT_STYLE_RANGE", "A1:T40"),
        tab_api_title=api_title,
        tab_news_title=tab_news,
    )
    styles_ok = bool(style_res.get("ok"))

    ok = bool(top_ok and grid_ok and sensitive_ok and styles_ok)
    return {
        "ok": ok,
        "lang": str(lang or ""),
        "top_rows": {"ok": top_ok, "bad": len(top_bad), "bad_titles": top_bad[:30]},
        "grid": {"ok": grid_ok, "bad": len(grid_bad), "bad_titles": [str(r.get("title") or "") for r in grid_bad][:30]},
        "sensitive": {"ok": sensitive_ok, "violations": len(sensitive_hits), "hits": sensitive_hits[:50]},
        "styles": {"ok": styles_ok, "bad": int(style_res.get("bad") or 0), "hits": list(style_res.get("hits") or [])[:80]},
    }


def _maybe_publish_api_table(sa_writer: SaSheetsWriter, *, force: bool, lang: str) -> None:
    if not _env_bool("SHEETS_API_ENABLE", "0"):
        return

    api_title = _env_text("SHEETS_API_TAB_TITLE", "API")
    include_hidden = _env_bool("SHEETS_API_INCLUDE_HIDDEN", "0")
    max_tabs = _env_int("SHEETS_API_MAX_TABS", 80)
    read_col_r = _env_text("SHEETS_API_READ_COL_R", "BS").upper()
    read_max_rows = _env_int("SHEETS_API_READ_MAX_ROWS", 2000)

    now = int(time.time())
    interval = _env_int("SHEETS_API_INTERVAL_SECONDS", 900)
    meta = sa_writer.meta_get()
    try:
        last = int(str(meta.get("api_table_last_attempt_epoch") or "0").strip() or "0")
    except Exception:
        last = 0
    should = bool(force) or interval <= 0 or (now - last) >= interval
    if not should:
        return

    # 先落盘 attempt epoch：避免 publish 失败时每轮都重试刷屏
    try:
        sa_writer.meta_set(
            {
                "api_table_last_attempt_epoch": str(now),
                "api_table_interval_seconds": str(int(interval)),
            }
        )
    except Exception:
        pass

    try:
        res = publish_api_table(
            sa_writer,
            api_title=api_title,
            include_hidden=include_hidden,
            max_tabs=max_tabs,
            read_col_r=read_col_r,
            read_max_rows=read_max_rows,
            lang=lang,
        )
        sa_writer.meta_set(
            {
                "api_table_last_ok_epoch": str(now),
                "api_table_last_error": "",
                "api_table_interval_seconds": str(int(interval)),
                "api_table_last_published": str(int(res.get("published") or 0)),
                "api_table_last_gid": str(int(res.get("api_gid") or 0)),
            }
        )
    except Exception as exc:
        try:
            sa_writer.meta_set({"api_table_last_error": f"{type(exc).__name__}:{exc}"[:2000]})
        except Exception:
            pass
        print(f"⚠️ API 表发布失败（不影响主流程）：{type(exc).__name__}: {exc}")


def _maybe_publish_news_filter_config(sa_writer: SaSheetsWriter, *, force: bool, lang: str) -> None:
    """
    仅“引导式”创建配置表：
    - 默认只在首次（或 force=1）写入模板，避免覆盖用户在表格里维护的配置
    - 用 local_meta 做幂等，避免在 daemon tick 中反复读写
    """
    if not _env_bool("SHEETS_NEWS_FILTER_CONFIG_ENABLE", "0"):
        return
    if publish_news_filter_config_tab is None:
        return

    title = _env_text("SHEETS_TAB_NEWS_FILTER", "新闻过滤配置")
    force_write = bool(force) or _env_bool("SHEETS_NEWS_FILTER_CONFIG_FORCE", "0")

    meta = sa_writer.local_meta_get()
    boot = str(meta.get("news_filter_config_bootstrapped") or "").strip()
    if (not force_write) and boot == "1":
        return

    try:
        publish_news_filter_config_tab(sa_writer, tab_title=title, force=force_write, lang=lang)
        # 不管是“创建/覆盖”还是“已存在跳过”，都视为已完成引导（避免 daemon tick 反复读写）。
        sa_writer.local_meta_set({"news_filter_config_bootstrapped": "1", "news_filter_config_title": str(title)})
    except Exception as exc:
        print(f"⚠️ 新闻过滤配置表发布失败（不影响主流程）：{type(exc).__name__}: {exc}")


def _sleep_backoff(attempt: int, *, base: float, max_seconds: float) -> None:
    # attempt: 1..N
    delay = min(base * (2 ** (attempt - 1)), max_seconds)
    time.sleep(max(delay, 0.0))


def _new_sa_writer(settings: Settings) -> SaSheetsWriter:
    return SaSheetsWriter(
        spreadsheet_id=settings.spreadsheet_id,
        credentials_path=settings.sa_credentials_path,
        dashboard_col_l=settings.dashboard_col_l,
        dashboard_col_r=settings.dashboard_col_r,
        dashboard_mode=settings.dashboard_mode,
        dashboard_slot_height=settings.dashboard_slot_height,
        facts_mode=settings.facts_mode,
        share_email=settings.share_email,
        public_read=settings.public_read,
        drive_folder_id=settings.drive_folder_id,
        blob_threshold_chars=settings.blob_threshold_chars,
        timeout_seconds=settings.webhook_timeout_seconds,
        schema_mode=settings.schema_mode,
        local_meta_path=settings.local_meta_path,
    )


def _start_news_style_worker(
    settings: Settings,
    *,
    tab_news: str,
    hide_news: bool,
) -> threading.Thread:
    retry_seconds = max(_env_float("SHEETS_NEWS_STYLE_RETRY_SECONDS", 15.0), 1.0)
    interval_seconds = max(_env_float("SHEETS_NEWS_STYLE_INTERVAL_SECONDS", 0.0), 0.0)
    max_retries = max(_env_int("SHEETS_NEWS_STYLE_MAX_RETRIES", 0), 0)

    def _run() -> None:
        attempt = 0
        while True:
            attempt += 1
            try:
                style_writer = _new_sa_writer(settings)
                res = style_news_tab_only(style_writer, tab_news=tab_news, hide_tab=hide_news)
                print(
                    json.dumps(
                        {"ok": True, "op": "daemon_news_style", "attempt": int(attempt), "interval_seconds": interval_seconds, "style": res},
                        ensure_ascii=False,
                    )
                )
                if interval_seconds <= 0:
                    return
                time.sleep(max(float(interval_seconds), 1.0))
                attempt = 0
                continue
            except Exception as exc:
                print(f"⚠️ 实时新闻样式维护失败：{type(exc).__name__}: {exc}")
                if interval_seconds <= 0 and max_retries > 0 and attempt >= max_retries:
                    print(
                        json.dumps(
                            {
                                "ok": False,
                                "op": "daemon_news_style",
                                "stopped": True,
                                "reason": "max_retries_reached",
                                "attempt": int(attempt),
                            },
                            ensure_ascii=False,
                        )
                    )
                    return
                time.sleep(max(float(retry_seconds), 1.0))

    t = threading.Thread(target=_run, name="news-style-worker", daemon=True)
    t.start()
    return t


def _post_with_retry(
    client: SheetsWebhookClient,
    payload: dict,
    *,
    max_retries: int,
    backoff_base_seconds: float,
    backoff_max_seconds: float,
) -> tuple[bool, int, dict]:
    attempt = 0
    while True:
        resp = client.post_json(payload)
        if resp.ok:
            return True, resp.status, resp.body

        if not _should_retry(resp.status):
            return False, resp.status, resp.body

        attempt += 1
        if attempt > max_retries:
            return False, resp.status, resp.body

        _sleep_backoff(attempt, base=backoff_base_seconds, max_seconds=backoff_max_seconds)


def _extract_volume_sorted_symbols(payloads: list[dict]) -> list[str]:
    """
    统一交易对排序口径：使用“成交量榜单（volume_ranking）”的行顺序作为全局币种顺序。
    - volume_ranking 的 base_period 默认 15m。
    - 在 sheets-service 导出侧会默认把 volume_ranking 改为按成交额（quote_volume）降序排序，
      因此其 rows 顺序即“按交易量(成交额)排序”。
    - 不做数值解析（避免 K/M/B 格式与语言变化引入误差）。
    """
    card_type = (os.environ.get("SHEETS_SYMBOL_SORT_CARD_TYPE", "volume_ranking") or "volume_ranking").strip()
    if not card_type:
        card_type = "volume_ranking"

    vol_payload = None
    for p in payloads:
        if str(p.get("card_type") or "").strip() == card_type:
            vol_payload = p
            break
    if not isinstance(vol_payload, dict):
        return []

    table = vol_payload.get("table") or {}
    cols = table.get("columns") or []
    rows = table.get("rows") or []
    if not (isinstance(cols, list) and isinstance(rows, list) and cols):
        return []

    sym_key = str(cols[0] or "").strip() or "币种"
    out: list[str] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        sym = str(r.get(sym_key) or r.get("币种") or r.get("symbol") or "").strip()
        if sym and sym not in out:
            out.append(sym)
    return out


def _reorder_payload_rows_by_symbols(payload: dict, *, symbols: list[str]) -> None:
    table = payload.get("table") or {}
    cols = table.get("columns") or []
    rows = table.get("rows") or []
    if not (isinstance(cols, list) and isinstance(rows, list) and cols and rows):
        return

    sym_key = str(cols[0] or "").strip() or "币种"
    order = {s: i for i, s in enumerate(symbols or [])}

    indexed: list[tuple[int, int, object]] = []
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            indexed.append((10**9, i, r))
            continue
        sym = str(r.get(sym_key) or r.get("币种") or r.get("symbol") or "").strip()
        indexed.append((int(order.get(sym, 10**9)), int(i), r))

    indexed.sort(key=lambda t: (t[0], t[1]))
    table["rows"] = [t[2] for t in indexed]
    payload["table"] = table


async def _run_once(
    settings: Settings,
    *,
    only_cards: list[str] | None,
    lang: str,
    dashboard_variants: bool,
    dashboard_variants_only: bool,
) -> int:
    write_target_error = _validate_write_target(settings)
    if write_target_error:
        print(write_target_error)
        return 2

    # dashboard 模式：每轮把“看板”作为展示面整体覆盖重绘（避免 slot 高度非递减导致的空洞/错位）
    # - 只建议用于 SA 模式（纯 CLI，可直接 reset+写入）
    # - webhook 模式无法安全 reset，因此自动降级为 snapshot
    is_dashboard_mode = settings.sync_mode == "dashboard" and settings.write_mode == "sa"

    webhook_client = None
    sa_writer = None
    if settings.dry_run:
        pass
    elif settings.write_mode == "webhook":
        webhook_client = SheetsWebhookClient(
            settings.webhook_url,
            settings.webhook_secret,
            timeout_seconds=settings.webhook_timeout_seconds,
        )
    elif settings.write_mode == "sa":
        sa_writer = SaSheetsWriter(
            spreadsheet_id=settings.spreadsheet_id,
            credentials_path=settings.sa_credentials_path,
            dashboard_col_l=settings.dashboard_col_l,
            dashboard_col_r=settings.dashboard_col_r,
            dashboard_mode=settings.dashboard_mode,
            dashboard_slot_height=settings.dashboard_slot_height,
            facts_mode=settings.facts_mode,
            share_email=settings.share_email,
            public_read=settings.public_read,
            drive_folder_id=settings.drive_folder_id,
            blob_threshold_chars=settings.blob_threshold_chars,
            timeout_seconds=settings.webhook_timeout_seconds,
            schema_mode=settings.schema_mode,
            local_meta_path=settings.local_meta_path,
        )
    def _send_one(payload: dict) -> tuple[bool, int, dict]:
        if settings.write_mode == "webhook":
            assert webhook_client is not None
            return _post_with_retry(
                webhook_client,
                payload,
                max_retries=settings.webhook_max_retries,
                backoff_base_seconds=settings.webhook_backoff_base_seconds,
                backoff_max_seconds=settings.webhook_backoff_max_seconds,
            )

        assert sa_writer is not None
        attempt = 0
        last_status = 0
        last_body: dict = {}
        while True:
            try:
                body = sa_writer.write_card(payload)
                return True, 200, body
            except Exception as exc:
                last_status = int(getattr(getattr(exc, "resp", None), "status", 0) or 0)
                last_body = {"error": f"{type(exc).__name__}: {exc}"}
                if not _should_retry(last_status):
                    return False, last_status, last_body
                attempt += 1
                if attempt > settings.webhook_max_retries:
                    return False, last_status, last_body
                _sleep_backoff(
                    attempt,
                    base=settings.webhook_backoff_base_seconds,
                    max_seconds=settings.webhook_backoff_max_seconds,
                )

    # dashboard 模式：不走 outbox/idempotency（每轮全量重绘；失败下轮重试即可）
    if is_dashboard_mode:
        exporter = TgCardsExporter(include_blacklist=settings.include_blacklist, lang=lang)
        results = await exporter.export(only_cards=only_cards)

        payloads: list[dict] = []
        for r in results:
            if r.event is None:
                continue
            payload = r.event.to_dict()
            payloads.append(payload)

        # 主看板去重：把“重复基础字段”抽到第一个“基础数据”卡片，其它卡片删掉这些列
        payloads = inject_base_card_and_dedup(payloads)

        # 选择主看板渲染方案：
        # - v5：字段纵向 + 周期横向（宽度稳定、可冻结、可筛选；当前默认）
        # - legacy：保留原始“超宽分块纵向堆叠”渲染
        main_variant = (os.environ.get("SHEETS_DASHBOARD_MAIN_VARIANT", "5") or "5").strip().lower()
        use_v5_main = main_variant in {"5", "v5", "方案5"}

        # 统一交易对排序：按“成交量榜单”行顺序作为全局币种顺序（即按交易量排序）
        # - 默认开启，可用 SHEETS_SYMBOL_SORT_BY_VOLUME=0 关闭
        if use_v5_main and (os.environ.get("SHEETS_SYMBOL_SORT_BY_VOLUME", "1") or "1").strip() != "0":
            syms = _extract_volume_sorted_symbols(payloads)
            if syms:
                for p in payloads:
                    _reorder_payload_rows_by_symbols(p, symbols=syms)

        # 为 auto width 预估列数（v5 固定 10 列：卡片/币种/字段/7周期；legacy 取原始最大列数）
        if use_v5_main:
            max_cols = 10
        else:
            max_cols = 1
            for payload in payloads:
                cols = (payload.get("table") or {}).get("columns") or []
                try:
                    max_cols = max(max_cols, len(cols))
                except Exception:
                    pass

        if settings.dry_run:
            print(
                f"[dry-run] mode=dashboard cards={len(payloads)} max_cols={max_cols} spreadsheet_id={settings.spreadsheet_id}"
            )
            return 0

        assert sa_writer is not None
        # minimal schema：确保只保留“主看板 + 配置的币种查询 tab”，避免旧表残留/复活。
        if settings.schema_mode == "minimal":
            keep_symbol_tabs = []
            for sym in settings.symbol_tabs or []:
                keep_symbol_tabs.append(normalize_symbol_tab_title(symbol=sym, prefix=settings.symbol_tab_prefix))
            try:
                t0 = time.time()
                res = sa_writer.prune_tabs(symbol_tab_prefix=settings.symbol_tab_prefix, keep_symbol_tabs=keep_symbol_tabs)
                elapsed_ms = int((time.time() - t0) * 1000.0)

                skipped = int(res.get("skipped") or 0)
                ok = bool(res.get("ok", True))
                deleted = int(res.get("deleted") or 0)
                reason = str(res.get("reason") or "").strip() or "run"
                err = str(res.get("error") or "").strip()

                msg = f"prune_tabs {'skip' if skipped else 'run'} ok={int(ok)} deleted={deleted} reason={reason} ms={elapsed_ms}"
                if err:
                    msg = f"{msg} error={err[:200]}"

                # 默认安静：skip 只在 debug 输出；异常/失败一律输出
                if skipped and ok and (not err):
                    _debug_print(msg)
                else:
                    print(msg)
            except Exception as exc:
                print(f"⚠️ prune_tabs 失败（将继续执行）：{type(exc).__name__}: {exc}")

        # 自动宽度：避免“超宽表头纵向分块”让用户误以为列丢失
        col_l = settings.dashboard_col_l
        col_r = settings.dashboard_col_r
        if settings.dashboard_auto_width:
            min_r = "J" if use_v5_main else col_r
            col_r = sa_writer.compute_col_r(col_l=col_l, needed_cols=max_cols, min_col_r=min_r)

        sent = 0
        if not dashboard_variants_only:
            if use_v5_main:
                # v5 主表：先将每张卡的原始表结构变换为 v5 统一表头（币种/字段/7周期）
                transformed: list[dict] = []
                for p in payloads:
                    table = p.get("table") or {}
                    cols = table.get("columns") or []
                    rows = table.get("rows") or []
                    if isinstance(cols, list) and isinstance(rows, list):
                        cols_s = [str(c) for c in cols if c is not None]
                        vt = field_rows_period_columns(columns=cols_s, rows=rows)
                        np = dict(p)
                        np["table"] = {"columns": vt.columns, "rows": vt.rows}
                        transformed.append(np)
                    else:
                        transformed.append(p)

                try:
                    sa_writer.write_dashboard_v5_main(payloads=transformed, col_l=col_l, col_r=col_r)
                    sent = len(transformed)
                except Exception as exc:
                    print(f"❌ 看板重绘失败（v5）{type(exc).__name__}: {exc}")
                    return 3
            else:
                # legacy：逐张卡写入（兼容“超宽字段分块纵向堆叠”）
                # dashboard 模式强制用 append（配合 reset），实现“紧凑排布、不卡槽位、不卡高度”
                sa_writer.set_dashboard_mode("append")
                sa_writer.reset_dashboard(col_l=col_l, col_r=col_r, compact=True)

                for p in payloads:
                    ok, status, body = _send_one(p)
                    if not ok:
                        print(f"❌ 看板重绘失败 status={status} body={json.dumps(body, ensure_ascii=False)}")
                        return 3
                    sent += 1

        if dashboard_variants or dashboard_variants_only:
            try:
                sa_writer.write_dashboard_variants(payloads=payloads, col_l=col_l, min_col_r="M")
            except Exception as exc:
                print(f"⚠️ 变体看板生成失败：{type(exc).__name__}: {exc}")

        # 统一时钟刷新（用户要求）：
        # - 关闭“币种查询/Polymarket 的独立 interval 节流”，每轮只按 daemon 的 tick 统一刷新一次
        # - 仍保留各功能 enable 开关（symbol_tabs_mode/pm_enable/pme_enable）
        unified_refresh = _env_bool("SHEETS_UNIFIED_REFRESH", "0")

        # 币种查询子表（4 个交易对）：覆盖写，不走 facts（默认每 15 分钟刷新一次，避免配额爆炸）
        if settings.symbol_tabs_mode != "none" and settings.symbol_tabs:
            now = int(time.time())
            meta = sa_writer.meta_get()
            try:
                last = int(
                    str(meta.get("symbol_tabs_last_attempt_epoch") or meta.get("symbol_tabs_last_epoch") or "0").strip()
                    or "0"
                )
            except Exception:
                last = 0
            interval = int(settings.symbol_tabs_interval_seconds)
            should = bool(settings.force_render) or unified_refresh or interval <= 0 or (now - last) >= interval
            if should:
                try:
                    sa_writer.meta_set(
                        {
                            "symbol_tabs_last_attempt_epoch": str(now),
                            "symbol_tabs_last_epoch": str(now),  # legacy 兼容
                            "symbol_tabs_interval_seconds": str(int(interval)),
                        }
                    )
                except Exception:
                    pass
                errors: list[str] = []
                for sym in settings.symbol_tabs:
                    try:
                        tab_title = normalize_symbol_tab_title(symbol=sym, prefix=settings.symbol_tab_prefix)
                        sheet = export_symbol_query_sheet(symbol=sym, lang=lang)
                        sa_writer.write_symbol_query_tab(tab_title=tab_title, sheet=sheet)
                    except Exception as exc:
                        errors.append(f"{sym}:{type(exc).__name__}:{exc}")
                try:
                    kv = {
                        "symbol_tabs_last_error": ";".join(errors)[:2000],
                        "symbol_tabs_interval_seconds": str(int(interval)),
                    }
                    if not errors:
                        kv["symbol_tabs_last_ok_epoch"] = str(now)
                        kv["symbol_tabs_last_error"] = ""
                    sa_writer.meta_set(kv)
                except Exception:
                    pass

        # Polymarket 统计子表：默认 auto（配置了 ssh host 则走 ssh，否则本机运行 node）
        pm_enable = (os.environ.get("SHEETS_POLYMARKET_STATS_ENABLE", "1") or "1").strip().lower()
        if pm_enable in {"0", "off", "false", "no"}:
            pm_on = False
        elif pm_enable in {"1", "on", "true", "yes"}:
            pm_on = True
        else:
            # auto：默认启用（即使导出失败也会写入“错误卡片”，避免用户“看不到 tab”）
            pm_on = True
        if pm_on:
            now = int(time.time())
            meta = sa_writer.meta_get()
            try:
                last = int(
                    str(
                        meta.get("polymarket_stats_last_attempt_epoch")
                        or meta.get("polymarket_stats_last_epoch")
                        or "0"
                    ).strip()
                    or "0"
                )
            except Exception:
                last = 0
            interval = int((os.environ.get("SHEETS_POLYMARKET_STATS_INTERVAL_SECONDS", "900") or "900").strip() or "900")
            should = bool(settings.force_render) or unified_refresh or interval <= 0 or (now - last) >= interval
            if should:
                try:
                    sa_writer.meta_set(
                        {
                            "polymarket_stats_last_attempt_epoch": str(now),
                            "polymarket_stats_last_epoch": str(now),  # legacy 兼容
                            "polymarket_stats_interval_seconds": str(int(interval)),
                        }
                    )
                except Exception:
                    pass
                tab_title = (os.environ.get("SHEETS_TAB_POLYMARKET_STATS", "Polymarket统计") or "Polymarket统计").strip()
                err = ""
                try:
                    pm_sheet = export_polymarket_stats_sheet(lang=lang)
                    sa_writer.write_polymarket_stats_tab(tab_title=tab_title, sheet=pm_sheet)
                except Exception as exc:
                    err = f"{type(exc).__name__}:{exc}"
                try:
                    kv = {
                        "polymarket_stats_last_error": (err or "")[:2000],
                        "polymarket_stats_interval_seconds": str(int(interval)),
                    }
                    if not err:
                        kv["polymarket_stats_last_ok_epoch"] = str(now)
                        kv["polymarket_stats_last_error"] = ""
                    sa_writer.meta_set(kv)
                except Exception:
                    pass

        # Polymarket facts 事件子表：结构化事实（append-only 的长期源头）抽取为“最近 24h”可审计视图
        # 默认关闭：这是高频明细表，会造成表格负担与额外噪声；需要时再显式开启。
        pme_enable = (os.environ.get("SHEETS_POLYMARKET_FACTS_EVENTS_ENABLE", "0") or "0").strip().lower()
        if pme_enable not in {"0", "off", "false", "no"}:
            now = int(time.time())
            meta = sa_writer.meta_get()
            try:
                last = int(
                    str(
                        meta.get("polymarket_events_last_attempt_epoch")
                        or meta.get("polymarket_events_last_epoch")
                        or "0"
                    ).strip()
                    or "0"
                )
            except Exception:
                last = 0
            interval = int(
                (os.environ.get("SHEETS_POLYMARKET_FACTS_EVENTS_INTERVAL_SECONDS", "900") or "900").strip() or "900"
            )
            should = bool(settings.force_render) or unified_refresh or interval <= 0 or (now - last) >= interval
            if should:
                try:
                    sa_writer.meta_set(
                        {
                            "polymarket_events_last_attempt_epoch": str(now),
                            "polymarket_events_last_epoch": str(now),  # legacy 兼容
                            "polymarket_events_interval_seconds": str(int(interval)),
                        }
                    )
                except Exception:
                    pass
                tab_title = (os.environ.get("SHEETS_TAB_POLYMARKET_EVENTS", "Polymarket事件") or "Polymarket事件").strip()
                err = ""
                try:
                    pm_sheet = export_polymarket_facts_events_sheet(lang=lang)
                    sa_writer.write_polymarket_stats_tab(tab_title=tab_title, sheet=pm_sheet)
                except Exception as exc:
                    err = f"{type(exc).__name__}:{exc}"
                try:
                    kv = {
                        "polymarket_events_last_error": (err or "")[:2000],
                        "polymarket_events_interval_seconds": str(int(interval)),
                    }
                    if not err:
                        kv["polymarket_events_last_ok_epoch"] = str(now)
                        kv["polymarket_events_last_error"] = ""
                    sa_writer.meta_set(kv)
                except Exception:
                    pass

        # 宏观快照：从公开数据源抓取并覆盖写入 10_SNAPSHOT_WIDE 第2行（给“宏观大宗看板”引用展示）
        if _env_bool("SHEETS_MACRO_SNAPSHOT_ENABLE", "0"):
            now = int(time.time())
            meta = sa_writer.meta_get()
            try:
                last = int(str(meta.get("macro_snapshot_last_attempt_epoch") or "0").strip() or "0")
            except Exception:
                last = 0
            interval = _env_int("SHEETS_MACRO_SNAPSHOT_INTERVAL_SECONDS", 900)
            should = bool(settings.force_render) or unified_refresh or interval <= 0 or (now - last) >= interval
            if should:
                # 先落盘 attempt epoch：避免失败后每轮都重试刷屏
                try:
                    sa_writer.meta_set(
                        {
                            "macro_snapshot_last_attempt_epoch": str(now),
                            "macro_snapshot_interval_seconds": str(int(interval)),
                        }
                    )
                except Exception:
                    pass

                tab_snapshot = _env_text("SHEETS_MACRO_TAB_SNAPSHOT", "10_SNAPSHOT_WIDE")
                lookback_days = _env_int("SHEETS_MACRO_LOOKBACK_DAYS", 120)
                try:
                    res = refresh_macro_snapshot_stooq(sa_writer, tab_snapshot=tab_snapshot, lookback_days=lookback_days)
                    sa_writer.meta_set(
                        {
                            "macro_snapshot_last_ok_epoch": str(now),
                            "macro_snapshot_last_error": "",
                            "macro_snapshot_interval_seconds": str(int(interval)),
                            "macro_snapshot_elapsed_ms": str(int(res.get("elapsed_ms") or 0)),
                            "macro_snapshot_asof_dates": ",".join(list(res.get("asof_dates") or []))[:2000],
                        }
                    )
                except Exception as exc:
                    try:
                        sa_writer.meta_set({"macro_snapshot_last_error": f"{type(exc).__name__}:{exc}"[:2000]})
                    except Exception:
                        pass
                    print(f"⚠️ 宏观快照刷新失败（不影响主流程）：{type(exc).__name__}: {exc}")

        # 实时新闻：窗口化导出到 Sheets（用于看板/AI 分析）
        if _env_bool("SHEETS_NEWS_ENABLE", "0"):
            now = int(time.time())
            meta = sa_writer.meta_get()
            try:
                last = int(str(meta.get("news_last_attempt_epoch") or "0").strip() or "0")
            except Exception:
                last = 0
            interval = _env_int("SHEETS_NEWS_INTERVAL_SECONDS", 300)
            should = bool(settings.force_render) or unified_refresh or interval <= 0 or (now - last) >= interval
            if should:
                try:
                    sa_writer.meta_set(
                        {
                            "news_last_attempt_epoch": str(now),
                            "news_interval_seconds": str(int(interval)),
                        }
                    )
                except Exception:
                    pass

                tab_news = _env_text("SHEETS_TAB_NEWS", "实时新闻")
                hide_tab = _env_bool("SHEETS_NEWS_HIDE_TAB", "0")
                try:
                    res = write_news_tab(sa_writer, tab_news=tab_news, hide_tab=hide_tab, lang=lang)
                    sa_writer.meta_set(
                        {
                            "news_last_ok_epoch": str(now),
                            "news_last_error": "",
                            "news_interval_seconds": str(int(interval)),
                            "news_rows": str(int(res.get("rows") or 0)),
                        }
                    )
                except Exception as exc:
                    try:
                        sa_writer.meta_set({"news_last_error": f"{type(exc).__name__}:{exc}"[:2000]})
                    except Exception:
                        pass
                    print(f"⚠️ 实时新闻导出失败（不影响主流程）：{type(exc).__name__}: {exc}")

        if dashboard_variants_only:
            print(
                f"✅ 看板变体生成完成 mode=dashboard variants_only=1 cards={len(payloads)} col_l={col_l} col_r={col_r}"
            )
        else:
            print(f"✅ 看板重绘完成 mode=dashboard cards={sent} col_l={col_l} col_r={col_r}")

        # 新闻过滤配置：仅引导式创建模板（默认不覆盖用户在表格里维护的配置）
        _maybe_publish_news_filter_config(sa_writer, force=False, lang=lang)

        # API tab：在所有写入完成后统一生成（读取现有表格 values -> facts -> gzip+base64）
        _maybe_publish_api_table(sa_writer, force=bool(settings.force_render) or unified_refresh, lang=lang)
        return 0

    # snapshot/append 模式：outbox + 幂等（用于事实表或 slot 覆盖写）
    outbox = JsonlOutbox(settings.outbox_path, settings.checkpoint_path)
    idem = IdempotencyStore()

    def _flush_outbox() -> tuple[int, int] | None:
        sent = 0
        skipped = 0
        for item in outbox.iter_unsent():
            card_key = str((item.payload or {}).get("card_key") or "").strip()
            if (not settings.force_render) and card_key and idem.has(card_key):
                outbox.save_checkpoint(item.offset)
                skipped += 1
                continue

            ok, status, body = _send_one(item.payload)
            if not ok:
                print(f"❌ 写入失败 offset={item.offset} status={status} body={json.dumps(body, ensure_ascii=False)}")
                return None
            if card_key:
                idem.mark(card_key)
            outbox.save_checkpoint(item.offset)
            sent += 1
        return sent, skipped

    if not settings.dry_run:
        # 先 flush 旧积压，避免“限流/失败时仍不断 append 新 outbox”导致 outbox 无界增长。
        if _flush_outbox() is None:
            return 3

    exporter = TgCardsExporter(include_blacklist=settings.include_blacklist, lang=lang)
    results = await exporter.export(only_cards=only_cards)

    appended = 0
    skipped_append = 0
    for r in results:
        if r.event is None:
            continue
        payload = r.event.to_dict()
        card_key = str(payload.get("card_key") or "").strip()
        if (not settings.force_render) and card_key and idem.has(card_key):
            skipped_append += 1
            continue
        outbox.append(payload)
        appended += 1

    if settings.dry_run:
        print(f"[dry-run] appended={appended} skipped_append={skipped_append} outbox={settings.outbox_path}")
        return 0

    flushed = _flush_outbox()
    if flushed is None:
        return 3
    sent, skipped = flushed

    # snapshot 模式默认不刷新币种查询子表（否则每轮写入量过大，容易触发配额/超时）
    # 如需要可配置：SHEETS_SYMBOL_TABS_MODE=every
    if settings.write_mode == "sa" and settings.symbol_tabs_mode == "every" and settings.symbol_tabs:
        assert sa_writer is not None
        errors: list[str] = []
        for sym in settings.symbol_tabs:
            try:
                tab_title = normalize_symbol_tab_title(symbol=sym, prefix=settings.symbol_tab_prefix)
                sheet = export_symbol_query_sheet(symbol=sym, lang=lang)
                sa_writer.write_symbol_query_tab(tab_title=tab_title, sheet=sheet)
            except Exception as exc:
                errors.append(f"{sym}:{type(exc).__name__}:{exc}")
        if errors:
            try:
                sa_writer.meta_set({"symbol_tabs_last_error": ";".join(errors)[:2000]})
            except Exception:
                pass

    if settings.write_mode == "sa":
        assert sa_writer is not None
        _maybe_publish_api_table(sa_writer, force=bool(settings.force_render), lang=lang)

    print(
        f"✅ flush 完成 appended={appended} skipped_append={skipped_append} sent={sent} skipped={skipped} checkpoint={outbox.load_checkpoint()} mode={settings.write_mode}"
    )
    return 0


def main() -> None:
    # 尝试加载全局 .env（不强制；服务启动脚本通常已 export）
    try:
        repo_root = find_repo_root(Path(__file__).resolve())
        env_path = repo_root / "assets" / "config" / ".env"
        if not env_path.exists():
            env_path = repo_root / "config" / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=False)
    except Exception:
        # 允许在“非仓库根目录结构”下运行（例如单文件调试/容器内挂载结构不同），此时依赖外部 export 的环境变量。
        pass

    args = _parse_args()
    # .../plugins/sheets/src/__main__.py
    # parents[0]=src, parents[1]=sheets-service
    service_dir = Path(__file__).resolve().parents[1]
    settings = Settings.from_env(service_dir)

    if args.mock_webhook:
        secret = settings.webhook_secret or "dev-secret"
        data_dir = service_dir / "data" / "mock_webhook"
        data_dir.mkdir(parents=True, exist_ok=True)
        serve_mock_webhook(host="127.0.0.1", port=args.mock_port, secret=secret, data_dir=data_dir)
        return

    if args.write_mode.strip():
        settings = replace(settings, write_mode=args.write_mode.strip())

    if args.bootstrap:
        if settings.write_mode != "sa":
            print("❌ --bootstrap 仅支持 SA 模式：设置 SHEETS_WRITE_MODE=sa 或 --write-mode sa")
            sys.exit(2)
        writer = SaSheetsWriter(
            spreadsheet_id=settings.spreadsheet_id,
            credentials_path=settings.sa_credentials_path,
            dashboard_col_l=settings.dashboard_col_l,
            dashboard_col_r=settings.dashboard_col_r,
            dashboard_mode=settings.dashboard_mode,
            dashboard_slot_height=settings.dashboard_slot_height,
            facts_mode=settings.facts_mode,
            share_email=settings.share_email,
            public_read=settings.public_read,
            drive_folder_id=settings.drive_folder_id,
            blob_threshold_chars=settings.blob_threshold_chars,
            timeout_seconds=settings.webhook_timeout_seconds,
            schema_mode=settings.schema_mode,
            local_meta_path=settings.local_meta_path,
        )
        res = writer.bootstrap(title=args.bootstrap_title)
        print(
            json.dumps(
                {"ok": True, "spreadsheet_id": res.spreadsheet_id, "url": res.spreadsheet_url}, ensure_ascii=False
            )
        )
        sys.exit(0)

    if args.delete_tab.strip():
        if settings.write_mode != "sa":
            print("❌ --delete-tab 仅支持 SA 模式：设置 SHEETS_WRITE_MODE=sa 或 --write-mode sa")
            sys.exit(2)
        writer = SaSheetsWriter(
            spreadsheet_id=settings.spreadsheet_id,
            credentials_path=settings.sa_credentials_path,
            dashboard_col_l=settings.dashboard_col_l,
            dashboard_col_r=settings.dashboard_col_r,
            dashboard_mode=settings.dashboard_mode,
            dashboard_slot_height=settings.dashboard_slot_height,
            facts_mode=settings.facts_mode,
            share_email=settings.share_email,
            public_read=settings.public_read,
            drive_folder_id=settings.drive_folder_id,
            blob_threshold_chars=settings.blob_threshold_chars,
            timeout_seconds=settings.webhook_timeout_seconds,
            schema_mode=settings.schema_mode,
            local_meta_path=settings.local_meta_path,
        )
        try:
            title = args.delete_tab.strip()
            res = writer.delete_tab_if_exists(title=title)
            print(json.dumps({"ok": True, "op": "delete_tab", **res}, ensure_ascii=False))
            sys.exit(0)
        except Exception as exc:
            print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
            sys.exit(3)

    if args.reset_dashboard or args.rebuild_dashboard or args.prune_tabs:
        if settings.write_mode != "sa":
            print(
                "❌ --reset-dashboard/--rebuild-dashboard 仅支持 SA 模式：设置 SHEETS_WRITE_MODE=sa 或 --write-mode sa"
            )
            sys.exit(2)
        writer = SaSheetsWriter(
            spreadsheet_id=settings.spreadsheet_id,
            credentials_path=settings.sa_credentials_path,
            dashboard_col_l=settings.dashboard_col_l,
            dashboard_col_r=settings.dashboard_col_r,
            dashboard_mode=settings.dashboard_mode,
            dashboard_slot_height=settings.dashboard_slot_height,
            facts_mode=settings.facts_mode,
            share_email=settings.share_email,
            public_read=settings.public_read,
            drive_folder_id=settings.drive_folder_id,
            blob_threshold_chars=settings.blob_threshold_chars,
            timeout_seconds=settings.webhook_timeout_seconds,
            schema_mode=settings.schema_mode,
            local_meta_path=settings.local_meta_path,
        )
        try:
            if args.prune_tabs:
                keep_symbol_tabs = []
                for sym in settings.symbol_tabs:
                    keep_symbol_tabs.append(normalize_symbol_tab_title(symbol=sym, prefix=settings.symbol_tab_prefix))
                res = writer.prune_tabs(symbol_tab_prefix=settings.symbol_tab_prefix, keep_symbol_tabs=keep_symbol_tabs)
                print(json.dumps({"ok": True, "op": "prune_tabs", **res}, ensure_ascii=False))
                sys.exit(0)
            if args.reset_dashboard:
                res = writer.reset_dashboard(col_l=settings.dashboard_col_l, col_r=settings.dashboard_col_r)
                print(json.dumps({"ok": True, "op": "reset_dashboard", **res}, ensure_ascii=False))
                sys.exit(0)
            res = writer.rebuild_dashboard(max_cards=int(args.rebuild_max_cards or 0))
            print(json.dumps({"ok": True, "op": "rebuild_dashboard", **res}, ensure_ascii=False))
            sys.exit(0)
        except Exception as exc:
            print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
            sys.exit(3)

    if args.snapshot_polymarket_col_widths:
        if settings.write_mode != "sa":
            print("❌ --snapshot-polymarket-col-widths 仅支持 SA 模式：设置 SHEETS_WRITE_MODE=sa 或 --write-mode sa")
            sys.exit(2)
        writer = SaSheetsWriter(
            spreadsheet_id=settings.spreadsheet_id,
            credentials_path=settings.sa_credentials_path,
            dashboard_col_l=settings.dashboard_col_l,
            dashboard_col_r=settings.dashboard_col_r,
            dashboard_mode=settings.dashboard_mode,
            dashboard_slot_height=settings.dashboard_slot_height,
            facts_mode=settings.facts_mode,
            share_email=settings.share_email,
            public_read=settings.public_read,
            drive_folder_id=settings.drive_folder_id,
            blob_threshold_chars=settings.blob_threshold_chars,
            timeout_seconds=settings.webhook_timeout_seconds,
            schema_mode=settings.schema_mode,
            local_meta_path=settings.local_meta_path,
        )
        tab_top15 = (os.environ.get("SHEETS_TAB_POLYMARKET_TOP15", "PolymarketTop15") or "PolymarketTop15").strip()
        tab_timeslot = (os.environ.get("SHEETS_TAB_POLYMARKET_TIMESLOT", "Polymarket时段分布") or "Polymarket时段分布").strip()
        tab_category = (os.environ.get("SHEETS_TAB_POLYMARKET_CATEGORY", "Polymarket类别偏好") or "Polymarket类别偏好").strip()

        w_top15 = writer.snapshot_column_widths(tab_top15)
        w_timeslot = writer.snapshot_column_widths(tab_timeslot)
        w_category = writer.snapshot_column_widths(tab_category)

        print(f"SHEETS_POLYMARKET_FIXED_COL_WIDTHS_TOP15={','.join(str(x) for x in w_top15)}")
        print(f"SHEETS_POLYMARKET_FIXED_COL_WIDTHS_TIMESLOT={','.join(str(x) for x in w_timeslot)}")
        print(f"SHEETS_POLYMARKET_FIXED_COL_WIDTHS_CATEGORY={','.join(str(x) for x in w_category)}")
        sys.exit(0)

    if args.snapshot_col_widths:
        if settings.write_mode != "sa":
            print("❌ --snapshot-col-widths 仅支持 SA 模式：设置 SHEETS_WRITE_MODE=sa 或 --write-mode sa")
            sys.exit(2)
        writer = SaSheetsWriter(
            spreadsheet_id=settings.spreadsheet_id,
            credentials_path=settings.sa_credentials_path,
            dashboard_col_l=settings.dashboard_col_l,
            dashboard_col_r=settings.dashboard_col_r,
            dashboard_mode=settings.dashboard_mode,
            dashboard_slot_height=settings.dashboard_slot_height,
            facts_mode=settings.facts_mode,
            share_email=settings.share_email,
            public_read=settings.public_read,
            drive_folder_id=settings.drive_folder_id,
            blob_threshold_chars=settings.blob_threshold_chars,
            timeout_seconds=settings.webhook_timeout_seconds,
            schema_mode=settings.schema_mode,
            local_meta_path=settings.local_meta_path,
        )

        def snap(title: str, key: str) -> str:
            try:
                w = writer.snapshot_column_widths(title)
                return ",".join(str(x) for x in w)
            except Exception as exc:
                print(f"⚠️ snapshot_column_widths 失败 key={key} tab={title}: {type(exc).__name__}: {exc}", file=sys.stderr)
                return ""

        tab_dashboard = (os.environ.get("SHEETS_TAB_DASHBOARD", "加密货币看板") or "加密货币看板").strip()
        sym0 = settings.symbol_tabs[0] if settings.symbol_tabs else "BTCUSDT"
        tab_symbol_query = normalize_symbol_tab_title(symbol=sym0, prefix=settings.symbol_tab_prefix)

        tab_top15 = (os.environ.get("SHEETS_TAB_POLYMARKET_TOP15", "PolymarketTop15") or "PolymarketTop15").strip()
        tab_timeslot = (
            os.environ.get("SHEETS_TAB_POLYMARKET_TIMESLOT", "Polymarket时段分布") or "Polymarket时段分布"
        ).strip()
        tab_category = (
            os.environ.get("SHEETS_TAB_POLYMARKET_CATEGORY", "Polymarket类别偏好") or "Polymarket类别偏好"
        ).strip()

        print(f"SHEETS_DASHBOARD_FIXED_COL_WIDTHS={snap(tab_dashboard, 'SHEETS_DASHBOARD_FIXED_COL_WIDTHS')}")
        print(f"SHEETS_SYMBOL_QUERY_FIXED_COL_WIDTHS={snap(tab_symbol_query, 'SHEETS_SYMBOL_QUERY_FIXED_COL_WIDTHS')}")
        print(f"SHEETS_POLYMARKET_FIXED_COL_WIDTHS_TOP15={snap(tab_top15, 'SHEETS_POLYMARKET_FIXED_COL_WIDTHS_TOP15')}")
        print(
            f"SHEETS_POLYMARKET_FIXED_COL_WIDTHS_TIMESLOT={snap(tab_timeslot, 'SHEETS_POLYMARKET_FIXED_COL_WIDTHS_TIMESLOT')}"
        )
        print(
            f"SHEETS_POLYMARKET_FIXED_COL_WIDTHS_CATEGORY={snap(tab_category, 'SHEETS_POLYMARKET_FIXED_COL_WIDTHS_CATEGORY')}"
        )
        sys.exit(0)

    if args.publish_api_table:
        if settings.write_mode != "sa":
            print("❌ --publish-api-table 仅支持 SA 模式：设置 SHEETS_WRITE_MODE=sa 或 --write-mode sa")
            sys.exit(2)
        lang = (args.lang or settings.export_lang).strip() or "zh_CN"
        writer = SaSheetsWriter(
            spreadsheet_id=settings.spreadsheet_id,
            credentials_path=settings.sa_credentials_path,
            dashboard_col_l=settings.dashboard_col_l,
            dashboard_col_r=settings.dashboard_col_r,
            dashboard_mode=settings.dashboard_mode,
            dashboard_slot_height=settings.dashboard_slot_height,
            facts_mode=settings.facts_mode,
            share_email=settings.share_email,
            public_read=settings.public_read,
            drive_folder_id=settings.drive_folder_id,
            blob_threshold_chars=settings.blob_threshold_chars,
            timeout_seconds=settings.webhook_timeout_seconds,
            schema_mode=settings.schema_mode,
            local_meta_path=settings.local_meta_path,
        )
        try:
            api_title = _env_text("SHEETS_API_TAB_TITLE", "API")
            include_hidden = _env_bool("SHEETS_API_INCLUDE_HIDDEN", "0")
            max_tabs = _env_int("SHEETS_API_MAX_TABS", 80)
            read_col_r = _env_text("SHEETS_API_READ_COL_R", "BS").upper()
            read_max_rows = _env_int("SHEETS_API_READ_MAX_ROWS", 2000)

            now = int(time.time())
            res = publish_api_table(
                writer,
                api_title=api_title,
                include_hidden=include_hidden,
                max_tabs=max_tabs,
                read_col_r=read_col_r,
                read_max_rows=read_max_rows,
                lang=lang,
            )
            try:
                writer.meta_set(
                    {
                        "api_table_last_ok_epoch": str(now),
                        "api_table_last_error": "",
                        "api_table_last_published": str(int(res.get("published") or 0)),
                        "api_table_last_gid": str(int(res.get("api_gid") or 0)),
                    }
                )
            except Exception:
                pass
            print(json.dumps({"ok": True, "op": "publish_api_table", **res}, ensure_ascii=False))
            sys.exit(0)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            print(json.dumps({"ok": False, "op": "publish_api_table", "error": err}, ensure_ascii=False))
            sys.exit(3)

    if args.publish_news_filter_config:
        if settings.write_mode != "sa":
            print("❌ --publish-news-filter-config 仅支持 SA 模式：设置 SHEETS_WRITE_MODE=sa 或 --write-mode sa")
            sys.exit(2)
        if publish_news_filter_config_tab is None:
            print(json.dumps({"ok": False, "op": "publish_news_filter_config", "error": "module_unavailable"}, ensure_ascii=False))
            sys.exit(3)
        lang = (args.lang or settings.export_lang).strip() or "zh_CN"
        writer = SaSheetsWriter(
            spreadsheet_id=settings.spreadsheet_id,
            credentials_path=settings.sa_credentials_path,
            dashboard_col_l=settings.dashboard_col_l,
            dashboard_col_r=settings.dashboard_col_r,
            dashboard_mode=settings.dashboard_mode,
            dashboard_slot_height=settings.dashboard_slot_height,
            facts_mode=settings.facts_mode,
            share_email=settings.share_email,
            public_read=settings.public_read,
            drive_folder_id=settings.drive_folder_id,
            blob_threshold_chars=settings.blob_threshold_chars,
            timeout_seconds=settings.webhook_timeout_seconds,
            schema_mode=settings.schema_mode,
            local_meta_path=settings.local_meta_path,
        )
        try:
            title = _env_text("SHEETS_TAB_NEWS_FILTER", "新闻过滤配置")
            force_write = bool(args.force) or _env_bool("SHEETS_NEWS_FILTER_CONFIG_FORCE", "0")
            res = publish_news_filter_config_tab(writer, tab_title=title, force=force_write, lang=lang)
            print(json.dumps({"ok": True, "op": "publish_news_filter_config", **res}, ensure_ascii=False))
            sys.exit(0)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            print(json.dumps({"ok": False, "op": "publish_news_filter_config", "error": err}, ensure_ascii=False))
            sys.exit(3)

    if args.publish_macro_display:
        if settings.write_mode != "sa":
            print("❌ --publish-macro-display 仅支持 SA 模式：设置 SHEETS_WRITE_MODE=sa 或 --write-mode sa")
            sys.exit(2)
        if publish_macro_display is None:
            print(json.dumps({"ok": False, "op": "publish_macro_display", "error": "module_unavailable"}, ensure_ascii=False))
            sys.exit(3)
        writer = SaSheetsWriter(
            spreadsheet_id=settings.spreadsheet_id,
            credentials_path=settings.sa_credentials_path,
            dashboard_col_l=settings.dashboard_col_l,
            dashboard_col_r=settings.dashboard_col_r,
            dashboard_mode=settings.dashboard_mode,
            dashboard_slot_height=settings.dashboard_slot_height,
            facts_mode=settings.facts_mode,
            share_email=settings.share_email,
            public_read=settings.public_read,
            drive_folder_id=settings.drive_folder_id,
            blob_threshold_chars=settings.blob_threshold_chars,
            timeout_seconds=settings.webhook_timeout_seconds,
            schema_mode=settings.schema_mode,
            local_meta_path=settings.local_meta_path,
        )
        try:
            res = publish_macro_display(writer)
            print(json.dumps(res, ensure_ascii=False))
            sys.exit(0)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            print(json.dumps({"ok": False, "op": "publish_macro_display", "error": err}, ensure_ascii=False))
            sys.exit(3)

    if args.refresh_macro_snapshot:
        if settings.write_mode != "sa":
            print("❌ --refresh-macro-snapshot 仅支持 SA 模式：设置 SHEETS_WRITE_MODE=sa 或 --write-mode sa")
            sys.exit(2)
        if refresh_macro_snapshot_stooq is None:
            print(json.dumps({"ok": False, "op": "refresh_macro_snapshot", "error": "module_unavailable"}, ensure_ascii=False))
            sys.exit(3)
        writer = SaSheetsWriter(
            spreadsheet_id=settings.spreadsheet_id,
            credentials_path=settings.sa_credentials_path,
            dashboard_col_l=settings.dashboard_col_l,
            dashboard_col_r=settings.dashboard_col_r,
            dashboard_mode=settings.dashboard_mode,
            dashboard_slot_height=settings.dashboard_slot_height,
            facts_mode=settings.facts_mode,
            share_email=settings.share_email,
            public_read=settings.public_read,
            drive_folder_id=settings.drive_folder_id,
            blob_threshold_chars=settings.blob_threshold_chars,
            timeout_seconds=settings.webhook_timeout_seconds,
            schema_mode=settings.schema_mode,
            local_meta_path=settings.local_meta_path,
        )
        try:
            tab_snapshot = _env_text("SHEETS_MACRO_TAB_SNAPSHOT", "10_SNAPSHOT_WIDE")
            lookback_days = _env_int("SHEETS_MACRO_LOOKBACK_DAYS", 120)
            res = refresh_macro_snapshot_stooq(writer, tab_snapshot=tab_snapshot, lookback_days=lookback_days)
            print(json.dumps(res, ensure_ascii=False))
            sys.exit(0)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            print(json.dumps({"ok": False, "op": "refresh_macro_snapshot", "error": err}, ensure_ascii=False))
            sys.exit(3)

    if args.refresh_external_tabs:
        if settings.write_mode != "sa":
            print("❌ --refresh-external-tabs 仅支持 SA 模式：设置 SHEETS_WRITE_MODE=sa 或 --write-mode sa")
            sys.exit(2)
        lang = (args.lang or settings.export_lang).strip() or "zh_CN"
        writer = SaSheetsWriter(
            spreadsheet_id=settings.spreadsheet_id,
            credentials_path=settings.sa_credentials_path,
            dashboard_col_l=settings.dashboard_col_l,
            dashboard_col_r=settings.dashboard_col_r,
            dashboard_mode=settings.dashboard_mode,
            dashboard_slot_height=settings.dashboard_slot_height,
            facts_mode=settings.facts_mode,
            share_email=settings.share_email,
            public_read=settings.public_read,
            drive_folder_id=settings.drive_folder_id,
            blob_threshold_chars=settings.blob_threshold_chars,
            timeout_seconds=settings.webhook_timeout_seconds,
            schema_mode=settings.schema_mode,
            local_meta_path=settings.local_meta_path,
        )
        try:
            tab_news = _env_text("SHEETS_TAB_NEWS", "实时新闻")
            hide_news = _env_bool("SHEETS_NEWS_HIDE_TAB", "0")

            deleted_tabs: dict[str, int] = {}
            # 安全：显式清理 Telegram 相关 tab（用户要求只保留实时新闻）
            for t in [_env_text("SHEETS_TAB_TG_MESSAGES", "Telegram消息"), "Telegram频道"]:
                try:
                    deleted_tabs[str(t)] = int(writer.delete_tab_if_exists(title=str(t)).get("deleted") or 0)
                except Exception:
                    deleted_tabs[str(t)] = 0

            res = {
                "ok": True,
                "deleted_tabs": deleted_tabs,
                "news": write_news_tab(writer, tab_news=tab_news, hide_tab=hide_news, lang=lang),
            }

            # 如开启 API tab，则顺手刷新（避免外部消费拿到旧数据）
            _maybe_publish_api_table(writer, force=True, lang=lang)

            print(json.dumps(res, ensure_ascii=False))
            sys.exit(0)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            print(json.dumps({"ok": False, "op": "refresh_external_tabs", "error": err}, ensure_ascii=False))
            sys.exit(3)

    if args.audit_grid:
        if settings.write_mode != "sa":
            print("❌ --audit-grid 仅支持 SA 模式：设置 SHEETS_WRITE_MODE=sa 或 --write-mode sa")
            sys.exit(2)
        writer = SaSheetsWriter(
            spreadsheet_id=settings.spreadsheet_id,
            credentials_path=settings.sa_credentials_path,
            dashboard_col_l=settings.dashboard_col_l,
            dashboard_col_r=settings.dashboard_col_r,
            dashboard_mode=settings.dashboard_mode,
            dashboard_slot_height=settings.dashboard_slot_height,
            facts_mode=settings.facts_mode,
            share_email=settings.share_email,
            public_read=settings.public_read,
            drive_folder_id=settings.drive_folder_id,
            blob_threshold_chars=settings.blob_threshold_chars,
            timeout_seconds=settings.webhook_timeout_seconds,
            schema_mode=settings.schema_mode,
            local_meta_path=settings.local_meta_path,
        )
        try:
            rows = writer.audit_grid_used_range_all(include_hidden=True, value_render_option="FORMULA")
            print(json.dumps({"ok": True, "op": "audit_grid", "rows": rows}, ensure_ascii=False))
            sys.exit(0)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            print(json.dumps({"ok": False, "op": "audit_grid", "error": err}, ensure_ascii=False))
            sys.exit(3)

    if args.audit_top_rows:
        if settings.write_mode != "sa":
            print("❌ --audit-top-rows 仅支持 SA 模式：设置 SHEETS_WRITE_MODE=sa 或 --write-mode sa")
            sys.exit(2)
        writer = SaSheetsWriter(
            spreadsheet_id=settings.spreadsheet_id,
            credentials_path=settings.sa_credentials_path,
            dashboard_col_l=settings.dashboard_col_l,
            dashboard_col_r=settings.dashboard_col_r,
            dashboard_mode=settings.dashboard_mode,
            dashboard_slot_height=settings.dashboard_slot_height,
            facts_mode=settings.facts_mode,
            share_email=settings.share_email,
            public_read=settings.public_read,
            drive_folder_id=settings.drive_folder_id,
            blob_threshold_chars=settings.blob_threshold_chars,
            timeout_seconds=settings.webhook_timeout_seconds,
            schema_mode=settings.schema_mode,
            local_meta_path=settings.local_meta_path,
        )
        try:
            expected_banner, _expected_lines = writer.public_top_banner_text()

            include_hidden = _env_bool("SHEETS_AUDIT_INCLUDE_HIDDEN", "0")
            sheets = writer.list_sheet_properties()
            out_rows: list[dict[str, Any]] = []
            for sh in sheets:
                title = str(sh.get("title") or "").strip()
                hidden = bool(sh.get("hidden") or False)
                gid = int(sh.get("gid") or 0)
                if not title or gid <= 0:
                    continue
                if hidden and (not include_hidden):
                    continue

                a1 = ""
                a2 = ""
                try:
                    got = writer.read_values_a1(title=title, a1_range="A1:A2")
                    if got and got[0]:
                        a1 = str(got[0][0] or "")
                    if len(got) >= 2 and got[1]:
                        a2 = str(got[1][0] or "")
                except Exception as exc:
                    out_rows.append(
                        {
                            "title": title,
                            "gid": gid,
                            "hidden": hidden,
                            "ok": False,
                            "error": f"{type(exc).__name__}:{exc}"[:200],
                        }
                    )
                    continue

                ok_banner = bool(expected_banner) and (a1.strip() == expected_banner.strip())
                ok_meta = ("数据源" in a2) and ("导出时间(UTC+8)" in a2)
                out_rows.append(
                    {
                        "title": title,
                        "gid": gid,
                        "hidden": hidden,
                        "ok": bool(ok_banner and ok_meta),
                        "ok_banner": bool(ok_banner),
                        "ok_meta": bool(ok_meta),
                        "a1_preview": a1.replace("\n", "\\n")[:160],
                        "a2_preview": a2.replace("\n", "\\n")[:200],
                    }
                )

            bad = [r for r in out_rows if not bool(r.get("ok"))]
            print(
                json.dumps(
                    {
                        "ok": True,
                        "op": "audit_top_rows",
                        "expected_banner_preview": expected_banner.replace("\n", "\\n")[:160],
                        "include_hidden": include_hidden,
                        "total": len(out_rows),
                        "bad": len(bad),
                        "rows": out_rows,
                    },
                    ensure_ascii=False,
                )
            )
            sys.exit(0)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            print(json.dumps({"ok": False, "op": "audit_top_rows", "error": err}, ensure_ascii=False))
            sys.exit(3)

    if args.audit_tab_names_public:
        if settings.write_mode != "sa":
            print("❌ --audit-tab-names-public 仅支持 SA 模式：设置 SHEETS_WRITE_MODE=sa 或 --write-mode sa")
            sys.exit(2)
        if audit_public_tab_names is None:
            print(json.dumps({"ok": False, "op": "audit_tab_names_public", "error": "module_unavailable"}, ensure_ascii=False))
            sys.exit(3)
        writer = SaSheetsWriter(
            spreadsheet_id=settings.spreadsheet_id,
            credentials_path=settings.sa_credentials_path,
            dashboard_col_l=settings.dashboard_col_l,
            dashboard_col_r=settings.dashboard_col_r,
            dashboard_mode=settings.dashboard_mode,
            dashboard_slot_height=settings.dashboard_slot_height,
            facts_mode=settings.facts_mode,
            share_email=settings.share_email,
            public_read=settings.public_read,
            drive_folder_id=settings.drive_folder_id,
            blob_threshold_chars=settings.blob_threshold_chars,
            timeout_seconds=settings.webhook_timeout_seconds,
            schema_mode=settings.schema_mode,
            local_meta_path=settings.local_meta_path,
        )
        try:
            sheets = writer.list_sheet_properties()
            titles = [str(s.get("title") or "").strip() for s in sheets if not bool(s.get("hidden") or False)]
            bad = audit_public_tab_names(titles=titles)
            ok = len(bad) == 0
            print(json.dumps({"ok": ok, "op": "audit_tab_names_public", "bad": bad, "total": len(titles)}, ensure_ascii=False))
            sys.exit(0 if ok else 3)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            print(json.dumps({"ok": False, "op": "audit_tab_names_public", "error": err}, ensure_ascii=False))
            sys.exit(3)

    if args.audit_sensitive_public:
        if settings.write_mode != "sa":
            print("❌ --audit-sensitive-public 仅支持 SA 模式：设置 SHEETS_WRITE_MODE=sa 或 --write-mode sa")
            sys.exit(2)
        if audit_sensitive_text_cells is None:
            print(json.dumps({"ok": False, "op": "audit_sensitive_public", "error": "module_unavailable"}, ensure_ascii=False))
            sys.exit(3)
        writer = SaSheetsWriter(
            spreadsheet_id=settings.spreadsheet_id,
            credentials_path=settings.sa_credentials_path,
            dashboard_col_l=settings.dashboard_col_l,
            dashboard_col_r=settings.dashboard_col_r,
            dashboard_mode=settings.dashboard_mode,
            dashboard_slot_height=settings.dashboard_slot_height,
            facts_mode=settings.facts_mode,
            share_email=settings.share_email,
            public_read=settings.public_read,
            drive_folder_id=settings.drive_folder_id,
            blob_threshold_chars=settings.blob_threshold_chars,
            timeout_seconds=settings.webhook_timeout_seconds,
            schema_mode=settings.schema_mode,
            local_meta_path=settings.local_meta_path,
        )
        try:
            api_title = _env_text("SHEETS_API_TAB_TITLE", "API")
            tab_news = _env_text("SHEETS_TAB_NEWS", "实时新闻")
            max_api_rows = _env_int("SHEETS_AUDIT_API_ROWS", 200)
            max_news_rows = _env_int("SHEETS_AUDIT_NEWS_ROWS", 260)

            sheets = writer.list_sheet_properties()
            hits: list[dict[str, Any]] = []
            scanned: list[dict[str, Any]] = []
            for sh in sheets:
                title = str(sh.get("title") or "").strip()
                hidden = bool(sh.get("hidden") or False)
                gid = int(sh.get("gid") or 0)
                if not title or gid <= 0 or hidden:
                    continue

                ranges: list[str] = ["A1:A2"]
                if title == str(api_title or "").strip():
                    ranges.append(f"A1:C{int(max_api_rows)}")
                if title == str(tab_news or "").strip():
                    ranges.append(f"A1:B{int(max_news_rows)}")

                for a1_range in ranges:
                    try:
                        vals = writer.read_values_a1(title=title, a1_range=a1_range)
                    except Exception as exc:
                        hits.append(
                            {
                                "sheet": title,
                                "cell": a1_range,
                                "rule": "read_failed",
                                "excerpt": f"{type(exc).__name__}:{exc}"[:160],
                            }
                        )
                        continue
                    scanned.append({"title": title, "range": a1_range, "rows": len(vals), "cols": max((len(r) for r in vals if isinstance(r, list)), default=0)})
                    for h in audit_sensitive_text_cells(sheet_title=title, a1_range=a1_range, values=vals):
                        hits.append({"sheet": h.sheet, "cell": h.cell, "rule": h.rule, "excerpt": h.excerpt})

            ok = len(hits) == 0
            print(
                json.dumps(
                    {
                        "ok": ok,
                        "op": "audit_sensitive_public",
                        "violations": len(hits),
                        "hits": hits,
                        "scanned": scanned,
                    },
                    ensure_ascii=False,
                )
            )
            sys.exit(0 if ok else 3)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            print(json.dumps({"ok": False, "op": "audit_sensitive_public", "error": err}, ensure_ascii=False))
            sys.exit(3)

    if args.audit_public_styles:
        if settings.write_mode != "sa":
            print("❌ --audit-public-styles 仅支持 SA 模式：设置 SHEETS_WRITE_MODE=sa 或 --write-mode sa")
            sys.exit(2)
        if audit_public_styles is None:
            print(json.dumps({"ok": False, "op": "audit_public_styles", "error": "module_unavailable"}, ensure_ascii=False))
            sys.exit(3)
        writer = SaSheetsWriter(
            spreadsheet_id=settings.spreadsheet_id,
            credentials_path=settings.sa_credentials_path,
            dashboard_col_l=settings.dashboard_col_l,
            dashboard_col_r=settings.dashboard_col_r,
            dashboard_mode=settings.dashboard_mode,
            dashboard_slot_height=settings.dashboard_slot_height,
            facts_mode=settings.facts_mode,
            share_email=settings.share_email,
            public_read=settings.public_read,
            drive_folder_id=settings.drive_folder_id,
            blob_threshold_chars=settings.blob_threshold_chars,
            timeout_seconds=settings.webhook_timeout_seconds,
            schema_mode=settings.schema_mode,
            local_meta_path=settings.local_meta_path,
        )
        try:
            include_hidden = _env_bool("SHEETS_AUDIT_INCLUDE_HIDDEN", "0")
            api_title = _env_text("SHEETS_API_TAB_TITLE", "API")
            tab_news = _env_text("SHEETS_TAB_NEWS", "实时新闻")
            a1_range = _env_text("SHEETS_AUDIT_STYLE_RANGE", "A1:T40")

            res = audit_public_styles(
                writer,
                include_hidden=include_hidden,
                a1_range=a1_range,
                tab_api_title=api_title,
                tab_news_title=tab_news,
            )
            ok = bool(res.get("ok"))
            print(json.dumps({"ok": ok, "op": "audit_public_styles", **res}, ensure_ascii=False))
            sys.exit(0 if ok else 3)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            print(json.dumps({"ok": False, "op": "audit_public_styles", "error": err}, ensure_ascii=False))
            sys.exit(3)

    if args.fix_top_rows:
        if settings.write_mode != "sa":
            print("❌ --fix-top-rows 仅支持 SA 模式：设置 SHEETS_WRITE_MODE=sa 或 --write-mode sa")
            sys.exit(2)
        writer = SaSheetsWriter(
            spreadsheet_id=settings.spreadsheet_id,
            credentials_path=settings.sa_credentials_path,
            dashboard_col_l=settings.dashboard_col_l,
            dashboard_col_r=settings.dashboard_col_r,
            dashboard_mode=settings.dashboard_mode,
            dashboard_slot_height=settings.dashboard_slot_height,
            facts_mode=settings.facts_mode,
            share_email=settings.share_email,
            public_read=settings.public_read,
            drive_folder_id=settings.drive_folder_id,
            blob_threshold_chars=settings.blob_threshold_chars,
            timeout_seconds=settings.webhook_timeout_seconds,
            schema_mode=settings.schema_mode,
            local_meta_path=settings.local_meta_path,
        )
        try:
            include_hidden = _env_bool("SHEETS_AUDIT_INCLUDE_HIDDEN", "0")
            res = writer.fix_workbook_public_top_rows_all(include_hidden=include_hidden, min_frozen_rows=3, dry_run=False)
            print(json.dumps({"ok": True, "op": "fix_top_rows", "result": res}, ensure_ascii=False))
            sys.exit(0)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            print(json.dumps({"ok": False, "op": "fix_top_rows", "error": err}, ensure_ascii=False))
            sys.exit(3)

    if args.reorder_tabs_public:
        if settings.write_mode != "sa":
            print("❌ --reorder-tabs-public 仅支持 SA 模式：设置 SHEETS_WRITE_MODE=sa 或 --write-mode sa")
            sys.exit(2)
        writer = SaSheetsWriter(
            spreadsheet_id=settings.spreadsheet_id,
            credentials_path=settings.sa_credentials_path,
            dashboard_col_l=settings.dashboard_col_l,
            dashboard_col_r=settings.dashboard_col_r,
            dashboard_mode=settings.dashboard_mode,
            dashboard_slot_height=settings.dashboard_slot_height,
            facts_mode=settings.facts_mode,
            share_email=settings.share_email,
            public_read=settings.public_read,
            drive_folder_id=settings.drive_folder_id,
            blob_threshold_chars=settings.blob_threshold_chars,
            timeout_seconds=settings.webhook_timeout_seconds,
            schema_mode=settings.schema_mode,
            local_meta_path=settings.local_meta_path,
        )
        try:
            dashboard_title = _env_text("SHEETS_TAB_DASHBOARD", "加密货币看板")
            macro_title = _env_text("SHEETS_TAB_MACRO_DASHBOARD", "宏观大宗看板")
            api_title = _env_text("SHEETS_API_TAB_TITLE", "API")
            tab_news = _env_text("SHEETS_TAB_NEWS", "实时新闻")
            sym_prefix = _env_text("SHEETS_SYMBOL_TAB_PREFIX", "币种查询_")
            pm_top15 = _env_text("SHEETS_TAB_POLYMARKET_TOP15", "PolymarketTop15")
            pm_timeslot = _env_text("SHEETS_TAB_POLYMARKET_TIMESLOT", "Polymarket时段分布")
            pm_category = _env_text("SHEETS_TAB_POLYMARKET_CATEGORY", "Polymarket类别偏好")

            sheets = writer.list_sheet_properties()
            visible = [s for s in sheets if not bool(s.get("hidden") or False)]
            hidden = [s for s in sheets if bool(s.get("hidden") or False)]

            visible_titles = [str(s.get("title") or "").strip() for s in visible if str(s.get("title") or "").strip()]
            hidden_titles = [str(s.get("title") or "").strip() for s in hidden if str(s.get("title") or "").strip()]

            desired_visible: list[str] = []

            def add(t: str) -> None:
                tt = str(t or "").strip()
                if not tt:
                    return
                if tt in visible_titles and tt not in desired_visible:
                    desired_visible.append(tt)

            # 1) dashboards（主视觉入口）
            add(dashboard_title)
            add(macro_title)
            # 2) API / News（对外消费入口）
            add(api_title)
            add(tab_news)
            # 3) symbol query tabs（固定前缀）
            for t in sorted([x for x in visible_titles if sym_prefix and x.startswith(sym_prefix)]):
                add(t)
            # 4) polymarket split tabs（固定顺序）
            for t in [pm_top15, pm_timeslot, pm_category]:
                add(t)
            # 5) remaining visible tabs（保持原顺序）
            for t in visible_titles:
                add(t)

            final_titles = [*desired_visible, *hidden_titles]

            id_by_title = {str(s.get("title") or "").strip(): int(s.get("gid") or 0) for s in sheets}
            requests: list[dict[str, Any]] = []
            for idx, t in enumerate(final_titles):
                sid = int(id_by_title.get(t) or 0)
                if sid <= 0:
                    continue
                requests.append(
                    {
                        "updateSheetProperties": {
                            "properties": {"sheetId": int(sid), "index": int(idx)},
                            "fields": "index",
                        }
                    }
                )

            if args.dry_run:
                print(
                    json.dumps(
                        {"ok": True, "op": "reorder_tabs_public", "dry_run": True, "visible_order": desired_visible},
                        ensure_ascii=False,
                    )
                )
                sys.exit(0)

            writer.batch_update(requests=requests)
            print(
                json.dumps(
                    {"ok": True, "op": "reorder_tabs_public", "dry_run": False, "visible_order": desired_visible},
                    ensure_ascii=False,
                )
            )
            sys.exit(0)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            print(json.dumps({"ok": False, "op": "reorder_tabs_public", "error": err}, ensure_ascii=False))
            sys.exit(3)

    if args.compact_grid_public:
        if settings.write_mode != "sa":
            print("❌ --compact-grid-public 仅支持 SA 模式：设置 SHEETS_WRITE_MODE=sa 或 --write-mode sa")
            sys.exit(2)
        writer = SaSheetsWriter(
            spreadsheet_id=settings.spreadsheet_id,
            credentials_path=settings.sa_credentials_path,
            dashboard_col_l=settings.dashboard_col_l,
            dashboard_col_r=settings.dashboard_col_r,
            dashboard_mode=settings.dashboard_mode,
            dashboard_slot_height=settings.dashboard_slot_height,
            facts_mode=settings.facts_mode,
            share_email=settings.share_email,
            public_read=settings.public_read,
            drive_folder_id=settings.drive_folder_id,
            blob_threshold_chars=settings.blob_threshold_chars,
            timeout_seconds=settings.webhook_timeout_seconds,
            schema_mode=settings.schema_mode,
            local_meta_path=settings.local_meta_path,
        )
        try:
            res = writer.compact_grid_to_used_range_all(
                include_hidden=False,
                value_render_option="FORMULA",
                dry_run=bool(args.dry_run),
            )
            print(json.dumps({"ok": True, "op": "compact_grid_public", **res}, ensure_ascii=False))
            sys.exit(0)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            print(json.dumps({"ok": False, "op": "compact_grid_public", "error": err}, ensure_ascii=False))
            sys.exit(3)

    if args.compact_grid_all:
        if settings.write_mode != "sa":
            print("❌ --compact-grid-all 仅支持 SA 模式：设置 SHEETS_WRITE_MODE=sa 或 --write-mode sa")
            sys.exit(2)
        writer = SaSheetsWriter(
            spreadsheet_id=settings.spreadsheet_id,
            credentials_path=settings.sa_credentials_path,
            dashboard_col_l=settings.dashboard_col_l,
            dashboard_col_r=settings.dashboard_col_r,
            dashboard_mode=settings.dashboard_mode,
            dashboard_slot_height=settings.dashboard_slot_height,
            facts_mode=settings.facts_mode,
            share_email=settings.share_email,
            public_read=settings.public_read,
            drive_folder_id=settings.drive_folder_id,
            blob_threshold_chars=settings.blob_threshold_chars,
            timeout_seconds=settings.webhook_timeout_seconds,
            schema_mode=settings.schema_mode,
            local_meta_path=settings.local_meta_path,
        )
        try:
            res = writer.compact_grid_to_used_range_all(
                include_hidden=True,
                value_render_option="FORMULA",
                dry_run=bool(args.dry_run),
            )
            print(json.dumps({"ok": True, "op": "compact_grid_all", **res}, ensure_ascii=False))
            sys.exit(0)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            print(json.dumps({"ok": False, "op": "compact_grid_all", "error": err}, ensure_ascii=False))
            sys.exit(3)

    if args.style_borderless_all:
        if settings.write_mode != "sa":
            print("❌ --style-borderless-all 仅支持 SA 模式：设置 SHEETS_WRITE_MODE=sa 或 --write-mode sa")
            sys.exit(2)
        writer = SaSheetsWriter(
            spreadsheet_id=settings.spreadsheet_id,
            credentials_path=settings.sa_credentials_path,
            dashboard_col_l=settings.dashboard_col_l,
            dashboard_col_r=settings.dashboard_col_r,
            dashboard_mode=settings.dashboard_mode,
            dashboard_slot_height=settings.dashboard_slot_height,
            facts_mode=settings.facts_mode,
            share_email=settings.share_email,
            public_read=settings.public_read,
            drive_folder_id=settings.drive_folder_id,
            blob_threshold_chars=settings.blob_threshold_chars,
            timeout_seconds=settings.webhook_timeout_seconds,
            schema_mode=settings.schema_mode,
            local_meta_path=settings.local_meta_path,
        )
        try:
            res = writer.style_workbook_borderless_all(include_hidden=True)
            print(json.dumps(res, ensure_ascii=False))
            sys.exit(0)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            print(json.dumps({"ok": False, "op": "style_borderless_all", "error": err}, ensure_ascii=False))
            sys.exit(3)

    if args.dry_run:
        settings = replace(settings, dry_run=True)
    if args.force:
        settings = replace(settings, force_render=True)

    only_cards = [c.strip() for c in (args.cards or settings.export_cards).split(",") if c.strip()] or None
    lang = (args.lang or settings.export_lang).strip() or "zh_CN"

    if args.daemon_news:
        if args.daemon or args.once:
            print("❌ 不能同时指定 --daemon-news 与 --daemon/--once")
            sys.exit(2)
        if settings.write_mode != "sa":
            print("❌ --daemon-news 仅支持 SA 模式：设置 SHEETS_WRITE_MODE=sa 或 --write-mode sa")
            sys.exit(2)

        tab_news = _env_text("SHEETS_TAB_NEWS", "实时新闻")
        hide_news = _env_bool("SHEETS_NEWS_HIDE_TAB", "0")

        interval = float(args.daemon_news_interval_seconds or 0.0)
        if interval <= 0:
            interval = _env_float("SHEETS_NEWS_DAEMON_INTERVAL_SECONDS", 5.0)
        interval = max(float(interval), 0.2)

        log_interval = _env_int("SHEETS_NEWS_DAEMON_LOG_INTERVAL_SECONDS", 30)

        writer = _new_sa_writer(settings)
        _start_news_style_worker(settings, tab_news=tab_news, hide_news=hide_news)

        last_log_at = 0.0
        attempt = 0
        while True:
            started_at = time.time()
            try:
                res = write_news_values_only(writer, tab_news=tab_news, lang=lang)
                attempt = 0
                if log_interval > 0 and (time.time() - last_log_at) >= float(log_interval):
                    last_log_at = time.time()
                    print(
                        json.dumps(
                            {"ok": True, "op": "daemon_news", "interval_seconds": interval, "news": res},
                            ensure_ascii=False,
                        )
                    )
            except Exception as exc:
                attempt += 1
                print(f"⚠️ 实时新闻刷新失败：{type(exc).__name__}: {exc}")
                _sleep_backoff(attempt, base=2.0, max_seconds=60.0)
                continue

            elapsed = time.time() - started_at
            sleep_s = max(interval - elapsed, 0.0)
            time.sleep(max(sleep_s, 0.05))

    if args.daemon and args.once:
        print("❌ 不能同时指定 --daemon 和 --once")
        sys.exit(2)

    if args.daemon:
        while True:
            rc = asyncio.run(
                _run_once(
                    settings,
                    only_cards=only_cards,
                    lang=lang,
                    dashboard_variants=args.dashboard_variants,
                    dashboard_variants_only=args.dashboard_variants_only,
                )
            )
            if rc == 2:
                print("❌ sheets-service 配置错误，daemon 退出；请先修复配置后再启动")
                sys.exit(2)
            if rc == 0 and _publish_guard_enabled() and (not settings.dry_run) and settings.write_mode == "sa":
                try:
                    guard = _run_public_publish_guard(settings, lang=lang)
                    print(json.dumps({"ok": bool(guard.get("ok")), "op": "public_publish_guard", **guard}, ensure_ascii=False))
                    if not bool(guard.get("ok")):
                        rc = 3
                except Exception as exc:
                    print(f"⚠️ public_publish_guard_failed {type(exc).__name__}: {exc}")
                    rc = 3
            if rc != 0:
                # daemon 模式失败：不退出，避免写入临时错误导致服务退出；由外层守护脚本管理
                print(f"⚠️ 本轮执行失败 rc={rc}，{settings.interval_seconds}s 后重试")
            time.sleep(max(settings.interval_seconds, 5))
    else:
        rc = asyncio.run(
            _run_once(
                settings,
                only_cards=only_cards,
                lang=lang,
                dashboard_variants=args.dashboard_variants,
                dashboard_variants_only=args.dashboard_variants_only,
            )
        )
        if rc == 0 and _publish_guard_enabled() and (not settings.dry_run) and settings.write_mode == "sa":
            try:
                guard = _run_public_publish_guard(settings, lang=lang)
                print(json.dumps({"ok": bool(guard.get("ok")), "op": "public_publish_guard", **guard}, ensure_ascii=False))
                if not bool(guard.get("ok")):
                    rc = 3
            except Exception as exc:
                print(f"⚠️ public_publish_guard_failed {type(exc).__name__}: {exc}")
                rc = 3
        sys.exit(rc)


if __name__ == "__main__":
    main()
