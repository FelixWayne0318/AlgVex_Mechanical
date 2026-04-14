"""
Risk Controller Module (v3.12)

Institutional-grade risk management with circuit breakers.

Features:
- Maximum drawdown control (reduce/halt thresholds)
- Daily loss limits
- Consecutive loss protection
- Volatility circuit breakers
- Trade frequency limits

Reference: Two Sigma / Citadel risk management standards

Author: AlgVex Team
"""

import logging
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field


class TradingState(Enum):
    """Trading state managed by risk controller."""
    ACTIVE = "ACTIVE"           # Normal trading
    REDUCED = "REDUCED"         # Reduced position sizing (drawdown warning)
    HALTED = "HALTED"           # Trading halted (circuit breaker triggered)
    COOLDOWN = "COOLDOWN"       # Cooling down after consecutive losses


@dataclass
class RiskMetrics:
    """Current risk metrics snapshot."""
    peak_equity: float = 0.0
    current_equity: float = 0.0
    drawdown_pct: float = 0.0
    daily_pnl: float = 0.0
    daily_pnl_pct: float = 0.0
    consecutive_losses: int = 0
    current_atr: float = 0.0
    normal_atr: float = 0.0
    trading_state: TradingState = TradingState.ACTIVE
    halt_reason: str = ""


@dataclass
class TradeRecord:
    """Record of a completed trade."""
    timestamp: datetime
    side: str           # LONG / SHORT
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    pnl_pct: float


