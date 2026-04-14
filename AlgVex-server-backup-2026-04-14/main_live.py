"""
Live Trading Entrypoint for AlgVex Trading Strategy

Runs the mechanical anticipatory strategy on Binance Futures (BTCUSDT-PERP) with live market data.
"""

import os
import sys
import time
import signal
import argparse
import traceback
import requests
from pathlib import Path

# =============================================================================
# CRITICAL: Apply patches BEFORE importing NautilusTrader
# This fixes Binance enum compatibility issues (e.g., POSITION_RISK_CONTROL)
# =============================================================================
# Ensure project root is in path for patches import
project_root = Path(__file__).parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from patches.binance_enums import apply_all_patches
apply_all_patches()
# =============================================================================

from dotenv import load_dotenv

from utils.config_manager import ConfigManager

from nautilus_trader.adapters.binance.common.enums import BinanceAccountType, BinanceEnvironment
from nautilus_trader.adapters.binance.config import BinanceDataClientConfig, BinanceExecClientConfig
from nautilus_trader.adapters.binance.factories import BinanceLiveDataClientFactory, BinanceLiveExecClientFactory
from nautilus_trader.config import InstrumentProviderConfig, LiveExecEngineConfig, LoggingConfig, TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import TraderId, InstrumentId
from nautilus_trader.trading.config import ImportableStrategyConfig

from strategy.ai_strategy import AITradingStrategy, AITradingStrategyConfig
from utils.binance_orderbook_client import BinanceOrderBookClient
from utils.orderbook_processor import OrderBookProcessor


# Load environment variables
# Priority: 1. ~/.env.algvex (permanent) 2. .env (local/symlink)
env_permanent = Path.home() / ".env.algvex"
env_local = project_root / ".env"

if env_permanent.exists():
    load_dotenv(env_permanent)
    print(f"[CONFIG] Loaded environment from {env_permanent}")
elif env_local.exists():
    load_dotenv(env_local)
    print(f"[CONFIG] Loaded environment from {env_local}")
else:
    load_dotenv()  # Try default locations
    print("[CONFIG] Warning: No .env file found, using system environment")



def _strip_env_comment(value: str) -> str:
    """
    Safely strip inline comments from environment variable value.

    Only strips comments that are clearly separated (space + #).
    This preserves values that legitimately contain '#' (like API keys).

    Examples:
        "abc123"       -> "abc123"       (no comment)
        "abc#123"      -> "abc#123"      (# is part of value, no space before)
        "abc123 #note" -> "abc123"       (space+# indicates comment)
        "abc # test"   -> "abc"          (space+# indicates comment)
    """
    # Only strip if there's " #" (space followed by #)
    if ' #' in value:
        value = value.split(' #')[0]
    return value.strip()


def get_env_float(key: str, default: str) -> float:
    """
    Safely get float environment variable, removing any inline comments.
    """
    value = os.getenv(key, default)
    value = _strip_env_comment(value)
    return float(value)


def get_env_str(key: str, default: str) -> str:
    """
    Safely get string environment variable, removing any inline comments.
    """
    value = os.getenv(key, default)
    return _strip_env_comment(value)


def get_env_int(key: str, default: str) -> int:
    """
    Safely get integer environment variable, removing any inline comments.
    """
    value = os.getenv(key, default)
    value = _strip_env_comment(value)
    return int(value)


