from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from src.sa_sheets_writer import SaSheetsWriter
from src.public_meta import format_public_meta_row_text


@dataclass(frozen=True)
class _Series:
    series_key: str
    wide_slug: str
    name_cn: str
    category: str
    unit: str
    refresh_policy: str
    is_public: int
    notes: str = ""


def _index_to_col(idx_1: int) -> str:
    n = int(idx_1)
    if n <= 0:
        return "A"
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


def _macro_series_mvp() -> tuple[list[_Series], list[_Series], list[_Series], list[_Series], list[_Series], list[_Series], list[_Series]]:
    """
    返回：(commodities, fx, indices, etfs, risk, crypto, derived)
    wide_slug 规则：全部小写 snake，前缀 com_/fx_/idx_/etf_/risk_/crypto_/ratio_/spread_，与 10_SNAPSHOT_WIDE 列名一致。
    """
    commodities = [
        _Series("COM.GOLD", "com_gold", "黄金", "commodities", "usd_per_oz", "intraday_15m", 1),
        _Series("COM.SILVER", "com_silver", "白银", "commodities", "usd_per_oz", "intraday_15m", 1),
        _Series("COM.OIL_WTI", "com_oil_wti", "WTI原油", "commodities", "usd_per_bbl", "intraday_15m", 1),
        _Series("COM.OIL_BRENT", "com_oil_brent", "布伦特原油", "commodities", "usd_per_bbl", "intraday_15m", 1),
        _Series("COM.NATGAS", "com_natgas", "天然气", "commodities", "usd_per_mmbtu", "intraday_15m", 1),
        _Series("COM.GASOLINE", "com_gasoline", "汽油(RBOB)", "commodities", "usd_per_gal", "intraday_15m", 1),
        _Series("COM.COPPER", "com_copper", "铜", "commodities", "usd_per_lb", "intraday_15m", 1),
        _Series("COM.CORN", "com_corn", "玉米", "commodities", "cents_per_bu", "intraday_15m", 1),
        _Series("COM.SOY", "com_soy", "大豆", "commodities", "cents_per_bu", "intraday_15m", 1),
        _Series("COM.WHEAT", "com_wheat", "小麦", "commodities", "cents_per_bu", "intraday_15m", 1),
    ]

    fx = [
        _Series("FX.DXY", "fx_dxy", "美元指数(DXY)", "fx", "index", "intraday_15m", 1),
        _Series("FX.EURUSD", "fx_eurusd", "欧元/美元", "fx", "fx_rate", "intraday_15m", 1),
        _Series("FX.USDJPY", "fx_usdjpy", "美元/日元", "fx", "fx_rate", "intraday_15m", 1),
        _Series("FX.GBPUSD", "fx_gbpusd", "英镑/美元", "fx", "fx_rate", "intraday_15m", 1),
        _Series("FX.USDCNY", "fx_usdcny", "美元/人民币", "fx", "fx_rate", "intraday_15m", 1),
        _Series("FX.AUDUSD", "fx_audusd", "澳元/美元", "fx", "fx_rate", "intraday_15m", 1),
        _Series("FX.NZDUSD", "fx_nzdusd", "纽元/美元", "fx", "fx_rate", "intraday_15m", 1),
        _Series("FX.USDCAD", "fx_usdcad", "美元/加元", "fx", "fx_rate", "intraday_15m", 1),
        _Series("FX.USDCHF", "fx_usdchf", "美元/瑞郎", "fx", "fx_rate", "intraday_15m", 1),
    ]

    indices = [
        _Series("IDX.SPX", "idx_spx", "标普500(SPX)", "indices", "index", "intraday_15m", 1),
        _Series("IDX.NDX", "idx_ndx", "纳斯达克100(NDX)", "indices", "index", "intraday_15m", 1),
        _Series("IDX.DJI", "idx_dji", "道琼斯(DJI)", "indices", "index", "intraday_15m", 1),
        _Series("IDX.RUT", "idx_rut", "罗素2000(RUT)", "indices", "index", "intraday_15m", 1),
        _Series("IDX.SSE", "idx_sse", "上证指数(SSE)", "indices", "index", "intraday_15m", 1),
        _Series("IDX.CSI300", "idx_csi300", "沪深300(CSI300)", "indices", "index", "intraday_15m", 1),
    ]

    etfs = [
        _Series("ETF.SPY", "etf_spy", "SPY", "etfs", "usd", "intraday_15m", 1),
        _Series("ETF.QQQ", "etf_qqq", "QQQ", "etfs", "usd", "intraday_15m", 1),
        _Series("ETF.IWM", "etf_iwm", "IWM", "etfs", "usd", "intraday_15m", 1),
        _Series("ETF.TLT", "etf_tlt", "TLT(美债20+年)", "etfs", "usd", "intraday_15m", 1),
        _Series("ETF.HYG", "etf_hyg", "HYG(高收益债)", "etfs", "usd", "intraday_15m", 1),
        _Series("ETF.GLD", "etf_gld", "GLD(黄金ETF)", "etfs", "usd", "intraday_15m", 1),
        _Series("ETF.USO", "etf_uso", "USO(原油ETF)", "etfs", "usd", "intraday_15m", 1),
        _Series("ETF.UUP", "etf_uup", "UUP(美元多头)", "etfs", "usd", "intraday_15m", 1),
    ]

    risk = [
        _Series("RISK.VIX", "risk_vix", "VIX恐慌指数", "risk", "index", "intraday_15m", 1),
        _Series("RISK.VVIX", "risk_vvix", "VVIX(VIX波动率)", "risk", "index", "intraday_15m", 1),
        _Series("RISK.SKEW", "risk_skew", "SKEW(尾部风险)", "risk", "index", "intraday_15m", 1),
        _Series("RISK.MOVE", "risk_move", "MOVE(债券波动率)", "risk", "index", "intraday_15m", 1),
    ]

    crypto = [
        _Series("CRYPTO.BTC", "crypto_btc", "比特币(BTC)", "crypto", "usd", "intraday_15m", 1),
        _Series("CRYPTO.ETH", "crypto_eth", "以太坊(ETH)", "crypto", "usd", "intraday_15m", 1),
    ]

    derived = [
        _Series("RATIO.GOLD_SILVER", "ratio_gold_silver", "金银比", "derived", "ratio", "intraday_15m", 1, "GOLD/SILVER"),
        _Series("RATIO.GOLD_OIL_WTI", "ratio_gold_oil_wti", "金油比(WTI)", "derived", "ratio", "intraday_15m", 1, "GOLD/WTI"),
        _Series(
            "RATIO.GOLD_OIL_BRENT",
            "ratio_gold_oil_brent",
            "金油比(Brent)",
            "derived",
            "ratio",
            "intraday_15m",
            1,
            "GOLD/BRENT",
        ),
        _Series(
            "RATIO.SILVER_OIL_WTI",
            "ratio_silver_oil_wti",
            "银油比(WTI)",
            "derived",
            "ratio",
            "intraday_15m",
            1,
            "SILVER/WTI",
        ),
        _Series(
            "RATIO.SILVER_OIL_BRENT",
            "ratio_silver_oil_brent",
            "银油比(Brent)",
            "derived",
            "ratio",
            "intraday_15m",
            1,
            "SILVER/BRENT",
        ),
        _Series("RATIO.COPPER_GOLD", "ratio_copper_gold", "铜金比", "derived", "ratio", "intraday_15m", 1, "COPPER/GOLD"),
        _Series("SPREAD.BRENT_WTI", "spread_brent_wti", "布油-美油价差", "derived", "usd_spread", "intraday_15m", 1, "BRENT-WTI"),
        _Series("RATIO.SPX_VIX", "ratio_spx_vix", "SPX/VIX", "derived", "ratio", "intraday_15m", 1, "SPX/VIX"),
        _Series("RATIO.VVIX_VIX", "ratio_vvix_vix", "VVIX/VIX", "derived", "ratio", "intraday_15m", 1, "VVIX/VIX"),
        _Series("RATIO.MOVE_VIX", "ratio_move_vix", "MOVE/VIX", "derived", "ratio", "intraday_15m", 1, "MOVE/VIX"),
        _Series("RATIO.QQQ_SPY", "ratio_qqq_spy", "QQQ/SPY", "derived", "ratio", "intraday_15m", 1, "QQQ/SPY"),
        _Series("RATIO.SPY_TLT", "ratio_spy_tlt", "SPY/TLT", "derived", "ratio", "intraday_15m", 1, "SPY/TLT"),
        _Series("RATIO.HYG_TLT", "ratio_hyg_tlt", "HYG/TLT", "derived", "ratio", "intraday_15m", 1, "HYG/TLT"),
        _Series("RATIO.BTC_ETH", "ratio_btc_eth", "BTC/ETH", "derived", "ratio", "intraday_15m", 1, "BTC/ETH"),
        _Series("RATIO.BTC_OIL_WTI", "ratio_btc_oil_wti", "BTC/原油(WTI)", "derived", "ratio", "intraday_15m", 1, "BTC/WTI"),
        _Series("RATIO.ETH_OIL_WTI", "ratio_eth_oil_wti", "ETH/原油(WTI)", "derived", "ratio", "intraday_15m", 1, "ETH/WTI"),
        _Series("RATIO.GOLD_BTC", "ratio_gold_btc", "黄金/BTC", "derived", "ratio", "intraday_15m", 1, "GOLD/BTC"),
    ]
    return commodities, fx, indices, etfs, risk, crypto, derived


