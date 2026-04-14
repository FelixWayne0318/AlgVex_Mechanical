"""
Shared business logic constants and pure functions — Single Source of Truth (SSoT).

This module centralises logic that was previously copy-pasted across production
code, diagnostic scripts, and verification scripts.  Every site that needs this
logic MUST import from here; never re-implement locally.

Usage:
    from utils.shared_logic import (
        classify_extension_regime,
        classify_volatility_regime,
        calculate_cvd_trend,
        EXTENSION_THRESHOLDS,
        VOLATILITY_REGIME_THRESHOLDS,
        CVD_TREND_RELATIVE_THRESHOLD,
        CVD_TREND_ABSOLUTE_FLOOR,
        CVD_TREND_MIN_BARS,
    )

Dependency map (update when adding importers):
    classify_extension_regime:
        - indicators/technical_manager.py  (_calculate_extension_ratios)
        - scripts/verify_extension_ratio.py
    classify_volatility_regime:
        - indicators/technical_manager.py  (_calculate_atr_regime)
    calculate_cvd_trend:
        - utils/order_flow_processor.py    (_calculate_cvd_trend)
        - scripts/verify_indicators.py     (ReferenceCalculations.cvd_trend)
        - scripts/validate_data_pipeline.py (test_cvd_trend)
"""

from typing import Dict, List

# ──────────────────────────────────────────────────────────
# ATR Extension Ratio thresholds  (v19.1)
# Domain-knowledge constants — NOT configurable via YAML.
# ──────────────────────────────────────────────────────────

EXTENSION_THRESHOLDS: Dict[str, float] = {
    "EXTREME": 5.0,
    "OVEREXTENDED": 3.0,
    "EXTENDED": 2.0,
    "NORMAL": 0.0,
}


# ──────────────────────────────────────────────────────────
# ATR Volatility Regime thresholds  (v20.0)
# Percentile-based regime — NOT configurable via YAML.
# Reference: TradingView ATRP Percentile Zones, QuantMonitor
# ──────────────────────────────────────────────────────────

VOLATILITY_REGIME_THRESHOLDS: Dict[str, float] = {
    "EXTREME": 90.0,   # >90th percentile
    "HIGH": 70.0,      # 70-90th percentile
    "LOW": 30.0,       # <30th percentile
    # 30-70th → NORMAL (implicit)
}


def classify_volatility_regime(percentile: float) -> str:
    """Classify ATR volatility regime from percentile rank.

    Args:
        percentile: ATR% percentile rank (0-100).

    Returns:
        One of 'EXTREME', 'HIGH', 'NORMAL', 'LOW'.
    """
    if percentile >= VOLATILITY_REGIME_THRESHOLDS["EXTREME"]:
        return "EXTREME"
    if percentile >= VOLATILITY_REGIME_THRESHOLDS["HIGH"]:
        return "HIGH"
    if percentile >= VOLATILITY_REGIME_THRESHOLDS["LOW"]:
        return "NORMAL"
    return "LOW"


def classify_extension_regime(primary_ratio: float) -> str:
    """Classify extension regime from primary ATR extension ratio.

    Args:
        primary_ratio: Absolute ATR extension ratio (non-negative).

    Returns:
        One of 'EXTREME', 'OVEREXTENDED', 'EXTENDED', 'NORMAL'.
    """
    ratio = abs(primary_ratio)
    if ratio >= EXTENSION_THRESHOLDS["EXTREME"]:
        return "EXTREME"
    if ratio >= EXTENSION_THRESHOLDS["OVEREXTENDED"]:
        return "OVEREXTENDED"
    if ratio >= EXTENSION_THRESHOLDS["EXTENDED"]:
        return "EXTENDED"
    return "NORMAL"


# ──────────────────────────────────────────────────────────
# CVD Trend calculation  (H6 fix — absolute threshold)
# ──────────────────────────────────────────────────────────

CVD_TREND_MIN_BARS: int = 5
CVD_TREND_COMPARE_BARS: int = 10
CVD_TREND_RELATIVE_THRESHOLD: float = 0.1   # 10 % of |avg_older|
CVD_TREND_ABSOLUTE_FLOOR: float = 1.0       # minimum threshold


def calculate_cvd_trend(cvd_history: List[float]) -> str:
    """Classify CVD trend as RISING / FALLING / NEUTRAL.

    Uses an absolute-symmetric threshold so negative CVD values are handled
    correctly (H6 fix).

    Args:
        cvd_history: Chronological list of cumulative volume delta values.

    Returns:
        One of 'RISING', 'FALLING', 'NEUTRAL'.
    """
    if len(cvd_history) < CVD_TREND_MIN_BARS:
        return "NEUTRAL"

    recent = cvd_history[-CVD_TREND_MIN_BARS:]
    avg_recent = sum(recent) / len(recent)

    if len(cvd_history) >= CVD_TREND_COMPARE_BARS:
        older = cvd_history[-CVD_TREND_COMPARE_BARS:-CVD_TREND_MIN_BARS]
        avg_older = sum(older) / len(older)

        threshold = max(
            abs(avg_older) * CVD_TREND_RELATIVE_THRESHOLD,
            CVD_TREND_ABSOLUTE_FLOOR,
        )
        if avg_recent > avg_older + threshold:
            return "RISING"
        if avg_recent < avg_older - threshold:
            return "FALLING"

    return "NEUTRAL"
