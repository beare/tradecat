#!/bin/bash

# Deployment configuration
# 强制从环境变量读取，避免明文暴露
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEFAULT_LOCAL_PATH="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVER_IP="${SERVER_IP:?set SERVER_IP}"
SERVER_USER="${SERVER_USER:-root}"
SERVER_PASSWORD="${SERVER_PASSWORD:?set SERVER_PASSWORD}"
REMOTE_PATH="${REMOTE_PATH:-~/.projects/polymarket}"
LOCAL_PATH="${LOCAL_PATH:-$DEFAULT_LOCAL_PATH}"
TMP_DIR="${TMP_DIR:-${TMPDIR:-/tmp}}"
NODEJS_SETUP_URL="${NODEJS_SETUP_URL:?set NODEJS_SETUP_URL}"

echo "==================================="
echo "Polymarket Bot Deployment Script"
echo "==================================="
echo ""

# Check if sshpass is installed
if ! command -v sshpass &> /dev/null; then
    echo "Installing sshpass..."
    sudo apt-get update -qq
    sudo apt-get install -y sshpass
fi

# Test SSH connection
echo "Testing SSH connection..."
sshpass -p "$SERVER_PASSWORD" ssh -o StrictHostKeyChecking=no "$SERVER_USER@$SERVER_IP" "echo 'Connection successful'" || {
    echo "Failed to connect to server"
    exit 1
}

# Create remote directory
echo "Creating remote directory..."
sshpass -p "$SERVER_PASSWORD" ssh -o StrictHostKeyChecking=no "$SERVER_USER@$SERVER_IP" "mkdir -p $REMOTE_PATH"

# Prepare files for transfer (exclude unnecessary files)
echo "Preparing files for transfer..."
cd "$LOCAL_PATH"

# Create a temporary list of files to exclude
RSYNC_EXCLUDE_FILE="$(mktemp "${TMP_DIR%/}/rsync-exclude.XXXXXX.txt")"
cat > "$RSYNC_EXCLUDE_FILE" <<EOF
.git
node_modules
bot/node_modules
dist
*.log
.env
bot/.env
bot/data/users.json
bot/data/translation-cache.json
*.zip
archive/
.日志
polymarket-bot-windows/
polymarket-signal-bot-release/
EOF

# Transfer files to server
echo "Transferring files to server..."
sshpass -p "$SERVER_PASSWORD" rsync -avz --progress \
    --exclude-from="$RSYNC_EXCLUDE_FILE" \
    "$LOCAL_PATH/" \
    "$SERVER_USER@$SERVER_IP:$REMOTE_PATH/"

# Install dependencies and setup
echo "Installing dependencies on server..."
sshpass -p "$SERVER_PASSWORD" ssh -o StrictHostKeyChecking=no "$SERVER_USER@$SERVER_IP" "REMOTE_PATH='$REMOTE_PATH' NODEJS_SETUP_URL='$NODEJS_SETUP_URL' bash" << 'REMOTE_SCRIPT'
cd "$REMOTE_PATH"
NODEJS_SETUP_URL="${NODEJS_SETUP_URL:?set NODEJS_SETUP_URL}"

echo "Checking Node.js installation..."
if ! command -v node &> /dev/null; then
    echo "Installing Node.js..."
    curl -fsSL "$NODEJS_SETUP_URL" | bash -
    apt-get install -y nodejs
fi

echo "Node version: $(node --version)"
echo "NPM version: $(npm --version)"

echo "Installing main project dependencies..."
npm install

echo "Installing bot dependencies..."
cd bot
npm install

echo "Creating necessary directories..."
mkdir -p data logs

echo "Checking if .env exists..."
if [ ! -f .env ]; then
    echo "Warning: .env file not found. Please configure it manually."
    if [ -f .env.example ]; then
        cp .env.example .env
        echo "Created .env from .env.example"
    fi
fi

echo ""
echo "==================================="
echo "Deployment completed successfully!"
echo "==================================="
echo ""
echo "Next steps:"
echo "1. SSH to server: ssh ${SERVER_USER}@${SERVER_IP}"
echo "2. Configure .env: nano ${REMOTE_PATH}/bot/.env"
echo "3. Start bot: cd ${REMOTE_PATH}/bot && node src/bot.js"
echo ""
REMOTE_SCRIPT

# Cleanup
rm -f "$RSYNC_EXCLUDE_FILE"

echo ""
echo "Deployment process completed!"
echo "Server: $SERVER_IP"
echo "Path: $REMOTE_PATH"
