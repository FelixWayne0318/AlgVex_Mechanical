# SRP — VWMA + RSI-MFI Mean Reversion with DCA

Self-contained trading strategy module for AlgVex.

## Architecture

```
srp/
├── docs/           # Pine SSoT + review
├── strategy/       # Python implementation (3-layer)
│   ├── pine_indicators.py  # Layer 1a: Pine-exact indicators (verified)
│   ├── signal_engine.py    # Layer 1b: Pure signal logic (zero NT dep)
│   └── srp_strategy.py     # Layer 3:  NT Strategy thin shell
├── scripts/        # Backtest + optimize + validate
├── configs/        # Standalone YAML config
├── web/            # Dashboard stubs
└── deploy/         # systemd service
```

## Key Design

- **Pine is SSoT**: `docs/v6.pine` is the source of truth. Python replicates it.
- **Signal engine has zero NT dependency**: Can be used in backtest, optimize, parity check, and NT Strategy.
- **NT Strategy is a thin shell**: Only handles order execution and event callbacks.
- **Indicators are Pine-exact**: Extracted from parity-verified backtest code.

## Quick Start

```bash
# Backtest
python3 srp/scripts/backtest.py --days 365

# Config validation
python3 srp/main_srp.py --dry-run

# Production (via main_live.py)
python3 main_live.py --strategy srp --env production
```

## Version

Current: v6.1 (see docs/v6.pine changelog for full fix history)
