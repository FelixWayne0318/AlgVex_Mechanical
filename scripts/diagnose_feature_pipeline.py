#!/usr/bin/env python3
"""
Feature Pipeline Diagnostic v37.1

Validates that extract_features() produces correct, non-default values
from real market data.

Coverage depth (all 135 FEATURE_SCHEMA keys):
  - Existence:  135/135 — Phase 3 schema completeness check
  - Type:       135/135 — Phase 4 iterates FEATURE_SCHEMA, validates each type
  - Non-default: ~90/118 — Phase 5 per-feature non-zero/non-None checks
  - Logic:       24/26  — Phase 13 re-derives categoricals from raw values
  - Cross-TF:    13     — Phase 7 detects data source contamination

Detects:
- Wrong data source (30M data used for 4H/1D features)
- Non-existent dict keys (always returning default values)
- Config mismatch (feature expects indicator that doesn't exist)
- Cross-TF contamination (same values across different timeframes)
- Pre-computed categorical logic errors (macd_cross, di_direction, etc.)
- Extension/volatility regime classification logic errors
- _avail_* flag correctness
- Position/Account/SR/FR data extraction parity with production
- sentiment_degraded flag consistency
- CVD-Price cross 30M + 4H coverage

v37.1 changes from v37.0:
- P1-HIGH: Phase 15 adds cvd_price_cross_4h validation (was missing)
- P1-MEDIUM: Phase 13 adds sentiment_degraded cross-validation
- P2: Phase 13 adds 13 categorical logic cross-validations:
  extension_regime (3 TF), volatility_regime (3 TF), momentum_shift_30m,
  cvd_trend (2 TF), funding_rate_trend, oi_trend, liquidation_bias
- P3: Docstring updated with coverage depth breakdown
- P4: Phase 7 adds ATR absolute value cross-TF check

v37.0 changes from v36.0:
- Phase 5: 20 → 85+ features checked for non-default values
- Phase 7: Cross-TF expanded from RSI/ADX only → all comparable indicators
- Phase 8: Fixed to only check features actually extracted (not raw td keys)
- NEW Phase 12: _avail_* flags validation
- NEW Phase 13: Pre-computed categoricals cross-validation
- NEW Phase 14: Position/Account/SR data injection + extraction parity
- NEW Phase 15: External data individual feature validation (--with-external)
- Fix: 30M SMA config [5,20,50] matches production (was [5,20,50,200])
- Fix: 1D SMA config [200] matches production (was [5,20,50,200])

Usage:
  cd /home/linuxuser/nautilus_AlgVex && source venv/bin/activate && \
    python3 scripts/diagnose_feature_pipeline.py

  # With external data (API calls for sentiment, order flow, etc.)
  python3 scripts/diagnose_feature_pipeline.py --with-external

  # JSON output for automated analysis
  python3 scripts/diagnose_feature_pipeline.py --json
"""
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Project root
project_root = Path(__file__).parent.parent.absolute()
sys.path.insert(0, str(project_root))

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("feature_pipeline_diag")

# ═══════════════════════════════════════════════════════════════════
# Result Tracker
# ═══════════════════════════════════════════════════════════════════

