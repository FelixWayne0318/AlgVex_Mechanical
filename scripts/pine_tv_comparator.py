#!/usr/bin/env python3
"""
Pine Script Execution Model Simulator + SRP v5.0 Parity Comparator

Faithfully simulates TradingView's strategy broker emulator:
  - Orders fill at NEXT bar open (process_orders_on_close=false)
  - strategy.position_size / opentrades update ONLY at fill time
  - var variables (v_qty, dealcount etc.) update IMMEDIATELY inline
  - strategy.close() + strategy.entry() same bar → both fill, close first
  - pyramiding=5 allows up to 5 entries

Then compares bar-by-bar against backtest_srp_v5_exact.py to find any
remaining differences.

Usage:
    python3 scripts/pine_tv_comparator.py --days 456
    python3 scripts/pine_tv_comparator.py --days 365 --verbose
"""

import argparse, sys, os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.backtest_srp_v5_exact import fetch_klines, pine_rma, pine_vwma, pine_mfi, run_backtest


# =============================================================================
# Pine Broker Emulator
# =============================================================================

class PineBroker:
    """Simulates TradingView's strategy broker emulator.

    Key invariant: strategy.position_size, strategy.position_avg_price,
    strategy.opentrades are READ-ONLY during script execution. They only
    change when orders FILL at the next bar's open.
    """

    def __init__(self, initial_capital=1000, commission_pct=0.075, pyramiding=5):
        self.initial_capital = initial_capital
        self.equity = initial_capital
        self.commission = commission_pct / 100

        # --- Broker-managed state (updated at fill time ONLY) ---
        self.position_size = 0.0
        self.position_avg_price = 0.0
        self.opentrades = 0
        self.pyramiding = pyramiding
        self._entry_fill_prices = []  # strategy.opentrades.entry_price(i)

        # --- Order queue (generated during script, filled at next bar open) ---
        self._close_order = None     # comment string, or None
        self._entry_orders = []      # [(qty, comment), ...]

        # --- Results ---
        self.trades = []

    def strategy_close(self, comment=""):
        """Pine: strategy.close('LONG', comment=...)"""
        self._close_order = comment

    def strategy_entry(self, qty, comment=""):
        """Pine: strategy.entry('LONG', strategy.long, qty=qty, comment=...)"""
        self._entry_orders.append((qty, comment))

    def entry_price(self, index):
        """Pine: strategy.opentrades.entry_price(index)"""
        if 0 <= index < len(self._entry_fill_prices):
            return self._entry_fill_prices[index]
        return 0.0

    def process_fills(self, open_price):
        """Called at bar open. Fills pending orders: close first, then entries."""
        filled_close = False
        filled_entries = 0

        # 1. Close fills first
        if self._close_order is not None:
            if self.position_size > 0:
                pnl = self.position_size * (open_price - self.position_avg_price)
                fee = self.position_size * open_price * self.commission
                self.equity += pnl - fee
                self.trades.append({
                    "pnl": pnl - fee,
                    "type": self._close_order,
                    "dca": self.opentrades - 1,
                    "close_price": open_price,
                })
                filled_close = True
            # Reset position
            self.position_size = 0.0
            self.position_avg_price = 0.0
            self.opentrades = 0
            self._entry_fill_prices = []
            self._close_order = None

        # 2. Entries fill after close
        for qty, comment in self._entry_orders:
            if self.opentrades < self.pyramiding:
                fee = qty * open_price * self.commission
                self.equity -= fee
                # VWAP average
                total_cost = (self.position_size * self.position_avg_price
                              + qty * open_price)
                self.position_size += qty
                self.position_avg_price = (total_cost / self.position_size
                                           if self.position_size > 0 else 0)
                self.opentrades += 1
                self._entry_fill_prices.append(open_price)
                filled_entries += 1
        self._entry_orders = []

        return filled_close, filled_entries


# =============================================================================
# Pine Script SRP v5.0 — line-by-line translation
# =============================================================================

