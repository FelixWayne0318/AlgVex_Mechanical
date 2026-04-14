"""
Prompt Constants — Mechanical Mode

Retains only the three items needed by the mechanical trading path:
1. FEATURE_SCHEMA  — 124 typed feature definitions (used by extract_features())
2. REASON_TAGS     — valid tag vocabulary (used by compute_valid_tags())
3. _get_multiplier — regime-aware annotation helper (used by report_formatter.py)
"""


# =============================================================================
# Signal Annotations — regime-aware multiplier table
# Used by _get_multiplier() and report_formatter.extract_features()
# =============================================================================

_SIGNAL_ANNOTATIONS = {
    # v41.0: Unified Indicator Classification — nature labels aligned with
    # compute_scores_from_features() 5 dimensions + Structure + Context.
    # Only nature labels changed; all multiplier values are UNCHANGED.
    # Layer 1 — TREND (1D)
    '1d_sma200':  ('Trend',      {'strong': 1.3, 'weak': 1.0, 'ranging': 0.4}),
    '1d_adx_di':  ('Trend',      {'strong': 1.2, 'weak': 1.0, 'ranging': 0.3}),
    '1d_macd':    ('Trend',      {'strong': 1.1, 'weak': 1.0, 'ranging': 0.3}),
    '1d_macd_h':  ('Momentum',   {'strong': 1.0, 'weak': 1.0, 'ranging': 0.3}),
    '1d_rsi':     ('Momentum',   {'strong': 0.9, 'weak': 1.0, 'ranging': 0.7}),
    # Layer 2 — MOMENTUM (4H)
    '4h_rsi':     ('Momentum',   {'strong': 0.8, 'weak': 1.0, 'ranging': 1.2}),
    '4h_macd':    ('Trend',      {'strong': 1.2, 'weak': 1.0, 'ranging': 0.3}),
    '4h_macd_h':  ('Momentum',   {'strong': 1.0, 'weak': 1.0, 'ranging': 0.5}),
    '4h_adx_di':  ('Trend',      {'strong': 1.1, 'weak': 1.0, 'ranging': 0.4}),
    '4h_bb':      ('Momentum',   {'strong': 0.6, 'weak': 0.9, 'ranging': 1.2}),
    '4h_sma':     ('Trend',      {'strong': 1.1, 'weak': 1.0, 'ranging': 0.4}),
    # ATR/BB
    '1d_bb':      ('Momentum',   {'strong': 0.6, 'weak': 0.9, 'ranging': 1.2}),
    '1d_atr':     ('Volatility', {'strong': 1.0, 'weak': 1.0, 'ranging': 1.0}),
    '4h_atr':     ('Volatility', {'strong': 1.0, 'weak': 1.0, 'ranging': 1.0}),
    '4h_vol_ratio': ('Momentum', {'strong': 0.9, 'weak': 1.0, 'ranging': 1.1}),
    # Layer 3 — KEY LEVELS (30M)
    '30m_rsi':    ('Momentum',   {'strong': 0.8, 'weak': 1.0, 'ranging': 1.2}),
    '30m_macd':   ('Momentum',   {'strong': 1.0, 'weak': 1.0, 'ranging': 0.5}),
    '30m_macd_h': ('Momentum',   {'strong': 0.9, 'weak': 1.0, 'ranging': 0.5}),
    '30m_adx':    ('Trend',      {'strong': 1.1, 'weak': 1.0, 'ranging': 0.4}),
    '30m_bb':     ('Momentum',   {'strong': 0.6, 'weak': 0.9, 'ranging': 1.2}),
    '30m_sma':    ('Trend',      {'strong': 0.9, 'weak': 1.0, 'ranging': 0.6}),
    '30m_volume': ('Momentum',   {'strong': 0.9, 'weak': 1.0, 'ranging': 1.1}),
    # v36.1: OBV — macro volume accumulation/distribution
    '30m_obv':    ('Momentum',   {'strong': 0.7, 'weak': 0.9, 'ranging': 1.0}),
    '4h_obv':     ('Momentum',   {'strong': 0.8, 'weak': 1.0, 'ranging': 1.0}),
    '1d_obv':     ('Momentum',   {'strong': 0.9, 'weak': 1.0, 'ranging': 0.8}),
    # v36.1: 1D/4H volume ratio
    '1d_volume':  ('Momentum',   {'strong': 0.9, 'weak': 1.0, 'ranging': 1.0}),
    '4h_volume':  ('Momentum',   {'strong': 0.9, 'weak': 1.0, 'ranging': 1.1}),
}


