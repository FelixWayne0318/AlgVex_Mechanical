"""
Telegram Bot for Trading Notifications

Provides real-time notifications for trading signals, order fills,
position updates, and system status via Telegram.

v3.0 Redesign (2026-02):
- Context-aware heartbeat (different layout for position vs no position)
- Visual progress bars for RSI, buy ratio, BB position
- Information hierarchy (most important data first)
- Clean formatting without version labels
- Consolidated message types
"""

import asyncio
import logging
from typing import Optional, Dict, Any
from datetime import datetime

try:
    from telegram import Bot
    from telegram.error import TelegramError
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    Bot = None
    TelegramError = Exception

# Import message queue (optional, graceful degradation if not available)
try:
    from utils.telegram_queue import TelegramMessageQueue, MessagePriority
    QUEUE_AVAILABLE = True
except ImportError:
    QUEUE_AVAILABLE = False
    TelegramMessageQueue = None
    MessagePriority = None


class TelegramBot:
    """
    Telegram Bot for sending trading notifications.

    Features:
    - Send formatted trading signals
    - Send order fill notifications
    - Send position updates
    - Send error/warning alerts
    - Async message queue (v2.0 - non-blocking)
    - Message persistence and retry (v2.0)
    - Alert convergence (v2.0)
    """

    def __init__(
        self,
        token: str,
        chat_id: str,
        logger: Optional[logging.Logger] = None,
        enabled: bool = True,
        message_timeout: float = 5.0,  # v2.0: Reduced from 30s to 5s
        use_queue: bool = True,  # v2.0: Use async message queue
        queue_db_path: str = "data/telegram_queue.db",
        queue_max_retries: int = 3,
        queue_alert_cooldown: int = 300,  # 5 minutes
        queue_send_interval: float = 0.5,  # v2.0: Interval between sends (rate limit)
        # v14.0: Notification group (separate bot for public signal broadcasting)
        notification_token: str = "",
        notification_chat_id: str = "",
    ):
        """
        Initialize Telegram Bot.

        Parameters
        ----------
        token : str
            Telegram Bot token from @BotFather
        chat_id : str
            Telegram chat ID to send messages to
        logger : Optional[logging.Logger]
            Logger instance for logging
        enabled : bool
            Whether the bot is enabled (default: True)
        message_timeout : float
            Timeout for sending messages (seconds), default: 5.0
        use_queue : bool
            Use async message queue for non-blocking sends (default: True)
        queue_db_path : str
            Path to SQLite database for message persistence
        queue_max_retries : int
            Maximum retry attempts for failed messages
        queue_alert_cooldown : int
            Cooldown period for alert convergence (seconds)
        queue_send_interval : float
            Interval between sends in seconds (rate limiting), default: 0.5
        """
        if not TELEGRAM_AVAILABLE:
            raise ImportError(
                "python-telegram-bot is not installed. "
                "Install it with: pip install python-telegram-bot"
            )

        self.token = token
        self.chat_id = chat_id
        self.logger = logger or logging.getLogger(__name__)
        self.enabled = enabled
        self.message_timeout = message_timeout

        # Initialize bot
        try:
            self.bot = Bot(token=token)
            self.logger.info("✅ Telegram Bot initialized successfully")
        except Exception as e:
            self.logger.error(f"❌ Failed to initialize Telegram Bot: {e}")
            self.enabled = False
            raise

        # v14.0: Notification group bot (separate bot for public signal broadcasting)
        self.notification_token = notification_token
        self.notification_chat_id = notification_chat_id
        self.notification_enabled = bool(notification_token and notification_chat_id)
        if self.notification_enabled:
            self.logger.info("✅ Notification group bot configured")

        # v2.0: Initialize message queue
        self.message_queue: Optional[TelegramMessageQueue] = None
        self.use_queue = use_queue and QUEUE_AVAILABLE

        if self.use_queue:
            try:
                self.message_queue = TelegramMessageQueue(
                    send_func=self._send_direct,
                    db_path=queue_db_path,
                    max_retries=queue_max_retries,
                    alert_cooldown=queue_alert_cooldown,
                    send_interval=queue_send_interval,
                    logger=self.logger,
                )
                self.message_queue.start()
                self.logger.info("✅ Telegram message queue initialized")
            except Exception as e:
                self.logger.warning(f"⚠️ Message queue init failed, using direct send: {e}")
                self.message_queue = None
                self.use_queue = False

    @staticmethod
    def side_to_cn(side: str, action: str = 'position') -> str:
        """
        Convert position/order side to standard Chinese futures terminology.

        Industry standard (Binance/OKX/Bybit Chinese):
          开多 = open long, 开空 = open short
          平多 = close long, 平空 = close short
          多仓 = long position, 空仓 = short position

        Parameters
        ----------
        side : str
            'LONG', 'SHORT', 'BUY', or 'SELL'
        action : str
            'open'     → 开多/开空
            'close'    → 平多/平空
            'position' → 多仓/空仓
            'side'     → 多/空
        """
        is_long = side.upper() in ('LONG', 'BUY')
        if action == 'open':
            return '开多' if is_long else '开空'
        elif action == 'close':
            return '平多' if is_long else '平空'
        elif action == 'position':
            return '多仓' if is_long else '空仓'
        else:  # 'side'
            return '多' if is_long else '空'

    @staticmethod
    def escape_markdown(text: str) -> str:
        """
        Escape special Markdown characters in text.

        Telegram Markdown uses: _ * [ ] ( ) ~ ` > # + - = | { } . !
        For basic Markdown mode, we escape characters that can break formatting.

        Note: We escape in a specific order to avoid double-escaping.
        The backslash must NOT be escaped here (would break intentional escapes).
        """
        if not text:
            return text
        result = str(text)
        # Characters that have special meaning in Telegram basic Markdown:
        # - _ * ` [ ] ( ) for formatting and links
        # We don't escape \ as it would break intentional escapes
        escape_chars = ['_', '*', '`', '[', ']', '(', ')']
        for char in escape_chars:
            result = result.replace(char, '\\' + char)
        return result

    @staticmethod
    def _split_message(text: str, max_len: int = 4096) -> list:
        """Split long message into chunks respecting Telegram's 4096 char limit.

        Splits at newline boundaries when possible, falls back to hard cut.
        """
        if len(text) <= max_len:
            return [text]
        chunks = []
        while text:
            if len(text) <= max_len:
                chunks.append(text)
                break
            # Find last newline within limit
            cut = text.rfind('\n', 0, max_len)
            if cut <= max_len * 0.3:
                # No good newline break, hard cut
                cut = max_len
            chunk = text[:cut]
            chunks.append(chunk)
            text = text[cut:].lstrip('\n')
        return chunks

    async def send_message(
        self,
        message: str,
        parse_mode: str = 'Markdown',
        disable_notification: bool = False
    ) -> bool:
        """
        Send a text message to Telegram.

        Parameters
        ----------
        message : str
            Message text to send
        parse_mode : str
            Parse mode for formatting (Markdown, HTML, or None)
        disable_notification : bool
            Send silently without notification

        Returns
        -------
        bool
            True if message sent successfully, False otherwise
        """
        if not self.enabled:
            self.logger.debug("Telegram bot is disabled, skipping message")
            return False

        # Auto-split long messages at Telegram's 4096 char limit
        chunks = self._split_message(message)

        try:
            for chunk in chunks:
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=chunk,
                    parse_mode=parse_mode,
                    disable_notification=disable_notification
                )
            self.logger.info(f"📱 Telegram message sent ({len(chunks)} part(s), {len(message)} chars)")
            return True

        except TelegramError as e:
            # If parse error, retry without formatting
            if "can't parse" in str(e).lower() or "parse entities" in str(e).lower():
                self.logger.warning(f"⚠️ Markdown parse error, retrying without formatting: {e}")
                try:
                    for chunk in chunks:
                        await self.bot.send_message(
                            chat_id=self.chat_id,
                            text=chunk,
                            parse_mode=None,  # Send as plain text
                            disable_notification=disable_notification
                        )
                    self.logger.info(f"📱 Telegram message sent (plain text, {len(chunks)} part(s))")
                    return True
                except Exception as retry_e:
                    self.logger.error(f"❌ Failed to send even without formatting: {retry_e}")
                    return False
            else:
                self.logger.error(f"❌ Telegram error: {e}")
                return False
        except Exception as e:
            self.logger.error(f"❌ Failed to send Telegram message: {e}")
            return False

    def send_message_sync(
        self,
        message: str,
        priority: Optional[int] = None,
        use_queue: Optional[bool] = None,
        broadcast: bool = False,
        **kwargs
    ) -> bool:
        """
        Synchronous method to send Telegram message.

        v2.0: Uses async message queue by default (non-blocking).
        Falls back to direct send if queue not available.
        v14.0: broadcast=True → send ONLY to notification channel (not private chat).
               broadcast=False → send ONLY to private chat (default).
               Each message goes to exactly one destination, zero duplication.

        Parameters
        ----------
        message : str
            Message text to send
        priority : int, optional
            Message priority (0=LOW, 1=NORMAL, 2=HIGH, 3=CRITICAL)
            Higher priority messages are sent first.
        use_queue : bool, optional
            Override queue usage for this message.
            Set to False for immediate blocking send.
        broadcast : bool
            If True, send ONLY to notification channel (v14.0).
            If False, send ONLY to private chat (default).
        **kwargs
            Additional arguments (parse_mode, disable_notification)

        Returns
        -------
        bool
            True if enqueued/sent successfully
        """
        if not self.enabled:
            self.logger.debug("Telegram bot is disabled, skipping message")
            return False

        # v14.0: broadcast=True → notification channel ONLY
        # v30.5: broadcast messages must NOT fall through to private chat queue,
        # because queue's send_func is _send_direct (private chat only) and
        # broadcast parameter is lost during enqueue/dequeue cycle.
        if broadcast:
            if self.notification_enabled:
                result = self._send_notification_direct(message, **kwargs)
                if result:
                    return True
                # v30.6: Non-blocking retry — schedule retry via one-shot timer
                # instead of blocking sleep which stalls NautilusTrader event loop.
                # Use threading.Timer for non-blocking delayed retry.
                import threading
                def _retry_broadcast():
                    retry_result = self._send_notification_direct(message, **kwargs)
                    if not retry_result:
                        self.logger.warning(
                            "⚠️ Notification channel send failed after retry, "
                            "falling back to private chat"
                        )
                        # v30.6: Fallback to private chat so subscribers at least
                        # get the message via control bot (better than total loss)
                        self._send_direct(message, **kwargs)
                timer = threading.Timer(1.0, _retry_broadcast)
                timer.daemon = True
                timer.start()
                return True  # Optimistically return True, retry handles failure
            else:
                # Notification channel not configured → fallback to private chat
                # Better to deliver to private chat than silently drop
                self.logger.warning(
                    "⚠️ broadcast=True but notification channel not enabled, "
                    "falling back to private chat"
                )
                # Fall through to private chat send below (don't return False)

        # Private chat send (non-broadcast only)
        # Determine whether to use queue
        should_use_queue = use_queue if use_queue is not None else self.use_queue

        # v2.0: Use queue for non-blocking send
        if should_use_queue and self.message_queue:
            # Convert priority to MessagePriority enum
            if priority is None:
                priority = 1  # NORMAL
            if QUEUE_AVAILABLE and MessagePriority:
                try:
                    msg_priority = MessagePriority(priority)
                except ValueError:
                    self.logger.debug("Using default value due to error")
                    msg_priority = MessagePriority.NORMAL
            else:
                msg_priority = priority

            result = self.message_queue.enqueue(
                message=message,
                priority=msg_priority,
                **kwargs
            )
        else:
            # Fallback: Direct send (blocking)
            result = self._send_direct(message, **kwargs)

        return result

    def _send_direct(self, message: str, **kwargs) -> bool:
        """
        Direct (blocking) message send via requests.

        This is the actual send implementation used by both
        direct calls and the message queue background thread.

        Reference: https://github.com/python-telegram-bot/python-telegram-bot/discussions/4096
        """
        import requests

        parse_mode = kwargs.get('parse_mode', 'Markdown')
        disable_notification = kwargs.get('disable_notification', False)

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            'chat_id': self.chat_id,
            'text': message,
            'disable_notification': disable_notification,
        }
        if parse_mode:
            payload['parse_mode'] = parse_mode

        try:
            response = requests.post(url, json=payload, timeout=self.message_timeout)
            result = response.json()

            if result.get('ok'):
                self.logger.debug(f"📱 Telegram message sent: {message}")
                return True

            # Handle Markdown parse errors - retry without formatting
            error_desc = result.get('description', '')
            if "can't parse" in error_desc.lower() or "parse entities" in error_desc.lower():
                self.logger.warning(f"⚠️ Markdown parse error, retrying without formatting")
                payload.pop('parse_mode', None)
                response = requests.post(url, json=payload, timeout=self.message_timeout)
                result = response.json()
                if result.get('ok'):
                    return True

            self.logger.error(f"❌ Telegram API error: {error_desc}")
            return False

        except requests.Timeout:
            self.logger.warning(f"⚠️ Telegram message timed out ({self.message_timeout}s)")
            return False
        except Exception as e:
            self.logger.error(f"❌ Error sending Telegram message: {e}")
            return False

    def _send_notification_direct(self, message: str, **kwargs) -> bool:
        """
        Send message to notification group via separate bot (v14.0).

        Fire-and-forget: failures are logged but don't affect control bot.
        Uses notification_token + notification_chat_id.
        """
        if not self.notification_enabled:
            return False

        import requests

        parse_mode = kwargs.get('parse_mode', 'Markdown')
        disable_notification = kwargs.get('disable_notification', False)

        url = f"https://api.telegram.org/bot{self.notification_token}/sendMessage"
        payload = {
            'chat_id': self.notification_chat_id,
            'text': message,
            'disable_notification': disable_notification,
        }
        if parse_mode:
            payload['parse_mode'] = parse_mode

        try:
            response = requests.post(url, json=payload, timeout=self.message_timeout)
            result = response.json()

            if result.get('ok'):
                self.logger.debug(f"📢 Notification group message sent: {message}")
                return True

            # Handle Markdown parse errors - retry without formatting
            error_desc = result.get('description', '')
            if "can't parse" in error_desc.lower() or "parse entities" in error_desc.lower():
                payload.pop('parse_mode', None)
                response = requests.post(url, json=payload, timeout=self.message_timeout)
                result = response.json()
                if result.get('ok'):
                    return True

            self.logger.warning(f"⚠️ Notification group send failed: {error_desc}")
            return False

        except Exception as e:
            self.logger.warning(f"⚠️ Notification group send error: {e}")
            return False

    def get_queue_stats(self) -> Dict[str, Any]:
        """Get message queue statistics (v2.0)."""
        if self.message_queue:
            return self.message_queue.get_stats()
        return {"queue_enabled": False}

    def stop_queue(self):
        """Stop the message queue (call on shutdown)."""
        if self.message_queue:
            self.message_queue.stop()
            self.logger.info("🛑 Telegram message queue stopped")

    # ==================== Visual Helpers ====================

    @staticmethod
    def _bar(value: float, max_val: float = 100, width: int = 10) -> str:
        """Create Unicode progress bar: ▓▓▓▓▓░░░░░"""
        if max_val <= 0 or value != value:  # handle NaN
            return '░' * width
        ratio = max(0.0, min(1.0, value / max_val))
        filled = int(round(ratio * width))
        return '▓' * filled + '░' * (width - filled)

    @staticmethod
    def _pnl_icon(value: float) -> str:
        """PnL directional emoji."""
        return '📈' if value > 0 else '📉' if value < 0 else '➖'

    @staticmethod
    def _signal_icon(signal: str) -> str:
        """Signal emoji mapping."""
        return {
            'LONG': '🟢', 'BUY': '🟢',
            'SHORT': '🔴', 'SELL': '🔴',
            'CLOSE': '🔵', 'REDUCE': '🟡',
            'HOLD': '⚪', 'PENDING': '⏳',
        }.get(signal, '❓')

    @staticmethod
    def _trend_icon(direction: str) -> str:
        """Trend direction emoji."""
        return {
            'BULLISH': '🟢', 'BEARISH': '🔴', 'NEUTRAL': '⚪',
        }.get(direction, '⚪')

    @staticmethod
    def _funding_display(raw_rate: float) -> float:
        """Convert raw funding rate to display percentage.

        Coinalyze: 0.0001 = 0.01%. If |rate| > 0.01, assume already percentage.
        """
        if abs(raw_rate) > 0.01:
            return raw_rate  # Already in percentage form
        return raw_rate * 100

    @staticmethod
    def _format_sr_compact(sr_zone: dict, ref_price: float) -> str:
        """Format S/R zones in compact form for trade execution / position closed messages.

        Supports both full zone arrays (from heartbeat data) and simple
        nearest_support / nearest_resistance (legacy format).
        """
        if not sr_zone or ref_price <= 0:
            return ''

        lines = []
        # Full zone arrays (v5.0+ format from heartbeat)
        support_zones = sr_zone.get('support_zones', [])
        resistance_zones = sr_zone.get('resistance_zones', [])

        if support_zones or resistance_zones:
            lines.append("\n📍 *S/R*")
            for z in sorted(resistance_zones, key=lambda x: x.get('price', 0), reverse=True):
                z_price = z.get('price', 0)
                if z_price <= ref_price:
                    continue
                z_pct = ((z_price - ref_price) / ref_price * 100)
                strength = z.get('strength', 'LOW')
                s_icon = '🔴' if strength == 'HIGH' else '🟠' if strength == 'MEDIUM' else '⚪'
                lines.append(f"  {s_icon} R ${z_price:,.0f} ({z_pct:+.1f}%) [{strength}]")
            # Current price marker (same as heartbeat format)
            lines.append(f"  ── 当前 ${ref_price:,.0f} ──")
            for z in sorted(support_zones, key=lambda x: x.get('price', 0), reverse=True):
                z_price = z.get('price', 0)
                if z_price >= ref_price:
                    continue
                z_pct = ((z_price - ref_price) / ref_price * 100)
                strength = z.get('strength', 'LOW')
                s_icon = '🟢' if strength == 'HIGH' else '🟡' if strength == 'MEDIUM' else '⚪'
                lines.append(f"  {s_icon} S ${z_price:,.0f} ({z_pct:+.1f}%) [{strength}]")
        else:
            # Legacy: simple nearest support/resistance
            nearest_support = sr_zone.get('nearest_support')
            nearest_resistance = sr_zone.get('nearest_resistance')
            if nearest_support and nearest_support < ref_price:
                s_pct = ((nearest_support - ref_price) / ref_price * 100)
                lines.append(f"📉 支撑 ${nearest_support:,.2f} ({s_pct:+.2f}%)")
            if nearest_resistance and nearest_resistance > ref_price:
                r_pct = ((nearest_resistance - ref_price) / ref_price * 100)
                lines.append(f"📈 阻力 ${nearest_resistance:,.2f} ({r_pct:+.2f}%)")

        return '\n'.join(lines)

    # ==================== Message Formatters ====================

    def format_heartbeat_message(self, heartbeat_data: Dict[str, Any], compact: bool = False) -> str:
        """
        Format heartbeat message — context-aware market pulse.

        Two display modes based on position state:
        - NO POSITION: Full market overview with progress bars and detailed technicals
        - HAS POSITION: Position P&L focus + compact market data

        Parameters
        ----------
        heartbeat_data : dict
            Complete market state data
        compact : bool
            Compact single-line mode (for mobile, not commonly used)
        """
        # === Extract core data ===
        price = heartbeat_data.get('price') or 0
        rsi = heartbeat_data.get('rsi') or 0
        signal = heartbeat_data.get('signal') or 'PENDING'
        confidence = heartbeat_data.get('confidence') or 'N/A'
        timer_count = heartbeat_data.get('timer_count') or 0
        equity = heartbeat_data.get('equity') or 0
        uptime_str = heartbeat_data.get('uptime_str') or 'N/A'

        # Position data
        position_side = heartbeat_data.get('position_side')
        entry_price = heartbeat_data.get('entry_price') or 0
        position_size = heartbeat_data.get('position_size') or 0
        position_pnl_pct = heartbeat_data.get('position_pnl_pct') or 0
        sl_price = heartbeat_data.get('sl_price')
        tp_price = heartbeat_data.get('tp_price')
        trailing_status = heartbeat_data.get('trailing_status')
        has_position = position_side in ('LONG', 'SHORT') if position_side else False

        # Enhanced technical data (new in v3.0 redesign)
        tech = heartbeat_data.get('technical') or {}
        adx = tech.get('adx')
        adx_regime = tech.get('adx_regime', '')
        trend_direction = tech.get('trend_direction', '')
        volume_ratio = tech.get('volume_ratio')
        bb_position = tech.get('bb_position')
        macd_histogram = tech.get('macd_histogram')

        # Order flow
        order_flow = heartbeat_data.get('order_flow') or {}
        buy_ratio = order_flow.get('buy_ratio')
        cvd_trend = order_flow.get('cvd_trend')
        # v19.2: Pre-computed flow signals
        flow_signals = order_flow.get('flow_signals', {})

        # Derivatives
        derivatives = heartbeat_data.get('derivatives') or {}
        funding_rate = derivatives.get('funding_rate')
        oi_change_pct = derivatives.get('oi_change_pct')

        # Order book
        order_book = heartbeat_data.get('order_book') or {}
        weighted_obi = order_book.get('weighted_obi')

        # S/R zones (v5.0: full zone data with strength/level)
        sr_zone = heartbeat_data.get('sr_zone') or {}
        support_zones = sr_zone.get('support_zones', [])
        resistance_zones = sr_zone.get('resistance_zones', [])
        block_long = sr_zone.get('block_long', False)
        block_short = sr_zone.get('block_short', False)

        # Derivatives (Binance: settled + predicted funding rate + trend)
        funding_rate_pct = derivatives.get('funding_rate_pct')       # Settled funding rate
        predicted_rate_pct = derivatives.get('predicted_rate_pct')   # Predicted rate
        next_funding_min = derivatives.get('next_funding_countdown_min')
        funding_trend = derivatives.get('funding_trend')
        liq_long = derivatives.get('liq_long')
        liq_short = derivatives.get('liq_short')

        # Signal status
        signal_status = heartbeat_data.get('signal_status') or {}

        # v15.0 P2: Confidence decay
        confidence_decay = heartbeat_data.get('confidence_decay') or {}

        now_str = datetime.utcnow().strftime('%H:%M')

        # === Compact mode (single line) ===
        if compact:
            sig_icon = self._signal_icon(signal)
            msg = f"📡 #{timer_count} | ${price:,.0f} | {sig_icon}{signal}"
            if has_position:
                pnl_icon = self._pnl_icon(position_pnl_pct)
                msg += f" | {pnl_icon}{position_pnl_pct:+.1f}%"
            msg += f" | ${equity:,.0f}"
            return msg

        # === Full mode ===
        msg = f"📡 #{timer_count} | BTC ${price:,.2f}\n"
        msg += "━━━━━━━━━━━━━━━━━━\n"

        # ======= POSITION SECTION (only when holding) =======
        if has_position:
            pos_emoji = '🟢' if position_side == 'LONG' else '🔴'
            pos_cn = self.side_to_cn(position_side, 'position')
            notional_value = position_size * price if price > 0 else 0
            msg += f"\n{pos_emoji} {pos_cn} {position_size:.4f} BTC (${notional_value:,.0f})\n"
            msg += f"  入场 ${entry_price:,.2f}\n"

            # P&L calculation
            pnl_usd = position_size * entry_price * (position_pnl_pct / 100) if entry_price > 0 else 0
            pnl_icon = self._pnl_icon(position_pnl_pct)
            msg += f"  {pnl_icon} 盈亏 ${pnl_usd:+,.2f} ({position_pnl_pct:+.2f}%)\n"

            # SL/TP
            if sl_price is not None:
                sl_pct = ((sl_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
                msg += f"  🛑 止损 ${sl_price:,.2f} ({sl_pct:+.2f}%)\n"
            if tp_price is not None:
                tp_pct = ((tp_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
                msg += f"  🎯 止盈 ${tp_price:,.2f} ({tp_pct:+.2f}%)\n"

            # R/R ratio
            if sl_price and tp_price and entry_price > 0:
                if position_side == 'LONG':
                    sl_dist = entry_price - sl_price
                    tp_dist = tp_price - entry_price
                else:
                    sl_dist = sl_price - entry_price
                    tp_dist = entry_price - tp_price
                if sl_dist > 0 and tp_dist > 0:
                    rr = tp_dist / sl_dist
                    rr_icon = '✅' if rr >= 2.0 else '✓' if rr >= 1.5 else '⚠️'
                    msg += f"  📊 R/R {rr:.1f}:1 {rr_icon}\n"

            # Trailing stop status (v24.2: Binance server-side)
            if trailing_status:
                _tr_count = trailing_status.get('active_count', 0)
                _tr_total = trailing_status.get('total_layers', 0)
                _tr_bps = trailing_status.get('callback_bps', 0)
                _tr_act = trailing_status.get('activation_price', 0)
                msg += f"  🔄 Trailing: {_tr_count}/{_tr_total}层"
                if _tr_bps:
                    msg += f" 回调{_tr_bps / 100:.1f}%"
                if _tr_act > 0:
                    msg += f" 激活@${_tr_act:,.0f}"
                msg += "\n"

            # v15.0 P2: Confidence decay status
            if confidence_decay:
                _conf_levels = {'HIGH': 3, 'MEDIUM': 2, 'LOW': 1}
                _entry_conf = confidence_decay.get('entry_confidence', '')
                _history = confidence_decay.get('history', [])
                if _entry_conf and _history:
                    _entry_lvl = _conf_levels.get(_entry_conf, 0)
                    _current_lvl = _conf_levels.get(_history[-1], 0)
                    _declining = _current_lvl < _entry_lvl
                    _arrow = '↘️ 衰减' if _declining else '→ 稳定'
                    msg += f"  📊 信心: {_entry_conf} → {_history[-1]} {_arrow}\n"

        # ======= TECHNICAL SECTION =======
        if has_position:
            # Compact layout when position is held (focus stays on P&L)
            msg += f"\n📊 技术面\n"
            parts = []
            parts.append(f"RSI {rsi:.0f}")
            if trend_direction:
                t_icon = self._trend_icon(trend_direction)
                adx_str = f" ADX {adx:.0f}" if adx else ""
                parts.append(f"趋势 {t_icon}{adx_str}")
            if macd_histogram is not None:
                m_icon = '📈' if macd_histogram > 0 else '📉'
                parts.append(f"MACD {m_icon}")
            if volume_ratio is not None:
                parts.append(f"量比 {volume_ratio:.1f}x")
            # Display 2 per line for compact view
            for i in range(0, len(parts), 2):
                msg += f"  {' | '.join(parts[i:i+2])}\n"
        else:
            # Detailed layout with progress bars when no position
            msg += f"\n📊 技术面\n"
            msg += f"  RSI   [{self._bar(rsi)}] {rsi:.1f}\n"
            if macd_histogram is not None:
                m_icon = '📈' if macd_histogram > 0 else '📉'
                msg += f"  MACD  {m_icon} {macd_histogram:+.1f}\n"
            if trend_direction:
                trend_map = {'BULLISH': '🟢 上涨', 'BEARISH': '🔴 下跌', 'NEUTRAL': '⚪ 震荡'}
                trend_text = trend_map.get(trend_direction, f'⚪ {trend_direction}')
                adx_str = f" (ADX {adx:.0f})" if adx else ""
                msg += f"  趋势  {trend_text}{adx_str}\n"
            if bb_position is not None:
                bb_pct = bb_position * 100
                if bb_pct < 20:
                    bb_label = '超卖区'
                elif bb_pct < 40:
                    bb_label = '下轨'
                elif bb_pct < 60:
                    bb_label = '中轨'
                elif bb_pct < 80:
                    bb_label = '上轨'
                else:
                    bb_label = '超买区'
                msg += f"  BB    {bb_label} ({bb_pct:.0f}%)\n"
            if volume_ratio is not None:
                v_icon = '🔥' if volume_ratio > 1.5 else '📊' if volume_ratio > 0.8 else '😴'
                msg += f"  量比  {v_icon} {volume_ratio:.1f}x\n"

        # ======= FLOW & SENTIMENT SECTION =======
        has_flow = (buy_ratio is not None or cvd_trend or
                    funding_rate is not None or funding_rate_pct is not None or
                    oi_change_pct is not None or weighted_obi is not None)

        if has_flow:
            if has_position:
                # Compact flow data for position mode
                msg += f"\n📈 资金流\n"
                flow_parts = []
                if buy_ratio is not None:
                    br_icon = '🟢' if buy_ratio > 0.55 else '🔴' if buy_ratio < 0.45 else '⚪'
                    flow_parts.append(f"买入 {buy_ratio*100:.0f}% {br_icon}")
                if funding_rate_pct is not None:
                    fr_icon = '🔴' if funding_rate_pct > 0.01 else '🟢' if funding_rate_pct < -0.01 else '⚪'
                    fr_str = f"已结算 {funding_rate_pct:.5f}% {fr_icon}"
                    if funding_trend:
                        ft_icon = '📈' if funding_trend == 'RISING' else '📉' if funding_trend == 'FALLING' else '➖'
                        fr_str += f" {ft_icon}"
                    flow_parts.append(fr_str)
                    # Predicted rate (from premiumIndex.lastFundingRate)
                    if predicted_rate_pct is not None:
                        pr_icon = '🔴' if predicted_rate_pct > 0.01 else '🟢' if predicted_rate_pct < -0.01 else '⚪'
                        flow_parts.append(f"预期 {predicted_rate_pct:.5f}% {pr_icon}")
                elif funding_rate is not None:
                    fr = self._funding_display(funding_rate)
                    flow_parts.append(f"费率 {fr:.5f}%")
                if oi_change_pct is not None:
                    flow_parts.append(f"OI {oi_change_pct:+.1f}%")
                if cvd_trend:
                    c_icon = '📈' if cvd_trend == 'RISING' else '📉' if cvd_trend == 'FALLING' else '➖'
                    flow_parts.append(f"CVD {c_icon}")
                # v19.2: OI×CVD and CVD-Price compact display
                if flow_signals.get('oi_cvd_signal'):
                    flow_parts.append(f"OI×CVD: {flow_signals['oi_cvd_signal']}")
                if flow_signals.get('cvd_price_signal'):
                    _cn = flow_signals.get('cvd_price_cn', '')
                    flow_parts.append(f"CVD: {_cn}")
                for i in range(0, len(flow_parts), 2):
                    msg += f"  {' | '.join(flow_parts[i:i+2])}\n"
                # Liquidations (compact)
                if liq_long is not None or liq_short is not None:
                    l_long = liq_long or 0
                    l_short = liq_short or 0
                    if l_long > 0 or l_short > 0:
                        msg += f"  爆仓 多${l_long*price:,.0f} 空${l_short*price:,.0f}\n"
            else:
                # Detailed flow data when no position
                msg += f"\n📈 资金流向\n"
                if buy_ratio is not None:
                    br_icon = '🟢' if buy_ratio > 0.55 else '🔴' if buy_ratio < 0.45 else '⚪'
                    msg += f"  买入比 [{self._bar(buy_ratio * 100)}] {buy_ratio*100:.1f}% {br_icon}\n"
                if cvd_trend:
                    c_icon = '📈' if cvd_trend == 'RISING' else '📉' if cvd_trend == 'FALLING' else '➖'
                    msg += f"  CVD   {c_icon} {cvd_trend}\n"
                if funding_rate_pct is not None:
                    fr_icon = '🔴' if funding_rate_pct > 0.01 else '🟢' if funding_rate_pct < -0.01 else '⚪'
                    fr_line = f"  已结算 {fr_icon} {funding_rate_pct:.5f}%"
                    if funding_trend:
                        ft_icon = '📈' if funding_trend == 'RISING' else '📉' if funding_trend == 'FALLING' else '➖'
                        fr_line += f" {ft_icon}"
                    msg += fr_line + "\n"
                    # Predicted rate (from premiumIndex.lastFundingRate, real-time)
                    if predicted_rate_pct is not None:
                        pr_icon = '🔴' if predicted_rate_pct > 0.01 else '🟢' if predicted_rate_pct < -0.01 else '⚪'
                        msg += f"  预期  {pr_icon} {predicted_rate_pct:.5f}%\n"
                    if next_funding_min is not None:
                        hours = next_funding_min // 60
                        mins = next_funding_min % 60
                        msg += f"  结算  ⏱ {hours}h {mins}m\n"
                elif funding_rate is not None:
                    fr = self._funding_display(funding_rate)
                    fr_icon = '🔴' if fr > 0.01 else '🟢' if fr < -0.01 else '⚪'
                    msg += f"  费率  {fr_icon} {fr:.4f}%\n"
                if oi_change_pct is not None:
                    oi_icon = '📈' if oi_change_pct > 5 else '📉' if oi_change_pct < -5 else '➖'
                    msg += f"  OI    {oi_icon} {oi_change_pct:+.1f}%\n"
                # v19.2: OI×CVD and CVD-Price cross-analysis signals
                if flow_signals.get('oi_cvd_signal'):
                    msg += f"  OI×CVD 📊 {flow_signals['oi_cvd_signal']}\n"
                if flow_signals.get('cvd_price_signal'):
                    _sig = flow_signals['cvd_price_signal']
                    _cn = flow_signals.get('cvd_price_cn', '')
                    _sig_icon = {'ACCUMULATION': '🟢', 'DISTRIBUTION': '🔴', 'ABSORPTION': '🟡', 'CONFIRMED': '⚪'}.get(_sig, '📊')
                    _sig_cn_map = {'ACCUMULATION': '吸筹', 'DISTRIBUTION': '派发', 'ABSORPTION': '吸收', 'CONFIRMED': '确认'}
                    _sig_display = _sig_cn_map.get(_sig, _sig)
                    msg += f"  CVD信号 {_sig_icon} {_sig_display} ({_cn})\n"
                if weighted_obi is not None:
                    obi_icon = '🟢' if weighted_obi > 0.1 else '🔴' if weighted_obi < -0.1 else '⚪'
                    msg += f"  OBI   {obi_icon} {weighted_obi:+.3f}\n"
                # Liquidations (1h)
                if liq_long is not None or liq_short is not None:
                    l_long = liq_long or 0
                    l_short = liq_short or 0
                    if l_long > 0 or l_short > 0:
                        liq_icon = '🔥' if (l_long + l_short) * price > 50_000_000 else '💥'
                        msg += f"  爆仓  {liq_icon} 多${l_long*price:,.0f} | 空${l_short*price:,.0f}\n"

        # ======= S/R ZONES SECTION (v5.0: full zone display) =======
        has_zones = bool(support_zones or resistance_zones)
        if has_zones:
            msg += f"\n📍 支撑 / 阻力\n"
            # Sort: resistance by price ascending (closest to price first, displayed bottom-up)
            resistance_zones = sorted(resistance_zones, key=lambda z: z.get('price', 0))
            # Sort: support by price descending (closest to price first)
            support_zones = sorted(support_zones, key=lambda z: z.get('price', 0), reverse=True)
            # Resistance zones (closest first, then farther)
            for z in reversed(resistance_zones):  # show highest at top
                z_price = z.get('price', 0)
                if z_price <= price:
                    continue
                z_pct = ((z_price - price) / price * 100) if price > 0 else 0
                strength = z.get('strength', 'LOW')
                level = z.get('level', 'MINOR')
                touch = z.get('touch_count', 0)
                s_icon = '🔴' if strength == 'HIGH' else '🟠' if strength == 'MEDIUM' else '⚪'
                l_tag = '日' if level == 'MAJOR' else '4H' if level == 'INTERMEDIATE' else '15m'
                touch_str = f" T{touch}" if touch > 0 else ""
                msg += f"  {s_icon} R ${z_price:,.0f} ({z_pct:+.1f}%) [{l_tag}|{strength}{touch_str}]\n"
            # Current price marker
            msg += f"  ── 当前 ${price:,.0f} ──\n"
            # Support zones (top → bottom, closest first)
            for z in support_zones:
                z_price = z.get('price', 0)
                if z_price >= price:
                    continue
                z_pct = ((z_price - price) / price * 100) if price > 0 else 0
                strength = z.get('strength', 'LOW')
                level = z.get('level', 'MINOR')
                touch = z.get('touch_count', 0)
                s_icon = '🟢' if strength == 'HIGH' else '🟡' if strength == 'MEDIUM' else '⚪'
                l_tag = '日' if level == 'MAJOR' else '4H' if level == 'INTERMEDIATE' else '15m'
                touch_str = f" T{touch}" if touch > 0 else ""
                msg += f"  {s_icon} S ${z_price:,.0f} ({z_pct:+.1f}%) [{l_tag}|{strength}{touch_str}]\n"
            # Hard control warnings
            if block_long or block_short:
                blocks = []
                if block_long:
                    blocks.append("🚫 开多")
                if block_short:
                    blocks.append("🚫 开空")
                msg += f"  ⚠️ {' | '.join(blocks)}\n"

        # ======= SIGNAL SECTION =======
        sig_icon = self._signal_icon(signal)
        signal_is_stale = heartbeat_data.get('signal_is_stale', False)
        stale_label = " (上次)" if signal_is_stale else ""
        risk_level = heartbeat_data.get('risk_level')
        position_size_pct = heartbeat_data.get('position_size_pct')

        # v32.2: Convert all signal labels to Chinese per CLAUDE.md language spec
        _signal_cn_map = {'HOLD': '观望', 'CLOSE': '平仓', 'REDUCE': '减仓'}
        if signal in ('LONG', 'SHORT'):
            signal_display = self.side_to_cn(signal, 'open')
        else:
            signal_display = _signal_cn_map.get(signal, signal)
        msg += f"\n📐 {sig_icon} {signal_display} ({confidence}){stale_label}"

        # v4.14: Show Risk Manager's position sizing and risk assessment
        if signal not in ('HOLD', 'PENDING') and (risk_level or position_size_pct is not None):
            rm_parts = []
            if position_size_pct is not None:
                rm_parts.append(f"仓位 {position_size_pct}%")
            if risk_level:
                risk_cn = {'LOW': '低', 'MEDIUM': '中', 'HIGH': '高'}.get(risk_level, risk_level)
                rm_parts.append(f"风险 {risk_cn}")
            if rm_parts:
                msg += f"\n📐 {' | '.join(rm_parts)}"

        # Signal execution status
        # Guard: don't show stale open-position action when there's no position
        if signal_status:
            executed = signal_status.get('executed', False)
            reason = signal_status.get('reason', '')
            action_taken = signal_status.get('action_taken', '')
            if executed and action_taken:
                # Only show open-position action if we actually have a position
                if has_position or '开' not in action_taken:
                    msg += f"\n✅ {action_taken}"
                # else: skip stale open-position action when position is already closed
            elif reason:
                msg += f"\n⏸️ {reason}"
                # v34.2: Show HOLD source for transparency
                _hold_src = signal_status.get('hold_source', '')
                if _hold_src:
                    _src_cn = {
                        'cooldown': '止损冷静期',
                        'gate_skip': '市场未变化',
                        'dedup': '重复信号',
                        'risk_breaker': '风控熔断',
                        'mechanical_hold': '评分不足',
                        'direction_lock': '方向锁定',
                        'confidence_gate': '信心不足',
                    }.get(_hold_src, _hold_src)
                    msg += f" [{_src_cn}]"

        # ======= MECHANICAL SCORING (v47.0) =======
        ant = heartbeat_data.get('anticipatory_scores')
        if ant:
            _di = {'BULLISH': '🟢', 'BEARISH': '🔴', 'NEUTRAL': '⚪',
                   'FADING': '🟡', 'MIXED': '🟡', 'N/A': '⚪'}
            struct = ant.get('structure', {})
            div = ant.get('divergence', {})
            flow = ant.get('order_flow', {})
            msg += f"\n\n📐 预判评分"
            msg += f"\n  {_di.get(struct.get('direction', ''), '⚪')} Structure {struct.get('score', 0)}/10 ({struct.get('raw', 0):+.2f})"
            msg += f"\n  {_di.get(div.get('direction', ''), '⚪')} Divergence {div.get('score', 0)}/10 ({div.get('raw', 0):+.2f})"
            msg += f"\n  {_di.get(flow.get('direction', ''), '⚪')} OrderFlow {flow.get('score', 0)}/10 ({flow.get('raw', 0):+.2f})"
            _net = ant.get('anticipatory_raw', 0)
            _ni = '🟢' if _net > 0.05 else '🔴' if _net < -0.05 else '⚪'
            msg += f"\n  {_ni} Net: {_net:+.3f}"
            _regime_cn = {'TRENDING': '趋势', 'RANGING': '震荡', 'MEAN_REVERSION': '均值回归',
                          'VOLATILE': '高波动', 'DEFAULT': '默认'}.get(ant.get('regime', ''), ant.get('regime', ''))
            _ctx_cn = {'CONFIRMING': '确认', 'NEUTRAL': '中性', 'OPPOSING': '对抗'}.get(
                ant.get('trend_context', ''), ant.get('trend_context', ''))
            msg += f"\n  体制: {_regime_cn} | 趋势: {_ctx_cn}"
            _risk = ant.get('risk_env', {})
            msg += f"\n  风险环境: {_risk.get('level', 'N/A')} ({_risk.get('score', 0)}/10)"

        # ======= LAYER 3: OUTCOME FEEDBACK (v35.0) =======
        layer3 = heartbeat_data.get('layer3')
        if layer3:
            total = layer3.get('total_trades', 0)
            wr = layer3.get('overall_win_rate', 0)
            streak = layer3.get('current_streak', 0)
            streak_type = layer3.get('current_streak_type', 'win')
            streak_icon = '🟢' if streak_type == 'win' else '🔴'
            streak_cn = '连胜' if streak_type == 'win' else '连负'
            msg += f"\n\n📈 交易统计 ({total}笔): 胜率{wr:.0%} | {streak_icon}{streak}{streak_cn}"
            # Confidence calibration compact display
            conf_cal = layer3.get('confidence_calibration', {})
            conf_parts = []
            for level in ['HIGH', 'MEDIUM', 'LOW']:
                d = conf_cal.get(level)
                if d and d.get('n', 0) > 0:
                    wr_val = d['win_rate']
                    ev = d.get('ev')
                    ev_str = f" EV{ev:+.2f}" if ev is not None else ""
                    conf_parts.append(f"{level[0]}:{wr_val:.0%}({d['n']}){ev_str}")
            if conf_parts:
                msg += f"\n  信心校准: {' | '.join(conf_parts)}"
            # Calibration flags (overconfident, miscalibrated)
            flags = layer3.get('flags', [])
            for flag in flags:
                if 'OVERCONFIDENT' in flag:
                    msg += "\n  ⚠️ 过度自信: HIGH胜率低于MEDIUM"
                elif 'MISCALIBRATED' in flag:
                    msg += "\n  ⚠️ 校准偏差: MEDIUM胜率低于LOW"
                elif 'NEGATIVE_EV' in flag:
                    msg += "\n  ⚠️ 负期望值: HIGH信心亏损"

        # ======= CALIBRATION STATUS =======
        cal_data = heartbeat_data.get('calibration')
        if cal_data:
            cal_src = cal_data.get('source', 'defaults')
            cal_ver = cal_data.get('version', '?')
            cal_stale = cal_data.get('stale', False)
            if cal_stale:
                age_h = cal_data.get('age_hours')
                age_str = f" ({age_h / 24:.0f}d)" if age_h else ""
                msg += f"\n\n⚠️ S/R校准过期{age_str} `{cal_ver}`"
            elif cal_src == 'file':
                msg += f"\n\n📊 S/R校准 `{cal_ver}`"
            # defaults: don't clutter heartbeat

        # ======= HMM REGIME / KELLY / FEAR & GREED (v2.0) =======
        regime_info = heartbeat_data.get('regime_info')
        if regime_info:
            _regime = regime_info.get('regime', 'RANGING')
            _regime_src = regime_info.get('source', 'adx_fallback')
            _regime_cn = {'STRONG_TREND': '强趋势', 'WEAK_TREND': '弱趋势', 'RANGING': '震荡'}.get(_regime, _regime)
            msg += f"\n\n🔀 体制: {_regime_cn} ({_regime_src})"

        kelly_info = heartbeat_data.get('kelly_info')
        if kelly_info:
            _kelly_src = kelly_info.get('source', 'warmup')
            if _kelly_src == 'warmup':
                _k_count = kelly_info.get('trade_count', 0)
                _k_min = kelly_info.get('min_trades', 50)
                _k_pct = (_k_count / _k_min * 100) if _k_min > 0 else 0
                msg += f"\n📐 仓位: Kelly warmup {_k_pct:.1f}%"
            else:
                _k_frac = kelly_info.get('fraction', 0.25)
                msg += f"\n📐 仓位: Kelly f={_k_frac:.2f}"

        fear_greed = heartbeat_data.get('fear_greed')
        if fear_greed and isinstance(fear_greed, dict):
            _fg_val = fear_greed.get('value')
            _fg_class = fear_greed.get('classification', '')
            if _fg_val is not None:
                msg += f"\n😱 恐贪: {_fg_val} ({_fg_class})"

        # ======= FOOTER =======
        pos_text = self.side_to_cn(position_side, 'position') if has_position else '空仓'
        msg += f"\n\n💼 {pos_text} | 🏦 ${equity:,.2f}"
        msg += f"\n⏱ {uptime_str} | {now_str} UTC"

        return msg

    def format_trade_execution(self, execution_data: Dict[str, Any]) -> str:
        """
        Format unified trade execution notification.

        Combines signal, fill, and position info into a single message.

        Parameters
        ----------
        execution_data : dict
            Contains signal, confidence, side, quantity, entry_price,
            sl_price, tp_price, rsi, macd, winning_side, reasoning,
            action_taken, entry_quality, sr_zone
        """
        signal = execution_data.get('signal', 'UNKNOWN')
        confidence = execution_data.get('confidence', 'UNKNOWN')
        side = execution_data.get('side', 'UNKNOWN')
        quantity = execution_data.get('quantity', 0.0)
        entry_price = execution_data.get('entry_price', 0.0)
        sl_price = execution_data.get('sl_price')
        tp_price = execution_data.get('tp_price')
        rsi = execution_data.get('rsi')
        macd = execution_data.get('macd')
        winning_side = execution_data.get('winning_side', '')
        reasoning = execution_data.get('reasoning', '')
        action_taken = execution_data.get('action_taken', '')
        entry_quality = execution_data.get('entry_quality')
        sr_zone = execution_data.get('sr_zone') or {}

        # Emoji and text
        side_emoji = '🟢' if side in ('LONG', 'BUY') else '🔴' if side in ('SHORT', 'SELL') else '⚪'
        side_cn = self.side_to_cn(side, 'side')
        conf_cn = {'HIGH': '高', 'MEDIUM': '中', 'LOW': '低'}.get(confidence, confidence)
        amount = quantity * entry_price

        # Title — use action_taken from strategy (already localized via side_to_cn())
        if action_taken:
            title = action_taken
        else:
            signal_map = {
                'LONG': '开多', 'BUY': '开多', 'SHORT': '开空', 'SELL': '开空',
                'CLOSE': '平仓', 'REDUCE': '减仓',
            }
            title = signal_map.get(signal, signal)

        msg = f"{side_emoji} *交易执行 — {title}*\n"
        msg += "━━━━━━━━━━━━━━━━━━\n"
        msg += f"📊 {quantity:.4f} BTC @ ${entry_price:,.2f} (${amount:,.2f})\n"
        risk_level = execution_data.get('risk_level')
        position_size_pct = execution_data.get('position_size_pct')

        msg += f"📋 信心: {conf_cn}"

        msg += "\n"

        # v47.0: 3-dimension mechanical confluence
        confluence = execution_data.get('confluence', {})
        if confluence and confluence.get('aligned_layers') is not None:
            aligned = confluence.get('aligned_layers', 0)
            _di = {'BULLISH': '🟢', 'BEARISH': '🔴', 'NEUTRAL': '⚪'}
            dims = []
            for key, label in [('structure', 'S'), ('divergence', 'D'), ('order_flow', 'F')]:
                val = confluence.get(key, 'NEUTRAL')
                dims.append(f"{_di.get(val, '⚪')}{label}")
            msg += f"📊 {' '.join(dims)} ({aligned}维一致)\n"

        # v4.14: Risk Manager assessment
        if risk_level or position_size_pct is not None:
            rm_parts = []
            if position_size_pct is not None:
                rm_parts.append(f"仓位 {position_size_pct}%")
            if risk_level:
                risk_cn = {'LOW': '低风险', 'MEDIUM': '中风险', 'HIGH': '高风险'}.get(risk_level, risk_level)
                rm_parts.append(risk_cn)
            msg += f"📐 {' | '.join(rm_parts)}\n"

        # SL/TP and R/R
        if sl_price or tp_price:
            msg += "\n"
            if sl_price:
                sl_pct = ((sl_price / entry_price) - 1) * 100 if entry_price > 0 else 0
                msg += f"🛑 止损 ${sl_price:,.2f} ({sl_pct:+.2f}%)\n"
            if tp_price:
                tp_pct = ((tp_price / entry_price) - 1) * 100 if entry_price > 0 else 0
                msg += f"🎯 止盈 ${tp_price:,.2f} ({tp_pct:+.2f}%)\n"

            # R/R ratio
            if sl_price and tp_price and entry_price > 0:
                if side in ('LONG', 'BUY'):
                    sl_dist = entry_price - sl_price
                    tp_dist = tp_price - entry_price
                else:
                    sl_dist = sl_price - entry_price
                    tp_dist = entry_price - tp_price
                if sl_dist > 0 and tp_dist > 0:
                    rr = tp_dist / sl_dist
                    rr_icon = '✅' if rr >= 2.0 else '✓' if rr >= 1.5 else '⚠️'
                    msg += f"📊 R/R {rr:.1f}:1 {rr_icon}\n"

        # S/R levels (unified format via helper)
        sr_text = self._format_sr_compact(sr_zone, entry_price)
        if sr_text:
            msg += sr_text + "\n"

        # Technical indicators
        if rsi is not None or macd is not None:
            parts = []
            if rsi is not None:
                parts.append(f"RSI {rsi:.1f}")
            if macd is not None:
                parts.append(f"MACD {macd:.4f}")
            msg += f"\n📊 {' | '.join(parts)}\n"

        # v19.2: Flow signals (OI×CVD + CVD-Price)
        flow_signals = execution_data.get('flow_signals', {})
        _flow_parts = []
        if flow_signals.get('oi_cvd_signal'):
            _flow_parts.append(f"OI×CVD: {flow_signals['oi_cvd_signal']}")
        if flow_signals.get('cvd_price_signal'):
            _cn = flow_signals.get('cvd_price_cn', '')
            _sig = flow_signals['cvd_price_signal']
            _flow_parts.append(f"CVD: {_cn}")
        if _flow_parts:
            msg += f"📊 {' | '.join(_flow_parts)}\n"

        # Entry quality
        if entry_quality:
            msg += f"\n📍 入场质量: {entry_quality}\n"

        # v47.0: Mechanical net score + regime + S/R calibration
        _meta_parts = []
        ant_scores = execution_data.get('anticipatory_scores')
        if ant_scores:
            _net = ant_scores.get('anticipatory_raw', 0)
            _regime = ant_scores.get('regime', '')
            _regime_cn = {'TRENDING': '趋势', 'RANGING': '震荡', 'MEAN_REVERSION': '均值回归',
                          'VOLATILE': '高波动', 'DEFAULT': '默认'}.get(_regime, _regime)
            _meta_parts.append(f"📐 Net {_net:+.3f} ({_regime_cn})")
        cal_data = execution_data.get('calibration')
        if cal_data:
            cal_stale = cal_data.get('stale', False)
            if cal_stale:
                _meta_parts.append(f"⚠️ S/R校准过期")
            else:
                _meta_parts.append(f"📊 S/R校准 {cal_data.get('version', '?')}")
        if _meta_parts:
            msg += f"\n{' | '.join(_meta_parts)}\n"

        # v47.0: Mechanical reasoning
        if reasoning:
            safe = self.escape_markdown(reasoning)
            msg += f"\n📐 {safe}\n"

        msg += f"\n⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
        return msg

    def format_position_update(self, position_data: Dict[str, Any]) -> str:
        """
        Format position update notification.

        Parameters
        ----------
        position_data : dict
            Contains action (OPENED/CLOSED/UPDATE), side, quantity,
            entry_price, current_price, pnl, pnl_pct, sl_price, tp_price,
            close_reason, close_reason_detail, entry_quality, rr_ratio, sr_zone
        """
        action = position_data.get('action', 'UPDATE')
        side = position_data.get('side', 'UNKNOWN')
        quantity = position_data.get('quantity', 0.0)
        entry_price = position_data.get('entry_price', 0.0)
        current_price = position_data.get('current_price', 0.0)
        pnl = position_data.get('pnl', 0.0)
        pnl_pct = position_data.get('pnl_pct', 0.0)
        sl_price = position_data.get('sl_price')
        tp_price = position_data.get('tp_price')
        close_reason = position_data.get('close_reason', 'MANUAL')
        close_reason_detail = position_data.get('close_reason_detail', '')
        entry_quality = position_data.get('entry_quality')
        sr_zone = position_data.get('sr_zone') or {}

        side_cn = self.side_to_cn(side, 'side')

        if action == 'OPENED':
            emoji = '📈' if side == 'LONG' else '📉'
            msg = f"{emoji} *开{side_cn}仓成功*\n"
            msg += "━━━━━━━━━━━━━━━━━━\n"
            notional = quantity * entry_price
            msg += f"📊 {quantity:.4f} BTC (${notional:,.0f}) @ ${entry_price:,.2f}\n"

            # SL/TP and R/R
            if sl_price:
                sl_pct = ((sl_price / entry_price) - 1) * 100 if entry_price > 0 else 0
                msg += f"🛑 止损 ${sl_price:,.2f} ({sl_pct:+.2f}%)\n"
            if tp_price:
                tp_pct = ((tp_price / entry_price) - 1) * 100 if entry_price > 0 else 0
                msg += f"🎯 止盈 ${tp_price:,.2f} ({tp_pct:+.2f}%)\n"

            if sl_price and tp_price and entry_price > 0:
                if side == 'LONG':
                    sl_d = entry_price - sl_price
                    tp_d = tp_price - entry_price
                else:
                    sl_d = sl_price - entry_price
                    tp_d = entry_price - tp_price
                if sl_d > 0 and tp_d > 0:
                    rr = tp_d / sl_d
                    rr_icon = '✅' if rr >= 2.0 else '✓' if rr >= 1.5 else '⚠️'
                    msg += f"📊 R/R {rr:.1f}:1 {rr_icon}\n"

            if entry_quality:
                msg += f"📍 入场质量: {entry_quality}\n"

        elif action == 'CLOSED':
            # Determine close type
            if close_reason == 'TAKE_PROFIT':
                emoji = '🎯'
                title = '止盈平仓'
            elif close_reason == 'STOP_LOSS':
                emoji = '🛑'
                title = '止损平仓'
            elif close_reason == 'TIME_BARRIER':
                emoji = '⏰'
                title = '时间屏障平仓'
            elif close_reason == 'EMERGENCY':
                emoji = '🚨'
                title = '紧急平仓'
            elif close_reason == 'REVERSAL':
                emoji = '🔄'
                title = '反转平仓'
            else:
                emoji = '✅' if pnl >= 0 else '❌'
                title = '平仓完成'

            msg = f"{emoji} *{title} — {side_cn}*\n"
            msg += "━━━━━━━━━━━━━━━━━━\n"
            notional = quantity * current_price
            msg += f"📊 {quantity:.4f} BTC (${notional:,.0f}) @ ${entry_price:,.2f} → ${current_price:,.2f}\n"

            pnl_icon = self._pnl_icon(pnl)
            msg += f"{pnl_icon} *盈亏: ${pnl:,.2f} ({pnl_pct:+.2f}%)*\n"

            if close_reason_detail:
                msg += f"📋 {close_reason_detail}\n"

            # S/R zones at close time (unified format)
            sr_text = self._format_sr_compact(sr_zone, current_price)
            if sr_text:
                msg += sr_text + "\n"

        else:  # UPDATE
            pnl_icon = self._pnl_icon(pnl)
            notional = quantity * current_price if current_price > 0 else quantity * entry_price
            msg = f"📊 *持仓更新 — {side_cn}*\n"
            msg += "━━━━━━━━━━━━━━━━━━\n"
            msg += f"📊 {quantity:.4f} BTC (${notional:,.0f}) @ ${entry_price:,.2f}\n"
            msg += f"💵 当前 ${current_price:,.2f}\n"
            msg += f"{pnl_icon} 盈亏: ${pnl:,.2f} ({pnl_pct:+.2f}%)\n"

        msg += f"\n⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
        return msg

    def format_startup_message(self, instrument_id: str, config: Dict[str, Any]) -> str:
        """
        Format strategy startup notification.

        Parameters
        ----------
        instrument_id : str
            Trading instrument identifier
        config : dict
            Strategy configuration
        """
        safe_instrument = self.escape_markdown(str(instrument_id))
        timeframe = config.get('timeframe', '30m')

        # Build feature flags
        features = []
        if config.get('enable_auto_sl_tp', True):
            features.append("自动 SL/TP")
        if config.get('enable_oco', True):
            features.append("OCO 订单")
        if config.get('mtf_enabled', False):
            features.append("MTF 多时间框架")
        features.append("机械评分")

        features_str = " | ".join(features)

        return (
            f"🚀 *策略已启动*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 {safe_instrument} | {timeframe}\n"
            f"✅ {features_str}\n"
            f"🎯 监控市场中...\n"
            f"\n⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )

    def format_shutdown_message(self, shutdown_data: Dict[str, Any]) -> str:
        """
        Format strategy shutdown notification.

        Parameters
        ----------
        shutdown_data : dict
            Shutdown information
        """
        instrument_id = shutdown_data.get('instrument_id', 'N/A')
        safe_instrument = self.escape_markdown(str(instrument_id))
        reason = shutdown_data.get('reason', 'normal')
        reason_map = {
            'normal': '正常停止',
            'user_stop': '用户停止',
            'error': '异常停止',
            'maintenance': '维护',
            'signal': '收到信号',
        }
        reason_text = reason_map.get(reason, reason)
        uptime = shutdown_data.get('uptime', 'N/A')

        msg = (
            f"🛑 *策略已停止*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 {safe_instrument}\n"
            f"📋 {reason_text} | ⏱ {uptime}\n"
        )

        # Session stats
        total_trades = shutdown_data.get('total_trades')
        total_pnl = shutdown_data.get('total_pnl')
        final_equity = shutdown_data.get('final_equity')

        if total_trades is not None or total_pnl is not None:
            msg += "\n📈 *本次统计*\n"
            if total_trades is not None:
                msg += f"  交易数: {total_trades}\n"
            if total_pnl is not None:
                pnl_icon = '🟢' if total_pnl >= 0 else '🔴'
                msg += f"  盈亏: {pnl_icon} ${total_pnl:,.2f}\n"
            if final_equity is not None:
                msg += f"  余额: ${final_equity:,.2f}\n"

        msg += f"\n⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
        return msg

    def format_error_alert(self, error_data: Dict[str, Any]) -> str:
        """Format error/warning notification with priority-based formatting."""
        level = error_data.get('level', 'ERROR')
        message = self.escape_markdown(str(error_data.get('message', '未知错误')))
        context = error_data.get('context', '')

        level_map = {
            'CRITICAL': ('🚨', '严重错误'),
            'WARNING': ('⚠️', '警告'),
            'ERROR': ('❌', '错误'),
        }
        emoji, level_cn = level_map.get(level, ('❌', '错误'))

        msg = f"{emoji} *{level_cn}*\n\n{message}\n"

        if context:
            msg += f"\n📋 {self.escape_markdown(str(context))}\n"

        msg += f"\n⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
        return msg

    def format_daily_summary(self, summary_data: Dict[str, Any]) -> str:
        """
        Format daily performance summary.

        Parameters
        ----------
        summary_data : dict
            Daily summary data
        """
        date = summary_data.get('date', datetime.utcnow().strftime('%Y-%m-%d'))
        total_trades = summary_data.get('total_trades', 0)
        winning_trades = summary_data.get('winning_trades', 0)
        losing_trades = summary_data.get('losing_trades', 0)
        total_pnl = summary_data.get('total_pnl', 0.0)
        total_pnl_pct = summary_data.get('total_pnl_pct', 0.0)
        largest_win = summary_data.get('largest_win', 0.0)
        largest_loss = summary_data.get('largest_loss', 0.0)
        starting_equity = summary_data.get('starting_equity', 0.0)
        ending_equity = summary_data.get('ending_equity', 0.0)
        signals_generated = summary_data.get('signals_generated', 0)
        signals_executed = summary_data.get('signals_executed', 0)

        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
        pnl_icon = '🟢' if total_pnl >= 0 else '🔴'
        trend_icon = '📈' if total_pnl >= 0 else '📉'

        msg = f"📊 *日报 — {date}*\n"
        msg += "━━━━━━━━━━━━━━━━━━\n"
        msg += f"\n💰 *盈亏*\n"
        msg += f"  {pnl_icon} ${total_pnl:+,.2f} ({total_pnl_pct:+.2f}%)\n"
        msg += f"  最佳: +{largest_win:.2f}% | 最差: -{largest_loss:.2f}%\n"
        msg += f"\n📈 *交易*\n"
        msg += f"  总数: {total_trades} | 盈利: {winning_trades} | 亏损: {losing_trades}\n"
        msg += f"  胜率: {win_rate:.1f}%\n"

        if signals_generated > 0:
            msg += f"\n🎯 *信号*\n"
            msg += f"  生成: {signals_generated} | 执行: {signals_executed}\n"

        msg += f"\n💵 *余额*\n"
        msg += f"  ${starting_equity:,.2f} → ${ending_equity:,.2f}"
        change = ending_equity - starting_equity
        msg += f" ({trend_icon} ${change:+,.2f})\n"

        # v5.1: Trade evaluation stats
        eval_stats = summary_data.get('evaluation', {})
        if eval_stats and eval_stats.get('total_evaluated', 0) > 0:
            msg += f"\n🏅 *交易质量*\n"
            grades = eval_stats.get('grade_distribution', {})
            grade_str = " ".join(f"{g}:{c}" for g, c in sorted(grades.items()))
            msg += f"  评级: {grade_str}\n"
            msg += f"  方向准确率: {eval_stats.get('direction_accuracy', 0):.0f}%\n"
            avg_rr = eval_stats.get('avg_winning_rr', 0)
            if avg_rr > 0:
                msg += f"  平均盈利 R/R: {avg_rr:.1f}:1\n"
            # v11.5: SL/TP optimization stats
            avg_mae = eval_stats.get('avg_mae_pct')
            avg_mfe = eval_stats.get('avg_mfe_pct')
            if avg_mae or avg_mfe:
                msg += f"  MAE/MFE: {avg_mae or 0:.1f}% / {avg_mfe or 0:.1f}%\n"
            ct_pct = eval_stats.get('counter_trend_pct')
            if ct_pct:
                msg += f"  逆势占比: {ct_pct:.0f}%\n"

        msg += f"\n⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
        return msg

    def format_weekly_summary(self, summary_data: Dict[str, Any]) -> str:
        """
        Format weekly performance summary.

        Parameters
        ----------
        summary_data : dict
            Weekly summary data
        """
        week_start = summary_data.get('week_start', 'N/A')
        week_end = summary_data.get('week_end', 'N/A')
        total_trades = summary_data.get('total_trades', 0)
        winning_trades = summary_data.get('winning_trades', 0)
        losing_trades = summary_data.get('losing_trades', 0)
        total_pnl = summary_data.get('total_pnl', 0.0)
        total_pnl_pct = summary_data.get('total_pnl_pct', 0.0)
        best_day = summary_data.get('best_day', {})
        worst_day = summary_data.get('worst_day', {})
        avg_daily_pnl = summary_data.get('avg_daily_pnl', 0.0)
        starting_equity = summary_data.get('starting_equity', 0.0)
        ending_equity = summary_data.get('ending_equity', 0.0)
        max_drawdown_pct = summary_data.get('max_drawdown_pct', 0.0)
        daily_breakdown = summary_data.get('daily_breakdown', [])

        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
        pnl_icon = '🟢' if total_pnl >= 0 else '🔴'
        trend_icon = '📈' if total_pnl >= 0 else '📉'

        msg = f"📊 *周报*\n"
        msg += f"━━━━━━━━━━━━━━━━━━\n"
        msg += f"📅 {week_start} ~ {week_end}\n"
        msg += f"\n💰 *盈亏*\n"
        msg += f"  {pnl_icon} ${total_pnl:+,.2f} ({total_pnl_pct:+.2f}%)\n"
        msg += f"  日均: {avg_daily_pnl:+.2f}%\n"
        msg += f"  最大回撤: {max_drawdown_pct:.2f}%\n"
        msg += f"\n📈 *交易*\n"
        msg += f"  总数: {total_trades} | 盈利: {winning_trades} | 亏损: {losing_trades}\n"
        msg += f"  胜率: {win_rate:.1f}%\n"
        msg += f"\n🏆 *最佳/最差*\n"
        msg += f"  最佳: {best_day.get('date', 'N/A')} ({best_day.get('pnl', 0):+.2f}%)\n"
        msg += f"  最差: {worst_day.get('date', 'N/A')} ({worst_day.get('pnl', 0):+.2f}%)\n"
        msg += f"\n💵 *余额*\n"
        change = ending_equity - starting_equity
        msg += f"  ${starting_equity:,.2f} → ${ending_equity:,.2f} ({trend_icon} ${change:+,.2f})\n"

        # Daily breakdown
        if daily_breakdown:
            msg += f"\n📋 *每日明细*\n"
            for day in daily_breakdown[:7]:
                d = day.get('date', 'N/A')[-5:]
                p = day.get('pnl', 0)
                icon = '🟢' if p >= 0 else '🔴'
                msg += f"  {icon} {d}: {p:+.2f}%\n"

        # v5.1: Trade evaluation stats
        eval_stats = summary_data.get('evaluation', {})
        if eval_stats and eval_stats.get('total_evaluated', 0) > 0:
            msg += f"\n🏅 *交易质量*\n"
            grades = eval_stats.get('grade_distribution', {})
            grade_str = " ".join(f"{g}:{c}" for g, c in sorted(grades.items()))
            msg += f"  评级: {grade_str}\n"
            msg += f"  方向准确率: {eval_stats.get('direction_accuracy', 0):.0f}%\n"
            avg_rr = eval_stats.get('avg_winning_rr', 0)
            if avg_rr > 0:
                msg += f"  平均盈利 R/R: {avg_rr:.1f}:1\n"
            # Confidence accuracy breakdown
            conf_stats = eval_stats.get('confidence_accuracy', {})
            if conf_stats:
                conf_parts = []
                for conf in ('HIGH', 'MEDIUM', 'LOW'):
                    s = conf_stats.get(conf)
                    if s and s.get('total', 0) > 0:
                        conf_cn = {'HIGH': '高', 'MEDIUM': '中', 'LOW': '低'}[conf]
                        conf_parts.append(f"{conf_cn}:{s['accuracy']:.0f}%({s['total']})")
                if conf_parts:
                    msg += f"  信心度: {' '.join(conf_parts)}\n"
            # v11.5: SL/TP optimization stats
            avg_mae = eval_stats.get('avg_mae_pct')
            avg_mfe = eval_stats.get('avg_mfe_pct')
            if avg_mae or avg_mfe:
                msg += f"  MAE/MFE: {avg_mae or 0:.1f}% / {avg_mfe or 0:.1f}%\n"
            ct_pct = eval_stats.get('counter_trend_pct')
            if ct_pct:
                msg += f"  逆势占比: {ct_pct:.0f}%\n"

        msg += f"\n⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
        return msg

    async def test_connection(self) -> bool:
        """
        Test Telegram bot connection.

        Returns
        -------
        bool
            True if connection successful, False otherwise
        """
        try:
            me = await self.bot.get_me()
            self.logger.info(f"✅ Connected to Telegram as @{me.username}")
            return True
        except Exception as e:
            self.logger.error(f"❌ Failed to connect to Telegram: {e}")
            return False

    # ==================== Command Response Formatters ====================

    def format_status_response(self, status_info: Dict[str, Any]) -> str:
        """
        Format strategy status response for /status command.

        Parameters
        ----------
        status_info : dict
            Status information
        """
        is_running = status_info.get('is_running', False)
        is_paused = status_info.get('is_paused', False)

        if not is_running:
            status_emoji, status_text = '🔴', '已停止'
        elif is_paused:
            status_emoji, status_text = '⏸️', '已暂停'
        else:
            status_emoji, status_text = '🟢', '运行中'

        msg = f"{status_emoji} *策略状态*\n"
        msg += "━━━━━━━━━━━━━━━━━━\n"
        msg += f"*状态*: {status_text}\n"
        msg += f"*交易对*: {self.escape_markdown(str(status_info.get('instrument_id', 'N/A')))}\n"
        current_price = status_info.get('current_price') or 0
        equity = status_info.get('equity') or 0
        msg += f"*价格*: ${current_price:,.2f}\n"
        msg += f"*余额*: ${equity:,.2f}\n"

        pnl = status_info.get('unrealized_pnl') or 0
        pnl_icon = self._pnl_icon(pnl)
        msg += f"*未实现 PnL*: {pnl_icon} ${pnl:,.2f}\n"

        # v5.0: SL/TP display (dynamic, from sltp_state or Binance)
        position_side = status_info.get('position_side')
        sl_price = status_info.get('sl_price')
        tp_price = status_info.get('tp_price')
        trailing_active = status_info.get('trailing_active', False)

        if position_side and (sl_price or tp_price):
            side_emoji = '🟢' if position_side == 'LONG' else '🔴'
            pos_cn = self.side_to_cn(position_side, 'position')
            msg += f"\n{side_emoji} *{pos_cn}*\n"
            if sl_price:
                sl_dist = abs(current_price - sl_price) / current_price * 100 if current_price > 0 else 0
                msg += f"  🛑 SL: ${sl_price:,.2f} ({sl_dist:.1f}%)\n"
            if tp_price:
                tp_dist = abs(tp_price - current_price) / current_price * 100 if current_price > 0 else 0
                msg += f"  🎯 TP: ${tp_price:,.2f} ({tp_dist:.1f}%)\n"
            if sl_price and tp_price and current_price > 0:
                risk = abs(current_price - sl_price)
                reward = abs(tp_price - current_price)
                if risk > 0:
                    rr = reward / risk
                    rr_icon = '✅' if rr >= 2.0 else '✓' if rr >= 1.5 else '⚠️'
                    msg += f"  📐 R/R: 1:{rr:.1f} {rr_icon}\n"
            if trailing_active:
                msg += f"  🔒 利润锁定已激活\n"

        msg += f"\n*上次信号*: {self.escape_markdown(str(status_info.get('last_signal', 'N/A')))}\n"
        msg += f"*信号时间*: {self.escape_markdown(str(status_info.get('last_signal_time', 'N/A')))}\n"
        msg += f"*运行时间*: {self.escape_markdown(str(status_info.get('uptime', 'N/A')))}\n"

        # Portfolio risk
        liq_buffer_min = status_info.get('liquidation_buffer_portfolio_min_pct')
        total_funding = status_info.get('total_daily_funding_cost_usd')
        can_add_safely = status_info.get('can_add_position_safely')

        if liq_buffer_min is not None or total_funding is not None:
            msg += f"\n⚠️ *账户风险*\n"
            if liq_buffer_min is not None:
                risk_icon = '🔴' if liq_buffer_min < 10 else '🟡' if liq_buffer_min < 15 else '🟢'
                msg += f"  爆仓距离: {risk_icon} {liq_buffer_min:.1f}%\n"
                if liq_buffer_min < 10:
                    msg += "  ⚠️ *高爆仓风险*\n"
            if total_funding is not None and total_funding > 0:
                msg += f"  日费率: ${total_funding:.2f}\n"

        # Account capacity
        used_margin = status_info.get('used_margin_pct')
        leverage = status_info.get('leverage')

        if used_margin is not None or leverage is not None:
            msg += f"\n📊 *账户*\n"
            if leverage is not None:
                msg += f"  杠杆: {leverage}x\n"
            if used_margin is not None:
                cap_icon = '🔴' if used_margin > 80 else '🟡' if used_margin > 60 else '🟢'
                msg += f"  已用保证金: {cap_icon} {used_margin:.1f}%\n"
            if can_add_safely is not None:
                safety_icon = '✅' if can_add_safely else '⚠️'
                safety_text = '可安全加仓' if can_add_safely else '谨慎'
                msg += f"  {safety_icon} {safety_text}\n"

        msg += f"\n⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
        return msg

    def format_position_response(self, position_info: Dict[str, Any]) -> str:
        """
        Format comprehensive position information for /position command.

        Parameters
        ----------
        position_info : dict
            Position information including v4.9 enhanced fields
        """
        if not position_info.get('has_position', False):
            return "ℹ️ *无持仓*\n\n当前无活跃持仓。"

        side = position_info.get('side', 'UNKNOWN')
        side_emoji = '🟢' if side == 'LONG' else '🔴' if side == 'SHORT' else '⚪'
        side_cn = self.side_to_cn(side, 'position')

        msg = f"{side_emoji} *{side_cn}*\n"
        msg += "━━━━━━━━━━━━━━━━━━\n"

        # Core position info
        quantity = position_info.get('quantity', 0)
        entry_price = position_info.get('entry_price', 0)
        current_price = position_info.get('current_price', 0)
        leverage = position_info.get('leverage')
        notional = position_info.get('notional_value')

        qty_notional = quantity * current_price if current_price > 0 else quantity * entry_price
        msg += f"*数量*: {quantity:.4f} BTC (${qty_notional:,.0f})\n"
        msg += f"*开仓价*: ${entry_price:,.2f}\n"
        msg += f"*当前价*: ${current_price:,.2f}\n"

        # v4.9: Notional value + leverage
        if notional:
            msg += f"*仓位价值*: ${notional:,.2f}"
            if leverage:
                msg += f" ({leverage}x)"
            msg += "\n"

        # P&L section
        pnl = position_info.get('unrealized_pnl', 0)
        pnl_pct = position_info.get('pnl_pct', 0)
        roe_pct = position_info.get('roe_pct')
        pnl_icon = self._pnl_icon(pnl)
        msg += f"\n{pnl_icon} *盈亏 (PnL)*: ${pnl:,.2f} ({pnl_pct:+.2f}%)\n"
        if roe_pct is not None:
            roe_icon = self._pnl_icon(roe_pct)
            msg += f"{roe_icon} *收益率 (ROE)*: {roe_pct:+.2f}%\n"

        # SL/TP with R/R ratio
        sl_price = position_info.get('sl_price')
        tp_price = position_info.get('tp_price')
        if sl_price or tp_price:
            msg += f"\n🎯 *止损/止盈*\n"
            if sl_price:
                sl_dist = abs(current_price - sl_price) / current_price * 100 if current_price > 0 else 0
                msg += f"  🛑 SL: ${sl_price:,.2f} (-{sl_dist:.1f}%)\n"
            if tp_price:
                tp_dist = abs(tp_price - current_price) / current_price * 100 if current_price > 0 else 0
                msg += f"  🎯 TP: ${tp_price:,.2f} (+{tp_dist:.1f}%)\n"
            # R/R ratio
            if sl_price and tp_price and current_price > 0:
                risk = abs(current_price - sl_price)
                reward = abs(tp_price - current_price)
                if risk > 0:
                    rr = reward / risk
                    msg += f"  📐 R/R: 1:{rr:.1f}\n"

        # v4.9: Trailing stop
        if position_info.get('trailing_active'):
            trailing_sl = position_info.get('trailing_sl', 0)
            trailing_peak = position_info.get('trailing_peak', 0)
            msg += f"\n📈 *移动止损*\n"
            msg += f"  SL: ${trailing_sl:,.2f}\n"
            if trailing_peak > 0:
                msg += f"  峰值: ${trailing_peak:,.2f}\n"

        # Liquidation risk
        liq_price = position_info.get('liquidation_price')
        liq_buffer = position_info.get('liquidation_buffer_pct')
        is_liq_risk_high = position_info.get('is_liquidation_risk_high', False)

        if liq_price is not None:
            msg += f"\n⚠️ *爆仓风险*\n"
            msg += f"  价格: ${liq_price:,.2f}\n"
            if liq_buffer is not None:
                risk_icon = '🔴' if is_liq_risk_high else '🟢'
                msg += f"  缓冲: {risk_icon} {liq_buffer:.1f}%\n"

        # v4.9: Margin info
        margin_used = position_info.get('margin_used_pct')
        available = position_info.get('available_balance')
        initial_margin = position_info.get('initial_margin')
        if margin_used is not None or available is not None:
            msg += f"\n💳 *保证金*\n"
            if initial_margin:
                msg += f"  已用: ${initial_margin:,.2f}\n"
            if available is not None:
                msg += f"  可用: ${available:,.2f}\n"
            if margin_used is not None:
                cap_icon = '🔴' if margin_used > 80 else '🟡' if margin_used > 60 else '🟢'
                msg += f"  占用率: {cap_icon} {margin_used:.1f}%\n"

        # Funding rate
        funding_rate = position_info.get('funding_rate_current')
        daily_cost = position_info.get('daily_funding_cost_usd')
        cumulative_funding = position_info.get('funding_rate_cumulative_usd')
        effective_pnl = position_info.get('effective_pnl_after_funding')

        if funding_rate is not None:
            msg += f"\n💰 *资金费率*\n"
            fr_pct = funding_rate * 100
            fr_icon = '🔴' if fr_pct > 0.01 else '🟢' if fr_pct < -0.01 else '⚪'
            msg += f"  费率: {fr_icon} {fr_pct:.4f}%/8h\n"
            if daily_cost is not None:
                msg += f"  日费用: ${daily_cost:.2f}\n"
            if cumulative_funding is not None and cumulative_funding != 0:
                cum_icon = '🔴' if cumulative_funding > 0 else '🟢'
                msg += f"  累计: {cum_icon} ${cumulative_funding:+.2f}\n"
            if effective_pnl is not None:
                eff_icon = self._pnl_icon(effective_pnl)
                msg += f"  扣费后盈亏: {eff_icon} ${effective_pnl:,.2f}\n"

        # Drawdown
        max_dd = position_info.get('max_drawdown_pct')
        peak_pnl = position_info.get('peak_pnl_pct')
        if max_dd is not None and max_dd > 0:
            msg += f"\n📊 *回撤*\n"
            if peak_pnl:
                msg += f"  峰值: {peak_pnl:+.2f}%\n"
            msg += f"  最大回撤: -{max_dd:.2f}%\n"

        # Duration and confidence
        duration = position_info.get('duration_minutes')
        confidence = position_info.get('entry_confidence')
        if duration is not None or confidence:
            msg += "\n"
            if duration is not None:
                hours = duration // 60
                mins = duration % 60
                msg += f"⏱ 持仓时长: {int(hours)}h {int(mins)}m\n"
            if confidence:
                conf_cn = {'HIGH': '高', 'MEDIUM': '中', 'LOW': '低'}.get(confidence, confidence)
                msg += f"📊 信心度: {conf_cn}\n"

        msg += f"\n⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
        return msg

    def format_scaling_notification(self, scaling_info: Dict[str, Any]) -> str:
        """
        Format position scaling (add/reduce) notification.

        Parameters
        ----------
        scaling_info : dict
            Scaling information with action, side, qty changes, reasoning, etc.
        """
        action = scaling_info.get('action', 'SCALE')
        side = scaling_info.get('side', 'UNKNOWN')
        side_cn = self.side_to_cn(side, 'side')

        is_long = side.upper() in ('LONG', 'BUY')
        if action == 'ADD':
            emoji = '📈' if is_long else '📉'
            action_cn = '加仓'
        else:
            emoji = '📉' if is_long else '📈'
            action_cn = '减仓'

        old_qty = scaling_info.get('old_qty', 0)
        new_qty = scaling_info.get('new_qty', 0)
        change_qty = scaling_info.get('change_qty', 0)
        current_price = scaling_info.get('current_price', 0)

        confidence = scaling_info.get('confidence', '')
        confidence_icon = {'HIGH': '🟢', 'MEDIUM': '🟡', 'LOW': '🔴'}.get(confidence, '')
        confidence_str = f" | {confidence_icon} {confidence}" if confidence else ''
        msg = f"{emoji} *{action_cn} — {side_cn}{confidence_str}*\n"
        msg += "━━━━━━━━━━━━━━━━━━\n"
        change_value = abs(change_qty) * current_price if current_price > 0 else 0
        new_value = new_qty * current_price if current_price > 0 else 0
        msg += f"变化: {'+' if action == 'ADD' else '-'}{abs(change_qty):.4f} BTC (${change_value:,.0f})\n"
        msg += f"仓位: {old_qty:.4f} → {new_qty:.4f} BTC (${new_value:,.0f})\n"

        if current_price > 0:
            new_notional = new_qty * current_price
            msg += f"价格: ${current_price:,.2f}\n"
            msg += f"仓位价值: ${new_notional:,.2f}\n"

        # P&L if available
        pnl = scaling_info.get('unrealized_pnl')
        if pnl is not None:
            pnl_icon = self._pnl_icon(pnl)
            msg += f"P&L: {pnl_icon} ${pnl:,.2f}\n"

        # SL/TP with percentage (consistent with opening message format)
        sl_price = scaling_info.get('sl_price')
        tp_price = scaling_info.get('tp_price')
        if sl_price or tp_price:
            if sl_price:
                sl_pct = ((sl_price / current_price) - 1) * 100 if current_price > 0 else 0
                msg += f"🛑 止损 ${sl_price:,.2f} ({sl_pct:+.2f}%)\n"
            if tp_price:
                tp_pct = ((tp_price / current_price) - 1) * 100 if current_price > 0 else 0
                msg += f"🎯 止盈 ${tp_price:,.2f} ({tp_pct:+.2f}%)\n"
            # R/R
            if sl_price and tp_price and current_price > 0:
                if is_long:
                    sl_d = current_price - sl_price
                    tp_d = tp_price - current_price
                else:
                    sl_d = sl_price - current_price
                    tp_d = current_price - tp_price
                if sl_d > 0 and tp_d > 0:
                    rr = tp_d / sl_d
                    rr_icon = '✅' if rr >= 2.0 else '✓' if rr >= 1.5 else '⚠️'
                    msg += f"📊 R/R {rr:.1f}:1 {rr_icon}\n"

        # Mechanical reasoning — full content, _split_message() handles Telegram 4096 limit
        reasoning = scaling_info.get('reasoning', '')
        if reasoning:
            safe = self.escape_markdown(reasoning)
            msg += f"\n📐 {safe}\n"

        msg += f"\n⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
        return msg

    def format_pause_response(self, success: bool, message: str = "") -> str:
        """Format response for /pause command."""
        ts = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        if success:
            return f"⏸️ *交易已暂停*\n\n不会下新单。\n使用 /resume 恢复。\n\n⏰ {ts} UTC"
        return f"❌ *暂停失败*\n\n{message}\n\n⏰ {ts} UTC"

    def format_resume_response(self, success: bool, message: str = "") -> str:
        """Format response for /resume command."""
        ts = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        if success:
            return f"▶️ *交易已恢复*\n\n策略已激活。\n\n⏰ {ts} UTC"
        return f"❌ *恢复失败*\n\n{message}\n\n⏰ {ts} UTC"


# Convenience function for quick testing
async def test_telegram_bot(token: str, chat_id: str) -> bool:
    """
    Quick test function for Telegram bot.

    Parameters
    ----------
    token : str
        Bot token from @BotFather
    chat_id : str
        Chat ID to send test message to

    Returns
    -------
    bool
        True if test successful
    """
    try:
        bot = TelegramBot(token=token, chat_id=chat_id)

        # Test connection
        if not await bot.test_connection():
            return False

        # Send test message
        success = await bot.send_message(
            "🧪 *Test Message*\n\n"
            "Telegram bot is working correctly!\n"
            "Ready to send trading notifications."
        )

        return success

    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False


if __name__ == "__main__":
    """
    Standalone test mode.

    Usage:
        python telegram_bot.py <token> <chat_id>
    """
    import sys

    if len(sys.argv) != 3:
        print("Usage: python telegram_bot.py <token> <chat_id>")
        sys.exit(1)

    token = sys.argv[1]
    chat_id = sys.argv[2]

    # Run test
    result = asyncio.run(test_telegram_bot(token, chat_id))

    if result:
        print("✅ Test successful!")
        sys.exit(0)
    else:
        print("❌ Test failed!")
        sys.exit(1)
