"""
Shared Trading Logic Module

This module contains core trading logic functions that are used by both:
- ai_strategy.py (live trading)
- diagnose_realtime.py (diagnostic tool)

This ensures 100% consistency between diagnostic and live trading behavior.

Functions:
- calculate_mechanical_sltp() — v11.0-simple: ATR-based mechanical SL/TP (Lopez de Prado Triple Barrier)
- calculate_position_size() — 仓位计算 (含 appetite_scale + 2% single-trade risk clamp)
- validate_multiagent_sltp() — SL/TP 方向 + R/R 验证 (safety net)
"""

import math
from typing import Dict, Any, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


# =============================================================================
# CONFIGURATION LOADING (Phase 3: ConfigManager Integration)
# =============================================================================

# Module-level configuration cache (lazy-loaded to avoid circular imports)
_TRADING_LOGIC_CONFIG = None


def _get_trading_logic_config() -> Dict[str, Any]:
    """
    从 ConfigManager 加载交易逻辑配置 (lazy-loaded)

    Returns
    -------
    Dict[str, Any]
        交易逻辑配置字典，包含所有 SL/TP 参数和 Binance 交易限制

    Notes
    -----
    使用延迟导入避免循环依赖:
    - config_manager → strategy → utils (正常)
    - 不触发: trading_logic (模块级) → config_manager (循环)

    Reference: CLAUDE.md - 配置分层架构原则
    """
    global _TRADING_LOGIC_CONFIG
    if _TRADING_LOGIC_CONFIG is None:
        # Lazy import to avoid circular dependency
        from utils.config_manager import get_config
        config = get_config()

        _TRADING_LOGIC_CONFIG = {
            # SL/TP 参数
            # v6.3: ATR-primary min SL distance (自适应波动率)
            'min_sl_distance_atr': config.get('trading_logic', 'min_sl_distance_atr', default=2.0),
            'min_sl_distance_pct': config.get('trading_logic', 'min_sl_distance_pct', default=0.003),
            'min_tp_distance_pct': config.get('trading_logic', 'min_tp_distance_pct', default=0.005),
            'default_sl_pct': config.get('trading_logic', 'default_sl_pct', default=0.02),
            'default_tp_pct': config.get('trading_logic', 'default_tp_pct', default=0.03),
            'min_rr_ratio': config.get('trading_logic', 'min_rr_ratio', default=1.3),
            'counter_trend_rr_multiplier': config.get('trading_logic', 'counter_trend_rr_multiplier', default=1.3),
            'tp_pct_by_confidence': config.get('trading_logic', 'tp_pct_by_confidence', default={
                'high': 0.03,
                'medium': 0.02,
                'low': 0.01,
            }),
            # Binance 交易限制 (从配置读取，禁止硬编码)
            'min_notional_usdt': config.get('trading_logic', 'min_notional_usdt', default=100.0),
            'min_notional_safety_margin': config.get('trading_logic', 'min_notional_safety_margin', default=1.01),
            'quantity_adjustment_step': config.get('trading_logic', 'quantity_adjustment_step', default=0.001),
            # v15.0: Emergency SL 配置 (原硬编码值提取)
            'emergency_sl': config.get('trading_logic', 'emergency_sl', default={
                'base_pct': 0.02,
                'atr_multiplier': 1.5,
                'cooldown_seconds': 120,
                'max_consecutive': 3,
            }),
            # v11.0-simple: Mechanical SL/TP 配置 (ATR-based, Lopez de Prado Triple Barrier)
            'mechanical_sltp': config.get('trading_logic', 'mechanical_sltp', default={
                'enabled': True,
                'sl_atr_multiplier': {'HIGH': 0.8, 'MEDIUM': 1.0, 'LOW': 1.0},
                'tp_rr_target': {'HIGH': 1.5, 'MEDIUM': 1.5, 'LOW': 1.5},
                'sl_atr_multiplier_floor': 0.5,
                'counter_trend_sl_tighten': 1.0,
            }),
            # v11.0-simple: Time Barrier 配置
            'time_barrier': config.get('trading_logic', 'time_barrier', default={
                'enabled': True,
                'max_holding_hours_trend': 12,
                'max_holding_hours_counter': 6,
                'action': 'close',
            }),
        }

    return _TRADING_LOGIC_CONFIG


# Public accessor functions (used by agents/multi_agent_analyzer.py)
def get_min_sl_distance_pct() -> float:
    """获取最小止损距离 PCT floor (v6.3: ATR=0 时使用)"""
    return _get_trading_logic_config()['min_sl_distance_pct']


def get_min_sl_distance(atr_value: float = 0.0, entry_price: float = 0.0) -> float:
    """
    v6.3: ATR-based minimum SL distance (as fraction of price).

    Returns max(min_sl_distance_atr × ATR / price, min_sl_distance_pct).
    Core principle: use ATR to measure noise, not fixed percentages.

    Parameters
    ----------
    atr_value : float
        Current ATR value. 0 = use PCT floor only.
    entry_price : float
        Entry/current price. 0 = use PCT floor only.

    Returns
    -------
    float
        Minimum SL distance as fraction of price (e.g. 0.01 = 1%).
    """
    cfg = _get_trading_logic_config()
    atr_mult = cfg.get('min_sl_distance_atr', 2.0)
    pct_floor = cfg['min_sl_distance_pct']
    if atr_value > 0 and entry_price > 0:
        return max(atr_mult * atr_value / entry_price, pct_floor)
    return pct_floor


def get_min_tp_distance_pct() -> float:
    """获取最小止盈距离百分比"""
    return _get_trading_logic_config()['min_tp_distance_pct']


def get_min_rr_ratio() -> float:
    """获取最小风险收益比 (R/R)"""
    return _get_trading_logic_config()['min_rr_ratio']


def get_counter_trend_rr_multiplier() -> float:
    """v5.12: 获取逆势交易 R/R 门槛倍数"""
    return _get_trading_logic_config().get('counter_trend_rr_multiplier', 1.3)


def _is_counter_trend(is_long: bool, trend_info: Dict[str, Any]) -> bool:
    """
    v5.12: 检测交易是否逆势 (基于 MTF 趋势层或 ADX 方向).

    v6.7: 数据完全缺失时返回 True (保守路径 → 要求更高 R/R)。
    只在有明确趋势时 (ADX >= 25) 才判定顺势/逆势。
    ADX < 25 (无明确趋势) 返回 False，退化为标准 R/R 门槛。

    Parameters
    ----------
    is_long : bool
        True if the trade is LONG/BUY
    trend_info : Dict
        Technical data containing trend_direction, adx, di_plus, di_minus, etc.

    Returns
    -------
    bool
        True if the trade is counter-trend (or data absent → conservative)
    """
    # v6.7: If trend_info is empty/None, data is genuinely absent → conservative path
    if not trend_info:
        return True

    # Method 1: Use trend_direction from historical context / indicator manager
    trend_direction = trend_info.get('trend_direction', '').upper()
    adx = trend_info.get('adx')

    # v6.7: ADX is None/missing (not just low) → data absent → conservative
    if adx is None:
        return True

    adx = float(adx) if adx else 0

    # ADX < 25 = no clear trend → standard R/R gate (not counter-trend)
    if adx < 25:
        return False

    if trend_direction in ('UPTREND', 'BULLISH') and not is_long:
        return True  # Shorting in uptrend = counter-trend
    if trend_direction in ('DOWNTREND', 'BEARISH') and is_long:
        return True  # Longing in downtrend = counter-trend

    # Method 2: Fall back to DI+/DI- direction (if trend_direction not available)
    if not trend_direction or trend_direction in ('NEUTRAL', 'SIDEWAYS', ''):
        di_plus = trend_info.get('di_plus', 0) or 0
        di_minus = trend_info.get('di_minus', 0) or 0
        if di_plus > di_minus and not is_long:
            return True
        if di_minus > di_plus and is_long:
            return True

    return False


