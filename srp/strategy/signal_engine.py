"""
SRP v6.1 Signal Engine — Pure Function, Zero NT Dependency

1:1 translation of srp/docs/v6.pine Sections 0-6.
Input: OHLCV bar data + equity + trade_dir
Output: SRPSignal dataclass

This module can be used in:
  - NautilusTrader Strategy (srp/strategy/srp_strategy.py)
  - Standalone backtest (srp/scripts/backtest.py)
  - Parameter optimization (srp/scripts/optimize.py)
  - Parity verification (srp/scripts/parity_check.py)
  - pytest unit tests

NO imports from nautilus_trader. NO network calls. NO side effects.
"""

import math
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime, timezone

from srp.strategy.pine_indicators import (
    pine_rsi,
    pine_vwma,
    pine_mfi,
    pine_rsi_mfi,
)


# =============================================================================
# Data Types
# =============================================================================

@dataclass
class SRPSignal:
    """Output of signal engine — one per bar."""
    action: str              # HOLD | ENTRY_LONG | ENTRY_SHORT | DCA_LONG | DCA_SHORT
                             # | EXIT_SL | EXIT_CYCLE | EXIT_TP | EXIT_BAND
    quantity: float = 0.0    # Suggested qty in coins (0 for HOLD/EXIT)
    price: float = 0.0       # Current close
    tp_target: float = 0.0   # Take-profit target price
    sl_price: float = 0.0    # Stop-loss price
    reason: str = ""         # Human-readable reason
    # State snapshot for external consumers (Webhook, Data Window)
    v_avg: float = 0.0
    v_count: int = 0
    deal_dir: int = 0        # 1=long, -1=short, 0=flat
    socounter: int = 0
    dealcount: int = 0
    rsi_mfi: float = 0.0
    dd_pct: float = 0.0      # Current drawdown % (for SL)


@dataclass
class SRPState:
    """All persistent (var) variables from Pine.

    Python equivalent of Pine's `var` declarations.
    Serializable to JSON for state persistence.
    """
    deal_dir: int = 0
    socounter: int = 0
    dealcount: int = 0
    deal_base: float = 0.0
    deal_start_bar: int = 0

    # Position tracking (mirrors Pine strategy.position_*)
    avg_price: float = 0.0
    total_qty: float = 0.0
    total_cost: float = 0.0
    opentrades: int = 0
    entry_prices: List[float] = field(default_factory=list)

    # Virtual DCA (Pine: var v_*)
    v_qty: float = 0.0
    v_cost: float = 0.0
    v_avg: float = 0.0
    v_last_px: float = 0.0
    v_count: int = 0

    # Statistics
    peak_equity: float = 0.0
    max_drawdown_pct: float = 0.0
    exit_tp_count: int = 0
    exit_band_count: int = 0
    exit_sl_count: int = 0
    exit_cycle_count: int = 0
    closed_deal_count: int = 0
    consec_losses: int = 0
    max_consec_losses: int = 0

    # Last exit info
    last_exit_bar: int = -1
    last_exit_type: str = ""


@dataclass
class SRPConfig:
    """Strategy parameters — loaded from srp/configs/srp.yaml."""
    # Signal
    src_type: str = "HLC3"
    vwma_length: int = 14
    srp_pct: float = 1.0
    rsi_period: int = 7
    mfi_period: int = 7
    rsi_mfi_below: float = 55.0
    rsi_mfi_above: float = 95.0

    # Position sizing
    base_order_pct: float = 0.10      # 10% as decimal
    mintp: float = 0.025              # 2.5%
    mintp_protection: bool = True

    # DCA
    max_dca_count: int = 4
    dca_multiplier: float = 1.5
    dca_min_change_pct: float = 3.0
    dca_type: str = "volume_multiply"

    # Virtual DCA
    vdca_enabled: bool = True
    vdca_max: int = 10

    # Risk
    max_loss_pct: float = 0.06        # 6%

    # Cycle
    cycle_enabled: bool = True
    cycle_dates: List[dict] = field(default_factory=lambda: [
        {"top": "2013-11-30", "bottom": "2015-01-14"},
        {"top": "2017-12-17", "bottom": "2018-12-15"},
        {"top": "2021-11-10", "bottom": "2022-11-21"},
        {"top": "2025-10-06", "bottom": "2026-10-06"},
    ])

    # Execution
    commission_pct: float = 0.00075   # 0.075% per side
    mintick: float = 0.1              # BTCUSDT price tick

    @property
    def sonum(self) -> int:
        """Pine: sonum = max_dca_count + 1 (includes base entry)."""
        return self.max_dca_count + 1


