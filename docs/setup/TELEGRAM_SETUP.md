# Telegram Setup Guide

## Quick Start

### Step 1: Create TWO Telegram Bots (v14.0 Dual Channel)

AlgVex uses **two independent bots** with separate responsibilities:

| Bot | Purpose | Messages |
|-----|---------|----------|
| **Control Bot** (Private Chat) | Operations monitoring + command interaction | Heartbeat, errors, SL/TP adjustments, command responses |
| **Notification Bot** (Channel) | Trade signals + performance for subscribers | Open/close signals, P&L, daily/weekly reports |

**For each bot:**
1. Open Telegram and search for `@BotFather`
2. Send `/newbot` command
3. Follow the instructions (name + username ending with 'bot')
4. Copy the **Bot Token**

### Step 2: Get Your Chat IDs

**Control Bot (Private Chat):**
1. Search for `@userinfobot` in Telegram
2. Send any message to it
3. Copy your user ID (a number like `987654321`)

**Notification Bot (Channel):**
1. Create a Telegram channel
2. Add the Notification Bot as administrator
3. Send a message in the channel
4. Visit `https://api.telegram.org/bot<NOTIFICATION_BOT_TOKEN>/getUpdates`
5. Find the channel's `chat_id` (negative number like `-1001234567890`)

### Step 3: Configure Environment

Add to `~/.env.algvex`:

```bash
# Control Bot (private chat - operations)
TELEGRAM_BOT_TOKEN="YOUR_CONTROL_BOT_TOKEN"
TELEGRAM_CHAT_ID="YOUR_PERSONAL_CHAT_ID"

# Notification Bot (channel - subscribers) [optional]
TELEGRAM_NOTIFICATION_BOT_TOKEN="YOUR_NOTIFICATION_BOT_TOKEN"
TELEGRAM_NOTIFICATION_CHAT_ID="YOUR_CHANNEL_CHAT_ID"
```

### Step 4: Test Connection

```bash
cd /home/linuxuser/nautilus_AlgVex && source venv/bin/activate && python3 scripts/diagnose.py --quick
```

### Step 5: Restart Service

```bash
sudo systemctl restart nautilus-trader
```

You should receive a startup message on the Control Bot.

---

## Message Routing (v14.0)

Each message goes to exactly ONE destination (zero duplication):

| Message Type | Control Bot (Private) | Notification Bot (Channel) |
|-------------|:--------------------:|:-------------------------:|
| System startup/shutdown | Yes | - |
| Heartbeat monitoring | Yes | - |
| **Open position signal** | - | Yes |
| **Close position result** | - | Yes |
| **Scale in/out** | - | Yes |
| **Daily report** | - | Yes |
| **Weekly report** | - | Yes |
| Error/warning alerts | Yes | - |
| SL/TP adjustments | Yes | - |
| Emergency SL | Yes | - |
| Command responses | Yes | - |

## Notification Format

All direction displays use Chinese futures terminology via `side_to_cn()`:

| Context | Display | NOT |
|---------|---------|-----|
| Open direction | **开多** / **开空** | ~~LONG/SHORT/BUY~~ |
| Close direction | **平多** / **平空** | ~~SELL/CLOSE LONG~~ |
| Position status | **多仓** / **空仓** | ~~多头/空头~~ |

---

## Commands (v3.0+)

Commands are sent to the **Control Bot** only.

**Query Commands** (no PIN): `/status`, `/position`, `/balance`, `/analyze`, `/orders`, `/history`, `/risk`, `/daily`, `/weekly`, `/config`, `/version`, `/logs`, `/profit`

**Control Commands** (PIN required): `/pause`, `/resume`, `/close`, `/force_analysis`, `/partial_close 50`, `/set_leverage 10`, `/modify_sl`, `/modify_tp`, `/restart`, `/calibrate`, `/reload_config`

Quick menu: Send `/menu` to see all available commands.

---

## Troubleshooting

### Bot doesn't respond to commands
1. Check chat ID matches: `cd /home/linuxuser/nautilus_AlgVex && source venv/bin/activate && python3 scripts/diagnose.py --quick`
2. Ensure only one bot instance is running
3. Check logs: `sudo journalctl -u nautilus-trader -f --no-hostname | grep -i telegram`

### Notifications work but commands don't
- Chat ID mismatch is the most common cause
- Verify with `@userinfobot` and compare with `~/.env.algvex`

### "Unauthorized" responses
- Update `TELEGRAM_CHAT_ID` in `~/.env.algvex` with correct ID

---

## Security

- Keep bot tokens in `~/.env.algvex` only (chmod 600)
- Never commit tokens to Git
- PIN-protected control commands prevent unauthorized trading actions
- Rate limiting prevents command spam

---

**Last Updated**: 2026-02
**Version**: 3.0 (Dual Channel, v14.0)
