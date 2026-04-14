"""
v49.0 Hybrid Mechanical Decision Function.

Primary entry signal: net_raw threshold from 3-dimension anticipatory scores.
Zone conditions serve as confidence modifier (not gate).

Decision tiers:
  - |net_raw| >= high_threshold (0.45): signal + HIGH confidence
  - |net_raw| >= med_threshold  (0.35): signal + MEDIUM confidence
  - |net_raw| >= low_threshold  (0.20) + zone>=1: signal + LOW confidence
  - else: HOLD

Zone conditions (confidence boost, not gate):
  1. 4H Extension EXTENDED+ (24% trigger rate)
  2. RSI oversold/overbought (82% trigger rate)
  3. CVD Accumulation/Distribution (43% trigger rate)
  4. S/R proximity (relaxed to 3 ATR)

Data-verified: net_raw >= 0.35 has 60.6% directional accuracy at 2h horizon
with +0.16% mean return (500 snapshot backtest, 2026-03-19 to 2026-03-31).

DCA managed downstream (order_execution + on_bar).
Direction lock state persisted across restarts.
"""

import json
import logging
import os
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_MECH_STATE_FILE = "data/mechanical_state.json"


def _downgrade(confidence: str) -> Optional[str]:
    """Downgrade confidence by one level. Returns None if already LOW."""
    return {"HIGH": "MEDIUM", "MEDIUM": "LOW"}.get(confidence)


def _upgrade(confidence: str) -> str:
    """Upgrade confidence by one level. Caps at HIGH."""
    return {"LOW": "MEDIUM", "MEDIUM": "HIGH"}.get(confidence, confidence)


# Direction lock state (module-level)
_direction_sl_count: Dict[str, int] = {"LONG": 0, "SHORT": 0}


def load_direction_lock_state() -> None:
    """Load direction lock counters from data/mechanical_state.json."""
    global _direction_sl_count
    try:
        if os.path.exists(_MECH_STATE_FILE):
            with open(_MECH_STATE_FILE, "r") as f:
                state = json.load(f)
            counts = state.get("direction_sl_count", {})
            _direction_sl_count["LONG"] = int(counts.get("LONG", 0))
            _direction_sl_count["SHORT"] = int(counts.get("SHORT", 0))
            logger.info(
                f"Loaded direction lock: LONG={_direction_sl_count['LONG']}, "
                f"SHORT={_direction_sl_count['SHORT']}"
            )
    except Exception as e:
        logger.warning(f"Failed to load mechanical state: {e}")


def save_direction_lock_state() -> None:
    """Persist direction lock counters."""
    try:
        os.makedirs(os.path.dirname(_MECH_STATE_FILE), exist_ok=True)
        with open(_MECH_STATE_FILE, "w") as f:
            json.dump({"direction_sl_count": dict(_direction_sl_count)}, f)
    except Exception as e:
        logger.warning(f"Failed to save mechanical state: {e}")


def reset_direction_lock(direction: str) -> None:
    """Reset direction lock counter on successful trade."""
    if direction in _direction_sl_count:
        _direction_sl_count[direction] = 0
        save_direction_lock_state()
        logger.info(f"Direction lock reset for {direction}")


def record_stoploss(direction: str) -> None:
    """Record a stoploss event for direction locking."""
    if direction in _direction_sl_count:
        _direction_sl_count[direction] += 1
        save_direction_lock_state()
        logger.info(
            f"Direction SL count: {direction}={_direction_sl_count[direction]}"
        )


def _direction_locked(signal: str, threshold: int = 2) -> bool:
    """Check if a direction is locked due to consecutive SL."""
    if signal in ("LONG", "SHORT"):
        return _direction_sl_count.get(signal, 0) >= threshold
    return False