def get_default_sl_pct() -> float:
    """获取默认止损百分比"""
    return _get_trading_logic_config()['default_sl_pct']


def get_default_tp_pct_buy() -> float:
    """获取买入默认止盈百分比"""
    return _get_trading_logic_config()['default_tp_pct']


def get_default_tp_pct_sell() -> float:
    """获取卖出默认止盈百分比"""
    return _get_trading_logic_config()['default_tp_pct']


def get_tp_pct_by_confidence(confidence: str) -> float:
    """
    根据信心级别获取止盈百分比

    Parameters
    ----------
    confidence : str
        信心级别 ('HIGH', 'MEDIUM', 'LOW')

    Returns
    -------
    float
        对应的止盈百分比
    """
    tp_config = _get_trading_logic_config()['tp_pct_by_confidence']
    return tp_config.get(confidence.lower(), tp_config['medium'])


def get_min_notional_usdt() -> float:
    """获取 Binance 最低名义价值 (USDT)"""
    return _get_trading_logic_config()['min_notional_usdt']


def get_min_notional_safety_margin() -> float:
    """获取最低名义价值安全边际"""
    return _get_trading_logic_config()['min_notional_safety_margin']


def get_quantity_adjustment_step() -> float:
    """获取仓位调整步长"""
    return _get_trading_logic_config()['quantity_adjustment_step']


# =============================================================================
# v11.0: MECHANICAL SL/TP (Lopez de Prado Triple Barrier Style)
# =============================================================================


def calculate_mechanical_sltp(
    entry_price: float,
    side: str,
    atr_value: float,
    confidence: str = "MEDIUM",
    risk_appetite: str = "NORMAL",
    is_counter_trend: bool = False,
    atr_4h: float = 0.0,
) -> Tuple[bool, float, float, str]:
    """
    v11.0-simple: Pure mechanical ATR-based SL/TP calculation.
    v39.0: Uses 4H ATR as primary SL/TP basis (matches decision timeframe).
           30M ATR as fallback when 4H unavailable.

    Based on Lopez de Prado (2018) Triple Barrier Method:
    - SL = entry ± (ATR × sl_multiplier)  — confidence 唯一决定
    - TP = entry ∓ (SL_distance × rr_target) — confidence 唯一决定
    - R/R is determined by tp_rr_target per confidence (v44.0: HIGH/MED/LOW=1.5)
    - risk_appetite 不影响 SL/TP，仅作用于仓位大小 (正交设计)

    Parameters
    ----------
    entry_price : float
        Entry price for the trade.
    side : str
        Trade side ('BUY', 'SELL', 'LONG', or 'SHORT').
    atr_value : float
        Current 30M ATR(14) value. Used as fallback when atr_4h unavailable.
    confidence : str
        AI confidence level ('HIGH', 'MEDIUM', 'LOW').
        Maps to SL ATR multiplier and TP R/R target.
        v38.1: LOW re-enabled (30% position, same SL/TP as MEDIUM, data accumulation).
    risk_appetite : str
        AI risk appetite — NOT used for SL/TP, only logged.
    is_counter_trend : bool
        Whether the trade is against the dominant trend.
        Counter-trend trades require higher R/R (×1.3), SL width unchanged.
    atr_4h : float
        v39.0: 4H ATR(14) value. Primary ATR source for SL/TP calculation.
        When > 0, used instead of atr_value (30M). Matches decision timeframe.

    Returns
    -------
    Tuple[bool, float, float, str]
        (success, sl_price, tp_price, method_description)
        success=False when ATR is invalid or price is zero.
    """
    logger = logging.getLogger(__name__)

    # v39.0: Allow atr_value=0 when atr_4h is available
    # NaN guard: float NaN passes `<= 0` as False, so check explicitly
    if entry_price <= 0 or math.isnan(entry_price):
        return False, 0.0, 0.0, f"Invalid inputs: price={entry_price}"
    _atr_30m = atr_value if atr_value and not math.isnan(atr_value) else 0
    _atr_4h = atr_4h if atr_4h and not math.isnan(atr_4h) else 0
    if _atr_30m <= 0 and _atr_4h <= 0:
        return False, 0.0, 0.0, f"No valid ATR: 4h={atr_4h}, 30m={atr_value}"

    cfg = _get_trading_logic_config()
    mech_cfg = cfg.get('mechanical_sltp', {})

    if not mech_cfg.get('enabled', True):
        return False, 0.0, 0.0, "Mechanical SL/TP disabled in config"

    is_long = side.upper() in ('BUY', 'LONG')

    # Step 1: Get SL multiplier from confidence level (唯一决定因素)
    sl_multipliers = mech_cfg.get('sl_atr_multiplier', {'HIGH': 0.8, 'MEDIUM': 1.0, 'LOW': 1.0})
    conf_key = confidence.upper() if confidence else 'MEDIUM'
    final_sl_mult = sl_multipliers.get(conf_key, sl_multipliers.get('MEDIUM', 1.0))

    # Step 2: Floor — never below sl_atr_multiplier_floor (noise protection)
    sl_floor = mech_cfg.get('sl_atr_multiplier_floor', 0.5)
    final_sl_mult = max(final_sl_mult, sl_floor)

    # Step 3: Calculate SL distance
    # v39.0: Use 4H ATR as primary (matches decision timeframe).
    # 30M ATR as fallback when 4H unavailable (startup, insufficient data).
    effective_atr = _atr_4h if _atr_4h > 0 else _atr_30m
    if effective_atr <= 0:
        return False, 0.0, 0.0, f"No valid ATR: 4h={atr_4h}, 30m={atr_value}"
    sl_distance = effective_atr * final_sl_mult

    # Step 4: Get R/R target from confidence level
    rr_targets = mech_cfg.get('tp_rr_target', {'HIGH': 1.5, 'MEDIUM': 1.5, 'LOW': 1.5})
    rr_target = rr_targets.get(conf_key, rr_targets.get('MEDIUM', 1.5))

    # Step 5: Counter-trend R/R escalation (SL width unchanged, only R/R raised)
    effective_rr = rr_target
    ct_note = ""
    if is_counter_trend:
        ct_rr_mult = cfg.get('counter_trend_rr_multiplier', 1.3)
        min_rr = cfg.get('min_rr_ratio', 1.3)
        min_ct_rr = min_rr * ct_rr_mult
        effective_rr = max(rr_target, min_ct_rr)
        ct_note = f" CT(rr≥{min_ct_rr:.2f})"

    # Step 6: Calculate TP distance
    tp_distance = sl_distance * effective_rr

    # Step 7: Compute prices
    if is_long:
        sl_price = entry_price - sl_distance
        tp_price = entry_price + tp_distance
    else:
        sl_price = entry_price + sl_distance
        tp_price = entry_price - tp_distance

    # Safety: ensure SL and TP are positive
    if sl_price <= 0 or tp_price <= 0:
        return False, 0.0, 0.0, f"Computed SL/TP non-positive: SL={sl_price:.2f}, TP={tp_price:.2f}"

    # Build method description
    atr_source = "4H" if (atr_4h or 0) > 0 else "30M"
    method = (
        f"mechanical|conf={conf_key}|atr_src={atr_source}"
        f"|sl_mult={final_sl_mult:.2f}{ct_note}"
        f"|rr={effective_rr:.2f}"
        f"|sl_dist=${sl_distance:,.2f}|tp_dist=${tp_distance:,.2f}"
    )
    logger.info(
        f"🔧 Mechanical SL/TP: {side} @ ${entry_price:,.2f} | "
        f"ATR({atr_source})=${effective_atr:,.2f} | "
        f"SL=${sl_price:,.2f} ({final_sl_mult:.1f}×ATR) | "
        f"TP=${tp_price:,.2f} (R/R {effective_rr:.2f}:1)"
    )

    return True, sl_price, tp_price, method


