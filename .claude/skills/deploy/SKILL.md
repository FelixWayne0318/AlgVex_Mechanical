---
name: deploy
description: |
  Deploy and manage the AlgVex trading bot on the server. 部署和管理 AlgVex 交易机器人。

  Use this skill when:
  - Deploying or updating code to the server (部署或更新代码)
  - Restarting the nautilus-trader service (重启服务)
  - Checking deployment status (检查部署状态)
  - Performing a complete reinstall (完全重装)
  - Managing systemd service configuration (管理 systemd 服务)
  - Troubleshooting deployment issues (排查部署问题)

  Keywords: deploy, restart, update, reinstall, systemd, service, server, 部署, 重启, 更新
disable-model-invocation: true
---

# Deploy Trading Bot

## Key Information

| Item | Value |
|------|-------|
| **Entry File** | `main_live.py` (NOT main.py!) |
| **Server** | 139.180.157.152 |
| **User** | linuxuser |
| **Path** | /home/linuxuser/nautilus_AlgVex |
| **Service** | nautilus-trader |
| **Branch** | main |
| **Config** | ~/.env.algvex (permanent storage) |

## Configuration Management

| Location | Description |
|----------|-------------|
| `~/.env.algvex` | Permanent storage, survives reinstall |
| `.env` | Symlink to ~/.env.algvex |

```bash
# Edit configuration
nano ~/.env.algvex

# Check symlink
ls -la /home/linuxuser/nautilus_AlgVex/.env
```

## Deployment Commands

### Complete Reinstall
```bash
curl -fsSL https://raw.githubusercontent.com/FelixWayne0318/AlgVex/main/reinstall.sh | bash
```

### Update and Restart
```bash
cd /home/linuxuser/nautilus_AlgVex
git pull origin main
sudo systemctl restart nautilus-trader
```

### Check Status
```bash
sudo systemctl status nautilus-trader
sudo journalctl -u nautilus-trader -n 30 --no-hostname
```

## systemd Service Configuration

```ini
[Unit]
Description=Nautilus AlgVex Bot
After=network.target

[Service]
Type=simple
User=linuxuser
WorkingDirectory=/home/linuxuser/nautilus_AlgVex
Environment="PATH=/home/linuxuser/nautilus_AlgVex/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="AUTO_CONFIRM=true"
EnvironmentFile=-/home/linuxuser/nautilus_AlgVex/.env
ExecStart=/home/linuxuser/nautilus_AlgVex/venv/bin/python main_live.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

## Common Errors

| Error | Cause | Solution |
|-------|-------|----------|
| `can't open file 'main.py'` | Wrong entry file | Use `main_live.py` |
| `EOFError: EOF when reading a line` | Missing AUTO_CONFIRM | Add `Environment=AUTO_CONFIRM=true` |
| Service keeps restarting | Config error | Check ExecStart path |
| `.env` missing | Broken symlink | `ln -sf ~/.env.algvex .env` |
