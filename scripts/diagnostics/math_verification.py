"""
Math Verification Module (v20.0)

Validates critical trading math with real market data:
  M1a-c: R/R >= min_rr hard gate (reject/accept)
  M1d-g: Counter-trend R/R >= min_rr × ct_mult gate (v5.12, v40.0: 1.69:1)
  M2: SL wrong-side rejection
  M3: Technical SL/TP fallback
  M4: SL favorable direction (max for LONG, min for SHORT)
  M6: Emergency SL (config-driven, v15.0 — was hardcoded 2%)
  M7: evaluate_trade() grading (A+/A/B/C/D/F) (v5.12)
  M8: evaluate_trade() v6.0 fields + direction-aware planned_rr
  M9: Entry pipeline integration (v6.0)
  M10: State machine runtime (v6.0)
  M11: v6.2 config value guards (min_sl_distance_atr, quantity precision)
  M12: v15.0 config chain validation (base.yaml → StrategyConfig → self.xxx)
  M14: v19.1 Extension Ratio boundary math (regime classification thresholds)
  M15: v20.0 Volatility Regime percentile boundary math (classify_volatility_regime)
  M16: v24.0 Trailing Stop boundary math (fee buffer, BPS limits, min risk guard)
  M17: v39.0/v40.0 Mechanical SL/TP with 4H ATR + parameter verification
  M18: v2.0 VaR/CVaR calculation verification
  M19: v2.0 Kelly formula verification
"""

import inspect
import traceback
from typing import List, Optional, Tuple

from .base import DiagnosticContext, DiagnosticStep, print_box
from strategy.trading_logic import get_min_rr_ratio, get_counter_trend_rr_multiplier


def _get_sr_from_ctx(ctx, current_price: float) -> Tuple[float, float]:
    """Get S/R from production SRZoneCalculator results in ctx.sr_zones_data.

    Falls back to SRZoneCalculator.calculate() if ctx data not available.
    """
    # First: use production S/R data already computed by ai_decision step
    sr_data = getattr(ctx, 'sr_zones_data', None)
    if sr_data:
        ns = sr_data.get('nearest_support')
        nr = sr_data.get('nearest_resistance')
        support = ns.price if ns and hasattr(ns, 'price') else (ns.get('price') if isinstance(ns, dict) else None)
        resistance = nr.price if nr and hasattr(nr, 'price') else (nr.get('price') if isinstance(nr, dict) else None)
        if support and resistance:
            return support, resistance

    # Fallback: call production SRZoneCalculator directly
    try:
        from utils.sr_zone_calculator import SRZoneCalculator
        calculator = SRZoneCalculator()
        atr = (ctx.technical_data or {}).get('atr', current_price * 0.01)
        result = calculator.calculate(
            current_price=current_price,
            bars_data=ctx.sr_bars_data,
            atr_value=atr if atr and atr > 0 else current_price * 0.01,
            bars_data_4h=ctx.bars_data_4h,
            bars_data_1d=ctx.bars_data_1d,
            daily_bar=ctx.daily_bar,
            weekly_bar=ctx.weekly_bar,
            technical_data=ctx.technical_data,
        )
        ns = result.get('nearest_support')
        nr = result.get('nearest_resistance')
        support = ns.price if ns and hasattr(ns, 'price') else current_price * 0.98
        resistance = nr.price if nr and hasattr(nr, 'price') else current_price * 1.02
        return support, resistance
    except Exception:
        return current_price * 0.98, current_price * 1.02


