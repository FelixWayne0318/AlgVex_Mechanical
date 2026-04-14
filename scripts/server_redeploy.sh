#!/bin/bash
# 服务器本地重部署脚本 - 直接在服务器上运行
# 用法: ./scripts/server_redeploy.sh
# 功能: 清空本地代码、拉取最新、重新安装、运行诊断

set -e

# ============================================================================
# 配置
# ============================================================================
INSTALL_DIR="/home/linuxuser/nautilus_AlgVex"
BRANCH="main"
SERVICE_NAME="nautilus-trader"
REPO_URL="https://github.com/FelixWayne0318/AlgVex.git"
BACKUP_DIR="/home/linuxuser/backup_$(date +%Y%m%d_%H%M%S)"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_step() {
    echo -e "\n${BLUE}======================================${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}======================================${NC}\n"
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
# 开始
# ============================================================================
echo -e "${GREEN}"
echo "╔════════════════════════════════════════╗"
echo "║   AlgVex 服务器重部署脚本             ║"
echo "║   清空 → 拉取 → 安装 → 诊断             ║"
echo "╚════════════════════════════════════════╝"
echo -e "${NC}"

echo "路径:   $INSTALL_DIR"
echo "分支:   $BRANCH"
echo "备份:   $BACKUP_DIR"
echo ""

# 确认
if [ "$AUTO_CONFIRM" != "true" ]; then
    read -p "确认清空并重新部署? (y/N): " confirm
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        echo "取消"
        exit 0
    fi
fi

# ============================================================================
# Step 1: 停止服务
# ============================================================================
print_step "Step 1/5: 停止服务"

sudo systemctl stop $SERVICE_NAME 2>/dev/null || true

# 清理残留进程
if pgrep -f "main_live.py" > /dev/null 2>&1; then
    echo "清理残留进程..."
    sudo pkill -f "main_live.py" 2>/dev/null || true
    sleep 2
    sudo pkill -9 -f "main_live.py" 2>/dev/null || true
fi

print_success "服务已停止"

# ============================================================================
# Step 2: 备份重要文件
# ============================================================================
print_step "Step 2/5: 备份重要文件"

mkdir -p $BACKUP_DIR

# 备份 .env
if [ -f "$INSTALL_DIR/.env" ]; then
    cp "$INSTALL_DIR/.env" "$BACKUP_DIR/.env"
    print_success "已备份 .env"
fi

# 备份配置文件
if [ -d "$INSTALL_DIR/configs" ]; then
    cp -r "$INSTALL_DIR/configs" "$BACKUP_DIR/configs"
    print_success "已备份 configs/"
fi

# 备份日志 (可选)
if [ -d "$INSTALL_DIR/logs" ]; then
    cp -r "$INSTALL_DIR/logs" "$BACKUP_DIR/logs" 2>/dev/null || true
    print_success "已备份 logs/"
fi

# 保留 venv (节省时间)
if [ -d "$INSTALL_DIR/venv" ]; then
    echo "移动 venv 到临时位置..."
    mv "$INSTALL_DIR/venv" /tmp/nautilus_venv_backup
    print_success "已保留 venv"
fi

echo ""
echo "备份目录: $BACKUP_DIR"

# ============================================================================
# Step 3: 清空并拉取最新代码
# ============================================================================
print_step "Step 3/5: 清空并拉取最新代码"

# 清空目录
echo "清空 $INSTALL_DIR ..."
rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"

# 克隆仓库
echo "克隆仓库 (分支: $BRANCH)..."
git clone --branch "$BRANCH" --single-branch "$REPO_URL" "$INSTALL_DIR"

cd "$INSTALL_DIR"

echo ""
echo "最新提交:"
git log --oneline -5

print_success "代码拉取完成"

# ============================================================================
# Step 4: 恢复文件并安装
# ============================================================================
print_step "Step 4/5: 恢复文件并安装依赖"

# 恢复 .env
if [ -f "$BACKUP_DIR/.env" ]; then
    cp "$BACKUP_DIR/.env" "$INSTALL_DIR/.env"
    print_success "已恢复 .env"
fi

# 恢复 configs (如果本地有自定义)
# 注意: 一般使用仓库中的配置，除非有本地覆盖
# if [ -d "$BACKUP_DIR/configs" ]; then
#     cp -r "$BACKUP_DIR/configs"/* "$INSTALL_DIR/configs/"
#     print_success "已恢复 configs"
# fi

# 恢复 venv
if [ -d /tmp/nautilus_venv_backup ]; then
    echo "恢复 venv..."
    mv /tmp/nautilus_venv_backup "$INSTALL_DIR/venv"
    print_success "已恢复 venv"
fi

# 创建日志目录
mkdir -p logs

# 运行 setup.sh
echo ""
echo "运行安装脚本..."
export AUTO_CONFIRM=true
chmod +x setup.sh
./setup.sh

# 更新 systemd 服务
echo ""
echo "更新 systemd 服务..."
sudo cp nautilus-trader.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable $SERVICE_NAME

print_success "安装完成"

# ============================================================================
# Step 5: 运行诊断
# ============================================================================
print_step "Step 5/5: 运行诊断"

source venv/bin/activate
python3 diagnose.py --quick

# ============================================================================
# 完成
# ============================================================================
echo -e "\n${GREEN}"
echo "╔════════════════════════════════════════╗"
echo "║            部署完成!                   ║"
echo "╚════════════════════════════════════════╝"
echo -e "${NC}"

echo "备份位置: $BACKUP_DIR"
echo ""
echo "后续操作:"
echo "  启动服务: sudo systemctl start $SERVICE_NAME"
echo "  查看日志: sudo journalctl -u $SERVICE_NAME -f --no-hostname"
echo "  完整诊断: python3 diagnose.py"
echo ""

# 询问是否启动
if [ "$AUTO_CONFIRM" != "true" ]; then
    read -p "是否立即启动服务? (y/N): " start
    if [ "$start" = "y" ] || [ "$start" = "Y" ]; then
        sudo systemctl start $SERVICE_NAME
        sleep 3
        echo ""
        sudo systemctl status $SERVICE_NAME --no-pager | head -15
    fi
fi
