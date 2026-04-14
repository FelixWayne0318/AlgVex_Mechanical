#!/usr/bin/env python3
"""Three-way cross-validation: signal_engine fill@close vs fill@next-open vs TV."""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from srp.strategy.signal_engine import SRPSignalEngine, SRPConfig
from datetime import datetime, timezone
from collections import Counter
import requests

# === Fetch data ===
days = int(sys.argv[1]) if len(sys.argv) > 1 else 470
now_ms = int(time.time() * 1000)
start_ms = now_ms - (days * 24 * 3600 * 1000)
current_start, all_bars = start_ms, []
print(f"Fetching {days}d data...", flush=True)
while current_start < now_ms:
    resp = requests.get("https://fapi.binance.com/fapi/v1/klines", params={
        'symbol': 'BTCUSDT', 'interval': '30m',
        'startTime': current_start, 'endTime': now_ms, 'limit': 1500,
    }, timeout=30)
    data = resp.json() if resp.ok else None
    if not data: break
    all_bars.extend(data)
    if len(data) < 1500: break
    current_start = int(data[-1][0]) + 1
seen = set()
klines = sorted([b for b in all_bars if b[0] not in seen and not seen.add(b[0])], key=lambda x: x[0])
if klines: klines = klines[:-1]
print(f"Bars: {len(klines)}")

def run_engine(klines, fill_at_open=False):
    cfg = SRPConfig()
    engine = SRPSignalEngine(cfg)
    eq, pos, avg, dr = 1500.0, 0.0, 0.0, 0
    deals, current = [], None
    pending = None
    for i, k in enumerate(klines):
        ts=int(k[0]); o,h,l,c,v=float(k[1]),float(k[2]),float(k[3]),float(k[4]),float(k[5])
        dt = datetime.fromtimestamp(ts/1000,tz=timezone.utc).strftime('%m-%d %H:%M')
        # Execute pending at open
        if fill_at_open and pending:
            ps, pending = pending, None
            fp = o  # fill price = open
            if ps.action.startswith('EXIT') and pos > 0:
                pnl = pos*(fp-avg) if dr==1 else pos*(avg-fp)
                eq += pnl - pos*fp*0.00075*2
                if current: current.update(exit_time=dt, exit_px=fp, pnl=round(pnl-pos*fp*0.00075*2,2), exit_type=ps.action.replace('EXIT_','')); deals.append(current); current=None
                pos=avg=0.0; dr=0; engine.update_fill(0,0,True)
            elif ps.action.startswith('ENTRY'):
                qty=ps.quantity; eq-=qty*fp*0.00075; pos=qty; avg=fp; dr=1 if 'LONG' in ps.action else -1
                engine.update_fill(fp,qty,False); current={'entry_time':dt,'entry_px':fp,'dir':'L' if dr==1 else 'S','dcas':0}
            elif ps.action.startswith('DCA'):
                qty=ps.quantity; eq-=qty*fp*0.00075; tc=avg*pos+fp*qty; pos+=qty; avg=tc/pos
                engine.update_fill(fp,qty,False);
                if current: current['dcas']+=1
        sig = engine.on_bar(o,h,l,c,v,eq,ts)
        if sig.action == 'HOLD': continue
        if fill_at_open:
            pending = sig
        else:
            fp = c
            if sig.action.startswith('EXIT') and pos > 0:
                pnl = pos*(fp-avg) if dr==1 else pos*(avg-fp)
                eq += pnl - pos*fp*0.00075*2
                if current: current.update(exit_time=dt, exit_px=fp, pnl=round(pnl-pos*fp*0.00075*2,2), exit_type=sig.action.replace('EXIT_','')); deals.append(current); current=None
                pos=avg=0.0; dr=0; engine.update_fill(0,0,True)
            elif sig.action.startswith('ENTRY'):
                qty=sig.quantity; eq-=qty*fp*0.00075; pos=qty; avg=fp; dr=1 if 'LONG' in sig.action else -1
                engine.update_fill(fp,qty,False); current={'entry_time':dt,'entry_px':fp,'dir':'L' if dr==1 else 'S','dcas':0}
            elif sig.action.startswith('DCA'):
                qty=sig.quantity; eq-=qty*fp*0.00075; tc=avg*pos+fp*qty; pos+=qty; avg=tc/pos
                engine.update_fill(fp,qty,False)
                if current: current['dcas']+=1
    return deals, eq

deals_a, eq_a = run_engine(klines, fill_at_open=False)
deals_b, eq_b = run_engine(klines, fill_at_open=True)

print(f"\n{'='*90}")
print(f"METHOD A (fill@close):     {len(deals_a)} deals, equity ${eq_a:.2f}, PnL ${eq_a-1500:+.2f}")
print(f"METHOD B (fill@next-open): {len(deals_b)} deals, equity ${eq_b:.2f}, PnL ${eq_b-1500:+.2f}")
print(f"TV reference:              156 deals, PnL ~$46")
print(f"{'='*90}")

# Exit types
a_types = Counter(d.get('exit_type','?') for d in deals_a)
b_types = Counter(d.get('exit_type','?') for d in deals_b)
a_dirs = Counter(d['dir'] for d in deals_a)
b_dirs = Counter(d['dir'] for d in deals_b)
print(f"\nA exit types: {dict(a_types)}, dirs: {dict(a_dirs)}")
print(f"B exit types: {dict(b_types)}, dirs: {dict(b_dirs)}")

# Last 25 deals
print(f"\n--- Last 25 deals comparison ---")
print(f"{'#':>3} | {'A_entry':>12} {'A_exit':>12} {'A_dir':>2} {'A_dca':>3} {'A_type':>6} {'A_pnl':>8} | {'B_entry':>12} {'B_exit':>12} {'B_pnl':>8} | {'match':>5}")
start = max(0, max(len(deals_a), len(deals_b)) - 25)
for i in range(start, max(len(deals_a), len(deals_b))):
    a = deals_a[i] if i < len(deals_a) else {}
    b = deals_b[i] if i < len(deals_b) else {}
    match = 'YES' if a.get('entry_time') == b.get('entry_time') else 'NO'
    print(f"{i+1:3d} | {a.get('entry_time',''):>12} {a.get('exit_time',''):>12} {a.get('dir',''):>2} {a.get('dcas',0):>3} {a.get('exit_type',''):>6} {a.get('pnl',0):>+8.2f} | {b.get('entry_time',''):>12} {b.get('exit_time',''):>12} {b.get('pnl',0):>+8.2f} | {match:>5}")

# Check for divergence points
diverged = 0
for i in range(min(len(deals_a), len(deals_b))):
    if deals_a[i].get('entry_time') != deals_b[i].get('entry_time'):
        if diverged == 0:
            print(f"\n⚠️ FIRST DIVERGENCE at deal #{i+1}:")
            print(f"  A: entry {deals_a[i].get('entry_time')} @ {deals_a[i].get('entry_px')}")
            print(f"  B: entry {deals_b[i].get('entry_time')} @ {deals_b[i].get('entry_px')}")
        diverged += 1
if diverged == 0:
    print(f"\n✅ Methods A and B produce IDENTICAL deal sequences ({len(deals_a)} deals)")
else:
    print(f"\n⚠️ {diverged} deals diverged between methods A and B")
