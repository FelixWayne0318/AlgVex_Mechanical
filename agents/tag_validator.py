"""
Deterministic tag-data validation for the structured debate pipeline.

Solves the core problem: LLM can use ANY REASON_TAG regardless of whether
the feature data supports it. This module pre-computes which tags are
factually valid given the current feature dict, eliminating hallucination.

Flow:
  features → compute_valid_tags() → valid_tags set
  → AVAILABLE TAGS in prompt = sorted(valid_tags)
  → Post-LLM: strip any tag not in valid_tags

v28.0 — Deterministic tag pre-computation layer.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

from agents.prompt_constants import REASON_TAGS

logger = logging.getLogger(__name__)


# ── Tags that are always valid (judgment/memory-based, not data-driven) ──
_ALWAYS_VALID: Set[str] = {
    "LATE_ENTRY",
    "EARLY_ENTRY",
    "TREND_ALIGNED",            # Direction alignment is a judgment call
    "COUNTER_TREND_WIN",
    "COUNTER_TREND_LOSS",
    "SL_TOO_TIGHT",
    "SL_TOO_WIDE",
    "TP_TOO_GREEDY",
    "WRONG_DIRECTION",
    "CORRECT_THESIS",
    "OVEREXTENDED_ENTRY",       # Overlaps EXTENSION_* but is a judgment tag
    "LOW_VOLUME_ENTRY",         # Judgment about entry quality
    # v36.0: BB_SQUEEZE/BB_EXPANSION moved to compute_valid_tags() — data-driven via bb_width_trend
}


def _g(features: Dict[str, Any], key: str, default: Any = None) -> Any:
    """Safe feature getter with default."""
    return features.get(key, default)


def compute_valid_tags(features: Dict[str, Any]) -> Set[str]:
    """
    Compute the set of REASON_TAGS that are factually supported by feature data.

    Each tag has a deterministic rule. If the feature data does not support
    the tag's semantic meaning, it is excluded. This prevents LLM hallucination
    (e.g., using TREND_1D_BEARISH when adx_direction_1d is BULLISH).

    Args:
        features: The feature dictionary passed to AI agents.

    Returns:
        Set of valid tag strings (subset of REASON_TAGS).
    """
    valid: Set[str] = set(_ALWAYS_VALID)

    # ── Shorthand feature extraction ──
    adx_1d = _g(features, "adx_1d", 0.0)
    direction_1d = _g(features, "adx_direction_1d", "")
    di_plus_1d = _g(features, "di_plus_1d", 0.0)
    di_minus_1d = _g(features, "di_minus_1d", 0.0)
    adx_1d_trend = _g(features, "adx_1d_trend_5bar", "")

    adx_4h = _g(features, "adx_4h", 0.0)
    di_plus_4h = _g(features, "di_plus_4h", 0.0)
    di_minus_4h = _g(features, "di_minus_4h", 0.0)

    adx_30m = _g(features, "adx_30m", 0.0)
    di_plus_30m = _g(features, "di_plus_30m", 0.0)
    di_minus_30m = _g(features, "di_minus_30m", 0.0)

    rsi_30m = _g(features, "rsi_30m", 50.0)
    rsi_4h = _g(features, "rsi_4h", 50.0)

    macd_30m = _g(features, "macd_30m", 0.0)
    macd_signal_30m = _g(features, "macd_signal_30m", 0.0)
    macd_hist_30m = _g(features, "macd_histogram_30m", 0.0)
    macd_4h = _g(features, "macd_4h", 0.0)
    macd_signal_4h = _g(features, "macd_signal_4h", 0.0)
    macd_hist_4h = _g(features, "macd_histogram_4h", 0.0)
    macd_hist_4h_trend = _g(features, "macd_histogram_4h_trend_5bar", "")

    bb_pos_30m = _g(features, "bb_position_30m", 0.5)
    bb_pos_4h = _g(features, "bb_position_4h", 0.5)

    vol_ratio_30m = _g(features, "volume_ratio_30m", 1.0)
    vol_ratio_4h = _g(features, "volume_ratio_4h", 1.0)

    ext_regime = _g(features, "extension_regime_30m", "NORMAL")
    vol_regime = _g(features, "volatility_regime_30m", "NORMAL")

    # Divergences
    rsi_div_4h = _g(features, "rsi_divergence_4h", "NONE")
    macd_div_4h = _g(features, "macd_divergence_4h", "NONE")
    obv_div_4h = _g(features, "obv_divergence_4h", "NONE")
    rsi_div_30m = _g(features, "rsi_divergence_30m", "NONE")
    macd_div_30m = _g(features, "macd_divergence_30m", "NONE")
    obv_div_30m = _g(features, "obv_divergence_30m", "NONE")

    # Order flow
    cvd_trend_30m = _g(features, "cvd_trend_30m", "NEUTRAL")
    cvd_cumulative = _g(features, "cvd_cumulative_30m", 0.0)
    buy_ratio_30m = _g(features, "buy_ratio_30m", 0.5)
    buy_ratio_4h = _g(features, "buy_ratio_4h", 0.5)
    cvd_cross_30m = _g(features, "cvd_price_cross_30m", "NONE")
    cvd_cross_4h = _g(features, "cvd_price_cross_4h", "NONE")
    cvd_trend_4h = _g(features, "cvd_trend_4h", "NEUTRAL")

    # Orderbook
    obi = _g(features, "obi_weighted", 0.0)
    obi_change = _g(features, "obi_change_pct", 0.0)
    bid_vol = _g(features, "bid_volume_usd", 0.0)
    ask_vol = _g(features, "ask_volume_usd", 0.0)

    # Derivatives
    fr_pct = _g(features, "funding_rate_pct", 0.0)
    fr_trend = _g(features, "funding_rate_trend", "STABLE")
    premium_idx = _g(features, "premium_index", 0.0)
    oi_trend = _g(features, "oi_trend", "STABLE")
    liq_bias = _g(features, "liquidation_bias", "NONE")
    top_long = _g(features, "top_traders_long_ratio", 0.0)
    taker_buy = _g(features, "taker_buy_ratio", 0.5)

    # SMA (30M + 4H)
    # v36.0 FIX: 30M has sma_periods=[5,20], not [20,50]. Use SMA 5/20 crossover.
    sma_5_30m = _g(features, "sma_5_30m", 0.0)
    sma_20_30m = _g(features, "sma_20_30m", 0.0)
    sma_20_4h = _g(features, "sma_20_4h", 0.0)
    sma_50_4h = _g(features, "sma_50_4h", 0.0)

    # EMA (4H)
    ema_12_4h = _g(features, "ema_12_4h", 0.0)
    ema_26_4h = _g(features, "ema_26_4h", 0.0)

    # 1D MACD
    macd_1d = _g(features, "macd_1d", 0.0)
    macd_signal_1d = _g(features, "macd_signal_1d", 0.0)

    # 4H/1D extension and volatility regimes
    ext_regime_4h = _g(features, "extension_regime_4h", "NORMAL")
    ext_regime_1d = _g(features, "extension_regime_1d", "NORMAL")
    vol_regime_4h = _g(features, "volatility_regime_4h", "NORMAL")
    vol_regime_1d = _g(features, "volatility_regime_1d", "NORMAL")

    # S/R
    sup_price = _g(features, "nearest_support_price", 0.0)
    sup_strength = _g(features, "nearest_support_strength", "NONE")
    sup_dist = _g(features, "nearest_support_dist_atr", 0.0)
    res_price = _g(features, "nearest_resist_price", 0.0)
    res_strength = _g(features, "nearest_resist_strength", "NONE")
    res_dist = _g(features, "nearest_resist_dist_atr", 0.0)

    # Sentiment
    long_ratio = _g(features, "long_ratio", 0.5)
    short_ratio = _g(features, "short_ratio", 0.5)

    # Position
    liq_buffer = _g(features, "liquidation_buffer_pct", 100.0)

    # Price change
    price_30m_chg = _g(features, "price_30m_change_5bar_pct", 0.0)

    # ═════════════════════════════════════════════════════════════════
    # TREND (1D)
    # ═════════════════════════════════════════════════════════════════
    if direction_1d == "BULLISH":
        valid.add("TREND_1D_BULLISH")
    elif direction_1d == "BEARISH":
        valid.add("TREND_1D_BEARISH")
    else:
        valid.add("TREND_1D_NEUTRAL")

    if adx_1d >= 40 or adx_4h >= 40 or adx_30m >= 40:
        valid.add("STRONG_TREND_ADX40")
    if 0 < adx_1d < 25:
        valid.add("WEAK_TREND_ADX_LOW")
    if adx_1d_trend == "FALLING":
        valid.add("TREND_EXHAUSTION")

    # DI crosses — check across timeframes
    if di_plus_1d > di_minus_1d or di_plus_4h > di_minus_4h:
        valid.add("DI_BULLISH_CROSS")
    if di_minus_1d > di_plus_1d or di_minus_4h > di_plus_4h or di_minus_30m > di_plus_30m:
        valid.add("DI_BEARISH_CROSS")

    # ═════════════════════════════════════════════════════════════════
    # MOMENTUM (4H / 30M)
    # ═════════════════════════════════════════════════════════════════
    # 4H general momentum direction — requires ≥2 of 3 indicators to agree
    _4h_bull_signals = (
        (1 if macd_4h > macd_signal_4h else 0)
        + (1 if di_plus_4h > di_minus_4h else 0)
        + (1 if rsi_4h > 50 else 0)
    )
    _4h_bear_signals = (
        (1 if macd_4h < macd_signal_4h else 0)
        + (1 if di_minus_4h > di_plus_4h else 0)
        + (1 if rsi_4h < 50 else 0)
    )
    if _4h_bull_signals >= 2:
        valid.add("MOMENTUM_4H_BULLISH")
    if _4h_bear_signals >= 2:
        valid.add("MOMENTUM_4H_BEARISH")

    if rsi_30m > 70 or rsi_4h > 70:
        valid.add("RSI_OVERBOUGHT")
    if rsi_30m < 30 or rsi_4h < 30:
        valid.add("RSI_OVERSOLD")
    # Cardwell RSI Range Theory: uptrend RSI oscillates 40-80, downtrend 20-60.
    # Overlap at 40-60 is the transition zone — use 4H DI direction to disambiguate.
    if rsi_4h > 60:
        valid.add("RSI_CARDWELL_BULL")       # Unambiguously in bull range
    elif rsi_4h < 40:
        valid.add("RSI_CARDWELL_BEAR")       # Unambiguously in bear range
    elif 40 <= rsi_4h <= 60:
        # Transition zone: DI direction breaks the tie
        if di_plus_4h >= di_minus_4h:
            valid.add("RSI_CARDWELL_BULL")
        else:
            valid.add("RSI_CARDWELL_BEAR")

    if macd_30m > macd_signal_30m or macd_4h > macd_signal_4h:
        valid.add("MACD_BULLISH_CROSS")
    if macd_30m < macd_signal_30m or macd_4h < macd_signal_4h:
        valid.add("MACD_BEARISH_CROSS")

    # Histogram direction — use trend if available, else compare magnitude
    if macd_hist_4h_trend in ("RISING", "EXPANDING"):
        valid.add("MACD_HISTOGRAM_EXPANDING")
    if macd_hist_4h_trend in ("FALLING", "CONTRACTING", "FLAT"):
        valid.add("MACD_HISTOGRAM_CONTRACTING")
    # If no trend data, use absolute magnitude heuristic
    if not macd_hist_4h_trend:
        if abs(macd_hist_4h) > abs(macd_hist_30m) * 0.5:
            valid.add("MACD_HISTOGRAM_EXPANDING")
            valid.add("MACD_HISTOGRAM_CONTRACTING")

    # Volume ratio
    if vol_ratio_30m > 1.5 or vol_ratio_4h > 1.5:
        valid.add("VOLUME_SURGE")
    if vol_ratio_30m < 0.5 or vol_ratio_4h < 0.5:
        valid.add("VOLUME_DRY")

    # SMA cross (30M)
    # v36.0 FIX: 30M uses SMA 5/20 crossover (execution layer has sma_periods=[5,20])
    if sma_5_30m > 0 and sma_20_30m > 0:
        if sma_5_30m > sma_20_30m:
            valid.add("SMA_BULLISH_CROSS_30M")
        elif sma_5_30m < sma_20_30m:
            valid.add("SMA_BEARISH_CROSS_30M")

    # v29.2: SMA cross (4H)
    if sma_20_4h > 0 and sma_50_4h > 0:
        if sma_20_4h > sma_50_4h:
            valid.add("SMA_BULLISH_CROSS_4H")
        elif sma_20_4h < sma_50_4h:
            valid.add("SMA_BEARISH_CROSS_4H")

    # v29.2: EMA cross (4H)
    if ema_12_4h > 0 and ema_26_4h > 0:
        if ema_12_4h > ema_26_4h:
            valid.add("EMA_BULLISH_CROSS_4H")
        elif ema_12_4h < ema_26_4h:
            valid.add("EMA_BEARISH_CROSS_4H")

    # v29.2: 1D MACD direction
    if macd_1d > macd_signal_1d:
        valid.add("MACD_1D_BULLISH")
    elif macd_1d < macd_signal_1d:
        valid.add("MACD_1D_BEARISH")

    # Bollinger Bands
    if bb_pos_30m > 0.8 or bb_pos_4h > 0.8:
        valid.add("BB_UPPER_ZONE")
    if bb_pos_30m < 0.2 or bb_pos_4h < 0.2:
        valid.add("BB_LOWER_ZONE")
    # v36.0: BB_SQUEEZE/BB_EXPANSION — data-driven via bb_width_trend
    bb_w_30m = _g(features, "bb_width_30m_trend_5bar", "FLAT")
    bb_w_4h = _g(features, "bb_width_4h_trend_5bar", "FLAT")
    if bb_w_30m == "FALLING" or bb_w_4h == "FALLING":
        valid.add("BB_SQUEEZE")
    if bb_w_30m == "RISING" or bb_w_4h == "RISING":
        valid.add("BB_EXPANSION")

    # ═════════════════════════════════════════════════════════════════
    # DIVERGENCES
    # ═════════════════════════════════════════════════════════════════
    _div_map = {
        "RSI_BULLISH_DIV_4H": (rsi_div_4h, "BULLISH"),
        "RSI_BEARISH_DIV_4H": (rsi_div_4h, "BEARISH"),
        "MACD_BULLISH_DIV_4H": (macd_div_4h, "BULLISH"),
        "MACD_BEARISH_DIV_4H": (macd_div_4h, "BEARISH"),
        "OBV_BULLISH_DIV_4H": (obv_div_4h, "BULLISH"),
        "OBV_BEARISH_DIV_4H": (obv_div_4h, "BEARISH"),
        "RSI_BULLISH_DIV_30M": (rsi_div_30m, "BULLISH"),
        "RSI_BEARISH_DIV_30M": (rsi_div_30m, "BEARISH"),
        "MACD_BULLISH_DIV_30M": (macd_div_30m, "BULLISH"),
        "MACD_BEARISH_DIV_30M": (macd_div_30m, "BEARISH"),
        "OBV_BULLISH_DIV_30M": (obv_div_30m, "BULLISH"),
        "OBV_BEARISH_DIV_30M": (obv_div_30m, "BEARISH"),
    }
    any_divergence = False
    for tag, (feat_val, expected) in _div_map.items():
        if feat_val == expected:
            valid.add(tag)
            any_divergence = True
    if any_divergence:
        valid.add("DIVERGENCE_CONFIRMED")

    # ═════════════════════════════════════════════════════════════════
    # ORDER FLOW (CVD / Buy Ratio)
    # ═════════════════════════════════════════════════════════════════
    if cvd_trend_30m == "POSITIVE" or cvd_trend_4h == "POSITIVE" or cvd_cumulative > 0:
        valid.add("CVD_POSITIVE")
    if cvd_trend_30m == "NEGATIVE" or cvd_trend_4h == "NEGATIVE" or cvd_cumulative < 0:
        valid.add("CVD_NEGATIVE")

    for cross in (cvd_cross_30m, cvd_cross_4h):
        if cross == "ACCUMULATION":
            valid.add("CVD_ACCUMULATION")
        elif cross == "DISTRIBUTION":
            valid.add("CVD_DISTRIBUTION")
        elif cross == "ABSORPTION_BUY":
            valid.add("CVD_ABSORPTION_BUY")
        elif cross == "ABSORPTION_SELL":
            valid.add("CVD_ABSORPTION_SELL")

    if buy_ratio_30m > 0.54 or buy_ratio_4h > 0.54:
        valid.add("BUY_RATIO_HIGH")
    if buy_ratio_30m < 0.46 or buy_ratio_4h < 0.46:
        valid.add("BUY_RATIO_LOW")

    # ═════════════════════════════════════════════════════════════════
    # ORDERBOOK (OBI)
    # ═════════════════════════════════════════════════════════════════
    if obi > 0.2:
        valid.add("OBI_BUY_PRESSURE")
    elif obi < -0.2:
        valid.add("OBI_SELL_PRESSURE")
    else:
        valid.add("OBI_BALANCED")          # OBI neutral — no directional bias
    # OBI dynamic shift
    if obi_change > 20:
        valid.add("OBI_SHIFTING_BULLISH")
    elif obi_change < -20:
        valid.add("OBI_SHIFTING_BEARISH")
    # Thin liquidity: either side < $500K
    if bid_vol > 0 and ask_vol > 0:
        if min(bid_vol, ask_vol) < 500_000:
            valid.add("LIQUIDITY_THIN")
        if abs(bid_vol - ask_vol) / max(bid_vol, ask_vol) > 0.7:
            valid.add("SLIPPAGE_HIGH")

    # ═════════════════════════════════════════════════════════════════
    # DERIVATIVES (Funding Rate / OI / Liquidations / Top Traders)
    # ═════════════════════════════════════════════════════════════════
    if abs(fr_pct) <= 0.001:
        valid.add("FR_IGNORED")            # FR neutral — no cost/benefit
    if fr_pct < -0.001:
        valid.add("FR_FAVORABLE_LONG")     # Shorts pay longs
    if fr_pct > 0.001:
        valid.add("FR_FAVORABLE_SHORT")    # Longs pay shorts
    if fr_pct > 0.01:
        valid.add("FR_ADVERSE_LONG")
    if fr_pct < -0.01:
        valid.add("FR_ADVERSE_SHORT")
    if abs(fr_pct) > 0.05:
        valid.add("FR_EXTREME")
    if fr_trend == "RISING":
        valid.add("FR_TREND_RISING")
    elif fr_trend == "FALLING":
        valid.add("FR_TREND_FALLING")

    # Premium index — futures premium/discount
    if premium_idx > 0.0005:
        valid.add("PREMIUM_POSITIVE")
    elif premium_idx < -0.0005:
        valid.add("PREMIUM_NEGATIVE")

    # OI trend + price direction → opening/closing
    if oi_trend == "RISING":
        if price_30m_chg > 0:
            valid.add("OI_LONG_OPENING")
        elif price_30m_chg < 0:
            valid.add("OI_SHORT_OPENING")
    elif oi_trend == "FALLING":
        if price_30m_chg > 0:
            valid.add("OI_SHORT_CLOSING")
        elif price_30m_chg < 0:
            valid.add("OI_LONG_CLOSING")

    if liq_bias in ("LONG", "LONG_DOMINANT"):
        valid.add("LIQUIDATION_CASCADE_LONG")
    elif liq_bias in ("SHORT", "SHORT_DOMINANT"):
        valid.add("LIQUIDATION_CASCADE_SHORT")

    # Top traders — only valid if data exists (ratio > 0)
    if top_long > 0.01:
        if top_long > 0.55:
            valid.add("TOP_TRADERS_LONG_BIAS")
        if top_long < 0.45:
            valid.add("TOP_TRADERS_SHORT_BIAS")

    # Taker buy ratio — only valid if data exists
    if taker_buy > 0.01:
        if taker_buy > 0.55:
            valid.add("TAKER_BUY_DOMINANT")
        if taker_buy < 0.45:
            valid.add("TAKER_SELL_DOMINANT")

    # ═════════════════════════════════════════════════════════════════
    # S/R ZONES
    # ═════════════════════════════════════════════════════════════════
    has_support = sup_price > 0 and sup_strength != "NONE"
    has_resist = res_price > 0 and res_strength != "NONE"

    if has_support and sup_dist < 3:
        valid.add("NEAR_STRONG_SUPPORT")
    if has_resist and res_dist < 3:
        valid.add("NEAR_STRONG_RESISTANCE")

    if has_support or has_resist:
        # Breakout potential if price is very close to S/R (<1 ATR)
        if (has_support and sup_dist < 1) or (has_resist and res_dist < 1):
            valid.add("SR_BREAKOUT_POTENTIAL")
            valid.add("SR_REJECTION")
        valid.add("SR_TRAPPED")

    if not has_support and not has_resist:
        valid.add("SR_CLEAR_SPACE")
    elif (has_support and sup_dist > 3) and (has_resist and res_dist > 3):
        valid.add("SR_CLEAR_SPACE")

    # ═════════════════════════════════════════════════════════════════
    # RISK SIGNALS (Extension / Volatility)
    # ═════════════════════════════════════════════════════════════════
    # 30M extension/volatility
    # EXTENSION_NORMAL: fires when extension regime exists and is not extreme
    # "没有信号也是信号" — checking extension and finding it normal IS a valid data reference
    if ext_regime in ("NORMAL", "EXTENDED"):
        valid.add("EXTENSION_NORMAL")
    if ext_regime == "OVEREXTENDED":
        valid.add("EXTENSION_OVEREXTENDED")
    elif ext_regime == "EXTREME":
        valid.add("EXTENSION_EXTREME")

    if vol_regime == "EXTREME":
        valid.add("VOL_EXTREME")
    if vol_regime in ("HIGH", "EXTREME"):
        valid.add("VOL_HIGH")
    if vol_regime == "LOW":
        valid.add("VOL_LOW")

    # v29.2: 4H extension/volatility
    if ext_regime_4h == "OVEREXTENDED":
        valid.add("EXTENSION_4H_OVEREXTENDED")
    elif ext_regime_4h == "EXTREME":
        valid.add("EXTENSION_4H_EXTREME")

    if vol_regime_4h == "EXTREME":
        valid.add("VOL_4H_EXTREME")
    if vol_regime_4h in ("HIGH", "EXTREME"):
        valid.add("VOL_4H_HIGH")
    if vol_regime_4h == "LOW":
        valid.add("VOL_4H_LOW")

    # v29.2: 1D extension/volatility
    if ext_regime_1d == "OVEREXTENDED":
        valid.add("EXTENSION_1D_OVEREXTENDED")
    elif ext_regime_1d == "EXTREME":
        valid.add("EXTENSION_1D_EXTREME")

    if vol_regime_1d == "EXTREME":
        valid.add("VOL_1D_EXTREME")
    if vol_regime_1d in ("HIGH", "EXTREME"):
        valid.add("VOL_1D_HIGH")
    if vol_regime_1d == "LOW":
        valid.add("VOL_1D_LOW")

    # Liquidation buffer — only relevant when in a position
    if 0 < liq_buffer < 10:
        valid.add("LIQUIDATION_BUFFER_LOW")
    if 0 < liq_buffer < 5:
        valid.add("LIQUIDATION_BUFFER_CRITICAL")

    # ═════════════════════════════════════════════════════════════════
    # SENTIMENT
    # ═════════════════════════════════════════════════════════════════
    if long_ratio > 0.60:
        valid.add("SENTIMENT_CROWDED_LONG")
    if short_ratio > 0.60:
        valid.add("SENTIMENT_CROWDED_SHORT")
    if long_ratio > 0.70 or short_ratio > 0.70:
        valid.add("SENTIMENT_EXTREME")
    if long_ratio <= 0.60 and short_ratio <= 0.60:
        valid.add("SENTIMENT_NEUTRAL")    # Balanced — no crowding signal

    # ── Fear & Greed (v44.0) ──
    # ═════════════════════════════════════════════════════════════════
    fg_index = int(_g(features, "fear_greed_index", 50))
    if fg_index < 20:
        valid.add("EXTREME_FEAR")          # Contrarian bullish
    if fg_index > 80:
        valid.add("EXTREME_GREED")         # Contrarian bearish

    # Only return tags that are in REASON_TAGS (safety)
    return valid & REASON_TAGS


def compute_annotated_tags(features: Dict[str, Any], valid_tags: Set[str]) -> str:
    """
    Generate annotated tag list with semantic context and reliability notes.

    Instead of a flat list like "RSI_OVERBOUGHT, MACD_BULLISH_CROSS",
    produces contextual annotations:
      RSI_OVERBOUGHT (RSI_30m=72, 4H=65; ⚠️ ADX>40: Cardwell range 40-80 applies)
      MACD_BULLISH_CROSS (4H MACD>Signal; ⚠️ ADX<25: 74-97% false positive rate)

    v28.0: Addresses the gap where AI sees tag names without understanding
    their reliability or regime-dependent interpretation.
    """
    adx_1d = _g(features, "adx_1d", 0.0)
    market_regime = _g(features, "market_regime", "WEAK_TREND")
    ext_regime = _g(features, "extension_regime_30m", "NORMAL")
    vol_regime = _g(features, "volatility_regime_30m", "NORMAL")

    is_strong_trend = adx_1d >= 40
    is_ranging = adx_1d < 25

    # Tag annotation rules — only for tags in valid_tags
    annotations: Dict[str, str] = {}

    # ── Trend (1D) tags ── v36.0
    di_plus_1d = _g(features, "di_plus_1d", 0.0)
    di_minus_1d = _g(features, "di_minus_1d", 0.0)
    if "TREND_1D_BULLISH" in valid_tags:
        annotations["TREND_1D_BULLISH"] = f"1D ADX={adx_1d:.1f}, DI+ {di_plus_1d:.1f} > DI- {di_minus_1d:.1f}"
    if "TREND_1D_BEARISH" in valid_tags:
        annotations["TREND_1D_BEARISH"] = f"1D ADX={adx_1d:.1f}, DI- {di_minus_1d:.1f} > DI+ {di_plus_1d:.1f}"
    if "TREND_1D_NEUTRAL" in valid_tags:
        annotations["TREND_1D_NEUTRAL"] = f"1D ADX={adx_1d:.1f} — no clear directional bias"
    if "STRONG_TREND_ADX40" in valid_tags:
        _strong_tfs = []
        if adx_1d >= 40:
            _strong_tfs.append(f"1D={adx_1d:.1f}")
        _adx_4h = _g(features, "adx_4h", 0.0)
        if _adx_4h >= 40:
            _strong_tfs.append(f"4H={_adx_4h:.1f}")
        _adx_30m = _g(features, "adx_30m", 0.0)
        if _adx_30m >= 40:
            _strong_tfs.append(f"30M={_adx_30m:.1f}")
        annotations["STRONG_TREND_ADX40"] = f"ADX>40: {', '.join(_strong_tfs)} — trend-following preferred"
    if "WEAK_TREND_ADX_LOW" in valid_tags:
        annotations["WEAK_TREND_ADX_LOW"] = f"1D ADX={adx_1d:.1f} <25 — RANGING, MACD/SMA cross reliability LOW"
    if "TREND_EXHAUSTION" in valid_tags:
        annotations["TREND_EXHAUSTION"] = f"1D ADX 5-bar trend FALLING from {adx_1d:.1f} — trend momentum weakening"
    if "DI_BULLISH_CROSS" in valid_tags:
        _parts = []
        if di_plus_1d > di_minus_1d:
            _parts.append(f"1D DI+{di_plus_1d:.1f}>DI-{di_minus_1d:.1f}")
        _dip4 = _g(features, "di_plus_4h", 0.0)
        _dim4 = _g(features, "di_minus_4h", 0.0)
        if _dip4 > _dim4:
            _parts.append(f"4H DI+{_dip4:.1f}>DI-{_dim4:.1f}")
        annotations["DI_BULLISH_CROSS"] = "; ".join(_parts) if _parts else "DI+ > DI-"
    if "DI_BEARISH_CROSS" in valid_tags:
        _parts = []
        if di_minus_1d > di_plus_1d:
            _parts.append(f"1D DI-{di_minus_1d:.1f}>DI+{di_plus_1d:.1f}")
        _dip4 = _g(features, "di_plus_4h", 0.0)
        _dim4 = _g(features, "di_minus_4h", 0.0)
        if _dim4 > _dip4:
            _parts.append(f"4H DI-{_dim4:.1f}>DI+{_dip4:.1f}")
        _dip30 = _g(features, "di_plus_30m", 0.0)
        _dim30 = _g(features, "di_minus_30m", 0.0)
        if _dim30 > _dip30:
            _parts.append(f"30M DI-{_dim30:.1f}>DI+{_dip30:.1f}")
        annotations["DI_BEARISH_CROSS"] = "; ".join(_parts) if _parts else "DI- > DI+"

    # RSI tags (need 4H DI for Cardwell transition zone annotation)
    rsi_30m = _g(features, "rsi_30m", 50.0)
    rsi_4h = _g(features, "rsi_4h", 50.0)
    di_plus_4h = _g(features, "di_plus_4h", 0.0)
    di_minus_4h = _g(features, "di_minus_4h", 0.0)
    if "RSI_OVERBOUGHT" in valid_tags:
        ctx = f"RSI 30m={rsi_30m:.0f}, 4H={rsi_4h:.0f}"
        if is_strong_trend:
            ctx += "; ⚠️ ADX>40: Cardwell range 40-80, may not signal reversal"
        annotations["RSI_OVERBOUGHT"] = ctx
    if "RSI_OVERSOLD" in valid_tags:
        ctx = f"RSI 30m={rsi_30m:.0f}, 4H={rsi_4h:.0f}"
        if is_strong_trend:
            ctx += "; ⚠️ ADX>40: Cardwell range 20-60, may not signal reversal"
        annotations["RSI_OVERSOLD"] = ctx
    if "RSI_CARDWELL_BULL" in valid_tags:
        ctx = f"4H RSI={rsi_4h:.0f} in uptrend zone 40-80"
        if 40 <= rsi_4h <= 60:
            ctx += f" (transition zone, DI+={di_plus_4h:.1f}>DI-={di_minus_4h:.1f} → bullish)"
        annotations["RSI_CARDWELL_BULL"] = ctx
    if "RSI_CARDWELL_BEAR" in valid_tags:
        ctx = f"4H RSI={rsi_4h:.0f} in downtrend zone 20-60"
        if 40 <= rsi_4h <= 60:
            ctx += f" (transition zone, DI-={di_minus_4h:.1f}>DI+={di_plus_4h:.1f} → bearish)"
        annotations["RSI_CARDWELL_BEAR"] = ctx

    # 4H Momentum direction tags (di_plus_4h/di_minus_4h already extracted above for Cardwell)
    macd_4h = _g(features, "macd_4h", 0.0)
    macd_signal_4h = _g(features, "macd_signal_4h", 0.0)
    if "MOMENTUM_4H_BULLISH" in valid_tags:
        annotations["MOMENTUM_4H_BULLISH"] = (
            f"4H RSI={rsi_4h:.0f}, MACD {'>' if macd_4h > macd_signal_4h else '<'} Signal, "
            f"DI+ {di_plus_4h:.1f} {'>' if di_plus_4h > di_minus_4h else '<'} DI- {di_minus_4h:.1f}"
        )
    if "MOMENTUM_4H_BEARISH" in valid_tags:
        annotations["MOMENTUM_4H_BEARISH"] = (
            f"4H RSI={rsi_4h:.0f}, MACD {'<' if macd_4h < macd_signal_4h else '>'} Signal, "
            f"DI- {di_minus_4h:.1f} {'>' if di_minus_4h > di_plus_4h else '<'} DI+ {di_plus_4h:.1f}"
        )

    # MACD tags — regime-dependent reliability
    if "MACD_BULLISH_CROSS" in valid_tags:
        ctx = "4H MACD > Signal"
        if is_ranging:
            ctx += "; ⚠️ ADX<25 RANGING: 74-97% false positive, LOW reliability"
        annotations["MACD_BULLISH_CROSS"] = ctx
    if "MACD_BEARISH_CROSS" in valid_tags:
        ctx = "4H MACD < Signal"
        if is_ranging:
            ctx += "; ⚠️ ADX<25 RANGING: 74-97% false positive, LOW reliability"
        annotations["MACD_BEARISH_CROSS"] = ctx

    # MACD histogram — v36.0
    macd_hist_4h = _g(features, "macd_histogram_4h", 0.0)
    if "MACD_HISTOGRAM_EXPANDING" in valid_tags:
        annotations["MACD_HISTOGRAM_EXPANDING"] = f"4H MACD histogram={macd_hist_4h:+.2f}, expanding — momentum accelerating"
    if "MACD_HISTOGRAM_CONTRACTING" in valid_tags:
        annotations["MACD_HISTOGRAM_CONTRACTING"] = f"4H MACD histogram={macd_hist_4h:+.2f}, contracting — momentum fading"

    # BB zones — v36.0
    bb_pos_30m = _g(features, "bb_position_30m", 0.5)
    bb_pos_4h = _g(features, "bb_position_4h", 0.5)
    if "BB_UPPER_ZONE" in valid_tags:
        annotations["BB_UPPER_ZONE"] = f"BB position 30m={bb_pos_30m:.2f}, 4H={bb_pos_4h:.2f} — near upper band, potential overextension"
    if "BB_LOWER_ZONE" in valid_tags:
        annotations["BB_LOWER_ZONE"] = f"BB position 30m={bb_pos_30m:.2f}, 4H={bb_pos_4h:.2f} — near lower band, potential bounce zone"
    # BB squeeze/expansion — v36.0
    bb_w_30m = _g(features, "bb_width_30m_trend_5bar", "FLAT")
    bb_w_4h = _g(features, "bb_width_4h_trend_5bar", "FLAT")
    if "BB_SQUEEZE" in valid_tags:
        _tfs = []
        if bb_w_30m == "FALLING":
            _tfs.append("30M")
        if bb_w_4h == "FALLING":
            _tfs.append("4H")
        annotations["BB_SQUEEZE"] = f"BB width contracting on {'+'.join(_tfs)} — low volatility, breakout imminent"
    if "BB_EXPANSION" in valid_tags:
        _tfs = []
        if bb_w_30m == "RISING":
            _tfs.append("30M")
        if bb_w_4h == "RISING":
            _tfs.append("4H")
        annotations["BB_EXPANSION"] = f"BB width expanding on {'+'.join(_tfs)} — volatility increasing, trend move underway"

    # SMA cross 4H — v36.0
    sma_20_4h = _g(features, "sma_20_4h", 0.0)
    sma_50_4h = _g(features, "sma_50_4h", 0.0)
    if "SMA_BULLISH_CROSS_4H" in valid_tags:
        annotations["SMA_BULLISH_CROSS_4H"] = f"4H SMA20={sma_20_4h:.1f} > SMA50={sma_50_4h:.1f}"
    if "SMA_BEARISH_CROSS_4H" in valid_tags:
        annotations["SMA_BEARISH_CROSS_4H"] = f"4H SMA20={sma_20_4h:.1f} < SMA50={sma_50_4h:.1f}"

    # EMA cross 4H — v36.0
    ema_12_4h = _g(features, "ema_12_4h", 0.0)
    ema_26_4h = _g(features, "ema_26_4h", 0.0)
    if "EMA_BULLISH_CROSS_4H" in valid_tags:
        annotations["EMA_BULLISH_CROSS_4H"] = f"4H EMA12={ema_12_4h:.1f} > EMA26={ema_26_4h:.1f}"
    if "EMA_BEARISH_CROSS_4H" in valid_tags:
        annotations["EMA_BEARISH_CROSS_4H"] = f"4H EMA12={ema_12_4h:.1f} < EMA26={ema_26_4h:.1f}"

    # 1D MACD direction — v36.0
    macd_1d = _g(features, "macd_1d", 0.0)
    macd_signal_1d = _g(features, "macd_signal_1d", 0.0)
    if "MACD_1D_BULLISH" in valid_tags:
        annotations["MACD_1D_BULLISH"] = f"1D MACD={macd_1d:.2f} > Signal={macd_signal_1d:.2f} — macro bullish momentum"
    if "MACD_1D_BEARISH" in valid_tags:
        annotations["MACD_1D_BEARISH"] = f"1D MACD={macd_1d:.2f} < Signal={macd_signal_1d:.2f} — macro bearish momentum"

    # Extension tags — trend-aware (30M)
    if "EXTENSION_OVEREXTENDED" in valid_tags:
        ext_ratio = _g(features, "extension_ratio_30m", 0.0)
        ctx = f"Extension={ext_ratio:+.1f} ATR from SMA"
        if is_strong_trend:
            ctx += "; ℹ️ ADX>40: 3-5 ATR is COMMON in strong trends, not alarming"
        else:
            ctx += "; ⚠️ Mean-reversion pressure building"
        annotations["EXTENSION_OVEREXTENDED"] = ctx
    if "EXTENSION_EXTREME" in valid_tags:
        ext_ratio = _g(features, "extension_ratio_30m", 0.0)
        _dir = "ABOVE SMA (overextended UP → risk to LONG)" if ext_ratio > 0 else "BELOW SMA (overextended DOWN → risk to SHORT)"
        annotations["EXTENSION_EXTREME"] = f"Extension={ext_ratio:+.1f} ATR {_dir} — historically rare, high reversion probability"

    # Extension tags — 4H (v36.0: annotation parity with 30M)
    if "EXTENSION_4H_OVEREXTENDED" in valid_tags:
        ext_ratio_4h = _g(features, "extension_ratio_4h", 0.0)
        adx_4h = _g(features, "adx_4h", 0.0)
        _dir = "above" if ext_ratio_4h > 0 else "below"
        ctx = f"4H Extension={ext_ratio_4h:+.1f} ATR {_dir} SMA"
        if adx_4h >= 40:
            ctx += "; ℹ️ 4H ADX>40: 3-5 ATR common in strong trends"
        else:
            ctx += "; ⚠️ Mean-reversion pressure building"
        annotations["EXTENSION_4H_OVEREXTENDED"] = ctx
    if "EXTENSION_4H_EXTREME" in valid_tags:
        ext_ratio_4h = _g(features, "extension_ratio_4h", 0.0)
        _dir = "ABOVE SMA (overextended UP → risk to LONG)" if ext_ratio_4h > 0 else "BELOW SMA (overextended DOWN → risk to SHORT)"
        annotations["EXTENSION_4H_EXTREME"] = f"4H Extension={ext_ratio_4h:+.1f} ATR {_dir} — historically rare, high reversion probability"

    # Extension tags — 1D (v36.0: annotation parity with 30M)
    if "EXTENSION_1D_OVEREXTENDED" in valid_tags:
        ext_ratio_1d = _g(features, "extension_ratio_1d", 0.0)
        _dir = "above" if ext_ratio_1d > 0 else "below"
        ctx = f"1D Extension={ext_ratio_1d:+.1f} ATR {_dir} SMA_200"
        if is_strong_trend:
            ctx += "; ℹ️ ADX>40: 3-5 ATR common in strong trends"
        else:
            ctx += "; ⚠️ Mean-reversion pressure building"
        annotations["EXTENSION_1D_OVEREXTENDED"] = ctx
    if "EXTENSION_1D_EXTREME" in valid_tags:
        ext_ratio_1d = _g(features, "extension_ratio_1d", 0.0)
        _dir = "ABOVE SMA_200 (overextended UP → risk to LONG)" if ext_ratio_1d > 0 else "BELOW SMA_200 (overextended DOWN → risk to SHORT)"
        annotations["EXTENSION_1D_EXTREME"] = f"1D Extension={ext_ratio_1d:+.1f} ATR {_dir} — historically rare, high reversion probability"

    # S/R tags — trend-aware
    if "NEAR_STRONG_SUPPORT" in valid_tags:
        sup_str = _g(features, 'nearest_support_strength', 'NONE')
        ctx = f"Support dist={_g(features, 'nearest_support_dist_atr', 0):.1f} ATR, strength={sup_str}"
        if is_strong_trend:
            ctx += "; ⚠️ ADX>40: bounce rate only ~25%"
        annotations["NEAR_STRONG_SUPPORT"] = ctx
    if "NEAR_STRONG_RESISTANCE" in valid_tags:
        res_str = _g(features, 'nearest_resist_strength', 'NONE')
        ctx = f"Resistance dist={_g(features, 'nearest_resist_dist_atr', 0):.1f} ATR, strength={res_str}"
        if is_strong_trend:
            ctx += "; ⚠️ ADX>40: bounce rate only ~25%"
        annotations["NEAR_STRONG_RESISTANCE"] = ctx
    # S/R context tags — v36.0 (strength included for AI interpretation)
    sup_dist = _g(features, "nearest_support_dist_atr", 0.0)
    res_dist = _g(features, "nearest_resist_dist_atr", 0.0)
    _sup_str = _g(features, "nearest_support_strength", "NONE")
    _res_str = _g(features, "nearest_resist_strength", "NONE")
    if "SR_BREAKOUT_POTENTIAL" in valid_tags:
        if sup_dist < res_dist:
            _close_to = f"support (strength={_sup_str}, dist={sup_dist:.1f} ATR)"
        else:
            _close_to = f"resistance (strength={_res_str}, dist={res_dist:.1f} ATR)"
        annotations["SR_BREAKOUT_POTENTIAL"] = f"Price within 1 ATR of {_close_to} — breakout or rejection imminent"
    if "SR_REJECTION" in valid_tags:
        annotations["SR_REJECTION"] = f"Price testing S/R level (S dist={sup_dist:.1f} ATR strength={_sup_str}, R dist={res_dist:.1f} ATR strength={_res_str}) — watch for rejection pattern"
    if "SR_TRAPPED" in valid_tags:
        annotations["SR_TRAPPED"] = f"Between S/R zones (S={sup_dist:.1f} ATR strength={_sup_str}, R={res_dist:.1f} ATR strength={_res_str}) — range-bound risk"
    if "SR_CLEAR_SPACE" in valid_tags:
        annotations["SR_CLEAR_SPACE"] = "No nearby S/R levels — price has room to move freely"

    # Divergence tags — v36.0: per-type annotations with reliability context
    div_tags = [t for t in valid_tags if "DIV" in t and t != "DIVERGENCE_CONFIRMED"]
    for dt in div_tags:
        _tf = "4H" if "4H" in dt else "30M"
        _dir = "BULLISH" if "BULLISH" in dt else "BEARISH"
        if "OBV" in dt:
            annotations[dt] = f"{_tf} OBV {_dir.lower()} divergence; ⚠️ alone: 40-60% false positive, confirm with RSI/CVD"
        elif "MACD" in dt:
            ctx = f"{_tf} MACD {_dir.lower()} divergence"
            if is_ranging:
                ctx += "; ⚠️ ADX<25 RANGING: low reliability"
            else:
                ctx += "; moderate reliability, best with RSI/OBV confluence"
            annotations[dt] = ctx
        elif "RSI" in dt:
            ctx = f"{_tf} RSI {_dir.lower()} divergence (RSI {_tf.lower()}={_g(features, f'rsi_{_tf.lower()}', 50.0):.0f})"
            if is_ranging:
                ctx += "; ⚠️ RSI divergence in ranging market: higher false positive"
            else:
                ctx += "; high reliability in trending market"
            annotations[dt] = ctx
    if "DIVERGENCE_CONFIRMED" in valid_tags:
        _active = [t for t in valid_tags if "DIV" in t and t != "DIVERGENCE_CONFIRMED"]
        annotations["DIVERGENCE_CONFIRMED"] = f"{len(_active)} divergence(s) detected — confluence strengthens signal"

    # Volume ratio
    vr_30m = _g(features, "volume_ratio_30m", 1.0)
    vr_4h = _g(features, "volume_ratio_4h", 1.0)
    if "VOLUME_SURGE" in valid_tags:
        annotations["VOLUME_SURGE"] = f"VolRatio 30m={vr_30m:.2f}, 4H={vr_4h:.2f} — high participation confirms move"
    if "VOLUME_DRY" in valid_tags:
        annotations["VOLUME_DRY"] = f"VolRatio 30m={vr_30m:.2f}, 4H={vr_4h:.2f} — low volume, move may lack conviction"

    # SMA cross — v36.0 FIX: 30M uses SMA 5/20 (execution layer has sma_periods=[5,20])
    sma5_30m = _g(features, "sma_5_30m", 0.0)
    sma20_30m = _g(features, "sma_20_30m", 0.0)
    if "SMA_BULLISH_CROSS_30M" in valid_tags:
        annotations["SMA_BULLISH_CROSS_30M"] = f"30M SMA5={sma5_30m:.1f} > SMA20={sma20_30m:.1f} — short-term bullish"
    if "SMA_BEARISH_CROSS_30M" in valid_tags:
        annotations["SMA_BEARISH_CROSS_30M"] = f"30M SMA5={sma5_30m:.1f} < SMA20={sma20_30m:.1f} — short-term bearish"

    # ── Order Flow (CVD / Buy Ratio) ── v36.0
    cvd_cum = _g(features, "cvd_cumulative_30m", 0.0)
    if "CVD_POSITIVE" in valid_tags:
        annotations["CVD_POSITIVE"] = f"CVD cumulative={cvd_cum:+.0f} — net aggressive buying"
    if "CVD_NEGATIVE" in valid_tags:
        annotations["CVD_NEGATIVE"] = f"CVD cumulative={cvd_cum:+.0f} — net aggressive selling"
    _price_chg_5bar = _g(features, "price_30m_change_5bar_pct", 0.0)
    if "CVD_ACCUMULATION" in valid_tags:
        annotations["CVD_ACCUMULATION"] = f"Price {_price_chg_5bar:+.2f}% + CVD={cvd_cum:+.0f} — smart money buying dip (吸筹)"
    if "CVD_DISTRIBUTION" in valid_tags:
        annotations["CVD_DISTRIBUTION"] = f"Price {_price_chg_5bar:+.2f}% + CVD={cvd_cum:+.0f} — rally on weak buying (派发)"
    if "CVD_ABSORPTION_BUY" in valid_tags:
        annotations["CVD_ABSORPTION_BUY"] = f"CVD={cvd_cum:+.0f} + price flat ({_price_chg_5bar:+.2f}%) — passive sellers absorbing buy pressure (买方吸收)"
    if "CVD_ABSORPTION_SELL" in valid_tags:
        annotations["CVD_ABSORPTION_SELL"] = f"CVD={cvd_cum:+.0f} + price flat ({_price_chg_5bar:+.2f}%) — passive buyers absorbing sell pressure (卖方吸收)"
    buy_ratio_30m = _g(features, "buy_ratio_30m", 0.5)
    buy_ratio_4h = _g(features, "buy_ratio_4h", 0.5)
    if "BUY_RATIO_HIGH" in valid_tags:
        annotations["BUY_RATIO_HIGH"] = f"Taker buy ratio 30m={buy_ratio_30m:.2f}, 4H={buy_ratio_4h:.2f} — buyers aggressive"
    if "BUY_RATIO_LOW" in valid_tags:
        annotations["BUY_RATIO_LOW"] = f"Taker buy ratio 30m={buy_ratio_30m:.2f}, 4H={buy_ratio_4h:.2f} — sellers aggressive"

    # OBI — v36.0: added OBI_BUY/SELL_PRESSURE + LIQUIDITY + SLIPPAGE
    obi_val = _g(features, "obi_weighted", 0.0)
    if "OBI_BUY_PRESSURE" in valid_tags:
        annotations["OBI_BUY_PRESSURE"] = f"OBI={obi_val:+.3f} — orderbook skewed to buy side"
    if "OBI_SELL_PRESSURE" in valid_tags:
        annotations["OBI_SELL_PRESSURE"] = f"OBI={obi_val:+.3f} — orderbook skewed to sell side"
    if "OBI_BALANCED" in valid_tags:
        annotations["OBI_BALANCED"] = f"OBI={obi_val:+.3f} — balanced orderbook, no pressure bias"
    obi_chg = _g(features, "obi_change_pct", 0.0)
    if "OBI_SHIFTING_BULLISH" in valid_tags:
        annotations["OBI_SHIFTING_BULLISH"] = f"OBI shift={obi_chg:+.1f}% — significant bullish rotation"
    if "OBI_SHIFTING_BEARISH" in valid_tags:
        annotations["OBI_SHIFTING_BEARISH"] = f"OBI shift={obi_chg:+.1f}% — significant bearish rotation"
    bid_vol = _g(features, "bid_volume_usd", 0.0)
    ask_vol = _g(features, "ask_volume_usd", 0.0)
    if "LIQUIDITY_THIN" in valid_tags:
        _min_side = min(bid_vol, ask_vol)
        annotations["LIQUIDITY_THIN"] = f"Min side depth=${_min_side:,.0f} <$500K — slippage risk, reduce size"
    if "SLIPPAGE_HIGH" in valid_tags:
        _imbal = abs(bid_vol - ask_vol) / max(bid_vol, ask_vol, 1) * 100
        annotations["SLIPPAGE_HIGH"] = f"Bid/Ask imbalance={_imbal:.0f}% — directional slippage risk"

    # FR tags
    fr_pct = _g(features, "funding_rate_pct", 0.0)
    if "FR_IGNORED" in valid_tags:
        annotations["FR_IGNORED"] = f"FR={fr_pct*100:.5f}% ≈ neutral — no cost/benefit to either side"
    if "FR_FAVORABLE_LONG" in valid_tags:
        annotations["FR_FAVORABLE_LONG"] = f"FR={fr_pct*100:.5f}% negative — shorts pay longs, favorable for LONG"
    if "FR_FAVORABLE_SHORT" in valid_tags:
        annotations["FR_FAVORABLE_SHORT"] = f"FR={fr_pct*100:.5f}% positive — longs pay shorts, favorable for SHORT"
    if "FR_EXTREME" in valid_tags:
        annotations["FR_EXTREME"] = f"|FR|={abs(fr_pct)*100:.3f}% — extreme, reversal probability elevated"
    if "FR_ADVERSE_LONG" in valid_tags:
        annotations["FR_ADVERSE_LONG"] = f"FR={fr_pct*100:.5f}% costs longs"
    if "FR_ADVERSE_SHORT" in valid_tags:
        annotations["FR_ADVERSE_SHORT"] = f"FR={fr_pct*100:.5f}% costs shorts"
    if "FR_TREND_RISING" in valid_tags:
        annotations["FR_TREND_RISING"] = "FR trending higher — increasing long cost pressure"
    if "FR_TREND_FALLING" in valid_tags:
        annotations["FR_TREND_FALLING"] = "FR trending lower — decreasing long cost pressure"

    # Premium index
    prem = _g(features, "premium_index", 0.0)
    if "PREMIUM_POSITIVE" in valid_tags:
        annotations["PREMIUM_POSITIVE"] = f"Premium={prem*100:.3f}% — futures at premium, bullish demand"
    if "PREMIUM_NEGATIVE" in valid_tags:
        annotations["PREMIUM_NEGATIVE"] = f"Premium={prem*100:.3f}% — futures at discount, bearish demand"

    # OI positioning — v36.0
    oi_trend = _g(features, "oi_trend", "STABLE")
    price_chg = _g(features, "price_30m_change_5bar_pct", 0.0)
    if "OI_LONG_OPENING" in valid_tags:
        annotations["OI_LONG_OPENING"] = f"OI {oi_trend} + price {price_chg:+.2f}% — new long positions opening"
    if "OI_SHORT_OPENING" in valid_tags:
        annotations["OI_SHORT_OPENING"] = f"OI {oi_trend} + price {price_chg:+.2f}% — new short positions opening"
    if "OI_LONG_CLOSING" in valid_tags:
        annotations["OI_LONG_CLOSING"] = f"OI {oi_trend} + price {price_chg:+.2f}% — longs closing (profit-taking/stop-loss)"
    if "OI_SHORT_CLOSING" in valid_tags:
        annotations["OI_SHORT_CLOSING"] = f"OI {oi_trend} + price {price_chg:+.2f}% — shorts closing (short squeeze/covering)"

    # Liquidation cascade — v36.0
    liq_bias = _g(features, "liquidation_bias", "NONE")
    if "LIQUIDATION_CASCADE_LONG" in valid_tags:
        annotations["LIQUIDATION_CASCADE_LONG"] = f"Liquidation bias: LONG dominant — cascading long liquidations, bearish pressure"
    if "LIQUIDATION_CASCADE_SHORT" in valid_tags:
        annotations["LIQUIDATION_CASCADE_SHORT"] = f"Liquidation bias: SHORT dominant — cascading short liquidations, bullish pressure"

    # Top traders — v36.0
    top_long = _g(features, "top_traders_long_ratio", 0.0)
    if "TOP_TRADERS_LONG_BIAS" in valid_tags:
        annotations["TOP_TRADERS_LONG_BIAS"] = f"Top traders L/S ratio={top_long:.2f} — smart money net long"
    if "TOP_TRADERS_SHORT_BIAS" in valid_tags:
        annotations["TOP_TRADERS_SHORT_BIAS"] = f"Top traders L/S ratio={top_long:.2f} — smart money net short"

    # Taker ratio
    taker = _g(features, "taker_buy_ratio", 0.5)
    if "TAKER_BUY_DOMINANT" in valid_tags:
        annotations["TAKER_BUY_DOMINANT"] = f"Taker buy ratio={taker:.2f} — aggressive buying dominant"
    if "TAKER_SELL_DOMINANT" in valid_tags:
        annotations["TAKER_SELL_DOMINANT"] = f"Taker buy ratio={taker:.2f} — aggressive selling dominant"

    # Volatility — 30M
    if "VOL_EXTREME" in valid_tags:
        pctl = _g(features, "volatility_percentile_30m", 0.0)
        annotations["VOL_EXTREME"] = f"30M ATR% at {pctl:.0f}th percentile — high whipsaw risk, reduce size"
    if "VOL_HIGH" in valid_tags:
        pctl = _g(features, "volatility_percentile_30m", 0.0)
        annotations["VOL_HIGH"] = f"30M ATR% at {pctl:.0f}th percentile — elevated volatility, widen stops"
    if "VOL_LOW" in valid_tags:
        pctl = _g(features, "volatility_percentile_30m", 0.0)
        annotations["VOL_LOW"] = f"30M ATR% at {pctl:.0f}th percentile — calm/squeeze, breakout may be imminent"

    # Volatility — 4H — v36.0
    if "VOL_4H_EXTREME" in valid_tags:
        pctl_4h = _g(features, "volatility_percentile_4h", 0.0)
        annotations["VOL_4H_EXTREME"] = f"4H ATR% at {pctl_4h:.0f}th percentile — extreme volatility regime"
    if "VOL_4H_HIGH" in valid_tags:
        pctl_4h = _g(features, "volatility_percentile_4h", 0.0)
        annotations["VOL_4H_HIGH"] = f"4H ATR% at {pctl_4h:.0f}th percentile — elevated 4H volatility"
    if "VOL_4H_LOW" in valid_tags:
        pctl_4h = _g(features, "volatility_percentile_4h", 0.0)
        annotations["VOL_4H_LOW"] = f"4H ATR% at {pctl_4h:.0f}th percentile — compressed 4H volatility"

    # Volatility — 1D — v36.0
    if "VOL_1D_EXTREME" in valid_tags:
        pctl_1d = _g(features, "volatility_percentile_1d", 0.0)
        annotations["VOL_1D_EXTREME"] = f"1D ATR% at {pctl_1d:.0f}th percentile — extreme daily volatility"
    if "VOL_1D_HIGH" in valid_tags:
        pctl_1d = _g(features, "volatility_percentile_1d", 0.0)
        annotations["VOL_1D_HIGH"] = f"1D ATR% at {pctl_1d:.0f}th percentile — elevated daily volatility"
    if "VOL_1D_LOW" in valid_tags:
        pctl_1d = _g(features, "volatility_percentile_1d", 0.0)
        annotations["VOL_1D_LOW"] = f"1D ATR% at {pctl_1d:.0f}th percentile — compressed daily volatility, breakout setup"

    # Extension normal — v36.0
    if "EXTENSION_NORMAL" in valid_tags:
        ext_r = _g(features, "extension_ratio_30m", 0.0)
        annotations["EXTENSION_NORMAL"] = f"Extension={ext_r:+.1f} ATR — within normal range, no overextension"

    # Liquidation buffer — v36.0
    liq_buf = _g(features, "liquidation_buffer_pct", 100.0)
    if "LIQUIDATION_BUFFER_LOW" in valid_tags:
        annotations["LIQUIDATION_BUFFER_LOW"] = f"Liquidation buffer={liq_buf:.1f}% <10% — reduce exposure"
    if "LIQUIDATION_BUFFER_CRITICAL" in valid_tags:
        annotations["LIQUIDATION_BUFFER_CRITICAL"] = f"Liquidation buffer={liq_buf:.1f}% <5% — CRITICAL, immediate risk reduction needed"

    # Sentiment — v36.0
    long_ratio = _g(features, "long_ratio", 0.5)
    short_ratio = _g(features, "short_ratio", 0.5)
    if "SENTIMENT_CROWDED_LONG" in valid_tags:
        annotations["SENTIMENT_CROWDED_LONG"] = f"L/S ratio={long_ratio:.1%}/{short_ratio:.1%} — crowded long, contrarian SHORT risk"
    if "SENTIMENT_CROWDED_SHORT" in valid_tags:
        annotations["SENTIMENT_CROWDED_SHORT"] = f"L/S ratio={long_ratio:.1%}/{short_ratio:.1%} — crowded short, contrarian LONG risk"
    if "SENTIMENT_EXTREME" in valid_tags:
        annotations["SENTIMENT_EXTREME"] = f"L/S ratio={long_ratio:.1%}/{short_ratio:.1%} — extreme crowding, high reversal probability"
    if "SENTIMENT_NEUTRAL" in valid_tags:
        annotations["SENTIMENT_NEUTRAL"] = f"L/S ratio={long_ratio:.1%}/{short_ratio:.1%} — balanced, no crowding signal"

    # ── Fear & Greed (v44.0) ──
    fg_index = int(_g(features, "fear_greed_index", 50))
    if "EXTREME_FEAR" in valid_tags:
        annotations["EXTREME_FEAR"] = f"Fear & Greed={fg_index}/100 — extreme fear, contrarian bullish (historically high probability of bounce)"
    if "EXTREME_GREED" in valid_tags:
        annotations["EXTREME_GREED"] = f"Fear & Greed={fg_index}/100 — extreme greed, contrarian bearish (historically high probability of correction)"

    # Build output: annotated tags first, then plain tags
    lines = []
    for tag in sorted(valid_tags):
        if tag in annotations:
            lines.append(f"  {tag} ({annotations[tag]})")
        else:
            lines.append(f"  {tag}")

    return "\n".join(lines)


def filter_output_tags(
    output: Dict[str, Any],
    valid_tags: Set[str],
    tag_fields: tuple = ("evidence", "risk_flags", "decisive_reasons", "risk_factors", "acknowledged_risks"),
) -> int:
    """
    Strip tags from agent output that are not in the valid_tags set.

    Mutates output in-place. Returns count of removed tags.

    Args:
        output: Agent output dict (after schema validation).
        valid_tags: Set of data-supported tags from compute_valid_tags().
        tag_fields: Field names that contain tag lists.

    Returns:
        Number of tags removed.
    """
    removed = 0
    removed_tags: list = []
    for field in tag_fields:
        if field not in output or not isinstance(output[field], list):
            continue
        original = output[field]
        filtered = [t for t in original if t in valid_tags]
        diff = len(original) - len(filtered)
        if diff > 0:
            stripped = [t for t in original if t not in valid_tags]
            removed_tags.extend(f"{field}:{t}" for t in stripped)
        removed += diff
        output[field] = filtered
    if removed > 0:
        logger.warning(
            f"[TagValidator] Stripped {removed} data-inconsistent tag(s): "
            f"{', '.join(removed_tags)}"
        )
    return removed


def validate_judge_confluence(
    result: Dict[str, Any],
    features: Dict[str, Any],
    dim_scores: Optional[Dict[str, Any]] = None,
) -> int:
    """
    Deterministically override Judge's confluence layer assessments
    when they contradict feature data.

    Mutates result["confluence"] in-place. Returns count of corrections.

    Rules:
    - trend_1d MUST match adx_direction_1d (BULLISH→BULLISH, BEARISH→BEARISH)
    - momentum_4h: if MACD histogram trend is FLAT and ADX < 25 → NEUTRAL
    - aligned_layers is recomputed after corrections
    """
    confluence = result.get("confluence")
    if not isinstance(confluence, dict):
        return 0

    corrections = 0
    decision = result.get("decision", "HOLD")

    # ── trend_1d: must match adx_direction_1d ──
    direction_1d = _g(features, "adx_direction_1d", "")
    expected_trend = {
        "BULLISH": "BULLISH",
        "BEARISH": "BEARISH",
    }.get(direction_1d, "NEUTRAL")

    actual_trend = confluence.get("trend_1d", "NEUTRAL")
    if actual_trend != expected_trend:
        logger.warning(
            f"[TagValidator] Judge confluence.trend_1d corrected: "
            f"{actual_trend} → {expected_trend} (adx_direction_1d={direction_1d})"
        )
        confluence["trend_1d"] = expected_trend
        corrections += 1

    # ── momentum_4h: contradict check ──
    macd_4h = _g(features, "macd_4h", 0.0)
    macd_signal_4h = _g(features, "macd_signal_4h", 0.0)
    macd_hist_trend = _g(features, "macd_histogram_4h_trend_5bar", "")
    di_plus_4h = _g(features, "di_plus_4h", 0.0)
    di_minus_4h = _g(features, "di_minus_4h", 0.0)
    momentum_4h = confluence.get("momentum_4h", "NEUTRAL")

    # If Judge says BULLISH momentum but MACD is bearish cross AND DI- > DI+ → correct
    if momentum_4h == "BULLISH" and macd_4h < macd_signal_4h and di_minus_4h > di_plus_4h:
        logger.warning(
            f"[TagValidator] Judge confluence.momentum_4h corrected: "
            f"BULLISH → BEARISH (MACD bearish cross + DI- > DI+)"
        )
        confluence["momentum_4h"] = "BEARISH"
        corrections += 1
    elif momentum_4h == "BEARISH" and macd_4h > macd_signal_4h and di_plus_4h > di_minus_4h:
        logger.warning(
            f"[TagValidator] Judge confluence.momentum_4h corrected: "
            f"BEARISH → BULLISH (MACD bullish cross + DI+ > DI-)"
        )
        confluence["momentum_4h"] = "BULLISH"
        corrections += 1

    # v44.1: order_flow soft validation (warn but don't override — subjective layer)
    if dim_scores:
        flow_dir_scores = dim_scores.get("order_flow", {}).get("direction", "NEUTRAL") if isinstance(dim_scores.get("order_flow"), dict) else "NEUTRAL"
        judge_flow_raw = str(confluence.get("order_flow", "NEUTRAL")).upper()
        judge_flow = "BULLISH" if "BULLISH" in judge_flow_raw else ("BEARISH" if "BEARISH" in judge_flow_raw else "NEUTRAL")
        if flow_dir_scores not in ("NEUTRAL", "N/A", "MIXED") and judge_flow not in ("NEUTRAL",) and flow_dir_scores != judge_flow:
            logger.warning(
                f"[TagValidator] Judge order_flow={judge_flow} contradicts "
                f"scores.order_flow={flow_dir_scores} — not overriding (subjective layer)"
            )

    # ── Recompute aligned_layers after corrections ──
    if corrections > 0:
        layers = [
            confluence.get("trend_1d", "NEUTRAL"),
            confluence.get("momentum_4h", "NEUTRAL"),
            confluence.get("order_flow", "NEUTRAL"),
            confluence.get("levels_30m", "NEUTRAL"),
            confluence.get("derivatives", "NEUTRAL"),
        ]
        if decision in ("LONG", "SHORT"):
            target = "BULLISH" if decision == "LONG" else "BEARISH"
            aligned = sum(1 for l in layers if l == target)
        else:
            aligned = 0
        old_aligned = confluence.get("aligned_layers", 0)
        if aligned != old_aligned:
            logger.warning(
                f"[TagValidator] Judge aligned_layers corrected: "
                f"{old_aligned} → {aligned}"
            )
            confluence["aligned_layers"] = aligned

    return corrections