def calculate_dca_sltp(
    real_avg_price: float,
    virtual_avg_price: float,
    side: str,
    tp_pct: float = 0.025,
    sl_pct: float = 0.06,
) -> Tuple[bool, float, float, str]:
    """
    v48.0: Fixed percentage SL/TP for DCA mode (SRP-inspired).

    Both TP and SL anchored on real_avg_price (actual cost basis).
    DCA improves avg through real fills — virtual_avg is informational only.
    """
    if real_avg_price <= 0:
        return False, 0.0, 0.0, "Invalid average price"

    is_long = side.upper() in ("LONG", "BUY")
    if is_long:
        tp_price = real_avg_price * (1 + tp_pct)
        sl_price = real_avg_price * (1 - sl_pct)
    else:
        tp_price = real_avg_price * (1 - tp_pct)
        sl_price = real_avg_price * (1 + sl_pct)

    if sl_price <= 0 or tp_price <= 0:
        return False, 0.0, 0.0, "Computed SL/TP non-positive"

    method = (
        f"DCA fixed-%|side={side}|real_avg=${real_avg_price:,.0f}"
        f"|tp_pct={tp_pct:.1%}|sl_pct={sl_pct:.1%}"
        f"|SL=${sl_price:,.0f}|TP=${tp_price:,.0f}"
    )
    logger.info(
        f"🔧 DCA SL/TP: {side} | avg=${real_avg_price:,.2f} "
        f"| SL=${sl_price:,.2f} ({sl_pct:.0%}) | TP=${tp_price:,.2f} ({tp_pct:.0%})"
    )

    return True, sl_price, tp_price, method


def get_time_barrier_config() -> Dict[str, Any]:
    """v11.0-simple: 获取 Time Barrier 配置"""
    return _get_trading_logic_config().get('time_barrier', {})




# =============================================================================
# LOGIC CONSTANTS (不可配置 - 这些是业务逻辑规则)
# =============================================================================



