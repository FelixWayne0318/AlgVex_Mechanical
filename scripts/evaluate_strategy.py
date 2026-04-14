#!/usr/bin/env python3
"""Evaluate strategy: compare current vs improved approaches."""
import json, glob
from collections import Counter

def main():
    snap_dir = "data/feature_snapshots"
    files = sorted(glob.glob(f"{snap_dir}/*.json"))[-500:]
    prices, net_raws, div_raws, flow_raws, weights_list = [], [], [], [], []

    for fp in files:
        try:
            with open(fp) as f:
                snap = json.load(f)
            p = snap.get("features", {}).get("price", 0)
            scores = snap.get("scores", {})
            nr = scores.get("anticipatory_raw", 0)
            if p and float(p) > 0:
                prices.append(float(p))
                net_raws.append(float(nr) if not isinstance(nr, str) else 0)
                div_raws.append(float(scores.get("divergence", {}).get("raw", 0)))
                flow_raws.append(float(scores.get("order_flow", {}).get("raw", 0)))
                w = scores.get("weights_applied", {})
                weights_list.append(w)
        except:
            pass

    n = len(prices)
    print(f"Data: {n} snapshots")
    print(f"Price: ${prices[0]:,.0f} -> ${prices[-1]:,.0f} ({(prices[-1]/prices[0]-1)*100:+.1f}%)")
    print(f"High: ${max(prices):,.0f}, Low: ${min(prices):,.0f}")
    print()

    def simulate(signal_fn, tp, sl, label):
        """Run sequential non-overlapping trades."""
        i = 0
        wins = losses = 0
        pnl = 0.0
        while i < n - 1:
            direction = signal_fn(i)  # 1=LONG, -1=SHORT, 0=HOLD
            if direction == 0:
                i += 1
                continue
            entry = prices[i]
            is_long = direction > 0
            hit = False
            for j in range(i + 1, min(i + 72, n)):
                p = prices[j]
                if is_long:
                    hit_tp = p >= entry * (1 + tp / 100)
                    hit_sl = p <= entry * (1 - sl / 100)
                else:
                    hit_tp = p <= entry * (1 - tp / 100)
                    hit_sl = p >= entry * (1 + sl / 100)
                if hit_tp:
                    wins += 1
                    pnl += tp - 0.15
                    i = j + 1
                    hit = True
                    break
                if hit_sl:
                    losses += 1
                    pnl -= sl + 0.15
                    i = j + 1
                    hit = True
                    break
            if not hit:
                i += 72
        total = wins + losses
        if total > 0:
            wr = wins / total * 100
            avg = pnl / total
            print(f"  {label:40s}: {wins}W/{losses}L WR={wr:.0f}% PnL={pnl:+.1f}% Avg={avg:+.3f}%/trade ({total} trades)")
        else:
            print(f"  {label:40s}: No trades")

    # Current system: LONG only when nr > 0.2
    print("=== Test 1: Current System (LONG only) ===")
    for tp, sl in [(1.5, 2.0), (2.0, 2.0), (2.0, 2.5), (1.5, 1.5)]:
        simulate(
            lambda i: 1 if i < len(net_raws) and net_raws[i] > 0.2 else 0,
            tp, sl, f"LONG-only TP={tp}/SL={sl}"
        )

    # With SHORT: use current net_raw sign
    print()
    print("=== Test 2: Current System + SHORT capability ===")
    for tp, sl in [(1.5, 2.0), (2.0, 2.0), (2.0, 2.5), (1.5, 1.5)]:
        simulate(
            lambda i: (1 if net_raws[i] > 0.2 else (-1 if net_raws[i] < -0.2 else 0)) if i < len(net_raws) else 0,
            tp, sl, f"LONG+SHORT TP={tp}/SL={sl}"
        )

    # Structure decay: remove Structure, use only Div + Flow
    adjusted_nrs = []
    for i in range(len(div_raws)):
        w = weights_list[i] if i < len(weights_list) else {}
        w_d = w.get("divergence", 0.3)
        w_f = w.get("order_flow", 0.3)
        if w_d + w_f > 0:
            adj = (div_raws[i] * w_d + flow_raws[i] * w_f) / (w_d + w_f)
        else:
            adj = 0
        adjusted_nrs.append(adj)

    print()
    print("=== Test 3: Remove Structure (Div+Flow only) + LONG+SHORT ===")
    sig_dist = Counter("LONG" if x > 0.15 else ("SHORT" if x < -0.15 else "HOLD") for x in adjusted_nrs)
    print(f"  Signal distribution: {dict(sig_dist)}")
    for tp, sl in [(1.5, 2.0), (2.0, 2.0), (2.0, 2.5), (1.5, 1.5), (2.5, 2.5)]:
        simulate(
            lambda i: (1 if adjusted_nrs[i] > 0.15 else (-1 if adjusted_nrs[i] < -0.15 else 0)) if i < len(adjusted_nrs) else 0,
            tp, sl, f"NoStruct L+S TP={tp}/SL={sl}"
        )

    # Optimal: grid search
    print()
    print("=== Test 4: Grid Search — best TP/SL for each approach ===")
    for approach_name, signal_fn_factory in [
        ("Current LONG-only", lambda: (lambda i: 1 if i < len(net_raws) and net_raws[i] > 0.2 else 0)),
        ("Current L+S", lambda: (lambda i: (1 if net_raws[i] > 0.2 else (-1 if net_raws[i] < -0.2 else 0)) if i < len(net_raws) else 0)),
        ("NoStruct L+S", lambda: (lambda i: (1 if adjusted_nrs[i] > 0.15 else (-1 if adjusted_nrs[i] < -0.15 else 0)) if i < len(adjusted_nrs) else 0)),
    ]:
        best_pnl = -999
        best = None
        signal_fn = signal_fn_factory()
        for tp in [1.0, 1.5, 2.0, 2.5, 3.0]:
            for sl in [1.0, 1.5, 2.0, 2.5, 3.0]:
                i = 0
                wins = losses = 0
                pnl = 0.0
                while i < n - 1:
                    d = signal_fn(i)
                    if d == 0:
                        i += 1
                        continue
                    entry = prices[i]
                    is_long = d > 0
                    hit = False
                    for j in range(i + 1, min(i + 72, n)):
                        p = prices[j]
                        if is_long:
                            ht = p >= entry * (1 + tp / 100)
                            hs = p <= entry * (1 - sl / 100)
                        else:
                            ht = p <= entry * (1 - tp / 100)
                            hs = p >= entry * (1 + sl / 100)
                        if ht:
                            wins += 1; pnl += tp - 0.15; i = j + 1; hit = True; break
                        if hs:
                            losses += 1; pnl -= sl + 0.15; i = j + 1; hit = True; break
                    if not hit:
                        i += 72
                total = wins + losses
                if total >= 5 and pnl > best_pnl:
                    best_pnl = pnl
                    best = (tp, sl, wins, losses, total, pnl)
        if best:
            tp, sl, w, l, t, pnl = best
            print(f"  {approach_name:20s} BEST: TP={tp}% SL={sl}% | {w}W/{l}L WR={w/t*100:.0f}% PnL={pnl:+.1f}%")
        else:
            print(f"  {approach_name:20s} BEST: Insufficient trades")

if __name__ == "__main__":
    main()
