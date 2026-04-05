from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from src.sa_sheets_writer import SaSheetsWriter


@dataclass(frozen=True)
class _BaseSeries:
    wide_slug: str
    stooq_symbol: str
    yahoo_symbol: str


@dataclass(frozen=True)
class _DerivedSeries:
    wide_slug: str
    op: str  # "div" | "sub"
    a_slug: str
    b_slug: str


_STOOQ_DAILY_URL = "https://stooq.com/q/d/l/"
_YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"

def _openers_for_env() -> list[urllib.request.OpenerDirector]:
    """
    网络环境差异很大：
    - 有的机器必须走 HTTP(S)_PROXY 才能出网（例如公司网络/WSL）
    - 有的机器代理不稳定/不通，直连更可靠

    策略：
    - 默认优先使用“环境代理”（urllib 默认行为）
    - 若失败，再自动 fallback 到“禁用代理”的直连
    - 可用 env 强制禁用代理：SHEETS_MACRO_DISABLE_PROXY=1
    """
    disable_proxy = (os.environ.get("SHEETS_MACRO_DISABLE_PROXY", "0") or "0").strip() == "1"
    env_opener = urllib.request.build_opener()
    no_proxy_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return [no_proxy_opener, env_opener] if disable_proxy else [env_opener, no_proxy_opener]

def _fetch_bytes(req: urllib.request.Request, *, timeout_seconds: int, max_retries: int = 0) -> bytes:
    """
    用 opener 双栈（环境代理优先/直连 fallback）抓取 bytes。
    - 对 429/5xx 做有限重试（指数退避）；其余错误直接抛出
    """
    timeout = max(int(timeout_seconds), 1)
    max_retries = max(int(max_retries), 0)

    last_exc: Exception | None = None
    for attempt in range(0, max_retries + 1):
        for opener in _openers_for_env():
            try:
                with opener.open(req, timeout=timeout) as resp:
                    return resp.read()
            except urllib.error.HTTPError as exc:
                last_exc = exc
                code = int(getattr(exc, "code", 0) or 0)
                # 非临时错误：不重试
                if code and code != 429 and not (500 <= code <= 599):
                    raise
                continue
            except Exception as exc:
                last_exc = exc
                continue

        if attempt >= max_retries:
            break
        # 简单指数退避（不加随机抖动，避免引入不可重复性）
        sleep_for = min(0.8 * (2**attempt), 8.0)
        time.sleep(max(sleep_for, 0.1))

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("fetch_failed")


def _fetch_stooq_daily_closes(
    symbol: str,
    *,
    start_date: dt.date,
    end_date: dt.date,
    timeout_seconds: int = 20,
) -> list[tuple[dt.date, float]]:
    sym = str(symbol).strip()
    if not sym:
        return []
    params = {
        "s": sym,
        "i": "d",
        "d1": start_date.strftime("%Y%m%d"),
        "d2": end_date.strftime("%Y%m%d"),
    }
    url = f"{_STOOQ_DAILY_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "tradecat-sheets-service/1.0",
            "Accept": "text/plain,*/*;q=0.8",
        },
        method="GET",
    )
    raw = _fetch_bytes(req, timeout_seconds=int(timeout_seconds), max_retries=1)
    text = raw.decode("utf-8", errors="replace").strip()
    if not text or "No data" in text:
        return []

    # 兼容 CRLF
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    buf = io.StringIO(text)
    reader = csv.DictReader(buf)
    out: list[tuple[dt.date, float]] = []
    for row in reader:
        d = (row.get("Date") or "").strip()
        c = (row.get("Close") or "").strip()
        if not d or not c:
            continue
        try:
            day = dt.date.fromisoformat(d)
            close = float(c)
        except Exception:
            continue
        out.append((day, close))
    return out