def calculate_position_size(
    signal_data: Dict[str, Any],
    price_data: Dict[str, Any],
    technical_data: Dict[str, Any],
    config: Dict[str, Any],
    logger: Optional[logging.Logger] = None,
    risk_multiplier: float = 1.0,
    atr_4h: float = 0.0,
) -> Tuple[float, Dict[str, Any]]:
    """
    Calculate intelligent position size.

    v3.12: Supports multiple sizing methods:
    - fixed_pct: Original confidence-based sizing
    - atr_based: ATR-based risk-adjusted sizing
    - ai_controlled: AI specifies position_size_pct directly

    Parameters
    ----------
    signal_data : Dict
        AI-generated signal with 'confidence', 'position_size_pct' (v3.12)
    price_data : Dict
        Current price data with 'price'
    technical_data : Dict
        Technical indicators with 'overall_trend', 'rsi', 'atr' (v3.12)
    config : Dict
        Configuration with keys:
        - base_usdt: Base USDT amount
        - equity: Total equity
        - position_sizing.method: 'fixed_pct' | 'atr_based' | 'ai_controlled'
        - position_sizing.atr_based.*: ATR sizing parameters
        - max_position_ratio
        - min_trade_amount
    logger : Logger, optional
        Logger for output messages
    risk_multiplier : float, optional
        Multiplier from RiskController (0.0-1.0), default 1.0

    Returns
    -------
    Tuple[float, Dict]
        (btc_quantity, calculation_details)
    """
    current_price = price_data.get('price', 0)
    if current_price <= 0:
        if logger:
            logger.error("Invalid price for position sizing")
        return 0.0, {'error': 'Invalid price'}

    equity = config.get('equity', 1000)
    leverage = config.get('leverage', 5)
    max_position_ratio = config.get('max_position_ratio', 0.12)
    # v4.8: max_usdt 现在包含杠杆
    # 例: $1000 × 30% × 10杠杆 = $3000 最大仓位
    max_usdt = equity * max_position_ratio * leverage

    # v3.12: Determine sizing method
    sizing_config = config.get('position_sizing', {})
    method = sizing_config.get('method', 'fixed_pct')

    # v3.13: Get AI position_size_pct (used by hybrid and ai_controlled)
    ai_size_pct = signal_data.get('position_size_pct')

    # v3.12 legacy: Override to ai_controlled if AI provides size and method is not hybrid
    if ai_size_pct is not None and ai_size_pct >= 0 and method not in ('hybrid_atr_ai',):
        method = 'ai_controlled'

    if method == 'hybrid_atr_ai':
        # v3.13: Hybrid ATR-AI Position Sizing
        # 公式: 最终仓位 = ATR仓位 × AI调节系数
        # AI调节系数 = min_mult + ai_weight × (AI_pct / 100), 范围 [min, max]
        atr_config = sizing_config.get('atr_based', {})
        hybrid_config = sizing_config.get('hybrid_atr_ai', {})

        risk_per_trade = atr_config.get('risk_per_trade_pct', 0.01)
        atr_multiplier = atr_config.get('atr_multiplier', 2.0)

        min_mult = hybrid_config.get('min_multiplier', 0.3)
        max_mult = hybrid_config.get('max_multiplier', 1.0)
        ai_weight = hybrid_config.get('ai_weight', 0.7)
        fallback_to_atr = hybrid_config.get('fallback_to_atr', True)

        # Step 1: Calculate ATR-based position (risk ceiling)
        atr = technical_data.get('atr', 0)
        if atr <= 0:
            atr = current_price * 0.02  # Fallback: 2% of price
            if logger:
                logger.warning(f"ATR not available, using estimate: ${atr:.2f}")

        dollar_risk = equity * risk_per_trade
        stop_distance = atr * atr_multiplier
        stop_pct = stop_distance / current_price if current_price > 0 else 0.02

        if stop_pct > 0:
            atr_position_usdt = dollar_risk / stop_pct
        else:
            atr_position_usdt = max_usdt

        # Apply max limit to ATR position
        atr_position_usdt = min(atr_position_usdt, max_usdt)

        # Step 2: Calculate AI adjustment multiplier
        if ai_size_pct is not None and ai_size_pct >= 0:
            # AI provided a position size percentage
            ai_pct_normalized = min(ai_size_pct / 100.0, 1.0)  # Cap at 100%
            ai_multiplier = min_mult + ai_weight * ai_pct_normalized
            ai_multiplier = max(min_mult, min(ai_multiplier, max_mult))  # Clamp to range
            ai_source = 'ai_provided'
        else:
            # AI didn't provide position size
            if fallback_to_atr:
                ai_multiplier = 1.0  # Use full ATR position
                ai_source = 'fallback_atr'
            else:
                ai_multiplier = (min_mult + max_mult) / 2  # Use middle value
                ai_source = 'default_middle'

        # Step 3: Apply AI multiplier to ATR position
        position_usdt = atr_position_usdt * ai_multiplier

        # Apply risk multiplier from RiskController
        position_usdt *= risk_multiplier

        # v7.1: Enforce max_usdt ceiling (ai_multiplier or fallback_atr=1.0 can exceed
        # the clamped atr_position_usdt when max_multiplier > 1.0)
        final_usdt = min(position_usdt, max_usdt)

        details = {
            'method': 'hybrid_atr_ai',
            'equity': equity,
            'risk_per_trade_pct': risk_per_trade,
            'dollar_risk': dollar_risk,
            'atr': atr,
            'atr_multiplier': atr_multiplier,
            'stop_distance': stop_distance,
            'stop_pct': stop_pct * 100,
            'atr_position_usdt': atr_position_usdt,
            'ai_size_pct': ai_size_pct,
            'ai_source': ai_source,
            'ai_multiplier': ai_multiplier,
            'min_multiplier': min_mult,
            'max_multiplier': max_mult,
            'risk_multiplier': risk_multiplier,
            'max_usdt': max_usdt,
            'final_usdt': final_usdt,
        }

        if logger:
            logger.info(
                f"📊 Hybrid ATR-AI: ATR=${atr_position_usdt:.2f} × "
                f"AI_mult={ai_multiplier:.2f} ({ai_source}) = ${final_usdt:.2f}"
            )

    elif method == 'atr_based':
        # ATR-Based Position Sizing
        atr_config = sizing_config.get('atr_based', {})
        risk_per_trade = atr_config.get('risk_per_trade_pct', 0.01)
        atr_multiplier = atr_config.get('atr_multiplier', 2.0)

        # Get ATR from technical data
        atr = technical_data.get('atr', 0)
        if atr <= 0:
            # Fallback: estimate ATR as 2% of price
            atr = current_price * 0.02
            if logger:
                logger.warning(f"ATR not available, using estimate: ${atr:.2f}")

        # Calculate dollar risk
        dollar_risk = equity * risk_per_trade

        # Calculate stop distance
        stop_distance = atr * atr_multiplier

        # Position size = Risk / (Stop Distance as % of price)
        stop_pct = stop_distance / current_price
        if stop_pct > 0:
            position_usdt = dollar_risk / stop_pct
        else:
            position_usdt = max_usdt

        # Apply risk multiplier from RiskController
        position_usdt *= risk_multiplier

        # Apply max limit
        final_usdt = min(position_usdt, max_usdt)

        details = {
            'method': 'atr_based',
            'equity': equity,
            'risk_per_trade_pct': risk_per_trade,
            'dollar_risk': dollar_risk,
            'atr': atr,
            'atr_multiplier': atr_multiplier,
            'stop_distance': stop_distance,
            'stop_pct': stop_pct * 100,
            'risk_multiplier': risk_multiplier,
            'position_usdt': position_usdt,
            'max_usdt': max_usdt,
            'final_usdt': final_usdt,
        }

    elif method == 'ai_controlled':
        # v4.8: AI 控制仓位计算
        # 公式: 最终仓位 = max_usdt × AI建议百分比
        # max_usdt = equity × max_position_ratio × leverage (已在上面计算)

        ai_config = sizing_config.get('ai_controlled', {})
        default_size_pct = ai_config.get('default_size_pct', 50)
        confidence_mapping = ai_config.get('confidence_mapping', {
            'HIGH': 80,
            'MEDIUM': 50,
            'LOW': 30,
        })

        # 确定仓位百分比 (优先级: AI 输出 > 信心映射 > 默认值)
        if ai_size_pct is not None and ai_size_pct >= 0:
            # AI 直接提供了仓位百分比
            # v7.1: Clamp to [0, 100] — AI may return >100 which would exceed max_usdt
            size_pct = min(float(ai_size_pct), 100.0)
            size_source = 'ai_provided'
        else:
            # 根据信心等级映射
            confidence = signal_data.get('confidence', 'MEDIUM').upper()
            size_pct = confidence_mapping.get(confidence, default_size_pct)
            size_source = f'confidence_{confidence}'

        # 转换为小数并计算仓位
        size_ratio = size_pct / 100.0  # Convert 0-100 to 0-1
        position_usdt = max_usdt * size_ratio

        # v11.0-simple: Apply appetite_scale (risk_appetite → position sizing only)
        appetite_scale_map = ai_config.get('appetite_scale', {
            'AGGRESSIVE': 1.0,
            'NORMAL': 0.8,
            'CONSERVATIVE': 0.5,
        })
        risk_appetite = signal_data.get('risk_appetite', 'NORMAL')
        if isinstance(risk_appetite, str):
            risk_appetite = risk_appetite.upper()
        appetite_scale = appetite_scale_map.get(risk_appetite, 0.8)
        position_usdt *= appetite_scale

        # Apply risk multiplier
        position_usdt *= risk_multiplier

        # v7.1: Enforce max_usdt ceiling (consistent with atr_based and fixed_pct)
        final_usdt = min(position_usdt, max_usdt)

        # v11.0-simple: Single-trade risk clamp (plan Section 3/6)
        # position_usdt × SL_distance / entry_price ≤ max_single_trade_risk_pct × equity
        # Computes SL distance from confidence → ATR multiplier mapping
        # v39.0: Use 4H ATR (matches actual SL/TP calculation ATR source)
        max_risk_pct = sizing_config.get('max_single_trade_risk_pct', 0.02)
        atr_30m = technical_data.get('atr', 0)
        effective_atr = atr_4h if (atr_4h or 0) > 0 else atr_30m
        if effective_atr > 0 and max_risk_pct > 0:  # current_price > 0 guaranteed by early return
            conf_for_sl = signal_data.get('confidence', 'MEDIUM').upper()
            mech_cfg = _get_trading_logic_config().get('mechanical_sltp', {})
            sl_multipliers = mech_cfg.get('sl_atr_multiplier', {'HIGH': 0.8, 'MEDIUM': 1.0, 'LOW': 1.0})
            sl_mult = sl_multipliers.get(conf_for_sl, 1.0)
            sl_distance_frac = sl_mult * effective_atr / current_price  # SL distance as fraction
            atr_source = "4H" if (atr_4h or 0) > 0 else "30M"
            if sl_distance_frac > 0:
                max_risk_usdt = equity * max_risk_pct / sl_distance_frac
                if final_usdt > max_risk_usdt:
                    if logger:
                        logger.info(
                            f"⚠️ Risk clamp: ${final_usdt:.2f} → ${max_risk_usdt:.2f} "
                            f"(SL={sl_mult:.1f}×ATR({atr_source})={sl_distance_frac*100:.2f}%, "
                            f"max risk={max_risk_pct*100:.0f}% equity)"
                        )
                    final_usdt = max_risk_usdt

        details = {
            'method': 'ai_controlled',
            'ai_size_pct': ai_size_pct,
            'size_pct_used': size_pct,
            'size_source': size_source,
            'confidence': signal_data.get('confidence', 'MEDIUM'),
            'risk_appetite': risk_appetite,
            'appetite_scale': appetite_scale,
            'equity': equity,
            'leverage': leverage,
            'max_position_ratio': max_position_ratio,
            'max_usdt': max_usdt,
            'risk_multiplier': risk_multiplier,
            'final_usdt': final_usdt,
        }

        if logger:
            logger.info(
                f"📊 AI-controlled sizing: {size_pct}% of ${max_usdt:.0f} "
                f"× appetite={appetite_scale:.0%} ({risk_appetite}) "
                f"(equity=${equity} × {max_position_ratio*100:.0f}% × {leverage}x) "
                f"({size_source}) = ${final_usdt:.2f}"
            )

    else:
        # Original fixed_pct method (legacy)
        base_usdt = config.get('base_usdt', 100)

        # Confidence multiplier
        confidence = signal_data.get('confidence', 'MEDIUM').lower()
        conf_mult = config.get(f'{confidence}_confidence_multiplier', 1.0)

        # Trend multiplier
        trend = technical_data.get('overall_trend', '震荡整理')
        trend_mult = (
            config.get('trend_strength_multiplier', 1.2)
            if trend in ['强势上涨', '强势下跌']
            else 1.0
        )

        # RSI multiplier (reduce size in extreme RSI)
        rsi = technical_data.get('rsi', 50)
        rsi_extreme_upper = config.get('rsi_extreme_upper', 70)
        rsi_extreme_lower = config.get('rsi_extreme_lower', 30)
        rsi_mult = (
            config.get('rsi_extreme_multiplier', 0.7)
            if rsi > rsi_extreme_upper or rsi < rsi_extreme_lower
            else 1.0
        )

        # Calculate suggested USDT
        suggested_usdt = base_usdt * conf_mult * trend_mult * rsi_mult

        # Apply risk multiplier
        suggested_usdt *= risk_multiplier

        # Apply max position ratio limit
        final_usdt = min(suggested_usdt, max_usdt)

        details = {
            'method': 'fixed_pct',
            'base_usdt': base_usdt,
            'conf_mult': conf_mult,
            'trend_mult': trend_mult,
            'trend': trend,
            'rsi_mult': rsi_mult,
            'rsi': rsi,
            'risk_multiplier': risk_multiplier,
            'suggested_usdt': suggested_usdt,
            'max_usdt': max_usdt,
            'final_usdt': final_usdt,
        }

    # Get Binance limits from config (禁止硬编码 - Reference: CLAUDE.md)
    min_notional_usdt = get_min_notional_usdt()
    min_notional_safety_margin = get_min_notional_safety_margin()
    quantity_step = get_quantity_adjustment_step()

    # Enforce Binance minimum notional requirement
    if final_usdt < min_notional_usdt:
        final_usdt = min_notional_usdt
        details['final_usdt'] = final_usdt

    # Convert to BTC quantity
    btc_quantity = final_usdt / current_price

    # Apply minimum trade amount
    min_trade = config.get('min_trade_amount', 0.001)
    if btc_quantity < min_trade:
        btc_quantity = min_trade

    # Round to instrument precision (using config quantity_step, not hardcoded)
    btc_quantity = math.floor(btc_quantity / quantity_step) * quantity_step

    # v15.1: Re-clamp after rounding to ensure notional ≤ max_usdt
    # round() can round UP (e.g. 0.01271 → 0.013), causing notional to exceed max_usdt
    if btc_quantity * current_price > max_usdt:
        btc_quantity = math.floor(max_usdt / current_price / quantity_step) * quantity_step

    # CRITICAL: Re-check notional after rounding
    min_notional_with_margin = min_notional_usdt * min_notional_safety_margin

    actual_notional = btc_quantity * current_price
    adjusted = False
    if actual_notional < min_notional_with_margin:
        # Increase quantity to meet minimum notional with safety margin (round UP)
        btc_quantity = min_notional_with_margin / current_price
        # Round up to next step
        btc_quantity = math.ceil(btc_quantity / quantity_step) * quantity_step
        # Final verification
        final_notional = btc_quantity * current_price
        if final_notional < min_notional_usdt:
            btc_quantity += quantity_step
        adjusted = True
        if logger:
            logger.warning(
                f"⚠️ Adjusted quantity after rounding: {btc_quantity:.3f} BTC "
                f"to meet ${min_notional_usdt} minimum notional"
            )

    # Calculate final notional
    notional = btc_quantity * current_price

    # Update details with final values
    details['btc_quantity'] = btc_quantity
    details['notional'] = notional
    details['adjusted'] = adjusted

    # Log calculation details
    if logger:
        method_name = details.get('method', 'unknown')
        logger.info(
            f"📊 Position Sizing ({method_name}): "
            f"${final_usdt:.2f} = {btc_quantity:.3f} BTC "
            f"(notional: ${notional:.2f}, risk_mult: {risk_multiplier:.1f})"
        )

    return btc_quantity, details


