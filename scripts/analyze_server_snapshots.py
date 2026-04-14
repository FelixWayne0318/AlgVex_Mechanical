#!/usr/bin/env python3
"""Analyze server feature snapshots for net_raw predictive power."""

import json
import os
import sys


def main():
    d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data", "server_snapshots")

    files = sorted(f for f in os.listdir(d) if f.endswith('.json'))
    print(f"Loading {len(files)} server snapshots...")

    results = []
    for fname in files:
        try:
            with open(os.path.join(d, fname)) as fp:
                snap = json.load(fp)
            feats = snap.get('features', {})
            scores = snap.get('scores', {})
            price = feats.get('price', 0)
            net_raw = scores.get('anticipatory_raw')
            if not price or price <= 0 or net_raw is None:
                continue
            results.append({
                'ts': fname.replace('snapshot_', '').replace('.json', ''),
                'price': price,
                'net_raw': net_raw,
                'regime': scores.get('regime', 'DEFAULT'),
                'struct_dir': scores.get('structure', {}).get('direction', 'N/A'),
                'struct_score': scores.get('structure', {}).get('score', 0),
                'div_dir': scores.get('divergence', {}).get('direction', 'N/A'),
                'div_score': scores.get('divergence', {}).get('score', 0),
                'flow_dir': scores.get('order_flow', {}).get('direction', 'N/A'),
                'flow_score': scores.get('order_flow', {}).get('score', 0),
                'ext_4h': str(feats.get('extension_regime_4h', 'NORMAL')),
                'rsi_30m': feats.get('rsi_30m', 50),
                'rsi_4h': feats.get('rsi_4h', 50),
                'cvd_30m': str(feats.get('cvd_price_cross_30m', '')),
                'cvd_4h': str(feats.get('cvd_price_cross_4h', '')),
                'sup_dist': feats.get('nearest_support_dist_atr', 99),
                'sup_str': str(feats.get('nearest_support_strength', 'NONE')),
            })
        except Exception:
            continue

    # Forward returns
    for i in range(len(results)):
        for p, k in [(1, 'fwd1'), (3, 'fwd3'), (6, 'fwd6'), (12, 'fwd12')]:
            if i + p < len(results):
                results[i][k] = (results[i + p]['price'] - results[i]['price']) / results[i]['price']
            else:
                results[i][k] = None

    N = len(results)
    net_raws = [r['net_raw'] for r in results]

    print("=" * 70)
    print(f"SERVER SNAPSHOT ANALYSIS (n={N}, {results[0]['ts']} ~ {results[-1]['ts']})")
    print("=" * 70)
    prices = [r['price'] for r in results]
    print(f"  Price range: ${min(prices):,.0f} ~ ${max(prices):,.0f}")
    print(f"  Mean:  {sum(net_raws)/N:.4f}")
    print(f"  Min:   {min(net_raws):.4f}")
    print(f"  Max:   {max(net_raws):.4f}")
    sv = sorted(net_raws)
    for p in [10, 25, 50, 75, 90]:
        print(f"  P{p:2d}:   {sv[int(N*p/100)]:.4f}")

    print(f"\n  Distribution:")
    bkts = [(-1, -0.4), (-0.4, -0.25), (-0.25, -0.15), (-0.15, -0.05),
            (-0.05, 0.05), (0.05, 0.15), (0.15, 0.25), (0.25, 0.4), (0.4, 1)]
    for lo, hi in bkts:
        c = sum(1 for v in net_raws if lo <= v < hi)
        pct = c / N * 100
        print(f"    [{lo:+.2f},{hi:+.2f}): {c:4d} ({pct:5.1f}%) {'#' * int(pct / 2)}")

    print(f"\n  Regime:")
    regs = {}
    for r in results:
        regs.setdefault(r['regime'], []).append(r['net_raw'])
    for rg, vals in sorted(regs.items()):
        print(f"    {rg:20s}: n={len(vals):3d} mean={sum(vals)/len(vals):+.4f} "
              f"[{min(vals):+.3f},{max(vals):+.3f}]")

    # Directional accuracy
    print()
    print("=" * 70)
    print("DIRECTIONAL ACCURACY")
    print("=" * 70)
    intervals = {1: '20min', 3: '1h', 6: '2h', 12: '4h'}
    for horizon, key in [(1, 'fwd1'), (3, 'fwd3'), (6, 'fwd6'), (12, 'fwd12')]:
        valid = [r for r in results if r.get(key) is not None and abs(r['net_raw']) > 0.01]
        if not valid:
            continue
        correct = sum(1 for r in valid
                      if (r['net_raw'] > 0 and r[key] > 0) or (r['net_raw'] < 0 and r[key] < 0))
        acc = correct / len(valid) * 100
        print(f"\n  Horizon {horizon} (~{intervals.get(horizon, '?')}):  "
              f"{acc:.1f}% ({correct}/{len(valid)})")
        for th in [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.45]:
            strong = [r for r in valid if abs(r['net_raw']) >= th]
            if not strong:
                continue
            sc = sum(1 for r in strong
                     if (r['net_raw'] > 0 and r[key] > 0) or (r['net_raw'] < 0 and r[key] < 0))
            sa = sc / len(strong) * 100
            srets = [r[key] * (1 if r['net_raw'] > 0 else -1) for r in strong]
            mr = sum(srets) / len(srets) * 100
            print(f"    |raw|>={th:.2f}: {sa:.1f}% ({sc}/{len(strong):3d}) "
                  f"mean_ret={mr:+.4f}%")

    # Threshold simulation
    print()
    print("=" * 70)
    print("THRESHOLD SIMULATION")
    print("=" * 70)
    for horizon, key in [(3, 'fwd3'), (6, 'fwd6'), (12, 'fwd12')]:
        print(f"\n  Horizon ~{intervals.get(horizon)}:")
        print(f"  {'Thresh':>8} {'Trades':>7} {'% All':>7} {'WinR':>7} {'AvgRet':>10} {'TotRet':>10}")
        for th in [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45]:
            trades = [r for r in results if r.get(key) is not None and abs(r['net_raw']) >= th]
            if not trades:
                continue
            srets = [r[key] * (1 if r['net_raw'] > 0 else -1) for r in trades]
            wins = sum(1 for s in srets if s > 0)
            wr = wins / len(srets) * 100
            ar = sum(srets) / len(srets) * 100
            tr = sum(srets) * 100
            print(f"  {th:>8.2f} {len(trades):>7} {len(trades)/N*100:>6.1f}% {wr:>6.1f}% "
                  f"{ar:>+9.4f}% {tr:>+9.2f}%")

    # Zone trigger rates
    print()
    print("=" * 70)
    print("ZONE TRIGGER RATES (SERVER DATA)")
    print("=" * 70)
    ext_oe = sum(1 for r in results if r['ext_4h'].upper() in ('OVEREXTENDED', 'EXTREME'))
    ext_e = sum(1 for r in results if r['ext_4h'].upper() in ('EXTENDED', 'OVEREXTENDED', 'EXTREME'))
    rsi_l = sum(1 for r in results if (r.get('rsi_30m') or 50) < 50 or (r.get('rsi_4h') or 50) < 45)
    cvd_l = sum(1 for r in results
                if str(r.get('cvd_30m', '')).upper() in ('ACCUMULATION', 'ABSORPTION_BUY')
                or str(r.get('cvd_4h', '')).upper() in ('ACCUMULATION', 'ABSORPTION_BUY'))
    sr15 = sum(1 for r in results if (r.get('sup_dist') or 99) < 1.5)
    sr3 = sum(1 for r in results if (r.get('sup_dist') or 99) < 3.0)
    sr5 = sum(1 for r in results if (r.get('sup_dist') or 99) < 5.0)
    print(f"  4H Ext OVEREXT+:  {ext_oe:4d}/{N} ({ext_oe/N*100:.1f}%)")
    print(f"  4H Ext EXTENDED+: {ext_e:4d}/{N} ({ext_e/N*100:.1f}%)")
    print(f"  RSI oversold:     {rsi_l:4d}/{N} ({rsi_l/N*100:.1f}%)")
    print(f"  CVD accum:        {cvd_l:4d}/{N} ({cvd_l/N*100:.1f}%)")
    print(f"  S/R <1.5 ATR:     {sr15:4d}/{N} ({sr15/N*100:.1f}%)")
    print(f"  S/R <3.0 ATR:     {sr3:4d}/{N} ({sr3/N*100:.1f}%)")
    print(f"  S/R <5.0 ATR:     {sr5:4d}/{N} ({sr5/N*100:.1f}%)")

    print(f"\n  v48 Confluence (OVEREXT+, min=2):")
    for mc in [1, 2, 3, 4]:
        cnt = 0
        for r in results:
            nc = 0
            if r['ext_4h'].upper() in ('OVEREXTENDED', 'EXTREME'):
                nc += 1
            if (r.get('rsi_30m') or 50) < 50 or (r.get('rsi_4h') or 50) < 45:
                nc += 1
            if (str(r.get('cvd_30m', '')).upper() in ('ACCUMULATION', 'ABSORPTION_BUY')
                    or str(r.get('cvd_4h', '')).upper() in ('ACCUMULATION', 'ABSORPTION_BUY')):
                nc += 1
            if (r.get('sup_dist') or 99) < 3.0:
                nc += 1
            if nc >= mc:
                cnt += 1
        print(f"    >={mc}: {cnt:4d}/{N} ({cnt/N*100:.1f}%)")

    # Dimension distribution
    print()
    print("=" * 70)
    print("DIMENSION DISTRIBUTION")
    print("=" * 70)
    for dim, dk, sk in [('Structure', 'struct_dir', 'struct_score'),
                         ('Divergence', 'div_dir', 'div_score'),
                         ('Order Flow', 'flow_dir', 'flow_score')]:
        dirs = {}
        for r in results:
            dirs[r[dk]] = dirs.get(r[dk], 0) + 1
        scores_list = [r[sk] for r in results]
        print(f"  {dim}:")
        for dd, c in sorted(dirs.items(), key=lambda x: -x[1]):
            print(f"    {dd:12s}: {c:4d} ({c/N*100:.1f}%)")
        print(f"    avg_score: {sum(scores_list)/len(scores_list):.1f}")

    # v49 backtest simulation
    print()
    print("=" * 70)
    print("v49 HYBRID DECISION SIMULATION")
    print("=" * 70)

    # Simulate mechanical_decide with server data
    sig_counts = {'HOLD': 0, 'LONG': 0, 'SHORT': 0}
    hold_reasons = {}
    conf_counts = {'HIGH': 0, 'MEDIUM': 0, 'LOW': 0}
    trade_results = []

    for i, r in enumerate(results):
        abs_raw = abs(r['net_raw'])
        # Count zone conditions for LONG
        n_long_zone = 0
        if r['ext_4h'].upper() in ('EXTENDED', 'OVEREXTENDED', 'EXTREME'):
            if (r.get('ext_ratio_4h', 0) or 0) < 0 if r['net_raw'] > 0 else True:
                n_long_zone += 1
        if (r.get('rsi_30m') or 50) < 50 or (r.get('rsi_4h') or 50) < 45:
            n_long_zone += 1
        if (str(r.get('cvd_30m', '')).upper() in ('ACCUMULATION', 'ABSORPTION_BUY')
                or str(r.get('cvd_4h', '')).upper() in ('ACCUMULATION', 'ABSORPTION_BUY')):
            n_long_zone += 1
        if (r.get('sup_dist') or 99) < 3.0:
            n_long_zone += 1

        n_short_zone = 0
        if (r.get('rsi_30m') or 50) > 70 or (r.get('rsi_4h') or 50) > 80:
            n_short_zone += 1
        if (str(r.get('cvd_30m', '')).upper() in ('DISTRIBUTION', 'ABSORPTION_SELL', 'CONFIRMED_SELL')
                or str(r.get('cvd_4h', '')).upper() in ('DISTRIBUTION', 'ABSORPTION_SELL', 'CONFIRMED_SELL')):
            n_short_zone += 1

        n_zone = n_long_zone if r['net_raw'] > 0 else n_short_zone

        # Decision tiers
        signal = 'HOLD'
        confidence = 'LOW'
        hold_source = ''

        if abs_raw >= 0.45:
            signal = 'LONG' if r['net_raw'] > 0 else 'SHORT'
            confidence = 'HIGH'
        elif abs_raw >= 0.35:
            signal = 'LONG' if r['net_raw'] > 0 else 'SHORT'
            confidence = 'MEDIUM'
        elif abs_raw >= 0.15 and n_zone >= 1:
            signal = 'LONG' if r['net_raw'] > 0 else 'SHORT'
            confidence = 'LOW'
        else:
            hold_source = 'weak_signal' if abs_raw >= 0.15 else 'below_threshold'

        # Zone boost
        if signal != 'HOLD' and n_zone >= 3:
            confidence = {'LOW': 'MEDIUM', 'MEDIUM': 'HIGH'}.get(confidence, confidence)
        elif signal != 'HOLD' and n_zone >= 2 and confidence == 'LOW':
            confidence = 'MEDIUM'

        # SHORT gate
        if signal == 'SHORT' and confidence == 'LOW':
            signal = 'HOLD'
            hold_source = 'short_low_conf'

        sig_counts[signal] = sig_counts.get(signal, 0) + 1
        if signal == 'HOLD':
            hold_reasons[hold_source] = hold_reasons.get(hold_source, 0) + 1
        else:
            conf_counts[confidence] = conf_counts.get(confidence, 0) + 1

        # Forward return
        if signal in ('LONG', 'SHORT') and i + 6 < len(results):
            fwd = results[i + 6]['price'] - r['price']
            fwd_pct = fwd / r['price']
            if signal == 'SHORT':
                fwd_pct = -fwd_pct
            trade_results.append((signal, confidence, r['net_raw'], fwd_pct))

    for s, c in sorted(sig_counts.items()):
        print(f"  {s:8s}: {c:4d} ({c/N*100:.1f}%)")

    print(f"\n  HOLD reasons:")
    for src, c in sorted(hold_reasons.items(), key=lambda x: -x[1]):
        print(f"    {src:20s}: {c:4d}")

    print(f"\n  Confidence (trades only):")
    for c, n in sorted(conf_counts.items()):
        print(f"    {c:8s}: {n:4d}")

    print(f"\n  Trade performance (6-period forward, ~2h):")
    if trade_results:
        wins = sum(1 for _, _, _, r in trade_results if r > 0)
        total_ret = sum(r for _, _, _, r in trade_results)
        avg_ret = total_ret / len(trade_results)
        print(f"    Trades:  {len(trade_results)}")
        print(f"    WinRate: {wins/len(trade_results)*100:.1f}%")
        print(f"    AvgRet:  {avg_ret*100:+.4f}%")
        print(f"    TotRet:  {total_ret*100:+.2f}%")

        for cl in ['HIGH', 'MEDIUM', 'LOW']:
            ct = [(s, c, nr, r) for s, c, nr, r in trade_results if c == cl]
            if ct:
                cw = sum(1 for _, _, _, r in ct if r > 0)
                cr = sum(r for _, _, _, r in ct)
                print(f"    {cl:8s}: n={len(ct):3d} wr={cw/len(ct)*100:4.0f}% "
                      f"avg={cr/len(ct)*100:+.4f}% tot={cr*100:+.2f}%")


if __name__ == '__main__':
    main()
