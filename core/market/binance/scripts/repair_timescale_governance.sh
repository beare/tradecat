#!/usr/bin/env bash
# Binance Timescale 生产治理修复

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STACK_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(cd "$STACK_DIR/../../.." && pwd)"
SQL_FILE="$PROJECT_ROOT/scripts/ddl/binance_timescale_governance_repair.sql"
ENV_FILE="$PROJECT_ROOT/assets/config/.env"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE" >/dev/null 2>&1
  set +a
fi

DB_URL="${FACTS_DATABASE_URL:-${DATA_SERVICE_DATABASE_URL:-${DATABASE_URL:-}}}"
if [[ -z "${DB_URL:-}" ]]; then
  echo "✗ 未配置 FACTS_DATABASE_URL / DATA_SERVICE_DATABASE_URL / DATABASE_URL"
  exit 1
fi

psql "$DB_URL" -X -v ON_ERROR_STOP=1 -f "$SQL_FILE"

JOB_IDS="$(
  psql "$DB_URL" -X -A -t -v ON_ERROR_STOP=1 <<'SQL'
SELECT job_id
FROM timescaledb_information.jobs
WHERE hypertable_schema = 'market'
  AND hypertable_name IN (
    'binance_futures_um_trades',
    'binance_futures_um_book_ticker',
    'binance_futures_um_book_depth',
    'binance_spot_trades',
    'binance_cagg_spot_klines_1m'
  )
ORDER BY job_id;
SQL
)"

while IFS= read -r job_id; do
  [[ -z "${job_id:-}" ]] && continue
  psql "$DB_URL" -X -v ON_ERROR_STOP=1 -c "CALL run_job(${job_id});" >/dev/null
done <<< "$JOB_IDS"

"$SCRIPT_DIR/check_timescale_governance.sh"