def _fetch_yahoo_daily_closes(
    symbol: str,
    *,
    start_date: dt.date,
    end_date: dt.date,
    timeout_seconds: int = 20,
) -> list[tuple[dt.date, float]]:
    """
    Yahoo Finance 非官方 chart 接口（日频）。
    注意：该接口可能被限流/变更；推荐作为 “auto fallback” 的备用源，而非唯一权威源。
    """
    sym = str(symbol).strip()
    if not sym:
        return []

    p1 = int(dt.datetime.combine(start_date, dt.time(0, 0), tzinfo=dt.timezone.utc).timestamp())
    p2 = int(dt.datetime.combine(end_date + dt.timedelta(days=1), dt.time(0, 0), tzinfo=dt.timezone.utc).timestamp())
    params = {
        "period1": str(p1),
        "period2": str(p2),
        "interval": "1d",
        "includePrePost": "false",
        "events": "div|split",
    }
    url = f"{_YAHOO_CHART_URL}{urllib.parse.quote(sym, safe='')}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={
            # 不加 UA 容易被边缘层直接 429（我们在容器内已观测到 “Edge: Too Many Requests”）
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) tradecat-sheets-service/1.0",
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://finance.yahoo.com/",
        },
        method="GET",
    )
    retries = int(os.environ.get("SHEETS_MACRO_YAHOO_RETRIES", "2") or "2")
    raw = _fetch_bytes(req, timeout_seconds=int(timeout_seconds), max_retries=int(retries))
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return []
    if text.startswith("Edge:"):
        # 典型：Edge: Too Many Requests（非 JSON）
        raise RuntimeError(text[:200])

    obj = json.loads(text)
    chart = (obj.get("chart") or {}) if isinstance(obj, dict) else {}
    err = (chart.get("error") or {}) if isinstance(chart, dict) else {}
    if err:
        desc = str(err.get("description") or err.get("code") or "yahoo_error").strip()
        raise RuntimeError(f"yahoo:{sym}:{desc}")

    res0 = None
    results = chart.get("result") if isinstance(chart, dict) else None
    if isinstance(results, list) and results:
        res0 = results[0] or {}
    if not isinstance(res0, dict):
        return []

    ts = res0.get("timestamp") or []
    ind = (res0.get("indicators") or {}) if isinstance(res0.get("indicators"), dict) else {}
    quotes = ind.get("quote") or []
    quote0 = (quotes[0] or {}) if isinstance(quotes, list) and quotes else {}
    closes = quote0.get("close") or []

    if not isinstance(ts, list) or not isinstance(closes, list):
        return []

    out: list[tuple[dt.date, float]] = []
    for t, c in zip(ts, closes, strict=False):
        if t is None or c is None:
            continue
        try:
            day = dt.datetime.fromtimestamp(int(t), tz=dt.timezone.utc).date()
            close = float(c)
        except Exception:
            continue
        out.append((day, close))
    return out


