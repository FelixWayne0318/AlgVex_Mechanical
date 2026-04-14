#!/bin/bash
# ============================================================================
# Frontend Deployment Script
#
# This script ensures proper Tailwind CSS build by:
# 1. Clearing the .next cache (CRITICAL - prevents stale CSS)
# 2. Running a fresh build
# 3. Restarting systemd service
#
# Usage: cd /home/linuxuser/nautilus_AlgVex && bash web/frontend/scripts/deploy.sh
#
# Reference: https://github.com/tailwindlabs/tailwindcss/discussions/8521
# ============================================================================

set -e  # Exit on error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$(dirname "$SCRIPT_DIR")"

echo "========================================"
echo "Frontend Deployment Script"
echo "========================================"

cd "$FRONTEND_DIR"

# Step 1: Clear build cache (CRITICAL for Tailwind CSS)
echo ""
echo "[1/4] Clearing .next cache..."
rm -rf .next
echo "      ✓ Cache cleared"

# Step 2: Clear node_modules/.cache (optional but recommended)
echo ""
echo "[2/4] Clearing node_modules cache..."
rm -rf node_modules/.cache
echo "      ✓ Node cache cleared"

# Step 3: Run fresh build
echo ""
echo "[3/4] Running production build..."
npm run build
echo "      ✓ Build complete"

# Step 4: Restart systemd service
echo ""
echo "[4/4] Restarting algvex-frontend service..."
sudo systemctl restart algvex-frontend
sleep 2
if systemctl is-active --quiet algvex-frontend; then
    echo "      ✓ algvex-frontend service restarted"
else
    echo "      ✗ algvex-frontend failed to start"
    echo "      Run: sudo journalctl -u algvex-frontend -n 20"
    exit 1
fi

echo ""
echo "========================================"
echo "Deployment complete!"
echo ""
echo "To verify:"
echo "  1. Open https://algvex.com on mobile"
echo "  2. Should show: Logo + Hamburger menu only"
echo "  3. Desktop should show: Logo + Nav + Metrics"
echo "========================================"
