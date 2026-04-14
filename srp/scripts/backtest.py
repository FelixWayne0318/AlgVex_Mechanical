#!/usr/bin/env python3
"""
SRP v6.1 Backtest — NautilusTrader BacktestEngine

Uses the same SRPStrategy that runs in production, with NT's simulated exchange
for order matching, position tracking, commission calculation, and PnL accounting.

Usage:
    python3 srp/scripts/backtest.py --days 365
    python3 srp/scripts/backtest.py --days 470 --capital 1500
    python3 srp/scripts/backtest.py --days 365 --srp-pct 1.2 --dca-spacing 2.5
"""

import os
import sys
import argparse
import logging
import time
from decimal import Decimal

import pandas as pd

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.model import BarType, Money, TraderId, Venue
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.currencies import USDT
from nautilus_trader.persistence.wranglers import BarDataWrangler
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from srp.strategy.srp_strategy import SRPStrategy, SRPStrategyConfig

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SRP-Backtest")
logger.setLevel(logging.INFO)

def _create_btcusdt_perp():
    """Create BTCUSDT perpetual with REALISTIC Binance Futures fees.

    TestInstrumentProvider.btcusdt_perp_binance() uses outdated fees
    (maker=0.02%, taker=0.018%). Real Binance VIP0 is maker=0.02%, taker=0.05%.
    Pine uses 0.075% (includes slippage estimate).
    We match Pine for parity.
    """
    from nautilus_trader.model.instruments import CryptoPerpetual
    template = TestInstrumentProvider.btcusdt_perp_binance()
    d = CryptoPerpetual.to_dict(template)
    d['maker_fee'] = '0.000400'    # 0.04%
    d['taker_fee'] = '0.000750'    # 0.075% — matches Pine commission_value
    d['margin_init'] = '0.1000'    # 10% = 10x leverage
    return CryptoPerpetual.from_dict(d)


# =============================================================================
# Data Fetching — reuses project's BinanceKlineClient (retry, rate limit)
# =============================================================================

def fetch_klines_df(symbol="BTCUSDT", interval="30m", days=365) -> pd.DataFrame:
    """Fetch historical klines via BinanceKlineClient → pandas DataFrame.

    Handles pagination (API max 1500 bars/request) automatically.
    Reuses utils/binance_kline_client.py for retry/timeout/error handling.
    """
    try:
        from utils.binance_kline_client import BinanceKlineClient
        client = BinanceKlineClient(timeout=30)
    except ImportError:
        logger.error("BinanceKlineClient not available. Is utils/ in PYTHONPATH?")
        return pd.DataFrame()

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (days * 24 * 3600 * 1000)
    current_start, limit, all_bars = start_ms, 1500, []

    logger.info(f"Fetching {symbol} {interval}: {days} days via BinanceKlineClient...")
    while current_start < now_ms:
        # BinanceKlineClient.get_klines uses @api_retry internally
        klines = client.get_klines(symbol=symbol, interval=interval, limit=limit)
        if not klines:
            break

        # Filter to requested time range (get_klines doesn't support startTime)
        # So we use raw API with startTime for pagination
        import requests
        url = f"https://fapi.binance.com/fapi/v1/klines"
        try:
            resp = requests.get(url, params={
                'symbol': symbol, 'interval': interval,
                'startTime': current_start, 'endTime': now_ms, 'limit': limit,
            }, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"Fetch failed at {current_start}: {e}")
            break

        if not data:
            break
        all_bars.extend(data)
        if len(data) < limit:
            break
        current_start = int(data[-1][0]) + 1

    # Deduplicate and sort
    seen = set()
    unique = [b for b in all_bars if b[0] not in seen and not seen.add(b[0])]
    unique.sort(key=lambda x: x[0])

    # Strip last incomplete bar
    if unique:
        unique = unique[:-1]

    logger.info(f"Fetched {len(unique)} bars")

    if not unique:
        return pd.DataFrame()

    # Convert to DataFrame with UTC datetime index
    df = pd.DataFrame(unique, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_volume', 'trades', 'taker_buy_base',
        'taker_buy_quote', 'ignore'
    ])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    df = df.set_index('timestamp')
    df = df[['open', 'high', 'low', 'close', 'volume']].astype(float)

    return df


# =============================================================================
# BacktestEngine Setup
# =============================================================================

