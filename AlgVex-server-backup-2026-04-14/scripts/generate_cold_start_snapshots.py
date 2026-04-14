#!/usr/bin/env python3
"""
Generate cold-start feature snapshots from Binance historical klines.

Downloads real BTC/USDT OHLCV data from Binance public API (no key needed),
computes indicators, and saves feature snapshots to data/feature_snapshots/
in the format expected by calibrate_anticipatory.py.

Run this once when starting fresh (e.g., after clearing AI-era data).

Usage:
    python3 scripts/generate_cold_start_snapshots.py          # default 90 days
    python3 scripts/generate_cold_start_snapshots.py --days 120
    python3 scripts/generate_cold_start_snapshots.py --clear   # clear dir first
"""

import argparse
import json
import logging
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import urllib.request

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

SNAPSHOT_DIR = PROJECT_ROOT / "data" / "feature_snapshots"
BINANCE_BASE = "https://fapi.binance.com"


# ─────────────────────────────────────────────────────────────────────────────
# Binance public API helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get(url: str, params: dict) -> list:
    """Simple GET with query string, returns parsed JSON."""
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    full_url = f"{url}?{qs}"
    for attempt in range(3):
        try:
            with urllib.request.urlopen(full_url, timeout=10) as r:
                return json.loads(r.read())
        except Exception as e:
            if attempt == 2:
                raise
            logger.warning(f"Retry {attempt+1}: {e}")
            time.sleep(2)
    return []


def fetch_klines(symbol: str, interval: str, limit: int = 1500) -> List[Dict]:
    """
    Fetch historical klines from Binance futures public API.

    Returns list of dicts with keys: open_time, open, high, low, close, volume.
    """
    raw = _get(
        f"{BINANCE_BASE}/fapi/v1/klines",
        {"symbol": symbol, "interval": interval, "limit": limit},
    )
    bars = []
    for row in raw:
        bars.append({
            "open_time": int(row[0]) // 1000,  # seconds
            "open":   float(row[1]),
            "high":   float(row[2]),
            "low":    float(row[3]),
            "close":  float(row[4]),
            "volume": float(row[5]),
        })
    logger.info(f"Fetched {len(bars)} {interval} bars for {symbol}")
    return bars


def fetch_funding_rates(symbol: str, limit: int = 1000) -> List[Dict]:
    """Fetch recent funding rates from Binance."""
    try:
        raw = _get(
            f"{BINANCE_BASE}/fapi/v1/fundingRate",
            {"symbol": symbol, "limit": limit},
        )
        rates = {}
        for row in raw:
            ts = int(row["fundingTime"]) // 1000
            rates[ts] = float(row["fundingRate"])
        logger.info(f"Fetched {len(rates)} funding rate records")
        return rates
    except Exception as e:
        logger.warning(f"Could not fetch funding rates: {e} — using 0.01% default")
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Indicator computation (pure Python, no external deps)
# ─────────────────────────────────────────────────────────────────────────────

def compute_sma(values: List[float], period: int) -> List[Optional[float]]:
    """Simple moving average. Returns None for warmup bars."""
    result = [None] * len(values)
    for i in range(period - 1, len(values)):
        result[i] = sum(values[i - period + 1 : i + 1]) / period
    return result


def compute_ema(values: List[float], period: int) -> List[Optional[float]]:
    """Exponential moving average. Returns None for warmup bars."""
    result = [None] * len(values)
    k = 2.0 / (period + 1)
    for i in range(len(values)):
        if i < period - 1:
            continue
        if i == period - 1:
            result[i] = sum(values[:period]) / period
        else:
            result[i] = values[i] * k + result[i - 1] * (1 - k)
    return result


def compute_atr(bars: List[Dict], period: int = 14) -> List[float]:
    """ATR using Wilder's smoothing."""
    trs = [bars[0]["high"] - bars[0]["low"]]
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))

    atrs = [0.0] * len(bars)
    if len(trs) >= period:
        atrs[period - 1] = sum(trs[:period]) / period
        for i in range(period, len(trs)):
            atrs[i] = (atrs[i - 1] * (period - 1) + trs[i]) / period
    return atrs


