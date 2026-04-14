#!/bin/bash
# =============================================================================
# AlgVex Web 一键重新部署脚本
#
# 解决的问题: 每次修改网站后 CSS/样式不加载
# 根因: 1) 服务运行期间重建导致状态不一致
#        2) Caddy 未正确配置缓存策略
#        3) 重启顺序不对
#
# 正确顺序: 停服务 → 拉代码 → 重建 → 更新配置 → 启服务 → 验证
#
# 用法:
#   cd /home/linuxuser/nautilus_AlgVex && bash web/deploy/redeploy.sh
#   cd /home/linuxuser/nautilus_AlgVex && bash web/deploy/redeploy.sh --branch main
#   cd /home/linuxuser/nautilus_AlgVex && bash web/deploy/redeploy.sh --skip-pull
# =============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

REPO_DIR="/home/linuxuser/nautilus_AlgVex"
BRANCH="main"
SKIP_PULL=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --branch) BRANCH="$2"; shift 2 ;;
        --skip-pull) SKIP_PULL=true; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

cd "$REPO_DIR" || { echo -e "${RED}目录不存在: $REPO_DIR${NC}"; exit 1; }

echo ""
echo -e "${BLUE}╔═════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║      AlgVex Web 一键重新部署 v2.0       ║${NC}"
echo -e "${BLUE}╚═════════════════════════════════════════╝${NC}"
echo ""

# ==================== Step 1: 停止所有 web 服务 ====================
echo -e "${YELLOW}[1/7] 停止所有 web 服务...${NC}"
sudo systemctl stop algvex-frontend 2>/dev/null || true
sudo systemctl stop algvex-backend 2>/dev/null || true
sudo systemctl stop caddy 2>/dev/null || true
# 同时清理残留 PM2 进程 (如果有)
pm2 delete all 2>/dev/null || true
echo -e "${GREEN}  ✓ 服务已停止${NC}"

# ==================== Step 2: 拉取最新代码 ====================
if [ "$SKIP_PULL" = false ]; then
    echo -e "${YELLOW}[2/7] 拉取最新代码 (branch: $BRANCH)...${NC}"
    git fetch origin "$BRANCH"
    git checkout "$BRANCH" 2>/dev/null || true
    git pull origin "$BRANCH"
    echo -e "${GREEN}  ✓ 代码已更新${NC}"
    git log --oneline -1
else
    echo -e "${YELLOW}[2/7] 跳过代码拉取 (--skip-pull)${NC}"
fi

# ==================== Step 3: 重建前端 ====================
echo -e "${YELLOW}[3/7] 重建前端...${NC}"
cd web/frontend

# 彻底清除所有缓存
rm -rf .next node_modules/.cache
echo -e "  ✓ 缓存已清除"

# 安装依赖 (如有变化)
npm install --prefer-offline 2>&1 | tail -3
echo -e "  ✓ 依赖已安装"

# 构建
npm run build 2>&1 | tail -15
echo -e "${GREEN}  ✓ 前端构建完成${NC}"