def _fetch_yfinance_daily_closes(
    symbol: str,
    *,
    start_date: dt.date,
    end_date: dt.date,
) -> list[tuple[dt.date, float]]:
    """
    yfinance（第三方库）封装 Yahoo Finance。
    - 优点：通常更“人性化”，会处理部分 session/cookie 行为
    - 风险：依赖较重（pandas/numpy）；仍属非官方数据源，可能限流/变更
    """
    sym = str(symbol).strip()
    if not sym:
        return []

    try:
        import yfinance as yf  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"missing_dep:yfinance ({type(exc).__name__}:{exc})")

    # yfinance 的 end 为“非包含”，因此加 1 天覆盖到 end_date
    start = start_date.isoformat()
    end = (end_date + dt.timedelta(days=1)).isoformat()
    try:
        import warnings

        with warnings.catch_warnings():
            # yfinance/pandas 的 deprecation warning 会刷屏；这里统一静默（不影响数据正确性）
            warnings.simplefilter("ignore")
            df = yf.download(
                tickers=sym,
                start=start,
                end=end,
                interval="1d",
                progress=False,
                threads=False,
                auto_adjust=False,
                prepost=False,
            )
    except Exception:
        # fallback：不让 warnings 相关逻辑干扰主流程
        df = yf.download(
            tickers=sym,
            start=start,
            end=end,
            interval="1d",
            progress=False,
            threads=False,
            auto_adjust=False,
            prepost=False,
        )
    if df is None:
        return []

    # 兼容：yfinance 新版对单 ticker 也可能返回 MultiIndex columns:
    #   ('Close','GC=F') ... names=['Price','Ticker']
    close_series = None
    try:
        import pandas as pd  # type: ignore

        if isinstance(getattr(df, "columns", None), pd.MultiIndex):
            # 优先精确命中 (Close, <ticker>)
            if ("Close", sym) in df.columns:
                close_series = df[("Close", sym)]
            else:
                # 次选：取 level0=Close 的子表，再取第一个 ticker 列
                closes_df = df.xs("Close", axis=1, level=0)
                if hasattr(closes_df, "columns") and len(closes_df.columns) > 0:
                    col = sym if sym in closes_df.columns else closes_df.columns[0]
                    close_series = closes_df[col]
        else:
            close_series = df["Close"]
    except Exception:
        close_series = None
    if close_series is None:
        return []

    out: list[tuple[dt.date, float]] = []
    try:
        items = close_series.items()
    except Exception:
        items = []
    for idx, val in items:
        if val is None:
            continue
        try:
            close = float(val)
        except Exception:
            continue

        day: dt.date | None = None
        try:
            if hasattr(idx, "to_pydatetime"):
                day = idx.to_pydatetime().date()
            elif isinstance(idx, dt.datetime):
                day = idx.date()
            elif isinstance(idx, dt.date):
                day = idx
        except Exception:
            day = None
        if day is None:
            continue
        out.append((day, close))
    return out


def _pick_returns(values: list[float]) -> tuple[float | None, float | None, float | None, float | None]:
    if not values:
        return None, None, None, None
    last = float(values[-1])

    def ret(n: int) -> float | None:
        idx = len(values) - 1 - int(n)
        if idx < 0:
            return None
        base = float(values[idx])
        if base == 0.0:
            return None
        return (last / base) - 1.0

    return last, ret(1), ret(5), ret(20)


def _align_on_dates(
    a: list[tuple[dt.date, float]],
    b: list[tuple[dt.date, float]],
) -> list[tuple[dt.date, float, float]]:
    amap = {d: v for d, v in a}
    bmap = {d: v for d, v in b}
    dates = sorted(set(amap.keys()) & set(bmap.keys()))
    out: list[tuple[dt.date, float, float]] = []
    for d in dates:
        out.append((d, float(amap[d]), float(bmap[d])))
    return out


def _derived_series_values(
    aligned: list[tuple[dt.date, float, float]],
    *,
    op: str,
) -> list[tuple[dt.date, float]]:
    out: list[tuple[dt.date, float]] = []
    for d, a, b in aligned:
        if op == "div":
            if b == 0.0:
                continue
            out.append((d, float(a) / float(b)))
        elif op == "sub":
            out.append((d, float(a) - float(b)))
        else:
            raise ValueError(f"unsupported op={op}")
    return out


