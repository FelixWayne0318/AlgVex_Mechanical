#!/usr/bin/env python3
"""
Backtest using real AI signals extracted from journalctl logs.

v3.1: Multi-layer cross-scan PnL fix:
  - Fix: layers exiting across different scan calls now correctly accumulate PnL
  - Before: only the final scan's PnL was added to equity (earlier exits lost)
  - After: all layers' dollar_pnl summed from layer dicts on position close

v3.0: Multi-layer position + trailing stop simulation:
  - Multi-layer pyramiding: same-direction signals add layers (up to 7)
  - Per-layer independent SL/TP (matches production v7.2)
  - Trailing stop: activates at 1.5R profit, 4H ATR × 0.6 callback (v43.0)
  - CLOSE/REDUCE signals: AI-initiated position close / partial close
  - LIFO reduction: newest layer exits first
  - Pyramiding conditions: min_profit_atr=0.5, min_confidence=HIGH, same direction only

v2.1: Production parity fixes (retained):
  - Fee: 0.075% × 2 (round-trip entry + exit)
  - Position sizing: confidence_mapping {HIGH=80, MED=50} × appetite_scale(0.8)
  - max_position_ratio: 0.12
  - Counter-trend: R/R × 1.3, time barrier 6h (was always 12h)
  - Drawdown circuit breaker: 10% → REDUCED(0.5×), 15% → HALT
  - Volatility circuit breaker: ATR > 3× baseline → HALT
  - SL slippage: 0.03% adverse fill simulation
  - Fingerprint dedup: signal|confidence|risk_appetite (3 fields)

v2.0: Production gates (retained):
  1. Per-stop cooldown — SL → 40min default, noise=20min, reversal=2h, volatility=4h
  2. Consecutive loss circuit breaker — 3 SL → 4h cooldown; 2 SL → position halved
  3. Signal fingerprint dedup — same signal|confidence|risk_appetite → skip
  4. Market change gate — price change <0.2% → skip (watchdog after 3 skips)
  5. Daily loss limit — 3% daily loss → halt trading
  6. Post-close forced analysis — 2 forced cycles after position close

Usage (on server):
  cd /home/linuxuser/nautilus_AlgVex && source venv/bin/activate && \\
  python3 scripts/backtest_from_logs.py

  # Specify days of logs to scan:
  python3 scripts/backtest_from_logs.py --days 30

  # Export extracted signals without running backtest:
  python3 scripts/backtest_from_logs.py --export-only

  # Use previously exported signals:
  python3 scripts/backtest_from_logs.py --signals data/extracted_signals.json

  # v1 mode (no production gates, for comparison):
  python3 scripts/backtest_from_logs.py --no-gates
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.request import Request, urlopen
from urllib.error import URLError

# Project root for shared imports
sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.backtest_math import calculate_atr_wilder

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

SYMBOL = "BTCUSDT"
BINANCE_FUTURES_BASE = "https://fapi.binance.com"

# Time barrier (hours) — trend vs counter-trend
TIME_BARRIER_HOURS_TREND = 12
TIME_BARRIER_HOURS_COUNTER = 6

# v44.0: Current production params (4H ATR scale, unified R/R=1.5)
PLAN_V44 = {
    "label": "v44.0 (current production)",
    "sl_atr_multiplier": {"HIGH": 0.8, "MEDIUM": 1.0, "LOW": 1.0},
    "tp_rr_target": {"HIGH": 1.5, "MEDIUM": 1.5, "LOW": 1.5},
    "sl_atr_multiplier_floor": 0.5,
    "min_confidence": "LOW",
    "counter_trend_rr_multiplier": 1.3,
    "min_rr_ratio": 1.3,
}

# v39.0: Previous production params (for regression comparison)
PLAN_V39 = {
    "label": "v39.0 (prior production, R/R=2.0/1.8)",
    "sl_atr_multiplier": {"HIGH": 0.8, "MEDIUM": 1.0, "LOW": 1.0},
    "tp_rr_target": {"HIGH": 2.0, "MEDIUM": 1.8, "LOW": 1.8},
    "sl_atr_multiplier_floor": 0.5,
    "min_confidence": "LOW",
    "counter_trend_rr_multiplier": 1.3,
    "min_rr_ratio": 1.5,
}

CONFIDENCE_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}

# Confidence-based position sizing (matches production configs/base.yaml:271-274)
# confidence_mapping: HIGH=80%, MEDIUM=50%, LOW=30% of max_usdt
CONFIDENCE_POSITION_PCT = {"HIGH": 0.80, "MEDIUM": 0.50, "LOW": 0.30}

# Appetite scale (production: strategy/trading_logic.py:580-625)
# Most signals have risk_appetite=NORMAL from Risk Manager
APPETITE_SCALE = {"AGGRESSIVE": 1.0, "NORMAL": 0.8, "CONSERVATIVE": 0.5}

# max_position_ratio from configs/base.yaml:90
MAX_POSITION_RATIO = 0.12

# Leverage from configs/base.yaml:90
LEVERAGE = 10

# Fee per side (Binance futures taker)
FEE_PER_SIDE = 0.00075  # 0.075%

# SL slippage estimate (conservative)
SL_SLIPPAGE_PCT = 0.0003  # 0.03% adverse fill on SL

# v3.0: Trailing stop parameters (matches strategy/order_execution.py v43.0)
TRAILING_ACTIVATION_R = 1.5        # Activate trailing at 1.5R profit (v43.0: 1.1→1.5)
TRAILING_ATR_MULTIPLIER = 0.6      # Callback distance = 0.6 × 4H ATR (v43.0: 1.5→0.6)
TRAILING_MIN_BPS = 10              # 0.1% minimum callback (Binance limit)
TRAILING_MAX_BPS = 1000            # 10.0% maximum callback (Binance limit)

# v3.0: Pyramiding parameters (matches configs/base.yaml:215-222)
PYRAMIDING_LAYER_SIZES = [0.25, 0.22, 0.20, 0.20, 0.20, 0.20, 0.20]
PYRAMIDING_MIN_PROFIT_ATR = 0.5    # Min unrealized profit in ATR units to add
PYRAMIDING_MIN_CONFIDENCE = "MEDIUM"  # v42.1: MEDIUM (was HIGH, too restrictive)
PYRAMIDING_COUNTER_TREND_ALLOWED = False
PYRAMIDING_MAX_LAYERS = 7

# ============================================================================
# Production gate configuration (matches configs/base.yaml + production.yaml)
# ============================================================================
PRODUCTION_GATES = {
    # Timer interval in production (seconds)
    "timer_interval_sec": 1200,  # 20 minutes

    # Per-stop cooldown (v6.0) — candles × timer_interval
    "cooldown_enabled": True,
    "cooldown_per_stoploss_candles": 2,        # default: 2 × 20min = 40min
    "cooldown_noise_stop_candles": 1,           # noise: 1 × 20min = 20min
    "cooldown_reversal_stop_candles": 6,        # reversal: 6 × 20min = 2h
    "cooldown_volatility_stop_candles": 12,     # volatility: 12 × 20min = 4h
    "cooldown_detection_candles": 2,            # observation period before refine

    # Market change gate (v15.0)
    "market_change_price_threshold": 0.002,     # 0.2% price change triggers re-analysis
    "market_change_atr_threshold": 0.15,        # 15% ATR change triggers re-analysis
    "max_skips_before_force": 3,                # watchdog: force after 3 skips

    # Signal fingerprint dedup (v15.0)
    "dedup_enabled": True,

    # Risk circuit breakers (v3.12)
    "circuit_breakers_enabled": True,
    "consecutive_loss_max": 3,                  # 3 SL → COOLDOWN
    "consecutive_loss_cooldown_hours": 4,       # 4h cooldown
    "consecutive_loss_reduce_at": 2,            # 2 SL → REDUCED (0.5× size)
    "daily_loss_max_pct": 0.03,                 # 3% daily loss → halt

    # Drawdown circuit breaker (configs/base.yaml:231-236)
    "drawdown_reduce_pct": 0.10,               # 10% drawdown → REDUCED (0.5×)
    "drawdown_halt_pct": 0.15,                 # 15% drawdown → HALT
    "drawdown_recovery_pct": 0.05,             # Re-enable after recovery to 5%

    # Volatility circuit breaker (configs/base.yaml:256)
    "volatility_halt_multiplier": 3.0,         # ATR > 3× baseline → HALT

    # Post-close forced analysis (v18.3)
    "post_close_forced_cycles": 2,
}


# ============================================================================
# Production-faithful trade simulator
# ============================================================================
class ProductionSimulator:
    """
    v3.0: Multi-layer position simulator with trailing stops.
    Simulates production execution pipeline including pyramiding, per-layer
    SL/TP/trailing, LIFO reduction, and all production gates.
    """

    def __init__(self, params: Dict, gates: Dict, bars_1m: List[Dict],
                 bars_30m: List[Dict], use_gates: bool = True):
        self.params = params
        self.gates = gates
        self.bars_1m = bars_1m
        self.bars_30m = bars_30m
        self.use_gates = use_gates

        # v3.0: Multi-layer position state
        self.in_position = False
        self.position_side: Optional[str] = None  # 'LONG' or 'SHORT'
        self.layers: List[Dict] = []  # Each layer: {entry_price, quantity_pct, sl, tp, trailing_sl, confidence, ...}
        self.position_entry_time_ms: int = 0  # First layer entry time (for time barrier)
        self.position_is_counter_trend: bool = False

        # Per-stop cooldown state
        self.cooldown_until_ms: int = 0
        self.cooldown_type: str = ""
        self.last_sl_price: float = 0
        self.last_sl_time_ms: int = 0
        self.last_sl_side: str = ""

        # Market change gate
        self.last_analysis_price: float = 0
        self.last_analysis_atr: float = 0
        self.consecutive_skips: int = 0

        # Signal fingerprint dedup
        self.last_fingerprint: str = ""

        # Risk circuit breakers
        self.consecutive_losses: int = 0
        self.consecutive_wins: int = 0
        self.circuit_breaker_until_ms: int = 0
        self.circuit_breaker_reason: str = ""
        self.position_size_multiplier: float = 1.0  # 1.0=ACTIVE, 0.5=REDUCED

        # Daily loss tracking
        self.daily_equity_start: float = 100.0
        self.daily_date: str = ""
        self.daily_halted: bool = False

        # Drawdown circuit breaker state
        self.drawdown_reduced: bool = False
        self.drawdown_halted: bool = False

        # Volatility circuit breaker
        self.baseline_atr: float = 0
        self.volatility_halted: bool = False

        # Post-close forced analysis
        self.forced_analysis_remaining: int = 0

        # Equity tracking
        self.equity: float = 100.0
        self.peak_equity: float = 100.0
        self.max_dd: float = 0

        # Results
        self.trades: List[Dict] = []
        self.skip_log: List[Dict] = []

        # v3.0: Statistics
        self.total_layers_opened: int = 0
        self.pyramiding_events: int = 0
        self.trailing_activations: int = 0
        self.trailing_exits: int = 0

    def _ts_to_ms(self, ts_str: str) -> int:
        """Convert ISO timestamp to epoch ms."""
        dt = datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)

    def _ms_to_str(self, ms: int) -> str:
        """Convert epoch ms to ISO string."""
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    def _get_price_at(self, time_ms: int) -> float:
        """Get price from 1M bars at a given time."""
        for bar in self.bars_1m:
            if bar["open_time"] <= time_ms <= bar.get("close_time", bar["open_time"] + 59999):
                return bar["close"]
        # Fallback: find nearest bar before
        for i in range(len(self.bars_1m) - 1, -1, -1):
            if self.bars_1m[i]["open_time"] <= time_ms:
                return self.bars_1m[i]["close"]
        return 0

    def _get_atr_at(self, time_ms: int) -> float:
        """Get ATR from 30M bars at a given time."""
        bar_idx = None
        for i, b in enumerate(self.bars_30m):
            if b["open_time"] <= time_ms <= b.get("close_time", b["open_time"] + 1799999):
                bar_idx = i
                break
            elif b["open_time"] > time_ms:
                bar_idx = i - 1 if i > 0 else 0
                break
        if bar_idx is None:
            bar_idx = len(self.bars_30m) - 1
        if bar_idx < 0:
            return 0
        atr_bars = self.bars_30m[max(0, bar_idx - 15): bar_idx + 1]
        return calculate_atr_wilder(atr_bars, period=14)

    def _is_counter_trend(self, side: str, time_ms: int) -> bool:
        """
        Approximate counter-trend detection using price vs SMA.
        Production uses 1D SMA200 + DI+/DI- from MTF manager.
        In backtest, we approximate using 30M bars price direction over ~24h.
        """
        is_long = side.upper() in ("BUY", "LONG")

        # Use recent 30M bars (48 bars ≈ 24h) to estimate trend
        bar_idx = None
        for i, b in enumerate(self.bars_30m):
            if b["open_time"] > time_ms:
                bar_idx = i - 1 if i > 0 else 0
                break
        if bar_idx is None:
            bar_idx = len(self.bars_30m) - 1
        if bar_idx < 48:
            return False  # Not enough data

        lookback = self.bars_30m[bar_idx - 47: bar_idx + 1]
        if len(lookback) < 20:
            return False

        # Simple trend: compare first half avg vs second half avg
        half = len(lookback) // 2
        first_avg = sum(b["close"] for b in lookback[:half]) / half
        second_avg = sum(b["close"] for b in lookback[half:]) / half

        trend_up = second_avg > first_avg
        # Counter-trend: going long in downtrend, or short in uptrend
        return (is_long and not trend_up) or (not is_long and trend_up)

    def _calc_sltp(self, entry_price: float, side: str, atr: float,
                   confidence: str, is_counter_trend: bool = False) -> Optional[Dict]:
        """Calculate SL/TP for given parameters, with counter-trend R/R escalation."""
        conf_upper = confidence.upper()
        min_conf = self.params["min_confidence"]
        if CONFIDENCE_RANK.get(conf_upper, 0) < CONFIDENCE_RANK.get(min_conf, 0):
            return None

        is_long = side.upper() in ("BUY", "LONG")
        sl_mult = self.params["sl_atr_multiplier"].get(conf_upper, 1.8)
        sl_mult = max(sl_mult, self.params["sl_atr_multiplier_floor"])
        sl_distance = atr * sl_mult
        rr_target = self.params["tp_rr_target"].get(conf_upper, 1.5)

        # v5.12: Counter-trend R/R escalation (1.5 × 1.3 = 1.95:1)
        if is_counter_trend:
            ct_mult = self.params.get("counter_trend_rr_multiplier", 1.3)
            min_rr = self.params.get("min_rr_ratio", 1.5)
            min_ct_rr = min_rr * ct_mult
            rr_target = max(rr_target, min_ct_rr)

        tp_distance = sl_distance * rr_target

        if is_long:
            sl_price = entry_price - sl_distance
            tp_price = entry_price + tp_distance
        else:
            sl_price = entry_price + sl_distance
            tp_price = entry_price - tp_distance

        if sl_price <= 0 or tp_price <= 0:
            return None

        return {"sl_price": sl_price, "tp_price": tp_price, "rr_target": rr_target,
                "is_counter_trend": is_counter_trend}

    def _create_layer(self, entry_price: float, side: str, atr: float,
                      confidence: str, risk_appetite: str,
                      entry_time_ms: int, is_counter_trend: bool,
                      layer_idx: int, is_addon: bool = False) -> Dict:
        """Create a new position layer with independent SL/TP/trailing."""
        sltp = self._calc_sltp(entry_price, side, atr, confidence,
                               is_counter_trend=is_counter_trend)
        if sltp is None:
            return {}

        sl_price = sltp["sl_price"]
        tp_price = sltp["tp_price"]
        rr_target = sltp["rr_target"]
        is_long = side.upper() in ("BUY", "LONG")

        # SL distance = 1R for trailing activation
        sl_distance = abs(entry_price - sl_price)

        # Trailing stop activation price: entry + 1.5R (v43.0)
        if is_long:
            trailing_activation = entry_price + sl_distance * TRAILING_ACTIVATION_R
        else:
            trailing_activation = entry_price - sl_distance * TRAILING_ACTIVATION_R

        # Trailing callback in bps (int truncation matches production)
        trailing_distance = atr * TRAILING_ATR_MULTIPLIER
        trailing_bps = int((trailing_distance / entry_price) * 10000)
        trailing_bps = max(TRAILING_MIN_BPS, min(TRAILING_MAX_BPS, trailing_bps))

        # Position sizing — matches production parity:
        # Initial position: max_usdt × conf_pct × appetite × risk_mult
        #   (production: calculate_position_size → ai_controlled path)
        # Add-on layers:   max_usdt × layer_ratio (no conf/appetite)
        #   (production: _execute_trade → layer_usdt = max_usdt * layer_ratio)
        risk_mult = self.position_size_multiplier
        if is_addon:
            # Pyramiding add-on: pure layer_ratio, no conf/appetite multiplier
            layer_ratio = PYRAMIDING_LAYER_SIZES[min(layer_idx, len(PYRAMIDING_LAYER_SIZES) - 1)]
            effective_size = layer_ratio * risk_mult
        else:
            # Initial position: confidence_mapping × appetite_scale × risk_multiplier
            conf_pct = CONFIDENCE_POSITION_PCT.get(confidence, 0.50)
            appetite_mult = APPETITE_SCALE.get(risk_appetite, 0.8)
            effective_size = conf_pct * appetite_mult * risk_mult

        return {
            "entry_price": entry_price,
            "entry_time_ms": entry_time_ms,
            "sl_price": sl_price,
            "tp_price": tp_price,
            "rr_target": rr_target,
            "confidence": confidence,
            "risk_appetite": risk_appetite,
            "layer_idx": layer_idx,
            "effective_size": effective_size,  # Fraction of max_usdt this layer uses
            "is_counter_trend": is_counter_trend,
            "atr": atr,
            # Trailing stop state
            "trailing_activation": trailing_activation,
            "trailing_bps": trailing_bps,
            "trailing_active": False,
            "trailing_sl": 0.0,  # Trailing SL price (updated each bar once active)
            "trailing_high_water": entry_price if is_long else entry_price,  # Best price seen
        }

    def _update_trailing_for_bar(self, layer: Dict, bar: Dict, is_long: bool):
        """Update trailing stop for a layer based on current bar."""
        entry = layer["entry_price"]
        callback_frac = layer["trailing_bps"] / 10000.0

        if is_long:
            # Track highest price
            if bar["high"] > layer["trailing_high_water"]:
                layer["trailing_high_water"] = bar["high"]

            # Check activation
            if not layer["trailing_active"]:
                if bar["high"] >= layer["trailing_activation"]:
                    layer["trailing_active"] = True
                    self.trailing_activations += 1
                    # Set initial trailing SL
                    layer["trailing_sl"] = layer["trailing_high_water"] * (1 - callback_frac)
            else:
                # Update trailing SL (only moves up for long)
                new_trailing = layer["trailing_high_water"] * (1 - callback_frac)
                if new_trailing > layer["trailing_sl"]:
                    layer["trailing_sl"] = new_trailing
        else:
            # Track lowest price
            if bar["low"] < layer["trailing_high_water"]:
                layer["trailing_high_water"] = bar["low"]

            if not layer["trailing_active"]:
                if bar["low"] <= layer["trailing_activation"]:
                    layer["trailing_active"] = True
                    self.trailing_activations += 1
                    layer["trailing_sl"] = layer["trailing_high_water"] * (1 + callback_frac)
            else:
                new_trailing = layer["trailing_high_water"] * (1 + callback_frac)
                if new_trailing < layer["trailing_sl"]:
                    layer["trailing_sl"] = new_trailing

    def _check_layer_exit(self, layer: Dict, bar: Dict, is_long: bool) -> Optional[str]:
        """Check if a layer's SL, TP, or trailing SL is hit. Returns exit type or None."""
        sl = layer["sl_price"]
        tp = layer["tp_price"]
        trailing_sl = layer["trailing_sl"] if layer["trailing_active"] else 0

        if is_long:
            # Fixed SL
            sl_hit = bar["low"] <= sl
            # Trailing SL (if active and tighter than fixed SL)
            trailing_hit = trailing_sl > 0 and bar["low"] <= trailing_sl and trailing_sl > sl
            tp_hit = bar["high"] >= tp
        else:
            sl_hit = bar["high"] >= sl
            trailing_hit = trailing_sl > 0 and bar["high"] >= trailing_sl and trailing_sl < sl
            tp_hit = bar["low"] <= tp

        # Same-bar conflict priority: SL > Trailing > TP (conservative)
        # When both SL and TP hit in the same 1M bar, assume SL fires first
        # (pessimistic — intra-bar order unknown at 1M resolution)
        if sl_hit:
            return "SL"
        if trailing_hit:
            return "TRAILING"
        if tp_hit:
            return "TP"
        return None

    def _get_exit_price(self, layer: Dict, exit_type: str, bar: Dict, is_long: bool) -> float:
        """Get the exit price for a layer based on exit type."""
        if exit_type == "TP":
            return layer["tp_price"]
        elif exit_type == "TRAILING":
            return layer["trailing_sl"]
        elif exit_type == "SL":
            # Apply slippage on SL
            slippage = layer["entry_price"] * SL_SLIPPAGE_PCT
            if is_long:
                return layer["sl_price"] - slippage
            else:
                return layer["sl_price"] + slippage
        elif exit_type == "TIME_BARRIER":
            return bar["open"]
        elif exit_type == "CLOSE":
            return bar["close"]  # Market close at current price
        return bar["close"]

    def _close_layer(self, layer: Dict, exit_price: float, exit_type: str,
                     exit_time_ms: int) -> float:
        """Close a single layer and return dollar PnL change to equity."""
        is_long = self.position_side and self.position_side.upper() in ("LONG", "BUY")
        entry = layer["entry_price"]

        if is_long:
            pnl_pct = (exit_price - entry) / entry * 100
        else:
            pnl_pct = (entry - exit_price) / entry * 100

        # Position value for this layer
        max_usdt = self.equity * MAX_POSITION_RATIO * LEVERAGE
        pv = max_usdt * layer["effective_size"]

        # Round-trip fees
        fee = pv * FEE_PER_SIDE * 2
        dollar_pnl = pv * pnl_pct / 100 - fee

        layer["exit_price"] = exit_price
        layer["exit_type"] = exit_type
        layer["exit_time_ms"] = exit_time_ms
        layer["pnl_pct"] = round(pnl_pct, 4)
        layer["dollar_pnl"] = round(dollar_pnl, 4)
        layer["minutes_held"] = round((exit_time_ms - layer["entry_time_ms"]) / 60000, 1)

        return dollar_pnl

    def _close_all_layers(self, exit_type: str, exit_time_ms: int, bar: Dict) -> Tuple[float, List[Dict]]:
        """Close all layers at once (time barrier, CLOSE signal). Returns (total_dollar_pnl, closed_layers)."""
        is_long = self.position_side and self.position_side.upper() in ("LONG", "BUY")
        total_pnl = 0.0
        closed = []
        for layer in self.layers:
            if "exit_type" in layer:
                continue  # Already closed
            exit_price = self._get_exit_price(layer, exit_type, bar, is_long)
            pnl = self._close_layer(layer, exit_price, exit_type, exit_time_ms)
            total_pnl += pnl
            closed.append(layer)
        return total_pnl, closed

    def _reduce_layers_lifo(self, exit_time_ms: int, bar: Dict, fraction: float = 1.0) -> Tuple[float, List[Dict]]:
        """LIFO reduction: close newest layers first. fraction=1.0 closes all, 0.5 closes ~half."""
        is_long = self.position_side and self.position_side.upper() in ("LONG", "BUY")
        open_layers = [l for l in self.layers if "exit_type" not in l]
        if not open_layers:
            return 0.0, []

        # Determine how many to close
        n_close = max(1, round(len(open_layers) * fraction))
        # LIFO: close from newest (last added)
        to_close = open_layers[-n_close:]

        total_pnl = 0.0
        closed = []
        for layer in to_close:
            exit_price = self._get_exit_price(layer, "REDUCE", bar, is_long)
            pnl = self._close_layer(layer, exit_price, "REDUCE", exit_time_ms)
            total_pnl += pnl
            closed.append(layer)
        return total_pnl, closed

    def _scan_layers_tick_by_tick(self, until_time_ms: int) -> Optional[Dict]:
        """
        Scan 1M bars from position entry to until_time_ms, checking all layers
        for SL/TP/trailing exits and time barrier.
        Returns event dict if all layers closed, or None if position still open.
        """
        is_long = self.position_side and self.position_side.upper() in ("LONG", "BUY")
        is_ct = self.position_is_counter_trend
        tb_hours = TIME_BARRIER_HOURS_COUNTER if is_ct else TIME_BARRIER_HOURS_TREND
        deadline_ms = self.position_entry_time_ms + int(tb_hours * 3600 * 1000)

        total_dollar_pnl = 0.0
        any_sl = False
        any_trailing = False

        for bar in self.bars_1m:
            if bar["open_time"] < self.position_entry_time_ms:
                continue
            if bar["open_time"] > until_time_ms:
                break

            # Check time barrier first (highest priority)
            if bar["open_time"] > deadline_ms:
                open_layers = [l for l in self.layers if "exit_type" not in l]
                if open_layers:
                    pnl, closed = self._close_all_layers("TIME_BARRIER", bar["open_time"], bar)
                    total_dollar_pnl += pnl
                    # v3.1 fix: Use cumulative PnL from ALL layers, not just this scan.
                    # Layers that exited in previous scans have dollar_pnl set but were
                    # never added to equity (total_dollar_pnl resets each scan call).
                    all_layers_pnl = sum(l.get("dollar_pnl", 0) for l in self.layers)
                    self.equity += all_layers_pnl
                    self._update_peak_dd()
                    return {"event": "TIME_BARRIER", "time_ms": bar["open_time"],
                            "dollar_pnl": all_layers_pnl, "layers_closed": len(closed)}

            # Update trailing stops for all open layers
            open_layers = [l for l in self.layers if "exit_type" not in l]
            for layer in open_layers:
                # Only update trailing for bars after this layer's entry
                if bar["open_time"] >= layer["entry_time_ms"]:
                    self._update_trailing_for_bar(layer, bar, is_long)

            # Check each layer for exit
            for layer in open_layers:
                if bar["open_time"] < layer["entry_time_ms"]:
                    continue
                exit_type = self._check_layer_exit(layer, bar, is_long)
                if exit_type:
                    exit_price = self._get_exit_price(layer, exit_type, bar, is_long)
                    pnl = self._close_layer(layer, exit_price, exit_type, bar["open_time"])
                    total_dollar_pnl += pnl
                    if exit_type == "SL":
                        any_sl = True
                    if exit_type == "TRAILING":
                        any_trailing = True
                        self.trailing_exits += 1

            # Check if all layers are now closed
            remaining = [l for l in self.layers if "exit_type" not in l]
            if not remaining and self.layers:
                # v3.1 fix: Use cumulative PnL from ALL layers, not just this scan.
                # When layers exit across different scan calls, total_dollar_pnl
                # only captures the current scan's exits. Earlier exits stored their
                # dollar_pnl in the layer dict but never reached equity.
                all_layers_pnl = sum(l.get("dollar_pnl", 0) for l in self.layers)
                self.equity += all_layers_pnl
                self._update_peak_dd()
                primary_exit = "SL" if any_sl else ("TRAILING" if any_trailing else "TP")
                return {"event": primary_exit, "time_ms": bar["open_time"],
                        "dollar_pnl": all_layers_pnl,
                        "layers_closed": len(self.layers)}

        return None  # Still open

    def _update_peak_dd(self):
        """Update peak equity and max drawdown."""
        if self.equity > self.peak_equity:
            self.peak_equity = self.equity
        dd = (self.peak_equity - self.equity) / self.peak_equity * 100
        if dd > self.max_dd:
            self.max_dd = dd

    def _finalize_position_close(self, exit_type: str, exit_time_ms: int, total_dollar_pnl: float):
        """Post-close bookkeeping: cooldowns, circuit breakers, state reset."""
        pos_side = self.position_side

        # Clear position state
        self.in_position = False
        self.position_side = None
        self.layers = []
        self.position_entry_time_ms = 0
        self.position_is_counter_trend = False

        # Post-close forced analysis (v18.3)
        if self.use_gates:
            self.forced_analysis_remaining = self.gates["post_close_forced_cycles"]
            self.last_fingerprint = ""

        # Per-stop cooldown (v6.0) — triggered on any SL exit
        if self.use_gates and exit_type == "SL":
            timer_ms = self.gates["timer_interval_sec"] * 1000
            default_cd = self.gates["cooldown_per_stoploss_candles"] * timer_ms
            self.cooldown_until_ms = exit_time_ms + default_cd
            self.cooldown_type = "default"
            self.last_sl_time_ms = exit_time_ms
            self.last_sl_side = pos_side or ""

        # Consecutive loss tracking
        if self.use_gates and self.gates["circuit_breakers_enabled"]:
            if total_dollar_pnl < 0:
                self.consecutive_losses += 1
                self.consecutive_wins = 0

                if self.consecutive_losses >= self.gates["consecutive_loss_reduce_at"]:
                    self.position_size_multiplier = 0.5

                if self.consecutive_losses >= self.gates["consecutive_loss_max"]:
                    cd_hours = self.gates["consecutive_loss_cooldown_hours"]
                    self.circuit_breaker_until_ms = exit_time_ms + int(cd_hours * 3600 * 1000)
                    self.circuit_breaker_reason = (
                        f"consecutive_{self.consecutive_losses}_losses_{cd_hours}h"
                    )
            else:
                self.consecutive_wins += 1
                if self.consecutive_wins >= 1:
                    self.consecutive_losses = 0
                    self.position_size_multiplier = 1.0
                    self.circuit_breaker_until_ms = 0
                    self.circuit_breaker_reason = ""

        # Daily loss check
        if self.use_gates and self.gates["circuit_breakers_enabled"]:
            daily_pnl = (self.equity - self.daily_equity_start) / self.daily_equity_start
            if daily_pnl < -self.gates["daily_loss_max_pct"]:
                self.daily_halted = True

        # Drawdown circuit breaker
        if self.use_gates and self.gates["circuit_breakers_enabled"]:
            current_dd = (self.peak_equity - self.equity) / self.peak_equity if self.peak_equity > 0 else 0
            if current_dd >= self.gates["drawdown_halt_pct"]:
                self.drawdown_halted = True
                self.position_size_multiplier = 0.0
            elif current_dd >= self.gates["drawdown_reduce_pct"]:
                self.drawdown_reduced = True
                if self.position_size_multiplier > 0.5:
                    self.position_size_multiplier = 0.5
            elif current_dd < self.gates["drawdown_recovery_pct"]:
                if self.drawdown_reduced or self.drawdown_halted:
                    self.drawdown_reduced = False
                    self.drawdown_halted = False
                    if self.consecutive_losses < self.gates["consecutive_loss_reduce_at"]:
                        self.position_size_multiplier = 1.0

    def _refine_cooldown(self, current_time_ms: int):
        """
        Refine stop type after observation period (v6.0).
        Noise: price recovered past SL → shorter cooldown.
        Reversal: price continued away ≥1 ATR → longer cooldown.
        """
        if not self.last_sl_price or self.cooldown_type != "default":
            return

        timer_ms = self.gates["timer_interval_sec"] * 1000
        detection_ms = self.gates["cooldown_detection_candles"] * timer_ms

        if current_time_ms - self.last_sl_time_ms < detection_ms:
            return  # Still in observation period

        current_price = self._get_price_at(current_time_ms)
        atr = self._get_atr_at(current_time_ms)
        if current_price <= 0 or atr <= 0:
            return

        sl_price = self.last_sl_price
        if self.last_sl_side.upper() in ("LONG", "BUY"):
            price_from_sl = current_price - sl_price
        else:
            price_from_sl = sl_price - current_price

        if price_from_sl > 0:
            new_type = "noise"
            candles = self.gates["cooldown_noise_stop_candles"]
        elif abs(price_from_sl) >= atr:
            new_type = "reversal"
            candles = self.gates["cooldown_reversal_stop_candles"]
        else:
            return  # Indeterminate

        self.cooldown_type = new_type
        new_end = self.last_sl_time_ms + candles * timer_ms
        if new_end > current_time_ms:
            self.cooldown_until_ms = new_end
        else:
            self.cooldown_until_ms = current_time_ms  # Already expired

    def _check_gates(self, sig: Dict, sig_time_ms: int) -> Optional[str]:
        """
        Check all production gates. Returns skip reason string if signal
        should be skipped, or None if signal should be processed.
        """
        if not self.use_gates:
            return None

        signal = sig["signal"]
        confidence = sig["confidence"]

        # Gate 0: Daily loss halt
        sig_date = sig["timestamp"][:10]
        if sig_date != self.daily_date:
            self.daily_date = sig_date
            self.daily_equity_start = self.equity
            self.daily_halted = False
        if self.daily_halted:
            return "daily_loss_halt"

        # Gate 0b: Drawdown halt (15% → full halt)
        if self.drawdown_halted:
            return "drawdown_halt"

        # Gate 0c: Volatility halt (ATR > 3× baseline)
        current_atr = self._get_atr_at(sig_time_ms)
        if current_atr > 0:
            if self.baseline_atr <= 0:
                self.baseline_atr = current_atr
            else:
                # Update baseline with EMA-like smoothing
                self.baseline_atr = self.baseline_atr * 0.95 + current_atr * 0.05
            if current_atr > self.baseline_atr * self.gates["volatility_halt_multiplier"]:
                self.volatility_halted = True
                return f"volatility_halt (ATR {current_atr:.1f} > {self.baseline_atr * self.gates['volatility_halt_multiplier']:.1f})"
            else:
                self.volatility_halted = False

        # Gate 1: Circuit breaker (consecutive loss cooldown)
        if self.circuit_breaker_until_ms > 0 and sig_time_ms < self.circuit_breaker_until_ms:
            return f"circuit_breaker ({self.circuit_breaker_reason})"

        # Gate 2: Position occupancy — cannot open new trade while in position
        # v3.0: Pyramiding is handled before _check_gates in run() loop
        if self.in_position:
            return "position_occupied"

        # Gate 3: Per-stop cooldown (v6.0)
        if self.cooldown_until_ms > 0 and sig_time_ms < self.cooldown_until_ms:
            # Refine cooldown type if observation period passed
            self._refine_cooldown(sig_time_ms)
            if sig_time_ms < self.cooldown_until_ms:
                return f"stoploss_cooldown ({self.cooldown_type})"

        # Gate 4: Market change gate (v15.0)
        if self.last_analysis_price > 0:
            current_price = sig.get("entry_price") or self._get_price_at(sig_time_ms)
            if current_price > 0:
                price_change = abs(current_price - self.last_analysis_price) / self.last_analysis_price

                # Check for forced analysis (post-close reentry)
                is_forced = False
                if self.forced_analysis_remaining > 0:
                    is_forced = True

                if price_change < self.gates["market_change_price_threshold"] and not is_forced:
                    self.consecutive_skips += 1
                    if self.consecutive_skips < self.gates["max_skips_before_force"]:
                        return f"market_unchanged (Δ{price_change:.4f} < 0.002, skip #{self.consecutive_skips})"
                    else:
                        # Watchdog: force analysis
                        self.consecutive_skips = 0

        # Gate 5: Signal fingerprint dedup (v15.0)
        # Production uses signal|confidence|risk_appetite (ai_strategy.py:3087)
        risk_appetite = sig.get("risk", "NORMAL")
        fingerprint = f"{signal}|{confidence}|{risk_appetite}"
        if self.gates["dedup_enabled"] and fingerprint == self.last_fingerprint:
            return f"dedup ({fingerprint})"

        # Gate 6: Minimum confidence
        min_conf = self.params["min_confidence"]
        if CONFIDENCE_RANK.get(confidence.upper(), 0) < CONFIDENCE_RANK.get(min_conf, 0):
            return f"min_confidence ({confidence} < {min_conf})"

        # All gates passed
        return None

    def _update_gate_state_after_analysis(self, sig: Dict, sig_time_ms: int):
        """Update gate state after a signal is processed (whether traded or skipped by AI)."""
        current_price = sig.get("entry_price") or self._get_price_at(sig_time_ms)
        if current_price > 0:
            self.last_analysis_price = current_price
        self.last_analysis_atr = sig.get("atr") or self._get_atr_at(sig_time_ms)
        self.consecutive_skips = 0

        # Consume forced analysis cycle
        if self.forced_analysis_remaining > 0:
            self.forced_analysis_remaining -= 1

        # Update fingerprint (3 fields, matching production)
        self.last_fingerprint = f"{sig['signal']}|{sig['confidence']}|{sig.get('risk', 'NORMAL')}"

    def _can_pyramid(self, sig: Dict, sig_time_ms: int) -> bool:
        """Check if pyramiding conditions are met for adding a layer."""
        if not self.in_position or not self.layers:
            return False

        # Same direction only
        if sig["signal"] != self.position_side:
            return False

        # Max layers
        open_layers = [l for l in self.layers if "exit_type" not in l]
        if len(open_layers) >= PYRAMIDING_MAX_LAYERS:
            return False

        # Counter-trend: no pyramiding
        if PYRAMIDING_COUNTER_TREND_ALLOWED is False and self.position_is_counter_trend:
            return False

        # Minimum confidence for add-on
        conf = sig["confidence"].upper()
        if CONFIDENCE_RANK.get(conf, 0) < CONFIDENCE_RANK.get(PYRAMIDING_MIN_CONFIDENCE, 2):
            return False

        # Minimum unrealized profit in ATR units
        current_price = self._get_price_at(sig_time_ms)
        if current_price <= 0:
            return False

        is_long = self.position_side.upper() in ("LONG", "BUY")
        # Use the first layer as reference for profit calculation
        first_open = open_layers[0]
        entry = first_open["entry_price"]
        atr = first_open["atr"]
        if atr <= 0:
            return False

        if is_long:
            unrealized_atr = (current_price - entry) / atr
        else:
            unrealized_atr = (entry - current_price) / atr

        return unrealized_atr >= PYRAMIDING_MIN_PROFIT_ATR

    def run(self, signals: List[Dict]) -> Dict:
        """Run the full production-faithful backtest with multi-layer support."""
        self.equity = 100.0
        self.peak_equity = 100.0
        self.max_dd = 0
        self.trades = []
        self.skip_log = []
        self.total_layers_opened = 0
        self.pyramiding_events = 0
        self.trailing_activations = 0
        self.trailing_exits = 0

        # Reset all state
        self.in_position = False
        self.position_side = None
        self.layers = []
        self.position_is_counter_trend = False
        self.cooldown_until_ms = 0
        self.last_analysis_price = 0
        self.consecutive_skips = 0
        self.last_fingerprint = ""
        self.consecutive_losses = 0
        self.consecutive_wins = 0
        self.circuit_breaker_until_ms = 0
        self.position_size_multiplier = 1.0
        self.daily_date = ""
        self.daily_halted = False
        self.drawdown_reduced = False
        self.drawdown_halted = False
        self.baseline_atr = 0
        self.volatility_halted = False
        self.forced_analysis_remaining = 0

        for sig in signals:
            sig_time_ms = self._ts_to_ms(sig["timestamp"])
            signal = sig["signal"]

            # If in position, advance simulation to this signal's time
            if self.in_position:
                self._check_position_exit(sig_time_ms)

            # v3.0: Handle CLOSE/REDUCE signals while in position
            if self.in_position and signal in ("CLOSE", "REDUCE"):
                current_price = sig.get("entry_price") or self._get_price_at(sig_time_ms)
                if current_price > 0:
                    # Find nearest bar for exit
                    exit_bar = {"open": current_price, "close": current_price,
                                "high": current_price, "low": current_price}
                    for b in self.bars_1m:
                        if b["open_time"] >= sig_time_ms:
                            exit_bar = b
                            break

                    if signal == "CLOSE":
                        total_pnl, closed = self._close_all_layers("CLOSE", sig_time_ms, exit_bar)
                        # v3.1 fix: Include PnL from layers that exited earlier via SL/TP/trailing
                        all_layers_pnl = sum(l.get("dollar_pnl", 0) for l in self.layers)
                        self.equity += all_layers_pnl
                        self._update_peak_dd()
                        self._finalize_position_close("CLOSE", sig_time_ms, all_layers_pnl)
                        self.trades.append({
                            "timestamp": sig["timestamp"], "signal": "CLOSE",
                            "confidence": sig["confidence"],
                            "entry_price": 0, "atr": 0,
                            "outcome": "CLOSE", "pnl_pct": 0,
                            "layers_closed": len(closed),
                            "dollar_pnl": round(all_layers_pnl, 4),
                            "equity_after": round(self.equity, 2),
                            "blocked_originally": sig.get("blocked", False),
                        })
                    elif signal == "REDUCE":
                        total_pnl, closed = self._reduce_layers_lifo(sig_time_ms, exit_bar, fraction=0.5)
                        # v3.1 fix: Include PnL from layers that exited earlier
                        remaining = [l for l in self.layers if "exit_type" not in l]
                        if not remaining:
                            # All layers closed — use cumulative PnL from all layers
                            all_layers_pnl = sum(l.get("dollar_pnl", 0) for l in self.layers)
                            self.equity += all_layers_pnl
                        else:
                            # Some layers still open — only add REDUCE PnL
                            self.equity += total_pnl
                        self._update_peak_dd()
                        if not remaining:
                            self._finalize_position_close("REDUCE", sig_time_ms,
                                                          sum(l.get("dollar_pnl", 0) for l in self.layers))
                        reported_pnl = total_pnl if remaining else sum(l.get("dollar_pnl", 0) for l in self.layers)
                        self.trades.append({
                            "timestamp": sig["timestamp"], "signal": "REDUCE",
                            "confidence": sig["confidence"],
                            "entry_price": 0, "atr": 0,
                            "outcome": "REDUCE", "pnl_pct": 0,
                            "layers_closed": len(closed),
                            "dollar_pnl": round(reported_pnl, 4),
                            "equity_after": round(self.equity, 2),
                            "blocked_originally": sig.get("blocked", False),
                        })
                continue

            # v3.0: Pyramiding — same direction signal while in position
            if self.in_position and signal in ("LONG", "SHORT"):
                if self._can_pyramid(sig, sig_time_ms):
                    entry_price = sig.get("entry_price") or self._get_price_at(sig_time_ms)
                    atr = sig.get("atr") or self._get_atr_at(sig_time_ms)
                    if entry_price and entry_price > 0 and atr and atr > 0:
                        # Align add-on entry to 30M bar close (same as initial position)
                        addon_entry_time = sig_time_ms
                        for b in self.bars_30m:
                            if b["open_time"] <= sig_time_ms <= b.get("close_time", b["open_time"] + 1799999):
                                addon_entry_time = b.get("close_time", b["open_time"] + 1799999) + 1
                                break
                        open_layers = [l for l in self.layers if "exit_type" not in l]
                        layer_idx = len(self.layers)
                        is_ct = self.position_is_counter_trend
                        new_layer = self._create_layer(
                            entry_price, signal, atr, sig["confidence"],
                            sig.get("risk", "NORMAL"), addon_entry_time, is_ct, layer_idx,
                            is_addon=True,
                        )
                        if new_layer:
                            self.layers.append(new_layer)
                            self.total_layers_opened += 1
                            self.pyramiding_events += 1
                            self.trades.append({
                                "timestamp": sig["timestamp"], "signal": signal,
                                "confidence": sig["confidence"],
                                "entry_price": round(entry_price, 2),
                                "atr": round(atr, 2),
                                "sl_price": round(new_layer["sl_price"], 2),
                                "tp_price": round(new_layer["tp_price"], 2),
                                "rr_target": new_layer["rr_target"],
                                "outcome": "PYRAMID",
                                "pnl_pct": 0,
                                "layer_idx": layer_idx,
                                "layers_open": len(open_layers) + 1,
                                "size_multiplier": round(new_layer["effective_size"], 3),
                                "equity_after": round(self.equity, 2),
                                "blocked_originally": sig.get("blocked", False),
                            })
                        continue
                # Not eligible for pyramid — skip as position_occupied
                # (opposite direction or conditions not met)

            # Check all production gates (includes position_occupied for non-pyramiding)
            skip_reason = self._check_gates(sig, sig_time_ms)
            if skip_reason:
                self.skip_log.append({
                    "timestamp": sig["timestamp"],
                    "signal": sig["signal"],
                    "confidence": sig["confidence"],
                    "reason": skip_reason,
                })
                continue

            # Only LONG/SHORT signals reach here (HOLD filtered in extraction)
            if signal not in ("LONG", "SHORT"):
                continue

            # Signal passed all gates — this is an AI analysis cycle
            entry_price = sig.get("entry_price") or self._get_price_at(sig_time_ms)
            atr = sig.get("atr") or self._get_atr_at(sig_time_ms)

            if not entry_price or entry_price <= 0 or not atr or atr <= 0:
                self.skip_log.append({
                    "timestamp": sig["timestamp"],
                    "signal": sig["signal"],
                    "confidence": sig["confidence"],
                    "reason": "no_price_or_atr",
                })
                continue

            # Update gate state (AI analysis happened)
            self._update_gate_state_after_analysis(sig, sig_time_ms)

            # ET-rejected signals
            if sig.get("blocked") and sig.get("block_reason") == "et_reject":
                self.trades.append({
                    "timestamp": sig["timestamp"],
                    "signal": sig["signal"],
                    "confidence": sig["confidence"],
                    "entry_price": round(entry_price, 2),
                    "atr": round(atr, 2),
                    "outcome": "FILTERED",
                    "pnl_pct": 0,
                    "skip_reason": "et_reject",
                    "blocked_originally": sig.get("blocked", False),
                })
                continue

            # Counter-trend detection
            is_ct = self._is_counter_trend(signal, sig_time_ms)

            # Confidence check (via _calc_sltp returning None)
            sltp = self._calc_sltp(entry_price, signal, atr, sig["confidence"],
                                   is_counter_trend=is_ct)
            if sltp is None:
                self.trades.append({
                    "timestamp": sig["timestamp"],
                    "signal": signal,
                    "confidence": sig["confidence"],
                    "entry_price": round(entry_price, 2),
                    "atr": round(atr, 2),
                    "outcome": "FILTERED",
                    "pnl_pct": 0,
                    "skip_reason": f"confidence {sig['confidence']} < {self.params['min_confidence']}",
                    "blocked_originally": sig.get("blocked", False),
                })
                continue

            # Determine entry time (wait for bar close)
            entry_time_ms = sig_time_ms
            for b in self.bars_30m:
                if b["open_time"] <= sig_time_ms <= b.get("close_time", b["open_time"] + 1799999):
                    entry_time_ms = b.get("close_time", b["open_time"] + 1799999) + 1
                    break

            # v3.0: Create first layer
            layer = self._create_layer(
                entry_price, signal, atr, sig["confidence"],
                sig.get("risk", "NORMAL"), entry_time_ms, is_ct, 0
            )
            if not layer:
                continue

            # Open position with first layer
            self.in_position = True
            self.position_side = signal
            self.position_entry_time_ms = entry_time_ms
            self.position_is_counter_trend = is_ct
            self.layers = [layer]
            self.total_layers_opened += 1

            # Record trade entry
            self.trades.append({
                "timestamp": sig["timestamp"],
                "signal": signal,
                "confidence": sig["confidence"],
                "entry_price": round(entry_price, 2),
                "atr": round(atr, 2),
                "sl_price": round(layer["sl_price"], 2),
                "tp_price": round(layer["tp_price"], 2),
                "rr_target": layer["rr_target"],
                "is_counter_trend": is_ct,
                "blocked_originally": sig.get("blocked", False),
                "size_multiplier": round(layer["effective_size"], 3),
                "layer_idx": 0,
                "outcome": "OPEN",
                "pnl_pct": 0,
                "equity_after": round(self.equity, 2),
            })

        # Final position check — if still in position at end
        if self.in_position and self.bars_1m:
            last_bar = self.bars_1m[-1]
            self._check_position_exit(last_bar["open_time"])

        return self._build_results()

    def _check_position_exit(self, current_time_ms: int):
        """Check if all position layers have exited by given time (via SL/TP/trailing/TB)."""
        if not self.in_position or not self.layers:
            return

        result = self._scan_layers_tick_by_tick(current_time_ms)
        if result:
            # Record the position close event
            # Compute aggregate pnl_pct from all layer pnl_pcts weighted by effective_size
            closed_layers = [l for l in self.layers if "exit_type" in l]
            if closed_layers:
                total_weight = sum(l["effective_size"] for l in closed_layers)
                if total_weight > 0:
                    weighted_pnl_pct = sum(
                        l.get("pnl_pct", 0) * l["effective_size"] for l in closed_layers
                    ) / total_weight
                else:
                    weighted_pnl_pct = 0
            else:
                weighted_pnl_pct = 0

            self.trades.append({
                "timestamp": self._ms_to_str(result["time_ms"]),
                "signal": self.position_side or "",
                "confidence": self.layers[0]["confidence"] if self.layers else "",
                "entry_price": round(self.layers[0]["entry_price"], 2) if self.layers else 0,
                "atr": round(self.layers[0]["atr"], 2) if self.layers else 0,
                "sl_price": round(self.layers[0]["sl_price"], 2) if self.layers else 0,
                "tp_price": round(self.layers[0]["tp_price"], 2) if self.layers else 0,
                "outcome": result["event"],
                "pnl_pct": round(weighted_pnl_pct, 4),
                "dollar_pnl": round(result["dollar_pnl"], 4),
                "layers_closed": result["layers_closed"],
                "total_layers": len(self.layers),
                "equity_after": round(self.equity, 2),
                "blocked_originally": False,
            })

            # Finalize position close
            self._finalize_position_close(
                result["event"], result["time_ms"], result["dollar_pnl"]
            )

    def _build_results(self) -> Dict:
        """Build summary results with v3.0 multi-layer statistics."""
        # Exclude FILTERED, PYRAMID (add-on entries), OPEN
        active = [t for t in self.trades if t["outcome"] not in ("FILTERED", "PYRAMID")]
        # Count outcomes from all records that aren't entry/pyramid markers
        all_outcomes = [t for t in self.trades if t["outcome"] not in ("FILTERED", "PYRAMID", "OPEN")]

        if not all_outcomes:
            return {
                "trades": self.trades,
                "skip_log": self.skip_log,
                "summary": {
                    "total_signals_processed": len(self.trades) + len(self.skip_log),
                    "signals_skipped_by_gates": len(self.skip_log),
                    "trades_executed": 0,
                    "trades_closed": 0,
                    "tp": 0, "sl": 0, "tb": 0, "trailing": 0, "trailing_exits": 0,
                    "close": 0, "reduce": 0, "win_rate": 0,
                    "total_layers": self.total_layers_opened,
                    "pyramiding_events": self.pyramiding_events,
                    "trailing_activations": self.trailing_activations,
                },
                "equity": self.equity,
                "pnl": round(self.equity - 100, 2),
                "max_dd": self.max_dd,
                "skip_reasons": {},
            }

        # Position close events are recorded in _check_position_exit
        # OPEN entries are just markers for when positions were opened
        tps = sum(1 for t in all_outcomes if t.get("outcome") == "TP")
        sls = sum(1 for t in all_outcomes if t.get("outcome") == "SL")
        tbs = sum(1 for t in all_outcomes if t.get("outcome") == "TIME_BARRIER")
        trailing_exits = self.trailing_exits
        closes = sum(1 for t in all_outcomes if t.get("outcome") == "CLOSE")
        reduces = sum(1 for t in all_outcomes if t.get("outcome") == "REDUCE")
        # Dollar PnL based trades
        dollar_pnl_trades = [t for t in all_outcomes if t.get("dollar_pnl") is not None]
        wins = sum(1 for t in dollar_pnl_trades if t.get("dollar_pnl", 0) > 0)
        total_positions = len(dollar_pnl_trades) if dollar_pnl_trades else len(all_outcomes)
        wr = wins / total_positions * 100 if total_positions else 0

        skip_reasons = Counter(s["reason"].split(" (")[0] for s in self.skip_log)

        return {
            "trades": self.trades,
            "skip_log": self.skip_log,
            "summary": {
                "total_signals_processed": len(self.trades) + len(self.skip_log),
                "signals_skipped_by_gates": len(self.skip_log),
                "trades_executed": len(active),
                "trades_filtered": sum(1 for t in self.trades if t["outcome"] == "FILTERED"),
                "trades_closed": total_positions,
                "tp": tps,
                "sl": sls,
                "tb": tbs,
                "trailing": trailing_exits,
                "close": closes,
                "reduce": reduces,
                "win_rate": round(wr, 1),
                # v3.0 stats
                "total_layers": self.total_layers_opened,
                "pyramiding_events": self.pyramiding_events,
                "trailing_activations": self.trailing_activations,
                "trailing_exits": trailing_exits,
            },
            "equity": round(self.equity, 2),
            "pnl": round(self.equity - 100, 2),
            "max_dd": round(self.max_dd, 2),
            "skip_reasons": dict(skip_reasons),
        }