def get_strategy_config(config_manager: ConfigManager) -> AITradingStrategyConfig:
    """
    Build strategy configuration from ConfigManager.

    Parameters
    ----------
    config_manager : ConfigManager
        Configuration manager instance

    Returns
    -------
    AITradingStrategyConfig
        Strategy configuration
    """
    # Get configuration values from ConfigManager ONLY (no env var overrides for business params)
    # Reference: CLAUDE.md - 配置分层架构原则
    equity = config_manager.get('capital', 'equity', default=1000)
    leverage = config_manager.get('capital', 'leverage', default=5)
    base_position = config_manager.get('position', 'base_usdt_amount', default=100)

    # Get timeframe from config (environment-specific via {env}.yaml)
    timeframe = config_manager.get('trading', 'timeframe', default='30m')

    # Debug output
    print(f"[CONFIG] Equity: {equity}")
    print(f"[CONFIG] Base Position: {base_position}")
    print(f"[CONFIG] Timeframe: {timeframe}")

    # Parse timeframe to bar specification
    timeframe_to_bar_spec = {
        '1m': '1-MINUTE-LAST',
        '5m': '5-MINUTE-LAST',
        '15m': '15-MINUTE-LAST',
        '30m': '30-MINUTE-LAST',
        '1h': '1-HOUR-LAST',
        '4h': '4-HOUR-LAST',
        '1d': '1-DAY-LAST',
    }
    bar_spec = timeframe_to_bar_spec.get(timeframe, '30-MINUTE-LAST')

    # Get instrument from config
    instrument_id = config_manager.get('trading', 'instrument_id', default='BTCUSDT-PERP.BINANCE')
    symbol = instrument_id.split('.')[0]  # Extract symbol from instrument_id
    final_bar_type = f"{symbol}.BINANCE-{bar_spec}-EXTERNAL"

    return AITradingStrategyConfig(
        instrument_id=instrument_id,
        bar_type=final_bar_type,

        # Capital
        equity=equity,
        leverage=leverage,
        use_real_balance_as_equity=config_manager.get('capital', 'use_real_balance_as_equity', default=True),

        # Position sizing (all from ConfigManager, no env var overrides)
        base_usdt_amount=base_position,
        high_confidence_multiplier=config_manager.get('position', 'high_confidence_multiplier', default=1.5),
        medium_confidence_multiplier=config_manager.get('position', 'medium_confidence_multiplier', default=1.0),
        low_confidence_multiplier=config_manager.get('position', 'low_confidence_multiplier', default=0.5),
        max_position_ratio=config_manager.get('position', 'max_position_ratio', default=0.12),
        trend_strength_multiplier=config_manager.get('position', 'trend_strength_multiplier', default=1.2),
        min_trade_amount=config_manager.get('position', 'min_trade_amount', default=0.001),

        # v4.8: Position sizing method configuration
        position_sizing_method=config_manager.get('risk', 'position_sizing', 'method', default='ai_controlled'),
        position_sizing_default_pct=config_manager.get('risk', 'position_sizing', 'ai_controlled', 'default_size_pct', default=50.0),
        position_sizing_high_pct=config_manager.get('risk', 'position_sizing', 'ai_controlled', 'confidence_mapping', 'HIGH', default=80.0),
        position_sizing_medium_pct=config_manager.get('risk', 'position_sizing', 'ai_controlled', 'confidence_mapping', 'MEDIUM', default=50.0),
        position_sizing_low_pct=config_manager.get('risk', 'position_sizing', 'ai_controlled', 'confidence_mapping', 'LOW', default=30.0),
        position_sizing_cumulative=True,  # v4.8: 累加模式，允许多次加仓

        # v6.0: Cooldown configuration
        cooldown_enabled=config_manager.get('risk', 'cooldown', 'enabled', default=True),
        cooldown_per_stoploss_candles=config_manager.get('risk', 'cooldown', 'per_stoploss_candles', default=2),
        cooldown_noise_stop_candles=config_manager.get('risk', 'cooldown', 'noise_stop_candles', default=1),
        cooldown_reversal_stop_candles=config_manager.get('risk', 'cooldown', 'reversal_stop_candles', default=6),
        cooldown_volatility_stop_candles=config_manager.get('risk', 'cooldown', 'volatility_stop_candles', default=12),
        cooldown_detection_candles=config_manager.get('risk', 'cooldown', 'detection_candles', default=2),

        # v6.0: Pyramiding configuration
        pyramiding_enabled=config_manager.get('risk', 'pyramiding', 'enabled', default=True),
        pyramiding_layer_sizes=tuple(config_manager.get('risk', 'pyramiding', 'layer_sizes', default=[0.50, 0.30, 0.20])),
        pyramiding_min_profit_atr=config_manager.get('risk', 'pyramiding', 'min_profit_atr', default=1.0),
        pyramiding_min_confidence=config_manager.get('risk', 'pyramiding', 'min_confidence', default='HIGH'),
        pyramiding_counter_trend_allowed=config_manager.get('risk', 'pyramiding', 'counter_trend_allowed', default=False),
        pyramiding_max_funding_rate=config_manager.get('risk', 'pyramiding', 'max_funding_rate', default=0.0003),
        pyramiding_min_adx=config_manager.get('risk', 'pyramiding', 'min_adx', default=25.0),

        # Technical indicators (all from ConfigManager)
        # For 1m timeframe, use development.yaml with shorter periods
        sma_periods=config_manager.get('indicators', 'sma_periods', default=[5, 20, 50]),
        rsi_period=config_manager.get('indicators', 'rsi_period', default=14),
        macd_fast=config_manager.get('indicators', 'macd_fast', default=12),
        macd_slow=config_manager.get('indicators', 'macd_slow', default=26),
        bb_period=config_manager.get('indicators', 'bb_period', default=20),
        bb_std=config_manager.get('indicators', 'bb_std', default=2.0),
        volume_ma_period=config_manager.get('indicators', 'volume_ma_period', default=20),
        support_resistance_lookback=config_manager.get('indicators', 'support_resistance_lookback', default=20),

        # v3.0: S/R Zone Calculator config (passed as dict to MultiAgentAnalyzer)
        sr_zones_config=config_manager.get('sr_zones', default={}),

        # v10.0: Strategy mode
        strategy_mode=config_manager.get('strategy_mode', default='mechanical'),

        # Sentiment
        sentiment_enabled=config_manager.get('sentiment', 'enabled', default=True),
        sentiment_lookback_hours=config_manager.get('sentiment', 'lookback_hours', default=4),
        # Set sentiment timeframe based on bar timeframe (default to 30m, v18.2)
        sentiment_timeframe="1m" if timeframe == "1m" else ("5m" if timeframe == "5m" else config_manager.get('sentiment', 'timeframe', default='30m')),

        # Risk (all from ConfigManager, no env var overrides)
        min_confidence_to_trade=config_manager.get('risk', 'min_confidence_to_trade', default='LOW'),
        allow_reversals=config_manager.get('risk', 'allow_reversals', default=True),
        require_high_confidence_for_reversal=config_manager.get('risk', 'require_high_confidence_for_reversal', default=False),
        rsi_extreme_threshold_upper=config_manager.get('risk', 'rsi_extreme_threshold_upper', default=70.0),
        rsi_extreme_threshold_lower=config_manager.get('risk', 'rsi_extreme_threshold_lower', default=30.0),
        rsi_extreme_multiplier=config_manager.get('risk', 'rsi_extreme_multiplier', default=0.7),

        # Stop Loss & Take Profit (from ConfigManager)
        enable_auto_sl_tp=config_manager.get('risk', 'stop_loss', 'enabled', default=True),
        tp_high_confidence_pct=config_manager.get('risk', 'take_profit', 'high_confidence_pct', default=0.03),
        tp_medium_confidence_pct=config_manager.get('risk', 'take_profit', 'medium_confidence_pct', default=0.02),
        tp_low_confidence_pct=config_manager.get('risk', 'take_profit', 'low_confidence_pct', default=0.01),

        # OCO (from ConfigManager)
        enable_oco=config_manager.get('risk', 'oco', 'enabled', default=True),

        # Execution
        position_adjustment_threshold=config_manager.get('execution', 'position_adjustment_threshold', default=0.001),

        # Timing (from ConfigManager, environment-specific via {env}.yaml)
        timer_interval_sec=config_manager.get('timing', 'timer_interval_sec', default=1200),

        # Telegram Notifications
        enable_telegram=config_manager.get('telegram', 'enabled', default=False),
        telegram_bot_token=get_env_str('TELEGRAM_BOT_TOKEN', ''),
        telegram_chat_id=get_env_str('TELEGRAM_CHAT_ID', ''),
        # v14.0: Notification group (separate bot for public signal broadcasting)
        telegram_notification_bot_token=config_manager.get('telegram', 'notification_group', 'bot_token', default='') or get_env_str('TELEGRAM_NOTIFICATION_BOT_TOKEN', ''),
        telegram_notification_chat_id=config_manager.get('telegram', 'notification_group', 'chat_id', default='') or get_env_str('TELEGRAM_NOTIFICATION_CHAT_ID', ''),
        telegram_notify_signals=config_manager.get('telegram', 'notify', 'signals', default=True),
        telegram_notify_fills=config_manager.get('telegram', 'notify', 'fills', default=True),
        telegram_notify_positions=config_manager.get('telegram', 'notify', 'positions', default=True),
        telegram_notify_errors=config_manager.get('telegram', 'notify', 'errors', default=True),
        telegram_notify_heartbeat=config_manager.get('telegram', 'notify', 'heartbeat', default=True),  # v2.1
        # v3.13: 新增通知开关
        telegram_notify_sltp_update=config_manager.get('telegram', 'notify', 'sltp_update', default=True),
        telegram_notify_startup=config_manager.get('telegram', 'notify', 'startup', default=True),
        telegram_notify_shutdown=config_manager.get('telegram', 'notify', 'shutdown', default=True),
        # v3.13: 自动总结配置
        telegram_auto_daily=config_manager.get('telegram', 'summary', 'auto_daily', default=False),
        telegram_auto_weekly=config_manager.get('telegram', 'summary', 'auto_weekly', default=False),
        telegram_daily_hour_utc=config_manager.get('telegram', 'summary', 'daily_hour_utc', default=0),
        telegram_weekly_day=config_manager.get('telegram', 'summary', 'weekly_day', default=0),

        # Telegram Queue (v4.0 - Non-blocking message sending)
        telegram_queue_enabled=config_manager.get('telegram', 'queue', 'enabled', default=True),
        telegram_queue_db_path=config_manager.get('telegram', 'queue', 'db_path', default='data/telegram_queue.db'),
        telegram_queue_max_retries=config_manager.get('telegram', 'queue', 'max_retries', default=3),
        telegram_queue_alert_cooldown=config_manager.get('telegram', 'queue', 'alert_cooldown', default=300),
        telegram_queue_send_interval=config_manager.get('telegram', 'queue', 'send_interval', default=0.5),

        # Telegram Security (v4.0 - PIN verification + audit logging)
        telegram_security_enable_pin=config_manager.get('telegram', 'security', 'enable_pin', default=True),
        telegram_security_pin_code=config_manager.get('telegram', 'security', 'pin_code', default=''),
        telegram_security_pin_expiry_seconds=config_manager.get('telegram', 'security', 'pin_expiry_seconds', default=60),
        telegram_security_rate_limit_per_minute=config_manager.get('telegram', 'security', 'rate_limit_per_minute', default=30),
        telegram_security_enable_audit=config_manager.get('telegram', 'security', 'enable_audit', default=True),
        telegram_security_audit_log_dir=config_manager.get('telegram', 'security', 'audit_log_dir', default='logs/audit'),

        # Network configuration
        network_telegram_startup_delay=config_manager.get('network', 'telegram', 'startup_delay', default=5.0),
        network_telegram_polling_max_retries=config_manager.get('network', 'telegram', 'polling_max_retries', default=3),
        network_telegram_polling_base_delay=config_manager.get('network', 'telegram', 'polling_base_delay', default=10.0),
        network_binance_recv_window=config_manager.get('network', 'binance', 'recv_window', default=5000),
        network_binance_balance_cache_ttl=config_manager.get('network', 'binance', 'balance_cache_ttl', default=5.0),
        network_bar_persistence_max_limit=config_manager.get('network', 'bar_persistence', 'max_limit', default=1500),
        network_bar_persistence_timeout=config_manager.get('network', 'bar_persistence', 'timeout', default=10.0),
        sentiment_timeout=config_manager.get('sentiment', 'timeout', default=10.0),

        # Multi-Timeframe Configuration (v3.3: removed unused filter configs)
        multi_timeframe_enabled=config_manager.get('multi_timeframe', 'enabled', default=False),
        mtf_trend_sma_period=config_manager.get('multi_timeframe', 'trend_layer', 'sma_period', default=200),

        # Network: Instrument Discovery (previously hardcoded in on_start)
        network_instrument_discovery_max_retries=config_manager.get('network', 'instrument_discovery', 'max_retries', default=60),
        network_instrument_discovery_retry_interval=config_manager.get('network', 'instrument_discovery', 'retry_interval', default=1.0),

        # Network: Binance API timeout
        network_binance_api_timeout=config_manager.get('network', 'binance', 'api_timeout', default=10.0),

        # Network: Telegram message timeout
        network_telegram_message_timeout=config_manager.get('network', 'telegram', 'message_timeout', default=30.0),

        # Network: Telegram command handler API timeout (connect/read/write for polling)
        network_telegram_api_timeout=config_manager.get('network', 'telegram', 'api_timeout', default=30.0),

        # v3.12: Risk Controller / Circuit Breakers configuration
        risk_config=config_manager.get('risk', default={}),

        # v2.0: Upgrade plan component configs
        hmm_config=config_manager.get('hmm', default={}),
        kelly_config=config_manager.get('kelly', default={}),
        risk_regime_config=config_manager.get('risk_regime', default={}),
        prometheus_config=config_manager.get('prometheus', default={}),

        # Order Book Configuration (v3.7)
        order_book_enabled=config_manager.get('order_book', 'enabled', default=False),
        order_book_api_timeout=config_manager.get('order_book', 'api', 'timeout', default=10.0),
        order_book_api_max_retries=config_manager.get('order_book', 'api', 'max_retries', default=2),
        order_book_api_retry_delay=config_manager.get('order_book', 'api', 'retry_delay', default=1.0),
        order_book_price_band_pct=config_manager.get('order_book', 'processing', 'price_band_pct', default=0.5),
        order_book_anomaly_threshold=config_manager.get('order_book', 'processing', 'anomaly_detection', 'base_threshold', default=3.0),
        order_book_slippage_amounts=tuple(config_manager.get('order_book', 'processing', 'slippage_amounts', default=[0.1, 0.5, 1.0])),
        order_book_weighted_decay=config_manager.get('order_book', 'processing', 'weighted_obi', 'base_decay', default=0.8),
        order_book_adaptive_decay=config_manager.get('order_book', 'processing', 'weighted_obi', 'adaptive', default=True),
        order_book_history_size=config_manager.get('order_book', 'processing', 'history', 'size', default=10),

        # v15.0: 原硬编码值提取到配置
        emergency_sl_base_pct=config_manager.get('trading_logic', 'emergency_sl', 'base_pct', default=0.02),
        emergency_sl_atr_multiplier=config_manager.get('trading_logic', 'emergency_sl', 'atr_multiplier', default=1.5),
        emergency_sl_cooldown_seconds=config_manager.get('trading_logic', 'emergency_sl', 'cooldown_seconds', default=120),
        emergency_sl_max_consecutive=config_manager.get('trading_logic', 'emergency_sl', 'max_consecutive', default=3),
        sr_zones_cache_ttl_seconds=config_manager.get('sr_zones', 'cache_ttl_seconds', default=1800),
        price_cache_ttl_seconds=config_manager.get('timing', 'price_cache_ttl_seconds', default=300),
        reversal_timeout_seconds=config_manager.get('timing', 'reversal_timeout_seconds', default=300),
        max_leverage_limit=config_manager.get('capital', 'max_leverage_limit', default=125),
    )


