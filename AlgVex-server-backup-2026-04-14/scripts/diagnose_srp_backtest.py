#!/usr/bin/env python3
"""
Diagnose SRP BacktestEngine 2-trade issue.

Runs compound vs fixed backtest side-by-side with detailed instrumentation
to identify exactly where the compound mode diverges.

Usage:
    python3 scripts/diagnose_srp_backtest.py
    python3 scripts/diagnose_srp_backtest.py --days 60
"""

import os
import sys
import json
import time
import argparse
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Dict, Optional, Any

import pandas as pd
import numpy as np

# NautilusTrader imports
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.objects import Price, Quantity, Money
from nautilus_trader.model.identifiers import InstrumentId, TraderId, Venue, Symbol
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.currencies import BTC, USDT
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.config import LoggingConfig
from nautilus_trader.persistence.wranglers import BarDataWrangler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from srp_strategy.srp_strategy import SRPStrategy, SRPStrategyConfig

# Reuse data fetch from backtest_srp_engine
from scripts.backtest_srp_engine import fetch_klines, klines_to_dataframe, create_btcusdt_perp

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SRP-Diagnose")

FEE_TAKER = Decimal("0.00075")
FEE_MAKER = Decimal("0.00020")


def run_diagnostic(klines, sizing_mode, label, starting_balance=1500.0, leverage=10.0):
    """Run backtest with diagnostic output."""
    instrument = create_btcusdt_perp(pine_parity=True)
    bar_type_str = "BTCUSDT-PERP.BINANCE-30-MINUTE-LAST-EXTERNAL"
    bar_type = BarType.from_str(bar_type_str)

    df = klines_to_dataframe(klines)
    wrangler = BarDataWrangler(bar_type=bar_type, instrument=instrument)
    bars = wrangler.process(df)

    engine_config = BacktestEngineConfig(
        trader_id=TraderId("SRP-DIAG-001"),
        logging=LoggingConfig(log_level="WARNING"),
    )
    engine = BacktestEngine(config=engine_config)

    BINANCE = Venue("BINANCE")
    engine.add_venue(
        venue=BINANCE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=USDT,
        starting_balances=[Money(starting_balance, USDT)],
        default_leverage=Decimal(str(leverage)),
    )
    engine.add_instrument(instrument)
    engine.add_data(bars)

    # Reset state file
    state_file = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "srp_state.json"
    )
    if os.path.exists(state_file):
        os.remove(state_file)

    # Configure strategy
    params = {
        "instrument_id": "BTCUSDT-PERP.BINANCE",
        "bar_type": bar_type_str,
        "enable_telegram": False,
    }
    if sizing_mode == "fixed":
        params["sizing_mode"] = "fixed"
        params["base_order_usdt"] = starting_balance * 0.10  # Same % for fair comparison
    else:
        params["sizing_mode"] = "percent"
        params["base_order_pct"] = 10.0

    strategy_config = SRPStrategyConfig(**params)
    strategy = SRPStrategy(config=strategy_config)
    engine.add_strategy(strategy)

    # Run
    t0 = time.time()
    engine.run()
    elapsed = time.time() - t0

    # === DIAGNOSTIC OUTPUT ===
    print(f"\n{'=' * 70}")
    print(f"  DIAGNOSTIC: {label} (sizing_mode={sizing_mode})")
    print(f"{'=' * 70}")

    # 1. Positions report
    positions_report = engine.trader.generate_positions_report()
    pos_count = len(positions_report) if positions_report is not None and not positions_report.empty else 0
    print(f"\n  [1] positions_report rows: {pos_count}")
    if positions_report is not None and not positions_report.empty:
        print(f"      Columns: {list(positions_report.columns)}")
        for idx, row in positions_report.iterrows():
            print(f"      Row {idx}: side={row.get('side','?')} "
                  f"qty={row.get('quantity','?')} "
                  f"avg_px={row.get('avg_px_open','?')} "
                  f"realized_pnl={row.get('realized_pnl','?')} "
                  f"status={row.get('status','?')}")

    # 2. Order fills report
    order_fills = engine.trader.generate_order_fills_report()
    fill_count = len(order_fills) if order_fills is not None and not order_fills.empty else 0
    print(f"\n  [2] order_fills_report rows: {fill_count}")
    if order_fills is not None and not order_fills.empty:
        print(f"      Columns: {list(order_fills.columns)}")
        # Count BUY vs SELL fills
        buy_fills = 0
        sell_fills = 0
        for _, row in order_fills.iterrows():
            side = str(row.get("side", ""))
            if "BUY" in side.upper():
                buy_fills += 1
            elif "SELL" in side.upper():
                sell_fills += 1
        print(f"      BUY fills: {buy_fills}, SELL fills: {sell_fills}")
        # Show first and last 3 fills
        if fill_count > 0:
            print(f"      First 3 fills:")
            for idx, (_, row) in enumerate(order_fills.head(3).iterrows()):
                print(f"        [{idx}] side={row.get('side','?')} "
                      f"qty={row.get('filled_qty', row.get('quantity','?'))} "
                      f"px={row.get('avg_px','?')} "
                      f"ts={row.get('ts_last','?')}")
            if fill_count > 6:
                print(f"      Last 3 fills:")
                for idx, (_, row) in enumerate(order_fills.tail(3).iterrows()):
                    print(f"        [{fill_count-3+idx}] side={row.get('side','?')} "
                          f"qty={row.get('filled_qty', row.get('quantity','?'))} "
                          f"px={row.get('avg_px','?')} "
                          f"ts={row.get('ts_last','?')}")

    # 3. Orders report (includes rejected)
    try:
        orders_report = engine.trader.generate_orders_report()
        order_count = len(orders_report) if orders_report is not None and not orders_report.empty else 0
        print(f"\n  [3] orders_report rows: {order_count}")
        if orders_report is not None and not orders_report.empty:
            # Count by status
            statuses = {}
            for _, row in orders_report.iterrows():
                status = str(row.get("status", "UNKNOWN"))
                statuses[status] = statuses.get(status, 0) + 1
            print(f"      By status: {statuses}")
    except Exception as e:
        print(f"\n  [3] orders_report: error - {e}")

    # 4. Strategy internal state
    print(f"\n  [4] Strategy internal state:")
    print(f"      _completed_trades: {len(getattr(strategy, '_completed_trades', []))}")
    print(f"      _dealcount: {strategy._dealcount}")
    print(f"      _pending_close_reason: {strategy._pending_close_reason}")
    print(f"      _side: {strategy._side}")
    print(f"      _total_quantity: {strategy._total_quantity}")
    print(f"      Buffer sizes: closes={len(strategy._closes)} "
          f"highs={len(strategy._highs)} "
          f"lows={len(strategy._lows)} "
          f"volumes={len(strategy._volumes)}")

    # 5. Account state
    try:
        account = strategy.portfolio.account(strategy.instrument_id.venue)
        if account:
            balances = account.balances()
            for currency, balance in balances.items():
                print(f"\n  [5] Account balance: {currency} = "
                      f"total={balance.total} free={balance.free} locked={balance.locked}")
        else:
            print(f"\n  [5] Account: None!")
    except Exception as e:
        print(f"\n  [5] Account error: {e}")

    # 6. Reconstruct trade count from fills (SELL fills = close events)
    if order_fills is not None and not order_fills.empty:
        # Track position to identify full close events
        cum_qty = 0.0
        close_events = 0
        for _, row in order_fills.iterrows():
            side = str(row.get("side", "")).upper()
            qty = abs(float(row.get("filled_qty", row.get("quantity", 0))))
            if "BUY" in side:
                cum_qty += qty
            elif "SELL" in side:
                cum_qty -= qty
                if abs(cum_qty) < 0.0001:  # Position closed
                    close_events += 1
                    cum_qty = 0.0
        print(f"\n  [6] Reconstructed trade count (from fills): {close_events}")
        print(f"      Remaining position after all fills: {cum_qty:.6f}")

    print(f"\n  Elapsed: {elapsed:.1f}s, Bars: {len(df)}")
    print(f"{'=' * 70}\n")

    engine.dispose()
    return {
        "positions_report_rows": pos_count,
        "fill_count": fill_count,
        "buy_fills": buy_fills if fill_count > 0 else 0,
        "sell_fills": sell_fills if fill_count > 0 else 0,
        "internal_trades": len(getattr(strategy, '_completed_trades', [])),
        "dealcount": strategy._dealcount,
        "pending_close_reason": strategy._pending_close_reason,
    }


