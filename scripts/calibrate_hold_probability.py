#!/usr/bin/env python3
"""
S/R Zone Hold Probability Auto-Calibration v1.0

Fetches historical 30M bars from Binance, recalculates S/R zones at each
time step, forward-scans to determine hold/break outcomes, then aggregates
statistics by zone attributes (source type, touch count, swing, side,
strength). Outputs calibration factors to data/calibration/latest.json.

Data flow:
  calibrate_hold_probability.py --auto-calibrate  (weekly cron)
    ↓ writes
  data/calibration/latest.json
    ↓ read by (mtime-cached)
  sr_zone_calculator._estimate_hold_probability()

Usage:
  python3 scripts/calibrate_hold_probability.py                  # Interactive
  python3 scripts/calibrate_hold_probability.py --auto-calibrate # Cron mode
  python3 scripts/calibrate_hold_probability.py --days 45        # Custom range
  python3 scripts/calibrate_hold_probability.py --dry-run        # Preview only
  python3 scripts/calibrate_hold_probability.py --no-telegram    # Skip notification

Author: AlgVex Team
Date: 2026-02
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

# Project root
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.sr_zone_calculator import SRZoneCalculator, SRZone
from utils.backtest_math import calculate_atr_wilder, calculate_sma, calculate_bb

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BINANCE_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
SYMBOL = "BTCUSDT"
INTERVAL = "30m"  # v18.2: execution layer migrated from 15M to 30M
MAX_KLINES_PER_REQUEST = 1500
FETCH_TIMEOUT = 15.0
FETCH_RETRY_COUNT = 4
FETCH_RETRY_BASE_DELAY = 2.0

# Calibration parameters (code constants, not YAML — same reason as reflection)
LOOKBACK_BARS = 100         # Bars used for zone calculation
FORWARD_SCAN_BARS = 24      # Forward scan window (24 × 30min = 12 hours)
STEP_BARS = 2               # Step every 2 bars (= 1 hour at 30M)
MIN_SAMPLES = 100           # Minimum samples for valid calibration
BREAK_THRESHOLD_ATR = 0.3   # Zone considered "broken" when close crosses zone ± ATR×this
PROXIMITY_THRESHOLD_ATR = 1.5  # Zone must be "approached" within ATR×this to count as tested

# Output paths
CALIBRATION_DIR = PROJECT_ROOT / "data" / "calibration"
CALIBRATION_FILE = CALIBRATION_DIR / "latest.json"
CALIBRATION_HISTORY_DIR = CALIBRATION_DIR / "history"

# Telegram
ENV_FILE = Path.home() / ".env.algvex"

logger = logging.getLogger("calibrate_hold_prob")


# ===========================================================================
# Data fetching
# ===========================================================================

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


def fetch_bars(days: int = 30) -> List[Dict[str, Any]]:
    """
    Fetch N days of completed 30M bars from Binance.

    Returns list of dicts sorted oldest→newest:
      {'timestamp': int, 'open': float, 'high': float, 'low': float,
       'close': float, 'volume': float, 'taker_buy_volume': float}
    """
    bars_needed = days * 48  # 48 bars per day at 30M
    all_klines: List[List] = []
    end_time: Optional[int] = None

    logger.info(f"Fetching {bars_needed} bars ({days} days of {INTERVAL})...")

    while len(all_klines) < bars_needed:
        batch = fetch_klines(end_time=end_time)
        if not batch:
            break

        # Prepend (older data first)
        all_klines = batch + all_klines

        # Next batch ends just before the oldest bar in current batch
        end_time = batch[0][0] - 1

        remaining = bars_needed - len(all_klines)
        logger.info(f"  Fetched {len(all_klines)} bars, {max(0, remaining)} remaining...")

        if len(batch) < MAX_KLINES_PER_REQUEST:
            break  # No more data

        time.sleep(0.3)  # Rate limit courtesy

    # Strip last (incomplete) candle
    if all_klines:
        all_klines = all_klines[:-1]

    # Convert to structured dicts
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
# Old inline implementations removed — calibrate_hold_probability.py had a
# critical ATR bug (simple SMA instead of Wilder's smoothing at line 183).
calculate_atr = calculate_atr_wilder  # alias for call-site compatibility


# ===========================================================================
# Zone hold/break detection
# ===========================================================================

def classify_zone_outcome(
    zone: SRZone,
    bars: List[Dict],
    start_idx: int,
    forward_bars: int,
    atr: float,
) -> Optional[str]:
    """
    Forward-scan to determine if a zone held or was broken.

    Phase 1 (proximity filter): Check if price actually approached the zone
    during the forward scan. A zone that was never tested is not a valid sample.
    - Support: any bar low must come within zone.price_high + ATR×PROXIMITY_THRESHOLD
    - Resistance: any bar high must come within zone.price_low - ATR×PROXIMITY_THRESHOLD

    Phase 2 (hold/break classification):
    A support zone is "broken" when a bar close falls below zone_low - ATR×BREAK_THRESHOLD.
    A resistance zone is "broken" when a bar close rises above zone_high + ATR×BREAK_THRESHOLD.
    Otherwise the zone "held".

    Returns "HELD", "BROKE", or None (insufficient data or never approached).
    """
    end_idx = min(start_idx + forward_bars, len(bars))
    if end_idx <= start_idx:
        return None

    break_margin = atr * BREAK_THRESHOLD_ATR
    proximity_margin = atr * PROXIMITY_THRESHOLD_ATR
    is_support = zone.side == "support"

    # Phase 1: Proximity filter — was this zone actually tested?
    approached = False
    for i in range(start_idx, end_idx):
        if is_support:
            # Support tested when bar low drops near the zone's upper edge
            if bars[i]["low"] <= zone.price_high + proximity_margin:
                approached = True
                break
        else:
            # Resistance tested when bar high rises near the zone's lower edge
            if bars[i]["high"] >= zone.price_low - proximity_margin:
                approached = True
                break

    if not approached:
        return None  # Zone never tested — not a valid sample

    # Phase 2: Hold/break classification
    for i in range(start_idx, end_idx):
        bar_close = bars[i]["close"]

        if is_support:
            # Support broken if close falls below zone_low - margin
            if bar_close < zone.price_low - break_margin:
                return "BROKE"
        else:
            # Resistance broken if close rises above zone_high + margin
            if bar_close > zone.price_high + break_margin:
                return "BROKE"

    return "HELD"


# ===========================================================================
# Bucketing helpers
# ===========================================================================

def touch_bucket(count: int) -> str:
    """Map touch count to calibration bucket key."""
    if count <= 1:
        return "0-1"
    elif count <= 3:
        return "2-3"
    elif count == 4:
        return "4"
    else:
        return "5+"


def strength_bucket(strength: str) -> str:
    """Normalize strength to bucket key."""
    return strength.upper() if strength else "LOW"


# ===========================================================================
# Main calibration logic
# ===========================================================================

def run_calibration(
    days: int = 30,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Run the full calibration pipeline.

    Returns the calibration result dict.
    """
    # 1. Fetch data
    bars = fetch_bars(days=days)
    if len(bars) < LOOKBACK_BARS + FORWARD_SCAN_BARS + 10:
        raise ValueError(
            f"Insufficient data: {len(bars)} bars "
            f"(need at least {LOOKBACK_BARS + FORWARD_SCAN_BARS + 10})"
        )

    logger.info(f"Starting calibration with {len(bars)} bars...")

    # 2. Initialize calculator (no orderbook, no MTF — pure bar-based)
    sr_calc = SRZoneCalculator(
        swing_detection_enabled=True,
        swing_left_bars=5,
        swing_right_bars=5,
        touch_count_enabled=True,
        use_atr_adaptive=True,
    )

    # 3. Sliding window: calculate zones, then forward-scan for outcome
    # Accumulators
    total_zones = 0
    total_held = 0
    total_skipped_untouched = 0  # Zones filtered by proximity check

    # Per-dimension accumulators: {bucket_key: [total, held]}
    by_source: Dict[str, List[int]] = {}
    by_touch: Dict[str, List[int]] = {}
    by_strength: Dict[str, List[int]] = {}
    by_swing: Dict[str, List[int]] = {"with": [0, 0], "without": [0, 0]}
    by_side: Dict[str, List[int]] = {"support": [0, 0], "resistance": [0, 0]}

    # Weight stats for base formula regression
    weight_held_pairs: List[Tuple[float, int]] = []  # (weight, 1=held/0=broke)

    scan_start = LOOKBACK_BARS
    scan_end = len(bars) - FORWARD_SCAN_BARS

    if scan_start >= scan_end:
        raise ValueError(
            f"No scannable range: start={scan_start}, end={scan_end}"
        )

    steps = 0
    for idx in range(scan_start, scan_end, STEP_BARS):
        # P1 fix: Reset stickiness/flip caches each step to prevent
        # cross-step pollution. In production these caches only accumulate
        # within a single session (20min ticks). In calibration, continuous
        # sliding would cause late-step stickiness to freeze zone positions
        # and systematically inflate hold rates.
        sr_calc._zone_stickiness_cache.clear()
        sr_calc._flip_discount_cache.clear()

        # Window for zone calculation
        window = bars[idx - LOOKBACK_BARS: idx]
        current_price = bars[idx]["close"]
        atr = calculate_atr(window)

        if atr <= 0 or current_price <= 0:
            continue

        # Calculate basic indicators for zone generation
        sma_50 = calculate_sma(window, 50)
        sma_200 = calculate_sma(window, 200) if len(window) >= 200 else None
        bb = calculate_bb(window, 20, 2.0)

        sma_data = {}
        if sma_50:
            sma_data["sma_50"] = sma_50
        if sma_200:
            sma_data["sma_200"] = sma_200

        # Calculate zones (no orderbook, no MTF — bar-based only)
        result = sr_calc.calculate(
            current_price=current_price,
            bb_data=bb,
            sma_data=sma_data if sma_data else None,
            orderbook_anomalies=None,
            bars_data=window,
            atr_value=atr,
        )

        all_zones = result["support_zones"] + result["resistance_zones"]

        for zone in all_zones:
            # Forward scan for outcome (with proximity filter)
            outcome = classify_zone_outcome(
                zone, bars, idx, FORWARD_SCAN_BARS, atr
            )
            if outcome is None:
                total_skipped_untouched += 1
                continue

            held = 1 if outcome == "HELD" else 0
            total_zones += 1
            total_held += held

            # By source type
            src = zone.source_type if isinstance(zone.source_type, str) else str(zone.source_type)
            if src not in by_source:
                by_source[src] = [0, 0]
            by_source[src][0] += 1
            by_source[src][1] += held

            # By touch count
            tb = touch_bucket(zone.touch_count)
            if tb not in by_touch:
                by_touch[tb] = [0, 0]
            by_touch[tb][0] += 1
            by_touch[tb][1] += held

            # By strength
            sb = strength_bucket(zone.strength)
            if sb not in by_strength:
                by_strength[sb] = [0, 0]
            by_strength[sb][0] += 1
            by_strength[sb][1] += held

            # By swing
            sw_key = "with" if zone.has_swing_point else "without"
            by_swing[sw_key][0] += 1
            by_swing[sw_key][1] += held

            # By side
            by_side[zone.side][0] += 1
            by_side[zone.side][1] += held

            # Weight for regression
            max_w = sr_calc._max_zone_weight
            norm_weight = min(zone.total_weight / max_w, 1.0) if max_w > 0 else 0.0
            weight_held_pairs.append((norm_weight, held))

        steps += 1
        if steps % 100 == 0:
            total_candidates = total_zones + total_skipped_untouched
            logger.info(
                f"  Step {steps}: {total_zones} tested / {total_candidates} total zones "
                f"({total_skipped_untouched} untouched filtered), "
                f"hold rate = {total_held / total_zones * 100:.1f}%"
            )

    # 4. Aggregate results
    total_candidates = total_zones + total_skipped_untouched
    filter_pct = total_skipped_untouched / total_candidates * 100 if total_candidates > 0 else 0
    logger.info(
        f"Calibration complete: {total_zones} tested zones / {total_candidates} total "
        f"({total_skipped_untouched} untouched filtered = {filter_pct:.1f}%), {steps} steps"
    )

    if total_zones < MIN_SAMPLES:
        raise ValueError(
            f"Insufficient samples: {total_zones} < {MIN_SAMPLES}. "
            f"Increase --days or reduce STEP_BARS."
        )

    overall_hold_rate = total_held / total_zones

    # 4a. Base formula regression (simple linear: hold = intercept + slope × norm_weight)
    intercept, slope = _linear_regression(weight_held_pairs)

    # 4b. Source factors (relative to overall)
    source_factors = {}
    for src, (total, held) in by_source.items():
        if total >= 20:  # Minimum 20 samples per bucket
            rate = held / total
            source_factors[src] = round(rate / overall_hold_rate, 3)
        else:
            source_factors[src] = 1.0  # Not enough data, neutral

    # 4c. Touch factors
    touch_factors = {}
    for tb, (total, held) in by_touch.items():
        if total >= 20:
            rate = held / total
            touch_factors[tb] = round(rate / overall_hold_rate, 3)
        else:
            touch_factors[tb] = 1.0

    # 4d. Swing factors
    swing_with_rate = by_swing["with"][1] / by_swing["with"][0] if by_swing["with"][0] >= 20 else overall_hold_rate
    swing_without_rate = by_swing["without"][1] / by_swing["without"][0] if by_swing["without"][0] >= 20 else overall_hold_rate
    swing_factor_with = round(swing_with_rate / overall_hold_rate, 3)
    swing_factor_without = round(swing_without_rate / overall_hold_rate, 3)

    # 4e. Side factors
    side_factors = {}
    for side, (total, held) in by_side.items():
        if total >= 30:  # Higher threshold for side (regime-dependent)
            rate = held / total
            side_factors[side] = round(rate / overall_hold_rate, 3)

    # 5. Build calibration result
    now_utc = datetime.now(timezone.utc)
    calibration = {
        "version": f"v-auto-{now_utc.strftime('%Y%m%d')}",
        "calibrated_at": now_utc.isoformat(),
        "sample_count": total_zones,
        "overall_hold_rate": round(overall_hold_rate, 4),
        "data_days": days,
        "bars_count": len(bars),
        "steps_count": steps,
        "proximity_filter": {
            "total_candidates": total_candidates,
            "tested_zones": total_zones,
            "skipped_untouched": total_skipped_untouched,
            "filter_rate": round(filter_pct / 100, 4),
            "threshold_atr": PROXIMITY_THRESHOLD_ATR,
        },
        "base_intercept": round(intercept, 4),
        "base_slope": round(slope, 4),
        "source_factors": source_factors,
        "touch_factors": touch_factors,
        "swing_factor_with": swing_factor_with,
        "swing_factor_without": swing_factor_without,
        "side_factors": side_factors,
        # Detailed statistics (for diagnostics, not consumed by loader)
        "stats": {
            "by_source": {k: {"total": v[0], "held": v[1], "rate": round(v[1] / v[0], 4) if v[0] > 0 else 0} for k, v in by_source.items()},
            "by_touch": {k: {"total": v[0], "held": v[1], "rate": round(v[1] / v[0], 4) if v[0] > 0 else 0} for k, v in by_touch.items()},
            "by_strength": {k: {"total": v[0], "held": v[1], "rate": round(v[1] / v[0], 4) if v[0] > 0 else 0} for k, v in by_strength.items()},
            "by_swing": {k: {"total": v[0], "held": v[1], "rate": round(v[1] / v[0], 4) if v[0] > 0 else 0} for k, v in by_swing.items()},
            "by_side": {k: {"total": v[0], "held": v[1], "rate": round(v[1] / v[0], 4) if v[0] > 0 else 0} for k, v in by_side.items()},
        },
    }

    # 6. Sanity checks
    _sanity_check(calibration)

    # 7. Save
    if not dry_run:
        _save_calibration(calibration)
        logger.info(f"Calibration saved to {CALIBRATION_FILE}")
    else:
        logger.info("Dry run — calibration not saved")

    return calibration


