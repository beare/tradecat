#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Reuse the repo's safe .env loader (avoid `source assets/config/.env`).
# shellcheck disable=SC1091
source "${REPO_ROOT}/scripts/alternative/runtime_helpers.sh"

SERVICE_DIR="${REPO_ROOT}/plugins/TradingAgents-main"

usage() {
    cat <<'EOF'
Usage:
  ./scripts/tradingagents.sh install
  ./scripts/tradingagents.sh run [-- <tradingagents args...>]

Notes:
  - This repo tracks TradingAgents as a git submodule at plugins/TradingAgents-main.
  - Initialize it first:
      git submodule update --init --recursive plugins/TradingAgents-main
  - API keys are loaded from assets/config/.env (allowlist) into env.
EOF
}

ensure_submodule_ready() {
    if [[ ! -d "$SERVICE_DIR" || ! -f "$SERVICE_DIR/pyproject.toml" ]]; then
        echo "TradingAgents submodule 未初始化。请先执行：" >&2
        echo "  git submodule update --init --recursive plugins/TradingAgents-main" >&2
        exit 1
    fi
}

main() {
    local cmd="${1:-}"
    case "$cmd" in
        install)
            ensure_submodule_ready
            (cd "$SERVICE_DIR" && uv sync --frozen)
            ;;
        run)
            ensure_submodule_ready
            # Keep it minimal: only load the keys TradingAgents might need.
            load_repo_env_keys "$REPO_ROOT" \
                OPENAI_API_KEY GOOGLE_API_KEY ANTHROPIC_API_KEY XAI_API_KEY OPENROUTER_API_KEY ALPHA_VANTAGE_API_KEY \
                HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY

            shift || true
            if [[ "${1:-}" == "--" ]]; then
                shift || true
            fi
            (cd "$SERVICE_DIR" && uv run tradingagents "$@")
            ;;
        ""|"-h"|"--help"|help)
            usage
            ;;
        *)
            echo "Unknown command: $cmd" >&2
            usage >&2
            exit 2
            ;;
    esac
}

main "$@"

