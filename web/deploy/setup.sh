#!/bin/bash

# =============================================================================
# Algvex Web Deployment Script
# Domain: algvex.com
# Server: 139.180.157.152
# =============================================================================

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}"
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║                    Algvex Web Deployment                      ║"
echo "║                      algvex.com                               ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Check if running as root or with sudo
if [[ $EUID -eq 0 ]]; then
    echo -e "${YELLOW}Warning: Running as root. Will create user directories.${NC}"
fi

# Variables
# Web files live inside the main repo — no separate directory needed.
# This avoids path mismatches between the service files and the actual code.
REPO_DIR="/home/linuxuser/nautilus_AlgVex"
INSTALL_DIR="$REPO_DIR/web"
REPO_URL="https://github.com/FelixWayne0318/AlgVex.git"
BRANCH="${ALGVEX_BRANCH:-main}"  # Use main by default, can override with ALGVEX_BRANCH env var

# =============================================================================
# Step 1: Install System Dependencies
# =============================================================================
echo -e "\n${GREEN}[1/8] Installing system dependencies...${NC}"

sudo apt-get update
sudo apt-get install -y \
    curl \
    git \
    python3.12 \
    python3.12-venv \
    python3-pip \
    nodejs \
    npm

# Install Caddy
if ! command -v caddy &> /dev/null; then
    echo -e "${YELLOW}Installing Caddy...${NC}"
    sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
    sudo apt-get update
    sudo apt-get install -y caddy
fi

# Update Node.js to LTS if needed
NODE_VERSION=$(node -v 2>/dev/null | cut -d'v' -f2 | cut -d'.' -f1)
if [[ -z "$NODE_VERSION" || "$NODE_VERSION" -lt 18 ]]; then
    echo -e "${YELLOW}Updating Node.js to v20 LTS...${NC}"
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y nodejs
fi

echo -e "${GREEN}✓ System dependencies installed${NC}"

# =============================================================================
# Step 2: Create Directory Structure
# =============================================================================
echo -e "\n${GREEN}[2/8] Creating directory structure...${NC}"

sudo mkdir -p /var/log/caddy
sudo chown caddy:caddy /var/log/caddy

echo -e "${GREEN}✓ Directory structure created${NC}"

# =============================================================================
# Step 3: Clone/Update Repository
# =============================================================================
echo -e "\n${GREEN}[3/8] Updating repository...${NC}"

if [[ -d "$REPO_DIR/.git" ]]; then
    echo "Repository exists, pulling latest..."
    cd "$REPO_DIR"
    git fetch origin "$BRANCH"
    git reset --hard "origin/$BRANCH"
else
    echo "Cloning repository..."
    git clone --branch "$BRANCH" "$REPO_URL" "$REPO_DIR"
fi

# Copy deploy configs to system locations
sudo cp "$INSTALL_DIR/deploy/Caddyfile" /etc/caddy/Caddyfile
sudo cp "$INSTALL_DIR/deploy/algvex-backend.service" /etc/systemd/system/
sudo cp "$INSTALL_DIR/deploy/algvex-frontend.service" /etc/systemd/system/

echo -e "${GREEN}✓ Repository updated${NC}"

# =============================================================================
# Step 4: Setup Backend
# =============================================================================
echo -e "\n${GREEN}[4/8] Setting up backend...${NC}"

cd "$INSTALL_DIR/backend" || { echo -e "${RED}Backend directory not found!${NC}"; exit 1; }

# Create virtual environment
python3.12 -m venv venv
source venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Create .env file if not exists
if [[ ! -f .env ]]; then
    echo -e "${YELLOW}Creating backend .env file...${NC}"
    cat > .env << 'EOF'
# Algvex Backend Configuration
DEBUG=false
SECRET_KEY=CHANGE_THIS_TO_A_SECURE_RANDOM_STRING

