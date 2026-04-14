#!/bin/bash
# 完整部署脚本 - 清空服务器、拉取最新代码、覆盖服务器、运行诊断
# 用法: ./scripts/full_deploy.sh

set -e

# ============================================================================
# 配置
# ============================================================================
SERVER_IP="139.180.157.152"
SERVER_USER="linuxuser"
INSTALL_DIR="/home/linuxuser/nautilus_AlgVex"
BRANCH="main"
SERVICE_NAME="nautilus-trader"
REPO_URL="https://github.com/FelixWayne0318/AlgVex.git"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_step() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}========================================${NC}\n"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

# ============================================================================
# 开始部署
# ============================================================================
echo -e "${GREEN}"
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║         AlgVex 完整部署脚本                                  ║"
echo "║         清空 → 拉取 → 覆盖 → 诊断                              ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

echo "服务器: $SERVER_USER@$SERVER_IP"
echo "路径:   $INSTALL_DIR"
echo "分支:   $BRANCH"
echo ""

# 确认操作
if [ "$AUTO_CONFIRM" != "true" ]; then
    read -p "此操作将清空服务器代码并重新部署，确认继续? (y/N): " confirm
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        echo "取消部署"
        exit 0
    fi
fi

# ============================================================================
# Step 1: 停止服务器上的服务
# ============================================================================
print_step "Step 1/5: 停止服务器上的服务"

ssh ${SERVER_USER}@${SERVER_IP} << 'ENDSSH'
    echo "停止 nautilus-trader 服务..."
    sudo systemctl stop nautilus-trader 2>/dev/null || true

    # 确保所有相关进程都已停止
    if pgrep -f "main_live.py" > /dev/null 2>&1; then
        echo "清理残留进程..."
        sudo pkill -f "main_live.py" 2>/dev/null || true
        sleep 2
        sudo pkill -9 -f "main_live.py" 2>/dev/null || true
    fi
    echo "服务已停止"
ENDSSH

print_success "服务已停止"

# ============================================================================
# Step 2: 备份并清空服务器代码目录
# ============================================================================
print_step "Step 2/5: 备份并清空服务器代码"

ssh ${SERVER_USER}@${SERVER_IP} << ENDSSH
    cd /home/linuxuser

    # 备份 .env 文件 (重要!)
    if [ -f "${INSTALL_DIR}/.env" ]; then
        echo "备份 .env 文件..."
        cp "${INSTALL_DIR}/.env" /home/linuxuser/.env.backup
        echo "已备份 .env 到 /home/linuxuser/.env.backup"
    fi

    # 备份 venv (可选，节省重新安装时间)
    if [ -d "${INSTALL_DIR}/venv" ]; then
        echo "保留 venv 目录..."
        mv "${INSTALL_DIR}/venv" /home/linuxuser/venv_temp 2>/dev/null || true
    fi

    # 清空代码目录
    echo "清空代码目录: ${INSTALL_DIR}"
    rm -rf ${INSTALL_DIR}
    mkdir -p ${INSTALL_DIR}

    echo "代码目录已清空"
ENDSSH

print_success "服务器代码已清空"

# ============================================================================
# Step 3: 拉取最新代码
# ============================================================================
print_step "Step 3/5: 拉取最新代码到服务器"

ssh ${SERVER_USER}@${SERVER_IP} << ENDSSH
    cd /home/linuxuser

    echo "克隆仓库..."
    git clone --branch ${BRANCH} --single-branch ${REPO_URL} ${INSTALL_DIR}

    cd ${INSTALL_DIR}
    echo ""
    echo "最新提交:"
    git log --oneline -5

    # 恢复 .env 文件
    if [ -f /home/linuxuser/.env.backup ]; then
        echo ""
        echo "恢复 .env 文件..."
        cp /home/linuxuser/.env.backup ${INSTALL_DIR}/.env
        echo "已恢复 .env"
    fi

    # 恢复 venv
    if [ -d /home/linuxuser/venv_temp ]; then
        echo "恢复 venv 目录..."
        mv /home/linuxuser/venv_temp ${INSTALL_DIR}/venv
        echo "已恢复 venv"
    fi
ENDSSH

print_success "代码拉取完成"

# ============================================================================
# Step 4: 运行安装脚本
# ============================================================================
print_step "Step 4/5: 运行安装脚本"

ssh ${SERVER_USER}@${SERVER_IP} << ENDSSH
    cd ${INSTALL_DIR}

    # 设置自动确认
    export AUTO_CONFIRM=true

    # 运行 setup.sh
    chmod +x setup.sh
    ./setup.sh

    # 更新 systemd 服务
    echo ""
    echo "更新 systemd 服务..."
    sudo cp nautilus-trader.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable nautilus-trader
ENDSSH

print_success "安装脚本执行完成"

# ============================================================================
# Step 5: 运行诊断脚本
# ============================================================================
print_step "Step 5/5: 运行诊断脚本"

ssh ${SERVER_USER}@${SERVER_IP} << ENDSSH
    cd ${INSTALL_DIR}

    # 激活虚拟环境并运行诊断
    source venv/bin/activate

    echo "运行诊断..."
    python3 diagnose.py --quick
ENDSSH

print_success "诊断完成"

# ============================================================================
# 完成
# ============================================================================
echo -e "\n${GREEN}"
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║                    部署完成!                                   ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

echo "后续操作:"
echo ""
echo "  启动服务:"
echo "    ssh ${SERVER_USER}@${SERVER_IP} 'sudo systemctl start ${SERVICE_NAME}'"
echo ""
echo "  查看日志:"
echo "    ssh ${SERVER_USER}@${SERVER_IP} 'sudo journalctl -u ${SERVICE_NAME} -f --no-hostname'"
echo ""
echo "  运行完整诊断:"
echo "    ssh ${SERVER_USER}@${SERVER_IP} 'cd ${INSTALL_DIR} && source venv/bin/activate && python3 diagnose.py'"
echo ""

# 询问是否启动服务
if [ "$AUTO_CONFIRM" != "true" ]; then
    read -p "是否立即启动服务? (y/N): " start_service
    if [ "$start_service" = "y" ] || [ "$start_service" = "Y" ]; then
        echo ""
        echo "启动服务..."
        ssh ${SERVER_USER}@${SERVER_IP} "sudo systemctl start ${SERVICE_NAME}"
        sleep 3
        echo ""
        echo "服务状态:"
        ssh ${SERVER_USER}@${SERVER_IP} "sudo systemctl status ${SERVICE_NAME} --no-pager | head -15"
    fi
fi
