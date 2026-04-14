#!/usr/bin/env python3
"""Verify backtest data authenticity and completeness."""
import json, glob, os

files = sorted(glob.glob("data/feature_snapshots/*.json"))

# Feature count by month
periods = {"Jan": [], "Feb": [], "Mar": [], "Apr": []}
for fp in files:
    bn = os.path.basename(fp)
    with open(fp) as f:
        d = json.load(f)
    n = len(d.get("features", {}))
    if "202601" in bn: periods["Jan"].append(n)
    elif "202602" in bn: periods["Feb"].append(n)
    elif "202603" in bn: periods["Mar"].append(n)
    elif "202604" in bn: periods["Apr"].append(n)

print("=== FEATURE COUNT BY MONTH ===")
for month, counts in periods.items():
    if counts:
        print(f"  {month}: {len(counts)} snapshots, features: min={min(counts)} max={max(counts)}")

# Old vs new snapshot keys
with open(files[0]) as f:
    old_feats = json.load(f).get("features", {})
with open(files[-1]) as f:
    new_feats = json.load(f).get("features", {})

print(f"\nOld snapshot ({os.path.basename(files[0])}): {len(old_feats)} features")
print(f"New snapshot ({os.path.basename(files[-1])}): {len(new_feats)} features")

# Zone-critical features check
critical = [
    "extension_regime_1d", "extension_regime_4h",
    "extension_ratio_1d", "extension_ratio_4h",
    "rsi_4h", "rsi_30m",
    "cvd_price_cross_30m", "cvd_price_cross_4h",
    "top_traders_long_ratio", "taker_buy_ratio",
    "nearest_support_dist_atr", "nearest_support_strength",
]

print("\n=== ZONE-CRITICAL FEATURES: OLD vs NEW ===")
print(f"  {'Feature':38s} {'Old':>12s} {'New':>12s}")
for key in critical:
    old_v = old_feats.get(key, "MISSING")
    new_v = new_feats.get(key, "MISSING")
    old_s = "MISSING" if old_v == "MISSING" else ("None" if old_v is None else str(old_v)[:12])
    new_s = "MISSING" if new_v == "MISSING" else ("None" if new_v is None else str(new_v)[:12])
    marker = " !!!" if old_s in ("MISSING", "None") and new_s not in ("MISSING", "None") else ""
    print(f"  {key:38s} {old_s:>12s} {new_s:>12s}{marker}")

# Count availability across all snapshots
print("\n=== FEATURE AVAILABILITY (all snapshots) ===")
counts = {k: 0 for k in critical}
for fp in files:
    with open(fp) as f:
        feats = json.load(f).get("features", {})
    for key in critical:
        v = feats.get(key)
        if v is not None and v != "" and v != "MISSING":
            counts[key] += 1

for key in critical:
    pct = counts[key] / len(files) * 100
    status = "OK" if pct > 80 else "PARTIAL" if pct > 20 else "MISSING"
    print(f"  {key:38s}: {counts[key]:>4d}/{len(files)} ({pct:5.1f}%) [{status}]")

# Snapshot interval analysis
print("\n=== SNAPSHOT INTERVALS ===")
from datetime import datetime
timestamps = []
for fp in files[:50]:
    ts = os.path.basename(fp).replace("snapshot_", "").replace(".json", "")
    try:
        timestamps.append(datetime.strptime(ts, "%Y%m%d_%H%M%S"))
    except:
        pass
if len(timestamps) >= 2:
    gaps = [(timestamps[i+1] - timestamps[i]).total_seconds() / 60 for i in range(len(timestamps)-1)]
    print(f"  First 50 gaps: min={min(gaps):.0f}min max={max(gaps):.0f}min mean={sum(gaps)/len(gaps):.0f}min")
    gap_counts = {}
    for g in gaps:
        bucket = f"{int(g)}min"
        gap_counts[bucket] = gap_counts.get(bucket, 0) + 1
    print(f"  Distribution: {dict(sorted(gap_counts.items(), key=lambda x: -x[1])[:5])}")