def run_backtest(days=365, capital=1500.0, params=None):
    """Run NT BacktestEngine with SRPStrategy."""

    # 1. Fetch data
    df = fetch_klines_df("BTCUSDT", "30m", days)
    if df.empty:
        logger.error("No data fetched")
        return None

    # 2. Create instrument with REALISTIC fees (matches Pine commission_value=0.075%)
    #    TestInstrumentProvider uses outdated fees (0.018%), causing ~4x PnL inflation.
    instrument = _create_btcusdt_perp()
    bar_type = BarType.from_str("BTCUSDT-PERP.BINANCE-30-MINUTE-LAST-EXTERNAL")

    # 3. Wrangle bars
    wrangler = BarDataWrangler(bar_type=bar_type, instrument=instrument)
    bars = wrangler.process(df)
    logger.info(f"Wrangled {len(bars)} NT Bar objects")

    # 4. Configure engine
    engine_config = BacktestEngineConfig(
        trader_id=TraderId("SRP-BACKTESTER-001"),
    )
    engine = BacktestEngine(config=engine_config)

    # 5. Add venue — Binance Futures (MARGIN account, NETTING for perpetuals)
    BINANCE = Venue("BINANCE")
    engine.add_venue(
        venue=BINANCE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=None,
        starting_balances=[Money(capital, USDT)],
        leverages={instrument.id: Decimal("10")},
    )

    # 6. Add data
    engine.add_instrument(instrument)
    engine.add_data(bars)

    # 7. Write temporary config with overrides
    config_path = "srp/configs/srp.yaml"
    if params:
        import yaml
        tmp_path = "/tmp/srp_backtest_config.yaml"
        if os.path.exists(config_path):
            with open(config_path) as f:
                cfg = yaml.safe_load(f)
        else:
            cfg = {}
        cfg.update(params)
        with open(tmp_path, "w") as f:
            yaml.dump(cfg, f)
        config_path = tmp_path

    # 8. Add strategy
    strategy_config = SRPStrategyConfig(
        instrument_id="BTCUSDT-PERP.BINANCE",
        bar_type="BTCUSDT-PERP.BINANCE-30-MINUTE-LAST-EXTERNAL",
        config_path=config_path,
    )
    strategy = SRPStrategy(config=strategy_config)
    engine.add_strategy(strategy)

    # 9. Run
    logger.info("Running backtest...")
    engine.run()

    # 10. Results
    logger.info("Backtest complete. Generating report...")

    # Get account stats
    account = engine.trader.generate_account_report(BINANCE)
    fills = engine.trader.generate_fills_report()
    orders = engine.trader.generate_order_fills_report()
    positions = engine.trader.generate_positions_report()

    # Calculate metrics from engine
    final_balance = float(engine.portfolio.net_exposures(BINANCE).get(USDT, Money(0, USDT)))
    account_balances = engine.portfolio.account(BINANCE).balances()
    final_equity = 0.0
    for currency, balance in account_balances.items():
        if str(currency) == "USDT":
            final_equity = float(balance.total)

    pnl = final_equity - capital
    ret_pct = (pnl / capital) * 100

    # Trade stats
    total_trades = len(positions) if positions is not None else 0

    print(f"\n{'='*60}")
    print(f"SRP v6.1 NT Backtest — BTCUSDT {days}d (capital=${capital})")
    print(f"{'='*60}")
    print(f"Final Equity:  ${final_equity:,.2f}")
    print(f"PnL:           ${pnl:+,.2f} ({ret_pct:+.2f}%)")
    print(f"Positions:     {total_trades}")

    if fills is not None and len(fills) > 0:
        print(f"Total Fills:   {len(fills)}")

    if positions is not None and len(positions) > 0:
        print(f"\n--- Positions Report ---")
        print(positions.to_string())

    # Print engine statistics
    print(f"\n--- Engine Statistics ---")
    for stat_name, stat_value in engine.get_result().stats_pnls.items():
        print(f"  {stat_name}: {stat_value}")

    for stat_name, stat_value in engine.get_result().stats_returns.items():
        print(f"  {stat_name}: {stat_value}")

    engine.dispose()
    return {"pnl": pnl, "return_pct": ret_pct, "trades": total_trades}


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="SRP v6.1 NT Backtest")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--capital", type=float, default=1500)
    parser.add_argument("--srp-pct", type=float, default=None)
    parser.add_argument("--dca-spacing", type=float, default=None)
    parser.add_argument("--mintp", type=float, default=None)
    parser.add_argument("--max-loss", type=float, default=None)
    args = parser.parse_args()

    params = {}
    if args.srp_pct is not None:
        params["srp_pct"] = args.srp_pct
    if args.dca_spacing is not None:
        params["dca_min_change_pct"] = args.dca_spacing
    if args.mintp is not None:
        params["mintp"] = args.mintp
    if args.max_loss is not None:
        params["max_loss_pct"] = args.max_loss

    run_backtest(days=args.days, capital=args.capital, params=params or None)


if __name__ == "__main__":
    main()