# ============================================================================
# Journalctl signal extraction (unchanged from v1)
# ============================================================================
def extract_signals_from_journalctl(days: int = 30) -> List[Dict]:
    """Extract AI signals from journalctl logs of nautilus-trader service."""
    logger.info(f"Extracting signals from journalctl (last {days} days)...")

    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    try:
        result = subprocess.run(
            ["journalctl", "-u", "nautilus-trader", "--since", since,
             "--no-pager", "-o", "short-iso"],
            capture_output=True, text=True, timeout=120,
        )
        lines = result.stdout.splitlines()
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning(f"journalctl failed: {e}")
        return []

    logger.info(f"Got {len(lines)} log lines")

    signals = []
    current_signal = None

    judge_pattern = re.compile(
        r'Judge [Dd]ecision:\s*(LONG|SHORT|HOLD|CLOSE|REDUCE)\s*'
        r'(?:\|\s*Confidence:\s*(HIGH|MEDIUM|LOW))?'
        r'(?:\|\s*Risk:\s*(HIGH|MEDIUM|LOW|NORMAL))?'
    )
    judge_pattern2 = re.compile(
        r'Judge decision:\s*(LONG|SHORT|HOLD|CLOSE|REDUCE)\s*'
        r'\(\s*(HIGH|MEDIUM|LOW)\s*confidence\s*\)'
    )
    multi_agent_pattern = re.compile(
        r'Multi-agent decision:\s*(LONG|SHORT|HOLD|CLOSE|REDUCE)\s*'
        r'\(\s*(HIGH|MEDIUM|LOW)\s*confidence\s*\)'
    )
    # v10.0: Mechanical mode decision pattern
    mechanical_pattern = re.compile(
        r'Mechanical decision:\s*(LONG|SHORT|HOLD|CLOSE)\s+'
        r'(HIGH|MEDIUM|LOW)'
    )
    blocked_pattern = re.compile(
        r'Signal confidence\s+(HIGH|MEDIUM|LOW)\s+below minimum\s+(HIGH|MEDIUM|LOW)'
    )
    final_signal_pattern = re.compile(
        r'(?:Final signal|📊\s*Signal):\s*(LONG|SHORT|HOLD|CLOSE|REDUCE)'
    )
    et_reject_pattern = re.compile(
        r'Entry Timing REJECT:\s*(LONG|SHORT)\s*→\s*HOLD'
    )
    et_adjust_pattern = re.compile(
        r'Entry Timing adjusted confidence:\s*(HIGH|MEDIUM|LOW)\s*→\s*(HIGH|MEDIUM|LOW)'
    )
    # v38.0: Confidence chain log captures the DEFINITIVE final confidence
    # Format: "[ctx_id] Confidence chain: judge:HIGH(AI) → entry_timing:MEDIUM(AI)"
    confidence_chain_pattern = re.compile(
        r'Confidence chain:.*?(\w+):(HIGH|MEDIUM|LOW)\(\w+\)\s*$'
    )
    sltp_pattern = re.compile(
        r'SL/TP validated.*?Price=\$?([\d,.]+).*?SL=\$?([\d,.]+).*?TP=\$?([\d,.]+).*?R/R=([\d.]+)'
    )
    mech_sltp_pattern = re.compile(
        r'Mechanical SL/TP.*?(?:entry|price)[=:]\s*\$?([\d,.]+).*?ATR[=:]\s*\$?([\d,.]+)'
    )
    ts_pattern = re.compile(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{4})')

    for line in lines:
        ts_match = ts_pattern.match(line)
        if not ts_match:
            continue
        timestamp_str = ts_match.group(1)

        try:
            ts_clean = timestamp_str
            if len(ts_clean) > 19 and ts_clean[-5] in ('+', '-') and ts_clean[-4:].isdigit():
                ts_clean = ts_clean[:-2] + ':' + ts_clean[-2:]
            dt = datetime.fromisoformat(ts_clean)
            ts_iso = dt.strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            ts_iso = timestamp_str[:19]

        m = judge_pattern.search(line)
        if m:
            signal, confidence, risk = m.group(1), m.group(2), m.group(3)
            if signal in ("LONG", "SHORT"):
                # v38.0: Do NOT default to "LOW" when regex fails to capture confidence.
                # None means "unknown" — will be resolved by confidence_chain or filtered out.
                current_signal = {
                    "timestamp": ts_iso,
                    "signal": signal,
                    "confidence": confidence,  # None if not captured (was: or "LOW")
                    "confidence_source": "judge",  # Track where confidence came from
                    "risk": risk or "NORMAL",
                    "source": "judge_decision",
                    "entry_price": None,
                    "atr": None,
                    "blocked": False,
                    "block_reason": None,
                }
            continue

        m = judge_pattern2.search(line)
        if m:
            signal, confidence = m.group(1), m.group(2)
            if signal in ("LONG", "SHORT"):
                current_signal = {
                    "timestamp": ts_iso,
                    "signal": signal,
                    "confidence": confidence,
                    "confidence_source": "judge",
                    "risk": "NORMAL",
                    "source": "judge_decision_v2",
                    "entry_price": None,
                    "atr": None,
                    "blocked": False,
                    "block_reason": None,
                }
            continue

        # v10.0: Mechanical mode decision
        m = mechanical_pattern.search(line)
        if m:
            signal, confidence = m.group(1), m.group(2)
            if signal in ("LONG", "SHORT"):
                current_signal = {
                    "timestamp": ts_iso,
                    "signal": signal,
                    "confidence": confidence,
                    "confidence_source": "mechanical",
                    "risk": "NORMAL",
                    "source": "mechanical_decision",
                    "entry_price": None,
                    "atr": None,
                    "blocked": False,
                    "block_reason": None,
                }
            continue

        m = et_reject_pattern.search(line)
        if m and current_signal:
            current_signal["blocked"] = True
            current_signal["block_reason"] = "et_reject"
            current_signal["confidence_source"] = "et_reject"
            signals.append(current_signal)
            current_signal = None
            continue

        m = et_adjust_pattern.search(line)
        if m and current_signal:
            current_signal["confidence_original"] = m.group(1)
            current_signal["confidence"] = m.group(2)
            current_signal["confidence_source"] = "entry_timing"
            continue

        # v38.0: Parse Confidence chain log — this is the DEFINITIVE final confidence
        # Format: "[ctx_id] Confidence chain: judge:HIGH(AI) → entry_timing:MEDIUM(AI)"
        # The LAST step in the chain is the final confidence.
        m = confidence_chain_pattern.search(line)
        if m and current_signal:
            chain_final_conf = m.group(2)  # Last step's confidence value
            if current_signal["confidence"] != chain_final_conf:
                current_signal["confidence_original"] = current_signal.get("confidence_original") or current_signal["confidence"]
                current_signal["confidence"] = chain_final_conf
                current_signal["confidence_source"] = "confidence_chain"
            continue

        m = blocked_pattern.search(line)
        if m and current_signal:
            # The confidence in blocked_pattern IS the final confidence (post-ET)
            current_signal["confidence"] = m.group(1)
            current_signal["confidence_source"] = "blocked_gate"
            current_signal["blocked"] = True
            current_signal["block_reason"] = f"min_confidence ({m.group(1)} < {m.group(2)})"
            signals.append(current_signal)
            current_signal = None
            continue

        m = sltp_pattern.search(line)
        if m and current_signal:
            current_signal["entry_price"] = float(m.group(1).replace(",", ""))
            current_signal["executed"] = True
            signals.append(current_signal)
            current_signal = None
            continue

        m = mech_sltp_pattern.search(line)
        if m and current_signal:
            current_signal["entry_price"] = float(m.group(1).replace(",", ""))
            current_signal["atr"] = float(m.group(2).replace(",", ""))
            continue

        if current_signal and ("on_timer" in line or "Phase 1:" in line or "Phase 0:" in line):
            if current_signal.get("signal") in ("LONG", "SHORT", "CLOSE", "REDUCE"):
                current_signal["block_reason"] = current_signal.get("block_reason", "unknown")
                signals.append(current_signal)
            current_signal = None

    if current_signal and current_signal.get("signal") in ("LONG", "SHORT", "CLOSE", "REDUCE"):
        signals.append(current_signal)

    # v38.0: Data quality reporting
    dir_signals = sum(1 for s in signals if s["signal"] in ("LONG", "SHORT"))
    close_signals = sum(1 for s in signals if s["signal"] in ("CLOSE", "REDUCE"))
    conf_none = sum(1 for s in signals if s.get("confidence") is None)
    conf_sources = Counter(s.get("confidence_source", "unknown") for s in signals)
    logger.info(f"Extracted {len(signals)} signals ({dir_signals} directional, {close_signals} close/reduce)")
    if conf_none > 0:
        logger.warning(f"  ⚠️ {conf_none} signals have NULL confidence (regex failed to capture)")
    logger.info(f"  Confidence sources: {dict(conf_sources)}")

    # v38.0: Filter out signals with NULL confidence (unparseable → unreliable)
    before_filter = len(signals)
    signals = [s for s in signals if s.get("confidence") is not None]
    if before_filter != len(signals):
        logger.warning(f"  Filtered {before_filter - len(signals)} signals with NULL confidence")

    return signals


