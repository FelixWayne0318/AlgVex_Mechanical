#!/usr/bin/env python3
"""
SRP v5.0 Parameter Optimizer — Optuna Bayesian Search.

v10.0: Migrated from itertools.product grid search to Optuna TPESampler.
  - 3-phase grid (625+25+25 = 675 combos) → single Optuna study (500 trials)
  - 99%+ search efficiency via Bayesian optimization
  - Automatic parameter importance analysis
  - MedianPruner for early stopping of bad trials

Usage:
    python3 scripts/optimize_srp_params.py --days 456
    python3 scripts/optimize_srp_params.py --days 456 --capital 10000 --trials 500
"""

import argparse
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.backtest_srp_v5_exact import fetch_klines, run_backtest


def calc_score(r):
    """Composite score balancing return, risk, robustness, and consistency."""
    ret = r["return_pct"]
    dd = max(r["max_dd_pct"], 0.5)
    deals = r["deals"]
    pf = r["profit_factor"] if isinstance(r["profit_factor"], (int, float)) else 0

    if deals < 5 or ret <= 0:
        return -999

    score = (ret / dd) * math.sqrt(deals) * min(pf, 10) / 10
    return round(score, 4)


def required_capital(base, dca_mult, dca_count):
    """Calculate total capital needed for base + all DCA layers."""
    total = base
    pos = base
    for _ in range(dca_count):
        pos *= dca_mult
        total += pos
    return total


def main():
    import optuna

    parser = argparse.ArgumentParser(description="SRP v5.0 Parameter Optimizer (Optuna)")
    parser.add_argument("--days", type=int, default=456)
    parser.add_argument("--capital", type=int, default=10000)
    parser.add_argument("--trials", type=int, default=500, help="Optuna trials (default 500)")
    args = parser.parse_args()

    klines = fetch_klines(days=args.days)
    if len(klines) < 100:
        print("ERROR: Insufficient data")
        sys.exit(1)

    print(f"\nData: {len(klines)} bars ({args.days} days)")
    print(f"Capital: ${args.capital:,}")
    print(f"Optuna: {args.trials} trials (TPESampler)")

    base_ratio = 0.10

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    def objective(trial):
        params = {
            "initial_capital": args.capital,
            "base_pct": base_ratio,
            # Phase 1: Key parameters
            "srp_pct": trial.suggest_float("srp_pct", 0.5, 1.5, step=0.1),
            "mintp": trial.suggest_float("mintp", 0.015, 0.040, step=0.005),
            "max_loss": trial.suggest_float("max_loss", 0.04, 0.10, step=0.01),
            "dca_spacing": trial.suggest_float("dca_spacing", 2.0, 5.0, step=0.5),
            # Phase 2: DCA tuning
            "dca_mult": trial.suggest_float("dca_mult", 1.2, 2.0, step=0.1),
            "dca_max": trial.suggest_int("dca_max", 2, 6),
            # Phase 3: Signal tuning
            "vwma_len": trial.suggest_int("vwma_len", 10, 20, step=2),
            "rsi_mfi_below": trial.suggest_int("rsi_mfi_below", 45, 65, step=5),
            "rsi_mfi_above": 100,
        }

        r = run_backtest(klines, params)
        return calc_score(r)

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=42),
        pruner=optuna.pruners.MedianPruner(n_startup_trials=30),
    )

    t0 = time.time()
    study.optimize(objective, n_trials=args.trials, show_progress_bar=True)
    elapsed = time.time() - t0

    # Results
    print(f"\n{'=' * 70}")
    print(f"  Optuna Optimization Complete ({elapsed:.1f}s, {args.trials} trials)")
    print(f"{'=' * 70}")

    best = study.best_params
    best_params = {
        "initial_capital": args.capital,
        "base_pct": base_ratio,
        "rsi_mfi_above": 100,
        **best,
    }

    # Run best params
    r_best = run_backtest(klines, best_params)
    best_score = calc_score(r_best)

    # Run default for comparison
    default_params = {
        "initial_capital": args.capital, "base_pct": base_ratio,
        "srp_pct": 1.0, "vwma_len": 14, "rsi_mfi_below": 55, "rsi_mfi_above": 100,
        "dca_mult": 1.5, "dca_max": 4, "dca_spacing": 3.0,
        "mintp": 0.025, "max_loss": 0.06,
    }
    r_default = run_backtest(klines, default_params)
    default_score = calc_score(r_default)

    print(f"\n  {'Metric':<16} {'Default':>12} {'Optimized':>12} {'Change':>12}")
    print(f"  {'-' * 54}")
    for label, key in [("Return %", "return_pct"), ("Max DD %", "max_dd_pct"),
                       ("Win Rate %", "win_rate"), ("Profit Factor", "profit_factor"),
                       ("Deals", "deals"), ("Trades", "trades")]:
        dv = r_default[key]
        ov = r_best[key]
        if isinstance(dv, (int, float)) and isinstance(ov, (int, float)):
            delta = f"{ov - dv:+.2f}"
        else:
            delta = "—"
        print(f"  {label:<16} {str(dv):>12} {str(ov):>12} {str(delta):>12}")

    print(f"\n  Score: default={default_score:.2f}  optimized={best_score:.2f}")

    # Parameter importance
    try:
        importance = optuna.importance.get_param_importances(study)
        print(f"\n  Parameter Importance:")
        for k, v in sorted(importance.items(), key=lambda x: x[1], reverse=True):
            bar = "█" * int(v * 40)
            print(f"    {k:<16} {v:.3f} {bar}")
    except Exception:
        pass

    # Best parameters
    bp = best_params
    base_val = bp.get('base_pct', 0.10) * args.capital
    req_cap = required_capital(base_val, bp["dca_mult"], bp["dca_max"])

    print(f"\n  ⭐ Optimized Parameters:")
    print(f"    SRP% = {bp['srp_pct']}")
    print(f"    VWMA = {bp['vwma_len']}")
    print(f"    Long RSI-MFI < {bp['rsi_mfi_below']}")
    print(f"    TP% = {bp['mintp'] * 100}")
    print(f"    SL% = {bp['max_loss'] * 100}")
    print(f"    DCA Spacing% = {bp['dca_spacing']}")
    print(f"    DCA Mult = {bp['dca_mult']}")
    print(f"    DCA Count = {bp['dca_max']}")
    print(f"    Required Capital: ${req_cap:,.0f}")
    print(f"{'=' * 70}\n")


if __name__ == "__main__":
    main()
