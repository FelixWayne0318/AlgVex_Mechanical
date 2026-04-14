#!/usr/bin/env python3
"""
S/R Zone Effectiveness Diagnostic v3.0

Comprehensive S/R zone backtesting aligned with current system (v11.0+).
Zones are pure information for AI — no mechanical SL/TP anchoring.

Three diagnostic layers:
  Layer 1: Zone Accuracy (hold/break rate by strength, source, side, touch count)
  Layer 2: Trading Simulation (mechanical ATR SL/TP, MAE/MFE, TP reachability)
  Layer 3: Statistical Validation (Walk-Forward, Wilson CI, Bootstrap vs random)

CONSISTENCY with calibrate_hold_probability.py:
  - Same data source (Binance Futures 15M klines)
  - Same bar format (timestamp, open, high, low, close, volume, taker_buy_volume)
  - Same ATR calculation (Wilder's smoothing, period=14)
  - Same SMA/BB calculation
  - Same SRZoneCalculator params (swing=True, left/right=5, touch=True, atr_adaptive=True)
  - Same stickiness/flip cache reset per step
  - Same LOOKBACK_BARS=100, STEP_BARS=4
  - Same FORWARD_SCAN_BARS=48 as time barrier
  - Same proximity filter (ATR x 1.5) for "zone tested" determination
  - Same break detection (close-based, ATR x 0.3 margin)

Usage:
  python3 scripts/backtest_sr_zones.py                    # Full diagnostic (90 days)
  python3 scripts/backtest_sr_zones.py --days 30           # Custom period
  python3 scripts/backtest_sr_zones.py --validate          # + Walk-Forward + Bootstrap
  python3 scripts/backtest_sr_zones.py --output result.json # Custom output path
"""

import argparse
import json
import logging
import math
import os
import random
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Project root
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils.sr_zone_calculator import SRZoneCalculator, SRZone
from utils.backtest_math import calculate_atr_wilder, calculate_sma, calculate_bb

import requests

# ============================================================================
# Logging
# ============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ============================================================================
# Constants — mirrors calibrate_hold_probability.py exactly
# ============================================================================
SYMBOL = "BTCUSDT"
INTERVAL = "15m"
BINANCE_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
MAX_KLINES_PER_REQUEST = 1500
FETCH_RETRY_COUNT = 4
FETCH_TIMEOUT = 15.0
FETCH_RETRY_BASE_DELAY = 2.0

# Zone calculation — same as calibration
LOOKBACK_BARS = 100
STEP_BARS = 4
FORWARD_SCAN_BARS = 48

# Zone proximity/break thresholds — same as calibration
BREAK_THRESHOLD_ATR = 0.3
PROXIMITY_THRESHOLD_ATR = 1.5

# Mechanical SL/TP config (mirrors base.yaml)
MECHANICAL_SLTP = {
    "sl_atr_multiplier": {"HIGH": 2.0, "MEDIUM": 2.5},
    "tp_rr_target": {"HIGH": 2.5, "MEDIUM": 2.0},
    "sl_atr_multiplier_floor": 1.5,
}

# Position sizing and costs
NOTIONAL_USDT = 1000.0
ROUND_TRIP_COST_PCT = 0.08  # Binance Futures taker 0.04% x 2 sides

# Validation
BOOTSTRAP_RESAMPLES = 10000
BOOTSTRAP_SEED = 42
WF_SPLITS = 3
WF_PURGE_BARS = 16  # 4 hours purge gap between folds


# ============================================================================
# Trade result dataclass
# ============================================================================
@dataclass
class TradeResult:
    """Single trade outcome with full diagnostics."""
    entry_idx: int
    entry_time: str
    side: str
    zone_strength: str
    zone_side: str
    zone_center: float
    zone_sources: List[str]
    zone_weight: float
    zone_hold_prob: float
    zone_source_type: str
    zone_touch_count: int
    entry_price: float
    sl_price: float
    tp_price: float
    rr_target: float
    atr: float
    outcome: str           # TP, SL, TIME_BARRIER
    exit_price: float
    bars_held: int
    pnl_pct: float
    pnl_usdt: float
    # MAE/MFE diagnostics
    mae_pct: float = 0.0          # Maximum Adverse Excursion (worst drawdown)
    mfe_pct: float = 0.0          # Maximum Favorable Excursion (best unrealized profit)
    bars_to_mae: int = 0          # How fast did it reach max drawdown
    tp_reached_pct: float = 0.0   # How close it got to TP (0-100%)
    sl_distance_pct: float = 0.0  # SL distance as % of entry