def load_hold_counterfactuals(path: str) -> List[Dict]:
    """Load hold counterfactual records as additional signal data.

    v38.0: Only include records with explicit proposed_confidence.
    Records without confidence are filtered (was: defaulted to "LOW").
    """
    try:
        with open(path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.info(f"No hold_counterfactuals.json: {e}")
        return []

    signals = []
    skipped_no_conf = 0
    for record in data:
        if record.get("proposed_signal") in ("LONG", "SHORT"):
            conf = record.get("proposed_confidence")
            if conf not in ("HIGH", "MEDIUM", "LOW"):
                # v38.0: Skip records without explicit confidence
                # (was: defaulted to "LOW", polluting LOW signal count)
                skipped_no_conf += 1
                continue
            signals.append({
                "timestamp": record.get("timestamp", ""),
                "signal": record["proposed_signal"],
                "confidence": conf,
                "confidence_source": "hold_counterfactual",
                "risk": "NORMAL",
                "source": "hold_counterfactual",
                "entry_price": record.get("entry_price"),
                "atr": None,
                "blocked": True,
                "block_reason": record.get("hold_source", "unknown"),
            })

    logger.info(f"Loaded {len(signals)} signals from hold_counterfactuals.json"
                f" (skipped {skipped_no_conf} without explicit confidence)")
    return signals


# ============================================================================
# Binance API
# ============================================================================
def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> List[Dict]:
    """Fetch klines with pagination and retry."""
    all_bars = []
    current_start = start_ms
    limit = 1500

    while current_start < end_ms:
        url = (
            f"{BINANCE_FUTURES_BASE}/fapi/v1/klines"
            f"?symbol={symbol}&interval={interval}"
            f"&startTime={current_start}&endTime={end_ms}&limit={limit}"
        )

        data = None
        for attempt in range(4):
            try:
                req = Request(url, headers={"User-Agent": "AlgVex-Backtest/3.0"})
                with urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read())
                break
            except (URLError, OSError) as e:
                wait = 2 ** (attempt + 1)
                logger.warning(f"API fail (attempt {attempt+1}): {e}, retry in {wait}s")
                time.sleep(wait)
                if attempt == 3:
                    raise RuntimeError(f"Failed after 4 retries: {e}")

        if not data:
            break

        for k in data:
            all_bars.append({
                "open_time": k[0],
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "close_time": k[6],
            })

        current_start = data[-1][6] + 1
        if len(data) < limit:
            break
        time.sleep(0.2)

    return all_bars


