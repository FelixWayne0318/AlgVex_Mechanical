#!/usr/bin/env python3
"""
SRP v5.0 Exact Pine Script Replication
Line-by-line translation of docs/SRP_strategy_v5.pine

Every calculation mirrors Pine exactly:
- ta.vwma(hlc3, 14) → vwma using hlc3 and volume
- ta.rma(x, 7) → Wilder's RMA (SMA seed + exponential)
- ta.mfi(hlc3, 7) → Money Flow Index
- SOconditions: calcChangeFromLastDeal > spacing AND close < avg - spacing%
- DCA qty: strategy.position_size × mult (volume multiply)
- strategy.position_avg_price: VWAP of all entries
- Virtual DCA: continues after real DCA exhausted

Usage:
    python3 scripts/backtest_srp_v5_exact.py --days 365
"""

import os, sys, json, time, argparse, logging
from datetime import datetime, timezone
from typing import List, Dict, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SRP-v5-Exact")

BINANCE_FUTURES_BASE = "https://fapi.binance.com"
COMMISSION = 0.00075  # 0.075% per side
MINTICK = 0.1         # BTCUSDT price tick size


def fetch_klines(symbol="BTCUSDT", interval="30m", days=30):
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (days * 24 * 3600 * 1000)
    current_start, limit, all_bars = start_ms, 1500, []
    logger.info(f"Fetching {symbol} {interval}: {days} days")
    while current_start < now_ms:
        url = (f"{BINANCE_FUTURES_BASE}/fapi/v1/klines"
               f"?symbol={symbol}&interval={interval}"
               f"&startTime={current_start}&endTime={now_ms}&limit={limit}")
        for attempt in range(4):
            try:
                req = Request(url, headers={"User-Agent": "AlgVex/5.0"})
                with urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read())
                break
            except (URLError, OSError):
                time.sleep(2 ** (attempt + 1))
                data = None
        if not data:
            break
        all_bars.extend(data)
        if len(data) < limit:
            break
        current_start = int(data[-1][0]) + 1
    seen = set()
    unique = [b for b in all_bars if b[0] not in seen and not seen.add(b[0])]
    unique.sort(key=lambda x: x[0])
    logger.info(f"Fetched {len(unique)} bars")
    return unique


# =============================================================================
# Pine Indicator Replications
# =============================================================================

def pine_rma(values, period):
    """Exact Pine ta.rma: SMA seed for first `period` values, then Wilder's."""
    n = len(values)
    result = [0.0] * n
    if n < period:
        return result
    # SMA seed
    result[period - 1] = sum(values[:period]) / period
    # Wilder's smoothing
    alpha = 1.0 / period
    for i in range(period, n):
        result[i] = alpha * values[i] + (1 - alpha) * result[i - 1]
    return result


def pine_vwma(hlc3_list, vol_list, length, i):
    """Exact Pine ta.vwma(hlc3, length) at bar i."""
    if i < length - 1:
        return hlc3_list[i]
    num = sum(hlc3_list[j] * vol_list[j] for j in range(i - length + 1, i + 1))
    den = sum(vol_list[j] for j in range(i - length + 1, i + 1))
    return num / den if den > 0 else hlc3_list[i]


def pine_mfi(tp_list, vol_list, period, i):
    """Exact Pine ta.mfi(hlc3, period) at bar i."""
    if i < period:
        return 50.0
    pos_flow = 0.0
    neg_flow = 0.0
    for j in range(i - period + 1, i + 1):
        mf = tp_list[j] * vol_list[j]
        if j > 0 and tp_list[j] > tp_list[j - 1]:
            pos_flow += mf
        elif j > 0 and tp_list[j] < tp_list[j - 1]:
            neg_flow += mf
    if neg_flow == 0:
        return 100.0
    if pos_flow == 0:
        return 0.0
    return 100.0 - (100.0 / (1.0 + pos_flow / neg_flow))


# =============================================================================
# Strategy: Exact Pine v5.0 replication
# =============================================================================

