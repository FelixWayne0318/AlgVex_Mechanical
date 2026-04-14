# Telegram Command Handler Troubleshooting Guide

## Problem
Commands like `/status`, `/help`, `/position` from Telegram don't get responses, but notifications (open/close signals) are working.

## Root Causes

### 1. Chat ID Mismatch (Most Common)
The `TELEGRAM_CHAT_ID` in `~/.env.algvex` doesn't match your actual Telegram chat ID.

**Solution:**
```bash
cd /home/linuxuser/nautilus_AlgVex && source venv/bin/activate && python3 scripts/diagnose.py --quick
```
This runs a quick diagnostic check covering service health, configuration, and connectivity.

### 2. Environment File Missing or Incorrect
The `~/.env.algvex` file might not exist or have wrong credentials.

**Solution:**
```bash
ls -la ~/.env.algvex
cat ~/.env.algvex | grep TELEGRAM
```
Required variables:
```bash
TELEGRAM_BOT_TOKEN=your_bot_token_from_botfather
TELEGRAM_CHAT_ID=your_actual_chat_id
```

### 3. Command Handler Not Starting
The background thread that listens for commands might not be starting.

**Solution:**
```bash
sudo journalctl -u nautilus-trader -f --no-hostname | grep -i "telegram\|command"
```
Look for:
- "Telegram Bot initialized successfully"
- "Telegram Command Handler started in background thread"

### 4. Multiple Bot Instances
If multiple instances are running, only one receives updates.

**Solution:**
```bash
sudo systemctl stop nautilus-trader
# Wait a few seconds
sudo systemctl start nautilus-trader
```

## Diagnostic Steps

### Step 1: Run Diagnostic Script
```bash
cd /home/linuxuser/nautilus_AlgVex && source venv/bin/activate && python3 scripts/diagnose.py --quick
```

### Step 2: Get Your Actual Chat ID
1. Send any message to your bot
2. Run:
```python
import asyncio
from telegram import Bot

async def get_chat_id():
    bot = Bot(token="YOUR_BOT_TOKEN_HERE")
    updates = await bot.get_updates()
    for update in updates:
        print(f"Chat ID: {update.message.chat.id}")

asyncio.run(get_chat_id())
```
3. Update `TELEGRAM_CHAT_ID` in `~/.env.algvex`

### Step 3: Verify Dual Channel Setup (v14.0)
If using the notification channel, ensure both bots are configured:
```bash
grep -E "TELEGRAM_(BOT_TOKEN|CHAT_ID|NOTIFICATION)" ~/.env.algvex
```

## Quick Fix Checklist

- [ ] `~/.env.algvex` exists with correct credentials
- [ ] `TELEGRAM_CHAT_ID` matches your actual chat ID (verified with `@userinfobot`)
- [ ] python-telegram-bot is installed (`pip list | grep telegram`)
- [ ] Only one bot instance running (`sudo systemctl status nautilus-trader`)
- [ ] Strategy logs show "Telegram Command Handler started"
- [ ] Bot can send messages (notifications work)
- [ ] You're chatting directly with the Control Bot (not the Notification Bot)

## Security Note

The command handler only responds to the configured `TELEGRAM_CHAT_ID`. Unauthorized users receive no response. Control commands (close, pause, etc.) additionally require PIN verification.

---

**Last Updated**: 2026-02
