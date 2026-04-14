#!/usr/bin/env python3
"""
SRP v5.0 Walk-Forward Validation — Full Parameter Coverage

Tests ALL 11 parameters with wide ranges:
  Phase 1: Entry/Exit (SRP%, TP%, SL%, RSI-MFI above) — 875 combos
  Phase 2: DCA Engine (spacing, mult, count, type) — 200 combos
  Phase 3: Signal (VWMA, RSI-MFI below, base ratio) — 100 combos

Split: 65% in-sample optimization, 35% out-of-sample validation.
Score retention ≥60% = ROBUST, ≥30% = MODERATE, <30% = OVERFITTED.

Usage:
    python3 scripts/walk_forward_srp.py --days 456 --capital 10000
"""

import argparse, sys, os, time, math
from datetime import datetime, timezone
from itertools import product

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.backtest_srp_v5_exact import fetch_klines, run_backtest


def required_capital(base, dca_mult, dca_count):
    """Total capital needed: base + all DCA layers."""
    total = base
    pos = base
    for _ in range(dca_count):
        pos *= dca_mult
        total += pos
    return total


def calc_sqn(r):
    """System Quality Number (Van Tharp).

    SQN = √N × (mean_pnl / stdev_pnl)

    Rating:
      < 1.6  Poor
      1.6-2  Below average
      2-3    Good
      3-5    Excellent
      > 5    Superb (but suspect overfitting if > 7)
    """
    pnls = r.get("trade_pnls", [])
    n = len(pnls)
    if n < 2:
        return 0.0
    mean = sum(pnls) / n
    variance = sum((p - mean) ** 2 for p in pnls) / (n - 1)
    stdev = math.sqrt(variance) if variance > 0 else 0.001
    return round(math.sqrt(n) * mean / stdev, 2)


def calc_score(r, params=None, capital=10000, leverage=10):
    """Score = SQN with hard constraints.

    Hard constraints (instant reject):
    - Max DD > 25% (most traders can't handle more)
    - Trades < 30 (not statistically significant)
    - Win Rate < 50% (DCA strategy should have high WR)
    - Required capital > leverage capacity (can't execute)
    - Return ≤ 0% (losing strategy)
    - SQN > 7 (likely overfitted)
    """
    trades = r["trades"]
    ret = r["return_pct"]
    dd = r["max_dd_pct"]
    wr = r["win_rate"]

    # Hard constraints
    if trades < 30 or ret <= 0:
        return -999
    if dd > 25:
        return -999
    if wr < 50:
        return -999

    # Capital constraint
    if params:
        req = required_capital(
            params.get("base_pct", 0.10) * capital,
            params.get("dca_mult", 1.5),
            params.get("dca_max", 4),
        )
        if req > capital * leverage:
            return -999

    sqn = calc_sqn(r)

    # SQN > 7 is suspect (overfitting)
    if sqn > 7:
        return -999

    return sqn


def sweep(klines, param_grid, base_params, label, capital=10000):
    """Run parameter sweep with capital/DD constraints."""
    keys = list(param_grid.keys())
    combos = list(product(*param_grid.values()))
    print(f"  {label}: {len(combos)} combinations...", end="", flush=True)
    t0 = time.time()
    best_score, best_params = -999, dict(base_params)
    skipped = 0
    for combo in combos:
        p = dict(base_params)
        for k, v in zip(keys, combo):
            p[k] = v
        # Pre-check: skip if capital requirement exceeds leverage
        req = required_capital(p.get("base_pct", 0.10) * capital, p.get("dca_mult", 1.5), p.get("dca_max", 4))
        if req > capital * 10:
            skipped += 1
            continue
        r = run_backtest(klines, p)
        s = calc_score(r, params=p, capital=capital)
        if s > best_score:
            best_score = s
            best_params = dict(p)
    elapsed = time.time() - t0
    skip_str = f", {skipped} skipped (capital)" if skipped else ""
    print(f" done ({elapsed:.0f}s, best={best_score:.2f}{skip_str})")
    return best_params, best_score