def run_backtest(klines, params=None):
    p = {
        "srp_pct": 1.0, "vwma_len": 14, "rsi_mfi_below": 55, "rsi_mfi_above": 100,
        "base_pct": 0.10, "dca_mult": 1.5, "dca_max": 4, "dca_spacing": 3.0,
        "mintp": 0.025, "max_loss": 0.06, "initial_capital": 1500,
        "dca_type": "volume",  # "volume" = pos_size × mult, "base" = base × mult^step
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

    # Pre-compute RSI via RMA (Pine: ta.rma with period 7)
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

    # =========================================================================
    # Pine state variables
    # =========================================================================

    pos_size = 0.0
    pos_avg = 0.0
    pos_cost = 0.0
    opentrades = 0
    entry_prices = []
    socounter = 0
    dealcount = 0
    v_qty = v_cost = v_avg = v_last_px = 0.0
    v_count = 0

    capital = p["initial_capital"]
    equity = capital
    deal_base = 0.0
    trades = []
    sonum = p["dca_max"] + 1
    warmup = p["vwma_len"]  # FIX D2: match Pine (~14 bars, not 20)
    equity_curve = []
    max_eq = capital
    max_dd = 0.0

    # Pine order queue: signal on bar N → execute at bar N+1 open
    pending_close = False
    pending_close_type = ""
    pending_entry = None  # "base" or "dca"
    pending_qty = 0.0     # qty pre-stored at signal time

    for i in range(n):
        c = bars[i]["c"]
        h = bars[i]["h"]
        l = bars[i]["l"]
        o = bars[i]["o"]

        if i < warmup:
            equity_curve.append(capital)
            continue

        # =================================================================
        # Execute pending orders at this bar's OPEN
        # Pine broker: close fills first, then entries. No re-checking.
        # v_* NOT touched here (already set at signal time).
        # =================================================================

        if pending_close:
            pnl = pos_size * (o - pos_avg)
            fee = pos_size * o * COMMISSION
            equity += pnl - fee
            trades.append({"pnl": pnl - fee, "type": pending_close_type, "dca": socounter,
                           "date": datetime.fromtimestamp(bars[i]["ts"]/1000).strftime("%Y-%m-%d")})
            pos_size = pos_avg = pos_cost = 0.0
            opentrades = 0
            entry_prices = []
            # v_* already reset at signal time (Pine behavior)
            pending_close = False
            pending_close_type = ""

        if pending_entry is not None:
            qty = pending_qty
            fee = qty * o * COMMISSION
            equity -= fee
            if opentrades == 0:
                # New position (base entry, or DCA-after-close)
                pos_cost = qty * o
                pos_size = qty
                pos_avg = o
                opentrades = 1
                entry_prices = [o]
            else:
                # Add to existing position (normal DCA)
                pos_cost += qty * o
                pos_size += qty
                pos_avg = pos_cost / pos_size
                opentrades += 1
                entry_prices.append(o)
            # v_* already set at signal time (Pine behavior)
            pending_entry = None
            pending_qty = 0.0

        # =================================================================
        # Indicators
        # =================================================================
        vwma_val = pine_vwma(hlc3, volumes, p["vwma_len"], i)
        upper_band = vwma_val * (1 + p["srp_pct"] / 100)
        lower_band = vwma_val * (1 - p["srp_pct"] / 100)

        mfi_val = pine_mfi(hlc3, volumes, 7, i)
        rsi_mfi = abs(rsi[i] + mfi_val / 2)

        sig_long = l <= lower_band and rsi_mfi < p["rsi_mfi_below"]
        sig_short = h >= upper_band and rsi_mfi > p["rsi_mfi_above"]

        # =================================================================
        # Safety Net (Pine L115-124) → close + immediate v_* reset
        # =================================================================
        if pos_size > 0 and pos_avg > 0:
            dd = (pos_avg - c) / pos_avg
            if dd >= p["max_loss"]:
                pending_close = True
                pending_close_type = "SL"
                v_qty = v_cost = v_avg = v_last_px = 0.0
                v_count = 0
                socounter = 0

        # =================================================================
        # Exit: TP / Band (Pine L131-148) — if/else-if + immediate v_* reset
        # =================================================================
        tp_target = v_avg * (1 + p["mintp"]) if v_avg > 0 else float("inf")
        if pos_size > 0 and v_avg > 0 and c > tp_target:
            pending_close = True
            pending_close_type = "TP"
            v_qty = v_cost = v_avg = v_last_px = 0.0
            v_count = 0
            socounter = 0
        elif pos_size > 0 and sig_short and c > pos_avg:
            pending_close = True
            pending_close_type = "Band"
            v_qty = v_cost = v_avg = v_last_px = 0.0
            v_count = 0
            socounter = 0

        # =================================================================
        # Virtual DCA (Pine L154-160) — no guard needed, v_last_px=0 blocks
        # =================================================================
        if pos_size > 0 and opentrades >= sonum and v_last_px > 0:
            chg = abs((c - v_last_px) / v_last_px) * 100
            if chg > p["dca_spacing"] and c < v_last_px:
                vdca_qty = v_qty * p["dca_mult"]
                v_cost += vdca_qty * c
                v_qty += vdca_qty
                v_avg = v_cost / v_qty
                v_last_px = c
                v_count += 1

        # =================================================================
        # Entry: base deal (Pine L167-176)
        # v_*, dealcount, qty all set at signal time using close
        # =================================================================
        if sig_long and opentrades == 0:
            socounter = 0
            dealcount += 1
            deal_base = equity * p["base_pct"]  # equity × base% at deal start
            qty = deal_base / c
            pending_entry = "base"
            pending_qty = qty
            v_qty = qty
            v_cost = qty * c
            v_avg = c
            v_last_px = c
            v_count = 0

        # =================================================================
        # Entry: DCA (Pine L179-187) — separate `if`, not `elif`
        # Uses opentrades/position_size from broker (not yet updated)
        # =================================================================
        if sig_long and opentrades > 0 and opentrades < sonum:
            last_deal = entry_prices[opentrades - 1] if opentrades <= len(entry_prices) else 0
            chg_from_last = abs((c - last_deal) / c) * 100 if last_deal > 0 else 0
            next_so = pos_avg - round(p["dca_spacing"] / 100 * pos_avg / MINTICK) * MINTICK
            if chg_from_last > p["dca_spacing"] and c < next_so:
                socounter += 1
                if p["dca_type"] == "volume":
                    dca_qty = pos_size * p["dca_mult"]
                else:  # "base" multiply: base × mult^step / price
                    dca_qty = (deal_base * (p["dca_mult"] ** socounter)) / c
                pending_entry = "dca"
                pending_qty = dca_qty
                v_cost += dca_qty * c
                v_qty += dca_qty
                v_avg = v_cost / v_qty
                v_last_px = c
                v_count += 1

        # =================================================================
        # Sync (Pine L193-199)
        # =================================================================
        if pos_size == 0 and v_qty > 0:
            v_qty = v_cost = v_avg = v_last_px = 0.0
            v_count = 0
            socounter = 0

        # Track equity
        unrealized = pos_size * (c - pos_avg) if pos_size > 0 else 0
        cur_eq = equity + unrealized
        equity_curve.append(cur_eq)
        if cur_eq > max_eq:
            max_eq = cur_eq
        dd_pct = (max_eq - cur_eq) / max_eq * 100 if max_eq > 0 else 0
        if dd_pct > max_dd:
            max_dd = dd_pct

    # Close open position at end
    if pos_size > 0:
        pnl = pos_size * (bars[-1]["c"] - pos_avg)
        fee = pos_size * bars[-1]["c"] * COMMISSION
        equity += pnl - fee
        trades.append({"pnl": pnl - fee, "type": "EOD", "dca": socounter,
                       "date": datetime.fromtimestamp(bars[-1]["ts"]/1000).strftime("%Y-%m-%d")})

    # Stats
    total_pnl = equity - capital
    wins = sum(1 for t in trades if t["pnl"] > 0)
    losses_n = sum(1 for t in trades if t["pnl"] <= 0)
    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0))
    pf = gp / gl if gl > 0 else float("inf")

    return {
        "pnl": round(total_pnl, 2),
        "return_pct": round(total_pnl / capital * 100, 2),
        "trades": len(trades),
        "wins": wins,
        "losses": losses_n,
        "win_rate": round(wins / len(trades) * 100, 2) if trades else 0,
        "max_dd_pct": round(max_dd, 2),
        "profit_factor": round(pf, 2) if pf != float("inf") else "inf",
        "deals": dealcount,
        "exit_types": {
            "TP": sum(1 for t in trades if t["type"] == "TP"),
            "Band": sum(1 for t in trades if t["type"] == "Band"),
            "SL": sum(1 for t in trades if t["type"] == "SL"),
            "EOD": sum(1 for t in trades if t["type"] == "EOD"),
        },
        "gross_profit": round(gp, 2),
        "gross_loss": round(gl, 2),
        "trade_pnls": [t["pnl"] for t in trades],
    }


