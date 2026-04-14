#!/bin/bash
# Web Backend Dependency Fix Script (v4.0)
# Fixes recurring pandas-datareader incompatibility with Python 3.12
# Service management: systemd (algvex-backend.service)

set -e

echo "========================================"
echo "Web Backend Dependency Fix (v4.0)"
echo "========================================"
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Change to backend directory
BACKEND_DIR="/home/linuxuser/nautilus_AlgVex/web/backend"
cd "$BACKEND_DIR" || exit 1

echo -e "${YELLOW}1. Stopping backend service...${NC}"
sudo systemctl stop algvex-backend || echo "  (service not running)"
echo ""

echo -e "${YELLOW}2. Activating virtual environment...${NC}"
if [ ! -f "venv/bin/activate" ]; then
    echo -e "${RED}ERROR: venv not found. Creating venv...${NC}"
    python3 -m venv venv
fi
source venv/bin/activate
echo "  ✓ venv activated"
echo ""

echo -e "${YELLOW}3. Removing problematic packages...${NC}"
echo "  - Uninstalling empyrical (unmaintained)"
echo "  - Uninstalling pandas-datareader (Python 3.12 incompatible)"
pip uninstall -y empyrical pandas-datareader 2>/dev/null || echo "  (packages not found)"
echo "  ✓ Old packages removed"
echo ""

echo -e "${YELLOW}4. Installing dependencies...${NC}"
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
echo "  ✓ Dependencies installed"
echo ""

echo -e "${YELLOW}5. Verification...${NC}"
PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')")
echo "  Python version: $PYTHON_VERSION"

# Test import of performance_service
echo "  Testing performance_service import..."
python3 -c "
import sys
sys.path.insert(0, '.')
from services.performance_service import PerformanceService
print('  ✓ performance_service import successful')
" || {
    echo -e "${RED}  ✗ performance_service import failed!${NC}"
    exit 1
}
echo ""

echo -e "${YELLOW}6. Restarting backend service...${NC}"
sudo systemctl restart algvex-backend
sleep 2
if systemctl is-active --quiet algvex-backend; then
    echo -e "  ${GREEN}✓ Service restarted successfully${NC}"
else
    echo -e "  ${RED}✗ Service failed to start${NC}"
    sudo journalctl -u algvex-backend -n 20 --no-pager
    exit 1
fi
echo ""

echo -e "${YELLOW}7. Health check...${NC}"
sleep 2
HEALTH=$(curl -s http://localhost:8000/api/health 2>/dev/null || echo "FAILED")
if echo "$HEALTH" | grep -q "healthy"; then
    echo -e "  ${GREEN}✓ Health check passed${NC}"
else
    echo -e "  ${YELLOW}⚠ Health check: $HEALTH${NC}"
fi
echo ""

echo -e "${GREEN}========================================"
echo "✅ Dependency fix completed!"
echo "========================================${NC}"
echo ""
echo "Useful commands:"
echo "  sudo systemctl status algvex-backend    - Check status"
echo "  sudo journalctl -u algvex-backend -f    - View logs"
echo "  curl http://localhost:8000/api/health    - Health check"
echo ""
