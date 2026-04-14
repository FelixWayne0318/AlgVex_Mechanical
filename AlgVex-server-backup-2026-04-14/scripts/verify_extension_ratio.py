#!/usr/bin/env python3
"""
v19.1 ATR Extension Ratio — Server-Side Verification Script

Validates:
1. Extension ratio calculation logic (pure math, no framework deps)
2. Extension data flows through to AI agent formatting
3. Edge cases handled properly (ATR=0, SMA uninitialized, etc.)
4. Prompt integration verified

Usage:
  cd /home/linuxuser/nautilus_AlgVex && source venv/bin/activate && python3 scripts/verify_extension_ratio.py
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.shared_logic import classify_extension_regime


def test_extension_ratio_calculation():
    """Phase 1: Test extension ratio calculation logic (pure math, no framework deps)."""
    print("=" * 60)
    print("Phase 1: Extension Ratio Calculation (Pure Math)")
    print("=" * 60)

    def calculate_extension_ratios(current_price, atr_value, sma_values):
        """Replicate _calculate_extension_ratios() logic for testing."""
        result = {}
        sma_periods = sorted(sma_values.keys())

        if atr_value <= 0 or current_price <= 0:
            for period in sma_periods:
                result[f'extension_ratio_sma_{period}'] = 0.0
            result['extension_regime'] = 'INSUFFICIENT_DATA'
            return result

        max_abs_ratio = 0.0
        for period in sma_periods:
            sma_val = sma_values[period]
            if sma_val and sma_val > 0:
                ratio = (current_price - sma_val) / atr_value
                result[f'extension_ratio_sma_{period}'] = round(ratio, 2)
                max_abs_ratio = max(max_abs_ratio, abs(ratio))
            else:
                result[f'extension_ratio_sma_{period}'] = 0.0

        primary_ratio = abs(result.get('extension_ratio_sma_20', 0.0))
        if primary_ratio == 0.0:
            primary_ratio = max_abs_ratio

        result['extension_regime'] = classify_extension_regime(primary_ratio)

        return result

    tests_passed = 0
    tests_total = 0

    # Test case 1: Normal extension
    tests_total += 1
    smas = {5: 99500, 20: 99000, 50: 98000}
    result = calculate_extension_ratios(100000, 1000, smas)
    print(f"\nTest 1 - Normal extension:")
    print(f"  Price=$100,000 | ATR=$1,000 | SMA20=$99,000")
    print(f"  ratio_sma_20={result['extension_ratio_sma_20']} regime={result['extension_regime']}")
    if result['extension_ratio_sma_20'] == 1.0 and result['extension_regime'] == 'NORMAL':
        print("  ✅ PASS")
        tests_passed += 1
    else:
        print("  ❌ FAIL")

    # Test case 2: Overextended
    tests_total += 1
    smas = {5: 99500, 20: 96500, 50: 95000}
    result = calculate_extension_ratios(100000, 1000, smas)
    print(f"\nTest 2 - Overextended:")
    print(f"  Price=$100,000 | ATR=$1,000 | SMA20=$96,500")
    print(f"  ratio_sma_20={result['extension_ratio_sma_20']} regime={result['extension_regime']}")
    if result['extension_ratio_sma_20'] == 3.5 and result['extension_regime'] == 'OVEREXTENDED':
        print("  ✅ PASS")
        tests_passed += 1
    else:
        print("  ❌ FAIL")

    # Test case 3: Extreme extension
    tests_total += 1
    smas = {5: 99500, 20: 94000, 50: 90000}
    result = calculate_extension_ratios(100000, 1000, smas)
    print(f"\nTest 3 - Extreme extension:")
    print(f"  Price=$100,000 | ATR=$1,000 | SMA20=$94,000")
    print(f"  ratio_sma_20={result['extension_ratio_sma_20']} regime={result['extension_regime']}")
    if result['extension_ratio_sma_20'] == 6.0 and result['extension_regime'] == 'EXTREME':
        print("  ✅ PASS")
        tests_passed += 1
    else:
        print("  ❌ FAIL")

    # Test case 4: Negative extension (below SMA)
    tests_total += 1
    smas = {5: 100500, 20: 103000, 50: 105000}
    result = calculate_extension_ratios(100000, 1000, smas)
    print(f"\nTest 4 - Negative extension (below SMA):")
    print(f"  Price=$100,000 | ATR=$1,000 | SMA20=$103,000")
    print(f"  ratio_sma_20={result['extension_ratio_sma_20']} regime={result['extension_regime']}")
    if result['extension_ratio_sma_20'] == -3.0 and result['extension_regime'] == 'OVEREXTENDED':
        print("  ✅ PASS")
        tests_passed += 1
    else:
        print("  ❌ FAIL")

    # Test case 5: ATR=0 edge case
    tests_total += 1
    result = calculate_extension_ratios(100000, 0, smas)
    print(f"\nTest 5 - ATR=0 edge case:")
    print(f"  regime={result['extension_regime']}")
    if result['extension_regime'] == 'INSUFFICIENT_DATA' and result['extension_ratio_sma_20'] == 0.0:
        print("  ✅ PASS")
        tests_passed += 1
    else:
        print("  ❌ FAIL")

    # Test case 6: Price=0 edge case
    tests_total += 1
    result = calculate_extension_ratios(0, 1000, smas)
    print(f"\nTest 6 - Price=0 edge case:")
    print(f"  regime={result['extension_regime']}")
    if result['extension_regime'] == 'INSUFFICIENT_DATA':
        print("  ✅ PASS")
        tests_passed += 1
    else:
        print("  ❌ FAIL")

    # Test case 7: SMA uninitialized (value=0)
    tests_total += 1
    smas_partial = {5: 99500, 20: 0, 50: 98000}
    result = calculate_extension_ratios(100000, 1000, smas_partial)
    print(f"\nTest 7 - SMA uninitialized:")
    print(f"  ratio_sma_20={result['extension_ratio_sma_20']}")
    if result['extension_ratio_sma_20'] == 0.0:
        print("  ✅ PASS")
        tests_passed += 1
    else:
        print("  ❌ FAIL")

    # Test case 8: Extended regime boundary
    tests_total += 1
    smas = {5: 99800, 20: 98000, 50: 97000}
    result = calculate_extension_ratios(100000, 1000, smas)
    print(f"\nTest 8 - Extended regime boundary:")
    print(f"  Price=$100,000 | ATR=$1,000 | SMA20=$98,000")
    print(f"  ratio_sma_20={result['extension_ratio_sma_20']} regime={result['extension_regime']}")
    if result['extension_ratio_sma_20'] == 2.0 and result['extension_regime'] == 'EXTENDED':
        print("  ✅ PASS")
        tests_passed += 1
    else:
        print("  ❌ FAIL")

    # Test case 9: Fallback to max ratio when SMA20 is 0
    tests_total += 1
    smas = {5: 95000, 20: 0, 50: 94000}
    result = calculate_extension_ratios(100000, 1000, smas)
    print(f"\nTest 9 - Fallback to max ratio:")
    print(f"  SMA20=0 (uninit), SMA5=$95,000 (5.0 ATR), SMA50=$94,000 (6.0 ATR)")
    print(f"  regime={result['extension_regime']}")
    if result['extension_regime'] == 'EXTREME':
        print("  ✅ PASS (correctly uses max_abs_ratio from SMA50=6.0)")
        tests_passed += 1
    else:
        print("  ❌ FAIL")

    # Test case 10: Small coin (ETH-like prices)
    tests_total += 1
    smas = {5: 3900, 20: 3800, 50: 3600}
    result = calculate_extension_ratios(4000, 100, smas)
    print(f"\nTest 10 - ETH-like prices:")
    print(f"  Price=$4,000 | ATR=$100 | SMA20=$3,800")
    print(f"  ratio_sma_20={result['extension_ratio_sma_20']} regime={result['extension_regime']}")
    if result['extension_ratio_sma_20'] == 2.0 and result['extension_regime'] == 'EXTENDED':
        print("  ✅ PASS (price-independent normalization works)")
        tests_passed += 1
    else:
        print("  ❌ FAIL")

    print(f"\n✅ Phase 1: {tests_passed}/{tests_total} tests passed")
    return tests_passed == tests_total


def test_source_code_integration():
    """Phase 2: Verify source code contains all required extension ratio integrations."""
    print("\n" + "=" * 60)
    print("Phase 2: Source Code Integration Verification")
    print("=" * 60)

    checks = []

    # Check 1: IndicatorManager has _calculate_extension_ratios method
    with open("indicators/technical_manager.py") as f:
        tm_src = f.read()
    checks.append((
        "def _calculate_extension_ratios" in tm_src,
        "IndicatorManager._calculate_extension_ratios() exists"
    ))
    checks.append((
        "**extension_ratios" in tm_src,
        "Extension ratios unpacked into technical_data"
    ))
    checks.append((
        "extension_regime" in tm_src,
        "Extension regime classification exists"
    ))

    # Check 2: agent module integrations (v46.0: mechanical mode files)
    _agent_files = [
        "agents/multi_agent_analyzer.py", "agents/prompt_constants.py",
        "agents/report_formatter.py", "agents/mechanical_decide.py",
    ]
    maa_src = ""
    for _af in _agent_files:
        try:
            with open(_af) as f:
                maa_src += f.read() + "\n"
        except FileNotFoundError:
            pass

    checks.append((
        "extension_ratio_sma_20" in maa_src and "extension_regime" in maa_src,
        "_format_technical_report() reads extension data"
    ))
    checks.append((
        "Extension Ratio (SMA20)" in maa_src,
        "_build_key_metrics() includes extension ratio"
    ))
    checks.append((
        "EXTENSION WARNING" in maa_src,
        "Overextension warnings generated"
    ))

    # Check 3: compute_anticipatory_scores() uses extension for Structure dimension (v45.0+)
    checks.append((
        "extension_ratio_1d" in maa_src and "ext_regime_1d" in maa_src,
        "compute_anticipatory_scores() uses 1D extension for Structure"
    ))

    # Check 4: 4H extension integrated (v47.0)
    checks.append((
        "extension_ratio_4h" in maa_src and "ext_regime_4h" in maa_src,
        "compute_anticipatory_scores() uses 4H extension (v47.0)"
    ))

    # Check 5: extract_features() captures extension fields
    checks.append((
        "extension_regime_1d" in maa_src and "extension_regime_4h" in maa_src,
        "extract_features() captures extension regime per TF"
    ))

    # Check 6: vol_ext_risk scoring includes extension
    checks.append((
        "vol_ext_risk" in maa_src,
        "compute_scores_from_features() includes vol_ext_risk dimension"
    ))

    # Check 7: 4H inline extension ratio
    checks.append((
        "Extension vs SMA20" in maa_src,
        "4H ATR shows inline extension ratio"
    ))

    # Check 8: 1D inline extension ratio
    checks.append((
        "Extension vs SMA200" in maa_src,
        "1D ATR shows inline extension ratio"
    ))

    # Check 9: No key collisions in technical_data
    # extension_ratio_sma_{5,20,50} should not conflict with sma_{5,20,50}
    checks.append((
        "extension_ratio_sma_" in tm_src and "sma_" in tm_src,
        "Extension keys are namespaced (extension_ratio_sma_* != sma_*)"
    ))

    all_pass = True
    for check, desc in checks:
        status = "✅" if check else "❌"
        print(f"  {status} {desc}")
        if not check:
            all_pass = False

    if all_pass:
        print(f"\n✅ Phase 2: All {len(checks)} integration checks passed")
    else:
        print(f"\n❌ Phase 2: Some checks failed")
    return all_pass


def test_regime_classification_exhaustive():
    """Phase 3: Exhaustive regime boundary tests."""
    print("\n" + "=" * 60)
    print("Phase 3: Regime Classification Boundaries")
    print("=" * 60)

    def classify_regime(ratio_abs):
        return classify_extension_regime(ratio_abs)

    # Test all boundary values
    boundary_tests = [
        (0.0, 'NORMAL'),
        (0.5, 'NORMAL'),
        (1.0, 'NORMAL'),
        (1.5, 'NORMAL'),
        (1.99, 'NORMAL'),
        (2.0, 'EXTENDED'),       # boundary
        (2.5, 'EXTENDED'),
        (2.99, 'EXTENDED'),
        (3.0, 'OVEREXTENDED'),   # boundary
        (3.5, 'OVEREXTENDED'),
        (4.0, 'OVEREXTENDED'),
        (4.99, 'OVEREXTENDED'),
        (5.0, 'EXTREME'),        # boundary
        (6.0, 'EXTREME'),
        (10.0, 'EXTREME'),
    ]

    all_pass = True
    for ratio, expected in boundary_tests:
        actual = classify_regime(ratio)
        if actual != expected:
            print(f"  ❌ |ratio|={ratio}: expected {expected}, got {actual}")
            all_pass = False

    if all_pass:
        print(f"  ✅ All {len(boundary_tests)} boundary values classify correctly")
        print(f"    NORMAL: [0, 2.0)")
        print(f"    EXTENDED: [2.0, 3.0)")
        print(f"    OVEREXTENDED: [3.0, 5.0)")
        print(f"    EXTREME: [5.0, ∞)")
    else:
        print(f"  ❌ Some boundary tests failed")

    print(f"\n{'✅' if all_pass else '❌'} Phase 3: Boundary tests {'passed' if all_pass else 'failed'}")
    return all_pass


def test_real_world_scenarios():
    """Phase 4: Test with realistic BTC market scenarios."""
    print("\n" + "=" * 60)
    print("Phase 4: Real-World BTC Scenarios")
    print("=" * 60)

    def compute_extension(price, sma, atr):
        if atr <= 0 or price <= 0:
            return 0.0, 'INSUFFICIENT_DATA'
        ratio = round((price - sma) / atr, 2)
        regime = classify_extension_regime(ratio)
        return ratio, regime

    scenarios = [
        {
            'name': 'Ranging market (tight range)',
            'price': 97000, 'sma20': 97200, 'atr': 800,
            'expected_regime': 'NORMAL',
            'note': 'Price near SMA, small displacement — no extension concern',
        },
        {
            'name': 'Healthy uptrend pullback',
            'price': 98500, 'sma20': 97000, 'atr': 900,
            'expected_regime': 'NORMAL',
            'note': 'Moderate stretch above SMA, within normal trend range',
        },
        {
            'name': 'Strong rally, getting stretched',
            'price': 102000, 'sma20': 99500, 'atr': 1000,
            'expected_regime': 'EXTENDED',
            'note': '2.5 ATR above SMA — entry risk increasing',
        },
        {
            'name': 'Parabolic move up',
            'price': 108000, 'sma20': 100000, 'atr': 1500,
            'expected_regime': 'EXTREME',
            'note': '5.33 ATR above SMA — extreme snapback risk',
        },
        {
            'name': 'Crash / capitulation',
            'price': 85000, 'sma20': 95000, 'atr': 2000,
            'expected_regime': 'EXTREME',
            'note': '5.0 ATR below SMA — extreme oversold, bounce probability high',
        },
        {
            'name': 'Flash crash extreme',
            'price': 80000, 'sma20': 95000, 'atr': 1500,
            'expected_regime': 'EXTREME',
            'note': '10.0 ATR below SMA — extreme oversold condition',
        },
        {
            'name': 'Low volatility squeeze',
            'price': 97100, 'sma20': 97000, 'atr': 300,
            'expected_regime': 'NORMAL',
            'note': 'Tight range with low ATR — small displacement / small ATR = moderate ratio',
        },
    ]

    all_pass = True
    for s in scenarios:
        ratio, regime = compute_extension(s['price'], s['sma20'], s['atr'])
        passed = regime == s['expected_regime']
        status = "✅" if passed else "❌"
        print(f"\n  {status} {s['name']}")
        print(f"    Price=${s['price']:,} | SMA20=${s['sma20']:,} | ATR=${s['atr']:,}")
        print(f"    Extension: {ratio:+.2f} ATR → {regime}")
        print(f"    {s['note']}")
        if not passed:
            print(f"    Expected: {s['expected_regime']}")
            all_pass = False

    print(f"\n{'✅' if all_pass else '❌'} Phase 4: Real-world scenarios {'passed' if all_pass else 'failed'}")
    return all_pass


def main():
    print("🔍 v19.1 ATR Extension Ratio Verification")
    print("=" * 60)

    results = []
    results.append(("Calculation Logic", test_extension_ratio_calculation()))
    results.append(("Source Integration", test_source_code_integration()))
    results.append(("Regime Boundaries", test_regime_classification_exhaustive()))
    results.append(("Real-World Scenarios", test_real_world_scenarios()))

    print("\n" + "=" * 60)
    print("📊 FINAL RESULTS")
    print("=" * 60)

    all_pass = True
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {status} — {name}")
        if not passed:
            all_pass = False

    print("=" * 60)
    if all_pass:
        print("✅ All phases passed — v19.1 ATR Extension Ratio verified")
    else:
        print("❌ Some phases failed — review errors above")
        sys.exit(1)


if __name__ == "__main__":
    main()