# 验证构建产物
CSS_COUNT=$(ls .next/static/css/*.css 2>/dev/null | wc -l)
if [ "$CSS_COUNT" -eq 0 ]; then
    echo -e "${RED}  ✗ 错误: 构建后没有 CSS 文件!${NC}"
    exit 1
fi
echo -e "${GREEN}  ✓ CSS 文件存在 ($CSS_COUNT 个)${NC}"

cd "$REPO_DIR"

# ==================== Step 4: 检查后端 ====================
echo -e "${YELLOW}[4/7] 检查后端...${NC}"
if [ ! -d "web/backend/venv" ]; then
    echo -e "  创建后端虚拟环境..."
    cd web/backend
    python3 -m venv venv
    source venv/bin/activate
    pip install --upgrade pip -q
    pip install -r requirements.txt -q
    deactivate
    cd "$REPO_DIR"
    echo -e "${GREEN}  ✓ 后端虚拟环境已创建${NC}"
else
    echo -e "${GREEN}  ✓ 后端虚拟环境已存在${NC}"
fi

# ==================== Step 5: 更新 systemd + Caddy 配置 ====================
echo -e "${YELLOW}[5/7] 更新服务配置...${NC}"
sudo cp web/deploy/algvex-backend.service /etc/systemd/system/
sudo cp web/deploy/algvex-frontend.service /etc/systemd/system/
sudo cp web/deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl daemon-reload
echo -e "${GREEN}  ✓ 配置已更新${NC}"

# ==================== Step 6: 按顺序启动服务 ====================
echo -e "${YELLOW}[6/7] 启动服务 (后端 → 前端 → Caddy)...${NC}"

# 后端先启动 (前端依赖后端 API)
sudo systemctl start algvex-backend
sleep 2
if systemctl is-active --quiet algvex-backend; then
    echo -e "${GREEN}  ✓ 后端已启动${NC}"
else
    echo -e "${RED}  ✗ 后端启动失败${NC}"
    sudo journalctl -u algvex-backend -n 10 --no-pager
    exit 1
fi

# 前端启动
sudo systemctl start algvex-frontend
sleep 2
if systemctl is-active --quiet algvex-frontend; then
    echo -e "${GREEN}  ✓ 前端已启动${NC}"
else
    echo -e "${RED}  ✗ 前端启动失败${NC}"
    sudo journalctl -u algvex-frontend -n 10 --no-pager
    exit 1
fi

# Caddy 最后启动
sudo systemctl start caddy
sleep 1
if systemctl is-active --quiet caddy; then
    echo -e "${GREEN}  ✓ Caddy 已启动${NC}"
else
    echo -e "${RED}  ✗ Caddy 启动失败${NC}"
    sudo journalctl -u caddy -n 10 --no-pager
    exit 1
fi

# ==================== Step 7: 验证 ====================
echo -e "${YELLOW}[7/7] 验证...${NC}"

# 检查后端健康
HEALTH=$(curl -s http://localhost:8000/api/health 2>/dev/null || echo "FAILED")
if echo "$HEALTH" | grep -q "healthy"; then
    echo -e "${GREEN}  ✓ 后端 API 健康${NC}"
else
    echo -e "${YELLOW}  ⚠ 后端健康检查: $HEALTH${NC}"
fi

# 检查前端 HTTP
FE_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:3000 2>/dev/null || echo "000")
if [ "$FE_CODE" = "200" ]; then
    echo -e "${GREEN}  ✓ 前端 HTTP 200${NC}"
else
    echo -e "${YELLOW}  ⚠ 前端 HTTP $FE_CODE${NC}"
fi

# 检查 CSS 是否可访问
CSS_FILE=$(ls web/frontend/.next/static/css/ | head -1)
CSS_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/_next/static/css/$CSS_FILE 2>/dev/null || echo "000")
if [ "$CSS_CODE" = "200" ]; then
    echo -e "${GREEN}  ✓ CSS 可访问 ($CSS_FILE)${NC}"
else
    echo -e "${RED}  ✗ CSS 不可访问 (HTTP $CSS_CODE)${NC}"
fi

# 检查 HTML 是否包含 CSS 链接
HTML_HAS_CSS=$(curl -s http://localhost:3000 2>/dev/null | grep -c "\.css" || echo "0")
if [ "$HTML_HAS_CSS" -gt 0 ]; then
    echo -e "${GREEN}  ✓ HTML 包含 CSS 引用 ($HTML_HAS_CSS 处)${NC}"
else
    echo -e "${RED}  ✗ HTML 不包含 CSS 引用 — 这是页面无样式的根因!${NC}"
    echo -e "${RED}    请检查前端日志: sudo journalctl -u algvex-frontend -n 30${NC}"
fi

# 检查 Caddy 代理
CADDY_CODE=$(curl -s -o /dev/null -w "%{http_code}" https://algvex.com 2>/dev/null || echo "000")
echo -e "  Caddy 代理: HTTP $CADDY_CODE"

echo ""
echo -e "${GREEN}╔═════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║            部署完成!                     ║${NC}"
echo -e "${GREEN}╚═════════════════════════════════════════╝${NC}"
echo ""
echo -e "前端: https://algvex.com"
echo -e "后端: https://algvex.com/api/health"
echo ""
echo -e "如果页面仍无样式，查看日志:"
echo -e "  sudo journalctl -u algvex-frontend -n 30"
echo -e "  sudo journalctl -u caddy -n 10"
echo ""