def get_binance_config(config_manager: ConfigManager | None = None, strategy_type: str = 'ai') -> tuple:
    """
    Build Binance data and execution client configs.

    Parameters
    ----------
    config_manager : ConfigManager, optional
        Configuration manager for reading recv_window_ms etc.
    strategy_type : str
        'ai' or 'srp' — SRP uses SRP_BINANCE_API_KEY if available

    Returns
    -------
    tuple
        (data_config, exec_config)
    """
    # Get API credentials — SRP uses its own keys if configured
    if strategy_type == 'srp':
        api_key = os.getenv('SRP_BINANCE_API_KEY', os.getenv('BINANCE_API_KEY'))
        api_secret = os.getenv('SRP_BINANCE_API_SECRET', os.getenv('BINANCE_API_SECRET'))
    else:
        api_key = os.getenv('BINANCE_API_KEY')
        api_secret = os.getenv('BINANCE_API_SECRET')

    if not api_key or not api_secret:
        raise ValueError("Binance API credentials required in .env")

    # Read recv_window from config (fixes -1021 timestamp errors)
    recv_window_ms = 5000
    if config_manager:
        recv_window_ms = config_manager.get('network', 'binance', 'recv_window', default=5000)

    # CRITICAL: Use load_all=True for proper instrument initialization
    # NautilusTrader 1.221.0 has fixed non-ASCII symbol issues
    # The binance_positions.py patch provides additional filtering if needed

    # Data client config
    data_config = BinanceDataClientConfig(
        api_key=api_key,
        api_secret=api_secret,
        account_type=BinanceAccountType.USDT_FUTURES,  # Binance Futures
        environment=BinanceEnvironment.LIVE,  # v1.223.0+: replaces deprecated testnet=False
        instrument_provider=InstrumentProviderConfig(
            load_all=True,  # Load all instruments for proper execution
        ),
    )

    # Execution client config
    exec_config = BinanceExecClientConfig(
        api_key=api_key,
        api_secret=api_secret,
        account_type=BinanceAccountType.USDT_FUTURES,
        environment=BinanceEnvironment.LIVE,  # v1.223.0+: replaces deprecated testnet=False
        recv_window_ms=recv_window_ms,
        instrument_provider=InstrumentProviderConfig(
            load_all=True,  # Load all instruments for proper execution
        ),
    )

    return data_config, exec_config


