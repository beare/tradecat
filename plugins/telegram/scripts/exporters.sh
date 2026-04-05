#!/usr/bin/env bash

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_DIR="$(dirname "$SCRIPT_DIR")"

find_project_root() {
    local current="$1"
    for _ in {1..12}; do
        if [[ -f "$current/assets/config/.env.example" ]] && { [[ -d "$current/core" ]] || [[ -d "$current/plugins" ]]; }; then
            echo "$current"
            return 0
        fi
        local parent
        parent="$(dirname "$current")"
        [[ "$parent" == "$current" ]] && break
        current="$parent"
    done
    (cd "$SERVICE_DIR/../.." && pwd)
}

resolve_env_file() {
    local root="$1"
    local candidates=(
        "$root/assets/config/.env"
        "$root/config/.env"
    )
    for candidate in "${candidates[@]}"; do
        if [[ -f "$candidate" ]]; then
            echo "$candidate"
            return 0
        fi
    done
    echo "${candidates[0]}"
}

safe_load_env() {
    local file="$1"
    [ -f "$file" ] || return 0
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        [[ "$line" =~ ^[[:space:]]*export ]] && continue
        [[ "$line" =~ \$\( ]] && continue
        [[ "$line" =~ \` ]] && continue
        if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
            local key="${BASH_REMATCH[1]}"
            local val="${BASH_REMATCH[2]}"
            val="${val#\"}" && val="${val%\"}"
            val="${val#\'}" && val="${val%\'}"
            export "$key=$val"
        fi
    done < "$file"
}

PROJECT_ROOT="$(find_project_root "$SERVICE_DIR")"
ENV_FILE="$(resolve_env_file "$PROJECT_ROOT")"
safe_load_env "$ENV_FILE"

RUN_DIR="$SERVICE_DIR/pids"
LOG_DIR="$SERVICE_DIR/logs"
VENV_DIR="$SERVICE_DIR/.venv"
PUBLISHER_PID="$RUN_DIR/publisher.pid"
SIGNAL_PID="$RUN_DIR/signal.pid"

init_dirs() {
    mkdir -p "$RUN_DIR" "$LOG_DIR"
}

ensure_venv() {
    if [[ -d "$VENV_DIR" ]]; then
        return 0
    fi
    python3 -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install -q --upgrade pip
    "$VENV_DIR/bin/pip" install -q -r "$SERVICE_DIR/requirements.txt"
}

check_proxy() {
    local proxy="${HTTP_PROXY:-${HTTPS_PROXY:-}}"
    [ -z "$proxy" ] && return 0
    local ping_url="${BINANCE_PING_URL:-https://fapi.binance.com/fapi/v1/ping}"
    if curl -s --max-time 3 --proxy "$proxy" "$ping_url" >/dev/null 2>&1; then
        echo "✓ 代理可用: $proxy"
        return 0
    fi
    echo "⚠️  代理不可用，已忽略: $proxy"
    unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy
}

preflight_query_service() {
    local base="${QUERY_SERVICE_BASE_URL:-}"
    if [[ -z "$base" ]]; then
        echo "❌ 错误: QUERY_SERVICE_BASE_URL 未配置"
        exit 1
    fi
    base="${base%/}"
    local mode="${QUERY_SERVICE_AUTH_MODE:-required}"
    mode="$(echo "$mode" | tr '[:upper:]' '[:lower:]' | xargs)"
    local url="$base/api/v1/health"
    local resp=""
    if [[ "$mode" == "disabled" || "$mode" == "off" ]]; then
        resp="$(curl -s --max-time 2 "$url" || true)"
    else
        if [[ -z "${QUERY_SERVICE_TOKEN:-}" || "${QUERY_SERVICE_TOKEN:-}" == "dev-token-change-me" || "${QUERY_SERVICE_TOKEN:-}" == "your_token_here" ]]; then
            echo "❌ 错误: QUERY_SERVICE_TOKEN 未配置或仍为占位值"
            exit 1
        fi
        resp="$(curl -s --max-time 2 -H "X-Internal-Token: $QUERY_SERVICE_TOKEN" "$url" || true)"
    fi
    if echo "$resp" | grep -q '\"success\":true'; then
        echo "✓ Query Service 就绪: $base"
        return 0
    fi
    echo "❌ Query Service 不可用: $base"
    exit 1
}

is_running() {
    local pid="$1"
    [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

get_pid() {
    local pid_file="$1"
    [[ -f "$pid_file" ]] && cat "$pid_file"
}

start_worker() {
    local mode="$1"
    local pid_file="$2"
    local log_file="$3"
    init_dirs
    ensure_venv
    check_proxy
    preflight_query_service
    local pid
    pid="$(get_pid "$pid_file")"
    if is_running "$pid"; then
        echo "✓ ${mode} 已运行 (PID: $pid)"
        return 0
    fi
    cd "$SERVICE_DIR"
    setsid "$VENV_DIR/bin/python" -m src.publishers.runner "$mode" >> "$log_file" 2>&1 < /dev/null &
    local new_pid=$!
    echo "$new_pid" > "$pid_file"
    sleep 2
    if is_running "$new_pid"; then
        echo "✓ ${mode} 已启动 (PID: $new_pid)"
        return 0
    fi
    echo "✗ ${mode} 启动失败"
    return 1
}

stop_worker() {
    local mode="$1"
    local pid_file="$2"
    local pid
    pid="$(get_pid "$pid_file")"
    if ! is_running "$pid"; then
        rm -f "$pid_file"
        echo "${mode} 未运行"
        return 0
    fi
    kill "$pid" 2>/dev/null || true
    sleep 1
    if is_running "$pid"; then
        kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$pid_file"
    echo "✓ ${mode} 已停止"
}

status_worker() {
    local mode="$1"
    local pid_file="$2"
    local log_file="$3"
    local pid
    pid="$(get_pid "$pid_file")"
    if is_running "$pid"; then
        local uptime
        uptime="$(ps -o etime= -p "$pid" 2>/dev/null | tr -d ' ')"
        echo "✓ ${mode} 运行中 (PID: $pid, 运行: $uptime)"
        echo ""
        echo "=== 最近日志 ==="
        tail -10 "$log_file" 2>/dev/null
        return 0
    fi
    rm -f "$pid_file"
    echo "✗ ${mode} 未运行"
    return 1
}

case "${1:-status}" in
    publisher-start) start_worker "publisher" "$PUBLISHER_PID" "$LOG_DIR/publisher.log" ;;
    publisher-stop) stop_worker "publisher" "$PUBLISHER_PID" ;;
    publisher-status) status_worker "publisher" "$PUBLISHER_PID" "$LOG_DIR/publisher.log" ;;
    signal-start) start_worker "signal" "$SIGNAL_PID" "$LOG_DIR/signal.log" ;;
    signal-stop) stop_worker "signal" "$SIGNAL_PID" ;;
    signal-status) status_worker "signal" "$SIGNAL_PID" "$LOG_DIR/signal.log" ;;
    all-start) start_worker "publisher" "$PUBLISHER_PID" "$LOG_DIR/publisher.log" && start_worker "signal" "$SIGNAL_PID" "$LOG_DIR/signal.log" ;;
    all-stop) stop_worker "publisher" "$PUBLISHER_PID" && stop_worker "signal" "$SIGNAL_PID" ;;
    all-status)
        status_worker "publisher" "$PUBLISHER_PID" "$LOG_DIR/publisher.log" || true
        echo "---"
        status_worker "signal" "$SIGNAL_PID" "$LOG_DIR/signal.log" || true
        ;;
    *)
        echo "用法: $0 {publisher-start|publisher-stop|publisher-status|signal-start|signal-stop|signal-status|all-start|all-stop|all-status}"
        exit 1
        ;;
esac