# =============================================================================
# SL/TP VALIDATION FUNCTIONS (v9.0)
# Used by both ai_strategy.py and diagnose_realtime.py
# =============================================================================

# =============================================================================
# SL/TP CONFIGURATION (Phase 3: Migrated to ConfigManager)
# =============================================================================
# 这些值已迁移到 configs/base.yaml 的 trading_logic 节
#
# BREAKING CHANGE (Phase 3):
# - 旧代码使用: MIN_SL_DISTANCE_PCT (常量)
# - 新代码使用: get_min_sl_distance_pct() (函数)
#
# 为了避免循环导入，这些值不能在模块级别初始化
# 必须使用函数形式访问
# =============================================================================

# NOTE: 这些注释保留用于文档目的，实际值从配置加载
# MIN_SL_DISTANCE_PCT = 0.01   # 1% minimum SL distance (avoid too tight stops)
# MIN_TP_DISTANCE_PCT = 0.005  # 0.5% minimum TP distance
# DEFAULT_SL_PCT = 0.02        # 2% default stop loss distance
# DEFAULT_TP_PCT_BUY = 0.03    # 3% take profit for BUY (above entry)
# DEFAULT_TP_PCT_SELL = 0.03   # 3% take profit for SELL (below entry)
# TP_PCT_CONFIG = {'HIGH': 0.03, 'MEDIUM': 0.02, 'LOW': 0.01}

# 实际值通过以下函数访问:
# - get_min_sl_distance_pct()
# - get_min_tp_distance_pct()
# - get_default_sl_pct()
# - get_default_tp_pct_buy()
# - get_default_tp_pct_sell()
# - get_tp_pct_by_confidence(confidence)