def get_srp_strategy_config(config_manager: ConfigManager) -> 'SRPStrategyConfig':
    """
    Build SRP strategy configuration from ConfigManager.

    SRP parameters are read from configs/base.yaml under the 'srp' key,
    with sensible defaults for BTC futures.
    """
    from srp_strategy.srp_strategy import SRPStrategyConfig

    instrument_id = config_manager.get('trading', 'instrument_id', default='BTCUSDT-PERP.BINANCE')
    timeframe = config_manager.get('srp', 'timeframe', default='30m')

    timeframe_to_bar_spec = {
        '1m': '1-MINUTE-LAST', '5m': '5-MINUTE-LAST', '15m': '15-MINUTE-LAST',
        '30m': '30-MINUTE-LAST', '1h': '1-HOUR-LAST', '4h': '4-HOUR-LAST', '1d': '1-DAY-LAST',
    }
    bar_spec = timeframe_to_bar_spec.get(timeframe, '30-MINUTE-LAST')
    symbol = instrument_id.split('.')[0]
    bar_type = f"{symbol}.BINANCE-{bar_spec}-EXTERNAL"

    return SRPStrategyConfig(
        instrument_id=instrument_id,
        bar_type=bar_type,

        # SRP core (Pine v5.0 defaults)
        vwma_length=config_manager.get('srp', 'vwma_length', default=14),
        srp_pct=config_manager.get('srp', 'srp_pct', default=1.0),
        rsi_mfi_below=config_manager.get('srp', 'rsi_mfi_below', default=55.0),
        rsi_mfi_above=config_manager.get('srp', 'rsi_mfi_above', default=100.0),
        rsi_mfi_period=config_manager.get('srp', 'rsi_mfi_period', default=7),

        # Position sizing
        sizing_mode=config_manager.get('srp', 'sizing_mode', default='percent'),
        base_order_pct=config_manager.get('srp', 'base_order_pct', default=10.0),
        base_order_usdt=config_manager.get('srp', 'base_order_usdt', default=100.0),

        # DCA (Pine v5.0)
        dca_multiplier=config_manager.get('srp', 'dca_multiplier', default=1.5),
        max_dca_count=config_manager.get('srp', 'max_dca_count', default=4),
        dca_min_change_pct=config_manager.get('srp', 'dca_min_change_pct', default=3.0),
        dca_type=config_manager.get('srp', 'dca_type', default='volume_multiply'),

        # Exit
        mintp=config_manager.get('srp', 'mintp', default=0.025),
        max_portfolio_loss_pct=config_manager.get('srp', 'max_portfolio_loss_pct', default=0.06),

        # Telegram
        enable_telegram=config_manager.get('telegram', 'enabled', default=False),
        telegram_bot_token=get_env_str('TELEGRAM_BOT_TOKEN', ''),
        telegram_chat_id=get_env_str('TELEGRAM_CHAT_ID', ''),

        # Timing
        timer_interval_sec=config_manager.get('timing', 'timer_interval_sec', default=1200),
    )


