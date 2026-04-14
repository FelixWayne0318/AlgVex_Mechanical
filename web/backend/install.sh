#!/bin/bash
# AlgVex Web Backend - One-Click Installation Script
# Usage: chmod +x install.sh && ./install.sh

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  AlgVex Web Backend Installation${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Detect current user and paths
CURRENT_USER=$(whoami)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
BACKEND_DIR="$SCRIPT_DIR"

echo -e "${YELLOW}Detected paths:${NC}"
echo "  User: $CURRENT_USER"
echo "  Backend dir: $BACKEND_DIR"
echo "  Project root: $PROJECT_ROOT"
echo ""

# Check if running as root for systemd operations
if [ "$EUID" -ne 0 ]; then
    echo -e "${YELLOW}Note: Will use sudo for systemd operations${NC}"
    SUDO="sudo"
else
    SUDO=""
fi

# Step 1: Check Python version
echo -e "${YELLOW}[1/6] Checking Python version...${NC}"
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
    echo -e "  Python version: ${GREEN}$PYTHON_VERSION${NC}"
else
    echo -e "${RED}Error: Python 3 is not installed${NC}"
    exit 1
fi

# Step 2: Create virtual environment if not exists
echo -e "${YELLOW}[2/6] Setting up virtual environment...${NC}"
VENV_DIR="$BACKEND_DIR/venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "  Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
    echo -e "  ${GREEN}Virtual environment created${NC}"
else
    echo -e "  ${GREEN}Virtual environment already exists${NC}"
fi

# Activate virtual environment
source "$VENV_DIR/bin/activate"

# Step 3: Install dependencies
echo -e "${YELLOW}[3/6] Installing Python dependencies...${NC}"
pip install --upgrade pip -q
pip install -r "$BACKEND_DIR/requirements.txt" -q
echo -e "  ${GREEN}Dependencies installed${NC}"

# Step 4: Create uploads directory
echo -e "${YELLOW}[4/6] Creating uploads directory...${NC}"
mkdir -p "$BACKEND_DIR/uploads"
echo -e "  ${GREEN}Uploads directory ready${NC}"

# Step 5: Create systemd service file
echo -e "${YELLOW}[5/6] Creating systemd service...${NC}"

SERVICE_FILE="/etc/systemd/system/algvex-backend.service"
SERVICE_CONTENT="[Unit]
Description=AlgVex Web Backend API
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
Group=$CURRENT_USER
WorkingDirectory=$BACKEND_DIR
Environment=PATH=$VENV_DIR/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$VENV_DIR/bin/python3 main.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

# Security hardening
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
"

# Write service file
echo "$SERVICE_CONTENT" | $SUDO tee "$SERVICE_FILE" > /dev/null
echo -e "  ${GREEN}Service file created: $SERVICE_FILE${NC}"

# Reload systemd
$SUDO systemctl daemon-reload

# Step 6: Enable and start service
echo -e "${YELLOW}[6/6] Starting service...${NC}"

# Stop existing service if running
if systemctl is-active --quiet algvex-backend; then
    echo "  Stopping existing service..."
    $SUDO systemctl stop algvex-backend
fi

# Enable and start
$SUDO systemctl enable algvex-backend
$SUDO systemctl start algvex-backend

# Wait a moment for service to start
sleep 2

# Check status
if systemctl is-active --quiet algvex-backend; then
    echo -e "  ${GREEN}Service started successfully!${NC}"
else
    echo -e "  ${RED}Service failed to start. Checking logs...${NC}"
    $SUDO journalctl -u algvex-backend -n 20 --no-pager
    exit 1
fi

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Installation Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "Service name: ${YELLOW}algvex-backend${NC}"
echo -e "Backend URL:  ${YELLOW}http://localhost:8000${NC}"
echo -e "API Docs:     ${YELLOW}http://localhost:8000/api/docs${NC}"
echo ""
echo -e "Useful commands:"
echo -e "  ${YELLOW}sudo systemctl status algvex-backend${NC}  - Check status"
echo -e "  ${YELLOW}sudo systemctl restart algvex-backend${NC} - Restart service"
echo -e "  ${YELLOW}sudo systemctl stop algvex-backend${NC}    - Stop service"
echo -e "  ${YELLOW}sudo journalctl -u algvex-backend -f${NC}  - View logs"
echo ""

# Show current status
echo -e "${YELLOW}Current service status:${NC}"
$SUDO systemctl status algvex-backend --no-pager -l