class Results:
    def __init__(self):
        self.checks: List[Dict[str, Any]] = []
        self._phase = ""

    def phase(self, name: str):
        self._phase = name
        print(f"\n{'═' * 60}")
        print(f"  {name}")
        print(f"{'═' * 60}")

    def ok(self, name: str, detail: str = ""):
        self.checks.append({"phase": self._phase, "name": name, "status": "ok", "detail": detail})
        print(f"  ✅ {name}: {detail}")

    def fail(self, name: str, detail: str = ""):
        self.checks.append({"phase": self._phase, "name": name, "status": "fail", "detail": detail})
        print(f"  ❌ {name}: {detail}")

    def warn(self, name: str, detail: str = ""):
        self.checks.append({"phase": self._phase, "name": name, "status": "warn", "detail": detail})
        print(f"  ⚠️  {name}: {detail}")

    def summary(self) -> bool:
        ok = sum(1 for c in self.checks if c["status"] == "ok")
        fail = sum(1 for c in self.checks if c["status"] == "fail")
        warn = sum(1 for c in self.checks if c["status"] == "warn")
        total = len(self.checks)
        print(f"\n{'═' * 60}")
        print(f"  Feature Pipeline Diagnostic v37.1 Summary")
        print(f"{'═' * 60}")
        print(f"  Total: {total}  ✅ {ok}  ❌ {fail}  ⚠️  {warn}")
        if fail == 0:
            print(f"  ✅ ALL CHECKS PASSED")
        else:
            print(f"  ❌ {fail} CHECKS FAILED — feature pipeline has issues")
        print(f"{'═' * 60}")
        return fail == 0

    def to_json(self) -> str:
        ok = sum(1 for c in self.checks if c["status"] == "ok")
        fail = sum(1 for c in self.checks if c["status"] == "fail")
        return json.dumps({
            "timestamp": time.time(),
            "total": len(self.checks),
            "ok": ok, "fail": fail,
            "checks": self.checks
        }, indent=2, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════
# Helper: Build TechnicalIndicatorManager from Binance K-lines
# ═══════════════════════════════════════════════════════════════════

def _build_indicator_manager(klines: list, sma_periods: list, ema_periods: list,
                             bar_type_str: str):
    """Feed K-lines into a TechnicalIndicatorManager and return it."""
    from indicators.technical_manager import TechnicalIndicatorManager
    from nautilus_trader.model.data import Bar, BarType
    from nautilus_trader.model.objects import Price, Quantity

    manager = TechnicalIndicatorManager(
        rsi_period=14, macd_fast=12, macd_slow=26, macd_signal=9,
        bb_period=20, sma_periods=sma_periods, ema_periods=ema_periods,
    )
    bar_type = BarType.from_str(bar_type_str)

    for k in klines[:-1]:  # skip last incomplete bar
        bar = Bar(
            bar_type=bar_type,
            open=Price.from_str(k[1]),
            high=Price.from_str(k[2]),
            low=Price.from_str(k[3]),
            close=Price.from_str(k[4]),
            volume=Quantity.from_str(k[5]),
            ts_event=int(k[0]) * 1_000_000,
            ts_init=int(k[0]) * 1_000_000,
        )
        manager.update(bar)

    return manager


def _fetch_klines(symbol: str, interval: str, limit: int = 300) -> Optional[list]:
    """Fetch K-lines from Binance Futures API."""
    import requests
    url = "https://fapi.binance.com/fapi/v1/klines"
    try:
        resp = requests.get(url, params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and len(data) > 10:
            return data
    except Exception as e:
        logger.warning(f"K-line fetch failed ({interval}): {e}")
    return None


# ═══════════════════════════════════════════════════════════════════
# Phase 1: Build technical data (simulates production indicator pipeline)
# ═══════════════════════════════════════════════════════════════════

def phase1_build_technical_data(results: Results) -> Optional[Tuple[Dict[str, Any], float]]:
    """Build full technical_data dict from real Binance K-lines."""
    results.phase("Phase 1: Build Technical Data from Binance K-lines")

    from utils.config_manager import ConfigManager
    config = ConfigManager(env='production')
    config.load()

    # Read config for each layer — match PRODUCTION exactly
    # 30M base indicator_manager: ai_strategy.py line 600
    #   sma_periods = config.sma_periods if config.sma_periods else [5, 20, 50]
    #   ema_periods = [config.macd_fast, config.macd_slow] = [12, 26]
    # Note: StrategyConfig.sma_periods defaults to (5, 20, 50), NOT global config [5,20,50,200]
    macd_fast = config.get('indicators', 'macd_fast') or 12
    macd_slow = config.get('indicators', 'macd_slow') or 26
    base_sma = [5, 20, 50]  # matches ai_strategy.py StrategyConfig default
    base_ema = [macd_fast, macd_slow]  # [12, 26]

    # 4H decision layer: multi_timeframe_manager.py line 136-137
    dec_sma = config.get('mtf', 'decision_layer', 'indicators', 'sma_periods') or [20, 50]
    dec_ema = config.get('mtf', 'decision_layer', 'indicators', 'ema_periods') or [12, 26]

    # 1D trend layer: multi_timeframe_manager.py line 116-118
    #   sma_periods=[sma_period] where sma_period = trend_config.get('sma_period', 200)
    #   ema_periods=default_ema_periods = [12, 26]
    trend_sma_period = config.get('mtf', 'trend_layer', 'sma_period') or 200
    trend_sma = [trend_sma_period]  # [200] — matches production
    trend_ema = [12, 26]

    results.ok("Config loaded",
               f"30M SMA={base_sma} EMA={base_ema}, "
               f"4H SMA={dec_sma} EMA={dec_ema}, "
               f"1D SMA={trend_sma} EMA={trend_ema}")

    # Fetch K-lines for each timeframe
    klines_30m = _fetch_klines("BTCUSDT", "30m", limit=300)
    klines_4h = _fetch_klines("BTCUSDT", "4h", limit=200)
    klines_1d = _fetch_klines("BTCUSDT", "1d", limit=250)

    if not klines_30m:
        results.fail("30M K-lines", "Failed to fetch from Binance API")
        return None
    results.ok("30M K-lines", f"{len(klines_30m)} bars")

    if not klines_4h:
        results.fail("4H K-lines", "Failed to fetch from Binance API")
        return None
    results.ok("4H K-lines", f"{len(klines_4h)} bars")

    if not klines_1d:
        results.fail("1D K-lines", "Failed to fetch from Binance API")
        return None
    results.ok("1D K-lines", f"{len(klines_1d)} bars")

    # Build indicator managers — matching production config exactly
    mgr_30m = _build_indicator_manager(
        klines_30m, sma_periods=base_sma, ema_periods=base_ema,
        bar_type_str="BTCUSDT-PERP.BINANCE-30-MINUTE-LAST-EXTERNAL")
    mgr_4h = _build_indicator_manager(
        klines_4h, sma_periods=dec_sma, ema_periods=dec_ema,
        bar_type_str="BTCUSDT-PERP.BINANCE-240-MINUTE-LAST-EXTERNAL")
    mgr_1d = _build_indicator_manager(
        klines_1d, sma_periods=trend_sma, ema_periods=trend_ema,
        bar_type_str="BTCUSDT-PERP.BINANCE-1-DAY-LAST-EXTERNAL")

    price = float(klines_30m[-2][4])
    results.ok("Current price", f"${price:,.2f}")

    # Get technical data
    td_30m = mgr_30m.get_technical_data(current_price=price)
    td_4h = mgr_4h.get_technical_data(current_price=price)
    td_1d = mgr_1d.get_technical_data(current_price=price)

    # Get historical contexts
    hist_30m = mgr_30m.get_historical_context(count=20)
    hist_4h = mgr_4h.get_historical_context(count=16)
    hist_1d = mgr_1d.get_historical_context(count=10)

    # Assemble like production (ai_strategy.py pattern)
    technical_data = td_30m.copy()
    technical_data['price'] = price  # Production injects price separately
    technical_data['historical_context'] = hist_30m
    technical_data['mtf_decision_layer'] = td_4h.copy()
    technical_data['mtf_decision_layer']['historical_context'] = hist_4h
    technical_data['mtf_trend_layer'] = td_1d.copy()
    technical_data['mtf_trend_layer']['historical_context'] = hist_1d

    results.ok("Technical data assembled",
               f"30M keys={len(td_30m)}, 4H keys={len(td_4h)}, 1D keys={len(td_1d)}")

    return technical_data, price


# ═══════════════════════════════════════════════════════════════════
# Mock data builders for position/account/SR/FR contexts
# ═══════════════════════════════════════════════════════════════════

def _build_mock_position() -> Dict[str, Any]:
    """Build mock position data matching production _get_current_position_data() fields."""
    return {
        'side': 'LONG',
        'pnl_percentage': 1.25,       # v31.4: must be 'pnl_percentage' not 'pnl_pct'
        'margin_used_pct': 8.5,       # v31.4: must be 'margin_used_pct' not 'size_pct'
    }


def _build_mock_account() -> Dict[str, Any]:
    """Build mock account data matching production _get_account_context() fields."""
    return {
        'equity': 10000.0,
        'liquidation_buffer_portfolio_min_pct': 18.5,  # v31.4: full field name
        'leverage': 10,
    }


def _build_mock_sr_zones(price: float, atr: float):
    """Build mock SR zones using SRZone-like objects with dataclass attributes."""
    class MockSRZone:
        def __init__(self, price_center: float, strength: str):
            self.price_center = price_center
            self.strength = strength

    return {
        'nearest_support': MockSRZone(price_center=price - 2.5 * atr, strength='MEDIUM'),
        'nearest_resistance': MockSRZone(price_center=price + 1.8 * atr, strength='HIGH'),
    }


def _build_mock_fr_block_context() -> Dict[str, Any]:
    """Build mock FR block context for v21.0 testing."""
    return {
        'consecutive_blocks': 2,
        'blocked_direction': 'SHORT',
    }


# ═══════════════════════════════════════════════════════════════════
# Phase 2+: Extract features and validate
# ═══════════════════════════════════════════════════════════════════

def phase2_extract_and_validate(results: Results, technical_data: Dict,
                                current_price: float,
                                external_data: Optional[Dict] = None):
    """Run extract_features() and validate every feature."""
    results.phase("Phase 2: Extract Features")

    from agents.report_formatter import ReportFormatterMixin
    from agents.prompt_constants import FEATURE_SCHEMA

    formatter = ReportFormatterMixin.__new__(ReportFormatterMixin)
    formatter.logger = logging.getLogger("formatter")

    # Build mock data for position/account/SR/FR
    atr_30m = float(technical_data.get('atr', 300.0) or 300.0)
    mock_position = _build_mock_position()
    mock_account = _build_mock_account()
    mock_sr = _build_mock_sr_zones(current_price, atr_30m)
    mock_fr_ctx = _build_mock_fr_block_context()

    # Inject FR block context into technical_data (production does this in on_timer)
    technical_data['fr_block_context'] = mock_fr_ctx

    kwargs = {
        "technical_data": technical_data,
        "current_position": mock_position,
        "account_context": mock_account,
        "sr_zones": mock_sr,
    }
    if external_data:
        kwargs.update({
            "sentiment_data": external_data.get("sentiment_report"),
            "order_flow_data": external_data.get("order_flow_report"),
            "order_flow_4h": external_data.get("order_flow_report_4h"),
            "derivatives_data": external_data.get("derivatives_report"),
            "binance_derivatives": external_data.get("binance_derivatives_report"),
            "orderbook_data": external_data.get("orderbook_report"),
        })

    features = formatter.extract_features(**kwargs)

    results.ok("extract_features()", f"{len(features)} features extracted")

    # ── Phase 3: Schema completeness ──
    results.phase("Phase 3: Schema Completeness")
    schema_keys = set(FEATURE_SCHEMA.keys())
    feature_keys = set(features.keys())
    # _avail_* and _reliability/_unavailable are all valid feature keys
    schema_non_internal = {k for k in schema_keys if not k.startswith('_')}
    feature_non_internal = {k for k in feature_keys if not k.startswith('_')}
    missing_from_features = schema_non_internal - feature_non_internal
    extra_in_features = feature_non_internal - schema_non_internal

    if missing_from_features:
        results.fail("Schema completeness",
                     f"{len(missing_from_features)} schema keys missing from features: "
                     f"{sorted(missing_from_features)[:10]}")
    else:
        results.ok("Schema completeness", "All FEATURE_SCHEMA keys present in features")

    if extra_in_features:
        results.warn("Extra features",
                     f"{len(extra_in_features)} features not in schema: "
                     f"{sorted(extra_in_features)[:10]}")

    # _avail_* flags must all be present
    avail_keys = {k for k in schema_keys if k.startswith('_avail_')}
    avail_missing = avail_keys - feature_keys
    if avail_missing:
        results.fail("_avail_* completeness",
                     f"Missing _avail_* flags: {sorted(avail_missing)}")
    else:
        results.ok("_avail_* completeness", f"All {len(avail_keys)} _avail_* flags present")

    # ── Phase 4: Type validation ──
    results.phase("Phase 4: Type Validation")
    type_errors = 0
    for key, spec in FEATURE_SCHEMA.items():
        val = features.get(key)
        if val is None:
            continue
        if spec["type"] == "float":
            if not isinstance(val, (int, float)):
                results.fail(f"Type: {key}", f"Expected float, got {type(val).__name__}: {val}")
                type_errors += 1
        elif spec["type"] == "int":
            if not isinstance(val, int):
                results.fail(f"Type: {key}", f"Expected int, got {type(val).__name__}: {val}")
                type_errors += 1
        elif spec["type"] == "bool":
            if not isinstance(val, bool):
                results.fail(f"Type: {key}", f"Expected bool, got {type(val).__name__}: {val}")
                type_errors += 1
        elif spec["type"] == "enum":
            valid_values = spec.get("values", [])
            if str(val).upper() not in [v.upper() for v in valid_values]:
                results.fail(f"Type: {key}", f"'{val}' not in {valid_values}")
                type_errors += 1
    if type_errors == 0:
        results.ok("Type validation", f"All features have correct types")
    else:
        results.fail("Type validation", f"{type_errors} type errors")

    # ══════════════════════════════════════════════════════════════
    # Phase 5: Non-default value check — ALL features by category
    # ══════════════════════════════════════════════════════════════
    results.phase("Phase 5: Non-Default Value Check — 30M Execution Layer")

    # Helper: check a feature is non-default
    def _check_nondefault(key: str, reason: str, allow_negative: bool = False):
        val = features.get(key)
        if val is None:
            results.fail(f"Missing: {key}", reason)
        elif not allow_negative and isinstance(val, (int, float)) and val == 0.0:
            results.fail(f"Default: {key}", f"Value is {val} — {reason}")
        else:
            fmt = f"{val:.4f}" if isinstance(val, float) else str(val)
            results.ok(f"{key}", fmt)

    # 30M Execution Layer (17 float features from technical_data)
    _check_nondefault("price", "Price should not be 0")
    _check_nondefault("rsi_30m", "30M RSI should not be 0")
    _check_nondefault("macd_30m", "30M MACD should not be None", allow_negative=True)
    _check_nondefault("macd_signal_30m", "30M MACD Signal should not be None", allow_negative=True)
    _check_nondefault("macd_histogram_30m", "30M MACD Histogram", allow_negative=True)
    _check_nondefault("adx_30m", "30M ADX should not be 0")
    _check_nondefault("di_plus_30m", "30M DI+ should not be 0")
    _check_nondefault("di_minus_30m", "30M DI- should not be 0")
    _check_nondefault("bb_position_30m", "30M BB Position should not be 0")
    _check_nondefault("bb_upper_30m", "30M BB Upper should not be 0")
    _check_nondefault("bb_lower_30m", "30M BB Lower should not be 0")
    _check_nondefault("sma_5_30m", "30M SMA 5 should not be 0")
    _check_nondefault("sma_20_30m", "30M SMA 20 should not be 0")
    _check_nondefault("volume_ratio_30m", "30M Volume Ratio should not be 0")
    _check_nondefault("atr_pct_30m", "30M ATR% should not be 0")
    _check_nondefault("ema_12_30m", "30M EMA 12 (base: [12,26])")
    _check_nondefault("ema_26_30m", "30M EMA 26 (base: [12,26])")

    # 30M Risk Context (5 features)
    results.phase("Phase 5b: Non-Default — 30M Risk Context")
    _check_nondefault("atr_30m", "30M ATR absolute should not be 0")
    _check_nondefault("extension_ratio_30m", "30M Extension Ratio", allow_negative=True)
    # extension_regime_30m is enum — check it has a non-NONE value
    ext_regime_30m = features.get("extension_regime_30m")
    if ext_regime_30m and ext_regime_30m in ("NORMAL", "EXTENDED", "OVEREXTENDED", "EXTREME"):
        results.ok("extension_regime_30m", ext_regime_30m)
    else:
        results.fail("extension_regime_30m", f"Got '{ext_regime_30m}', expected valid regime")

    vol_regime_30m = features.get("volatility_regime_30m")
    if vol_regime_30m and vol_regime_30m in ("LOW", "NORMAL", "HIGH", "EXTREME"):
        results.ok("volatility_regime_30m", vol_regime_30m)
    else:
        results.fail("volatility_regime_30m", f"Got '{vol_regime_30m}', expected valid regime")

    _check_nondefault("volatility_percentile_30m", "30M Vol Percentile should not be 0")

    # 4H Decision Layer (21 features)
    results.phase("Phase 5c: Non-Default — 4H Decision Layer")
    _check_nondefault("rsi_4h", "4H RSI should not be 0")
    _check_nondefault("macd_4h", "4H MACD", allow_negative=True)
    _check_nondefault("macd_signal_4h", "4H MACD Signal", allow_negative=True)
    _check_nondefault("macd_histogram_4h", "4H MACD Histogram", allow_negative=True)
    _check_nondefault("adx_4h", "4H ADX should not be 0")
    _check_nondefault("di_plus_4h", "4H DI+ should not be 0")
    _check_nondefault("di_minus_4h", "4H DI- should not be 0")
    _check_nondefault("bb_position_4h", "4H BB Position should not be 0")
    _check_nondefault("bb_upper_4h", "4H BB Upper should not be 0")
    _check_nondefault("bb_lower_4h", "4H BB Lower should not be 0")
    _check_nondefault("sma_20_4h", "4H SMA 20 should not be 0")
    _check_nondefault("sma_50_4h", "4H SMA 50 should not be 0")
    _check_nondefault("volume_ratio_4h", "4H Volume Ratio should not be 0")
    _check_nondefault("atr_4h", "4H ATR should not be 0")
    _check_nondefault("atr_pct_4h", "4H ATR% should not be 0")
    _check_nondefault("ema_12_4h", "4H EMA 12 should not be 0")
    _check_nondefault("ema_26_4h", "4H EMA 26 should not be 0")
    _check_nondefault("extension_ratio_4h", "4H Extension Ratio", allow_negative=True)

    ext_regime_4h = features.get("extension_regime_4h")
    if ext_regime_4h and ext_regime_4h in ("NORMAL", "EXTENDED", "OVEREXTENDED", "EXTREME"):
        results.ok("extension_regime_4h", ext_regime_4h)
    else:
        results.fail("extension_regime_4h", f"Got '{ext_regime_4h}', expected valid regime")

    vol_regime_4h = features.get("volatility_regime_4h")
    if vol_regime_4h and vol_regime_4h in ("LOW", "NORMAL", "HIGH", "EXTREME"):
        results.ok("volatility_regime_4h", vol_regime_4h)
    else:
        results.fail("volatility_regime_4h", f"Got '{vol_regime_4h}', expected valid regime")

    _check_nondefault("volatility_percentile_4h", "4H Vol Percentile should not be 0")

    # 1D Trend Layer (18 features)
    results.phase("Phase 5d: Non-Default — 1D Trend Layer")
    _check_nondefault("adx_1d", "1D ADX should not be 0")
    _check_nondefault("di_plus_1d", "1D DI+ should not be 0")
    _check_nondefault("di_minus_1d", "1D DI- should not be 0")
    _check_nondefault("rsi_1d", "1D RSI should not be 0")
    _check_nondefault("macd_1d", "1D MACD", allow_negative=True)
    _check_nondefault("macd_signal_1d", "1D MACD Signal", allow_negative=True)
    _check_nondefault("macd_histogram_1d", "1D MACD Histogram", allow_negative=True)
    _check_nondefault("sma_200_1d", "1D SMA 200 should not be 0")
    _check_nondefault("bb_position_1d", "1D BB Position should not be 0")
    _check_nondefault("volume_ratio_1d", "1D Volume Ratio should not be 0")
    _check_nondefault("atr_1d", "1D ATR should not be 0")
    _check_nondefault("atr_pct_1d", "1D ATR% should not be 0")
    _check_nondefault("ema_12_1d", "1D EMA 12 should not be 0")
    _check_nondefault("ema_26_1d", "1D EMA 26 should not be 0")
    _check_nondefault("extension_ratio_1d", "1D Extension Ratio", allow_negative=True)

    ext_regime_1d = features.get("extension_regime_1d")
    if ext_regime_1d and ext_regime_1d in ("NORMAL", "EXTENDED", "OVEREXTENDED", "EXTREME"):
        results.ok("extension_regime_1d", ext_regime_1d)
    else:
        results.fail("extension_regime_1d", f"Got '{ext_regime_1d}', expected valid regime")

    vol_regime_1d = features.get("volatility_regime_1d")
    if vol_regime_1d and vol_regime_1d in ("LOW", "NORMAL", "HIGH", "EXTREME"):
        results.ok("volatility_regime_1d", vol_regime_1d)
    else:
        results.fail("volatility_regime_1d", f"Got '{vol_regime_1d}', expected valid regime")

    _check_nondefault("volatility_percentile_1d", "1D Vol Percentile should not be 0")

    # ── Phase 6: Time series features ──
    results.phase("Phase 6: Time Series Features (v36.0 Core)")

    ts_features = {
        # 1D time series
        "adx_1d_trend_5bar": "1D ADX trend",
        "rsi_1d_trend_5bar": "1D RSI trend",
        "di_spread_1d_trend_5bar": "1D DI+/DI- spread trend",
        "price_1d_change_5bar_pct": "1D price change over 5 bars",
        # 4H time series
        "rsi_4h_trend_5bar": "4H RSI trend",
        "adx_4h_trend_5bar": "4H ADX trend",
        "macd_histogram_4h_trend_5bar": "4H MACD histogram trend",
        "price_4h_change_5bar_pct": "4H price change over 5 bars",
        "bb_width_4h_trend_5bar": "4H BB width trend (squeeze/expansion)",
        # 30M time series
        "rsi_30m_trend_5bar": "30M RSI trend",
        "momentum_shift_30m": "30M momentum shift",
        "price_30m_change_5bar_pct": "30M price change over 5 bars",
        "bb_width_30m_trend_5bar": "30M BB width trend (squeeze/expansion)",
    }

    _flat_diag_map = {
        "adx_4h_trend_5bar": ("mtf_decision_layer", "adx_trend"),
        "bb_width_4h_trend_5bar": ("mtf_decision_layer", "bb_width_trend"),
        "adx_1d_trend_5bar": ("mtf_trend_layer", "adx_trend"),
        "bb_width_30m_trend_5bar": (None, "bb_width_trend"),
        "rsi_30m_trend_5bar": (None, "rsi_trend"),
        "rsi_4h_trend_5bar": ("mtf_decision_layer", "rsi_trend"),
        "rsi_1d_trend_5bar": ("mtf_trend_layer", "rsi_trend"),
        "macd_histogram_4h_trend_5bar": ("mtf_decision_layer", "macd_histogram_trend"),
    }

    # Features that use _classify_abs_trend (last/first ratio, ±15%) instead of _classify_trend (last/first %, ±5%)
    _abs_trend_features = {"macd_histogram_4h_trend_5bar"}

    def _compute_last_first_pct(series):
        """Mirror _classify_trend: last-vs-first percentage change."""
        if not series or len(series) < 2:
            return None
        return (series[-1] - series[0]) / max(abs(series[0]), 1e-9) * 100

    def _compute_abs_ratio(series):
        """Mirror _classify_abs_trend: last/first abs-value ratio."""
        if not series or len(series) < 2:
            return None
        first_val = abs(series[0])
        last_val = abs(series[-1])
        if first_val < 1e-9:
            return None
        return last_val / first_val

    flat_count = 0
    non_flat_count = 0
    for key, desc in ts_features.items():
        val = features.get(key)
        if val is None:
            results.fail(f"Missing: {key}", desc)
        elif isinstance(val, str) and val in ("FLAT", "STABLE"):
            flat_count += 1
            diag_detail = ""
            if key in _flat_diag_map:
                layer, hist_key = _flat_diag_map[key]
                if layer:
                    ctx = technical_data.get(layer, {}).get('historical_context', {})
                else:
                    ctx = technical_data.get('historical_context', {})
                arr = ctx.get(hist_key, [])
                if len(arr) >= 5:
                    last5 = arr[-5:]
                    rounded = [round(v, 2) for v in last5]
                    if key in _abs_trend_features:
                        ratio = _compute_abs_ratio(last5)
                        if ratio is not None:
                            diag_detail = (f" (last/first_ratio={ratio:.3f}, "
                                           f"EXPANDING>1.15/CONTRACTING<0.85, last5={rounded})")
                    else:
                        diff = _compute_last_first_pct(last5)
                        diag_detail = f" (last/first={diff:+.2f}%, threshold=±5%, last5={rounded})"
                else:
                    diag_detail = f" (array len={len(arr)}, need ≥5)"
            results.warn(f"{key} = {val}", f"{desc}{diag_detail}")
        elif isinstance(val, (int, float)) and val == 0.0:
            flat_count += 1
            results.warn(f"{key} = 0.0", f"Zero value — {desc}")
        else:
            non_flat_count += 1
            results.ok(f"{key}", f"{val}")

    total_ts = flat_count + non_flat_count
    if total_ts > 0 and flat_count == total_ts:
        results.fail("ALL TIME SERIES DEFAULT",
                     "Every time series feature is FLAT/0.0 — likely wrong data source (v36.0 bug class)")
    elif total_ts > 0 and flat_count > total_ts * 0.7:
        results.warn("Most time series default",
                     f"{flat_count}/{total_ts} are FLAT/0.0 — market may be calm or data source issue")
    elif total_ts > 0:
        results.ok("Time series diversity",
                    f"{non_flat_count}/{total_ts} have non-default values")

    # ══════════════════════════════════════════════════════════════
    # Phase 7: Cross-TF Consistency — ALL comparable indicators
    # ══════════════════════════════════════════════════════════════
    results.phase("Phase 7: Cross-TF Consistency (All Indicators)")

    # Each tuple: (name, 30M_key, 4H_key, 1D_key_or_None)
    cross_tf_checks = [
        ("RSI",            "rsi_30m",            "rsi_4h",            "rsi_1d"),
        ("ADX",            "adx_30m",            "adx_4h",            "adx_1d"),
        ("MACD",           "macd_30m",           "macd_4h",           "macd_1d"),
        ("MACD Signal",    "macd_signal_30m",    "macd_signal_4h",    "macd_signal_1d"),
        ("MACD Histogram", "macd_histogram_30m", "macd_histogram_4h", "macd_histogram_1d"),
        ("DI+",            "di_plus_30m",        "di_plus_4h",        "di_plus_1d"),
        ("DI-",            "di_minus_30m",       "di_minus_4h",       "di_minus_1d"),
        ("BB Position",    "bb_position_30m",    "bb_position_4h",    "bb_position_1d"),
        ("EMA 12",         "ema_12_30m",         "ema_12_4h",         "ema_12_1d"),
        ("EMA 26",         "ema_26_30m",         "ema_26_4h",         "ema_26_1d"),
        ("ATR%",           "atr_pct_30m",        "atr_pct_4h",        "atr_pct_1d"),
        ("ATR Absolute",   "atr_30m",            "atr_4h",            "atr_1d"),
        ("Volume Ratio",   "volume_ratio_30m",   "volume_ratio_4h",   "volume_ratio_1d"),
    ]

    for name, k30, k4h, k1d in cross_tf_checks:
        v30 = features.get(k30, 0)
        v4h = features.get(k4h, 0)
        v1d = features.get(k1d, 0) if k1d else None

        # 30M vs 4H
        if isinstance(v30, (int, float)) and isinstance(v4h, (int, float)):
            if v30 != 0 and v4h != 0 and abs(v30 - v4h) < 0.01:
                results.fail(f"{name} 30M vs 4H",
                             f"Identical ({v30:.4f}) — likely same data source!")
            elif v30 != 0 and v4h != 0:
                results.ok(f"{name} 30M vs 4H",
                           f"30M={v30:.2f}, 4H={v4h:.2f}")

        # 4H vs 1D
        if v1d is not None and isinstance(v4h, (int, float)) and isinstance(v1d, (int, float)):
            if v4h != 0 and v1d != 0 and abs(v4h - v1d) < 0.01:
                results.fail(f"{name} 4H vs 1D",
                             f"Identical ({v4h:.4f}) — likely same data source!")
            elif v4h != 0 and v1d != 0:
                results.ok(f"{name} 4H vs 1D",
                           f"4H={v4h:.2f}, 1D={v1d:.2f}")

    # ══════════════════════════════════════════════════════════════
    # Phase 8: Config-Feature Alignment (actually-extracted features only)
    # ══════════════════════════════════════════════════════════════
    results.phase("Phase 8: Config → Feature Extraction Alignment")

    td = technical_data
    mtf_dec = td.get('mtf_decision_layer', {})
    mtf_trend = td.get('mtf_trend_layer', {})

    # 30M: extract_features() pulls sma_5, sma_20, ema_12, ema_26
    # (NOT sma_50 or sma_200 — those exist in td but are not extracted to features)
    for raw_key, feat_key in [('sma_5', 'sma_5_30m'), ('sma_20', 'sma_20_30m'),
                               ('ema_12', 'ema_12_30m'), ('ema_26', 'ema_26_30m')]:
        raw_val = td.get(raw_key)
        feat_val = features.get(feat_key)
        if raw_val and feat_val and float(raw_val) > 0 and float(feat_val) > 0:
            if abs(float(raw_val) - float(feat_val)) < 0.01:
                results.ok(f"30M {raw_key} → {feat_key}", f"{float(feat_val):,.2f}")
            else:
                results.fail(f"30M {raw_key} → {feat_key}",
                             f"raw={float(raw_val):.2f} != feat={float(feat_val):.2f}")
        else:
            results.fail(f"30M {raw_key} → {feat_key}",
                         f"raw={raw_val}, feat={feat_val}")

    # 4H: sma_20, sma_50, ema_12, ema_26
    for raw_key, feat_key in [('sma_20', 'sma_20_4h'), ('sma_50', 'sma_50_4h'),
                               ('ema_12', 'ema_12_4h'), ('ema_26', 'ema_26_4h')]:
        raw_val = mtf_dec.get(raw_key)
        feat_val = features.get(feat_key)
        if raw_val and feat_val and float(raw_val) > 0 and float(feat_val) > 0:
            if abs(float(raw_val) - float(feat_val)) < 0.01:
                results.ok(f"4H {raw_key} → {feat_key}", f"{float(feat_val):,.2f}")
            else:
                results.fail(f"4H {raw_key} → {feat_key}",
                             f"raw={float(raw_val):.2f} != feat={float(feat_val):.2f}")
        else:
            results.fail(f"4H {raw_key} → {feat_key}",
                         f"raw={raw_val}, feat={feat_val}")

    # 1D: sma_200
    raw_val = mtf_trend.get('sma_200')
    feat_val = features.get('sma_200_1d')
    if raw_val and feat_val and float(raw_val) > 0 and float(feat_val) > 0:
        results.ok(f"1D sma_200 → sma_200_1d", f"{float(feat_val):,.2f}")
    else:
        results.fail(f"1D sma_200 → sma_200_1d",
                     f"raw={raw_val}, feat={feat_val}")

    # ── Phase 9: Historical Context Key Verification ──
    results.phase("Phase 9: Historical Context Key Verification")

    expected_hist_keys = ['price_trend', 'rsi_trend', 'adx_trend', 'di_plus_trend',
                          'di_minus_trend', 'bb_width_trend', 'macd_histogram_trend',
                          'obv_trend']

    for ctx_name, ctx in [("30M", td.get('historical_context', {})),
                           ("4H", mtf_dec.get('historical_context', {})),
                           ("1D", mtf_trend.get('historical_context', {}))]:
        if not ctx:
            results.fail(f"{ctx_name} historical_context", "Missing!")
            continue
        for key in expected_hist_keys:
            val = ctx.get(key)
            if isinstance(val, list) and len(val) >= 5:
                results.ok(f"{ctx_name} hist:{key}", f"{len(val)} values")
            elif isinstance(val, list):
                results.warn(f"{ctx_name} hist:{key}", f"Only {len(val)} values (need ≥5)")
            else:
                results.fail(f"{ctx_name} hist:{key}", f"Missing or not a list: {type(val)}")

    # ── Phase 10: Divergence detection ──
    results.phase("Phase 10: Divergence Detection")

    for div_key in ["rsi_divergence_4h", "macd_divergence_4h", "obv_divergence_4h",
                     "rsi_divergence_30m", "macd_divergence_30m", "obv_divergence_30m"]:
        val = features.get(div_key)
        if val is None:
            results.fail(f"Missing: {div_key}", "Divergence feature not extracted")
        elif val != "NONE":
            results.ok(f"{div_key}", f"{val} — divergence detected")
        else:
            results.ok(f"{div_key}", "NONE (no divergence — normal)")

    # ── Phase 11: Tag validation ──
    results.phase("Phase 11: Tag Validation Sanity")

    from agents.tag_validator import compute_valid_tags, compute_annotated_tags

    valid_tags = compute_valid_tags(features)
    annotated = compute_annotated_tags(features, valid_tags)

    results.ok("compute_valid_tags()", f"{len(valid_tags)} tags validated as available")

    sma5 = features.get("sma_5_30m", 0)
    sma20 = features.get("sma_20_30m", 0)
    if sma5 > 0 and sma20 > 0:
        expected_tag = "SMA_BULLISH_CROSS_30M" if sma5 > sma20 else "SMA_BEARISH_CROSS_30M"
        if expected_tag in valid_tags:
            results.ok(f"30M SMA cross tag", f"{expected_tag} ✓ (SMA5={sma5:.0f} vs SMA20={sma20:.0f})")
        else:
            results.fail(f"30M SMA cross tag", f"{expected_tag} should be valid but isn't")
    else:
        results.warn("30M SMA cross", "SMA values are 0 — cannot validate tags")

    bb_w_30m = features.get("bb_width_30m_trend_5bar", "FLAT")
    bb_w_4h = features.get("bb_width_4h_trend_5bar", "FLAT")
    if bb_w_30m == "FALLING" or bb_w_4h == "FALLING":
        if "BB_SQUEEZE" in valid_tags:
            results.ok("BB_SQUEEZE tag", f"Validated from data (30M={bb_w_30m}, 4H={bb_w_4h})")
        else:
            results.fail("BB_SQUEEZE tag", "BB width FALLING but tag not validated")
    elif bb_w_30m == "RISING" or bb_w_4h == "RISING":
        if "BB_EXPANSION" in valid_tags:
            results.ok("BB_EXPANSION tag", f"Validated from data (30M={bb_w_30m}, 4H={bb_w_4h})")
        else:
            results.fail("BB_EXPANSION tag", "BB width RISING but tag not validated")
    else:
        if "BB_SQUEEZE" not in valid_tags and "BB_EXPANSION" not in valid_tags:
            results.ok("BB tags", f"Correctly absent when width is FLAT (30M={bb_w_30m}, 4H={bb_w_4h})")
        else:
            results.fail("BB tags", "BB_SQUEEZE/EXPANSION present but width is FLAT — should not happen")

    # ══════════════════════════════════════════════════════════════
    # Phase 12: _avail_* Flags Validation (v34.1)
    # ══════════════════════════════════════════════════════════════
    results.phase("Phase 12: Data Availability Flags (_avail_*)")

    # With mock data injected, these should be True
    avail_checks_true = {
        '_avail_mtf_4h': 'mtf_decision_layer provided',
        '_avail_mtf_1d': 'mtf_trend_layer provided',
        '_avail_account': 'account_context provided (mock)',
        '_avail_sr_zones': 'sr_zones provided (mock)',
    }
    for key, reason in avail_checks_true.items():
        val = features.get(key)
        if val is True:
            results.ok(f"{key}=True", reason)
        else:
            results.fail(f"{key}", f"Expected True ({reason}), got {val}")

    # Without external data, these should be False
    if not external_data:
        avail_checks_false = {
            '_avail_order_flow': 'order_flow_data not provided',
            '_avail_derivatives': 'derivatives_data not provided',
            '_avail_binance_derivatives': 'binance_derivatives not provided',
            '_avail_orderbook': 'orderbook_data not provided',
            '_avail_sentiment': 'sentiment_data not provided',
        }
        for key, reason in avail_checks_false.items():
            val = features.get(key)
            if val is False:
                results.ok(f"{key}=False", reason)
            else:
                results.fail(f"{key}", f"Expected False ({reason}), got {val}")
    else:
        # With external data, at least some should be True
        ext_avail_keys = ['_avail_order_flow', '_avail_derivatives',
                          '_avail_binance_derivatives', '_avail_orderbook',
                          '_avail_sentiment']
        true_count = sum(1 for k in ext_avail_keys if features.get(k) is True)
        results.ok("External _avail_* flags", f"{true_count}/{len(ext_avail_keys)} True with external data")

    # ══════════════════════════════════════════════════════════════
    # Phase 13: Pre-computed Categoricals Cross-Validation
    # ══════════════════════════════════════════════════════════════
    results.phase("Phase 13: Pre-computed Categorical Logic Verification")

    # market_regime: v39.0 uses max(1D, 4H) ADX → >=40 STRONG, >=25 WEAK, <25 RANGING
    adx_1d_val = features.get("adx_1d", 0)
    adx_4h_val = features.get("adx_4h", 0)
    effective_adx = max(adx_1d_val, adx_4h_val)
    market_regime = features.get("market_regime")
    if effective_adx >= 40:
        expected_regime = "STRONG_TREND"
    elif effective_adx >= 25:
        expected_regime = "WEAK_TREND"
    else:
        expected_regime = "RANGING"
    adx_source = "4H" if adx_4h_val >= adx_1d_val else "1D"
    if market_regime == expected_regime:
        results.ok("market_regime", f"max(1D={adx_1d_val:.1f},4H={adx_4h_val:.1f})={effective_adx:.1f}(src={adx_source}) → {market_regime} ✓")
    else:
        results.fail("market_regime",
                     f"max(1D={adx_1d_val:.1f},4H={adx_4h_val:.1f})={effective_adx:.1f} → expected {expected_regime}, got {market_regime}")

    # adx_direction_1d: DI+ > DI- → BULLISH, DI- > DI+ → BEARISH, equal → NEUTRAL (v36.2)
    di_p_1d = features.get("di_plus_1d", 0)
    di_m_1d = features.get("di_minus_1d", 0)
    adx_dir = features.get("adx_direction_1d")
    if di_p_1d > di_m_1d:
        expected_dir = "BULLISH"
    elif di_m_1d > di_p_1d:
        expected_dir = "BEARISH"
    else:
        expected_dir = "NEUTRAL"
    if adx_dir == expected_dir:
        results.ok("adx_direction_1d", f"DI+={di_p_1d:.1f}, DI-={di_m_1d:.1f} → {adx_dir} ✓")
    else:
        results.fail("adx_direction_1d",
                     f"DI+={di_p_1d:.1f}, DI-={di_m_1d:.1f} → expected {expected_dir}, got {adx_dir}")

    # MACD cross: MACD > Signal → BULLISH (with ATR-relative threshold)
    for suffix in ("30m", "4h", "1d"):
        macd_val = features.get(f"macd_{suffix}", 0.0)
        sig_val = features.get(f"macd_signal_{suffix}", 0.0)
        cross_val = features.get(f"macd_cross_{suffix}")
        diff = macd_val - sig_val
        atr_ref = features.get(f"atr_pct_{suffix}", 0.0)
        threshold = atr_ref * 0.01 if atr_ref > 0 else 0.0
        if diff > threshold:
            expected_cross = "BULLISH"
        elif diff < -threshold:
            expected_cross = "BEARISH"
        else:
            expected_cross = "NEUTRAL"
        if cross_val == expected_cross:
            results.ok(f"macd_cross_{suffix}", f"MACD-Sig={diff:.2f}, threshold={threshold:.4f} → {cross_val} ✓")
        else:
            results.fail(f"macd_cross_{suffix}",
                         f"MACD-Sig={diff:.2f}, threshold={threshold:.4f} → expected {expected_cross}, got {cross_val}")

    # DI direction: DI+ > DI- → BULLISH (30M and 4H only)
    for suffix in ("30m", "4h"):
        di_p = features.get(f"di_plus_{suffix}", 0)
        di_m = features.get(f"di_minus_{suffix}", 0)
        di_dir = features.get(f"di_direction_{suffix}")
        expected_di = "BULLISH" if di_p > di_m else "BEARISH"
        if di_dir == expected_di:
            results.ok(f"di_direction_{suffix}", f"DI+={di_p:.1f}, DI-={di_m:.1f} → {di_dir} ✓")
        else:
            results.fail(f"di_direction_{suffix}",
                         f"DI+={di_p:.1f}, DI-={di_m:.1f} → expected {expected_di}, got {di_dir}")

    # RSI zone: < 30 → OVERSOLD, > 70 → OVERBOUGHT, else NEUTRAL
    for suffix in ("30m", "4h", "1d"):
        rsi_val = features.get(f"rsi_{suffix}", 50.0)
        zone = features.get(f"rsi_zone_{suffix}")
        if rsi_val < 30:
            expected_zone = "OVERSOLD"
        elif rsi_val > 70:
            expected_zone = "OVERBOUGHT"
        else:
            expected_zone = "NEUTRAL"
        if zone == expected_zone:
            results.ok(f"rsi_zone_{suffix}", f"RSI={rsi_val:.1f} → {zone} ✓")
        else:
            results.fail(f"rsi_zone_{suffix}",
                         f"RSI={rsi_val:.1f} → expected {expected_zone}, got {zone}")

    # FR direction: > 0.005 → POSITIVE, < -0.005 → NEGATIVE, else NEUTRAL
    fr_val = features.get("funding_rate_pct", 0.0)
    fr_dir = features.get("fr_direction")
    if fr_val > 0.005:
        expected_fr = "POSITIVE"
    elif fr_val < -0.005:
        expected_fr = "NEGATIVE"
    else:
        expected_fr = "NEUTRAL"
    if fr_dir == expected_fr:
        results.ok("fr_direction", f"FR={fr_val:.5f} → {fr_dir} ✓")
    else:
        results.fail("fr_direction",
                     f"FR={fr_val:.5f} → expected {expected_fr}, got {fr_dir}")

    # Extension regime: re-derive from extension_ratio using SSoT thresholds
    from utils.shared_logic import EXTENSION_THRESHOLDS, VOLATILITY_REGIME_THRESHOLDS

    for suffix in ("30m", "4h", "1d"):
        ext_ratio = features.get(f"extension_ratio_{suffix}", 0.0)
        ext_regime = features.get(f"extension_regime_{suffix}")
        abs_ratio = abs(ext_ratio)
        if abs_ratio >= EXTENSION_THRESHOLDS["EXTREME"]:
            expected_ext = "EXTREME"
        elif abs_ratio >= EXTENSION_THRESHOLDS["OVEREXTENDED"]:
            expected_ext = "OVEREXTENDED"
        elif abs_ratio >= EXTENSION_THRESHOLDS["EXTENDED"]:
            expected_ext = "EXTENDED"
        else:
            expected_ext = "NORMAL"
        if ext_regime == expected_ext:
            results.ok(f"extension_regime_{suffix}",
                       f"ratio={ext_ratio:.2f} → {ext_regime} ✓")
        else:
            results.fail(f"extension_regime_{suffix}",
                         f"ratio={ext_ratio:.2f} → expected {expected_ext}, got {ext_regime}")

    # Volatility regime: re-derive from volatility_percentile using SSoT thresholds
    for suffix in ("30m", "4h", "1d"):
        vol_pct = features.get(f"volatility_percentile_{suffix}", 50.0)
        vol_regime = features.get(f"volatility_regime_{suffix}")
        if vol_pct >= VOLATILITY_REGIME_THRESHOLDS["EXTREME"]:
            expected_vol = "EXTREME"
        elif vol_pct >= VOLATILITY_REGIME_THRESHOLDS["HIGH"]:
            expected_vol = "HIGH"
        elif vol_pct >= VOLATILITY_REGIME_THRESHOLDS["LOW"]:
            expected_vol = "NORMAL"
        else:
            expected_vol = "LOW"
        if vol_regime == expected_vol:
            results.ok(f"volatility_regime_{suffix}",
                       f"percentile={vol_pct:.1f} → {vol_regime} ✓")
        else:
            results.fail(f"volatility_regime_{suffix}",
                         f"percentile={vol_pct:.1f} → expected {expected_vol}, got {vol_regime}")

    # momentum_shift_30m: re-derive from RSI historical context
    rsi_30m_hist = technical_data.get('historical_context', {}).get('rsi_trend', [])
    mom_shift = features.get("momentum_shift_30m")
    if len(rsi_30m_hist) >= 5:
        recent_slope = rsi_30m_hist[-1] - rsi_30m_hist[-3]
        older_slope = rsi_30m_hist[-3] - rsi_30m_hist[-5]
        if abs(recent_slope) > abs(older_slope) * 1.3:
            expected_mom = "ACCELERATING"
        elif abs(recent_slope) < abs(older_slope) * 0.7:
            expected_mom = "DECELERATING"
        else:
            expected_mom = "STABLE"
        if mom_shift == expected_mom:
            results.ok("momentum_shift_30m",
                       f"recent={recent_slope:+.2f}, older={older_slope:+.2f} → {mom_shift} ✓")
        else:
            results.fail("momentum_shift_30m",
                         f"recent={recent_slope:+.2f}, older={older_slope:+.2f} → expected {expected_mom}, got {mom_shift}")
    else:
        if mom_shift in ("ACCELERATING", "DECELERATING", "STABLE"):
            results.ok("momentum_shift_30m", f"{mom_shift} (insufficient history for re-derivation)")
        else:
            results.fail("momentum_shift_30m", f"Invalid value: {mom_shift}")

    # sentiment_degraded: without external data should be False (mock has no degraded flag)
    sent_degraded = features.get("sentiment_degraded")
    if not external_data:
        if sent_degraded is False:
            results.ok("sentiment_degraded", "False (no sentiment data → not degraded)")
        else:
            results.fail("sentiment_degraded",
                         f"Expected False without external data, got {sent_degraded}")
    else:
        sr = external_data.get("sentiment_report") or {}
        expected_degraded = bool(sr.get('degraded', False))
        if sent_degraded == expected_degraded:
            results.ok("sentiment_degraded", f"{sent_degraded} (matches sentiment_report.degraded)")
        else:
            results.fail("sentiment_degraded",
                         f"Expected {expected_degraded} from sentiment_report, got {sent_degraded}")

    # cvd_trend / funding_rate_trend / oi_trend / liquidation_bias — enum validity
    # These depend on external data; cannot re-derive from mock, but validate enum range
    _enum_checks = {
        "cvd_trend_30m": ("POSITIVE", "NEGATIVE", "NEUTRAL"),
        "cvd_trend_4h": ("POSITIVE", "NEGATIVE", "NEUTRAL"),
        "funding_rate_trend": ("RISING", "FALLING", "STABLE"),
        "oi_trend": ("RISING", "FALLING", "STABLE"),
        "liquidation_bias": ("LONG_DOMINANT", "SHORT_DOMINANT", "BALANCED", "NONE"),
    }
    for key, valid_vals in _enum_checks.items():
        val = features.get(key)
        if val is None:
            # Without external data these default to None — acceptable
            if not external_data:
                results.ok(f"{key}", "None (no external data)")
            else:
                results.fail(f"{key}", "None despite external data available")
        elif val in valid_vals:
            results.ok(f"{key}", f"{val} ✓")
        else:
            results.fail(f"{key}", f"'{val}' not in {valid_vals}")

    # ══════════════════════════════════════════════════════════════
    # Phase 14: Position / Account / SR / FR Block Context
    # ══════════════════════════════════════════════════════════════
    results.phase("Phase 14: Position / Account / SR / FR Data Extraction")

    # Position (v31.4 field parity: pnl_percentage, margin_used_pct)
    pos_side = features.get("position_side")
    if pos_side == "LONG":
        results.ok("position_side", "LONG (from mock)")
    else:
        results.fail("position_side", f"Expected LONG, got {pos_side}")

    pos_pnl = features.get("position_pnl_pct")
    if isinstance(pos_pnl, (int, float)) and abs(pos_pnl - 1.25) < 0.01:
        results.ok("position_pnl_pct", f"{pos_pnl} (v31.4: from 'pnl_percentage')")
    else:
        results.fail("position_pnl_pct",
                     f"Expected 1.25, got {pos_pnl} — v31.4 field name 'pnl_percentage' mismatch?")

    pos_size = features.get("position_size_pct")
    if isinstance(pos_size, (int, float)) and abs(pos_size - 8.5) < 0.01:
        results.ok("position_size_pct", f"{pos_size} (v31.4: from 'margin_used_pct')")
    else:
        results.fail("position_size_pct",
                     f"Expected 8.5, got {pos_size} — v31.4 field name 'margin_used_pct' mismatch?")

    # Account
    equity = features.get("account_equity_usdt")
    if isinstance(equity, (int, float)) and abs(equity - 10000.0) < 0.01:
        results.ok("account_equity_usdt", f"{equity}")
    else:
        results.fail("account_equity_usdt", f"Expected 10000.0, got {equity}")

    liq_buffer = features.get("liquidation_buffer_pct")
    if isinstance(liq_buffer, (int, float)) and abs(liq_buffer - 18.5) < 0.01:
        results.ok("liquidation_buffer_pct",
                   f"{liq_buffer} (v31.4: from 'liquidation_buffer_portfolio_min_pct')")
    else:
        results.fail("liquidation_buffer_pct",
                     f"Expected 18.5, got {liq_buffer} — v31.4 field name mismatch?")

    leverage = features.get("leverage")
    if leverage == 10:
        results.ok("leverage", f"{leverage}")
    else:
        results.fail("leverage", f"Expected 10, got {leverage}")

    # S/R Zones (getattr() path from SRZone dataclass)
    sup_price = features.get("nearest_support_price")
    if isinstance(sup_price, (int, float)) and sup_price > 0:
        results.ok("nearest_support_price", f"${sup_price:,.2f} (mock SR injected)")
    else:
        results.fail("nearest_support_price",
                     f"Expected > 0, got {sup_price} — SRZone.price_center extraction failed?")

    sup_strength = features.get("nearest_support_strength")
    if sup_strength == "MEDIUM":
        results.ok("nearest_support_strength", "MEDIUM (from mock)")
    else:
        results.fail("nearest_support_strength",
                     f"Expected MEDIUM, got {sup_strength} — SRZone.strength extraction failed?")

    sup_dist = features.get("nearest_support_dist_atr")
    if isinstance(sup_dist, (int, float)) and sup_dist > 0:
        results.ok("nearest_support_dist_atr", f"{sup_dist:.2f} ATR")
    else:
        results.fail("nearest_support_dist_atr",
                     f"Expected > 0, got {sup_dist}")

    res_price = features.get("nearest_resist_price")
    if isinstance(res_price, (int, float)) and res_price > 0:
        results.ok("nearest_resist_price", f"${res_price:,.2f}")
    else:
        results.fail("nearest_resist_price", f"Expected > 0, got {res_price}")

    res_strength = features.get("nearest_resist_strength")
    if res_strength == "HIGH":
        results.ok("nearest_resist_strength", "HIGH (from mock)")
    else:
        results.fail("nearest_resist_strength", f"Expected HIGH, got {res_strength}")

    res_dist = features.get("nearest_resist_dist_atr")
    if isinstance(res_dist, (int, float)) and res_dist > 0:
        results.ok("nearest_resist_dist_atr", f"{res_dist:.2f} ATR")
    else:
        results.fail("nearest_resist_dist_atr", f"Expected > 0, got {res_dist}")

    # FR Block Context (v21.0)
    fr_blocks = features.get("fr_consecutive_blocks")
    if fr_blocks == 2:
        results.ok("fr_consecutive_blocks", f"{fr_blocks} (from mock)")
    else:
        results.fail("fr_consecutive_blocks", f"Expected 2, got {fr_blocks}")

    fr_block_dir = features.get("fr_blocked_direction")
    if fr_block_dir == "SHORT":
        results.ok("fr_blocked_direction", f"{fr_block_dir} (from mock)")
    else:
        results.fail("fr_blocked_direction", f"Expected SHORT, got {fr_block_dir}")

    # ══════════════════════════════════════════════════════════════
    # Phase 15: External Data Individual Feature Validation
    # ══════════════════════════════════════════════════════════════
    if external_data:
        results.phase("Phase 15: External Data Feature Extraction (--with-external)")

        # Order flow features
        if external_data.get("order_flow_report"):
            of = external_data["order_flow_report"]
            cvd_trend = features.get("cvd_trend_30m")
            if cvd_trend and cvd_trend in ("POSITIVE", "NEGATIVE", "NEUTRAL"):
                results.ok("cvd_trend_30m", f"{cvd_trend} (from API)")
            else:
                results.fail("cvd_trend_30m", f"Got '{cvd_trend}' with order_flow available")

            buy_ratio = features.get("buy_ratio_30m")
            if isinstance(buy_ratio, (int, float)) and buy_ratio > 0:
                results.ok("buy_ratio_30m", f"{buy_ratio:.4f}")
            else:
                results.fail("buy_ratio_30m", f"Expected > 0 with order_flow, got {buy_ratio}")

            cvd_cum = features.get("cvd_cumulative_30m")
            # cvd_cumulative can be negative, just check it's not None
            if isinstance(cvd_cum, (int, float)):
                results.ok("cvd_cumulative_30m", f"{cvd_cum:.2f}")
            else:
                results.fail("cvd_cumulative_30m", f"Expected float, got {cvd_cum}")

            cvd_cross = features.get("cvd_price_cross_30m")
            if cvd_cross and cvd_cross in ("ACCUMULATION", "DISTRIBUTION", "CONFIRMED_SELL",
                                            "ABSORPTION_BUY", "ABSORPTION_SELL", "NONE"):
                results.ok("cvd_price_cross_30m", f"{cvd_cross}")
            else:
                results.fail("cvd_price_cross_30m", f"Invalid value: {cvd_cross}")

        # 4H CVD
        if external_data.get("order_flow_report_4h"):
            cvd_4h = features.get("cvd_trend_4h")
            if cvd_4h and cvd_4h in ("POSITIVE", "NEGATIVE", "NEUTRAL"):
                results.ok("cvd_trend_4h", f"{cvd_4h}")
            else:
                results.fail("cvd_trend_4h", f"Got '{cvd_4h}' with 4H order flow available")

            cvd_cross_4h = features.get("cvd_price_cross_4h")
            if cvd_cross_4h and cvd_cross_4h in ("ACCUMULATION", "DISTRIBUTION", "CONFIRMED_SELL",
                                                    "ABSORPTION_BUY", "ABSORPTION_SELL", "NONE"):
                results.ok("cvd_price_cross_4h", f"{cvd_cross_4h}")
            else:
                results.fail("cvd_price_cross_4h", f"Invalid value: {cvd_cross_4h}")

            buy_4h = features.get("buy_ratio_4h")
            if isinstance(buy_4h, (int, float)) and buy_4h > 0:
                results.ok("buy_ratio_4h", f"{buy_4h:.4f}")
            else:
                results.fail("buy_ratio_4h", f"Expected > 0, got {buy_4h}")

        # Derivatives (FR, OI, liquidation)
        if external_data.get("derivatives_report"):
            fr_pct = features.get("funding_rate_pct")
            if isinstance(fr_pct, (int, float)):
                results.ok("funding_rate_pct", f"{fr_pct:.5f}")
            else:
                results.fail("funding_rate_pct", f"Expected float, got {fr_pct}")

            fr_trend = features.get("funding_rate_trend")
            if fr_trend and fr_trend in ("RISING", "FALLING", "STABLE"):
                results.ok("funding_rate_trend", f"{fr_trend}")
            else:
                results.fail("funding_rate_trend", f"Got '{fr_trend}'")

            oi = features.get("oi_trend")
            if oi and oi in ("RISING", "FALLING", "STABLE"):
                results.ok("oi_trend", f"{oi}")
            else:
                results.fail("oi_trend", f"Got '{oi}'")

            liq_bias = features.get("liquidation_bias")
            if liq_bias and liq_bias in ("LONG_DOMINANT", "SHORT_DOMINANT", "BALANCED", "NONE"):
                results.ok("liquidation_bias", f"{liq_bias}")
            else:
                results.fail("liquidation_bias", f"Got '{liq_bias}'")

            premium = features.get("premium_index")
            if isinstance(premium, (int, float)):
                results.ok("premium_index", f"{premium:.6f}")
            else:
                results.fail("premium_index", f"Expected float, got {premium}")

        # Orderbook
        if external_data.get("orderbook_report"):
            for key in ("obi_weighted", "obi_change_pct", "bid_volume_usd", "ask_volume_usd"):
                val = features.get(key)
                if isinstance(val, (int, float)):
                    results.ok(key, f"{val:.4f}")
                else:
                    results.fail(key, f"Expected float with orderbook data, got {val}")

        # Sentiment
        sr = external_data.get("sentiment_report")
        if sr and not sr.get('degraded'):
            long_r = features.get("long_ratio")
            short_r = features.get("short_ratio")
            if isinstance(long_r, (int, float)) and long_r > 0:
                results.ok("long_ratio", f"{long_r:.4f}")
            else:
                results.fail("long_ratio", f"Expected > 0 with sentiment, got {long_r}")
            if isinstance(short_r, (int, float)) and short_r > 0:
                results.ok("short_ratio", f"{short_r:.4f}")
            else:
                results.fail("short_ratio", f"Expected > 0 with sentiment, got {short_r}")

        # Top traders (binance_derivatives)
        if external_data.get("binance_derivatives_report"):
            top_lr = features.get("top_traders_long_ratio")
            if isinstance(top_lr, (int, float)) and top_lr > 0:
                results.ok("top_traders_long_ratio", f"{top_lr:.4f}")
            else:
                results.fail("top_traders_long_ratio", f"Expected > 0, got {top_lr}")

            taker_br = features.get("taker_buy_ratio")
            if isinstance(taker_br, (int, float)) and taker_br > 0:
                results.ok("taker_buy_ratio", f"{taker_br:.4f}")
            else:
                results.fail("taker_buy_ratio", f"Expected > 0, got {taker_br}")

    return features


# ═══════════════════════════════════════════════════════════════════
# External data fetch
# ═══════════════════════════════════════════════════════════════════

def fetch_external_data(results: Results, current_price: float = 0) -> Optional[Dict]:
    """Fetch external data using AIDataAssembler."""
    results.phase("External Data Fetch")

    try:
        import os
        from utils.config_manager import ConfigManager
        from utils.ai_data_assembler import AIDataAssembler
        from utils.binance_kline_client import BinanceKlineClient
        from utils.order_flow_processor import OrderFlowProcessor
        from utils.coinalyze_client import CoinalyzeClient
        from utils.sentiment_client import SentimentDataFetcher
        from utils.binance_orderbook_client import BinanceOrderBookClient
        from utils.binance_derivatives_client import BinanceDerivativesClient
        from utils.orderbook_processor import OrderBookProcessor

        config = ConfigManager(env='production')
        config.load()

        kline_client = BinanceKlineClient(timeout=10)
        processor = OrderFlowProcessor(logger=None)
        coinalyze_cfg = config.get('order_flow', 'coinalyze') or {}
        coinalyze_api_key = (coinalyze_cfg.get('api_key') if isinstance(coinalyze_cfg, dict) else None) or os.getenv('COINALYZE_API_KEY')
        coinalyze_client = CoinalyzeClient(api_key=coinalyze_api_key, timeout=10, logger=None)
        sentiment_client = SentimentDataFetcher()
        orderbook_client = BinanceOrderBookClient(timeout=10)
        derivatives_client = BinanceDerivativesClient(timeout=10)
        orderbook_processor = OrderBookProcessor(logger=None)

        assembler = AIDataAssembler(
            binance_kline_client=kline_client,
            order_flow_processor=processor,
            coinalyze_client=coinalyze_client,
            sentiment_client=sentiment_client,
            binance_derivatives_client=derivatives_client,
            binance_orderbook_client=orderbook_client,
            orderbook_processor=orderbook_processor,
            logger=None,
        )
        data = assembler.fetch_external_data(
            symbol="BTCUSDT",
            interval="30m",
            current_price=current_price,
            volatility=0.02,
        )

        for key in ['sentiment_report', 'order_flow_report', 'order_flow_report_4h',
                     'derivatives_report', 'orderbook_report', 'binance_derivatives_report']:
            val = data.get(key)
            if val:
                detail = f"{len(val)} keys" if isinstance(val, dict) else "available"
                results.ok(f"{key}", detail)
            else:
                results.warn(f"{key}", "None (degraded mode)")

        return data
    except Exception as e:
        results.fail("External data fetch", str(e))
        return None


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Feature Pipeline Diagnostic v37.1")
    parser.add_argument("--with-external", action="store_true",
                        help="Also fetch external data (sentiment, order flow, etc.)")
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON")
    args = parser.parse_args()

    print("╔════════════════════════════════════════════════════════════╗")
    print("║   Feature Pipeline Diagnostic v37.1                      ║")
    print("║   Full coverage: all data · all indicators · all TFs     ║")
    print("╚════════════════════════════════════════════════════════════╝")

    results = Results()

    # Phase 1: Build technical data
    result = phase1_build_technical_data(results)
    if not result:
        print("\n❌ Cannot proceed without technical data")
        if args.json:
            print(results.to_json())
        return 1
    technical_data, current_price = result

    # Phase 2 (optional): External data
    external_data = None
    if args.with_external:
        external_data = fetch_external_data(results, current_price=current_price)

    # Phase 2+: Extract and validate features (all phases)
    phase2_extract_and_validate(results, technical_data, current_price, external_data)

    # Summary
    success = results.summary()

    if args.json:
        print("\n--- JSON OUTPUT ---")
        print(results.to_json())

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