# =============================================================================
# Signal Engine
# =============================================================================

class SRPSignalEngine:
    """Pure signal engine — input OHLCV, output SRPSignal.

    Mirrors Pine v6.1 Sections 0-6 execution order:
      0. Max drawdown tracking
      1. Safety Net (SL)
      2. Cycle switch
      3. TP / Band exit
      3.5. Centralized state reset
      4. Virtual DCA
      5. State sync
      6. Entry (base / DCA)
    """

    def __init__(self, config: SRPConfig):
        self.cfg = config
        self.state = SRPState()

        # Indicator buffers
        self._closes: List[float] = []
        self._highs: List[float] = []
        self._lows: List[float] = []
        self._volumes: List[float] = []
        self._hlc3: List[float] = []

        # Incremental RSI state (Wilder's RMA — O(1) per bar instead of O(n))
        self._rsi_avg_gain: float = 0.0
        self._rsi_avg_loss: float = 0.0
        self._rsi_initialized: bool = False
        self._rsi_warmup_gains: List[float] = []
        self._rsi_warmup_losses: List[float] = []

        # Current bar index
        self._bar_index: int = -1

        # Cycle date ranges (parsed once)
        self._bear_ranges = self._parse_cycle_dates()

    def _parse_cycle_dates(self) -> List[tuple]:
        """Parse cycle_dates config into (top_ts, bottom_ts) pairs."""
        ranges = []
        for cd in self.cfg.cycle_dates:
            top = datetime.strptime(cd["top"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            bottom = datetime.strptime(cd["bottom"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            ranges.append((top.timestamp() * 1000, bottom.timestamp() * 1000))
        return ranges

    def _is_bear(self, ts_ms: int) -> bool:
        """Check if timestamp falls in a bear market range."""
        if not self.cfg.cycle_enabled:
            return False
        for top_ms, bottom_ms in self._bear_ranges:
            if top_ms <= ts_ms < bottom_ms:
                return True
        return False

    # =========================================================================
    # Public API — one method, one bar, one signal
    # =========================================================================

    def on_bar(self, o: float, h: float, l: float, c: float, v: float,
               equity: float, ts_ms: int) -> SRPSignal:
        """Process one bar and return a signal.

        Args:
            o, h, l, c, v: OHLCV data
            equity: Current account equity in USDT
            ts_ms: Bar timestamp in milliseconds (for cycle detection)

        Returns:
            SRPSignal with action and metadata
        """
        self._bar_index += 1
        bi = self._bar_index
        s = self.state
        cfg = self.cfg

        # Append to buffers
        self._closes.append(c)
        self._highs.append(h)
        self._lows.append(l)
        self._volumes.append(v)

        # Source price (Pine: src)
        if cfg.src_type == "Close":
            src = c
        elif cfg.src_type == "OHLC4":
            src = (o + h + l + c) / 4.0
        elif cfg.src_type == "HL2":
            src = (h + l) / 2.0
        else:  # HLC3
            src = (h + l + c) / 3.0
        self._hlc3.append(src)

        # Warmup
        min_bars = cfg.vwma_length + 5
        if len(self._closes) < min_bars:
            return SRPSignal("HOLD", price=c, reason=f"warmup {len(self._closes)}/{min_bars}")

        # === Indicators ===
        vwma = pine_vwma(self._hlc3, self._volumes, cfg.vwma_length, bi)
        upper = vwma * (1.0 + cfg.srp_pct / 100.0)
        lower = vwma * (1.0 - cfg.srp_pct / 100.0)

        rsi_val = self._update_rsi_incremental(c, cfg.rsi_period)
        mfi_val = pine_mfi(self._hlc3, self._volumes, cfg.mfi_period, bi)
        rsi_mfi = pine_rsi_mfi(rsi_val, mfi_val)

        sig_long = l <= lower and rsi_mfi < cfg.rsi_mfi_below
        sig_short = h >= upper and rsi_mfi > cfg.rsi_mfi_above

        # Cycle direction
        in_bear = self._is_bear(ts_ms)
        trade_dir = -1 if in_bear else 1
        entry_signal = sig_long if trade_dir == 1 else sig_short

        # Exit band signal (Pine L223: deal_dir==1 ? sig_short : sig_long)
        exit_band_signal = sig_short if s.deal_dir == 1 else sig_long

        # TP target (Pine L265)
        tp_target = 0.0
        if s.v_avg > 0:
            tp_target = s.v_avg * (1 - cfg.mintp) if s.deal_dir == -1 else s.v_avg * (1 + cfg.mintp)

        # SL price
        sl_price = 0.0
        if s.avg_price > 0 and s.deal_dir != 0:
            sl_price = s.avg_price * (1 + cfg.max_loss_pct) if s.deal_dir == -1 else s.avg_price * (1 - cfg.max_loss_pct)

        # Helper: build signal with current state snapshot
        def _sig(action, qty=0.0, reason="", dd=0.0):
            return SRPSignal(
                action=action, quantity=qty, price=c,
                tp_target=tp_target, sl_price=sl_price, reason=reason,
                v_avg=s.v_avg, v_count=s.v_count, deal_dir=s.deal_dir,
                socounter=s.socounter, dealcount=s.dealcount,
                rsi_mfi=rsi_mfi, dd_pct=dd,
            )

        # === 0. Max drawdown tracking (Pine L343-347) ===
        if equity > s.peak_equity:
            s.peak_equity = equity
        if s.peak_equity > 0:
            dd = (s.peak_equity - equity) / s.peak_equity * 100
            if dd > s.max_drawdown_pct:
                s.max_drawdown_pct = dd

        # Flag: need_reset after exit (Pine FIX-G1)
        need_reset = False

        # === 1. Safety Net — SL (Pine L350-371) ===
        if (bi != s.last_exit_bar and s.opentrades > 0 and s.avg_price > 0):
            if s.deal_dir == -1:
                dd = (c - s.avg_price) / s.avg_price
            else:
                dd = (s.avg_price - c) / s.avg_price
            if dd >= cfg.max_loss_pct:
                signal = _sig("EXIT_SL", reason=f"SL ({dd*100:.1f}%)", dd=dd*100)
                s.exit_sl_count += 1
                s.consec_losses += 1
                if s.consec_losses > s.max_consec_losses:
                    s.max_consec_losses = s.consec_losses
                s.closed_deal_count += 1
                s.last_exit_type = "止损"
                s.deal_dir = 0
                s.last_exit_bar = bi
                need_reset = True
                if need_reset:
                    self._reset_virtual_state()
                return signal

        # === 2. Cycle switch (Pine L374-397) ===
        if (bi != s.last_exit_bar and s.opentrades > 0
                and s.deal_dir != 0 and s.deal_dir != trade_dir):
            signal = _sig("EXIT_CYCLE", reason="cycle switch")
            s.exit_cycle_count += 1
            # PnL check for consec_losses
            pnl = (s.avg_price - c) if s.deal_dir == -1 else (c - s.avg_price)
            if pnl < 0:
                s.consec_losses += 1
                if s.consec_losses > s.max_consec_losses:
                    s.max_consec_losses = s.consec_losses
            else:
                s.consec_losses = 0
            s.closed_deal_count += 1
            s.last_exit_type = "周期切换"
            s.deal_dir = 0
            s.last_exit_bar = bi
            self._reset_virtual_state()
            return signal

        # === 3. TP / Band exit (Pine L400-472) ===
        if bi != s.last_exit_bar and s.opentrades > 0 and s.v_avg > 0:
            tp_hit = (c < tp_target) if s.deal_dir == -1 else (c > tp_target)
            if tp_hit:
                signal = _sig("EXIT_TP", reason=f"TP v{s.v_count}")
                s.exit_tp_count += 1
                s.consec_losses = 0
                s.closed_deal_count += 1
                s.last_exit_type = "止盈"
                s.deal_dir = 0
                s.last_exit_bar = bi
                self._reset_virtual_state()
                return signal
            else:
                in_profit = (c < s.avg_price) if s.deal_dir == -1 else (c > s.avg_price)
                if exit_band_signal and (in_profit if cfg.mintp_protection else True):
                    signal = _sig("EXIT_BAND", reason="band exit")
                    s.exit_band_count += 1
                    pnl = (s.avg_price - c) if s.deal_dir == -1 else (c - s.avg_price)
                    if pnl < 0:
                        s.consec_losses += 1
                        if s.consec_losses > s.max_consec_losses:
                            s.max_consec_losses = s.consec_losses
                    else:
                        s.consec_losses = 0
                    s.closed_deal_count += 1
                    s.last_exit_type = "触轨"
                    s.deal_dir = 0
                    s.last_exit_bar = bi
                    self._reset_virtual_state()
                    return signal

        elif bi != s.last_exit_bar and s.opentrades > 0 and s.v_avg == 0:
            # Edge case: first bar after entry, v_avg not yet set
            in_profit = (c < s.avg_price) if s.deal_dir == -1 else (c > s.avg_price)
            if exit_band_signal and (in_profit if cfg.mintp_protection else True):
                signal = _sig("EXIT_BAND", reason="band exit (first bar)")
                s.exit_band_count += 1
                pnl = (s.avg_price - c) if s.deal_dir == -1 else (c - s.avg_price)
                if pnl < 0:
                    s.consec_losses += 1
                    if s.consec_losses > s.max_consec_losses:
                        s.max_consec_losses = s.consec_losses
                else:
                    s.consec_losses = 0
                s.closed_deal_count += 1
                s.last_exit_type = "触轨"
                s.deal_dir = 0
                s.last_exit_bar = bi
                self._reset_virtual_state()
                return signal

        # === 4. Virtual DCA (Pine L489-499) ===
        if (cfg.vdca_enabled and cfg.sonum >= 1
                and s.opentrades > 0 and s.opentrades >= cfg.sonum
                and s.deal_dir == trade_dir
                and s.v_count < cfg.vdca_max
                and self._vdca_conditions(c, s.deal_dir)):
            vdca_qty = s.v_qty * cfg.dca_multiplier
            s.v_cost += vdca_qty * c
            s.v_qty += vdca_qty
            s.v_avg = s.v_cost / s.v_qty
            s.v_last_px = c
            s.v_count += 1

        # === 5. State sync (Pine L502-512) ===
        if s.opentrades == 0 and s.v_qty > 0:
            self._reset_virtual_state()
            s.deal_dir = 0

        # === 6. Entry (Pine L515-557) ===

        # 6a. New entry
        if (bi != s.last_exit_bar and entry_signal
                and s.opentrades == 0):
            s.socounter = 0
            s.dealcount += 1
            s.deal_start_bar = bi
            s.deal_dir = trade_dir
            s.deal_base = equity * cfg.base_order_pct
            qty = s.deal_base / c

            s.v_qty = qty
            s.v_cost = qty * c
            s.v_avg = c
            s.v_last_px = c
            s.v_count = 0

            action = "ENTRY_LONG" if trade_dir == 1 else "ENTRY_SHORT"
            return _sig(action, qty=qty, reason=f"deal #{s.dealcount}")

        # 6b. DCA entry
        if (bi != s.last_exit_bar and entry_signal
                and s.opentrades > 0 and s.opentrades < cfg.sonum
                and s.deal_dir == trade_dir
                and self._so_conditions(c, s.deal_dir)):
            s.socounter += 1
            if cfg.dca_type == "volume_multiply":
                dca_qty = s.total_qty * cfg.dca_multiplier
            else:
                dca_qty = (s.deal_base * (cfg.dca_multiplier ** s.socounter)) / c

            # Sync virtual state (real DCA affects v_avg but NOT v_count — FIX-2)
            s.v_cost += dca_qty * c
            s.v_qty += dca_qty
            s.v_avg = s.v_cost / s.v_qty
            s.v_last_px = c

            action = "DCA_LONG" if s.deal_dir == 1 else "DCA_SHORT"
            return _sig(action, qty=dca_qty, reason=f"DCA #{s.socounter}")

        # No signal
        return _sig("HOLD", reason="no signal")

    # =========================================================================
    # DCA Conditions — exact Pine replication
    # =========================================================================

    def _update_rsi_incremental(self, close: float, period: int) -> float:
        """Incremental Wilder's RSI — O(1) per bar.

        Exact same output as pine_rsi() but without recalculating entire series.
        Pine: ta.rma = SMA seed for first `period` bars, then alpha smoothing.
        """
        n = len(self._closes)
        if n < 2:
            return 50.0

        change = self._closes[-1] - self._closes[-2]
        gain = max(change, 0.0)
        loss = max(-change, 0.0)

        if not self._rsi_initialized:
            self._rsi_warmup_gains.append(gain)
            self._rsi_warmup_losses.append(loss)
            if len(self._rsi_warmup_gains) >= period:
                # SMA seed (matches pine_rma)
                self._rsi_avg_gain = sum(self._rsi_warmup_gains[:period]) / period
                self._rsi_avg_loss = sum(self._rsi_warmup_losses[:period]) / period
                self._rsi_initialized = True
                # Apply remaining warmup values
                alpha = 1.0 / period
                for i in range(period, len(self._rsi_warmup_gains)):
                    self._rsi_avg_gain = alpha * self._rsi_warmup_gains[i] + (1 - alpha) * self._rsi_avg_gain
                    self._rsi_avg_loss = alpha * self._rsi_warmup_losses[i] + (1 - alpha) * self._rsi_avg_loss
                self._rsi_warmup_gains = []
                self._rsi_warmup_losses = []
            else:
                return 50.0
        else:
            # Wilder's smoothing: alpha = 1/period
            alpha = 1.0 / period
            self._rsi_avg_gain = alpha * gain + (1 - alpha) * self._rsi_avg_gain
            self._rsi_avg_loss = alpha * loss + (1 - alpha) * self._rsi_avg_loss

        if self._rsi_avg_loss == 0:
            return 100.0
        if self._rsi_avg_gain == 0:
            return 0.0
        return 100.0 - (100.0 / (1.0 + self._rsi_avg_gain / self._rsi_avg_loss))

    def _so_conditions(self, close: float, dir: int) -> bool:
        """Pine: SOconditions(dir) — real DCA trigger.

        [FIX-B3] Dual-base design:
          chg = |close - last_entry| / close × 100  (denominator = current price)
          next_so = avg_price ± spacing% × avg_price (base = avg price)
          Both must be true (AND). Conservative by design.
        """
        s = self.state
        if not s.entry_prices:
            return False
        last_deal = s.entry_prices[-1]
        if last_deal <= 0:
            return False
        chg = abs((close - last_deal) / close) * 100.0

        # calcNextSOprice
        if s.avg_price <= 0:
            return False
        offset = round(self.cfg.dca_min_change_pct / 100 * s.avg_price / self.cfg.mintick) * self.cfg.mintick
        if dir == 1:
            next_so = s.avg_price - offset
            return chg > self.cfg.dca_min_change_pct and close < next_so
        else:
            next_so = s.avg_price + offset
            return chg > self.cfg.dca_min_change_pct and close > next_so

    def _vdca_conditions(self, close: float, dir: int) -> bool:
        """Pine: vSOconditions(dir) — virtual DCA trigger."""
        s = self.state
        if s.v_last_px <= 0:
            return False
        chg = abs((close - s.v_last_px) / s.v_last_px) * 100.0
        if dir == 1:
            return chg > self.cfg.dca_min_change_pct and close < s.v_last_px
        else:
            return chg > self.cfg.dca_min_change_pct and close > s.v_last_px

    # =========================================================================
    # State Management
    # =========================================================================

    def _reset_virtual_state(self):
        """Pine: centralized reset (FIX-G1, Section 3.5).

        In Python this is a simple method call — no flag pattern needed.
        """
        s = self.state
        s.v_qty = 0.0
        s.v_cost = 0.0
        s.v_avg = 0.0
        s.v_last_px = 0.0
        s.v_count = 0
        s.socounter = 0

    def update_fill(self, fill_price: float, fill_qty: float, is_close: bool = False):
        """Called by NT Strategy when an order fills.

        Updates position state to match actual exchange state.
        This replaces Pine's inline state updates in entry/close blocks.
        """
        s = self.state
        if is_close:
            s.opentrades = 0
            s.avg_price = 0.0
            s.total_qty = 0.0
            s.total_cost = 0.0
            s.entry_prices = []
        else:
            s.opentrades += 1
            s.entry_prices.append(fill_price)
            s.total_qty += fill_qty
            s.total_cost += fill_qty * fill_price
            s.avg_price = s.total_cost / s.total_qty

    def reset_state(self):
        """Full state reset (called when position confirmed closed)."""
        self.state = SRPState(
            peak_equity=self.state.peak_equity,
            max_drawdown_pct=self.state.max_drawdown_pct,
            exit_tp_count=self.state.exit_tp_count,
            exit_band_count=self.state.exit_band_count,
            exit_sl_count=self.state.exit_sl_count,
            exit_cycle_count=self.state.exit_cycle_count,
            closed_deal_count=self.state.closed_deal_count,
            consec_losses=self.state.consec_losses,
            max_consec_losses=self.state.max_consec_losses,
            dealcount=self.state.dealcount,
        )

    def get_state_dict(self) -> dict:
        """Serialize state for JSON persistence."""
        from dataclasses import asdict
        return asdict(self.state)

    def load_state_dict(self, d: dict):
        """Restore state from JSON."""
        for k, v in d.items():
            if hasattr(self.state, k):
                setattr(self.state, k, v)
