#!/usr/bin/env python3
"""Check prediction accuracy of anticipatory scoring system."""
import json, glob, os, sys

def main():
    # 1. Trading memory
    print("=== Trading Memory ===")
    try:
        with open("data/trading_memory.json") as f:
            memory = json.load(f)
        trades = [m for m in memory if m.get("outcome")]
        print(f"  Total records: {len(memory)}")
        print(f"  With outcome: {len(trades)}")
        if trades:
            wins = sum(1 for t in trades if t.get("outcome", {}).get("pnl_pct", 0) > 0)
            total_pnl = sum(t.get("outcome", {}).get("pnl_pct", 0) for t in trades)
            print(f"  Win rate: {wins}/{len(trades)} ({wins/len(trades)*100:.0f}%)")
            print(f"  Total PnL: {total_pnl:.2f}%")
            for t in trades[-5:]:
                o = t.get("outcome", {})
                print(f"    {t.get('timestamp', '?')[:19]}: {o.get('side', '?')} "
                      f"PnL={o.get('pnl_pct', 0):.2f}% close={o.get('close_reason', '?')}")
    except Exception as e:
        print(f"  Error: {e}")

    # 2. HOLD counterfactuals
    print()
    print("=== HOLD Counterfactuals ===")
    try:
        with open("data/hold_counterfactuals.json") as f:
            cfs = json.load(f)
        if cfs:
            correct = [c for c in cfs if c.get("verdict") == "correct"]
            wrong = [c for c in cfs if c.get("verdict") == "wrong"]
            neutral = [c for c in cfs if c.get("verdict") == "neutral"]
            print(f"  Total: {len(cfs)}")
            print(f"  Correct HOLD: {len(correct)} ({len(correct)/len(cfs)*100:.0f}%)")
            print(f"  Wrong HOLD:   {len(wrong)} ({len(wrong)/len(cfs)*100:.0f}%)")
            print(f"  Neutral:      {len(neutral)} ({len(neutral)/len(cfs)*100:.0f}%)")
            if wrong:
                avg_miss = sum(abs(c.get("price_change_pct", 0)) for c in wrong) / len(wrong)
                print(f"  Avg missed move: {avg_miss:.2f}%")
            print(f"  Recent:")
            for c in cfs[-5:]:
                print(f"    {c.get('timestamp', '?')[:19]}: proposed={c.get('proposed_signal', '?')} "
                      f"verdict={c.get('verdict')} change={c.get('price_change_pct', 0):+.2f}%")
    except Exception as e:
        print(f"  Error: {e}")

    # 3. Signal direction accuracy from snapshots
    print()
    snap_dir = "data/feature_snapshots"
    files = sorted(glob.glob(f"{snap_dir}/*.json"))[-300:]
    signals = []
    for fp in files:
        try:
            with open(fp) as f:
                snap = json.load(f)
            scores = snap.get("scores", {})
            features = snap.get("features", {})
            nr = scores.get("anticipatory_raw", 0)
            if isinstance(nr, str):
                continue
            price = features.get("price", 0)
            ts_str = snap.get("timestamp", "")
            signals.append({
                "ts": ts_str,
                "net_raw": float(nr),
                "price": float(price) if price else 0,
                "struct": scores.get("structure", {}).get("direction", "N/A"),
                "div": scores.get("divergence", {}).get("direction", "N/A"),
                "flow": scores.get("order_flow", {}).get("direction", "N/A"),
                "regime": scores.get("regime", "N/A"),
            })
        except Exception:
            pass

    if not signals:
        print("No valid snapshots found")
        return

    print(f"=== Direction Accuracy ({len(signals)} snapshots) ===")

    # For each signal with net_raw, check if price moved correctly after N periods
    for lookahead_name, lookahead in [("1h (3 periods)", 3), ("2h (6 periods)", 6), ("4h (12 periods)", 12)]:
        correct = wrong = neutral_count = 0
        for i in range(len(signals) - lookahead):
            s = signals[i]
            future = signals[i + lookahead]
            if s["price"] <= 0 or future["price"] <= 0:
                continue
            price_change = (future["price"] - s["price"]) / s["price"] * 100
            nr = s["net_raw"]
            if abs(nr) < 0.15:
                neutral_count += 1
                continue
            predicted_up = nr > 0
            actual_up = price_change > 0.05
            actual_down = price_change < -0.05
            if predicted_up and actual_up:
                correct += 1
            elif not predicted_up and actual_down:
                correct += 1
            elif predicted_up and actual_down:
                wrong += 1
            elif not predicted_up and actual_up:
                wrong += 1
            else:
                neutral_count += 1

        total = correct + wrong
        if total > 0:
            print(f"  {lookahead_name}: {correct}/{total} ({correct/total*100:.0f}%) correct, {neutral_count} neutral")

    # 4. MFE/MAE analysis — would TP/SL hit?
    print()
    print("=== TP/SL Hit Probability ===")
    prices = [s["price"] for s in signals if s["price"] > 0]

    for tp_pct, sl_pct, label in [(4.0, 5.0, "Current (TP=4%/SL=5%)"), (2.0, 3.0, "Tighter (TP=2%/SL=3%)"), (1.5, 2.0, "Very tight (TP=1.5%/SL=2%)")]:
        tp_hits = sl_hits = neither = 0
        for i in range(len(prices) - 36):  # 36 periods = ~12h window
            entry = prices[i]
            future_prices = prices[i+1:i+36]
            max_p = max(future_prices)
            min_p = min(future_prices)
            mfe = (max_p - entry) / entry * 100
            mae = (entry - min_p) / entry * 100
            if mfe >= tp_pct:
                tp_hits += 1
            elif mae >= sl_pct:
                sl_hits += 1
            else:
                neither += 1
        total = tp_hits + sl_hits + neither
        if total > 0:
            wr = tp_hits / (tp_hits + sl_hits) * 100 if (tp_hits + sl_hits) > 0 else 0
            print(f"  {label}: TP={tp_hits}({tp_hits/total*100:.0f}%) SL={sl_hits}({sl_hits/total*100:.0f}%) Neither={neither}({neither/total*100:.0f}%) | WR={wr:.0f}%")

    # 5. Price context
    print()
    recent = prices[-72:] if len(prices) >= 72 else prices
    if len(recent) > 1:
        high = max(recent)
        low = min(recent)
        vol = (high - low) / low * 100
        print(f"=== Price Context (last ~24h) ===")
        print(f"  Range: ${low:,.0f} - ${high:,.0f} ({vol:.2f}%)")
        print(f"  Current TP needs +4.0% move")
        if vol < 4.0:
            print(f"  WARNING: 24h volatility {vol:.1f}% < TP target 4.0% — TP unlikely to hit in current conditions")
        else:
            print(f"  Volatility {vol:.1f}% >= TP 4.0% — TP achievable")

    # 6. Dimension agreement analysis
    print()
    print("=== Dimension Agreement vs Outcome ===")
    agree_correct = agree_wrong = disagree_correct = disagree_wrong = 0
    for i in range(len(signals) - 6):
        s = signals[i]
        future = signals[i + 6]
        if s["price"] <= 0 or future["price"] <= 0:
            continue
        nr = s["net_raw"]
        if abs(nr) < 0.15:
            continue
        price_change = (future["price"] - s["price"]) / s["price"] * 100
        predicted_up = nr > 0
        actual_correct = (predicted_up and price_change > 0.05) or (not predicted_up and price_change < -0.05)

        # Count aligned dimensions
        dirs = [s["struct"], s["div"], s["flow"]]
        bullish = sum(1 for d in dirs if d == "BULLISH")
        bearish = sum(1 for d in dirs if d == "BEARISH")
        all_agree = (bullish >= 2 and predicted_up) or (bearish >= 2 and not predicted_up)

        if all_agree and actual_correct:
            agree_correct += 1
        elif all_agree and not actual_correct:
            agree_wrong += 1
        elif not all_agree and actual_correct:
            disagree_correct += 1
        elif not all_agree:
            disagree_wrong += 1

    if agree_correct + agree_wrong > 0:
        agree_wr = agree_correct / (agree_correct + agree_wrong) * 100
        print(f"  2+ dimensions agree: {agree_correct}/{agree_correct + agree_wrong} ({agree_wr:.0f}%) correct")
    if disagree_correct + disagree_wrong > 0:
        dis_wr = disagree_correct / (disagree_correct + disagree_wrong) * 100
        print(f"  Dimensions conflict: {disagree_correct}/{disagree_correct + disagree_wrong} ({dis_wr:.0f}%) correct")

if __name__ == "__main__":
    main()
