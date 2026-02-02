#!/usr/bin/env python3
"""
Binance U 本位合约（binanceusdm）端点扫描（ccxt REST + ccxt.pro WS）。

目标（人话）：
- 把“ccxt 在当前版本里暴露出来的、可用于 binanceusdm 的能力”扫出来；
- 包含：
  - REST：describe()['api'] 路由树、隐式 API 方法名、unified has 能力表
  - WS（ccxt.pro）：watch* 与 *Ws 方法集合（含签名、是否空桩）

注意：
- “无遗漏”仅在“指定交易所 + 指定 ccxt/ccxtpro 版本 + 当前环境”这个范围内成立。
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _snake(name: str) -> str:
    out: list[str] = []
    for i, c in enumerate(name):
        if c.isupper() and i:
            out.append("_")
        out.append(c.lower())
    return "".join(out)


def _is_not_supported_stub(fn) -> bool:
    try:
        src = inspect.getsource(fn)
    except Exception:
        return False
    return ("raise NotSupported" in src) or ("NotSupported(" in src and "raise" in src)


def _safe_signature(fn) -> str:
    try:
        return str(inspect.signature(fn))
    except Exception:
        return "(signature unavailable)"


def _safe_doc_params(fn) -> list[str]:
    doc = getattr(fn, "__doc__", "") or ""
    lines = [ln.strip() for ln in doc.splitlines() if ln.strip()]
    return [ln for ln in lines if ln.startswith(":param") or ln.startswith(":returns") or "params." in ln]


def _pick_proxy(cli_proxy: Optional[str]) -> Optional[str]:
    if cli_proxy:
        return cli_proxy
    return os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY")


@dataclass(frozen=True)
class ScanResult:
    ok: bool
    payload: dict[str, Any]


def scan_rest_binanceusdm(*, proxy: Optional[str]) -> ScanResult:
    try:
        import ccxt  # type: ignore
    except Exception as e:
        return ScanResult(False, {"error": f"ccxt import 失败: {e!r}"})

    try:
        ex = ccxt.binanceusdm(  # type: ignore[attr-defined]
            {
                "enableRateLimit": True,
                "timeout": 20000,
                "proxies": {"http": proxy, "https": proxy} if proxy else None,
            }
        )
    except Exception as e:
        return ScanResult(False, {"error": f"ccxt.binanceusdm 初始化失败: {e!r}"})

    payload: dict[str, Any] = {"exchange": "binanceusdm", "proxy": proxy, "ccxt_version": getattr(ccxt, "__version__", None)}
    try:
        desc = ex.describe()
        payload["describe_api"] = desc.get("api")
        payload["has_true"] = sorted([k for k, v in (ex.has or {}).items() if v])  # type: ignore[attr-defined]

        # 隐式 API：public/private + 各 api group（fapiPublic、fapiPrivate 等）
        implicit_methods: list[str] = []
        implicit_re = re.compile(r".*(Get|Post|Put|Delete|Patch)[A-Z].*")
        for name in dir(ex):
            if implicit_re.fullmatch(name):
                implicit_methods.append(name)
        payload["implicit_methods"] = sorted(set(implicit_methods))

        # 额外：统一接口（fetch_* 等）方法名集合（便于用户快速定位）
        unified_methods: list[str] = []
        unified_re = re.compile(r"^(fetch|load|create|edit|cancel|watch|subscribe|unsubscribe)_[a-z0-9_]+$")
        for name in dir(ex):
            if unified_re.fullmatch(name):
                unified_methods.append(name)
        payload["unified_methods"] = sorted(set(unified_methods))

        return ScanResult(True, payload)
    except Exception as e:
        payload["error"] = f"REST 扫描失败: {e!r}"
        return ScanResult(False, payload)
    finally:
        try:
            ex.close()
        except Exception:
            pass


def scan_ws_binanceusdm(*, proxy: Optional[str]) -> ScanResult:
    try:
        import ccxt.pro as ccxtpro  # type: ignore
    except Exception as e:
        return ScanResult(False, {"error": f"ccxt.pro import 失败: {e!r}"})

    try:
        ex = ccxtpro.binanceusdm(  # type: ignore[attr-defined]
            {
                "enableRateLimit": True,
                "timeout": 20000,
                # REST（load_markets）走 httpsProxy；WS 走 wssProxy
                "httpsProxy": proxy,
                "wssProxy": proxy,
            }
        )
    except Exception as e:
        return ScanResult(False, {"error": f"ccxt.pro.binanceusdm 初始化失败: {e!r}"})

    payload: dict[str, Any] = {"exchange": "binanceusdm", "proxy": proxy, "ccxtpro_version": getattr(ccxtpro, "__version__", None)}
    try:
        # 只为了让 symbol/market 类型等数据完整；失败也不阻塞“端点枚举”
        try:
            import asyncio

            try:
                asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(ex.load_markets())
        except Exception:
            pass

        has_dict = ex.has or {}
        watch_keys = sorted([k for k, v in has_dict.items() if isinstance(k, str) and k.startswith("watch") and v])
        wsapi_keys = sorted([k for k, v in has_dict.items() if isinstance(k, str) and k.endswith("Ws") and v])

        def build_method_info(camel_name: str) -> dict[str, Any]:
            py_name = _snake(camel_name)
            fn = getattr(ex, py_name, None) or getattr(ex, camel_name, None)
            if fn is None:
                return {"name": camel_name, "py_name": py_name, "exists": False}
            return {
                "name": camel_name,
                "py_name": py_name,
                "exists": True,
                "module": getattr(fn, "__module__", None),
                "signature": _safe_signature(fn),
                "is_stub": _is_not_supported_stub(fn),
                "doc_params": _safe_doc_params(fn)[:60],
            }

        payload["watch_methods"] = [build_method_info(k) for k in watch_keys]
        payload["wsapi_methods"] = [build_method_info(k) for k in wsapi_keys]
        payload["has_true"] = sorted([k for k, v in has_dict.items() if v])

        return ScanResult(True, payload)
    except Exception as e:
        payload["error"] = f"WS 扫描失败: {e!r}"
        return ScanResult(False, payload)
    finally:
        try:
            import asyncio

            try:
                asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(ex.close())
        except Exception:
            pass


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="scan ccxt endpoints for binanceusdm (REST + WS)")
    p.add_argument("--proxy", help="代理地址，例如 http://127.0.0.1:7890（默认读取 HTTP_PROXY/HTTPS_PROXY）")
    p.add_argument("--out", help="输出 JSON 文件路径（默认写到 artifacts/ 下）")
    args = p.parse_args(argv)

    proxy = _pick_proxy(args.proxy)

    rest = scan_rest_binanceusdm(proxy=proxy)
    ws = scan_ws_binanceusdm(proxy=proxy)

    report: dict[str, Any] = {
        "generated_at": _utc_now_iso(),
        "target": "binanceusdm",
        "proxy": proxy,
        "rest": rest.payload,
        "ws": ws.payload,
        "ok": bool(rest.ok and ws.ok),
    }

    out = Path(args.out) if args.out else (Path(__file__).resolve().parents[1] / "artifacts" / "scan_binanceusdm_ccxt.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    # stdout：子弹总结（人话）
    rest_has = len(report["rest"].get("has_true", []) or [])
    rest_implicit = len(report["rest"].get("implicit_methods", []) or [])
    ws_watch = len(report["ws"].get("watch_methods", []) or [])
    ws_wsapi = len(report["ws"].get("wsapi_methods", []) or [])

    print(f"已输出: {out}")
    print(f"REST: has_true={rest_has} implicit_methods={rest_implicit}")
    print(f"WS: watch_methods={ws_watch} wsapi_methods={ws_wsapi}")

    # 如果 WS/REST 有 error，也打印出来方便你直接定位
    if report["rest"].get("error"):
        print(f"REST error: {report['rest']['error']}")
    if report["ws"].get("error"):
        print(f"WS error: {report['ws']['error']}")

    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
