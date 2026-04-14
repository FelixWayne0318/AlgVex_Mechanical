"""
AI Trading Strategy for NautilusTrader

AI-powered cryptocurrency trading strategy using TradingAgents-inspired architecture:
- Bull/Bear Analyst Debate: Two opposing AI agents argue market direction
- Judge (Portfolio Manager): Evaluates debate and makes final decision
- Entry Timing Agent: Evaluates optimal entry timing (v23.0)
- Risk Manager: Determines position sizing and risk assessment

This implements a hierarchical decision architecture where the Judge's decision is final,
avoiding signal conflicts that can occur with parallel multi-agent systems.

The strategy class is split into mixins for code organization:
- OrderExecutionMixin: Trade execution pipeline
- SafetyManagerMixin: Emergency SL/TP and safety logic
- PositionManagerMixin: Layer system, pyramiding, cooldown
- EventHandlersMixin: NautilusTrader event callbacks
- TelegramCommandsMixin: Telegram bot command handlers

Reference: TradingAgents (UCLA/MIT) - https://github.com/TauricResearch/TradingAgents
"""

import os
import math
import re
import json
import asyncio
import time
import threading
import requests
from typing import Dict, Any, List, Optional, Tuple

from nautilus_trader.config import StrategyConfig
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import PositionSide, PriceType, OrderType, OrderSide, TriggerType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.position import Position
from datetime import datetime, timedelta, timezone

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from indicators.technical_manager import TechnicalIndicatorManager

from utils.sentiment_client import SentimentDataFetcher
from utils.binance_account import BinanceAccountFetcher
from agents.multi_agent_analyzer import MultiAgentAnalyzer
# Order Flow and Derivatives clients (MTF v2.1)
from utils.binance_kline_client import BinanceKlineClient
from utils.order_flow_processor import OrderFlowProcessor
from utils.coinalyze_client import CoinalyzeClient
from utils.binance_orderbook_client import BinanceOrderBookClient
from utils.orderbook_processor import OrderBookProcessor
from utils.binance_derivatives_client import BinanceDerivativesClient
from utils.ai_data_assembler import AIDataAssembler
from strategy.trading_logic import (
    calculate_position_size,
    get_min_rr_ratio,
    get_counter_trend_rr_multiplier,
    _is_counter_trend,
    calculate_mechanical_sltp,
    get_default_sl_pct,
    get_default_tp_pct_buy,
    get_time_barrier_config,
    get_min_notional_usdt,
    get_min_notional_safety_margin,
    evaluate_trade,
    get_evaluation_summary,
)
from utils.risk_controller import RiskController, TradingState

# Strategy mixins (code organization)
from strategy.order_execution import OrderExecutionMixin
from strategy.safety_manager import SafetyManagerMixin
from strategy.position_manager import PositionManagerMixin
from strategy.event_handlers import EventHandlersMixin
from strategy.telegram_commands import TelegramCommandsMixin


class AITradingStrategyConfig(StrategyConfig, frozen=True):
    """Configuration for AI Trading Strategy."""

    # Instrument
    instrument_id: str
    bar_type: str

    # Capital
    equity: float = 1000.0  # Fallback when real balance unavailable
    leverage: float = 5.0   # Leverage multiplier (recommended 3-10)
    use_real_balance_as_equity: bool = True  # Auto-fetch real balance from Binance as equity

    # Position sizing
    base_usdt_amount: float = 100.0
    high_confidence_multiplier: float = 1.5
    medium_confidence_multiplier: float = 1.0
    low_confidence_multiplier: float = 0.5
    max_position_ratio: float = 0.12  # Max position ratio (matches base.yaml)
    trend_strength_multiplier: float = 1.2
    min_trade_amount: float = 0.001

    # v4.8: Position sizing method configuration
    position_sizing_method: str = "ai_controlled"  # fixed_pct | atr_based | ai_controlled | hybrid_atr_ai
    position_sizing_default_pct: float = 50.0  # Default % when AI doesn't specify
    position_sizing_high_pct: float = 80.0     # HIGH confidence position %
    position_sizing_medium_pct: float = 50.0   # MEDIUM confidence position %
    position_sizing_low_pct: float = 30.0      # LOW confidence position %
    position_sizing_cumulative: bool = True    # Cumulative mode: allow pyramiding (v4.8)

    # Technical indicators
    sma_periods: Tuple[int, ...] = (5, 20, 50)
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    bb_period: int = 20
    bb_std: float = 2.0
    volume_ma_period: int = 20  # Volume MA period for analysis
    support_resistance_lookback: int = 20  # Support/resistance lookback period

    # v3.0: S/R Zone Calculator config (from configs/base.yaml sr_zones section)
    sr_zones_config: Dict = None  # type: ignore  # Passed as dict to MultiAgentAnalyzer

    # v10.0: Strategy mode (mechanical only since v46.0)
    strategy_mode: str = "mechanical"

    # Sentiment
    sentiment_enabled: bool = True
    sentiment_lookback_hours: int = 4
    sentiment_timeframe: str = "30m"  # Sentiment data timeframe (should match execution layer)

    # Risk management
    min_confidence_to_trade: str = "LOW"
    allow_reversals: bool = True
    require_high_confidence_for_reversal: bool = False
    rsi_extreme_threshold_upper: float = 70.0
    rsi_extreme_threshold_lower: float = 30.0
    rsi_extreme_multiplier: float = 0.7

    # Stop Loss & Take Profit
    enable_auto_sl_tp: bool = True
    tp_high_confidence_pct: float = 0.03
    tp_medium_confidence_pct: float = 0.02
    tp_low_confidence_pct: float = 0.01
    
    # OCO (One-Cancels-the-Other) - now handled by NautilusTrader bracket orders
    enable_oco: bool = True  # Controls orphan order cleanup (bracket orders handle OCO automatically)

    # Telegram Notifications
    enable_telegram: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    # v14.0: Notification group (separate bot for public signal broadcasting)
    telegram_notification_bot_token: str = ""
    telegram_notification_chat_id: str = ""
    telegram_notify_signals: bool = True
    telegram_notify_fills: bool = True
    telegram_notify_positions: bool = True
    telegram_notify_errors: bool = True
    telegram_notify_heartbeat: bool = True  # v2.1: Send heartbeat on each on_timer
    telegram_notify_sltp_update: bool = True  # v5.0: SL/TP update notifications
    telegram_notify_startup: bool = True  # v3.13: Strategy startup notification
    telegram_notify_shutdown: bool = True  # v3.13: Strategy shutdown notification
    telegram_auto_daily: bool = False  # v3.13: Auto-send daily summary
    telegram_auto_weekly: bool = False  # v3.13: Auto-send weekly summary
    telegram_daily_hour_utc: int = 0  # v3.13: Daily summary send hour (UTC)
    telegram_weekly_day: int = 0  # v3.13: Weekly summary send day (0=Monday)

    # Telegram Queue (v4.0 - Non-blocking message sending)
    telegram_queue_enabled: bool = True  # Enable message queue (default on)
    telegram_queue_db_path: str = "data/telegram_queue.db"  # SQLite persistence path
    telegram_queue_max_retries: int = 3  # Max retry count
    telegram_queue_alert_cooldown: int = 300  # Alert cooldown period (seconds)
    telegram_queue_send_interval: float = 0.5  # Send interval (seconds)

    # Telegram Security (v4.0 - Enhanced authentication)
    telegram_security_enable_pin: bool = True  # Enable PIN verification
    telegram_security_pin_code: str = ""  # PIN code (empty = auto-generate)
    telegram_security_pin_expiry_seconds: int = 60  # PIN expiry time (seconds)
    telegram_security_rate_limit_per_minute: int = 30  # Rate limit per minute
    telegram_security_enable_audit: bool = True  # Enable audit logging
    telegram_security_audit_log_dir: str = "logs/audit"  # Audit log directory

    # Execution
    position_adjustment_threshold: float = 0.001

    # Timing
    timer_interval_sec: int = 1200

    # Network configuration
    network_telegram_startup_delay: float = 5.0
    network_telegram_polling_max_retries: int = 3
    network_telegram_polling_base_delay: float = 10.0
    network_binance_recv_window: int = 5000
    network_binance_balance_cache_ttl: float = 5.0
    network_bar_persistence_max_limit: int = 1500
    network_bar_persistence_timeout: float = 10.0
    network_instrument_discovery_max_retries: int = 60  # Instrument discovery max retries
    network_instrument_discovery_retry_interval: float = 1.0  # Instrument discovery retry interval (seconds)
    network_binance_api_timeout: float = 10.0  # Binance API timeout (seconds)
    network_telegram_message_timeout: float = 30.0  # Telegram message send timeout (seconds)
    network_telegram_api_timeout: float = 30.0  # Telegram command handler API timeout (connect/read/write)
    sentiment_timeout: float = 10.0

    # Multi-Timeframe Configuration (v3.3)
    multi_timeframe_enabled: bool = False  # Default disabled for backward compatibility
    mtf_trend_sma_period: int = 200        # SMA period for trend layer (1D)

    # v6.0: Cooldown configuration (post-stop cooldown)
    cooldown_enabled: bool = True
    cooldown_per_stoploss_candles: int = 2
    cooldown_noise_stop_candles: int = 1
    cooldown_reversal_stop_candles: int = 6
    cooldown_volatility_stop_candles: int = 12
    cooldown_detection_candles: int = 2

    # v6.0: Pyramiding configuration
    pyramiding_enabled: bool = True
    pyramiding_layer_sizes: Tuple[float, ...] = (0.50, 0.30, 0.20)
    pyramiding_min_profit_atr: float = 1.0
    pyramiding_min_confidence: str = "HIGH"
    pyramiding_counter_trend_allowed: bool = False
    pyramiding_max_funding_rate: float = 0.0003
    pyramiding_min_adx: float = 25.0

    # v3.12: Risk Circuit Breakers configuration (passed as dict from ConfigManager)
    risk_config: Dict = None  # type: ignore  # risk.circuit_breakers section from base.yaml

    # v2.0: Upgrade plan component configs (passed as dicts from ConfigManager)
    hmm_config: Dict = None  # type: ignore  # hmm section from base.yaml
    kelly_config: Dict = None  # type: ignore  # kelly section from base.yaml
    risk_regime_config: Dict = None  # type: ignore  # risk_regime section from base.yaml
    prometheus_config: Dict = None  # type: ignore  # prometheus section from base.yaml

    # Order Book Configuration (v3.7)
    order_book_enabled: bool = False  # Enable orderbook depth data (default off)
    order_book_api_timeout: float = 10.0  # API timeout (seconds)
    order_book_api_max_retries: int = 2  # Max retry count
    order_book_api_retry_delay: float = 1.0  # Retry delay (seconds)
    order_book_price_band_pct: float = 0.5  # Price band % (depth distribution)
    order_book_anomaly_threshold: float = 3.0  # Anomaly detection threshold (multiplier)
    order_book_slippage_amounts: Tuple[float, ...] = (0.1, 0.5, 1.0)  # Slippage estimation amounts (BTC)
    order_book_weighted_decay: float = 0.8  # Weighted OBI decay factor
    order_book_adaptive_decay: bool = True  # Enable adaptive decay (volatility-based)
    order_book_history_size: int = 10  # History cache size (for change rate calculation)

    # v15.0: Previously hardcoded values extracted to config
    emergency_sl_base_pct: float = 0.02       # Emergency SL min distance (2%)
    emergency_sl_atr_multiplier: float = 1.5  # Dynamic: max(base_pct, ATR × this)
    emergency_sl_cooldown_seconds: int = 120  # Emergency SL retry cooldown (seconds)
    emergency_sl_max_consecutive: int = 3     # Max consecutive retries
    sr_zones_cache_ttl_seconds: int = 1800    # S/R zones cache TTL (seconds)
    price_cache_ttl_seconds: int = 300        # Price cache max TTL (seconds)
    reversal_timeout_seconds: int = 300       # Reversal Phase 1→2 timeout (seconds)
    max_leverage_limit: int = 125             # Binance USDM max leverage