def setup_trading_node(config_manager: ConfigManager, strategy_type: str = 'ai') -> TradingNodeConfig:
    """
    Configure the NautilusTrader trading node.

    Parameters
    ----------
    config_manager : ConfigManager
        Configuration manager instance
    strategy_type : str
        'ai' for mechanical anticipatory strategy, 'srp' for SRP mean reversion DCA

    Returns
    -------
    TradingNodeConfig
        Trading node configuration
    """
    data_config, exec_config = get_binance_config(config_manager, strategy_type=strategy_type)

    if strategy_type == 'srp':
        # SRP Strategy
        strategy_config = get_srp_strategy_config(config_manager)
        importable_config = ImportableStrategyConfig(
            strategy_path="srp_strategy.srp_strategy:SRPStrategy",
            config_path="srp_strategy.srp_strategy:SRPStrategyConfig",
            config=strategy_config.dict(),
        )
        trader_id = TraderId("SRPTrader-001")
        log_file_name = "srp_trader"
    else:
        # Default: AI Strategy
        strategy_config = get_strategy_config(config_manager)
        importable_config = ImportableStrategyConfig(
            strategy_path="strategy.ai_strategy:AITradingStrategy",
            config_path="strategy.ai_strategy:AITradingStrategyConfig",
            config=strategy_config.dict(),
        )
        trader_id = TraderId("DeepSeekTrader-001")
        log_file_name = "deepseek_trader"

    # Logging configuration (from ConfigManager, environment-specific via {env}.yaml)
    log_level = config_manager.get('logging', 'level', default='INFO')

    # LoggingConfig - only use parameters supported by NautilusTrader 1.202.0
    logging_config = LoggingConfig(
        log_level=log_level,
        log_level_file=log_level,
        log_directory="logs",
        log_file_name=log_file_name,
        bypass_logging=False,
    )

    # Execution engine configuration (from ConfigManager, not hardcoded)
    exec_reconciliation = config_manager.get('execution', 'engine', 'reconciliation', default=True)
    exec_inflight_check_ms = config_manager.get('execution', 'engine', 'inflight_check_interval_ms', default=5000)
    exec_filter_position_reports = config_manager.get('execution', 'engine', 'filter_position_reports', default=True)
    exec_filter_unclaimed_orders = config_manager.get('execution', 'engine', 'filter_unclaimed_external_orders', default=True)

    # Trading node config
    config = TradingNodeConfig(
        trader_id=trader_id,
        logging=logging_config,
        exec_engine=LiveExecEngineConfig(
            reconciliation=exec_reconciliation,
            inflight_check_interval_ms=exec_inflight_check_ms,
            filter_position_reports=exec_filter_position_reports,
            filter_unclaimed_external_orders=exec_filter_unclaimed_orders,
        ),
        # Data clients
        data_clients={
            "BINANCE": data_config,
        },
        # Execution clients
        exec_clients={
            "BINANCE": exec_config,
        },
        # Strategy configs
        strategies=[importable_config],
    )

    return config


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description='AlgVex - NautilusTrader DeepSeek Bot')
    parser.add_argument(
        '--env',
        type=str,
        default='production',
        choices=['production', 'development', 'backtest'],
        help='Trading environment (default: production)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Dry run mode (load config but don\'t start trading)'
    )
    parser.add_argument(
        '--strategy',
        type=str,
        default='ai',
        choices=['ai', 'srp'],
        help='Strategy to run: ai (default, mechanical anticipatory) or srp (SRP mean reversion DCA)'
    )
    parser.add_argument(
        '--mode',
        type=str,
        default=None,
        choices=['ai', 'mechanical'],
        help='Strategy mode override: ai (default, DeepSeek agents) or mechanical (anticipatory scoring, no AI API calls)'
    )
    return parser.parse_args()


