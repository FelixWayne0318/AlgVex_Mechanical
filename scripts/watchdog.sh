#!/bin/bash
SERVICE_NAME="nautilus-trader"
LOG_FILE="/home/linuxuser/nautilus_AlgVex/logs/watchdog.log"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Watchdog check..." >> "$LOG_FILE"

# 检查服务状态
if ! systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ❌ Service stopped, restarting..." >> "$LOG_FILE"
    sudo systemctl restart "$SERVICE_NAME"
    exit 1
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✅ Service running" >> "$LOG_FILE"
