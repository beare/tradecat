#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""路径助手

统一提供仓库/服务/数据目录的解析，避免在脚本中反复手写 parents[n]。
约定：新增路径相关逻辑一律经由本模块，严禁再写相对路径或随意猜测层级。
"""

from __future__ import annotations

from pathlib import Path
from typing import Final


def _探测仓库根(start: Path) -> Path:
    for p in start.parents:
        if (p / "core").exists() and (p / "assets" / "config" / ".env.example").exists():
            return p
        if (p / "core").exists() and (p / "config" / ".env.example").exists():
            return p
        if (p / "services").exists() and (p / "assets" / "config" / ".env.example").exists():
            return p
        if (p / "services").exists() and (p / "config" / ".env.example").exists():
            return p
    fallback_idx = 3 if len(start.parents) > 3 else len(start.parents) - 1
    return start.parents[fallback_idx]


_HERE: Final[Path] = Path(__file__).resolve()
仓库根目录: Final[Path] = _探测仓库根(_HERE)
_BINANCE_ROOT: Final[Path] = 仓库根目录 / "core" / "market" / "binance"


def 获取仓库根目录() -> Path:
    return 仓库根目录


def 获取服务根目录(service: str) -> Path:
    alias_map = {
        "data-service": "binance",
        "data": "binance",
        "lf": "binance",
        "lf-service": "binance",
        "hf": "binance",
        "hf-service": "binance",
    }
    normalized = alias_map.get(service, service)

    if normalized == "binance":
        return _BINANCE_ROOT

    candidates = [
        仓库根目录 / "core" / "market" / normalized,
        仓库根目录 / "core" / "alternative" / normalized,
        仓库根目录 / "core" / "query" / normalized,
        仓库根目录 / "core" / "storage" / normalized,
        仓库根目录 / "plugins" / normalized,
        仓库根目录 / "services" / normalized,
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[-1]


def 获取数据服务CSV目录() -> Path:
    return _BINANCE_ROOT / "var" / "lf" / "data" / "csv"


def 获取日志目录(service: str) -> Path:
    if service in {"lf", "lf-service", "data", "data-service"}:
        return _BINANCE_ROOT / "var" / "lf" / "logs"
    if service in {"hf", "hf-service"}:
        return _BINANCE_ROOT / "var" / "hf" / "logs"
    return 获取服务根目录(service) / "logs"


def 确保目录(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


__all__ = [
    "仓库根目录",
    "获取仓库根目录",
    "获取服务根目录",
    "获取数据服务CSV目录",
    "获取日志目录",
    "确保目录",
]