def validate_multiagent_sltp(
    side: str,
    multi_sl: Optional[float],
    multi_tp: Optional[float],
    entry_price: float,
    sr_zones: Optional[Dict[str, Any]] = None,
    atr_value: Optional[float] = None,
    trend_info: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, Optional[float], Optional[float], str]:
    """
    v11.0-simple: Validate mechanical SL/TP values.

    Simplified from v10.x: removed S/R zone anchored bypass, zone breach detection.
    SL/TP comes from calculate_mechanical_sltp() (guaranteed R/R by construction).
    This function is a safety net — validates direction correctness and R/R floor.

    Parameters
    ----------
    side : str
        Trade side ('BUY', 'SELL', 'LONG', or 'SHORT')
    multi_sl : float, optional
        Stop loss price from mechanical calculation
    multi_tp : float, optional
        Take profit price from mechanical calculation
    entry_price : float
        Entry price
    sr_zones : Dict, optional
        Unused in v11.0-simple (kept for API compatibility)
    atr_value : float, optional
        Current ATR value for min SL distance check
    trend_info : Dict, optional
        Technical data for counter-trend R/R threshold escalation.

    Returns
    -------
    Tuple[bool, Optional[float], Optional[float], str]
        (is_valid, final_sl, final_tp, reason)
    """
    logger = logging.getLogger(__name__)

    if not multi_sl or not multi_tp or multi_sl <= 0 or multi_tp <= 0:
        return False, None, None, "SL/TP not provided or invalid"

    sl_distance = abs(multi_sl - entry_price) / entry_price
    tp_distance = abs(multi_tp - entry_price) / entry_price

    is_long = side.upper() in ('BUY', 'LONG')

    # Minimum SL distance check (ATR-primary)
    min_sl = get_min_sl_distance(atr_value=atr_value or 0.0, entry_price=entry_price)
    min_tp = get_min_tp_distance_pct()

    # Use small epsilon tolerance for floating-point edge case:
    # mechanical SL and min_sl can use the same ATR multiplier, producing nearly identical values.
    if sl_distance < min_sl * 0.999:
        return False, None, None, (
            f"SL too close to entry ({sl_distance*100:.2f}% < {min_sl*100:.2f}% minimum). "
            f"Mechanical calculation failed safety check."
        )

    if is_long:
        if multi_sl >= entry_price:
            return False, None, None, f"BUY SL (${multi_sl:,.2f}) must be < entry (${entry_price:,.2f})"
        if multi_tp <= entry_price:
            return False, None, None, f"BUY TP (${multi_tp:,.2f}) must be > entry (${entry_price:,.2f})"

        risk = entry_price - multi_sl
        reward = multi_tp - entry_price
        if risk > 0:
            rr_ratio = reward / risk
            min_rr = get_min_rr_ratio()

            counter_trend = False
            if trend_info:
                counter_trend = _is_counter_trend(is_long, trend_info)
                if counter_trend:
                    ct_multiplier = get_counter_trend_rr_multiplier()
                    min_rr = min_rr * ct_multiplier
                    logger.info(
                        f"📐 Counter-trend detected (LONG vs downtrend): "
                        f"R/R threshold raised to {min_rr:.2f}:1"
                    )

            # Use small epsilon tolerance for floating-point edge case:
            # calculate_mechanical_sltp() constructs tp = sl_dist * rr_target,
            # but back-calculating rr from prices can lose precision.
            if rr_ratio < min_rr * 0.999:
                ct_note = " (counter-trend)" if counter_trend else ""
                return False, None, None, (
                    f"R/R {rr_ratio:.2f}:1 < {min_rr:.2f}:1 minimum{ct_note} "
                    f"(SL ${multi_sl:,.2f}, TP ${multi_tp:,.2f})."
                )

        if tp_distance < min_tp:
            return True, multi_sl, multi_tp, f"Valid with note: TP close to entry ({tp_distance*100:.2f}%)"
        return True, multi_sl, multi_tp, f"Valid (SL: {sl_distance*100:.2f}%, TP: {tp_distance*100:.2f}%)"

    else:  # SELL
        if multi_sl <= entry_price:
            return False, None, None, f"SELL SL (${multi_sl:,.2f}) must be > entry (${entry_price:,.2f})"
        if multi_tp >= entry_price:
            return False, None, None, f"SELL TP (${multi_tp:,.2f}) must be < entry (${entry_price:,.2f})"

        risk = multi_sl - entry_price
        reward = entry_price - multi_tp
        if risk > 0:
            rr_ratio = reward / risk
            min_rr = get_min_rr_ratio()

            counter_trend = False
            if trend_info:
                counter_trend = _is_counter_trend(is_long, trend_info)
                if counter_trend:
                    ct_multiplier = get_counter_trend_rr_multiplier()
                    min_rr = min_rr * ct_multiplier
                    logger.info(
                        f"📐 Counter-trend detected (SHORT vs uptrend): "
                        f"R/R threshold raised to {min_rr:.2f}:1"
                    )

            # Use small epsilon tolerance (same as BUY side above)
            if rr_ratio < min_rr * 0.999:
                ct_note = " (counter-trend)" if counter_trend else ""
                return False, None, None, (
                    f"R/R {rr_ratio:.2f}:1 < {min_rr:.2f}:1 minimum{ct_note} "
                    f"(SL ${multi_sl:,.2f}, TP ${multi_tp:,.2f})."
                )

        if tp_distance < min_tp:
            return True, multi_sl, multi_tp, f"Valid with note: TP close to entry ({tp_distance*100:.2f}%)"
        return True, multi_sl, multi_tp, f"Valid (SL: {sl_distance*100:.2f}%, TP: {tp_distance*100:.2f}%)"



# =============================================================================
# TRADE EVALUATION (v5.1: Trading Evaluation Standards)
# Integrated with decision_memory for AI learning
# =============================================================================

# Grade thresholds (business logic constants, not configurable)
GRADE_THRESHOLDS = {
    'A+': 2.5,   # actual R/R >= 2.5 (exceptional trade)
    'A':  1.5,   # actual R/R >= 1.5 (strong win)
    'B':  1.0,   # actual R/R >= 1.0 (acceptable profit)
    'C':  0.0,   # pnl > 0 but R/R < 1.0 (small profit, poor R/R)
    'D':  None,   # loss within planned SL (controlled loss)
    'F':  None,   # loss exceeded planned SL (uncontrolled)
}


