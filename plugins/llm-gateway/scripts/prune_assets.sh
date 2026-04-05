#!/bin/bash
# 只清理 run_asset 目录内的 trace 资产（目录名为 32 位 hex trace_id）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_DIR="$(dirname "$SCRIPT_DIR")"

ROOT_DEFAULT="$SERVICE_DIR/data/run_asset"
ROOT="${LLM_GATEWAY_RUN_ASSETS_DIR:-$ROOT_DEFAULT}"

# 默认保留 7 天，最多保留 2000 份（双保险）
TTL_HOURS="${LLM_GATEWAY_RUN_ASSETS_TTL_HOURS:-168}"
MAX_DIRS="${LLM_GATEWAY_RUN_ASSETS_MAX_DIRS:-2000}"

usage() {
  echo "用法：$0 [--root <dir>] [--ttl-hours <hours>] [--max-dirs <n>]"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) ROOT="$2"; shift 2 ;;
    --ttl-hours) TTL_HOURS="$2"; shift 2 ;;
    --max-dirs) MAX_DIRS="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数: $1"; usage; exit 2 ;;
  esac
done

PROJECT_ROOT="$(python3 - <<PY
from pathlib import Path
start = Path(${SERVICE_DIR@Q}).resolve()
for p in [start] + list(start.parents):
    if not (p / "assets").is_dir():
        continue
    if not ((p / "core").is_dir() or (p / "plugins").is_dir()):
        continue
    if (p / "assets" / "config" / ".env.example").exists() or (p / "config" / ".env.example").exists():
        print(str(p))
        break
else:
    print(str(start))
PY
)"

if [[ "$ROOT" != /* ]]; then
  ROOT="$PROJECT_ROOT/$ROOT"
fi

if [[ -z "$ROOT" || "$ROOT" == "/" ]]; then
  echo "拒绝清理不安全目录: ROOT=$ROOT"
  exit 2
fi

ROOT="$(python3 - <<PY
import os
print(os.path.abspath(os.path.expanduser(${ROOT@Q})))
PY
)"

if [[ ! -d "$ROOT" ]]; then
  echo "目录不存在，无需清理: $ROOT"
  exit 0
fi

echo "清理 run_asset: root=$ROOT ttl_hours=$TTL_HOURS max_dirs=$MAX_DIRS"

# 仅匹配 trace_id 目录（32 位 hex）
regex='^.*/[0-9a-f]{32}$'

# 1) TTL 清理
find "$ROOT" -mindepth 1 -maxdepth 1 -type d -regextype posix-extended -regex "$regex" -mmin "+$((TTL_HOURS*60))" -print0 \
  | xargs -0r rm -rf --

# 2) 超量清理（按 mtime 从老到新删）
count="$(find "$ROOT" -mindepth 1 -maxdepth 1 -type d -regextype posix-extended -regex "$regex" | wc -l | xargs)"
if [[ "$count" -le "$MAX_DIRS" ]]; then
  echo "✅ 目录数量正常: $count"
  exit 0
fi

excess="$((count - MAX_DIRS))"
echo "目录数量超限: $count，需删除最老的 $excess 个"

find "$ROOT" -mindepth 1 -maxdepth 1 -type d -regextype posix-extended -regex "$regex" -printf '%T@ %p\n' \
  | sort -n \
  | head -n "$excess" \
  | cut -d' ' -f2- \
  | xargs -r rm -rf --

echo "✅ 清理完成"