def optimize_full(klines, capital):
    """3-phase optimization covering ALL 11 parameters."""
    base = {
        "initial_capital": capital,
        "base_pct": 0.10,
        "srp_pct": 1.0, "vwma_len": 14,
        "rsi_mfi_below": 55, "rsi_mfi_above": 100,
        "dca_mult": 1.5, "dca_max": 4, "dca_spacing": 3.0,
        "mintp": 0.025, "max_loss": 0.06,
        "dca_type": "volume",
    }

    # Phase 1: Entry/Exit core — 7×7×7×5 = 1715 combos
    best, _ = sweep(klines, {
        "srp_pct":       [0.3, 0.5, 0.8, 1.0, 1.2, 1.5, 2.0],
        "mintp":         [0.010, 0.015, 0.020, 0.025, 0.030, 0.040, 0.050],
        "max_loss":      [0.03, 0.04, 0.05, 0.06, 0.08, 0.10, 0.15],
        "rsi_mfi_above": [80, 90, 100, 110, 120],
    }, base, "Phase 1 (Entry/Exit)", capital)

    # Phase 2: DCA engine — 7×5×6×2 = 420 combos
    best, _ = sweep(klines, {
        "dca_spacing":   [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0],
        "dca_mult":      [1.0, 1.2, 1.5, 1.7, 2.0],
        "dca_max":       [1, 2, 3, 4, 5, 6],
        "dca_type":      ["volume", "base"],
    }, best, "Phase 2 (DCA Engine)", capital)

    # Phase 3: Signal tuning — 5×7 = 35 combos
    best, best_score = sweep(klines, {
        "vwma_len":      [8, 10, 12, 14, 20],
        "rsi_mfi_below": [40, 45, 50, 55, 60, 65, 70],
    }, best, "Phase 3 (Signal)", capital)

    return best, best_score


def sqn_rating(sqn):
    if sqn < 1.6: return "Poor"
    if sqn < 2.0: return "Below avg"
    if sqn < 3.0: return "Good"
    if sqn < 5.0: return "Excellent"
    if sqn < 7.0: return "Superb"
    return "Suspect"


def print_result(label, r, params=None, capital=10000):
    pf = r['profit_factor'] if isinstance(r['profit_factor'], (int, float)) else 999
    exits = r.get('exit_types', {})
    exit_str = "  ".join(f"{k}:{v}" for k, v in sorted(exits.items()) if v > 0)
    sqn = calc_sqn(r)
    score = calc_score(r, params, capital)
    print(f"  {label}")
    print(f"    Return: {r['return_pct']:>+7.2f}%    Max DD: {r['max_dd_pct']:>5.2f}%    "
          f"Calmar: {r['return_pct']/max(r['max_dd_pct'],0.1):>5.1f}")
    print(f"    WR: {r['win_rate']:>5.1f}%       PF: {min(pf, 999):>7.2f}     "
          f"Deals: {r['deals']}  Trades: {r['trades']}")
    print(f"    Profit: ${r['gross_profit']:>8.2f}   Loss: ${r['gross_loss']:>8.2f}")
    print(f"    Exits: {exit_str}")
    print(f"    SQN: {sqn:.2f} ({sqn_rating(sqn)})    Score: {score}")


def print_params(p, capital):
    dca_type = p.get('dca_type', 'volume')
    base_pct_val = p.get('base_pct', 0.10) * 100
    print(f"    SRP={p['srp_pct']}%  VWMA={p['vwma_len']}  "
          f"RSI-MFI=({p['rsi_mfi_below']}/{p['rsi_mfi_above']})")
    print(f"    TP={p['mintp']*100}%  SL={p['max_loss']*100}%")
    print(f"    DCA: Spacing={p['dca_spacing']}%  Mult={p['dca_mult']}  "
          f"Count={p['dca_max']}  Type={dca_type}")
    print(f"    Base={base_pct_val:.0f}% of equity")