def _send_shutdown_telegram(config_manager):
    """
    Fallback shutdown notification via direct HTTP call to Telegram API.

    This runs in the finally block of main() to guarantee the user is notified
    even if NautilusTrader's on_stop() was never called (e.g., SIGTERM killed
    the event loop before strategy cleanup).
    """
    try:
        from datetime import datetime

        enabled = config_manager.get('telegram', 'enabled', default=False)
        token = os.getenv('TELEGRAM_BOT_TOKEN', '')
        chat_id = os.getenv('TELEGRAM_CHAT_ID', '')

        if not enabled or not token or not chat_id:
            return

        msg = (
            "🛑 *Service Stopped*\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "📋 Process exiting\n"
            f"\n⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={'chat_id': chat_id, 'text': msg, 'parse_mode': 'Markdown'},
            timeout=10,
        )
        print("📱 Sent shutdown notification to Telegram")
    except Exception as e:
        print(f"⚠️  Failed to send shutdown notification: {e}")


def main():
    """
    Main entry point for live trading.
    """
    # Parse command-line arguments
    args = parse_args()

    strategy_name = "SRP Mean Reversion DCA" if args.strategy == 'srp' else "Mechanical Anticipatory"
    # v10.0: --mode override for mechanical anticipatory trading
    strategy_mode = args.mode  # None = use config default
    if args.strategy == 'srp' and strategy_mode == 'mechanical':
        print("⚠️ --mode mechanical is only for AI strategy, ignoring for SRP")
        strategy_mode = None
    mode_label = f" [{strategy_mode}]" if strategy_mode else ""
    print("=" * 70)
    print(f"AlgVex Trading - {strategy_name}{mode_label} - Live Trading Mode")
    print("=" * 70)
    print(f"Environment: {args.env}")
    print(f"Exchange: Binance Futures (USDT-M)")
    print(f"Strategy: {strategy_name}{mode_label}")
    print("=" * 70)

    # Initialize ConfigManager
    print("\n📋 Loading configuration...")
    config_manager = ConfigManager(env=args.env)
    config_dict = config_manager.load()

    # v10.0: Apply mode override to config
    if strategy_mode:
        config_manager._config['strategy_mode'] = strategy_mode

    # Validate configuration
    if not config_manager.validate():
        print("\n❌ Configuration validation failed:")
        errors = config_manager.get_errors()
        for error in errors:
            print(f"  - {error.field}: {error.message}")
        sys.exit(1)

    print(f"✅ Configuration loaded and validated ({args.env} environment)")

    # Dry run mode - print config summary and exit
    if args.dry_run:
        print("\n" + "=" * 70)
        print("DRY RUN MODE - Configuration Summary")
        print("=" * 70)
        config_manager.print_summary()
        print("\n✅ Dry run complete. Configuration is valid. Exiting.")
        return

    # Get instrument from config
    instrument_id = config_manager.get('trading', 'instrument_id', default='BTCUSDT-PERP.BINANCE')
    print(f"Instrument: {instrument_id}")

    # Safety check
    test_mode = os.getenv('TEST_MODE', 'false').strip().lower() == 'true'
    auto_confirm = os.getenv('AUTO_CONFIRM', 'false').strip().lower() == 'true'

    if test_mode:
        print("⚠️  TEST_MODE=true - This is a simulation, no real orders will be placed")
    else:
        print("🚨 LIVE TRADING MODE - Real orders will be placed!")
        if auto_confirm:
            print("⚠️  AUTO_CONFIRM=true - Skipping user confirmation")
        else:
            response = input("Are you sure you want to continue? (yes/no): ")
            if response.lower() != 'yes':
                print("Exiting...")
                return

    # v6.3: Pre-flight checks — catch common PANIC triggers before Rust starts
    print("\n🔍 Pre-flight checks...")
    _preflight_ok = True

    # Check 1: Verify patches loaded successfully
    from patches.binance_enums import apply_all_patches as _verify_patches
    try:
        from nautilus_trader.adapters.binance.common.enums import BinanceSymbolFilterType
        if not hasattr(BinanceSymbolFilterType, '_nautilus_patched'):
            print("  ⚠️ Enum patch NOT applied — re-applying...")
            _verify_patches()
        else:
            print("  ✅ Enum patch active")
    except Exception as e:
        print(f"  🔴 Enum patch check failed: {e}")
        _preflight_ok = False

    # Check 2: Verify aiohttp patch (non-ASCII symbols)
    try:
        from patches.binance_positions import _position_patch_applied
        if _position_patch_applied:
            print("  ✅ Non-ASCII symbol filter active")
        else:
            print("  ⚠️ Non-ASCII filter NOT applied — re-applying...")
            from patches.binance_positions import apply_http_response_filter
            if apply_http_response_filter():
                print("  ✅ Non-ASCII symbol filter applied on retry")
            else:
                print("  🔴 Non-ASCII filter failed — risk of Rust PANIC on 币安人生USDT")
    except Exception as e:
        print(f"  🔴 Non-ASCII filter check failed: {e}")

    # Check 3: Verify instrument_id and bar_type format won't cause parse PANIC
    try:
        from nautilus_trader.model.identifiers import InstrumentId as _IID
        test_iid = _IID.from_str(instrument_id)
        print(f"  ✅ InstrumentId parse OK: {test_iid}")
    except Exception as e:
        print(f"  🔴 InstrumentId parse FAIL: {e}")
        print(f"     → This WILL cause a Rust PANIC at startup!")
        _preflight_ok = False

    # Check 4: Verify Binance API reachability
    try:
        resp = requests.get("https://fapi.binance.com/fapi/v1/ping", timeout=5)
        if resp.status_code == 200:
            print("  ✅ Binance Futures API reachable")
        else:
            print(f"  ⚠️ Binance API returned HTTP {resp.status_code}")
    except Exception as e:
        print(f"  ⚠️ Binance API unreachable: {e}")

    if not _preflight_ok:
        print("\n🔴 Pre-flight checks FAILED — aborting to prevent Rust PANIC")
        print("   Fix the above issues and restart the service.")
        sys.exit(1)

    print("✅ All pre-flight checks passed\n")

    # Build configuration
    print("📋 Building trading node configuration...")
    config = setup_trading_node(config_manager, strategy_type=args.strategy)

    print(f"✅ Trader ID: {config.trader_id}")
    print(f"✅ Strategy: {strategy_name}")
    print(f"✅ Binance Futures adapter configured")

    # Create and start trading node
    print("\n🚀 Starting trading node...")
    node = TradingNode(config=config)
    
    # Register Binance factories
    node.add_data_client_factory("BINANCE", BinanceLiveDataClientFactory)
    node.add_exec_client_factory("BINANCE", BinanceLiveExecClientFactory)
    print("✅ Binance factories registered")

    # Register SIGTERM handler for systemctl stop graceful shutdown
    # Converts SIGTERM to KeyboardInterrupt so NautilusTrader's on_stop() is called
    def _sigterm_handler(signum, frame):
        print("\n⚠️  SIGTERM received (systemctl stop)...")
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _sigterm_handler)

    try:
        # Build the node (connects to exchange, loads instruments)
        node.build()
        print("✅ Trading node built successfully")

        # Run the node (this starts strategies and begins event processing)
        print("✅ Starting trading node...")
        print("\n🟢 Strategy is now running. Press Ctrl+C to stop.\n")
        
        # Run the node - this is a blocking call that processes all events
        node.run()

    except KeyboardInterrupt:
        print("\n\n⚠️  Keyboard interrupt received...")

    except Exception as e:
        error_msg = str(e)
        print(f"\n❌ Error occurred: {error_msg}")
        traceback.print_exc()

        # v6.3: Detect Rust PANIC patterns for better diagnosis
        if "panic" in error_msg.lower() or "pyo3" in error_msg.lower():
            print("\n" + "=" * 70)
            print("🔴 RUST PANIC DETECTED — 常见原因:")
            print("=" * 70)
            if "interpreter" in error_msg.lower() and "not initialized" in error_msg.lower():
                print("  → PyO3 interpreter lifecycle PANIC")
                print("  → tokio-runtime-worker 在 Python 关闭后尝试调用 Python APIs")
                print("  → v6.5 已通过 os._exit() 跳过 CPython finalization 修复")
                print("  → 如仍出现, 可能是运行中崩溃 (非 shutdown), 检查 PANIC 前的日志")
            elif "ascii" in error_msg.lower() or "decode" in error_msg.lower():
                print("  → 非 ASCII 符号 (币安人生USDT)")
                print("  → 修复: 确认 patches/binance_positions.py 正确加载")
            elif "enum" in error_msg.lower() or "variant" in error_msg.lower():
                print("  → Binance 新枚举值 (如 POSITION_RISK_CONTROL)")
                print("  → 修复: 确认 patches/binance_enums.py 在 NT 导入前加载")
            elif "thread" in error_msg.lower() or "send" in error_msg.lower():
                print("  → Rust 指标跨线程访问")
                print("  → 修复: 检查 Telegram 线程是否访问了 indicator_manager")
            elif "instrument" in error_msg.lower() or "cache" in error_msg.lower():
                print("  → 合约加载失败")
                print("  → 修复: 检查 Binance API 连接和合约名称")
            else:
                print(f"  → 未知 PANIC 类型: {error_msg}")
            print("=" * 70)

    finally:
        # Dispose the node to clean up resources
        print("\n🛑 Cleaning up resources...")
        try:
            node.dispose()
            print("✅ Resources cleaned up")
        except Exception as dispose_err:
            print(f"⚠️ Cleanup error (non-fatal): {dispose_err}")

        # v6.5: 给 tokio 工作线程时间排空 pending 任务
        # node.dispose() 通知 tokio 关闭, 但异步任务需要时间完成
        # 最终由 os._exit() 彻底终止 (跳过 CPython finalization)
        print("⏳ Waiting for Rust async runtime drain (5s)...")
        time.sleep(5)

        # Fallback shutdown notification via direct Telegram API
        # Strategy's on_stop() may not be called if SIGTERM kills the event loop
        _send_shutdown_telegram(config_manager)

        print("\n" + "=" * 70)
        print("Trading session ended")
        print("=" * 70)