def _snapshot_headers(
    *,
    commodities: list[_Series],
    fx: list[_Series],
    indices: list[_Series],
    etfs: list[_Series],
    risk: list[_Series],
    crypto: list[_Series],
    derived: list[_Series],
) -> list[str]:
    headers = ["datetime_bj", "updated_at_utc"]
    for s in [*commodities, *fx, *indices, *etfs, *risk, *crypto, *derived]:
        for m in ("last", "ret_1d", "ret_5d", "ret_20d"):
            headers.append(f"{s.wide_slug}__{m}")
    return headers


def _a1_quote_title(title: str) -> str:
    t = str(title or "").replace("'", "''")
    return f"'{t}'"


def _safe_pick_snapshot(*, tab_snapshot: str, header: str) -> str:
    """
    从 10_SNAPSHOT_WIDE 第2行按 header 取值。
    用 MATCH(header, 1:1) 找列，INDEX(2:2) 取值；IFERROR 避免空表报错。
    """
    h = str(header).replace('"', '""')
    q = _a1_quote_title(tab_snapshot)
    return f'=IFERROR(INDEX({q}!2:2, MATCH("{h}", {q}!1:1, 0)), "")'


def publish_macro_display(
    writer: SaSheetsWriter,
    *,
    tab_dashboard: str = "宏观大宗看板",
    tab_snapshot: str = "10_SNAPSHOT_WIDE",
    tab_series_map: str = "90_SERIES_MAP",
    tab_refresh_log: str = "98_REFRESH_LOG",
    hide_support_tabs: bool = True,
) -> dict[str, Any]:
    """
    创建/刷新“宏观大宗展示页”与其依赖的基础表（快照/注册表/刷新日志）。
    目标：用户打开 Google Sheets 就能直接看到“看板式表格”。
    """
    commodities, fx, indices, etfs, risk, crypto, derived = _macro_series_mvp()
    headers = _snapshot_headers(
        commodities=commodities,
        fx=fx,
        indices=indices,
        etfs=etfs,
        risk=risk,
        crypto=crypto,
        derived=derived,
    )

    banner_text, banner_line_count = writer.public_top_banner_text()
    if not banner_text:
        banner_text, banner_line_count = "广告位", 1

    # -------------------- 10_SNAPSHOT_WIDE --------------------
    col_r = _index_to_col(len(headers))
    # 重要：展示页刷新不应“擦掉数据”。10_SNAPSHOT_WIDE 的第2行由 refresh_macro_snapshot 写入，
    # publish_macro_display 只负责：表结构/样式/裁剪/冻结等“展示面”，因此需要尽量保留旧的 row2。
    preserved_row2_by_header: dict[str, Any] = {}
    try:
        # 必须用 UNFORMATTED_VALUE，否则数值会被读取为格式化字符串，再写回就会“变成文本”，导致百分比/小数格式失效。
        q = _a1_quote_title(tab_snapshot)
        got = writer._exec(  # type: ignore[attr-defined]
            writer._sheets.spreadsheets()  # type: ignore[attr-defined]
            .values()
            .get(
                spreadsheetId=writer._spreadsheet_id,  # type: ignore[attr-defined]
                range=f"{q}!1:2",
                valueRenderOption="UNFORMATTED_VALUE",
            ),
            is_write=False,
        )
        values = got.get("values") or []
        old_headers = (values[0] if values and isinstance(values[0], list) else []) or []
        old_row2 = (values[1] if len(values) >= 2 and isinstance(values[1], list) else []) or []
        for i, h in enumerate(old_headers):
            hs = str(h or "").strip()
            if not hs:
                continue
            v = old_row2[i] if i < len(old_row2) else ""
            if v != "":
                preserved_row2_by_header[hs] = v
    except Exception:
        preserved_row2_by_header = {}

    writer.reset_sheet_display(
        title=tab_snapshot,
        col_l="A",
        col_r=col_r,
        compact=True,
        frozen_row_count=1,
        frozen_column_count=0,
    )
    snapshot_row2 = [preserved_row2_by_header.get(h, "") for h in headers]
    snapshot_values: list[list[Any]] = [headers, snapshot_row2]
    writer.write_values_matrix(title=tab_snapshot, values=snapshot_values, value_input_option="RAW")
    try:
        # 智能裁剪：快照表是完全托管展示面（2 行），允许精确收缩网格
        writer._set_sheet_grid_properties(
            tab_snapshot,
            row_count=2,
            col_count=len(headers),
            frozen_row_count=1,
            frozen_column_count=0,
        )
    except Exception:
        pass

    # -------------------- 90_SERIES_MAP --------------------
    series_map_headers = [
        "series_key",
        "wide_slug",
        "name_cn",
        "category",
        "unit",
        "refresh_policy",
        "is_public",
        "notes",
    ]
    series_rows: list[list[Any]] = [series_map_headers]
    for s in [*commodities, *fx, *indices, *etfs, *risk, *crypto, *derived]:
        series_rows.append(
            [s.series_key, s.wide_slug, s.name_cn, s.category, s.unit, s.refresh_policy, int(s.is_public), s.notes]
        )
    writer.reset_sheet_display(
        title=tab_series_map,
        col_l="A",
        col_r=_index_to_col(len(series_map_headers)),
        compact=True,
        frozen_row_count=1,
        frozen_column_count=0,
    )
    writer.write_values_matrix(title=tab_series_map, values=series_rows, value_input_option="RAW")
    try:
        # 智能裁剪：注册表是完全托管配置面，允许精确收缩网格
        writer._set_sheet_grid_properties(
            tab_series_map,
            row_count=len(series_rows),
            col_count=len(series_map_headers),
            frozen_row_count=1,
            frozen_column_count=0,
        )
    except Exception:
        pass

    # -------------------- 98_REFRESH_LOG --------------------
    refresh_headers = [
        "run_id",
        "job_name",
        "status",
        "started_at_utc",
        "finished_at_utc",
        "duration_ms",
        "rows_written",
        "error_message",
    ]
    writer.reset_sheet_display(
        title=tab_refresh_log,
        col_l="A",
        col_r=_index_to_col(len(refresh_headers)),
        compact=True,
        frozen_row_count=1,
        frozen_column_count=0,
    )
    writer.write_values_matrix(title=tab_refresh_log, values=[refresh_headers], value_input_option="RAW")
    try:
        # 智能裁剪：日志表目前只保留表头（未来若要 append，会自动扩容）
        writer._set_sheet_grid_properties(
            tab_refresh_log,
            row_count=2,  # frozen=1 -> 最小 2
            col_count=len(refresh_headers),
            frozen_row_count=1,
            frozen_column_count=0,
        )
    except Exception:
        pass

    # -------------------- 宏观大宗看板（tab_dashboard） --------------------
    # A-F 共 6 列（用户只要核心展示字段；不显示“单位/口径”）
    dash_cols = 6
    dash_col_r = _index_to_col(dash_cols)
    frozen_rows_final = 3  # banner + meta + header
    frozen_cols_final = 2  # 冻结“类别 + 品种”，横向滚动时保持品种可见
    writer.reset_sheet_display(
        title=tab_dashboard,
        col_l="A",
        col_r=dash_col_r,
        compact=True,
        # 注意：Google Sheets API 有一个坑：当当前 frozenRowCount 较大时，若在同一次 updateSheetProperties 里
        # 同时把 rowCount 缩得很小 + 下调 frozenRowCount，可能会报
        # “it is not possible to delete all non-frozen rows”。
        # 这里先用 >= 6 的冻结行数完成 compact，稍后在 batch_update 里再把冻结行改成 frozen_rows_final。
        frozen_row_count=max(int(frozen_rows_final), 6),
        frozen_column_count=int(frozen_cols_final),
    )

    def row(*cells: Any) -> list[Any]:
        r = list(cells)
        if len(r) < dash_cols:
            r.extend([""] * (dash_cols - len(r)))
        return r[:dash_cols]

    values: list[list[Any]] = []

    # 统一首行：复用全局 banner（与其它表一致）
    row_banner = len(values)
    values.append(row(banner_text))

    # 元信息行（单单元格）
    tz8 = timezone(timedelta(hours=8))
    export_ts = datetime.now(timezone.utc).astimezone(tz8).replace(microsecond=0).isoformat()
    interval_s: float | None = None
    for k in ["SHEETS_MACRO_SNAPSHOT_INTERVAL_SECONDS", "SHEETS_SYNC_INTERVAL_SECONDS"]:
        raw = (os.environ.get(k, "") or "").strip()
        if not raw:
            continue
        try:
            interval_s = float(raw)
        except Exception:
            interval_s = None
        break
    total_series = int(len(commodities) + len(fx) + len(indices) + len(etfs) + len(risk) + len(crypto) + len(derived))
    meta_text = format_public_meta_row_text(
        dataset=str(tab_dashboard),
        exported_at_utc8=str(export_ts),
        interval_seconds=interval_s,
        window=None,
        row_count=total_series,
        lang=(os.environ.get("SHEETS_EXPORT_LANG", "zh_CN") or "zh_CN").strip() or "zh_CN",
        mode=(os.environ.get("SHEETS_WRITE_MODE", "") or "").strip().lower() or "sa",
        schema="macro_dashboard_v1",
    )
    row_meta = len(values)
    values.append(row(meta_text))

    # commodities table
    row_commod_header = len(values)
    values.append(row("类别", "品种", "最新", "1日%", "5日%", "20日%"))
    row_commod_data_start = len(values)
    for s in commodities:
        values.append(
            row(
                "大宗",
                s.name_cn,
                _safe_pick_snapshot(tab_snapshot=tab_snapshot, header=f"{s.wide_slug}__last"),
                _safe_pick_snapshot(tab_snapshot=tab_snapshot, header=f"{s.wide_slug}__ret_1d"),
                _safe_pick_snapshot(tab_snapshot=tab_snapshot, header=f"{s.wide_slug}__ret_5d"),
                _safe_pick_snapshot(tab_snapshot=tab_snapshot, header=f"{s.wide_slug}__ret_20d"),
            )
        )
    row_commod_data_end = len(values)

    # fx section（核心外汇）
    row_fx_data_start = len(values)
    for s in fx:
        values.append(
            row(
                "外汇",
                s.name_cn,
                _safe_pick_snapshot(tab_snapshot=tab_snapshot, header=f"{s.wide_slug}__last"),
                _safe_pick_snapshot(tab_snapshot=tab_snapshot, header=f"{s.wide_slug}__ret_1d"),
                _safe_pick_snapshot(tab_snapshot=tab_snapshot, header=f"{s.wide_slug}__ret_5d"),
                _safe_pick_snapshot(tab_snapshot=tab_snapshot, header=f"{s.wide_slug}__ret_20d"),
            )
        )
    row_fx_data_end = len(values)

    # indices section（主要股指）
    row_indices_data_start = len(values)
    for s in indices:
        values.append(
            row(
                "指数",
                s.name_cn,
                _safe_pick_snapshot(tab_snapshot=tab_snapshot, header=f"{s.wide_slug}__last"),
                _safe_pick_snapshot(tab_snapshot=tab_snapshot, header=f"{s.wide_slug}__ret_1d"),
                _safe_pick_snapshot(tab_snapshot=tab_snapshot, header=f"{s.wide_slug}__ret_5d"),
                _safe_pick_snapshot(tab_snapshot=tab_snapshot, header=f"{s.wide_slug}__ret_20d"),
            )
        )
    row_indices_data_end = len(values)

    # etfs section（核心ETF）
    row_etfs_data_start = len(values)
    for s in etfs:
        values.append(
            row(
                "ETF",
                s.name_cn,
                _safe_pick_snapshot(tab_snapshot=tab_snapshot, header=f"{s.wide_slug}__last"),
                _safe_pick_snapshot(tab_snapshot=tab_snapshot, header=f"{s.wide_slug}__ret_1d"),
                _safe_pick_snapshot(tab_snapshot=tab_snapshot, header=f"{s.wide_slug}__ret_5d"),
                _safe_pick_snapshot(tab_snapshot=tab_snapshot, header=f"{s.wide_slug}__ret_20d"),
            )
        )
    row_etfs_data_end = len(values)

    # risk section（恐慌/风险指数）
    row_risk_data_start = len(values)
    for s in risk:
        values.append(
            row(
                "恐慌指数",
                s.name_cn,
                _safe_pick_snapshot(tab_snapshot=tab_snapshot, header=f"{s.wide_slug}__last"),
                _safe_pick_snapshot(tab_snapshot=tab_snapshot, header=f"{s.wide_slug}__ret_1d"),
                _safe_pick_snapshot(tab_snapshot=tab_snapshot, header=f"{s.wide_slug}__ret_5d"),
                _safe_pick_snapshot(tab_snapshot=tab_snapshot, header=f"{s.wide_slug}__ret_20d"),
            )
        )
    row_risk_data_end = len(values)

    # crypto section（核心加密货币）
    row_crypto_data_start = len(values)
    for s in crypto:
        values.append(
            row(
                "加密货币",
                s.name_cn,
                _safe_pick_snapshot(tab_snapshot=tab_snapshot, header=f"{s.wide_slug}__last"),
                _safe_pick_snapshot(tab_snapshot=tab_snapshot, header=f"{s.wide_slug}__ret_1d"),
                _safe_pick_snapshot(tab_snapshot=tab_snapshot, header=f"{s.wide_slug}__ret_5d"),
                _safe_pick_snapshot(tab_snapshot=tab_snapshot, header=f"{s.wide_slug}__ret_20d"),
            )
        )
    row_crypto_data_end = len(values)

    # ratios section
    row_ratio_data_start = len(values)
    for s in derived:
        values.append(
            row(
                "比值/价差",
                s.name_cn,
                _safe_pick_snapshot(tab_snapshot=tab_snapshot, header=f"{s.wide_slug}__last"),
                _safe_pick_snapshot(tab_snapshot=tab_snapshot, header=f"{s.wide_slug}__ret_1d"),
                _safe_pick_snapshot(tab_snapshot=tab_snapshot, header=f"{s.wide_slug}__ret_5d"),
                _safe_pick_snapshot(tab_snapshot=tab_snapshot, header=f"{s.wide_slug}__ret_20d"),
            )
        )
    row_ratio_data_end = len(values)

    dash_rows = len(values)

    writer.write_values_matrix(title=tab_dashboard, values=values, value_input_option="USER_ENTERED")

    sh_id = writer.sheet_id(tab_dashboard)
    reqs: list[dict[str, Any]] = []

    # 冻结行：只冻结 banner + 表头（最终值）
    reqs.append(
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": int(sh_id),
                    "gridProperties": {
                        "frozenRowCount": int(frozen_rows_final),
                        "frozenColumnCount": int(frozen_cols_final),
                    },
                },
                "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount,gridProperties.hideGridlines",
            }
        }
    )
    # 无边框：隐藏 gridlines（等价于 UI 的 View -> Show -> Gridlines 关闭）
    reqs[-1]["updateSheetProperties"]["properties"]["gridProperties"]["hideGridlines"] = True

    # merges
    def merge(r0: int, r1: int, c0: int, c1: int) -> None:
        reqs.append(
            {
                "mergeCells": {
                    "range": {
                        "sheetId": int(sh_id),
                        "startRowIndex": int(r0),
                        "endRowIndex": int(r1),
                        "startColumnIndex": int(c0),
                        "endColumnIndex": int(c1),
                    },
                    "mergeType": "MERGE_ALL",
                }
            }
        )

    # banner/meta：不做 merge。
    # 原因：冻结列场景下，banner 若被合并到“冻结区块”内，会导致长链接无法横向溢出而在 UI 里被截断。
    # 这里依赖 hideGridlines + 顶部行样式/行高，仍能获得“单行广告位/元信息”的视觉效果。

    # A 列“类别”合并：同类只显示一次（参考加密货币看板的分组样式）
    if int(row_commod_data_end) - int(row_commod_data_start) > 1:
        merge(int(row_commod_data_start), int(row_commod_data_end), 0, 1)
    if int(row_fx_data_end) - int(row_fx_data_start) > 1:
        merge(int(row_fx_data_start), int(row_fx_data_end), 0, 1)
    if int(row_indices_data_end) - int(row_indices_data_start) > 1:
        merge(int(row_indices_data_start), int(row_indices_data_end), 0, 1)
    if int(row_etfs_data_end) - int(row_etfs_data_start) > 1:
        merge(int(row_etfs_data_start), int(row_etfs_data_end), 0, 1)
    if int(row_risk_data_end) - int(row_risk_data_start) > 1:
        merge(int(row_risk_data_start), int(row_risk_data_end), 0, 1)
    if int(row_crypto_data_end) - int(row_crypto_data_start) > 1:
        merge(int(row_crypto_data_start), int(row_crypto_data_end), 0, 1)
    if int(row_ratio_data_end) - int(row_ratio_data_start) > 1:
        merge(int(row_ratio_data_start), int(row_ratio_data_end), 0, 1)

    # column widths（像素）
    col_widths = [90, 200, 110, 70, 70, 70]
    for ci, px in enumerate(col_widths):
        reqs.append(
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": int(sh_id),
                        "dimension": "COLUMNS",
                        "startIndex": int(ci),
                        "endIndex": int(ci + 1),
                    },
                    "properties": {"pixelSize": int(px)},
                    "fields": "pixelSize",
                }
            }
        )

    # row heights（像素）
    row_heights: dict[int, int] = {}
    try:
        banner_lines = int(banner_line_count or 1)
    except Exception:
        banner_lines = 1
    banner_lines = max(1, min(int(banner_lines), 6))
    row_heights[int(row_banner)] = int(21 * int(banner_lines))
    row_heights[int(row_meta)] = 21
    row_heights[int(row_commod_header)] = 24
    for ri, px in row_heights.items():
        reqs.append(
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": int(sh_id),
                        "dimension": "ROWS",
                        "startIndex": int(ri),
                        "endIndex": int(ri + 1),
                    },
                    "properties": {"pixelSize": int(px)},
                    "fields": "pixelSize",
                }
            }
        )

    # formatting
    def repeat_cell(r0: int, r1: int, c0: int, c1: int, fmt: dict[str, Any], fields: str) -> None:
        reqs.append(
            {
                "repeatCell": {
                    "range": {
                        "sheetId": int(sh_id),
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

    # banner style（复用其它表的“全表首行广告位”风格）
    repeat_cell(
        int(row_banner),
        int(row_banner) + 1,
        0,
        dash_cols,
        {
            "backgroundColor": {"red": 1.0, "green": 0.97, "blue": 0.86},
            "textFormat": {"fontFamily": "Arial", "fontSize": 11, "bold": True},
            "horizontalAlignment": "LEFT",
            "verticalAlignment": "MIDDLE",
            "wrapStrategy": "OVERFLOW_CELL",
        },
        "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)",
    )

    # meta style（单单元格）
    repeat_cell(
        int(row_meta),
        int(row_meta) + 1,
        0,
        dash_cols,
        {
            "backgroundColor": {"red": 0.96, "green": 0.97, "blue": 0.98},
            "textFormat": {"fontFamily": "Arial", "fontSize": 10, "bold": True},
            "horizontalAlignment": "LEFT",
            "verticalAlignment": "MIDDLE",
            "wrapStrategy": "OVERFLOW_CELL",
        },
        "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)",
    )

    # table header rows
    repeat_cell(
        int(row_commod_header),
        int(row_commod_header) + 1,
        0,
        dash_cols,
        {
            "backgroundColor": {"red": 0.95, "green": 0.95, "blue": 0.95},
            "textFormat": {"fontFamily": "Arial", "fontSize": 10, "bold": True},
            "horizontalAlignment": "CENTER",
            "verticalAlignment": "MIDDLE",
            "wrapStrategy": "CLIP",
        },
        "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)",
    )

    # 类别块灰白交替（只刷灰色块；白色保持默认不动）
    alt_bg = {"red": 0.97, "green": 0.97, "blue": 0.97}
    category_blocks = [
        ("大宗", int(row_commod_data_start), int(row_commod_data_end)),
        ("外汇", int(row_fx_data_start), int(row_fx_data_end)),
        ("指数", int(row_indices_data_start), int(row_indices_data_end)),
        ("ETF", int(row_etfs_data_start), int(row_etfs_data_end)),
        ("恐慌指数", int(row_risk_data_start), int(row_risk_data_end)),
        ("加密货币", int(row_crypto_data_start), int(row_crypto_data_end)),
        ("比值/价差", int(row_ratio_data_start), int(row_ratio_data_end)),
    ]
    for bi, (_cat, r0, r1) in enumerate(category_blocks):
        if r1 <= r0:
            continue
        if int(bi) % 2 == 1:
            repeat_cell(
                int(r0),
                int(r1),
                0,
                dash_cols,
                {"backgroundColor": alt_bg},
                "userEnteredFormat.backgroundColor",
            )

    # category column（A 列）在合并后默认显示在左上角，这里强制居中并加粗，视觉更像“分组标题”
    category_fmt = {
        "textFormat": {"fontFamily": "Arial", "fontSize": 10, "bold": True},
        "horizontalAlignment": "CENTER",
        "verticalAlignment": "MIDDLE",
        "wrapStrategy": "CLIP",
    }
    repeat_cell(
        int(row_commod_data_start),
        int(row_commod_data_end),
        0,
        1,
        category_fmt,
        "userEnteredFormat(textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)",
    )
    repeat_cell(
        int(row_fx_data_start),
        int(row_fx_data_end),
        0,
        1,
        category_fmt,
        "userEnteredFormat(textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)",
    )
    repeat_cell(
        int(row_indices_data_start),
        int(row_indices_data_end),
        0,
        1,
        category_fmt,
        "userEnteredFormat(textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)",
    )
    repeat_cell(
        int(row_etfs_data_start),
        int(row_etfs_data_end),
        0,
        1,
        category_fmt,
        "userEnteredFormat(textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)",
    )
    repeat_cell(
        int(row_risk_data_start),
        int(row_risk_data_end),
        0,
        1,
        category_fmt,
        "userEnteredFormat(textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)",
    )
    repeat_cell(
        int(row_crypto_data_start),
        int(row_crypto_data_end),
        0,
        1,
        category_fmt,
        "userEnteredFormat(textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)",
    )
    repeat_cell(
        int(row_ratio_data_start),
        int(row_ratio_data_end),
        0,
        1,
        category_fmt,
        "userEnteredFormat(textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)",
    )

    # percent columns D/E/F in all sections -> numberFormat
    percent_fmt = {"numberFormat": {"type": "PERCENT", "pattern": "0.00%"}}
    # last（最新）列：两位小数显示（仅影响展示，不改变底层数值）
    last_fmt = {"numberFormat": {"type": "NUMBER", "pattern": "0.00"}}
    # FX 汇率：两位小数会丢 pips，默认用 4 位小数展示
    fx_rate_last_fmt = {"numberFormat": {"type": "NUMBER", "pattern": "0.0000"}}
    # 对极小比值（例如 铜金比 ~0.001），“0.00” 会被四舍五入成 0.00，信息丢失。
    # 这里对特定指标做覆盖：保留 4 位小数即可（既不刷屏，也能看出变化）。
    small_ratio_last_fmt = {"numberFormat": {"type": "NUMBER", "pattern": "0.0000"}}

    # commodities last col: C（idx=2）
    repeat_cell(
        int(row_commod_data_start),
        int(row_commod_data_end),
        2,
        3,
        last_fmt,
        "userEnteredFormat.numberFormat",
    )
    # fx last col: C（idx=2）
    repeat_cell(
        int(row_fx_data_start),
        int(row_fx_data_end),
        2,
        3,
        fx_rate_last_fmt,
        "userEnteredFormat.numberFormat",
    )
    # 覆盖：美元指数（DXY）按两位小数展示即可
    try:
        idx_dxy = [i for i, s in enumerate(fx) if s.wide_slug == "fx_dxy"]
        if idx_dxy:
            ri = int(row_fx_data_start) + int(idx_dxy[0])
            repeat_cell(
                int(ri),
                int(ri) + 1,
                2,
                3,
                last_fmt,
                "userEnteredFormat.numberFormat",
            )
    except Exception:
        pass
    # indices last col: C（idx=2）
    repeat_cell(
        int(row_indices_data_start),
        int(row_indices_data_end),
        2,
        3,
        last_fmt,
        "userEnteredFormat.numberFormat",
    )
    # etfs last col: C（idx=2）
    repeat_cell(
        int(row_etfs_data_start),
        int(row_etfs_data_end),
        2,
        3,
        last_fmt,
        "userEnteredFormat.numberFormat",
    )
    # risk last col: C（idx=2）
    repeat_cell(
        int(row_risk_data_start),
        int(row_risk_data_end),
        2,
        3,
        last_fmt,
        "userEnteredFormat.numberFormat",
    )
    # crypto last col: C（idx=2）
    repeat_cell(
        int(row_crypto_data_start),
        int(row_crypto_data_end),
        2,
        3,
        last_fmt,
        "userEnteredFormat.numberFormat",
    )
    # ratios last col: C（idx=2）
    repeat_cell(
        int(row_ratio_data_start),
        int(row_ratio_data_end),
        2,
        3,
        last_fmt,
        "userEnteredFormat.numberFormat",
    )
    # 覆盖：铜金比（行内定位）
    try:
        idx_small = [i for i, s in enumerate(derived) if s.wide_slug == "ratio_copper_gold"]
        if idx_small:
            ri = int(row_ratio_data_start) + int(idx_small[0])
            repeat_cell(
                int(ri),
                int(ri) + 1,
                2,
                3,
                small_ratio_last_fmt,
                "userEnteredFormat.numberFormat",
            )
    except Exception:
        pass

    # commodities ret cols: D-F (3-6 col index: 3,4,5)
    for ci in (3, 4, 5):
        repeat_cell(
            int(row_commod_data_start),
            int(row_commod_data_end),
            ci,
            ci + 1,
            percent_fmt,
            "userEnteredFormat.numberFormat",
        )
    # fx ret cols: D-F (3,4,5)
    for ci in (3, 4, 5):
        repeat_cell(
            int(row_fx_data_start),
            int(row_fx_data_end),
            ci,
            ci + 1,
            percent_fmt,
            "userEnteredFormat.numberFormat",
        )
    # indices ret cols: D-F (3,4,5)
    for ci in (3, 4, 5):
        repeat_cell(
            int(row_indices_data_start),
            int(row_indices_data_end),
            ci,
            ci + 1,
            percent_fmt,
            "userEnteredFormat.numberFormat",
        )
    # etfs ret cols: D-F (3,4,5)
    for ci in (3, 4, 5):
        repeat_cell(
            int(row_etfs_data_start),
            int(row_etfs_data_end),
            ci,
            ci + 1,
            percent_fmt,
            "userEnteredFormat.numberFormat",
        )
    # risk ret cols: D-F (3,4,5)
    for ci in (3, 4, 5):
        repeat_cell(
            int(row_risk_data_start),
            int(row_risk_data_end),
            ci,
            ci + 1,
            percent_fmt,
            "userEnteredFormat.numberFormat",
        )
    # crypto ret cols: D-F (3,4,5)
    for ci in (3, 4, 5):
        repeat_cell(
            int(row_crypto_data_start),
            int(row_crypto_data_end),
            ci,
            ci + 1,
            percent_fmt,
            "userEnteredFormat.numberFormat",
        )
    # ratios ret cols: D-F (3,4,5)
    for ci in (3, 4, 5):
        repeat_cell(
            int(row_ratio_data_start),
            int(row_ratio_data_end),
            ci,
            ci + 1,
            percent_fmt,
            "userEnteredFormat.numberFormat",
        )

    # 无边框：清理表格边框（即便用户手动加过边框，也会被覆盖掉）
    reqs.append(
        {
            "updateBorders": {
                "range": {
                    "sheetId": int(sh_id),
                    "startRowIndex": 0,
                    "endRowIndex": int(dash_rows),
                    "startColumnIndex": 0,
                    "endColumnIndex": int(dash_cols),
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

    if reqs:
        writer.batch_update(requests=reqs)

    # 智能裁剪：宏观看板是完全托管展示面，允许把底部/右侧空白网格彻底裁剪掉
    try:
        writer._set_sheet_grid_properties(
            tab_dashboard,
            row_count=int(dash_rows),
            col_count=int(dash_cols),
            frozen_row_count=int(frozen_rows_final),
            frozen_column_count=int(frozen_cols_final),
        )
    except Exception:
        pass

    # 体验：用户只需要看到宏观大宗看板（tab_dashboard）；其余基础表默认隐藏（但仍保留用于稳定引用/治理/审计）。
    if hide_support_tabs:
        hide_reqs: list[dict[str, Any]] = []
        for title, hidden in (
            (tab_dashboard, False),
            (tab_snapshot, True),
            (tab_series_map, True),
            (tab_refresh_log, True),
        ):
            try:
                sid = int(writer.sheet_id(title))
            except Exception:
                continue
            hide_reqs.append(
                {
                    "updateSheetProperties": {
                        "properties": {"sheetId": int(sid), "hidden": bool(hidden)},
                        "fields": "hidden",
                    }
                }
            )
        if hide_reqs:
            writer.batch_update(requests=hide_reqs)

    return {
        "ok": True,
        "tabs": {
            "dashboard": tab_dashboard,
            "snapshot": tab_snapshot,
            "series_map": tab_series_map,
            "refresh_log": tab_refresh_log,
        },
        "snapshot_cols": len(headers),
        "commodities": len(commodities),
        "fx": len(fx),
        "indices": len(indices),
        "etfs": len(etfs),
        "risk": len(risk),
        "crypto": len(crypto),
        "derived": len(derived),
    }
