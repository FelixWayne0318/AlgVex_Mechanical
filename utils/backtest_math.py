"""
Shared math utilities for standalone backtest/calibration scripts.

These functions mirror production implementations but operate on raw OHLCV
dicts (no NautilusTrader dependency), so scripts can run without the full
framework installed.

SSoT relationship:
- calculate_atr_wilder() mirrors indicators/technical_manager.py ATR(14)
- calculate_mechanical_sltp() mirrors strategy/trading_logic.py
- calculate_sma() / calculate_bb() are trivial helpers

Tracked by check_logic_sync.py — any divergence from production is flagged.
"""

import math
from typing import Dict, List, Optional, Tuple  # noqa: F401 — Tuple kept for callers importing it


# ============================================================================
# Configuration constants (must match configs/base.yaml mechanical_sltp)
# ============================================================================
MECHANICAL_SLTP_DEFAULTS = {
    "sl_atr_multiplier": {"HIGH": 0.8, "MEDIUM": 1.0, "LOW": 1.0},  # v39.0: based on 4H ATR
    "tp_rr_target": {"HIGH": 1.5, "MEDIUM": 1.5, "LOW": 1.5},
    "sl_atr_multiplier_floor": 0.5,  # v39.0: 4H ATR floor (was 1.2 on 30M)
    "counter_trend_rr_multiplier": 1.3,
    "min_rr_ratio": 1.3,
}


# ============================================================================
# ATR — Wilder's Smoothing (scalar: returns latest ATR value)
# ============================================================================
def calculate_atr_wilder(bars: List[Dict], period: int = 14) -> float:
    """
    Calculate ATR using Wilder's smoothing from raw OHLCV dicts.

    Mirrors the Cython ATR indicator in indicators/technical_manager.py.
    Bars must have 'high', 'low', 'close' keys.

    Returns 0.0 if insufficient data.
    """
    if len(bars) < period + 1:
        return 0.0

    true_ranges = []
    for i in range(1, len(bars)):
        h = float(bars[i].get("high", 0))
        l = float(bars[i].get("low", 0))
        pc = float(bars[i - 1].get("close", 0))
        # v2.0 Phase 1: NaN/inf guard — skip corrupted bars
        if not (math.isfinite(h) and math.isfinite(l) and math.isfinite(pc)):
            continue
        tr = max(h - l, abs(h - pc), abs(l - pc))
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return 0.0

    # Wilder's smoothing: first ATR = SMA of first `period` TRs,
    # then recursive: ATR_new = (ATR_prev * (period-1) + TR) / period
    atr = sum(true_ranges[:period]) / period
    for tr in true_ranges[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


# ============================================================================
# ATR — Wilder's Smoothing (series: returns ATR for each bar)
# ============================================================================
def calculate_atr_series(bars: List[Dict], period: int = 14) -> List[float]:
    """
    Calculate ATR series (one value per bar) using Wilder's smoothing.

    Returns a list of the same length as bars. Values before the period
    warm-up are 0.
    """
    if len(bars) < 2:
        return [0.0] * len(bars)

    trs = [bars[0]["high"] - bars[0]["low"]]
    for i in range(1, len(bars)):
        h = bars[i]["high"]
        l = bars[i]["low"]
        pc = bars[i - 1]["close"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)

    atrs = [0.0] * len(bars)
    if len(trs) >= period:
        atrs[period - 1] = sum(trs[:period]) / period
        for i in range(period, len(trs)):
            atrs[i] = (atrs[i - 1] * (period - 1) + trs[i]) / period

    return atrs


# ============================================================================
# Mechanical SL/TP (tuple return — matches trading_logic.py signature)
# ============================================================================
def calculate_mechanical_sltp(
    entry_price: float,
    side: str,
    atr_value: float,
    confidence: str = "MEDIUM",
    is_counter_trend: bool = False,
    config: Optional[Dict] = None,
    atr_4h: float = 0.0,
) -> Tuple[bool, float, float, float, str]:
    """
    Calculate mechanical SL/TP. Mirrors strategy/trading_logic.py logic.
    v39.0: Uses 4H ATR as primary, 30M ATR as fallback.

    Returns (success, sl_price, tp_price, rr_ratio, description).
    """
    if entry_price <= 0 or (atr_value <= 0 and (atr_4h or 0) <= 0):
        return False, 0.0, 0.0, 0.0, f"Invalid: price={entry_price}, atr={atr_value}, atr_4h={atr_4h}"

    cfg = config or MECHANICAL_SLTP_DEFAULTS
    is_long = side.upper() in ("BUY", "LONG")

    # SL multiplier from confidence
    sl_mult = cfg["sl_atr_multiplier"].get(confidence.upper(), 1.0)
    sl_mult = max(sl_mult, cfg["sl_atr_multiplier_floor"])

    # v39.0: Use 4H ATR as primary, 30M ATR as fallback
    effective_atr = atr_4h if (atr_4h or 0) > 0 else atr_value

    # SL distance
    sl_distance = effective_atr * sl_mult

    # R/R target from confidence
    rr_target = cfg["tp_rr_target"].get(confidence.upper(), 1.5)

    # Counter-trend R/R escalation
    effective_rr = rr_target
    ct_note = ""
    if is_counter_trend:
        ct_rr_mult = cfg["counter_trend_rr_multiplier"]
        min_rr = cfg["min_rr_ratio"]
        min_ct_rr = min_rr * ct_rr_mult
        effective_rr = max(rr_target, min_ct_rr)
        ct_note = f" CT(rr>={min_ct_rr:.2f})"

    # TP distance
    tp_distance = sl_distance * effective_rr

    # Compute prices
    if is_long:
        sl_price = entry_price - sl_distance
        tp_price = entry_price + tp_distance
    else:
        sl_price = entry_price + sl_distance
        tp_price = entry_price - tp_distance

    if sl_price <= 0 or tp_price <= 0:
        return False, 0.0, 0.0, 0.0, "SL/TP non-positive"

    actual_rr = tp_distance / sl_distance if sl_distance > 0 else 0
    desc = (
        f"conf={confidence.upper()}|sl_mult={sl_mult:.2f}{ct_note}"
        f"|rr={effective_rr:.2f}|sl_dist=${sl_distance:,.2f}|tp_dist=${tp_distance:,.2f}"
    )

    return True, sl_price, tp_price, actual_rr, desc


# ============================================================================
# SMA / Bollinger Bands helpers
# ============================================================================
def calculate_sma(bars: List[Dict], period: int) -> Optional[float]:
    """Calculate SMA from the last N bars' close prices."""
    if len(bars) < period:
        return None
    closes = [b["close"] for b in bars[-period:]]
    return sum(closes) / period


def calculate_bb(
    bars: List[Dict], period: int = 20, std_dev: float = 2.0
) -> Optional[Dict[str, float]]:
    """Calculate Bollinger Bands."""
    if len(bars) < period:
        return None
    closes = [b["close"] for b in bars[-period:]]
    middle = sum(closes) / period
    variance = sum((c - middle) ** 2 for c in closes) / period
    std = variance ** 0.5
    return {
        "upper": middle + std_dev * std,
        "lower": middle - std_dev * std,
        "middle": middle,
    }

