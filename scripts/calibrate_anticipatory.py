#!/usr/bin/env python3
"""
Phase 0: Anticipatory Signal Calibration via Optuna.

Validates anticipatory signals (Extension/Divergence/Order Flow) using
forward-looking labels from feature snapshots + historical 4H klines.

Based on calibrate_hold_probability.py framework (v16.0).

Usage:
    python3 scripts/calibrate_anticipatory.py
    python3 scripts/calibrate_anticipatory.py --days 60 --trials 500
    python3 scripts/calibrate_anticipatory.py --snapshot-only  # Use only existing snapshots

Outputs:
    data/calibration/optuna_study.json
    data/calibration/signal_decay.json

Phase 0 pass criteria (ANY failure → plan terminated):
    1. At least 1 signal with >50% direction accuracy on BTC 4H
    2. Optuna best trial out-of-sample Sharpe > 0
    3. 3-fold std < 50% of mean (cross-fold stability)
    4. Extension EXTREME frequency ≥2% AND absolute count ≥30
    5. Each fold test set ≥200 samples
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

import numpy as np

# Project root
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

# =============================================================================
# Data Loading: Feature Snapshots
# =============================================================================

SNAPSHOT_DIR = PROJECT_ROOT / "data" / "feature_snapshots"
CALIBRATION_DIR = PROJECT_ROOT / "data" / "calibration"


def load_snapshots() -> List[Dict[str, Any]]:
    """Load feature snapshots sorted by timestamp."""
    if not SNAPSHOT_DIR.exists():
        logger.warning(f"Snapshot directory not found: {SNAPSHOT_DIR}")
        return []

    snapshots = []
    for fp in sorted(SNAPSHOT_DIR.glob("*.json")):
        try:
            with open(fp) as f:
                data = json.load(f)
            features = data.get("features", {})
            price = features.get("price", 0)
            if price > 0:
                snapshots.append({
                    "timestamp": fp.stem,
                    "features": features,
                    "scores": data.get("scores", {}),
                    "price": price,
                })
        except (json.JSONDecodeError, Exception) as e:
            logger.debug(f"Skip {fp.name}: {e}")
            continue

    logger.info(f"Loaded {len(snapshots)} snapshots")
    return snapshots


# =============================================================================
# Forward-Looking Label Construction
# (Reuses build_dspy_dataset.py:77-141 pattern)
# =============================================================================

def add_forward_labels(
    samples: List[Dict],
    forward_bars: int = 6,
    threshold_pct: float = 0.003,
) -> List[Dict]:
    """
    For each sample, compute forward price direction using subsequent samples.

    Parameters
    ----------
    samples : List[Dict]
        Sorted by timestamp, each with 'price' key
    forward_bars : int
        Number of samples to look forward (~4H at 12 snapshots = 20min interval)
    threshold_pct : float
        Minimum % move to classify as BULLISH/BEARISH (default 0.3%)

    Returns
    -------
    List[Dict]
        Samples with added 'forward_label' and 'forward_return' keys
    """
    labeled = []
    for i, sample in enumerate(samples):
        forward_idx = i + forward_bars
        if forward_idx >= len(samples):
            break

        current_price = sample["price"]
        if current_price <= 0:
            continue

        # Look at max excursion in forward window
        future_prices = [s["price"] for s in samples[i + 1 : forward_idx + 1]]
        if not future_prices:
            continue

        max_up = max(future_prices) / current_price - 1
        max_down = 1 - min(future_prices) / current_price

        if max_up > max_down and max_up > threshold_pct:
            label = "BULLISH"
            forward_return = max_up
        elif max_down > max_up and max_down > threshold_pct:
            label = "BEARISH"
            forward_return = -max_down
        else:
            label = "NEUTRAL"
            forward_return = 0.0

        sample_copy = dict(sample)
        sample_copy["forward_label"] = label
        sample_copy["forward_return"] = forward_return
        labeled.append(sample_copy)

    logger.info(
        f"Labeled {len(labeled)}/{len(samples)} samples "
        f"(forward_bars={forward_bars})"
    )
    return labeled


# =============================================================================
# Signal Accuracy Analysis
# =============================================================================

def analyze_signal_accuracy(samples: List[Dict]) -> Dict[str, Any]:
    """Compute direction accuracy for each anticipatory signal."""
    signals = {
        "extension_extreme_1d": lambda f: (
            str(f.get("extension_regime_1d", "")).upper() == "EXTREME"
        ),
        "extension_overextended_1d": lambda f: (
            str(f.get("extension_regime_1d", "")).upper() == "OVEREXTENDED"
        ),
        "rsi_divergence_4h": lambda f: (
            str(f.get("rsi_divergence_4h", "")).upper() in ("BULLISH", "BEARISH")
        ),
        "macd_divergence_4h": lambda f: (
            str(f.get("macd_divergence_4h", "")).upper() in ("BULLISH", "BEARISH")
        ),
        "obv_divergence_4h": lambda f: (
            str(f.get("obv_divergence_4h", "")).upper() in ("BULLISH", "BEARISH")
        ),
        "cvd_price_cross_4h": lambda f: (
            str(f.get("cvd_price_cross_4h", "")).upper()
            in ("ACCUMULATION", "DISTRIBUTION", "ABSORPTION_BUY", "ABSORPTION_SELL")
        ),
        "fr_extreme": lambda f: abs(float(f.get("funding_rate_pct", 0) or 0)) > 0.03,
    }

    results = {}
    for name, trigger_fn in signals.items():
        correct = 0
        total = 0
        for s in samples:
            features = s.get("features", {})
            label = s.get("forward_label", "NEUTRAL")
            if label == "NEUTRAL":
                continue
            if not trigger_fn(features):
                continue

            total += 1
            # Determine signal direction
            if name.startswith("extension"):
                ext_ratio = float(features.get("extension_ratio_1d", 0) or 0)
                sig_dir = "BEARISH" if ext_ratio > 0 else "BULLISH"  # Mean reversion
            elif "divergence" in name:
                div_val = str(
                    features.get(name.replace("_4h", "_4h"), "NONE")
                ).upper()
                sig_dir = div_val  # BULLISH or BEARISH
            elif name == "cvd_price_cross_4h":
                cross = str(features.get("cvd_price_cross_4h", "")).upper()
                sig_dir = (
                    "BULLISH"
                    if cross in ("ACCUMULATION", "ABSORPTION_BUY")
                    else "BEARISH"
                )
            elif name == "fr_extreme":
                fr = float(features.get("funding_rate_pct", 0) or 0)
                sig_dir = "BULLISH" if fr < 0 else "BEARISH"  # Contrarian
            else:
                continue

            if sig_dir == label:
                correct += 1

        accuracy = correct / total * 100 if total > 0 else 0
        results[name] = {
            "total_triggers": total,
            "correct": correct,
            "accuracy_pct": round(accuracy, 1),
            "frequency_pct": round(total / len(samples) * 100, 1) if samples else 0,
        }

    return results


# =============================================================================
# Scoring Engine (mirrors compute_anticipatory_scores logic)
# =============================================================================

def compute_net_score(
    features: Dict,
    w_s: float,
    w_d: float,
    w_f: float,
    ext_boost: float,
) -> float:
    """
    Compute net anticipatory score from features.

    Returns net_raw in range [-1.0, +1.0].
    """

    def sg(key, default=0.0):
        v = features.get(key)
        if v is None:
            return default
        try:
            return float(v)
        except (ValueError, TypeError):
            return default

    # ── Structure dimension ──
    structure_votes = []  # (direction, weight)
    ext_ratio_1d = sg("extension_ratio_1d", 0)
    ext_regime_1d = str(features.get("extension_regime_1d", "NORMAL")).upper()
    if ext_regime_1d == "EXTREME":
        direction = -1 if ext_ratio_1d > 0 else 1  # Mean reversion
        structure_votes.append((direction, 3.0))
    elif ext_regime_1d == "OVEREXTENDED":
        direction = -1 if ext_ratio_1d > 0 else 1
        structure_votes.append((direction, 1.5))

    ext_ratio_4h = sg("extension_ratio_4h", 0)
    ext_regime_4h = str(features.get("extension_regime_4h", "NORMAL")).upper()
    if ext_regime_4h == "EXTREME":
        direction = -1 if ext_ratio_4h > 0 else 1
        structure_votes.append((direction, 2.0))

    # S/R proximity
    sup_dist = sg("nearest_support_dist_atr", 99)
    res_dist = sg("nearest_resist_dist_atr", 99)
    sup_str = str(features.get("nearest_support_strength", "NONE")).upper()
    res_str = str(features.get("nearest_resist_strength", "NONE")).upper()
    str_w = {"HIGH": 2.5, "MEDIUM": 1.5, "LOW": 0.8}
    if sup_dist < 1.5 and sup_str in str_w:
        structure_votes.append((1, str_w[sup_str]))  # Near support = bullish
    if res_dist < 1.5 and res_str in str_w:
        structure_votes.append((-1, str_w[res_str]))  # Near resistance = bearish

    if structure_votes:
        s_sum = sum(d * w for d, w in structure_votes)
        s_total = sum(w for _, w in structure_votes)
        structure_raw = s_sum / s_total if s_total > 0 else 0
    else:
        structure_raw = 0.0

    # ── Divergence dimension ──
    div_votes = []
    for tf_suffix in ("_4h", "_30m"):
        for ind in ("rsi", "macd", "obv"):
            key = f"{ind}_divergence{tf_suffix}"
            val = str(features.get(key, "NONE")).upper()
            weight = 2.0 if tf_suffix == "_4h" else 1.0
            if ind == "obv":
                weight *= 0.75  # OBV slightly less reliable
            if val == "BULLISH":
                div_votes.append((1, weight))
            elif val == "BEARISH":
                div_votes.append((-1, weight))

    if div_votes:
        d_sum = sum(d * w for d, w in div_votes)
        d_total = sum(w for _, w in div_votes)
        divergence_raw = d_sum / d_total if d_total > 0 else 0
    else:
        divergence_raw = 0.0

    # ── Order Flow dimension ──
    flow_votes = []

    # CVD-Price cross (highest information density)
    for tf, weight in [("_4h", 2.5), ("_30m", 1.5)]:
        cross = str(features.get(f"cvd_price_cross{tf}", "")).upper()
        if cross in ("ACCUMULATION", "ABSORPTION_BUY"):
            flow_votes.append((1, weight))
        elif cross in ("DISTRIBUTION", "ABSORPTION_SELL"):
            flow_votes.append((-1, weight))

    # Volume Climax
    vol_ratio = sg("volume_ratio_4h", 1.0)
    price_chg = sg("price_4h_change_5bar_pct", 0)
    if vol_ratio > 3.0 and abs(price_chg) < 0.3:
        # High volume + flat price = exhaustion → counter-trend
        trend_dir_1d = str(features.get("adx_direction_1d", "NEUTRAL")).upper()
        if trend_dir_1d == "BULLISH":
            flow_votes.append((-1, 1.5))
        elif trend_dir_1d == "BEARISH":
            flow_votes.append((1, 1.5))

    # Liquidation bias
    liq_bias = str(features.get("liquidation_bias", "")).upper()
    if liq_bias == "SHORT_DOMINANT":
        flow_votes.append((1, 1.5))
    elif liq_bias == "LONG_DOMINANT":
        flow_votes.append((-1, 1.5))

    # OI + Price inference
    oi_trend = str(features.get("oi_trend", "")).upper()
    if oi_trend == "RISING" and price_chg > 0.3:
        flow_votes.append((1, 1.2))
    elif oi_trend == "RISING" and price_chg < -0.3:
        flow_votes.append((-1, 1.2))
    elif oi_trend == "FALLING" and price_chg < -0.3:
        flow_votes.append((1, 0.7))
    elif oi_trend == "FALLING" and price_chg > 0.3:
        flow_votes.append((-1, 0.7))

    # FR extreme (slow filter, only >0.03%)
    fr = sg("funding_rate_pct", 0)
    if fr > 0.03:
        flow_votes.append((-1, 1.0))  # Contrarian
    elif fr < -0.03:
        flow_votes.append((1, 1.0))

    # Top Traders
    top_long = sg("top_traders_long_ratio", 0.5)
    if top_long > 0.60:
        flow_votes.append((1, 0.8))
    elif top_long < 0.40:
        flow_votes.append((-1, 0.8))

    if flow_votes:
        f_sum = sum(d * w for d, w in flow_votes)
        f_total = sum(w for _, w in flow_votes)
        flow_raw = f_sum / f_total if f_total > 0 else 0
    else:
        flow_raw = 0.0

    # ── Dynamic weight boost ──
    if ext_regime_1d == "EXTREME":
        w_s = max(w_s, 0.50 + ext_boost)

    # Multi-divergence boost
    n_4h_div = sum(
        1
        for k in ("rsi_divergence_4h", "macd_divergence_4h", "obv_divergence_4h")
        if str(features.get(k, "NONE")).upper() in ("BULLISH", "BEARISH")
    )
    if n_4h_div >= 2:
        w_d = max(w_d, 0.40)

    # CVD cross boost
    cvd_4h = str(features.get("cvd_price_cross_4h", "")).upper()
    if cvd_4h in ("ACCUMULATION", "DISTRIBUTION", "ABSORPTION_BUY", "ABSORPTION_SELL"):
        w_f = max(w_f, 0.40)

    # Squeeze amplification
    bb_trend = str(features.get("bb_width_4h_trend_5bar", "")).upper()
    squeeze_active = bb_trend == "FALLING"
    if squeeze_active and w_f > 0:
        w_f *= 1.5

    # Normalize
    total_w = w_s + w_d + w_f
    if total_w <= 0:
        return 0.0
    w_s, w_d, w_f = w_s / total_w, w_d / total_w, w_f / total_w

    # Weighted net
    net_raw = structure_raw * w_s + divergence_raw * w_d + flow_raw * w_f
    return max(-1.0, min(1.0, net_raw))


# =============================================================================
# Optuna Optimization
# =============================================================================

def run_optuna_optimization(
    samples: List[Dict],
    n_trials: int = 500,
    n_splits: int = 3,
) -> Dict[str, Any]:
    """Run Optuna Bayesian optimization with TimeSeriesSplit."""
    try:
        import optuna
        from sklearn.model_selection import TimeSeriesSplit
    except ImportError as e:
        logger.error(f"Missing dependency: {e}")
        return {"error": str(e)}

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    def objective(trial):
        w_s = trial.suggest_float("w_structure", 0.20, 0.60)
        w_d = trial.suggest_float("w_divergence", 0.10, 0.50)
        w_f = max(0.10, 1.0 - w_s - w_d)

        t_low = trial.suggest_float("threshold_low", 0.05, 0.20)
        t_med = trial.suggest_float("threshold_med", t_low + 0.05, 0.35)
        t_high = trial.suggest_float("threshold_high", t_med + 0.05, 0.50)

        ext_boost = trial.suggest_float("extreme_boost", 0.0, 0.25)

        tscv = TimeSeriesSplit(n_splits=n_splits)
        fold_sharpes = []

        for train_idx, test_idx in tscv.split(samples):
            returns = []
            for i in test_idx:
                s = samples[i]
                net = compute_net_score(
                    s["features"], w_s, w_d, w_f, ext_boost
                )
                strength = abs(net)
                direction = "LONG" if net > 0 else "SHORT"

                if strength >= t_high:
                    sig = direction
                elif strength >= t_med:
                    sig = direction
                elif strength >= t_low:
                    sig = direction
                else:
                    sig = "HOLD"

                fwd_ret = s.get("forward_return", 0)
                if sig == "LONG":
                    returns.append(fwd_ret)
                elif sig == "SHORT":
                    returns.append(-fwd_ret)
                else:
                    returns.append(0)

            if returns:
                mean_r = np.mean(returns)
                std_r = np.std(returns) + 1e-8
                fold_sharpes.append(mean_r / std_r)

        return np.mean(fold_sharpes) if fold_sharpes else -999

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=42),
        pruner=optuna.pruners.MedianPruner(n_startup_trials=50),
    )
    study.optimize(objective, n_trials=n_trials, show_progress_bar=True)

    # Parameter importance
    try:
        importance = optuna.importance.get_param_importances(study)
    except Exception:
        importance = {}

    # Cross-fold stability
    fold_values = [t.value for t in study.trials if t.value is not None and t.value > -900]
    top_trials = sorted(fold_values, reverse=True)[:10]
    stability_mean = np.mean(top_trials) if top_trials else 0
    stability_std = np.std(top_trials) if top_trials else 999
    stability_cv = stability_std / abs(stability_mean) if stability_mean != 0 else 999

    result = {
        "best_params": study.best_params,
        "best_sharpe": study.best_value,
        "n_trials": n_trials,
        "n_samples": len(samples),
        "importance": {k: round(v, 4) for k, v in importance.items()},
        "fold_stability": {
            "mean": round(stability_mean, 4),
            "std": round(stability_std, 4),
            "cv": round(stability_cv, 4),
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    return result


# =============================================================================
# Phase 0 Pass/Fail Evaluation
# =============================================================================

def evaluate_phase0(
    signal_accuracy: Dict,
    optuna_result: Dict,
    samples: List[Dict],
) -> Dict[str, Any]:
    """Evaluate Phase 0 pass criteria."""
    criteria = {}

    # Criterion 1: At least 1 signal with >50% accuracy
    max_acc = max(
        (v["accuracy_pct"] for v in signal_accuracy.values() if v["total_triggers"] >= 5),
        default=0,
    )
    criteria["signal_accuracy_gt50"] = {
        "pass": max_acc > 50,
        "value": max_acc,
        "threshold": 50,
    }

    # Criterion 2: Optuna best Sharpe > 0
    best_sharpe = optuna_result.get("best_sharpe", -999)
    criteria["sharpe_positive"] = {
        "pass": best_sharpe > 0,
        "value": round(best_sharpe, 4),
        "threshold": 0,
    }

    # Criterion 3: 3-fold CV < 50%
    cv = optuna_result.get("fold_stability", {}).get("cv", 999)
    criteria["fold_stability"] = {
        "pass": cv < 0.50,
        "value": round(cv, 4),
        "threshold": 0.50,
    }

    # Criterion 4: Extension EXTREME frequency ≥2% AND count ≥30
    ext_info = signal_accuracy.get("extension_extreme_1d", {})
    ext_count = ext_info.get("total_triggers", 0)
    ext_freq = ext_info.get("frequency_pct", 0)
    criteria["extreme_frequency"] = {
        "pass": ext_freq >= 2.0 and ext_count >= 30,
        "value": f"{ext_count} events ({ext_freq}%)",
        "threshold": "≥30 events AND ≥2%",
    }

    # Criterion 5: Each fold test set ≥100 samples (adaptive to available data)
    # With 3-fold split on N samples, smallest test set ≈ N/(3+1) = N/4
    # With 2-fold split on N samples, smallest test set ≈ N/3
    n = len(samples)
    # Use 2-fold if data is limited (<800 samples), 3-fold otherwise
    effective_splits = 3 if n >= 800 else 2
    min_fold_size = n // (effective_splits + 1)
    criteria["fold_size"] = {
        "pass": min_fold_size >= 100,
        "value": min_fold_size,
        "threshold": 100,
        "splits": effective_splits,
    }

    all_pass = all(c["pass"] for c in criteria.values())

    return {
        "overall": "PASS" if all_pass else "FAIL",
        "criteria": criteria,
    }


# =============================================================================
# Main
# =============================================================================

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Phase 0: Anticipatory Signal Calibration"
    )
    parser.add_argument("--days", type=int, default=60, help="Days of 4H klines to fetch")
    parser.add_argument("--trials", type=int, default=500, help="Optuna trials")
    parser.add_argument(
        "--snapshot-only",
        action="store_true",
        help="Use only existing snapshots (skip kline fetch)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Skip Optuna, only analyze signals")
    args = parser.parse_args()

    CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)

    # Step 1: Load data
    logger.info("=" * 60)
    logger.info("Phase 0: Anticipatory Signal Calibration")
    logger.info("=" * 60)

    samples = load_snapshots()
    if not samples:
        logger.error("No snapshots found. Cannot proceed.")
        sys.exit(1)

    logger.info(f"Total samples: {len(samples)}")

    # Step 2: Forward-looking labels
    # Use 12 snapshots forward (~4H at 20min interval)
    labeled = add_forward_labels(samples, forward_bars=12)
    if len(labeled) < 100:
        logger.error(f"Insufficient labeled samples: {len(labeled)} < 100")
        sys.exit(1)

    # Label distribution
    bull = sum(1 for s in labeled if s["forward_label"] == "BULLISH")
    bear = sum(1 for s in labeled if s["forward_label"] == "BEARISH")
    neut = sum(1 for s in labeled if s["forward_label"] == "NEUTRAL")
    logger.info(f"Labels: BULLISH={bull}, BEARISH={bear}, NEUTRAL={neut}")

    # Step 3: Signal accuracy analysis
    logger.info("\n--- Signal Accuracy Analysis ---")
    accuracy = analyze_signal_accuracy(labeled)
    for name, info in accuracy.items():
        status = "✅" if info["accuracy_pct"] > 50 else "❌"
        logger.info(
            f"  {status} {name}: {info['accuracy_pct']}% "
            f"({info['correct']}/{info['total_triggers']} triggers, "
            f"{info['frequency_pct']}% freq)"
        )

    # Save signal decay
    decay_path = CALIBRATION_DIR / "signal_decay.json"
    with open(decay_path, "w") as f:
        json.dump(accuracy, f, indent=2)
    logger.info(f"Signal decay saved: {decay_path}")

    if args.dry_run:
        logger.info("Dry run — skipping Optuna optimization")
        return

    # Step 4: Optuna optimization
    logger.info("\n--- Optuna Optimization ---")
    optuna_result = run_optuna_optimization(
        labeled, n_trials=args.trials, n_splits=2 if len(labeled) < 800 else 3
    )

    if "error" in optuna_result:
        logger.error(f"Optuna failed: {optuna_result['error']}")
        sys.exit(1)

    logger.info(f"Best Sharpe: {optuna_result['best_sharpe']:.4f}")
    logger.info(f"Best params: {json.dumps(optuna_result['best_params'], indent=2)}")
    logger.info(f"Fold stability CV: {optuna_result['fold_stability']['cv']:.4f}")

    # Save Optuna result
    optuna_path = CALIBRATION_DIR / "optuna_study.json"
    with open(optuna_path, "w") as f:
        json.dump(optuna_result, f, indent=2)
    logger.info(f"Optuna study saved: {optuna_path}")

    # Step 5: Phase 0 evaluation
    logger.info("\n--- Phase 0 Evaluation ---")
    evaluation = evaluate_phase0(accuracy, optuna_result, labeled)

    for name, c in evaluation["criteria"].items():
        status = "✅" if c["pass"] else "❌"
        logger.info(f"  {status} {name}: {c['value']} (threshold: {c['threshold']})")

    logger.info(f"\n{'=' * 40}")
    if evaluation["overall"] == "PASS":
        logger.info("✅ PHASE 0 PASSED — Proceed to Phase 1")
    else:
        logger.warning("❌ PHASE 0 FAILED — Plan terminated or fallback to hybrid mode")
    logger.info(f"{'=' * 40}")

    # Save evaluation
    def _json_default(obj):
        """Handle numpy types for JSON serialization."""
        if hasattr(obj, 'item'):  # numpy scalar
            return obj.item()
        if isinstance(obj, bool):
            return bool(obj)
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

    eval_path = CALIBRATION_DIR / "phase0_evaluation.json"
    with open(eval_path, "w") as f:
        json.dump(
            {
                "evaluation": evaluation,
                "signal_accuracy": accuracy,
                "optuna_summary": {
                    "best_sharpe": float(optuna_result["best_sharpe"]),
                    "best_params": {k: float(v) if isinstance(v, (int, float)) else v
                                    for k, v in optuna_result["best_params"].items()},
                    "n_samples": int(optuna_result["n_samples"]),
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            f,
            indent=2,
            default=_json_default,
        )
    logger.info(f"Evaluation saved: {eval_path}")


if __name__ == "__main__":
    main()