def _linear_regression(pairs: List[Tuple[float, int]]) -> Tuple[float, float]:
    """
    Simple OLS: hold_rate = intercept + slope × norm_weight.

    Uses binned approach: group by weight decile, compute hold rate per bin,
    then fit line to bin centroids. More robust than raw point regression.
    """
    if not pairs:
        return 0.58, 0.14  # Default v8.2

    # Bin into 10 deciles
    n_bins = 10
    bins: Dict[int, List[int]] = {i: [] for i in range(n_bins)}
    for w, h in pairs:
        bin_idx = min(int(w * n_bins), n_bins - 1)
        bins[bin_idx].append(h)

    # Centroids
    xs = []
    ys = []
    for i in range(n_bins):
        if len(bins[i]) >= 5:  # Minimum 5 per bin
            x = (i + 0.5) / n_bins
            y = sum(bins[i]) / len(bins[i])
            xs.append(x)
            ys.append(y)

    if len(xs) < 3:
        # Not enough bins, use simple mean
        overall = sum(h for _, h in pairs) / len(pairs)
        return round(overall, 4), 0.0

    # OLS
    n = len(xs)
    sx = sum(xs)
    sy = sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))

    denom = n * sxx - sx * sx
    if abs(denom) < 1e-12:
        return round(sy / n, 4), 0.0

    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n

    # Clamp to reasonable range
    intercept = max(0.45, min(0.70, intercept))
    slope = max(-0.10, min(0.30, slope))

    return round(intercept, 4), round(slope, 4)


