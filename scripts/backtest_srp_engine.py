#!/usr/bin/env python3
"""
SRP Strategy BacktestEngine v1.0 — NautilusTrader Native Backtest

Uses NautilusTrader's BacktestEngine (low-level API) to run SRPStrategy
with full event-driven fidelity: real order matching, pyramiding, partial exits.

Usage:
    python3 scripts/backtest_srp_engine.py                     # Default 30 days
    python3 scripts/backtest_srp_engine.py --days 90            # 90 days
    python3 scripts/backtest_srp_engine.py --days 60 --long-only
    python3 scripts/backtest_srp_engine.py --days 60 --short-only
    python3 scripts/backtest_srp_engine.py --srp-pct 3.0        # Override SRP band %
    python3 scripts/backtest_srp_engine.py --dca-spacing 5.0    # Override DCA spacing %
    python3 scripts/backtest_srp_engine.py --compare            # Compare parameter sets
    python3 scripts/backtest_srp_engine.py --output result.json  # Save results
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
from urllib.request import Request, urlopen
from urllib.error import URLError

import pandas as pd
import numpy as np

# ---------------------------------------------------------------------------
# NautilusTrader imports
# ---------------------------------------------------------------------------
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

# Project imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from srp_strategy.srp_strategy import SRPStrategy, SRPStrategyConfig

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BINANCE_FUTURES_BASE = "https://fapi.binance.com"
FEE_TAKER = Decimal("0.00075")   # 0.075% taker fee
FEE_MAKER = Decimal("0.00020")   # 0.020% maker fee

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SRP-BacktestEngine")


# =============================================================================
# Data Fetching
# =============================================================================

def fetch_klines(
    symbol: str = "BTCUSDT",
    interval: str = "30m",
    days: int = 30,
    end_time_ms: Optional[int] = None,
) -> List[List]:
    """Fetch historical klines from Binance Futures API."""
    now_ms = end_time_ms or int(time.time() * 1000)
    start_ms = now_ms - (days * 24 * 3600 * 1000)
    current_start = start_ms
    limit = 1500
    all_bars = []

    logger.info(
        f"Fetching {symbol} {interval} klines: {days} days "
        f"({datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d')} → "
        f"{datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d')})"
    )

    while current_start < now_ms:
        url = (
            f"{BINANCE_FUTURES_BASE}/fapi/v1/klines"
            f"?symbol={symbol}&interval={interval}"
            f"&startTime={current_start}&endTime={now_ms}&limit={limit}"
        )
        data = None
        for attempt in range(4):
            try:
                req = Request(url, headers={"User-Agent": "AlgVex-SRP-BacktestEngine/1.0"})
                with urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read())
                break
            except (URLError, OSError) as e:
                wait = 2 ** (attempt + 1)
                logger.warning(f"API fail (attempt {attempt + 1}): {e}, retry in {wait}s")
                time.sleep(wait)
                if attempt == 3:
                    raise RuntimeError(f"Failed after 4 retries: {e}")

        if not data:
            break

        all_bars.extend(data)
        if len(data) < limit:
            break
        current_start = int(data[-1][0]) + 1

    # Deduplicate by open_time
    seen = set()
    unique = []
    for bar in all_bars:
        t = bar[0]
        if t not in seen:
            seen.add(t)
            unique.append(bar)

    unique.sort(key=lambda x: x[0])
    logger.info(f"Fetched {len(unique)} bars")
    return unique


def fetch_funding_rates(
    symbol: str = "BTCUSDT",
    days: int = 30,
    end_time_ms: Optional[int] = None,
) -> pd.DataFrame:
    """Fetch historical funding rates from Binance Futures API."""
    now_ms = end_time_ms or int(time.time() * 1000)
    start_ms = now_ms - (days * 24 * 3600 * 1000)
    current_start = start_ms
    limit = 1000
    all_records = []

    while current_start < now_ms:
        url = (
            f"{BINANCE_FUTURES_BASE}/fapi/v1/fundingRate"
            f"?symbol={symbol}&startTime={current_start}&endTime={now_ms}&limit={limit}"
        )
        data = None
        for attempt in range(4):
            try:
                req = Request(url, headers={"User-Agent": "AlgVex-SRP-BacktestEngine/1.0"})
                with urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read())
                break
            except (URLError, OSError) as e:
                wait = 2 ** (attempt + 1)
                if attempt < 3:
                    time.sleep(wait)
                else:
                    logger.warning(f"Funding rate fetch failed: {e}, using default 0.01%")
                    return pd.DataFrame()

        if not data:
            break
        all_records.extend(data)
        if len(data) < limit:
            break
        current_start = int(data[-1]["fundingTime"]) + 1

    if not all_records:
        return pd.DataFrame()

    records = []
    for r in all_records:
        records.append({
            "timestamp": pd.Timestamp(int(r["fundingTime"]), unit="ms", tz="UTC"),
            "funding_rate": float(r["fundingRate"]),
        })
    df = pd.DataFrame(records).drop_duplicates("timestamp").set_index("timestamp").sort_index()
    df.index = df.index.tz_localize(None)
    logger.info(f"Fetched {len(df)} funding rate records, avg={df['funding_rate'].mean()*100:.4f}%")
    return df


def klines_to_dataframe(klines: List[List]) -> pd.DataFrame:
    """Convert Binance klines to a pandas DataFrame for BarDataWrangler."""
    records = []
    for k in klines:
        records.append({
            "timestamp": pd.Timestamp(int(k[0]), unit="ms", tz="UTC"),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        })
    df = pd.DataFrame(records)
    df = df.set_index("timestamp")
    df.index = df.index.tz_localize(None)  # BarDataWrangler expects tz-naive
    return df


# =============================================================================
# Instrument Creation
# =============================================================================

def create_btcusdt_perp(pine_parity: bool = False) -> CryptoPerpetual:
    """Create a BTCUSDT-PERP CryptoPerpetual instrument.

    Args:
        pine_parity: If True, use high precision to match Pine's fractional
                     quantities (no 0.001 BTC rounding). If False, use actual
                     Binance Futures specs.
    """
    instrument_id = InstrumentId(Symbol("BTCUSDT-PERP"), Venue("BINANCE"))
    now_ns = int(time.time() * 1e9)

    if pine_parity:
        # High precision: eliminates quantity rounding difference vs Pine
        size_prec = 8
        size_inc = Quantity.from_str("0.00000001")
        min_qty = Quantity.from_str("0.00000001")
        min_not = Money(0.01, USDT)  # Minimal notional (Pine has none)
    else:
        # Actual Binance Futures specs
        size_prec = 3
        size_inc = Quantity.from_str("0.001")
        min_qty = Quantity.from_str("0.001")
        min_not = Money(5.0, USDT)

    return CryptoPerpetual(
        instrument_id=instrument_id,
        raw_symbol=Symbol("BTCUSDT"),
        base_currency=BTC,
        quote_currency=USDT,
        settlement_currency=USDT,
        is_inverse=False,
        price_precision=1,          # BTCUSDT: 0.1 tick
        size_precision=size_prec,
        price_increment=Price.from_str("0.1"),
        size_increment=size_inc,
        ts_event=now_ns,
        ts_init=now_ns,
        multiplier=Quantity.from_int(1),
        max_quantity=Quantity.from_str("1000.0"),
        min_quantity=min_qty,
        min_notional=min_not,
        max_price=Price.from_str("1000000.0"),
        min_price=Price.from_str("0.1"),
        margin_init=Decimal("0.05"),     # 20x max leverage → 5% initial margin
        margin_maint=Decimal("0.025"),   # 2.5% maintenance margin
        maker_fee=FEE_MAKER,
        taker_fee=FEE_TAKER,
    )


# =============================================================================
# Backtest Runner
# =============================================================================

INTERVAL_TO_BAR_SPEC = {
    "30m": "30-MINUTE-LAST",
    "1h": "1-HOUR-LAST",
    "4h": "4-HOUR-LAST",
    "1d": "1-DAY-LAST",
}


def run_backtest(
    klines: List[List],
    params: Optional[Dict[str, Any]] = None,
    starting_balance: float = 1500.0,
    leverage: float = 10.0,
    label: str = "Default",
    interval: str = "30m",
    production: bool = False,
) -> Dict[str, Any]:
    """
    Run a single SRP backtest using NautilusTrader BacktestEngine.

    Parameters
    ----------
    klines : Raw Binance kline data
    params : Override SRPStrategyConfig parameters
    starting_balance : Starting USDT balance
    leverage : Account leverage
    label : Human-readable label for this run
    interval : Kline interval (30m, 1h, 4h)
    production : If True, use Binance production constraints (0.001 BTC step,
                 $105 min order, immediate MARKET fills). If False (default),
                 use Pine parity mode (high precision, pending order queue).

    Returns
    -------
    Dict with performance metrics
    """
    params = params or {}

    # Pine parity: high-precision instrument (no qty rounding)
    # Production: actual Binance specs (0.001 step, $5 notional)
    instrument = create_btcusdt_perp(pine_parity=not production)
    bar_spec = INTERVAL_TO_BAR_SPEC.get(interval, "30-MINUTE-LAST")
    bar_type_str = f"BTCUSDT-PERP.BINANCE-{bar_spec}-EXTERNAL"
    bar_type = BarType.from_str(bar_type_str)

    # 2. Convert klines to NT Bars via BarDataWrangler
    df = klines_to_dataframe(klines)
    if df.empty:
        logger.error("No bar data to backtest")
        return {"error": "No data"}

    wrangler = BarDataWrangler(bar_type=bar_type, instrument=instrument)
    bars = wrangler.process(df)
    logger.info(f"[{label}] Processed {len(bars)} NT bars "
                f"({df.index[0]} → {df.index[-1]})")

    # 3. Configure BacktestEngine
    log_level = params.pop("_log_level", "ERROR")  # Default quiet for batch runs
    engine_config = BacktestEngineConfig(
        trader_id=TraderId("SRP-BACKTESTER-001"),
        logging=LoggingConfig(log_level=log_level),
    )
    engine = BacktestEngine(config=engine_config)

    # 4. Add simulated venue
    BINANCE = Venue("BINANCE")
    engine.add_venue(
        venue=BINANCE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=USDT,
        starting_balances=[Money(starting_balance, USDT)],
        default_leverage=Decimal(str(leverage)),
    )

    # 5. Add instrument + data
    engine.add_instrument(instrument)
    engine.add_data(bars)

    # 6. Create SRPStrategy with config overrides
    strategy_params = {
        "instrument_id": "BTCUSDT-PERP.BINANCE",
        "bar_type": bar_type_str,
        "enable_telegram": False,
    }
    strategy_params.update(params)

    # Reset state file so each backtest starts fresh (no stale position from prior run)
    # Skip removal if file is locked (e.g., live SRP service running)
    state_file = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "srp_state.json"
    )
    try:
        if os.path.exists(state_file):
            os.remove(state_file)
    except OSError:
        pass  # Live service may hold the file; backtest starts fresh anyway

    strategy_config = SRPStrategyConfig(**strategy_params)
    strategy = SRPStrategy(config=strategy_config)
    engine.add_strategy(strategy)

    # 7. Fetch funding rates for the backtest period
    days_span = (df.index[-1] - df.index[0]).days + 1
    fr_df = fetch_funding_rates(days=days_span)

    # 8. Run backtest
    t0 = time.time()
    engine.run()
    elapsed = time.time() - t0

    # 9. Extract results with funding rate + per-bar equity
    result = extract_results(engine, strategy, df, fr_df, elapsed, label, starting_balance)

    # 10. Cleanup
    engine.dispose()

    return result


def _parse_nt_money(val) -> float:
    """Parse NT money value like '-144.26256478 USDT' to float."""
    if isinstance(val, str):
        return float(val.split()[0])
    return float(val)


def _reconstruct_position_history(
    order_fills_report: pd.DataFrame,
) -> List[Dict]:
    """Reconstruct position changes from order fills for per-bar equity tracking.

    Returns list of {timestamp, side, qty_delta, price, cumulative_qty, avg_price}.
    Uses FIFO tracking to compute average entry price.
    """
    if order_fills_report is None or order_fills_report.empty:
        return []

    events = []
    cum_qty = 0.0
    total_cost = 0.0

    for idx, row in order_fills_report.iterrows():
        side = str(row.get("side", ""))
        qty = abs(float(row.get("filled_qty", row.get("quantity", 0))))
        price = float(row.get("avg_px", 0))

        # Get timestamp from ts_last column (tz-aware), strip tz for bar alignment
        ts = pd.Timestamp(row["ts_last"])
        if ts.tzinfo is not None:
            ts = ts.tz_localize(None)

        if "BUY" in side.upper():
            cum_qty += qty
            total_cost += qty * price
        elif "SELL" in side.upper():
            cum_qty -= qty
            if cum_qty > 0:
                total_cost = (total_cost / (cum_qty + qty)) * cum_qty
            else:
                total_cost = 0.0
                cum_qty = 0.0

        avg_px = (total_cost / cum_qty) if cum_qty > 0 else 0.0
        events.append({
            "timestamp": ts,
            "side": side,
            "qty_delta": qty,
            "price": price,
            "cumulative_qty": abs(cum_qty),
            "avg_price": avg_px if avg_px > 0 else price,
        })

    return events


def _compute_per_bar_equity(
    df: pd.DataFrame,
    position_events: List[Dict],
    fr_df: pd.DataFrame,
    starting_balance: float,
) -> Dict[str, Any]:
    """Compute per-bar equity curve including unrealized PnL and funding costs.

    Returns dict with equity_curve, true_mdd, funding_cost, max_unrealized_dd.
    """
    event_idx = 0
    n_events = len(position_events)

    pos_qty = 0.0     # absolute quantity
    pos_side = None    # "LONG" or "SHORT" or None
    pos_avg = 0.0
    realized_pnl = 0.0

    # Funding rate lookup (8h intervals: 00:00, 08:00, 16:00 UTC)
    fr_map = {}
    if not fr_df.empty:
        for ts, row in fr_df.iterrows():
            fr_map[ts] = row["funding_rate"]

    equity_values = []
    funding_total = 0.0
    max_unrealized_dd_pct = 0.0

    for bar_ts, bar_row in df.iterrows():
        close = bar_row["close"]

        # Apply position events at or before this bar
        while event_idx < n_events:
            evt = position_events[event_idx]
            evt_ts = evt["timestamp"]
            if evt_ts <= bar_ts:
                old_qty = pos_qty
                old_side = pos_side

                # Determine if this is opening or closing
                evt_side = evt["side"].upper()
                new_cum_qty = evt["cumulative_qty"]

                if old_side is None or old_qty == 0:
                    # Opening new position
                    pos_side = "LONG" if "BUY" in evt_side else "SHORT"
                elif new_cum_qty == 0:
                    # Fully closed — realize PnL
                    if pos_side == "LONG":
                        realized_pnl += old_qty * (evt["price"] - pos_avg)
                    else:
                        realized_pnl += old_qty * (pos_avg - evt["price"])
                    pos_side = None
                elif new_cum_qty < old_qty:
                    # Partial close — realize PnL on closed portion
                    closed_qty = old_qty - new_cum_qty
                    if pos_side == "LONG":
                        realized_pnl += closed_qty * (evt["price"] - pos_avg)
                    else:
                        realized_pnl += closed_qty * (pos_avg - evt["price"])

                pos_qty = new_cum_qty
                pos_avg = evt["avg_price"]
                event_idx += 1
            else:
                break

        # Funding rate cost at settlement times
        if pos_qty > 0 and bar_ts in fr_map:
            fr_rate = fr_map[bar_ts]
            notional = pos_qty * close
            # Long pays positive FR; Short receives positive FR
            if pos_side == "LONG":
                funding_total += notional * fr_rate
            else:
                funding_total -= notional * fr_rate

        # Unrealized PnL
        unrealized_pnl = 0.0
        if pos_qty > 0 and pos_avg > 0:
            if pos_side == "LONG":
                unrealized_pnl = pos_qty * (close - pos_avg)
            else:
                unrealized_pnl = pos_qty * (pos_avg - close)

        # Track max unrealized drawdown
        if pos_qty > 0 and unrealized_pnl < 0:
            unrealized_dd_pct = abs(unrealized_pnl) / starting_balance * 100
            max_unrealized_dd_pct = max(max_unrealized_dd_pct, unrealized_dd_pct)

        equity = starting_balance + realized_pnl - funding_total + unrealized_pnl
        equity_values.append(equity)

    # True MDD from per-bar equity
    equity_arr = np.array(equity_values)
    peak = np.maximum.accumulate(equity_arr)
    drawdown = np.where(peak > 0, (peak - equity_arr) / peak, 0)
    true_mdd_pct = float(np.max(drawdown)) * 100 if len(drawdown) > 0 else 0.0

    return {
        "equity_curve": equity_values,
        "true_mdd_pct": true_mdd_pct,
        "funding_total": funding_total,
        "max_unrealized_dd_pct": max_unrealized_dd_pct,
        "final_equity": equity_values[-1] if equity_values else starting_balance,
    }


def extract_results(
    engine: BacktestEngine,
    strategy: SRPStrategy,
    df: pd.DataFrame,
    fr_df: pd.DataFrame,
    elapsed: float,
    label: str,
    starting_balance: float,
) -> Dict[str, Any]:
    """Extract performance metrics from completed backtest."""

    # Account state
    order_fills_report = engine.trader.generate_order_fills_report()
    positions_report = engine.trader.generate_positions_report()

    # Calculate metrics from positions report
    total_pnl = 0.0
    trade_count = 0
    wins = 0
    losses = 0
    pnl_list = []

    if positions_report is not None and not positions_report.empty:
        for _, row in positions_report.iterrows():
            pnl = _parse_nt_money(row.get("realized_pnl", 0))
            pnl_list.append(pnl)
            total_pnl += pnl
            trade_count += 1
            if pnl > 0:
                wins += 1
            elif pnl < 0:
                losses += 1

    # Cross-check with strategy's internal trade tracking
    internal_trade_count = len(strategy._completed_trades)
    if internal_trade_count != trade_count:
        logger.warning(
            f"[{label}] Trade count mismatch: positions_report={trade_count}, "
            f"strategy._completed_trades={internal_trade_count}"
        )

    # Per-bar equity curve with funding rate + unrealized PnL
    position_events = _reconstruct_position_history(order_fills_report)
    equity_info = _compute_per_bar_equity(df, position_events, fr_df, starting_balance)

    funding_cost = equity_info["funding_total"]
    adjusted_pnl = total_pnl - funding_cost
    adjusted_return_pct = (adjusted_pnl / starting_balance) * 100

    win_rate = (wins / trade_count * 100) if trade_count > 0 else 0.0
    avg_pnl = (adjusted_pnl / trade_count) if trade_count > 0 else 0.0

    # Buy & Hold reference
    first_close = df.iloc[0]["close"]
    last_close = df.iloc[-1]["close"]
    bh_return_pct = ((last_close - first_close) / first_close) * 100

    # Sharpe ratio from per-bar equity
    sharpe = 0.0
    eq = equity_info["equity_curve"]
    if len(eq) > 48:  # At least 1 day of 30m bars
        eq_arr = np.array(eq)
        daily_returns = (eq_arr[48::48] - eq_arr[:-48:48]) / eq_arr[:-48:48]  # ~daily
        if len(daily_returns) > 1 and np.std(daily_returns) > 0:
            s = float(np.mean(daily_returns) / np.std(daily_returns) * np.sqrt(365))
            sharpe = s if np.isfinite(s) else 0.0

    # Profit factor
    gross_profit = sum(p for p in pnl_list if p > 0)
    gross_loss = abs(sum(p for p in pnl_list if p < 0)) + funding_cost
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

    fill_count = len(order_fills_report) if order_fills_report is not None else 0

    result = {
        "label": label,
        "total_pnl": round(total_pnl, 2),
        "funding_cost": round(funding_cost, 2),
        "adjusted_pnl": round(adjusted_pnl, 2),
        "adjusted_return_pct": round(adjusted_return_pct, 2),
        "buy_hold_return_pct": round(bh_return_pct, 2),
        "trade_count": trade_count,
        "fill_count": fill_count,
        "internal_trade_count": internal_trade_count,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(win_rate, 2),
        "avg_pnl": round(avg_pnl, 2),
        "true_mdd_pct": round(equity_info["true_mdd_pct"], 2),
        "max_unrealized_dd_pct": round(equity_info["max_unrealized_dd_pct"], 2),
        "sharpe_ratio": round(sharpe, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else "inf",
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "elapsed_sec": round(elapsed, 2),
        "bar_count": len(df),
        "period": f"{df.index[0]} → {df.index[-1]}",
    }

    # Sanitize NaN/inf values (JSON does not support them)
    for k, v in result.items():
        if isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
            result[k] = 0.0

    # Per-trade details
    trades = []
    if positions_report is not None and not positions_report.empty:
        for i, row in positions_report.iterrows():
            pnl = _parse_nt_money(row.get("realized_pnl", 0))
            entry_px = _parse_nt_money(row.get("avg_px_open", 0))
            exit_px = _parse_nt_money(row.get("avg_px_close", 0))
            qty = _parse_nt_money(row.get("peak_qty", 0) or row.get("quantity", 0))

            # Get exit reason from strategy's internal tracking
            reason = "unknown"
            dca_cnt = 0
            trade_idx = len(trades)
            if trade_idx < len(strategy._completed_trades):
                ct = strategy._completed_trades[trade_idx]
                reason = ct.get("reason", "unknown")
                dca_cnt = ct.get("dca_count", 0)

            realized_ret = float(row.get("realized_return", 0) or 0)
            pnl_pct = realized_ret * 100 if realized_ret != 0 else ((pnl / (entry_px * qty) * 100) if entry_px > 0 and qty > 0 else 0)
            trades.append({
                "id": trade_idx + 1,
                "entry_price": round(entry_px, 2),
                "exit_price": round(exit_px, 2),
                "qty": round(qty, 6),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "exit_reason": reason,
                "dca_count": dca_cnt,
            })

    result["trades"] = trades

    # Sampled equity curve (max 500 points for web)
    eq = equity_info["equity_curve"]
    if len(eq) > 0:
        n_samples = min(500, len(eq))
        indices = np.linspace(0, len(eq) - 1, n_samples, dtype=int)

        # Get timestamps from DataFrame index
        timestamps = df.index.tolist()

        sampled_equity = []
        sampled_dd = []
        peak = eq[0]
        peaks = [peak]
        for j in range(1, len(eq)):
            peak = max(peak, eq[j])
            peaks.append(peak)

        for idx in indices:
            t_str = str(timestamps[idx])[:10] if idx < len(timestamps) else ""
            sampled_equity.append({"t": t_str, "v": round(eq[idx], 2)})
            dd_pct = ((peaks[idx] - eq[idx]) / peaks[idx] * 100) if peaks[idx] > 0 else 0
            sampled_dd.append({"t": t_str, "v": round(dd_pct, 2)})

        result["equity_curve_sampled"] = sampled_equity
        result["drawdown_curve"] = sampled_dd

    return result


# =============================================================================
# Parameter Comparison
# =============================================================================

COMPARE_SETS = {
    "v5.0 Default": {
        # Uses SRPStrategyConfig defaults: srp=1.0%, rsi=7, dca=3%/1.5×/4, long-only, compound
    },
    "Tight SRP (0.5%)": {
        "srp_pct": 0.5,
    },
    "Wide SRP (1.5%)": {
        "srp_pct": 1.5,
    },
    "Tight DCA (2%)": {
        "dca_min_change_pct": 2.0,
    },
    "Wide DCA (5%)": {
        "dca_min_change_pct": 5.0,
    },
    "DCA ×2.0": {
        "dca_multiplier": 2.0,
    },
}


def run_compare(
    klines: List[List],
    starting_balance: float = 1000.0,
    leverage: float = 5.0,
    verbose: bool = False,
) -> Dict[str, Dict]:
    """Run comparison across parameter sets."""
    results = {}
    for name, params in COMPARE_SETS.items():
        logger.info(f"Running: {name}")
        run_params = dict(params)
        if verbose:
            run_params["_log_level"] = "INFO"
        try:
            result = run_backtest(
                klines,
                params=run_params,
                starting_balance=starting_balance,
                leverage=leverage,
                label=name,
            )
            results[name] = result
        except Exception as e:
            logger.error(f"  Failed: {e}")
            results[name] = {"error": str(e)}
    return results


# =============================================================================
# Display
# =============================================================================

def print_summary(result: Dict[str, Any]):
    """Print a single backtest result."""
    if "error" in result:
        print(f"\n  ❌ Error: {result['error']}")
        return

    print(f"\n{'=' * 65}")
    print(f"  SRP BacktestEngine Results: {result.get('label', 'Default')}")
    print(f"{'=' * 65}")
    print(f"  Period:           {result['period']}")
    print(f"  Bars:             {result['bar_count']}")
    print(f"  Elapsed:          {result['elapsed_sec']}s")
    print(f"{'─' * 65}")
    print(f"  Trade PnL:        ${result['total_pnl']:+,.2f}")
    print(f"  Funding Cost:     ${result['funding_cost']:+,.2f}")
    print(f"  Net PnL:          ${result['adjusted_pnl']:+,.2f} ({result['adjusted_return_pct']:+.2f}%)")
    print(f"  Buy & Hold:       {result['buy_hold_return_pct']:+.2f}%")
    print(f"{'─' * 65}")
    print(f"  Trades:           {result['trade_count']} (fills: {result['fill_count']}, internal: {result.get('internal_trade_count', '?')})")
    print(f"  Win Rate:         {result['win_rate_pct']:.1f}%  ({result['wins']}W / {result['losses']}L)")
    print(f"  Avg PnL/Trade:    ${result['avg_pnl']:+,.2f}")
    print(f"  Profit Factor:    {result['profit_factor']}")
    print(f"{'─' * 65}")
    print(f"  True MDD (bar):   {result['true_mdd_pct']:.2f}%  (per-bar equity incl. unrealized)")
    print(f"  Max Unrealized DD:{result['max_unrealized_dd_pct']:.2f}%  (worst intra-position float)")
    print(f"  Sharpe Ratio:     {result['sharpe_ratio']:.2f}")
    print(f"  Gross Profit:     ${result['gross_profit']:+,.2f}")
    print(f"  Gross Loss:       ${result['gross_loss']:+,.2f} (incl. funding)")
    print(f"{'=' * 65}\n")


def print_compare(results: Dict[str, Dict]):
    """Print comparison table."""
    print(f"\n{'=' * 120}")
    print(f"  SRP Parameter Comparison (NautilusTrader BacktestEngine + Funding Rate + Per-Bar MDD)")
    print(f"{'=' * 120}")

    header = (f"  {'Name':<22} {'Net PnL':>10} {'Return%':>9} {'FR Cost':>8} "
              f"{'Trades':>7} {'WR':>6} {'TrueMDD':>8} {'FloatDD':>8} {'Sharpe':>7} {'PF':>7}")
    print(header)
    print(f"  {'─' * 115}")

    for name, r in results.items():
        if "error" in r:
            print(f"  {name:<22} {'ERROR':>10}")
            continue
        pf = r['profit_factor'] if isinstance(r['profit_factor'], str) else f"{r['profit_factor']:.2f}"
        print(
            f"  {name:<22} "
            f"${r['adjusted_pnl']:>+8,.2f} "
            f"{r['adjusted_return_pct']:>+8.2f}% "
            f"${r.get('funding_cost', 0):>6,.2f} "
            f"{r['trade_count']:>6} "
            f"{r['win_rate_pct']:>5.1f}% "
            f"{r['true_mdd_pct']:>7.2f}% "
            f"{r['max_unrealized_dd_pct']:>7.2f}% "
            f"{r['sharpe_ratio']:>6.2f} "
            f"{pf:>7}"
        )

    print(f"  {'─' * 115}")
    print(f"  TrueMDD = per-bar equity (incl. unrealized PnL + funding)")
    print(f"  FloatDD = worst unrealized drawdown during any open position")
    print(f"{'=' * 120}\n")


# =============================================================================
# CLI
# =============================================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description="SRP Strategy BacktestEngine — NautilusTrader native backtest"
    )
    parser.add_argument("--days", type=int, default=456, help="Backtest period in days (default: 456)")
    parser.add_argument("--balance", type=float, default=1500.0, help="Starting USDT balance (default: 1500)")
    parser.add_argument("--leverage", type=float, default=10.0, help="Account leverage (default: 10)")
    parser.add_argument("--timeframes", action="store_true", help="Run timeframe comparison (30m/1h/4h)")

    # Strategy parameter overrides
    parser.add_argument("--srp-pct", type=float, default=None, help="Override SRP band %% (default: 5.0)")
    parser.add_argument("--dca-spacing", type=float, default=None, help="Override DCA min change %% (default: 8.0)")
    parser.add_argument("--dca-mult", type=float, default=None, help="Override DCA multiplier (default: 2.5)")
    parser.add_argument("--rsi-mfi-period", type=int, default=None, help="Override RSI-MFI period (default: 14)")

    # Note: v5.0 is long-only by default (short_enabled=False in Config)

    # Modes
    parser.add_argument("--compare", action="store_true", help="Run parameter comparison")
    parser.add_argument("--sizing", action="store_true", help="Run position sizing + leverage sweep")
    parser.add_argument("--production", action="store_true",
                        help="Use production constraints (Binance 0.001 BTC step, $105 min, immediate fills). "
                             "Default: Pine parity mode (high precision, pending order queue)")
    parser.add_argument("--output", type=str, default=None, help="Save results to JSON file")
    parser.add_argument("--json", action="store_true", help="Output JSON only (for Web API)")
    parser.add_argument("--verbose", action="store_true", help="Show detailed NT logs")
    parser.add_argument("--max-dca-count", type=int, default=None, help="Override max DCA count")
    parser.add_argument("--tp-pct", type=float, default=None, help="Override TP %% (e.g. 2.5)")
    parser.add_argument("--sl-pct", type=float, default=None, help="Override SL %% (e.g. 6.0)")
    parser.add_argument("--include-equity-curve", action="store_true", help="Include sampled equity curve in output")
    parser.add_argument("--include-trades", action="store_true", help="Include per-trade details in output")

    return parser.parse_args()


def main():
    args = parse_args()

    # Fetch data
    klines = fetch_klines(days=args.days)
    if len(klines) < 50:
        logger.error(f"Insufficient data: {len(klines)} bars (need >= 50)")
        sys.exit(1)

    # Buy & Hold reference
    first_close = float(klines[0][4])
    last_close = float(klines[-1][4])
    bh_return = ((last_close - first_close) / first_close) * 100

    if args.sizing:
        logger.info("Running position sizing sweep (fixed vs compound)...")
        sizing_results = {}
        equity = args.balance

        # Fixed sizing: base_order_usdt = absolute USDT
        for base_pct in [10, 15, 20]:
            base_usdt = equity * base_pct / 100
            label = f"Fixed {base_pct}% (${base_usdt:.0f})"
            logger.info(f"  Running: {label}")
            try:
                result = run_backtest(
                    klines,
                    params={"sizing_mode": "fixed",
                            "base_order_usdt": base_usdt,
                            "short_base_order_usdt": base_usdt * 0.8},
                    starting_balance=equity,
                    leverage=args.leverage,
                    label=label,
                )
                sizing_results[label] = result
            except Exception as e:
                logger.error(f"    Failed: {e}")
                sizing_results[label] = {"error": str(e)}

        # Compound sizing: base_order_pct = % of current equity
        for base_pct in [10, 15, 20]:
            label = f"Compound {base_pct}%"
            logger.info(f"  Running: {label}")
            try:
                result = run_backtest(
                    klines,
                    params={"sizing_mode": "percent",
                            "base_order_pct": float(base_pct)},
                    starting_balance=equity,
                    leverage=args.leverage,
                    label=label,
                )
                sizing_results[label] = result
            except Exception as e:
                logger.error(f"    Failed: {e}")
                sizing_results[label] = {"error": str(e)}

        # Display
        print(f"\n{'=' * 120}")
        print(f"  Position Sizing: Fixed vs Compound (Balanced A, equity=${equity:,.0f}, lev={args.leverage:.0f}×)")
        print(f"{'=' * 120}")
        header = (f"  {'Config':<25} {'Net PnL':>10} {'Return%':>9} {'FR Cost':>8} "
                  f"{'Trades':>7} {'WR':>6} {'TrueMDD':>8} {'FloatDD':>8} {'Sharpe':>7} {'Ret/DD':>7}")
        print(header)
        print(f"  {'─' * 115}")

        for label, r in sizing_results.items():
            if "error" in r:
                print(f"  {label:<25} {'ERROR':>10}")
                continue
            mdd = r['true_mdd_pct']
            ret = r['adjusted_return_pct']
            ret_dd = (ret / mdd) if mdd > 0 else float('inf')
            safe = "✅" if r["max_unrealized_dd_pct"] < 15 else ("⚠️" if r["max_unrealized_dd_pct"] < 22 else "❌")
            print(
                f"  {label:<25} "
                f"${r['adjusted_pnl']:>+8,.2f} "
                f"{ret:>+8.2f}% "
                f"${r.get('funding_cost', 0):>6,.2f} "
                f"{r['trade_count']:>6} "
                f"{r['win_rate_pct']:>5.1f}% "
                f"{mdd:>7.2f}% "
                f"{r['max_unrealized_dd_pct']:>7.2f}% "
                f"{r['sharpe_ratio']:>6.2f} "
                f"{ret_dd:>6.2f} {safe}"
            )

        print(f"  {'─' * 115}")
        print(f"  Ret/DD = Return% ÷ TrueMDD% (higher = better risk-adjusted return)")
        print(f"  ✅ FloatDD < 15%   ⚠️ FloatDD 15-22%   ❌ FloatDD > 22%")
        print(f"{'=' * 120}")
        print(f"  📊 Buy & Hold reference: {bh_return:+.2f}%\n")
        return

    if args.timeframes:
        logger.info("Running timeframe comparison...")
        # Best params from compare: Original Pine + compound
        pine_params = {
            "srp_pct": 1.4, "rsi_mfi_period": 7,
            "dca_min_change_pct": 2.0, "dca_multiplier": 1.5,
            "max_dca_count": 4, "short_enabled": False,
            "sizing_mode": "percent", "base_order_pct": 10.0,
        }
        balanced_params = {
            "sizing_mode": "percent", "base_order_pct": 10.0,
        }

        tf_results = {}
        for interval in ["30m", "1h", "4h"]:
            logger.info(f"  Fetching {interval} klines...")
            tf_klines = fetch_klines(interval=interval, days=args.days)
            if len(tf_klines) < 30:
                logger.warning(f"  {interval}: insufficient data ({len(tf_klines)} bars)")
                continue

            for name, params_base in [("Pine", pine_params), ("BalancedA", balanced_params)]:
                label = f"{interval} {name}"
                logger.info(f"  Running: {label}")
                try:
                    result = run_backtest(
                        tf_klines,
                        params=dict(params_base),
                        starting_balance=args.balance,
                        leverage=args.leverage,
                        label=label,
                        interval=interval,
                    )
                    tf_results[label] = result
                except Exception as e:
                    logger.error(f"    Failed: {e}")
                    tf_results[label] = {"error": str(e)}

        # Display
        print(f"\n{'=' * 120}")
        print(f"  Timeframe Comparison: Pine vs Balanced A (Compound 10%, equity=${args.balance:,.0f})")
        print(f"{'=' * 120}")
        header = (f"  {'Config':<18} {'Bars':>6} {'Net PnL':>10} {'Return%':>9} "
                  f"{'Trades':>7} {'WR':>6} {'TrueMDD':>8} {'FloatDD':>8} {'Sharpe':>7} {'Ret/DD':>7}")
        print(header)
        print(f"  {'─' * 115}")

        for label, r in tf_results.items():
            if "error" in r:
                print(f"  {label:<18} {'ERROR':>10}  {r['error'][:60]}")
                continue
            mdd = r['true_mdd_pct']
            ret = r['adjusted_return_pct']
            ret_dd = (ret / mdd) if mdd > 0 else float('inf')
            safe = "✅" if r["max_unrealized_dd_pct"] < 15 else ("⚠️" if r["max_unrealized_dd_pct"] < 22 else "❌")
            print(
                f"  {label:<18} "
                f"{r['bar_count']:>5} "
                f"${r['adjusted_pnl']:>+8,.2f} "
                f"{ret:>+8.2f}% "
                f"{r['trade_count']:>6} "
                f"{r['win_rate_pct']:>5.1f}% "
                f"{mdd:>7.2f}% "
                f"{r['max_unrealized_dd_pct']:>7.2f}% "
                f"{r['sharpe_ratio']:>6.2f} "
                f"{ret_dd:>6.2f} {safe}"
            )

        print(f"  {'─' * 115}")
        print(f"  ✅ FloatDD < 15%   ⚠️ FloatDD 15-22%   ❌ FloatDD > 22%")
        print(f"{'=' * 120}")
        print(f"  📊 Buy & Hold reference: {bh_return:+.2f}%\n")
        return

    if args.compare:
        logger.info("Running parameter comparison...")
        results = run_compare(
            klines,
            starting_balance=args.balance,
            leverage=args.leverage,
            verbose=args.verbose,
        )
        print_compare(results)
        print(f"  📊 Buy & Hold reference: {bh_return:+.2f}%\n")

        if args.output:
            save = {}
            for name, r in results.items():
                save[name] = r
            save["buy_and_hold_pct"] = round(bh_return, 4)
            save["engine"] = "NautilusTrader BacktestEngine"
            with open(args.output, "w") as f:
                json.dump(save, f, indent=2)
            logger.info(f"Results saved to {args.output}")
    else:
        # Build parameter overrides
        params = {}
        if args.srp_pct is not None:
            params["srp_pct"] = args.srp_pct
        if args.dca_spacing is not None:
            params["dca_min_change_pct"] = args.dca_spacing
        if args.dca_mult is not None:
            params["dca_multiplier"] = args.dca_mult
        if args.rsi_mfi_period is not None:
            params["rsi_mfi_period"] = args.rsi_mfi_period
        if args.max_dca_count is not None:
            params["max_dca_count"] = args.max_dca_count
        if args.tp_pct is not None:
            params["mintp"] = args.tp_pct / 100.0
        if args.sl_pct is not None:
            params["max_portfolio_loss_pct"] = args.sl_pct / 100.0
        if args.verbose:
            params["_log_level"] = "INFO"

        mode_label = "Production" if args.production else ("Custom" if params else "Default")
        result = run_backtest(
            klines,
            params=params,
            starting_balance=args.balance,
            leverage=args.leverage,
            label=mode_label,
            production=args.production,
        )

        result["buy_hold_return_pct"] = round(bh_return, 2)
        result["engine"] = "NautilusTrader BacktestEngine"

        if args.json:
            output = {k: v for k, v in result.items() if k != "equity_curve"}
            if not args.include_equity_curve:
                output.pop("equity_curve_sampled", None)
                output.pop("drawdown_curve", None)
            if not args.include_trades:
                output.pop("trades", None)
            print(json.dumps(output, indent=2))
        else:
            print_summary(result)
            print(f"  📊 Buy & Hold reference: {bh_return:+.2f}%\n")

        if args.output:
            output = {k: v for k, v in result.items() if k != "equity_curve"}
            if not args.include_equity_curve:
                output.pop("equity_curve_sampled", None)
                output.pop("drawdown_curve", None)
            if not args.include_trades:
                output.pop("trades", None)
            with open(args.output, "w") as f:
                json.dump(output, f, indent=2)
            if not args.json:
                logger.info(f"Results saved to {args.output}")


if __name__ == "__main__":
    main()
