#!/usr/bin/env python3
"""
SRP v6.1 Parity Check — Pine vs Python Signal Comparison

Verifies that signal_engine.py produces IDENTICAL signals to v6.pine.

Usage:
    1. Export Pine signals from TradingView (Data Window → CSV)
    2. python3 srp/scripts/parity_check.py --pine-csv data/pine_signals.csv --days 365
"""

# TODO: Port from scripts/pine_tv_comparator.py
# Compares bar-by-bar: action, v_avg, tp_target, rsi_mfi

print("TODO: Port from scripts/pine_tv_comparator.py — compares signal_engine vs Pine")