def main():
    parser = argparse.ArgumentParser(description="Diagnose SRP backtest 2-trade issue")
    parser.add_argument("--days", type=int, default=60, help="Backtest days (default: 60)")
    parser.add_argument("--balance", type=float, default=1500.0, help="Starting balance")
    args = parser.parse_args()

    logger.info(f"Fetching {args.days} days of BTCUSDT 30m klines...")
    klines = fetch_klines(days=args.days)
    logger.info(f"Got {len(klines)} bars")

    if len(klines) < 50:
        logger.error("Insufficient data")
        sys.exit(1)

    # Run BOTH modes
    fixed_result = run_diagnostic(klines, "fixed", "Fixed Sizing", args.balance)
    compound_result = run_diagnostic(klines, "percent", "Compound Sizing", args.balance)

    # Compare
    print(f"\n{'=' * 70}")
    print(f"  COMPARISON SUMMARY")
    print(f"{'=' * 70}")
    print(f"  {'Metric':<35} {'Fixed':>12} {'Compound':>12}")
    print(f"  {'─' * 60}")
    for key in ["positions_report_rows", "fill_count", "buy_fills", "sell_fills",
                 "internal_trades", "dealcount"]:
        f_val = fixed_result.get(key, "?")
        c_val = compound_result.get(key, "?")
        flag = " ❌" if f_val != c_val and key != "fill_count" else ""
        print(f"  {key:<35} {str(f_val):>12} {str(c_val):>12}{flag}")

    # Key diagnostic
    print(f"\n  pending_close_reason (should be None):")
    print(f"    Fixed:    {fixed_result.get('pending_close_reason')}")
    print(f"    Compound: {compound_result.get('pending_close_reason')}")

    if compound_result.get("pending_close_reason") is not None:
        print(f"\n  ⚠️  CONFIRMED: _pending_close_reason stuck in compound mode!")
        print(f"      This means submit_order processes SYNCHRONOUSLY in BacktestEngine.")
        print(f"      The race condition IS the root cause.")
    elif compound_result.get("fill_count", 0) < 10 and fixed_result.get("fill_count", 0) > 10:
        print(f"\n  ⚠️  Strategy produces fewer fills in compound mode.")
        print(f"      Check _get_equity() / _get_base_usdt() for issues.")
    elif compound_result.get("positions_report_rows", 0) < compound_result.get("internal_trades", 0):
        print(f"\n  ⚠️  positions_report undercounts trades in NETTING mode!")
        print(f"      positions_report: {compound_result.get('positions_report_rows')}")
        print(f"      internal_trades:  {compound_result.get('internal_trades')}")
        print(f"      Reconstructed:    check [6] above")
        print(f"      → extract_results() should use fill-based counting, not positions_report")


if __name__ == "__main__":
    main()