def evaluate_trade(
    entry_price: float,
    exit_price: float,
    planned_sl: Optional[float],
    planned_tp: Optional[float],
    direction: str,
    pnl_pct: float,
    confidence: str = "MEDIUM",
    position_size_pct: float = 0.0,
    entry_timestamp: Optional[str] = None,
    exit_timestamp: Optional[str] = None,
    # v6.0: Additional position management metrics
    pyramid_layers_used: int = 1,
    partial_close_count: int = 0,
    confidence_at_exit: str = "",
    # v11.5: SL/TP optimization data — enables post-hoc analysis of ATR multiplier performance
    atr_value: float = 0.0,
    sl_atr_multiplier: float = 0.0,
    is_counter_trend: bool = False,
    risk_appetite: str = "",
    trend_direction: str = "",
    adx: float = 0.0,
    # v11.5: MAE/MFE — Maximum Adverse/Favorable Excursion (percentage from entry)
    mae_pct: float = 0.0,
    mfe_pct: float = 0.0,
) -> Dict[str, Any]:
    """
    Evaluate trade quality and assign a grade.

    Integrated with decision_memory - the returned dict is stored as the
    'evaluation' field in each memory entry.

    Grading System:
        A+ : Actual R/R >= 2.5 (exceptional)
        A  : Actual R/R >= 1.5 (strong win)
        B  : Actual R/R >= 1.0 (acceptable profit)
        C  : Profit but R/R < 1.0 (small profit)
        D  : Loss within planned SL (controlled loss - discipline maintained)
        F  : Loss exceeded planned SL by > 20% (uncontrolled)

    Parameters
    ----------
    entry_price : float
        Actual entry price
    exit_price : float
        Actual exit price
    planned_sl : float, optional
        Planned stop loss price at entry
    planned_tp : float, optional
        Planned take profit price at entry
    direction : str
        Trade direction ('LONG' or 'SHORT')
    pnl_pct : float
        Realized P&L percentage
    confidence : str
        Signal confidence level ('HIGH', 'MEDIUM', 'LOW')
    position_size_pct : float
        Position size percentage used
    entry_timestamp : str, optional
        ISO format entry timestamp
    exit_timestamp : str, optional
        ISO format exit timestamp
    pyramid_layers_used : int
        v6.0: Number of pyramid layers used in this trade (1 = no pyramiding)
    partial_close_count : int
        v6.0: Number of partial closes executed during this trade
    confidence_at_exit : str
        v6.0: Confidence level at exit (may differ from entry)
    atr_value : float
        v11.5: ATR(14) value at entry time
    sl_atr_multiplier : float
        v11.5: Effective SL ATR multiplier used (e.g. 2.0 or 2.5)
    is_counter_trend : bool
        v11.5: Whether trade was against dominant trend
    risk_appetite : str
        v11.5: AI risk appetite at entry (AGGRESSIVE/NORMAL/CONSERVATIVE)
    trend_direction : str
        v11.5: Trend direction at entry (UPTREND/DOWNTREND/SIDEWAYS)
    adx : float
        v11.5: ADX value at entry (trend strength)
    mae_pct : float
        v11.5: Maximum Adverse Excursion — max drawdown % from entry during trade
    mfe_pct : float
        v11.5: Maximum Favorable Excursion — max unrealized profit % from entry during trade

    Returns
    -------
    Dict[str, Any]
        Evaluation data dict to be stored in decision_memory
    """
    # v5.12: Input validation — sanitize numeric inputs
    if not entry_price or entry_price <= 0:
        entry_price = 0.0
    if not exit_price or exit_price <= 0:
        exit_price = 0.0
    # Ensure planned_sl/tp are valid positive numbers or None
    if planned_sl is not None and planned_sl <= 0:
        planned_sl = None
    if planned_tp is not None and planned_tp <= 0:
        planned_tp = None

    is_long = direction.upper() in ('LONG', 'BUY')

    # --- Determine exit type ---
    exit_type = _classify_exit_type(
        exit_price, planned_sl, planned_tp, entry_price, is_long,
    )

    # --- Calculate actual R/R ---
    actual_rr = 0.0
    planned_rr = 0.0
    direction_correct = pnl_pct > 0

    if entry_price > 0 and planned_sl and planned_sl > 0:
        risk = abs(entry_price - planned_sl)
        if risk > 0:
            if is_long:
                actual_reward = exit_price - entry_price
            else:
                actual_reward = entry_price - exit_price
            actual_rr = round(actual_reward / risk, 2)

            # Planned R/R (direction-aware, not abs — catches stale TP on wrong side)
            if planned_tp and planned_tp > 0:
                if is_long:
                    planned_reward = planned_tp - entry_price
                else:
                    planned_reward = entry_price - planned_tp
                if planned_reward > 0:
                    planned_rr = round(planned_reward / risk, 2)
                else:
                    # TP on wrong side of entry — stale data or averaged entry
                    planned_rr = 0.0

    # --- Execution quality (how well did actual match plan) ---
    # Clamp to [0, 2.0]: negative actual_rr (losing trade) → 0, cap at 2.0
    execution_quality = 0.0
    if planned_rr > 0:
        execution_quality = round(max(0.0, min(actual_rr / planned_rr, 2.0)), 2)

    # --- Assign grade ---
    grade = _assign_grade(pnl_pct, actual_rr, exit_type, planned_sl, exit_price, entry_price, is_long)

    # --- Hold duration ---
    hold_duration_min = _calc_hold_duration(entry_timestamp, exit_timestamp)

    result = {
        'grade': grade,
        'direction_correct': direction_correct,
        'entry_price': round(entry_price, 2),
        'exit_price': round(exit_price, 2),
        'planned_sl': round(planned_sl, 2) if planned_sl else None,
        'planned_tp': round(planned_tp, 2) if planned_tp else None,
        'planned_rr': planned_rr,
        'actual_rr': actual_rr,
        'execution_quality': execution_quality,
        'exit_type': exit_type,
        'confidence': confidence.upper(),
        'position_size_pct': position_size_pct,
        'hold_duration_min': hold_duration_min,
    }

    # v11.5: SL/TP optimization data (only store non-default values to keep JSON lean)
    if atr_value > 0:
        result['atr_value'] = round(atr_value, 2)
    if sl_atr_multiplier > 0:
        result['sl_atr_multiplier'] = round(sl_atr_multiplier, 2)
    if is_counter_trend:
        result['is_counter_trend'] = True
    if risk_appetite:
        result['risk_appetite'] = risk_appetite.upper()
    if trend_direction:
        result['trend_direction'] = trend_direction.upper()
    if adx > 0:
        result['adx'] = round(adx, 1)
    if mae_pct > 0:
        result['mae_pct'] = round(mae_pct, 2)
    if mfe_pct > 0:
        result['mfe_pct'] = round(mfe_pct, 2)

    # v6.0: Position management metrics (for memory system learning)
    if pyramid_layers_used > 1:
        result['pyramid_layers_used'] = pyramid_layers_used
    if partial_close_count > 0:
        result['partial_close_count'] = partial_close_count
    if confidence_at_exit:
        result['confidence_at_exit'] = confidence_at_exit.upper()

    return result


def _classify_exit_type(
    exit_price: float,
    planned_sl: Optional[float],
    planned_tp: Optional[float],
    entry_price: float,
    is_long: bool,
) -> str:
    """
    Classify how the trade was closed.

    Uses proximity-based matching: exit must be NEAR SL/TP (within 0.15%
    slippage tolerance), not just on the loss/profit side.

    Returns: 'TAKE_PROFIT', 'STOP_LOSS', 'MANUAL', or 'REVERSAL'
    """
    if not planned_sl or not planned_tp or entry_price <= 0:
        return 'MANUAL'

    # Proximity tolerance for SL/TP fill slippage (0.15% of each price)
    sl_tolerance = abs(planned_sl) * 0.0015
    tp_tolerance = abs(planned_tp) * 0.0015

    if is_long:
        # LONG SL: exit near SL and losing
        if abs(exit_price - planned_sl) <= sl_tolerance and exit_price < entry_price:
            return 'STOP_LOSS'
        # LONG TP: exit near TP and winning
        if abs(exit_price - planned_tp) <= tp_tolerance and exit_price > entry_price:
            return 'TAKE_PROFIT'
    else:
        # SHORT SL: exit near SL and losing
        if abs(exit_price - planned_sl) <= sl_tolerance and exit_price > entry_price:
            return 'STOP_LOSS'
        # SHORT TP: exit near TP and winning
        if abs(exit_price - planned_tp) <= tp_tolerance and exit_price < entry_price:
            return 'TAKE_PROFIT'

    return 'MANUAL'