# ============================================================================
# Main backtest
# ============================================================================
def run_backtest(signals: List[Dict], use_gates: bool = True):
    """Run v37.1 backtest v3.0 with multi-layer + trailing stop."""

    if not signals:
        logger.error("No signals to backtest!")
        return

    signals.sort(key=lambda s: s["timestamp"])

    total = len(signals)
    dir_dist = Counter(s["signal"] for s in signals)
    conf_dist = Counter(s["confidence"] for s in signals)
    blocked_count = sum(1 for s in signals if s.get("blocked"))
    source_dist = Counter(s.get("source", "unknown") for s in signals)
    block_reason_dist = Counter(
        s.get("block_reason", "none") for s in signals if s.get("blocked")
    )

    print("\n" + "=" * 120)
    print(f"  AlgVex v37.1 回测 v3.0 — 多层加仓 + Trailing Stop {'(全部 gate 启用)' if use_gates else '(gate 关闭, v1 模式)'}")
    print("=" * 120)
    print(f"  v3.0: 多层加仓(LIFO) | Trailing Stop(1.5R激活, 4H ATR) | CLOSE/REDUCE信号 | 手续费0.15%/RT | SL滑点0.03%")
    print(f"  交易对: {SYMBOL}")
    print(f"  信号数量: {total}")
    print(f"  时间范围: {signals[0]['timestamp']} → {signals[-1]['timestamp']}")
    print(f"  方向分布: {dict(dir_dist)}")
    print(f"  信心分布: {dict(conf_dist)}")
    print(f"  被拦截 (ET/conf): {blocked_count}/{total} ({blocked_count/total*100:.0f}%)")
    print(f"  拦截原因: {dict(block_reason_dist)}")
    print(f"  数据来源: {dict(source_dist)}")

    first_dt = datetime.fromisoformat(signals[0]["timestamp"]).replace(tzinfo=timezone.utc)
    last_dt = datetime.fromisoformat(signals[-1]["timestamp"]).replace(tzinfo=timezone.utc)
    data_start = first_dt - timedelta(days=1)
    data_end = last_dt + timedelta(hours=14)
    start_ms = int(data_start.timestamp() * 1000)
    end_ms = int(data_end.timestamp() * 1000)
    days_span = (last_dt - first_dt).total_seconds() / 86400
    print(f"  跨度: {days_span:.1f} 天")

    # Fetch klines
    logger.info("Fetching 30M klines for ATR calculation...")
    bars_30m = fetch_klines(SYMBOL, "30m", start_ms, end_ms)
    logger.info(f"Got {len(bars_30m)} 30M bars")

    logger.info(f"Fetching 1M klines ({days_span:.0f} days, may take a few minutes)...")
    bars_1m = fetch_klines(SYMBOL, "1m", start_ms, end_ms)
    logger.info(f"Got {len(bars_1m)} 1M bars")

    # Fill missing entry prices and ATR
    filled_price = 0
    filled_atr = 0
    for sig in signals:
        sig_dt = datetime.fromisoformat(sig["timestamp"]).replace(tzinfo=timezone.utc)
        sig_ms = int(sig_dt.timestamp() * 1000)

        if sig["entry_price"] is None:
            for b in bars_30m:
                if b["open_time"] <= sig_ms <= b["close_time"]:
                    sig["entry_price"] = b["close"]
                    filled_price += 1
                    break
            if sig["entry_price"] is None:
                for i, b in enumerate(bars_30m):
                    if b["open_time"] > sig_ms and i > 0:
                        sig["entry_price"] = bars_30m[i - 1]["close"]
                        filled_price += 1
                        break

        if sig["atr"] is None or sig["atr"] <= 0:
            bar_idx = None
            for i, b in enumerate(bars_30m):
                if b["open_time"] <= sig_ms <= b["close_time"]:
                    bar_idx = i
                    break
                elif b["open_time"] > sig_ms:
                    bar_idx = i - 1 if i > 0 else 0
                    break
            if bar_idx is not None:
                atr_bars = bars_30m[max(0, bar_idx - 15): bar_idx + 1]
                atr_val = calculate_atr_wilder(atr_bars, period=14)
                if atr_val > 0:
                    sig["atr"] = atr_val
                    filled_atr += 1

    logger.info(f"Filled {filled_price} entry prices, {filled_atr} ATR values from klines")

    valid_signals = [s for s in signals if s["entry_price"] and s.get("atr") and s["atr"] > 0]
    skipped = total - len(valid_signals)
    if skipped:
        logger.warning(f"Skipped {skipped} signals due to missing price/ATR data")
    print(f"  有效信号: {len(valid_signals)}/{total}")

    if not valid_signals:
        logger.error("No valid signals after data filling!")
        return

    # ========================================================================
    # Run simulations: v44 (current production) vs v39 (prior), each with/without gates
    # ========================================================================
    results = {}

    logger.info(f"Backtesting: v44.0 (current production) {'with' if use_gates else 'without'} production gates...")
    results["v44_gates"] = ProductionSimulator(PLAN_V44, PRODUCTION_GATES, bars_1m, bars_30m, use_gates=use_gates).run(valid_signals)

    logger.info("Backtesting: v44.0 (current production) without gates (baseline)...")
    results["v44_nogates"] = ProductionSimulator(PLAN_V44, PRODUCTION_GATES, bars_1m, bars_30m, use_gates=False).run(valid_signals)

    logger.info(f"Backtesting: v39.0 (prior production) {'with' if use_gates else 'without'} production gates...")
    results["v39_gates"] = ProductionSimulator(PLAN_V39, PRODUCTION_GATES, bars_1m, bars_30m, use_gates=use_gates).run(valid_signals)

    logger.info("Backtesting: v39.0 (prior production) without gates...")
    results["v39_nogates"] = ProductionSimulator(PLAN_V39, PRODUCTION_GATES, bars_1m, bars_30m, use_gates=False).run(valid_signals)

    # ========================================================================
    # Print results
    # ========================================================================
    print(f"\n{'=' * 130}")
    print(f"  对比总览 (v3.0: 多层加仓+Trailing | 手续费 0.15%/RT | 10x 杠杆)")
    print(f"{'=' * 130}")
    print(f"  {'方案':<45} {'信号':>4} {'跳过':>5} {'仓位':>4} {'TP':>3} {'SL':>3} {'TB':>3} {'Trail':>5} "
          f"{'加仓':>4} {'胜率':>6} {'PnL':>8} {'回撤':>7} {'净值':>8}")
    print(f"  {'-' * 125}")

    for key, label in [
        ("v44_gates", f"★ v44.0 (current) + Gate ({'ON' if use_gates else 'OFF'})"),
        ("v44_nogates", "  v44.0 (current) 无 Gate (baseline)"),
        ("v39_gates", f"  v39.0 (prior) + Gate ({'ON' if use_gates else 'OFF'})"),
        ("v39_nogates", "  v39.0 (prior) 无 Gate"),
    ]:
        r = results[key]
        s = r["summary"]
        trades_closed = s.get("trades_closed", 0)
        wr = s.get("win_rate", 0)
        print(
            f"  {label:<45} {s['total_signals_processed']:>4} "
            f"{s['signals_skipped_by_gates']:>5} "
            f"{trades_closed:>4} "
            f"{s.get('tp', 0):>3} {s.get('sl', 0):>3} {s.get('tb', 0):>3} "
            f"{s.get('trailing_exits', 0):>5} "
            f"{s.get('pyramiding_events', 0):>4} "
            f"{wr:>5.1f}% "
            f"{r.get('pnl', 0):>+7.2f}% "
            f"{r.get('max_dd', 0):>6.2f}% "
            f"{r.get('equity', 100):>7.2f}"
        )

    # Skip reason breakdown for gated simulations
    for key, label in [("v44_gates", "v44.0"), ("v39_gates", "v39.0")]:
        r = results[key]
        skip_reasons = r.get("skip_reasons", {})
        if skip_reasons:
            print(f"\n  {label} Gate 跳过原因:")
            for reason, count in sorted(skip_reasons.items(), key=lambda x: -x[1]):
                print(f"    {reason:<45} {count:>4} ({count/r['summary']['total_signals_processed']*100:.1f}%)")

    # ── Per-trade detail for v44 with gates ──
    plan_detail = results["v44_gates"]
    active_trades = [t for t in plan_detail["trades"] if t["outcome"] != "FILTERED"]

    if active_trades:
        print(f"\n{'=' * 150}")
        print(f"  v44.0 + 生产 Gate — 每笔详情 ({len(active_trades)} 笔)")
        print(f"{'=' * 150}")
        print(f"  {'#':>3} {'时间':>14} {'方向':>6} {'信心':>4} {'层':>2} {'Size':>5} {'入场':>12} "
              f"{'SL':>12} {'TP':>12} {'结果':>8} {'$PnL':>10} {'净值':>8}")
        print(f"  {'-' * 148}")

        for i, t in enumerate(active_trades, 1):
            outcome_str = t["outcome"]
            if outcome_str == "TP":
                outcome_str = "✅TP"
            elif outcome_str == "SL":
                outcome_str = "❌SL"
            elif outcome_str == "TIME_BARRIER":
                outcome_str = "⏰TB"
            elif outcome_str == "TRAILING":
                outcome_str = "🔄Trail"
            elif outcome_str == "PYRAMID":
                outcome_str = "📈加仓"
            elif outcome_str == "CLOSE":
                outcome_str = "🔒平仓"
            elif outcome_str == "REDUCE":
                outcome_str = "📉减仓"
            elif outcome_str == "OPEN":
                outcome_str = "🟢开仓"

            size_m = t.get("size_multiplier", 0)
            layer_idx = t.get("layer_idx", 0)
            dollar_pnl = t.get("dollar_pnl", 0)
            print(
                f"  {i:>3} {t['timestamp'][5:19]:>14} {t['signal']:>6} {t.get('confidence', ''):>4} "
                f"{layer_idx:>2} {size_m:>5.2f} "
                f"${t.get('entry_price', 0):>11,.2f} "
                f"${t.get('sl_price', 0):>11,.2f} ${t.get('tp_price', 0):>11,.2f} "
                f"{outcome_str:>8} {dollar_pnl:>+9.4f} "
                f"{t.get('equity_after', 100):>7.2f}"
            )

    # ── Confidence breakdown for v44 gated ──
    closed_trades = [t for t in active_trades if t["outcome"] not in ("OPEN", "FILTERED", "NO_DATA")]
    if closed_trades:
        print(f"\n{'=' * 120}")
        print(f"  v44.0 + Gate: 按信心等级分组")
        print(f"{'=' * 120}")

        for conf_level in ["HIGH", "MEDIUM", "LOW"]:
            subset = [t for t in closed_trades if t["confidence"] == conf_level]
            if not subset:
                print(f"  {conf_level}: 无交易")
                continue
            wins = sum(1 for t in subset if t.get("dollar_pnl", 0) > 0)
            wr = wins / len(subset) * 100
            raw_pnl = sum(t.get("dollar_pnl", 0) for t in subset)
            avg_pnl = raw_pnl / len(subset)
            tps = sum(1 for t in subset if t["outcome"] == "TP")
            sls = sum(1 for t in subset if t["outcome"] == "SL")
            tbs = sum(1 for t in subset if t["outcome"] == "TIME_BARRIER")
            dir_d = Counter(t["signal"] for t in subset)
            print(f"  {conf_level}: {len(subset)} 笔 | 方向: {dict(dir_d)}")
            print(f"    TP={tps} SL={sls} TB={tbs} | 胜率={wr:.1f}% | avg PnL={avg_pnl:+.4f}%/笔")

    # ── Direction breakdown ──
    if closed_trades:
        print(f"\n{'=' * 120}")
        print(f"  v44.0 + Gate: 按方向分组")
        print(f"{'=' * 120}")
        for direction in ["LONG", "SHORT"]:
            subset = [t for t in closed_trades if t["signal"] == direction]
            if not subset:
                print(f"  {direction}: 无交易")
                continue
            wins = sum(1 for t in subset if t.get("dollar_pnl", 0) > 0)
            wr = wins / len(subset) * 100
            avg_pnl = sum(t.get("dollar_pnl", 0) for t in subset) / len(subset)
            tps = sum(1 for t in subset if t["outcome"] == "TP")
            sls = sum(1 for t in subset if t["outcome"] == "SL")
            tbs = sum(1 for t in subset if t["outcome"] == "TIME_BARRIER")
            print(f"  {direction}: {len(subset)} 笔 | TP={tps} SL={sls} TB={tbs} | 胜率={wr:.1f}% | avg PnL={avg_pnl:+.4f}%/笔")

    # ── Weekly breakdown ──
    if closed_trades:
        print(f"\n{'=' * 120}")
        print(f"  v44.0 + Gate: 按周分组")
        print(f"{'=' * 120}")
        weekly = defaultdict(list)
        for t in closed_trades:
            dt = datetime.fromisoformat(t["timestamp"])
            week_key = f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}"
            weekly[week_key].append(t)

        print(f"  {'周':>10} {'笔':>4} {'胜率':>6} {'Raw PnL':>8} {'方向分布':<30}")
        print(f"  {'-' * 65}")
        for week, trades_in_week in sorted(weekly.items()):
            wins = sum(1 for t in trades_in_week if t.get("dollar_pnl", 0) > 0)
            wr = wins / len(trades_in_week) * 100
            raw_pnl = sum(t.get("dollar_pnl", 0) for t in trades_in_week)
            dir_d = Counter(t["signal"] for t in trades_in_week)
            print(f"  {week:>10} {len(trades_in_week):>4} {wr:>5.1f}% {raw_pnl:>+7.2f}% {dict(dir_d)}")

    # ── Originally blocked analysis ──
    if closed_trades:
        blocked_trades = [t for t in closed_trades if t.get("blocked_originally")]
        executed_trades = [t for t in closed_trades if not t.get("blocked_originally")]

        if blocked_trades or executed_trades:
            print(f"\n{'=' * 120}")
            print(f"  v44.0 + Gate: 原被拦截 vs 原已执行")
            print(f"{'=' * 120}")
            for label, subset in [("原被拦截 (新增)", blocked_trades), ("原已执行 (存量)", executed_trades)]:
                if not subset:
                    print(f"  {label}: 无交易")
                    continue
                wins = sum(1 for t in subset if t.get("dollar_pnl", 0) > 0)
                wr = wins / len(subset) * 100
                raw_pnl = sum(t.get("dollar_pnl", 0) for t in subset)
                tps = sum(1 for t in subset if t["outcome"] == "TP")
                sls = sum(1 for t in subset if t["outcome"] == "SL")
                tbs = sum(1 for t in subset if t["outcome"] == "TIME_BARRIER")
                dir_d = Counter(t["signal"] for t in subset)
                print(f"  {label}: {len(subset)} 笔 | 方向: {dict(dir_d)}")
                print(f"    TP={tps} SL={sls} TB={tbs} | 胜率={wr:.1f}% | Raw PnL={raw_pnl:+.2f}%")

    # ── Save results ──
    output_path = Path(__file__).parent.parent / "data" / "backtest_from_logs_result.json"
    output = {
        "backtest_time": datetime.now(timezone.utc).isoformat(),
        "version": "3.1 (multi-layer PnL fix + trailing stop)",
        "symbol": SYMBOL,
        "signals_count": len(valid_signals),
        "use_gates": use_gates,
        "date_range": {
            "start": valid_signals[0]["timestamp"],
            "end": valid_signals[-1]["timestamp"],
        },
        "production_gates": PRODUCTION_GATES,
        "params": {"v44": PLAN_V44, "v39": PLAN_V39},
        "results": {
            key: {
                "summary": r["summary"],
                "equity": r.get("equity", 100),
                "pnl": r.get("pnl", 0),
                "max_dd": r.get("max_dd", 0),
                "skip_reasons": r.get("skip_reasons", {}),
                "trades_count": len([t for t in r["trades"] if t["outcome"] != "FILTERED"]),
            }
            for key, r in results.items()
        },
    }

    os.makedirs(output_path.parent, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    logger.info(f"Results saved to {output_path}")

    print(f"\n{'=' * 120}")
    print(f"  ✅ 完成! 结果已保存到 {output_path}")
    print(f"{'=' * 120}")


# ============================================================================
# Entry point
# ============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AlgVex 回测 v3.1 (多层加仓+Trailing, v44/v39 对比)")
    parser.add_argument("--days", type=int, default=30, help="Scan last N days of logs (default 30)")
    parser.add_argument("--signals", type=str, default=None, help="Use previously exported signals JSON")
    parser.add_argument("--export-only", action="store_true", help="Only extract signals, don't run backtest")
    parser.add_argument("--no-gates", action="store_true", help="Disable production gates (v1 mode)")
    args = parser.parse_args()

    project_root = Path(__file__).parent.parent

    if args.signals:
        sig_path = Path(args.signals)
        if not sig_path.is_absolute():
            sig_path = project_root / args.signals
        logger.info(f"Loading signals from {sig_path}...")
        with open(sig_path) as f:
            signals = json.load(f)
        logger.info(f"Loaded {len(signals)} signals from file")
    else:
        signals = extract_signals_from_journalctl(days=args.days)

        cf_path = project_root / "data" / "hold_counterfactuals.json"
        cf_signals = load_hold_counterfactuals(str(cf_path))

        if cf_signals:
            existing_ts = set()
            for s in signals:
                try:
                    dt = datetime.fromisoformat(s["timestamp"])
                    existing_ts.add(int(dt.timestamp()) // 120)
                except ValueError:
                    pass

            added = 0
            for s in cf_signals:
                try:
                    dt = datetime.fromisoformat(s["timestamp"])
                    bucket = int(dt.timestamp()) // 120
                    if bucket not in existing_ts:
                        signals.append(s)
                        existing_ts.add(bucket)
                        added += 1
                except ValueError:
                    pass
            logger.info(f"Added {added} non-duplicate signals from hold_counterfactuals")

        signals.sort(key=lambda s: s["timestamp"])

    if not signals:
        logger.error("No signals found! Check if nautilus-trader service has been running.")
        logger.info("Tip: Try 'journalctl -u nautilus-trader --since \"2026-02-22\" | grep \"Judge\" | head'")
        sys.exit(1)

    export_path = project_root / "data" / "extracted_signals.json"
    os.makedirs(export_path.parent, exist_ok=True)
    with open(export_path, "w") as f:
        json.dump(signals, f, indent=2, ensure_ascii=False, default=str)
    logger.info(f"Exported {len(signals)} signals to {export_path}")

    if args.export_only:
        print(f"\n✅ Exported {len(signals)} signals to {export_path}")
        print("\nSignal summary:")
        dir_dist = Counter(s["signal"] for s in signals)
        conf_dist = Counter(s["confidence"] for s in signals)
        blocked = sum(1 for s in signals if s.get("blocked"))
        print(f"  Direction: {dict(dir_dist)}")
        print(f"  Confidence: {dict(conf_dist)}")
        print(f"  Blocked: {blocked}/{len(signals)}")
        print(f"  Date range: {signals[0]['timestamp']} to {signals[-1]['timestamp']}")
        sys.exit(0)

    run_backtest(signals, use_gates=not args.no_gates)
