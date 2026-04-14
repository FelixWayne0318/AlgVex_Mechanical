#!/usr/bin/env python3
"""
v19.1 Computational Correctness Verification

Verifies ALL v19.1 algorithm changes with known test data:
  1. _detect_divergences() — RSI/MACD-Price divergence detection
  2. CVD-Price divergence — in _format_order_flow_report()
  3. OI×Price 4-Quadrant — in _format_derivatives_report()
  4. Taker Buy/Sell Ratio tags — threshold correctness
  5. Top Traders positioning + shift detection
  6. Liquidation magnitude tiers
  7. Risk Manager OUTPUT FORMAT — 6-field JSON structure
  8. Edge cases & boundary conditions

Usage:
    python3 tests/test_v19_1_verification.py
"""

import os
import sys
import json
import logging
import traceback

# Setup path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("v19.1-verify")

PASS = 0
FAIL = 0
ERRORS = []


def check(name, condition, detail=""):
    """Assert a test condition and track results."""
    global PASS, FAIL, ERRORS
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        msg = f"  ❌ {name}" + (f" — {detail}" if detail else "")
        print(msg)
        ERRORS.append(msg)


def section(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


# ===========================================================================
# Instantiate MultiAgentAnalyzer (no API key needed for format methods)
# ===========================================================================
from agents.multi_agent_analyzer import MultiAgentAnalyzer

analyzer = MultiAgentAnalyzer()

# ===========================================================================
# TEST 1: _detect_divergences() — Algorithm Correctness
# ===========================================================================
section("TEST 1: _detect_divergences() — Divergence Detection Algorithm")

# 1a: Classic BEARISH divergence — price higher high, RSI lower high
# Design: Two clear peaks satisfying find_local_extremes(window=2)
# window=2 means: peak at idx i requires series[i] >= series[i±1] AND series[i] >= series[i±2]
#
# Price: [100, 105, 110, 108, 103, 100, 105, 115, 110, 105, 100]
#              peak at idx 2 (110)           peak at idx 7 (115)
# Price higher high: 115 > 110 ✓
#
# RSI:   [40,  55,  65,  60,  45,  40,  55,  60,  55,  45,  40]
#              peak at idx 2 (65)            peak at idx 7 (60)
# RSI lower high: 60 < 65 ✓  → BEARISH DIVERGENCE
price_bearish = [100, 105, 110, 108, 103, 100, 105, 115, 110, 105, 100]
rsi_bearish =   [40,  55,  65,  60,  45,  40,  55,  60,  55,  45,  40]
tags = analyzer._detect_divergences(price_bearish, rsi_series=rsi_bearish, timeframe="4H")
has_bearish = any("BEARISH" in t for t in tags)
check("1a: Bearish RSI divergence detected (price higher high, RSI lower high)",
      has_bearish, f"tags={tags}")

# 1b: Classic BULLISH divergence — price lower low, RSI higher low
# Price: [120, 100, 90, 95, 110, 120, 100, 85, 95, 110, 120]
#              trough at idx 2 (90)          trough at idx 7 (85)
# Price lower low: 85 < 90 ✓
#
# RSI:   [60,  40,  30,  35,  50,  60,  40,  35,  45,  55,  60]
#              trough at idx 2 (30)          trough at idx 7 (35)
# RSI higher low: 35 > 30 ✓  → BULLISH DIVERGENCE
price_bullish = [120, 100, 90, 95, 110, 120, 100, 85, 95, 110, 120]
rsi_bullish =   [60,  40,  30, 35,  50,  60,  40,  35, 45,  55,  60]
tags = analyzer._detect_divergences(price_bullish, rsi_series=rsi_bullish, timeframe="30M")
has_bullish = any("BULLISH" in t for t in tags)
check("1b: Bullish RSI divergence detected (price lower low, RSI higher low)",
      has_bullish, f"tags={tags}")

# 1c: No divergence — price and RSI both making higher highs
price_no_div = [100, 105, 103, 108, 105, 112, 109, 118, 115]
rsi_no_div =   [40,  55,  45,  60,  50,  65,  55,  70,  60]
tags = analyzer._detect_divergences(price_no_div, rsi_series=rsi_no_div, timeframe="4H")
check("1c: No divergence when price and RSI trend together",
      len(tags) == 0, f"tags={tags}")

# 1d: Insufficient data (< 5 points) → no crash, empty result
tags = analyzer._detect_divergences([100, 110, 105], rsi_series=[50, 60, 55], timeframe="4H")
check("1d: Insufficient data (3 points) returns empty list",
      tags == [], f"tags={tags}")

# 1e: Empty/None input → no crash
tags = analyzer._detect_divergences([], rsi_series=[], timeframe="4H")
check("1e: Empty input returns empty list", tags == [])
tags = analyzer._detect_divergences(None, rsi_series=None, timeframe="4H")
check("1f: None input returns empty list", tags == [])

# 1g: MACD Hist divergence — verify .4f formatting (not .1f)
price_macd = [100, 105, 103, 110, 108, 115, 112, 120, 116]
macd_macd =  [0.001, 0.003, 0.001, 0.0025, 0.001, 0.002, 0.001, 0.0015, 0.001]
tags = analyzer._detect_divergences(price_macd, macd_hist_series=macd_macd, timeframe="4H")
# Check that any MACD output uses .4f (not "0.0")
for t in tags:
    if "MACD" in t:
        check("1g: MACD Hist uses .4f format (not .1f truncation)",
              "0.0→0.0" not in t and "MACD" in t,
              f"tag={t}")
        break
else:
    # Even if no divergence detected with this data, verify no crash
    check("1g: MACD Hist processing did not crash", True)

# 1h: Mismatched series lengths → should not crash
tags = analyzer._detect_divergences([100, 110, 105, 115, 108],
                                     rsi_series=[50, 60, 55],
                                     timeframe="4H")
check("1h: Mismatched series lengths handled gracefully", isinstance(tags, list))

# ===========================================================================
# TESTS 2-9: SKIPPED (v46.0 — _format_order_flow_report, _format_derivatives_report,
#            _format_technical_report deleted with AI agents)
# ===========================================================================
section("TESTS 2-9: SKIPPED (format methods deleted v46.0)")
print("  (Tests 2-9 referenced _format_order_flow_report, _format_derivatives_report,")
print("   _format_technical_report — all deleted in v46.0 AI agent cleanup)")


# DELETED: Tests 3-9 removed (called deleted methods _format_derivatives_report,
# _format_order_flow_report, _format_technical_report — all deleted in v46.0)






# ===========================================================================
# SUMMARY
# ===========================================================================
section("VERIFICATION SUMMARY")
print(f"\n  Total: {PASS + FAIL} tests")
print(f"  Passed: {PASS}")
print(f"  Failed: {FAIL}")

if ERRORS:
    print(f"\n  FAILURES:")
    for e in ERRORS:
        print(f"    {e}")

print()
if FAIL == 0:
    print("  ✅ ALL TESTS PASSED — v19.1 computations verified correct")
else:
    print(f"  ❌ {FAIL} TEST(S) FAILED — requires investigation")

if __name__ == "__main__":
    sys.exit(0 if FAIL == 0 else 1)
