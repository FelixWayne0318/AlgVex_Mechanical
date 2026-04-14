#!/usr/bin/env python3
"""
Production S/R Zone Validation v1.0

Reads decision snapshots saved by ai_strategy._save_decision_snapshot()
and validates whether each S/R zone actually held or broke in the subsequent
market data (fetched from Binance).

This uses REAL production-calculated zones (with orderbook walls, MTF swing
points, pivot points, volume profile, hold_probability corrections) — not
offline recalculations.

Data flow:
  logs/decisions/decision_*.json  (contains sr_zones from production)
      ↓ read by this script
  For each snapshot:
    1. Extract all S/R zones + timestamp + current_price
    2. Fetch 30M bars from Binance starting at that timestamp
    3. Forward-scan each zone for hold/break (same logic as calibration)
    4. Aggregate statistics by strength, source_type, side, etc.

Usage:
  python3 scripts/validate_production_sr.py                 # All snapshots
  python3 scripts/validate_production_sr.py --days 7        # Last 7 days only
  python3 scripts/validate_production_sr.py --output out.json
  python3 scripts/validate_production_sr.py --min-snapshots 50  # Require N snapshots

Prerequisites:
  - Decision snapshots with sr_zones field (added in v17.0)
  - Internet access to Binance Futures API (for forward price data)
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

# Project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.backtest_math import calculate_atr_wilder

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
# Constants — same as calibrate_hold_probability.py
# ============================================================================
BINANCE_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
SYMBOL = "BTCUSDT"
INTERVAL = "30m"  # v18.2: execution layer migrated from 15M to 30M
FETCH_TIMEOUT = 15.0
FETCH_RETRY_COUNT = 4
FETCH_RETRY_BASE_DELAY = 2.0

FORWARD_SCAN_BARS = 24       # 24 × 30min = 12 hours
BREAK_THRESHOLD_ATR = 0.3    # Same as calibration
PROXIMITY_THRESHOLD_ATR = 1.5

DECISIONS_DIR = PROJECT_ROOT / "logs" / "decisions"


# ============================================================================
# Binance API
# ============================================================================
def fetch_klines_from(start_ms: int, count: int = 60) -> List[Dict]:
    """Fetch klines starting from a specific timestamp."""
    params = {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "startTime": start_ms,
        "limit": min(count, 1500),
    }

    for attempt in range(FETCH_RETRY_COUNT):
        try:
            resp = requests.get(BINANCE_KLINES_URL, params=params, timeout=FETCH_TIMEOUT)
            resp.raise_for_status()
            raw = resp.json()
            bars = []
            for k in raw:
                bars.append({
                    "timestamp": k[0],
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                })
            return bars
        except Exception as e:
            delay = FETCH_RETRY_BASE_DELAY * (2 ** attempt)
            logger.warning(f"Fetch attempt {attempt + 1} failed: {e}, retry in {delay}s")
            time.sleep(delay)

    raise RuntimeError(f"Failed to fetch klines after {FETCH_RETRY_COUNT} attempts")


# ATR imported from utils.backtest_math (SSoT)
calculate_atr = calculate_atr_wilder


# ============================================================================
# Zone hold/break classification (same as calibration)
# ============================================================================
def classify_zone_outcome(
    zone: Dict,
    bars: List[Dict],
    atr: float,
) -> Optional[str]:
    """
    Forward-scan bars to determine if zone held or broke.
    Same Phase 1 + Phase 2 logic as calibrate_hold_probability.py.

    Returns "HELD", "BROKE", or None (never approached).
    """
    if not bars or atr <= 0:
        return None

    is_support = zone["side"] == "support"
    proximity_margin = atr * PROXIMITY_THRESHOLD_ATR
    break_margin = atr * BREAK_THRESHOLD_ATR

    # Phase 1: Was the zone actually tested (approached)?
    approached = False
    for bar in bars:
        if is_support:
            if bar["low"] <= zone["price_high"] + proximity_margin:
                approached = True
                break
        else:
            if bar["high"] >= zone["price_low"] - proximity_margin:
                approached = True
                break

    if not approached:
        return None

    # Phase 2: Hold or break?
    for bar in bars:
        if is_support:
            if bar["close"] < zone["price_low"] - break_margin:
                return "BROKE"
        else:
            if bar["close"] > zone["price_high"] + break_margin:
                return "BROKE"

    return "HELD"


# ============================================================================
# Load decision snapshots
# ============================================================================
def load_snapshots(days: Optional[int] = None) -> List[Dict]:
    """Load decision snapshots that contain sr_zones data."""
    if not DECISIONS_DIR.exists():
        logger.error(f"Decisions directory not found: {DECISIONS_DIR}")
        return []

    cutoff = None
    if days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    snapshots = []
    files = sorted(DECISIONS_DIR.glob("decision_*.json"))
    logger.info(f"Found {len(files)} decision snapshot files")

    skipped_no_sr = 0
    skipped_old = 0

    for f in files:
        try:
            with open(f) as fh:
                data = json.load(fh)

            # Check if sr_zones exists
            sr_zones = data.get("inputs", {}).get("sr_zones")
            if not sr_zones:
                skipped_no_sr += 1
                continue

            # Parse timestamp
            ts_str = data.get("timestamp", "")
            try:
                ts = datetime.fromisoformat(ts_str)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except Exception:
                continue

            if cutoff and ts < cutoff:
                skipped_old += 1
                continue

            # Extract price
            price_data = data.get("inputs", {}).get("price_data", {})
            current_price = price_data.get("price", 0)
            if not current_price:
                # Try technical_data
                tech = data.get("inputs", {}).get("technical_data", {})
                current_price = tech.get("close", 0) or tech.get("price", 0)

            if not current_price or current_price <= 0:
                continue

            snapshots.append({
                "file": f.name,
                "timestamp": ts,
                "timestamp_ms": int(ts.timestamp() * 1000),
                "current_price": current_price,
                "sr_zones": sr_zones,
                "signal": data.get("ai_outputs", {}).get("signal", "HOLD"),
                "confidence": data.get("ai_outputs", {}).get("confidence", "MEDIUM"),
            })

        except Exception as e:
            logger.debug(f"Failed to parse {f.name}: {e}")

    logger.info(
        f"Loaded {len(snapshots)} snapshots with S/R data "
        f"(skipped: {skipped_no_sr} no SR, {skipped_old} too old)"
    )
    return snapshots


# ============================================================================
# Main validation
# ============================================================================
def run_validation(
    snapshots: List[Dict],
    fetch_bars_per_snapshot: int = FORWARD_SCAN_BARS + 20,
) -> Dict[str, Any]:
    """
    Validate each zone in each snapshot against real forward price data.
    """
    # Accumulators
    total_zones = 0
    total_held = 0
    total_skipped = 0  # Never approached

    by_strength: Dict[str, List[int]] = {}
    by_source: Dict[str, List[int]] = {}
    by_side: Dict[str, List[int]] = {"support": [0, 0], "resistance": [0, 0]}
    by_touch: Dict[str, List[int]] = {}

    # Hold probability calibration check
    hp_buckets: Dict[str, List[int]] = {}

    # Per-snapshot results
    snapshot_results = []

    # Batch fetch: group snapshots by time proximity to reduce API calls
    # Sort by timestamp
    snapshots_sorted = sorted(snapshots, key=lambda s: s["timestamp_ms"])

    # Cache fetched bars by start_ms (rounded to 30min)
    bars_cache: Dict[int, List[Dict]] = {}

    for i, snap in enumerate(snapshots_sorted):
        if (i + 1) % 20 == 0:
            logger.info(f"  Processing snapshot {i + 1}/{len(snapshots_sorted)}...")

        start_ms = snap["timestamp_ms"]
        # Round to nearest 30min bar
        bar_ms = 30 * 60 * 1000
        rounded_ms = (start_ms // bar_ms) * bar_ms

        # Fetch bars if not cached
        if rounded_ms not in bars_cache:
            try:
                bars = fetch_klines_from(rounded_ms, count=fetch_bars_per_snapshot)
                bars_cache[rounded_ms] = bars
                time.sleep(0.2)  # Rate limit
            except Exception as e:
                logger.warning(f"Failed to fetch bars for {snap['file']}: {e}")
                continue
        else:
            bars = bars_cache[rounded_ms]

        if len(bars) < 20:
            continue

        # Calculate ATR from initial bars
        atr = calculate_atr(bars[:20])
        if atr <= 0:
            continue

        # Forward bars (skip first bar which is the "current" bar)
        forward_bars = bars[1:FORWARD_SCAN_BARS + 1]
        if len(forward_bars) < 10:
            continue

        sr_data = snap["sr_zones"]
        all_zones = sr_data.get("support_zones", []) + sr_data.get("resistance_zones", [])

        snap_held = snap_broke = snap_skip = 0

        for zone in all_zones:
            outcome = classify_zone_outcome(zone, forward_bars, atr)

            if outcome is None:
                total_skipped += 1
                snap_skip += 1
                continue

            held = 1 if outcome == "HELD" else 0
            total_zones += 1
            total_held += held

            if held:
                snap_held += 1
            else:
                snap_broke += 1

            # By strength
            strength = zone.get("strength", "LOW").upper()
            by_strength.setdefault(strength, [0, 0])
            by_strength[strength][0] += 1
            by_strength[strength][1] += held

            # By source type
            src = zone.get("source_type", "UNKNOWN")
            if isinstance(src, str):
                by_source.setdefault(src, [0, 0])
                by_source[src][0] += 1
                by_source[src][1] += held

            # By side
            side = zone.get("side", "support")
            if side in by_side:
                by_side[side][0] += 1
                by_side[side][1] += held

            # By touch count
            tc = zone.get("touch_count", 0)
            tc_bucket = _touch_bucket(tc)
            by_touch.setdefault(tc_bucket, [0, 0])
            by_touch[tc_bucket][0] += 1
            by_touch[tc_bucket][1] += held

            # By hold probability
            hp = zone.get("hold_probability", 0)
            hp_bucket = _hp_bucket(hp)
            hp_buckets.setdefault(hp_bucket, [0, 0])
            hp_buckets[hp_bucket][0] += 1
            hp_buckets[hp_bucket][1] += held

        snapshot_results.append({
            "file": snap["file"],
            "timestamp": snap["timestamp"].isoformat(),
            "price": snap["current_price"],
            "signal": snap["signal"],
            "zones_total": len(all_zones),
            "zones_tested": snap_held + snap_broke,
            "zones_held": snap_held,
            "zones_broke": snap_broke,
            "zones_skipped": snap_skip,
        })

    # Aggregate
    overall_hold_rate = total_held / total_zones * 100 if total_zones > 0 else 0

    def _dim_stats(dim: Dict[str, List[int]]) -> Dict:
        out = {}
        for k, (total, held) in sorted(dim.items()):
            out[k] = {
                "total": total, "held": held,
                "hold_rate": round(held / total * 100, 1) if total > 0 else 0,
            }
        return out

    # Load calibration for comparison
    cal_info = {}
    cal_path = PROJECT_ROOT / "data" / "calibration" / "latest.json"
    if cal_path.exists():
        try:
            with open(cal_path) as f:
                cal_data = json.load(f)
            cal_info = {
                "version": cal_data.get("version"),
                "overall_hold_rate": cal_data.get("overall_hold_rate"),
                "sample_count": cal_data.get("sample_count"),
            }
        except Exception:
            pass

    return {
        "summary": {
            "snapshots_processed": len(snapshot_results),
            "total_zones_tested": total_zones,
            "total_skipped_untouched": total_skipped,
            "total_held": total_held,
            "overall_hold_rate": round(overall_hold_rate, 1),
            "calibration_comparison": cal_info,
        },
        "by_strength": _dim_stats(by_strength),
        "by_source": _dim_stats(by_source),
        "by_side": _dim_stats(by_side),
        "by_touch": _dim_stats(by_touch),
        "by_hold_probability": _dim_stats(hp_buckets),
        "per_snapshot": snapshot_results,
    }


def _touch_bucket(count: int) -> str:
    if count <= 1:
        return "0-1"
    elif count <= 3:
        return "2-3"
    elif count == 4:
        return "4"
    return "5+"


def _hp_bucket(hp: float) -> str:
    if hp < 0.50:
        return "<0.50"
    elif hp < 0.55:
        return "0.50-0.55"
    elif hp < 0.60:
        return "0.55-0.60"
    elif hp < 0.65:
        return "0.60-0.65"
    elif hp < 0.70:
        return "0.65-0.70"
    elif hp < 0.75:
        return "0.70-0.75"
    return ">0.75"


# ============================================================================
# Display
# ============================================================================
def print_results(results: Dict[str, Any]):
    s = results["summary"]

    print("\n" + "=" * 80)
    print("PRODUCTION S/R ZONE VALIDATION")
    print("=" * 80)
    print(f"Snapshots: {s['snapshots_processed']}")
    print(f"Zones tested: {s['total_zones_tested']} | Skipped (untouched): {s['total_skipped_untouched']}")
    print(f"Overall hold rate: {s['overall_hold_rate']}%")

    cal = s.get("calibration_comparison", {})
    if cal:
        cal_hr = cal.get("overall_hold_rate", 0)
        if cal_hr:
            diff = s["overall_hold_rate"] - cal_hr * 100
            print(f"Calibration comparison: {cal.get('version')} = {cal_hr * 100:.1f}% (diff: {diff:+.1f}pp)")

    print(f"\n  By Strength:")
    for k, v in sorted(results["by_strength"].items()):
        print(f"    {k:8s}: {v['hold_rate']:5.1f}% hold  (N={v['total']})")

    print(f"\n  By Source Type:")
    for k, v in sorted(results["by_source"].items()):
        if v["total"] >= 5:
            print(f"    {k:15s}: {v['hold_rate']:5.1f}% hold  (N={v['total']})")

    print(f"\n  By Side:")
    for k, v in sorted(results["by_side"].items()):
        print(f"    {k:12s}: {v['hold_rate']:5.1f}% hold  (N={v['total']})")

    print(f"\n  By Touch Count:")
    for k in ["0-1", "2-3", "4", "5+"]:
        v = results["by_touch"].get(k, {})
        if v and v.get("total", 0) > 0:
            print(f"    {k:5s}: {v['hold_rate']:5.1f}% hold  (N={v['total']})")

    print(f"\n  HoldProb Calibration Check (does higher hold_prob → higher hold rate?):")
    for k in sorted(results["by_hold_probability"].keys()):
        v = results["by_hold_probability"][k]
        if v.get("total", 0) > 0:
            print(f"    {k:10s}: {v['hold_rate']:5.1f}% actual hold  (N={v['total']})")

    # Timeline summary (by date)
    daily: Dict[str, Dict] = {}
    for snap in results["per_snapshot"]:
        date = snap["timestamp"][:10]
        daily.setdefault(date, {"tested": 0, "held": 0, "snapshots": 0})
        daily[date]["tested"] += snap["zones_tested"]
        daily[date]["held"] += snap["zones_held"]
        daily[date]["snapshots"] += 1

    if daily:
        print(f"\n  Daily Timeline:")
        print(f"  {'Date':<12} {'Snapshots':>10} {'Tested':>8} {'Held':>6} {'Hold%':>7}")
        for date in sorted(daily.keys()):
            d = daily[date]
            hr = d["held"] / d["tested"] * 100 if d["tested"] > 0 else 0
            print(f"  {date:<12} {d['snapshots']:>10} {d['tested']:>8} {d['held']:>6} {hr:>6.1f}%")

    print("\n" + "=" * 80)


# ============================================================================
# Entry point
# ============================================================================
def main():
    parser = argparse.ArgumentParser(description="Validate production S/R zones against real price data")
    parser.add_argument("--days", type=int, default=None, help="Only process snapshots from last N days")
    parser.add_argument("--min-snapshots", type=int, default=10, help="Minimum snapshots required (default: 10)")
    parser.add_argument("--output", type=str, default=None, help="Output JSON file path")
    args = parser.parse_args()

    snapshots = load_snapshots(days=args.days)

    if len(snapshots) < args.min_snapshots:
        print(f"\nInsufficient data: {len(snapshots)} snapshots (need {args.min_snapshots})")
        print(f"S/R zone data is saved since the code update.")
        print(f"Wait for the system to accumulate snapshots (1 per 20 min = ~72/day).")
        if snapshots:
            first_ts = min(s["timestamp"] for s in snapshots)
            last_ts = max(s["timestamp"] for s in snapshots)
            print(f"Current data range: {first_ts.strftime('%Y-%m-%d %H:%M')} → {last_ts.strftime('%Y-%m-%d %H:%M')}")
        sys.exit(1)

    logger.info(f"Validating {len(snapshots)} snapshots...")
    results = run_validation(snapshots)

    print_results(results)

    output_path = args.output or str(PROJECT_ROOT / "data" / "validate_production_sr_result.json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    logger.info(f"Results saved to {output_path}")


if __name__ == "__main__":
    main()
