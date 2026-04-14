#!/bin/bash
# 服务器同步脚本 - 从 GitHub 仓库同步代码
# 用法: ./scripts/sync_from_repo.sh

set -e

# 配置
BRANCH="main"
INSTALL_DIR="/home/linuxuser/nautilus_AlgVex"
SERVICE_NAME="nautilus-trader"

echo "========================================"
echo "  AlgVex 代码同步工具"
echo "========================================"
echo ""
echo "分支: $BRANCH"
echo "目录: $INSTALL_DIR"
echo ""

# 检查是否在正确目录
if [ ! -d "$INSTALL_DIR" ]; then
    echo "错误: 目录 $INSTALL_DIR 不存在"
    exit 1
fi

cd "$INSTALL_DIR"

# 显示当前状态
echo ">> 当前状态:"
git status --short
echo ""

# 检查是否有本地修改
if [ -n "$(git status --porcelain)" ]; then
    echo "警告: 检测到本地修改"
    echo ""
    read -p "是否丢弃本地修改并强制同步? (y/N): " confirm
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        echo "取消同步"
        exit 0
    fi
    echo ""
    echo ">> 丢弃本地修改..."
    git checkout .
    git clean -fd
fi

# 获取最新代码
echo ">> 拉取最新代码..."
git fetch origin "$BRANCH"
git reset --hard "origin/$BRANCH"

echo ""
echo ">> 最新提交:"
git log --oneline -3

# 重启服务
echo ""
read -p "是否重启服务? (Y/n): " restart
if [ "$restart" != "n" ] && [ "$restart" != "N" ]; then
    echo ">> 重启服务..."
    sudo systemctl restart "$SERVICE_NAME"
    sleep 2
    echo ""
    echo ">> 服务状态:"
    sudo systemctl status "$SERVICE_NAME" --no-pager | head -20
fi

echo ""
echo "========================================"
echo "  同步完成!"
echo "========================================"
