"""
SRP Strategy v5.0 for NautilusTrader

100% parity with docs/SRP_strategy_v5.pine.
Verified via scripts/pine_tv_comparator.py (PERFECT PARITY).

Strategy logic:
  Entry: price <= VWMA lower band AND RSI-MFI < 55 → LONG
  DCA:   up to 4 layers, spacing 3%, qty = position × 1.5
  Virtual DCA: after real DCA exhausted, virtual averaging
  Exit:  TP (virtual_avg × 1.025) | Band (short signal + profit) | SL (6%)
  Sync:  reset virtual state when flat

Execution order per bar (matches Pine):
  1. Safety Net (SL check)
  2. TP / Band exit
  3. Virtual DCA
  4. Entry (base / DCA)
  5. Sync
"""

import os
import json
import logging
import threading
import requests
from typing import Dict, Any, List, Optional
from collections import deque
from datetime import datetime, timezone, timedelta

from nautilus_trader.config import StrategyConfig
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce, TriggerType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument


# =============================================================================
# Configuration — matches Pine v5.0 defaults
# =============================================================================

class SRPStrategyConfig(StrategyConfig, frozen=True):
    instrument_id: str = "BTCUSDT-PERP.BINANCE"
    bar_type: str = "BTCUSDT-PERP.BINANCE-30-MINUTE-LAST-EXTERNAL"

    # SRP Core (Pine: SRP Settings)
    vwma_length: int = 14
    srp_pct: float = 1.0
    rsi_mfi_below: float = 55.0
    rsi_mfi_above: float = 100.0
    rsi_mfi_period: int = 7

    # Position Sizing (Pine: Base %)
    sizing_mode: str = "percent"       # "percent" or "fixed"
    base_order_pct: float = 10.0       # % of equity (sizing_mode="percent")
    base_order_usdt: float = 100.0     # USDT (sizing_mode="fixed")

    # DCA (Pine: Strategy Settings)
    dca_multiplier: float = 1.5        # Pine: DCA Multi
    max_dca_count: int = 4             # Pine: DCA Count
    dca_min_change_pct: float = 3.0    # Pine: DCA Spacing %
    dca_type: str = "volume_multiply"  # Pine: DCA Type

    # Exit (Pine: TP% + Safety Net)
    mintp: float = 0.025               # Pine: TP 2.5%
    max_portfolio_loss_pct: float = 0.06  # Pine: Max Loss 6%

    # Telegram
    enable_telegram: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Timing
    timer_interval_sec: int = 1200

    # Short (disabled — Pine v5.0 is long-only)
    short_enabled: bool = False


# =============================================================================
# MINTICK fallback for next-SO price calculation (overridden by instrument)
# =============================================================================

MINTICK_DEFAULT = 0.1


# =============================================================================
# Strategy Implementation
# =============================================================================