if __name__ == "__main__":
    # Run the trading bot (Python path already configured at module level)
    _exit_code = 0
    try:
        main()
    except KeyboardInterrupt:
        print("\n✅ Program terminated by user")
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        traceback.print_exc()
        _exit_code = 1
    finally:
        # v6.5: PyO3 interpreter lifecycle PANIC 彻底修复
        #
        # 根因: CPython 正常退出时经历 interpreter finalization 阶段
        # (模块清理 → 全局对象释放 → interpreter shutdown)。此时 NautilusTrader
        # 的 tokio-runtime-worker 线程仍在运行，尝试回调 Python API →
        # PyO3 0.27.2 的 assertion 触发 → SIGABRT。
        #
        # v6.3/v6.4 的 sleep(5s) 只延迟了退出，但 CPython finalization
        # 仍然会触发 PANIC (日志证实: "Trading session ended" 之后才 PANIC)。
        #
        # 修复: 使用 os._exit() 跳过 CPython finalization，直接终止进程。
        # 所有 cleanup 已在 main() 的 finally 块中完成:
        #   1. node.dispose() — NautilusTrader 资源清理
        #   2. sleep(5s) — tokio runtime 排空时间
        #   3. Telegram 通知 — 已发送
        #   4. on_stop() — SL/TP 保留在 Binance
        #
        # os._exit() 不经过 interpreter finalization, tokio 线程随进程消亡,
        # 永远不会遇到 "interpreter not initialized" 状态。
        #
        # 参考: NautilusTrader #3027, PyO3 #5317
        print(f"\n🔚 Process exit (code={_exit_code})")
        os._exit(_exit_code)