class RiskController:
    """
    Risk Controller with circuit breakers.

    Manages trading state based on:
    - Maximum drawdown
    - Daily P&L
    - Consecutive losses
    - Volatility
    - Trade frequency

    Usage:
        risk_controller = RiskController(config, logger)

        # Before each trade
        can_trade, reason = risk_controller.can_open_trade()
        if not can_trade:
            logger.warning(f"Trade blocked: {reason}")
            return

        # Get position size multiplier
        size_mult = risk_controller.get_position_size_multiplier()

        # After trade closes
        risk_controller.record_trade(trade_record)

        # Update equity periodically
        risk_controller.update_equity(current_equity)
    """

    def __init__(
        self,
        config: Dict[str, Any],
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize risk controller.

        Parameters
        ----------
        config : Dict
            Risk configuration from configs/base.yaml under 'risk.circuit_breakers'
        logger : Logger, optional
            Logger instance
        """
        self.logger = logger or logging.getLogger(__name__)
        self.config = config

        # Circuit breaker configs
        cb_config = config.get('circuit_breakers', {})
        self.enabled = cb_config.get('enabled', True)

        # Max drawdown config (static defaults)
        dd_config = cb_config.get('max_drawdown', {})
        self.dd_enabled = dd_config.get('enabled', True)
        self.dd_reduce_threshold = dd_config.get('reduce_threshold_pct', 0.10)
        self.dd_halt_threshold = dd_config.get('halt_threshold_pct', 0.15)
        self.dd_recovery_threshold = dd_config.get('recovery_threshold_pct', 0.05)

        # v2.0 Phase 2: Regime-Adaptive thresholds (overrides static when enabled)
        risk_regime_config = config.get('risk_regime', {})
        self._regime_adaptive_enabled = risk_regime_config.get('enabled', False)
        self._regime_thresholds = risk_regime_config.get('thresholds', {})

        # Daily loss config
        dl_config = cb_config.get('daily_loss', {})
        self.dl_enabled = dl_config.get('enabled', True)
        self.dl_max_loss_pct = dl_config.get('max_loss_pct', 0.03)
        self.dl_reset_hour = dl_config.get('reset_hour_utc', 0)

        # Consecutive loss config
        cl_config = cb_config.get('consecutive_losses', {})
        self.cl_enabled = cl_config.get('enabled', True)
        self.cl_max_losses = cl_config.get('max_losses', 3)
        self.cl_cooldown_hours = cl_config.get('cooldown_hours', 4)
        self.cl_recovery_wins = cl_config.get('recovery_wins_needed', 1)
        # v6.0: 2-consecutive-loss → REDUCED (position size halved before full cooldown)
        self.cl_reduce_at_losses = cl_config.get('reduce_at_losses', 2)

        # Volatility config
        vol_config = cb_config.get('volatility', {})
        self.vol_enabled = vol_config.get('enabled', True)
        self.vol_normal_atr_pct = vol_config.get('normal_atr_pct', 0.02)
        self.vol_halt_multiplier = vol_config.get('halt_multiplier', 3.0)

        # State tracking
        self.metrics = RiskMetrics()
        self.trade_history: List[TradeRecord] = []
        self.daily_start_equity: float = 0.0
        self.daily_reset_date: Optional[datetime] = None
        self.cooldown_until: Optional[datetime] = None
        self.consecutive_wins: int = 0

        # v2.0 Phase 2: Current regime (updated by strategy)
        self._current_regime: Optional[str] = None

        self.logger.info(f"RiskController initialized (enabled={self.enabled}, regime_adaptive={self._regime_adaptive_enabled})")

    def set_regime(self, regime: str) -> None:
        """v2.0 Phase 2: Update current market regime for adaptive thresholds.

        Called from strategy when HMM or ADX regime changes.
        When regime_adaptive is enabled, DD/daily_loss thresholds adjust dynamically.
        """
        self._current_regime = regime
        if self._regime_adaptive_enabled and regime in self._regime_thresholds:
            rt = self._regime_thresholds[regime]
            self.dd_reduce_threshold = rt.get('dd_reduced_pct', self.dd_reduce_threshold * 100) / 100.0
            self.dd_halt_threshold = rt.get('dd_halted_pct', self.dd_halt_threshold * 100) / 100.0
            self.dl_max_loss_pct = rt.get('daily_loss_pct', self.dl_max_loss_pct * 100) / 100.0
            self.logger.info(
                f"Regime-adaptive thresholds: {regime} → "
                f"DD reduce={self.dd_reduce_threshold:.1%}, halt={self.dd_halt_threshold:.1%}, "
                f"daily_loss={self.dl_max_loss_pct:.1%}"
            )

    def update_equity(self, current_equity: float, current_atr: Optional[float] = None):
        """
        Update current equity and recalculate metrics.

        Should be called periodically (e.g., every on_timer).

        Parameters
        ----------
        current_equity : float
            Current account equity
        current_atr : float, optional
            Current ATR value for volatility check
        """
        # Initialize peak equity
        if self.metrics.peak_equity == 0:
            self.metrics.peak_equity = current_equity
            self.daily_start_equity = current_equity
            self.daily_reset_date = datetime.now(timezone.utc).date()

        # Update peak equity (only if higher)
        if current_equity > self.metrics.peak_equity:
            self.metrics.peak_equity = current_equity

        # Calculate drawdown
        self.metrics.current_equity = current_equity
        if self.metrics.peak_equity > 0:
            self.metrics.drawdown_pct = (
                self.metrics.peak_equity - current_equity
            ) / self.metrics.peak_equity
        else:
            self.metrics.drawdown_pct = 0.0

        # Check for daily reset
        self._check_daily_reset()

        # Calculate daily P&L
        if self.daily_start_equity > 0:
            self.metrics.daily_pnl = current_equity - self.daily_start_equity
            self.metrics.daily_pnl_pct = self.metrics.daily_pnl / self.daily_start_equity

        # Update ATR metrics — use EMA to adapt baseline over time
        if current_atr is not None:
            self.metrics.current_atr = current_atr
            if self.metrics.normal_atr == 0:
                self.metrics.normal_atr = current_atr
            else:
                # Slow EMA (alpha=0.01) adapts to structural market changes
                # while remaining stable enough for circuit breaker reference
                alpha = 0.01
                self.metrics.normal_atr = (
                    alpha * current_atr + (1 - alpha) * self.metrics.normal_atr
                )

        # Update trading state
        self._update_trading_state()

    def _check_daily_reset(self):
        """Check if daily metrics should be reset."""
        now = datetime.now(timezone.utc)
        today = now.date()

        if self.daily_reset_date is None or today > self.daily_reset_date:
            # New day - reset daily metrics
            self.daily_start_equity = self.metrics.current_equity
            self.daily_reset_date = today
            self.metrics.daily_pnl = 0.0
            self.metrics.daily_pnl_pct = 0.0
            self.logger.info(f"Daily metrics reset. Start equity: ${self.daily_start_equity:,.2f}")

    def _update_trading_state(self):
        """Update trading state based on current metrics."""
        if not self.enabled:
            self.metrics.trading_state = TradingState.ACTIVE
            self.metrics.halt_reason = ""
            return

        # Check cooldown (3+ consecutive losses → full cooldown)
        if self.cooldown_until:
            if datetime.now(timezone.utc) < self.cooldown_until:
                self.metrics.trading_state = TradingState.COOLDOWN
                remaining = (self.cooldown_until - datetime.now(timezone.utc)).total_seconds() / 60
                self.metrics.halt_reason = f"连续亏损冷却中 (剩余 {remaining:.0f} 分钟)"
                return
            else:
                # Cooldown expired by time — reset for a fresh start
                self.metrics.consecutive_losses = 0
                self.cooldown_until = None
                self.logger.info("Cooldown expired by time — consecutive losses reset")

        # Check max drawdown - HALT
        if self.dd_enabled and self.metrics.drawdown_pct >= self.dd_halt_threshold:
            self.metrics.trading_state = TradingState.HALTED
            self.metrics.halt_reason = f"最大回撤熔断 ({self.metrics.drawdown_pct*100:.1f}% >= {self.dd_halt_threshold*100:.0f}%)"
            return

        # Check daily loss - HALT
        if self.dl_enabled and self.metrics.daily_pnl_pct <= -self.dl_max_loss_pct:
            self.metrics.trading_state = TradingState.HALTED
            self.metrics.halt_reason = f"日亏损限制 ({self.metrics.daily_pnl_pct*100:.1f}% <= -{self.dl_max_loss_pct*100:.0f}%)"
            return

        # Check volatility - HALT
        if self.vol_enabled and self.metrics.normal_atr > 0:
            vol_ratio = self.metrics.current_atr / self.metrics.normal_atr
            if vol_ratio >= self.vol_halt_multiplier:
                self.metrics.trading_state = TradingState.HALTED
                self.metrics.halt_reason = f"波动率熔断 (ATR {vol_ratio:.1f}x 正常值)"
                return

        # v18.0: Drawdown REDUCED with hysteresis band
        # Enter REDUCED at dd_reduce_threshold (10%), exit only below dd_recovery_threshold (5%)
        if self.dd_enabled:
            if self.metrics.drawdown_pct >= self.dd_reduce_threshold:
                # Above entry threshold → always REDUCED
                self.metrics.trading_state = TradingState.REDUCED
                self.metrics.halt_reason = (
                    f"回撤警告 ({self.metrics.drawdown_pct*100:.1f}% >= "
                    f"{self.dd_reduce_threshold*100:.0f}%), 仓位减半"
                )
                return
            elif (self.metrics.trading_state == TradingState.REDUCED
                  and self.metrics.drawdown_pct >= self.dd_recovery_threshold):
                # In hysteresis band (5%-10%) AND already REDUCED → stay REDUCED
                # Explicitly re-set state for defensive clarity (condition guarantees REDUCED,
                # but explicit assignment prevents bugs if upstream code changes)
                self.metrics.trading_state = TradingState.REDUCED
                self.metrics.halt_reason = (
                    f"回撤恢复中 ({self.metrics.drawdown_pct*100:.1f}%, "
                    f"需降至 {self.dd_recovery_threshold*100:.0f}% 以下恢复)"
                )
                return
            # Below recovery threshold (or was never REDUCED) → fall through to other checks

        # v6.0: Check consecutive losses - REDUCED (2 losses = half size, 3 = full cooldown)
        if (self.cl_enabled
                and self.metrics.consecutive_losses >= self.cl_reduce_at_losses
                and self.metrics.consecutive_losses < self.cl_max_losses):
            self.metrics.trading_state = TradingState.REDUCED
            self.metrics.halt_reason = (
                f"连续 {self.metrics.consecutive_losses} 次亏损, "
                f"仓位减半 ({self.cl_reduce_at_losses} 次触发)"
            )
            return

        # All checks passed
        self.metrics.trading_state = TradingState.ACTIVE
        self.metrics.halt_reason = ""

    def can_open_trade(self) -> Tuple[bool, str]:
        """
        Check if a new trade can be opened.

        Returns
        -------
        Tuple[bool, str]
            (can_trade, reason)
        """
        if not self.enabled:
            return True, ""

        # Check trading state
        if self.metrics.trading_state == TradingState.HALTED:
            return False, self.metrics.halt_reason

        if self.metrics.trading_state == TradingState.COOLDOWN:
            return False, self.metrics.halt_reason

        return True, ""

    def get_position_size_multiplier(self) -> float:
        """
        Get position size multiplier based on risk state.

        Returns
        -------
        float
            Multiplier for position size (0.0 to 1.0)
            - ACTIVE: 1.0
            - REDUCED: 0.5
            - HALTED/COOLDOWN: 0.0
        """
        if not self.enabled:
            return 1.0

        state = self.metrics.trading_state

        if state == TradingState.HALTED:
            return 0.0
        elif state == TradingState.COOLDOWN:
            return 0.0
        elif state == TradingState.REDUCED:
            return 0.5
        else:
            return 1.0

    def record_trade(self, trade: TradeRecord):
        """
        Record a completed trade and update consecutive loss tracking.

        Parameters
        ----------
        trade : TradeRecord
            Completed trade record
        """
        self.trade_history.append(trade)
        # Update consecutive loss tracking
        if trade.pnl < 0:
            self.metrics.consecutive_losses += 1
            self.consecutive_wins = 0
            self.logger.warning(
                f"Trade loss: ${trade.pnl:.2f} ({trade.pnl_pct*100:.1f}%) | "
                f"Consecutive losses: {self.metrics.consecutive_losses}"
            )

            # Check if cooldown needed
            if self.cl_enabled and self.metrics.consecutive_losses >= self.cl_max_losses:
                self.cooldown_until = datetime.now(timezone.utc) + timedelta(hours=self.cl_cooldown_hours)
                self.metrics.trading_state = TradingState.COOLDOWN
                self.metrics.halt_reason = f"连续 {self.cl_max_losses} 次亏损，冷却 {self.cl_cooldown_hours} 小时"
                self.logger.warning(f"Consecutive loss limit reached. Cooldown until {self.cooldown_until}")
        else:
            self.consecutive_wins += 1

            # Check if recovered from consecutive losses
            if self.consecutive_wins >= self.cl_recovery_wins:
                self.metrics.consecutive_losses = 0
                self.cooldown_until = None
                if self.metrics.trading_state == TradingState.COOLDOWN:
                    self.metrics.trading_state = TradingState.ACTIVE
                    self.metrics.halt_reason = ""
                    self.logger.info("Recovered from consecutive losses")

            self.logger.info(
                f"Trade profit: ${trade.pnl:.2f} ({trade.pnl_pct*100:.1f}%) | "
                f"Consecutive wins: {self.consecutive_wins}"
            )

    def record_trade_simple(
        self,
        side: str,
        entry_price: float,
        exit_price: float,
        quantity: float,
    ):
        """
        Convenience method to record a trade with basic info.

        Parameters
        ----------
        side : str
            Trade side (LONG/SHORT)
        entry_price : float
            Entry price
        exit_price : float
            Exit price
        quantity : float
            Position quantity
        """
        if side.upper() == "LONG":
            pnl = (exit_price - entry_price) * quantity
        else:
            pnl = (entry_price - exit_price) * quantity

        pnl_pct = pnl / (entry_price * quantity) if entry_price > 0 else 0

        trade = TradeRecord(
            timestamp=datetime.now(timezone.utc),
            side=side.upper(),
            entry_price=entry_price,
            exit_price=exit_price,
            quantity=quantity,
            pnl=pnl,
            pnl_pct=pnl_pct,
        )
        self.record_trade(trade)

    def get_status(self) -> Dict[str, Any]:
        """
        Get current risk status for display/logging.

        Returns
        -------
        Dict
            Risk status summary
        """
        return {
            'trading_state': self.metrics.trading_state.value,
            'halt_reason': self.metrics.halt_reason,
            'drawdown_pct': round(self.metrics.drawdown_pct * 100, 2),
            'daily_pnl_pct': round(self.metrics.daily_pnl_pct * 100, 2),
            'consecutive_losses': self.metrics.consecutive_losses,
            'peak_equity': round(self.metrics.peak_equity, 2),
            'current_equity': round(self.metrics.current_equity, 2),
            'position_multiplier': self.get_position_size_multiplier(),
        }

    def format_status_message(self) -> str:
        """
        Format risk status for Telegram display.

        Returns
        -------
        str
            Formatted status message
        """
        status = self.get_status()

        state_emoji = {
            'ACTIVE': '🟢',
            'REDUCED': '🟡',
            'HALTED': '🔴',
            'COOLDOWN': '⏸️',
        }.get(status['trading_state'], '⚪')

        msg = f"""📊 *风险状态*

{state_emoji} 状态: {status['trading_state']}
"""
        if status['halt_reason']:
            msg += f"⚠️ 原因: {status['halt_reason']}\n"

        msg += f"""
💰 账户: ${status['current_equity']:,.2f} (峰值: ${status['peak_equity']:,.2f})
📉 回撤: {status['drawdown_pct']:.1f}%
📅 今日盈亏: {status['daily_pnl_pct']:+.1f}%
🔢 连续亏损: {status['consecutive_losses']}次
⚖️ 仓位系数: {status['position_multiplier']:.1f}x
"""
        return msg

    def calculate_var(self, confidence: float = 0.95) -> float:
        """
        Historical VaR at given confidence level.

        Returns the loss threshold that is exceeded with (1-confidence) probability.
        Requires at least 10 trades for statistical significance.

        Parameters
        ----------
        confidence : float
            Confidence level (default 0.95 = 95%)

        Returns
        -------
        float
            VaR as a positive percentage (e.g., 2.5 means 2.5% loss threshold)
        """
        if len(self.trade_history) < 10:
            return 0.0
        pnl_pcts = [t.pnl_pct for t in self.trade_history]
        sorted_pnl = sorted(pnl_pcts)
        index = int(len(sorted_pnl) * (1 - confidence))
        return abs(sorted_pnl[max(0, index)])

    def calculate_cvar(self, confidence: float = 0.95) -> float:
        """
        Conditional VaR (Expected Shortfall) — average loss beyond VaR.

        More conservative than VaR, captures tail risk by averaging all losses
        in the worst (1-confidence) percentile.

        Parameters
        ----------
        confidence : float
            Confidence level (default 0.95 = 95%)

        Returns
        -------
        float
            CVaR as a positive percentage
        """
        if len(self.trade_history) < 10:
            return 0.0
        pnl_pcts = [t.pnl_pct for t in self.trade_history]
        sorted_pnl = sorted(pnl_pcts)
        cutoff = int(len(sorted_pnl) * (1 - confidence))
        if cutoff <= 0:
            return abs(sorted_pnl[0]) if sorted_pnl else 0.0
        tail = sorted_pnl[:cutoff]
        return abs(sum(tail) / len(tail)) if tail else 0.0

    def get_risk_summary(self) -> Dict[str, Any]:
        """
        Get comprehensive risk summary including VaR/CVaR metrics.

        Returns
        -------
        Dict[str, Any]
            {
                'var_95': float,          # 95% Historical VaR (positive pct)
                'cvar_95': float,         # 95% CVaR / Expected Shortfall (positive pct)
                'current_regime': str,    # Current market regime or None
                'trading_state': str,     # ACTIVE/REDUCED/HALTED/COOLDOWN
                'effective_thresholds': { # Currently active thresholds
                    'dd_reduce_pct': float,
                    'dd_halt_pct': float,
                    'daily_loss_pct': float,
                    'max_consecutive_losses': int,
                },
                'trade_count': int,       # Total trades in history
            }
        """
        return {
            'var_95': round(self.calculate_var(0.95) * 100, 4),
            'cvar_95': round(self.calculate_cvar(0.95) * 100, 4),
            'current_regime': self._current_regime,
            'trading_state': self.metrics.trading_state.value,
            'effective_thresholds': {
                'dd_reduce_pct': round(self.dd_reduce_threshold * 100, 2),
                'dd_halt_pct': round(self.dd_halt_threshold * 100, 2),
                'daily_loss_pct': round(self.dl_max_loss_pct * 100, 2),
                'max_consecutive_losses': self.cl_max_losses,
            },
            'trade_count': len(self.trade_history),
        }

    def reset(self):
        """Reset all risk metrics (use with caution)."""
        self.metrics = RiskMetrics()
        self.trade_history = []
        self.daily_start_equity = 0.0
        self.daily_reset_date = None
        self.cooldown_until = None
        self.consecutive_wins = 0
        self.logger.warning("RiskController reset - all metrics cleared")


def calculate_atr_position_size(
    account_equity: float,
    risk_per_trade_pct: float,
    current_atr: float,
    atr_multiplier: float,
    current_price: float,
    risk_multiplier: float = 1.0,
    max_position_pct: float = 0.30,
    min_notional_usdt: float = 100.0,
) -> Tuple[float, Dict[str, Any]]:
    """
    Calculate position size based on ATR (Average True Range).

    Formula: Position Size = (Equity × Risk%) / (ATR × Multiplier)

    Parameters
    ----------
    account_equity : float
        Total account equity in USDT
    risk_per_trade_pct : float
        Risk per trade as decimal (e.g., 0.01 = 1%)
    current_atr : float
        Current ATR value in price terms
    atr_multiplier : float
        Multiplier for stop loss distance (e.g., 2.0 = 2×ATR)
    current_price : float
        Current price for BTC conversion
    risk_multiplier : float, optional
        Additional multiplier from RiskController (0.0-1.0)
    max_position_pct : float, optional
        Maximum position as percentage of equity
    min_notional_usdt : float, optional
        Minimum notional value (Binance requires $100)

    Returns
    -------
    Tuple[float, Dict]
        (btc_quantity, calculation_details)
    """
    # Guard against invalid inputs - return zero position with details
    if current_price <= 0 or account_equity <= 0:
        return 0.0, {
            'method': 'atr_based',
            'error': 'Invalid input: price or equity <= 0',
            'account_equity': account_equity,
            'current_price': current_price,
            'btc_quantity': 0.0,
            'actual_notional': 0.0,
        }

    # Calculate dollar risk
    dollar_risk = account_equity * risk_per_trade_pct

    # Calculate stop distance
    stop_distance = current_atr * atr_multiplier

    # Prevent division by zero - fallback to 2% of price
    if stop_distance <= 0:
        stop_distance = current_price * 0.02

    # Calculate position size in USDT
    # Formula: risk_amount / (stop_distance_pct) = risk_amount / (stop_distance / price)
    position_usdt = dollar_risk / (stop_distance / current_price)

    # Apply risk multiplier from RiskController
    position_usdt *= risk_multiplier

    # Apply max position limit
    max_usdt = account_equity * max_position_pct
    position_usdt = min(position_usdt, max_usdt)

    # Apply minimum notional
    if position_usdt < min_notional_usdt and position_usdt > 0:
        position_usdt = min_notional_usdt

    # Convert to BTC
    btc_quantity = position_usdt / current_price if current_price > 0 else 0

    # Round to 3 decimal places (Binance precision)
    btc_quantity = round(btc_quantity, 3)

    # Recalculate actual notional
    actual_notional = btc_quantity * current_price

    details = {
        'method': 'atr_based',
        'account_equity': account_equity,
        'risk_per_trade_pct': risk_per_trade_pct,
        'dollar_risk': dollar_risk,
        'current_atr': current_atr,
        'atr_multiplier': atr_multiplier,
        'stop_distance': stop_distance,
        'stop_distance_pct': stop_distance / current_price * 100,
        'risk_multiplier': risk_multiplier,
        'position_usdt': position_usdt,
        'max_usdt': max_usdt,
        'btc_quantity': btc_quantity,
        'actual_notional': actual_notional,
    }

    return btc_quantity, details