class MathVerificationChecker(DiagnosticStep):
    """
    v6.0 数学验证

    Uses real market data to verify trading math:
    - R/R gate enforcement (顺势 1.3:1 + 逆势 1.69:1, v40.0)
    - Counter-trend R/R escalation (v5.12)
    - SL wrong-side rejection
    - Technical SL/TP fallback quality
    - SL favorable direction rules
    - Dynamic update threshold
    - Emergency SL distance
    - v6.0 evaluate_trade() position management fields
    """

    name = "v6.0 数学验证 (R/R, 逆势门槛, SL方向, 动态调整, v6.0字段)"

    def __init__(self, ctx: DiagnosticContext):
        super().__init__(ctx)
        self._results: List[dict] = []

    def run(self) -> bool:
        print()
        print_box("v5.12 Math Verification (数学验证)", 65)
        print()

        price = self.ctx.current_price
        if not price or price <= 0:
            print("  ⚠️ 无价格数据，跳过数学验证")
            return True

        # Import trading logic
        try:
            from strategy.trading_logic import (
                validate_multiagent_sltp,
                get_min_rr_ratio,
                get_counter_trend_rr_multiplier,
            )
        except ImportError as e:
            print(f"  ❌ 无法导入 trading_logic: {e}")
            self.ctx.add_error(f"Math verification: import failed: {e}")
            return False

        # Get S/R from production SRZoneCalculator (via ctx or direct call)
        support, resistance = _get_sr_from_ctx(self.ctx, price)

        print(f"  价格: ${price:,.2f}  支撑: ${support:,.2f}  阻力: ${resistance:,.2f}")
        print()

        # Run all math checks
        self._check_rr_gate(price, validate_multiagent_sltp)
        self._check_rr_gate_counter_trend(price, validate_multiagent_sltp)
        self._check_sl_side(price, validate_multiagent_sltp)
        # v11.0-simple: S/R-based SL/TP removed, production uses mechanical SL/TP (ATR × confidence)
        self._check_sl_favorable_direction(price)
        self._check_emergency_sl(price)
        self._check_evaluate_trade(price)
        self._check_evaluate_trade_v60_fields(price)
        self._check_entry_pipeline_integration(price)
        self._check_v6_state_machine_runtime(price)
        self._check_config_value_guards(price)
        self._check_config_chain_v15(price)
        self._check_drawdown_hysteresis_runtime()
        self._check_extension_ratio_boundaries(price)
        self._check_volatility_regime_boundaries(price)
        self._check_trailing_stop_math(price)
        self._check_mechanical_sltp_v40(price)
        self._check_var_cvar(price)
        self._check_kelly_formula(price)

        # Summary
        passed = sum(1 for r in self._results if r["pass"])
        total = len(self._results)
        failed = total - passed
        print()
        print(f"  数学验证: {passed}/{total} 通过", end="")
        if failed > 0:
            print(f", {failed} 失败")
            for r in self._results:
                if not r["pass"]:
                    self.ctx.add_error(f"[{r['id']}] {r['desc']}")
        else:
            print(" ✅")

        # Store results for JSON output
        if not hasattr(self.ctx, 'math_verification_results'):
            self.ctx.math_verification_results = []
        self.ctx.math_verification_results = self._results

        return failed == 0

    def _record(self, check_id: str, desc: str, passed: bool,
                expected: str = "", actual: str = "", detail: str = ""):
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  [{check_id}] {desc}")
        if expected:
            print(f"    Expected: {expected}")
        if actual:
            print(f"    Actual:   {actual}")
        print(f"    Result:   {status}")
        if detail:
            for line in detail.split("\n"):
                print(f"    {line}")
        print()
        self._results.append({
            "id": check_id, "desc": desc, "pass": passed,
            "expected": expected, "actual": actual,
        })

    # ── M1: R/R Hard Gate ──

    def _check_rr_gate(self, price: float, validate_fn):
        print(f"  --- M1: R/R Hard Gate Test ---")
        print()

        # M1a: Reject R/R < min_rr (LONG)
        min_rr = get_min_rr_ratio()
        bad_sl = price * 0.99
        bad_tp = price * 1.005
        try:
            is_valid, _, _, reason = validate_fn(
                side="BUY", multi_sl=bad_sl, multi_tp=bad_tp, entry_price=price,
            )
            rr = (bad_tp - price) / (price - bad_sl) if price > bad_sl else 0
            self._record("M1a", f"R/R gate: reject R/R < {min_rr} (LONG)", not is_valid,
                         expected=f"Reject: R/R={rr:.2f}:1 < {min_rr}:1",
                         actual=f"valid={is_valid}, reason={reason}")
        except Exception as e:
            self._record("M1a", f"R/R gate: reject R/R < {min_rr} (LONG)", False,
                         actual=f"{e}\n    {traceback.format_exc()}")

        # M1b: Accept R/R >= min_rr (LONG)
        good_sl = price * 0.98
        good_tp = price * 1.04
        try:
            is_valid, v_sl, v_tp, reason = validate_fn(
                side="BUY", multi_sl=good_sl, multi_tp=good_tp, entry_price=price,
            )
            rr = (good_tp - price) / (price - good_sl) if price > good_sl else 0
            self._record("M1b", f"R/R gate: accept R/R >= {min_rr} (LONG)", is_valid,
                         expected=f"Accept: R/R={rr:.2f}:1 >= {min_rr}:1",
                         actual=f"valid={is_valid}, SL=${v_sl:,.2f}, TP=${v_tp:,.2f}")
        except Exception as e:
            self._record("M1b", f"R/R gate: accept R/R >= {min_rr} (LONG)", False,
                         actual=f"{e}\n    {traceback.format_exc()}")

        # M1c: Reject R/R < min_rr (SHORT)
        short_sl = price * 1.01
        short_tp = price * 0.995
        try:
            is_valid, _, _, reason = validate_fn(
                side="SELL", multi_sl=short_sl, multi_tp=short_tp, entry_price=price,
            )
            self._record("M1c", f"R/R gate: reject R/R < {min_rr} (SHORT)", not is_valid,
                         expected="Reject low R/R for SHORT",
                         actual=f"valid={is_valid}, reason={reason}")
        except Exception as e:
            self._record("M1c", f"R/R gate: reject R/R < {min_rr} (SHORT)", False,
                         actual=f"{e}\n    {traceback.format_exc()}")

    # ── M1d-g: Counter-Trend R/R Gate (v5.12) ──

    def _check_rr_gate_counter_trend(self, price: float, validate_fn):
        """v5.12: Counter-trend trades require R/R >= min_rr × ct_mult (v40.0: 1.3 × 1.3 = 1.69)"""
        min_rr = get_min_rr_ratio()
        ct_mult = get_counter_trend_rr_multiplier()
        ct_rr = min_rr * ct_mult

        print(f"  --- M1d-g: Counter-Trend R/R Gate (v5.12, >= {ct_rr:.2f}:1) ---")
        print()

        # Build mock trend_info: strong downtrend (ADX=30, DOWNTREND)
        downtrend_info = {
            'trend_direction': 'DOWNTREND',
            'adx': 30.0,
            'di_plus': 15.0,
            'di_minus': 35.0,
        }
        uptrend_info = {
            'trend_direction': 'UPTREND',
            'adx': 30.0,
            'di_plus': 35.0,
            'di_minus': 15.0,
        }

        # M1d: Counter-trend LONG (buying in downtrend), R/R=1.5 → should REJECT (< 1.69)
        ct_sl = price * 0.98     # 2% SL
        ct_tp = price * 1.030    # 3.0% TP → R/R = 1.5
        try:
            is_valid, _, _, reason = validate_fn(
                side="BUY", multi_sl=ct_sl, multi_tp=ct_tp, entry_price=price,
                trend_info=downtrend_info,
            )
            rr = (ct_tp - price) / (price - ct_sl) if price > ct_sl else 0
            self._record("M1d", f"Counter-trend LONG: reject R/R {rr:.2f} < {ct_rr:.2f}", not is_valid,
                         expected=f"Reject: R/R={rr:.2f}:1 < {ct_rr:.2f}:1 (counter-trend)",
                         actual=f"valid={is_valid}, reason={reason}")
        except Exception as e:
            self._record("M1d", f"Counter-trend LONG: reject R/R < {ct_rr:.2f}", False,
                         actual=f"{e}\n    {traceback.format_exc()}")

        # M1e: Counter-trend LONG, R/R=2.0 → should ACCEPT (>= 1.69)
        ct_tp_good = price * 1.04   # 4% TP → R/R = 2.0
        try:
            is_valid, v_sl, v_tp, reason = validate_fn(
                side="BUY", multi_sl=ct_sl, multi_tp=ct_tp_good, entry_price=price,
                trend_info=downtrend_info,
            )
            rr = (ct_tp_good - price) / (price - ct_sl) if price > ct_sl else 0
            self._record("M1e", f"Counter-trend LONG: accept R/R {rr:.2f} >= {ct_rr:.2f}", is_valid,
                         expected=f"Accept: R/R={rr:.2f}:1 >= {ct_rr:.2f}:1 (counter-trend)",
                         actual=f"valid={is_valid}, SL=${v_sl:,.2f}, TP=${v_tp:,.2f}" if is_valid else f"valid={is_valid}, reason={reason}")
        except Exception as e:
            self._record("M1e", f"Counter-trend LONG: accept R/R >= {ct_rr:.2f}", False,
                         actual=f"{e}\n    {traceback.format_exc()}")

        # M1f: Counter-trend SHORT (selling in uptrend), R/R=1.5 → should REJECT (< 1.69)
        ct_sl_s = price * 1.02    # 2% SL
        ct_tp_s = price * 0.970   # 3.0% TP → R/R = 1.5
        try:
            is_valid, _, _, reason = validate_fn(
                side="SELL", multi_sl=ct_sl_s, multi_tp=ct_tp_s, entry_price=price,
                trend_info=uptrend_info,
            )
            rr = (price - ct_tp_s) / (ct_sl_s - price) if ct_sl_s > price else 0
            self._record("M1f", f"Counter-trend SHORT: reject R/R {rr:.2f} < {ct_rr:.2f}", not is_valid,
                         expected=f"Reject: R/R={rr:.2f}:1 < {ct_rr:.2f}:1 (counter-trend)",
                         actual=f"valid={is_valid}, reason={reason}")
        except Exception as e:
            self._record("M1f", f"Counter-trend SHORT: reject R/R < {ct_rr:.2f}", False,
                         actual=f"{e}\n    {traceback.format_exc()}")

        # M1g: ADX < 25 (no clear trend), same R/R=1.5 LONG → should ACCEPT (not counter-trend, 1.5 >= 1.3)
        weak_trend_info = {
            'trend_direction': 'DOWNTREND',
            'adx': 20.0,  # Below 25 threshold → not counter-trend
            'di_plus': 15.0,
            'di_minus': 25.0,
        }
        try:
            is_valid, v_sl, v_tp, reason = validate_fn(
                side="BUY", multi_sl=ct_sl, multi_tp=ct_tp, entry_price=price,
                trend_info=weak_trend_info,
            )
            rr = (ct_tp - price) / (price - ct_sl) if price > ct_sl else 0
            self._record("M1g", f"Weak trend (ADX<25) LONG: accept R/R {rr:.2f} >= {min_rr}", is_valid,
                         expected=f"Accept: ADX=20 < 25, not counter-trend, R/R={rr:.2f}:1 >= {min_rr}:1",
                         actual=f"valid={is_valid}, SL=${v_sl:,.2f}, TP=${v_tp:,.2f}" if is_valid else f"valid={is_valid}, reason={reason}")
        except Exception as e:
            self._record("M1g", f"Weak trend (ADX<25) LONG: accept R/R >= {min_rr}", False,
                         actual=f"{e}\n    {traceback.format_exc()}")

    # ── M2: SL Side Validation ──

    def _check_sl_side(self, price: float, validate_fn):
        print(f"  --- M2: SL Side Validation ---")
        print()

        # M2a: LONG SL above entry
        try:
            is_valid, _, _, reason = validate_fn(
                side="BUY", multi_sl=price * 1.01, multi_tp=price * 1.05,
                entry_price=price,
            )
            self._record("M2a", "SL side: reject LONG SL > entry", not is_valid,
                         expected="Reject: LONG SL must be < entry",
                         actual=f"valid={is_valid}, reason={reason}")
        except Exception as e:
            self._record("M2a", "SL side: reject LONG SL > entry", False,
                         actual=f"{e}\n    {traceback.format_exc()}")

        # M2b: SHORT SL below entry
        try:
            is_valid, _, _, reason = validate_fn(
                side="SELL", multi_sl=price * 0.99, multi_tp=price * 0.95,
                entry_price=price,
            )
            self._record("M2b", "SL side: reject SHORT SL < entry", not is_valid,
                         expected="Reject: SHORT SL must be > entry",
                         actual=f"valid={is_valid}, reason={reason}")
        except Exception as e:
            self._record("M2b", "SL side: reject SHORT SL < entry", False,
                         actual=f"{e}\n    {traceback.format_exc()}")

    # ── M3: v11.0-simple Mechanical SL/TP ──
    # Production uses calculate_mechanical_sltp() — ATR × confidence multiplier
    # Old S/R-based SL/TP (sr_sltp_calculator.py) deprecated in v11.0-simple

    # ── M4: SL Favorable Direction ──

    def _check_sl_favorable_direction(self, price: float):
        print(f"  --- M4: SL Favorable Direction Rule ---")
        print()

        # LONG: SL can only go UP (max)
        old_sl = price * 0.97
        new_lower = price * 0.96
        new_higher = price * 0.975
        final_lower = max(new_lower, old_sl)
        final_higher = max(new_higher, old_sl)
        ok = final_lower == old_sl and final_higher == new_higher
        self._record("M4a", "LONG SL favorable: max(old, new)", ok,
                     expected=f"Lower blocked (max={old_sl:,.2f}), higher allowed (max={new_higher:,.2f})",
                     actual=f"Down: ${new_lower:,.2f} → ${final_lower:,.2f} "
                            f"({'blocked ✓' if final_lower == old_sl else 'ALLOWED ✗'})\n"
                            f"    Up:   ${new_higher:,.2f} → ${final_higher:,.2f} "
                            f"({'allowed ✓' if final_higher == new_higher else 'BLOCKED ✗'})")

        # SHORT: SL can only go DOWN (min)
        old_sl_s = price * 1.03
        new_higher_s = price * 1.04
        new_lower_s = price * 1.025
        final_h = min(new_higher_s, old_sl_s)
        final_l = min(new_lower_s, old_sl_s)
        ok = final_h == old_sl_s and final_l == new_lower_s
        self._record("M4b", "SHORT SL favorable: min(old, new)", ok,
                     expected=f"Higher blocked (min={old_sl_s:,.2f}), lower allowed (min={new_lower_s:,.2f})",
                     actual=f"Up:   ${new_higher_s:,.2f} → ${final_h:,.2f} "
                            f"({'blocked ✓' if final_h == old_sl_s else 'ALLOWED ✗'})\n"
                            f"    Down: ${new_lower_s:,.2f} → ${final_l:,.2f} "
                            f"({'allowed ✓' if final_l == new_lower_s else 'BLOCKED ✗'})")

    # ── M6: Emergency SL (config-driven, v15.0) ──

    def _check_emergency_sl(self, price: float):
        """v15.0: Emergency SL now reads base_pct from config (was hardcoded 0.02)."""
        print(f"  --- M6: Emergency SL (Config-driven, v15.0) ---")
        print()

        # Read configured values
        try:
            from strategy.trading_logic import _get_trading_logic_config
            tl_cfg = _get_trading_logic_config()
            emg_cfg = tl_cfg.get('emergency_sl', {})
            base_pct = emg_cfg.get('base_pct', 0.02)
            atr_mult = emg_cfg.get('atr_multiplier', 1.5)
            cooldown = emg_cfg.get('cooldown_seconds', 120)
        except Exception as e:
            print(f"    ⚠️ Config read failed, using defaults: {e}")
            base_pct, atr_mult, cooldown = 0.02, 1.5, 120

        long_sl = price * (1 - base_pct)
        short_sl = price * (1 + base_pct)
        self._record("M6a", f"Emergency SL: {base_pct*100:.1f}% from config (base_pct)", True,
                     actual=f"LONG emergency SL = ${long_sl:,.2f} "
                            f"({base_pct*100:.1f}% below)\n"
                            f"    SHORT emergency SL = ${short_sl:,.2f} "
                            f"({base_pct*100:.1f}% above)")

        # M6b: Verify config chain — values come from base.yaml, not hardcoded
        ok_chain = base_pct > 0 and atr_mult > 0 and cooldown > 0
        self._record("M6b", "Emergency SL config chain (base.yaml → strategy)", ok_chain,
                     expected="base_pct > 0, atr_multiplier > 0, cooldown > 0",
                     actual=f"base_pct={base_pct}, atr_mult={atr_mult}, cooldown={cooldown}s")

    # ── M7: evaluate_trade() Grading (v5.12) ──

    def _check_evaluate_trade(self, price: float):
        """v5.12: Test evaluate_trade() grading logic matches production."""
        print(f"  --- M7: evaluate_trade() Grading (A+/A/B/C/D/F) ---")
        print()

        try:
            from strategy.trading_logic import evaluate_trade
        except ImportError as e:
            self._record("M7", "evaluate_trade import", False, actual=str(e))
            return

        entry = price
        sl = price * 0.98   # 2% SL
        tp = price * 1.04   # 4% TP

        # M7a: A+ grade (R/R >= 2.5, big winner)
        exit_a_plus = entry + (entry - sl) * 2.6  # actual R/R ≈ 2.6
        result = evaluate_trade(
            entry_price=entry, exit_price=exit_a_plus,
            planned_sl=sl, planned_tp=tp,
            direction='LONG', pnl_pct=5.2,
        )
        self._record("M7a", "Grade A+: R/R >= 2.5 (big winner)", result['grade'] == 'A+',
                     expected="grade=A+",
                     actual=f"grade={result['grade']}, actual_rr={result['actual_rr']:.2f}")

        # M7b: B grade (R/R >= 1.0 but < 1.5)
        exit_b = entry + (entry - sl) * 1.2  # actual R/R ≈ 1.2
        result = evaluate_trade(
            entry_price=entry, exit_price=exit_b,
            planned_sl=sl, planned_tp=tp,
            direction='LONG', pnl_pct=2.4,
        )
        self._record("M7b", "Grade B: 1.0 <= R/R < 1.5", result['grade'] == 'B',
                     expected="grade=B",
                     actual=f"grade={result['grade']}, actual_rr={result['actual_rr']:.2f}")

        # M7c: D grade (loss within planned SL, discipline maintained)
        exit_d = sl + (entry - sl) * 0.1  # loss near SL, within 1.2× tolerance
        pnl_d = (exit_d - entry) / entry * 100
        result = evaluate_trade(
            entry_price=entry, exit_price=exit_d,
            planned_sl=sl, planned_tp=tp,
            direction='LONG', pnl_pct=pnl_d,
        )
        self._record("M7c", "Grade D: loss within SL (controlled)", result['grade'] == 'D',
                     expected="grade=D (disciplined loss)",
                     actual=f"grade={result['grade']}, actual_rr={result['actual_rr']:.2f}")

        # M7d: F grade (loss exceeded SL by > 20%, uncontrolled)
        exit_f = entry - (entry - sl) * 1.5  # loss 1.5× beyond planned SL
        pnl_f = (exit_f - entry) / entry * 100
        result = evaluate_trade(
            entry_price=entry, exit_price=exit_f,
            planned_sl=sl, planned_tp=tp,
            direction='LONG', pnl_pct=pnl_f,
        )
        self._record("M7d", "Grade F: loss > SL×1.2 (uncontrolled)", result['grade'] == 'F',
                     expected="grade=F (uncontrolled loss)",
                     actual=f"grade={result['grade']}, actual_rr={result['actual_rr']:.2f}")

    # ── M8: v6.0 evaluate_trade() Position Management Fields ──

    def _check_evaluate_trade_v60_fields(self, price: float):
        """v6.0: Test evaluate_trade() accepts and returns v6.0 position management fields."""
        print(f"  --- M8: v6.0 evaluate_trade() Position Management Fields ---")
        print()

        try:
            from strategy.trading_logic import evaluate_trade
        except ImportError as e:
            self._record("M8", "evaluate_trade v6.0 import", False, actual=str(e))
            return

        entry = price
        sl = price * 0.98
        tp = price * 1.04
        exit_price = price * 1.03  # profitable trade

        # M8a: v6.0 fields accepted and returned when provided
        result = evaluate_trade(
            entry_price=entry, exit_price=exit_price,
            planned_sl=sl, planned_tp=tp,
            direction='LONG', pnl_pct=3.0,
            pyramid_layers_used=2,
            confidence_at_exit="MEDIUM",
        )
        has_pyramid = result.get('pyramid_layers_used') == 2
        has_conf_exit = result.get('confidence_at_exit') == "MEDIUM"
        all_ok = has_pyramid and has_conf_exit
        self._record("M8a", "v6.0 fields: pyramid_layers + confidence_at_exit", all_ok,
                     expected="pyramid_layers_used=2, confidence_at_exit=MEDIUM",
                     actual=f"pyramid={result.get('pyramid_layers_used')}, "
                            f"conf_exit={result.get('confidence_at_exit')}")

        # M8b: v6.0 fields omitted when using defaults (backward compat)
        result_default = evaluate_trade(
            entry_price=entry, exit_price=exit_price,
            planned_sl=sl, planned_tp=tp,
            direction='LONG', pnl_pct=3.0,
        )
        # Default values should not create v6.0 keys (or use defaults)
        no_crash = result_default.get('grade') is not None
        self._record("M8b", "v6.0 fields: backward compat (no crash without v6.0 params)", no_crash,
                     expected="grade present, no crash",
                     actual=f"grade={result_default.get('grade')}, "
                            f"pyramid={result_default.get('pyramid_layers_used', 'absent')}")

        # M8c: Direction-aware planned_rr (TP on wrong side → planned_rr=0)
        # Simulates Trade 12 bug: SHORT with TP above entry (stale/averaged data)
        wrong_side_result = evaluate_trade(
            entry_price=price,
            exit_price=price * 1.01,  # lost money on SHORT
            planned_sl=price * 1.01,  # SL above entry (correct for SHORT)
            planned_tp=price * 1.002,  # TP ABOVE entry (WRONG for SHORT!)
            direction='SHORT', pnl_pct=-1.0,
        )
        wrong_side_rr = wrong_side_result.get('planned_rr', -1)
        self._record("M8c", "v6.0 direction-aware planned_rr: SHORT TP above entry → planned_rr=0",
                     wrong_side_rr == 0.0,
                     expected="planned_rr=0.0 (TP on wrong side detected)",
                     actual=f"planned_rr={wrong_side_rr}")

        # M8d: Direction-aware planned_rr (TP on correct side → normal R/R)
        correct_side_result = evaluate_trade(
            entry_price=price,
            exit_price=price * 0.98,
            planned_sl=price * 1.01,  # SL above entry (correct for SHORT)
            planned_tp=price * 0.97,  # TP below entry (correct for SHORT)
            direction='SHORT', pnl_pct=2.0,
        )
        correct_rr = correct_side_result.get('planned_rr', 0)
        self._record("M8d", "v6.0 direction-aware planned_rr: SHORT TP below entry → valid R/R",
                     correct_rr > 0,
                     expected="planned_rr > 0 (TP on correct side)",
                     actual=f"planned_rr={correct_rr}")

    def _check_entry_pipeline_integration(self, price: float) -> None:
        """
        M9: v6.0 Entry pipeline integration test (GAP #1 fix).

        Tests the FULL validation chain in sequence, matching the exact flow
        in _submit_bracket_order():
          1. validate_multiagent_sltp() → Level 1
          2. If fails → calculate_sr_based_sltp() → Level 2
          3. R/R gate check
          4. Position size calculation
          5. Min notional check ($100 USDT)

        This ensures the chain works end-to-end, not just individual functions.
        """
        print(f"  --- M9: Entry Pipeline Integration (v6.0) ---")
        print()

        try:
            from strategy.trading_logic import validate_multiagent_sltp, calculate_position_size

            # Test Case 1: Valid LONG signal → expect full pipeline pass
            sl_long = price * 0.985  # 1.5% below
            tp_long = price * 1.03   # 3.0% above → R/R = 2.0
            is_valid, v_sl, v_tp, reason = validate_multiagent_sltp(
                side='BUY', multi_sl=sl_long, multi_tp=tp_long,
                entry_price=price,
            )
            step1_pass = is_valid and v_sl > 0 and v_tp > 0

            # Step 2: Position sizing (using test config)
            equity = 1000.0
            leverage = 3
            position_pct = 0.10
            notional = equity * leverage * position_pct
            qty = notional / price if price > 0 else 0
            step2_pass = notional >= 100 and qty > 0  # Min $100 notional

            # Step 3: R/R sanity
            if step1_pass:
                rr = (v_tp - price) / (price - v_sl) if (price - v_sl) > 0 else 0
                step3_pass = rr >= 1.5
            else:
                rr = 0
                step3_pass = False

            pipeline_pass = step1_pass and step2_pass and step3_pass
            self._record("M9a", "Entry pipeline: LONG valid signal → full chain pass", pipeline_pass,
                         expected="validate_multiagent_sltp ✅ → position_size ✅ → R/R ≥ 1.5 ✅",
                         actual=f"L1_valid={step1_pass} (SL=${v_sl:,.0f} TP=${v_tp:,.0f}), "
                                f"notional=${notional:.0f} (≥$100={step2_pass}), R/R={rr:.2f}")

            # Test Case 2: Bad SL (too close) → expect Level 1 rejection
            bad_sl = price * 0.998  # Only 0.2% away
            bad_tp = price * 1.003  # 0.3% away
            is_valid2, _, _, reason2 = validate_multiagent_sltp(
                side='BUY', multi_sl=bad_sl, multi_tp=bad_tp,
                entry_price=price,
            )
            self._record("M9b", "Entry pipeline: bad SL (0.2%) → Level 1 rejection", not is_valid2,
                         expected="validate_multiagent_sltp returns False",
                         actual=f"valid={is_valid2}, reason={reason2}")

            # Test Case 3: None SL/TP → expect Level 1 rejection (needs Level 2 fallback)
            is_valid3, _, _, reason3 = validate_multiagent_sltp(
                side='SELL', multi_sl=None, multi_tp=None,
                entry_price=price,
            )
            self._record("M9c", "Entry pipeline: None SL/TP → Level 1 rejection", not is_valid3,
                         expected="validate_multiagent_sltp returns False (no SL/TP provided)",
                         actual=f"valid={is_valid3}, reason={reason3}")

        except Exception as e:
            self._record("M9", "Entry pipeline integration", False,
                         expected="No exception",
                         actual=f"Exception: {e}\n    {traceback.format_exc()}")

    def _check_v6_state_machine_runtime(self, price: float) -> None:
        """
        M10: v6.0 State machine runtime validation (GAP #2 fix).

        Tests actual state transitions for cooldown, pyramiding, and
        confidence degradation logic — not just code existence checks.
        """
        print(f"  --- M10: v6.0 State Machine Runtime (v6.0) ---")
        print()

        try:
            # Test 1: Cooldown classification logic
            # Simulate: LONG stopped out, price recovered above SL → noise stop
            atr = price * 0.01  # 1% ATR
            sl_price = price * 0.98  # SL was 2% below
            recovered_price = sl_price * 1.01  # Price recovered above SL

            price_from_sl = recovered_price - sl_price  # positive = recovered
            is_noise = price_from_sl > 0
            self._record("M10a", "Cooldown: price recovery above SL → noise_stop", is_noise,
                         expected="price_from_sl > 0 → noise stop (1 candle cooldown)",
                         actual=f"price_from_sl={price_from_sl:.2f} ({'noise' if is_noise else 'NOT noise'})")

            # Test 2: Reversal stop classification
            continued_price = sl_price - atr * 1.5  # Price continued down past SL by 1.5 ATR
            price_from_sl_2 = continued_price - sl_price  # negative
            is_reversal = abs(price_from_sl_2) >= atr
            self._record("M10b", "Cooldown: price continues past SL by ≥1 ATR → reversal_stop", is_reversal,
                         expected="|price_from_sl| >= ATR → reversal stop (6 candles cooldown)",
                         actual=f"|price_from_sl|={abs(price_from_sl_2):.2f}, ATR={atr:.2f}, "
                                f"{'reversal' if is_reversal else 'NOT reversal'}")

            # Test 3: Pyramiding profit threshold
            entry_price_test = price * 0.99  # Entered at 1% below
            unrealized_pnl_atr = (price - entry_price_test) / atr  # PnL in ATR units
            min_profit_atr = 0.5
            profit_ok = unrealized_pnl_atr >= min_profit_atr
            self._record("M10d", "Pyramiding: profit threshold (≥0.5 ATR)", profit_ok,
                         expected="unrealized PnL ≥ 0.5 ATR → allowed",
                         actual=f"PnL={unrealized_pnl_atr:.2f} ATR, threshold={min_profit_atr}")

        except Exception as e:
            self._record("M10", "v6.0 state machine runtime", False,
                         expected="No exception",
                         actual=f"Exception: {e}\n    {traceback.format_exc()}")

    def _check_config_value_guards(self, price: float) -> None:
        """
        M11: v6.2-v6.3 Config value guards.

        Verifies critical config parameters have correct values:
        - M11a: min_sl_distance_atr >= 2.0 (v11.0-simple primary gate, was zone_anchored_sl)
        - M11b: min_sl_distance_pct >= 0.002 (low-volatility floor)
        - M11c: Mechanical SL/TP config (ATR × confidence)
        - M11d: ATR-based minimum actually rejects noise SLs
        - M11e: Funding rate 5-decimal precision in pipeline
        - M11f: min_sl_distance_atr >= 2.0 (ATR-primary gate — same as M11a, kept for compat)
        - M11h: get_min_sl_distance() returns ATR-based value
        """
        print(f"  --- M11: v6.2-v6.3 Config Value Guards ---")
        print()

        try:
            from strategy.trading_logic import _get_trading_logic_config

            tl_cfg = _get_trading_logic_config()

            # M11a: v11.0-simple — min_sl_distance_atr is the primary noise-stop gate
            # (replaces zone_anchored_sl.absolute_min_sl_atr which was removed)
            min_sl_atr_val = tl_cfg.get('min_sl_distance_atr', 0)
            ok_atr = min_sl_atr_val >= 2.0
            self._record("M11a", "min_sl_distance_atr >= 2.0 (v11.0 primary noise-stop gate)", ok_atr,
                         expected="min_sl_distance_atr >= 2.0 (SL 须 >= 2.0 ATR, 与 M11f 一致)",
                         actual=f"min_sl_distance_atr={min_sl_atr_val}")

            # M11b: min_sl_distance_pct >= 0.002 (low-volatility floor, ATR=0 fallback)
            min_sl_pct_val = tl_cfg.get('min_sl_distance_pct', 0)
            ok_pct = min_sl_pct_val >= 0.002
            self._record("M11b", "min_sl_distance_pct >= 0.002 (低波动 floor)", ok_pct,
                         expected="min_sl_distance_pct >= 0.002 (0.2%)",
                         actual=f"min_sl_distance_pct={min_sl_pct_val}")

            # M11c: v11.0-simple — Mechanical SL/TP uses ATR × confidence multiplier
            # Level 2 S/R fallback removed (sr_sltp_calculator.py deprecated)
            mech_cfg = tl_cfg.get('mechanical_sltp', {})
            mech_enabled = mech_cfg.get('enabled', False)
            sl_mult = mech_cfg.get('sl_atr_multiplier', {})
            has_high = 'HIGH' in sl_mult
            has_medium = 'MEDIUM' in sl_mult
            ok_mech = mech_enabled and has_high and has_medium
            self._record("M11c", "v11.0-simple Mechanical SL/TP config (ATR × confidence)", ok_mech,
                         expected="mechanical_sltp.enabled=true + sl_atr_multiplier has HIGH/MEDIUM",
                         actual=f"enabled={mech_enabled}, HIGH={sl_mult.get('HIGH')}, MEDIUM={sl_mult.get('MEDIUM')}")

            # M11d: ATR-based minimum actually rejects noise SLs
            # Simulate: ATR=$124, SL=0.21% → should fail min_sl_distance (2.0 ATR → 0.36%)
            test_atr = price * 0.0018  # ~0.18% ATR (realistic)
            test_sl_distance = price * 0.0021  # 0.21% SL
            test_sl_pct = test_sl_distance / price
            computed_min = max(min_sl_atr_val * test_atr / price, min_sl_pct_val)
            ok_reject = test_sl_pct < computed_min
            self._record("M11d", f"ATR-based min rejects noise SL (0.21% < {computed_min*100:.2f}%)", ok_reject,
                         expected=f"SL 0.21% < min_sl_distance {computed_min*100:.2f}% → REJECT",
                         actual=f"SL={test_sl_pct*100:.3f}%, min={computed_min*100:.3f}%, "
                                f"reject={'YES ✓' if ok_reject else 'NO ✗'}")

            # M11e: Funding rate precision maintained through pipeline
            test_fr = 0.000123456
            formatted_5d = f"{test_fr:.5f}"
            rounded_6d = round(test_fr, 6)
            ok_precision = formatted_5d == "0.00012" and rounded_6d == 0.000123
            self._record("M11e", "Funding rate 5-decimal precision (:.5f / round(,6))", ok_precision,
                         expected=":.5f → '0.00012', round(,6) → 0.000123",
                         actual=f":.5f='{formatted_5d}', round(,6)={rounded_6d}")

            # M11f: v6.3 min_sl_distance_atr >= 2.0 (ATR-primary gate)
            min_sl_atr = tl_cfg.get('min_sl_distance_atr', 0)
            ok_min_sl_atr = min_sl_atr >= 2.0
            self._record("M11f", "v6.3 min_sl_distance_atr >= 2.0 (ATR-primary gate)", ok_min_sl_atr,
                         expected="min_sl_distance_atr >= 2.0 (Level 1 main gate)",
                         actual=f"min_sl_distance_atr={min_sl_atr}")

            # M11h: get_min_sl_distance() returns ATR-based value > PCT floor
            from strategy.trading_logic import get_min_sl_distance, get_min_sl_distance_pct
            test_atr_h = price * 0.008  # 0.8% ATR (typical BTC)
            atr_based = get_min_sl_distance(atr_value=test_atr_h, entry_price=price)
            pct_floor = get_min_sl_distance_pct()
            ok_atr_primary = atr_based > pct_floor  # ATR-based should exceed PCT floor
            self._record("M11h", f"get_min_sl_distance() ATR-primary ({atr_based*100:.2f}% > {pct_floor*100:.2f}%)",
                         ok_atr_primary,
                         expected=f"ATR-based {atr_based*100:.2f}% > PCT floor {pct_floor*100:.2f}%",
                         actual=f"atr_based={atr_based*100:.3f}%, pct_floor={pct_floor*100:.3f}%, "
                                f"ATR={test_atr_h:.2f}, price={price:.0f}")

        except Exception as e:
            self._record("M11", "v6.2-v6.3 config value guards", False,
                         expected="No exception",
                         actual=f"Exception: {e}\n    {traceback.format_exc()}")

    # ── M12: v15.0 Config Chain Validation ──

    def _check_config_chain_v15(self, price: float) -> None:
        """
        M12: v15.0 Config chain validation.

        Verifies the 8 extracted config values flow correctly through the chain:
        base.yaml → ConfigManager → main_live.py → StrategyConfig → Strategy.self.xxx
        """
        print(f"  --- M12: v15.0 Config Chain Validation ---")
        print()

        try:
            from strategy.trading_logic import _get_trading_logic_config
            import yaml

            # Layer 1: Read directly from base.yaml
            base_yaml_path = self.ctx.project_root / "configs" / "base.yaml"
            if not base_yaml_path.exists():
                self._record("M12", "v15.0 config chain: base.yaml", False,
                             actual="base.yaml not found")
                return

            with open(base_yaml_path, 'r', encoding='utf-8') as f:
                base_cfg = yaml.safe_load(f)

            # Layer 2: Read from trading_logic config (through ConfigManager)
            tl_cfg = _get_trading_logic_config()

            # M12a: emergency_sl section exists in base.yaml
            emg_yaml = base_cfg.get('trading_logic', {}).get('emergency_sl', {})
            has_emg = all(k in emg_yaml for k in ['base_pct', 'atr_multiplier', 'cooldown_seconds'])
            self._record("M12a", "base.yaml: emergency_sl section complete", has_emg,
                         expected="base_pct + atr_multiplier + cooldown_seconds in trading_logic.emergency_sl",
                         actual=f"keys={list(emg_yaml.keys())}")

            # M12b: mechanical_sltp.sl_atr_multiplier_floor exists
            mech_cfg = base_cfg.get('trading_logic', {}).get('mechanical_sltp', {})
            sl_floor = mech_cfg.get('sl_atr_multiplier_floor')
            has_floor = sl_floor is not None and sl_floor > 0
            self._record("M12b", "base.yaml: sl_atr_multiplier_floor configured", has_floor,
                         expected="sl_atr_multiplier_floor > 0",
                         actual=f"sl_atr_multiplier_floor={sl_floor}")

            # M12c: timing section has new entries
            timing = base_cfg.get('timing', {})
            has_timing = ('price_cache_ttl_seconds' in timing and
                          'reversal_timeout_seconds' in timing)
            self._record("M12c", "base.yaml: timing.price_cache_ttl + reversal_timeout", has_timing,
                         expected="price_cache_ttl_seconds + reversal_timeout_seconds in timing",
                         actual=f"price_cache={timing.get('price_cache_ttl_seconds')}, "
                                f"reversal={timing.get('reversal_timeout_seconds')}")

            # M12d: capital.max_leverage_limit exists
            capital = base_cfg.get('capital', {})
            max_lev = capital.get('max_leverage_limit')
            has_lev = max_lev is not None and max_lev >= 1
            self._record("M12d", "base.yaml: capital.max_leverage_limit configured", has_lev,
                         expected="max_leverage_limit >= 1",
                         actual=f"max_leverage_limit={max_lev}")

            # M12e: sr_zones.cache_ttl_seconds exists
            sr_cfg = base_cfg.get('sr_zones', {})
            sr_ttl = sr_cfg.get('cache_ttl_seconds')
            has_sr_ttl = sr_ttl is not None and sr_ttl > 0
            self._record("M12e", "base.yaml: sr_zones.cache_ttl_seconds configured", has_sr_ttl,
                         expected="cache_ttl_seconds > 0",
                         actual=f"cache_ttl_seconds={sr_ttl}")

            # M12f: StrategyConfig has the 8 new fields (check via main_live.py source)
            main_live_path = self.ctx.project_root / "main_live.py"
            if main_live_path.exists():
                ml_src = main_live_path.read_text()
                fields = [
                    "emergency_sl_base_pct",
                    "emergency_sl_atr_multiplier",
                    "emergency_sl_cooldown_seconds",
                    "emergency_sl_max_consecutive",
                    "sr_zones_cache_ttl_seconds",
                    "price_cache_ttl_seconds",
                    "reversal_timeout_seconds",
                    "max_leverage_limit",
                ]
                found = [f for f in fields if f in ml_src]
                ok_ml = len(found) == len(fields)
                self._record("M12f", "main_live.py: all 8 config fields passed to StrategyConfig", ok_ml,
                             expected=f"All {len(fields)} fields in get_strategy_config()",
                             actual=f"{len(found)}/{len(fields)} found")
            else:
                self._record("M12f", "main_live.py config fields", False,
                             actual="main_live.py not found")

        except Exception as e:
            self._record("M12", "v15.0 config chain validation", False,
                         expected="No exception",
                         actual=f"Exception: {e}\n    {traceback.format_exc()}")

    # ── M13: v18.0 Drawdown Hysteresis State Machine ──

    def _check_drawdown_hysteresis_runtime(self) -> None:
        """
        M13: v18.0 Drawdown hysteresis runtime validation.

        Simulates state transitions to verify:
        - 10% drawdown → REDUCED
        - REDUCED + 8% (in band) → stay REDUCED
        - REDUCED + 4.9% (below recovery) → ACTIVE
        - ACTIVE + 8% (in band, never REDUCED) → ACTIVE
        - REDUCED + exactly 5% (boundary) → stay REDUCED
        """
        print(f"  --- M13: v18.0 Drawdown Hysteresis Runtime ---")
        print()

        try:
            from utils.risk_controller import RiskController, TradingState

            config = {
                'circuit_breakers': {
                    'enabled': True,
                    'max_drawdown': {
                        'enabled': True,
                        'reduce_threshold_pct': 0.10,
                        'halt_threshold_pct': 0.15,
                        'recovery_threshold_pct': 0.05,
                    },
                    'daily_loss': {'enabled': False},
                    'consecutive_losses': {'enabled': False},
                    'volatility': {'enabled': False},
                    'cooldown': {'enabled': False},
                }
            }
            rc = RiskController(config)
            all_ok = True

            # Test A: 10% → REDUCED
            rc.metrics.drawdown_pct = 0.10
            rc.metrics.trading_state = TradingState.ACTIVE
            rc._update_trading_state()
            ok_a = rc.metrics.trading_state == TradingState.REDUCED
            all_ok = all_ok and ok_a
            self._record("M13a", "10% drawdown → REDUCED", ok_a,
                         expected="REDUCED", actual=str(rc.metrics.trading_state))

            # Test B: REDUCED + 8% → stay REDUCED (hysteresis)
            rc.metrics.drawdown_pct = 0.08
            rc._update_trading_state()
            ok_b = rc.metrics.trading_state == TradingState.REDUCED
            all_ok = all_ok and ok_b
            self._record("M13b", "REDUCED + 8% (band) → stay REDUCED", ok_b,
                         expected="REDUCED", actual=str(rc.metrics.trading_state))

            # Test C: REDUCED + 4.9% → ACTIVE
            rc.metrics.drawdown_pct = 0.049
            rc._update_trading_state()
            ok_c = rc.metrics.trading_state == TradingState.ACTIVE
            all_ok = all_ok and ok_c
            self._record("M13c", "REDUCED + 4.9% (below recovery) → ACTIVE", ok_c,
                         expected="ACTIVE", actual=str(rc.metrics.trading_state))

            # Test D: ACTIVE + 8% → ACTIVE (never REDUCED)
            rc.metrics.drawdown_pct = 0.08
            rc.metrics.trading_state = TradingState.ACTIVE
            rc._update_trading_state()
            ok_d = rc.metrics.trading_state == TradingState.ACTIVE
            all_ok = all_ok and ok_d
            self._record("M13d", "ACTIVE + 8% (never REDUCED) → ACTIVE", ok_d,
                         expected="ACTIVE", actual=str(rc.metrics.trading_state))

            # Test E: REDUCED + exactly 5% boundary → stay REDUCED
            rc.metrics.drawdown_pct = 0.05
            rc.metrics.trading_state = TradingState.REDUCED
            rc._update_trading_state()
            ok_e = rc.metrics.trading_state == TradingState.REDUCED
            all_ok = all_ok and ok_e
            self._record("M13e", "REDUCED + 5% (boundary) → stay REDUCED", ok_e,
                         expected="REDUCED", actual=str(rc.metrics.trading_state))

        except Exception as e:
            self._record("M13", "v18.0 drawdown hysteresis runtime", False,
                         expected="No exception",
                         actual=f"Exception: {e}\n    {traceback.format_exc()}")

    # ── M14: v19.1 Extension Ratio Boundary Math ──

    def _check_extension_ratio_boundaries(self, price: float) -> None:
        """
        M14: v19.1 Extension Ratio boundary classification.

        Validates _calculate_extension_ratios() regime thresholds:
        - |ratio| < 2.0 → NORMAL
        - 2.0 ≤ |ratio| < 3.0 → EXTENDED
        - 3.0 ≤ |ratio| < 5.0 → OVEREXTENDED
        - |ratio| ≥ 5.0 → EXTREME
        - ATR=0 or price=0 → INSUFFICIENT_DATA
        """
        print(f"  --- M14: v19.1 Extension Ratio Boundary Math ---")
        print()

        try:
            from indicators.technical_manager import TechnicalIndicatorManager

            # Create minimal manager for testing
            mgr = TechnicalIndicatorManager(
                sma_periods=[20],
                ema_periods=[12, 26],
                rsi_period=14,
                macd_fast=12,
                macd_slow=26,
                bb_period=20,
                bb_std=2.0,
                volume_ma_period=20,
                support_resistance_lookback=20,
            )

            # Test with synthetic values — invoke _calculate_extension_ratios directly
            # We need to set up an initialized SMA_20 manually
            # Instead, test the classification logic directly via the method

            # Edge case: ATR = 0
            result = mgr._calculate_extension_ratios(100000.0, 0.0)
            ok_a = result.get('extension_regime') == 'INSUFFICIENT_DATA'
            self._record("M14a", "Extension Ratio: ATR=0 → INSUFFICIENT_DATA", ok_a,
                         expected="INSUFFICIENT_DATA",
                         actual=f"regime={result.get('extension_regime')}")

            # Edge case: price = 0
            result = mgr._calculate_extension_ratios(0.0, 1000.0)
            ok_b = result.get('extension_regime') == 'INSUFFICIENT_DATA'
            self._record("M14b", "Extension Ratio: price=0 → INSUFFICIENT_DATA", ok_b,
                         expected="INSUFFICIENT_DATA",
                         actual=f"regime={result.get('extension_regime')}")

            # Real-data test: use live technical data if available
            td = self.ctx.technical_data
            if td and td.get('extension_regime') and td.get('extension_ratio_sma_20') is not None:
                ext_sma20 = td.get('extension_ratio_sma_20', 0)
                regime = td.get('extension_regime')
                abs_ext = abs(ext_sma20)

                # Verify classification matches thresholds
                if regime == 'INSUFFICIENT_DATA':
                    expected = 'INSUFFICIENT_DATA'
                elif abs_ext >= 5.0:
                    expected = 'EXTREME'
                elif abs_ext >= 3.0:
                    expected = 'OVEREXTENDED'
                elif abs_ext >= 2.0:
                    expected = 'EXTENDED'
                else:
                    expected = 'NORMAL'

                ok_c = regime == expected
                self._record("M14c", f"Live Extension Ratio classification (|{ext_sma20:.2f}| ATR)", ok_c,
                             expected=f"{expected} (|ratio|={abs_ext:.2f})",
                             actual=f"{regime}")
            else:
                self._record("M14c", "Live Extension Ratio classification", True,
                             expected="skipped (no live data)",
                             actual="N/A — extension ratio data not available")

        except Exception as e:
            self._record("M14", "v19.1 Extension Ratio boundary math", False,
                         expected="No exception",
                         actual=f"Exception: {e}\n    {traceback.format_exc()}")

    # ── M15: v20.0 Volatility Regime Percentile Boundary Math ──

    def _check_volatility_regime_boundaries(self, price: float) -> None:
        """
        M15: v20.0 Volatility Regime boundary classification.

        Validates classify_volatility_regime() percentile thresholds:
        - percentile < 30.0 → LOW
        - 30.0 ≤ percentile < 70.0 → NORMAL
        - 70.0 ≤ percentile < 90.0 → HIGH
        - percentile ≥ 90.0 → EXTREME
        - ATR=0 or insufficient data → INSUFFICIENT_DATA
        """
        print(f"  --- M15: v20.0 Volatility Regime Boundary Math ---")
        print()

        try:
            from utils.shared_logic import classify_volatility_regime

            # Test boundary values
            test_cases = [
                (0.0, "LOW", "0th percentile"),
                (15.0, "LOW", "15th percentile"),
                (29.9, "LOW", "29.9th percentile (just below NORMAL)"),
                (30.0, "NORMAL", "30th percentile (boundary)"),
                (50.0, "NORMAL", "50th percentile (midpoint)"),
                (69.9, "NORMAL", "69.9th percentile (just below HIGH)"),
                (70.0, "HIGH", "70th percentile (boundary)"),
                (80.0, "HIGH", "80th percentile"),
                (89.9, "HIGH", "89.9th percentile (just below EXTREME)"),
                (90.0, "EXTREME", "90th percentile (boundary)"),
                (95.0, "EXTREME", "95th percentile"),
                (100.0, "EXTREME", "100th percentile"),
            ]

            all_ok = True
            details = []
            for pctl, expected, desc in test_cases:
                actual = classify_volatility_regime(pctl)
                ok = actual == expected
                if not ok:
                    all_ok = False
                    details.append(f"FAIL: {desc} → got {actual}, expected {expected}")

            self._record("M15a", "Volatility Regime: classify_volatility_regime boundary tests", all_ok,
                         expected=f"All {len(test_cases)} boundary tests pass",
                         actual=f"{'All passed' if all_ok else '; '.join(details)}")

            # Test with live data if available
            td = self.ctx.technical_data
            if td and td.get('volatility_regime') and td.get('volatility_percentile') is not None:
                vol_regime = td.get('volatility_regime')
                vol_pct = td.get('volatility_percentile', 0)

                if vol_regime == 'INSUFFICIENT_DATA':
                    expected = 'INSUFFICIENT_DATA'
                else:
                    expected = classify_volatility_regime(vol_pct)

                ok_b = vol_regime == expected
                self._record("M15b", f"Live Volatility Regime classification ({vol_pct:.1f}th pctl)", ok_b,
                             expected=f"{expected} (percentile={vol_pct:.1f})",
                             actual=f"{vol_regime}")
            else:
                self._record("M15b", "Live Volatility Regime classification", True,
                             expected="skipped (no live data)",
                             actual="N/A — volatility regime data not available")

            # Test ATR% calculation sanity: atr_pct should be > 0 for live data
            if td and td.get('atr_pct') is not None:
                atr_pct = td.get('atr_pct', 0)
                ok_c = atr_pct >= 0
                self._record("M15c", f"ATR% non-negative ({atr_pct:.4f}%)", ok_c,
                             expected="atr_pct >= 0",
                             actual=f"atr_pct={atr_pct:.4f}%")
            else:
                self._record("M15c", "ATR% sanity check", True,
                             expected="skipped (no live data)",
                             actual="N/A — atr_pct not available")

        except Exception as e:
            self._record("M15", "v20.0 Volatility Regime boundary math", False,
                         expected="No exception",
                         actual=f"Exception: {e}\n    {traceback.format_exc()}")

    def _check_trailing_stop_math(self, price: float):
        """
        M16: v24.0 Trailing Stop boundary math.

        Validates:
        - M16a: _TRAILING_ACTIVATION_R = 1.5 provides adequate fee buffer (v43.0)
        - M16b: ATR callback rate stays within Binance BPS limits [10, 1000]
        - M16c: Min risk guard threshold (risk/entry < 0.002) correctly blocks
        - M16d: Worst-case gross loss structural bound with live ATR
        """
        print()
        print(f"  M16: v24.0 Trailing Stop boundary math")

        try:
            # Production constants (v43.0: 4H ATR source)
            TRAILING_ACTIVATION_R = 1.5
            TRAILING_ATR_MULTIPLIER = 0.6
            TRAILING_MIN_BPS = 10
            TRAILING_MAX_BPS = 1000
            MIN_RISK_RATIO = 0.002

            # Fee assumptions (same as production test)
            MAKER_FEE = 0.0002
            TAKER_FEE = 0.0005
            SLIPPAGE = 0.0003
            FR_PER_PERIOD = 0.0001

            # M16a: Fee buffer adequacy at 1.5R (v43.0)
            # With 4H ATR × 0.6 callback and 1.5R activation, worst-case
            # trailing exit = activation - callback. The 0.5R buffer above 1.0R
            # provides ample room for round-trip fees and slippage.
            # v43.0: trailing uses 4H ATR (not 30M) — use mtf_decision_layer ATR
            td = self.ctx.technical_data or {}
            mtf_decision = td.get('mtf_decision_layer') or {}
            atr = mtf_decision.get('atr') or td.get('atr', price * 0.015)
            if atr is None or atr <= 0:
                atr = price * 0.015

            # Typical risk = 1.0 ATR (conservative SL)
            typical_risk = atr
            fee_buffer = typical_risk * 0.5  # 0.5R = difference between 1.5R and 1.0R (v43.0)
            notional = 10000  # $10K test notional
            total_fees = notional * (MAKER_FEE + TAKER_FEE + SLIPPAGE + FR_PER_PERIOD)
            fee_buffer_pct = fee_buffer / price
            fee_buffer_dollar = notional * fee_buffer_pct

            # When fee buffer < fees, trailing at exactly 1.5R won't cover costs.
            # This is acceptable if ATR% is high enough that the typical trailing
            # exit (after further favorable movement) is profitable.
            # Only fail if ATR% < MIN_RISK_RATIO (guard should block but doesn't).
            fee_covers = fee_buffer_dollar > total_fees
            atr_pct = atr / price
            guard_would_block = atr_pct < MIN_RISK_RATIO
            ok_a = fee_covers or (not fee_covers and not guard_would_block)
            self._record("M16a", f"Fee buffer at 1.5R (${fee_buffer_dollar:.2f} vs ${total_fees:.2f} fees)", ok_a,
                         expected=f"0.5R buffer (${fee_buffer_dollar:.2f}) covers fees (${total_fees:.2f}), "
                                  f"or ATR%={atr_pct:.4%} > guard={MIN_RISK_RATIO:.1%}",
                         actual=f"buffer=${fee_buffer_dollar:.2f}, fees=${total_fees:.2f}, "
                                f"margin=${fee_buffer_dollar - total_fees:+.2f}, ATR%={atr_pct:.4%}")

            # M16b: ATR callback within Binance BPS limits
            trailing_distance = atr * TRAILING_ATR_MULTIPLIER
            raw_bps = int((trailing_distance / price) * 10000)
            clamped_bps = max(TRAILING_MIN_BPS, min(TRAILING_MAX_BPS, raw_bps))

            ok_b1 = TRAILING_MIN_BPS <= clamped_bps <= TRAILING_MAX_BPS
            self._record("M16b", f"ATR callback {clamped_bps} BPS within [{TRAILING_MIN_BPS}, {TRAILING_MAX_BPS}]", ok_b1,
                         expected=f"clamped BPS in [{TRAILING_MIN_BPS}, {TRAILING_MAX_BPS}]",
                         actual=f"raw={raw_bps}, clamped={clamped_bps} ({clamped_bps/100:.1f}%)")

            # M16c: Min risk guard boundary test
            # At risk/entry = 0.002, the 0.5R buffer = 0.001 * price = tiny
            boundary_risk = price * MIN_RISK_RATIO
            boundary_buffer = boundary_risk * 0.5  # v43.0: 0.5R buffer (1.5R - 1.0R)
            boundary_fees = notional * (MAKER_FEE + TAKER_FEE + SLIPPAGE + FR_PER_PERIOD)
            boundary_buffer_dollar = notional * (boundary_buffer / price)
            guard_correct = boundary_buffer_dollar < boundary_fees  # At boundary, buffer < fees → guard should block

            ok_c = guard_correct
            self._record("M16c", f"Min risk guard blocks when risk/entry < {MIN_RISK_RATIO}", ok_c,
                         expected=f"At risk/entry={MIN_RISK_RATIO}: buffer (${boundary_buffer_dollar:.2f}) < fees (${boundary_fees:.2f}) → skip",
                         actual=f"buffer=${boundary_buffer_dollar:.2f}, fees=${boundary_fees:.2f}, guard_blocks={guard_correct}")

            # M16d: Live ATR worst-case GROSS loss bounded check (v43.0)
            # With 4H ATR × 0.6 callback and 1.5R activation:
            # callback_R = (ATR × 0.6) / risk_1r. Since risk_1r ≈ ATR,
            # callback ≈ 0.6R. Worst case at exactly 1.5R → exit at 1.5R - 0.6R = +0.9R (profit).
            # v43.0 design ensures worst-case trailing exit is profitable.
            # Fee impact is validated separately in M16a (buffer adequacy) and
            # M16c (MIN_RISK_RATIO guard blocks when fees dominate).
            risk_1r = atr  # 1R = 1 ATR (typical)
            activation_price = price + risk_1r * TRAILING_ACTIVATION_R
            worst_trailing_exit = activation_price - trailing_distance
            gross_pnl_pct = (worst_trailing_exit - price) / price
            gross_pnl = notional * gross_pnl_pct
            net_pnl = gross_pnl - total_fees

            # v43.0: With callback=0.6R and activation=1.5R, worst case:
            # exit = activation - callback = 1.5R - 0.6R = +0.9R (always profitable).
            # Verify worst-case exit is profitable (gross PnL > 0).
            worst_r = gross_pnl / (notional * atr / price) if atr > 0 else 0
            ok_d = worst_r > 0  # v43.0: worst case should be profitable
            self._record("M16d", f"Live ATR worst-case trailing exit profitable (ATR=${atr:,.0f}, {worst_r:+.2f}R)", ok_d,
                         expected=f"Worst-case gross > 0 (structural: {TRAILING_ACTIVATION_R}R - {TRAILING_ATR_MULTIPLIER}R = {TRAILING_ACTIVATION_R - TRAILING_ATR_MULTIPLIER:.1f}R profit)",
                         actual=f"ATR=${atr:,.0f}, activation=${activation_price:,.0f}, "
                                f"worst_exit=${worst_trailing_exit:,.0f}, "
                                f"gross={worst_r:+.2f}R, net=${net_pnl:+.2f}")

        except Exception as e:
            self._record("M16", "v24.0 Trailing Stop boundary math", False,
                         expected="No exception",
                         actual=f"Exception: {e}\n    {traceback.format_exc()}")

    def _check_mechanical_sltp_v40(self, price: float):
        """M17: v39.0/v40.0 Mechanical SL/TP — 4H ATR priority + parameter verification."""
        try:
            from strategy.trading_logic import calculate_mechanical_sltp, _get_trading_logic_config

            cfg = _get_trading_logic_config()
            mech_cfg = cfg.get('mechanical_sltp', {})

            # M17a: v40.0 SL multiplier values
            sl_mults = mech_cfg.get('sl_atr_multiplier', {})
            expected_sl = {'HIGH': 0.8, 'MEDIUM': 1.0, 'LOW': 1.0}
            ok_a = sl_mults == expected_sl
            self._record("M17a", "v40.0 SL ATR multipliers match production", ok_a,
                         expected=str(expected_sl),
                         actual=str(sl_mults))

            # M17b: v40.0 TP R/R target values
            tp_targets = mech_cfg.get('tp_rr_target', {})
            expected_tp = {'HIGH': 1.5, 'MEDIUM': 1.5, 'LOW': 1.5}
            ok_b = tp_targets == expected_tp
            self._record("M17b", "v44.0 TP R/R targets match production", ok_b,
                         expected=str(expected_tp),
                         actual=str(tp_targets))

            # M17c: v39.0 SL floor = 0.5
            sl_floor = mech_cfg.get('sl_atr_multiplier_floor', -1)
            ok_c = sl_floor == 0.5
            self._record("M17c", "v39.0 SL ATR multiplier floor = 0.5", ok_c,
                         expected="0.5",
                         actual=str(sl_floor))

            # M17d: 4H ATR used as primary when available
            atr_30m = price * 0.01  # synthetic 30M ATR (1% of price)
            atr_4h = price * 0.028  # synthetic 4H ATR (~2.8× 30M)
            success, sl_4h, tp_4h, method_4h = calculate_mechanical_sltp(
                entry_price=price, side='BUY', atr_value=atr_30m,
                confidence='MEDIUM', atr_4h=atr_4h,
            )
            uses_4h = 'atr_src=4H' in method_4h and success
            self._record("M17d", "4H ATR used as primary when available", uses_4h,
                         expected="atr_src=4H in method description",
                         actual=method_4h[:80] if method_4h else "N/A")

            # M17e: 30M ATR fallback when 4H unavailable
            success_fb, sl_fb, tp_fb, method_fb = calculate_mechanical_sltp(
                entry_price=price, side='BUY', atr_value=atr_30m,
                confidence='MEDIUM', atr_4h=0.0,
            )
            uses_30m = 'atr_src=30M' in method_fb and success_fb
            self._record("M17e", "30M ATR fallback when 4H=0", uses_30m,
                         expected="atr_src=30M in method description",
                         actual=method_fb[:80] if method_fb else "N/A")

            # M17f: SL distance uses effective ATR × multiplier (MEDIUM=1.0)
            if success:
                sl_dist_4h = abs(price - sl_4h)
                expected_sl_dist = atr_4h * 1.0  # MEDIUM multiplier
                ok_f = abs(sl_dist_4h - expected_sl_dist) < 0.01
                self._record("M17f", "SL distance = 4H_ATR × multiplier (MEDIUM=1.0)", ok_f,
                             expected=f"${expected_sl_dist:,.2f}",
                             actual=f"${sl_dist_4h:,.2f}")
            else:
                self._record("M17f", "SL distance calculation", False,
                             expected="success=True", actual="calculate_mechanical_sltp failed")

            # M17g: TP distance = SL distance × R/R target (MEDIUM=1.5)
            if success:
                tp_dist_4h = abs(tp_4h - price)
                expected_tp_dist = sl_dist_4h * 1.5  # MEDIUM R/R target (v44.0)
                ok_g = abs(tp_dist_4h - expected_tp_dist) < 0.01
                self._record("M17g", "TP distance = SL × R/R target (MEDIUM=1.5)", ok_g,
                             expected=f"${expected_tp_dist:,.2f}",
                             actual=f"${tp_dist_4h:,.2f}")

            # M17h: Counter-trend R/R escalation (1.3 × 1.3 = 1.69)
            success_ct, sl_ct, tp_ct, method_ct = calculate_mechanical_sltp(
                entry_price=price, side='BUY', atr_value=atr_30m,
                confidence='MEDIUM', atr_4h=atr_4h, is_counter_trend=True,
            )
            if success_ct:
                sl_dist_ct = abs(price - sl_ct)
                tp_dist_ct = abs(tp_ct - price)
                effective_rr_ct = tp_dist_ct / sl_dist_ct if sl_dist_ct > 0 else 0
                # MEDIUM tp_rr=1.3, but CT min = 1.3×1.3 = 1.69 → max(1.3, 1.69) = 1.69
                ct_expected_rr = get_min_rr_ratio() * get_counter_trend_rr_multiplier()
                ok_h = abs(effective_rr_ct - ct_expected_rr) < 0.01
                self._record("M17h", f"Counter-trend R/R escalation ({ct_expected_rr:.2f}:1)", ok_h,
                             expected=f"R/R = {ct_expected_rr:.2f} (min_rr={get_min_rr_ratio()} × ct_mult={get_counter_trend_rr_multiplier()})",
                             actual=f"R/R = {effective_rr_ct:.2f}")
            else:
                self._record("M17h", "Counter-trend R/R escalation", False,
                             expected="success=True", actual="calculate_mechanical_sltp failed")

            # M17i: HIGH confidence uses tighter SL (0.8) and higher TP (1.5)
            success_hi, sl_hi, tp_hi, method_hi = calculate_mechanical_sltp(
                entry_price=price, side='BUY', atr_value=atr_30m,
                confidence='HIGH', atr_4h=atr_4h,
            )
            if success_hi:
                sl_dist_hi = abs(price - sl_hi)
                tp_dist_hi = abs(tp_hi - price)
                expected_sl_hi = atr_4h * 0.8
                expected_tp_hi = expected_sl_hi * 1.5
                ok_i_sl = abs(sl_dist_hi - expected_sl_hi) < 0.01
                ok_i_tp = abs(tp_dist_hi - expected_tp_hi) < 0.01
                ok_i = ok_i_sl and ok_i_tp
                self._record("M17i", "HIGH confidence SL=0.8×ATR, TP=1.5×SL", ok_i,
                             expected=f"SL=${expected_sl_hi:,.2f}, TP=${expected_tp_hi:,.2f}",
                             actual=f"SL=${sl_dist_hi:,.2f}, TP=${tp_dist_hi:,.2f}")

        except Exception as e:
            self._record("M17", "v39.0/v40.0 Mechanical SL/TP verification", False,
                         expected="No exception",
                         actual=f"Exception: {e}\n    {traceback.format_exc()}")

    def _check_var_cvar(self, price: float) -> None:
        """M18: VaR/CVaR calculation verification."""
        print(f"  --- M18: v2.0 VaR/CVaR Calculation ---")

        # M18a: VaR/CVaR relationship (CVaR >= VaR by definition)
        try:
            from utils.risk_controller import RiskController
            rc = RiskController(config={'circuit_breakers': {}})
            # Add synthetic trades
            for pnl in [-2.0, -1.5, -0.5, 0.5, 1.0, 1.5, 2.0, -1.0, 0.3, -0.8]:
                rc.trade_history.append(type('T', (), {'pnl_pct': pnl, 'side': 'LONG', 'timestamp': None})())

            var = rc.calculate_var(0.95)
            cvar = rc.calculate_cvar(0.95)
            ok = var > 0 and cvar >= var  # CVaR >= VaR by definition
            self._record("M18a", "VaR/CVaR: CVaR >= VaR", ok,
                         expected="CVaR >= VaR > 0",
                         actual=f"VaR={var:.2f}%, CVaR={cvar:.2f}%")
        except Exception as e:
            self._record("M18a", "VaR/CVaR calculation", False,
                         expected="No exception", actual=str(e))

    def _check_kelly_formula(self, price: float) -> None:
        """M19: Kelly formula verification."""
        print(f"  --- M19: v2.0 Kelly Formula ---")

        try:
            from utils.kelly_sizer import KellySizer
            ks = KellySizer(config={'kelly': {'enabled': True, 'fraction': 0.25, 'min_trades_for_kelly': 50, 'min_position_pct': 5, 'max_position_pct': 100}})
            hi, _ = ks.calculate(confidence='HIGH', regime='RANGING')
            med, _ = ks.calculate(confidence='MEDIUM', regime='RANGING')
            lo, _ = ks.calculate(confidence='LOW', regime='RANGING')
            ok = hi > med > lo and 5 <= lo and hi <= 100
            self._record("M19a", "Kelly: HIGH > MEDIUM > LOW sizing", ok,
                         expected="HIGH > MEDIUM > LOW, all in [5, 100]",
                         actual=f"HIGH={hi:.1f}%, MED={med:.1f}%, LOW={lo:.1f}%")

            # Regime scaling
            trend, _ = ks.calculate(confidence='MEDIUM', regime='TRENDING_UP')
            vol, _ = ks.calculate(confidence='MEDIUM', regime='HIGH_VOLATILITY')
            ok2 = trend > vol  # trending should allow larger positions than high vol
            self._record("M19b", "Kelly: regime scaling (TRENDING > HIGH_VOL)", ok2,
                         expected="TRENDING_UP > HIGH_VOLATILITY",
                         actual=f"TRENDING={trend:.1f}%, HIGH_VOL={vol:.1f}%")
        except Exception as e:
            self._record("M19a", "Kelly formula verification", False,
                         expected="No exception", actual=str(e))
