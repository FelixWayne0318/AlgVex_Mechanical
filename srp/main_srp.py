#!/usr/bin/env python3
"""
SRP v6.1 Standalone Entry Point

Usage:
    python3 srp/main_srp.py --env production
    python3 srp/main_srp.py --env development --dry-run
    python3 srp/main_srp.py --env backtest

Reads config from srp/configs/srp.yaml.
API keys from ~/.env.algvex (SRP_BINANCE_API_KEY, SRP_BINANCE_API_SECRET).
"""

import os
import sys
import argparse
import logging

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from srp.strategy.srp_strategy import SRPStrategy, SRPStrategyConfig


def main():
    parser = argparse.ArgumentParser(description="SRP v6.1 Trading Strategy")
    parser.add_argument("--env", choices=["production", "development", "backtest"], default="production")
    parser.add_argument("--dry-run", action="store_true", help="Validate config without trading")
    parser.add_argument("--config", default="srp/configs/srp.yaml", help="Config file path")
    args = parser.parse_args()

    log_level = logging.DEBUG if args.env == "development" else logging.INFO
    logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(message)s")

    if args.dry_run:
        from srp.strategy.signal_engine import SRPConfig
        from srp.strategy.srp_strategy import load_srp_config
        cfg = load_srp_config(args.config)
        print(f"SRP v6.1 config loaded: SRP={cfg.srp_pct}% DCA={cfg.max_dca_count}x "
              f"cycle={'ON' if cfg.cycle_enabled else 'OFF'}")
        print("Dry run complete — config is valid.")
        return

    # TODO: Initialize NautilusTrader TradingNode with SRP strategy
    # This requires the full NT bootstrap (venue, data client, exec client).
    # For now, use main_live.py --strategy srp as the production entry point.
    print("Standalone NT bootstrap not yet implemented.")
    print("Use: python3 main_live.py --strategy srp --env production")


if __name__ == "__main__":
    main()