def _sanity_check(cal: Dict[str, Any]) -> None:
    """
    Validate calibration output for obvious anomalies.

    Raises ValueError for hard failures (data is clearly wrong).
    Logs warnings for soft issues (data is usable but unusual).
    """
    hard_failures = []
    warnings = []

    # Overall hold rate should be in [0.40, 0.85]
    ohr = cal["overall_hold_rate"]
    if ohr < 0.30 or ohr > 0.90:
        hard_failures.append(f"overall_hold_rate={ohr:.3f} outside [0.30, 0.90] — data likely corrupted")
    elif ohr < 0.40 or ohr > 0.85:
        warnings.append(f"overall_hold_rate={ohr:.3f} outside [0.40, 0.85]")

    # Base formula range
    intercept = cal["base_intercept"]
    slope = cal["base_slope"]
    if intercept + slope > 0.90:
        warnings.append(f"base max ({intercept}+{slope}={intercept + slope}) > 0.90")
    if intercept < 0.40:
        warnings.append(f"base_intercept={intercept} < 0.40")

    # Factors should be in [0.70, 1.40] (soft), [0.50, 1.60] (hard)
    for dim_name, factors in [("source", cal["source_factors"]), ("touch", cal["touch_factors"])]:
        for key, val in factors.items():
            if val < 0.50 or val > 1.60:
                hard_failures.append(f"{dim_name}[{key}]={val} outside [0.50, 1.60] — extreme outlier")
            elif val < 0.70 or val > 1.40:
                warnings.append(f"{dim_name}[{key}]={val} outside [0.70, 1.40]")

    if hard_failures:
        for f in hard_failures:
            logger.error(f"  HARD FAIL: {f}")
        raise ValueError(
            f"Sanity check FAILED ({len(hard_failures)} hard failures): "
            f"{'; '.join(hard_failures)}. "
            f"Calibration NOT saved — system continues with previous/default factors."
        )

    if warnings:
        logger.warning(f"Sanity check warnings ({len(warnings)}):")
        for w in warnings:
            logger.warning(f"  - {w}")
    else:
        logger.info("Sanity check passed")