def run_pine_simulator(klines, params=None):
    """Run SRP v5.0 Pine strategy through the Pine broker emulator.

    Every line mirrors docs/SRP_strategy_v5.pine exactly.
    """
    p = {
        "srp_pct": 1.0, "vwma_len": 14, "rsi_mfi_below": 55, "rsi_mfi_above": 100,
        "base_pct": 0.10, "dca_mult": 1.5, "dca_max": 4, "dca_spacing": 3.0,
        "mintp": 0.025, "max_loss": 0.06, "initial_capital": 1500,
        "dca_type": "volume",
    }
    if params:
        p.update(params)

    # Parse bars
    bars = []
    for k in klines:
        o, h, l, c, v = float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])
        bars.append({"o": o, "h": h, "l": l, "c": c, "v": v, "ts": int(k[0])})

    n = len(bars)
    hlc3 = [(b["h"] + b["l"] + b["c"]) / 3.0 for b in bars]
    closes = [b["c"] for b in bars]
    volumes = [b["v"] for b in bars]

    # Pre-compute RSI
    changes = [0.0] + [closes[i] - closes[i - 1] for i in range(1, n)]
    gains = [max(c, 0) for c in changes]
    losses = [max(-c, 0) for c in changes]
    avg_gain = pine_rma(gains, 7)
    avg_loss = pine_rma(losses, 7)
    rsi = [50.0] * n
    for i in range(7, n):
        if avg_loss[i] == 0:
            rsi[i] = 100.0
        elif avg_gain[i] == 0:
            rsi[i] = 0.0
        else:
            rsi[i] = 100.0 - (100.0 / (1.0 + avg_gain[i] / avg_loss[i]))

    # --- Broker ---
    broker = PineBroker(
        initial_capital=p["initial_capital"],
        commission_pct=0.075,
        pyramiding=p["dca_max"] + 1,  # sonum
    )
    sonum = p["dca_max"] + 1
    MINTICK = 0.1

    # --- Pine var state (updated inline, persist across bars) ---
    socounter = 0
    dealcount = 0
    v_qty = v_cost = v_avg = v_last_px = 0.0
    v_count = 0
    deal_base = 0.0

    # --- Bar-by-bar log for comparison ---
    bar_log = []

    for i in range(n):
        c = bars[i]["c"]
        h = bars[i]["h"]
        l = bars[i]["l"]
        o = bars[i]["o"]

        # Pine fills pending orders at bar open
        broker.process_fills(o)

        # Skip bars where indicators aren't ready
        if i < p["vwma_len"]:
            bar_log.append(None)
            continue

        # =================================================================
        # Indicators (Pine lines 57-68)
        # =================================================================
        vwma_val = pine_vwma(hlc3, volumes, p["vwma_len"], i)
        upper_band = vwma_val * (1 + p["srp_pct"] / 100)
        lower_band = vwma_val * (1 - p["srp_pct"] / 100)

        mfi_val = pine_mfi(hlc3, volumes, 7, i)
        rsi_mfi = abs(rsi[i] + mfi_val / 2)

        sig_long = l <= lower_band and rsi_mfi < p["rsi_mfi_below"]
        sig_short = h >= upper_band and rsi_mfi > p["rsi_mfi_above"]

        # =================================================================
        # Safety Net (Pine lines 115-124)
        # =================================================================
        if broker.position_size > 0 and broker.position_avg_price > 0:
            dd = (broker.position_avg_price - c) / broker.position_avg_price
            if dd >= p["max_loss"]:
                broker.strategy_close(comment="SL")
                v_qty = v_cost = v_avg = v_last_px = 0.0
                v_count = 0
                socounter = 0

        # =================================================================
        # Exit Logic (Pine lines 131-148)
        # Pine: if / else if — only one fires per bar
        # =================================================================
        tp_target = v_avg * (1 + p["mintp"]) if v_avg > 0 else float("inf")
        if broker.position_size > 0 and v_avg > 0 and c > tp_target:
            broker.strategy_close(comment="TP")
            v_qty = v_cost = v_avg = v_last_px = 0.0
            v_count = 0
            socounter = 0
        elif broker.position_size > 0 and sig_short and c > broker.position_avg_price:
            broker.strategy_close(comment="Band")
            v_qty = v_cost = v_avg = v_last_px = 0.0
            v_count = 0
            socounter = 0

        # =================================================================
        # Virtual DCA (Pine lines 154-160)
        # =================================================================
        if (broker.position_size > 0 and broker.opentrades >= sonum
                and v_last_px > 0):
            chg = abs((c - v_last_px) / v_last_px) * 100
            if chg > p["dca_spacing"] and c < v_last_px:
                vdca_qty = v_qty * p["dca_mult"]
                v_cost += vdca_qty * c
                v_qty += vdca_qty
                v_avg = v_cost / v_qty
                v_last_px = c
                v_count += 1

        # =================================================================
        # Entry: base deal (Pine lines 167-176)
        # =================================================================
        if sig_long and broker.opentrades == 0:
            socounter = 0
            dealcount += 1
            deal_base = broker.equity * p["base_pct"]
            qty = deal_base / c
            broker.strategy_entry(qty, comment=f"D#{dealcount}")
            v_qty = qty
            v_cost = qty * c
            v_avg = c
            v_last_px = c
            v_count = 0

        # =================================================================
        # Entry: DCA (Pine lines 179-187)
        # Separate `if` block (not elif) — matches Pine
        # =================================================================
        if (sig_long and broker.opentrades > 0
                and broker.opentrades < sonum):
            # SOconditions()
            last_deal_price = broker.entry_price(broker.opentrades - 1)
            if last_deal_price > 0:
                chg_from_last = abs((c - last_deal_price) / c) * 100
            else:
                chg_from_last = 0.0
            next_so_price = (broker.position_avg_price
                             - round(p["dca_spacing"] / 100
                                     * broker.position_avg_price / MINTICK)
                             * MINTICK)
            if chg_from_last > p["dca_spacing"] and c < next_so_price:
                socounter += 1
                if p["dca_type"] == "volume":
                    dca_qty = broker.position_size * p["dca_mult"]
                else:
                    dca_qty = (deal_base * (p["dca_mult"] ** socounter)) / c
                broker.strategy_entry(dca_qty, comment=f"SO#{socounter}")
                v_cost += dca_qty * c
                v_qty += dca_qty
                v_avg = v_cost / v_qty
                v_last_px = c
                v_count += 1

        # =================================================================
        # Sync (Pine lines 193-199)
        # =================================================================
        if broker.position_size == 0 and v_qty > 0:
            v_qty = v_cost = v_avg = v_last_px = 0.0
            v_count = 0
            socounter = 0

        # Log state for comparison
        bar_log.append({
            "i": i,
            "pos_size": broker.position_size,
            "pos_avg": broker.position_avg_price,
            "opentrades": broker.opentrades,
            "v_avg": v_avg,
            "dealcount": dealcount,
            "pending_close": broker._close_order is not None,
            "pending_entry": len(broker._entry_orders) > 0,
        })

    # Close open position at end
    if broker.position_size > 0:
        pnl = broker.position_size * (bars[-1]["c"] - broker.position_avg_price)
        fee = broker.position_size * bars[-1]["c"] * broker.commission
        broker.equity += pnl - fee
        broker.trades.append({
            "pnl": pnl - fee, "type": "EOD", "dca": socounter,
            "close_price": bars[-1]["c"],
        })

    # Stats
    capital = p["initial_capital"]
    total_pnl = broker.equity - capital
    wins = sum(1 for t in broker.trades if t["pnl"] > 0)
    losses_n = sum(1 for t in broker.trades if t["pnl"] <= 0)
    gp = sum(t["pnl"] for t in broker.trades if t["pnl"] > 0)
    gl = abs(sum(t["pnl"] for t in broker.trades if t["pnl"] <= 0))
    pf = gp / gl if gl > 0 else float("inf")

    exit_types = {}
    for t in broker.trades:
        tp = t["type"].split()[0]  # "SL 6.1%" → "SL"
        exit_types[tp] = exit_types.get(tp, 0) + 1

    return {
        "pnl": round(total_pnl, 2),
        "return_pct": round(total_pnl / capital * 100, 2),
        "trades": len(broker.trades),
        "wins": wins,
        "losses": losses_n,
        "win_rate": round(wins / len(broker.trades) * 100, 2) if broker.trades else 0,
        "profit_factor": round(pf, 2) if pf != float("inf") else "inf",
        "deals": dealcount,
        "exit_types": exit_types,
        "gross_profit": round(gp, 2),
        "gross_loss": round(gl, 2),
        "bar_log": bar_log,
        "trade_list": broker.trades,
    }