def _get_multiplier(key: str, adx_1d: float) -> tuple:
    """Return (nature, multiplier, tier) for a given annotation key and ADX.

    Used by extract_features() for regime-aware indicator weighting.
    """
    info = _SIGNAL_ANNOTATIONS.get(key)
    if not info:
        return ('N/A', 1.0, 'std')
    nature, multipliers = info
    if adx_1d >= 40:
        m = multipliers['strong']
    elif adx_1d >= 25:
        m = multipliers['weak']
    else:
        m = multipliers['ranging']
    if m >= 1.2:
        tier = 'high'
    elif m >= 0.8:
        tier = 'std'
    elif m >= 0.5:
        tier = 'low'
    else:
        tier = 'skip'
    return (nature, m, tier)


# =============================================================================
# Feature Schema — 124 typed feature definitions
# All agents receive one unified feature_dict instead of text reports.
# Each feature maps to an exact key in existing raw data dicts.
# =============================================================================

FEATURE_SCHEMA = {
    # ── v2.0 HMM Features (Phase 1, from close / prev_close) ──
    "log_return_30m":           {"type": "float", "source": "log(technical_data['close'] / prev_close_30m)"},
    "log_return_4h":            {"type": "float", "source": "log(mtf_decision_layer['close'] / prev_close_4h)"},

    # ── 30M Execution Layer (from technical_manager.get_technical_data()) ──
    "price":                    {"type": "float", "source": "technical_data['price']"},
    "rsi_30m":                  {"type": "float", "source": "technical_data['rsi']"},
    "macd_30m":                 {"type": "float", "source": "technical_data['macd']"},
    "macd_signal_30m":          {"type": "float", "source": "technical_data['macd_signal']"},
    "macd_histogram_30m":       {"type": "float", "source": "technical_data['macd_histogram']"},
    "adx_30m":                  {"type": "float", "source": "technical_data['adx']"},
    "di_plus_30m":              {"type": "float", "source": "technical_data['di_plus']"},
    "di_minus_30m":             {"type": "float", "source": "technical_data['di_minus']"},
    "bb_position_30m":          {"type": "float", "source": "technical_data['bb_position']"},
    "bb_upper_30m":             {"type": "float", "source": "technical_data['bb_upper']"},
    "bb_lower_30m":             {"type": "float", "source": "technical_data['bb_lower']"},
    "sma_5_30m":                {"type": "float", "source": "technical_data['sma_5']"},   # v36.0 FIX: 30M has sma_periods=[5,20]
    "sma_20_30m":               {"type": "float", "source": "technical_data['sma_20']"},
    "volume_ratio_30m":         {"type": "float", "source": "technical_data['volume_ratio']"},
    "atr_pct_30m":              {"type": "float", "source": "technical_data['atr_pct']"},
    "ema_12_30m":               {"type": "float", "source": "technical_data['ema_12']"},  # base indicator_manager: ema_periods=[macd_fast=12, macd_slow=26]
    "ema_26_30m":               {"type": "float", "source": "technical_data['ema_26']"},

    # ── 4H Decision Layer (from technical_data['mtf_decision_layer']) ──
    "rsi_4h":                   {"type": "float", "source": "mtf_decision_layer['rsi']"},
    "macd_4h":                  {"type": "float", "source": "mtf_decision_layer['macd']"},
    "macd_signal_4h":           {"type": "float", "source": "mtf_decision_layer['macd_signal']"},
    "macd_histogram_4h":        {"type": "float", "source": "mtf_decision_layer['macd_histogram']"},
    "adx_4h":                   {"type": "float", "source": "mtf_decision_layer['adx']"},
    "di_plus_4h":               {"type": "float", "source": "mtf_decision_layer['di_plus']"},
    "di_minus_4h":              {"type": "float", "source": "mtf_decision_layer['di_minus']"},
    "bb_position_4h":           {"type": "float", "source": "mtf_decision_layer['bb_position']"},
    "bb_upper_4h":              {"type": "float", "source": "mtf_decision_layer['bb_upper']"},
    "bb_lower_4h":              {"type": "float", "source": "mtf_decision_layer['bb_lower']"},
    "sma_20_4h":                {"type": "float", "source": "mtf_decision_layer['sma_20']"},
    "sma_50_4h":                {"type": "float", "source": "mtf_decision_layer['sma_50']"},
    "volume_ratio_4h":          {"type": "float", "source": "mtf_decision_layer['volume_ratio']"},
    "atr_4h":                   {"type": "float", "source": "mtf_decision_layer['atr']"},
    "atr_pct_4h":               {"type": "float", "source": "mtf_decision_layer['atr_pct']"},
    "ema_12_4h":                {"type": "float", "source": "mtf_decision_layer['ema_12']"},
    "ema_26_4h":                {"type": "float", "source": "mtf_decision_layer['ema_26']"},
    "extension_ratio_4h":       {"type": "float", "source": "mtf_decision_layer['extension_ratio_sma_20']"},
    "extension_regime_4h":      {"type": "enum",  "values": ["NORMAL", "EXTENDED", "OVEREXTENDED", "EXTREME"]},
    "volatility_regime_4h":     {"type": "enum",  "values": ["LOW", "NORMAL", "HIGH", "EXTREME"]},
    "volatility_percentile_4h": {"type": "float", "source": "mtf_decision_layer['volatility_percentile']"},

    # ── 1D Trend Layer (from technical_data['mtf_trend_layer']) ──
    "adx_1d":                   {"type": "float", "source": "mtf_trend_layer['adx']"},
    "di_plus_1d":               {"type": "float", "source": "mtf_trend_layer['di_plus']"},
    "di_minus_1d":              {"type": "float", "source": "mtf_trend_layer['di_minus']"},
    "rsi_1d":                   {"type": "float", "source": "mtf_trend_layer['rsi']"},
    "macd_1d":                  {"type": "float", "source": "mtf_trend_layer['macd']"},
    "macd_signal_1d":           {"type": "float", "source": "mtf_trend_layer['macd_signal']"},
    "macd_histogram_1d":        {"type": "float", "source": "mtf_trend_layer['macd_histogram']"},
    "sma_200_1d":               {"type": "float", "source": "mtf_trend_layer['sma_200']"},
    "bb_position_1d":           {"type": "float", "source": "mtf_trend_layer['bb_position']"},
    "volume_ratio_1d":          {"type": "float", "source": "mtf_trend_layer['volume_ratio']"},
    "atr_1d":                   {"type": "float", "source": "mtf_trend_layer['atr']"},
    "atr_pct_1d":               {"type": "float", "source": "mtf_trend_layer['atr_pct']"},
    "ema_12_1d":                {"type": "float", "source": "mtf_trend_layer['ema_12']"},
    "ema_26_1d":                {"type": "float", "source": "mtf_trend_layer['ema_26']"},
    "extension_ratio_1d":       {"type": "float", "source": "mtf_trend_layer['extension_ratio_sma_200']"},
    "extension_regime_1d":      {"type": "enum",  "values": ["NORMAL", "EXTENDED", "OVEREXTENDED", "EXTREME"]},
    "volatility_regime_1d":     {"type": "enum",  "values": ["LOW", "NORMAL", "HIGH", "EXTREME"]},
    "volatility_percentile_1d": {"type": "float", "source": "mtf_trend_layer['volatility_percentile']"},

    # ── Risk Context (30M, renamed from unsuffixed to match 4H/1D convention) ──
    "extension_ratio_30m":      {"type": "float", "source": "technical_data['extension_ratio_sma_20']"},
    "extension_regime_30m":     {"type": "enum",  "values": ["NORMAL", "EXTENDED", "OVEREXTENDED", "EXTREME"]},
    "volatility_regime_30m":    {"type": "enum",  "values": ["LOW", "NORMAL", "HIGH", "EXTREME"]},
    "volatility_percentile_30m": {"type": "float", "source": "technical_data['volatility_percentile']"},
    "atr_30m":                  {"type": "float", "source": "technical_data['atr']"},

    # ── Market Regime (pre-computed) ──
    "market_regime":            {"type": "enum",  "values": ["STRONG_TREND", "WEAK_TREND", "RANGING"]},
    "adx_direction_1d":         {"type": "enum",  "values": ["BULLISH", "BEARISH", "NEUTRAL"]},  # v36.2: NEUTRAL when DI+ == DI-

    # ── Pre-computed Categorical (v31.0: LLM-friendly labels from raw numerics) ──
    "macd_cross_30m":           {"type": "enum",  "values": ["BULLISH", "BEARISH", "NEUTRAL"]},
    "macd_cross_4h":            {"type": "enum",  "values": ["BULLISH", "BEARISH", "NEUTRAL"]},
    "macd_cross_1d":            {"type": "enum",  "values": ["BULLISH", "BEARISH", "NEUTRAL"]},
    "di_direction_30m":         {"type": "enum",  "values": ["BULLISH", "BEARISH"]},
    "di_direction_4h":          {"type": "enum",  "values": ["BULLISH", "BEARISH"]},
    "rsi_zone_30m":             {"type": "enum",  "values": ["OVERSOLD", "NEUTRAL", "OVERBOUGHT"]},
    "rsi_zone_4h":              {"type": "enum",  "values": ["OVERSOLD", "NEUTRAL", "OVERBOUGHT"]},
    "rsi_zone_1d":              {"type": "enum",  "values": ["OVERSOLD", "NEUTRAL", "OVERBOUGHT"]},
    "fr_direction":             {"type": "enum",  "values": ["POSITIVE", "NEGATIVE", "NEUTRAL"]},

    # ── Divergences (pre-computed by _detect_divergences()) ──
    "rsi_divergence_4h":        {"type": "enum",  "values": ["BULLISH", "BEARISH", "NONE"]},
    "macd_divergence_4h":       {"type": "enum",  "values": ["BULLISH", "BEARISH", "NONE"]},
    "obv_divergence_4h":        {"type": "enum",  "values": ["BULLISH", "BEARISH", "NONE"]},
    "rsi_divergence_30m":       {"type": "enum",  "values": ["BULLISH", "BEARISH", "NONE"]},
    "macd_divergence_30m":      {"type": "enum",  "values": ["BULLISH", "BEARISH", "NONE"]},
    "obv_divergence_30m":       {"type": "enum",  "values": ["BULLISH", "BEARISH", "NONE"]},

    # ── Order Flow (from order_flow_report) ──
    "cvd_trend_30m":            {"type": "enum",  "values": ["POSITIVE", "NEGATIVE", "NEUTRAL"]},
    "buy_ratio_30m":            {"type": "float", "source": "order_flow_report['buy_ratio']"},
    "cvd_cumulative_30m":       {"type": "float", "source": "order_flow_report['cvd_cumulative']"},
    "cvd_price_cross_30m":      {"type": "enum",  "values": ["ACCUMULATION", "DISTRIBUTION", "CONFIRMED_SELL", "ABSORPTION_BUY", "ABSORPTION_SELL", "NONE"]},

    # ── 4H CVD (from order_flow_4h) ──
    "cvd_trend_4h":             {"type": "enum",  "values": ["POSITIVE", "NEGATIVE", "NEUTRAL"]},
    "buy_ratio_4h":             {"type": "float", "source": "order_flow_4h['buy_ratio']"},
    "cvd_price_cross_4h":       {"type": "enum",  "values": ["ACCUMULATION", "DISTRIBUTION", "CONFIRMED_SELL", "ABSORPTION_BUY", "ABSORPTION_SELL", "NONE"]},

    # ── Derivatives (from derivatives_report) ──
    "funding_rate_pct":         {"type": "float", "source": "funding_rate['current_pct']"},
    "funding_rate_trend":       {"type": "enum",  "values": ["RISING", "FALLING", "STABLE"]},
    "oi_trend":                 {"type": "enum",  "values": ["RISING", "FALLING", "STABLE"]},
    "liquidation_bias":         {"type": "enum",  "values": ["LONG_DOMINANT", "SHORT_DOMINANT", "BALANCED", "NONE"]},
    "premium_index":            {"type": "float", "source": "funding_rate['premium_index']"},

    # ── Orderbook (from orderbook_report) ──
    "obi_weighted":             {"type": "float", "source": "orderbook['obi']['weighted']"},
    "obi_change_pct":           {"type": "float", "source": "orderbook['dynamics']['obi_change_pct']"},
    "bid_volume_usd":           {"type": "float", "source": "orderbook['obi']['bid_volume_usd']"},
    "ask_volume_usd":           {"type": "float", "source": "orderbook['obi']['ask_volume_usd']"},

    # ── Sentiment (from sentiment_report) ──
    "long_ratio":               {"type": "float", "source": "sentiment['positive_ratio']"},
    "short_ratio":              {"type": "float", "source": "sentiment['negative_ratio']"},
    "sentiment_degraded":       {"type": "bool",  "source": "sentiment['degraded']"},

    # ── Fear & Greed Index (v44.0, from alternative.me API) ──
    "fear_greed_index":         {"type": "int",   "range": [0, 100], "source": "alternative.me Fear & Greed API"},

    # ── Top Traders (from binance_derivatives_report) ──
    "top_traders_long_ratio":   {"type": "float", "source": "binance_derivatives['top_traders']"},
    "taker_buy_ratio":          {"type": "float", "source": "binance_derivatives['taker_ratio']"},

    # ── S/R Zones (from sr_zones calculation) ──
    "nearest_support_price":    {"type": "float"},
    "nearest_support_strength": {"type": "enum",  "values": ["HIGH", "MEDIUM", "LOW", "NONE"]},
    "nearest_support_dist_atr": {"type": "float"},
    "nearest_resist_price":     {"type": "float"},
    "nearest_resist_strength":  {"type": "enum",  "values": ["HIGH", "MEDIUM", "LOW", "NONE"]},
    "nearest_resist_dist_atr":  {"type": "float"},

    # ── Position Context (from current_position + account_context) ──
    # v31.4: Source comments match production field names:
    #   position_pnl_pct ← current_position['pnl_percentage']
    #   position_size_pct ← current_position['margin_used_pct']
    #   liquidation_buffer_pct ← account_context['liquidation_buffer_portfolio_min_pct']
    "position_side":            {"type": "enum",  "values": ["LONG", "SHORT", "FLAT"]},
    "position_pnl_pct":         {"type": "float", "source": "current_position['pnl_percentage']"},
    "position_size_pct":        {"type": "float", "source": "current_position['margin_used_pct']"},
    "account_equity_usdt":      {"type": "float", "source": "account_context['equity']"},
    "liquidation_buffer_pct":   {"type": "float", "source": "account_context['liquidation_buffer_portfolio_min_pct']"},
    "leverage":                 {"type": "int",   "source": "account_context['leverage']"},

    # ── FR Block Context (v21.0) ──
    "fr_consecutive_blocks":    {"type": "int",   "source": "fr_block_context['consecutive_blocks']"},
    "fr_blocked_direction":     {"type": "enum",  "values": ["LONG", "SHORT", "NONE"]},

    # ── Trend Time Series (1D, last 5 bars summary) ──
    "adx_1d_trend_5bar":        {"type": "enum",  "values": ["RISING", "FALLING", "FLAT"]},
    "di_spread_1d_trend_5bar":  {"type": "enum",  "values": ["WIDENING", "NARROWING", "FLAT"]},
    "rsi_1d_trend_5bar":        {"type": "enum",  "values": ["RISING", "FALLING", "FLAT"]},
    "price_1d_change_5bar_pct": {"type": "float"},

    # ── 4H Time Series (last 5 bars summary, Entry Timing Agent key input) ──
    "rsi_4h_trend_5bar":            {"type": "enum",  "values": ["RISING", "FALLING", "FLAT"]},
    "macd_histogram_4h_trend_5bar": {"type": "enum",  "values": ["EXPANDING", "CONTRACTING", "FLAT"]},
    "adx_4h_trend_5bar":            {"type": "enum",  "values": ["RISING", "FALLING", "FLAT"]},
    "price_4h_change_5bar_pct":     {"type": "float"},
    "bb_width_4h_trend_5bar":       {"type": "enum",  "values": ["RISING", "FALLING", "FLAT"]},  # v36.0

    # ── 30M Time Series (last 5 bars summary, execution layer momentum) ──
    "momentum_shift_30m":           {"type": "enum",  "values": ["ACCELERATING", "DECELERATING", "STABLE"]},
    "price_30m_change_5bar_pct":    {"type": "float"},
    "rsi_30m_trend_5bar":           {"type": "enum",  "values": ["RISING", "FALLING", "FLAT"]},
    "bb_width_30m_trend_5bar":      {"type": "enum",  "values": ["RISING", "FALLING", "FLAT"]},  # v36.0

    # ── Data Availability Flags (v34.1) ──
    "_avail_order_flow":            {"type": "bool",  "source": "order_flow_data is not None"},
    "_avail_derivatives":           {"type": "bool",  "source": "derivatives_data is not None"},
    "_avail_binance_derivatives":   {"type": "bool",  "source": "binance_derivatives_data is not None"},
    "_avail_orderbook":             {"type": "bool",  "source": "orderbook_data is not None"},
    "_avail_mtf_4h":                {"type": "bool",  "source": "mtf_4h is not None"},
    "_avail_mtf_1d":                {"type": "bool",  "source": "mtf_1d is not None"},
    "_avail_account":               {"type": "bool",  "source": "account_context is not None"},
    "_avail_sr_zones":              {"type": "bool",  "source": "sr_zones is not None"},
    "_avail_sentiment":             {"type": "bool",  "source": "sentiment_data is not None and not degraded"},
}
# Total: ~124 features (numeric + categorical)