def refresh_macro_snapshot_stooq(
    writer: SaSheetsWriter,
    *,
    tab_snapshot: str = "10_SNAPSHOT_WIDE",
    lookback_days: int = 120,
) -> dict[str, Any]:
    """
    从公开数据源抓取大宗/衍生指标，写入 10_SNAPSHOT_WIDE 的第 2 行（快照）。

    说明：
    - 不依赖 FRED key（纯公开取数），先把大宗/比值跑通。
    - ret_1d/5d/20d 采用“交易日回溯”（按可用数据点回溯 1/5/20 个点）。
    - 数据源可选：SHEETS_MACRO_SOURCE=stooq|yahoo|yfinance|auto（默认 auto；auto 优先 yfinance，失败回退 stooq/yahoo）
    """
    # -------------------- series config --------------------
    bases: list[_BaseSeries] = [
        _BaseSeries("com_gold", "xauusd", "GC=F"),
        _BaseSeries("com_silver", "xagusd", "SI=F"),
        _BaseSeries("com_oil_wti", "cl.c", "CL=F"),
        _BaseSeries("com_oil_brent", "cb.c", "BZ=F"),
        _BaseSeries("com_natgas", "ng.c", "NG=F"),
        _BaseSeries("com_gasoline", "rb.c", "RB=F"),
        _BaseSeries("com_copper", "hg.c", "HG=F"),
        _BaseSeries("com_corn", "zc.c", "ZC=F"),
        _BaseSeries("com_soy", "zs.c", "ZS=F"),
        _BaseSeries("com_wheat", "mw.c", "ZW=F"),
        # -------------------- FX（核心外汇 + DXY） --------------------
        _BaseSeries("fx_dxy", "dxy", "DX-Y.NYB"),
        _BaseSeries("fx_eurusd", "eurusd", "EURUSD=X"),
        _BaseSeries("fx_usdjpy", "usdjpy", "USDJPY=X"),
        _BaseSeries("fx_gbpusd", "gbpusd", "GBPUSD=X"),
        _BaseSeries("fx_usdcny", "usdcny", "USDCNY=X"),
        _BaseSeries("fx_audusd", "audusd", "AUDUSD=X"),
        _BaseSeries("fx_nzdusd", "nzdusd", "NZDUSD=X"),
        _BaseSeries("fx_usdcad", "usdcad", "USDCAD=X"),
        _BaseSeries("fx_usdchf", "usdchf", "USDCHF=X"),
        # -------------------- Crypto（核心加密货币） --------------------
        _BaseSeries("crypto_btc", "btcusd", "BTC-USD"),
        _BaseSeries("crypto_eth", "ethusd", "ETH-USD"),
        # -------------------- Risk（恐慌/风险指数） --------------------
        _BaseSeries("risk_vix", "", "^VIX"),
        _BaseSeries("risk_vvix", "", "^VVIX"),
        _BaseSeries("risk_skew", "", "^SKEW"),
        _BaseSeries("risk_move", "", "^MOVE"),
        # -------------------- Indices（主要股指） --------------------
        _BaseSeries("idx_spx", "^spx", "^GSPC"),
        _BaseSeries("idx_ndx", "", "^NDX"),
        _BaseSeries("idx_dji", "", "^DJI"),
        _BaseSeries("idx_rut", "", "^RUT"),
        _BaseSeries("idx_sse", "", "000001.SS"),
        _BaseSeries("idx_csi300", "", "000300.SS"),
        # -------------------- ETFs（核心ETF） --------------------
        _BaseSeries("etf_spy", "", "SPY"),
        _BaseSeries("etf_qqq", "", "QQQ"),
        _BaseSeries("etf_iwm", "", "IWM"),
        _BaseSeries("etf_tlt", "", "TLT"),
        _BaseSeries("etf_hyg", "", "HYG"),
        _BaseSeries("etf_gld", "", "GLD"),
        _BaseSeries("etf_uso", "", "USO"),
        _BaseSeries("etf_uup", "", "UUP"),
    ]

    derived: list[_DerivedSeries] = [
        _DerivedSeries("ratio_gold_silver", "div", "com_gold", "com_silver"),
        _DerivedSeries("ratio_gold_oil_wti", "div", "com_gold", "com_oil_wti"),
        _DerivedSeries("ratio_gold_oil_brent", "div", "com_gold", "com_oil_brent"),
        _DerivedSeries("ratio_silver_oil_wti", "div", "com_silver", "com_oil_wti"),
        _DerivedSeries("ratio_silver_oil_brent", "div", "com_silver", "com_oil_brent"),
        _DerivedSeries("ratio_copper_gold", "div", "com_copper", "com_gold"),
        _DerivedSeries("spread_brent_wti", "sub", "com_oil_brent", "com_oil_wti"),
        _DerivedSeries("ratio_spx_vix", "div", "idx_spx", "risk_vix"),
        _DerivedSeries("ratio_vvix_vix", "div", "risk_vvix", "risk_vix"),
        _DerivedSeries("ratio_move_vix", "div", "risk_move", "risk_vix"),
        _DerivedSeries("ratio_qqq_spy", "div", "etf_qqq", "etf_spy"),
        _DerivedSeries("ratio_spy_tlt", "div", "etf_spy", "etf_tlt"),
        _DerivedSeries("ratio_hyg_tlt", "div", "etf_hyg", "etf_tlt"),
        _DerivedSeries("ratio_btc_eth", "div", "crypto_btc", "crypto_eth"),
        _DerivedSeries("ratio_btc_oil_wti", "div", "crypto_btc", "com_oil_wti"),
        _DerivedSeries("ratio_eth_oil_wti", "div", "crypto_eth", "com_oil_wti"),
        _DerivedSeries("ratio_gold_btc", "div", "com_gold", "crypto_btc"),
    ]

    expected_headers = ["datetime_bj", "updated_at_utc"]
    for s in [*bases, *derived]:
        for m in ("last", "ret_1d", "ret_5d", "ret_20d"):
            expected_headers.append(f"{s.wide_slug}__{m}")

    end_date = dt.datetime.now(dt.timezone.utc).date()
    start_date = end_date - dt.timedelta(days=max(int(lookback_days), 30))

    # -------------------- fetch bases --------------------
    t0 = time.time()
    base_ts: dict[str, list[tuple[dt.date, float]]] = {}
    base_sources: dict[str, str] = {}
    macro_source = (os.environ.get("SHEETS_MACRO_SOURCE", "auto") or "auto").strip().lower()
    if macro_source not in {"stooq", "yahoo", "yfinance", "auto"}:
        macro_source = "auto"
    for s in bases:
        timeout = int(os.environ.get("SHEETS_MACRO_HTTP_TIMEOUT_SECONDS", "20") or "20")
        ts: list[tuple[dt.date, float]] = []
        err_stooq: Exception | None = None
        err_yahoo: Exception | None = None
        err_yfinance: Exception | None = None

        # 优先 yfinance（用户偏好），失败再回退 stooq/yahoo
        if macro_source in {"yfinance", "auto"}:
            try:
                ts = _fetch_yfinance_daily_closes(
                    s.yahoo_symbol,
                    start_date=start_date,
                    end_date=end_date,
                )
                if ts:
                    base_sources[s.wide_slug] = "yfinance"
            except Exception as exc:
                err_yfinance = exc
                ts = []

        if (not ts) and macro_source in {"stooq", "auto"}:
            try:
                ts = _fetch_stooq_daily_closes(
                    s.stooq_symbol,
                    start_date=start_date,
                    end_date=end_date,
                    timeout_seconds=timeout,
                )
                if ts:
                    base_sources[s.wide_slug] = "stooq"
            except Exception as exc:
                err_stooq = exc
                ts = []

        if (not ts) and macro_source in {"yahoo", "auto"}:
            try:
                ts = _fetch_yahoo_daily_closes(
                    s.yahoo_symbol,
                    start_date=start_date,
                    end_date=end_date,
                    timeout_seconds=timeout,
                )
                if ts:
                    base_sources[s.wide_slug] = "yahoo"
            except Exception as exc:
                err_yahoo = exc
                ts = []

        if not ts:
            stooq_err = "" if err_stooq is None else f"{type(err_stooq).__name__}:{err_stooq}"
            yahoo_err = "" if err_yahoo is None else f"{type(err_yahoo).__name__}:{err_yahoo}"
            yfinance_err = "" if err_yfinance is None else f"{type(err_yfinance).__name__}:{err_yfinance}"
            raise RuntimeError(
                f"no data for {s.wide_slug} stooq={stooq_err[:200]} yahoo={yahoo_err[:200]} yfinance={yfinance_err[:200]}"
            )
        base_ts[s.wide_slug] = ts

    # -------------------- compute metrics --------------------
    cells: dict[str, Any] = {}
    asof_dates: set[dt.date] = set()

    for s in bases:
        closes = [v for _, v in base_ts[s.wide_slug]]
        last, r1, r5, r20 = _pick_returns(closes)
        if last is None:
            continue
        asof_dates.add(base_ts[s.wide_slug][-1][0])
        cells[f"{s.wide_slug}__last"] = float(last)
        cells[f"{s.wide_slug}__ret_1d"] = "" if r1 is None else float(r1)
        cells[f"{s.wide_slug}__ret_5d"] = "" if r5 is None else float(r5)
        cells[f"{s.wide_slug}__ret_20d"] = "" if r20 is None else float(r20)

    for s in derived:
        a = base_ts.get(s.a_slug) or []
        b = base_ts.get(s.b_slug) or []
        aligned = _align_on_dates(a, b)
        dts = _derived_series_values(aligned, op=s.op)
        closes = [v for _, v in dts]
        last, r1, r5, r20 = _pick_returns(closes)
        if last is None:
            continue
        asof_dates.add(dts[-1][0])
        cells[f"{s.wide_slug}__last"] = float(last)
        cells[f"{s.wide_slug}__ret_1d"] = "" if r1 is None else float(r1)
        cells[f"{s.wide_slug}__ret_5d"] = "" if r5 is None else float(r5)
        cells[f"{s.wide_slug}__ret_20d"] = "" if r20 is None else float(r20)

    # snapshot meta
    bj = dt.timezone(dt.timedelta(hours=8))
    now_bj = dt.datetime.now(bj).strftime("%Y-%m-%d %H:%M:%S")
    now_utc = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    cells["datetime_bj"] = now_bj
    cells["updated_at_utc"] = now_utc

    # -------------------- write row2 by headers --------------------
    writer.ensure_sheet(title=tab_snapshot)
    got = writer.read_values_a1(title=tab_snapshot, a1_range="1:1")
    headers = (got[0] if got and isinstance(got[0], list) else []) or []
    # 去掉末尾空列
    while headers and (str(headers[-1]).strip() == ""):
        headers.pop()
    headers_s = [str(h or "").strip() for h in headers]
    headers_s = [h for h in headers_s if h]

    # 自愈：若表头缺失/不完整，则补齐（只追加缺失列，不重排已有列）
    if not headers_s:
        headers_s = list(expected_headers)
        writer.write_values_a1(title=tab_snapshot, a1_start="A1", values=[headers_s], value_input_option="RAW")
    else:
        existing = set(headers_s)
        missing = [h for h in expected_headers if h not in existing]
        if missing:
            headers_s = [*headers_s, *missing]
            writer.write_values_a1(title=tab_snapshot, a1_start="A1", values=[headers_s], value_input_option="RAW")

    row2: list[Any] = []
    for key in headers_s:
        row2.append(cells.get(key, ""))

    writer.write_values_a1(title=tab_snapshot, a1_start="A2", values=[row2], value_input_option="RAW")

    elapsed_ms = int((time.time() - t0) * 1000.0)
    return {
        "ok": True,
        "op": "refresh_macro_snapshot_stooq",
        "tab_snapshot": tab_snapshot,
        "headers": len(headers_s),
        "macro_source": macro_source,
        "base_sources": base_sources,
        "asof_dates": sorted({d.isoformat() for d in asof_dates}),
        "elapsed_ms": elapsed_ms,
    }
