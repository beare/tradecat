#!/bin/bash
# LLM Gateway Service 启动脚本

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_DIR="$(dirname "$SCRIPT_DIR")"
SERVICE_NAME="llm-gateway-service"
PID_FILE="$SERVICE_DIR/pids/llm-gateway.pid"
LOG_FILE="$SERVICE_DIR/logs/llm-gateway.log"
PORT="${LLM_GATEWAY_PORT:-9010}"

cd "$SERVICE_DIR"

# 确保目录存在（目录本身在 .gitignore 中，不会污染仓库）
mkdir -p pids logs

start() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "✓ $SERVICE_NAME 已运行 (PID: $(cat "$PID_FILE"))"
        return 0
    fi

    echo "启动 $SERVICE_NAME (端口: $PORT)..."
    # 必须 setsid 脱钩，避免在非交互执行器中被“会话回收”误杀
    setsid bash -c "exec .venv/bin/python -m src" >> "$LOG_FILE" 2>&1 < /dev/null &
    echo $! > "$PID_FILE"
    sleep 2

    if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "✓ $SERVICE_NAME 已启动 (PID: $(cat "$PID_FILE"))"
    else
        echo "✗ $SERVICE_NAME 启动失败，查看日志: $LOG_FILE"
        return 1
    fi
}

stop() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "停止 $SERVICE_NAME (PID: $PID)..."
            kill "$PID"
            sleep 2
            if kill -0 "$PID" 2>/dev/null; then
                kill -9 "$PID" 2>/dev/null || true
            fi
            echo "✓ $SERVICE_NAME 已停止"
        else
            echo "$SERVICE_NAME 未运行"
        fi
        rm -f "$PID_FILE"
    else
        echo "$SERVICE_NAME 未运行"
    fi
}

status() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        PID=$(cat "$PID_FILE")
        UPTIME=$(ps -p "$PID" -o etime= 2>/dev/null | xargs)
        echo "✓ $SERVICE_NAME 运行中 (PID: $PID, 运行: $UPTIME)"
        echo ""
        echo "=== 最近日志 ==="
        tail -10 "$LOG_FILE" 2>/dev/null || echo "无日志"
    else
        echo "✗ $SERVICE_NAME 未运行"
    fi
}

case "${1:-status}" in
    start)   start ;;
    stop)    stop ;;
    restart) stop; sleep 1; start ;;
    status)  status ;;
    *)       echo "用法: $0 {start|stop|restart|status}"; exit 1 ;;
esac

