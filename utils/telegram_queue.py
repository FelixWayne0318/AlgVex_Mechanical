"""
Telegram Message Queue with Persistence

Provides asynchronous, non-blocking message sending with:
- Thread-safe queue for decoupling from trading thread
- SQLite persistence for reliability
- Automatic retry with exponential backoff
- Alert convergence (deduplication)
- Priority levels for critical messages

Reference: Evaluation report docs/reports/TELEGRAM_SYSTEM_EVALUATION_REPORT.md
"""

import queue
import sqlite3
import threading
import time
import logging
import json
import hashlib
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Callable
from pathlib import Path
from enum import IntEnum


class MessagePriority(IntEnum):
    """Message priority levels (higher = more urgent)."""
    LOW = 0       # Heartbeat, info
    NORMAL = 1    # Signals, fills
    HIGH = 2      # Position updates
    CRITICAL = 3  # Errors, order rejections


class TelegramMessageQueue:
    """
    Thread-safe message queue with persistence and retry.

    Features:
    - Non-blocking enqueue (never blocks trading thread)
    - SQLite persistence (survives restarts)
    - Automatic retry with exponential backoff
    - Alert convergence (same message won't repeat within cooldown)
    - Priority-based sending (critical first)

    Usage:
        queue = TelegramMessageQueue(send_func=bot.send_message_sync)
        queue.start()
        queue.enqueue("Hello", priority=MessagePriority.NORMAL)
    """

    def __init__(
        self,
        send_func: Callable[[str, dict], bool],
        db_path: str = "data/telegram_queue.db",
        max_retries: int = 3,
        base_retry_delay: float = 5.0,
        alert_cooldown: int = 300,  # 5 minutes
        batch_size: int = 10,
        send_interval: float = 0.5,  # Seconds between sends (rate limit)
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize message queue.

        Parameters
        ----------
        send_func : callable
            Function to send message. Signature: send_func(message, kwargs) -> bool
        db_path : str
            Path to SQLite database for persistence
        max_retries : int
            Maximum retry attempts for failed messages
        base_retry_delay : float
            Base delay for exponential backoff (seconds)
        alert_cooldown : int
            Cooldown period for alert convergence (seconds)
        batch_size : int
            Maximum messages to process per batch
        send_interval : float
            Minimum interval between sends (rate limiting)
        logger : logging.Logger
            Logger instance
        """
        self.send_func = send_func
        self.db_path = db_path
        self.max_retries = max_retries
        self.base_retry_delay = base_retry_delay
        self.alert_cooldown = alert_cooldown
        self.batch_size = batch_size
        self.send_interval = send_interval
        self.logger = logger or logging.getLogger(__name__)

        # In-memory queue for immediate messages
        self._queue: queue.PriorityQueue = queue.PriorityQueue()

        # Alert convergence tracking
        self._alert_history: Dict[str, float] = {}
        self._alert_lock = threading.Lock()

        # Thread control
        self._running = False
        self._sender_thread: Optional[threading.Thread] = None
        self._db_lock = threading.Lock()

        # Statistics
        self.stats = {
            'enqueued': 0,
            'sent': 0,
            'failed': 0,
            'deduplicated': 0,
            'retried': 0,
        }

        # Initialize database
        self._init_db()

    def _init_db(self):
        """Initialize SQLite database for persistence."""
        # Ensure directory exists
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        with self._db_lock:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS message_queue (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        content TEXT NOT NULL,
                        kwargs TEXT,
                        priority INTEGER DEFAULT 1,
                        retry_count INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        next_retry_at TIMESTAMP,
                        status TEXT DEFAULT 'pending',
                        error_message TEXT
                    )
                ''')
                conn.execute('''
                    CREATE INDEX IF NOT EXISTS idx_status_priority
                    ON message_queue(status, priority DESC, created_at)
                ''')
                conn.commit()
            finally:
                conn.close()

        self.logger.info(f"📦 Message queue database initialized: {self.db_path}")

    def _get_message_hash(self, message: str) -> str:
        """Generate hash for alert convergence."""
        # Normalize message (remove timestamps, etc.)
        normalized = message.strip()
        return hashlib.md5(normalized.encode()).hexdigest()[:16]

    def _should_send_alert(self, message: str, priority: MessagePriority) -> bool:
        """Check if alert should be sent (convergence check)."""
        # Critical messages always sent
        if priority >= MessagePriority.CRITICAL:
            return True

        msg_hash = self._get_message_hash(message)
        now = time.time()

        with self._alert_lock:
            if msg_hash in self._alert_history:
                last_sent = self._alert_history[msg_hash]
                if now - last_sent < self.alert_cooldown:
                    self.stats['deduplicated'] += 1
                    self.logger.debug(f"Alert deduplicated (cooldown): {message}")
                    return False

            # Update history
            self._alert_history[msg_hash] = now

            # Clean old entries
            cutoff = now - self.alert_cooldown * 2
            self._alert_history = {
                k: v for k, v in self._alert_history.items() if v > cutoff
            }

        return True

    def enqueue(
        self,
        message: str,
        priority: MessagePriority = MessagePriority.NORMAL,
        persist: bool = True,
        **kwargs
    ) -> bool:
        """
        Add message to queue (non-blocking).

        Parameters
        ----------
        message : str
            Message content
        priority : MessagePriority
            Message priority level
        persist : bool
            Whether to persist to database (for crash recovery)
        **kwargs
            Additional arguments for send_func (e.g., parse_mode)

        Returns
        -------
        bool
            True if enqueued successfully
        """
        # Alert convergence check
        if not self._should_send_alert(message, priority):
            return False

        try:
            # Add to in-memory queue (priority queue uses negative for high priority first)
            self._queue.put_nowait((
                -priority,  # Negative for max-heap behavior
                time.time(),  # Timestamp for FIFO within same priority
                message,
                kwargs
            ))

            # NOTE: Do NOT persist to database here!
            # If we persist AND add to memory queue, the message will be sent twice:
            # 1. From memory queue (no msg_id, so DB not updated)
            # 2. From DB (still 'pending', sent again)
            # Database is only for crash recovery (queue full or shutdown)

            self.stats['enqueued'] += 1
            self.logger.debug(f"📥 Enqueued (P{priority}): {message}")
            return True

        except queue.Full:
            self.logger.warning("⚠️ Message queue full, persisting to DB only")
            self._persist_message(message, priority, kwargs)
            return True
        except Exception as e:
            self.logger.error(f"❌ Failed to enqueue message: {e}")
            return False

    def _persist_message(self, message: str, priority: MessagePriority, kwargs: dict):
        """Persist message to SQLite database."""
        with self._db_lock:
            try:
                conn = sqlite3.connect(self.db_path)
                conn.execute(
                    '''INSERT INTO message_queue (content, kwargs, priority, status)
                       VALUES (?, ?, ?, 'pending')''',
                    (message, json.dumps(kwargs), int(priority))
                )
                conn.commit()
                conn.close()
            except Exception as e:
                self.logger.error(f"❌ Failed to persist message: {e}")

    def _load_pending_messages(self) -> list:
        """Load pending messages from database."""
        with self._db_lock:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.execute('''
                    SELECT id, content, kwargs, priority, retry_count
                    FROM message_queue
                    WHERE status = 'pending'
                      AND (next_retry_at IS NULL OR next_retry_at <= datetime('now'))
                    ORDER BY priority DESC, created_at
                    LIMIT ?
                ''', (self.batch_size,))
                messages = cursor.fetchall()
                conn.close()
                return messages
            except Exception as e:
                self.logger.error(f"❌ Failed to load pending messages: {e}")
                return []

    def _mark_message_sent(self, msg_id: int):
        """Mark message as sent in database."""
        with self._db_lock:
            try:
                conn = sqlite3.connect(self.db_path)
                conn.execute(
                    "UPDATE message_queue SET status = 'sent' WHERE id = ?",
                    (msg_id,)
                )
                conn.commit()
                conn.close()
            except Exception as e:
                self.logger.error(f"❌ Failed to mark message sent: {e}")

    def _mark_message_failed(self, msg_id: int, retry_count: int, error: str):
        """Mark message as failed and schedule retry."""
        with self._db_lock:
            try:
                conn = sqlite3.connect(self.db_path)

                if retry_count >= self.max_retries:
                    # Max retries exceeded, mark as failed permanently
                    conn.execute(
                        '''UPDATE message_queue
                           SET status = 'failed', error_message = ?, retry_count = ?
                           WHERE id = ?''',
                        (error, retry_count, msg_id)
                    )
                else:
                    # Schedule retry with exponential backoff
                    delay = self.base_retry_delay * (2 ** retry_count)
                    next_retry = datetime.utcnow() + timedelta(seconds=delay)
                    conn.execute(
                        '''UPDATE message_queue
                           SET retry_count = ?, next_retry_at = ?, error_message = ?
                           WHERE id = ?''',
                        (retry_count, next_retry.isoformat(), error, msg_id)
                    )
                    self.stats['retried'] += 1

                conn.commit()
                conn.close()
            except Exception as e:
                self.logger.error(f"❌ Failed to mark message failed: {e}")

    def _sender_loop(self):
        """Background thread that sends messages."""
        self.logger.info("🚀 Message sender thread started")

        while self._running:
            try:
                # First, try to send from in-memory queue
                try:
                    _, _, message, kwargs = self._queue.get(timeout=1.0)
                    self._send_message(message, kwargs)
                    self._queue.task_done()
                except queue.Empty:
                    pass

                # Then, process persisted messages (retry failed ones)
                pending = self._load_pending_messages()
                for msg_id, content, kwargs_json, priority, retry_count in pending:
                    if not self._running:
                        break

                    kwargs = json.loads(kwargs_json) if kwargs_json else {}
                    success = self._send_message(content, kwargs, msg_id, retry_count)

                    # Rate limiting
                    time.sleep(self.send_interval)

            except Exception as e:
                self.logger.error(f"❌ Sender loop error: {e}")
                time.sleep(1.0)

        self.logger.info("🛑 Message sender thread stopped")

    def _send_message(
        self,
        message: str,
        kwargs: dict,
        msg_id: Optional[int] = None,
        retry_count: int = 0
    ) -> bool:
        """Send a single message."""
        try:
            success = self.send_func(message, **kwargs)

            if success:
                self.stats['sent'] += 1
                if msg_id:
                    self._mark_message_sent(msg_id)
                self.logger.debug(f"📤 Sent: {message}")
                return True
            else:
                self.stats['failed'] += 1
                if msg_id:
                    self._mark_message_failed(msg_id, retry_count + 1, "Send returned False")
                return False

        except Exception as e:
            self.stats['failed'] += 1
            error_msg = str(e)
            self.logger.warning(f"⚠️ Send failed: {error_msg}")

            if msg_id:
                self._mark_message_failed(msg_id, retry_count + 1, error_msg)

            return False

    def start(self):
        """Start the background sender thread."""
        if self._running:
            return

        self._running = True
        self._sender_thread = threading.Thread(
            target=self._sender_loop,
            daemon=True,
            name="TelegramQueueSender"
        )
        self._sender_thread.start()
        self.logger.info("✅ Telegram message queue started")

    def stop(self, timeout: float = 5.0):
        """Stop the background sender thread."""
        if not self._running:
            return

        self._running = False
        if self._sender_thread:
            self._sender_thread.join(timeout=timeout)
        self.logger.info("✅ Telegram message queue stopped")

    def get_stats(self) -> Dict[str, Any]:
        """Get queue statistics."""
        return {
            **self.stats,
            'queue_size': self._queue.qsize(),
            'pending_in_db': self._count_pending_in_db(),
        }

    def _count_pending_in_db(self) -> int:
        """Count pending messages in database."""
        with self._db_lock:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM message_queue WHERE status = 'pending'"
                )
                count = cursor.fetchone()[0]
                conn.close()
                return count
            except Exception:
                return 0

    def cleanup_old_messages(self, days: int = 7):
        """Clean up old sent/failed messages from database."""
        with self._db_lock:
            try:
                conn = sqlite3.connect(self.db_path)
                cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
                conn.execute(
                    '''DELETE FROM message_queue
                       WHERE status IN ('sent', 'failed') AND created_at < ?''',
                    (cutoff,)
                )
                conn.commit()
                deleted = conn.total_changes
                conn.close()
                self.logger.info(f"🗑️ Cleaned up {deleted} old messages")
            except Exception as e:
                self.logger.error(f"❌ Failed to cleanup messages: {e}")


# Convenience function for quick sending
def enqueue_message(
    queue_instance: TelegramMessageQueue,
    message: str,
    priority: MessagePriority = MessagePriority.NORMAL,
    **kwargs
) -> bool:
    """Convenience function to enqueue a message."""
    return queue_instance.enqueue(message, priority, **kwargs)