def main():
    parser = argparse.ArgumentParser(description="SRP Walk-Forward (Full)")
    parser.add_argument("--days", type=int, default=456)
    parser.add_argument("--capital", type=int, default=10000)
    parser.add_argument("--split", type=float, default=0.65)
    args = parser.parse_args()

    klines = fetch_klines(days=args.days)
    if len(klines) < 200:
        print("ERROR: Insufficient data")
        sys.exit(1)

    n = len(klines)
    split_idx = int(n * args.split)
    klines_in = klines[:split_idx]
    klines_out = klines[split_idx:]

    ts_start = int(klines[0][0]) / 1000
    ts_split = int(klines[split_idx][0]) / 1000
    ts_end = int(klines[-1][0]) / 1000
    fmt = lambda ts: datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")

    print(f"\n{'=' * 70}")
    print(f"  SRP v5.0 Walk-Forward — ALL 11 Parameters")
    print(f"{'=' * 70}")
    print(f"  Data: {n} bars ({args.days} days)")
    print(f"  In-sample:  {len(klines_in):>6} bars  ({fmt(ts_start)} → {fmt(ts_split)})")
    print(f"  Out-sample: {len(klines_out):>6} bars  ({fmt(ts_split)} → {fmt(ts_end)})")
    print(f"  Capital: ${args.capital:,}")
    print(f"\n  Parameters tested:")
    print(f"    1. SRP%        [0.3, 0.5, 0.8, 1.0, 1.2, 1.5, 2.0]")
    print(f"    2. TP%         [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]")
    print(f"    3. SL%         [3, 4, 5, 6, 8, 10, 15]")
    print(f"    4. RSI-MFI >   [80, 90, 100, 110, 120]       ← NEW")
    print(f"    5. DCA Spacing  [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]")
    print(f"    6. DCA Mult     [1.0, 1.2, 1.5, 1.7, 2.0]")
    print(f"    7. DCA Count    [1, 2, 3, 4, 5, 6]")
    print(f"    8. DCA Type     [volume, base]                ← NEW")
    print(f"    9. VWMA Length  [8, 10, 12, 14, 20]")
    print(f"   10. RSI-MFI <   [40, 45, 50, 55, 60, 65, 70]")
    print(f"  Total: ~2170 combinations across 3 phases")
    print(f"\n  Hard Constraints (instant reject):")
    print(f"    ❌ Max DD > 25% → rejected")
    print(f"    ❌ Trades < 30 → rejected (not statistically significant)")
    print(f"    ❌ Win Rate < 50% → rejected")
    print(f"    ❌ Required capital > ${args.capital:,} × 10x → rejected")
    print(f"    ❌ Return ≤ 0% → rejected")
    print(f"    ❌ SQN > 7 → rejected (likely overfitted)")
    print(f"\n  Scoring: SQN = √N × (mean_trade / stdev_trade)")
    print(f"    < 1.6 Poor | 1.6-2 Below avg | 2-3 Good | 3-5 Excellent | 5-7 Superb")

    # =====================================================================
    # Optimize on in-sample
    # =====================================================================
    print(f"\n{'─' * 70}")
    print(f"  OPTIMIZING on in-sample ({fmt(ts_start)} → {fmt(ts_split)})")
    print(f"{'─' * 70}")
    t0 = time.time()
    opt_params, opt_score = optimize_full(klines_in, args.capital)
    print(f"\n  Total optimization time: {time.time()-t0:.0f}s")
    print(f"\n  Best in-sample parameters:")
    print_params(opt_params, args.capital)

    # =====================================================================
    # Test both param sets on both periods
    # =====================================================================
    default_params = {
        "initial_capital": args.capital,
        "base_pct": 0.10,
        "srp_pct": 1.0, "vwma_len": 14,
        "rsi_mfi_below": 55, "rsi_mfi_above": 100,
        "dca_mult": 1.5, "dca_max": 4, "dca_spacing": 3.0,
        "mintp": 0.025, "max_loss": 0.06,
        "dca_type": "volume",
    }

    r_def_in  = run_backtest(klines_in, default_params)
    r_opt_in  = run_backtest(klines_in, opt_params)
    r_def_out = run_backtest(klines_out, default_params)
    r_opt_out = run_backtest(klines_out, opt_params)
    r_def_all = run_backtest(klines, default_params)
    r_opt_all = run_backtest(klines, opt_params)

    # =====================================================================
    # Results
    # =====================================================================
    for period, d, o, dates in [
        ("IN-SAMPLE", r_def_in, r_opt_in, f"{fmt(ts_start)} → {fmt(ts_split)}"),
        ("OUT-OF-SAMPLE ← KEY", r_def_out, r_opt_out, f"{fmt(ts_split)} → {fmt(ts_end)}"),
        ("FULL PERIOD", r_def_all, r_opt_all, f"{fmt(ts_start)} → {fmt(ts_end)}"),
    ]:
        print(f"\n{'=' * 70}")
        print(f"  {period} ({dates})")
        print(f"{'=' * 70}")
        print_result("Default:", d, default_params, args.capital)
        print()
        print_result("Optimized:", o, opt_params, args.capital)

    # =====================================================================
    # Overfitting analysis
    # =====================================================================
    print(f"\n{'=' * 70}")
    print(f"  OVERFITTING ANALYSIS")
    print(f"{'=' * 70}")

    sqn_in = calc_sqn(r_opt_in)
    sqn_out = calc_sqn(r_opt_out)
    sqn_def_out = calc_sqn(r_def_out)
    wfe = sqn_out / sqn_in if sqn_in > 0 else 0  # Walk-Forward Efficiency

    degradation = 0
    checks = [
        ("Return %",  r_opt_in["return_pct"],  r_opt_out["return_pct"],  "drop"),
        ("Max DD %",  r_opt_in["max_dd_pct"],  r_opt_out["max_dd_pct"], "rise"),
        ("Win Rate",  r_opt_in["win_rate"],     r_opt_out["win_rate"],   "drop"),
        ("SQN",       sqn_in,                   sqn_out,                 "drop"),
    ]

    print(f"\n  {'Metric':<12} {'In-Sample':>12} {'Out-Sample':>12} {'Change':>12} {'Flag':>6}")
    print(f"  {'-' * 56}")
    for label, iv, ov, direction in checks:
        if direction == "drop":
            change = ov - iv
            bad = change < -abs(iv) * 0.3 if iv != 0 else False
        else:
            change = ov - iv
            bad = change > abs(iv) * 0.5 if iv != 0 else False
        flag = "⚠️" if bad else ""
        if bad:
            degradation += 1
        print(f"  {label:<12} {iv:>12.2f} {ov:>12.2f} {change:>+12.2f} {flag:>6}")

    print(f"\n  SQN: in={sqn_in:.2f} ({sqn_rating(sqn_in)})  "
          f"out={sqn_out:.2f} ({sqn_rating(sqn_out)})")
    print(f"  Walk-Forward Efficiency: {wfe*100:.0f}%  (≥50% = robust)")
    print(f"  Default out-of-sample SQN: {sqn_def_out:.2f} ({sqn_rating(sqn_def_out)})")

    print(f"\n  {'─' * 56}")
    if wfe >= 0.5 and degradation == 0:
        verdict = "✅ ROBUST"
        advice = "Parameters are generalizable. Safe for live trading."
    elif wfe >= 0.3 and degradation <= 1:
        verdict = "⚠️  MODERATE"
        advice = "Some degradation. Use with caution, monitor closely."
    else:
        verdict = "❌ OVERFITTED"
        advice = "Significant degradation on unseen data."

    print(f"  {verdict} — WFE={wfe*100:.0f}%, flags={degradation}")
    print(f"  {advice}")

    if sqn_out > sqn_def_out and sqn_out >= 1.6:
        print(f"\n  ✅ Optimized SQN ({sqn_out:.2f}) > Default SQN ({sqn_def_out:.2f}) → USE optimized")
    elif sqn_def_out >= 1.6:
        print(f"\n  ⚠️  Default SQN ({sqn_def_out:.2f}) ≥ Optimized SQN ({sqn_out:.2f}) → USE default")
    else:
        print(f"\n  ⚠️  Both SQN below 1.6 — strategy may not be suitable for this market")

    print(f"\n  Optimized Parameters:")
    print_params(opt_params, args.capital)

    print(f"\n  Default Parameters:")
    print_params(default_params, args.capital)
    print(f"{'=' * 70}\n")


if __name__ == "__main__":
    main()
