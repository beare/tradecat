from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.sa_sheets_writer import SaSheetsWriter


@dataclass(frozen=True)
class PublicStyleHit:
    sheet: str
    check: str
    detail: str


def _as_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def _color_brightness(c: Any) -> float:
    if not isinstance(c, dict):
        return 0.0
    r = _as_float(c.get("red"), 0.0)
    g = _as_float(c.get("green"), 0.0)
    b = _as_float(c.get("blue"), 0.0)
    return (r + g + b) / 3.0


def _color_close(c: Any, *, r: float, g: float, b: float, eps: float = 0.03) -> bool:
    if not isinstance(c, dict):
        return False
    return (
        abs(_as_float(c.get("red")) - float(r)) <= float(eps)
        and abs(_as_float(c.get("green")) - float(g)) <= float(eps)
        and abs(_as_float(c.get("blue")) - float(b)) <= float(eps)
    )


def _row_px(grid: dict[str, Any], idx0: int) -> int | None:
    md = grid.get("rowMetadata") or []
    if not isinstance(md, list) or idx0 < 0 or idx0 >= len(md):
        return None
    try:
        px = (md[idx0] or {}).get("pixelSize")
    except Exception:
        px = None
    if px is None:
        return None
    try:
        return int(px)
    except Exception:
        return None


def _col_px(grid: dict[str, Any], idx0: int) -> int | None:
    md = grid.get("columnMetadata") or []
    if not isinstance(md, list) or idx0 < 0 or idx0 >= len(md):
        return None
    try:
        px = (md[idx0] or {}).get("pixelSize")
    except Exception:
        px = None
    if px is None:
        return None
    try:
        return int(px)
    except Exception:
        return None


def _cell_format(grid: dict[str, Any], r0: int, c0: int) -> dict[str, Any] | None:
    rows = grid.get("rowData") or []
    if not isinstance(rows, list) or r0 < 0 or r0 >= len(rows):
        return None
    vals = (rows[r0] or {}).get("values") or []
    if not isinstance(vals, list) or c0 < 0 or c0 >= len(vals):
        return None
    fmt = (vals[c0] or {}).get("userEnteredFormat")
    if not isinstance(fmt, dict):
        return None
    return fmt


def _format_has_visible_border(fmt: dict[str, Any], *, brightness_lt: float) -> bool:
    borders = fmt.get("borders")
    if not isinstance(borders, dict):
        return False

    def side_visible(side: Any) -> bool:
        if not isinstance(side, dict):
            return False
        style = str(side.get("style") or "").strip().upper()
        if not style or style == "NONE":
            return False
        # 颜色缺失：保守认为可见（常见为默认黑色）
        c = side.get("color")
        if not isinstance(c, dict):
            return True
        return _color_brightness(c) < float(brightness_lt)

    for k in ("top", "bottom", "left", "right"):
        if side_visible(borders.get(k)):
            return True
    return False


