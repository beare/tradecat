#!/usr/bin/env bash
# Binance Stack 编排入口（薄壳）。
# 当前职责：只负责把外部调用转发到统一内部入口 `python -m binance.service_entry`。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BINANCE_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(cd "$BINANCE_DIR/../../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-}"

if [[ -z "$PYTHON_BIN" ]]; then
    if [[ -x "$BINANCE_DIR/.venv/bin/python3" ]]; then
        PYTHON_BIN="$BINANCE_DIR/.venv/bin/python3"
    else
        PYTHON_BIN="python3"
    fi
fi

if [[ "$#" -eq 0 ]]; then
    set -- status
fi

export PYTHONPATH="$BINANCE_DIR/src:$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON_BIN" -m binance.service_entry "$@"
