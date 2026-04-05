#!/usr/bin/env bash
# Binance Timescale 生产治理审计

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STACK_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(cd "$STACK_DIR/../../.." && pwd)"
SQL_FILE="$PROJECT_ROOT/scripts/ddl/binance_timescale_governance_audit.sql"
ENV_FILE="$PROJECT_ROOT/assets/config/.env"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE" >/dev/null 2>&1
  set +a
fi

DB_URL="${FACTS_DATABASE_URL:-${DATA_SERVICE_DATABASE_URL:-${DATABASE_URL:-}}}"
if [[ -z "${DB_URL:-}" ]]; then
  echo "⚠ 未配置 FACTS_DATABASE_URL / DATA_SERVICE_DATABASE_URL / DATABASE_URL，跳过 Binance Timescale 审计"
  exit 0
fi

psql "$DB_URL" -X -v ON_ERROR_STOP=1 -f "$SQL_FILE"
