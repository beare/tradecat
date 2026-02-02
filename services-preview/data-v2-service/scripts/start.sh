#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${ROOT_DIR}/logs"
PID_DIR="${ROOT_DIR}/pids"
PID_FILE="${PID_DIR}/data-v2-service.pid"
PY="${ROOT_DIR}/.venv/bin/python"

mkdir -p "${LOG_DIR}" "${PID_DIR}"

cmd_start() {
  if [[ -f "${PID_FILE}" ]] && kill -0 "$(cat "${PID_FILE}")" 2>/dev/null; then
    echo "已在运行 (PID=$(cat "${PID_FILE}"))"
    exit 0
  fi

  if [[ ! -x "${PY}" ]]; then
    echo "未找到虚拟环境 python: ${PY}"
    echo "请先执行: make install"
    exit 1
  fi

  nohup "${PY}" -m src --mode ws --tool ccxt --seconds 0 > "${LOG_DIR}/data-v2-service.log" 2>&1 &
  echo $! > "${PID_FILE}"
  echo "启动成功 (PID=$!)"
}

cmd_stop() {
  if [[ ! -f "${PID_FILE}" ]]; then
    echo "未运行"
    exit 0
  fi
  PID="$(cat "${PID_FILE}")"
  if kill -0 "${PID}" 2>/dev/null; then
    kill "${PID}"
    echo "已发送停止信号 (PID=${PID})"
  else
    echo "PID 不存在 (PID=${PID})"
  fi
  rm -f "${PID_FILE}"
}

cmd_status() {
  if [[ -f "${PID_FILE}" ]] && kill -0 "$(cat "${PID_FILE}")" 2>/dev/null; then
    echo "running PID=$(cat "${PID_FILE}")"
  else
    echo "stopped"
    exit 1
  fi
}

case "${1:-}" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  status) cmd_status ;;
  *)
    echo "用法: $0 start|stop|status"
    exit 1
    ;;
esac