def _assign_grade(
    pnl_pct: float,
    actual_rr: float,
    exit_type: str,
    planned_sl: Optional[float],
    exit_price: float,
    entry_price: float,
    is_long: bool,
) -> str:
    """
    Assign A+/A/B/C/D/F grade based on trade outcome.

    Profitable trades graded by actual R/R achieved.
    Losing trades graded by discipline (did SL work?).

    v5.12: When planned_sl is missing, losing trades are graded 'D-' (unknown
    discipline) instead of 'F' — we cannot confirm the loss was uncontrolled
    without the reference SL.  The exit_type hint is also used: if exit was
    via STOP_LOSS, it implies discipline even without planned_sl data.
    """
    if pnl_pct > 0:
        # Profitable - grade by R/R achievement
        if actual_rr >= 2.5:
            return 'A+'
        elif actual_rr >= 1.5:
            return 'A'
        elif actual_rr >= 1.0:
            return 'B'
        else:
            return 'C'
    elif pnl_pct == 0:
        # Breakeven — not a loss, grade as C (no meaningful profit)
        return 'C'
    else:
        # Loss - grade by discipline
        if not planned_sl or planned_sl <= 0:
            # v5.12: Missing SL data — cannot determine discipline
            # Use exit_type as a hint: STOP_LOSS implies controlled exit
            if exit_type == 'STOP_LOSS':
                return 'D'
            # Small loss (< 2%) without SL data is likely controlled
            if abs(pnl_pct) < 2.0:
                return 'D'
            # Large loss without SL data — grade as unknown discipline
            return 'D-'

        # Check if loss was within planned SL (with 20% tolerance)
        if entry_price > 0:
            planned_loss_pct = abs(entry_price - planned_sl) / entry_price
        else:
            planned_loss_pct = 0.0
        actual_loss_pct = abs(pnl_pct) / 100.0

        # D = loss within SL (controlled), F = exceeded SL significantly
        if actual_loss_pct <= planned_loss_pct * 1.2:
            return 'D'
        else:
            return 'F'


def _calc_hold_duration(
    entry_ts: Optional[str],
    exit_ts: Optional[str],
) -> int:
    """Calculate hold duration in minutes from ISO timestamps."""
    if not entry_ts or not exit_ts:
        return 0
    try:
        from datetime import datetime
        # Handle both with and without microseconds
        # Try fromisoformat first (handles most ISO 8601 formats in Python 3.12+)
        entry_dt = datetime.fromisoformat(entry_ts.replace('Z', '+00:00'))
        exit_dt = datetime.fromisoformat(exit_ts.replace('Z', '+00:00'))
        delta = exit_dt - entry_dt
        return max(0, int(delta.total_seconds() / 60))
    except Exception as e:
        logger.debug(f"Hold duration calculation failed (non-critical): {e}")
    return 0


def get_evaluation_summary(memories: list) -> Dict[str, Any]:
    """
    Compute aggregate evaluation statistics from decision_memory.

    Used by daily/weekly summaries and Telegram reports.

    Parameters
    ----------
    memories : list
        List of decision_memory entries (each may have 'evaluation' field)

    Returns
    -------
    Dict[str, Any]
        Aggregate stats: grade distribution, avg R/R, direction accuracy, etc.
    """
    evaluated = [m for m in memories if m.get('evaluation')]
    if not evaluated:
        return {}

    evals = [m['evaluation'] for m in evaluated]
    total = len(evals)

    # Grade distribution
    grade_counts = {}
    for e in evals:
        g = e.get('grade', '?')
        grade_counts[g] = grade_counts.get(g, 0) + 1

    # Direction accuracy
    correct = sum(1 for e in evals if e.get('direction_correct'))
    direction_accuracy = round(correct / total * 100, 1) if total > 0 else 0.0

    # Average actual R/R (only for profitable trades)
    profitable_rrs = [e.get('actual_rr', 0) for e in evals if e.get('direction_correct')]
    avg_winning_rr = round(sum(profitable_rrs) / len(profitable_rrs), 2) if profitable_rrs else 0.0

    # Average execution quality
    exec_quals = [e.get('execution_quality', 0) for e in evals if e.get('execution_quality', 0) > 0]
    avg_exec_quality = round(sum(exec_quals) / len(exec_quals), 2) if exec_quals else 0.0

    # Exit type distribution
    exit_types = {}
    for e in evals:
        et = e.get('exit_type', 'UNKNOWN')
        exit_types[et] = exit_types.get(et, 0) + 1

    # Confidence accuracy (does HIGH confidence actually win more?)
    confidence_stats = {}
    for e in evals:
        conf = e.get('confidence', 'MEDIUM')
        if conf not in confidence_stats:
            confidence_stats[conf] = {'total': 0, 'wins': 0}
        confidence_stats[conf]['total'] += 1
        if e.get('direction_correct'):
            confidence_stats[conf]['wins'] += 1

    for conf, stats in confidence_stats.items():
        stats['accuracy'] = round(stats['wins'] / stats['total'] * 100, 1) if stats['total'] > 0 else 0.0

    # Average hold duration
    durations = [e.get('hold_duration_min', 0) for e in evals if e.get('hold_duration_min', 0) > 0]
    avg_hold_min = round(sum(durations) / len(durations)) if durations else 0

    # v11.5: SL/TP optimization stats
    mae_vals = [e.get('mae_pct', 0) for e in evals if e.get('mae_pct')]
    mfe_vals = [e.get('mfe_pct', 0) for e in evals if e.get('mfe_pct')]
    ct_count = sum(1 for e in evals if e.get('is_counter_trend'))
    avg_mae = round(sum(mae_vals) / len(mae_vals), 2) if mae_vals else 0.0
    avg_mfe = round(sum(mfe_vals) / len(mfe_vals), 2) if mfe_vals else 0.0

    result = {
        'total_evaluated': total,
        'grade_distribution': grade_counts,
        'direction_accuracy': direction_accuracy,
        'avg_winning_rr': avg_winning_rr,
        'avg_execution_quality': avg_exec_quality,
        'exit_type_distribution': exit_types,
        'confidence_accuracy': confidence_stats,
        'avg_hold_duration_min': avg_hold_min,
    }

    # v11.5: Only include SL/TP stats if data exists
    if mae_vals or mfe_vals:
        result['avg_mae_pct'] = avg_mae
        result['avg_mfe_pct'] = avg_mfe
        result['counter_trend_count'] = ct_count
        result['counter_trend_pct'] = round(ct_count / total * 100, 1) if total > 0 else 0.0

    return result