def audit_public_styles(
    writer: SaSheetsWriter,
    *,
    include_hidden: bool = False,
    a1_range: str = "A1:T40",
    tab_api_title: str = "API",
    tab_news_title: str = "实时新闻",
) -> dict[str, Any]:
    """
    审计公开工作簿“可见 tabs”的样式契约（只读）。

    目标：
    - 网格线隐藏（hideGridlines=true）
    - 顶部两行（广告位/元信息）行高与底色统一
    - 冻结行数 >= 3（banner+meta+header）
    - API/实时新闻 的列宽与“禁止换行”策略符合约定
    - 抽样检查：正文区域不应出现“明显可见的深色边框”（避免 UI 出现线条）
    """
    banner_text, banner_line_count = writer.public_top_banner_text()
    _ = banner_text  # 只用于兜底；样式审计不检查 banner 内容
    try:
        banner_lines = int(banner_line_count or 1)
    except Exception:
        banner_lines = 1
    banner_lines = max(1, min(int(banner_lines), 6))
    want_banner_px = int(21 * int(banner_lines))
    want_meta_px = 21

    tab_api = str(tab_api_title or "API").strip() or "API"
    tab_news = str(tab_news_title or "实时新闻").strip() or "实时新闻"

    sheets = writer.list_sheet_properties()
    rows: list[dict[str, Any]] = []
    hits: list[PublicStyleHit] = []

    # 读优化：一次性拉取所有可见 sheet 的小范围 gridData，避免 N 次读触发 429
    wanted_titles: list[str] = []
    hidden_by_title: dict[str, bool] = {}
    for sh in sheets:
        title = str(sh.get("title") or "").strip()
        hidden = bool(sh.get("hidden") or False)
        if not title:
            continue
        if hidden and (not include_hidden):
            continue
        wanted_titles.append(title)
        hidden_by_title[title] = hidden

    fetched = writer.get_sheets_grid_fragments(
        titles=wanted_titles,
        a1_range=a1_range,
        fields=(
            "sheets("
            "properties(title,sheetId,hidden,gridProperties(hideGridlines,frozenRowCount,frozenColumnCount)),"
            "data(rowData(values(userEnteredFormat)),rowMetadata(pixelSize),columnMetadata(pixelSize))"
            ")"
        ),
    )
    fetched_by_title: dict[str, dict[str, Any]] = {}
    for s in fetched:
        props = (s or {}).get("properties") or {}
        t = str(props.get("title") or "").strip()
        if t:
            fetched_by_title[t] = s

    for title in wanted_titles:
        hidden = bool(hidden_by_title.get(title, False))
        row: dict[str, Any] = {"title": title, "hidden": hidden, "ok": True, "checks": {}}

        target = fetched_by_title.get(title)
        if not isinstance(target, dict):
            row["ok"] = False
            row["error"] = "missing_sheet_data"
            hits.append(PublicStyleHit(sheet=title, check="missing_sheet_data", detail=str(a1_range)))
            rows.append(row)
            continue

        try:
            props = target.get("properties") or {}
            gp = props.get("gridProperties") or {}
            hide_gridlines = bool(gp.get("hideGridlines") or False)
            try:
                frozen_rows = int(gp.get("frozenRowCount") or 0)
            except Exception:
                frozen_rows = 0

            grid = ((target.get("data") or [{}])[0] or {}) if isinstance(target.get("data"), list) else {}

            # -------- global checks --------
            row["checks"]["hideGridlines"] = hide_gridlines
            if not hide_gridlines:
                row["ok"] = False
                hits.append(PublicStyleHit(sheet=title, check="hideGridlines", detail="expected:true"))

            row["checks"]["frozenRowCount"] = frozen_rows
            if frozen_rows < 3:
                row["ok"] = False
                hits.append(PublicStyleHit(sheet=title, check="frozenRowCount", detail=f"expected:>=3 got:{frozen_rows}"))

            # -------- top rows (A1/A2) style --------
            fmt_a1 = _cell_format(grid, 0, 0) or {}
            fmt_a2 = _cell_format(grid, 1, 0) or {}
            px0 = _row_px(grid, 0)
            px1 = _row_px(grid, 1)

            row["checks"]["banner_row_px"] = px0
            row["checks"]["meta_row_px"] = px1

            if px0 is None or int(px0) != int(want_banner_px):
                row["ok"] = False
                hits.append(
                    PublicStyleHit(sheet=title, check="banner_row_px", detail=f"expected:{want_banner_px} got:{px0}")
                )
            if px1 is None or int(px1) != int(want_meta_px):
                row["ok"] = False
                hits.append(PublicStyleHit(sheet=title, check="meta_row_px", detail=f"expected:21 got:{px1}"))

            banner_bg = (fmt_a1 or {}).get("backgroundColor")
            banner_ok = _color_close(banner_bg, r=1.0, g=0.97, b=0.86)
            row["checks"]["banner_bg_ok"] = banner_ok
            if not banner_ok:
                row["ok"] = False
                hits.append(PublicStyleHit(sheet=title, check="banner_bg", detail="expected:rgb(1.00,0.97,0.86)"))

            meta_bg = (fmt_a2 or {}).get("backgroundColor")
            meta_ok = _color_close(meta_bg, r=0.96, g=0.97, b=0.98)
            row["checks"]["meta_bg_ok"] = meta_ok
            if not meta_ok:
                row["ok"] = False
                hits.append(PublicStyleHit(sheet=title, check="meta_bg", detail="expected:rgb(0.96,0.97,0.98)"))

            def check_top_row_fmt(fmt: dict[str, Any], *, which: str) -> None:
                nonlocal row
                tf = fmt.get("textFormat") or {}
                bold = bool((tf or {}).get("bold") or False)
                wrap = str(fmt.get("wrapStrategy") or "").strip().upper()
                ha = str(fmt.get("horizontalAlignment") or "").strip().upper()
                va = str(fmt.get("verticalAlignment") or "").strip().upper()
                if not bold:
                    row["ok"] = False
                    hits.append(PublicStyleHit(sheet=title, check=f"{which}_bold", detail="expected:true"))
                if wrap != "OVERFLOW_CELL":
                    row["ok"] = False
                    hits.append(
                        PublicStyleHit(sheet=title, check=f"{which}_wrapStrategy", detail=f"expected:OVERFLOW_CELL got:{wrap}")
                    )
                if ha and ha != "LEFT":
                    row["ok"] = False
                    hits.append(PublicStyleHit(sheet=title, check=f"{which}_hAlign", detail=f"expected:LEFT got:{ha}"))
                if va and va != "MIDDLE":
                    row["ok"] = False
                    hits.append(PublicStyleHit(sheet=title, check=f"{which}_vAlign", detail=f"expected:MIDDLE got:{va}"))

            check_top_row_fmt(fmt_a1, which="banner")
            check_top_row_fmt(fmt_a2, which="meta")

            # -------- per-tab checks --------
            if title == tab_api:
                want = [520, 520, 860]
                got = [_col_px(grid, 0), _col_px(grid, 1), _col_px(grid, 2)]
                row["checks"]["api_col_px"] = got
                for i, (g, w) in enumerate(zip(got, want, strict=False)):
                    if g is None or abs(int(g) - int(w)) > 10:
                        row["ok"] = False
                        hits.append(
                            PublicStyleHit(
                                sheet=title,
                                check=f"api_col_{i}_px",
                                detail=f"expected:{w}±10 got:{g}",
                            )
                        )

                # data 行禁止换行：检查 A4/B4/C4（若不存在则跳过）
                for c, col_name in [(0, "A"), (1, "B"), (2, "C")]:
                    fmt = _cell_format(grid, 3, c)
                    if not isinstance(fmt, dict):
                        continue
                    wrap = str(fmt.get("wrapStrategy") or "").strip().upper()
                    if wrap and wrap != "CLIP":
                        row["ok"] = False
                        hits.append(
                            PublicStyleHit(
                                sheet=title,
                                check=f"api_data_wrap_{col_name}",
                                detail=f"expected:CLIP got:{wrap}",
                            )
                        )

            if title == tab_news:
                want = [170, 980]
                got = [_col_px(grid, 0), _col_px(grid, 1)]
                row["checks"]["news_col_px"] = got
                for i, (g, w) in enumerate(zip(got, want, strict=False)):
                    if g is None or abs(int(g) - int(w)) > 10:
                        row["ok"] = False
                        hits.append(
                            PublicStyleHit(
                                sheet=title,
                                check=f"news_col_{i}_px",
                                detail=f"expected:{w}±10 got:{g}",
                            )
                        )

                # 内容列（B）不换行（CLIP）：检查 B4（若不存在则跳过）
                fmt_b4 = _cell_format(grid, 3, 1)
                if isinstance(fmt_b4, dict):
                    wrap = str(fmt_b4.get("wrapStrategy") or "").strip().upper()
                    if wrap and wrap != "CLIP":
                        row["ok"] = False
                        hits.append(
                            PublicStyleHit(sheet=title, check="news_content_wrap", detail=f"expected:CLIP got:{wrap}")
                        )

            # -------- border sample (dark borders) --------
            dark_border_found = False
            rd = grid.get("rowData") or []
            for r in range(2, min(40, len(rd))):
                vals = ((rd[r] or {}).get("values") or []) if isinstance(rd, list) else []
                for c in range(0, min(20, len(vals) if isinstance(vals, list) else 0)):
                    fmt = _cell_format(grid, r, c)
                    if not isinstance(fmt, dict):
                        continue
                    if _format_has_visible_border(fmt, brightness_lt=0.85):
                        dark_border_found = True
                        break
                if dark_border_found:
                    break
            row["checks"]["dark_border_sample"] = (not dark_border_found)
            if dark_border_found:
                row["ok"] = False
                hits.append(PublicStyleHit(sheet=title, check="dark_border_sample", detail="found"))

        except Exception as exc:
            row["ok"] = False
            row["error"] = f"{type(exc).__name__}:{exc}"[:200]
            hits.append(PublicStyleHit(sheet=title, check="exception", detail=row["error"]))

        rows.append(row)

    bad = [r for r in rows if not bool(r.get("ok"))]
    return {
        "ok": len(bad) == 0,
        "include_hidden": bool(include_hidden),
        "total": int(len(rows)),
        "bad": int(len(bad)),
        "rows": rows,
        # 输出 hits（不含任何单元格内容），便于快速定位违反项
        "hits": [h.__dict__ for h in hits][:800],
    }