def compute_rsi(closes: List[float], period: int = 14) -> List[float]:
    """RSI using Wilder's smoothing."""
    rsi = [50.0] * len(closes)
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    if len(gains) < period:
        return rsi

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        if avg_loss == 0:
            rsi[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi[i + 1] = 100 - 100 / (1 + rs)
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    return rsi


def compute_macd(
    closes: List[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Tuple[List[float], List[float], List[float]]:
    """MACD line, signal line, histogram."""
    ema_fast = compute_ema(closes, fast)
    ema_slow = compute_ema(closes, slow)

    macd_line = [0.0] * len(closes)
    for i in range(len(closes)):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            macd_line[i] = ema_fast[i] - ema_slow[i]

    # Compute signal as EMA(9) of macd_line (only valid portion)
    # Find first non-zero index
    start = next((i for i in range(len(macd_line)) if macd_line[i] != 0.0), len(macd_line))
    signal_line = [0.0] * len(closes)
    if start + signal <= len(closes):
        sig_ema = compute_ema(macd_line[start:], signal)
        for i, v in enumerate(sig_ema):
            if v is not None:
                signal_line[start + i] = v

    hist = [macd_line[i] - signal_line[i] for i in range(len(closes))]
    return macd_line, signal_line, hist


def compute_obv(closes: List[float], volumes: List[float]) -> List[float]:
    """On-Balance Volume (cumulative)."""
    obv = [0.0] * len(closes)
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            obv[i] = obv[i - 1] + volumes[i]
        elif closes[i] < closes[i - 1]:
            obv[i] = obv[i - 1] - volumes[i]
        else:
            obv[i] = obv[i - 1]
    return obv


def ema_smooth(values: List[float], period: int = 20) -> List[float]:
    """EMA smoothing for OBV."""
    result = list(values)
    k = 2.0 / (period + 1)
    for i in range(1, len(result)):
        result[i] = values[i] * k + result[i - 1] * (1 - k)
    return result


def classify_extension(ratio: float) -> str:
    """Map extension ratio to regime."""
    abs_ratio = abs(ratio)
    if abs_ratio >= 5.0:
        return "EXTREME"
    elif abs_ratio >= 3.0:
        return "OVEREXTENDED"
    elif abs_ratio >= 2.0:
        return "EXTENDED"
    return "NORMAL"


# ─────────────────────────────────────────────────────────────────────────────
# Divergence detection
# ─────────────────────────────────────────────────────────────────────────────

def find_local_maxima(values: List[float], window: int = 3) -> List[int]:
    """Indices of local maxima."""
    result = []
    for i in range(window, len(values) - window):
        if all(values[i] >= values[i - j] for j in range(1, window + 1)) and \
           all(values[i] >= values[i + j] for j in range(1, window + 1)):
            result.append(i)
    return result


def find_local_minima(values: List[float], window: int = 3) -> List[int]:
    """Indices of local minima."""
    result = []
    for i in range(window, len(values) - window):
        if all(values[i] <= values[i - j] for j in range(1, window + 1)) and \
           all(values[i] <= values[i + j] for j in range(1, window + 1)):
            result.append(i)
    return result


def detect_divergence(
    prices: List[float],
    indicator: List[float],
    i: int,
    lookback: int = 20,
) -> str:
    """
    Detect RSI/MACD/OBV divergence at position i.

    Returns: "BULLISH", "BEARISH", or "NONE"
    """
    start = max(0, i - lookback)
    price_slice = prices[start : i + 1]
    ind_slice = indicator[start : i + 1]

    if len(price_slice) < 5:
        return "NONE"

    # Bearish divergence: price higher high, indicator lower high
    price_max = max_peaks = [j for j in range(2, len(price_slice) - 1)
                              if price_slice[j] > price_slice[j - 1] and price_slice[j] > price_slice[j + 1]]
    if len(price_max) >= 2:
        p1, p2 = price_max[-2], price_max[-1]
        if (price_slice[p2] > price_slice[p1] and
                ind_slice[p2] < ind_slice[p1] and
                abs(p2 - p1) <= 15):
            return "BEARISH"

    # Bullish divergence: price lower low, indicator higher low
    price_min = [j for j in range(2, len(price_slice) - 1)
                 if price_slice[j] < price_slice[j - 1] and price_slice[j] < price_slice[j + 1]]
    if len(price_min) >= 2:
        p1, p2 = price_min[-2], price_min[-1]
        if (price_slice[p2] < price_slice[p1] and
                ind_slice[p2] > ind_slice[p1] and
                abs(p2 - p1) <= 15):
            return "BULLISH"

    return "NONE"


# ─────────────────────────────────────────────────────────────────────────────
# CVD approximation from OHLCV
# ─────────────────────────────────────────────────────────────────────────────

def compute_cvd_approx(bars: List[Dict]) -> List[float]:
    """
    Approximate CVD from OHLCV candles.

    CVD proxy = buy_vol - sell_vol where:
    buy_vol  = volume × (close - low) / (high - low)  [bullish fraction]
    sell_vol = volume × (high - close) / (high - low) [bearish fraction]

    Net = (2 × (close - low) / (high - low) - 1) × volume
    """
    cvd = [0.0]
    for bar in bars:
        rng = bar["high"] - bar["low"]
        if rng == 0:
            delta = 0.0
        else:
            buy_frac = (bar["close"] - bar["low"]) / rng
            delta = (2 * buy_frac - 1) * bar["volume"]
        cvd.append(cvd[-1] + delta)
    return cvd[1:]


def classify_cvd_cross(
    prices: List[float],
    cvd: List[float],
    i: int,
    window: int = 5,
) -> str:
    """
    CVD-Price cross classification at position i.
    Matches calibrate_anticipatory.py signal definitions.
    """
    if i < window:
        return "NONE"

    price_chg = (prices[i] - prices[i - window]) / prices[i - window] if prices[i - window] > 0 else 0
    cvd_5 = sum(cvd[i - window + 1 : i + 1])
    threshold = 0.003  # 0.3%

    if abs(price_chg) < threshold:
        # Price flat
        if cvd_5 > 0:
            return "ABSORPTION_BUY"
        elif cvd_5 < 0:
            return "ABSORPTION_SELL"
        return "NONE"

    price_up = price_chg > threshold
    cvd_positive = cvd_5 > 0

    if not price_up and cvd_positive:
        return "ACCUMULATION"   # Price falling, CVD positive → smart money buying
    elif price_up and not cvd_positive:
        return "DISTRIBUTION"   # Price rising, CVD negative → rally on weak buying
    elif not price_up and not cvd_positive:
        return "CONFIRMED_SELL"
    return "NONE"


# ─────────────────────────────────────────────────────────────────────────────
# Map 4H funding rate (FR is 8-hourly, so ~2 FR periods per day)
# ─────────────────────────────────────────────────────────────────────────────

def find_nearest_fr(
    bar_ts: int,
    fr_map: dict,
    max_gap_sec: int = 8 * 3600,
) -> float:
    """Find the closest funding rate timestamp to bar_ts."""
    if not fr_map:
        return 0.0001  # default 0.01%
    best_ts = min(fr_map.keys(), key=lambda t: abs(t - bar_ts))
    if abs(best_ts - bar_ts) <= max_gap_sec:
        return fr_map[best_ts]
    return 0.0001


# ─────────────────────────────────────────────────────────────────────────────
# Main snapshot generation
# ─────────────────────────────────────────────────────────────────────────────

def generate_snapshots(
    bars_4h: List[Dict],
    bars_1d: List[Dict],
    fr_map: dict,
    start_idx: int = 0,
) -> List[Dict]:
    """
    Generate feature snapshots for each 4H bar from start_idx onwards.

    Returns list of snapshot dicts, each matching the format:
    {
        "features": { "price": ..., "extension_ratio_1d": ..., ... },
        "scores": {},
        "timestamp": "2025-03-01T12:00:00",
    }
    """
    closes_4h = [b["close"] for b in bars_4h]
    volumes_4h = [b["volume"] for b in bars_4h]
    closes_1d = [b["close"] for b in bars_1d]

    # 4H indicators
    atr_4h = compute_atr(bars_4h, 14)
    rsi_4h = compute_rsi(closes_4h, 14)
    macd_line_4h, _, macd_hist_4h = compute_macd(closes_4h)
    sma50_4h = compute_sma(closes_4h, 50)
    obv_4h_raw = compute_obv(closes_4h, volumes_4h)
    obv_4h = ema_smooth(obv_4h_raw, 20)
    cvd_4h = compute_cvd_approx(bars_4h)

    # 1D indicators
    atr_1d = compute_atr(bars_1d, 14)
    sma200_1d = compute_sma(closes_1d, 200)
    rsi_1d = compute_rsi(closes_1d, 14)

    # Build a timestamp → 1D index map for fast lookup
    ts_to_1d = {}
    for j, b in enumerate(bars_1d):
        # Map seconds-of-day to date
        day_key = b["open_time"] // 86400
        ts_to_1d[day_key] = j

    snapshots = []
    for i in range(start_idx, len(bars_4h)):
        bar = bars_4h[i]
        price = bar["close"]
        bar_ts = bar["open_time"]

        # ── 4H extension ratio ──
        ext_ratio_4h_val = 0.0
        ext_regime_4h_val = "NORMAL"
        if sma50_4h[i] is not None and atr_4h[i] > 0:
            ext_ratio_4h_val = (price - sma50_4h[i]) / atr_4h[i]
            ext_regime_4h_val = classify_extension(ext_ratio_4h_val)

        # ── 1D extension ratio ──
        ext_ratio_1d_val = 0.0
        ext_regime_1d_val = "NORMAL"
        rsi_1d_val = 50.0
        day_key = bar_ts // 86400
        j_1d = ts_to_1d.get(day_key)
        if j_1d is None:
            # Try adjacent days
            for offset in range(1, 3):
                j_1d = ts_to_1d.get(day_key - offset)
                if j_1d is not None:
                    break
        if j_1d is not None and sma200_1d[j_1d] is not None and atr_1d[j_1d] > 0:
            ext_ratio_1d_val = (price - sma200_1d[j_1d]) / atr_1d[j_1d]
            ext_regime_1d_val = classify_extension(ext_ratio_1d_val)
            rsi_1d_val = rsi_1d[j_1d]

        # ── 4H divergences ──
        rsi_div_4h = detect_divergence(closes_4h, rsi_4h, i)
        macd_div_4h = detect_divergence(closes_4h, macd_line_4h, i)
        obv_div_4h = detect_divergence(closes_4h, obv_4h, i)

        # ── CVD cross classification ──
        cvd_cross_4h = classify_cvd_cross(closes_4h, cvd_4h, i)

        # ── Funding rate ──
        fr = find_nearest_fr(bar_ts, fr_map)

        features = {
            "price": price,
            # 1D extension
            "extension_ratio_1d": round(ext_ratio_1d_val, 4),
            "extension_regime_1d": ext_regime_1d_val,
            # 4H extension
            "extension_ratio_4h": round(ext_ratio_4h_val, 4),
            "extension_regime_4h": ext_regime_4h_val,
            # 30M extension (use 4H as proxy — no 30M data in cold-start)
            "extension_ratio_30m": round(ext_ratio_4h_val * 0.6, 4),
            "extension_regime_30m": "NORMAL",
            # 4H divergences
            "rsi_divergence_4h": rsi_div_4h,
            "macd_divergence_4h": macd_div_4h,
            "obv_divergence_4h": obv_div_4h,
            # 30M divergences (not available in cold-start, default NONE)
            "rsi_divergence_30m": "NONE",
            "macd_divergence_30m": "NONE",
            "obv_divergence_30m": "NONE",
            # CVD
            "cvd_price_cross_4h": cvd_cross_4h,
            "cvd_price_cross_30m": "NONE",
            # Funding rate
            "funding_rate_pct": round(fr * 100, 5),  # convert to pct
            # RSI values
            "rsi_4h": round(rsi_4h[i], 2),
            "rsi_1d": round(rsi_1d_val, 2),
            # ATR
            "atr_4h": round(atr_4h[i], 2),
            # S/R proximity (unavailable in cold-start, use safe defaults)
            "nearest_support_dist_atr": 99.0,
            "nearest_resist_dist_atr": 99.0,
            "nearest_support_strength": "NONE",
            "nearest_resist_strength": "NONE",
            # Volatility regime (approximation)
            "volatility_regime_4h": "NORMAL",
            "volatility_regime_30m": "NORMAL",
            "volatility_regime_1d": "NORMAL",
            # OI / liquidation (unavailable in cold-start)
            "oi_change_pct": None,
            "liq_buy_usd": None,
            "liq_sell_usd": None,
            # Misc
            "fr_consecutive_blocks": 0,
            "_avail_sr_zones": False,
            "_avail_order_flow": True,
            "_avail_derivatives": False,
            "_avail_sentiment": False,
            "_source": "cold_start_historical",
        }

        dt = datetime.fromtimestamp(bar_ts, tz=timezone.utc)
        snapshots.append({
            "timestamp": dt.strftime("%Y%m%d_%H%M%S"),
            "features": features,
            "scores": {},
        })

    logger.info(f"Generated {len(snapshots)} snapshots")
    return snapshots


def save_snapshots(
    snapshots: List[Dict],
    output_dir: Path,
    clear: bool = False,
) -> int:
    """Save snapshots to output_dir as individual JSON files."""
    output_dir.mkdir(parents=True, exist_ok=True)

    if clear:
        existing = list(output_dir.glob("*.json"))
        for f in existing:
            f.unlink()
        logger.info(f"Cleared {len(existing)} existing snapshots")

    saved = 0
    for snap in snapshots:
        ts = snap["timestamp"]
        out_file = output_dir / f"snapshot_{ts}.json"
        with open(out_file, "w") as f:
            json.dump(snap, f, indent=2)
        saved += 1

    logger.info(f"Saved {saved} snapshots to {output_dir}")
    return saved


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate cold-start feature snapshots from Binance historical klines"
    )
    parser.add_argument(
        "--days", type=int, default=90,
        help="Number of days of history to generate (default: 90)"
    )
    parser.add_argument(
        "--clear", action="store_true",
        help="Clear existing snapshots before generating (default: False)"
    )
    parser.add_argument(
        "--symbol", default="BTCUSDT",
        help="Trading symbol (default: BTCUSDT)"
    )
    args = parser.parse_args()

    logger.info(f"Generating cold-start snapshots: {args.days} days of {args.symbol}")

    # 4H bars needed: days × 6 bars/day + 50 (SMA50 warmup) + 14 (ATR warmup)
    limit_4h = min(1500, args.days * 6 + 100)
    # 1D bars needed: 200 (SMA200 warmup) + days + 5 buffer
    limit_1d = min(1500, 200 + args.days + 10)

    logger.info(f"Fetching {limit_4h} 4H bars and {limit_1d} 1D bars...")
    bars_4h = fetch_klines(args.symbol, "4h", limit=limit_4h)
    bars_1d = fetch_klines(args.symbol, "1d", limit=limit_1d)

    logger.info("Fetching funding rates...")
    fr_map = fetch_funding_rates(args.symbol, limit=1000)

    # Only generate snapshots for the requested number of days
    # (skip warmup portion at the start)
    target_bars = args.days * 6
    start_idx = max(0, len(bars_4h) - target_bars)
    # Also ensure indicators are warmed up (need at least 50 bars before start_idx)
    start_idx = max(50, start_idx)

    logger.info(f"Generating snapshots from bar {start_idx}/{len(bars_4h)}...")
    snapshots = generate_snapshots(bars_4h, bars_1d, fr_map, start_idx=start_idx)

    saved = save_snapshots(snapshots, SNAPSHOT_DIR, clear=args.clear)

    # Summary
    logger.info("")
    logger.info("=" * 60)
    logger.info(f"✅ Cold-start complete: {saved} snapshots saved to {SNAPSHOT_DIR}")
    logger.info(f"   Next step: python3 scripts/calibrate_anticipatory.py")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
