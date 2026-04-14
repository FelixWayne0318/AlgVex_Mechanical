#!/usr/bin/env python3
"""
Mechanical Mode End-to-End Diagnostic (v1.0)

Validates the ENTIRE mechanical trading pipeline from data acquisition to
order execution readiness. Designed as the primary health check for
mechanical mode (replacing diagnose_realtime.py which targets AI mode).

Pipeline under test:
  Binance API → 13 data classes → extract_features() →
  compute_anticipatory_scores() → mechanical_decide() →
  Gate checks → SL/TP calculation → Order readiness

Usage:
  python3 scripts/diagnose_mechanical.py              # Full diagnostic
  python3 scripts/diagnose_mechanical.py --quick      # Skip API calls (offline)
  python3 scripts/diagnose_mechanical.py --fix-check  # Verify known bug fixes
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Project root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


# ============================================================================
# Result tracking
# ============================================================================
class DiagResult:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.warnings = 0
        self.details: List[str] = []

    def ok(self, msg: str):
        self.passed += 1
        self.details.append(f"  ✅ {msg}")
        print(f"  ✅ {msg}")

    def fail(self, msg: str):
        self.failed += 1
        self.details.append(f"  ❌ {msg}")
        print(f"  ❌ {msg}")

    def warn(self, msg: str):
        self.warnings += 1
        self.details.append(f"  ⚠️ {msg}")
        print(f"  ⚠️ {msg}")

    def summary(self) -> str:
        total = self.passed + self.failed
        status = "PASS" if self.failed == 0 else "FAIL"
        return (
            f"\n{'='*60}\n"
            f"  Mechanical Mode Diagnostic: {status}\n"
            f"  Passed: {self.passed}/{total} | Warnings: {self.warnings}\n"
            f"{'='*60}"
        )


# ============================================================================
# Phase 1: Configuration & Import Validation
# ============================================================================
def phase1_config(r: DiagResult):
    print("\n" + "="*60)
    print("  Phase 1: Configuration & Import Validation")
    print("="*60)

    # 1.1 Config loads correctly
    try:
        from utils.config_manager import ConfigManager
        cm = ConfigManager()
        cm.load()
        r.ok("ConfigManager loads successfully")
    except Exception as e:
        r.fail(f"ConfigManager load failed: {e}")
        return

    # 1.2 strategy_mode = mechanical
    mode = cm.get('strategy_mode') or 'ai'
    if mode == 'mechanical':
        r.ok(f"strategy_mode = '{mode}'")
    else:
        r.warn(f"strategy_mode = '{mode}' (expected 'mechanical')")

    # 1.3 regime_config exists and has required keys
    rc = cm.get('anticipatory', 'regime_config')
    if rc and isinstance(rc, dict):
        expected_regimes = {'TRENDING', 'RANGING', 'MEAN_REVERSION', 'VOLATILE', 'DEFAULT'}
        found = set(rc.keys())
        missing = expected_regimes - found
        if not missing:
            r.ok(f"regime_config has all 5 regimes: {sorted(found)}")
        else:
            r.fail(f"regime_config missing regimes: {missing}")

        # Check threshold keys in each regime
        for regime_name, regime_val in rc.items():
            if isinstance(regime_val, dict):
                thresholds = regime_val.get('thresholds', {})
                for k in ('high', 'med', 'low'):
                    if k not in thresholds:
                        r.fail(f"regime_config[{regime_name}].thresholds missing '{k}'")
    else:
        r.fail("regime_config not found or not a dict")

    # 1.4 Key imports
    import_checks = [
        ("agents.mechanical_decide", "mechanical_decide"),
        ("agents.report_formatter", "ReportFormatterMixin"),
        ("agents.multi_agent_analyzer", "MultiAgentAnalyzer"),
    ]
    for module_path, attr_name in import_checks:
        try:
            mod = __import__(module_path, fromlist=[attr_name])
            getattr(mod, attr_name)
            r.ok(f"import {module_path}.{attr_name}")
        except Exception as e:
            r.fail(f"import {module_path}.{attr_name} failed: {e}")

    # 1.5 AI files should NOT exist
    ai_files = [
        "agents/ai_quality_auditor.py",
        "agents/memory_manager.py",
        "agents/llm_client.py",
        "agents/dspy_modules.py",
        "agents/output_schemas.py",
        "agents/analysis_context.py",
    ]
    for af in ai_files:
        path = os.path.join(PROJECT_ROOT, af)
        if os.path.exists(path):
            r.warn(f"AI file still exists: {af} (should be deleted)")
        else:
            r.ok(f"AI file removed: {af}")


# ============================================================================
# Phase 2: Data Acquisition (requires API)
# ============================================================================
def phase2_data(r: DiagResult, skip_api: bool = False):
    print("\n" + "="*60)
    print("  Phase 2: Data Acquisition (13 data classes)")
    print("="*60)

    if skip_api:
        r.warn("Phase 2 skipped (--quick mode, no API calls)")
        return {}

    try:
        from utils.ai_data_assembler import AIDataAssembler
        assembler = AIDataAssembler(
            config_manager=None,
            logger=logging.getLogger("diag"),
        )
    except Exception as e:
        r.fail(f"AIDataAssembler init failed: {e}")
        return {}

    # Fetch external data
    try:
        t0 = time.time()
        external = assembler.fetch_external_data()
        elapsed = time.time() - t0
        r.ok(f"fetch_external_data() completed in {elapsed:.1f}s")
    except Exception as e:
        r.fail(f"fetch_external_data() failed: {e}")
        return {}

    # Check each data class
    data_classes = {
        'sentiment_report': 'Sentiment (L/S ratio)',
        'order_flow_report': 'Order Flow 30M (CVD/Taker)',
        'order_flow_report_4h': 'Order Flow 4H',
        'derivatives_report': 'Derivatives (Coinalyze OI/Liq)',
        'binance_derivatives_report': 'Binance Derivatives (Top Traders)',
        'orderbook_report': 'Orderbook (OBI/Spread)',
        'fear_greed_report': 'Fear & Greed Index',
    }
    for key, label in data_classes.items():
        val = external.get(key)
        if val and isinstance(val, dict) and len(val) > 0:
            r.ok(f"{label}: {len(val)} fields")
        else:
            r.warn(f"{label}: empty or missing (degradation expected)")

    return external


# ============================================================================
# Phase 3: Feature Extraction
# ============================================================================
def phase3_features(r: DiagResult, external_data: Dict):
    print("\n" + "="*60)
    print("  Phase 3: Feature Extraction (extract_features)")
    print("="*60)

    try:
        from agents.multi_agent_analyzer import MultiAgentAnalyzer
        analyzer = MultiAgentAnalyzer()
    except Exception as e:
        r.fail(f"MultiAgentAnalyzer init failed: {e}")
        return {}

    # Build minimal technical_report
    try:
        features = analyzer.extract_features(
            technical_data=external_data.get('technical_data', {}),
            sentiment_data=external_data.get('sentiment_report'),
            order_flow_data=external_data.get('order_flow_report'),
            order_flow_4h=external_data.get('order_flow_report_4h'),
            derivatives_data=external_data.get('derivatives_report'),
            binance_derivatives=external_data.get('binance_derivatives_report'),
            orderbook_data=external_data.get('orderbook_report'),
            fear_greed_report=external_data.get('fear_greed_report'),
        )
        r.ok(f"extract_features() returned {len(features)} features")
    except Exception as e:
        r.fail(f"extract_features() failed: {e}")
        return {}

    # Check critical features
    critical_features = [
        'extension_ratio_1d', 'extension_regime_1d',
        'extension_ratio_4h', 'extension_regime_4h',
        'adx_1d', 'adx_direction_1d',
        'rsi_divergence_4h', 'macd_divergence_4h',
        'volatility_regime_1d', 'price',
    ]
    for feat in critical_features:
        val = features.get(feat)
        if val is not None:
            r.ok(f"feature[{feat}] = {val}")
        else:
            r.warn(f"feature[{feat}] = None (missing)")

    # Check _avail_* flags
    avail_flags = [k for k in features if k.startswith('_avail_')]
    avail_true = sum(1 for k in avail_flags if features[k])
    r.ok(f"Availability flags: {avail_true}/{len(avail_flags)} sources available")

    return features


# ============================================================================
# Phase 4: Anticipatory Scoring
# ============================================================================
def phase4_scoring(r: DiagResult, features: Dict):
    print("\n" + "="*60)
    print("  Phase 4: Anticipatory Scoring (3 dimensions)")
    print("="*60)

    if not features:
        r.fail("Skipped — no features available")
        return {}

    try:
        from utils.config_manager import ConfigManager
        cm = ConfigManager()
        cm.load()
        regime_config = cm.get('anticipatory', 'regime_config') or {}
    except Exception:
        regime_config = {}

    try:
        from agents.report_formatter import ReportFormatterMixin
        scores = ReportFormatterMixin.compute_anticipatory_scores(features, regime_config)
        r.ok(f"compute_anticipatory_scores() completed")
    except Exception as e:
        r.fail(f"compute_anticipatory_scores() failed: {e}")
        return {}

    # Validate output structure
    required_keys = ['structure', 'divergence', 'order_flow', 'anticipatory_raw',
                     'regime', 'trend_context', 'risk_env']
    for key in required_keys:
        if key in scores:
            r.ok(f"scores[{key}] present")
        else:
            r.fail(f"scores[{key}] MISSING")

    # Show dimension details
    for dim in ('structure', 'divergence', 'order_flow'):
        d = scores.get(dim, {})
        direction = d.get('direction', '?')
        score = d.get('score', '?')
        raw = d.get('raw', '?')
        print(f"    {dim}: direction={direction} score={score} raw={raw}")

    net = scores.get('anticipatory_raw', 0)
    regime = scores.get('regime', '?')
    trend_ctx = scores.get('trend_context', '?')
    print(f"    net_raw={net:.4f} regime={regime} trend_ctx={trend_ctx}")

    # Sanity: net_raw should be in [-1, 1]
    if -1.0 <= net <= 1.0:
        r.ok(f"net_raw={net:.4f} in valid range [-1, 1]")
    else:
        r.fail(f"net_raw={net:.4f} OUT OF RANGE [-1, 1]")

    # Check regime is valid
    valid_regimes = {'TRENDING', 'RANGING', 'MEAN_REVERSION', 'VOLATILE', 'DEFAULT'}
    if regime in valid_regimes:
        r.ok(f"regime='{regime}' is valid")
    else:
        r.fail(f"regime='{regime}' not in {valid_regimes}")

    # Score extremes warning
    for dim in ('structure', 'divergence', 'order_flow'):
        s = scores.get(dim, {}).get('score', 0)
        if s == 10:
            vote_count = len([v for v in features.keys()
                              if dim[:3] in v.lower() or v.startswith(f'{dim}_')])
            r.warn(f"{dim} score=10 (max) — verify sufficient signal confluence, not single-signal saturation")

    return scores


# ============================================================================
# Phase 5: Mechanical Decision
# ============================================================================
def phase5_decision(r: DiagResult, scores: Dict, features: Dict):
    print("\n" + "="*60)
    print("  Phase 5: Mechanical Decision (mechanical_decide)")
    print("="*60)

    if not scores:
        r.fail("Skipped — no scores available")
        return {}

    try:
        from utils.config_manager import ConfigManager
        cm = ConfigManager()
        cm.load()
        regime_config = cm.get('anticipatory', 'regime_config') or {}
    except Exception:
        regime_config = {}

    try:
        from agents.mechanical_decide import mechanical_decide
        signal, confidence, size_pct, risk_appetite, hold_source = mechanical_decide(
            scores, features, regime_config,
        )
        r.ok(f"mechanical_decide() → signal={signal} confidence={confidence}")
    except Exception as e:
        r.fail(f"mechanical_decide() failed: {e}")
        return {}

    print(f"    signal={signal} confidence={confidence} size_pct={size_pct}%")
    print(f"    risk_appetite={risk_appetite} hold_source='{hold_source}'")

    # Validate signal
    valid_signals = {'LONG', 'SHORT', 'HOLD', 'CLOSE'}
    if signal in valid_signals:
        r.ok(f"signal='{signal}' is valid")
    else:
        r.fail(f"signal='{signal}' not in {valid_signals}")

    # Validate confidence
    valid_conf = {'HIGH', 'MEDIUM', 'LOW'}
    if confidence in valid_conf:
        r.ok(f"confidence='{confidence}' is valid")
    else:
        r.fail(f"confidence='{confidence}' not in {valid_conf}")

    # Validate size_pct
    if 0 < size_pct <= 100:
        r.ok(f"size_pct={size_pct}% in valid range (1-100)")
    elif size_pct == 0 and signal == 'HOLD':
        r.ok(f"size_pct=0% for HOLD signal (expected)")
    else:
        r.fail(f"size_pct={size_pct}% invalid")

    return {
        'signal': signal, 'confidence': confidence,
        'size_pct': size_pct, 'risk_appetite': risk_appetite,
        'hold_source': hold_source,
    }


# ============================================================================
# Phase 6: SL/TP Calculation
# ============================================================================
def phase6_sltp(r: DiagResult, decision: Dict, features: Dict):
    print("\n" + "="*60)
    print("  Phase 6: SL/TP Calculation (calculate_mechanical_sltp)")
    print("="*60)

    signal = decision.get('signal', 'HOLD')
    if signal not in ('LONG', 'SHORT'):
        r.ok(f"signal={signal} — SL/TP not applicable (no entry)")
        return

    price = features.get('price', 0)
    atr_4h = features.get('atr_4h', 0)
    atr_30m = features.get('atr_30m', features.get('atr_value', 0))
    confidence = decision.get('confidence', 'MEDIUM')

    if price <= 0:
        r.fail(f"price={price} invalid for SL/TP calculation")
        return

    try:
        from strategy.trading_logic import TradingLogicMixin
        # Use the static calculation directly
        from utils.backtest_math import calculate_mechanical_sltp
        side = 'BUY' if signal == 'LONG' else 'SELL'
        success, sl, tp, rr, desc = calculate_mechanical_sltp(
            entry_price=price, side=side,
            atr_value=atr_30m, confidence=confidence,
            atr_4h=atr_4h,
        )
        if success:
            r.ok(f"SL/TP calculated: SL=${sl:,.2f} TP=${tp:,.2f} R/R={rr:.2f}")
            print(f"    entry=${price:,.2f} atr_4h={atr_4h:.2f} atr_30m={atr_30m:.2f}")
            print(f"    {desc}")
            if rr < 1.3:
                r.fail(f"R/R={rr:.2f} below minimum 1.3")
            elif rr < 1.5:
                r.warn(f"R/R={rr:.2f} below target 1.5")
        else:
            r.fail(f"SL/TP calculation failed: {desc}")
    except Exception as e:
        r.fail(f"SL/TP calculation error: {e}")


# ============================================================================
# Phase 7: Gate Check Simulation
# ============================================================================
def phase7_gates(r: DiagResult, decision: Dict):
    print("\n" + "="*60)
    print("  Phase 7: Gate Check Compatibility")
    print("="*60)

    signal = decision.get('signal', 'HOLD')
    confidence = decision.get('confidence', 'LOW')

    # Gate 9: FR exhaustion bypass (mechanical)
    try:
        import ast
        oe_path = os.path.join(PROJECT_ROOT, "strategy", "order_execution.py")
        with open(oe_path, 'r') as f:
            content = f.read()
        # Check Gate 9 has mechanical bypass
        if "strategy_mode" in content and "mechanical" in content and "fr_exhaustion" in content.lower():
            r.ok("Gate 9 (FR exhaustion): mechanical bypass present")
        elif "_strategy_mode" in content and "'mechanical'" in content:
            r.ok("Gate 9 (FR exhaustion): mechanical mode check present")
        else:
            r.warn("Gate 9 (FR exhaustion): mechanical bypass not clearly detected")
    except Exception as e:
        r.warn(f"Gate 9 check error: {e}")

    # Gate FR hard block (L1118): check mechanical bypass
    try:
        with open(oe_path, 'r') as f:
            content = f.read()
        if "_is_mechanical" in content and "FR gate bypassed" in content:
            r.ok("FR hard gate: mechanical bypass implemented")
        else:
            r.fail("FR hard gate: mechanical bypass NOT found — entries will be blocked!")
    except Exception as e:
        r.warn(f"FR hard gate check error: {e}")

    # Gate 10: min_confidence
    try:
        from utils.config_manager import ConfigManager
        cm = ConfigManager()
        cm.load()
        min_conf = cm.get('min_confidence_to_trade') or 'LOW'
        conf_rank = {'LOW': 0, 'MEDIUM': 1, 'HIGH': 2}
        if conf_rank.get(confidence, 0) >= conf_rank.get(min_conf, 0):
            r.ok(f"Gate 10: confidence={confidence} >= min={min_conf}")
        elif signal == 'HOLD':
            r.ok(f"Gate 10: signal=HOLD, confidence check N/A")
        else:
            r.warn(f"Gate 10: confidence={confidence} < min={min_conf} — would be blocked")
    except Exception as e:
        r.warn(f"Gate 10 check error: {e}")

    # Direction lock state
    try:
        from agents.mechanical_decide import load_direction_lock_state, _direction_sl_count
        load_direction_lock_state()  # Populates global _direction_sl_count
        for direction, count in _direction_sl_count.items():
            if count >= 2:
                r.warn(f"Direction lock: {direction} has {count} consecutive SL — next signal will be HOLD")
            else:
                r.ok(f"Direction lock: {direction} SL count = {count} (< 2, not locked)")
    except Exception as e:
        r.warn(f"Direction lock check error: {e}")


# ============================================================================
# Phase 8: Calibration Data Health
# ============================================================================
def phase8_calibration(r: DiagResult):
    print("\n" + "="*60)
    print("  Phase 8: Calibration & Snapshot Health")
    print("="*60)

    # signal_decay.json
    decay_path = os.path.join(PROJECT_ROOT, "data", "calibration", "signal_decay.json")
    if os.path.exists(decay_path):
        try:
            with open(decay_path) as f:
                decay = json.load(f)
            r.ok(f"signal_decay.json: {len(decay)} signals calibrated")
        except Exception as e:
            r.fail(f"signal_decay.json corrupted: {e}")
    else:
        r.warn("signal_decay.json not found — using defaults")

    # optuna_study.json
    optuna_path = os.path.join(PROJECT_ROOT, "data", "calibration", "optuna_study.json")
    if os.path.exists(optuna_path):
        try:
            with open(optuna_path) as f:
                study = json.load(f)
            sharpe = study.get('best_sharpe', '?')
            r.ok(f"optuna_study.json: best_sharpe={sharpe}")
        except Exception as e:
            r.fail(f"optuna_study.json corrupted: {e}")
    else:
        r.warn("optuna_study.json not found")

    # Feature snapshots
    snap_dir = os.path.join(PROJECT_ROOT, "data", "feature_snapshots")
    if os.path.exists(snap_dir):
        snaps = [f for f in os.listdir(snap_dir) if f.endswith('.json')]
        r.ok(f"Feature snapshots: {len(snaps)} files")
        if snaps:
            # Check most recent snapshot
            latest = sorted(snaps)[-1]
            latest_path = os.path.join(snap_dir, latest)
            try:
                with open(latest_path) as f:
                    snap = json.load(f)
                has_features = 'features' in snap
                has_scores = 'scores' in snap
                if has_features and has_scores:
                    r.ok(f"Latest snapshot ({latest}): valid schema (features+scores)")
                else:
                    r.fail(f"Latest snapshot ({latest}): missing keys (features={has_features}, scores={has_scores})")
            except Exception as e:
                r.fail(f"Latest snapshot corrupted: {e}")

            # Check snapshot freshness (should accumulate every 20min)
            try:
                ts = latest.replace('snapshot_', '').replace('.json', '')
                snap_time = datetime.strptime(ts, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
                age_hours = (datetime.now(timezone.utc) - snap_time).total_seconds() / 3600
                if age_hours < 1:
                    r.ok(f"Latest snapshot age: {age_hours:.1f}h (fresh)")
                elif age_hours < 24:
                    r.ok(f"Latest snapshot age: {age_hours:.1f}h")
                else:
                    r.warn(f"Latest snapshot age: {age_hours:.1f}h (>24h, check if service is writing)")
            except Exception:
                pass
    else:
        r.fail("Feature snapshots directory not found")

    # Trading memory
    mem_path = os.path.join(PROJECT_ROOT, "data", "trading_memory.json")
    if os.path.exists(mem_path):
        try:
            with open(mem_path) as f:
                mem = json.load(f)
            r.ok(f"trading_memory.json: {len(mem)} records")
            # Check for AI-era contamination
            ai_records = sum(1 for m in mem if m.get('source') != 'mechanical')
            if ai_records > 0:
                r.warn(f"trading_memory.json: {ai_records}/{len(mem)} non-mechanical records (possible AI contamination)")
            else:
                r.ok("trading_memory.json: all records are mechanical (clean)")
        except Exception as e:
            r.fail(f"trading_memory.json corrupted: {e}")
    else:
        r.ok("trading_memory.json: not yet created (expected for fresh start)")


# ============================================================================
# Phase 9: Known Bug Fix Verification
# ============================================================================
def phase9_fix_checks(r: DiagResult):
    print("\n" + "="*60)
    print("  Phase 9: Known Bug Fix Verification")
    print("="*60)

    oe_path = os.path.join(PROJECT_ROOT, "strategy", "order_execution.py")
    ai_path = os.path.join(PROJECT_ROOT, "strategy", "ai_strategy.py")

    # Fix 1: FR hard gate mechanical bypass
    try:
        with open(oe_path, 'r') as f:
            content = f.read()
        if "_is_mechanical" in content and "FR gate bypassed" in content:
            r.ok("Fix verified: FR hard gate has mechanical bypass")
        else:
            r.fail("Fix NOT applied: FR hard gate still blocks mechanical mode")
    except Exception as e:
        r.fail(f"FR gate check error: {e}")

    # Fix 2: Confluence display uses correct keys per mode
    try:
        with open(ai_path, 'r') as f:
            content = f.read()
        if "('structure', 'divergence', 'order_flow')" in content:
            r.ok("Fix verified: Confluence display uses mechanical dimension keys")
        else:
            r.fail("Fix NOT applied: Confluence display still uses AI 5-layer keys")
    except Exception as e:
        r.fail(f"Confluence check error: {e}")

    # Fix 3: Gate 9 FR exhaustion mechanical bypass
    try:
        with open(oe_path, 'r') as f:
            content = f.read()
        if "_strategy_mode" in content and "'mechanical'" in content:
            r.ok("Fix verified: Gate 9 (FR exhaustion) has mechanical bypass")
        else:
            r.warn("Gate 9 mechanical bypass not clearly detected")
    except Exception as e:
        r.warn(f"Gate 9 check error: {e}")

    # Fix 4: mechanical_analyze() saves snapshots
    try:
        ma_path = os.path.join(PROJECT_ROOT, "agents", "multi_agent_analyzer.py")
        with open(ma_path, 'r') as f:
            content = f.read()
        if "feature_snapshots" in content and "snap_path" in content:
            r.ok("Fix verified: mechanical_analyze() saves feature snapshots")
        else:
            r.fail("Fix NOT applied: mechanical_analyze() does not save snapshots")
    except Exception as e:
        r.fail(f"Snapshot save check error: {e}")

    # Fix 5: record_stoploss() wired in event_handlers
    try:
        eh_path = os.path.join(PROJECT_ROOT, "strategy", "event_handlers.py")
        with open(eh_path, 'r') as f:
            content = f.read()
        if "record_stoploss" in content:
            r.ok("Fix verified: record_stoploss() wired in event_handlers")
        else:
            r.fail("record_stoploss() NOT wired — direction lock won't work")
    except Exception as e:
        r.fail(f"record_stoploss check error: {e}")

    # Fix 6: reset_direction_lock() wired in event_handlers
    try:
        with open(eh_path, 'r') as f:
            content = f.read()
        if "reset_direction_lock" in content:
            r.ok("Fix verified: reset_direction_lock() wired in event_handlers")
        else:
            r.fail("reset_direction_lock() NOT wired — lock will never reset")
    except Exception as e:
        r.fail(f"reset_direction_lock check error: {e}")

    # Fix 7: CONFIRMED_SELL handled in CVD scoring
    try:
        rf_path = os.path.join(PROJECT_ROOT, "agents", "report_formatter.py")
        with open(rf_path, 'r') as f:
            content = f.read()
        if "CONFIRMED_SELL" in content and "flow_votes" in content:
            r.ok("Fix verified: CONFIRMED_SELL handled in CVD flow scoring")
        else:
            r.fail("CONFIRMED_SELL NOT handled — bearish CVD signals lost")
    except Exception as e:
        r.fail(f"CONFIRMED_SELL check error: {e}")

    # Fix 8: sr_zones passed to mechanical_analyze
    try:
        with open(ai_path, 'r') as f:
            content = f.read()
        if "sr_zones=self.latest_sr_zones_data" in content:
            r.ok("Fix verified: sr_zones passed to mechanical_analyze()")
        else:
            r.fail("sr_zones NOT passed — S/R proximity scoring disabled")
    except Exception as e:
        r.fail(f"sr_zones check error: {e}")

    # Fix 9: _quality_score field compatible with event_handlers
    try:
        with open(eh_path, 'r') as f:
            content = f.read()
        if "_quality_score" in content:
            r.ok("Fix verified: _quality_score field accessible in event_handlers")
        else:
            r.fail("_quality_score NOT accessible — quality score always None")
    except Exception as e:
        r.fail(f"quality_score check error: {e}")

    # Fix 10: Confluence damping in scoring
    try:
        with open(rf_path, 'r') as f:
            content = f.read()
        if "_s_conf" in content and "_s_damp" in content:
            r.ok("Fix verified: Confluence damping applied to scoring")
        else:
            r.fail("Confluence damping NOT applied — single-signal saturation risk")
    except Exception as e:
        r.fail(f"Confluence damping check error: {e}")


# ============================================================================
# Phase 10: End-to-End Integration (full mechanical_analyze)
# ============================================================================
def phase10_e2e(r: DiagResult, skip_api: bool = False):
    print("\n" + "="*60)
    print("  Phase 10: End-to-End Integration (mechanical_analyze)")
    print("="*60)

    if skip_api:
        r.warn("Phase 10 skipped (--quick mode)")
        return

    try:
        from agents.multi_agent_analyzer import MultiAgentAnalyzer
        analyzer = MultiAgentAnalyzer()

        # Minimal call with empty data (should produce HOLD, not crash)
        t0 = time.time()
        result = analyzer.mechanical_analyze(
            technical_report={},
            sentiment_report=None,
        )
        elapsed = time.time() - t0
        r.ok(f"mechanical_analyze() completed in {elapsed:.3f}s")

        # Validate output structure
        required = ['signal', 'confidence', 'position_size_pct', 'risk_appetite',
                     'reason', 'timestamp', 'judge_decision', 'hold_source']
        for key in required:
            if key in result:
                r.ok(f"output[{key}] present")
            else:
                r.fail(f"output[{key}] MISSING")

        signal = result.get('signal', '?')
        confidence = result.get('confidence', '?')
        reason = result.get('reason', '')
        print(f"    Result: signal={signal} confidence={confidence}")
        print(f"    Reason: {reason[:120]}")

        # judge_decision structure
        jd = result.get('judge_decision', {})
        confluence = jd.get('confluence', {})
        mech_keys = {'structure', 'divergence', 'order_flow'}
        if mech_keys.issubset(set(confluence.keys())):
            r.ok("judge_decision.confluence has mechanical dimension keys")
        else:
            r.warn(f"judge_decision.confluence keys: {list(confluence.keys())} (expected {mech_keys})")

    except Exception as e:
        r.fail(f"mechanical_analyze() failed: {e}")


# ============================================================================
# Main
# ============================================================================
def main():
    parser = argparse.ArgumentParser(description="Mechanical Mode E2E Diagnostic")
    parser.add_argument('--quick', action='store_true', help='Skip API calls (offline checks only)')
    parser.add_argument('--fix-check', action='store_true', help='Only run fix verification (Phase 9)')
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')
    r = DiagResult()

    print("="*60)
    print("  Mechanical Mode End-to-End Diagnostic v1.0")
    print(f"  Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("="*60)

    if args.fix_check:
        phase9_fix_checks(r)
        print(r.summary())
        return 1 if r.failed > 0 else 0

    # Full diagnostic
    phase1_config(r)
    external_data = phase2_data(r, skip_api=args.quick)
    features = phase3_features(r, external_data)
    scores = phase4_scoring(r, features)
    decision = phase5_decision(r, scores, features)
    phase6_sltp(r, decision, features)
    phase7_gates(r, decision)
    phase8_calibration(r)
    phase9_fix_checks(r)
    phase10_e2e(r, skip_api=args.quick)

    print(r.summary())
    return 1 if r.failed > 0 else 0


if __name__ == '__main__':
    sys.exit(main())