# =============================================================================
# Reason Tags — predefined vocabulary for structured debate
# Agents can ONLY reference these tags, never free text.
# =============================================================================

REASON_TAGS = {
    # ── Trend (Layer 1: 1D) ──
    "TREND_1D_BULLISH",         # Price > SMA200 + DI+ > DI-
    "TREND_1D_BEARISH",         # Price < SMA200 + DI- > DI+
    "TREND_1D_NEUTRAL",         # No clear direction
    "STRONG_TREND_ADX40",       # ADX_1D >= 40
    "WEAK_TREND_ADX_LOW",       # ADX_1D < 25
    "TREND_EXHAUSTION",         # ADX falling from peak
    "DI_BULLISH_CROSS",         # DI+ crossing above DI-
    "DI_BEARISH_CROSS",         # DI- crossing above DI+

    # ── Momentum (Layer 2: 4H) ──
    "MOMENTUM_4H_BULLISH",     # 4H momentum bias bullish (≥2 of: MACD>Signal, DI+>DI-, RSI>50)
    "MOMENTUM_4H_BEARISH",     # 4H momentum bias bearish (≥2 of: MACD<Signal, DI->DI+, RSI<50)
    "RSI_OVERBOUGHT",           # RSI > 70
    "RSI_OVERSOLD",             # RSI < 30
    "RSI_CARDWELL_BULL",        # RSI in 40-80 uptrend zone
    "RSI_CARDWELL_BEAR",        # RSI in 20-60 downtrend zone
    "MACD_BULLISH_CROSS",       # MACD crossing above signal
    "MACD_BEARISH_CROSS",       # MACD crossing below signal
    "MACD_HISTOGRAM_EXPANDING", # |histogram| increasing
    "MACD_HISTOGRAM_CONTRACTING",  # |histogram| decreasing
    "BB_UPPER_ZONE",            # BB position > 0.8
    "BB_LOWER_ZONE",            # BB position < 0.2
    "BB_SQUEEZE",               # BB width at local minimum
    "BB_EXPANSION",             # BB width expanding

    # ── Divergences ──
    "RSI_BULLISH_DIV_4H",
    "RSI_BEARISH_DIV_4H",
    "MACD_BULLISH_DIV_4H",
    "MACD_BEARISH_DIV_4H",
    "OBV_BULLISH_DIV_4H",
    "OBV_BEARISH_DIV_4H",
    "RSI_BULLISH_DIV_30M",
    "RSI_BEARISH_DIV_30M",
    "MACD_BULLISH_DIV_30M",
    "MACD_BEARISH_DIV_30M",
    "OBV_BULLISH_DIV_30M",      # v29.2: 30M OBV divergence
    "OBV_BEARISH_DIV_30M",      # v29.2: 30M OBV divergence

    # ── Order Flow ──
    "CVD_POSITIVE",             # CVD trend positive
    "CVD_NEGATIVE",             # CVD trend negative
    "CVD_ACCUMULATION",         # Price falling + CVD positive
    "CVD_DISTRIBUTION",         # Price rising + CVD negative
    "CVD_ABSORPTION_BUY",       # CVD negative + price flat = passive buying
    "CVD_ABSORPTION_SELL",      # CVD positive + price flat = passive selling
    "BUY_RATIO_HIGH",           # buy_ratio > 0.55
    "BUY_RATIO_LOW",            # buy_ratio < 0.45
    "OBI_BUY_PRESSURE",         # OBI weighted > 0.2
    "OBI_SELL_PRESSURE",        # OBI weighted < -0.2
    "OBI_BALANCED",             # |OBI| <= 0.2, neutral orderbook pressure
    "OBI_SHIFTING_BULLISH",     # OBI change > +20% (significant bullish shift)
    "OBI_SHIFTING_BEARISH",     # OBI change < -20% (significant bearish shift)
    "VOLUME_SURGE",             # Volume ratio > 1.5 (high relative volume)
    "VOLUME_DRY",               # Volume ratio < 0.5 (low relative volume)
    "SMA_BULLISH_CROSS_30M",    # 30M SMA20 > SMA50
    "SMA_BEARISH_CROSS_30M",    # 30M SMA20 < SMA50
    "SMA_BULLISH_CROSS_4H",    # v29.2: 4H SMA20 > SMA50
    "SMA_BEARISH_CROSS_4H",    # v29.2: 4H SMA20 < SMA50
    "EMA_BULLISH_CROSS_4H",    # v29.2: 4H EMA12 > EMA26
    "EMA_BEARISH_CROSS_4H",    # v29.2: 4H EMA12 < EMA26
    "MACD_1D_BULLISH",         # v29.2: 1D MACD > Signal
    "MACD_1D_BEARISH",         # v29.2: 1D MACD < Signal
    "TAKER_BUY_DOMINANT",       # Taker buy ratio > 0.55
    "TAKER_SELL_DOMINANT",      # Taker buy ratio < 0.45

    # ── Derivatives ──
    "FR_FAVORABLE_LONG",        # FR negative (pays longs)
    "FR_FAVORABLE_SHORT",       # FR positive (pays shorts)
    "FR_ADVERSE_LONG",          # FR > 0.01% (costs longs)
    "FR_ADVERSE_SHORT",         # FR < -0.01% (costs shorts)
    "FR_EXTREME",               # |FR| > 0.05%
    "FR_TREND_RISING",          # Funding rate trend rising
    "FR_TREND_FALLING",         # Funding rate trend falling
    "PREMIUM_POSITIVE",         # Futures premium > 0.05% (bullish demand)
    "PREMIUM_NEGATIVE",         # Futures discount < -0.05% (bearish demand)
    "OI_LONG_OPENING",          # OI rising + CVD positive
    "OI_SHORT_OPENING",         # OI rising + CVD negative
    "OI_LONG_CLOSING",          # OI falling + CVD negative
    "OI_SHORT_CLOSING",         # OI falling + CVD positive
    "LIQUIDATION_CASCADE_LONG", # Long liquidations dominant
    "LIQUIDATION_CASCADE_SHORT",  # Short liquidations dominant
    "TOP_TRADERS_LONG_BIAS",    # Top traders L/S > 0.55
    "TOP_TRADERS_SHORT_BIAS",   # Top traders L/S < 0.45

    # ── S/R Zones ──
    "NEAR_STRONG_SUPPORT",      # Within 3 ATR of support zone
    "NEAR_STRONG_RESISTANCE",   # Within 3 ATR of resistance zone
    "SR_BREAKOUT_POTENTIAL",    # Price testing S/R with momentum
    "SR_REJECTION",             # Price rejected at S/R
    "SR_TRAPPED",               # Between S and R with < 2 ATR spread
    "SR_CLEAR_SPACE",           # Far from both S and R

    # ── Risk Signals ──
    "EXTENSION_NORMAL",         # Extension ratio within normal range (NORMAL/EXTENDED)
    "EXTENSION_OVEREXTENDED",   # 30M: 3-5 ATR from SMA
    "EXTENSION_EXTREME",        # 30M: >5 ATR from SMA
    "EXTENSION_4H_OVEREXTENDED",  # v29.2: 4H 3-5 ATR from SMA
    "EXTENSION_4H_EXTREME",      # v29.2: 4H >5 ATR from SMA
    "EXTENSION_1D_OVEREXTENDED",  # v29.2: 1D 3-5 ATR from SMA200
    "EXTENSION_1D_EXTREME",      # v29.2: 1D >5 ATR from SMA200
    "VOL_EXTREME",              # 30M: >90th percentile volatility
    "VOL_HIGH",                 # 30M: >70th percentile volatility
    "VOL_LOW",                  # 30M: <30th percentile volatility
    "VOL_4H_HIGH",              # v29.2: 4H >70th percentile volatility
    "VOL_4H_EXTREME",           # v29.2: 4H >90th percentile volatility
    "VOL_4H_LOW",               # v29.2: 4H <30th percentile volatility
    "VOL_1D_HIGH",              # v29.2: 1D >70th percentile volatility
    "VOL_1D_EXTREME",           # v29.2: 1D >90th percentile volatility
    "VOL_1D_LOW",               # v29.2: 1D <30th percentile volatility
    "LIQUIDITY_THIN",           # Slippage > 50bps or thin orderbook
    "LIQUIDATION_BUFFER_LOW",   # Buffer 5-10%
    "LIQUIDATION_BUFFER_CRITICAL",  # Buffer < 5%
    "SLIPPAGE_HIGH",            # Expected slippage elevated

    # ── Sentiment ──
    "SENTIMENT_CROWDED_LONG",   # Long ratio > 0.60
    "SENTIMENT_CROWDED_SHORT",  # Short ratio > 0.60
    "SENTIMENT_EXTREME",        # Either ratio > 0.70
    "SENTIMENT_NEUTRAL",        # Both ratios <= 0.60, balanced sentiment

    # ── Fear & Greed (v44.0, contrarian signals) ──
    "EXTREME_FEAR",             # Fear & Greed Index < 20 (contrarian bullish)
    "EXTREME_GREED",            # Fear & Greed Index > 80 (contrarian bearish)

    # ── Memory Lesson Tags (used in _memory[].key_lesson_tags) ──
    "LATE_ENTRY",               # Entered after optimal timing window
    "EARLY_ENTRY",              # Entered before confirmation
    "TREND_ALIGNED",            # Trade was aligned with 1D trend
    "COUNTER_TREND_WIN",        # Counter-trend trade that succeeded
    "COUNTER_TREND_LOSS",       # Counter-trend trade that failed
    "SL_TOO_TIGHT",             # Stop loss triggered prematurely
    "SL_TOO_WIDE",              # Stop loss allowed excessive loss
    "TP_TOO_GREEDY",            # Take profit never reached, reversed
    "WRONG_DIRECTION",          # Fundamentally wrong direction call
    "CORRECT_THESIS",           # Analysis was correct, execution good
    "OVEREXTENDED_ENTRY",       # Entered at extension extreme
    "FR_IGNORED",               # Ignored funding rate pressure
    "LOW_VOLUME_ENTRY",         # Entered on thin volume/liquidity
    "DIVERGENCE_CONFIRMED",     # Divergence signal confirmed by price
}
