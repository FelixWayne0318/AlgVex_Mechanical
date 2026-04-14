#!/bin/bash
# 快速同步脚本 - 强制覆盖并重启（无需确认）
# 用法: ./scripts/quick_sync.sh

set -e

BRANCH="main"
INSTALL_DIR="/home/linuxuser/nautilus_AlgVex"
SERVICE_NAME="nautilus-trader"

cd "$INSTALL_DIR"

echo ">> 强制同步代码..."
git fetch origin "$BRANCH"
git reset --hard "origin/$BRANCH"

echo ">> 最新提交:"
git log --oneline -1

echo ">> 重启服务..."
sudo systemctl restart "$SERVICE_NAME"

echo ">> 完成!"