class SRPStrategy(Strategy):
    """SRP v5.0 — VWMA + RSI-MFI Mean Reversion with DCA (Long only)."""

    def __init__(self, config: SRPStrategyConfig):
        super().__init__(config)

        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)
        self.instrument: Optional[Instrument] = None

        # Indicator buffers — must be large enough for Wilder's RMA convergence
        # RSI period 7 needs ~200+ bars for stable values. 500 gives full convergence.
        buf = 500
        self._closes: deque = deque(maxlen=buf)
        self._highs: deque = deque(maxlen=buf)
        self._lows: deque = deque(maxlen=buf)
        self._volumes: deque = deque(maxlen=buf)

        # Indicators
        self._vwma: float = 0.0
        self._upper_band: float = 0.0
        self._lower_band: float = 0.0
        self._rsi_mfi: float = 50.0

        # Position state (Pine: strategy.position_size, opentrades, etc.)
        self._side: Optional[str] = None  # "LONG" or None
        self._dca_count: int = 0          # = Pine opentrades
        self._dca_entries: List[Dict] = []
        self._avg_price: float = 0.0      # = Pine position_avg_price
        self._total_quantity: float = 0.0
        self._total_cost: float = 0.0
        self._deal_base: float = 0.0      # base USDT for current deal
        self._dealcount: int = 0
        self._socounter: int = 0

        # Virtual DCA state (Pine: var v_qty, v_cost, v_avg, v_last_px, v_count)
        self._v_qty: float = 0.0
        self._v_cost: float = 0.0
        self._v_avg: float = 0.0
        self._v_last_px: float = 0.0
        self._v_count: int = 0

        # Async close tracking (NT event-driven pattern)
        self._pending_close_reason: Optional[str] = None

        # Backtest trade tracking (independent of NT positions_report)
        self._completed_trades: List[Dict] = []

        # Pine parity mode: pending order queue (backtest only)
        # Pine fills at NEXT bar open; NT fills immediately.
        # Queue orders on signal bar, execute at next bar's open via LIMIT order.
        self._bt_pending_close: Optional[str] = None     # close reason
        self._bt_pending_entry: Optional[Dict] = None     # {qty, label}
        self._pine_parity: bool = False  # set in on_start

        # Server-side SL order tracking (live mode)
        self._sl_order_id: Optional[str] = None

        # Persistence
        self._data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
        self._state_file = os.path.join(self._data_dir, "srp_state.json")

    # =========================================================================
    # Environment Detection
    # =========================================================================

    def _is_backtest(self) -> bool:
        """Detect if running inside BacktestEngine (vs live/sandbox)."""
        try:
            clock_type = type(self.clock).__name__
            return "TestClock" in clock_type or "Backtest" in clock_type
        except Exception:
            return False

    # =========================================================================
    # Helpers
    # =========================================================================

    def _get_equity(self) -> float:
        """Get current account equity for position sizing."""
        try:
            account = self.portfolio.account(self.instrument_id.venue)
            if account:
                balances = account.balances()
                for currency, balance in balances.items():
                    if str(currency) == "USDT":
                        return float(balance.total)
        except Exception:
            pass
        return self.config.base_order_usdt * 10  # Fallback

    def _get_base_usdt(self) -> float:
        """Calculate base order in USDT. Pine: strategy.equity × base_pct"""
        if self.config.sizing_mode == "percent":
            equity = self._get_equity()
            base = equity * self.config.base_order_pct / 100.0
            if not self._pine_parity:
                return max(base, 105.0)  # Binance min $100 + buffer (live only)
            return base  # Pine has no minimum
        return self.config.base_order_usdt

    def _calc_next_so_price(self, pcnt: float) -> float:
        """Pine: calcNextSOprice — next DCA trigger based on avg price."""
        if self._avg_price <= 0:
            return 0.0
        mintick = float(self.instrument.price_increment) if self.instrument else MINTICK_DEFAULT
        return self._avg_price - round(
            pcnt / 100 * self._avg_price / mintick
        ) * mintick

    def _process_pending_orders(self, open_price: float):
        """Pine parity: execute queued orders at next bar (1-bar delay).

        Matches Pine's broker emulator: orders signal on bar N, fill bar N+1.
        Uses MARKET orders (LIMIT rejected by BacktestEngine reduce_only).
        Fill price = BacktestEngine's matching price (close to open).
        """

        # 1. Close fills first (Pine: strategy.close fills before strategy.entry)
        if self._bt_pending_close is not None:
            reason = self._bt_pending_close
            self._bt_pending_close = None

            if self._side is not None and self._total_quantity > 0:
                # Use actual NT position quantity for close
                sell_qty = self._total_quantity
                try:
                    positions = self.cache.positions(venue=self.instrument_id.venue)
                    for pos in positions:
                        if pos.instrument_id == self.instrument_id and not pos.is_closed:
                            sell_qty = float(pos.quantity)
                            break
                except Exception:
                    pass

                try:
                    order = self.order_factory.market(
                        instrument_id=self.instrument_id,
                        order_side=OrderSide.SELL,
                        quantity=self.instrument.make_qty(sell_qty),
                        time_in_force=TimeInForce.GTC,
                        reduce_only=True,
                    )
                    self.submit_order(order)
                    self.log.info(f"SRP CLOSE (pine parity): {reason} @ bar open≈${open_price:.2f}")
                except Exception as e:
                    self.log.error(f"SRP pending close failed: {e}")

                # Wait for on_position_closed to reset state
                self._pending_close_reason = reason

        # 2. Entry fills after close (Pine: close fills first, then entries)
        if self._bt_pending_entry is not None:
            # If close was just submitted and position not yet flat,
            # defer entry to next bar (safety: avoid double position)
            if self._pending_close_reason is not None and self._side is not None:
                # Entry stays in queue for next bar
                return

            entry = self._bt_pending_entry
            self._bt_pending_entry = None
            qty = entry["qty"]
            label = entry["label"]

            try:
                order = self.order_factory.market(
                    instrument_id=self.instrument_id,
                    order_side=OrderSide.BUY,
                    quantity=self.instrument.make_qty(qty),
                    time_in_force=TimeInForce.GTC,
                    reduce_only=False,
                )
                self.submit_order(order)

                # Update position state (mirrors Pine's broker state update at fill)
                filled_qty = float(self.instrument.make_qty(qty))
                if self._side is None:
                    self._side = "LONG"
                self._dca_count += 1
                self._dca_entries.append({
                    "price": open_price,  # Track signal-time open price
                    "quantity": filled_qty,
                    "label": label,
                })
                self._total_quantity += filled_qty
                self._total_cost += filled_qty * open_price
                self._avg_price = self._total_cost / self._total_quantity

                self.log.info(f"SRP {label} (pine parity): @ bar open≈${open_price:.2f} "
                              f"qty={filled_qty:.8f} avg=${self._avg_price:.2f}")
            except Exception as e:
                self.log.error(f"SRP pending entry failed: {e}")

    def _calc_change_from_last(self, price: float) -> float:
        """Pine: calcChangeFromLastDeal — % change from last entry price."""
        if not self._dca_entries:
            return 0.0
        last = self._dca_entries[-1]["price"]
        if last <= 0:
            return 0.0
        return abs((price - last) / price) * 100.0

    # =========================================================================
    # Indicators — matches Pine exactly
    # =========================================================================

    def _calculate_indicators(self):
        closes = list(self._closes)
        highs = list(self._highs)
        lows = list(self._lows)
        volumes = list(self._volumes)
        n = len(closes)

        # VWMA(HLC3, length)
        length = self.config.vwma_length
        if n >= length:
            hlc3 = [(highs[i] + lows[i] + closes[i]) / 3.0 for i in range(n)]
            start = n - length
            num = sum(hlc3[start + i] * volumes[start + i] for i in range(length))
            den = sum(volumes[start + i] for i in range(length))
            self._vwma = num / den if den > 0 else closes[-1]
        else:
            self._vwma = closes[-1] if closes else 0

        pct = self.config.srp_pct / 100.0
        self._upper_band = self._vwma * (1.0 + pct)
        self._lower_band = self._vwma * (1.0 - pct)

        # RSI-MFI composite: abs(RSI + MFI/2)
        period = self.config.rsi_mfi_period
        if n >= period + 1:
            rsi = self._calc_rsi(closes, period)
            mfi = self._calc_mfi(highs, lows, closes, volumes, period)
            self._rsi_mfi = abs(rsi + mfi / 2.0)
        else:
            self._rsi_mfi = 50.0

    def _calc_rsi(self, closes: list, period: int) -> float:
        """Pine: ta.rma based RSI with Wilder's smoothing."""
        changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        if len(changes) < period:
            return 50.0
        # SMA seed
        gains = [max(c, 0) for c in changes[:period]]
        losses_v = [max(-c, 0) for c in changes[:period]]
        avg_gain = sum(gains) / period
        avg_loss = sum(losses_v) / period
        # Wilder's smoothing
        alpha = 1.0 / period
        for i in range(period, len(changes)):
            c = changes[i]
            avg_gain = alpha * max(c, 0) + (1 - alpha) * avg_gain
            avg_loss = alpha * max(-c, 0) + (1 - alpha) * avg_loss
        if avg_loss == 0:
            return 100.0
        if avg_gain == 0:
            return 0.0
        return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))

    def _calc_mfi(self, highs, lows, closes, volumes, period) -> float:
        """Pine: ta.mfi(hlc3, period)."""
        n = len(closes)
        if n < period + 1:
            return 50.0
        tp = [(highs[i] + lows[i] + closes[i]) / 3.0 for i in range(n)]
        pos, neg = 0.0, 0.0
        for i in range(n - period, n):
            mf = tp[i] * volumes[i]
            if tp[i] > tp[i - 1]:
                pos += mf
            elif tp[i] < tp[i - 1]:
                neg += mf
        if neg == 0:
            return 100.0
        if pos == 0:
            return 0.0
        return 100.0 - (100.0 / (1.0 + pos / neg))

    # =========================================================================
    # Lifecycle
    # =========================================================================

    def on_start(self):
        self.log.info("SRP v5.0 starting (Long only, Pine parity)...")
        self.instrument = self.cache.instrument(self.instrument_id)
        if self.instrument is None:
            self.log.error(f"Instrument {self.instrument_id} not found")
            return

        # Pine parity mode: match Pine's execution model in backtest.
        # Activated when BacktestEngine + high-precision instrument (size_precision > 3).
        # Production backtest uses Binance specs (size_precision=3) → pine_parity=False.
        is_high_precision = self.instrument.size_precision > 3 if self.instrument else False
        self._pine_parity = self._is_backtest() and is_high_precision
        if self._pine_parity:
            self.log.info("Pine parity mode: orders queue → fill at next bar open")
        elif self._is_backtest():
            self.log.info("Production backtest mode: Binance constraints, immediate MARKET fills")

        # Pre-fetch historical bars for LIVE mode only.
        # In BacktestEngine, bars are provided by the engine — prefetch would
        # pollute the buffer with CURRENT data during HISTORICAL backtest.
        if not self._is_backtest():
            self._prefetch_historical_bars(limit=500)

        self.subscribe_bars(self.bar_type)
        self._load_state()

        # P0: Reconcile state with actual exchange position on startup
        if not self._is_backtest():
            self._reconcile_position()

        self.clock.set_timer(
            name="srp_heartbeat",
            interval=timedelta(seconds=self.config.timer_interval_sec),
            callback=self._on_timer,
        )

        self._send_telegram(
            f"🟢 SRP v5.0 started (仅开多)\n"
            f"SRP: {self.config.srp_pct}% VWMA={self.config.vwma_length}\n"
            f"DCA: {self.config.max_dca_count}× spacing={self.config.dca_min_change_pct}% mult={self.config.dca_multiplier}\n"
            f"TP: {self.config.mintp*100:.1f}% SL: {self.config.max_portfolio_loss_pct*100:.0f}%"
        )

    def on_stop(self):
        self._save_state()
        self._send_telegram("🛑 SRP v5.0 stopped")

    # =========================================================================
    # Historical Bar Prefetch (from AI strategy pattern)
    # =========================================================================

    def _prefetch_historical_bars(self, limit: int = 100):
        """Pre-fetch historical bars from Binance to fill indicator buffers.

        Eliminates the 9.5h warmup wait (19 bars × 30 min).
        Same approach as AI strategy's _prefetch_historical_bars().
        """
        try:
            from nautilus_trader.core.datetime import millis_to_nanos

            symbol = str(self.instrument_id).split('-')[0]  # BTCUSDT-PERP.BINANCE → BTCUSDT
            bar_type_str = str(self.bar_type)
            interval = '30m'
            if '1-HOUR' in bar_type_str:
                interval = '1h'
            elif '4-HOUR' in bar_type_str:
                interval = '4h'

            self.log.info(f"📡 Pre-fetching {limit} historical bars ({symbol} {interval})...")

            url = "https://fapi.binance.com/fapi/v1/klines"
            resp = requests.get(url, params={
                'symbol': symbol, 'interval': interval, 'limit': min(limit, 1500)
            }, timeout=15)
            resp.raise_for_status()
            klines = resp.json()

            if not klines:
                self.log.warning("⚠️ No bars from Binance API")
                return

            # Strip last incomplete bar
            if len(klines) > 1:
                klines = klines[:-1]

            # Feed to indicator buffers (same as on_bar would)
            fed = 0
            for k in klines:
                self._closes.append(float(k[4]))
                self._highs.append(float(k[2]))
                self._lows.append(float(k[3]))
                self._volumes.append(float(k[5]))
                fed += 1

            # Calculate indicators with full buffer
            if len(self._closes) >= self.config.vwma_length:
                self._calculate_indicators()

            self.log.info(f"✅ Pre-fetched {fed} bars. Buffer: {len(self._closes)} bars. "
                         f"Indicators ready: VWMA={self._vwma:.1f} RSI-MFI={self._rsi_mfi:.1f}")

        except Exception as e:
            self.log.error(f"❌ Pre-fetch failed: {e}. Continuing with live bars only.")

    # =========================================================================
    # Bar Processing — ORDER MATCHES PINE EXACTLY
    # Pine: Safety → TP/Band → Virtual DCA → Entry → Sync
    # =========================================================================

    def on_bar(self, bar: Bar):
        close = float(bar.close)
        high = float(bar.high)
        low = float(bar.low)
        volume = float(bar.volume)
        open_price = float(bar.open)

        # === Pine parity: execute queued orders at bar OPEN (matches Pine broker) ===
        if self._pine_parity:
            self._process_pending_orders(open_price)

        self._closes.append(close)
        self._highs.append(high)
        self._lows.append(low)
        self._volumes.append(volume)

        n_bars = len(self._closes)
        min_bars = self.config.vwma_length + 5
        if n_bars < min_bars:
            if n_bars % 5 == 1:  # Log every 5 bars during warmup
                self.log.info(f"SRP warmup: {n_bars}/{min_bars} bars")
            return

        self._calculate_indicators()

        sig_long = (low <= self._lower_band) and (self._rsi_mfi < self.config.rsi_mfi_below)
        sig_short = (high >= self._upper_band) and (self._rsi_mfi > self.config.rsi_mfi_above)

        skip_entry = False
        sonum = self.config.max_dca_count + 1  # Pine: sonum = dca_count + 1

        # If a close is pending (submitted but not yet filled), skip everything
        # In pine_parity mode, _bt_pending_close serves this role instead
        if not self._pine_parity and getattr(self, '_pending_close_reason', None) is not None:
            return
        if self._pine_parity and self._bt_pending_close is not None:
            return

        # === 1. Safety Net (Pine L115-124) ===
        if self._side is not None and self._avg_price > 0:
            dd = (self._avg_price - close) / self._avg_price
            if dd >= self.config.max_portfolio_loss_pct:
                if self._pine_parity:
                    self._bt_pending_close = f"SL ({dd*100:.1f}%)"
                    # Pine resets v_* inline at signal time
                    self._v_qty = self._v_cost = self._v_avg = self._v_last_px = 0.0
                    self._v_count = 0
                    self._socounter = 0
                else:
                    self._submit_full_close(close, f"SL ({dd*100:.1f}%)")
                skip_entry = True

        # === 2. TP / Band (Pine L131-148) ===
        if not skip_entry and self._side is not None:
            if self._v_avg > 0:
                tp_target = self._v_avg * (1.0 + self.config.mintp)
                if close > tp_target:
                    if self._pine_parity:
                        self._bt_pending_close = f"TP (v{self._v_count})"
                        self._v_qty = self._v_cost = self._v_avg = self._v_last_px = 0.0
                        self._v_count = 0
                        self._socounter = 0
                    else:
                        self._submit_full_close(close, f"TP (v{self._v_count})")
                    skip_entry = True
                elif sig_short and close > self._avg_price:
                    if self._pine_parity:
                        self._bt_pending_close = "Band"
                        self._v_qty = self._v_cost = self._v_avg = self._v_last_px = 0.0
                        self._v_count = 0
                        self._socounter = 0
                    else:
                        self._submit_full_close(close, "Band")
                    skip_entry = True
            elif sig_short and close > self._avg_price:
                if self._pine_parity:
                    self._bt_pending_close = "Band"
                    self._v_qty = self._v_cost = self._v_avg = self._v_last_px = 0.0
                    self._v_count = 0
                    self._socounter = 0
                else:
                    self._submit_full_close(close, "Band")
                skip_entry = True

        # === 3. Virtual DCA (Pine L154-160) ===
        if (not skip_entry and self._side is not None
                and self._dca_count >= sonum and self._v_last_px > 0):
            chg = abs((close - self._v_last_px) / self._v_last_px) * 100
            if chg > self.config.dca_min_change_pct and close < self._v_last_px:
                vdca_qty = self._v_qty * self.config.dca_multiplier
                self._v_cost += vdca_qty * close
                self._v_qty += vdca_qty
                self._v_avg = self._v_cost / self._v_qty
                self._v_last_px = close
                self._v_count += 1
                self.log.info(f"Virtual DCA v{self._v_count}: v_avg=${self._v_avg:.1f}")

        # === 4. Entry: Base (Pine L167-176) ===
        if not skip_entry and sig_long and self._side is None:
            self._deal_base = self._get_base_usdt()
            qty = self._deal_base / close
            self._dealcount += 1
            self._socounter = 0
            if self._pine_parity:
                # Queue for next bar open (Pine: strategy.entry queues, fills next bar)
                self._bt_pending_entry = {"qty": qty, "label": f"D#{self._dealcount}"}
                # Pine var updates happen immediately at signal time
                self._v_qty = qty
                self._v_cost = qty * close
                self._v_avg = close
                self._v_last_px = close
                self._v_count = 0
            else:
                if self._submit_entry(close, qty, f"D#{self._dealcount}"):
                    self._v_qty = qty
                    self._v_cost = qty * close
                    self._v_avg = close
                    self._v_last_px = close
                    self._v_count = 0

        # === 4b. Entry: DCA (Pine L179-187) ===
        elif (not skip_entry and sig_long and self._side == "LONG"
              and self._dca_count > 0 and self._dca_count < sonum):
            # Pine: SOconditions = changeFromLast > spacing AND close < nextSO
            chg = self._calc_change_from_last(close)
            next_so = self._calc_next_so_price(self.config.dca_min_change_pct)
            if chg > self.config.dca_min_change_pct and close < next_so:
                self._socounter += 1
                if self.config.dca_type == "volume_multiply":
                    dca_qty = self._total_quantity * self.config.dca_multiplier
                else:
                    dca_qty = (self._deal_base * (self.config.dca_multiplier ** self._socounter)) / close
                if self._pine_parity:
                    self._bt_pending_entry = {"qty": dca_qty, "label": f"SO#{self._socounter}"}
                    self._v_cost += dca_qty * close
                    self._v_qty += dca_qty
                    self._v_avg = self._v_cost / self._v_qty
                    self._v_last_px = close
                    self._v_count += 1
                else:
                    if self._submit_entry(close, dca_qty, f"SO#{self._socounter}"):
                        self._v_cost += dca_qty * close
                        self._v_qty += dca_qty
                        self._v_avg = self._v_cost / self._v_qty
                        self._v_last_px = close
                        self._v_count += 1

        # === 5. Sync (Pine L193-199) ===
        if self._side is None and self._v_qty > 0:
            self._v_qty = self._v_cost = self._v_avg = self._v_last_px = 0.0
            self._v_count = 0
            self._socounter = 0

        # Bar processing log (every bar when no position, confirms data flowing)
        if not skip_entry and self._side is None:
            # Log every 10th bar to avoid spam
            if n_bars % 10 == 0:
                self.log.info(
                    f"SRP bar #{n_bars}: ${close:,.1f} "
                    f"band=[{self._lower_band:,.1f}, {self._upper_band:,.1f}] "
                    f"rsi_mfi={self._rsi_mfi:.1f} "
                    f"{'sig_long' if sig_long else 'no_signal'}"
                )

    # =========================================================================
    # Order Submission
    # =========================================================================

    def _submit_entry(self, price: float, quantity: float, label: str) -> bool:
        if self.instrument is None:
            return False

        # Use the ROUNDED quantity for all tracking (matches actual fill)
        rounded_qty = float(self.instrument.make_qty(quantity))
        min_qty = float(self.instrument.min_quantity) if self.instrument.min_quantity else 0.001
        if rounded_qty < min_qty:
            self.log.warning(f"SRP {label}: qty {quantity:.6f} rounds to {rounded_qty:.6f} < min {min_qty}")
            return False

        try:
            order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.BUY,
                quantity=self.instrument.make_qty(quantity),
                time_in_force=TimeInForce.GTC,
                reduce_only=False,
            )
            self.submit_order(order)

            # Update position state using rounded quantity (matches actual fill)
            if self._side is None:
                self._side = "LONG"
            self._dca_count += 1
            self._dca_entries.append({
                "price": price,
                "quantity": rounded_qty,
                "timestamp": self.clock.timestamp(),
                "label": label,
                "order_id": str(order.client_order_id),
            })
            self._total_quantity += rounded_qty
            self._total_cost += rounded_qty * price
            self._avg_price = self._total_cost / self._total_quantity

            self._save_state()

            # Update server-side SL to cover new position size/avg
            self._submit_server_sl()

            self.log.info(f"SRP {label}: price=${price:.2f} qty={quantity:.6f} "
                         f"avg=${self._avg_price:.2f} dca={self._dca_count}/{self.config.max_dca_count+1}")

            side_cn = "开多"
            self._send_telegram(
                f"📈 SRP {label} {side_cn}\n"
                f"价格: ${price:,.2f}\n"
                f"数量: {quantity:.6f} BTC\n"
                f"均价: ${self._avg_price:,.2f}\n"
                f"DCA: {self._dca_count}/{self.config.max_dca_count+1}"
            )
            return True

        except Exception as e:
            self.log.error(f"SRP entry failed: {e}")
            return False

    def _submit_full_close(self, price: float, reason: str):
        if self.instrument is None or self._total_quantity <= 0 or self._side is None:
            return

        min_qty = float(self.instrument.min_quantity) if self.instrument.min_quantity else 0.001
        if self._total_quantity < min_qty:
            self.log.warning(f"SRP dust position {self._total_quantity:.6f} — resetting ({reason})")
            self._reset_position_state()
            return

        # Use actual NT position quantity when available (avoids rounding divergence
        # between _total_quantity tracking and actual filled quantities).
        sell_qty = self._total_quantity
        try:
            positions = self.cache.positions(venue=self.instrument_id.venue)
            for pos in positions:
                if pos.instrument_id == self.instrument_id and not pos.is_closed:
                    sell_qty = float(pos.quantity)
                    break
        except Exception:
            pass  # Fall back to tracked quantity

        try:
            order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.SELL,
                quantity=self.instrument.make_qty(sell_qty),
                time_in_force=TimeInForce.GTC,
                reduce_only=True,
            )
            self.submit_order(order)

            pnl_pct = ((price - self._avg_price) / self._avg_price) * 100.0 if self._avg_price > 0 else 0.0

            self.log.info(f"SRP CLOSE: {reason} price=${price:.2f} pnl={pnl_pct:+.2f}%")

            self._send_telegram(
                f"📊 SRP 平仓\n"
                f"原因: {reason}\n"
                f"价格: ${price:,.2f}\n"
                f"均价: ${self._avg_price:,.2f}\n"
                f"盈亏: {pnl_pct:+.2f}%"
            )

            # Do NOT reset state here — wait for on_position_closed event
            self._pending_close_reason = reason

        except Exception as e:
            self._pending_close_reason = None  # Reset on failure so bars aren't blocked
            self.log.error(f"SRP close failed: {e}")

    # =========================================================================
    # Event Handlers (NT async pattern — state reset on confirmed fill)
    # =========================================================================

    def on_position_closed(self, event):
        """Called by NT when position is confirmed closed on exchange."""
        reason = getattr(self, '_pending_close_reason', 'unknown')
        self.log.info(f"SRP position closed confirmed: {reason}")

        # Track completed trade for accurate backtest counting
        try:
            realized_pnl = float(event.last_px) * float(event.last_qty) if hasattr(event, 'last_px') else 0.0
            self._completed_trades.append({
                "reason": reason,
                "avg_price": self._avg_price,
                "dca_count": self._dca_count,
                "timestamp": str(event.ts_event) if hasattr(event, 'ts_event') else "",
            })
        except Exception:
            self._completed_trades.append({"reason": reason})

        self._reset_position_state()

    def on_order_rejected(self, event):
        """Handle rejected orders — clear pending state to avoid blocking bars."""
        self.log.warning(f"SRP order rejected: {event}")
        if self._pending_close_reason is not None:
            self.log.error(f"SRP close order rejected! Clearing pending state to avoid deadlock.")
            self._pending_close_reason = None

    # =========================================================================
    # State Management
    # =========================================================================

    def _reset_position_state(self):
        self._side = None
        self._dca_count = 0
        self._dca_entries = []
        self._avg_price = 0.0
        self._total_quantity = 0.0
        self._total_cost = 0.0
        self._deal_base = 0.0
        self._socounter = 0
        self._v_qty = self._v_cost = self._v_avg = self._v_last_px = 0.0
        self._v_count = 0
        self._pending_close_reason = None
        # Cancel server-side SL (position is flat)
        self._cancel_server_sl()
        self._save_state()

    def _save_state(self):
        state = {
            "side": self._side,
            "dca_count": self._dca_count,
            "dca_entries": self._dca_entries,
            "avg_price": self._avg_price,
            "total_quantity": self._total_quantity,
            "total_cost": self._total_cost,
            "deal_base": self._deal_base,
            "dealcount": self._dealcount,
            "socounter": self._socounter,
            "v_qty": self._v_qty,
            "v_cost": self._v_cost,
            "v_avg": self._v_avg,
            "v_last_px": self._v_last_px,
            "v_count": self._v_count,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            os.makedirs(self._data_dir, exist_ok=True)
            with open(self._state_file, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            self.log.error(f"Save state failed: {e}")

    def _load_state(self):
        if not os.path.exists(self._state_file):
            self.log.info("No SRP state file, starting fresh")
            return
        try:
            with open(self._state_file, "r") as f:
                s = json.load(f)
            self._side = s.get("side")
            self._dca_count = s.get("dca_count", 0)
            self._dca_entries = s.get("dca_entries", [])
            self._avg_price = s.get("avg_price", 0.0)
            self._total_quantity = s.get("total_quantity", 0.0)
            self._total_cost = s.get("total_cost", 0.0)
            self._deal_base = s.get("deal_base", 0.0)
            self._dealcount = s.get("dealcount", 0)
            self._socounter = s.get("socounter", 0)
            self._v_qty = s.get("v_qty", 0.0)
            self._v_cost = s.get("v_cost", 0.0)
            self._v_avg = s.get("v_avg", 0.0)
            self._v_last_px = s.get("v_last_px", 0.0)
            self._v_count = s.get("v_count", 0)
            self.log.info(f"SRP state restored: side={self._side} dca={self._dca_count} "
                         f"avg=${self._avg_price:.2f} qty={self._total_quantity:.6f}")
        except Exception as e:
            self.log.error(f"Load state failed: {e}")

    # =========================================================================
    # Position Reconciliation (P0: verify state vs exchange on startup)
    # =========================================================================

    def _reconcile_position(self):
        """Reconcile strategy state with actual exchange position.

        Prevents state divergence after manual close, API timeout, or crash.
        Mirrors AI strategy's Tier 2 cross-validation pattern.
        """
        try:
            positions = self.cache.positions(venue=self.instrument_id.venue)
            exchange_qty = 0.0
            exchange_side = None
            for pos in positions:
                if pos.instrument_id == self.instrument_id and not pos.is_closed:
                    exchange_qty = float(pos.quantity)
                    exchange_side = "LONG" if pos.is_long else "SHORT"
                    break

            state_has_position = self._side is not None and self._total_quantity > 0
            exchange_has_position = exchange_qty > 0

            if state_has_position and not exchange_has_position:
                # State says we have a position, exchange says flat
                self.log.warning(
                    f"⚠️ RECONCILIATION: State has {self._side} qty={self._total_quantity:.6f} "
                    f"but exchange is FLAT. Resetting state."
                )
                self._send_telegram(
                    f"⚠️ SRP 重启对账: 状态文件有{self._side}仓位 "
                    f"qty={self._total_quantity:.6f}, 但交易所无仓位。已重置状态。"
                )
                self._reset_position_state()

            elif not state_has_position and exchange_has_position:
                # Exchange has a position we don't know about
                self.log.warning(
                    f"⚠️ RECONCILIATION: State is FLAT but exchange has "
                    f"{exchange_side} qty={exchange_qty:.6f}. Adopting exchange position."
                )
                self._side = exchange_side
                self._total_quantity = exchange_qty
                self._dca_count = 1  # Unknown DCA state, assume base entry
                self._send_telegram(
                    f"⚠️ SRP 重启对账: 状态文件无仓位, 但交易所有"
                    f"{exchange_side} qty={exchange_qty:.6f}。已同步。"
                )
                # Submit server-side SL for the adopted position
                self._submit_server_sl()

            elif state_has_position and exchange_has_position:
                # Both have position, check qty match
                qty_diff = abs(self._total_quantity - exchange_qty)
                if qty_diff > 0.0001:
                    self.log.warning(
                        f"⚠️ RECONCILIATION: Qty mismatch: state={self._total_quantity:.6f} "
                        f"exchange={exchange_qty:.6f}. Syncing to exchange."
                    )
                    self._total_quantity = exchange_qty
                    if self._total_quantity > 0:
                        self._total_cost = self._total_quantity * self._avg_price
                self.log.info(f"✅ Reconciliation OK: {self._side} qty={exchange_qty:.6f}")
                # Ensure server-side SL exists
                self._submit_server_sl()
            else:
                self.log.info("✅ Reconciliation OK: both FLAT")

        except Exception as e:
            self.log.error(f"Reconciliation failed: {e}. Continuing with state file.")

    # =========================================================================
    # Server-Side Stop Loss (P0: exchange-side protection)
    # =========================================================================

    def _submit_server_sl(self):
        """Submit StopMarket SL order on exchange for crash protection.

        If strategy crashes, this order persists on Binance and limits loss.
        Replaces any existing SL. Called after entry and on startup reconciliation.
        """
        if self._is_backtest() or self.instrument is None:
            return
        if self._side is None or self._avg_price <= 0 or self._total_quantity <= 0:
            return

        # Cancel existing SL first
        self._cancel_server_sl()

        sl_price = self._avg_price * (1.0 - self.config.max_portfolio_loss_pct)

        try:
            sell_qty = self._total_quantity
            # Use actual exchange position quantity
            positions = self.cache.positions(venue=self.instrument_id.venue)
            for pos in positions:
                if pos.instrument_id == self.instrument_id and not pos.is_closed:
                    sell_qty = float(pos.quantity)
                    break

            sl_order = self.order_factory.stop_market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.SELL,
                quantity=self.instrument.make_qty(sell_qty),
                trigger_price=self.instrument.make_price(sl_price),
                trigger_type=TriggerType.LAST_PRICE,
                reduce_only=True,
            )
            self.submit_order(sl_order)
            self._sl_order_id = str(sl_order.client_order_id)
            self.log.info(f"✅ Server-side SL submitted: SELL {sell_qty:.6f} @ ${sl_price:,.2f} "
                         f"({self.config.max_portfolio_loss_pct*100:.0f}% below avg)")
        except Exception as e:
            self.log.error(f"❌ Server-side SL failed: {e}")
            self._send_telegram(f"❌ SRP 服务端止损提交失败: {e}")

    def _cancel_server_sl(self):
        """Cancel existing server-side SL order."""
        if self._sl_order_id is None:
            return
        try:
            from nautilus_trader.model.identifiers import ClientOrderId
            order = self.cache.order(ClientOrderId(self._sl_order_id))
            if order and order.is_open:
                self.cancel_order(order)
                self.log.info(f"Cancelled old SL: {self._sl_order_id}")
        except Exception:
            pass
        self._sl_order_id = None

    # =========================================================================
    # Heartbeat
    # =========================================================================

    def _on_timer(self, event):
        if self._side is None:
            return
        price = float(self._closes[-1]) if self._closes else 0
        if price <= 0 or self._avg_price <= 0:
            return
        pnl_pct = ((price - self._avg_price) / self._avg_price) * 100.0
        self.log.info(
            f"SRP heartbeat: price=${price:,.1f} avg=${self._avg_price:,.1f} "
            f"pnl={pnl_pct:+.2f}% dca={self._dca_count} v_avg=${self._v_avg:.1f}"
        )

    # =========================================================================
    # Telegram
    # =========================================================================

    def _send_telegram(self, text: str):
        if not self.config.enable_telegram:
            return
        token = self.config.telegram_bot_token
        chat_id = self.config.telegram_chat_id
        if not token or not chat_id:
            return
        tagged = f"[SRP] {text}"
        def _send():
            try:
                requests.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": tagged, "parse_mode": "HTML"},
                    timeout=10,
                )
            except Exception:
                pass
        threading.Thread(target=_send, daemon=True).start()