def _save_calibration(cal: Dict[str, Any]) -> None:
    """Save calibration to latest.json and archive to history."""
    # Ensure directories exist
    CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
    CALIBRATION_HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    # Write latest
    with open(CALIBRATION_FILE, "w") as f:
        json.dump(cal, f, indent=2, default=str)

    # Archive with timestamp
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    history_file = CALIBRATION_HISTORY_DIR / f"calibration_{ts}.json"
    with open(history_file, "w") as f:
        json.dump(cal, f, indent=2, default=str)

    # Prune old history (keep last 12)
    history_files = sorted(CALIBRATION_HISTORY_DIR.glob("calibration_*.json"))
    if len(history_files) > 12:
        for old_file in history_files[:-12]:
            old_file.unlink()
            logger.debug(f"Pruned old calibration: {old_file.name}")


# ===========================================================================
# Telegram notification (private chat only)
# ===========================================================================

def _load_telegram_env() -> Tuple[Optional[str], Optional[str]]:
    """Load Telegram credentials from ~/.env.algvex."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if token and chat_id:
        return token, chat_id

    # Try loading from file
    if ENV_FILE.exists():
        try:
            with open(ENV_FILE) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, _, val = line.partition("=")
                        key = key.strip()
                        val = val.strip().strip('"').strip("'")
                        if key == "TELEGRAM_BOT_TOKEN" and not token:
                            token = val
                        elif key == "TELEGRAM_CHAT_ID" and not chat_id:
                            chat_id = val
        except Exception as e:
            logger.warning(f"Failed to read {ENV_FILE}: {e}")

    return token, chat_id


def send_telegram_notification(cal: Dict[str, Any], success: bool, error_msg: str = "") -> bool:
    """
    Send calibration result to Telegram private chat.

    broadcast=False (private chat only) — this is an operational/diagnostic
    message, not a trading signal.
    """
    token, chat_id = _load_telegram_env()
    if not token or not chat_id:
        logger.info("Telegram credentials not available, skipping notification")
        return False

    if success:
        msg = _format_success_message(cal)
    else:
        msg = _format_failure_message(error_msg)

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": msg,
        "parse_mode": "Markdown",
        "disable_notification": True,  # Silent — operational message
    }

    try:
        resp = requests.post(url, json=payload, timeout=10.0)
        result = resp.json()

        if result.get("ok"):
            logger.info("Telegram notification sent (private chat)")
            return True

        # Markdown parse error fallback
        error_desc = result.get("description", "")
        if "can't parse" in error_desc.lower() or "parse entities" in error_desc.lower():
            logger.warning("Markdown parse error, retrying without formatting")
            payload.pop("parse_mode", None)
            resp = requests.post(url, json=payload, timeout=10.0)
            result = resp.json()
            if result.get("ok"):
                return True

        logger.warning(f"Telegram API error: {error_desc}")
        return False

    except Exception as e:
        logger.warning(f"Telegram notification failed: {e}")
        return False


def _format_success_message(cal: Dict[str, Any]) -> str:
    """Format successful calibration result for Telegram."""
    stats = cal.get("stats", {})

    # Source type breakdown
    source_lines = []
    for src, data in sorted(stats.get("by_source", {}).items()):
        factor = cal["source_factors"].get(src, 1.0)
        source_lines.append(
            f"  {src}: {data['rate'] * 100:.1f}% ({data['total']}N) factor={factor}"
        )

    # Touch count breakdown
    touch_lines = []
    for tb in ["0-1", "2-3", "4", "5+"]:
        data = stats.get("by_touch", {}).get(tb, {})
        if data:
            factor = cal["touch_factors"].get(tb, 1.0)
            touch_lines.append(
                f"  {tb}: {data['rate'] * 100:.1f}% ({data['total']}N) factor={factor}"
            )

    # Side breakdown
    side_lines = []
    for side in ["support", "resistance"]:
        data = stats.get("by_side", {}).get(side, {})
        if data:
            factor = cal.get("side_factors", {}).get(side, 1.0)
            side_lines.append(
                f"  {side}: {data['rate'] * 100:.1f}% ({data['total']}N) factor={factor}"
            )

    # Proximity filter line
    pf = cal.get("proximity_filter", {})
    if pf:
        pf_line = (
            f"Filter: {pf.get('tested_zones', '?')} tested / "
            f"{pf.get('total_candidates', '?')} total "
            f"({pf.get('filter_rate', 0) * 100:.0f}% untouched filtered)\n"
        )
    else:
        pf_line = ""

    msg = (
        f"*S/R Hold Probability Calibration*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Version: `{cal['version']}`\n"
        f"Samples: {cal['sample_count']} tested zones\n"
        f"Data: {cal['data_days']}d / {cal['bars_count']} bars\n"
        f"{pf_line}"
        f"Overall hold rate: *{cal['overall_hold_rate'] * 100:.1f}%*\n"
        f"\n"
        f"Base: intercept={cal['base_intercept']}, slope={cal['base_slope']}\n"
        f"\n"
        f"*By Source:*\n"
        f"{chr(10).join(source_lines)}\n"
        f"\n"
        f"*By Touch Count:*\n"
        f"{chr(10).join(touch_lines)}\n"
        f"\n"
        f"*By Side:*\n"
        f"{chr(10).join(side_lines)}\n"
        f"\n"
        f"Swing: with={cal['swing_factor_with']}, without={cal['swing_factor_without']}\n"
        f"\n"
        f"Saved: `data/calibration/latest.json`"
    )
    return msg


def _format_failure_message(error_msg: str) -> str:
    """Format calibration failure notification."""
    return (
        f"*S/R Calibration FAILED*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Error: {error_msg}\n"
        f"\n"
        f"System will continue using default v8.2 factors.\n"
        f"Check logs: `scripts/calibrate_hold_probability.py`"
    )


# ===========================================================================
# Display helpers
# ===========================================================================

def print_summary(cal: Dict[str, Any]) -> None:
    """Print human-readable calibration summary to stdout."""
    print("\n" + "=" * 60)
    print("  S/R Zone Hold Probability Calibration Results")
    print("=" * 60)
    print(f"  Version:          {cal['version']}")
    print(f"  Calibrated at:    {cal['calibrated_at']}")
    print(f"  Data coverage:    {cal['data_days']} days ({cal['bars_count']} bars)")
    print(f"  Overall hold rate:{cal['overall_hold_rate'] * 100:.1f}%")
    print()

    # Proximity filter stats
    pf = cal.get("proximity_filter", {})
    if pf:
        print(f"  Proximity filter: {pf.get('tested_zones', '?')} tested / "
              f"{pf.get('total_candidates', '?')} total zones "
              f"({pf.get('skipped_untouched', '?')} untouched filtered = "
              f"{pf.get('filter_rate', 0) * 100:.1f}%)")
        print(f"  Threshold:        {pf.get('threshold_atr', '?')} × ATR")
    else:
        print(f"  Samples:          {cal['sample_count']} zones")
    print()
    print(f"  Base formula: hold = {cal['base_intercept']} + {cal['base_slope']} × (weight/max)")
    print()

    # Source factors
    print("  Source Type Factors:")
    stats = cal.get("stats", {})
    for src, factor in sorted(cal["source_factors"].items()):
        data = stats.get("by_source", {}).get(src, {})
        n = data.get("total", "?")
        rate = data.get("rate", 0)
        print(f"    {src:15s}: factor={factor:.3f}  (rate={rate * 100:.1f}%, N={n})")

    # Touch factors
    print("\n  Touch Count Factors:")
    for tb in ["0-1", "2-3", "4", "5+"]:
        factor = cal["touch_factors"].get(tb, 1.0)
        data = stats.get("by_touch", {}).get(tb, {})
        n = data.get("total", "?")
        rate = data.get("rate", 0)
        print(f"    {tb:5s}: factor={factor:.3f}  (rate={rate * 100:.1f}%, N={n})")

    # Swing
    print(f"\n  Swing: with={cal['swing_factor_with']:.3f}, without={cal['swing_factor_without']:.3f}")

    # Side
    if cal.get("side_factors"):
        print("\n  Side Factors:")
        for side, factor in cal["side_factors"].items():
            data = stats.get("by_side", {}).get(side, {})
            print(f"    {side:12s}: factor={factor:.3f}  (rate={data.get('rate', 0) * 100:.1f}%, N={data.get('total', '?')})")

    # Strength
    print("\n  Strength Stats (info only, not factored):")
    for s in ["LOW", "MEDIUM", "HIGH"]:
        data = stats.get("by_strength", {}).get(s, {})
        if data:
            print(f"    {s:8s}: {data.get('rate', 0) * 100:.1f}% (N={data.get('total', 0)})")

    print("\n" + "=" * 60)


# ===========================================================================
# Entry point
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="S/R Zone Hold Probability Auto-Calibration"
    )
    parser.add_argument(
        "--days", type=int, default=30,
        help="Number of days of historical data (default: 30)"
    )
    parser.add_argument(
        "--auto-calibrate", action="store_true",
        help="Non-interactive mode for cron jobs"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run calibration but do not save results"
    )
    parser.add_argument(
        "--no-telegram", action="store_true",
        help="Skip Telegram notification"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable verbose logging"
    )
    args = parser.parse_args()

    # Setup logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    logger.info("=" * 50)
    logger.info("S/R Zone Hold Probability Calibration")
    logger.info(f"Days: {args.days}, Dry run: {args.dry_run}")
    logger.info("=" * 50)

    success = False
    error_msg = ""
    cal = None

    try:
        cal = run_calibration(days=args.days, dry_run=args.dry_run)
        success = True
        print_summary(cal)
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Calibration failed: {e}", exc_info=True)

    # Telegram notification (private chat, broadcast=False)
    if not args.no_telegram and not args.dry_run:
        send_telegram_notification(cal or {}, success, error_msg)

    if not success:
        sys.exit(1)

    logger.info("Calibration complete")


if __name__ == "__main__":
    main()
