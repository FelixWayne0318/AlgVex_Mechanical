#!/usr/bin/env python3
"""
Logic Sync Checker — detect stale duplicated logic across the codebase.

When production code changes, diagnostic/verification scripts that copy-paste
the same logic can silently become stale.  This script maintains a registry of
known "logic clones" and verifies each clone still matches its source.

Two check strategies:
  1. IMPORT — verifies the clone actually imports from shared_logic.py (preferred)
  2. SIGNATURE — verifies a code fingerprint (hash of key lines) still matches

Usage:
    python3 scripts/check_logic_sync.py          # Run all checks
    python3 scripts/check_logic_sync.py --verbose # Show passing checks too
    python3 scripts/check_logic_sync.py --json    # Machine-readable output

Exit code: 0 if all synced, 1 if stale duplicates found.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).parent.parent.absolute()

# ─────────────────────────────────────────────────────────────
# Registry of known logic clones
#
# Each entry describes a piece of logic that MUST stay in sync.
# strategy = "import"  → file must import <symbol> from <source_module>
# strategy = "signature" → file must contain lines whose hash matches
# ─────────────────────────────────────────────────────────────

SYNC_REGISTRY: List[Dict] = [
    # ── CVD Trend (shared_logic.py SSoT) ──
    {
        "id": "CVD_TREND_ORDER_FLOW",
        "description": "OrderFlowProcessor delegates CVD trend to shared_logic",
        "strategy": "import",
        "file": "utils/order_flow_processor.py",
        "symbol": "calculate_cvd_trend",
        "source_module": "utils.shared_logic",
    },
    {
        "id": "CVD_TREND_VERIFY",
        "description": "verify_indicators.py delegates CVD trend to shared_logic",
        "strategy": "import",
        "file": "scripts/verify_indicators.py",
        "symbol": "calculate_cvd_trend",
        "source_module": "utils.shared_logic",
    },
    {
        "id": "CVD_TREND_VALIDATE",
        "description": "validate_data_pipeline.py imports CVD from shared_logic",
        "strategy": "import",
        "file": "scripts/validate_data_pipeline.py",
        "symbol": "calculate_cvd_trend",
        "source_module": "utils.shared_logic",
    },
    # ── Extension Regime (shared_logic.py SSoT) ──
    {
        "id": "EXT_REGIME_TECH_MGR",
        "description": "technical_manager.py delegates regime to shared_logic",
        "strategy": "import",
        "file": "indicators/technical_manager.py",
        "symbol": "classify_extension_regime",
        "source_module": "utils.shared_logic",
    },
    {
        "id": "EXT_REGIME_VERIFY",
        "description": "verify_extension_ratio.py imports regime from shared_logic",
        "strategy": "import",
        "file": "scripts/verify_extension_ratio.py",
        "symbol": "classify_extension_regime",
        "source_module": "utils.shared_logic",
    },
    # ── v20.0: Volatility Regime SSoT ──
    {
        "id": "VOL_REGIME_TECH_MGR",
        "description": "technical_manager.py delegates volatility regime to shared_logic",
        "strategy": "import",
        "file": "indicators/technical_manager.py",
        "symbol": "classify_volatility_regime",
        "source_module": "utils.shared_logic",
    },
    # ── Anti-patterns: files that must NOT contain inline reimplementations ──
    {
        "id": "NO_INLINE_CVD_VERIFY",
        "description": "verify_indicators.py must not reimplement CVD threshold inline",
        "strategy": "absent",
        "file": "scripts/verify_indicators.py",
        "forbidden_pattern": r"avg_older\s*\*\s*[01]\.\d",
    },
    {
        "id": "NO_INLINE_CVD_VALIDATE",
        "description": "validate_data_pipeline.py must not have old CVD multiplier",
        "strategy": "absent",
        "file": "scripts/validate_data_pipeline.py",
        "forbidden_pattern": r"avg_older\s*\*\s*[01]\.\d",
    },
    {
        "id": "NO_INLINE_REGIME_VERIFY",
        "description": "verify_extension_ratio.py should not hardcode 5.0/3.0/2.0 thresholds",
        "strategy": "absent",
        "file": "scripts/verify_extension_ratio.py",
        "forbidden_pattern": r">=\s*5\.0.*EXTREME|>=\s*3\.0.*OVEREXTENDED",
    },
    # ── Layer order counter must reset at every .clear() site ──
    {
        "id": "LAYER_COUNTER_RESET",
        "description": "Every _layer_orders.clear() must be followed by _next_layer_idx = 0",
        "strategy": "paired",
        "files": [
            "strategy/ai_strategy.py",
            "strategy/event_handlers.py",
            "strategy/safety_manager.py",
            "strategy/telegram_commands.py",
        ],
        "trigger_pattern": r"_layer_orders\.clear\(\)",
        "required_nearby": r"_next_layer_idx\s*=\s*0",
        "window": 3,  # lines to look ahead
    },
    # ── Mechanical SL/TP defaults must match between backtest and production ──
    {
        "id": "MECH_SLTP_SL_MULT",
        "description": "backtest_math.py sl_atr_multiplier must match trading_logic.py defaults",
        "strategy": "signature",
        "file": "utils/backtest_math.py",
        "reference_file": "strategy/trading_logic.py",
        "signature_pattern": r"['\"]sl_atr_multiplier['\"]\s*:\s*\{[^}]+\}",
        "reference_pattern": r"['\"]sl_atr_multiplier['\"]\s*:\s*\{[^}]+\}",
    },
    {
        "id": "MECH_SLTP_RR_TARGET",
        "description": "backtest_math.py tp_rr_target must match trading_logic.py defaults",
        "strategy": "signature",
        "file": "utils/backtest_math.py",
        "reference_file": "strategy/trading_logic.py",
        "signature_pattern": r"['\"]tp_rr_target['\"]\s*:\s*\{[^}]+\}",
        "reference_pattern": r"['\"]tp_rr_target['\"]\s*:\s*\{[^}]+\}",
    },
    {
        "id": "MECH_SLTP_CT_MULT",
        "description": "backtest_math.py counter_trend + min_rr must match trading_logic.py defaults",
        "strategy": "value_match",
        "file": "utils/backtest_math.py",
        "reference_file": "strategy/trading_logic.py",
        "extractions": [
            {
                "key": "counter_trend_rr_multiplier",
                "file_pattern": r"['\"]counter_trend_rr_multiplier['\"]\s*:\s*([\d.]+)",
                "ref_pattern": r"['\"]counter_trend_rr_multiplier['\"]\s*,\s*default\s*=\s*([\d.]+)",
            },
            {
                "key": "min_rr_ratio",
                "file_pattern": r"['\"]min_rr_ratio['\"]\s*:\s*([\d.]+)",
                "ref_pattern": r"['\"]min_rr_ratio['\"]\s*,\s*default\s*=\s*([\d.]+)",
            },
        ],
    },
    # ── v10.0: ATR Wilder's algorithm parity ──
    {
        "id": "ATR_WILDER_PARITY",
        "description": "backtest_math.py ATR Wilder's must match technical_manager.py _calculate_atr()",
        "strategy": "signature",
        "file": "utils/backtest_math.py",
        "reference_file": "indicators/technical_manager.py",
        "signature_pattern": r"atr\s*\*\s*\(period\s*-\s*1\)\s*\+\s*tr",
        "reference_pattern": r"atr\s*\*\s*\(period\s*-\s*1\)\s*\+\s*tr",
    },
    # ── v2.0: Kelly config parity ──
    {
        "id": "KELLY_CONFIG_PARITY",
        "description": "KellySizer must be wired into strategy (ai_strategy.py imports kelly_sizer)",
        "strategy": "import",
        "file": "strategy/ai_strategy.py",
        "symbol": "KellySizer",
        "source_module": "utils.kelly_sizer",
    },
    # ── v2.0: HMM 4-state labels ──
    {
        "id": "HMM_STATE_NAMES",
        "description": "HMM 4-state labels (TRENDING_UP/DOWN, RANGING, HIGH_VOLATILITY) must match between regime_detector.py and base.yaml thresholds",
        "strategy": "absent",
        "file": "utils/regime_detector.py",
        "forbidden_pattern": r"(?:TRENDING_LEFT|TRENDING_RIGHT|LOW_VOLATILITY|CHOPPY)",
    },
    # ── v2.0: Prometheus metrics call-site parity ──
    {
        "id": "PROMETHEUS_METRICS",
        "description": "MetricsExporter must be imported from utils.metrics_exporter in strategy files",
        "strategy": "import",
        "file": "utils/metrics_exporter.py",
        "symbol": "MetricsExporter",
        "source_module": "utils.metrics_exporter",
    },
    # ── Funding rate 5-decimal precision ──
    {
        "id": "FR_PRECISION",
        "description": "Funding rate display must use :.5f (not :.4f or :.6f)",
        "strategy": "absent",
        "files": [
            "agents/multi_agent_analyzer.py",
            "agents/prompt_constants.py",
            "agents/report_formatter.py",
            "agents/mechanical_decide.py",
        ],
        # Only match funding_rate (not funding_cost which is a dollar amount)
        "forbidden_pattern": r"funding_rate.*:\.\s*[^5]f|funding_rate.*:\s*\.4f|funding_rate.*:\s*\.6f",
    },
]


def check_import(entry: Dict) -> Tuple[bool, str]:
    """Verify a file imports <symbol> from <source_module>."""
    filepath = PROJECT_ROOT / entry["file"]
    if not filepath.exists():
        return False, f"File not found: {entry['file']}"

    symbol = entry["symbol"]
    source = entry["source_module"]
    pattern = re.compile(rf"from\s+{re.escape(source)}\s+import\s+.*\b{re.escape(symbol)}\b")

    # Check line-by-line, skipping comments
    for line in filepath.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if pattern.search(line):
            return True, f"✓ imports {symbol} from {source}"
    return False, f"✗ does NOT import {symbol} from {source}"


def check_absent(entry: Dict) -> Tuple[bool, str]:
    """Verify file(s) do NOT contain a forbidden pattern.

    Supports both ``"file": "..."`` (single) and ``"files": [...]`` (multi).
    """
    file_list = entry.get("files") or [entry["file"]]
    pattern = entry["forbidden_pattern"]

    for rel in file_list:
        filepath = PROJECT_ROOT / rel
        if not filepath.exists():
            continue
        content = filepath.read_text(encoding="utf-8", errors="ignore")
        matches = list(re.finditer(pattern, content))
        if matches:
            lines = content[:matches[0].start()].count("\n") + 1
            return False, f"✗ forbidden pattern found in {rel} at line ~{lines}: {matches[0].group()}"
    return True, "✓ no forbidden pattern found"


def check_signature(entry: Dict) -> Tuple[bool, str]:
    """Verify two files have matching code signatures for a pattern."""
    file_path = PROJECT_ROOT / entry["file"]
    ref_path = PROJECT_ROOT / entry["reference_file"]

    if not file_path.exists():
        return False, f"File not found: {entry['file']}"
    if not ref_path.exists():
        return False, f"Reference not found: {entry['reference_file']}"

    def extract_sig(path, pattern):
        content = path.read_text(encoding="utf-8", errors="ignore")
        match = re.search(pattern, content)
        if not match:
            return None
        # Normalize whitespace and quotes for comparison
        sig = re.sub(r"\s+", " ", match.group()).strip()
        sig = sig.replace("'", '"')
        return sig

    sig_file = extract_sig(file_path, entry["signature_pattern"])
    sig_ref = extract_sig(ref_path, entry["reference_pattern"])

    if sig_file is None:
        return False, f"✗ pattern not found in {entry['file']}"
    if sig_ref is None:
        return False, f"✗ reference pattern not found in {entry['reference_file']}"

    if sig_file == sig_ref:
        return True, f"✓ signatures match"

    return False, f"✗ STALE — file has [{sig_file[:60]}...] vs reference [{sig_ref[:60]}...]"


def check_value_match(entry: Dict) -> Tuple[bool, str]:
    """Verify extracted numeric values match between two files.

    Each extraction uses a regex with a capture group (group 1) to extract
    a value from both files, then compares them as floats.
    """
    file_path = PROJECT_ROOT / entry["file"]
    ref_path = PROJECT_ROOT / entry["reference_file"]

    if not file_path.exists():
        return False, f"File not found: {entry['file']}"
    if not ref_path.exists():
        return False, f"Reference not found: {entry['reference_file']}"

    file_content = file_path.read_text(encoding="utf-8", errors="ignore")
    ref_content = ref_path.read_text(encoding="utf-8", errors="ignore")

    mismatches = []
    for ext in entry["extractions"]:
        key = ext["key"]
        m_file = re.search(ext["file_pattern"], file_content)
        m_ref = re.search(ext["ref_pattern"], ref_content)

        if not m_file:
            mismatches.append(f"{key}: not found in {entry['file']}")
            continue
        if not m_ref:
            mismatches.append(f"{key}: not found in {entry['reference_file']}")
            continue

        try:
            v_file = float(m_file.group(1))
            v_ref = float(m_ref.group(1))
        except (ValueError, IndexError) as e:
            mismatches.append(f"{key}: parse error ({e})")
            continue

        if abs(v_file - v_ref) > 1e-9:
            mismatches.append(f"{key}: {v_file} != {v_ref}")

    if mismatches:
        return False, f"✗ STALE — {'; '.join(mismatches)}"
    keys = [e["key"] for e in entry["extractions"]]
    return True, f"✓ values match ({', '.join(keys)})"


def check_paired(entry: Dict) -> Tuple[bool, str]:
    """Verify every occurrence of trigger_pattern has required_nearby within N lines.

    Supports both ``"file": "..."`` (single) and ``"files": [...]`` (multi).
    """
    file_list = entry.get("files") or [entry["file"]]
    trigger = re.compile(entry["trigger_pattern"])
    required = re.compile(entry["required_nearby"])
    window = entry.get("window", 3)

    missing = []
    for rel in file_list:
        filepath = PROJECT_ROOT / rel
        if not filepath.exists():
            continue
        lines = filepath.read_text(encoding="utf-8", errors="ignore").splitlines()
        for i, line in enumerate(lines):
            if trigger.search(line):
                # Look ahead within window
                found = False
                for j in range(i, min(i + window + 1, len(lines))):
                    if required.search(lines[j]):
                        found = True
                        break
                if not found:
                    missing.append(f"{rel}:{i + 1}")

    if not file_list or not any((PROJECT_ROOT / r).exists() for r in file_list):
        return False, f"File(s) not found: {file_list}"
    if missing:
        return False, f"✗ trigger at {missing} missing nearby {entry['required_nearby']}"
    return True, "✓ all triggers have required companion"


# ─────────────────────────────────────────────────────────────

CHECKERS = {
    "import": check_import,
    "absent": check_absent,
    "signature": check_signature,
    "value_match": check_value_match,
    "paired": check_paired,
}


def run_all_checks(verbose: bool = False) -> Tuple[int, int, List[Dict]]:
    """Run all sync checks. Returns (passed, failed, details)."""
    passed = 0
    failed = 0
    details = []

    for entry in SYNC_REGISTRY:
        strategy = entry["strategy"]
        checker = CHECKERS.get(strategy)
        if not checker:
            details.append({"id": entry["id"], "ok": False, "msg": f"Unknown strategy: {strategy}"})
            failed += 1
            continue

        ok, msg = checker(entry)
        details.append({"id": entry["id"], "ok": ok, "msg": msg, "description": entry.get("description", "")})

        if ok:
            passed += 1
        else:
            failed += 1

    return passed, failed, details


def main():
    parser = argparse.ArgumentParser(description="Logic Sync Checker")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show passing checks")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    passed, failed, details = run_all_checks(verbose=args.verbose)

    if args.json:
        print(json.dumps({"passed": passed, "failed": failed, "checks": details}, indent=2))
        sys.exit(1 if failed else 0)

    print("=" * 60)
    print("🔗 Logic Sync Checker")
    print("=" * 60)

    for d in details:
        if d["ok"] and not args.verbose:
            continue
        icon = "✅" if d["ok"] else "❌"
        print(f"  {icon} [{d['id']}] {d['description']}")
        print(f"       {d['msg']}")

    print(f"\n{'=' * 60}")
    print(f"📊 Results: {passed} synced, {failed} stale")

    if failed:
        print(f"❌ {failed} logic clone(s) out of sync!")
        print("   Run with --verbose to see all checks.")
    else:
        print(f"✅ All {passed} logic clones in sync")

    print("=" * 60)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