# =============================================================================
# Comparison Engine
# =============================================================================

def compare_results(pine, python, verbose=False):
    """Compare Pine simulator vs Python backtest results."""

    print(f"\n{'=' * 78}")
    print(f"  Pine Simulator vs Python Backtest — Parity Comparison")
    print(f"{'=' * 78}")

    fields = [
        ("Deals",         "deals"),
        ("Trades",        "trades"),
        ("Net PnL",       "pnl"),
        ("Return %",      "return_pct"),
        ("Win Rate %",    "win_rate"),
        ("Profit Factor", "profit_factor"),
        ("Gross Profit",  "gross_profit"),
        ("Gross Loss",    "gross_loss"),
    ]

    all_match = True
    print(f"\n  {'Metric':<16} {'Pine':>12} {'Python':>12} {'Delta':>12} {'Match':>6}")
    print(f"  {'-' * 60}")

    for label, key in fields:
        pv = pine[key]
        yv = python[key]
        if isinstance(pv, str) or isinstance(yv, str):
            delta = "—"
            match = str(pv) == str(yv)
        elif isinstance(pv, int):
            delta = pv - yv
            match = delta == 0
        else:
            delta = round(pv - yv, 2)
            match = abs(delta) < 0.01
        if not match:
            all_match = False
        m = "✅" if match else "❌"
        print(f"  {label:<16} {pv:>12} {yv:>12} {str(delta):>12} {m:>6}")

    # Exit type comparison
    all_exit_types = sorted(set(list(pine.get("exit_types", {}).keys())
                                + list(python.get("exit_types", {}).keys())))
    print(f"\n  Exit Types:")
    for et in all_exit_types:
        pv = pine.get("exit_types", {}).get(et, 0)
        yv = python.get("exit_types", {}).get(et, 0)
        m = "✅" if pv == yv else "❌"
        if pv != yv:
            all_match = False
        print(f"    {et:<10} Pine: {pv:>4}   Python: {yv:>4}   {m}")

    # Summary
    print(f"\n  {'=' * 60}")
    if all_match:
        print(f"  ✅ PERFECT PARITY — Pine simulator and Python backtest match!")
    else:
        print(f"  ❌ DIFFERENCES FOUND — see details above")

    # Trade-by-trade comparison (first N differences)
    if verbose and not all_match:
        pine_trades = pine.get("trade_list", [])
        python_exit = python.get("exit_types", {})
        print(f"\n  First trade-level differences (Pine has {len(pine_trades)} trades):")
        # Show first 10 Pine trades
        for idx, t in enumerate(pine_trades[:20]):
            tp = t["type"].split()[0]
            pnl = t["pnl"]
            print(f"    [{idx+1:>3}] {tp:<6} PnL: ${pnl:>+8.2f}  DCA: {t['dca']}")

    print(f"{'=' * 78}\n")
    return all_match


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Pine TV Simulator vs Python Comparator")
    parser.add_argument("--days", type=int, default=456)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    klines = fetch_klines(days=args.days)
    if len(klines) < 100:
        print("ERROR: Insufficient data")
        sys.exit(1)

    print(f"\nData: {len(klines)} bars ({args.days} days)")

    # Run Pine simulator
    print("Running Pine simulator...")
    pine_result = run_pine_simulator(klines)

    # Run Python backtest
    print("Running Python backtest...")
    python_result = run_backtest(klines)

    # Compare
    match = compare_results(pine_result, python_result, verbose=args.verbose)

    # Individual summaries
    for label, r in [("Pine Simulator", pine_result), ("Python Backtest", python_result)]:
        pf = r['profit_factor'] if isinstance(r['profit_factor'], str) else f"{r['profit_factor']:.2f}"
        print(f"  [{label}]")
        print(f"    PnL: ${r['pnl']:>+10.2f} ({r['return_pct']:>+6.2f}%)")
        print(f"    Trades: {r['trades']}  Deals: {r['deals']}  "
              f"WR: {r['win_rate']}%  PF: {pf}")
        exits = "  ".join(f"{k}:{v}" for k, v in sorted(r.get("exit_types", {}).items()))
        print(f"    Exits: {exits}")
        print()

    sys.exit(0 if match else 1)


if __name__ == "__main__":
    main()
