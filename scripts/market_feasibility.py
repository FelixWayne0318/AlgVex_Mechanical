#!/usr/bin/env python3
"""Market feasibility: test if ANY strategy can profit with fixed TP/SL."""
import requests

def simulate(prices, signal_fn, tp, sl, max_hold=96):
    """Sequential non-overlapping trade simulation."""
    i = 0
    wins = losses = 0
    pnl = 0.0
    while i < len(prices) - 1:
        d = signal_fn(prices, i)
        if d == 0:
            i += 1
            continue
        entry = prices[i]
        is_long = d > 0
        hit = False
        for j in range(i + 1, min(i + max_hold, len(prices))):
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
            i += max_hold
    return wins, losses, pnl

def main():
    klines = requests.get("https://fapi.binance.com/fapi/v1/klines",
        params={"symbol": "BTCUSDT", "interval": "30m", "limit": 1500}).json()
    prices = [float(k[4]) for k in klines]
    print(f"Data: {len(prices)} bars (~{len(prices) / 48:.0f} days)")
    print(f"Range: ${min(prices):,.0f} - ${max(prices):,.0f}")
    print(f"Trend: ${prices[0]:,.0f} -> ${prices[-1]:,.0f} ({(prices[-1]/prices[0]-1)*100:+.1f}%)")
    print()

    # 1. Random LONG every 24h (baseline)
    print("=== Baseline: Random LONG every 24h ===")
    for tp, sl in [(1.0, 1.0), (1.5, 1.5), (2.0, 2.0), (1.5, 2.0), (2.0, 1.5)]:
        w, l, pnl = simulate(prices,
            lambda p, i: 1 if i % 48 == 0 else 0, tp, sl)
        t = w + l
        if t > 0:
            print(f"  TP={tp}/SL={sl}: {w}W/{l}L WR={w/t*100:.0f}% PnL={pnl:+.1f}%")

    # 2. Momentum: LONG when 3-bar up, SHORT when 3-bar down
    print()
    print("=== Momentum: 3-bar direction (LONG+SHORT) ===")
    def mom_signal(p, i):
        if i < 3: return 0
        chg = (p[i] - p[i-3]) / p[i-3]
        if chg > 0.003: return 1
        if chg < -0.003: return -1
        return 0
    for tp, sl in [(1.0, 1.0), (1.5, 1.5), (2.0, 2.0), (1.5, 2.0), (2.0, 1.5)]:
        w, l, pnl = simulate(prices, mom_signal, tp, sl, 48)
        t = w + l
        if t > 0:
            print(f"  TP={tp}/SL={sl}: {w}W/{l}L WR={w/t*100:.0f}% PnL={pnl:+.1f}% ({t} trades)")

    # 3. Mean-reversion: LONG when RSI<30, SHORT when RSI>70
    print()
    print("=== Mean-Reversion: RSI(14) oversold/overbought ===")
    # Precompute RSI
    rsi_vals = [50.0] * 14
    gains = [0.0] * len(prices)
    losses_arr = [0.0] * len(prices)
    for i in range(1, len(prices)):
        c = prices[i] - prices[i-1]
        gains[i] = max(c, 0)
        losses_arr[i] = max(-c, 0)
    ag = sum(gains[1:15]) / 14
    al = sum(losses_arr[1:15]) / 14
    rsi_vals.append(100 - 100/(1 + ag/al) if al > 0 else 100)
    for i in range(15, len(prices)):
        ag = (ag * 13 + gains[i]) / 14
        al = (al * 13 + losses_arr[i]) / 14
        rsi_vals.append(100 - 100/(1 + ag/al) if al > 0 else 100)

    def rsi_signal(p, i):
        if i >= len(rsi_vals): return 0
        if rsi_vals[i] < 30: return 1
        if rsi_vals[i] > 70: return -1
        return 0
    for tp, sl in [(1.0, 1.0), (1.5, 1.5), (2.0, 2.0), (1.5, 2.0), (2.0, 1.5)]:
        w, l, pnl = simulate(prices, rsi_signal, tp, sl, 48)
        t = w + l
        if t > 0:
            print(f"  TP={tp}/SL={sl}: {w}W/{l}L WR={w/t*100:.0f}% PnL={pnl:+.1f}% ({t} trades)")

    # 4. Anti-momentum (contrarian): SHORT when 6-bar up, LONG when 6-bar down
    print()
    print("=== Contrarian: Counter 6-bar moves ===")
    def contra_signal(p, i):
        if i < 6: return 0
        chg = (p[i] - p[i-6]) / p[i-6]
        if chg > 0.005: return -1  # went up, bet short
        if chg < -0.005: return 1  # went down, bet long
        return 0
    for tp, sl in [(1.0, 1.0), (1.5, 1.5), (2.0, 2.0), (1.5, 2.0), (2.0, 1.5)]:
        w, l, pnl = simulate(prices, contra_signal, tp, sl, 48)
        t = w + l
        if t > 0:
            print(f"  TP={tp}/SL={sl}: {w}W/{l}L WR={w/t*100:.0f}% PnL={pnl:+.1f}% ({t} trades)")

    # 5. Summary: which approach + params wins?
    print()
    print("=== GRID SEARCH: Best approach + TP/SL ===")
    strategies = [
        ("Random LONG", lambda p, i: 1 if i % 48 == 0 else 0),
        ("Momentum L+S", mom_signal),
        ("RSI Mean-Rev", rsi_signal),
        ("Contrarian", contra_signal),
    ]
    for name, fn in strategies:
        best_pnl = -999
        best = None
        for tp in [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]:
            for sl in [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]:
                w, l, pnl = simulate(prices, fn, tp, sl, 48)
                t = w + l
                if t >= 5 and pnl > best_pnl:
                    best_pnl = pnl
                    best = (tp, sl, w, l, t, pnl)
        if best:
            tp, sl, w, l, t, pnl = best
            print(f"  {name:15s}: TP={tp}% SL={sl}% | {w}W/{l}L WR={w/t*100:.0f}% PnL={pnl:+.1f}% ({t} trades)")

if __name__ == "__main__":
    main()