def main():
    parser = argparse.ArgumentParser(description="SRP v5.0 Exact Pine Replication")
    parser.add_argument("--days", type=int, default=365)
    args = parser.parse_args()

    klines = fetch_klines(days=args.days)
    if len(klines) < 100:
        logger.error("Insufficient data")
        sys.exit(1)

    r = run_backtest(klines)
    bh = (float(klines[-1][4]) - float(klines[0][4])) / float(klines[0][4]) * 100

    print(f"\n{'=' * 70}")
    print(f"  SRP v5.0 Exact Pine Replication ({args.days}d, {len(klines)} bars)")
    print(f"{'=' * 70}")
    print(f"  Net PnL:       ${r['pnl']:>+10.2f} ({r['return_pct']:>+6.2f}%)")
    print(f"  Buy & Hold:    {bh:>+6.2f}%")
    print(f"  Trades:        {r['trades']} (Deals: {r['deals']})")
    print(f"  Win Rate:      {r['win_rate']}% ({r['wins']}W / {r['losses']}L)")
    print(f"  Max DD:        {r['max_dd_pct']}%")
    pf = r['profit_factor'] if isinstance(r['profit_factor'], str) else f"{r['profit_factor']:.2f}"
    print(f"  Profit Factor: {pf}")
    print(f"  Gross Profit:  ${r['gross_profit']:>+10.2f}")
    print(f"  Gross Loss:    ${r['gross_loss']:>10.2f}")
    print(f"  Exits:         TP:{r['exit_types']['TP']} Band:{r['exit_types']['Band']} SL:{r['exit_types']['SL']} EOD:{r['exit_types']['EOD']}")
    print(f"{'=' * 70}\n")


if __name__ == "__main__":
    main()
