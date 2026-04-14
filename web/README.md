# AlgVex - AlgVex Web Interface

AI-Powered Cryptocurrency Trading System Web Dashboard

## Overview

AlgVex is a web interface for the AlgVex trading system, featuring:

- **Public Dashboard**: Performance metrics, P&L curves, system status
- **Admin Panel**: Strategy configuration, social links, service control
- **Copy Trading**: Links to follow trades on various exchanges

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Caddy                                │
│                    (Reverse Proxy + HTTPS)                  │
│                     algvex.com:443                          │
└─────────────────┬───────────────────────┬───────────────────┘
                  │                       │
                  ▼                       ▼
      ┌───────────────────┐   ┌───────────────────┐
      │    Frontend       │   │     Backend       │
      │    (Next.js)      │   │    (FastAPI)      │
      │   localhost:3000  │   │  localhost:8000   │
      └───────────────────┘   └───────────────────┘
                                      │
                              ┌───────┴───────┐
                              ▼               ▼
                    ┌─────────────┐   ┌─────────────┐
                    │   SQLite    │   │  AlgVex   │
                    │  (Config)   │   │  (Trading)  │
                    └─────────────┘   └─────────────┘
```

## Tech Stack

| Layer | Technology |
|-------|------------|
| Frontend | Next.js 14, TypeScript, Tailwind CSS, shadcn/ui |
| Charts | TradingView Lightweight Charts |
| Backend | FastAPI, Python 3.12, SQLAlchemy |
| Database | SQLite (async) |
| Auth | Google OAuth (via AuthLib) |
| Proxy | Caddy (auto HTTPS) |

## Directory Structure

```
web/
├── backend/                 # FastAPI Backend
│   ├── main.py             # Application entry point
│   ├── requirements.txt    # Python dependencies
│   ├── api/
│   │   ├── routes/
│   │   │   ├── public.py   # Public API (performance, links)
│   │   │   ├── admin.py    # Admin API (config, service control)
│   │   │   └── auth.py     # Authentication (Google OAuth)
│   │   └── deps.py         # Dependencies (auth helpers)
│   ├── core/
│   │   ├── config.py       # Settings
│   │   └── database.py     # SQLite connection
│   ├── models/
│   │   └── settings.py     # Database models
│   └── services/
│       ├── binance_service.py  # Binance API integration
│       └── config_service.py   # AlgVex config management
│
├── frontend/               # Next.js Frontend
│   ├── package.json
│   ├── pages/
│   │   ├── index.tsx       # Homepage
│   │   ├── performance.tsx # Performance page
│   │   ├── copy.tsx        # Copy trading page
│   │   ├── about.tsx       # About page
│   │   └── admin/
│   │       └── index.tsx   # Admin panel
│   ├── components/
│   │   ├── ui/             # shadcn/ui components
│   │   ├── layout/         # Header, Footer
│   │   └── charts/         # TradingView charts
│   └── lib/
│       ├── api.ts          # API client
│       ├── i18n.ts         # Translations (EN/ZH)
│       └── utils.ts        # Utilities
│
└── deploy/                 # Deployment Configuration
    ├── Caddyfile           # Caddy reverse proxy config
    ├── algvex-backend.service   # Backend systemd service
    ├── algvex-frontend.service  # Frontend systemd service
    └── setup.sh            # One-click deployment script
```

## Quick Deployment

### On Server (139.180.157.152)

```bash
# SSH into server
ssh linuxuser@139.180.157.152

# Clone repository
git clone https://github.com/FelixWayne0318/AlgVex.git
cd AlgVex/web/deploy

# Run deployment script
chmod +x setup.sh
./setup.sh
```

### Manual Deployment

1. **Install Dependencies**
   ```bash
   sudo apt update
   sudo apt install python3.12 python3.12-venv nodejs npm caddy
   ```

2. **Setup Backend**
   ```bash
   cd /home/linuxuser/algvex/backend
   python3.12 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env
   # Edit .env with your configuration
   ```

3. **Setup Frontend**
   ```bash
   cd /home/linuxuser/algvex/frontend
   npm install
   npm run build
   ```

4. **Configure Caddy**
   ```bash
   sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
   sudo systemctl restart caddy
   ```

5. **Start Services**
   ```bash
   sudo systemctl enable --now algvex-backend algvex-frontend
   ```

## Configuration

### Backend Environment (.env)

```bash
# Required
SECRET_KEY=your-secure-random-key
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
ADMIN_EMAILS=your-email@gmail.com

# AlgVex Integration
ALGVEX_PATH=/home/linuxuser/nautilus_AlgVex
ALGVEX_CONFIG_PATH=/home/linuxuser/nautilus_AlgVex/configs/base.yaml
ALGVEX_SERVICE_NAME=nautilus-trader
```

### Google OAuth Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/apis/credentials)
2. Create OAuth 2.0 Client ID
3. Add authorized redirect URI: `https://algvex.com/api/auth/callback/google`
4. Copy Client ID and Secret to `.env`

### DNS Configuration

Point your domain to the server:
```
A     algvex.com      139.180.157.152
A     www.algvex.com  139.180.157.152
```

## API Endpoints

### Public API (No Auth)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/public/performance` | Trading performance stats |
| GET | `/api/public/performance/summary` | Quick summary for homepage |
| GET | `/api/public/social-links` | Social media links |
| GET | `/api/public/copy-trading` | Copy trading links |
| GET | `/api/public/system-status` | Trading system status |

### Admin API (Auth Required)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/admin/config` | Get strategy config |
| PUT | `/api/admin/config` | Update config value |
| GET | `/api/admin/service/status` | Service status |
| POST | `/api/admin/service/control` | Restart/Stop/Start service |
| GET/PUT | `/api/admin/social-links/{platform}` | Manage social links |
| GET/POST/PUT/DELETE | `/api/admin/copy-trading` | Manage copy trading links |

### Auth API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/auth/login` | Initiate Google OAuth |
| GET | `/api/auth/callback/google` | OAuth callback |
| GET | `/api/auth/me` | Current user info |
| POST | `/api/auth/logout` | Logout |

## Useful Commands

```bash
# View logs
sudo journalctl -u algvex-backend -f
sudo journalctl -u algvex-frontend -f
sudo journalctl -u caddy -f

# Restart services
sudo systemctl restart algvex-backend algvex-frontend caddy

# Check status
sudo systemctl status algvex-backend algvex-frontend caddy
```

## Features

### Public Pages

- **Homepage**: Hero section, key stats, P&L chart, features
- **Performance**: Detailed stats with time period selector
- **Copy Trading**: Exchange links with step-by-step guide
- **About**: Strategy explanation, technology stack

### Admin Panel

- **Dashboard**: Service status, restart control
- **Strategy**: Leverage, position size, risk settings
- **Links**: Social media and copy trading URL management

### i18n Support

- English (en)
- Chinese (zh)
- Auto-detection based on browser

## Security

- HTTPS with auto-renewed Let's Encrypt certificates
- Google OAuth for admin authentication
- Admin email whitelist
- Secure headers (HSTS, XSS protection, etc.)
- No sensitive data exposed in public API

## License

MIT