# ============================================================================
# Binance API — same as calibrate_hold_probability.py
# ============================================================================
def fetch_klines(
    symbol: str = SYMBOL,
    interval: str = INTERVAL,
    limit: int = 1500,
    end_time: Optional[int] = None,
) -> List[List]:
    """Fetch raw klines from Binance Futures API with retry."""
    params: Dict[str, Any] = {
        "symbol": symbol,
        "interval": interval,
        "limit": min(limit, MAX_KLINES_PER_REQUEST),
    }
    if end_time is not None:
        params["endTime"] = end_time

    for attempt in range(FETCH_RETRY_COUNT):
        try:
            resp = requests.get(BINANCE_KLINES_URL, params=params, timeout=FETCH_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            delay = FETCH_RETRY_BASE_DELAY * (2 ** attempt)
            logger.warning(f"Kline fetch attempt {attempt + 1} failed: {e}, retry in {delay}s")
            time.sleep(delay)

    raise RuntimeError(f"Failed to fetch klines after {FETCH_RETRY_COUNT} attempts")


def fetch_bars(days: int = 90) -> List[Dict[str, Any]]:
    """Fetch N days of completed 15M bars from Binance."""
    bars_needed = days * 96
    all_klines: List[List] = []
    end_time: Optional[int] = None

    logger.info(f"Fetching {bars_needed} bars ({days} days of {INTERVAL})...")

    while len(all_klines) < bars_needed:
        batch = fetch_klines(end_time=end_time)
        if not batch:
            break

        all_klines = batch + all_klines
        end_time = batch[0][0] - 1

        remaining = bars_needed - len(all_klines)
        logger.info(f"  Fetched {len(all_klines)} bars, {max(0, remaining)} remaining...")

        if len(batch) < MAX_KLINES_PER_REQUEST:
            break
        time.sleep(0.3)

    # Strip last (incomplete) candle
    if all_klines:
        all_klines = all_klines[:-1]

    bars = []
    for k in all_klines:
        bars.append({
            "timestamp": k[0],
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
            "taker_buy_volume": float(k[9]),
        })

    logger.info(f"Total completed bars: {len(bars)}")
    return bars


# ATR, SMA, BB imported from utils.backtest_math (SSoT)
calculate_atr = calculate_atr_wilder  # alias for call-site compatibility


# ============================================================================
# Zone proximity/break — same logic as calibrate_hold_probability.py
# ============================================================================
def is_zone_approached(bar: Dict, zone: SRZone, atr: float) -> bool:
    """Check if price approached a zone (same as calibration Phase 1)."""
    proximity_margin = atr * PROXIMITY_THRESHOLD_ATR
    if zone.side == "support":
        return bar["low"] <= zone.price_high + proximity_margin
    else:
        return bar["high"] >= zone.price_low - proximity_margin


def is_zone_broken(bar: Dict, zone: SRZone, atr: float) -> bool:
    """Check if a zone is broken (same as calibration Phase 2)."""
    break_margin = atr * BREAK_THRESHOLD_ATR
    if zone.side == "support":
        return bar["close"] < zone.price_low - break_margin
    else:
        return bar["close"] > zone.price_high + break_margin


# ============================================================================
# Mechanical SL/TP (mirrors production trading_logic.py)
# ============================================================================
def calc_mechanical_sltp(
    entry_price: float,
    side: str,
    atr_value: float,
    confidence: str = "MEDIUM",
) -> Tuple[bool, float, float, float]:
    """Returns (success, sl_price, tp_price, rr_ratio)."""
    if entry_price <= 0 or atr_value <= 0:
        return False, 0.0, 0.0, 0.0

    is_long = side.upper() in ("BUY", "LONG")
    cfg = MECHANICAL_SLTP

    sl_mult = cfg["sl_atr_multiplier"].get(confidence, 2.5)
    sl_mult = max(sl_mult, cfg["sl_atr_multiplier_floor"])
    sl_distance = atr_value * sl_mult

    rr_target = cfg["tp_rr_target"].get(confidence, 2.0)
    tp_distance = sl_distance * rr_target

    if is_long:
        sl_price = entry_price - sl_distance
        tp_price = entry_price + tp_distance
    else:
        sl_price = entry_price + sl_distance
        tp_price = entry_price - tp_distance

    if sl_price <= 0 or tp_price <= 0:
        return False, 0.0, 0.0, 0.0

    return True, sl_price, tp_price, rr_target


# ============================================================================
# Forward scan with MAE/MFE tracking
# ============================================================================
def scan_trade_outcome(
    bars: List[Dict],
    entry_idx: int,
    entry_price: float,
    sl_price: float,
    tp_price: float,
    side: str,
    max_bars: int = FORWARD_SCAN_BARS,
) -> Dict[str, Any]:
    """
    Scan forward with MAE/MFE tracking.

    Returns outcome + MAE/MFE diagnostics for entry quality analysis.
    """
    is_long = side.upper() in ("BUY", "LONG")
    sl_dist = abs(entry_price - sl_price)
    tp_dist = abs(entry_price - tp_price)

    mae = 0.0       # Maximum adverse excursion (negative direction)
    mfe = 0.0       # Maximum favorable excursion (positive direction)
    bars_to_mae = 0
    best_toward_tp = 0.0  # Furthest progress toward TP (0-100%)

    for offset in range(1, max_bars + 1):
        bar_idx = entry_idx + offset
        if bar_idx >= len(bars):
            break

        bar = bars[bar_idx]

        # Track MAE/MFE using bar extremes
        if is_long:
            adverse = (entry_price - bar["low"]) / entry_price * 100
            favorable = (bar["high"] - entry_price) / entry_price * 100
            toward_tp = (bar["high"] - entry_price) / tp_dist * 100 if tp_dist > 0 else 0
        else:
            adverse = (bar["high"] - entry_price) / entry_price * 100
            favorable = (entry_price - bar["low"]) / entry_price * 100
            toward_tp = (entry_price - bar["low"]) / tp_dist * 100 if tp_dist > 0 else 0

        if adverse > mae:
            mae = adverse
            bars_to_mae = offset
        mfe = max(mfe, favorable)
        best_toward_tp = max(best_toward_tp, toward_tp)

        # Check SL/TP hit
        if is_long:
            sl_hit = bar["low"] <= sl_price
            tp_hit = bar["high"] >= tp_price
        else:
            sl_hit = bar["high"] >= sl_price
            tp_hit = bar["low"] <= tp_price

        if sl_hit and tp_hit:
            # Both in same bar — conservative: SL first
            return {
                "outcome": "SL", "exit_price": sl_price,
                "bars_held": offset, "exit_idx": bar_idx,
                "mae": mae, "mfe": mfe, "bars_to_mae": bars_to_mae,
                "tp_reached_pct": min(best_toward_tp, 100.0),
            }
        elif sl_hit:
            return {
                "outcome": "SL", "exit_price": sl_price,
                "bars_held": offset, "exit_idx": bar_idx,
                "mae": mae, "mfe": mfe, "bars_to_mae": bars_to_mae,
                "tp_reached_pct": min(best_toward_tp, 100.0),
            }
        elif tp_hit:
            return {
                "outcome": "TP", "exit_price": tp_price,
                "bars_held": offset, "exit_idx": bar_idx,
                "mae": mae, "mfe": mfe, "bars_to_mae": bars_to_mae,
                "tp_reached_pct": 100.0,
            }

    # Time barrier
    last_idx = min(entry_idx + max_bars, len(bars) - 1)
    exit_price = bars[last_idx]["close"]
    return {
        "outcome": "TIME_BARRIER", "exit_price": exit_price,
        "bars_held": last_idx - entry_idx, "exit_idx": last_idx,
        "mae": mae, "mfe": mfe, "bars_to_mae": bars_to_mae,
        "tp_reached_pct": min(best_toward_tp, 100.0),
    }


# ============================================================================
# Zone generation helper (shared by backtest and walk-forward)
# ============================================================================
def generate_zones_at_step(
    sr_calc: SRZoneCalculator,
    bars: List[Dict],
    idx: int,
) -> Tuple[List[SRZone], float]:
    """
    Generate zones at a given bar index. Returns (all_zones, atr).
    Mirrors calibrate_hold_probability.py zone generation exactly.
    """
    sr_calc._zone_stickiness_cache.clear()
    sr_calc._flip_discount_cache.clear()

    window = bars[idx - LOOKBACK_BARS: idx]
    current_price = bars[idx]["close"]
    atr = calculate_atr(window)

    if atr <= 0 or current_price <= 0:
        return [], 0.0

    sma_50 = calculate_sma(window, 50)
    sma_200 = calculate_sma(window, 200) if len(window) >= 200 else None
    bb = calculate_bb(window, 20, 2.0)

    sma_data = {}
    if sma_50:
        sma_data["sma_50"] = sma_50
    if sma_200:
        sma_data["sma_200"] = sma_200

    result = sr_calc.calculate(
        current_price=current_price,
        bb_data=bb,
        sma_data=sma_data if sma_data else None,
        orderbook_anomalies=None,
        bars_data=window,
        atr_value=atr,
    )

    all_zones = result["support_zones"] + result["resistance_zones"]
    return all_zones, atr


# ============================================================================
# Layer 1: Zone Accuracy (hold/break rate analysis)
# ============================================================================
def run_zone_accuracy(
    bars: List[Dict],
    sr_calc: SRZoneCalculator,
    scan_start: int,
    scan_end: int,
    step_bars: int,
) -> Dict[str, Any]:
    """
    Measure zone hold/break rates by dimension.

    Same methodology as calibrate_hold_probability.py but with additional
    bounce magnitude measurement.
    """
    # Accumulators
    total_zones = 0
    total_held = 0
    total_skipped = 0

    by_strength: Dict[str, List[int]] = {}
    by_source: Dict[str, List[int]] = {}
    by_side: Dict[str, List[int]] = {"support": [0, 0], "resistance": [0, 0]}
    by_touch: Dict[str, List[int]] = {}

    # Bounce magnitude tracking
    bounce_magnitudes: List[float] = []  # ATR-normalized bounce after hold

    for idx in range(scan_start, scan_end, step_bars):
        all_zones, atr = generate_zones_at_step(sr_calc, bars, idx)
        if atr <= 0:
            continue

        for zone in all_zones:
            # Phase 1: Proximity check
            approached = False
            for i in range(idx, min(idx + FORWARD_SCAN_BARS, len(bars))):
                if is_zone_approached(bars[i], zone, atr):
                    approached = True
                    break
            if not approached:
                total_skipped += 1
                continue

            # Phase 2: Hold/break classification
            broke = False
            for i in range(idx, min(idx + FORWARD_SCAN_BARS, len(bars))):
                if is_zone_broken(bars[i], zone, atr):
                    broke = True
                    break

            held = 0 if broke else 1
            total_zones += 1
            total_held += held

            # Bounce magnitude: max favorable move after zone hold
            if held:
                max_bounce = 0.0
                for i in range(idx, min(idx + FORWARD_SCAN_BARS, len(bars))):
                    if zone.side == "support":
                        bounce = (bars[i]["high"] - zone.price_center) / atr
                    else:
                        bounce = (zone.price_center - bars[i]["low"]) / atr
                    max_bounce = max(max_bounce, bounce)
                bounce_magnitudes.append(max_bounce)

            # Accumulate by dimension
            strength = zone.strength.upper() if zone.strength else "LOW"
            by_strength.setdefault(strength, [0, 0])
            by_strength[strength][0] += 1
            by_strength[strength][1] += held

            src = zone.source_type if isinstance(zone.source_type, str) else str(zone.source_type)
            by_source.setdefault(src, [0, 0])
            by_source[src][0] += 1
            by_source[src][1] += held

            by_side[zone.side][0] += 1
            by_side[zone.side][1] += held

            tc = _touch_bucket(zone.touch_count)
            by_touch.setdefault(tc, [0, 0])
            by_touch[tc][0] += 1
            by_touch[tc][1] += held

    overall_hold_rate = total_held / total_zones if total_zones > 0 else 0

    def _dim_stats(dim: Dict[str, List[int]]) -> Dict[str, Dict]:
        out = {}
        for k, (total, held) in sorted(dim.items()):
            out[k] = {
                "total": total,
                "held": held,
                "hold_rate": round(held / total * 100, 1) if total > 0 else 0,
            }
        return out

    # Bounce magnitude stats
    bounce_stats = {}
    if bounce_magnitudes:
        bounce_magnitudes.sort()
        bounce_stats = {
            "count": len(bounce_magnitudes),
            "mean_atr": round(sum(bounce_magnitudes) / len(bounce_magnitudes), 2),
            "median_atr": round(bounce_magnitudes[len(bounce_magnitudes) // 2], 2),
            "p25_atr": round(bounce_magnitudes[int(len(bounce_magnitudes) * 0.25)], 2),
            "p75_atr": round(bounce_magnitudes[int(len(bounce_magnitudes) * 0.75)], 2),
            "pct_above_2atr": round(
                sum(1 for b in bounce_magnitudes if b >= 2.0) / len(bounce_magnitudes) * 100, 1
            ),
        }

    return {
        "total_zones_tested": total_zones,
        "total_skipped_untouched": total_skipped,
        "total_held": total_held,
        "overall_hold_rate": round(overall_hold_rate * 100, 1),
        "by_strength": _dim_stats(by_strength),
        "by_source": _dim_stats(by_source),
        "by_side": _dim_stats(by_side),
        "by_touch": _dim_stats(by_touch),
        "bounce_magnitude": bounce_stats,
    }


# ============================================================================
# Layer 2: Trading Simulation
# ============================================================================
def run_trading_simulation(
    bars: List[Dict],
    sr_calc: SRZoneCalculator,
    scan_start: int,
    scan_end: int,
    step_bars: int,
) -> Dict[str, Any]:
    """
    Mechanical trading simulation with MAE/MFE tracking.

    Entry: zone approached + held -> bounce trade
    Exit: mechanical ATR SL/TP or time barrier
    """
    groups = {
        "HIGH": {"trades": [], "label": "HIGH only"},
        "HIGH+MEDIUM": {"trades": [], "label": "HIGH + MEDIUM"},
    }
    position = {"HIGH": None, "HIGH+MEDIUM": None}

    steps_done = 0
    total_steps = (scan_end - scan_start) // step_bars

    for idx in range(scan_start, scan_end, step_bars):
        steps_done += 1
        if steps_done % 500 == 0:
            logger.info(
                f"  Progress: {steps_done}/{total_steps} steps "
                f"| HIGH: {len(groups['HIGH']['trades'])} trades "
                f"| H+M: {len(groups['HIGH+MEDIUM']['trades'])} trades"
            )

        all_zones, atr = generate_zones_at_step(sr_calc, bars, idx)
        if atr <= 0:
            continue

        current_bar = bars[idx]

        for group_key in ["HIGH", "HIGH+MEDIUM"]:
            if position[group_key] is not None:
                if idx < position[group_key]["exit_idx"]:
                    continue
                else:
                    position[group_key] = None

            valid_strengths = {"HIGH"} if group_key == "HIGH" else {"HIGH", "MEDIUM"}

            signal_zone = None
            signal_side = None

            for zone in all_zones:
                if zone.strength not in valid_strengths:
                    continue
                if not is_zone_approached(current_bar, zone, atr):
                    continue
                if is_zone_broken(current_bar, zone, atr):
                    continue

                if zone.side == "support":
                    signal_zone = zone
                    signal_side = "LONG"
                    break
                else:
                    signal_zone = zone
                    signal_side = "SHORT"
                    break

            if signal_zone is None:
                continue

            entry_idx = idx + 1
            if entry_idx >= len(bars) - FORWARD_SCAN_BARS:
                continue
            entry_price = bars[entry_idx]["open"]
            confidence = signal_zone.strength

            ok, sl_price, tp_price, rr = calc_mechanical_sltp(
                entry_price, signal_side, atr, confidence
            )
            if not ok:
                continue

            outcome = scan_trade_outcome(
                bars, entry_idx, entry_price, sl_price, tp_price,
                signal_side, max_bars=FORWARD_SCAN_BARS,
            )

            is_long = signal_side == "LONG"
            if is_long:
                pnl_pct = (outcome["exit_price"] - entry_price) / entry_price * 100
            else:
                pnl_pct = (entry_price - outcome["exit_price"]) / entry_price * 100

            # Subtract transaction cost
            pnl_pct_net = pnl_pct - ROUND_TRIP_COST_PCT
            pnl_usdt = NOTIONAL_USDT * (pnl_pct_net / 100)

            sl_distance_pct = abs(entry_price - sl_price) / entry_price * 100

            trade = TradeResult(
                entry_idx=entry_idx,
                entry_time=datetime.fromtimestamp(
                    bars[entry_idx]["timestamp"] / 1000, tz=timezone.utc
                ).strftime("%Y-%m-%d %H:%M"),
                side=signal_side,
                zone_strength=signal_zone.strength,
                zone_side=signal_zone.side,
                zone_center=round(signal_zone.price_center, 2),
                zone_sources=signal_zone.sources[:3],
                zone_weight=round(signal_zone.total_weight, 2),
                zone_hold_prob=round(signal_zone.hold_probability, 3),
                zone_source_type=signal_zone.source_type if isinstance(signal_zone.source_type, str) else str(signal_zone.source_type),
                zone_touch_count=signal_zone.touch_count,
                entry_price=round(entry_price, 2),
                sl_price=round(sl_price, 2),
                tp_price=round(tp_price, 2),
                rr_target=rr,
                atr=round(atr, 2),
                outcome=outcome["outcome"],
                exit_price=round(outcome["exit_price"], 2),
                bars_held=outcome["bars_held"],
                pnl_pct=round(pnl_pct_net, 4),
                pnl_usdt=round(pnl_usdt, 2),
                mae_pct=round(outcome["mae"], 4),
                mfe_pct=round(outcome["mfe"], 4),
                bars_to_mae=outcome["bars_to_mae"],
                tp_reached_pct=round(outcome["tp_reached_pct"], 1),
                sl_distance_pct=round(sl_distance_pct, 4),
            )

            groups[group_key]["trades"].append(trade)
            position[group_key] = {"exit_idx": outcome["exit_idx"]}

    # Compute stats for each group
    stats = {}
    for group_key, group in groups.items():
        trades = group["trades"]
        stats[group_key] = _compute_trade_stats(trades, group["label"])

    return {
        "stats": stats,
        "trades": {k: [asdict(t) for t in v["trades"]] for k, v in groups.items()},
    }


def _compute_trade_stats(trades: List[TradeResult], label: str) -> Dict[str, Any]:
    """Compute comprehensive statistics for a group of trades."""
    if not trades:
        return {"total": 0, "label": label}

    wins = [t for t in trades if t.pnl_pct > 0]
    losses = [t for t in trades if t.pnl_pct <= 0]
    tp_trades = [t for t in trades if t.outcome == "TP"]
    sl_trades = [t for t in trades if t.outcome == "SL"]
    tb_trades = [t for t in trades if t.outcome == "TIME_BARRIER"]
    total = len(trades)

    total_pnl = sum(t.pnl_usdt for t in trades)
    total_pnl_pct = sum(t.pnl_pct for t in trades)
    avg_win = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0

    gross_profit = sum(t.pnl_usdt for t in wins) if wins else 0
    gross_loss = abs(sum(t.pnl_usdt for t in losses)) if losses else 0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

    # Max drawdown
    peak = equity = 0.0
    max_dd = 0.0
    for t in trades:
        equity += t.pnl_usdt
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    # Max consecutive losses
    max_consec = consec = 0
    for t in trades:
        if t.pnl_pct <= 0:
            consec += 1
            max_consec = max(max_consec, consec)
        else:
            consec = 0

    # By side
    longs = [t for t in trades if t.side == "LONG"]
    shorts = [t for t in trades if t.side == "SHORT"]
    long_wins = [t for t in longs if t.pnl_pct > 0]
    short_wins = [t for t in shorts if t.pnl_pct > 0]

    # By zone strength
    by_strength = {}
    for strength in ["HIGH", "MEDIUM"]:
        st = [t for t in trades if t.zone_strength == strength]
        if st:
            sw = [t for t in st if t.pnl_pct > 0]
            by_strength[strength] = {
                "count": len(st), "wins": len(sw),
                "win_rate": round(len(sw) / len(st) * 100, 1),
                "total_pnl": round(sum(t.pnl_usdt for t in st), 2),
                "avg_pnl_pct": round(sum(t.pnl_pct for t in st) / len(st), 4),
            }

    # By source type
    by_source = {}
    sources = set(t.zone_source_type for t in trades)
    for src in sorted(sources):
        st = [t for t in trades if t.zone_source_type == src]
        sw = [t for t in st if t.pnl_pct > 0]
        by_source[src] = {
            "count": len(st), "wins": len(sw),
            "win_rate": round(len(sw) / len(st) * 100, 1),
            "avg_pnl_pct": round(sum(t.pnl_pct for t in st) / len(st), 4),
        }

    # By hold probability bucket
    by_hold_prob = {}
    for bucket_name, lo, hi in [
        ("<0.55", 0, 0.55), ("0.55-0.65", 0.55, 0.65),
        ("0.65-0.75", 0.65, 0.75), (">0.75", 0.75, 2.0),
    ]:
        bt = [t for t in trades if lo <= t.zone_hold_prob < hi]
        if bt:
            bw = [t for t in bt if t.pnl_pct > 0]
            by_hold_prob[bucket_name] = {
                "count": len(bt), "wins": len(bw),
                "win_rate": round(len(bw) / len(bt) * 100, 1),
                "avg_hold_prob": round(sum(t.zone_hold_prob for t in bt) / len(bt), 3),
                "avg_pnl_pct": round(sum(t.pnl_pct for t in bt) / len(bt), 4),
            }

    # MAE/MFE diagnostics
    mae_mfe = {}
    if losses:
        loss_maes = [t.mae_pct for t in losses]
        near_miss = [t for t in losses if t.tp_reached_pct >= 50.0]
        immediate_sl = [t for t in losses if t.bars_to_mae <= 2]
        mae_mfe["losses"] = {
            "avg_mae": round(sum(loss_maes) / len(loss_maes), 4),
            "avg_mfe": round(sum(t.mfe_pct for t in losses) / len(losses), 4),
            "near_miss_count": len(near_miss),
            "near_miss_pct": round(len(near_miss) / len(losses) * 100, 1),
            "immediate_sl_count": len(immediate_sl),
            "immediate_sl_pct": round(len(immediate_sl) / len(losses) * 100, 1),
            "avg_bars_to_mae": round(sum(t.bars_to_mae for t in losses) / len(losses), 1),
        }
    if wins:
        mae_mfe["wins"] = {
            "avg_mae": round(sum(t.mae_pct for t in wins) / len(wins), 4),
            "avg_mfe": round(sum(t.mfe_pct for t in wins) / len(wins), 4),
            "avg_bars_held": round(sum(t.bars_held for t in wins) / len(wins), 1),
        }

    # TP reachability analysis
    tp_reach = {}
    non_wins = [t for t in trades if t.outcome != "TP"]
    for bucket_name, lo, hi in [
        ("0-25% (far)", 0, 25), ("25-50%", 25, 50),
        ("50-75% (near miss)", 50, 75), ("75-99% (very close)", 75, 100),
    ]:
        bt = [t for t in non_wins if lo <= t.tp_reached_pct < hi]
        tp_reach[bucket_name] = {
            "count": len(bt),
            "pct_of_non_wins": round(len(bt) / len(non_wins) * 100, 1) if non_wins else 0,
        }
    tp_reach["100% (TP hit)"] = {
        "count": len(tp_trades),
        "pct_of_total": round(len(tp_trades) / total * 100, 1),
    }

    # Exit time distribution
    time_exit = {}
    for bucket_name, lo, hi in [
        ("1-3 bars (instant)", 1, 4), ("4-8 bars (1-2h)", 4, 9),
        ("9-16 bars (2-4h)", 9, 17), ("17-32 bars (4-8h)", 17, 33),
        (">32 bars (8h+)", 33, 999),
    ]:
        bt = [t for t in trades if lo <= t.bars_held < hi]
        if bt:
            bw = [t for t in bt if t.pnl_pct > 0]
            time_exit[bucket_name] = {
                "count": len(bt), "win_rate": round(len(bw) / len(bt) * 100, 1),
                "avg_pnl": round(sum(t.pnl_pct for t in bt) / len(bt), 4),
            }

    avg_bars = sum(t.bars_held for t in trades) / total

    return {
        "label": label,
        "total": total,
        "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / total * 100, 1),
        "tp_count": len(tp_trades), "sl_count": len(sl_trades), "tb_count": len(tb_trades),
        "total_pnl_usdt": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 4),
        "avg_win_pct": round(avg_win, 4),
        "avg_loss_pct": round(avg_loss, 4),
        "profit_factor": round(profit_factor, 2),
        "max_drawdown_usdt": round(max_dd, 2),
        "max_consecutive_losses": max_consec,
        "avg_bars_held": round(avg_bars, 1),
        "avg_hold_hours": round(avg_bars * 0.25, 1),
        "longs": len(longs), "shorts": len(shorts),
        "long_win_rate": round(len(long_wins) / len(longs) * 100, 1) if longs else 0,
        "short_win_rate": round(len(short_wins) / len(shorts) * 100, 1) if shorts else 0,
        "by_strength": by_strength,
        "by_source": by_source,
        "by_hold_prob": by_hold_prob,
        "mae_mfe": mae_mfe,
        "tp_reachability": tp_reach,
        "time_exit": time_exit,
    }


# ============================================================================
# Layer 3: Statistical Validation
# ============================================================================
def wilson_ci(wins: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    """Wilson score 95% confidence interval for a proportion."""
    if n == 0:
        return (0.0, 1.0)
    p = wins / n
    denom = 1 + z ** 2 / n
    centre = (p + z ** 2 / (2 * n)) / denom
    spread = z * ((p * (1 - p) / n + z ** 2 / (4 * n ** 2)) ** 0.5) / denom
    return (round(max(0.0, centre - spread), 4), round(min(1.0, centre + spread), 4))


def compute_sharpe_calmar(pnl_list: List[float]) -> Dict[str, float]:
    """Per-trade Sharpe and Calmar ratios."""
    if not pnl_list:
        return {"sharpe": 0.0, "calmar": 0.0, "max_dd_pct": 0.0}

    n = len(pnl_list)
    mean_pnl = sum(pnl_list) / n
    variance = sum((p - mean_pnl) ** 2 for p in pnl_list) / n
    std_pnl = math.sqrt(variance) if variance > 0 else 0.0
    sharpe = mean_pnl / std_pnl if std_pnl > 0 else 0.0

    running = peak = 0.0
    max_dd = 0.0
    for p in pnl_list:
        running += p
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)

    calmar = sum(pnl_list) / max_dd if max_dd > 0 else 0.0
    return {"sharpe": round(sharpe, 4), "calmar": round(calmar, 4), "max_dd_pct": round(max_dd, 3)}


def bootstrap_vs_random(
    bars: List[Dict],
    actual_hold_rate: float,
    actual_sample_count: int,
    n_resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> Dict[str, Any]:
    """
    Bootstrap significance test: compare actual S/R hold rate against random levels.

    Methodology (Osler 2000):
    1. Generate random price levels within the actual price range
    2. Apply same proximity/break logic
    3. Compute hold rate for random levels
    4. Repeat N times to build null distribution
    5. If actual hold rate > 95th percentile -> statistically significant
    """
    rng = random.Random(seed)
    prices = [b["close"] for b in bars]
    price_min, price_max = min(prices), max(prices)
    bar_count = len(bars)

    random_hold_rates = []

    for _ in range(n_resamples):
        # Generate random "zone" prices
        n_random_zones = max(10, actual_sample_count // 50)
        held = tested = 0

        for _ in range(n_random_zones):
            zone_price = rng.uniform(price_min, price_max)
            # Random window start
            start_idx = rng.randint(LOOKBACK_BARS, bar_count - FORWARD_SCAN_BARS - 1)

            window = bars[start_idx - LOOKBACK_BARS: start_idx]
            atr = calculate_atr(window)
            if atr <= 0:
                continue

            proximity_margin = atr * PROXIMITY_THRESHOLD_ATR
            break_margin = atr * BREAK_THRESHOLD_ATR

            # Check if price approaches random level
            approached = False
            for i in range(start_idx, min(start_idx + FORWARD_SCAN_BARS, bar_count)):
                if abs(bars[i]["close"] - zone_price) <= proximity_margin:
                    approached = True
                    break
            if not approached:
                continue

            tested += 1

            # Check if random level "holds"
            broke = False
            for i in range(start_idx, min(start_idx + FORWARD_SCAN_BARS, bar_count)):
                if abs(bars[i]["close"] - zone_price) > proximity_margin + break_margin:
                    # Simplified: if price moves far enough away, consider "broken"
                    if bars[i]["close"] > zone_price + break_margin or bars[i]["close"] < zone_price - break_margin:
                        broke = True
                        break

            if not broke:
                held += 1

        if tested >= 5:
            random_hold_rates.append(held / tested)

    if not random_hold_rates:
        return {"success": False, "error": "No valid random samples"}

    random_hold_rates.sort()
    n = len(random_hold_rates)
    mean_random = sum(random_hold_rates) / n
    p95 = random_hold_rates[int(n * 0.95)]
    p99 = random_hold_rates[int(min(n * 0.99, n - 1))]

    # p-value: fraction of random results >= actual
    p_value = sum(1 for r in random_hold_rates if r >= actual_hold_rate / 100) / n

    return {
        "success": True,
        "actual_hold_rate": actual_hold_rate,
        "random_mean_hold_rate": round(mean_random * 100, 1),
        "random_p95_hold_rate": round(p95 * 100, 1),
        "random_p99_hold_rate": round(p99 * 100, 1),
        "p_value": round(p_value, 4),
        "significant_5pct": p_value < 0.05,
        "significant_1pct": p_value < 0.01,
        "edge_vs_random": round(actual_hold_rate - mean_random * 100, 1),
        "n_resamples": n,
    }


def run_walk_forward(
    bars: List[Dict],
    n_splits: int = WF_SPLITS,
    purge_bars: int = WF_PURGE_BARS,
) -> Dict[str, Any]:
    """
    Walk-Forward validation: split data into folds, train zone detection
    on lookback, test on forward window.

    Each fold runs independently to verify no lookahead and that results
    are consistent across time periods.
    """
    tradeable = len(bars) - LOOKBACK_BARS
    if tradeable <= 0:
        return {"success": False, "error": "Insufficient bars"}

    fold_size = tradeable // n_splits
    if fold_size < FORWARD_SCAN_BARS * 2:
        return {"success": False, "error": f"Fold too small ({fold_size} bars)"}

    fold_results = []

    for i in range(n_splits):
        fold_start = LOOKBACK_BARS + i * fold_size + purge_bars
        fold_end = LOOKBACK_BARS + (i + 1) * fold_size if i < n_splits - 1 else len(bars) - FORWARD_SCAN_BARS
        if fold_start >= fold_end:
            continue

        sr_calc = SRZoneCalculator(
            swing_detection_enabled=True, swing_left_bars=5, swing_right_bars=5,
            touch_count_enabled=True, use_atr_adaptive=True,
        )

        sim = run_trading_simulation(
            bars, sr_calc, fold_start, fold_end, STEP_BARS,
        )

        hm_stats = sim["stats"].get("HIGH+MEDIUM", {})
        if not hm_stats or hm_stats.get("total", 0) == 0:
            fold_results.append({"fold": i + 1, "success": False, "error": "No trades"})
            continue

        pnl_list = [t["pnl_pct"] for t in sim["trades"].get("HIGH+MEDIUM", [])]
        sc = compute_sharpe_calmar(pnl_list)
        ci_lo, ci_hi = wilson_ci(hm_stats["wins"], hm_stats["total"])

        fold_results.append({
            "fold": i + 1,
            "success": True,
            "bars": fold_end - fold_start,
            "days": round((fold_end - fold_start) / 96, 1),
            "total_trades": hm_stats["total"],
            "win_rate": hm_stats["win_rate"],
            "total_pnl_pct": hm_stats["total_pnl_pct"],
            "profit_factor": hm_stats["profit_factor"],
            "sharpe": sc["sharpe"],
            "calmar": sc["calmar"],
            "wr_wilson_ci_95": (round(ci_lo * 100, 1), round(ci_hi * 100, 1)),
        })

    valid = [f for f in fold_results if f.get("success")]
    if not valid:
        return {"success": False, "folds": fold_results, "error": "No valid folds"}

    # Aggregate
    total_trades = sum(f["total_trades"] for f in valid)
    total_wins = sum(int(f["total_trades"] * f["win_rate"] / 100) for f in valid)
    agg_ci_lo, agg_ci_hi = wilson_ci(total_wins, total_trades)

    return {
        "success": True,
        "n_splits": n_splits,
        "purge_bars": purge_bars,
        "folds": fold_results,
        "aggregate": {
            "mean_win_rate": round(sum(f["win_rate"] for f in valid) / len(valid), 1),
            "mean_pnl_pct": round(sum(f["total_pnl_pct"] for f in valid) / len(valid), 4),
            "mean_sharpe": round(sum(f["sharpe"] for f in valid) / len(valid), 4),
            "mean_calmar": round(sum(f["calmar"] for f in valid) / len(valid), 4),
            "total_trades": total_trades,
            "wr_wilson_ci_95": (round(agg_ci_lo * 100, 1), round(agg_ci_hi * 100, 1)),
            "folds_profitable": sum(1 for f in valid if f["total_pnl_pct"] > 0),
            "folds_total": len(valid),
        },
    }


# ============================================================================
# Helpers
# ============================================================================
def _touch_bucket(count: int) -> str:
    if count <= 1:
        return "0-1"
    elif count <= 3:
        return "2-3"
    elif count == 4:
        return "4"
    return "5+"


# ============================================================================
# Main orchestrator
# ============================================================================
def run_full_diagnostic(
    days: int = 90,
    step_bars: int = STEP_BARS,
    validate: bool = False,
) -> Dict[str, Any]:
    """Run all diagnostic layers."""
    bars = fetch_bars(days=days)
    if len(bars) < LOOKBACK_BARS + FORWARD_SCAN_BARS + 10:
        raise ValueError(f"Insufficient data: {len(bars)} bars")

    scan_start = LOOKBACK_BARS
    scan_end = len(bars) - FORWARD_SCAN_BARS

    sr_calc = SRZoneCalculator(
        swing_detection_enabled=True, swing_left_bars=5, swing_right_bars=5,
        touch_count_enabled=True, use_atr_adaptive=True,
    )

    # Load calibration info
    cal_info = "none"
    cal_path = Path(PROJECT_ROOT) / "data" / "calibration" / "latest.json"
    if cal_path.exists():
        try:
            with open(cal_path) as f:
                cal_data = json.load(f)
            cal_info = f"{cal_data.get('version', '?')}, {cal_data.get('sample_count', '?')} samples"
        except Exception:
            pass

    logger.info(f"Calibration: {cal_info}")

    # Layer 1: Zone Accuracy
    logger.info("=== Layer 1: Zone Accuracy ===")
    zone_accuracy = run_zone_accuracy(bars, sr_calc, scan_start, scan_end, step_bars)

    # Layer 2: Trading Simulation
    logger.info("=== Layer 2: Trading Simulation ===")
    trading_sim = run_trading_simulation(bars, sr_calc, scan_start, scan_end, step_bars)

    result = {
        "config": {
            "days": days,
            "step_bars": step_bars,
            "lookback_bars": LOOKBACK_BARS,
            "forward_scan_bars": FORWARD_SCAN_BARS,
            "proximity_threshold_atr": PROXIMITY_THRESHOLD_ATR,
            "break_threshold_atr": BREAK_THRESHOLD_ATR,
            "notional_usdt": NOTIONAL_USDT,
            "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
            "mechanical_sltp": MECHANICAL_SLTP,
            "total_bars": len(bars),
            "total_steps": (scan_end - scan_start) // step_bars,
            "calibration": cal_info,
        },
        "zone_accuracy": zone_accuracy,
        "trading_simulation": trading_sim["stats"],
        "trades": trading_sim["trades"],
    }

    # Layer 3: Statistical Validation (optional, slower)
    if validate:
        logger.info("=== Layer 3: Statistical Validation ===")

        # Bootstrap vs random
        logger.info("  Running bootstrap vs random levels...")
        overall_hr = zone_accuracy["overall_hold_rate"]
        sample_n = zone_accuracy["total_zones_tested"]
        bootstrap = bootstrap_vs_random(bars, overall_hr, sample_n)
        result["bootstrap_vs_random"] = bootstrap

        # Walk-Forward
        logger.info("  Running walk-forward validation...")
        wf = run_walk_forward(bars, n_splits=WF_SPLITS, purge_bars=WF_PURGE_BARS)
        result["walk_forward"] = wf

        # Wilson CI for main groups
        ci_results = {}
        for group_key in ["HIGH", "HIGH+MEDIUM"]:
            s = trading_sim["stats"].get(group_key, {})
            if s.get("total", 0) > 0:
                ci_lo, ci_hi = wilson_ci(s["wins"], s["total"])
                pnl_list = [t["pnl_pct"] for t in trading_sim["trades"].get(group_key, [])]
                sc = compute_sharpe_calmar(pnl_list)
                ci_results[group_key] = {
                    "wr_wilson_ci_95": (round(ci_lo * 100, 1), round(ci_hi * 100, 1)),
                    **sc,
                }
        result["statistical_tests"] = ci_results

    return result


# ============================================================================
# Display
# ============================================================================
def print_results(results: Dict[str, Any]):
    """Print formatted diagnostic results."""
    config = results["config"]
    za = results["zone_accuracy"]
    sim = results["trading_simulation"]

    print("\n" + "=" * 80)
    print("S/R ZONE EFFECTIVENESS DIAGNOSTIC")
    print("=" * 80)
    print(f"Period: {config['days']} days | Bars: {config['total_bars']} | Steps: {config['total_steps']}")
    print(f"Calibration: {config['calibration']}")
    print(f"Proximity: ATR x{config['proximity_threshold_atr']} | Break: ATR x{config['break_threshold_atr']}")
    print(f"Cost: {config['round_trip_cost_pct']}% round-trip | Notional: ${config['notional_usdt']:,.0f}")

    # --- Layer 1: Zone Accuracy ---
    print(f"\n{'━' * 80}")
    print("  LAYER 1: ZONE ACCURACY (hold/break rate)")
    print(f"{'━' * 80}")
    print(f"  Zones tested: {za['total_zones_tested']} | Skipped (untouched): {za['total_skipped_untouched']}")
    print(f"  Overall hold rate: {za['overall_hold_rate']}%")

    print(f"\n  By Strength:")
    for k, v in sorted(za["by_strength"].items()):
        print(f"    {k:8s}: {v['hold_rate']:5.1f}% hold  (N={v['total']})")

    print(f"\n  By Side:")
    for k, v in sorted(za["by_side"].items()):
        print(f"    {k:12s}: {v['hold_rate']:5.1f}% hold  (N={v['total']})")

    print(f"\n  By Source Type:")
    for k, v in sorted(za["by_source"].items()):
        if v["total"] >= 20:
            print(f"    {k:15s}: {v['hold_rate']:5.1f}% hold  (N={v['total']})")

    print(f"\n  By Touch Count:")
    for k in ["0-1", "2-3", "4", "5+"]:
        v = za["by_touch"].get(k, {})
        if v:
            print(f"    {k:5s}: {v['hold_rate']:5.1f}% hold  (N={v['total']})")

    bounce = za.get("bounce_magnitude", {})
    if bounce:
        print(f"\n  Bounce Magnitude (held zones):")
        print(f"    Mean: {bounce['mean_atr']} ATR | Median: {bounce['median_atr']} ATR")
        print(f"    P25: {bounce['p25_atr']} ATR | P75: {bounce['p75_atr']} ATR")
        print(f"    Bounces >= 2 ATR: {bounce['pct_above_2atr']}%")

    # --- Layer 2: Trading Simulation ---
    print(f"\n{'━' * 80}")
    print("  LAYER 2: TRADING SIMULATION (mechanical SL/TP)")
    print(f"{'━' * 80}")
    print(f"  SL/TP: HIGH=SL 2.0xATR R/R 2.5:1 | MEDIUM=SL 2.5xATR R/R 2.0:1")

    for group_key in ["HIGH", "HIGH+MEDIUM"]:
        s = sim.get(group_key, {})
        if s.get("total", 0) == 0:
            continue

        pnl_sign = "+" if s["total_pnl_usdt"] >= 0 else ""
        print(f"\n  --- {s['label']} ---")
        print(f"  Trades: {s['total']} ({s['longs']}L / {s['shorts']}S)")
        print(f"  Win Rate: {s['win_rate']}% ({s['wins']}W / {s['losses']}L)")
        print(f"  Outcomes: {s['tp_count']} TP / {s['sl_count']} SL / {s['tb_count']} TB")
        print(f"  Total PnL: {pnl_sign}${s['total_pnl_usdt']:,.2f} ({pnl_sign}{s['total_pnl_pct']:.2f}%)")
        print(f"  Profit Factor: {s['profit_factor']} | Max DD: ${s['max_drawdown_usdt']:,.2f} | Max Consec Loss: {s['max_consecutive_losses']}")
        print(f"  Avg Win: +{s['avg_win_pct']:.4f}% | Avg Loss: {s['avg_loss_pct']:.4f}%")
        print(f"  Long WR: {s['long_win_rate']}% | Short WR: {s['short_win_rate']}%")
        print(f"  Avg Hold: {s['avg_hold_hours']}h ({s['avg_bars_held']} bars)")

        if s.get("by_strength"):
            print(f"\n  By Zone Strength:")
            for strength, data in sorted(s["by_strength"].items()):
                c = "+" if data["total_pnl"] >= 0 else ""
                print(f"    {strength}: {data['count']} trades | WR {data['win_rate']}% | PnL {c}${data['total_pnl']:,.2f}")

        if s.get("by_hold_prob"):
            print(f"\n  HoldProb Calibration Check (does higher hold_prob → better win rate?):")
            for bucket, data in sorted(s["by_hold_prob"].items()):
                print(f"    {bucket:10s}: WR {data['win_rate']:5.1f}% | Avg hold_prob {data['avg_hold_prob']:.3f} | N={data['count']}")

        # MAE/MFE
        mm = s.get("mae_mfe", {})
        if mm.get("losses"):
            lm = mm["losses"]
            print(f"\n  MAE/MFE Diagnostics (entry quality):")
            print(f"    Losses — Avg MAE: {lm['avg_mae']:.4f}% | Avg MFE: {lm['avg_mfe']:.4f}%")
            print(f"    Immediate SL (<=2 bars): {lm['immediate_sl_count']} ({lm['immediate_sl_pct']}%)")
            print(f"    Near-miss (>50% to TP): {lm['near_miss_count']} ({lm['near_miss_pct']}%)")
        if mm.get("wins"):
            wm = mm["wins"]
            print(f"    Wins  — Avg MAE: {wm['avg_mae']:.4f}% | Avg MFE: {wm['avg_mfe']:.4f}%")

        # TP Reachability
        tp = s.get("tp_reachability", {})
        if tp:
            print(f"\n  TP Reachability (how close did non-TP trades get?):")
            for bucket, data in tp.items():
                if "pct_of_non_wins" in data:
                    print(f"    {bucket:25s}: {data['count']:4d} ({data['pct_of_non_wins']:5.1f}%)")
                else:
                    print(f"    {bucket:25s}: {data['count']:4d} ({data['pct_of_total']:5.1f}% of total)")

        # Exit time
        te = s.get("time_exit", {})
        if te:
            print(f"\n  Exit Time Distribution:")
            for bucket, data in te.items():
                print(f"    {bucket:25s}: {data['count']:4d} trades | WR {data['win_rate']:5.1f}% | Avg PnL {data['avg_pnl']:+.4f}%")

    # Monthly breakdown
    for group_key in ["HIGH", "HIGH+MEDIUM"]:
        trades = results["trades"].get(group_key, [])
        if not trades:
            continue

        print(f"\n  Monthly Breakdown: {sim[group_key]['label']}")
        monthly: Dict[str, List] = {}
        for t in trades:
            month = t["entry_time"][:7]
            monthly.setdefault(month, []).append(t)

        print(f"  {'Month':<10} {'Trades':>7} {'WR':>7} {'PnL($)':>10} {'PnL(%)':>10} {'TP':>5} {'SL':>5} {'TB':>5}")
        for month in sorted(monthly.keys()):
            mt = monthly[month]
            mw = [t for t in mt if t["pnl_pct"] > 0]
            mwr = len(mw) / len(mt) * 100 if mt else 0
            mpnl = sum(t["pnl_usdt"] for t in mt)
            mpnl_pct = sum(t["pnl_pct"] for t in mt)
            mtp = sum(1 for t in mt if t["outcome"] == "TP")
            msl = sum(1 for t in mt if t["outcome"] == "SL")
            mtb = sum(1 for t in mt if t["outcome"] == "TIME_BARRIER")
            c = "+" if mpnl >= 0 else ""
            print(f"  {month:<10} {len(mt):>7} {mwr:>6.1f}% {c}{mpnl:>9.2f} {c}{mpnl_pct:>9.2f}% {mtp:>5} {msl:>5} {mtb:>5}")

    # --- Layer 3: Statistical Validation ---
    if "bootstrap_vs_random" in results:
        print(f"\n{'━' * 80}")
        print("  LAYER 3: STATISTICAL VALIDATION")
        print(f"{'━' * 80}")

        bs = results["bootstrap_vs_random"]
        if bs.get("success"):
            sig = "YES ✓" if bs["significant_5pct"] else "NO ✗"
            print(f"\n  Bootstrap vs Random Levels ({bs['n_resamples']} resamples):")
            print(f"    Actual hold rate:     {bs['actual_hold_rate']:.1f}%")
            print(f"    Random mean hold rate: {bs['random_mean_hold_rate']:.1f}%")
            print(f"    Random P95 hold rate:  {bs['random_p95_hold_rate']:.1f}%")
            print(f"    Edge vs random:        {bs['edge_vs_random']:+.1f}pp")
            print(f"    p-value:               {bs['p_value']:.4f}")
            print(f"    Significant (5%):      {sig}")

        st = results.get("statistical_tests", {})
        for group_key, data in st.items():
            ci = data.get("wr_wilson_ci_95", (0, 0))
            print(f"\n  {group_key}:")
            print(f"    Win Rate 95% CI: [{ci[0]:.1f}%, {ci[1]:.1f}%]")
            print(f"    Sharpe: {data['sharpe']:.4f} | Calmar: {data['calmar']:.4f}")

        wf = results.get("walk_forward", {})
        if wf.get("success"):
            agg = wf["aggregate"]
            print(f"\n  Walk-Forward Validation ({wf['n_splits']} folds, purge={wf['purge_bars']} bars):")
            print(f"  {'Fold':<6} {'Trades':>7} {'WR':>7} {'PnL%':>10} {'PF':>6} {'Sharpe':>8} {'Wilson 95% CI':>18}")
            for f in wf["folds"]:
                if f.get("success"):
                    ci = f.get("wr_wilson_ci_95", (0, 0))
                    print(
                        f"    {f['fold']:<4} {f['total_trades']:>7} "
                        f"{f['win_rate']:>6.1f}% {f['total_pnl_pct']:>+9.2f}% "
                        f"{f['profit_factor']:>5.2f} {f['sharpe']:>8.4f} "
                        f"[{ci[0]:.1f}%, {ci[1]:.1f}%]"
                    )
                else:
                    print(f"    {f['fold']:<4} — {f.get('error', 'failed')}")

            ci_agg = agg.get("wr_wilson_ci_95", (0, 0))
            print(f"  {'AGG':<6} {agg['total_trades']:>7} {agg['mean_win_rate']:>6.1f}% "
                  f"{agg['mean_pnl_pct']:>+9.2f}% {'':>6} {agg['mean_sharpe']:>8.4f} "
                  f"[{ci_agg[0]:.1f}%, {ci_agg[1]:.1f}%]")
            print(f"    Folds profitable: {agg['folds_profitable']}/{agg['folds_total']}")

    print("\n" + "=" * 80)


# ============================================================================
# Entry point
# ============================================================================
def main():
    parser = argparse.ArgumentParser(description="S/R Zone Effectiveness Diagnostic")
    parser.add_argument("--days", type=int, default=90, help="Days of data (default: 90)")
    parser.add_argument("--step", type=int, default=STEP_BARS, help="Step bars (default: 4 = 1h)")
    parser.add_argument("--validate", action="store_true", help="Run Layer 3: Walk-Forward + Bootstrap")
    parser.add_argument("--output", type=str, default=None, help="Output JSON file path")
    args = parser.parse_args()

    logger.info(f"Starting S/R zone diagnostic: {args.days} days, step={args.step}, validate={args.validate}")

    results = run_full_diagnostic(days=args.days, step_bars=args.step, validate=args.validate)

    print_results(results)

    output_path = args.output or os.path.join(PROJECT_ROOT, "data", "backtest_sr_zones_result.json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    logger.info(f"Results saved to {output_path}")


if __name__ == "__main__":
    main()