# Google OAuth - Get from https://console.cloud.google.com/apis/credentials
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
GOOGLE_REDIRECT_URI=https://algvex.com/api/auth/callback/google

# Admin email (your Google account email)
ADMIN_EMAILS=your-email@gmail.com

# AlgVex paths
ALGVEX_PATH=/home/linuxuser/nautilus_AlgVex
ALGVEX_SERVICE_NAME=nautilus-trader
EOF
    echo -e "${RED}⚠ Please edit $INSTALL_DIR/backend/.env with your credentials!${NC}"
fi

deactivate

echo -e "${GREEN}✓ Backend setup complete${NC}"

# =============================================================================
# Step 5: Setup Frontend
# =============================================================================
echo -e "\n${GREEN}[5/8] Setting up frontend...${NC}"

cd "$INSTALL_DIR/frontend" || { echo -e "${RED}Frontend directory not found!${NC}"; exit 1; }

# Clear stale build cache
rm -rf .next node_modules/.cache

# Install dependencies
npm install

# Build for production
npm run build

echo -e "${GREEN}✓ Frontend setup complete${NC}"

# =============================================================================
# Step 6: Install systemd Services
# =============================================================================
echo -e "\n${GREEN}[6/8] Installing systemd services...${NC}"

# Backend service (already copied in step 3)

# Reload systemd
sudo systemctl daemon-reload

# Enable services
sudo systemctl enable algvex-backend
sudo systemctl enable algvex-frontend
sudo systemctl enable caddy

echo -e "${GREEN}✓ Systemd services installed${NC}"

# =============================================================================
# Step 7: Configure Caddy
# =============================================================================
echo -e "\n${GREEN}[7/8] Configuring Caddy...${NC}"

# Validate Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile

echo -e "${GREEN}✓ Caddy configured${NC}"

# =============================================================================
# Step 8: Start Services
# =============================================================================
echo -e "\n${GREEN}[8/8] Starting services...${NC}"

sudo systemctl restart algvex-backend
sudo systemctl restart algvex-frontend
sudo systemctl restart caddy

# Wait a moment for services to start
sleep 3

# Check status
echo -e "\n${BLUE}Service Status:${NC}"
echo -n "Backend:  "
systemctl is-active algvex-backend
echo -n "Frontend: "
systemctl is-active algvex-frontend
echo -n "Caddy:    "
systemctl is-active caddy

# =============================================================================
# Done
# =============================================================================
echo -e "\n${GREEN}"
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║                    Deployment Complete!                       ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

echo -e "
${YELLOW}Next Steps:${NC}

1. ${BLUE}Configure Google OAuth:${NC}
   - Go to https://console.cloud.google.com/apis/credentials
   - Create OAuth 2.0 Client ID
   - Add authorized redirect URI: https://algvex.com/api/auth/callback/google
   - Update $INSTALL_DIR/backend/.env with credentials

2. ${BLUE}Set a secure SECRET_KEY:${NC}
   python3 -c \"import secrets; print(secrets.token_urlsafe(32))\"
   Update $INSTALL_DIR/backend/.env

3. ${BLUE}Update your admin email:${NC}
   Edit ADMIN_EMAILS in $INSTALL_DIR/backend/.env

4. ${BLUE}Restart backend after configuration:${NC}
   sudo systemctl restart algvex-backend

5. ${BLUE}Point DNS to this server:${NC}
   A record: algvex.com -> 139.180.157.152
   A record: www.algvex.com -> 139.180.157.152

${GREEN}Website will be available at: https://algvex.com${NC}
${GREEN}Admin panel: https://algvex.com/admin${NC}
"

echo -e "${BLUE}Useful commands:${NC}
  View backend logs:  sudo journalctl -u algvex-backend -f
  View frontend logs: sudo journalctl -u algvex-frontend -f
  View Caddy logs:    sudo journalctl -u caddy -f
  Restart all:        sudo systemctl restart algvex-backend algvex-frontend caddy
"
