#!/usr/bin/env bash
# Binance 单工程根静态门禁

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STACK_DIR="$(dirname "$SCRIPT_DIR")"

required_paths=(
  "$STACK_DIR/pyproject.toml"
  "$STACK_DIR/Makefile"
  "$STACK_DIR/src/binance/config.py"
  "$STACK_DIR/src/binance/registry.py"
  "$STACK_DIR/src/binance/service_entry.py"
  "$STACK_DIR/src/binance/common/env.py"
  "$STACK_DIR/src/binance/common/hf_env.py"
  "$STACK_DIR/src/binance/runtime/stack_runner.py"
  "$STACK_DIR/src/binance/runtime/hf_canary.py"
  "$STACK_DIR/src/binance/sources/binance_api/config.py"
  "$STACK_DIR/src/binance/sources/archive_source/download_utils.py"
  "$STACK_DIR/src/binance/storage/ingest_meta.py"
  "$STACK_DIR/scripts/check_timescale_governance.sh"
  "$STACK_DIR/tests/test_registry_smoke.py"
)

for path in "${required_paths[@]}"; do
  if [[ ! -f "$path" ]]; then
    echo "✗ 缺少单工程根关键文件: $path"
    exit 1
  fi
done

if ! grep -q 'binance.service_entry' "$STACK_DIR/scripts/start.sh"; then
  echo "✗ core/market/binance/scripts/start.sh 未接入统一 service_entry"
  exit 1
fi

for forbidden in   "$STACK_DIR/lf"   "$STACK_DIR/hf"   "$STACK_DIR/data"   "$STACK_DIR/src/var"   "$STACK_DIR/src/binance/logs"   "$STACK_DIR/src/binance/lf_shared"   "$STACK_DIR/src/binance/writers"   "$STACK_DIR/src/binance/vision_runtime"   "$STACK_DIR/src/binance/vision_download"
do
  if [[ -e "$forbidden" ]]; then
    echo "✗ 发现未退役的旧结构残留: $forbidden"
    exit 1
  fi
done

echo "✓ Binance 结构契约门禁通过"