def mechanical_decide(
    scores: Dict[str, Any],
    features: Dict[str, Any],
    regime_config: Dict[str, Any],
    direction_lock_threshold: int = 2,
) -> Tuple[str, str, int, str, str]:
    """
    v49.0 Hybrid decision: net_raw threshold + zone confirmation.

    Primary signal from net_raw (3-dimension anticipatory scores).
    Zone conditions modify confidence, not gate entry.

    Decision tiers (data-verified on 500 snapshots):
      |net_raw| >= 0.45               → HIGH confidence
      |net_raw| >= 0.35               → MEDIUM confidence
      |net_raw| >= 0.20 + zone >= 1   → LOW confidence
      |net_raw| < 0.20                → HOLD

    Zone conditions (confidence boost):
      1. 4H Extension EXTENDED+ (24% trigger rate)
      2. RSI oversold/overbought (82% trigger rate)
      3. CVD Accumulation/Distribution (43% trigger rate)
      4. S/R proximity < 3 ATR

    Returns
    -------
    Tuple[signal, confidence, size_pct, risk_appetite, hold_source]
    """
    hold_source = ""
    net_raw = scores.get("anticipatory_raw", 0.0)
    regime = scores.get("regime", "DEFAULT")

    # Threshold config: per-regime thresholds take priority (Optuna calibrated),
    # then flat zone_entry thresholds, then hardcoded defaults.
    zone_cfg = regime_config.get("_zone_entry", {})
    _regime_cfg = regime_config.get(regime, {})
    _regime_thresholds = _regime_cfg.get("thresholds", {})
    high_threshold = _regime_thresholds.get("high", zone_cfg.get("high_threshold", 0.45))
    med_threshold = _regime_thresholds.get("med", zone_cfg.get("med_threshold", 0.35))
    low_threshold = _regime_thresholds.get("low", zone_cfg.get("low_threshold", 0.20))
    long_only = zone_cfg.get("long_only_default", True)

    # RSI thresholds for zone conditions
    rsi_oversold_30m = zone_cfg.get("rsi_oversold_30m", 50)
    rsi_oversold_4h = zone_cfg.get("rsi_oversold_4h", 45)
    rsi_overbought_30m = zone_cfg.get("rsi_overbought_30m", 70)
    rsi_overbought_4h = zone_cfg.get("rsi_overbought_4h", 80)
    sr_proximity = zone_cfg.get("support_proximity_atr", 3.0)

    _ext_levels = {"EXTENDED": 1, "OVEREXTENDED": 2, "EXTREME": 3}

    # ========================================
    # Step 1: Detect zone conditions (confidence modifier)
    # ========================================
    long_conds = []
    short_conds = []

    # Condition 1: 4H Extension EXTENDED+ (24% trigger, 1D excluded — always EXTREME)
    ext_4h = str(features.get("extension_regime_4h", "NORMAL")).upper()
    ext_ratio_4h = features.get("extension_ratio_4h")  # None = data absent
    if _ext_levels.get(ext_4h, 0) >= 1 and ext_ratio_4h is not None:
        _ext_ratio_f = _safe_float(ext_ratio_4h, 0)
        if _ext_ratio_f < 0:
            long_conds.append("EXT")
        elif _ext_ratio_f > 0:
            short_conds.append("EXT")

    # Condition 2: RSI oversold/overbought
    rsi_30m = _safe_float(features.get("rsi_30m", 50), 50)
    rsi_4h = _safe_float(features.get("rsi_4h", 50), 50)
    if rsi_30m < rsi_oversold_30m or rsi_4h < rsi_oversold_4h:
        long_conds.append("RSI")
    if rsi_30m > rsi_overbought_30m or rsi_4h > rsi_overbought_4h:
        short_conds.append("RSI")

    # Condition 3: CVD Accumulation/Distribution
    cvd_30m = str(features.get("cvd_price_cross_30m", "")).upper()
    cvd_4h = str(features.get("cvd_price_cross_4h", "")).upper()
    if cvd_30m in ("ACCUMULATION", "ABSORPTION_BUY") or cvd_4h in (
        "ACCUMULATION", "ABSORPTION_BUY",
    ):
        long_conds.append("CVD")
    if cvd_30m in ("DISTRIBUTION", "ABSORPTION_SELL", "CONFIRMED_SELL") or cvd_4h in (
        "DISTRIBUTION", "ABSORPTION_SELL", "CONFIRMED_SELL",
    ):
        short_conds.append("CVD")

    # Condition 4: Near S/R
    sup_dist = _safe_float(features.get("nearest_support_dist_atr", 99), 99)
    res_dist = _safe_float(features.get("nearest_resist_dist_atr", 99), 99)
    sup_str = str(features.get("nearest_support_strength", "NONE")).upper()
    res_str = str(features.get("nearest_resist_strength", "NONE")).upper()
    if sup_dist < sr_proximity and sup_str in ("HIGH", "MEDIUM", "LOW"):
        long_conds.append("S/R")
    if res_dist < sr_proximity and res_str in ("HIGH", "MEDIUM", "LOW"):
        short_conds.append("S/R")

    # ========================================
    # Step 2: net_raw → signal + confidence
    # ========================================
    abs_raw = abs(net_raw)
    signal = "HOLD"
    confidence = "LOW"

    # Determine direction from net_raw sign (0.0 = no direction → HOLD)
    if net_raw > 0:
        raw_signal = "LONG"
        n_zone = len(long_conds)
    elif net_raw < 0:
        raw_signal = "SHORT"
        n_zone = len(short_conds)
    else:
        raw_signal = "HOLD"
        n_zone = 0

    # Tier 1: Strong signal — no zone required
    if abs_raw >= high_threshold:
        signal = raw_signal
        confidence = "HIGH"
    # Tier 2: Medium signal — no zone required
    elif abs_raw >= med_threshold:
        signal = raw_signal
        confidence = "MEDIUM"
    # Tier 3: Weak signal — needs zone confirmation
    elif abs_raw >= low_threshold and n_zone >= 1:
        signal = raw_signal
        confidence = "LOW"
    else:
        hold_source = "weak_signal" if abs_raw >= low_threshold else "below_threshold"

    # Zone boost: upgrade confidence if zones strongly confirm
    if signal != "HOLD" and n_zone >= 3:
        confidence = _upgrade(confidence)
    elif signal != "HOLD" and n_zone >= 2 and confidence == "LOW":
        confidence = "MEDIUM"

    # SHORT gate: in long_only mode, SHORT needs MEDIUM+ confidence
    if signal == "SHORT" and long_only and confidence == "LOW":
        signal = "HOLD"
        hold_source = "short_low_conf"

    # ========================================
    # Step 3: Safety corrections
    # ========================================
    # Direction lock
    if signal != "HOLD" and _direction_locked(signal, direction_lock_threshold):
        logger.info(
            f"Direction locked: {signal} "
            f"(SL count={_direction_sl_count.get(signal, 0)})"
        )
        signal, confidence = "HOLD", "LOW"
        hold_source = "direction_lock"

    # Counter strong-trend downgrade
    trend_ctx = scores.get("trend_context", "NEUTRAL")
    adx = _safe_float(features.get("adx_1d", 0), 0)
    if signal != "HOLD" and trend_ctx == "OPPOSING" and adx >= 40:
        new_conf = _downgrade(confidence)
        if new_conf is None:
            signal, confidence = "HOLD", "LOW"
            hold_source = "counter_trend"
        else:
            confidence = new_conf

    # ========================================
    # Step 4: CLOSE detection + sizing
    # ========================================
    position_side = str(features.get("position_side", "FLAT")).upper()
    if position_side != "FLAT" and signal not in ("HOLD",):
        opposite = (position_side == "LONG" and signal == "SHORT") or (
            position_side == "SHORT" and signal == "LONG"
        )
        if opposite and confidence in ("HIGH", "MEDIUM"):
            signal = "CLOSE"
        elif opposite:
            signal = "HOLD"
            hold_source = "weak_reversal"

    # DCA base sizing
    size_pct = 100

    risk_score = scores.get("risk_env", {}).get("score", 0)
    if risk_score >= 6:
        risk_appetite = "CONSERVATIVE"
    elif risk_score <= 2:
        risk_appetite = "AGGRESSIVE"
    else:
        risk_appetite = "NORMAL"

    conds_str = "+".join(long_conds) if long_conds else "none"
    s_conds_str = "+".join(short_conds) if short_conds else "none"
    logger.info(
        f"Hybrid decision: {signal} {confidence} "
        f"(raw={net_raw:.3f} |raw|={abs_raw:.3f} "
        f"L_zone={len(long_conds)}[{conds_str}] S_zone={len(short_conds)}[{s_conds_str}] "
        f"rsi30m={rsi_30m:.0f} rsi4h={rsi_4h:.0f} "
        f"ext4h={ext_4h} regime={regime})"
    )

    return signal, confidence, size_pct, risk_appetite, hold_source


def _safe_float(val, default: float) -> float:
    """Safely convert to float."""
    if val is None:
        return default
    try:
        v = float(val)
        return v if v == v else default  # NaN check
    except (ValueError, TypeError):
        return default