class AITradingStrategy(
    TelegramCommandsMixin,
    EventHandlersMixin,
    OrderExecutionMixin,
    PositionManagerMixin,
    SafetyManagerMixin,
    Strategy,
):
    """
    AI-powered trading strategy for Binance Futures.

    Combines AI decision making, technical analysis, and sentiment data
    for intelligent cryptocurrency trading. Uses a mixin architecture
    for code organization (see strategy/ submodules).
    """

    def __init__(self, config: AITradingStrategyConfig):
        """
        Initialize DeepSeek AI strategy.

        Parameters
        ----------
        config : AITradingStrategyConfig
            Strategy configuration
        """
        super().__init__(config)

        # Configuration
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)

        # Position sizing config
        self.equity = config.equity
        self._equity_synced = False  # v15.1: True after first successful Binance balance fetch
        self.leverage = config.leverage
        self.base_usdt = config.base_usdt_amount
        self.position_config = {
            'high_confidence_multiplier': config.high_confidence_multiplier,
            'medium_confidence_multiplier': config.medium_confidence_multiplier,
            'low_confidence_multiplier': config.low_confidence_multiplier,
            'max_position_ratio': config.max_position_ratio,
            'trend_strength_multiplier': config.trend_strength_multiplier,
            'min_trade_amount': config.min_trade_amount,
            'adjustment_threshold': config.position_adjustment_threshold,
        }

        # v4.8: Position sizing configuration
        self.position_sizing_config = {
            'method': config.position_sizing_method,
            'ai_controlled': {
                'default_size_pct': config.position_sizing_default_pct,
                'confidence_mapping': {
                    'HIGH': config.position_sizing_high_pct,
                    'MEDIUM': config.position_sizing_medium_pct,
                    'LOW': config.position_sizing_low_pct,
                }
            }
        }
        self.position_sizing_cumulative = config.position_sizing_cumulative

        # v6.0: Cooldown configuration
        self.cooldown_enabled = config.cooldown_enabled
        self.cooldown_per_stoploss_candles = config.cooldown_per_stoploss_candles
        self.cooldown_noise_stop_candles = config.cooldown_noise_stop_candles
        self.cooldown_reversal_stop_candles = config.cooldown_reversal_stop_candles
        self.cooldown_volatility_stop_candles = config.cooldown_volatility_stop_candles
        self.cooldown_detection_candles = config.cooldown_detection_candles

        # v6.0: Pyramiding configuration
        self.pyramiding_enabled = config.pyramiding_enabled
        self.pyramiding_layer_sizes = config.pyramiding_layer_sizes
        self.pyramiding_min_profit_atr = config.pyramiding_min_profit_atr
        self.pyramiding_min_confidence = config.pyramiding_min_confidence
        self.pyramiding_counter_trend_allowed = config.pyramiding_counter_trend_allowed
        self.pyramiding_max_funding_rate = config.pyramiding_max_funding_rate
        self.pyramiding_min_adx = config.pyramiding_min_adx

        # Risk management
        self.min_confidence = config.min_confidence_to_trade
        self.allow_reversals = config.allow_reversals
        self.require_high_conf_reversal = config.require_high_confidence_for_reversal
        self.rsi_extreme_upper = config.rsi_extreme_threshold_upper
        self.rsi_extreme_lower = config.rsi_extreme_threshold_lower
        self.rsi_extreme_mult = config.rsi_extreme_multiplier

        # Stop Loss & Take Profit
        self.enable_auto_sl_tp = config.enable_auto_sl_tp
        self.tp_pct_config = {
            'HIGH': config.tp_high_confidence_pct,
            'MEDIUM': config.tp_medium_confidence_pct,
            'LOW': config.tp_low_confidence_pct,
        }
        
        # Store latest signal, technical, and price data for SL/TP calculation
        self.latest_signal_data: Optional[Dict[str, Any]] = None
        self.latest_technical_data: Optional[Dict[str, Any]] = None
        self.latest_price_data: Optional[Dict[str, Any]] = None

        # v3.6/3.7/3.8: Store latest indicator data for Telegram heartbeat
        self.latest_order_flow_data: Optional[Dict[str, Any]] = None
        self.latest_derivatives_data: Optional[Dict[str, Any]] = None
        self.latest_orderbook_data: Optional[Dict[str, Any]] = None
        self.latest_sr_zones_data: Optional[Dict[str, Any]] = None

        # OCO (One-Cancels-the-Other) - Now handled by NautilusTrader's bracket orders
        # No need for manual OCO manager anymore
        self.enable_oco = config.enable_oco

        # v5.12: Initialize min_rr_ratio from config (was only set in reload())
        self.min_rr_ratio = get_min_rr_ratio()

        # v15.0: Previously hardcoded values extracted to config
        self.emergency_sl_base_pct = config.emergency_sl_base_pct
        self.emergency_sl_atr_multiplier = config.emergency_sl_atr_multiplier
        self.emergency_sl_cooldown_seconds = config.emergency_sl_cooldown_seconds
        self.emergency_sl_max_consecutive = config.emergency_sl_max_consecutive
        self.sr_zones_cache_ttl_seconds = config.sr_zones_cache_ttl_seconds
        self.price_cache_ttl_seconds = config.price_cache_ttl_seconds
        self.reversal_timeout_seconds = config.reversal_timeout_seconds
        self.max_leverage_limit = config.max_leverage_limit

        # Thread lock for shared state (Telegram thread safety)
        self._state_lock = threading.Lock()

        # Thread lock for on_timer (prevent re-entry if AI calls take > timer_interval)
        self._timer_lock = threading.Lock()

        # Thread-safe cached price (updated in on_bar, read by Telegram commands)
        # IMPORTANT: Do NOT access indicator_manager from Telegram thread - it contains
        # Rust indicators (RSI, MACD) that are not Send/Sync and will cause panic
        self._cached_current_price: float = 0.0
        self._cached_current_price_time: float = 0.0  # v6.0: timestamp for staleness detection

        # Real-time Binance account fetcher for accurate balance info
        self.binance_account = BinanceAccountFetcher(
            logger=self.log,
            cache_ttl=config.network_binance_balance_cache_ttl,
            recv_window=config.network_binance_recv_window,
            api_timeout=config.network_binance_api_timeout,
        )
        self._real_balance: Dict[str, float] = {}  # Cached real balance from Binance

        # Track SL/TP state for each position (mechanical SL/TP)
        self.sltp_state: Dict[str, Dict[str, Any]] = {}

        # v4.0: Store pending execution data for unified Telegram notification
        # This allows on_position_opened to send a comprehensive message with signal + fill + position
        self._pending_execution_data: Optional[Dict[str, Any]] = None

        # v4.1: Track signal execution status for heartbeat display
        # Shows whether the signal was actually executed and why not if blocked
        self._last_signal_status: Dict[str, Any] = {
            'executed': False,       # Whether trade was executed
            'reason': '',            # Reason if not executed
            'action_taken': '',      # What action was taken (if any)
        }

        # v4.13: Pending SL/TP state for two-phase order submission
        # Entry order submitted first, SL/TP submitted after fill in on_position_opened()
        # Required because NT 1.222.0 rejects bracket orders with linked_order_ids
        self._pending_sltp: Optional[Dict[str, Any]] = None

        # v4.17: Track pending LIMIT entry order for lifecycle management
        # When entry uses LIMIT (instead of MARKET), the order may not fill immediately.
        # on_timer cancels unfilled entry orders before starting new analysis.
        self._pending_entry_order_id: Optional[str] = None

        # v7.1: Flag when _emergency_market_close exhausts all retries.
        # Next on_timer cycle will re-check position and attempt protection.
        self._needs_emergency_review: bool = False

        # v3.18: Pending reversal state for event-driven two-phase commit
        # This prevents race condition when reversing positions (close then open)
        # Format: {
        #   'target_side': 'long' or 'short',
        #   'target_quantity': float,
        #   'old_side': 'long' or 'short',
        #   'submitted_at': datetime,
        # }
        self._pending_reversal: Optional[Dict[str, Any]] = None

        # v13.0: Flag to prevent phantom position opens during manual Telegram close.
        # When NautilusTrader's internal position is out of sync with Binance after restart,
        # a reduce_only close BUY can be misinterpreted as opening a new LONG.
        self._manual_close_in_progress: bool = False

        # v14.1: Pending partial close broadcast info for subscriber notification.
        # Set in _cmd_partial_close(), consumed in on_order_filled().
        self._pending_partial_close_broadcast: Optional[Dict[str, Any]] = None

        # v14.1: Forced close reason for non-SL/TP closes (time barrier, emergency).
        # Set before triggering market close, consumed in on_position_closed().
        self._forced_close_reason: Optional[tuple] = None

        # v4.0: Cached ATR value for emergency SL calculation (updated in on_timer)
        self._cached_atr_value: float = 0.0
        # v39.0: 4H ATR for SL/TP calculation (primary, matches decision timeframe)
        self._cached_atr_4h: float = 0.0

        # v24.2: Ghost detection double-confirmation timestamp
        self._ghost_first_seen: float = 0.0

        # v5.10: Emergency SL cooldown to prevent infinite loop
        # When user manually cancels emergency SL, bot detects "SL canceled" and
        # re-submits another emergency SL → user cancels → infinite loop.
        # Cooldown ensures max 1 emergency SL per 120 seconds.
        self._last_emergency_sl_time: float = 0.0
        self._emergency_sl_count: int = 0  # consecutive count, reset on new position
        self._emergency_retry_count: int = 0  # v18.0: short-cycle retry counter
        self._reduce_only_rejection_count: int = 0  # v18.2: consecutive -2022 counter

        # v18.2: Price surge trigger — detect large intra-bar moves via trade ticks
        # and trigger early AI analysis with real-time external data.
        # Constants (code-level per Occam's Razor, not YAML):
        self._SURGE_THRESHOLD_PCT: float = 0.015  # 1.5% price deviation triggers analysis
        self._SURGE_COOLDOWN_SEC: float = 300.0   # 5-minute cooldown between surge triggers
        self._surge_cooldown_until: float = 0.0    # Timestamp until cooldown expires
        self._surge_triggered: bool = False         # Flag: current on_timer is surge-triggered
        self._surge_price_change_pct: float = 0.0   # Price change % that triggered the surge
        self._surge_alert_scheduled: bool = False    # Prevent duplicate scheduling

        # v5.13: Guard against same-cycle SL/TP replacement race condition.
        # When SL/TP are submitted (new entry) or replaced (add/reduce)
        # in the current on_timer cycle, skip further SL/TP modifications
        # to prevent async cancel race → -2022 ReduceOnly rejection.
        self._sltp_modified_this_cycle: bool = False

        # v5.13/v7.2: Track order IDs intentionally cancelled by per-layer SL replacement
        # (time barrier, reduce, manual close). When these cancels come back as
        # on_order_expired (STOP_MARKET via Algo API) or on_order_canceled, skip orphan
        # detection — we already submitted replacements.
        self._intentionally_cancelled_order_ids: set = set()

        # v12.0: Pending reflections queue — mechanical mode clears immediately,
        # but event_handlers still appends entries before next on_timer clears them.
        self._pending_reflections: List[Dict[str, Any]] = []

        # ===== v11.5: Entry-Time Data for SL/TP Optimization Analysis =====
        # Captured at position open, passed to evaluate_trade() at close
        self._entry_atr_value: float = 0.0
        self._entry_sl_atr_multiplier: float = 0.0
        self._entry_is_counter_trend: bool = False
        self._entry_risk_appetite: str = ""
        self._entry_trend_direction: str = ""
        self._entry_adx: float = 0.0

        # ===== v11.5: MAE/MFE Tracking (Maximum Adverse/Favorable Excursion) =====
        # Updated every on_bar during open position to track price extremes
        self._position_entry_price_for_mae: float = 0.0  # Entry price for MAE/MFE calculation
        self._position_max_price: float = 0.0  # Highest price seen during position
        self._position_min_price: float = float('inf')  # Lowest price seen during position
        self._position_is_long_for_mae: bool = True  # Direction for MAE/MFE calc

        # ===== v6.0: Cooldown State =====
        # Per-stop cooldown: skip N analysis cycles after any stop-loss
        self._stoploss_cooldown_until: Optional[datetime] = None
        self._stoploss_cooldown_type: str = ""  # "noise" | "reversal" | "volatility" | "default"
        self._last_stoploss_price: Optional[float] = None  # For stop-type detection
        self._last_stoploss_time: Optional[datetime] = None
        self._last_stoploss_side: Optional[str] = None  # "LONG" or "SHORT"

        # ===== v6.0: Pyramiding State =====
        # Track position layers for pyramiding control (persisted to file for restart safety)
        self._position_layers_file = "data/position_layers.json"
        self._position_layers: List[Dict[str, Any]] = self._load_position_layers()
        self._position_entry_confidence: Optional[str] = None  # Confidence at first entry

        # ===== v6.0: Confidence Tracking =====
        self._last_position_confidence: Optional[str] = None  # Last AI confidence for current position

        # ===== v15.0: P0 Market-Change Gate + Signal Dedup =====
        self._last_analysis_price: Optional[float] = None
        self._last_analysis_atr: Optional[float] = None
        self._last_analysis_had_position: bool = False
        self._consecutive_skips: int = 0
        self._max_skips_before_force: int = 3  # Watchdog: force analysis after 3 skips = 1 hour
        self._last_executed_fingerprint: str = ""  # Layer 3: Signal fingerprint
        self._force_analysis_cycles_remaining: int = 0  # v18.3: Post-close forced analysis cycles

        # ===== v21.0: FR Consecutive Block Counter =====
        # Track consecutive FR blocks in the same direction to detect trend exhaustion.
        # When count >= 3, degrade same-direction signals to HOLD to break dead loops.
        self._fr_consecutive_blocks: int = 0
        self._fr_block_direction: str = ""  # "long" or "short"

        # v23.0/v42.0: ET consecutive REJECT counter (dead in mechanical mode,
        # but referenced by order_execution reset logic on successful entry).
        self._et_consecutive_rejects: int = 0
        self._ET_EXHAUSTION_TIER1: int = 5
        self._ET_EXHAUSTION_TIER2: int = 8

        # ===== v23.0: REJECT Result Tracking =====
        # Record the price when REJECT occurs, evaluate on next cycle whether
        # REJECT was correct (price moved against proposed direction = good REJECT).
        self._last_reject_record: Optional[Dict[str, Any]] = None  # {signal, price, timestamp}

        # v34.1: HOLD counterfactual tracking
        # Records price + proposed signal when HOLD occurs, evaluates on next
        # on_timer whether the HOLD was correct (price moved against proposed = good HOLD)
        self._hold_counterfactual_record: Optional[Dict[str, Any]] = None

        # ===== v15.0: P2 Confidence Decay Tracking =====
        self._confidence_history: list = []  # Rolling window, last 4 confidence readings
        self._decay_warned_levels: set = set()  # Track warned levels to prevent spam

        # ===== v7.2: Per-Layer Independent SL/TP =====
        # Each entry (initial or pyramiding) creates an independent "layer" with its own
        # SL/TP orders on Binance. Layers don't interfere with each other.
        # Key: layer_id (str, e.g. "layer_0", "layer_1")
        # Value: {entry_price, quantity, side, sl_order_id, tp_order_id,
        #         sl_price, tp_price, highest_price, lowest_price,
        #         confidence, timestamp}
        self._layer_orders: Dict[str, Dict[str, Any]] = {}
        self._next_layer_idx: int = 0  # Monotonic counter — never decrements on remove
        # Reverse mapping: order_id → layer_id (for fast lookup in on_order_filled)
        self._order_to_layer: Dict[str, str] = {}
        # Snapshots saved by _remove_layer/_update_aggregate_sltp_state before clearing,
        # consumed by on_position_closed (which fires AFTER on_order_filled).
        self._pre_close_sltp_snapshot: Optional[Dict[str, Any]] = None
        self._pre_close_last_layer: Optional[Dict[str, Any]] = None

        # Aggregate sltp_state: computed from _layer_orders for display/Telegram/web
        # Format: {
        #   "instrument_id": {
        #       "entry_price": float (weighted avg),
        #       "highest_price": float (session high),
        #       "lowest_price": float (session low),
        #       "current_sl_price": float (tightest SL across layers),
        #       "current_tp_price": float (nearest TP across layers),
        #       "side": str (LONG/SHORT),
        #       "quantity": float (sum of all layers),
        #   }
        # }

        # Technical indicators manager
        sma_periods = config.sma_periods if config.sma_periods else [5, 20, 50]
        self.indicator_manager = TechnicalIndicatorManager(
            sma_periods=sma_periods,
            ema_periods=[config.macd_fast, config.macd_slow],
            rsi_period=config.rsi_period,
            macd_fast=config.macd_fast,
            macd_slow=config.macd_slow,
            bb_period=config.bb_period,
            bb_std=config.bb_std,
            volume_ma_period=config.volume_ma_period,
            support_resistance_lookback=config.support_resistance_lookback,
        )

        # Multi-Timeframe Manager (v3.2.8)
        self.mtf_enabled = getattr(config, 'multi_timeframe_enabled', False)
        self.mtf_manager = None
        self.trend_bar_type = None
        self.decision_bar_type = None
        self.execution_bar_type = None

        self._mtf_trend_initialized = False
        self._mtf_decision_initialized = False
        self._mtf_execution_initialized = False

        if self.mtf_enabled:
            try:
                from indicators.multi_timeframe_manager import MultiTimeframeManager

                # Build BarType objects for each layer
                instrument_str = str(self.instrument_id)
                self.trend_bar_type = BarType.from_str(f"{instrument_str}-1-DAY-LAST-EXTERNAL")
                self.decision_bar_type = BarType.from_str(f"{instrument_str}-4-HOUR-LAST-EXTERNAL")
                # v18.2: Aligned with main bar_type (30M). Previously hardcoded 15M,
                # causing indicator_manager to receive BOTH 15M and 30M bars mixed together.
                self.execution_bar_type = BarType.from_str(f"{instrument_str}-30-MINUTE-LAST-EXTERNAL")

                # Build MTF config from strategy config (v3.3: removed unused filter configs)
                mtf_config = {
                    'enabled': True,
                    'trend_layer': {
                        'timeframe': '1d',
                        'sma_period': getattr(config, 'mtf_trend_sma_period', 200),
                    },
                    'decision_layer': {
                        'timeframe': '4h',
                    },
                    'execution_layer': {
                        'timeframe': '30m',
                    }
                }

                self.mtf_manager = MultiTimeframeManager(
                    config=mtf_config,
                    trend_bar_type=self.trend_bar_type,
                    decision_bar_type=self.decision_bar_type,
                    execution_bar_type=self.execution_bar_type,
                    logger=self.log,
                )
                self.log.info(f"✅ MTF Manager initialized: trend={self.trend_bar_type}, decision={self.decision_bar_type}, exec={self.execution_bar_type}")
            except Exception as e:
                self.log.error(f"❌ Failed to initialize MTF Manager: {e}")
                self.mtf_enabled = False

        # v10.0: Detect strategy mode early (needed before Analyzer log below)
        self._strategy_mode = getattr(config, 'strategy_mode', 'ai') or 'ai'

        # Multi-Agent analyzer (mechanical mode: only uses extract_features + compute_anticipatory_scores)
        self.multi_agent = MultiAgentAnalyzer(
            sr_zones_config=config.sr_zones_config,
        )
        self.log.info(f"✅ Analyzer initialized (mode={self._strategy_mode})")

        # Telegram Bot
        self.telegram_bot = None
        self.enable_telegram = config.enable_telegram
        if self.enable_telegram:
            try:
                from utils.telegram_bot import TelegramBot
                
                bot_token = config.telegram_bot_token or os.getenv('TELEGRAM_BOT_TOKEN', '')
                chat_id = config.telegram_chat_id or os.getenv('TELEGRAM_CHAT_ID', '')

                # v14.0: Notification group bot (separate bot for public signal broadcasting)
                notif_token = config.telegram_notification_bot_token or os.getenv('TELEGRAM_NOTIFICATION_BOT_TOKEN', '')
                notif_chat_id = config.telegram_notification_chat_id or os.getenv('TELEGRAM_NOTIFICATION_CHAT_ID', '')

                if bot_token and chat_id:
                    self.telegram_bot = TelegramBot(
                        token=bot_token,
                        chat_id=chat_id,
                        logger=self.log,
                        enabled=True,
                        message_timeout=config.network_telegram_message_timeout,
                        # v4.0 Queue configuration (non-blocking message sending)
                        use_queue=config.telegram_queue_enabled,
                        queue_db_path=config.telegram_queue_db_path,
                        queue_max_retries=config.telegram_queue_max_retries,
                        queue_alert_cooldown=config.telegram_queue_alert_cooldown,
                        queue_send_interval=config.telegram_queue_send_interval,
                        # v14.0: Notification group
                        notification_token=notif_token,
                        notification_chat_id=notif_chat_id,
                    )
                    # Store notification preferences
                    self.telegram_notify_signals = config.telegram_notify_signals
                    self.telegram_notify_fills = config.telegram_notify_fills
                    self.telegram_notify_positions = config.telegram_notify_positions
                    self.telegram_notify_errors = config.telegram_notify_errors
                    self.telegram_notify_heartbeat = config.telegram_notify_heartbeat  # v2.1
                    # v3.13: Additional notification toggles
                    self.telegram_notify_sltp_update = config.telegram_notify_sltp_update
                    self.telegram_notify_startup = config.telegram_notify_startup
                    self.telegram_notify_shutdown = config.telegram_notify_shutdown
                    # v3.13: Auto-summary configuration
                    self.telegram_auto_daily = config.telegram_auto_daily
                    self.telegram_auto_weekly = config.telegram_auto_weekly
                    self.telegram_daily_hour_utc = config.telegram_daily_hour_utc
                    self.telegram_weekly_day = config.telegram_weekly_day
                    # v3.13: Date tracking (prevent duplicate sends)
                    self._last_daily_summary_date = None
                    self._last_weekly_summary_date = None

                    self.log.info("✅ Telegram Bot initialized successfully")
                    
                    # Initialize command handler for remote control
                    # Note: The command handler runs in a separate thread with its own event loop
                    # and handles Telegram Conflict errors gracefully with retries
                    try:
                        from utils.telegram_command_handler import TelegramCommandHandler
                        # Note: threading is already imported at module level (line 10)

                        # Create callback function for commands
                        def command_callback(command: str, args: Dict[str, Any]) -> Dict[str, Any]:
                            """Callback function for Telegram commands."""
                            return self.handle_telegram_command(command, args)

                        # Initialize command handler
                        allowed_chat_ids = [chat_id]  # Only allow the configured chat ID
                        self.telegram_command_handler = TelegramCommandHandler(
                            token=bot_token,
                            allowed_chat_ids=allowed_chat_ids,
                            strategy_callback=command_callback,
                            logger=self.log,
                            startup_delay=config.network_telegram_startup_delay,
                            polling_max_retries=config.network_telegram_polling_max_retries,
                            polling_base_delay=config.network_telegram_polling_base_delay,
                            # v4.0 Security configuration (PIN verification + audit logging)
                            enable_pin=config.telegram_security_enable_pin,
                            pin_code=config.telegram_security_pin_code or None,
                            pin_expiry_seconds=config.telegram_security_pin_expiry_seconds,
                            rate_limit_per_minute=config.telegram_security_rate_limit_per_minute,
                            enable_audit=config.telegram_security_enable_audit,
                            audit_log_dir=config.telegram_security_audit_log_dir,
                            api_timeout=config.network_telegram_api_timeout,
                        )

                        # Start command handler in background thread with isolated event loop
                        def run_command_handler():
                            """Run command handler in background thread with proper event loop isolation."""
                            loop = None
                            try:
                                # Create isolated event loop for this thread
                                loop = asyncio.new_event_loop()
                                asyncio.set_event_loop(loop)
                                # Start polling (this will run indefinitely)
                                # Note: start_polling() handles Telegram Conflict errors with retries
                                loop.run_until_complete(self.telegram_command_handler.start_polling())
                            except asyncio.CancelledError:
                                self.log.info("🔌 Telegram command handler cancelled")
                            except Exception as e:
                                # Log as warning, not error - command handler is non-critical
                                self.log.warning(f"⚠️ Telegram command handler stopped: {e}")
                            finally:
                                # Cleanup event loop
                                if loop is not None:
                                    try:
                                        loop.close()
                                    except Exception as e:
                                        self.log.warning(f"⚠️ Failed to close event loop: {e}")

                        # Start background thread for command listening
                        command_thread = threading.Thread(
                            target=run_command_handler,
                            daemon=True,
                            name="TelegramCommandHandler"
                        )
                        command_thread.start()
                        self.log.info("✅ Telegram Command Handler starting in background thread (conflicts will be retried)")

                    except ImportError:
                        self.log.warning("⚠️ Telegram command handler not available")
                        self.telegram_command_handler = None
                    except Exception as e:
                        self.log.warning(f"⚠️ Could not initialize command handler (non-critical): {e}")
                        self.telegram_command_handler = None
                    
                else:
                    self.log.warning("⚠️ Telegram enabled but token/chat_id not configured")
                    self.enable_telegram = False
            except ImportError:
                self.log.warning("⚠️ Telegram bot not available (python-telegram-bot not installed)")
                self.enable_telegram = False
            except Exception as e:
                self.log.error(f"❌ Failed to initialize Telegram Bot: {e}")
                self.enable_telegram = False
        
        # Strategy control state for remote commands
        self.is_trading_paused = False
        self.strategy_start_time = None

        # Sentiment data fetcher
        self.sentiment_enabled = config.sentiment_enabled
        if self.sentiment_enabled:
            # Use sentiment_timeframe from config, or derive from bar_type if not specified
            sentiment_tf = config.sentiment_timeframe
            if not sentiment_tf or sentiment_tf == "":
                # Extract timeframe from bar_type (e.g., "30-MINUTE" -> "30m")
                # NOTE: Must check longer strings first (30-MINUTE before 5-MINUTE)
                bar_str = str(self.bar_type)
                if "30-MINUTE" in bar_str:
                    sentiment_tf = "30m"
                elif "15-MINUTE" in bar_str:
                    sentiment_tf = "15m"
                elif "5-MINUTE" in bar_str:
                    sentiment_tf = "5m"
                elif "1-MINUTE" in bar_str:
                    sentiment_tf = "1m"
                elif "4-HOUR" in bar_str:
                    sentiment_tf = "4h"
                elif "1-HOUR" in bar_str:
                    sentiment_tf = "1h"
                else:
                    sentiment_tf = "30m"  # Default fallback (v18.2: 30M execution layer)
            
            self.sentiment_fetcher = SentimentDataFetcher(
                lookback_hours=config.sentiment_lookback_hours,
                timeframe=sentiment_tf,
                timeout=config.sentiment_timeout,
            )
            self.log.info(f"Sentiment fetcher initialized with timeframe: {sentiment_tf}")
        else:
            self.sentiment_fetcher = None

        # ========== Order Flow & Derivatives (MTF v2.1) ==========
        # Read parameters from config
        order_flow_enabled = config.order_flow_enabled if hasattr(config, 'order_flow_enabled') else True

        if order_flow_enabled:
            # Binance kline client (fetches full 12-column data)
            self.binance_kline_client = BinanceKlineClient(
                timeout=config.order_flow_binance_timeout if hasattr(config, 'order_flow_binance_timeout') else 10,
                logger=self.log,
            )

            # Order flow processor
            self.order_flow_processor = OrderFlowProcessor(logger=self.log)

            # Coinalyze client (derivatives data)
            coinalyze_enabled = config.order_flow_coinalyze_enabled if hasattr(config, 'order_flow_coinalyze_enabled') else True
            if coinalyze_enabled:
                self.coinalyze_client = CoinalyzeClient(
                    api_key=None,  # Read from environment variable
                    timeout=config.order_flow_coinalyze_timeout if hasattr(config, 'order_flow_coinalyze_timeout') else 10,
                    max_retries=config.order_flow_coinalyze_max_retries if hasattr(config, 'order_flow_coinalyze_max_retries') else 2,
                    retry_delay=config.order_flow_coinalyze_retry_delay if hasattr(config, 'order_flow_coinalyze_retry_delay') else 1.0,
                    logger=self.log,
                )
            else:
                self.coinalyze_client = None
                self.log.info("Coinalyze client disabled by config")

            # ========== Order Book Depth (v3.7) ==========
            # Orderbook depth data (provides liquidity, imbalance, slippage metrics)
            order_book_enabled = config.order_book_enabled if hasattr(config, 'order_book_enabled') else False
            self.order_book_enabled = order_book_enabled  # Store for on_timer access
            if order_book_enabled:
                # Binance orderbook client
                self.binance_orderbook_client = BinanceOrderBookClient(
                    timeout=config.order_book_api_timeout if hasattr(config, 'order_book_api_timeout') else 10,
                    max_retries=config.order_book_api_max_retries if hasattr(config, 'order_book_api_max_retries') else 2,
                    retry_delay=config.order_book_api_retry_delay if hasattr(config, 'order_book_api_retry_delay') else 1.0,
                    logger=self.log,
                )

                # Orderbook processor (computes OBI, slippage, anomaly, etc.)
                self.orderbook_processor = OrderBookProcessor(
                    price_band_pct=config.order_book_price_band_pct if hasattr(config, 'order_book_price_band_pct') else 0.5,
                    base_anomaly_threshold=config.order_book_anomaly_threshold if hasattr(config, 'order_book_anomaly_threshold') else 3.0,
                    slippage_amounts=config.order_book_slippage_amounts if hasattr(config, 'order_book_slippage_amounts') else [0.1, 0.5, 1.0],
                    weighted_obi_config={
                        'base_decay': config.order_book_weighted_decay if hasattr(config, 'order_book_weighted_decay') else 0.8,
                        'adaptive': config.order_book_adaptive_decay if hasattr(config, 'order_book_adaptive_decay') else True,
                        'volatility_factor': config.order_book_volatility_factor if hasattr(config, 'order_book_volatility_factor') else 0.1,
                        'min_decay': config.order_book_min_decay if hasattr(config, 'order_book_min_decay') else 0.5,
                        'max_decay': config.order_book_max_decay if hasattr(config, 'order_book_max_decay') else 0.95,
                    },
                    history_size=config.order_book_history_size if hasattr(config, 'order_book_history_size') else 10,
                    logger=self.log,
                )
                self.log.info("✅ Order Book clients initialized")
            else:
                self.binance_orderbook_client = None
                self.orderbook_processor = None
                self.log.info("Order Book disabled by config")

            # ========== Binance Derivatives (v3.21: Top Traders, Taker Ratio) ==========
            # No API key needed, uses public endpoint
            self.binance_derivatives_client = BinanceDerivativesClient(
                timeout=config.order_flow_binance_timeout if hasattr(config, 'order_flow_binance_timeout') else 10,
                logger=self.log,
            )

            self.log.info("✅ Order Flow & Derivatives clients initialized")
        else:
            self.binance_kline_client = None
            self.order_flow_processor = None
            self.coinalyze_client = None
            self.binance_orderbook_client = None
            self.orderbook_processor = None
            self.binance_derivatives_client = None
            self.order_book_enabled = False
            self.log.info("Order Flow disabled by config")

        # v7.0: Unified data assembler (SSoT for external API data fetching)
        self.data_assembler = AIDataAssembler(
            binance_kline_client=self.binance_kline_client,
            order_flow_processor=self.order_flow_processor,
            coinalyze_client=self.coinalyze_client,
            sentiment_client=self.sentiment_fetcher,
            binance_derivatives_client=self.binance_derivatives_client,
            binance_orderbook_client=self.binance_orderbook_client,
            orderbook_processor=self.orderbook_processor,
            logger=self.log,
        )

        # State tracking
        self.instrument: Optional[Instrument] = None
        self.last_signal: Optional[Dict[str, Any]] = None
        self.bars_received = 0

        # v3.12: Risk Controller with circuit breakers
        risk_config = config.risk_config if hasattr(config, 'risk_config') and config.risk_config else {}
        # v2.0: Merge risk_regime thresholds into risk_config for regime-adaptive DD
        _risk_regime_cfg = config.risk_regime_config if hasattr(config, 'risk_regime_config') and config.risk_regime_config else {}
        if _risk_regime_cfg:
            risk_config['risk_regime'] = _risk_regime_cfg
        self.risk_controller = RiskController(
            config=risk_config,
            logger=self.log,
        )

        # v2.0: HMM Regime Detector
        self._regime_detector = None
        self._current_regime = "RANGING"  # default until HMM predicts
        self._last_regime_result = None  # Full regime prediction result dict
        self._cached_fear_greed = None  # Cached Fear & Greed data for heartbeat/commands
        _hmm_cfg = config.hmm_config if hasattr(config, 'hmm_config') and config.hmm_config else {}
        if _hmm_cfg.get('enabled', False):
            try:
                from utils.regime_detector import RegimeDetector
                self._regime_detector = RegimeDetector(config=_hmm_cfg)
                self.log.info("✅ HMM RegimeDetector initialized")
            except Exception as e:
                self.log.warning(f"⚠️ HMM RegimeDetector failed to init: {e}")

        # v2.0: Kelly Position Sizer
        self._kelly_sizer = None
        _kelly_cfg = config.kelly_config if hasattr(config, 'kelly_config') and config.kelly_config else {}
        if _kelly_cfg.get('enabled', False):
            try:
                from utils.kelly_sizer import KellySizer
                self._kelly_sizer = KellySizer(config={'kelly': _kelly_cfg})
                self.log.info(f"✅ KellySizer initialized (min_trades={_kelly_cfg.get('min_trades_for_kelly', 50)})")
            except Exception as e:
                self.log.warning(f"⚠️ KellySizer failed to init: {e}")

        # v2.0: Prometheus Metrics Exporter
        self._metrics_exporter = None
        _prom_cfg = config.prometheus_config if hasattr(config, 'prometheus_config') and config.prometheus_config else {}
        if _prom_cfg.get('enabled', False):
            try:
                from utils.metrics_exporter import MetricsExporter
                self._metrics_exporter = MetricsExporter(config={'prometheus': _prom_cfg})
                self.log.info(f"✅ Prometheus MetricsExporter initialized (port={_prom_cfg.get('port', 9090)})")
            except Exception as e:
                self.log.warning(f"⚠️ MetricsExporter failed to init: {e}")

        self.log.info(f"DeepSeek AI Strategy initialized for {self.instrument_id}")

    def on_start(self):
        """Actions to be performed on strategy start."""
        # v10.0: Detect strategy mode from StrategyConfig
        self._strategy_mode = getattr(self.config, 'strategy_mode', 'ai') or 'ai'
        self.log.info(f"Starting DeepSeek AI Strategy (mode={self._strategy_mode})...")

        # v10.0: Direction lock counters for mechanical mode — load persisted state
        if self._strategy_mode == 'mechanical':
            from agents.mechanical_decide import load_direction_lock_state
            load_direction_lock_state()

        # v48.0: Load DCA config for zone-based entry mode
        self._dca_config = {}
        self._dca_virtual_avg = 0
        self._dca_virtual_last_price = 0
        try:
            from utils.config_manager import ConfigManager
            _cm = ConfigManager()
            _cm.load()
            self._dca_config = _cm.get('anticipatory', 'dca') or {}
            if self._dca_config.get('enabled'):
                self.log.info(f"✅ DCA mode enabled: TP={self._dca_config.get('tp_pct', 0.025):.1%} "
                              f"SL={self._dca_config.get('sl_pct', 0.06):.0%} "
                              f"spacing={self._dca_config.get('spacing_pct', 0.03):.0%} "
                              f"max_layers={self._dca_config.get('max_real_layers', 4)}")
        except Exception as e:
            self.log.debug(f"DCA config load: {e}")

        # v2.0: Start Prometheus metrics server
        if self._metrics_exporter:
            try:
                self._metrics_exporter.start()
                self.log.info("✅ Prometheus metrics server started")
            except Exception as e:
                self.log.warning(f"⚠️ Prometheus start failed: {e}")

        # v2.2: Record startup time (for heartbeat uptime display)
        self._start_time = datetime.now()

        # Send immediate "initializing" notification BEFORE instrument loading
        # This ensures user gets notified even if instrument loading fails/takes long
        if (self.telegram_bot and self.enable_telegram and
            getattr(self, 'telegram_notify_startup', True)):
            try:
                safe_id = self.telegram_bot.escape_markdown(str(self.instrument_id))
                init_msg = (
                    f"🔄 *Strategy Initializing*\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"📊 {safe_id}\n"
                    f"⏳ Loading instruments...\n"
                    f"\n⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
                )
                self.telegram_bot.send_message_sync(init_msg, use_queue=False)
            except Exception as e:
                self.log.warning(f"Failed to send init notification: {e}")

        # Load instrument with retry mechanism
        # The instrument may not be immediately available as the data client
        # loads instruments asynchronously from Binance
        # Read retry params from config (previously hardcoded 60/1.0)
        max_retries = self.config.network_instrument_discovery_max_retries
        retry_interval = self.config.network_instrument_discovery_retry_interval

        self.instrument = None
        for attempt in range(max_retries):
            self.instrument = self.cache.instrument(self.instrument_id)
            if self.instrument is not None:
                break

            if attempt == 0:
                self.log.info(f"Waiting for instrument {self.instrument_id} to be loaded...")
                # Log cache state for debugging
                all_instruments = self.cache.instruments()
                if all_instruments:
                    self.log.info(f"Currently loaded instruments: {[str(i.id) for i in all_instruments]}")
                else:
                    self.log.info("No instruments loaded yet in cache")
            elif attempt % 10 == 0:
                self.log.info(f"Still waiting for instrument... (attempt {attempt + 1}/{max_retries})")
                # Log cache state periodically for debugging
                all_instruments = self.cache.instruments()
                if all_instruments:
                    self.log.info(f"Currently loaded instruments: {[str(i.id) for i in all_instruments]}")

            time.sleep(retry_interval)

        if self.instrument is None:
            # Final diagnostic: list all instruments in cache
            all_instruments = self.cache.instruments()
            if all_instruments:
                available_ids = [str(i.id) for i in all_instruments]
                self.log.error(
                    f"Could not find instrument {self.instrument_id} after {max_retries} seconds. "
                    f"Available instruments: {available_ids}"
                )
            else:
                self.log.error(
                    f"Could not find instrument {self.instrument_id} after {max_retries} seconds. "
                    f"No instruments loaded in cache! Check that: "
                    f"1) Binance API keys are valid, "
                    f"2) Network connectivity to api.binance.com is working, "
                    f"3) InstrumentProviderConfig.load_ids contains the correct instrument ID."
                )
            self.stop()
            return

        self.log.info(f"Loaded instrument: {self.instrument.id}")

        # Pre-fetch historical bars before subscribing to live data
        self._prefetch_historical_bars(limit=200)

        # Subscribe to bars (live data)
        self.subscribe_bars(self.bar_type)
        self.log.info(f"Subscribed to {self.bar_type}")

        # v4.12: Subscribe to trade ticks for real-time price caching
        # Used by: _cached_current_price (Telegram commands), trailing stop price tracking
        # Note: No longer needed for OrderEmulator (v4.12 removed SL/TP emulation)
        try:
            self.subscribe_trade_ticks(self.instrument_id)
            self.log.info(f"Subscribed to trade ticks for {self.instrument_id} (price caching)")
        except Exception as e:
            self.log.warning(f"Failed to subscribe to trade ticks: {e}")

        # Multi-Timeframe subscriptions (v3.2.9)
        if self.mtf_enabled and self.mtf_manager:
            try:
                # Subscribe to all three timeframes
                self.subscribe_bars(self.trend_bar_type)
                self.subscribe_bars(self.decision_bar_type)
                self.subscribe_bars(self.execution_bar_type)
                self.log.info(f"MTF: Subscribed to 1D, 4H, 30M bars")

                # Prefetch historical data for each layer (async)
                self._prefetch_multi_timeframe_bars()
            except Exception as e:
                self.log.error(f"MTF: Failed to subscribe/prefetch: {e}")
                # Continue without MTF - graceful degradation

        # Set up timer for periodic analysis (clock-aligned to 00/20/40 minutes)
        interval_minutes = self.config.timer_interval_sec // 60  # Default 20 minutes
        next_aligned_time = self._calculate_next_aligned_time(interval_minutes)
        self.log.info(f"Timer aligned to clock: next trigger at {next_aligned_time.strftime('%H:%M:%S')} UTC")

        self.clock.set_timer(
            name="analysis_timer",
            interval=timedelta(seconds=self.config.timer_interval_sec),
            start_time=next_aligned_time,
            callback=self.on_timer,
        )

        self.log.info("Strategy started successfully")

        # Fetch real account balance from Binance
        self._update_real_balance()

        # v4.8: Sync leverage from Binance API
        self._sync_binance_leverage()

        # Record start time for uptime tracking
        self.strategy_start_time = datetime.now(timezone.utc)

        # v2.1: Timer counter for heartbeat tracking
        self._timer_count = 0

        # v11.5: Daily/weekly starting equity for PnL% calculation
        # Persisted to data/equity_snapshots.json to survive restarts
        self._daily_starting_equity: float = 0.0
        self._weekly_starting_equity: float = 0.0
        self._last_equity_date: str = ""
        self._last_equity_week: str = ""

        # v11.5: Signal counters (reset daily, persisted)
        self._signals_generated_today: int = 0
        self._signals_executed_today: int = 0

        # Load persisted equity snapshots
        self._load_equity_snapshots()

        # Send Telegram startup notification
        # v3.13: Added notify_startup switch
        if (self.telegram_bot and self.enable_telegram and
            getattr(self, 'telegram_notify_startup', True)):
            try:
                # v4.0: Extract timeframe from bar_type for display
                bar_type_str = str(self.bar_type)
                if '30-MINUTE' in bar_type_str:
                    timeframe = '30m'
                elif '15-MINUTE' in bar_type_str:
                    timeframe = '15m'
                elif '5-MINUTE' in bar_type_str:
                    timeframe = '5m'
                elif '1-MINUTE' in bar_type_str:
                    timeframe = '1m'
                elif '1-HOUR' in bar_type_str:
                    timeframe = '1h'
                elif '4-HOUR' in bar_type_str:
                    timeframe = '4h'
                elif '1-DAY' in bar_type_str:
                    timeframe = '1d'
                else:
                    timeframe = '30m'  # Default (v18.2: 30M execution layer)

                startup_msg = self.telegram_bot.format_startup_message(
                    instrument_id=str(self.instrument_id),
                    config={
                        'timeframe': timeframe,
                        'enable_auto_sl_tp': self.enable_auto_sl_tp,
                        'enable_oco': self.enable_oco,
                        'mtf_enabled': getattr(self, 'mtf_enabled', False),
                        'sr_hard_control_enabled': getattr(self, 'sr_hard_control_enabled', False),
                    }
                )
                # Use direct send (not queue) to ensure startup notification is immediate
                # v14.0: Startup → private chat only (operational info)
                self.telegram_bot.send_message_sync(startup_msg, use_queue=False)
                # Note: Help message removed - users can use /help command if needed

            except Exception as e:
                self.log.warning(f"Failed to send Telegram startup notification: {e}")

        # v4.12: Check for existing positions that need SL/TP protection
        # After process crash/restart, Binance position may exist but SL/TP orders
        # could have been lost (pre-v4.12 emulated orders) or missed during shutdown.
        self._recover_sltp_on_start()

    def _recover_sltp_on_start(self):
        """
        v7.2: Recover per-layer SL/TP state on startup.

        1. Load persisted _layer_orders from data/layer_orders.json
        2. Verify against actual open orders on Binance
        3. Rebuild reverse mapping and aggregate state
        4. If no persisted layers, create a single "recovery" layer from open orders
        5. If no SL found, submit emergency SL
        """
        try:
            position_data = self._get_current_position_data(
                current_price=0, from_telegram=False
            )
            if not position_data or position_data.get('quantity', 0) == 0:
                self.log.info("✅ No open position on startup — no SL/TP recovery needed")
                # Clear any stale layer data
                self._layer_orders.clear()
                self._order_to_layer.clear()
                self._next_layer_idx = 0
                self._persist_layer_orders()
                return

            quantity = position_data['quantity']
            side = position_data.get('side', 'long')
            entry_px = float(position_data.get('entry_price', 0))

            # Check existing orders on Binance via NT cache
            open_orders = self.cache.orders_open(instrument_id=self.instrument_id)
            reduce_only_orders = [o for o in open_orders if o.is_reduce_only]

            has_sl = any(
                o.order_type in (OrderType.STOP_MARKET, OrderType.TRAILING_STOP_MARKET)
                for o in reduce_only_orders
            )

            # v13.0: Also query Binance directly for live order IDs.
            # NT cache may be incomplete right after restart — Binance Algo API orders
            # (STOP_MARKET, TAKE_PROFIT submitted via position-linked Algo endpoint) might
            # not be loaded into the NT cache yet. Querying Binance ensures we don't
            # incorrectly treat live SL orders as stale during cross-validation.
            # v15.3: Query both regular + Algo API endpoints to avoid missing Algo orders
            binance_live_order_ids: set = set()
            binance_live_sl_ids: set = set()
            binance_best_sl_price: float = 0.0  # v35.0: Capture SL price for recovery layer
            binance_best_tp_price: float = 0.0  # Capture TP price for recovery layer
            try:
                if self.binance_account:
                    symbol = str(self.instrument_id).replace('-PERP', '').replace('.BINANCE', '').upper()

                    # 1) Regular orders: /fapi/v1/openOrders
                    binance_open_orders = self.binance_account.get_open_orders(symbol)
                    for bo in binance_open_orders:
                        client_id = bo.get('clientOrderId', '')
                        if client_id:
                            binance_live_order_ids.add(client_id)
                            bo_type = bo.get('type', '')
                            is_reduce = bo.get('reduceOnly', False)
                            if bo_type in ('STOP_MARKET', 'STOP', 'TRAILING_STOP_MARKET') and is_reduce:
                                binance_live_sl_ids.add(client_id)
                                # v35.0: Capture SL price (closest to current price = tightest)
                                sp = float(bo.get('stopPrice', 0))
                                if sp > 0 and (binance_best_sl_price == 0 or abs(sp - entry_px) < abs(binance_best_sl_price - entry_px)):
                                    binance_best_sl_price = sp
                            # Capture TP price from TAKE_PROFIT / LIMIT orders
                            if bo_type in ('TAKE_PROFIT', 'TAKE_PROFIT_MARKET', 'LIMIT') and is_reduce:
                                tp = float(bo.get('stopPrice', 0) or bo.get('price', 0))
                                if tp > 0 and (binance_best_tp_price == 0 or abs(tp - entry_px) < abs(binance_best_tp_price - entry_px)):
                                    binance_best_tp_price = tp

                    # 2) Algo API orders: /fapi/v1/openAlgoOrders
                    # v6.6+ TP uses limit_if_touched() → Algo API, SL may also be on this endpoint
                    algo_orders = self.binance_account.get_open_algo_orders(symbol)
                    for algo in algo_orders:
                        algo_status = algo.get('algoStatus', algo.get('status', ''))
                        if algo_status not in ('', 'WORKING', 'NEW'):
                            continue
                        # Algo orders use algoId as identifier, may also have clientOrderId
                        algo_id = str(algo.get('clientOrderId', algo.get('algoId', '')))
                        order_type = algo.get('orderType', algo.get('algoType', '')).upper()
                        if algo_id:
                            binance_live_order_ids.add(algo_id)
                            if order_type in ('STOP_MARKET', 'STOP', 'TRAILING_STOP_MARKET'):
                                binance_live_sl_ids.add(algo_id)
                                # v35.0: Capture SL price from Algo orders too
                                sp = float(algo.get('stopPrice', 0))
                                if sp > 0 and (binance_best_sl_price == 0 or abs(sp - entry_px) < abs(binance_best_sl_price - entry_px)):
                                    binance_best_sl_price = sp
                            # Capture TP price from Algo orders (TAKE_PROFIT via limit_if_touched)
                            if order_type in ('TAKE_PROFIT', 'TAKE_PROFIT_MARKET', 'LIMIT_IF_TOUCHED'):
                                tp = float(algo.get('stopPrice', 0) or algo.get('price', 0))
                                if tp > 0 and (binance_best_tp_price == 0 or abs(tp - entry_px) < abs(binance_best_tp_price - entry_px)):
                                    binance_best_tp_price = tp

                    self.log.info(
                        f"📌 Binance direct query: {len(binance_open_orders)} regular + "
                        f"{len(algo_orders)} algo orders "
                        f"({len(binance_live_sl_ids)} SL-type)"
                    )
                    # If Binance shows a live SL but NT cache doesn't, mark has_sl = True
                    if binance_live_sl_ids and not has_sl:
                        has_sl = True
                        self.log.info(f"📌 Binance SL found (not yet in NT cache): {binance_live_sl_ids}")
            except Exception as e:
                self.log.warning(f"⚠️ Could not query Binance for open orders during recovery: {e}")

            # Try to load persisted layer state
            persisted = self._load_layer_orders()
            if persisted:
                self._layer_orders = persisted
                # Restore monotonic counter to max(existing indices) + 1
                max_idx = max(
                    (l.get('layer_index', 0) for l in persisted.values()),
                    default=-1,
                )
                self._next_layer_idx = max_idx + 1
                # Rebuild reverse mapping
                self._order_to_layer.clear()
                for layer_id, layer in self._layer_orders.items():
                    for key in ('sl_order_id', 'tp_order_id', 'trailing_order_id'):
                        oid = layer.get(key, '')
                        if oid:
                            self._order_to_layer[oid] = layer_id
                self._update_aggregate_sltp_state()
                self.log.info(
                    f"✅ Restored {len(self._layer_orders)} layers from persisted state "
                    f"({len(reduce_only_orders)} NT-cache orders, {len(binance_live_sl_ids)} Binance SL orders)"
                )

                # v7.3: Cross-validate persisted SL order IDs against live exchange orders.
                # After a crash, SL orders may have been cancelled/filled while bot was offline.
                # Tier 2 must NOT blindly trust the JSON — verify each layer's SL is alive.
                # v13.0: Use union of NT cache IDs + Binance direct IDs to avoid false positives
                #        when NT hasn't loaded Binance Algo orders into cache yet.
                nt_order_ids = {str(o.client_order_id) for o in open_orders}
                live_order_ids = nt_order_ids | binance_live_order_ids  # v13.0: union
                live_sl_types = {
                    str(o.client_order_id) for o in reduce_only_orders
                    if o.order_type in (OrderType.STOP_MARKET, OrderType.TRAILING_STOP_MARKET)
                } | binance_live_sl_ids  # v13.0: union
                uncovered_qty = 0.0
                stale_layers = []
                stale_tp_layers = []  # v15.4: also track stale TP
                for lid, ldata in self._layer_orders.items():
                    sl_id = ldata.get('sl_order_id', '')
                    if sl_id and sl_id not in live_order_ids:
                        uncovered_qty += ldata.get('quantity', 0)
                        stale_layers.append(lid)
                    elif not sl_id:
                        uncovered_qty += ldata.get('quantity', 0)
                        stale_layers.append(lid)

                    # v15.4: Check TP orders too — stale TP references cause
                    # on_order_filled layer lookup failures and missed OCO cleanup
                    # v36.3: Also recover TP that was never submitted (tp_order_id empty
                    # but tp_price > 0), not just stale references
                    tp_id = ldata.get('tp_order_id', '')
                    tp_price_val = ldata.get('tp_price', 0)
                    if tp_id and tp_id not in live_order_ids:
                        stale_tp_layers.append(lid)
                    elif not tp_id and tp_price_val > 0:
                        # TP was never submitted — treat as stale for recovery
                        stale_tp_layers.append(lid)

                    # v24.0: Check trailing order — stale trailing is non-critical
                    # (fixed SL still protects), but clean reference to avoid confusion
                    trailing_id = ldata.get('trailing_order_id', '')
                    if trailing_id and trailing_id not in live_order_ids:
                        self._order_to_layer.pop(trailing_id, None)
                        ldata['trailing_order_id'] = ''
                        self.log.warning(
                            f"🧹 Layer {ldata.get('layer_index', '?')}: "
                            f"trailing {trailing_id[:8]}... not alive on exchange, cleared reference"
                        )

                if uncovered_qty > 0:
                    # v35.0: Check if exchange actually has live SL orders before
                    # submitting emergency SL. NT 1.222.0 routes STOP_MARKET via Binance
                    # Algo API, whose response uses algoId (not clientOrderId). This causes
                    # persisted sl_order_id to never match live_order_ids — false "stale"
                    # detection. Each restart would add another duplicate emergency SL.
                    # Fix: if exchange has live SL orders covering the position, reconstruct
                    # layers from exchange orders instead of submitting duplicate emergency SL.
                    if live_sl_types:
                        self.log.warning(
                            f"⚠️ Cross-validation: {len(stale_layers)} layer(s) have unmatched SL IDs, "
                            f"but exchange has {len(live_sl_types)} live SL orders "
                            f"(NT={len(nt_order_ids)}, Binance={len(binance_live_order_ids)}). "
                            f"ID format mismatch (Algo API algoId vs NT clientOrderId). "
                            f"Position IS protected — skipping emergency SL, rebuilding tracking layer."
                        )
                        # Clear stale persisted layers
                        for lid in stale_layers:
                            removed = self._layer_orders.pop(lid, None)
                            if removed:
                                for key in ('sl_order_id', 'tp_order_id', 'trailing_order_id'):
                                    oid = removed.get(key)
                                    if oid and oid in self._order_to_layer:
                                        del self._order_to_layer[oid]
                                self.log.info(
                                    f"🧹 Stale layer {lid} removed (qty: {removed.get('quantity', 0):.4f})"
                                )
                        # Create a tracking-only recovery layer using Binance SL price.
                        # The SL order already exists on exchange — we don't know its NT
                        # client_order_id (Algo API returns algoId), so sl_order_id stays
                        # empty. This is acceptable: the layer is for tracking only, and
                        # the exchange SL will fire regardless of layer system.
                        sl_price_recovery = binance_best_sl_price or (
                            entry_px * (1 - 0.02) if side.lower() == 'long' else entry_px * (1 + 0.02)
                        )
                        tp_price_recovery = binance_best_tp_price  # 0 if not found
                        recovery_layer_id = f"layer_{self._next_layer_idx}"
                        self._layer_orders[recovery_layer_id] = {
                            'entry_price': entry_px,
                            'quantity': quantity,
                            'side': side.lower(),
                            'sl_order_id': '',  # Can't match Algo API ID to NT ID
                            'tp_order_id': '',
                            'trailing_order_id': '',
                            'sl_price': sl_price_recovery,
                            'tp_price': tp_price_recovery,
                            'trailing_offset_bps': 0,
                            'trailing_activation_price': 0.0,
                            'highest_price': entry_px,
                            'lowest_price': entry_px,
                            'confidence': 'RECOVERED',
                            'timestamp': '',
                            'layer_index': self._next_layer_idx,
                        }
                        self._next_layer_idx += 1
                        self._update_aggregate_sltp_state()
                        self._persist_layer_orders()
                        self.log.info(
                            f"✅ Recovery layer created: {side} {quantity:.4f} BTC, "
                            f"SL=${sl_price_recovery:,.2f}, TP=${tp_price_recovery:,.2f} (from Binance), "
                            f"{len(live_sl_types)} live SL orders on exchange"
                        )
                    else:
                        # No live SL on exchange — position is truly unprotected
                        self.log.warning(
                            f"🚨 Cross-validation: {len(stale_layers)} layer(s) have stale/missing SL "
                            f"(uncovered {uncovered_qty:.4f} BTC). No live SL on exchange! "
                            f"(NT={len(nt_order_ids)}, Binance={len(binance_live_order_ids)}). "
                            f"Submitting emergency SL..."
                        )
                        self._submit_emergency_sl(
                            quantity=uncovered_qty,
                            position_side=side,
                            reason=f"重启交叉验证: {len(stale_layers)}层SL在交易所已不存在",
                        )
                        # v15.5: Remove stale layers — emergency SL creates its own layer.
                        for lid in stale_layers:
                            removed = self._layer_orders.pop(lid, None)
                            if removed:
                                for key in ('sl_order_id', 'tp_order_id', 'trailing_order_id'):
                                    oid = removed.get(key)
                                    if oid and oid in self._order_to_layer:
                                        del self._order_to_layer[oid]
                                self.log.info(
                                    f"🧹 Stale layer {lid} removed (qty: {removed.get('quantity', 0):.4f})"
                                )
                        self._persist_layer_orders()
                else:
                    self.log.info(
                        f"✅ Cross-validation passed: all {len(self._layer_orders)} layer SL orders "
                        f"confirmed alive on exchange (NT+Binance union check)"
                    )

                # v24.0: Resubmit stale TP orders at original planned price
                # (Previously v15.4 only cleared stale references without recovery)
                if stale_tp_layers:
                    for lid in stale_tp_layers:
                        # v15.5: Layer may have been removed above if it also had stale SL
                        if lid not in self._layer_orders:
                            continue
                        old_tp = self._layer_orders[lid].get('tp_order_id', '')
                        original_tp_price = self._layer_orders[lid].get('tp_price', 0)
                        # Clear stale reference first
                        self._layer_orders[lid]['tp_order_id'] = ''
                        if old_tp and old_tp in self._order_to_layer:
                            del self._order_to_layer[old_tp]
                        tp_desc = f"{old_tp[:8]}... not alive on exchange" if old_tp else "never submitted"
                        self.log.warning(
                            f"🧹 Layer {self._layer_orders[lid].get('layer_index', '?')}: "
                            f"TP {tp_desc}, attempting resubmit"
                        )
                        # v24.0: Resubmit at original planned price
                        if original_tp_price > 0:
                            self._resubmit_tp_for_layer(lid, original_tp_price)
                    self._persist_layer_orders()

                # v24.2: Backfill trailing for layers that were created before
                # trailing stop support. Submits TRAILING_STOP_MARKET for any
                # layer that has a fixed SL but no trailing order.
                self._backfill_trailing_for_existing_layers(side, entry_px)
                return

            # No persisted layers — reconstruct from open orders
            if has_sl:
                self.log.info(
                    f"📌 No persisted layers, reconstructing from {len(reduce_only_orders)} orders"
                )
                # v24.0: Separate fixed SL, trailing, and TP orders
                fixed_sl_orders = []
                trailing_orders = []
                tp_orders = []
                for o in reduce_only_orders:
                    if o.order_type == OrderType.TRAILING_STOP_MARKET:
                        trailing_orders.append(o)
                    elif o.order_type == OrderType.STOP_MARKET:
                        fixed_sl_orders.append(o)
                    else:
                        tp_orders.append(o)

                # Create one layer per fixed SL order (primary grouping key)
                trailing_pool = list(trailing_orders)
                for i, sl_o in enumerate(fixed_sl_orders):
                    sl_qty = float(sl_o.quantity)
                    sl_price = float(sl_o.trigger_price) if hasattr(sl_o, 'trigger_price') else 0
                    sl_id = str(sl_o.client_order_id)

                    # Try to find matching TP by quantity
                    tp_id = ""
                    tp_price = 0
                    for tp_o in tp_orders:
                        tp_qty = float(tp_o.quantity)
                        if abs(tp_qty - sl_qty) / max(sl_qty, 0.001) < 0.05:
                            tp_price = float(tp_o.price) if hasattr(tp_o, 'price') else 0
                            tp_id = str(tp_o.client_order_id)
                            tp_orders.remove(tp_o)
                            break

                    # Try to find matching trailing by quantity
                    trailing_id = ""
                    trailing_bps = 0
                    trailing_act_price = 0.0
                    for tr_o in trailing_pool:
                        tr_qty = float(tr_o.quantity)
                        if abs(tr_qty - sl_qty) / max(sl_qty, 0.001) < 0.05:
                            trailing_id = str(tr_o.client_order_id)
                            if hasattr(tr_o, 'trailing_offset') and tr_o.trailing_offset:
                                trailing_bps = int(float(tr_o.trailing_offset))
                            if hasattr(tr_o, 'activation_price') and tr_o.activation_price:
                                trailing_act_price = float(tr_o.activation_price)
                            trailing_pool.remove(tr_o)
                            break

                    layer_id = f"layer_{i}"
                    self._layer_orders[layer_id] = {
                        'entry_price': entry_px,
                        'quantity': sl_qty,
                        'side': side.lower(),
                        'sl_order_id': sl_id,
                        'tp_order_id': tp_id,
                        'trailing_order_id': trailing_id,
                        'sl_price': sl_price,
                        'tp_price': tp_price,
                        'trailing_offset_bps': trailing_bps,
                        'trailing_activation_price': trailing_act_price,
                        'highest_price': entry_px,
                        'lowest_price': entry_px,
                        'confidence': 'MEDIUM',
                        'timestamp': '',
                        'layer_index': i,
                    }
                    if sl_id:
                        self._order_to_layer[sl_id] = layer_id
                    if tp_id:
                        self._order_to_layer[tp_id] = layer_id
                    if trailing_id:
                        self._order_to_layer[trailing_id] = layer_id

                # Any remaining trailing orders without a matching fixed SL
                # still need a layer (they ARE the SL protection)
                offset = len(fixed_sl_orders)
                for j, tr_o in enumerate(trailing_pool):
                    tr_qty = float(tr_o.quantity)
                    tr_id = str(tr_o.client_order_id)
                    tr_price = float(tr_o.trigger_price) if hasattr(tr_o, 'trigger_price') and tr_o.trigger_price else 0

                    tp_id = ""
                    tp_price = 0
                    for tp_o in tp_orders:
                        tp_qty = float(tp_o.quantity)
                        if abs(tp_qty - tr_qty) / max(tr_qty, 0.001) < 0.05:
                            tp_price = float(tp_o.price) if hasattr(tp_o, 'price') else 0
                            tp_id = str(tp_o.client_order_id)
                            tp_orders.remove(tp_o)
                            break

                    layer_id = f"layer_{offset + j}"
                    # v24.2-fix: Trailing-only layer — sl_order_id is empty because
                    # there is no separate fixed SL. The trailing IS the SL protection.
                    # Previously sl_order_id=tr_id caused reverse mapping conflict.
                    trailing_bps = 0
                    trailing_act_price = 0.0
                    if hasattr(tr_o, 'trailing_offset') and tr_o.trailing_offset:
                        trailing_bps = int(float(tr_o.trailing_offset))
                    if hasattr(tr_o, 'activation_price') and tr_o.activation_price:
                        trailing_act_price = float(tr_o.activation_price)
                    self._layer_orders[layer_id] = {
                        'entry_price': entry_px,
                        'quantity': tr_qty,
                        'side': side.lower(),
                        'sl_order_id': '',
                        'tp_order_id': tp_id,
                        'trailing_order_id': tr_id,
                        'sl_price': tr_price,
                        'tp_price': tp_price,
                        'trailing_offset_bps': trailing_bps,
                        'trailing_activation_price': trailing_act_price,
                        'highest_price': entry_px,
                        'lowest_price': entry_px,
                        'confidence': 'MEDIUM',
                        'timestamp': '',
                        'layer_index': offset + j,
                    }
                    if tr_id:
                        self._order_to_layer[tr_id] = layer_id
                    if tp_id:
                        self._order_to_layer[tp_id] = layer_id

                self._next_layer_idx = len(self._layer_orders)  # Tier 3: sequential from 0
                self._update_aggregate_sltp_state()
                self._persist_layer_orders()
                self.log.info(
                    f"📌 Reconstructed {len(self._layer_orders)} layers from exchange orders "
                    f"({len(fixed_sl_orders)} SL + {len(trailing_orders)} trailing + "
                    f"{len(reduce_only_orders) - len(fixed_sl_orders) - len(trailing_orders)} TP)"
                )

                # v24.2: Backfill trailing for reconstructed layers without trailing
                self._backfill_trailing_for_existing_layers(side, entry_px)
                return

            # No SL found — position is UNPROTECTED!
            self.log.warning(
                f"🚨 Position {side} {quantity:.4f} BTC has NO SL/TP protection! "
                f"Creating emergency SL..."
            )
            self._submit_emergency_sl(
                quantity=quantity,
                position_side=side,
                reason="启动时检测到无保护仓位",
            )

        except Exception as e:
            self.log.error(f"❌ Failed to recover SL/TP on start: {e}")

    def on_stop(self):
        """Actions to be performed on strategy stop."""
        self.log.info("Stopping DeepSeek AI Strategy...")

        # v3.13: Send shutdown notification
        if (self.telegram_bot and self.enable_telegram and
            getattr(self, 'telegram_notify_shutdown', True)):
            try:
                # Calculate uptime
                uptime_str = "N/A"
                if self.strategy_start_time:
                    uptime_delta = datetime.now(timezone.utc) - self.strategy_start_time
                    hours = int(uptime_delta.total_seconds() // 3600)
                    minutes = int((uptime_delta.total_seconds() % 3600) // 60)
                    uptime_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"

                shutdown_msg = self.telegram_bot.format_shutdown_message({
                    'instrument_id': str(self.instrument_id),
                    'reason': 'normal',
                    'uptime': uptime_str,
                })
                # Use direct send (not queue) to ensure message is sent before shutdown
                # v14.0: Shutdown → private chat only (operational info)
                self.telegram_bot.send_message_sync(shutdown_msg, use_queue=False)
                self.log.info("📱 Sent shutdown notification to Telegram")
            except Exception as e:
                self.log.warning(f"Failed to send shutdown notification: {e}")

        # Stop Telegram message queue if running
        if self.telegram_bot:
            try:
                self.telegram_bot.stop_queue()
            except Exception as e:
                self.log.warning(f"Error stopping Telegram queue: {e}")

        # v4.12: Only cancel non-protective orders — keep SL/TP on Binance
        # Previously: cancel_all_orders() removed SL/TP, leaving position unprotected on restart.
        # Now: SL/TP (reduce_only orders) stay on Binance, protecting the position even when bot is off.
        try:
            open_orders = self.cache.orders_open(instrument_id=self.instrument_id)
            for order in open_orders:
                if not order.is_reduce_only:
                    self.cancel_order(order)
                    self.log.info(f"🗑️ Cancelled non-protective order on shutdown: {order.client_order_id}")
            protective_count = sum(1 for o in open_orders if o.is_reduce_only)
            if protective_count > 0:
                self.log.info(f"🛡️ Kept {protective_count} SL/TP orders on Binance for position protection")
        except Exception as e:
            self.log.warning(f"Error during selective order cleanup: {e}")
            # v7.3: Do NOT cancel all orders as fallback. The previous cancel_all_orders()
            # destroyed SL/TP protection during the most dangerous moment (a failing shutdown).
            # Leaving stale entry orders on Binance is harmless compared to losing SL protection.
            # SL/TP orders stay on exchange to protect the position while bot is offline.
            self.log.warning(
                "🛡️ Keeping all orders on Binance (including SL/TP) — "
                "failed to selectively cancel non-protective orders, "
                "but position protection takes priority over cleanup"
            )

        # v6.1: Clear pending state to prevent stale retry on restart
        self._pending_reversal = None
        with self._state_lock:
            self._pending_partial_close_broadcast = None  # v14.1
        self._forced_close_reason = None  # v14.1
        # Unsubscribe from data
        self.unsubscribe_bars(self.bar_type)

        # v3.16: Unsubscribe from trade ticks
        try:
            self.unsubscribe_trade_ticks(self.instrument_id)
        except Exception as e:
            self.log.debug(f"Ignore if not subscribed: {e}")
            pass  # Ignore if not subscribed

        self.log.info("Strategy stopped")

    def _calculate_next_aligned_time(self, interval_minutes: int = 20) -> datetime:
        """
        Calculate the next clock-aligned time point.

        For 20-minute interval, returns next 00/20/40 minute mark.
        For 5-minute interval, returns next 00/05/10/.../55 minute mark.

        Args:
            interval_minutes: Timer interval in minutes (default 20)

        Returns:
            datetime: Next aligned UTC time
        """
        now = datetime.now(timezone.utc)

        # Calculate next aligned minute
        current_minute = now.minute
        minutes_since_aligned = current_minute % interval_minutes
        minutes_to_next = interval_minutes - minutes_since_aligned

        if minutes_to_next == interval_minutes:
            # We're exactly at an aligned time, go to next interval
            minutes_to_next = interval_minutes

        # Calculate next aligned time (reset seconds and microseconds)
        next_time = now.replace(second=0, microsecond=0) + timedelta(minutes=minutes_to_next)

        return next_time

    def _update_real_balance(self) -> Dict[str, float]:
        """
        Fetch real account balance from Binance and update internal state.

        This method is called:
        - On strategy startup
        - Periodically by Telegram /risk command
        - Before position size calculation (optional)

        When use_real_balance_as_equity is True (default), this will automatically
        update self.equity to match the real Binance account balance.

        Returns
        -------
        dict
            Balance info with total_balance, available_balance, etc.
        """
        try:
            balance = self.binance_account.get_balance()

            if 'error' not in balance:
                self._real_balance = balance

                real_total = balance.get('total_balance', 0)
                if real_total > 0:
                    # Auto-update equity if enabled
                    if self.config.use_real_balance_as_equity:
                        old_equity = self.equity
                        self.equity = real_total
                        self._equity_synced = True  # v15.1: balance confirmed from Binance
                        # Also update position_config for position sizing
                        # max_position_ratio is based on equity
                        if abs(old_equity - real_total) > 1:
                            self.log.info(
                                f"💰 Equity auto-updated: ${old_equity:.2f} → ${real_total:.2f} "
                                f"(from Binance real balance)"
                            )
                    else:
                        # Just log if there's a significant difference
                        if abs(real_total - self.equity) > 10:
                            self.log.info(
                                f"💰 Real balance from Binance: ${real_total:.2f} "
                                f"(configured equity: ${self.equity:.2f})"
                            )

                return balance
            else:
                self.log.warning(f"Failed to fetch real balance: {balance.get('error')}")
                return {}

        except Exception as e:
            self.log.error(f"Error fetching real balance: {e}")
            return {}

    def _sync_binance_leverage(self) -> None:
        """
        v4.8: Sync leverage setting with Binance API.

        v6.0: Config is now source of truth. If Binance leverage differs from
        config, SET the Binance leverage to match config (prevents config changes
        from being silently overridden by stale Binance settings).
        """
        try:
            symbol = str(self.instrument_id)
            config_leverage = int(self.config.leverage)
            binance_leverage = self.binance_account.get_leverage(symbol)

            # v6.0: get_leverage() returns None on failure (not dangerous default 1)
            if binance_leverage is None:
                self.log.warning(
                    f"⚠️ Could not fetch leverage from Binance API, using config value: {config_leverage}x"
                )
                self.leverage = float(config_leverage)
            elif binance_leverage != config_leverage:
                self.log.warning(
                    f"⚠️ Leverage mismatch: Config={config_leverage}x, Binance={binance_leverage}x. "
                    f"Setting Binance to {config_leverage}x..."
                )
                success = self.binance_account.set_leverage(symbol, config_leverage)
                if success:
                    self.leverage = float(config_leverage)
                    self.log.info(f"✅ Binance leverage updated to {config_leverage}x (synced with config)")
                else:
                    # SET failed — use Binance's current value as fallback
                    self.leverage = float(binance_leverage)
                    self.log.error(
                        f"❌ Failed to set Binance leverage to {config_leverage}x, "
                        f"using current Binance value: {binance_leverage}x"
                    )
            else:
                self.leverage = float(binance_leverage)
                self.log.info(f"📊 Leverage synced: {binance_leverage}x (config and Binance match)")

        except Exception as e:
            self.log.error(f"Error syncing leverage: {e}")
            self.leverage = float(self.config.leverage)

    def _prefetch_historical_bars(self, limit: int = 200):
        """
        Pre-fetch historical bars from Binance API on startup.

        This eliminates the waiting period for indicators to initialize by loading
        historical data directly from Binance exchange on strategy startup.

        Parameters
        ----------
        limit : int
            Number of historical bars to fetch (default: 200)
        """
        try:
            from nautilus_trader.core.datetime import millis_to_nanos

            # Extract symbol from instrument_id
            # Example: BTCUSDT-PERP.BINANCE -> BTCUSDT
            symbol_str = str(self.instrument_id)
            symbol = symbol_str.split('-')[0]

            # Convert bar type to Binance interval
            # NOTE: Must check longer strings first (30-MINUTE before 5-MINUTE)
            bar_type_str = str(self.bar_type)
            if '30-MINUTE' in bar_type_str:
                interval = '30m'
            elif '15-MINUTE' in bar_type_str:
                interval = '15m'
            elif '5-MINUTE' in bar_type_str:
                interval = '5m'
            elif '1-MINUTE' in bar_type_str:
                interval = '1m'
            elif '4-HOUR' in bar_type_str:
                interval = '4h'
            elif '1-HOUR' in bar_type_str:
                interval = '1h'
            elif '1-DAY' in bar_type_str:
                interval = '1d'
            else:
                interval = '30m'  # Default fallback (v18.2: 30M execution layer)

            self.log.info(
                f"📡 Pre-fetching {limit} historical bars from Binance "
                f"(symbol={symbol}, interval={interval})..."
            )

            # Binance Futures API endpoint
            url = "https://fapi.binance.com/fapi/v1/klines"
            params = {
                'symbol': symbol,
                'interval': interval,
                'limit': min(limit, 1500),  # Binance max
            }

            response = requests.get(url, params=params, timeout=self.config.network_bar_persistence_timeout)
            response.raise_for_status()
            klines = response.json()

            if not klines:
                self.log.warning("⚠️ No bars received from Binance API")
                return

            # v6.5: Strip last (incomplete) bar — Binance API always returns the
            # current in-progress bar as the last element. Feeding it to indicators
            # causes volume artifacts (e.g., 0.03x volume ratio at bar start).
            if len(klines) > 1:
                klines = klines[:-1]

            self.log.info(f"📊 Received {len(klines)} completed bars from Binance")

            # Convert to NautilusTrader bars and feed to indicators
            bars_fed = 0
            for kline in klines:
                try:
                    # Create Bar object
                    bar = Bar(
                        bar_type=self.bar_type,
                        open=self.instrument.make_price(float(kline[1])),
                        high=self.instrument.make_price(float(kline[2])),
                        low=self.instrument.make_price(float(kline[3])),
                        close=self.instrument.make_price(float(kline[4])),
                        volume=self.instrument.make_qty(float(kline[5])),
                        ts_event=millis_to_nanos(kline[0]),
                        ts_init=millis_to_nanos(kline[0]),
                    )

                    # Feed to indicator manager
                    self.indicator_manager.update(bar)
                    bars_fed += 1

                except Exception as e:
                    self.log.warning(f"Failed to convert kline to bar: {e}")
                    continue

            self.log.info(
                f"✅ Pre-fetched {bars_fed} bars successfully! "
                f"Indicators ready: {self.indicator_manager.is_initialized()}"
            )

        except Exception as e:
            self.log.error(f"❌ Failed to pre-fetch bars from Binance: {e}")
            self.log.warning("Continuing with live bars only...")

    def on_bar(self, bar: Bar):
        """
        Handle bar updates.

        Parameters
        ----------
        bar : Bar
            The bar received
        """
        self.bars_received += 1

        # Multi-Timeframe routing (v3.2.8)
        if self.mtf_enabled and self.mtf_manager:
            layer = self.mtf_manager.route_bar(bar)
            if layer == "trend":
                # Trend layer (1D) only updates indicators, RISK state evaluated in on_timer
                self.log.debug(f"MTF: trend (1D) bar routed")
                return
            elif layer == "decision":
                # Decision layer (4H) data used by AI in on_timer, just recording here
                self.log.debug(f"[MTF] 4H bar 收盘，数据已更新 (AI 将在 on_timer 中使用)")
                return
            elif layer == "execution":
                # Update cached price for execution layer
                with self._state_lock:
                    self._cached_current_price = float(bar.close)
                    self._cached_current_price_time = time.time()
                # Continue to normal bar processing
            elif layer == "unknown":
                self.log.warning(f"MTF: Unknown bar type, falling back to single-timeframe")
                # Fall through to single-timeframe processing

        # Update technical indicators (single-timeframe mode)
        self.indicator_manager.update(bar)

        # Update cached price (thread-safe for Telegram commands)
        # This avoids accessing indicator_manager from Telegram thread which causes Rust panic
        with self._state_lock:
            self._cached_current_price = float(bar.close)
            self._cached_current_price_time = time.time()

        # v11.5: Update MAE/MFE tracking if position is open
        if self._position_entry_price_for_mae > 0:
            bar_high = float(bar.high)
            bar_low = float(bar.low)
            if bar_high > self._position_max_price:
                self._position_max_price = bar_high
            if bar_low < self._position_min_price:
                self._position_min_price = bar_low

        # v48.0: DCA trigger check on each execution-layer bar
        if (self._strategy_mode == 'mechanical'
                and self._dca_config.get('enabled')
                and hasattr(self, '_layer_orders') and self._layer_orders):
            self._check_dca_trigger(float(bar.close))

        # Log bar data
        if self.bars_received % 10 == 0:
            self.log.info(
                f"Bar #{self.bars_received}: "
                f"O:{bar.open} H:{bar.high} L:{bar.low} C:{bar.close} V:{bar.volume}"
            )

    def _check_dca_trigger(self, current_price: float):
        """v48.0: Check if price dropped enough from last entry to trigger DCA."""
        if not self._layer_orders:
            return
        dca_cfg = self._dca_config
        max_layers = dca_cfg.get('max_real_layers', 4)
        spacing_pct = dca_cfg.get('spacing_pct', 0.03)
        multiplier = dca_cfg.get('multiplier', 1.5)

        n_layers = len(self._layer_orders)
        if n_layers >= max_layers:
            # Virtual DCA: track theoretical avg for TP calculation
            self._update_virtual_dca(current_price, spacing_pct)
            return

        # Get last entry price
        layers = sorted(self._layer_orders.values(), key=lambda x: x.get('layer_index', 0))
        last_layer = layers[-1]
        last_entry = last_layer.get('entry_price', 0)
        if last_entry <= 0:
            return

        # Check position side
        position_side = last_layer.get('side', 'long').lower()
        if position_side == 'long':
            drop = (last_entry - current_price) / last_entry
        else:
            drop = (current_price - last_entry) / last_entry

        if drop < spacing_pct:
            return  # Not enough price movement

        # Risk controller gate: skip DCA if circuit breaker active
        try:
            from utils.risk_controller import TradingState
            risk_state = self.risk_controller.metrics.trading_state
            if risk_state != TradingState.ACTIVE:
                self.log.warning(
                    f"⚠️ DCA skipped: risk state={risk_state.name}, "
                    f"price=${current_price:,.0f} drop={drop:.1%}"
                )
                return
        except Exception:
            pass  # Risk controller not available, proceed

        # Calculate DCA quantity: multiplier × last layer qty (geometric scaling)
        last_qty = last_layer.get('quantity', 0)
        _step = float(self.instrument.size_increment)
        _step_inv = round(1.0 / _step)
        dca_qty = math.floor(last_qty * multiplier * _step_inv) / _step_inv

        # Min trade check + Binance min notional (dynamic from exchange filter)
        min_trade = float(self.instrument.min_quantity) if self.instrument.min_quantity else self.position_config.get('min_trade_amount', 0.001)
        min_notional = float(self.instrument.min_notional) * 1.01 if self.instrument.min_notional else 101.0
        min_qty_notional = min_notional / current_price if current_price > 0 else 0
        effective_min = max(min_trade, min_qty_notional)
        if dca_qty < effective_min:
            _step = float(self.instrument.size_increment)
            _step_inv = round(1.0 / _step)
            dca_qty = math.ceil(effective_min * _step_inv) / _step_inv
            self.log.info(
                f"📊 DCA qty bumped to min notional: {dca_qty:.6f} BTC "
                f"(${dca_qty * current_price:,.0f})"
            )

        # Calculate new real average price (for SL/TP)
        total_cost = sum(l.get('entry_price', 0) * l.get('quantity', 0) for l in layers)
        total_qty = sum(l.get('quantity', 0) for l in layers)
        new_cost = total_cost + current_price * dca_qty
        new_qty = total_qty + dca_qty
        new_avg = new_cost / new_qty if new_qty > 0 else current_price

        self.log.info(
            f"📈 DCA layer {n_layers+1}/{max_layers} | "
            f"price=${current_price:,.0f} drop={drop:.1%} from ${last_entry:,.0f} | "
            f"qty={dca_qty:.4f} BTC | avg ${layers[0].get('entry_price', 0):,.0f}→${new_avg:,.0f}"
        )

        # Submit DCA market order
        order_side = OrderSide.BUY if position_side == 'long' else OrderSide.SELL
        exit_side = OrderSide.SELL if position_side == 'long' else OrderSide.BUY

        try:
            self._submit_order(
                side=order_side,
                quantity=dca_qty,
                reduce_only=False,
            )
        except Exception as e:
            self.log.error(f"❌ DCA order submission failed: {e}")
            return

        # Calculate DCA SL/TP based on new real average
        tp_pct = dca_cfg.get('tp_pct', 0.025)
        sl_pct = dca_cfg.get('sl_pct', 0.06)
        virtual_avg = getattr(self, '_dca_virtual_avg', new_avg)

        from strategy.trading_logic import calculate_dca_sltp
        success, sl_price, tp_price, method = calculate_dca_sltp(
            real_avg_price=new_avg,
            virtual_avg_price=virtual_avg,
            side=position_side,
            tp_pct=tp_pct,
            sl_pct=sl_pct,
        )
        if not success:
            self.log.error(f"❌ DCA SL/TP calculation failed: {method}")
            self._submit_emergency_sl(
                quantity=dca_qty,
                position_side=position_side,
                reason="DCA SL/TP calculation failed"
            )
            return

        self.log.info(
            f"🎯 DCA SL/TP: avg=${new_avg:,.0f} | "
            f"SL=${sl_price:,.0f} ({sl_pct:.0%}) | TP=${tp_price:,.0f} ({tp_pct:.1%})"
        )

        # Submit SL for new DCA layer
        sl_order_id = ""
        try:
            sl_order = self.order_factory.stop_market(
                instrument_id=self.instrument_id,
                order_side=exit_side,
                quantity=self.instrument.make_qty(dca_qty),
                trigger_price=self.instrument.make_price(sl_price),
                trigger_type=TriggerType.LAST_PRICE,
                reduce_only=True,
            )
            self.submit_order(sl_order)
            sl_order_id = str(sl_order.client_order_id)
            self.log.info(f"✅ DCA SL: {exit_side.name} {dca_qty:.4f} @ ${sl_price:,.2f}")
        except Exception as e:
            self.log.error(f"❌ DCA SL submission failed: {e}")
            self._submit_emergency_sl(
                quantity=dca_qty,
                position_side=position_side,
                reason=f"DCA SL failed: {e}"
            )
            return

        # Submit TP for new DCA layer
        tp_order_id = ""
        try:
            tp_order = self.order_factory.limit_if_touched(
                instrument_id=self.instrument_id,
                order_side=exit_side,
                quantity=self.instrument.make_qty(dca_qty),
                price=self.instrument.make_price(tp_price),
                trigger_price=self.instrument.make_price(tp_price),
                trigger_type=TriggerType.LAST_PRICE,
                reduce_only=True,
            )
            self.submit_order(tp_order)
            tp_order_id = str(tp_order.client_order_id)
            self.log.info(f"✅ DCA TP: {exit_side.name} {dca_qty:.4f} @ ${tp_price:,.2f}")
        except Exception as e:
            self.log.error(f"❌ DCA TP submission failed: {e}")

        # Register new DCA layer
        if sl_order_id:
            self._create_layer(
                entry_price=current_price,
                quantity=dca_qty,
                side=position_side,
                sl_price=sl_price,
                tp_price=tp_price,
                sl_order_id=sl_order_id,
                tp_order_id=tp_order_id,
                confidence="LOW",
            )

        # Update ALL existing layers' SL/TP to match new average
        self._update_all_layers_dca_sltp(
            new_avg=new_avg,
            side=position_side,
            tp_pct=tp_pct,
            sl_pct=sl_pct,
            exclude_latest=True,
        )

        # Telegram notification
        if self.telegram_bot and self.enable_telegram:
            side_cn = '多' if position_side == 'long' else '空'
            try:
                self.telegram_bot.send_message_sync(
                    f"📈 DCA 加仓 (Layer {n_layers+1}/{max_layers})\n"
                    f"方向: {side_cn}\n"
                    f"价格: ${current_price:,.0f} (跌{drop:.1%})\n"
                    f"数量: {dca_qty:.4f} BTC\n"
                    f"均价: ${new_avg:,.0f}\n"
                    f"SL: ${sl_price:,.0f} | TP: ${tp_price:,.0f}",
                    broadcast=True,
                )
            except Exception as e:
                self.log.debug(f"DCA Telegram notification failed: {e}")

    def _update_all_layers_dca_sltp(
        self,
        new_avg: float,
        side: str,
        tp_pct: float,
        sl_pct: float,
        exclude_latest: bool = True,
    ):
        """
        v48.0: After DCA, recalculate and resubmit SL/TP for ALL existing layers.

        DCA changes the average price, so all layers should use the same
        SL/TP levels (based on the new average), not their individual entry prices.
        """
        if not self._layer_orders:
            return

        from strategy.trading_logic import calculate_dca_sltp
        virtual_avg = getattr(self, '_dca_virtual_avg', new_avg)
        success, new_sl, new_tp, _ = calculate_dca_sltp(
            real_avg_price=new_avg,
            virtual_avg_price=virtual_avg,
            side=side,
            tp_pct=tp_pct,
            sl_pct=sl_pct,
        )
        if not success:
            self.log.error("❌ Cannot recalculate DCA SL/TP for existing layers")
            return

        exit_side = OrderSide.SELL if side == 'long' else OrderSide.BUY

        layer_items = list(self._layer_orders.items())
        # Skip the latest layer (just created with correct SL/TP)
        if exclude_latest and len(layer_items) > 1:
            layer_items = layer_items[:-1]
        elif exclude_latest:
            return  # Only 1 layer, nothing to update

        updated = 0
        for layer_id, layer in layer_items:
            old_sl_id = layer.get('sl_order_id', '')
            old_tp_id = layer.get('tp_order_id', '')
            qty = layer.get('quantity', 0)
            if qty <= 0:
                continue

            # Cancel old SL
            if old_sl_id:
                try:
                    for order in self.cache.orders(self.instrument_id):
                        if str(order.client_order_id) == old_sl_id and order.is_open:
                            self._intentionally_cancelled_order_ids.add(old_sl_id)
                            self.cancel_order(order)
                            break
                except Exception as e:
                    self.log.warning(f"⚠️ Cancel old SL {old_sl_id[:8]}: {e}")

            # Cancel old TP
            if old_tp_id:
                try:
                    for order in self.cache.orders(self.instrument_id):
                        if str(order.client_order_id) == old_tp_id and order.is_open:
                            self._intentionally_cancelled_order_ids.add(old_tp_id)
                            self.cancel_order(order)
                            break
                except Exception as e:
                    self.log.warning(f"⚠️ Cancel old TP {old_tp_id[:8]}: {e}")

            # Resubmit SL at new DCA level
            new_sl_id = ""
            try:
                sl_order = self.order_factory.stop_market(
                    instrument_id=self.instrument_id,
                    order_side=exit_side,
                    quantity=self.instrument.make_qty(qty),
                    trigger_price=self.instrument.make_price(new_sl),
                    trigger_type=TriggerType.LAST_PRICE,
                    reduce_only=True,
                )
                self.submit_order(sl_order)
                new_sl_id = str(sl_order.client_order_id)
            except Exception as e:
                self.log.error(f"❌ DCA SL resubmit for {layer_id}: {e}")
                self._submit_emergency_sl(
                    quantity=qty,
                    position_side=side,
                    reason=f"DCA SL resubmit failed: {e}"
                )
                continue

            # Resubmit TP at new DCA level
            new_tp_id = ""
            try:
                tp_order = self.order_factory.limit_if_touched(
                    instrument_id=self.instrument_id,
                    order_side=exit_side,
                    quantity=self.instrument.make_qty(qty),
                    price=self.instrument.make_price(new_tp),
                    trigger_price=self.instrument.make_price(new_tp),
                    trigger_type=TriggerType.LAST_PRICE,
                    reduce_only=True,
                )
                self.submit_order(tp_order)
                new_tp_id = str(tp_order.client_order_id)
            except Exception as e:
                self.log.error(f"❌ DCA TP resubmit for {layer_id}: {e}")

            # Update layer records
            layer['sl_price'] = new_sl
            layer['tp_price'] = new_tp
            layer['sl_order_id'] = new_sl_id
            layer['tp_order_id'] = new_tp_id
            if new_sl_id:
                self._order_to_layer[new_sl_id] = layer_id
            if new_tp_id:
                self._order_to_layer[new_tp_id] = layer_id
            updated += 1

        if updated > 0:
            self._persist_layer_orders()
            self.log.info(
                f"✅ DCA SL/TP updated for {updated} existing layers: "
                f"SL=${new_sl:,.0f} TP=${new_tp:,.0f} (avg=${new_avg:,.0f})"
            )

    def _update_virtual_dca(self, current_price: float, spacing_pct: float):
        """
        v48.0: Track virtual DCA when max real layers exhausted.

        Virtual DCA doesn't submit orders, but tracks a theoretical average
        price that would lower the TP target, making it easier to hit.
        """
        virtual_avg = getattr(self, '_dca_virtual_avg', 0)
        virtual_last = getattr(self, '_dca_virtual_last_price', 0)

        if virtual_last <= 0:
            # Initialize from last real layer
            layers = sorted(self._layer_orders.values(), key=lambda x: x.get('layer_index', 0))
            if layers:
                virtual_last = layers[-1].get('entry_price', 0)
                self._dca_virtual_last_price = virtual_last

        if virtual_last <= 0:
            return

        position_side = list(self._layer_orders.values())[0].get('side', 'long').lower()
        if position_side == 'long':
            drop = (virtual_last - current_price) / virtual_last
        else:
            drop = (current_price - virtual_last) / virtual_last

        if drop < spacing_pct:
            return

        # Update virtual average
        if virtual_avg <= 0:
            # First virtual: compute from real layers
            layers = sorted(self._layer_orders.values(), key=lambda x: x.get('layer_index', 0))
            total_cost = sum(l.get('entry_price', 0) * l.get('quantity', 0) for l in layers)
            total_qty = sum(l.get('quantity', 0) for l in layers)
            virtual_avg = total_cost / total_qty if total_qty > 0 else current_price

        # Blend in virtual entry (no real qty, just avg tracking)
        self._dca_virtual_avg = (virtual_avg + current_price) / 2
        self._dca_virtual_last_price = current_price

        self.log.info(
            f"📊 Virtual DCA: price=${current_price:,.0f} drop={drop:.1%} | "
            f"virtual_avg=${self._dca_virtual_avg:,.0f}"
        )

    def on_trade_tick(self, tick):
        """
        v18.2: Handle trade tick events for real-time price monitoring.

        Updates _cached_current_price on every trade (previously only updated
        on bar close, causing up to 30-minute lag). Also monitors for large
        intra-bar price deviations and triggers early AI analysis.

        This runs on the NT event loop thread (same as on_timer/on_bar),
        so _cached_current_price updates are thread-safe with respect to
        the analysis pipeline. The _state_lock is only needed for Telegram
        thread access.
        """
        current_price = float(tick.price)

        # Update cached price (thread-safe for Telegram thread)
        with self._state_lock:
            self._cached_current_price = current_price
            self._cached_current_price_time = time.time()

        # Surge detection: check price deviation from last analysis
        if self._last_analysis_price and self._last_analysis_price > 0:
            deviation = abs(current_price - self._last_analysis_price) / self._last_analysis_price

            if (
                deviation >= self._SURGE_THRESHOLD_PCT
                and not self._surge_alert_scheduled
                and time.time() > self._surge_cooldown_until
            ):
                direction = "📉" if current_price < self._last_analysis_price else "📈"
                self.log.warning(
                    f"{direction} PRICE SURGE: {deviation:.2%} from last analysis "
                    f"(${self._last_analysis_price:,.0f} → ${current_price:,.0f}). "
                    f"Triggering early AI analysis."
                )
                self._surge_triggered = True
                self._surge_price_change_pct = (
                    (current_price - self._last_analysis_price)
                    / self._last_analysis_price
                )
                self._surge_alert_scheduled = True
                self._surge_cooldown_until = time.time() + self._SURGE_COOLDOWN_SEC

                # Schedule immediate analysis on event loop (same pattern as force_analysis)
                alert_name = f"surge_analysis_{int(time.time())}"
                try:
                    self.clock.set_time_alert(
                        name=alert_name,
                        alert_time=self.clock.utc_now() + timedelta(seconds=2),
                        callback=self.on_timer,
                    )
                except Exception as e:
                    self.log.warning(f"Surge analysis scheduling failed: {e}")
                    self._surge_alert_scheduled = False
                    self._surge_triggered = False

    def on_historical_data(self, data):
        """
        Handle historical data from request_bars() (v3.2.8).

        NautilusTrader calls this method when historical bars arrive
        from an asynchronous request_bars() call.

        Parameters
        ----------
        data : BarDataResponse
            Historical bar data response containing bars and bar_type
        """
        if not hasattr(data, 'bars') or not data.bars:
            self.log.warning("on_historical_data: Received empty or invalid data")
            return

        bars = data.bars
        bar_type = data.bar_type if hasattr(data, 'bar_type') else None

        if not self.mtf_enabled or not self.mtf_manager:
            # Single-timeframe mode: update indicator_manager
            for bar in bars:
                self.indicator_manager.update(bar)
            self.log.info(f"Historical data loaded: {len(bars)} bars")
            return

        # Multi-Timeframe mode: route to appropriate layer
        if bar_type == self.trend_bar_type:
            for bar in bars:
                self.mtf_manager.trend_manager.update(bar)
            self._mtf_trend_initialized = True
            self.log.info(f"MTF: 趋势层预取完成 ({len(bars)} bars)")

        elif bar_type == self.decision_bar_type:
            for bar in bars:
                self.mtf_manager.decision_manager.update(bar)
            self._mtf_decision_initialized = True
            self.log.info(f"MTF: 决策层预取完成 ({len(bars)} bars)")

        elif bar_type == self.execution_bar_type:
            for bar in bars:
                self.mtf_manager.execution_manager.update(bar)
            self._mtf_execution_initialized = True
            self.log.info(f"MTF: 执行层预取完成 ({len(bars)} bars)")

        else:
            # Unknown bar_type, update single-timeframe indicator
            for bar in bars:
                self.indicator_manager.update(bar)
            self.log.info(f"Historical data loaded (unknown type): {len(bars)} bars")

        # Check if all MTF layers are initialized
        if (self._mtf_trend_initialized and
            self._mtf_decision_initialized and
            self._mtf_execution_initialized):
            self._verify_mtf_initialization()

    def _verify_mtf_initialization(self):
        """Verify all MTF layers have sufficient data (v3.2.8)."""
        if not self.mtf_manager:
            return

        issues = []

        # Check trend layer (needs 200 bars for SMA_200)
        if self.mtf_manager.trend_manager:
            trend_bars = len(self.mtf_manager.trend_manager.recent_bars) if hasattr(self.mtf_manager.trend_manager, 'recent_bars') else 0
            if trend_bars < 200:
                issues.append(f"趋势层 bars 不足: {trend_bars}/200")

        # Check decision layer (needs 50 bars for SMA_50)
        if self.mtf_manager.decision_manager:
            decision_bars = len(self.mtf_manager.decision_manager.recent_bars) if hasattr(self.mtf_manager.decision_manager, 'recent_bars') else 0
            if decision_bars < 50:
                issues.append(f"决策层 bars 不足: {decision_bars}/50")

        # Check execution layer (needs 20 bars)
        if self.mtf_manager.execution_manager:
            exec_bars = len(self.mtf_manager.execution_manager.recent_bars) if hasattr(self.mtf_manager.execution_manager, 'recent_bars') else 0
            if exec_bars < 20:
                issues.append(f"执行层 bars 不足: {exec_bars}/20")

        if issues:
            self.log.warning(f"MTF 初始化警告: {', '.join(issues)}")
            if self.telegram_bot and self.enable_telegram:
                self.telegram_bot.send_message_sync(
                    f"⚠️ MTF 初始化警告:\n" + "\n".join(f"• {i}" for i in issues)
                    + f"\n\n⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
                )
        else:
            self.log.info("MTF: 所有层指标管理器初始化完成 ✓")

    def _prefetch_multi_timeframe_bars(self):
        """
        Prefetch historical bars for all MTF layers using direct Binance API.

        v3.2.10: Fixed to use direct Binance API calls instead of NautilusTrader
        request_bars() which doesn't work with EXTERNAL data sources.

        Uses the same approach as _prefetch_historical_bars() for reliability.
        """
        if not self.mtf_enabled or not self.mtf_manager:
            return

        self.log.info("MTF: 开始预取历史数据 (直接 Binance API)...")

        try:
            from nautilus_trader.core.datetime import millis_to_nanos

            # Extract symbol from instrument_id (BTCUSDT-PERP.BINANCE -> BTCUSDT)
            symbol_str = str(self.instrument_id)
            symbol = symbol_str.split('-')[0]

            # Binance Futures API endpoint
            url = "https://fapi.binance.com/fapi/v1/klines"
            timeout = self.config.network_bar_persistence_timeout

            # === Trend Layer (1D) - SMA_200 needs 220 bars ===
            self.log.info(f"MTF: 预取趋势层 (1D, 220 bars)...")
            trend_bars = self._fetch_binance_klines(
                url, symbol, '1d', 220, timeout,
                self.trend_bar_type, self.mtf_manager.trend_manager
            )
            if trend_bars > 0:
                self._mtf_trend_initialized = True
                self.log.info(f"✅ MTF 趋势层预取完成: {trend_bars} bars")

            # === Decision Layer (4H) - SMA_50, MACD need 60 bars ===
            self.log.info(f"MTF: 预取决策层 (4H, 60 bars)...")
            decision_bars = self._fetch_binance_klines(
                url, symbol, '4h', 60, timeout,
                self.decision_bar_type, self.mtf_manager.decision_manager
            )
            if decision_bars > 0:
                self._mtf_decision_initialized = True
                self.log.info(f"✅ MTF 决策层预取完成: {decision_bars} bars")

            # === Execution Layer (30M) - RSI, EMA need 40 bars ===
            self.log.info(f"MTF: 预取执行层 (30M, 40 bars)...")
            execution_bars = self._fetch_binance_klines(
                url, symbol, '30m', 40, timeout,
                self.execution_bar_type, self.mtf_manager.execution_manager
            )
            if execution_bars > 0:
                self._mtf_execution_initialized = True
                self.log.info(f"✅ MTF 执行层预取完成: {execution_bars} bars")

            # Summary
            self.log.info(
                f"✅ MTF 历史数据预取完成: "
                f"趋势={trend_bars}, 决策={decision_bars}, 执行={execution_bars}"
            )

        except Exception as e:
            self.log.error(f"❌ MTF 预取历史数据失败: {e}")
            self.log.warning("MTF 将使用实时数据初始化 (需要等待更长时间)")

    def _fetch_binance_klines(self, url, symbol, interval, limit, timeout, bar_type, indicator_manager):
        """
        Fetch klines from Binance API and feed to indicator manager.

        Returns number of bars successfully fed.
        """
        from nautilus_trader.core.datetime import millis_to_nanos

        try:
            params = {
                'symbol': symbol,
                'interval': interval,
                'limit': min(limit, 1500),  # Binance max
            }

            response = requests.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            klines = response.json()

            if not klines:
                self.log.warning(f"⚠️ Binance API 返回空数据 (interval={interval})")
                return 0

            # v6.5: Strip last (incomplete) bar — Binance API always returns the
            # current in-progress bar as the last element.
            if len(klines) > 1:
                klines = klines[:-1]

            bars_fed = 0
            for kline in klines:
                try:
                    bar = Bar(
                        bar_type=bar_type,
                        open=self.instrument.make_price(float(kline[1])),
                        high=self.instrument.make_price(float(kline[2])),
                        low=self.instrument.make_price(float(kline[3])),
                        close=self.instrument.make_price(float(kline[4])),
                        volume=self.instrument.make_qty(float(kline[5])),
                        ts_event=millis_to_nanos(kline[0]),
                        ts_init=millis_to_nanos(kline[0]),
                    )
                    indicator_manager.update(bar)
                    bars_fed += 1
                except Exception as e:
                    self.log.debug(f"转换 kline 失败: {e}")
                    continue

            return bars_fed

        except Exception as e:
            self.log.error(f"Binance API 请求失败 ({interval}): {e}")
            return 0

    def on_timer(self, event):
        """
        Periodic analysis and trading logic.

        Called every timer_interval_sec seconds (default: 20 minutes).
        """
        # 🔒 Fix I38: Prevent re-entry if previous on_timer is still running
        # (e.g., AI calls take longer than timer_interval_sec)
        if not self._timer_lock.acquire(blocking=False):
            self.log.warning("⚠️ Previous on_timer still running, skipping this cycle")
            return

        try:
            # v18.2: Capture and reset surge trigger state for this cycle.
            # Only consume surge state if THIS call IS the surge-scheduled timer
            # (or if no surge alert is pending). Prevents normal timer from
            # consuming surge context that the 2s-delayed surge timer needs.
            if self._surge_triggered and not self._surge_alert_scheduled:
                # Surge was triggered but alert already fired (we ARE the alert)
                _is_surge_analysis = True
                _surge_pct = self._surge_price_change_pct
                self._surge_triggered = False
                self._surge_price_change_pct = 0.0
            elif self._surge_triggered and self._surge_alert_scheduled:
                # Surge is pending (normal timer arrived before surge alert)
                # Let the surge alert handle it — run this as a normal cycle
                _is_surge_analysis = False
                _surge_pct = 0.0
            else:
                _is_surge_analysis = False
                _surge_pct = 0.0
            self._surge_alert_scheduled = False
            if _is_surge_analysis:
                self.log.info(f"⚡ SURGE-TRIGGERED analysis (price change: {_surge_pct:+.2%})")

            # v5.13: Reset per-cycle SL/TP guard flag
            self._sltp_modified_this_cycle = False
            # Reset TP resubmit rate-limit flags for all layers
            for _attr in list(vars(self)):
                if _attr.startswith("_tp_resubmit_attempted_"):
                    delattr(self, _attr)
            # v5.13: Clear stale intentional cancel IDs from previous cycle.
            # Events should have arrived by now (20 min cycle); if not, discard to
            # prevent unbounded set growth. Safety: orphan detection resumes next cycle.
            if self._intentionally_cancelled_order_ids:
                self.log.debug(
                    f"🧹 Clearing {len(self._intentionally_cancelled_order_ids)} stale "
                    f"intentional cancel IDs from previous cycle"
                )
                self._intentionally_cancelled_order_ids.clear()

            # v7.1: Re-check position protection if previous emergency close failed
            if self._needs_emergency_review:
                self._needs_emergency_review = False
                self.log.warning("🚨 Emergency review: re-checking position protection after previous failure")
                try:
                    current_pos = self._get_current_position_data()
                    if current_pos:
                        self._resubmit_sltp_if_needed(current_pos, "emergency_review")
                except Exception as e:
                    self.log.error(f"🚨 Emergency review failed: {e}")

            # v34.1: HOLD counterfactual — evaluate previous HOLD decision
            # v34.2: Persist verdict to data/hold_counterfactuals.json for Layer 3 analysis
            if self._hold_counterfactual_record:
                try:
                    with self._state_lock:
                        _cf_current_px = self._cached_current_price
                    _cf = self._hold_counterfactual_record
                    _cf_px = _cf.get('price', 0)
                    _cf_signal = _cf.get('proposed_signal', '')
                    _cf_source = _cf.get('hold_source', 'unknown')
                    if _cf_px and _cf_current_px:
                        _cf_change_pct = (_cf_current_px - _cf_px) / _cf_px * 100
                        # Good HOLD: price moved AGAINST proposed direction
                        # e.g., HOLD instead of LONG + price fell = correct HOLD
                        _cf_correct = (
                            (_cf_signal == 'LONG' and _cf_change_pct < -0.1) or
                            (_cf_signal == 'SHORT' and _cf_change_pct > 0.1)
                        )
                        _cf_verdict = "correct" if _cf_correct else "wrong" if abs(_cf_change_pct) > 0.1 else "neutral"
                        self.log.info(
                            f"📊 HOLD counterfactual: proposed={_cf_signal} source={_cf_source}, "
                            f"price_change={_cf_change_pct:+.2f}%, verdict={_cf_verdict}"
                        )
                        # v34.2: Persist to file for Layer 3 correlation analysis
                        self._persist_hold_counterfactual({
                            'timestamp': _cf.get('timestamp', ''),
                            'eval_timestamp': datetime.now(timezone.utc).isoformat(),
                            'proposed_signal': _cf_signal,
                            'hold_source': _cf_source,
                            'entry_price': _cf_px,
                            'eval_price': _cf_current_px,
                            'price_change_pct': round(_cf_change_pct, 4),
                            'verdict': _cf_verdict,
                        })
                except Exception as e:
                    self.log.debug(f"HOLD counterfactual evaluation failed: {e}")
                self._hold_counterfactual_record = None

            self.log.info("=" * 60)
            self.log.info("Running periodic analysis...")

            # v2.1: Increment timer counter for heartbeat tracking
            self._timer_count = getattr(self, '_timer_count', 0) + 1

            # v11.5: Track daily/weekly starting equity for report PnL% calculation
            try:
                utc_now = datetime.now(timezone.utc)
                utc_date = utc_now.strftime('%Y-%m-%d')
                utc_week = utc_now.strftime('%Y-W%W')
                if utc_date != self._last_equity_date:
                    # New UTC day — snapshot equity for daily report
                    # v15.6: Guard against using config default (1000) as snapshot.
                    # If _equity_synced is False and API also fails, skip snapshot
                    # to avoid corrupting PnL% calculations.
                    if self.binance_account:
                        bal = self.binance_account.get_balance()
                        eq = bal.get('total_balance', 0)
                    else:
                        eq = 0
                    if eq <= 0:
                        eq = self.equity if getattr(self, '_equity_synced', False) else 0
                    if eq <= 0:
                        self.log.warning(
                            "⚠️ Equity snapshot skipped: no confirmed balance from Binance"
                        )
                        # Don't update _last_equity_date — retry next cycle
                        raise ValueError("equity not confirmed")
                    self._daily_starting_equity = eq
                    self._last_equity_date = utc_date
                    self._signals_generated_today = 0
                    self._signals_executed_today = 0
                    self.log.info(f"📊 Daily equity snapshot: ${eq:,.2f} (UTC {utc_date})")
                    if utc_week != self._last_equity_week:
                        self._weekly_starting_equity = eq
                        self._last_equity_week = utc_week
                        self.log.info(f"📊 Weekly equity snapshot: ${eq:,.2f} (UTC {utc_week})")
                    self._save_equity_snapshots()
            except Exception as e:
                self.log.debug(f"Equity snapshot skipped: {e}")

            # v2.1: Send heartbeat - moved to start of on_timer to ensure delivery
            # Even if subsequent analysis fails, user knows the server is running
            self._send_heartbeat_notification()

            # v3.13: Check if scheduled summaries (daily/weekly) need sending
            self._check_scheduled_summaries()

            # v12.0: Process pending reflections (LLM-based deep reflection)
            # Runs before AI analysis so reflections are available in memory for current cycle
            self._process_pending_reflections()

            # v4.17: Cancel unfilled LIMIT entry orders from previous cycle
            # LIMIT entries may not fill if price moved away. Cancel before new analysis
            # to avoid stale orders conflicting with new signals.
            self._cancel_pending_entry_order()

            # Check if indicators are ready
            if not self.indicator_manager.is_initialized():
                self.log.warning("Indicators not yet initialized, skipping analysis")
                return

            # ========== MTF Initialization Check ==========
            # If MTF enabled, check all three layers are initialized
            if self.mtf_enabled and self.mtf_manager:
                if not self.mtf_manager.is_all_layers_initialized():
                    self.log.warning("[MTF] 多时间框架未完全初始化，跳过分析")
                    self.log.debug(f"[MTF] 初始化状态: trend={self._mtf_trend_initialized}, "
                                  f"decision={self._mtf_decision_initialized}, "
                                  f"execution={self._mtf_execution_initialized}")
                    return

            # Get current market data
            current_bar = self.indicator_manager.recent_bars[-1] if self.indicator_manager.recent_bars else None
            if not current_bar:
                self.log.warning("No bars available for analysis")
                return

            # v18.2: For surge-triggered analysis, use real-time tick price instead of
            # last bar close. The bar close can be up to 30 minutes old, while the tick
            # price reflects the actual current price that triggered the surge.
            _bar_close_price = float(current_bar.close)

            if _is_surge_analysis and self._cached_current_price > 0:
                current_price = self._cached_current_price
                self.log.info(
                    f"⚡ Using real-time tick price ${current_price:,.2f} "
                    f"(bar close was ${_bar_close_price:,.2f}, "
                    f"delta: {((current_price - _bar_close_price) / _bar_close_price):+.2%})"
                )
            else:
                current_price = _bar_close_price

            # Get technical data (30M - execution layer)
            try:
                technical_data = self.indicator_manager.get_technical_data(current_price)
                self.log.debug(f"Technical data (30M) retrieved: {len(technical_data)} indicators")
            except Exception as e:
                self.log.error(f"Failed to get technical data: {e}")
                return

            # v18.2: Inject surge context into technical data for AI awareness
            if _is_surge_analysis:
                bar_age_sec = time.time() - (float(current_bar.ts_event) / 1e9) if hasattr(current_bar, 'ts_event') else 0
                technical_data['price_surge_alert'] = {
                    'triggered': True,
                    'price_change_pct': round(_surge_pct * 100, 2),
                    'current_price': current_price,
                    'last_bar_close': _bar_close_price,
                    'bar_age_minutes': round(bar_age_sec / 60, 1) if bar_age_sec > 0 else 'unknown',
                    'note': (
                        f"SURGE ALERT: Price moved {_surge_pct:+.2%} since last analysis. "
                        f"Technical indicators are from the last closed bar "
                        f"(may be stale). Current price ${current_price:,.2f} is real-time. "
                        f"Weight current price and external data (FR/OI/sentiment) more heavily."
                    ),
                }

            # Get 4H decision layer technical data for AI debate (MTF Phase 2)
            decision_layer_data = None
            if self.mtf_enabled and self.mtf_manager:
                try:
                    decision_layer_data = self.mtf_manager.get_technical_data_for_layer("decision", current_price)
                    if decision_layer_data.get('_initialized', True):
                        self.log.info(
                            f"[MTF] 决策层 (4H) 数据: RSI={decision_layer_data.get('rsi', 0):.1f}, "
                            f"MACD={decision_layer_data.get('macd', 0):.2f}, "
                            f"SMA_20={decision_layer_data.get('sma_20', 0):.2f}"
                        )
                    else:
                        self.log.warning("[MTF] 决策层 (4H) 未完全初始化，使用 30M 数据")
                        decision_layer_data = None
                except Exception as e:
                    self.log.warning(f"[MTF] 获取决策层数据失败: {e}")
                    decision_layer_data = None

            # Get K-line data
            kline_data = self.indicator_manager.get_kline_data(count=10)
            self.log.debug(f"Retrieved {len(kline_data)} K-lines for analysis")

            # v7.0: Unified external data fetch (Single Source of Truth)
            # Replaces ~150 lines of inline API calls with data_assembler.fetch_external_data()
            # Logic is 1:1 identical — moved to utils/ai_data_assembler.py
            external_data = self.data_assembler.fetch_external_data(
                symbol="BTCUSDT",
                interval="30m",  # v18 Item 15: 15M→30M migration
                current_price=current_price,
                volatility=technical_data.get('bb_bandwidth', 0.02),
            )
            sentiment_data = external_data['sentiment_report']
            order_flow_data = external_data['order_flow_report']
            order_flow_data_4h = external_data.get('order_flow_report_4h')  # v18 Item 16
            derivatives_data = external_data['derivatives_report']
            orderbook_data = external_data['orderbook_report']
            binance_derivatives_data = external_data['binance_derivatives_report']
            fear_greed_data = external_data.get('fear_greed_report')  # v44.0: Fear & Greed Index
            self._cached_fear_greed = fear_greed_data  # Cache for heartbeat/commands

            # --- Logging & heartbeat storage (preserved from original inline code) ---

            # Sentiment
            if sentiment_data.get('source') != 'default_neutral':
                if self.sentiment_fetcher:
                    self.log.info(self.sentiment_fetcher.format_for_display(sentiment_data))
            else:
                self.log.info("📊 Using neutral sentiment data (no data available)")

            # Order flow
            if order_flow_data:
                self.latest_order_flow_data = order_flow_data
                self.log.info(
                    f"📊 Order Flow: buy_ratio={order_flow_data.get('buy_ratio', 0):.1%}, "
                    f"cvd_trend={order_flow_data.get('cvd_trend', 'N/A')}"
                )

            # Derivatives (Coinalyze OI + Binance FR already injected)
            if derivatives_data:
                self.latest_derivatives_data = derivatives_data
                oi = derivatives_data.get('open_interest')
                if oi:
                    self.log.info(f"📊 Derivatives: OI={oi.get('value', 0):.2f} BTC")
                fr = derivatives_data.get('funding_rate')
                if fr:
                    self.log.info(
                        f"📊 Funding Rate (Binance): settled={fr.get('settled_pct', fr.get('current_pct', 0)):.5f}%, "
                        f"predicted={fr.get('predicted_rate_pct', 'N/A')}"
                    )

            # Order book
            if orderbook_data:
                if orderbook_data.get('_status', {}).get('code') == 'OK':
                    self.latest_orderbook_data = orderbook_data
                    obi = orderbook_data.get('obi', {})
                    self.log.info(
                        f"📖 Order Book: OBI={obi.get('simple', 0):+.2f} "
                        f"(weighted={obi.get('weighted', 0):+.2f}), "
                        f"spread={orderbook_data.get('liquidity', {}).get('spread_pct', 0):.4f}%"
                    )
                else:
                    status_msg = orderbook_data.get('_status', {}).get('message', 'Unknown error')
                    self.log.warning(f"⚠️ Order Book: {status_msg}")

            # Binance derivatives (Top Traders)
            if binance_derivatives_data:
                top_pos = binance_derivatives_data.get('top_long_short_position', {})
                latest_top = top_pos.get('latest')
                if latest_top:
                    self.log.info(
                        f"📊 Binance Derivatives: Top Traders L/S={float(latest_top.get('longShortRatio', 1)):.2f}"
                    )

            # v6.6: Data quality gate — warn AI when critical data sources are unavailable.
            # AI still proceeds but the quality flag is passed to prompts so agents
            # know which data is reliable and which is default/missing.
            _data_quality_warnings = []
            if sentiment_data.get('source') == 'default_neutral':
                _data_quality_warnings.append("sentiment=neutral_default(API failure)")
            if not order_flow_data:
                _data_quality_warnings.append("order_flow=unavailable")
            if not derivatives_data:
                _data_quality_warnings.append("derivatives=unavailable")
            if not orderbook_data or orderbook_data.get('_status', {}).get('code') != 'OK':
                _data_quality_warnings.append("orderbook=unavailable")
            if _data_quality_warnings:
                self.log.warning(
                    f"⚠️ Data quality: {len(_data_quality_warnings)} source(s) degraded: "
                    f"{', '.join(_data_quality_warnings)}"
                )

            # Build price data for AI (v3.6: add period statistics)
            period_stats = self._calculate_period_statistics()
            price_data = {
                'price': current_price,
                'timestamp': self.clock.utc_now().isoformat(),
                'high': float(current_bar.high),
                'low': float(current_bar.low),
                'volume': float(current_bar.volume),
                'price_change': self._calculate_price_change(),
                'kline_data': kline_data,
                # v3.6: Period statistics (based on available bar history)
                'period_high': period_stats['period_high'],
                'period_low': period_stats['period_low'],
                'period_change_pct': period_stats['period_change_pct'],
                'period_hours': period_stats['period_hours'],
            }

            # Get current position
            current_position = self._get_current_position_data()

            # v24.1→v24.2: Active position reconciliation — detect ghost positions.
            # Double-confirmation: require 2 consecutive on_timer cycles (≥2 min)
            # before clearing state. Single API glitch must not destroy _layer_orders.
            _instrument_key = str(self.instrument_id)
            _has_stale_state = bool(self._layer_orders) or (_instrument_key in self.sltp_state)
            if not current_position and _has_stale_state:
                _ghost_ts = getattr(self, '_ghost_first_seen', 0.0)
                if _ghost_ts == 0.0:
                    # First detection — mark and wait for confirmation
                    self._ghost_first_seen = time.time()
                    self.log.warning(
                        f"⚠️ Potential ghost: Binance has no position but "
                        f"_layer_orders has {len(self._layer_orders)} layer(s). "
                        f"Will confirm on next cycle before clearing."
                    )
                elif time.time() - _ghost_ts > 120:
                    # Confirmed after 2+ minutes — safe to clear
                    _ghost_elapsed = time.time() - _ghost_ts
                    self.log.warning(
                        f"⚠️ Ghost confirmed ({_ghost_elapsed:.0f}s). "
                        f"Clearing {len(self._layer_orders)} stale layer(s)."
                    )

                    # v42.0: Record trade outcome BEFORE clearing state.
                    # Previously ghost detection silently discarded trade data,
                    # losing AI learning memory + Telegram close notification.
                    self._handle_ghost_close_recording(current_price)

                    self._clear_position_state()
                    self._ghost_first_seen = 0.0
                else:
                    self.log.info(
                        f"⏳ Ghost pending confirmation: "
                        f"{(time.time() - _ghost_ts):.0f}s / 120s"
                    )
            elif current_position:
                # Position exists — clear ghost flag if set
                if getattr(self, '_ghost_first_seen', 0.0) > 0:
                    self.log.info("✅ Position confirmed, ghost flag cleared")
                    self._ghost_first_seen = 0.0

            # Get account context for position sizing decisions (v4.6)
            account_context = self._get_account_context(current_price)

            # Log current state
            self.log.info(f"Current Price: ${current_price:,.2f}")
            self.log.info(f"Overall Trend: {technical_data.get('overall_trend', 'N/A')}")
            self.log.info(f"RSI: {technical_data.get('rsi', 0):.2f}")
            if current_position:
                self.log.info(
                    f"Current Position: {current_position['side']} "
                    f"{current_position['quantity']} @ ${current_position['avg_px']:.2f}"
                )
                # v4.7: Log critical risk fields
                liq_buffer = current_position.get('liquidation_buffer_pct')
                if liq_buffer is not None:
                    risk_level = "HIGH" if liq_buffer < 10 else "MEDIUM" if liq_buffer < 15 else "OK"
                    self.log.info(f"Liquidation Buffer: {liq_buffer:.1f}% ({risk_level})")
                funding_rate = current_position.get('funding_rate_current')
                if funding_rate is not None:
                    daily_cost = current_position.get('daily_funding_cost_usd', 0)
                    self.log.info(f"Settled FR: {funding_rate*100:.5f}%/8h (Daily Est: ${daily_cost:.2f})")

            # ========== Position management (runs BEFORE AI when holding) ==========
            # Time Barrier runs first as safety net.
            # AI analysis continues afterwards for pyramiding / reversal signals.
            if current_position:
                # v24.2: Runtime layer reconstruction guard.
                # If _layer_orders was cleared (ghost misfire, exception, etc.) while
                # a real position still exists, rebuild from exchange orders.
                # Without this, trailing/time-barrier/pyramiding all silently break.
                if not self._layer_orders:
                    self.log.warning(
                        "⚠️ Position exists but _layer_orders is empty — "
                        "attempting runtime reconstruction from exchange orders"
                    )
                    self._reconstruct_layers_runtime(current_position)

                # v15.6: Reconcile layer quantities against Binance position.
                # Detects drift from partial liquidation, external Binance APP reduction,
                # or any out-of-band position change that _layer_orders doesn't know about.
                self._reconcile_layer_quantities(current_position)

                # Update ATR cache (needed for Time Barrier calculation)
                try:
                    atr_bars = self.indicator_manager.get_kline_data(count=200)
                    if atr_bars and len(atr_bars) >= 14:
                        from utils.sr_zone_calculator import SRZoneCalculator
                        atr_val = SRZoneCalculator._calculate_atr_from_bars(atr_bars)
                        if atr_val and atr_val > 0:
                            self._cached_atr_value = atr_val
                except Exception as e:
                    self.log.debug(f"ATR cache is best-effort: {e}")
                    pass  # ATR cache is best-effort

                # Store technical data for _is_counter_trend() in time barrier
                self.latest_technical_data = technical_data

                self.log.info(
                    "📍 Position held → running 2-level management, "
                    "then continuing to AI analysis"
                )

                # v36.4: In-session TP coverage check — resubmit any missing TP orders
                # (catches TP submission failures from on_position_opened retry loop)
                self._check_tp_coverage()

                # Level 1: Time Barrier (highest priority — expire stale trades)
                time_barrier_triggered = self._check_time_barrier()
                if time_barrier_triggered:
                    # Position is being market-closed — skip AI this cycle
                    self.log.info("⏰ Time barrier triggered close → skip AI this cycle")
                    if self.enable_oco:
                        self._cleanup_oco_orphans()
                    return

                # Orphan order cleanup
                if self.enable_oco:
                    self._cleanup_oco_orphans()

                # Continue to AI analysis below (no early return)

            # ========== Hierarchical Decision Architecture (TradingAgents v3.1) ==========
            # Design: AI handles all trading decisions
            # Flow: Bull/Bear debate → Judge decision → Risk assessment → Final signal

            # v15.0: Unified skip-gate (flag pattern) — initialized before try
            # so it's always defined even if exceptions occur during data prep.
            _skip_ai_analysis = False

            try:
                self.log.info("🎭 Starting Multi-Agent Hierarchical Analysis...")
                self.log.info("   Phase 1: Bull/Bear Debate (using 4H decision layer data)")
                self.log.info("   Phase 2: Judge (Portfolio Manager) Decision")
                self.log.info("   Phase 3: Risk Evaluation")

                # Prepare AI analysis data: prefer 4H decision layer data
                # Per MTF design doc Section 1.5.4, Bull/Bear debate should use 4H data
                ai_technical_data = technical_data.copy()  # 30M execution layer data
                # Fix A4: Add timeframe tag to prevent AI confusing different timeframe data
                ai_technical_data['timeframe'] = '30M'
                # Important: Add price to technical_data (needed by extract_features)
                ai_technical_data['price'] = current_price
                # v3.6: Add price statistics (period high/low/change)
                ai_technical_data['price_change'] = price_data.get('price_change', 0)
                ai_technical_data['period_high'] = price_data.get('period_high', 0)
                ai_technical_data['period_low'] = price_data.get('period_low', 0)
                ai_technical_data['period_change_pct'] = price_data.get('period_change_pct', 0)
                ai_technical_data['period_hours'] = price_data.get('period_hours', 0)
                if decision_layer_data and decision_layer_data.get('_initialized', True):
                    # Add 4H data as decision layer information
                    # TradingAgents v3.3: Pass raw data only, no overall_trend pre-judgment
                    ai_technical_data['mtf_decision_layer'] = {
                        'timeframe': '4H',
                        'rsi': decision_layer_data.get('rsi', 50),
                        'macd': decision_layer_data.get('macd', 0),
                        'macd_signal': decision_layer_data.get('macd_signal', 0),
                        'sma_20': decision_layer_data.get('sma_20', 0),
                        'sma_50': decision_layer_data.get('sma_50', 0),
                        'bb_upper': decision_layer_data.get('bb_upper', 0),
                        'bb_middle': decision_layer_data.get('bb_middle', 0),
                        'bb_lower': decision_layer_data.get('bb_lower', 0),
                        'bb_position': decision_layer_data.get('bb_position', 0.5),
                        'adx': decision_layer_data.get('adx', 0),
                        'di_plus': decision_layer_data.get('di_plus', 0),
                        'di_minus': decision_layer_data.get('di_minus', 0),
                        'adx_regime': decision_layer_data.get('adx_regime', 'UNKNOWN'),
                        # v18 audit: Pass-through fields previously dropped at this boundary
                        'atr': decision_layer_data.get('atr', 0),
                        'volume_ratio': decision_layer_data.get('volume_ratio', 1.0),
                        'macd_histogram': decision_layer_data.get('macd_histogram', 0),
                        # v29.2: Full pass-through for complete feature coverage
                        'atr_pct': decision_layer_data.get('atr_pct', 0),
                        'extension_ratio_sma_20': decision_layer_data.get('extension_ratio_sma_20', 0),
                        'extension_ratio_sma_50': decision_layer_data.get('extension_ratio_sma_50', 0),
                        'extension_regime': decision_layer_data.get('extension_regime', 'NORMAL'),
                        'volatility_regime': decision_layer_data.get('volatility_regime', 'NORMAL'),
                        'volatility_percentile': decision_layer_data.get('volatility_percentile', 50),
                        'ema_12': decision_layer_data.get('ema_12', 0),
                        'ema_26': decision_layer_data.get('ema_26', 0),
                    }
                    # v39.0: Cache 4H ATR for SL/TP calculation (primary source)
                    _atr_4h = decision_layer_data.get('atr', 0)
                    if _atr_4h and _atr_4h > 0:
                        self._cached_atr_4h = _atr_4h

                    # v18 Item 7: Add 4H historical context (16-bar time series)
                    decision_mgr = getattr(self.mtf_manager, 'decision_manager', None)
                    if decision_mgr and hasattr(decision_mgr, 'get_historical_context'):
                        try:
                            hist_4h = decision_mgr.get_historical_context(count=16)
                            if hist_4h and hist_4h.get('trend_direction') not in ['INSUFFICIENT_DATA', 'ERROR', None]:
                                ai_technical_data['mtf_decision_layer']['historical_context'] = hist_4h
                                self.log.info(f"[MTF] 4H historical context: {len(hist_4h.get('rsi_trend', []))} bars")
                        except Exception as e:
                            self.log.debug(f"[MTF] 4H historical context not available: {e}")
                    self.log.info(f"[MTF] AI 分析使用 4H 决策层数据: RSI={ai_technical_data['mtf_decision_layer']['rsi']:.1f}")

                # ========== Fetch 1D Trend Layer Data (MTF v3.5) ==========
                trend_layer_data = None
                if self.mtf_enabled and self.mtf_manager:
                    try:
                        trend_layer_data = self.mtf_manager.get_technical_data_for_layer("trend", current_price)
                        if trend_layer_data and trend_layer_data.get('_initialized', True):
                            ai_technical_data['mtf_trend_layer'] = {
                                'timeframe': '1D',
                                'sma_200': trend_layer_data.get('sma_200', 0),
                                'macd': trend_layer_data.get('macd', 0),
                                'macd_signal': trend_layer_data.get('macd_signal', 0),
                                'rsi': trend_layer_data.get('rsi', 0),
                                'adx': trend_layer_data.get('adx', 0),
                                'di_plus': trend_layer_data.get('di_plus', 0),
                                'di_minus': trend_layer_data.get('di_minus', 0),
                                'adx_regime': trend_layer_data.get('adx_regime', 'UNKNOWN'),
                                # v18 Item 21: 1D BB/ATR pass-through
                                'bb_position': trend_layer_data.get('bb_position', 0.5),
                                'atr': trend_layer_data.get('atr', 0),
                                # v29.2: Full pass-through for complete feature coverage
                                'macd_histogram': trend_layer_data.get('macd_histogram', 0),
                                'volume_ratio': trend_layer_data.get('volume_ratio', 1.0),
                                'bb_upper': trend_layer_data.get('bb_upper', 0),
                                'bb_lower': trend_layer_data.get('bb_lower', 0),
                                'bb_middle': trend_layer_data.get('bb_middle', 0),
                                'atr_pct': trend_layer_data.get('atr_pct', 0),
                                'extension_ratio_sma_200': trend_layer_data.get('extension_ratio_sma_200', 0),
                                'extension_regime': trend_layer_data.get('extension_regime', 'NORMAL'),
                                'volatility_regime': trend_layer_data.get('volatility_regime', 'NORMAL'),
                                'volatility_percentile': trend_layer_data.get('volatility_percentile', 50),
                                'ema_12': trend_layer_data.get('ema_12', 0),
                                'ema_26': trend_layer_data.get('ema_26', 0),
                            }
                            self.log.info(f"[MTF] AI 分析使用 1D 趋势层数据: SMA_200=${ai_technical_data['mtf_trend_layer']['sma_200']:,.2f}, RSI={ai_technical_data['mtf_trend_layer']['rsi']:.1f}, ADX={ai_technical_data['mtf_trend_layer']['adx']:.1f}")
                            # v21.0: Add 1D historical context (10-bar time series)
                            # Pattern: same as 4H historical context (line 2394-2403)
                            trend_mgr = getattr(self.mtf_manager, 'trend_manager', None)
                            if trend_mgr and hasattr(trend_mgr, 'get_historical_context'):
                                try:
                                    hist_1d = trend_mgr.get_historical_context(count=10)
                                    if hist_1d and hist_1d.get('trend_direction') not in ['INSUFFICIENT_DATA', 'ERROR', None]:
                                        ai_technical_data['mtf_trend_layer']['historical_context'] = hist_1d
                                        self.log.info(f"[MTF] 1D historical context: {len(hist_1d.get('adx_trend', []))} bars")
                                except Exception as e:
                                    self.log.debug(f"[MTF] 1D historical context not available: {e}")
                    except Exception as e:
                        self.log.warning(f"[MTF] 获取趋势层数据失败: {e}")

                # ========== Fetch Historical Context (EVALUATION_FRAMEWORK v3.0.1) ==========
                # v18 Item 10: Reduced from 35→20 bars (30M × 20 = 10h coverage)
                # Rebalances 30M data volume vs 4H decision layer
                try:
                    historical_context = self.indicator_manager.get_historical_context(count=20)
                    if historical_context and historical_context.get('trend_direction') not in ['INSUFFICIENT_DATA', 'ERROR']:
                        ai_technical_data['historical_context'] = historical_context
                        self.log.info(
                            f"[历史上下文] trend={historical_context.get('trend_direction')}, "
                            f"momentum={historical_context.get('momentum_shift')}"
                        )
                    else:
                        self.log.debug("[历史上下文] 数据不足，跳过")
                except Exception as e:
                    self.log.warning(f"[历史上下文] 获取失败: {e}")

                # ========== Fetch Kline OHLCV Data (v3.21: show AI actual price action) ==========
                try:
                    kline_ohlcv = self.indicator_manager.get_kline_data(count=20)
                    if kline_ohlcv:
                        ai_technical_data['kline_ohlcv'] = kline_ohlcv
                        self.log.debug(f"[K线数据] {len(kline_ohlcv)} bars OHLCV 已加入 AI 数据")
                except Exception as e:
                    self.log.warning(f"[K线数据] 获取失败: {e}")

                # v7.0: External API data (order flow, derivatives, FR, orderbook,
                # binance derivatives) already fetched via data_assembler.fetch_external_data()
                # above. Variables order_flow_data, derivatives_data, orderbook_data,
                # binance_derivatives_data are set at the outer scope.

                # v21.0: Inject FR consecutive block context into AI data
                if self._fr_consecutive_blocks >= 2:
                    ai_technical_data['fr_block_context'] = {
                        'consecutive_blocks': self._fr_consecutive_blocks,
                        'blocked_direction': self._fr_block_direction,
                        'exhaustion_active': self._fr_consecutive_blocks >= 3,
                    }

                # v3.0: Get extended bars for S/R Swing Point detection
                # v4.0: Increased from 120 (30h) to 200 (50h) for robust swing detection + VP
                sr_bars_data = self.indicator_manager.get_kline_data(count=200)

                # v4.0 (E1): Update cached ATR value from 30M bars
                if sr_bars_data and len(sr_bars_data) >= 14:
                    try:
                        from utils.sr_zone_calculator import SRZoneCalculator
                        atr_val = SRZoneCalculator._calculate_atr_from_bars(sr_bars_data)
                        if atr_val and atr_val > 0:
                            self._cached_atr_value = atr_val
                    except Exception as e:
                        self.log.debug(f"ATR cache is best-effort: {e}")
                        pass  # ATR cache is best-effort

                # v3.12: Update risk controller with current equity and ATR
                try:
                    self.risk_controller.update_equity(
                        current_equity=self.equity,
                        current_atr=self._cached_atr_value if self._cached_atr_value > 0 else None,
                    )
                    risk_state = self.risk_controller.metrics.trading_state
                    if risk_state != TradingState.ACTIVE:
                        self.log.warning(
                            f"⚠️ Risk Controller: {risk_state.value} - "
                            f"{self.risk_controller.metrics.halt_reason}"
                        )
                except Exception as e:
                    self.log.warning(f"Risk controller update failed: {e}")

                # v2.0: HMM Regime Detection + Regime-Adaptive Risk
                if self._regime_detector and decision_layer_data:
                    try:
                        import math
                        # Extract HMM observation features from 4H data
                        _d = decision_layer_data
                        _t = trend_layer_data or {}
                        _recent = getattr(
                            getattr(self.mtf_manager, 'decision_manager', None),
                            'recent_bars', []
                        ) if self.mtf_manager else []
                        _log_ret = 0.0
                        if len(_recent) >= 2:
                            _prev_c = float(_recent[-2].close) if hasattr(_recent[-2], 'close') else 0
                            _curr_c = float(_recent[-1].close) if hasattr(_recent[-1], 'close') else 0
                            if _prev_c > 0 and _curr_c > 0:
                                _log_ret = math.log(_curr_c / _prev_c)
                        regime_features = {
                            'log_return_4h': _log_ret,
                            'atr_pct_4h': _d.get('atr_pct', 0),
                            'adx_4h': _d.get('adx', 0),
                            'volume_ratio_4h': _d.get('volume_ratio', 1.0),
                            'rsi_4h': _d.get('rsi', 50),
                        }
                        regime_result = self._regime_detector.predict(regime_features)
                        self._current_regime = regime_result.get('regime', 'RANGING')
                        self._last_regime_result = regime_result
                        self.log.info(
                            f"[HMM] Regime: {self._current_regime} "
                            f"(source={regime_result.get('source', 'unknown')}, "
                            f"conf={regime_result.get('confidence', 0):.2f})"
                        )
                        # Feed regime into risk controller for adaptive thresholds
                        self.risk_controller.set_regime(self._current_regime)
                        # v2.0: Check if model needs retraining (warn only, no auto-retrain)
                        if hasattr(self._regime_detector, 'needs_retrain') and self._regime_detector.needs_retrain():
                            self.log.warning("[HMM] Model is stale — consider running: python3 scripts/measure_baseline.py")
                    except Exception as e:
                        self.log.debug(f"[HMM] Regime detection skipped: {e}")

                # v4.0 (E1): Extract MTF bars from trend/decision managers (if available)
                bars_data_4h = None
                bars_data_1d = None
                daily_bar = None
                weekly_bar = None
                if self.mtf_enabled and self.mtf_manager:
                    try:
                        # 4H bars from decision layer
                        decision_mgr = getattr(self.mtf_manager, 'decision_manager', None)
                        if decision_mgr and hasattr(decision_mgr, 'recent_bars') and decision_mgr.recent_bars:
                            bars_data_4h = [
                                {'high': float(b.high), 'low': float(b.low),
                                 'close': float(b.close), 'open': float(b.open),
                                 'volume': float(b.volume)}
                                for b in decision_mgr.recent_bars[-50:]
                            ]
                        # 1D bars from trend layer
                        trend_mgr = getattr(self.mtf_manager, 'trend_manager', None)
                        if trend_mgr and hasattr(trend_mgr, 'recent_bars') and trend_mgr.recent_bars:
                            bars_1d_raw = [
                                {'high': float(b.high), 'low': float(b.low),
                                 'close': float(b.close), 'open': float(b.open),
                                 'volume': float(b.volume)}
                                for b in trend_mgr.recent_bars[-120:]
                            ]
                            bars_data_1d = bars_1d_raw
                            # Extract daily bar (last completed) and weekly bar (last 5 days)
                            if bars_1d_raw:
                                daily_bar = bars_1d_raw[-1]
                                from utils.sr_pivot_calculator import aggregate_weekly_bar
                                weekly_bar = aggregate_weekly_bar(bars_1d_raw)
                    except Exception as e:
                        self.log.debug(f"[MTF] Failed to extract MTF bars for S/R: {e}")

                # ===== v15.0: Skip-gate checks =====
                # All skip paths set _skip_ai_analysis = True instead of return,
                # so OCO cleanup + Time Barrier always run at the end.

                # v6.0: Per-stop cooldown check — skip AI analysis to save API calls
                # v15.0 (NEW-DESIGN-1): Changed from return to flag for path consistency
                # Surge-triggered analysis bypasses cooldown (significant price movement
                # during cooldown warrants re-evaluation)
                if self._check_stoploss_cooldown() and not current_position and not _is_surge_analysis:
                    # Only skip if no position (if we have a position, we still need
                    # AI analysis for confidence tracking / partial close decisions)
                    self._last_signal_status = {
                        'executed': False,
                        'reason': f'止损冷静期 ({self._stoploss_cooldown_type})',
                        'action_taken': '',
                        'hold_source': 'cooldown',
                    }
                    _skip_ai_analysis = True

                # v15.0: Market-change gate + Watchdog timer
                # Surge-triggered analysis always bypasses (price already moved significantly)
                if not _skip_ai_analysis:
                    if _is_surge_analysis:
                        should_analyze = True
                        gate_reason = ""
                        self.log.info("⚡ Surge bypass: skipping market-change gate")
                    else:
                        should_analyze, gate_reason = self._has_market_changed()

                    if not should_analyze:
                        self._consecutive_skips += 1

                        if self._consecutive_skips >= self._max_skips_before_force:
                            # Watchdog: too many consecutive skips, force analysis
                            self.log.warning(
                                f"⏰ Watchdog: forcing analysis after {self._consecutive_skips} "
                                f"consecutive skips (last skip reason: {gate_reason})"
                            )
                            self._consecutive_skips = 0
                        else:
                            self.log.info(
                                f"⏭️ Market unchanged ({gate_reason}), skip "
                                f"#{self._consecutive_skips}/{self._max_skips_before_force}"
                            )
                            self._last_signal_status = {
                                'executed': False,
                                'reason': f'市场未变化 ({gate_reason}), skip #{self._consecutive_skips}/{self._max_skips_before_force}',
                                'action_taken': '',
                                'hold_source': 'gate_skip',
                            }
                            _skip_ai_analysis = True

                if not _skip_ai_analysis:
                    # v42.0: ET Exhaustion Tier 2 — skip ET API call entirely
                    _skip_et = self._et_consecutive_rejects >= self._ET_EXHAUSTION_TIER2
                    if _skip_et:
                        self.log.warning(
                            f"⚡ v42.0: ET Exhaustion Tier 2 — {self._et_consecutive_rejects}× "
                            f"consecutive REJECTs ≥ {self._ET_EXHAUSTION_TIER2}, skipping ET"
                        )
                    # v46.0: Mechanical mode — no AI API calls
                    signal_data = self.multi_agent.mechanical_analyze(
                        technical_report=ai_technical_data,
                        sentiment_report=sentiment_data,
                        current_position=current_position,
                        price_data=price_data,
                        order_flow_report=order_flow_data,
                        derivatives_report=derivatives_data,
                        binance_derivatives_report=binance_derivatives_data,
                        orderbook_report=orderbook_data,
                        account_context=account_context,
                        order_flow_report_4h=order_flow_data_4h,
                        fear_greed_report=fear_greed_data,
                        sr_zones=self.latest_sr_zones_data,
                        atr_value=self._cached_atr_value,
                    )
                    self.log.info(
                        f"Zone decision: {signal_data.get('signal')} "
                        f"{signal_data.get('confidence')} | "
                        f"hold={signal_data.get('hold_source', '')} | "
                        f"{signal_data.get('reason', '')}"
                    )

                    # v47.0: Store anticipatory scores for heartbeat display
                    self._latest_anticipatory_scores = signal_data.get('_anticipatory_scores')

                    # v15.0: Update gate state after successful analysis
                    self._last_analysis_price = self._cached_current_price
                    self._last_analysis_atr = self._cached_atr_value
                    self._last_analysis_had_position = self._get_current_position_data() is not None
                    self._consecutive_skips = 0

                    # v2.0: Record Prometheus signal metric
                    if self._metrics_exporter:
                        try:
                            self._metrics_exporter.record_signal(signal_data.get('signal', 'HOLD'))
                        except Exception as e:
                            self.log.debug(f"Prometheus metrics recording failed: {e}")

                    # v3.8: Store S/R Zone data for heartbeat (from MultiAgentAnalyzer cache)
                    # v6.5: S/R zone freshness check (30min TTL)
                    if hasattr(self.multi_agent, '_sr_zones_cache') and self.multi_agent._sr_zones_cache:
                        sr_cache = self.multi_agent._sr_zones_cache
                        sr_age_sec = time.time() - sr_cache.get('_calculated_at', time.time())
                        if sr_age_sec > self.sr_zones_cache_ttl_seconds:
                            self.log.warning(
                                f"⚠️ S/R zones stale: {sr_age_sec:.0f}s old (>{self.sr_zones_cache_ttl_seconds}s). "
                                f"Data is informational only (v11.0-simple)."
                            )
                        self.latest_sr_zones_data = sr_cache

                    # ========== TradingAgents v3.1: Full AI Autonomous Decision ==========
                    # Design: "Autonomy is non-negotiable" - AI thinks like a human analyst
                    # Removed all local hardcoded rules:
                    #   - Trend direction permission check (allow_long/allow_short) - AI decides
                    #   - S/R boundary check - AI understands from data itself
                    # AI sees S/R data and decides whether to reference it

                    # Log Judge's final decision
                    self.log.info(
                        f"🎯 Judge Decision: {signal_data['signal']} | "
                        f"Confidence: {signal_data['confidence']} | "
                        f"Risk: {signal_data.get('risk_level', 'N/A')}"
                    )
                    self.log.info(f"📋 Reason: {signal_data.get('reason', 'N/A')}")

                    # Log judge's detailed decision if available
                    judge_decision = signal_data.get('judge_decision', {})
                    if judge_decision:
                        winning_side = judge_decision.get('winning_side', 'N/A')
                        # v3.10: Support both rationale (new) and key_reasons (legacy)
                        rationale = judge_decision.get('rationale', '')
                        strategic_actions = judge_decision.get('strategic_actions', [])
                        self.log.info(f"⚖️ Winning Side: {winning_side}")
                        # v5.7: Log confluence analysis
                        confluence = judge_decision.get('confluence', {})
                        if confluence:
                            aligned = confluence.get('aligned_layers', '?')
                            self.log.info(f"📊 Confluence ({aligned} layers aligned):")
                            _conf_keys = ('structure', 'divergence', 'order_flow')
                            for layer_key in _conf_keys:
                                layer_val = confluence.get(layer_key, 'N/A')
                                self.log.info(f"  {layer_key}: {layer_val}")
                        if rationale:
                            self.log.info(f"📌 Rationale: {rationale}")
                        if strategic_actions:
                            self.log.info(f"🎯 Actions: {', '.join(strategic_actions[:2])}")

                    # Telegram notification moved to after execution (see _execute_trade)
                    # This prevents "signal sent but not executed" confusion

            except Exception as e:
                self.log.error(f"Multi-Agent analysis failed: {e}", exc_info=True)

                # Send error notification
                if self.telegram_bot and self.enable_telegram and self.telegram_notify_errors:
                    try:
                        error_msg = self.telegram_bot.format_error_alert({
                            'level': 'ERROR',
                            'message': f"Multi-Agent Analysis Failed: {str(e)}",
                            'context': 'on_timer'
                        })
                        self.telegram_bot.send_message_sync(error_msg)
                    except Exception as e:
                        self.log.warning(f"Failed to send Telegram error notification: {e}")
                # v15.0: Don't return — fall through to OCO cleanup + Time Barrier
                _skip_ai_analysis = True

            # v15.0: Only process signal if AI analysis ran successfully
            if not _skip_ai_analysis:
                # Store signal
                self.last_signal = signal_data
                self._signals_generated_today = getattr(self, '_signals_generated_today', 0) + 1
                self._save_equity_snapshots()

                # 📸 Fix C16/J43: Save complete decision snapshot for replay
                try:
                    self._save_decision_snapshot(
                        signal_data=signal_data,
                        technical_data=technical_data,
                        sentiment_data=sentiment_data,
                        order_flow_data=order_flow_data if 'order_flow_data' in locals() else None,
                        derivatives_data=derivatives_data if 'derivatives_data' in locals() else None,
                        current_position=current_position,
                        price_data=price_data,
                    )
                except Exception as e:
                    self.log.debug(f"Failed to save decision snapshot: {e}")

                # v15.0 P0 Layer 3: Signal fingerprint deduplication
                # CLOSE/REDUCE signals are never deduplicated (safety-critical)
                _raw_signal = signal_data.get('signal', 'HOLD')
                new_fingerprint = f"{_raw_signal}|{signal_data.get('confidence', '')}|{signal_data.get('risk_appetite', '')}"

                if (
                    _raw_signal not in ('CLOSE', 'REDUCE')
                    and new_fingerprint == self._last_executed_fingerprint
                ):
                    self.log.info(
                        f"📋 Duplicate signal detected ({new_fingerprint}), skipping execution"
                    )
                    self._last_signal_status = {
                        'executed': False,
                        'reason': f'重复信号 ({new_fingerprint})',
                        'action_taken': '',
                        'hold_source': 'dedup',
                    }
                    # v34.1: Record counterfactual for dedup HOLDs
                    if _raw_signal in ('LONG', 'SHORT'):
                        with self._state_lock:
                            _cf_px = self._cached_current_price
                        self._hold_counterfactual_record = {
                            'proposed_signal': _raw_signal,
                            'price': _cf_px,
                            'hold_source': 'dedup',
                            'timestamp': datetime.now(timezone.utc).isoformat(),
                        }
                else:
                    # v3.12: Risk Controller gate - check circuit breakers before execution
                    signal = signal_data.get('signal', 'HOLD')
                    if signal in ('LONG', 'SHORT'):
                        can_trade, block_reason = self.risk_controller.can_open_trade()
                        if not can_trade:
                            self.log.warning(f"🚫 Risk Controller blocked trade: {block_reason}")
                            self._last_signal_status = {
                                'executed': False,
                                'reason': f'风控熔断: {block_reason}',
                                'action_taken': '',
                                'hold_source': 'risk_breaker',
                            }
                            # v34.1: Record counterfactual for risk_breaker HOLDs
                            with self._state_lock:
                                _cf_px = self._cached_current_price
                            self._hold_counterfactual_record = {
                                'proposed_signal': signal,
                                'price': _cf_px,
                                'hold_source': 'risk_breaker',
                                'timestamp': datetime.now(timezone.utc).isoformat(),
                            }
                            # Send Telegram alert for circuit breaker
                            if self.telegram_bot and self.enable_telegram and self.telegram_notify_errors:
                                try:
                                    alert_msg = self.telegram_bot.format_error_alert({
                                        'level': 'WARNING',
                                        'message': f"风控熔断阻止交易: {block_reason}",
                                        'context': f"信号: {self.telegram_bot.side_to_cn(signal, 'side') if signal in ('LONG', 'SHORT') else signal} {signal_data.get('confidence', 'N/A')}",
                                    })
                                    self.telegram_bot.send_message_sync(alert_msg)
                                except Exception as e:
                                    self.log.debug(f"Telegram notification failure is non-critical: {e}")
                                    pass  # Telegram notification failure is non-critical
                            # Skip trade execution but continue with OCO cleanup and SL/TP updates
                            signal_data = dict(signal_data)  # Make a copy
                            signal_data['signal'] = 'HOLD'
                            signal_data['reason'] = f"[风控熔断] {block_reason} | 原始: {signal}"

                        # Apply position size multiplier from risk state (REDUCED = 0.5x)
                        risk_mult = self.risk_controller.get_position_size_multiplier()
                        if risk_mult < 1.0 and risk_mult > 0:
                            signal_data['_risk_position_multiplier'] = risk_mult
                            self.log.info(f"⚠️ Risk Controller: position size ×{risk_mult:.1f}")

                    # v23.0: Evaluate previous REJECT accuracy (if any)
                    if self._last_reject_record:
                        try:
                            with self._state_lock:
                                _current_px = self._cached_current_price
                            _rej = self._last_reject_record
                            _rej_px = _rej.get('price', 0)
                            _rej_signal = _rej.get('signal', '')
                            if _rej_px and _current_px:
                                _px_change_pct = (_current_px - _rej_px) / _rej_px * 100
                                # Good REJECT: price moved against the proposed direction
                                # LONG rejected + price went down = correct
                                # SHORT rejected + price went up = correct
                                _was_correct = (
                                    (_rej_signal == 'LONG' and _px_change_pct < -0.1) or
                                    (_rej_signal == 'SHORT' and _px_change_pct > 0.1)
                                )
                                _verdict_str = "correct" if _was_correct else "wrong" if abs(_px_change_pct) > 0.1 else "neutral"
                                self.log.info(
                                    f"📊 REJECT accuracy: {_rej_signal} rejected at ${_rej_px:,.2f}, "
                                    f"now ${_current_px:,.2f} ({_px_change_pct:+.2f}%) → {_verdict_str}"
                                )
                        except Exception as e:
                            self.log.debug(f"REJECT accuracy evaluation failed: {e}")
                        self._last_reject_record = None

                    # v23.0: Entry Timing Agent gate
                    # Replaces v18 Alignment Gate + v18 Entry Quality Downgrade + v22.1 30M Cap.
                    # Entry timing evaluation is now done by dedicated AI agent in Phase 2.5
                    # of multi_agent_analyzer.analyze(). The signal_data already has:
                    #   - Confidence adjusted by Entry Timing Agent
                    #   - Signal changed to HOLD if timing_verdict == REJECT
                    # Here we just log the timing assessment for observability.
                    signal = signal_data.get('signal', 'HOLD')
                    _timing = signal_data.get('_timing_assessment', {})
                    if _timing:
                        _timing_verdict = _timing.get('timing_verdict', 'N/A')
                        _timing_quality = _timing.get('timing_quality', 'N/A')
                        _ctr_risk = _timing.get('counter_trend_risk', 'NONE')
                        _timing_reason = _timing.get('reason', '')

                        if signal_data.get('_timing_rejected'):
                            _orig_signal = signal_data.get('_timing_original_signal', '?')

                            # v42.1: ET Exhaustion Tier 1 — override was applied inside
                            # analyze() so Risk Manager could evaluate the restored signal.
                            # Here we only handle counter management + notifications.
                            if signal_data.get('_et_exhaustion_tier1_applied'):
                                self.log.warning(
                                    f"⚡ v42.1: ET Exhaustion Tier 1 — override applied in analyze() "
                                    f"({self._et_consecutive_rejects}× ≥ {self._ET_EXHAUSTION_TIER1}). "
                                    f"{_orig_signal} passed through Risk Manager at LOW confidence."
                                )
                                # Signal already restored inside analyze(); sync local variable
                                signal = signal_data.get('signal', _orig_signal)
                                signal_data['_et_exhaustion_rejects'] = self._et_consecutive_rejects
                                # Recompute fingerprint to reflect overridden signal
                                new_fingerprint = f"{signal}|LOW|{signal_data.get('risk_appetite', '')}"
                                self._last_executed_fingerprint = None
                                # Counter: reduce by 3 (not zero — remember recent pressure)
                                _prev_count = self._et_consecutive_rejects
                                self._et_consecutive_rejects = max(0, self._et_consecutive_rejects - 3)
                                self.log.info(
                                    f"📊 ET exhaustion counter: {_prev_count} → {self._et_consecutive_rejects} "
                                    f"(reduced by 3, will re-trigger at {self._ET_EXHAUSTION_TIER1})"
                                )
                                self._log_gate_audit(
                                    gate="entry_timing",
                                    result="EXHAUSTION_OVERRIDE",
                                    signal_was=_orig_signal,
                                    details={
                                        'tier': 1,
                                        'consecutive_rejects': _prev_count,
                                        'original_reject_reason': _timing_reason,
                                        'risk_evaluated': True,
                                    },
                                )
                                if self.telegram_bot and self.enable_telegram:
                                    try:
                                        _orig_cn = self.telegram_bot.side_to_cn(_orig_signal, 'side') if _orig_signal in ('LONG', 'SHORT') else _orig_signal
                                        self.telegram_bot.send_message_sync(
                                            f"⚡ ET Exhaustion 放行 (Tier 1)\n"
                                            f"信号: {_orig_cn} | 连续拦截: {_prev_count} 次\n"
                                            f"confidence: LOW (30% 小仓位探索)\n"
                                            f"Risk Manager: ✅ 已评估\n"
                                            f"ET 原因: {_timing_reason}",
                                            broadcast=False,
                                        )
                                    except Exception as e:
                                        self.log.debug(f"ET exhaustion Telegram notification failed: {e}")
                            elif signal_data.get('_et_exhaustion_tier1_blocked'):
                                # v42.2: Tier 1 threshold reached but override was blocked
                                # due to structural market risk (EXTREME counter-trend or
                                # HIGH counter-trend + POOR timing quality). ET is correctly
                                # protecting — do NOT force a trade. Still reduce counter by 3
                                # to prevent infinite accumulation.
                                _block_reason = signal_data.get('_et_exhaustion_block_reason', '')
                                _prev_count = self._et_consecutive_rejects
                                self._et_consecutive_rejects = max(0, self._et_consecutive_rejects - 3)
                                self.log.warning(
                                    f"🛡️ v42.2: ET Exhaustion Tier 1 BLOCKED — structural risk. "
                                    f"{_orig_signal} remains HOLD. "
                                    f"Reason: {_block_reason}. "
                                    f"Counter: {_prev_count} → {self._et_consecutive_rejects}"
                                )
                                self._log_gate_audit(
                                    gate="entry_timing",
                                    result="EXHAUSTION_BLOCKED",
                                    signal_was=_orig_signal,
                                    details={
                                        'tier': 1,
                                        'consecutive_rejects': _prev_count,
                                        'block_reason': _block_reason,
                                        'counter_trend_risk': _ctr_risk,
                                        'timing_quality': _timing_quality,
                                    },
                                )
                                self._last_signal_status = {
                                    'executed': False,
                                    'reason': f'ET exhaustion 被结构性风险阻止: {_block_reason}',
                                    'action_taken': '',
                                    'hold_source': 'et_reject',
                                }
                                # Record counterfactual (same as normal REJECT)
                                with self._state_lock:
                                    _cf_px = self._cached_current_price
                                self._hold_counterfactual_record = {
                                    'proposed_signal': _orig_signal,
                                    'price': _cf_px,
                                    'hold_source': 'et_reject',
                                    'timestamp': datetime.now(timezone.utc).isoformat(),
                                }
                                if self.telegram_bot and self.enable_telegram:
                                    try:
                                        _orig_cn = self.telegram_bot.side_to_cn(_orig_signal, 'side') if _orig_signal in ('LONG', 'SHORT') else _orig_signal
                                        self.telegram_bot.send_message_sync(
                                            f"🛡️ ET Exhaustion 拦截 (结构性风险)\n"
                                            f"信号: {_orig_cn} | 连续拦截: {_prev_count} 次\n"
                                            f"逆势风险: {_ctr_risk} | 时机质量: {_timing_quality}\n"
                                            f"Exhaustion 已达 Tier 1 门槛但不放行\n"
                                            f"原因: {_timing_reason}",
                                            broadcast=False,
                                        )
                                    except Exception as e:
                                        self.log.debug(f"ET exhaustion blocked Telegram notification failed: {e}")
                                # Still increment counter (this is a genuine REJECT)
                                self._et_consecutive_rejects += 1
                                self.log.info(
                                    f"📊 Entry Timing consecutive REJECTs: {self._et_consecutive_rejects} "
                                    f"(structural risk block, counter re-incremented after -3 reduction)"
                                )
                                # Record for accuracy tracking
                                with self._state_lock:
                                    _reject_price = self._cached_current_price
                                self._last_reject_record = {
                                    'signal': _orig_signal,
                                    'price': _reject_price,
                                    'timestamp': datetime.now(timezone.utc).isoformat(),
                                    'reason': f"[structural_block] {_timing_reason}",
                                }
                            elif self._et_consecutive_rejects >= self._ET_EXHAUSTION_TIER1:
                                # Fallback: Tier 1 threshold reached but override wasn't
                                # applied in analyze() (shouldn't happen, but defensive).
                                self.log.warning(
                                    f"⚡ v42.0: ET Exhaustion Tier 1 fallback — overriding REJECT "
                                    f"({self._et_consecutive_rejects}× ≥ {self._ET_EXHAUSTION_TIER1}). "
                                    f"{_orig_signal} forced ENTER at LOW confidence. "
                                    f"⚠️ Risk Manager NOT evaluated."
                                )
                                signal_data['signal'] = _orig_signal
                                signal_data['confidence'] = 'LOW'
                                signal_data['_timing_rejected'] = False
                                signal_data['_et_exhaustion_tier1'] = True
                                signal_data['_et_exhaustion_rejects'] = self._et_consecutive_rejects
                                signal = _orig_signal
                                new_fingerprint = f"{_orig_signal}|LOW|{signal_data.get('risk_appetite', '')}"
                                self._last_executed_fingerprint = None
                                _prev_count = self._et_consecutive_rejects
                                self._et_consecutive_rejects = max(0, self._et_consecutive_rejects - 3)
                                self.log.info(
                                    f"📊 ET exhaustion counter: {_prev_count} → {self._et_consecutive_rejects} "
                                    f"(reduced by 3, will re-trigger at {self._ET_EXHAUSTION_TIER1})"
                                )
                                self._log_gate_audit(
                                    gate="entry_timing",
                                    result="EXHAUSTION_OVERRIDE",
                                    signal_was=_orig_signal,
                                    details={
                                        'tier': 1,
                                        'consecutive_rejects': _prev_count,
                                        'original_reject_reason': _timing_reason,
                                        'risk_evaluated': False,
                                    },
                                )
                                if self.telegram_bot and self.enable_telegram:
                                    try:
                                        _orig_cn = self.telegram_bot.side_to_cn(_orig_signal, 'side') if _orig_signal in ('LONG', 'SHORT') else _orig_signal
                                        self.telegram_bot.send_message_sync(
                                            f"⚡ ET Exhaustion 放行 (Tier 1)\n"
                                            f"信号: {_orig_cn} | 连续拦截: {_prev_count} 次\n"
                                            f"confidence: LOW (30% 小仓位探索)\n"
                                            f"ET 原因: {_timing_reason}",
                                            broadcast=False,
                                        )
                                    except Exception as e:
                                        self.log.debug(f"ET exhaustion Telegram notification failed: {e}")
                            else:
                                # Normal ET REJECT handling (unchanged)
                                self.log.warning(
                                    f"🚫 Entry Timing REJECT: {_orig_signal} → HOLD "
                                    f"(quality={_timing_quality}, counter_trend={_ctr_risk})"
                                )
                                self._log_gate_audit(
                                    gate="entry_timing",
                                    result="REJECTED",
                                    signal_was=_orig_signal,
                                    details={
                                        'timing_verdict': _timing_verdict,
                                        'timing_quality': _timing_quality,
                                        'counter_trend_risk': _ctr_risk,
                                        'reason': _timing_reason,
                                    },
                                )
                                self._last_signal_status = {
                                    'executed': False,
                                    'reason': f'入场时机拦截: {_timing_reason}',
                                    'action_taken': '',
                                    'hold_source': 'et_reject',
                                }
                                # v34.1: Record counterfactual for ET reject HOLDs
                                with self._state_lock:
                                    _cf_px = self._cached_current_price
                                self._hold_counterfactual_record = {
                                    'proposed_signal': _orig_signal,
                                    'price': _cf_px,
                                    'hold_source': 'et_reject',
                                    'timestamp': datetime.now(timezone.utc).isoformat(),
                                }
                                if self.telegram_bot and self.enable_telegram:
                                    try:
                                        _orig_cn = self.telegram_bot.side_to_cn(_orig_signal, 'side') if _orig_signal in ('LONG', 'SHORT') else _orig_signal
                                        self.telegram_bot.send_message_sync(
                                            f"🚫 Entry Timing: {_orig_cn} 被拦截\n"
                                            f"质量: {_timing_quality} | 逆势: {_ctr_risk}\n"
                                            f"原因: {_timing_reason}",
                                            broadcast=False,
                                        )
                                    except Exception as e:
                                        self.log.debug(f"Entry timing Telegram notification failed: {e}")
                                # v23.0: Track consecutive REJECTs to detect dead loops
                                self._et_consecutive_rejects += 1
                                self.log.info(
                                    f"📊 Entry Timing consecutive REJECTs: {self._et_consecutive_rejects}"
                                )
                                # v23.0: Record REJECT for accuracy tracking
                                with self._state_lock:
                                    _reject_price = self._cached_current_price
                                self._last_reject_record = {
                                    'signal': _orig_signal,
                                    'price': _reject_price,
                                    'timestamp': datetime.now(timezone.utc).isoformat(),
                                    'reason': _timing_reason,
                                }
                                if self._et_consecutive_rejects >= 4:
                                    self.log.warning(
                                        f"⚠️ Entry Timing has REJECTED {self._et_consecutive_rejects}× "
                                        f"consecutively — possible dead loop. "
                                        f"Review market conditions or Entry Timing prompt."
                                    )
                                    if self.telegram_bot and self.enable_telegram:
                                        try:
                                            self.telegram_bot.send_message_sync(
                                                f"⚠️ Entry Timing 连续拦截 {self._et_consecutive_rejects} 次\n"
                                                f"可能存在死循环，请关注市场条件变化",
                                                broadcast=False,
                                            )
                                        except Exception as e:
                                            self.log.debug(f"ET consecutive REJECT Telegram notification failed: {e}")
                        elif signal in ('LONG', 'SHORT'):
                            self.log.info(
                                f"⏱️ Entry Timing: {_timing_verdict} "
                                f"quality={_timing_quality} counter_trend={_ctr_risk}"
                            )
                            _conf_adj = signal_data.get('_timing_confidence_adjusted', '')
                            if _conf_adj:
                                self.log.info(f"⏱️ Confidence adjusted by Entry Timing: {_conf_adj}")
                                # v23.0: Telegram notification for confidence downgrade
                                if self.telegram_bot and self.enable_telegram:
                                    try:
                                        _sig_cn = self.telegram_bot.side_to_cn(signal, 'side') if signal in ('LONG', 'SHORT') else signal
                                        self.telegram_bot.send_message_sync(
                                            f"⏱️ Entry Timing: confidence 降级\n"
                                            f"信号: {_sig_cn} | {_conf_adj}\n"
                                            f"质量: {_timing_quality} | 逆势: {_ctr_risk}",
                                            broadcast=False,
                                        )
                                    except Exception as e:
                                        self.log.debug(f"Confidence downgrade Telegram notification failed: {e}")
                            self._log_gate_audit(
                                gate="entry_timing",
                                result="PASS" if _timing_verdict == 'ENTER' else _timing_verdict,
                                signal_was=signal,
                                details={
                                    'timing_verdict': _timing_verdict,
                                    'timing_quality': _timing_quality,
                                    'counter_trend_risk': _ctr_risk,
                                    'confidence_adjusted': _conf_adj or 'unchanged',
                                },
                            )

                    # Execute trade
                    self._execute_trade(signal_data, price_data, technical_data, current_position)
                    # Only store fingerprint if trade was actually executed
                    # (prevents deadlock: rejected signal → fingerprint stored → never cleared)
                    if self._last_signal_status.get('executed', False):
                        self._last_executed_fingerprint = new_fingerprint

            # v15.0: Always run OCO cleanup + Time Barrier regardless of skip state
            # Orphan order cleanup: cancel reduce-only orders when no position exists
            if self.enable_oco:
                self._cleanup_oco_orphans()

            # Time Barrier: expire stale trades
            self._check_time_barrier()

        finally:
            # 🔒 Fix I38: Always release lock when on_timer exits
            self._timer_lock.release()

    def _persist_hold_counterfactual(self, record: dict):
        """v34.2: Persist HOLD counterfactual evaluation to JSON file for Layer 3 analysis.

        Stores hold_source + price movement verdict for analyze_quality_correlation.py.
        Max 200 records (FIFO). File: data/hold_counterfactuals.json.
        """
        _MAX_RECORDS = 200
        _filepath = os.path.join('data', 'hold_counterfactuals.json')
        try:
            os.makedirs('data', exist_ok=True)
            records = []
            if os.path.exists(_filepath):
                with open(_filepath, 'r') as f:
                    records = json.load(f)
            records.append(record)
            if len(records) > _MAX_RECORDS:
                records = records[-_MAX_RECORDS:]
            with open(_filepath, 'w') as f:
                json.dump(records, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.log.debug(f"Failed to persist HOLD counterfactual: {e}")

    def _save_decision_snapshot(
        self,
        signal_data: dict,
        technical_data: dict,
        sentiment_data: dict,
        order_flow_data: dict,
        derivatives_data: dict,
        current_position: dict,
        price_data: dict,
    ):
        """
        🔍 Fix C16/J43: Save complete decision snapshot for debugging and replay.

        Saves all inputs and AI outputs to a JSON file.
        This enables full replay of "why did the system make this decision?"

        Note: All trading decisions are made by AI (Bull/Bear/Judge).
        Local code only handles risk control (S/R proximity blocking).
        """
        try:

            # Create logs directory if it doesn't exist
            os.makedirs('logs/decisions', exist_ok=True)

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            snapshot_file = f'logs/decisions/decision_{timestamp}.json'

            # Serialize S/R zones from production cache for offline validation
            sr_zones_snapshot = None
            if self.latest_sr_zones_data:
                try:
                    sup_zones = self.latest_sr_zones_data.get('support_zones', [])
                    res_zones = self.latest_sr_zones_data.get('resistance_zones', [])

                    def _zone_to_snapshot(zone):
                        # Support both SRZone dataclass objects and dict fallback
                        if isinstance(zone, dict):
                            return {
                                'price_center': zone.get('price_center', 0),
                                'price_low': zone.get('price_low', 0),
                                'price_high': zone.get('price_high', 0),
                                'side': zone.get('side', ''),
                                'strength': zone.get('strength', 'LOW'),
                                'level': zone.get('level', 'MINOR'),
                                'source_type': zone.get('source_type', ''),
                                'sources': zone.get('sources', []),
                                'total_weight': zone.get('total_weight', 0),
                                'touch_count': zone.get('touch_count', 0),
                                'has_swing_point': zone.get('has_swing_point', False),
                                'has_order_wall': zone.get('has_order_wall', False),
                                'hold_probability': zone.get('hold_probability', 0),
                                'distance_pct': zone.get('distance_pct', 0),
                            }
                        return {
                            'price_center': zone.price_center,
                            'price_low': zone.price_low,
                            'price_high': zone.price_high,
                            'side': zone.side,
                            'strength': getattr(zone, 'strength', 'LOW'),
                            'level': getattr(zone, 'level', 'MINOR'),
                            'source_type': getattr(zone, 'source_type', ''),
                            'sources': getattr(zone, 'sources', []),
                            'total_weight': getattr(zone, 'total_weight', 0),
                            'touch_count': getattr(zone, 'touch_count', 0),
                            'has_swing_point': getattr(zone, 'has_swing_point', False),
                            'has_order_wall': getattr(zone, 'has_order_wall', False),
                            'hold_probability': getattr(zone, 'hold_probability', 0),
                            'distance_pct': getattr(zone, 'distance_pct', 0),
                        }

                    sr_zones_snapshot = {
                        'support_zones': [_zone_to_snapshot(z) for z in sup_zones],
                        'resistance_zones': [_zone_to_snapshot(z) for z in res_zones],
                        'block_long': self.latest_sr_zones_data.get('hard_control', {}).get('block_long', False),
                        'block_short': self.latest_sr_zones_data.get('hard_control', {}).get('block_short', False),
                        '_calculated_at': self.latest_sr_zones_data.get('_calculated_at'),
                    }
                except Exception as e:
                    self.log.warning(f"⚠️ S/R zone snapshot serialization failed: {e}")
                    # Fallback: save raw keys to diagnose the issue
                    try:
                        sr_zones_snapshot = {
                            '_serialization_error': str(e),
                            '_raw_keys': list(self.latest_sr_zones_data.keys()),
                            '_zone_types': {
                                'support': type(self.latest_sr_zones_data.get('support_zones', [None])[0]).__name__
                                if self.latest_sr_zones_data.get('support_zones') else 'empty',
                                'resistance': type(self.latest_sr_zones_data.get('resistance_zones', [None])[0]).__name__
                                if self.latest_sr_zones_data.get('resistance_zones') else 'empty',
                            },
                        }
                    except Exception:
                        sr_zones_snapshot = {'_serialization_error': str(e)}

            snapshot = {
                'timestamp': datetime.now().isoformat(),
                'inputs': {
                    'technical_data': technical_data,
                    'sentiment_data': sentiment_data,
                    'order_flow_data': order_flow_data,
                    'derivatives_data': derivatives_data,
                    'current_position': current_position,
                    'price_data': price_data,
                    'sr_zones': sr_zones_snapshot,
                },
                'ai_outputs': {
                    'signal': signal_data.get('signal'),
                    'confidence': signal_data.get('confidence'),
                    'risk_level': signal_data.get('risk_level'),
                    'position_size_pct': signal_data.get('position_size_pct'),
                    'stop_loss': signal_data.get('stop_loss'),
                    'take_profit': signal_data.get('take_profit'),
                    'reason': signal_data.get('reason'),

                    'judge_decision': signal_data.get('judge_decision'),
                },
            }

            with open(snapshot_file, 'w') as f:
                json.dump(snapshot, f, indent=2, default=str)

            self.log.debug(f"📸 Decision snapshot saved: {snapshot_file}")

            # 📡 Write latest_signal.json for web frontend API
            # This file is read by /api/public/latest-signal endpoint
            latest_signal_file = 'logs/latest_signal.json'
            latest_signal = {
                'signal': signal_data.get('signal', 'HOLD'),
                'confidence': signal_data.get('confidence', 'MEDIUM'),
                'reason': signal_data.get('reason', ''),
                'symbol': 'BTCUSDT',
                'timestamp': datetime.now().isoformat(),
                'risk_level': signal_data.get('risk_level', 'MEDIUM'),
                'stop_loss': signal_data.get('stop_loss'),
                'take_profit': signal_data.get('take_profit'),
            }
            with open(latest_signal_file, 'w') as f:
                json.dump(latest_signal, f, indent=2, default=str)
            self.log.debug(f"📡 Latest signal updated: {latest_signal_file}")

            # 📊 Write latest_analysis.json for AI analysis page
            # This file is read by /api/public/ai-analysis endpoint
            latest_analysis_file = 'logs/latest_analysis.json'

            # Mechanical mode: no Bull/Bear debate transcript
            bull_analysis = ''
            bear_analysis = ''

            # Get judge decision details (v3.10: support rationale + legacy key_reasons)
            judge_decision = signal_data.get('judge_decision', {})
            if isinstance(judge_decision, dict):
                # Prefer rationale (v3.10), fallback to key_reasons (legacy)
                judge_reasoning = judge_decision.get('rationale', '')
                if not judge_reasoning:
                    judge_reasons = judge_decision.get('key_reasons', [])
                    judge_reasoning = '. '.join(judge_reasons) if judge_reasons else ''
            else:
                judge_reasoning = ''
            judge_reasoning = judge_reasoning or signal_data.get('reason', '')

            # Calculate confidence score (HIGH=80, MEDIUM=60, LOW=40)
            confidence_map = {'HIGH': 80, 'MEDIUM': 60, 'LOW': 40}
            confidence_score = confidence_map.get(signal_data.get('confidence', 'MEDIUM'), 60)

            # Get current price for entry
            current_price = price_data.get('price') if price_data else None

            # v5.7: Include confluence analysis in latest_analysis
            judge_confluence = {}
            if isinstance(judge_decision, dict):
                judge_confluence = judge_decision.get('confluence', {})

            # Phase timeline (mechanical mode: empty)
            phase_timeline = {}

            # v14.0: R/R validation details for web transparency
            rr_validation = {}
            sl_price = signal_data.get('stop_loss')
            tp_price = signal_data.get('take_profit')
            signal_dir = signal_data.get('signal', 'HOLD')
            if sl_price and tp_price and current_price and signal_dir in ('LONG', 'SHORT'):
                is_long = signal_dir == 'LONG'
                try:
                    sl_f, tp_f, ep_f = float(sl_price), float(tp_price), float(current_price)
                    risk = abs(ep_f - sl_f)
                    reward = abs(tp_f - ep_f)
                    rr_ratio = round(reward / risk, 2) if risk > 0 else 0
                    min_rr = get_min_rr_ratio()
                    is_counter = _is_counter_trend(is_long, technical_data) if technical_data else False
                    effective_min_rr = min_rr
                    if is_counter:
                        effective_min_rr = round(min_rr * get_counter_trend_rr_multiplier(), 2)
                    rr_validation = {
                        'rr_ratio': rr_ratio,
                        'min_rr': effective_min_rr,
                        'is_valid': rr_ratio >= effective_min_rr,
                        'is_counter_trend': is_counter,
                    }
                except (ValueError, TypeError, ZeroDivisionError):
                    self.log.debug("Operation failed (non-critical)")
                    pass

            # v14.0: Data source quality summary for web transparency
            data_sources = {
                'technical': bool(technical_data),
                'sentiment': bool(sentiment_data) and sentiment_data.get('source') != 'default_neutral',
                'order_flow': bool(order_flow_data),
                'derivatives': bool(derivatives_data),
            }

            # v23.0: Extract Entry Timing assessment for web
            _ta = signal_data.get('_timing_assessment', {})
            entry_timing_data = {
                'timing_verdict': _ta.get('timing_verdict', 'N/A'),
                'timing_quality': _ta.get('timing_quality', 'N/A'),
                'counter_trend_risk': _ta.get('counter_trend_risk', 'NONE'),
                'alignment': _ta.get('alignment', ''),
                'reason': _ta.get('reason', ''),
            } if _ta else {}

            latest_analysis = {
                'signal': signal_data.get('signal', 'HOLD'),
                'confidence': signal_data.get('confidence', 'MEDIUM'),
                'confidence_score': confidence_score,
                'confluence': judge_confluence,
                'bull_analysis': bull_analysis or 'No bull analysis available',
                'bear_analysis': bear_analysis or 'No bear analysis available',
                'judge_reasoning': judge_reasoning or 'No judge reasoning available',
                'entry_price': current_price,
                'stop_loss': signal_data.get('stop_loss'),
                'take_profit': signal_data.get('take_profit'),
                'technical_score': technical_data.get('rsi', 50) if technical_data else 50,  # Use RSI as proxy
                'sentiment_score': sentiment_data.get('net_sentiment', 50) if sentiment_data else 50,
                'timestamp': datetime.now().isoformat(),
                # v14.0: New fields for web transparency
                'risk_appetite': signal_data.get('risk_appetite', 'NORMAL'),
                'phase_timeline': phase_timeline,
                'rr_validation': rr_validation,
                'data_sources': data_sources,
                'winning_side': judge_decision.get('winning_side', '') if isinstance(judge_decision, dict) else '',
                # v23.0: Entry Timing assessment for web dashboard
                'entry_timing': entry_timing_data,
                'timing_confidence_adjusted': signal_data.get('_timing_confidence_adjusted', ''),
            }
            with open(latest_analysis_file, 'w') as f:
                json.dump(latest_analysis, f, indent=2, default=str)
            self.log.debug(f"📊 Latest analysis updated: {latest_analysis_file}")

            # 📜 Update signal_history.json (append mode, keep last 100 signals)
            # This file is read by /api/public/signal-history endpoint
            signal_history_file = 'logs/signal_history.json'
            signal_entry = {
                'signal': signal_data.get('signal', 'HOLD'),
                'confidence': signal_data.get('confidence', 'MEDIUM'),
                'reason': signal_data.get('reason', ''),
                'timestamp': datetime.now().isoformat(),
                'result': None,  # Will be updated later when trade completes
                'stop_loss': signal_data.get('stop_loss'),
                'take_profit': signal_data.get('take_profit'),
                # Bull/Bear/Judge analysis for web AI Signal Log display
                'bull_analysis': bull_analysis or '',
                'bear_analysis': bear_analysis or '',
                'judge_reasoning': judge_reasoning or '',
                # v23.0: Entry Timing data for signal history
                'entry_timing_verdict': signal_data.get('_timing_assessment', {}).get('timing_verdict', ''),
                'entry_timing_quality': signal_data.get('_timing_assessment', {}).get('timing_quality', ''),
            }

            # Load existing history or create new
            try:
                if os.path.exists(signal_history_file):
                    with open(signal_history_file, 'r') as f:
                        history_data = json.load(f)
                        signals = history_data.get('signals', []) if isinstance(history_data, dict) else history_data
                else:
                    signals = []
            except Exception as e:
                self.log.debug(f"Using default value, original error: {e}")
                signals = []

            # Prepend new signal and keep last 100
            signals.insert(0, signal_entry)
            signals = signals[:100]

            with open(signal_history_file, 'w') as f:
                json.dump({'signals': signals}, f, indent=2, default=str)
            self.log.debug(f"📜 Signal history updated: {signal_history_file} ({len(signals)} signals)")

        except Exception as e:
            self.log.warning(f"Failed to save decision snapshot: {e}")

    def _send_heartbeat_notification(self):
        """
        v2.3: 发送心跳通知 (简化版) - 在 on_timer 开始时调用

        统一格式，简单可靠。
        """
        if not (self.telegram_bot and self.enable_telegram and getattr(self, 'telegram_notify_heartbeat', False)):
            return

        try:
            # 1. Get price - prefer real-time API price over cached bar close
            cached_price = getattr(self, '_cached_current_price', None)
            if cached_price is None and self.indicator_manager.recent_bars:
                cached_price = float(self.indicator_manager.recent_bars[-1].close)

            # Try real-time price from Binance API for more accurate display
            realtime_price = None
            try:
                if hasattr(self, 'binance_account') and self.binance_account:
                    realtime_price = self.binance_account.get_realtime_price(
                        str(self.instrument_id)
                    )
            except Exception as e:
                self.log.debug(f"Real-time price fetch is best-effort; cached price used as fallback: {e}")
                pass  # Real-time price fetch is best-effort; cached price used as fallback
            display_price = realtime_price or cached_price

            # 2. Get technical indicators (enhanced for v3.0 heartbeat)
            rsi = 0
            technical_heartbeat = {}
            try:
                if self.indicator_manager.is_initialized():
                    tech_data = self.indicator_manager.get_technical_data(cached_price or 0)
                    rsi = tech_data.get('rsi') or 0
                    technical_heartbeat = {
                        'adx': tech_data.get('adx'),
                        'adx_regime': tech_data.get('adx_regime'),
                        'trend_direction': tech_data.get('trend_direction'),
                        'volume_ratio': tech_data.get('volume_ratio'),
                        'bb_position': tech_data.get('bb_position'),
                        'macd_histogram': tech_data.get('macd_histogram'),
                    }
            except Exception as e:
                self.log.debug(f"Technical data is best-effort for heartbeat display: {e}")
                pass  # Technical data is best-effort for heartbeat display

            # 3. Get account balance
            equity = getattr(self, 'equity', 0) or 0

            # 5. Calculate uptime
            uptime_str = 'N/A'
            try:
                start_time = getattr(self, '_start_time', None)
                if start_time:
                    uptime_seconds = (datetime.now() - start_time).total_seconds()
                    hours = int(uptime_seconds // 3600)
                    minutes = int((uptime_seconds % 3600) // 60)
                    uptime_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
            except Exception as e:
                self.log.debug(f"Uptime display is non-critical: {e}")
                pass  # Uptime display is non-critical

            # 6. Get position info (v4.2: fix field names + add SL/TP)
            position_side = None
            entry_price = 0
            position_size = 0
            position_pnl_pct = 0
            sl_price = None
            tp_price = None
            try:
                pos_data = self._get_current_position_data(current_price=display_price, from_telegram=True)
                # Fix: _get_current_position_data returns 'quantity' not 'size', 'avg_px' not 'entry_price'
                if pos_data and pos_data.get('quantity', 0) != 0:
                    # Fix: side is lowercase ('long'/'short'), convert to uppercase for display
                    raw_side = pos_data.get('side', '')
                    position_side = raw_side.upper() if raw_side else None
                    entry_price = pos_data.get('avg_px') or 0
                    position_size = abs(pos_data.get('quantity') or 0)
                    if entry_price > 0 and display_price:
                        if position_side == 'LONG':
                            position_pnl_pct = ((display_price - entry_price) / entry_price) * 100
                        else:
                            position_pnl_pct = ((entry_price - display_price) / entry_price) * 100

                    # v4.2: Get SL/TP — 3-tier lookup (consistent with _get_current_position_data)
                    instrument_key = str(self.instrument_id)

                    # Level 1: sltp_state (in-memory, fastest, valid during runtime)
                    if instrument_key in self.sltp_state:
                        ts_state = self.sltp_state[instrument_key]
                        sl_price = ts_state.get('current_sl_price')
                        tp_price = ts_state.get('current_tp_price')

                    # Level 2: _layer_orders (persisted, valid after restart)
                    if (sl_price is None or tp_price is None) and self._layer_orders:
                        for layer_id, layer in self._layer_orders.items():
                            if sl_price is None and layer.get('sl_price'):
                                sl_price = layer['sl_price']
                            if tp_price is None and layer.get('tp_price'):
                                tp_price = layer['tp_price']

                    # Level 3: Binance API (regular + Algo endpoints, restart recovery fallback)
                    if sl_price is None or tp_price is None:
                        try:
                            symbol = str(self.instrument_id).split('.')[0].replace('-PERP', '')
                            pos_side = position_side.lower() if position_side else 'long'
                            sl_tp = self.binance_account.get_sl_tp_from_orders(symbol, pos_side)
                            if sl_price is None:
                                sl_price = sl_tp.get('sl_price')
                            if tp_price is None:
                                tp_price = sl_tp.get('tp_price')
                        except Exception as e:
                            self.log.debug(f"SL/TP fetch is best-effort for heartbeat display: {e}")
                            pass  # SL/TP fetch is best-effort for heartbeat display
            except Exception as e:
                self.log.debug(f"Position data is best-effort for heartbeat display: {e}")
                pass  # Position data is best-effort for heartbeat display

            # 7. Get last signal (heartbeat runs BEFORE AI analysis, so this is previous cycle's result)
            last_signal = getattr(self, 'last_signal', None) or {}
            signal = last_signal.get('signal') or 'PENDING'
            confidence = last_signal.get('confidence') or 'N/A'
            risk_level = last_signal.get('risk_level')
            position_size_pct = last_signal.get('position_size_pct')
            # Mark signal as stale (from previous cycle) so Telegram can label it correctly
            signal_is_stale = signal != 'PENDING'  # PENDING = no previous analysis yet

            # 8. Assemble v3.6/3.7/3.8 data (if available)
            order_flow_heartbeat = None
            if self.latest_order_flow_data:
                order_flow_heartbeat = {
                    'buy_ratio': self.latest_order_flow_data.get('buy_ratio'),
                    'cvd_trend': self.latest_order_flow_data.get('cvd_trend'),
                }
                # v19.2: Add pre-computed flow signals for Telegram display
                try:
                    flow_signals = self._compute_flow_signals()
                    if flow_signals:
                        order_flow_heartbeat['flow_signals'] = flow_signals
                except Exception as e:
                    self.log.debug(f"Flow signals for heartbeat: {e}")

            # v5.1: Binance funding rate (settled from /fundingRate, predicted from premiumIndex)
            derivatives_heartbeat = None
            try:
                if self.binance_kline_client:
                    binance_funding = self.binance_kline_client.get_funding_rate()
                    if binance_funding:
                        # Also fetch history for trend calculation
                        funding_history = None
                        try:
                            funding_history = self.binance_kline_client.get_funding_rate_history(limit=5)
                        except Exception as e:
                            self.log.debug(f"Funding rate history is optional for trend display: {e}")
                            pass  # Funding rate history is optional for trend display
                        # Calculate funding trend from history
                        funding_trend = None
                        if funding_history and len(funding_history) >= 3:
                            rates = [float(h.get('fundingRate', 0)) for h in funding_history]
                            if rates[-1] > rates[0] * 1.1:
                                funding_trend = 'RISING'
                            elif rates[-1] < rates[0] * 0.9:
                                funding_trend = 'FALLING'
                            else:
                                funding_trend = 'STABLE'
                        derivatives_heartbeat = {
                            'funding_rate': binance_funding.get('funding_rate'),          # Settled funding rate
                            'funding_rate_pct': binance_funding.get('funding_rate_pct'),  # Settled funding rate (%)
                            'predicted_rate': binance_funding.get('predicted_rate'),      # Predicted rate (from lastFundingRate)
                            'predicted_rate_pct': binance_funding.get('predicted_rate_pct'),  # Predicted rate (%)
                            'next_funding_countdown_min': binance_funding.get('next_funding_countdown_min'),
                            'funding_trend': funding_trend,
                            'source': 'binance',
                        }
            except Exception as e:
                self.log.debug(f"Derivatives data fetch is best-effort for heartbeat display: {e}")
                pass  # Derivatives data fetch is best-effort for heartbeat display
            # Fallback: Coinalyze OI change (funding rate already from Binance above)
            if self.latest_derivatives_data and self.latest_derivatives_data.get('enabled'):
                oi = self.latest_derivatives_data.get('open_interest', {})
                liq = self.latest_derivatives_data.get('liquidations', {})
                if derivatives_heartbeat is None:
                    derivatives_heartbeat = {}
                if oi:
                    derivatives_heartbeat['oi_change_pct'] = oi.get('change_pct')
                if liq:
                    liq_buy = float(liq.get('buy', 0) or 0)
                    liq_sell = float(liq.get('sell', 0) or 0)
                    if liq_buy > 0 or liq_sell > 0:
                        derivatives_heartbeat['liq_long'] = liq_buy
                        derivatives_heartbeat['liq_short'] = liq_sell

            orderbook_heartbeat = None
            if self.latest_orderbook_data and self.latest_orderbook_data.get('_status', {}).get('code') == 'OK':
                obi = self.latest_orderbook_data.get('obi', {})
                dynamics = self.latest_orderbook_data.get('dynamics', {})
                orderbook_heartbeat = {
                    'weighted_obi': obi.get('weighted'),
                    'obi_trend': dynamics.get('trend'),  # fix: field is 'trend' not 'obi_trend'
                }

            sr_zone_heartbeat = None
            if self.latest_sr_zones_data:
                # v5.0: Pass full S/R zone data with strength/level for Telegram display
                support_zones = self.latest_sr_zones_data.get('support_zones', [])
                resistance_zones = self.latest_sr_zones_data.get('resistance_zones', [])
                hard_control = self.latest_sr_zones_data.get('hard_control', {})

                def _zone_to_dict(zone):
                    """Convert SRZone object to dict for heartbeat."""
                    return {
                        'price': zone.price_center,
                        'price_low': zone.price_low,
                        'price_high': zone.price_high,
                        'strength': getattr(zone, 'strength', 'LOW'),
                        'level': getattr(zone, 'level', 'MINOR'),
                        'sources': getattr(zone, 'sources', []),
                        'touch_count': getattr(zone, 'touch_count', 0),
                        'has_swing_point': getattr(zone, 'has_swing_point', False),
                        'has_order_wall': getattr(zone, 'has_order_wall', False),
                        'distance_pct': getattr(zone, 'distance_pct', 0),
                    }

                # v17.0: zones are already 1+1 from calculator, no need to slice
                sr_zone_heartbeat = {
                    'support_zones': [_zone_to_dict(z) for z in support_zones],
                    'resistance_zones': [_zone_to_dict(z) for z in resistance_zones],
                    'block_long': hard_control.get('block_long', False),
                    'block_short': hard_control.get('block_short', False),
                }

            # 9. Get signal execution status (v4.1)
            # v4.4+: State consistency check - prevent cached state contradicting live position
            signal_status_heartbeat = getattr(self, '_last_signal_status', None)
            if signal_status_heartbeat and not position_side:
                cached_executed = signal_status_heartbeat.get('executed', False)
                cached_reason = signal_status_heartbeat.get('reason', '')
                cached_action = signal_status_heartbeat.get('action_taken', '')

                # Case 1: State says "has position" but actually none → position closed by SL/TP
                # Case 2: State says position opened (executed=True + action_taken contains open)
                #          but actually no position → position already closed, state is stale
                # In both cases heartbeat should not display stale info
                should_clear = False
                if '已持有' in cached_reason:
                    should_clear = True
                elif cached_executed and cached_action and '开' in cached_action:
                    # e.g. "Open Long 0.034 BTC" action but no position → stale
                    should_clear = True

                if should_clear:
                    signal_status_heartbeat = {
                        'executed': False,
                        'reason': '仓位已平仓 (SL/TP 触发)',
                        'action_taken': '',
                    }
                    self._last_signal_status = signal_status_heartbeat
                    self.log.info("🔄 检测到仓位已平仓，清除过时的执行状态")

            # 10. Send message
            heartbeat_msg = self.telegram_bot.format_heartbeat_message({
                'signal': signal,
                'confidence': confidence,
                'risk_level': risk_level,
                'position_size_pct': position_size_pct,
                'signal_is_stale': signal_is_stale,
                'price': display_price or 0,
                'rsi': rsi,
                'position_side': position_side,
                'position_pnl_pct': position_pnl_pct,
                'entry_price': entry_price,
                'position_size': position_size,
                'sl_price': sl_price,
                'tp_price': tp_price,
                'timer_count': getattr(self, '_timer_count', 0),
                'equity': equity,
                'uptime_str': uptime_str,
                'order_flow': order_flow_heartbeat,
                'derivatives': derivatives_heartbeat,
                'order_book': orderbook_heartbeat,
                'sr_zone': sr_zone_heartbeat,
                'technical': technical_heartbeat,
                'signal_status': signal_status_heartbeat,
                # v15.0 P2: Confidence decay info for heartbeat
                'confidence_decay': {
                    'history': self._confidence_history,
                    'entry_confidence': self._position_entry_confidence,
                } if self._confidence_history and self._position_entry_confidence else None,
                # v16.0: Calibration status for monitoring
                'calibration': self._get_calibration_summary(),
                # v47.0: Mechanical anticipatory scores for heartbeat display
                'anticipatory_scores': getattr(self, '_latest_anticipatory_scores', None),
                # v24.2: Trailing stop status (Binance native server-side)
                'trailing_status': self._get_trailing_heartbeat_info(),
                # v2.0: HMM regime status for heartbeat
                'regime_info': self._get_regime_heartbeat_info(),
                # v2.0: Kelly sizing status for heartbeat
                'kelly_info': self._get_kelly_heartbeat_info(),
                # v2.0: Fear & Greed index for heartbeat
                'fear_greed': getattr(self, '_cached_fear_greed', None),
            })
            # v14.0: Heartbeat → private chat only (monitoring, too frequent for subscribers)
            # v47.0: No Markdown — heartbeat has dynamic values that break parser.
            # Unicode bold/emoji provides visual structure without parse risk.
            self.telegram_bot.send_message_sync(heartbeat_msg, parse_mode=None)
            self.log.info(f"💓 Sent heartbeat #{self._timer_count}")
        except Exception as e:
            self.log.warning(f"Failed to send Telegram heartbeat: {e}")

    def _get_calibration_summary(self):
        """Get calibration status for heartbeat display."""
        try:
            from utils.calibration_loader import get_calibration_summary
            return get_calibration_summary()
        except Exception:
            return None


    def _get_trailing_heartbeat_info(self):
        """v24.2: Get trailing stop info for heartbeat display."""
        if not self._layer_orders:
            return None
        layers_with_trailing = 0
        total_layers = len(self._layer_orders)
        sample_bps = 0
        sample_activation = 0.0
        for layer in self._layer_orders.values():
            if layer.get('trailing_order_id'):
                layers_with_trailing += 1
                sample_bps = layer.get('trailing_offset_bps', 0)
                sample_activation = layer.get('trailing_activation_price', 0)
        if layers_with_trailing == 0:
            return None
        return {
            'active_count': layers_with_trailing,
            'total_layers': total_layers,
            'callback_bps': sample_bps,
            'activation_price': sample_activation,
        }

    def _get_regime_heartbeat_info(self):
        """Get HMM regime info for heartbeat display."""
        try:
            regime = getattr(self, '_current_regime', 'RANGING')
            regime_result = getattr(self, '_last_regime_result', None)
            source = 'adx_fallback'
            confidence = 1.0
            if regime_result:
                source = regime_result.get('source', 'adx_fallback')
                confidence = regime_result.get('confidence', 1.0)
            return {
                'regime': regime,
                'source': source,
                'confidence': confidence,
            }
        except Exception:
            return None

    def _get_kelly_heartbeat_info(self):
        """Get Kelly sizing info for heartbeat display."""
        try:
            kelly = getattr(self, '_kelly_sizer', None)
            if not kelly or not getattr(kelly, '_enabled', False):
                return None
            if kelly._stats is None:
                kelly._load_stats()
            total_trades = kelly._trade_count
            min_trades = kelly._min_trades
            if total_trades < min_trades:
                return {
                    'source': 'warmup',
                    'trade_count': total_trades,
                    'min_trades': min_trades,
                }
            else:
                # Get aggregate Kelly fraction info
                fraction = kelly._fraction
                return {
                    'source': 'kelly',
                    'fraction': fraction,
                    'trade_count': total_trades,
                }
        except Exception:
            return None

    def _calculate_price_change(self) -> float:
        """Calculate price change percentage (last bar only)."""
        bars = self.indicator_manager.recent_bars
        if len(bars) < 2:
            return 0.0

        current = float(bars[-1].close)
        previous = float(bars[-2].close)

        return ((current - previous) / previous) * 100

    def _compute_flow_signals(self) -> Dict[str, Any]:
        """Compute OI×CVD and CVD-Price cross-analysis signals for Telegram display.

        v19.2: Pre-compute structured signal labels from cached data.
        Used by heartbeat, trade execution, and /analyze.
        """
        signals = {}
        of = self.latest_order_flow_data or {}
        cvd_hist = of.get('cvd_history', [])

        # OI×CVD signal (CoinGlass 4-quadrant framework)
        deriv = self.latest_derivatives_data or {}
        oi_data = deriv.get('open_interest', {})
        oi_change = oi_data.get('change_pct')
        if cvd_hist and len(cvd_hist) >= 3 and oi_change is not None:
            cvd_net = sum(cvd_hist[-5:]) if len(cvd_hist) >= 5 else sum(cvd_hist)
            oi_up = oi_change > 0
            cvd_up = cvd_net > 0
            oi_cvd_map = {
                (True, True): "多方开仓中",
                (True, False): "空方开仓中",
                (False, False): "多方平仓中",
                (False, True): "空方平仓中",
            }
            signals['oi_cvd_signal'] = oi_cvd_map.get((oi_up, cvd_up))

        # CVD-Price cross-analysis (time-aligned 5-bar window)
        if cvd_hist and len(cvd_hist) >= 3:
            cvd_net = sum(cvd_hist[-5:]) if len(cvd_hist) >= 5 else sum(cvd_hist)
            price_change = None
            try:
                bars = self.indicator_manager.recent_bars
                if len(bars) >= 5:
                    price_change = (float(bars[-1].close) - float(bars[-5].close)) / float(bars[-5].close) * 100
            except Exception as e:
                self.log.debug(f"CVD price change calc: {e}")
            if price_change is not None:
                flat = abs(price_change) <= 0.3
                falling = price_change < -0.3
                rising = price_change > 0.3
                cvd_pos = cvd_net > 0
                cvd_neg = cvd_net < 0
                if falling and cvd_pos:
                    signals['cvd_price_signal'] = 'ACCUMULATION'
                    signals['cvd_price_cn'] = '逢低吸筹'
                elif rising and cvd_neg:
                    signals['cvd_price_signal'] = 'DISTRIBUTION'
                    signals['cvd_price_cn'] = '弱势推涨'
                elif falling and cvd_neg:
                    signals['cvd_price_signal'] = 'CONFIRMED'
                    signals['cvd_price_cn'] = '确认下跌'
                elif rising and cvd_pos:
                    signals['cvd_price_signal'] = 'CONFIRMED'
                    signals['cvd_price_cn'] = '确认上涨'
                elif flat and cvd_pos:
                    signals['cvd_price_signal'] = 'ABSORPTION'
                    signals['cvd_price_cn'] = '被动卖方吸收'
                elif flat and cvd_neg:
                    signals['cvd_price_signal'] = 'ABSORPTION'
                    signals['cvd_price_cn'] = '被动买方吸收'

        return signals

    def _calculate_period_statistics(self) -> Dict[str, Any]:
        """
        Calculate price statistics from available K-line history.

        Returns period high/low/change based on available bars.
        With 30m K-lines: 50 bars ≈ 25h, 48 bars = 24h

        Returns
        -------
        Dict with:
            - period_high: Highest price in period
            - period_low: Lowest price in period
            - period_change_pct: Price change % from period start
            - period_hours: Actual hours of data available
        """
        bars = self.indicator_manager.recent_bars
        if not bars or len(bars) < 2:
            return {
                'period_high': 0,
                'period_low': 0,
                'period_change_pct': 0,
                'period_hours': 0,
            }

        current_price = float(bars[-1].close)
        period_start_price = float(bars[0].open)

        # Calculate high/low from all available bars
        period_high = max(float(bar.high) for bar in bars)
        period_low = min(float(bar.low) for bar in bars)

        # Calculate price change from period start
        period_change_pct = ((current_price - period_start_price) / period_start_price) * 100 if period_start_price > 0 else 0

        # Estimate hours based on bar count (30m execution layer bars, v18.2)
        period_hours = len(bars) * 30 / 60

        return {
            'period_high': period_high,
            'period_low': period_low,
            'period_change_pct': period_change_pct,
            'period_hours': round(period_hours, 1),
        }

    def _get_account_context(self, current_price: Optional[float] = None) -> Dict[str, Any]:
        """
        Get account-level information for AI decision making (v4.6 + v4.7).

        This provides context for add/reduce position decisions:
        - How much capital is available
        - Current leverage setting
        - Maximum position capacity
        - Remaining capacity for new positions
        - Portfolio-level risk metrics (v4.7)

        Returns
        -------
        Dict with account fields:
            - equity: Total account equity (USDT)
            - leverage: Current leverage multiplier
            - max_position_value: Maximum position value allowed (equity * max_position_ratio * leverage)
            - current_position_value: Current position value (if any)
            - available_capacity: Remaining capacity for new positions
            - capacity_used_pct: Percentage of max capacity currently used
            - can_add_position: Boolean indicating if more positions can be added
            v4.7 additions:
            - total_unrealized_pnl_usd: Sum of all positions' unrealized P&L
            - liquidation_buffer_portfolio_min_pct: Minimum liquidation buffer across all positions
            - total_daily_funding_cost_usd: Daily funding cost for all positions
            - total_cumulative_funding_paid_usd: Cumulative funding paid since positions opened
            - can_add_position_safely: True if liquidation buffer > 15%
        """
        # v4.9.1: Always fetch fresh balance from Binance for accurate AI decisions
        try:
            balance = self.binance_account.get_balance()
            if 'error' not in balance and balance.get('total_balance', 0) > 0:
                real_total = balance['total_balance']
                if self.config.use_real_balance_as_equity:
                    self.equity = real_total
                    self._equity_synced = True  # v15.1
        except Exception as e:
            self.log.warning(f"Failed to refresh equity from Binance: {e}")

        # Get equity (now guaranteed to be up-to-date)
        equity = getattr(self, 'equity', 0) or self.config.equity

        # Get leverage
        leverage = getattr(self, 'leverage', self.config.leverage)

        # Get max position ratio from config
        max_position_ratio = getattr(self.config, 'max_position_ratio', 0.12)

        # Calculate max position value (notional)
        # max_position_value = equity * max_position_ratio * leverage
        max_position_value = equity * max_position_ratio * leverage

        # v4.9.1: Use Binance API for real-time position data (same as _get_current_position_data)
        current_position_value = 0.0
        total_unrealized_pnl_usd = 0.0
        liquidation_buffer_portfolio_min_pct = 100.0  # Start high, find minimum
        total_daily_funding_cost_usd = 0.0
        total_cumulative_funding_paid_usd = 0.0

        # Priority: Binance API for ground truth
        binance_positions = []
        positions = None  # v18.1: define upfront for fallback path
        try:
            symbol = str(self.instrument_id).split('.')[0].replace('-PERP', '')
            binance_positions = self.binance_account.get_positions(symbol)
            # v18.1: get_positions() returns None on API failure, [] on no positions
            if binance_positions is None:
                self.log.warning("Binance position API returned None, falling back to cache")
                positions = self.cache.positions_open(instrument_id=self.instrument_id)
        except Exception as e:
            self.log.warning(f"Binance position API failed, falling back to cache: {e}")
            # Fallback to cache
            positions = self.cache.positions_open(instrument_id=self.instrument_id)
            binance_positions = None

        maintenance_margin_ratio = 0.004  # Binance standard

        # v4.9.1: Process Binance API positions (priority) or cache fallback
        if binance_positions:
            # Process Binance API format
            for bp in binance_positions:
                position_amt = float(bp.get('positionAmt', 0))
                if position_amt == 0:
                    continue

                side = 'long' if position_amt > 0 else 'short'
                quantity = abs(position_amt)
                avg_px = float(bp.get('entryPrice', 0))
                unrealized_pnl = float(bp.get('unrealizedProfit', 0))
                mark_price = float(bp.get('markPrice', 0))
                liq_price_binance = float(bp.get('liquidationPrice', 0))
                pos_leverage = float(bp.get('leverage', leverage))

                # Use mark price or current price
                pos_price = mark_price if mark_price > 0 else (current_price if current_price and current_price > 0 else avg_px)
                position_value = quantity * pos_price
                current_position_value += position_value

                # Unrealized PnL from Binance
                total_unrealized_pnl_usd += unrealized_pnl

                # Calculate liquidation buffer
                if liq_price_binance and liq_price_binance > 0 and pos_price > 0:
                    if side == 'long':
                        buffer_pct = ((pos_price - liq_price_binance) / pos_price) * 100
                    else:
                        buffer_pct = ((liq_price_binance - pos_price) / pos_price) * 100
                    liquidation_buffer_portfolio_min_pct = min(
                        liquidation_buffer_portfolio_min_pct,
                        max(0, buffer_pct)
                    )

                # Get funding rate and calculate costs
                funding_data = getattr(self, 'latest_derivatives_data', None)
                funding_rate = 0.0
                if funding_data:
                    fr = funding_data.get('funding_rate', {})
                    if fr and isinstance(fr, dict):
                        funding_rate = fr.get('value', 0) or 0

                if funding_rate != 0 and position_value > 0:
                    daily_cost = position_value * abs(funding_rate) * 3
                    total_daily_funding_cost_usd += daily_cost
                    # Note: Binance doesn't provide position open time, estimate 24h for cumulative
                    cumulative = position_value * abs(funding_rate) * 3  # 1 day estimate
                    total_cumulative_funding_paid_usd += cumulative

        elif binance_positions is None:
            # Fallback: use NautilusTrader cache
            for position in positions:
                if position and position.is_open:
                    quantity = float(position.quantity)
                    avg_px = float(position.avg_px_open)
                    side = 'long' if position.side == PositionSide.LONG else 'short'

                    pos_price = current_price if current_price and current_price > 0 else avg_px
                    position_value = quantity * pos_price
                    current_position_value += position_value

                    try:
                        pnl = float(position.unrealized_pnl(pos_price)) if pos_price else 0.0
                        total_unrealized_pnl_usd += pnl
                    except Exception as e:
                        self.log.debug(f"PnL calculation is best-effort; position data still returned: {e}")
                        pass  # PnL calculation is best-effort; position data still returned

                    if avg_px > 0 and leverage > 0:
                        buffer_pct = None
                        if side == 'long':
                            liq_price = avg_px * (1 - 1/leverage + maintenance_margin_ratio)
                            if pos_price and liq_price > 0:
                                buffer_pct = ((pos_price - liq_price) / pos_price) * 100
                        else:
                            liq_price = avg_px * (1 + 1/leverage - maintenance_margin_ratio)
                            if pos_price and liq_price > 0:
                                buffer_pct = ((liq_price - pos_price) / pos_price) * 100

                        if buffer_pct is not None:
                            liquidation_buffer_portfolio_min_pct = min(
                                liquidation_buffer_portfolio_min_pct,
                                max(0, buffer_pct)
                            )

                    funding_data = getattr(self, 'latest_derivatives_data', None)
                    funding_rate = 0.0
                    if funding_data:
                        fr = funding_data.get('funding_rate', {})
                        if fr and isinstance(fr, dict):
                            funding_rate = fr.get('value', 0) or 0

                    if funding_rate != 0 and position_value > 0:
                        daily_cost = position_value * abs(funding_rate) * 3
                        total_daily_funding_cost_usd += daily_cost

                        try:
                            ts_opened_ns = position.ts_opened
                            if ts_opened_ns:
                                now_ns = self.clock.timestamp_ns()
                                hours_held = (now_ns - ts_opened_ns) / 1e9 / 3600
                                settlements = hours_held / 8
                                if side == 'long':
                                    cumulative = position_value * funding_rate * settlements
                                else:
                                    cumulative = -position_value * funding_rate * settlements
                                total_cumulative_funding_paid_usd += cumulative
                        except Exception as e:
                            self.log.debug(f"Funding cost calculation is best-effort for display: {e}")
                            pass  # Funding cost calculation is best-effort for display

        # If no positions, reset min buffer to N/A
        has_positions = (binance_positions and len(binance_positions) > 0) or (binance_positions is None and positions)
        if not has_positions or current_position_value == 0:
            liquidation_buffer_portfolio_min_pct = None

        # Calculate available capacity
        available_capacity = max(0, max_position_value - current_position_value)

        # Calculate capacity used percentage
        capacity_used_pct = 0.0
        if max_position_value > 0:
            capacity_used_pct = (current_position_value / max_position_value) * 100

        # Determine if can add position (at least 10% capacity remaining)
        can_add_position = capacity_used_pct < 90

        # v4.7: Safer check - also consider liquidation buffer
        can_add_position_safely = can_add_position and (
            liquidation_buffer_portfolio_min_pct is None or
            liquidation_buffer_portfolio_min_pct > 15
        )

        return {
            'equity': round(equity, 2),
            'leverage': leverage,
            'max_position_ratio': max_position_ratio,
            'max_position_value': round(max_position_value, 2),
            'current_position_value': round(current_position_value, 2),
            'available_capacity': round(available_capacity, 2),
            'capacity_used_pct': round(capacity_used_pct, 1),
            'can_add_position': can_add_position,
            # === v4.7: Portfolio-Level Risk Fields ===
            'total_unrealized_pnl_usd': round(total_unrealized_pnl_usd, 2),
            'liquidation_buffer_portfolio_min_pct': round(liquidation_buffer_portfolio_min_pct, 2) if liquidation_buffer_portfolio_min_pct is not None else None,
            'total_daily_funding_cost_usd': round(total_daily_funding_cost_usd, 2),
            'total_cumulative_funding_paid_usd': round(total_cumulative_funding_paid_usd, 2),
            'can_add_position_safely': can_add_position_safely,
        }

    def _handle_ghost_close_recording(self, current_price: float):
        """
        v42.0: Record trade outcome when ghost position is confirmed.

        When a position is closed on Binance but NT misses the on_position_closed event
        (e.g., due to order ID mismatch after restart), ghost detection catches it.
        Previously this silently cleared state — now we query Binance for actual close
        data, record the trade for AI learning, and send Telegram notification.

        Must be called BEFORE _clear_position_state() as it reads _layer_orders.
        """
        try:
            # Extract entry data from layer_orders (will be cleared after this)
            if not self._layer_orders:
                self.log.warning("Ghost close recording: no layer_orders to extract data from")
                return

            # Aggregate entry info across all layers
            total_qty = 0.0
            weighted_entry = 0.0
            first_entry_ts = None
            planned_sl = None
            planned_tp = None
            position_side = None

            for layer_id, layer in self._layer_orders.items():
                qty = layer.get('quantity', 0)
                ep = layer.get('entry_price', 0)
                total_qty += qty
                weighted_entry += qty * ep
                sl = layer.get('sl_price')
                tp = layer.get('tp_price')
                if sl and sl > 0 and not planned_sl:
                    planned_sl = sl
                if tp and tp > 0 and not planned_tp:
                    planned_tp = tp
                ts = layer.get('entry_timestamp')
                if ts and (first_entry_ts is None or ts < first_entry_ts):
                    first_entry_ts = ts

            entry_price = weighted_entry / total_qty if total_qty > 0 else 0.0

            # Determine direction from _position_is_long_for_mae or position_layers.json
            if hasattr(self, '_position_is_long_for_mae'):
                is_long = self._position_is_long_for_mae
            else:
                # Fallback: infer from SL/TP relationship
                if planned_sl and planned_sl > 0 and entry_price > 0:
                    is_long = planned_sl < entry_price
                else:
                    is_long = True  # Default guess

            position_side = 'LONG' if is_long else 'SHORT'

            # Query Binance for actual close trade
            exit_price = current_price  # Default: use current price
            pnl_usd = 0.0
            close_reason = 'GHOST_POSITION'
            close_reason_detail = '⚠️ 外部平仓 (系统未捕获)'

            if self.binance_account:
                try:
                    symbol = str(self.instrument_id).replace('-PERP', '').replace('.BINANCE', '').upper()
                    trades = self.binance_account.get_trades(symbol, limit=20)
                    if trades:
                        # Find the closing trade(s): look for SELL (closing LONG) or BUY (closing SHORT)
                        close_side = 'SELL' if is_long else 'BUY'
                        close_trades = [
                            t for t in trades
                            if t.get('side') == close_side
                            and float(t.get('realizedPnl', 0)) != 0
                        ]
                        if close_trades:
                            # Use the most recent closing trade
                            latest_close = close_trades[-1]
                            exit_price = float(latest_close.get('price', exit_price))
                            pnl_usd = float(latest_close.get('realizedPnl', 0))

                            # Detect SL vs TP from exit price
                            if planned_sl and planned_sl > 0:
                                sl_tolerance = entry_price * 0.005
                                if abs(exit_price - planned_sl) <= sl_tolerance:
                                    close_reason = 'STOP_LOSS'
                                    close_reason_detail = f'🛑 止损触发 (SL: ${planned_sl:,.2f})'
                            if planned_tp and planned_tp > 0 and close_reason == 'GHOST_POSITION':
                                tp_tolerance = entry_price * 0.005
                                if abs(exit_price - planned_tp) <= tp_tolerance:
                                    close_reason = 'TAKE_PROFIT'
                                    close_reason_detail = f'🎯 止盈触发 (TP: ${planned_tp:,.2f})'

                            self.log.info(
                                f"📊 Ghost close: found Binance trade — "
                                f"exit=${exit_price:,.2f}, pnl=${pnl_usd:.2f}, reason={close_reason}"
                            )
                except Exception as e:
                    self.log.warning(f"⚠️ Ghost close: Binance trade query failed: {e}")

            # Calculate PnL percentage
            if entry_price > 0:
                if is_long:
                    pnl_pct = (exit_price - entry_price) / entry_price * 100
                else:
                    pnl_pct = (entry_price - exit_price) / entry_price * 100
                if pnl_usd == 0:
                    pnl_usd = pnl_pct / 100 * entry_price * total_qty
            else:
                pnl_pct = 0.0

            self.log.info(
                f"📊 Ghost close P&L: {position_side} entry=${entry_price:,.2f} "
                f"exit=${exit_price:,.2f} pnl={pnl_pct:.2f}% (${pnl_usd:.2f})"
            )

            # Record in RiskController
            try:
                if entry_price > 0 and total_qty > 0:
                    self.risk_controller.record_trade_simple(
                        side=position_side,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        quantity=total_qty,
                    )
            except Exception as e:
                self.log.warning(f"Ghost close: RiskController record failed: {e}")

            # Evaluate trade
            evaluation = None
            eval_error_reason = None
            try:
                confidence = getattr(self, '_last_position_confidence', '') or 'MEDIUM'
                evaluation = evaluate_trade(
                    entry_price=entry_price,
                    exit_price=exit_price,
                    planned_sl=planned_sl if planned_sl and planned_sl > 0 else None,
                    planned_tp=planned_tp if planned_tp and planned_tp > 0 else None,
                    direction=position_side,
                    pnl_pct=pnl_pct,
                    confidence=confidence,
                    entry_timestamp=first_entry_ts,
                    exit_timestamp=datetime.now(timezone.utc).isoformat(),
                    pyramid_layers_used=len(self._layer_orders),
                )
                self.log.info(
                    f"📊 Ghost trade evaluation: Grade={evaluation.get('grade', '?')} | "
                    f"R/R actual={evaluation.get('actual_rr', 0):.1f}"
                )
            except Exception as e:
                eval_error_reason = str(e)
                self.log.warning(f"Ghost close: evaluate_trade failed: {e}")

            # Record outcome for AI learning
            try:
                if hasattr(self, 'multi_agent') and self.multi_agent:
                    self.multi_agent.record_outcome(
                        decision=position_side,
                        pnl=pnl_pct,
                        conditions=f"Ghost position detected — closed externally",
                        evaluation=evaluation,
                        eval_error_reason=eval_error_reason,
                        close_reason=close_reason,
                    )
                    self.log.info("📝 Ghost trade outcome recorded")
            except Exception as e:
                self.log.error(f"❌ Ghost close: record_outcome failed: {e}")

            # Send Telegram notification
            if self.telegram_bot and self.enable_telegram:
                try:
                    # Get S/R data for message
                    sr_zone_data = None
                    if self.latest_sr_zones_data:
                        nearest_sup = self.latest_sr_zones_data.get('nearest_support')
                        nearest_res = self.latest_sr_zones_data.get('nearest_resistance')
                        sr_zone_data = {
                            'nearest_support': nearest_sup.price_center if nearest_sup else None,
                            'nearest_resistance': nearest_res.price_center if nearest_res else None,
                        }

                    position_msg = self.telegram_bot.format_position_update({
                        'action': 'CLOSED',
                        'side': position_side,
                        'quantity': total_qty,
                        'entry_price': entry_price,
                        'current_price': exit_price,
                        'pnl': pnl_usd,
                        'pnl_pct': pnl_pct,
                        'sr_zone': sr_zone_data,
                        'close_reason': close_reason,
                        'close_reason_detail': close_reason_detail,
                    })
                    # Broadcast to subscriber channel
                    self.telegram_bot.send_message_sync(position_msg, broadcast=True)
                    self.log.info("📤 Ghost close notification sent to subscriber channel")
                except Exception as e:
                    self.log.warning(f"Ghost close: Telegram notification failed: {e}")

            # v42.0: Cancel orphaned orders on Binance (SL/TP left after ghost close).
            # Mirrors on_position_closed Phase 2 cleanup (event_handlers.py:888-904).
            # Without this, stale conditional orders remain on exchange and could
            # trigger on a future opposite-direction position.
            if self.binance_account:
                try:
                    symbol = str(self.instrument_id).replace('-PERP', '').replace('.BINANCE', '').upper()
                    cleanup = self.binance_account.cancel_all_open_orders(symbol)
                    cancelled = cleanup.get('regular_cancelled', 0)
                    algo_cancelled = cleanup.get('algo_cancelled', 0)
                    if cancelled or algo_cancelled:
                        self.log.warning(
                            f"🧹 Ghost close: cancelled {cancelled} regular + "
                            f"{algo_cancelled} Algo orphan orders on Binance"
                        )
                        if self.telegram_bot and self.enable_telegram:
                            try:
                                self.telegram_bot.send_message_sync(
                                    f"🧹 已清理 {cancelled + algo_cancelled} 个孤立条件委托挂单"
                                )
                            except Exception as e:
                                self.log.debug(f"Ghost close Telegram notification failed: {e}")
                    else:
                        self.log.info("🧹 Ghost close: no orphan orders found on Binance")
                except Exception as e:
                    self.log.warning(f"⚠️ Ghost close: Binance order cleanup failed: {e}")

        except Exception as e:
            self.log.error(f"❌ Ghost close recording failed: {e}")
            # Non-fatal: _clear_position_state will still be called after this

    def _get_current_position_data(self, current_price: Optional[float] = None, from_telegram: bool = False) -> Optional[Dict[str, Any]]:
        """
        Get current position information with enhanced data for AI decision making.

        Parameters
        ----------
        current_price : float, optional
            If provided, use this price for PnL calculation.
        from_telegram : bool, default False
            If True, NEVER access indicator_manager (Telegram thread safety).
            When True, will use cache.price() as fallback instead of indicator_manager.

        Returns
        -------
        Dict with Tier 1 + Tier 2 position fields for AI:
            - side, quantity, avg_px, unrealized_pnl (basic)
            - pnl_percentage, duration_minutes, entry_timestamp (Tier 1)
            - sl_price, tp_price, risk_reward_ratio (Tier 1)
            - peak_pnl_pct, worst_pnl_pct, entry_confidence (Tier 2)
            - margin_used_pct (Tier 2)
        """
        # v4.9.1: PRIORITY - Always try Binance API first for ground truth
        # AI decisions must be based on real exchange data, not potentially stale cache
        # This handles: server restart, manual trades on Binance web/app, cache sync issues
        use_binance_data = False
        binance_position = None
        binance_api_succeeded = False  # v18.1: distinguish API failure from "no position"

        try:
            symbol = str(self.instrument_id).split('.')[0].replace('-PERP', '')
            binance_positions = self.binance_account.get_positions(symbol)
            if binance_positions is None:
                # API call failed — fall back to NT cache
                self.log.warning("Binance API position fetch failed (None), falling back to cache")
            elif len(binance_positions) == 0:
                # v18.1: API succeeded but exchange confirms NO position.
                # Trust Binance over potentially stale NT cache.
                # This fixes: manual close on Binance app → bot still thinks position exists
                # → time barrier triggers → -2022 ReduceOnly rejection loop.
                binance_api_succeeded = True
                self.log.debug("Binance API confirms no open position")
                return None
            else:
                binance_position = binance_positions[0]
                use_binance_data = True
                binance_api_succeeded = True
                self.log.debug(f"Using Binance API for real-time position data: {symbol}")
        except Exception as e:
            self.log.warning(f"Binance API position fetch failed, falling back to cache: {e}")

        # Fallback to NautilusTrader cache only if Binance API failed
        positions = None
        if not use_binance_data and not binance_api_succeeded:
            positions = self.cache.positions_open(instrument_id=self.instrument_id)

        if not positions and not use_binance_data:
            return None

        # v4.9.1: Handle Binance API data format (priority source)
        if use_binance_data and binance_position:
            # Parse Binance position data format
            position_amt = float(binance_position.get('positionAmt', 0))
            if position_amt == 0:
                return None

            side = 'long' if position_amt > 0 else 'short'
            quantity = abs(position_amt)
            avg_px = float(binance_position.get('entryPrice', 0))
            unrealized_pnl = float(binance_position.get('unrealizedProfit', 0))

            # Get current price
            if current_price is None or current_price == 0:
                # Try markPrice from Binance position data first
                current_price = float(binance_position.get('markPrice', 0))
                if not current_price:
                    if from_telegram:
                        try:
                            current_price = self.cache.price(self.instrument_id, PriceType.LAST)
                        except (TypeError, AttributeError) as e:
                            self.log.debug(f"Using default value, original error: {e}")
                            current_price = None
                    else:
                        bars = self.indicator_manager.recent_bars
                        current_price = float(bars[-1].close) if bars else None

            # PnL percentage
            pnl_percentage = 0.0
            if avg_px > 0 and current_price:
                if side == 'long':
                    pnl_percentage = ((current_price - avg_px) / avg_px) * 100
                else:
                    pnl_percentage = ((avg_px - current_price) / avg_px) * 100

            # Duration - unknown for Binance fallback
            duration_minutes = 0
            entry_timestamp = None

            # v4.9: Get SL/TP — 3-tier lookup: sltp_state → _layer_orders → Binance API
            sl_price = None
            tp_price = None
            risk_reward_ratio = None
            instrument_key = str(self.instrument_id)

            # Level 1: sltp_state (in-memory, fastest, valid during runtime)
            if instrument_key in self.sltp_state:
                ts_state = self.sltp_state[instrument_key]
                sl_price = ts_state.get('current_sl_price')
                tp_price = ts_state.get('current_tp_price')

            # Level 2: _layer_orders (persisted, valid after restart)
            if (sl_price is None or tp_price is None) and self._layer_orders:
                for layer_id, layer in self._layer_orders.items():
                    if sl_price is None and layer.get('sl_price'):
                        sl_price = layer['sl_price']
                    if tp_price is None and layer.get('tp_price'):
                        tp_price = layer['tp_price']

            # Level 3: Binance API (regular + Algo endpoints)
            if sl_price is None or tp_price is None:
                try:
                    symbol = str(self.instrument_id).split('.')[0].replace('-PERP', '')
                    sl_tp = self.binance_account.get_sl_tp_from_orders(symbol, side)
                    if sl_price is None:
                        sl_price = sl_tp.get('sl_price')
                    if tp_price is None:
                        tp_price = sl_tp.get('tp_price')
                    if sl_price or tp_price:
                        self.log.info(f"Recovered SL/TP from Binance orders: SL={sl_price}, TP={tp_price}")
                except Exception as e:
                    self.log.warning(f"Failed to get SL/TP from Binance orders: {e}")

            # Calculate R/R ratio
            if sl_price and tp_price and avg_px > 0:
                if side == 'long':
                    risk = avg_px - sl_price
                    reward = tp_price - avg_px
                else:
                    risk = sl_price - avg_px
                    reward = avg_px - tp_price
                if risk > 0:
                    risk_reward_ratio = round(reward / risk, 2)

            # Tier 2 fields - limited data from Binance
            peak_pnl_pct = None
            worst_pnl_pct = None
            entry_confidence = None

            # Margin used percentage
            margin_used_pct = None
            equity = getattr(self, 'equity', 0)
            position_value = 0.0
            if equity and equity > 0 and current_price:
                position_value = quantity * current_price
                margin_used_pct = round((position_value / equity) * 100, 2)

            # v4.7: Liquidation fields from Binance
            leverage = float(binance_position.get('leverage', self.config.leverage))
            liquidation_price = float(binance_position.get('liquidationPrice', 0)) or None
            liquidation_buffer_pct = None
            is_liquidation_risk_high = False

            if liquidation_price and current_price:
                if side == 'long':
                    liquidation_buffer_pct = ((current_price - liquidation_price) / current_price) * 100
                else:
                    liquidation_buffer_pct = ((liquidation_price - current_price) / current_price) * 100
                liquidation_buffer_pct = round(max(0, liquidation_buffer_pct), 2)
                is_liquidation_risk_high = liquidation_buffer_pct < 10

            # v4.7: Funding fields - get from derivatives data
            funding_rate_current = None
            funding_rate_cumulative_usd = None
            effective_pnl_after_funding = None
            daily_funding_cost_usd = None

            funding_data = getattr(self, 'latest_derivatives_data', None)
            if funding_data:
                fr = funding_data.get('funding_rate', {})
                if fr and isinstance(fr, dict):
                    funding_rate_current = fr.get('value', 0) or 0

            if funding_rate_current is not None and position_value > 0:
                daily_funding_cost_usd = round(position_value * abs(funding_rate_current) * 3, 2)

            # v4.7: Drawdown fields - limited for Binance fallback
            max_drawdown_pct = None
            max_drawdown_duration_bars = None
            consecutive_lower_lows = None

            return {
                # Basic
                'side': side,
                'quantity': quantity,
                'avg_px': avg_px,
                'unrealized_pnl': unrealized_pnl,
                # Tier 1
                'pnl_percentage': round(pnl_percentage, 2),
                'duration_minutes': duration_minutes,
                'entry_timestamp': entry_timestamp,
                'sl_price': sl_price,
                'tp_price': tp_price,
                'risk_reward_ratio': risk_reward_ratio,
                # Tier 2
                'peak_pnl_pct': peak_pnl_pct,
                'worst_pnl_pct': worst_pnl_pct,
                'entry_confidence': entry_confidence,
                'margin_used_pct': margin_used_pct,
                'current_price': float(current_price) if current_price else None,
                # v4.7: Liquidation
                'liquidation_price': liquidation_price,
                'liquidation_buffer_pct': liquidation_buffer_pct,
                'is_liquidation_risk_high': is_liquidation_risk_high,
                # v4.7: Funding
                'funding_rate_current': funding_rate_current,
                'funding_rate_cumulative_usd': funding_rate_cumulative_usd,
                'effective_pnl_after_funding': effective_pnl_after_funding,
                'daily_funding_cost_usd': daily_funding_cost_usd,
                # v4.7: Drawdown
                'max_drawdown_pct': max_drawdown_pct,
                'max_drawdown_duration_bars': max_drawdown_duration_bars,
                'consecutive_lower_lows': consecutive_lower_lows,
                # v4.9: Source indicator
                '_source': 'binance_api_realtime',  # v4.9.1: Priority source for ground truth
            }

        # Get the first open position (should only be one for netting OMS)
        position = positions[0]

        if position and position.is_open:
            # Get current price for PnL calculation
            if current_price is None or current_price == 0:
                if from_telegram:
                    # CRITICAL: Never access indicator_manager from Telegram thread!
                    # Rust indicators (RSI, MACD) are not Send/Sync and will panic
                    try:
                        current_price = self.cache.price(self.instrument_id, PriceType.LAST)
                    except (TypeError, AttributeError) as e:
                        self.log.debug(f"Using default value, original error: {e}")
                        current_price = None
                else:
                    # Main thread: safe to access indicator_manager
                    bars = self.indicator_manager.recent_bars
                    if bars:
                        current_price = bars[-1].close
                    else:
                        try:
                            current_price = self.cache.price(self.instrument_id, PriceType.LAST)
                        except (TypeError, AttributeError) as e:
                            self.log.debug(f"Using default value, original error: {e}")
                            current_price = None

            # === Basic fields (existing) ===
            side = 'long' if position.side == PositionSide.LONG else 'short'
            quantity = float(position.quantity)
            avg_px = float(position.avg_px_open)
            unrealized_pnl = float(position.unrealized_pnl(current_price)) if current_price else 0.0

            # === Tier 1: Must have ===
            # PnL percentage
            pnl_percentage = 0.0
            if avg_px > 0 and current_price:
                if side == 'long':
                    pnl_percentage = ((current_price - avg_px) / avg_px) * 100
                else:
                    pnl_percentage = ((avg_px - current_price) / avg_px) * 100

            # Duration in minutes
            duration_minutes = 0
            entry_timestamp = None
            try:
                # NautilusTrader Position has ts_opened (nanoseconds)
                ts_opened_ns = position.ts_opened
                if ts_opened_ns:
                    entry_timestamp = datetime.fromtimestamp(ts_opened_ns / 1e9, tz=timezone.utc).isoformat()
                    now_ns = self.clock.timestamp_ns()
                    duration_minutes = int((now_ns - ts_opened_ns) / 1e9 / 60)
            except Exception as e:
                self.log.debug(f"Entry timestamp is best-effort for display: {e}")
                pass  # Entry timestamp is best-effort for display

            # SL/TP from sltp_state
            sl_price = None
            tp_price = None
            risk_reward_ratio = None
            instrument_key = str(self.instrument_id)
            if instrument_key in self.sltp_state:
                ts_state = self.sltp_state[instrument_key]
                sl_price = ts_state.get('current_sl_price')
                tp_price = ts_state.get('current_tp_price')

                # Calculate R/R ratio
                if sl_price and tp_price and avg_px > 0:
                    if side == 'long':
                        risk = avg_px - sl_price
                        reward = tp_price - avg_px
                    else:
                        risk = sl_price - avg_px
                        reward = avg_px - tp_price
                    if risk > 0:
                        risk_reward_ratio = round(reward / risk, 2)

            # === Tier 2: Recommended ===
            # Peak/worst PnL (from sltp_state tracking)
            peak_pnl_pct = None
            worst_pnl_pct = None
            if instrument_key in self.sltp_state:
                ts_state = self.sltp_state[instrument_key]
                high_price = ts_state.get('highest_price')
                low_price = ts_state.get('lowest_price')

                if side == 'long':
                    if high_price and avg_px > 0:
                        peak_pnl_pct = round(((high_price - avg_px) / avg_px) * 100, 2)
                    if low_price and avg_px > 0:
                        worst_pnl_pct = round(((low_price - avg_px) / avg_px) * 100, 2)
                else:  # short
                    if low_price and avg_px > 0:
                        peak_pnl_pct = round(((avg_px - low_price) / avg_px) * 100, 2)
                    if high_price and avg_px > 0:
                        worst_pnl_pct = round(((avg_px - high_price) / avg_px) * 100, 2)

            # Entry confidence from last_signal
            entry_confidence = None
            last_signal = getattr(self, 'last_signal', None)
            if last_signal:
                entry_confidence = last_signal.get('confidence')

            # Margin used percentage (position value / equity)
            margin_used_pct = None
            equity = getattr(self, 'equity', 0)
            position_value = 0.0
            if equity and equity > 0 and current_price:
                position_value = quantity * current_price
                margin_used_pct = round((position_value / equity) * 100, 2)

            # === v4.7: Liquidation Risk Fields (CRITICAL) ===
            # Calculate liquidation price using simplified formula
            # LONG: liq_price = entry * (1 - 1/leverage + maintenance_margin)
            # SHORT: liq_price = entry * (1 + 1/leverage - maintenance_margin)
            leverage = getattr(self, 'leverage', self.config.leverage)
            maintenance_margin_ratio = 0.004  # Binance standard for 20x leverage tier

            liquidation_price = None
            liquidation_buffer_pct = None
            is_liquidation_risk_high = False

            if avg_px > 0 and leverage > 0:
                if side == 'long':
                    liquidation_price = avg_px * (1 - 1/leverage + maintenance_margin_ratio)
                    if current_price and liquidation_price > 0:
                        liquidation_buffer_pct = ((current_price - liquidation_price) / current_price) * 100
                else:  # short
                    liquidation_price = avg_px * (1 + 1/leverage - maintenance_margin_ratio)
                    if current_price and liquidation_price > 0:
                        liquidation_buffer_pct = ((liquidation_price - current_price) / current_price) * 100

                if liquidation_buffer_pct is not None:
                    liquidation_buffer_pct = round(max(0, liquidation_buffer_pct), 2)
                    is_liquidation_risk_high = liquidation_buffer_pct < 10  # < 10% buffer is risky

            # === v4.7: Funding Rate Fields (CRITICAL for perpetuals) ===
            funding_rate_current = None
            funding_rate_cumulative_usd = None
            effective_pnl_after_funding = None
            daily_funding_cost_usd = None

            # Get funding rate from latest_derivatives_data
            funding_data = getattr(self, 'latest_derivatives_data', None)
            if funding_data:
                fr = funding_data.get('funding_rate', {})
                if fr and isinstance(fr, dict):
                    funding_rate_current = fr.get('value', 0) or 0

            # Calculate funding costs if we have the rate
            if funding_rate_current is not None and position_value > 0:
                # Daily funding cost = position_value * |rate| * 3 settlements/day
                daily_funding_cost_usd = round(position_value * abs(funding_rate_current) * 3, 2)

                # Estimate cumulative funding based on position duration
                # 8-hour settlements, so settlements_passed = hours_held / 8
                hours_held = duration_minutes / 60 if duration_minutes > 0 else 0
                settlements_passed = hours_held / 8

                # For LONG with positive funding: we pay; for SHORT with positive funding: we receive
                if side == 'long':
                    funding_rate_cumulative_usd = round(position_value * funding_rate_current * settlements_passed, 2)
                else:
                    funding_rate_cumulative_usd = round(-position_value * funding_rate_current * settlements_passed, 2)

                # Effective PnL = unrealized PnL - cumulative funding paid
                effective_pnl_after_funding = round(unrealized_pnl - funding_rate_cumulative_usd, 2)

            # === v4.7: Drawdown Attribution Fields (RECOMMENDED) ===
            max_drawdown_pct = None
            max_drawdown_duration_bars = None
            consecutive_lower_lows = 0

            # Calculate drawdown from peak
            if peak_pnl_pct is not None and pnl_percentage is not None:
                if peak_pnl_pct > pnl_percentage:
                    max_drawdown_pct = round(peak_pnl_pct - pnl_percentage, 2)
                else:
                    max_drawdown_pct = 0.0

            # Estimate drawdown duration in 30-min bars (v18.2: execution layer 30M)
            if max_drawdown_pct and max_drawdown_pct > 0:
                # Simplified: assume drawdown started at some point during position
                max_drawdown_duration_bars = max(1, duration_minutes // 30)

            # Count consecutive lower lows from recent bars (if accessible)
            if not from_telegram:  # Only in main thread
                try:
                    bars = self.indicator_manager.recent_bars
                    if bars and len(bars) >= 3:
                        count = 0
                        for i in range(len(bars) - 1, 0, -1):
                            if bars[i].low < bars[i-1].low:
                                count += 1
                            else:
                                break
                        consecutive_lower_lows = count
                except Exception as e:
                    self.log.debug(f"Bar analysis is best-effort; position data still returned: {e}")
                    pass  # Bar analysis is best-effort; position data still returned

            return {
                # Basic (existing)
                'side': side,
                'quantity': quantity,
                'avg_px': avg_px,
                'unrealized_pnl': unrealized_pnl,
                # Tier 1
                'pnl_percentage': round(pnl_percentage, 2),
                'duration_minutes': duration_minutes,
                'entry_timestamp': entry_timestamp,
                'sl_price': sl_price,
                'tp_price': tp_price,
                'risk_reward_ratio': risk_reward_ratio,
                # Tier 2
                'peak_pnl_pct': peak_pnl_pct,
                'worst_pnl_pct': worst_pnl_pct,
                'entry_confidence': entry_confidence,
                'margin_used_pct': margin_used_pct,
                # Context
                'current_price': float(current_price) if current_price else None,
                # === v4.7: Liquidation Risk (CRITICAL) ===
                'liquidation_price': round(liquidation_price, 2) if liquidation_price else None,
                'liquidation_buffer_pct': liquidation_buffer_pct,
                'is_liquidation_risk_high': is_liquidation_risk_high,
                # === v4.7: Funding Rate (CRITICAL) ===
                'funding_rate_current': funding_rate_current,
                'funding_rate_cumulative_usd': funding_rate_cumulative_usd,
                'effective_pnl_after_funding': effective_pnl_after_funding,
                'daily_funding_cost_usd': daily_funding_cost_usd,
                # === v4.7: Drawdown Attribution (RECOMMENDED) ===
                'max_drawdown_pct': max_drawdown_pct,
                'max_drawdown_duration_bars': max_drawdown_duration_bars,
                'consecutive_lower_lows': consecutive_lower_lows,
                # v4.9.1: Source indicator (fallback when Binance API failed)
                '_source': 'nautilus_cache_fallback',
            }

        return None

    # NOTE: Hierarchical Decision Architecture - MultiAgent Judge as sole decision maker
    # No signal merging logic needed; Judge decision is the final decision

    # v23.0: _quick_direction + _check_alignment_gate removed — replaced by Entry Timing Agent

    # ========== v18 Item 23: Gate Audit Log ==========

    def _log_gate_audit(self, gate: str, result: str, signal_was: str, details: dict):
        """Log gate decision to data/gate_audit.jsonl for observability."""
        try:
            import random

            # 10% sampling for PASS events (BLOCKED always logged)
            if result == "PASS" and random.random() > 0.1:
                return

            os.makedirs('data', exist_ok=True)

            # Weekly rotation: use ISO week suffix
            now = datetime.now(timezone.utc)
            week_suffix = now.strftime('%Y_W%W')
            log_path = f"data/gate_audit_{week_suffix}.jsonl"

            entry = {
                "timestamp": now.isoformat(),
                "gate": gate,
                "result": result,
                "signal_was": signal_was,
                "details": details,
            }

            with open(log_path, "a") as f:
                f.write(json.dumps(entry, default=str) + "\n")

            # Cleanup: keep max 4 weeks of files
            import glob
            audit_files = sorted(glob.glob("data/gate_audit_*.jsonl"))
            while len(audit_files) > 4:
                try:
                    os.remove(audit_files.pop(0))
                except OSError:
                    break
        except Exception as e:
            self.log.debug(f"Gate audit log write failed: {e}")

