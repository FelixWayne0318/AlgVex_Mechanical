"""
Pandera-based data validation for the trading pipeline.

Feature-level validation for the 141-feature dict after extraction.
"""

import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

# =============================================================================
# Tier 2: Feature-Level Validation
# =============================================================================

# Range constraints for key features (field_name → (min, max))
_FEATURE_BOUNDS = {
    # RSI: 0-100 all timeframes
    "rsi_30m": (0.0, 100.0),
    "rsi_4h": (0.0, 100.0),
    "rsi_1d": (0.0, 100.0),
    # ADX: 0-100 all timeframes
    "adx_30m": (0.0, 100.0),
    "adx_4h": (0.0, 100.0),
    "adx_1d": (0.0, 100.0),
    # DI: 0-100
    "di_plus_30m": (0.0, 100.0),
    "di_minus_30m": (0.0, 100.0),
    "di_plus_4h": (0.0, 100.0),
    "di_minus_4h": (0.0, 100.0),
    "di_plus_1d": (0.0, 100.0),
    "di_minus_1d": (0.0, 100.0),
    # BB position: 0-1 (can slightly exceed due to extreme moves)
    "bb_position_30m": (-0.5, 1.5),
    "bb_position_4h": (-0.5, 1.5),
    # Volume ratio: positive
    "volume_ratio_30m": (0.0, 100.0),
    "volume_ratio_4h": (0.0, 100.0),
    "volume_ratio_1d": (0.0, 100.0),
    # Buy ratio: 0-1
    "buy_ratio_30m": (0.0, 1.0),
    # Sentiment
    "long_ratio": (0.0, 1.0),
    "short_ratio": (0.0, 1.0),
    # Funding rate: realistic range
    "funding_rate_pct": (-1.0, 1.0),
}

# Enum constraints for key features
_FEATURE_ENUMS = {
    "extension_regime_30m": {"NORMAL", "EXTENDED", "OVEREXTENDED", "EXTREME"},
    "extension_regime_4h": {"NORMAL", "EXTENDED", "OVEREXTENDED", "EXTREME"},
    "extension_regime_1d": {"NORMAL", "EXTENDED", "OVEREXTENDED", "EXTREME"},
    "volatility_regime_30m": {"LOW", "NORMAL", "HIGH", "EXTREME"},
    "volatility_regime_4h": {"LOW", "NORMAL", "HIGH", "EXTREME"},
    "volatility_regime_1d": {"LOW", "NORMAL", "HIGH", "EXTREME"},
    "cvd_trend_30m": {"RISING", "FALLING", "NEUTRAL", None},
    "cvd_trend_4h": {"RISING", "FALLING", "NEUTRAL", None},
    "adx_direction_1d": {"BULLISH", "BEARISH", "NEUTRAL"},
    "market_regime": {"STRONG_TREND", "WEAK_TREND", "RANGING"},
}


def validate_features(features: Dict[str, Any]) -> List[str]:
    """Tier 2: Validate extracted feature dict against known bounds.

    Returns list of warning strings (empty = all valid).
    Does NOT modify the features dict — caller decides how to handle warnings.
    """
    import math
    warnings = []

    # Price must be positive
    price = features.get("price", 0)
    if not (isinstance(price, (int, float)) and price > 0):
        warnings.append(f"price={price} invalid")

    # Range checks
    for field, (lo, hi) in _FEATURE_BOUNDS.items():
        val = features.get(field)
        if val is None:
            continue
        if not isinstance(val, (int, float)):
            continue
        if math.isnan(val) or math.isinf(val):
            warnings.append(f"{field}={val} is NaN/inf")
        elif not (lo <= val <= hi):
            warnings.append(f"{field}={val} out of [{lo},{hi}]")

    # Enum checks
    for field, valid_set in _FEATURE_ENUMS.items():
        val = features.get(field)
        if val is None and None in valid_set:
            continue
        if val is not None and val not in valid_set:
            warnings.append(f"{field}={val!r} not in {valid_set}")

    if warnings:
        logger.warning(f"⚠️ Feature validation ({len(warnings)} issues): {'; '.join(warnings[:5])}")

    return warnings


