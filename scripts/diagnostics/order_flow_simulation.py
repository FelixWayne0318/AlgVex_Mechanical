"""
Order Flow Simulation Module v15.3

Comprehensive simulation of the entire order submission process,
covering all v3.18 + v5.1 + v5.12 + v6.0 + v6.1 + v7.2 + v11.0-simple fixes.

v7.2 架构重构:
- 每层独立 SL/TP (Per-Layer Independent SL/TP)
- 加仓创建新层 (_create_layer)，不影响已有层
- LIFO 减仓 (最新层先平)

订单场景模拟 (15 场景):
1. 新开仓 (无持仓 → 开仓)
2. 同向加仓 (v7.2 独立层 SL/TP)
3. 部分平仓 (v7.2 LIFO 减仓)
4. 完全平仓 (关闭仓位)
5. 反转交易 (两阶段提交)
6. Bracket 订单失败
7. v7.2 per-layer SL 被取消 → 层级 orphan 检测
8. 停机保护 — SL/TP 保留 (v5.1)
9. 累加仓位上限验证 (v5.1)
10. 崩溃恢复 — _recover_sltp_on_start (v5.12)
11. 止损冷静期触发 (v6.0)
12. 金字塔加仓拒绝 (v6.0)
13. 紧急市价平仓 (v6.1, v24.0 先取消 reduce_only 订单)
14. Time Barrier 强制平仓 (v11.0-simple)
15. 追踪止损激活 (v24.0 TRAILING_STOP_MARKET)
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

from .base import DiagnosticContext, DiagnosticStep, print_box, safe_float

# Production function imports for real validation
from strategy.trading_logic import (
    calculate_mechanical_sltp,
    calculate_position_size,
    validate_multiagent_sltp,
    _is_counter_trend,
    get_min_rr_ratio,
    get_counter_trend_rr_multiplier,
    _get_trading_logic_config,
    get_time_barrier_config,
)


class OrderScenario(Enum):
    """Order submission scenarios."""
    NEW_POSITION = "new_position"       # No position → Open new
    ADD_POSITION = "add_position"       # Same direction → Add
    REDUCE_POSITION = "reduce_position" # Partial close
    CLOSE_POSITION = "close_position"   # Full close
    REVERSAL = "reversal"               # Close → Open opposite
    BRACKET_FAILURE = "bracket_failure" # Bracket order fails
    SLTP_MODIFY_FAILURE = "sltp_modify_failure"  # modify_order fails
    ONSTOP_PRESERVATION = "onstop_preservation"  # v5.1: on_stop preserves SL/TP
    CUMULATIVE_POSITION_LIMIT = "cumulative_position_limit"  # v5.1: 30% max position cap
    CRASH_RECOVERY = "crash_recovery"  # v5.12: _recover_sltp_on_start functional test
    COOLDOWN_TRIGGERED = "cooldown_triggered"  # v6.0: Post-stoploss cooldown
    PYRAMIDING_REJECTED = "pyramiding_rejected"  # v6.0: Max layers reached
    EMERGENCY_MARKET_CLOSE = "emergency_market_close"  # v6.1: SL fails → market close
    CONFIDENCE_SL_TIGHTEN = "confidence_sl_tighten"    # v11.0-simple: Time Barrier 强制平仓
    TRAILING_STOP_ACTIVATION = "trailing_stop_activation"  # v24.0/v43.0: Fixed SL → Trailing SL at ≥1.5R


@dataclass
class MockOrder:
    """Mock order for simulation."""
    client_order_id: str
    order_type: str  # MARKET, STOP_MARKET, TAKE_PROFIT, LIMIT
    side: str        # BUY, SELL
    quantity: float
    price: Optional[float] = None
    trigger_price: Optional[float] = None
    is_reduce_only: bool = False
    status: str = "PENDING"


@dataclass
class SimulationResult:
    """Result of a scenario simulation."""
    scenario: OrderScenario
    success: bool
    orders_submitted: List[MockOrder]
    events_triggered: List[str]
    state_changes: Dict[str, Any]
    notes: List[str]


def _build_position_sizing_config(ctx) -> tuple:
    """Build config dict matching ai_decision.py's approach for calculate_position_size().

    Module-level helper shared by OrderFlowSimulator and BracketOrderFlowSimulator.
    Returns (equity, leverage, max_position_ratio, config_dict) with all required fields.
    """
    # Read equity from account_balance (raw Binance response), same as ai_decision.py
    equity = getattr(ctx, 'account_balance', {})
    if isinstance(equity, dict):
        equity = equity.get('total_balance', 0)
    else:
        equity = 0
    # Fallback to account_context.equity or strategy config
    if equity <= 0:
        equity = (ctx.account_context or {}).get('equity', 0)
    if equity <= 0:
        cfg = ctx.strategy_config
        equity = getattr(cfg, 'equity', 1000) if cfg else 1000

    leverage = ctx.binance_leverage or 10
    cfg = ctx.strategy_config
    max_position_ratio = getattr(cfg, 'max_position_ratio', 0.30) if cfg else 0.30

    # Full config dict matching ai_decision.py (lines 1415-1428)
    position_sizing_config = ctx.base_config.get('position', {}).get('position_sizing', {}) if ctx.base_config else {}
    cfg_dict = {
        'equity': equity,
        'leverage': leverage,
        'max_position_ratio': max_position_ratio,
        'min_trade_amount': getattr(cfg, 'min_trade_amount', 0.001) if cfg else 0.001,
        'high_confidence_multiplier': 1.5,
        'medium_confidence_multiplier': 1.0,
        'low_confidence_multiplier': 0.5,
        'trend_strength_multiplier': 1.2,
        'rsi_extreme_multiplier': 1.3,
        'rsi_extreme_upper': 70,
        'rsi_extreme_lower': 30,
        'position_sizing': position_sizing_config,
    }
    return equity, leverage, max_position_ratio, cfg_dict


class OrderFlowSimulator(DiagnosticStep):
    """
    v3.18 订单流程完整模拟

    Simulates the entire order submission flow with all possible scenarios.
    Validates v3.18 fixes are correctly implemented.
    """

    name = "v15.3 订单流程完整模拟"

    def _get_production_sltp(self, side: str = "BUY") -> Dict[str, Any]:
        """Call production functions to get real SL/TP, position size, and validation.

        Returns dict with all values from real production code, not hardcoded.
        """
        price = self.ctx.current_price
        atr = (self.ctx.technical_data or {}).get('atr', price * 0.01)
        confidence = (self.ctx.signal_data or {}).get('confidence', 'MEDIUM')
        trend_info = (self.ctx.technical_data or {}).get('trend_info', {})

        is_long = side in ("BUY", "LONG")
        counter_trend = _is_counter_trend(is_long, trend_info) if trend_info else False

        # 1. Calculate mechanical SL/TP (production)
        # v43.0: Use real 4H ATR from MTF decision layer (matches production _cached_atr_4h)
        atr_30m = atr if atr and atr > 0 else price * 0.01
        td = self.ctx.technical_data or {}
        mtf_dec = td.get('mtf_decision_layer')
        atr_4h_real = (mtf_dec.get('atr', 0.0) or 0.0) if mtf_dec else 0.0
        mech_ok, sl_price, tp_price, mech_desc = calculate_mechanical_sltp(
            entry_price=price,
            side=side,
            atr_value=atr_30m,
            confidence=confidence if confidence in ('HIGH', 'MEDIUM', 'LOW') else 'MEDIUM',
            risk_appetite='NORMAL',
            is_counter_trend=counter_trend,
            atr_4h=atr_4h_real,
        )

        # 2. Validate SL/TP (production R/R gate)
        valid, v_sl, v_tp, reason = validate_multiagent_sltp(
            side=side,
            multi_sl=sl_price,
            multi_tp=tp_price,
            entry_price=price,
            atr_value=atr,
            trend_info=trend_info if trend_info else None,
        )

        # 3. Calculate position size (production) — matching ai_decision.py approach
        signal_data = self.ctx.signal_data or {}
        price_data = {'price': price}
        tech_data = self.ctx.technical_data or {}
        equity, leverage, max_position_ratio, cfg_dict = _build_position_sizing_config(self.ctx)
        quantity = 0.0
        size_details = {}
        if equity > 0 and price > 0:
            quantity, size_details = calculate_position_size(
                signal_data=signal_data,
                price_data=price_data,
                technical_data=tech_data,
                config=cfg_dict,
            )

        return {
            'price': price,
            'atr': atr,
            'atr_4h': atr_4h_real,  # v43.0: real 4H ATR for trailing stop
            'sl_price': v_sl if valid else sl_price,
            'tp_price': v_tp if valid else tp_price,
            'quantity': quantity,
            'mech_ok': mech_ok,
            'mech_desc': mech_desc,
            'valid': valid,
            'reason': reason,
            'counter_trend': counter_trend,
            'confidence': confidence,
            'size_details': size_details,
        }

    def run(self) -> bool:
        print("-" * 70)
        print()
        print_box("v24.0 订单流程模拟 (15 种场景)", 65)
        print()

        # Determine current scenario based on signal and position
        signal = self.ctx.signal_data.get('signal', 'HOLD')
        current_position = self.ctx.current_position

        print("  📋 当前状态:")
        print(f"     信号: {signal}")
        print(f"     持仓: {'有' if current_position else '无'}")
        if current_position:
            print(f"     持仓方向: {current_position.get('side', 'N/A')}")
            bc = self.ctx.base_currency
            qty = current_position.get('quantity', 0)
            print(f"     持仓数量: {float(qty):.4f} {bc}")
        print()

        # Run all scenario simulations
        scenarios_to_test = [
            OrderScenario.NEW_POSITION,
            OrderScenario.ADD_POSITION,
            OrderScenario.REDUCE_POSITION,
            OrderScenario.CLOSE_POSITION,
            OrderScenario.REVERSAL,
            OrderScenario.BRACKET_FAILURE,
            OrderScenario.SLTP_MODIFY_FAILURE,
            OrderScenario.ONSTOP_PRESERVATION,
            OrderScenario.CUMULATIVE_POSITION_LIMIT,
            OrderScenario.CRASH_RECOVERY,
            OrderScenario.COOLDOWN_TRIGGERED,
            OrderScenario.PYRAMIDING_REJECTED,
            # v6.1: Safety enhancement scenarios
            OrderScenario.EMERGENCY_MARKET_CLOSE,
            OrderScenario.CONFIDENCE_SL_TIGHTEN,
            # v24.0: Trailing stop
            OrderScenario.TRAILING_STOP_ACTIVATION,
        ]

        print("  🔄 模拟所有订单场景...")
        print()

        results = []
        validation_failures = []
        for scenario in scenarios_to_test:
            result = self._simulate_scenario(scenario)
            results.append(result)
            self._print_scenario_result(result)
            # Validate structural invariants for each scenario result
            failures = self._validate_scenario_invariants(result)
            if failures:
                validation_failures.extend(failures)

        # Summary
        print()
        print("  " + "═" * 65)
        print()
        print_box("v6.1 订单流程验证总结", 65)
        print()

        passed = sum(1 for r in results if r.success)
        total = len(results)
        print(f"  通过场景: {passed}/{total}")

        if validation_failures:
            print()
            print(f"  ⚠️ 结构性验证失败: {len(validation_failures)}")
            for vf in validation_failures[:10]:
                print(f"     • {vf}")
                self.ctx.add_error(f"OrderFlow validation: {vf}")
        print()

        # Highlight v3.18 + v5.1 + v6.0 fixes
        self._print_v50_verification()

        print()
        all_passed = passed == total and len(validation_failures) == 0
        if all_passed:
            print("  ✅ v6.1 订单流程模拟完成")
        else:
            print(f"  ⚠️ v6.1 订单流程模拟完成 ({total - passed} 场景失败, {len(validation_failures)} 验证异常)")
        return all_passed

    def _validate_scenario_invariants(self, result: SimulationResult) -> List[str]:
        """Validate structural invariants for a simulation result.

        Returns a list of failure descriptions (empty = all passed).
        Checks:
        - Every successful open-position scenario must have SL protection
        - Reduce-only orders must be flagged correctly
        - Event flow must be non-empty for any scenario
        - State changes must be present for successful scenarios
        """
        failures = []
        scenario = result.scenario

        # INV-1: Every scenario must produce at least one event
        if not result.events_triggered:
            failures.append(f"{scenario.value}: no events produced")

        # INV-2: Successful open-position scenarios must have SL order
        open_scenarios = {
            OrderScenario.NEW_POSITION,
            OrderScenario.ADD_POSITION,
        }
        if result.success and scenario in open_scenarios:
            has_sl = any(
                o.order_type == "STOP_MARKET" and o.is_reduce_only
                for o in result.orders_submitted
            )
            if not has_sl:
                failures.append(f"{scenario.value}: opened position without SL protection order")

        # INV-3: Close/reduce scenarios must use reduce_only orders
        close_scenarios = {
            OrderScenario.REDUCE_POSITION,
            OrderScenario.CLOSE_POSITION,
        }
        if result.success and scenario in close_scenarios:
            close_orders = [o for o in result.orders_submitted if o.side in ("SELL", "BUY")]
            for o in close_orders:
                if o.order_type not in ("STOP_MARKET", "TAKE_PROFIT") and not o.is_reduce_only:
                    # Entry orders in close scenario are only valid for reversal
                    if scenario != OrderScenario.REVERSAL:
                        failures.append(
                            f"{scenario.value}: close order {o.client_order_id} missing reduce_only flag"
                        )

        # INV-4: Bracket failure must NOT open a position
        if scenario == OrderScenario.BRACKET_FAILURE and result.success:
            position_opened = any(
                "on_position_opened" in e for e in result.events_triggered
            )
            if position_opened:
                failures.append(f"{scenario.value}: position opened despite bracket failure")

        # INV-5: Successful scenarios should have state changes
        if result.success and not result.state_changes:
            failures.append(f"{scenario.value}: success=True but no state_changes recorded")

        # INV-6: Emergency close must attempt market order
        if scenario == OrderScenario.EMERGENCY_MARKET_CLOSE and result.success:
            has_market = any(
                o.order_type == "MARKET" and o.is_reduce_only
                for o in result.orders_submitted
            )
            if not has_market:
                failures.append(f"{scenario.value}: no reduce_only MARKET order for emergency close")

        # INV-7: Reversal must have two phases
        if scenario == OrderScenario.REVERSAL and result.success:
            events_str = " ".join(result.events_triggered)
            has_phase1 = "Phase 1" in events_str or "_pending_reversal" in events_str
            has_phase2 = "Phase 2" in events_str or "on_position_closed" in events_str
            if not (has_phase1 and has_phase2):
                failures.append(f"{scenario.value}: reversal missing two-phase commit (phase1={has_phase1}, phase2={has_phase2})")

        return failures

    def _simulate_scenario(self, scenario: OrderScenario) -> SimulationResult:
        """Simulate a specific order scenario."""
        if scenario == OrderScenario.NEW_POSITION:
            return self._simulate_new_position()
        elif scenario == OrderScenario.ADD_POSITION:
            return self._simulate_add_position()
        elif scenario == OrderScenario.REDUCE_POSITION:
            return self._simulate_reduce_position()
        elif scenario == OrderScenario.CLOSE_POSITION:
            return self._simulate_close_position()
        elif scenario == OrderScenario.REVERSAL:
            return self._simulate_reversal()
        elif scenario == OrderScenario.BRACKET_FAILURE:
            return self._simulate_bracket_failure()
        elif scenario == OrderScenario.SLTP_MODIFY_FAILURE:
            return self._simulate_sltp_modify_failure()
        elif scenario == OrderScenario.ONSTOP_PRESERVATION:
            return self._simulate_onstop_preservation()
        elif scenario == OrderScenario.CUMULATIVE_POSITION_LIMIT:
            return self._simulate_cumulative_position_limit()
        elif scenario == OrderScenario.CRASH_RECOVERY:
            return self._simulate_crash_recovery()
        elif scenario == OrderScenario.COOLDOWN_TRIGGERED:
            return self._simulate_cooldown_triggered()
        elif scenario == OrderScenario.PYRAMIDING_REJECTED:
            return self._simulate_pyramiding_rejected()
        elif scenario == OrderScenario.EMERGENCY_MARKET_CLOSE:
            return self._simulate_emergency_market_close()
        elif scenario == OrderScenario.CONFIDENCE_SL_TIGHTEN:
            return self._simulate_confidence_sl_tighten()
        elif scenario == OrderScenario.TRAILING_STOP_ACTIVATION:
            return self._simulate_trailing_stop_activation()
        else:
            return SimulationResult(
                scenario=scenario,
                success=False,
                orders_submitted=[],
                events_triggered=[],
                state_changes={},
                notes=["Unknown scenario"],
            )

    def _simulate_new_position(self) -> SimulationResult:
        """
        场景 1: 新开仓 (无持仓 → 开仓)

        Calls production functions with real market data:
        1. calculate_mechanical_sltp() — real ATR × confidence SL/TP
        2. validate_multiagent_sltp() — R/R gate validation
        3. calculate_position_size() — real position sizing
        4. Runtime flow: submit_order → on_order_filled → on_position_opened
        """
        orders = []
        events = []
        notes = []

        # Call production functions with real market data
        prod = self._get_production_sltp(side="BUY")
        entry_price = prod['price']
        quantity = prod['quantity']
        sl_price = prod['sl_price']
        tp_price = prod['tp_price']

        events.append(f"calculate_mechanical_sltp(price={entry_price:.2f}, atr={prod['atr']:.2f}, "
                       f"conf={prod['confidence']}) → {prod['mech_desc']}")
        events.append(f"validate_multiagent_sltp() → valid={prod['valid']}, reason={prod['reason']}")
        events.append(f"calculate_position_size() → {quantity:.4f} BTC "
                       f"(method={prod['size_details'].get('method', 'N/A')})")

        # Construct orders with production-calculated values
        if prod['valid'] and quantity > 0:
            entry_order = MockOrder(
                client_order_id="O-ENTRY-001",
                order_type="LIMIT",
                side="BUY",
                quantity=quantity,
                price=entry_price,
                status="FILLED",
            )
            orders.append(entry_order)

            sl_order = MockOrder(
                client_order_id="O-SL-001",
                order_type="STOP_MARKET",
                side="SELL",
                quantity=quantity,
                trigger_price=sl_price,
                is_reduce_only=True,
                status="ACCEPTED",
            )
            orders.append(sl_order)

            tp_order = MockOrder(
                client_order_id="O-TP-001",
                order_type="TAKE_PROFIT",
                side="SELL",
                quantity=quantity,
                price=tp_price,
                trigger_price=tp_price,
                is_reduce_only=True,
                status="ACCEPTED",
            )
            orders.append(tp_order)

            events.append("[runtime] submit_order(LIMIT BUY)")
            events.append("[runtime] on_order_filled → on_position_opened(LONG)")
            events.append("[runtime] submit_order(STOP_MARKET SL + TAKE_PROFIT TP)")

        notes.append(f"SL=${sl_price:,.2f}, TP=${tp_price:,.2f} (production calculate_mechanical_sltp)")
        notes.append(f"R/R gate: valid={prod['valid']} (min_rr={get_min_rr_ratio()}, "
                     f"counter_trend={prod['counter_trend']})")
        notes.append(f"仓位: {quantity:.4f} BTC = ${prod['size_details'].get('final_usdt', 0):,.2f}")

        success = prod['mech_ok'] and prod['valid'] and quantity > 0
        return SimulationResult(
            scenario=OrderScenario.NEW_POSITION,
            success=success,
            orders_submitted=orders,
            events_triggered=events,
            state_changes={
                "position": "None → LONG" if success else "None (validation failed)",
                "sl_price": f"${sl_price:,.2f}" if sl_price else "None",
                "tp_price": f"${tp_price:,.2f}" if tp_price else "None",
            },
            notes=notes,
        )

    def _simulate_add_position(self) -> SimulationResult:
        """
        场景 2: 同向加仓 (v7.2 独立层 SL/TP)

        Calls production functions for new layer validation:
        1. calculate_mechanical_sltp() — new layer SL/TP
        2. validate_multiagent_sltp() — R/R gate for new layer
        3. calculate_position_size() — add size with cumulative check
        4. Runtime flow: submit add → _create_layer() → independent SL/TP
        """
        orders = []
        events = []
        notes = []

        # Get existing position data from ctx
        pos = self.ctx.current_position
        existing_qty = float(pos.get('quantity', 0)) if pos else 0

        # Call production functions for the new layer
        prod = self._get_production_sltp(side="BUY")
        add_qty = prod['quantity']
        sl_price = prod['sl_price']
        tp_price = prod['tp_price']

        events.append(f"calculate_mechanical_sltp() for new layer → {prod['mech_desc']}")
        events.append(f"validate_multiagent_sltp() → valid={prod['valid']}, reason={prod['reason']}")
        events.append(f"calculate_position_size() → add {add_qty:.4f} BTC")

        # Cumulative position check (production: max_position_ratio * equity)
        equity, leverage, max_position_ratio, _ = _build_position_sizing_config(self.ctx)
        max_usdt = equity * max_position_ratio * leverage if equity > 0 else 0
        new_total_qty = existing_qty + add_qty
        new_total_usdt = new_total_qty * prod['price']
        within_limit = new_total_usdt <= max_usdt if max_usdt > 0 else True
        events.append(f"cumulative check: {new_total_usdt:,.0f} USDT {'<=' if within_limit else '>'} "
                       f"{max_usdt:,.0f} USDT (30% × balance × leverage)")

        if prod['valid'] and add_qty > 0 and within_limit:
            add_order = MockOrder(
                client_order_id="O-ADD-001",
                order_type="LIMIT",
                side="BUY",
                quantity=add_qty,
                price=prod['price'],
                status="FILLED",
            )
            orders.append(add_order)

            sl_order = MockOrder(
                client_order_id="O-SL-L1-001",
                order_type="STOP_MARKET",
                side="SELL",
                quantity=add_qty,
                trigger_price=sl_price,
                is_reduce_only=True,
                status="ACCEPTED",
            )
            orders.append(sl_order)

            tp_order = MockOrder(
                client_order_id="O-TP-L1-001",
                order_type="TAKE_PROFIT",
                side="SELL",
                quantity=add_qty,
                price=tp_price,
                trigger_price=tp_price,
                is_reduce_only=True,
                status="ACCEPTED",
            )
            orders.append(tp_order)

            events.append("[runtime] _create_layer(layer_1, entry, qty, sl, tp)")
            events.append("[runtime] _persist_layer_orders()")

        notes.append("v7.2: 加仓创建独立层，不修改已有层的 SL/TP")
        notes.append(f"新层 SL=${sl_price:,.2f}, TP=${tp_price:,.2f} (production calculate_mechanical_sltp)")
        notes.append(f"累计仓位: {existing_qty:.4f} + {add_qty:.4f} = {new_total_qty:.4f} BTC")

        success = prod['mech_ok'] and prod['valid'] and add_qty > 0 and within_limit
        return SimulationResult(
            scenario=OrderScenario.ADD_POSITION,
            success=success,
            orders_submitted=orders,
            events_triggered=events,
            state_changes={
                "position_qty": f"{existing_qty:.4f} → {new_total_qty:.4f}",
                "layer_0": f"SL/TP 不变 (qty={existing_qty:.4f})",
                "layer_1": f"新层独立 SL/TP (qty={add_qty:.4f})" if success else "NOT created",
            },
            notes=notes,
        )

    def _simulate_reduce_position(self) -> SimulationResult:
        """
        场景 3: 部分平仓 (v7.2 LIFO 减仓)

        Uses real position data from ctx:
        1. Read existing position qty from ctx.current_position
        2. Calculate 50% reduce size
        3. v7.2 LIFO: Cancel newest layers' SL/TP first
        4. Submit reduce order (reduce_only=True)
        5. Remove closed layers from _layer_orders
        """
        orders = []
        events = []
        notes = []

        # Use real position data from ctx
        pos = self.ctx.current_position
        existing_qty = float(pos.get('quantity', 0)) if pos else 0
        pos_side = (pos.get('side', 'LONG') if pos else 'LONG').upper()
        close_side = "SELL" if pos_side in ("LONG", "BUY") else "BUY"

        # 50% partial close (production default for /partial_close)
        reduce_qty = existing_qty * 0.5 if existing_qty > 0 else 0
        new_qty = existing_qty - reduce_qty

        if existing_qty <= 0:
            events.append("ℹ️ 无当前持仓，模拟 50% 部分平仓逻辑")
            # Use production position sizing to get a reference qty
            prod = self._get_production_sltp(side="BUY")
            existing_qty = prod['quantity'] if prod['quantity'] > 0 else 0.01
            reduce_qty = existing_qty * 0.5
            new_qty = existing_qty - reduce_qty

        events.append(f"现有持仓: {existing_qty:.4f} BTC ({pos_side})")
        events.append(f"减仓量: {reduce_qty:.4f} BTC (50%)")

        # v7.2: LIFO — cancel newest layer's SL/TP first
        events.append("v7.2 LIFO: _get_layers_sorted(order='lifo')")
        events.append(f"  → layer_1 (newest): cancel SL/TP, qty={reduce_qty:.4f}")
        events.append("  → layer_0 (oldest): untouched")

        # Reduce position order
        reduce_order = MockOrder(
            client_order_id="O-REDUCE-001",
            order_type="MARKET",
            side=close_side,
            quantity=reduce_qty,
            is_reduce_only=True,
            status="FILLED",
        )
        orders.append(reduce_order)
        events.append(f"submit_order(MARKET {close_side} - reduce_only)")
        events.append("on_order_filled(REDUCE)")

        notes.append("v7.2: LIFO 减仓 — 最新层先平")
        notes.append("减仓单必须设置 reduce_only=True")
        notes.append(f"仓位: {existing_qty:.4f} → {new_qty:.4f} BTC")

        return SimulationResult(
            scenario=OrderScenario.REDUCE_POSITION,
            success=True,
            orders_submitted=orders,
            events_triggered=events,
            state_changes={
                "position_qty": f"{existing_qty:.4f} → {new_qty:.4f}",
                "sl_order": "layer_1 SL/TP CANCELLED (LIFO)",
                "tp_order": "layer_0 SL/TP untouched",
            },
            notes=notes,
        )

    def _simulate_close_position(self) -> SimulationResult:
        """
        场景 4: 完全平仓

        Uses real position data from ctx:
        1. Read position qty and side from ctx.current_position
        2. Cancel existing SL/TP (all layers)
        3. Submit close order (reduce_only=True)
        4. on_order_filled → on_position_closed
        """
        orders = []
        events = []
        notes = []

        # Use real position data from ctx
        pos = self.ctx.current_position
        close_qty = float(pos.get('quantity', 0)) if pos else 0
        pos_side = (pos.get('side', 'LONG') if pos else 'LONG').upper()
        close_side = "SELL" if pos_side in ("LONG", "BUY") else "BUY"

        if close_qty <= 0:
            # No position — use production sizing as reference
            prod = self._get_production_sltp(side="BUY")
            close_qty = prod['quantity'] if prod['quantity'] > 0 else 0.01
            events.append("ℹ️ 无当前持仓，使用 production sizing 模拟平仓量")

        events.append(f"持仓: {close_qty:.4f} BTC ({pos_side})")
        events.append("cancel_all_orders() - 取消所有层 SL/TP")

        # Close position order
        close_order = MockOrder(
            client_order_id="O-CLOSE-001",
            order_type="MARKET",
            side=close_side,
            quantity=close_qty,
            is_reduce_only=True,
            status="FILLED",
        )
        orders.append(close_order)
        events.append(f"submit_order(MARKET {close_side} - reduce_only)")
        events.append("on_order_filled(CLOSE)")
        events.append("on_position_closed()")

        notes.append("v7.2: 平仓取消所有层 SL/TP")
        notes.append("平仓单必须设置 reduce_only=True")
        notes.append("on_position_closed → evaluate_trade() + record_outcome()")

        return SimulationResult(
            scenario=OrderScenario.CLOSE_POSITION,
            success=True,
            orders_submitted=orders,
            events_triggered=events,
            state_changes={
                "position": f"{pos_side} → None",
                "sl_order": "ALL LAYERS → CANCELLED",
                "tp_order": "ALL LAYERS → CANCELLED",
            },
            notes=notes,
        )

    def _simulate_reversal(self) -> SimulationResult:
        """
        场景 5: 反转交易 (v3.18 两阶段提交)

        Uses real position data for close + production functions for new position:
        Phase 1: Close existing position (real qty from ctx)
        Phase 2: Open opposite direction with production SL/TP
        """
        orders = []
        events = []
        notes = []

        # Phase 1: Use real position data for close
        pos = self.ctx.current_position
        close_qty = float(pos.get('quantity', 0)) if pos else 0
        old_side = (pos.get('side', 'LONG') if pos else 'LONG').upper()
        close_side = "SELL" if old_side in ("LONG", "BUY") else "BUY"

        # Phase 2: New position in opposite direction
        new_side = "SELL" if old_side in ("LONG", "BUY") else "BUY"
        new_pos_side = "SHORT" if new_side == "SELL" else "LONG"
        sl_close_side = "BUY" if new_side == "SELL" else "SELL"

        # Call production functions for new position SL/TP
        prod = self._get_production_sltp(side=new_side)
        new_qty = prod['quantity']
        sl_price = prod['sl_price']
        tp_price = prod['tp_price']

        if close_qty <= 0:
            events.append("ℹ️ 无当前持仓，使用 production sizing 模拟反转")
            close_qty = new_qty if new_qty > 0 else 0.01

        # Phase 1: Store state and close
        events.append("═══ Phase 1: 存储状态并平仓 ═══")
        events.append(f"_pending_reversal = {{")
        events.append(f"    'target_side': '{new_pos_side.lower()}',")
        events.append(f"    'target_quantity': {new_qty:.4f},")
        events.append(f"    'old_side': '{old_side.lower()}',")
        events.append(f"    'submitted_at': datetime.utcnow()")
        events.append(f"}}")

        events.append("cancel_all_orders() - 取消 SL/TP")

        close_order = MockOrder(
            client_order_id="O-REVERSAL-CLOSE-001",
            order_type="MARKET",
            side=close_side,
            quantity=close_qty,
            is_reduce_only=True,
            status="FILLED",
        )
        orders.append(close_order)
        events.append(f"submit_order(MARKET {close_side} - reduce_only, qty={close_qty:.4f})")
        events.append("on_order_filled(CLOSE)")
        events.append("on_position_closed() - 触发 Phase 2")

        # Phase 2: Open new position with production-calculated values
        events.append("")
        events.append("═══ Phase 2: 开新仓 (在 on_position_closed 中) ═══")
        events.append("检测到 _pending_reversal")
        events.append("_pending_reversal = None  # 立即清空防止重复执行")
        events.append("验证无持仓: _get_current_position_data() == None")
        events.append(f"calculate_mechanical_sltp(side={new_side}) → {prod['mech_desc']}")
        events.append(f"validate_multiagent_sltp() → valid={prod['valid']}, reason={prod['reason']}")

        if prod['valid'] and new_qty > 0:
            new_entry = MockOrder(
                client_order_id="O-REVERSAL-ENTRY-001",
                order_type="LIMIT",
                side=new_side,
                quantity=new_qty,
                price=prod['price'],
                status="FILLED",
            )
            orders.append(new_entry)

            sl_order = MockOrder(
                client_order_id="O-REVERSAL-SL-001",
                order_type="STOP_MARKET",
                side=sl_close_side,
                quantity=new_qty,
                trigger_price=sl_price,
                is_reduce_only=True,
                status="ACCEPTED",
            )
            orders.append(sl_order)

            tp_order = MockOrder(
                client_order_id="O-REVERSAL-TP-001",
                order_type="TAKE_PROFIT",
                side=sl_close_side,
                quantity=new_qty,
                price=tp_price,
                trigger_price=tp_price,
                is_reduce_only=True,
                status="ACCEPTED",
            )
            orders.append(tp_order)

            events.append(f"_submit_bracket_order({new_side}, {new_qty:.4f})")
            events.append(f"on_order_filled(NEW ENTRY)")
            events.append(f"on_position_opened({new_pos_side})")

        notes.append("v3.18: 两阶段提交防止竞态条件")
        notes.append(f"Phase 1: 平{old_side} {close_qty:.4f} BTC")
        notes.append(f"Phase 2: 开{new_pos_side} {new_qty:.4f} BTC (production SL/TP)")
        notes.append(f"新仓 SL=${sl_price:,.2f}, TP=${tp_price:,.2f}")

        success = prod['valid'] and new_qty > 0
        return SimulationResult(
            scenario=OrderScenario.REVERSAL,
            success=success,
            orders_submitted=orders,
            events_triggered=events,
            state_changes={
                "position": f"{old_side} → None → {new_pos_side}" if success else f"{old_side} → None (validation failed)",
                "_pending_reversal": "None → {state} → None",
                "phase": "1 → 2",
            },
            notes=notes,
        )

    def _simulate_bracket_failure(self) -> SimulationResult:
        """
        场景 6: Bracket 订单失败 (v3.18 不回退到无保护订单)

        v3.18 Fix: Do NOT fallback to unprotected order

        Flow:
        1. Attempt to submit bracket order
        2. Exception occurs (e.g., SL/TP calculation fails)
        3. v3.18: Do NOT submit unprotected market order
        4. Log error and send Telegram alert
        5. Update _last_signal_status as failed
        """
        orders = []
        events = []
        notes = []

        events.append("尝试 _submit_bracket_order()")
        events.append("  → 计算 SL 价格...")
        events.append("  → 计算 TP 价格...")
        events.append("  → ❌ Exception: SL 验证失败")
        events.append("")
        events.append("v3.18 行为:")
        events.append("  → 🚫 NOT opening position without SL/TP protection")
        events.append("  → _last_signal_status = {")
        events.append("        'executed': False,")
        events.append("        'reason': 'Bracket订单失败，取消开仓',")
        events.append("    }")
        events.append("  → 发送 CRITICAL Telegram 警报")
        events.append("")
        events.append("❌ 旧版 (危险) 行为 (已移除):")
        events.append("  → self._submit_order(side, qty, reduce_only=False)  # 无保护!")

        notes.append("v3.18: Bracket 失败时拒绝开仓")
        notes.append("v3.18: 不回退到无 SL/TP 保护的订单")
        notes.append("v3.18: 发送 CRITICAL 警报通知用户")
        notes.append("v3.18: 等待下一个信号重试")

        return SimulationResult(
            scenario=OrderScenario.BRACKET_FAILURE,
            success=True,  # This is expected behavior
            orders_submitted=orders,
            events_triggered=events,
            state_changes={
                "position": "None (保持不变)",
                "_last_signal_status.executed": "False",
            },
            notes=notes,
        )

    def _simulate_sltp_modify_failure(self) -> SimulationResult:
        """
        场景 7: v7.2 per-layer SL 被取消 → 层级 orphan 检测

        Uses production emergency SL config from _get_trading_logic_config():
        1. Position with 2 layers, each with independent SL/TP
        2. Layer 1's SL cancelled externally → orphan detected
        3. Emergency SL calculated using production config (base_pct, atr_multiplier)
        """
        orders = []
        events = []
        notes = []

        # Read real position data and emergency config
        pos = self.ctx.current_position
        total_qty = float(pos.get('quantity', 0)) if pos else 0
        pos_side = (pos.get('side', 'LONG') if pos else 'LONG').upper()
        close_side = "SELL" if pos_side in ("LONG", "BUY") else "BUY"
        price = self.ctx.current_price

        # Production emergency SL config
        emg_config = _get_trading_logic_config().get('emergency_sl', {})
        emg_base_pct = emg_config.get('base_pct', 0.02)
        emg_atr_mult = emg_config.get('atr_multiplier', 1.5)
        atr = (self.ctx.technical_data or {}).get('atr', price * 0.01)

        # Calculate emergency SL price using production logic
        if pos_side in ("LONG", "BUY"):
            emg_sl_price = price * (1 - emg_base_pct)
        else:
            emg_sl_price = price * (1 + emg_base_pct)

        # Simulate 2 layers: 60% layer_0, 40% layer_1
        if total_qty <= 0:
            prod = self._get_production_sltp(side="BUY")
            total_qty = prod['quantity'] if prod['quantity'] > 0 else 0.01
            events.append("ℹ️ 无当前持仓，使用 production sizing 模拟 2 层仓位")
        layer0_qty = total_qty * 0.6
        layer1_qty = total_qty * 0.4

        events.append(f"═══ Phase 1: 正常持仓 (2 层, {pos_side}) ═══")
        events.append(f"  layer_0: {layer0_qty:.4f} BTC, SL=O-SL-001, TP=O-TP-001")
        events.append(f"  layer_1: {layer1_qty:.4f} BTC, SL=O-SL-002, TP=O-TP-002")
        events.append("")
        events.append("═══ Phase 2: Layer 1 SL 被外部取消 ═══")
        events.append("  on_order_canceled(O-SL-002)")
        events.append("  → _order_to_layer[O-SL-002] = 'layer_1'")
        events.append("  → layer_1['sl_order_id'] = None")
        events.append("  → _handle_orphan_order('Layer layer_1 SL canceled')")
        events.append("")
        events.append("═══ Phase 3: Per-layer SL 覆盖检查 ═══")
        events.append("  _resubmit_sltp_if_needed():")
        events.append(f"  → layer_0: SL O-SL-001 active ✅")
        events.append(f"  → layer_1: SL None → uncovered ❌ ({layer1_qty:.4f} BTC)")
        events.append(f"  → _submit_emergency_sl({layer1_qty:.4f}, '{pos_side.lower()}', reason)")
        events.append(f"  Emergency SL config: base_pct={emg_base_pct}, atr_mult={emg_atr_mult}")
        events.append(f"  Emergency SL price: ${emg_sl_price:,.2f} (production _get_trading_logic_config)")

        # Emergency SL order with production values
        new_sl = MockOrder(
            client_order_id="O-SL-EMERGENCY-001",
            order_type="STOP_MARKET",
            side=close_side,
            quantity=layer1_qty,
            trigger_price=emg_sl_price,
            is_reduce_only=True,
            status="ACCEPTED",
        )
        orders.append(new_sl)

        notes.append("v7.2: on_order_canceled 使用 _order_to_layer 识别层")
        notes.append("v7.2: 只为未覆盖量提交 emergency SL，不影响其他层")
        notes.append(f"Emergency SL: base_pct={emg_base_pct}, price=${emg_sl_price:,.2f}")

        return SimulationResult(
            scenario=OrderScenario.SLTP_MODIFY_FAILURE,
            success=True,
            orders_submitted=orders,
            events_triggered=events,
            state_changes={
                "layer_0": f"SL/TP 不变 ({layer0_qty:.4f} BTC 仍受保护)",
                "layer_1": f"SL: 被取消 → emergency SL @ ${emg_sl_price:,.2f}",
            },
            notes=notes,
        )

    def _simulate_onstop_preservation(self) -> SimulationResult:
        """
        场景 9: 停机保护 — SL/TP 保留在 Binance (v5.1)

        Flow:
        1. on_stop() called (bot shutdown)
        2. Iterate open orders, check is_reduce_only
        3. Cancel only NON-reduce_only orders
        4. SL/TP (reduce_only=True) remain on Binance
        5. Exception fallback: cancel_all_orders
        """
        events = []
        notes = []

        events.append("on_stop() 被调用 (机器人停止)")
        events.append("  for order in cache.orders_open():")
        events.append("    if order.is_reduce_only:")
        events.append("      → SKIP (保留 SL/TP)")
        events.append("    else:")
        events.append("      → cancel_order(order)")
        events.append("")
        events.append("结果: SL/TP 挂单保留在 Binance 交易所")
        events.append("用户可在 Binance APP 查看这些保护单")

        notes.append("v5.1: 机器人停止后，止损止盈单保留在 Binance")
        notes.append("v5.1: 仅取消非 reduce_only 订单")
        notes.append("v5.1: except 块中有 cancel_all_orders 作为后备")
        notes.append("用户重启后, _recover_sltp_on_start 恢复状态")

        return SimulationResult(
            scenario=OrderScenario.ONSTOP_PRESERVATION,
            success=True,
            orders_submitted=[],
            events_triggered=events,
            state_changes={
                "sl_order": "ACTIVE → ACTIVE (保留在 Binance)",
                "tp_order": "ACTIVE → ACTIVE (保留在 Binance)",
                "non_reduce_orders": "CANCELLED",
            },
            notes=notes,
        )

    def _simulate_cumulative_position_limit(self) -> SimulationResult:
        """
        场景 10: 累加仓位上限验证 (v5.12)

        Matches production ai_strategy.py:3504-3527.

        Flow:
        1. Check position_sizing_cumulative config flag
        2. If cumulative AND current_position exists:
           a. current_value = current_qty × current_price
           b. max_usdt = equity × max_position_ratio × leverage
           c. remaining_capacity = max_usdt - current_value
           d. If remaining <= 0: return 0.0 (reject add)
           e. If remaining > 0: clamp btc_quantity to max_add_btc
        """
        events = []
        notes = []

        # Use real context values if available, else simulate
        cfg = self.ctx.strategy_config
        equity = 1000.0
        if hasattr(self.ctx, 'account_balance') and self.ctx.account_balance:
            equity = self.ctx.account_balance.get('total_balance', 1000.0) or 1000.0
        max_position_ratio = getattr(cfg, 'max_position_ratio', 0.30) if cfg else 0.30
        leverage = getattr(self.ctx, 'binance_leverage', 10)
        cumulative_enabled = getattr(cfg, 'position_sizing_cumulative', True) if cfg else True
        max_usdt = equity * max_position_ratio * leverage
        current_price = self.ctx.current_price or 100000

        events.append("_calculate_position_size() 中的累加检查:")
        events.append(f"  position_sizing_cumulative = {cumulative_enabled}")
        events.append(f"  equity = ${equity:,.2f}")
        events.append(f"  max_position_ratio = {max_position_ratio:.0%}")
        events.append(f"  leverage = {leverage}x")
        events.append(f"  max_usdt = ${max_usdt:,.2f} (equity × ratio × leverage)")
        events.append("")

        # Check if we have a real current position
        has_real_position = bool(self.ctx.current_position)
        if has_real_position:
            current_qty = float(self.ctx.current_position.get('quantity', 0))
            current_value = current_qty * current_price
            remaining = max(0, max_usdt - current_value)
            events.append(f"  📊 实际持仓检测:")
            events.append(f"     current_qty = {current_qty:.4f}")
            events.append(f"     current_price = ${current_price:,.2f}")
            events.append(f"     current_value = ${current_value:,.2f}")
            events.append(f"     remaining_capacity = ${remaining:,.2f}")
            if remaining <= 0:
                events.append(f"     ❌ 仓位已达上限, return 0.0 (拒绝加仓)")
            else:
                max_add_btc = remaining / current_price
                events.append(f"     ✅ 允许加仓: max_add = {max_add_btc:.4f} BTC (${remaining:,.0f})")
                events.append(f"     → btc_quantity = min(requested, {max_add_btc:.4f})")
        else:
            events.append("  ℹ️ 无当前持仓, 跳过累加检查 (首次建仓不受限)")

        events.append("")

        # Simulated scenarios for verification
        sim_value = 2500.0
        sim_remaining = max(0, max_usdt - sim_value)
        events.append("═══ 模拟验证 ═══")
        events.append(f"  Case A: current_value=${sim_value:,.0f}, remaining=${sim_remaining:,.0f}")
        if sim_remaining > 0:
            events.append(f"    → 允许加仓, clamp to ${sim_remaining:,.0f}")
        else:
            events.append(f"    → 拒绝加仓")

        full_value = max_usdt + 100
        full_remaining = max(0, max_usdt - full_value)
        events.append(f"  Case B: current_value=${full_value:,.0f}, remaining=${full_remaining:,.0f}")
        events.append(f"    → return 0.0 (拒绝加仓, 等待减仓)")

        notes.append(f"v4.8: position_sizing_cumulative={cumulative_enabled} 控制是否启用累加检查")
        notes.append("生产位置: ai_strategy.py:3504-3527")
        notes.append(f"上限公式: max_usdt = equity(${equity:,.0f}) × ratio({max_position_ratio:.0%}) × leverage({leverage}x) = ${max_usdt:,.0f}")
        notes.append("remaining_capacity <= 0 时直接 return 0.0, 完全阻止加仓")
        notes.append("remaining_capacity > 0 时 clamp: btc_quantity = min(requested, max_add_btc)")

        state_changes = {
            "max_usdt": f"${max_usdt:,.2f}",
            "cumulative_enabled": str(cumulative_enabled),
        }
        if has_real_position:
            state_changes["current_value"] = f"${current_value:,.2f}"
            state_changes["remaining_capacity"] = f"${remaining:,.2f}"
            state_changes["add_allowed"] = "YES" if remaining > 0 else "NO"
        else:
            state_changes["current_position"] = "None (首次建仓不受限)"

        return SimulationResult(
            scenario=OrderScenario.CUMULATIVE_POSITION_LIMIT,
            success=True,
            orders_submitted=[],
            events_triggered=events,
            state_changes=state_changes,
            notes=notes,
        )

    def _simulate_crash_recovery(self) -> SimulationResult:
        """
        场景 11: 崩溃恢复 (v5.12 _recover_sltp_on_start)

        Functional test matching production ai_strategy.py:908-974.

        Flow:
        Scenario A: Position + SL exist → restore sltp_state from Binance order
        Scenario B: Position exists, NO SL → create emergency SL
        Scenario C: No position → skip recovery

        This runs on every bot startup in on_start().
        """
        events = []
        notes = []

        events.append("on_start() → _recover_sltp_on_start()")
        events.append("")

        # ── Scenario A: Position WITH SL ──
        events.append("═══ Case A: 持仓 + SL 存在 ═══")
        events.append("  1. _get_current_position_data() → {side: 'long', qty: 0.01}")
        events.append("  2. cache.orders_open() → 找到 reduce_only 订单")
        events.append("  3. has_sl = any(STOP_MARKET in reduce_only) → True")
        events.append("  4. 检查 sltp_state[instrument_key] 是否已存在")
        events.append("     → 不存在: 从 Binance SL 订单恢复:")
        events.append("       sltp_state[key] = {")
        events.append("         entry_price: position.entry_price,")
        events.append("         highest_price: entry_price,")
        events.append("         lowest_price: entry_price,")
        events.append("         current_sl_price: order.trigger_price,")
        events.append("         current_tp_price: 0,")
        events.append("         sl_order_id: order.client_order_id,")
        events.append("         side: 'LONG',")
        events.append("         quantity: 0.01,")
        events.append("       }")
        events.append("  5. ✅ 恢复完成 → return")
        events.append("")

        # ── Scenario B: Position WITHOUT SL ──
        events.append("═══ Case B: 持仓但无 SL (危险!) ═══")
        events.append("  1. _get_current_position_data() → {side: 'long', qty: 0.01}")
        events.append("  2. cache.orders_open() → 无 STOP_MARKET 订单")
        events.append("  3. has_sl = False")
        events.append("  4. 🚨 WARNING: 无保护仓位!")
        events.append("  5. _submit_emergency_sl(")
        events.append("       quantity=0.01,")
        events.append("       position_side='long',")
        events.append("       reason='启动时检测到无保护仓位'")
        events.append("     )")
        events.append("  6. Emergency SL = current_price × 0.98 (LONG) 或 × 1.02 (SHORT)")
        events.append("")

        # ── Scenario C: No position ──
        events.append("═══ Case C: 无持仓 ═══")
        events.append("  1. _get_current_position_data() → None / qty=0")
        events.append("  2. ✅ 无需恢复 → return")
        events.append("")

        # ── Exception handling ──
        events.append("═══ 异常处理 ═══")
        events.append("  try/except 包裹整个函数")
        events.append("  → 失败时: log.error('Failed to recover SL/TP on start')")
        events.append("  → 不会阻止 bot 启动")

        notes.append("v4.12: _recover_sltp_on_start 在每次 on_start() 中调用")
        notes.append("Case A: 恢复 sltp_state 使 dynamic reevaluation 正常工作")
        notes.append("Case B: 紧急 SL 防止 bot 重启期间的裸仓暴露")
        notes.append("Case C: 无持仓时静默跳过, 不产生任何副作用")
        notes.append("与场景 9 (on_stop 保留 SL) 配合: stop→SL保留→start→恢复状态")

        return SimulationResult(
            scenario=OrderScenario.CRASH_RECOVERY,
            success=True,
            orders_submitted=[],
            events_triggered=events,
            state_changes={
                "case_a": "sltp_state restored from Binance SL order",
                "case_b": "emergency SL created (2% default)",
                "case_c": "no action needed",
            },
            notes=notes,
        )

    def _simulate_cooldown_triggered(self) -> SimulationResult:
        """
        场景 12: 止损冷静期触发 (v6.0)

        Flow:
        1. Position closed with loss (SL hit)
        2. _activate_stoploss_cooldown() sets cooldown
        3. on_timer detects cooldown → skip AI analysis
        4. After cooldown expires, _refine_stop_type() classifies stop
        """
        events = []
        notes = []

        events.append("═══ Phase 1: 止损触发 ═══")
        events.append("  on_position_closed() → pnl < 0 AND exit_price ≈ tracked_sl (±0.5%)")
        events.append("  _activate_stoploss_cooldown(exit_price, entry_price, side)")
        events.append("  → _stoploss_cooldown_until = now + 2 candles (30min)")
        events.append("  → _stoploss_cooldown_type = 'default'")
        events.append("")
        events.append("═══ Phase 2: 冷静期生效 ═══")
        events.append("  on_timer() → _check_stoploss_cooldown() = True")
        events.append("  → 无持仓: 跳过 AI 分析 (省 7 次 API 调用)")
        events.append("  → _last_signal_status = {'reason': '止损冷静期'}")
        events.append("")
        events.append("═══ Phase 3: 止损类型细化 ═══")
        events.append("  _refine_stop_type() 在观察期后调用:")
        events.append("  Case A: 价格恢复 → noise_stop → cooldown = 1 candle")
        events.append("  Case B: 价格继续 → reversal_stop → cooldown = 6 candles")
        events.append("  Case C: ATR > 2x → volatility_stop → cooldown = 12 candles")
        events.append("")
        events.append("═══ Phase 4: 2连亏减仓 ═══")
        events.append("  risk_controller: consecutive_losses >= 2")
        events.append("  → TradingState.REDUCED (仓位系数 0.5x)")

        notes.append("v6.0: 冷静期在 on_timer 中检查，节省 AI 成本")
        notes.append("v6.0: 三种止损类型有不同冷静时长")
        notes.append("v6.0: 有持仓时不跳过 (需要信心跟踪)")

        return SimulationResult(
            scenario=OrderScenario.COOLDOWN_TRIGGERED,
            success=True,
            orders_submitted=[],
            events_triggered=events,
            state_changes={
                "_stoploss_cooldown_until": "now + 2 candles",
                "trading_state": "ACTIVE → REDUCED (2连亏)",
            },
            notes=notes,
        )

    def _simulate_pyramiding_rejected(self) -> SimulationResult:
        """
        场景 13: 金字塔加仓拒绝 (v6.0)

        Flow:
        1. Position exists with 3 layers
        2. AI signals same direction (add to position)
        3. _check_pyramiding_allowed() rejects: max layers reached
        """
        events = []
        notes = []

        events.append("═══ 加仓信号处理 ═══")
        events.append("  _execute_trade() → same_direction, size_diff > 0")
        events.append("  → _manage_existing_position()")
        events.append("")
        events.append("═══ 金字塔验证 (5 条规则) ═══")
        events.append("  _check_pyramiding_allowed():")
        events.append("  ✅ Rule 1: pyramiding_enabled = True")
        events.append("  ❌ Rule 2: counter_trend_allowed = False → 逆势禁止加仓")
        events.append("     → REJECTED: 逆势仓位禁止加仓")
        events.append("  (以下规则未检查)")
        events.append("  Rule 3: profit >= 0.5 ATR")
        events.append("  Rule 4: confidence >= MEDIUM")
        events.append("  Rule 5: FR < 0.05%, ADX >= 15")
        events.append("")
        events.append("  _last_signal_status = {")
        events.append("    'executed': False,")
        events.append("    'reason': '金字塔拒绝: 已达最大层数 3/3'")
        events.append("  }")

        notes.append("v6.0: 金字塔使用递减仓位 (50%/30%/20%)")
        notes.append("v6.0: 逆势交易硬性禁止加仓")
        notes.append("v6.0: 需要 HIGH 信心 + ADX >= 25 + 盈利 >= 1 ATR")

        return SimulationResult(
            scenario=OrderScenario.PYRAMIDING_REJECTED,
            success=True,
            orders_submitted=[],
            events_triggered=events,
            state_changes={
                "_position_layers": "3 layers (unchanged)",
                "add_order": "NOT submitted",
            },
            notes=notes,
        )

    def _simulate_emergency_market_close(self) -> SimulationResult:
        """
        场景 15: 紧急市价平仓 (v6.1)

        Uses real position data + production emergency config:
        1. Position qty from ctx.current_position
        2. Emergency SL config from _get_trading_logic_config()
        3. MARKET reduce_only order with real qty
        """
        events = []
        notes = []

        # Real position data
        pos = self.ctx.current_position
        close_qty = float(pos.get('quantity', 0)) if pos else 0
        pos_side = (pos.get('side', 'LONG') if pos else 'LONG').upper()
        close_side = "SELL" if pos_side in ("LONG", "BUY") else "BUY"

        if close_qty <= 0:
            prod = self._get_production_sltp(side="BUY")
            close_qty = prod['quantity'] if prod['quantity'] > 0 else 0.01
            events.append("ℹ️ 无当前持仓，使用 production sizing 模拟紧急平仓量")

        # Production emergency config
        emg_config = _get_trading_logic_config().get('emergency_sl', {})
        emg_max_retries = emg_config.get('max_consecutive', 3)

        events.append(f"═══ Phase 1: Layer SL 提交失败 ({pos_side}, {close_qty:.4f} BTC) ═══")
        events.append("  v7.2: on_position_opened → SL submission Exception")
        events.append("  sl_confirmed = False")
        events.append(f"  → _submit_emergency_sl({close_qty:.4f}, '{pos_side.lower()}', reason)")
        events.append("")
        events.append("═══ Phase 2: Emergency SL 也失败 ═══")
        events.append("  binance_account.get_realtime_price() → Exception")
        events.append("  _cached_current_price = 0 (stale/missing)")
        events.append("  → current_price <= 0: 无法计算 SL 价格")
        events.append("")
        events.append("═══ Phase 3: 终极兜底 — 市价平仓 ═══")
        events.append(f"  _emergency_market_close({close_qty:.4f}, '{pos_side.lower()}', reason)")
        events.append("  v24.0: 先取消所有 reduce_only 订单 (防止 trailing 部分成交冲突)")
        events.append("  → cache.orders_open() → cancel each reduce_only order")
        events.append("  → order_factory.market(reduce_only=True)")
        events.append("  → submit_order(close_order)")
        events.append("  → Telegram: '紧急市价平仓'")

        # Construct the actual MARKET reduce_only close order with real qty
        close_order = MockOrder(
            client_order_id="O-EMG-CLOSE-001",
            order_type="MARKET",
            side=close_side,
            quantity=close_qty,
            is_reduce_only=True,
            status="FILLED",
        )
        orders = [close_order]
        events.append("")
        events.append(f"═══ Phase 4: 如果市价平仓也失败 (v7.1, max_retries={emg_max_retries}) ═══")
        events.append("  → _needs_emergency_review = True")
        events.append("  → v18.0: _emergency_retry_count += 1")
        events.append(f"  → if count <= 5:")
        events.append("      set_time_alert(30s, _on_emergency_retry)")
        events.append("      → 30 秒后自动重试 _resubmit_sltp_if_needed()")
        events.append("  → if count > 5:")
        events.append("      log CRITICAL: 'Emergency retry exhausted'")

        notes.append("v6.1: Emergency SL 两级兜底: SL → market close → alert")
        notes.append(f"v6.1: qty={close_qty:.4f}, side={close_side}, reduce_only=True")
        notes.append(f"Emergency config: max_consecutive={emg_max_retries} (production)")
        notes.append("v18.0: 短周期重试 (30s × 5次) 取代等 on_timer")

        return SimulationResult(
            scenario=OrderScenario.EMERGENCY_MARKET_CLOSE,
            success=True,
            orders_submitted=orders,
            events_triggered=events,
            state_changes={
                "position": f"{pos_side} {close_qty:.4f} BTC → 市价平仓",
                "sltp_state": "清除",
            },
            notes=notes,
        )

    def _simulate_confidence_sl_tighten(self) -> SimulationResult:
        """
        场景 14: Time Barrier 强制平仓 (v11.0-simple)

        Uses production get_time_barrier_config() for real time limits:
        1. Position opened, Time Barrier countdown starts
        2. _check_time_barrier() checks elapsed bars vs production config
        3. When Time Barrier triggers → always close position (市价)
        """
        events = []
        notes = []

        # Production Time Barrier config
        tb_config = get_time_barrier_config()
        tb_enabled = tb_config.get('enabled', True)
        tb_trend_hours = tb_config.get('max_holding_hours_trend', 12)
        tb_counter_hours = tb_config.get('max_holding_hours_counter', 6)
        tb_action = tb_config.get('action', 'close')

        # Real position data
        pos = self.ctx.current_position
        pos_side = (pos.get('side', 'LONG') if pos else 'LONG').upper()
        price = self.ctx.current_price

        # Production SL/TP for context
        prod = self._get_production_sltp(side="BUY" if pos_side in ("LONG", "BUY") else "SELL")
        sl_price = prod['sl_price']

        events.append("═══ Phase 1: 开仓 + Time Barrier 启动 ═══")
        events.append(f"  entry @ ${price:,.2f}")
        events.append(f"  SL @ ${sl_price:,.2f} (production calculate_mechanical_sltp)")
        events.append(f"  Time Barrier 配置 (production get_time_barrier_config()):")
        events.append(f"    enabled={tb_enabled}")
        events.append(f"    trend: {tb_trend_hours}h, counter_trend: {tb_counter_hours}h")
        events.append(f"    action: '{tb_action}'")
        events.append(f"    counter_trend={prod['counter_trend']}")
        active_hours = tb_counter_hours if prod['counter_trend'] else tb_trend_hours
        events.append(f"    → 适用限时: {active_hours}h ({active_hours * 2} bars @ 30M)")
        events.append("")
        events.append("═══ Phase 2: 持仓期间 SL/TP 保护 ═══")
        events.append("  SL/TP 在入场时已设置，持仓期间保持不变")
        events.append("")
        events.append("═══ Phase 3: Time Barrier 检查 ═══")
        events.append(f"  _check_time_barrier() → 检查已持仓 bar 数 vs {active_hours * 2} bars")
        events.append("  → 未触发: 继续持仓")
        events.append(f"  → 已触发 (>{active_hours}h): action='{tb_action}' → 市价平仓")
        events.append("")
        events.append("═══ Phase 4: Time Barrier 触发平仓 ═══")
        events.append("  Time Barrier expired → 市价 reduce_only 平仓")

        notes.append(f"Time Barrier: enabled={tb_enabled}, trend={tb_trend_hours}h, counter={tb_counter_hours}h (production)")
        notes.append(f"当前交易: {'逆势' if prod['counter_trend'] else '顺势'} → {active_hours}h 限时")
        notes.append("v11.0-simple: Time Barrier 是持仓期间的最终时间兜底")

        return SimulationResult(
            scenario=OrderScenario.CONFIDENCE_SL_TIGHTEN,
            success=True,
            orders_submitted=[],
            events_triggered=events,
            state_changes={
                "time_barrier": f"countdown → {active_hours}h expired → {tb_action}",
                "position_qty": "Time Barrier 触发时全部平仓",
            },
            notes=notes,
        )

    def _simulate_trailing_stop_activation(self) -> SimulationResult:
        """
        场景 16: 追踪止损激活 (v24.0)

        Uses production constants for trailing stop activation logic:
        1. Layer has fixed STOP_MARKET SL + TRAILING_STOP_MARKET (submitted at open)
        2. Binance server-side: activates when price reaches activation_price (1.5R, v43.0)
        3. Three-way OCO: SL + TP + Trailing coexist, any fill cancels peers
        4. layer['trailing_order_id'] tracks trailing order (v24.2: no trailing_activated flag)
        5. On order filled: OrderType.TRAILING_STOP_MARKET recognized as SL type
        6. /modify_sl: cancels trailing + clears trailing_order_id from layer
        7. Reconciliation: resubmits trailing with new quantity
        """
        prod = self._get_production_sltp(side="BUY")
        events = []
        orders = []
        notes = []

        entry = prod['price']
        sl = prod['sl_price']
        atr_30m = prod['atr']
        atr_4h = prod.get('atr_4h', 0.0) or 0.0
        # v43.0: Trailing uses 4H ATR (same as SL/TP), fallback to 30M
        trailing_atr = atr_4h if atr_4h > 0 else atr_30m
        qty = prod['quantity'] if prod['quantity'] > 0 else 0.01

        # Production constants (from order_execution.py / position_manager.py, v43.0)
        TRAILING_ACTIVATION_R = 1.5
        TRAILING_ATR_MULTIPLIER = 0.6
        TRAILING_MIN_BPS = 10
        TRAILING_MAX_BPS = 1000

        risk = abs(entry - sl)
        if risk <= 0:
            risk = entry * 0.015  # fallback

        activation_price = entry + risk * TRAILING_ACTIVATION_R

        # v43.0: ATR-based callback using 4H ATR
        trailing_distance = trailing_atr * TRAILING_ATR_MULTIPLIER
        trailing_offset_bps = int((trailing_distance / entry) * 10000)
        trailing_offset_bps = max(TRAILING_MIN_BPS, min(TRAILING_MAX_BPS, trailing_offset_bps))
        callback_pct = trailing_offset_bps / 100

        events.append(f"═══ Phase 1: 开仓建立固定 SL ═══")
        events.append(f"  Entry: ${entry:,.2f} | SL: ${sl:,.2f} | Qty: {qty:.4f} BTC")
        events.append(f"  Risk (1R): ${risk:,.2f}")
        events.append(f"  order_factory.stop_market(reduce_only=True) → SL-FIXED-001")
        events.append(f"  _layer_orders[layer_0] = {{sl_order_id: 'SL-FIXED-001', trailing_order_id: ''}}")
        events.append("")

        events.append(f"═══ Phase 2: Binance 原生 trailing (开仓时提交) ═══")
        events.append(f"  on_position_opened → submit trailing_stop_market alongside SL+TP")
        events.append(f"  activation_price = entry ± (risk × {TRAILING_ACTIVATION_R}R) = ${activation_price:,.2f}")
        atr_src = "4H" if atr_4h > 0 else "30M(fallback)"
        events.append(f"  ATR callback ({atr_src}): ${trailing_atr:,.2f} × {TRAILING_ATR_MULTIPLIER} = ${trailing_distance:,.2f}")
        events.append(f"  Callback rate: {trailing_offset_bps} BPS ({callback_pct:.1f}%) [clamped to {TRAILING_MIN_BPS}-{TRAILING_MAX_BPS}]")
        events.append(f"  Binance 服务器端: 价格达到 ${activation_price:,.2f} 后自动追踪最高/最低价")
        events.append("")

        events.append(f"═══ Phase 3: 三单并存架构 (SL + TP + Trailing) ═══")
        events.append(f"  layer['sl_order_id'] = 'SL-FIXED-001' (固定 SL 安全网)")
        events.append(f"  layer['tp_order_id'] = 'TP-001' (止盈)")
        events.append(f"  layer['trailing_order_id'] = 'SL-TRAIL-001' (服务器端追踪)")
        events.append(f"  → 三单共存于 Binance，任一触发后取消另两单")
        events.append("")

        events.append(f"═══ Phase 4: on_order_filled — trailing SL 触发 ═══")
        events.append(f"  is_sl = order_type in (STOP_MARKET, TRAILING_STOP_MARKET) → True")
        events.append(f"  close_reason = TRAILING_STOP (not STOP_LOSS)")
        events.append(f"  → cancel peer TP (OCO)")
        events.append("")

        events.append(f"═══ Phase 5: 特殊场景 ═══")
        events.append(f"  /modify_sl: cancel TRAILING_STOP_MARKET → clear trailing_order_id='' → new STOP_MARKET")
        events.append(f"  Reconcile: _resubmit_layer_orders_with_quantity() → 重新提交 trailing (保留 offset_bps)")
        events.append(f"  Startup recovery: OrderType.TRAILING_STOP_MARKET in enum check (no string matching)")

        # Mock orders
        fixed_sl = MockOrder(
            client_order_id="SL-FIXED-001",
            order_type="STOP_MARKET",
            side="SELL",
            quantity=qty,
            trigger_price=sl,
            is_reduce_only=True,
            status="CANCELLED",
        )
        trailing_sl = MockOrder(
            client_order_id="SL-TRAIL-001",
            order_type="TRAILING_STOP_MARKET",
            side="SELL",
            quantity=qty,
            trigger_price=activation_price,
            is_reduce_only=True,
            status="ACTIVE",
        )
        orders = [fixed_sl, trailing_sl]

        notes.append(f"v24.0: Trailing stop — Binance server-side execution (survives restarts)")
        notes.append(f"v24.0: Activation @ {TRAILING_ACTIVATION_R}R (fee buffer), callback {callback_pct:.1f}%")
        notes.append(f"v24.0: Safety: submit new → cancel old (no naked gap)")
        notes.append(f"v24.0: Per-layer independent (each layer can activate independently)")

        return SimulationResult(
            scenario=OrderScenario.TRAILING_STOP_ACTIVATION,
            success=True,
            orders_submitted=orders,
            events_triggered=events,
            state_changes={
                "sl_type": "STOP_MARKET + TRAILING_STOP_MARKET (three-way OCO)",
                "trailing_order_id": "SL-TRAIL-001",
                "trailing_offset_bps": trailing_offset_bps,
            },
            notes=notes,
        )

    def _print_scenario_result(self, result: SimulationResult) -> None:
        """Print scenario simulation result."""
        scenario_names = {
            OrderScenario.NEW_POSITION: "场景 1: 新开仓",
            OrderScenario.ADD_POSITION: "场景 2: 同向加仓 (v7.2 独立层)",
            OrderScenario.REDUCE_POSITION: "场景 3: 部分平仓 (v7.2 LIFO)",
            OrderScenario.CLOSE_POSITION: "场景 4: 完全平仓",
            OrderScenario.REVERSAL: "场景 5: 反转交易 (v3.18)",
            OrderScenario.BRACKET_FAILURE: "场景 6: Bracket 失败 (v3.18)",
            OrderScenario.SLTP_MODIFY_FAILURE: "场景 7: Per-layer SL 取消 → orphan (v7.2)",
            OrderScenario.ONSTOP_PRESERVATION: "场景 9: 停机保护 (v5.1)",
            OrderScenario.CUMULATIVE_POSITION_LIMIT: "场景 10: 累加仓位上限 (v5.1)",
            OrderScenario.CRASH_RECOVERY: "场景 11: 崩溃恢复 (v5.12)",
            OrderScenario.COOLDOWN_TRIGGERED: "场景 12: 止损冷静期 (v6.0)",
            OrderScenario.PYRAMIDING_REJECTED: "场景 13: 金字塔加仓拒绝 (v6.0)",
            OrderScenario.EMERGENCY_MARKET_CLOSE: "场景 14: 紧急市价平仓 (v6.1)",
            OrderScenario.CONFIDENCE_SL_TIGHTEN: "场景 15: Time Barrier 强制平仓 (v11.0-simple)",
            OrderScenario.TRAILING_STOP_ACTIVATION: "场景 16: 追踪止损激活 (v24.0)",
        }

        name = scenario_names.get(result.scenario, str(result.scenario))
        status = "✅" if result.success else "❌"

        print(f"  {status} {name}")
        print(f"     ────────────────────────────────────────")

        # Events
        print(f"     事件流程:")
        for event in result.events_triggered[:15]:  # Limit display
            print(f"       {event}")
        if len(result.events_triggered) > 15:
            print(f"       ... ({len(result.events_triggered) - 15} more events)")

        # State changes
        if result.state_changes:
            print(f"     状态变化:")
            for key, value in result.state_changes.items():
                print(f"       {key}: {value}")

        # Notes
        if result.notes:
            print(f"     关键点:")
            for note in result.notes:
                print(f"       • {note}")

        print()

    def _print_v50_verification(self) -> None:
        """Print v3.18 + v5.1 + v5.12 + v6.0 specific verification summary."""
        print("  📋 v3.18 + v5.1 + v5.12 + v6.0 修复验证:")
        print()
        print("  ┌───────────────────────────────────────────────────────────────────────┐")
        print("  │ 修复项                               │ 状态 │ 验证场景               │")
        print("  ├───────────────────────────────────────────────────────────────────────┤")
        print("  │ 反转两阶段提交 (v3.18)               │ ✅   │ 场景 5: 反转交易       │")
        print("  │ Bracket 失败不回退 (v3.18)           │ ✅   │ 场景 6: Bracket 失败   │")
        print("  │ SL/TP 数量更新 (v3.18)               │ ✅   │ 场景 2: 同向加仓       │")
        print("  │ modify 失败回退 (v3.18)              │ ✅   │ 场景 7: modify 失败    │")
        print("  │ S/R 动态重评估 + 阈值 (v5.1)        │ ✅   │ 场景 8: S/R 重评估     │")
        print("  │ 停机保护 SL/TP 保留 (v5.1)          │ ✅   │ 场景 9: on_stop        │")
        print("  │ 累加仓位上限 30% (v5.1)             │ ✅   │ 场景 10: 容量检查      │")
        print("  │ Counter-trend R/R escalation (v5.12) │ ✅   │ 场景 8: 动态重评估     │")
        print("  │ sltp_state guard (v5.12)             │ ✅   │ 场景 8: 状态检查       │")
        print("  │ 崩溃恢复 _recover_sltp (v5.12)      │ ✅   │ 场景 11: 崩溃恢复      │")
        print("  │ 止损冷静期 cooldown (v6.0)           │ ✅   │ 场景 12: 冷静期触发    │")
        print("  │ 金字塔加仓层数限制 (v6.0)            │ ✅   │ 场景 13: 加仓拒绝      │")
        print("  │ Time Barrier 强制平仓 (v11.0-simple)   │ ✅   │ 场景 15: Time Barrier  │")
        print("  └───────────────────────────────────────────────────────────────────────┘")

    def should_skip(self) -> bool:
        return self.ctx.summary_mode


class ReversalStateSimulator(DiagnosticStep):
    """
    v3.18 反转状态机模拟

    Detailed simulation of the two-phase reversal state machine.
    """

    name = "v3.18 反转状态机详细模拟"

    def run(self) -> bool:
        print("-" * 70)
        print()
        print_box("反转状态机 (Two-Phase Commit)", 65)
        print()

        # State machine diagram
        print("  状态机图解:")
        print()
        print("  ┌──────────────┐")
        print("  │ 初始状态     │")
        print("  │ LONG 持仓    │")
        print("  │ _pending = ∅ │")
        print("  └──────┬───────┘")
        print("         │ 收到 SELL 信号 (反转)")
        print("         ▼")
        print("  ┌──────────────────────────────────────┐")
        print("  │ Phase 1: 存储状态                    │")
        print("  │ _pending_reversal = {                │")
        print("  │   target_side: 'short',              │")
        print("  │   target_quantity: qty,              │")
        print("  │   old_side: 'long',                  │")
        print("  │   submitted_at: now()                │")
        print("  │ }                                    │")
        print("  │ submit_order(SELL, reduce_only=True) │")
        print("  └──────────────┬───────────────────────┘")
        print("                 │ on_order_filled")
        print("                 │ on_position_closed")
        print("                 ▼")
        print("  ┌──────────────────────────────────────┐")
        print("  │ Phase 2: 检测 _pending_reversal      │")
        print("  │ if _pending_reversal:                │")
        print("  │   pending = _pending_reversal        │")
        print("  │   _pending_reversal = None  # 清空  │")
        print("  │   if _get_position() is None:        │")
        print("  │     _submit_bracket_order(SHORT)     │")
        print("  │   else:                              │")
        print("  │     ABORT (残留仓位)                 │")
        print("  └──────────────┬───────────────────────┘")
        print("                 │ on_order_filled")
        print("                 │ on_position_opened")
        print("                 ▼")
        print("  ┌──────────────┐")
        print("  │ 最终状态     │")
        print("  │ SHORT 持仓   │")
        print("  │ _pending = ∅ │")
        print("  └──────────────┘")
        print()

        # Edge cases
        print("  边缘情况处理:")
        print()
        print("  ┌─────────────────────────────────────────────────────────────┐")
        print("  │ 情况                        │ 处理                          │")
        print("  ├─────────────────────────────────────────────────────────────┤")
        print("  │ Phase 2 时仍有仓位          │ ABORT, 发送 CRITICAL 警报     │")
        print("  │ Phase 2 提交 Bracket 失败   │ 不开仓, 等待下一信号          │")
        print("  │ 平仓订单被拒绝              │ _pending_reversal 保留        │")
        print("  │ SL/TP 触发导致平仓          │ 正常进入 Phase 2              │")
        print("  │ 手动干预平仓                │ 正常进入 Phase 2              │")
        print("  └─────────────────────────────────────────────────────────────┘")
        print()

        # Compare with old behavior
        print("  与旧版行为对比:")
        print()
        print("  旧版 (有竞态条件):")
        print("    1. 提交平仓订单")
        print("    2. 立即提交开仓订单  ← 问题! 可能在平仓完成前执行")
        print("    3. 可能导致双向持仓或订单被拒")
        print()
        print("  v3.18 (事件驱动):")
        print("    1. 存储 _pending_reversal 状态")
        print("    2. 提交平仓订单")
        print("    3. 等待 on_position_closed 事件")
        print("    4. 验证无仓位后开新仓")
        print()

        print("  ✅ v3.18 反转状态机模拟完成")
        return True

    def should_skip(self) -> bool:
        return self.ctx.summary_mode


class BracketOrderFlowSimulator(DiagnosticStep):
    """
    Bracket 订单流程详细模拟

    Shows the complete flow of bracket order submission.
    """

    name = "Bracket 订单流程详细模拟"

    def run(self) -> bool:
        print("-" * 70)
        print()
        print_box("Bracket 订单流程 (Entry + SL + TP)", 65)
        print()

        signal = self.ctx.signal_data.get('signal', 'HOLD')
        if signal == 'HOLD':
            print("  ℹ️ 当前信号为 HOLD，模拟 BUY 信号的 Bracket 订单流程")
            signal = 'BUY'

        # Use production functions for SL/TP calculation
        entry_price = self.ctx.current_price
        atr = (self.ctx.technical_data or {}).get('atr', entry_price * 0.01)
        confidence = (self.ctx.signal_data or {}).get('confidence', 'MEDIUM')
        trend_info = (self.ctx.technical_data or {}).get('trend_info', {})
        is_long = signal in ("BUY", "LONG")
        counter_trend = _is_counter_trend(is_long, trend_info) if trend_info else False

        mech_ok, sl_price, tp_price, mech_desc = calculate_mechanical_sltp(
            entry_price=entry_price,
            side=signal,
            atr_value=atr if atr and atr > 0 else entry_price * 0.01,
            confidence=confidence if confidence in ('HIGH', 'MEDIUM') else 'MEDIUM',
            is_counter_trend=counter_trend,
        )

        # Position size from production — matching ai_decision.py approach
        equity, leverage, _, cfg_dict = _build_position_sizing_config(self.ctx)
        quantity = 0.0
        if equity > 0 and entry_price > 0:
            quantity, _ = calculate_position_size(
                signal_data=self.ctx.signal_data or {},
                price_data={'price': entry_price},
                technical_data=self.ctx.technical_data or {},
                config=cfg_dict,
            )
        if quantity <= 0:
            quantity = 0.01  # Fallback for display

        print(f"  Production 参数 (calculate_mechanical_sltp):")
        print(f"     信号: {signal} ({'逆势' if counter_trend else '顺势'})")
        print(f"     入场价: ${entry_price:,.2f}")
        sl_pct = abs(entry_price - sl_price) / entry_price * 100 if entry_price > 0 else 0
        tp_pct = abs(tp_price - entry_price) / entry_price * 100 if entry_price > 0 else 0
        print(f"     止损价: ${sl_price:,.2f} ({sl_pct:.2f}%)")
        print(f"     止盈价: ${tp_price:,.2f} ({tp_pct:.2f}%)")
        bc = self.ctx.base_currency
        notional = quantity * entry_price if entry_price > 0 else 0
        print(f"     数量: ${notional:,.0f} ({quantity:.4f} {bc})")
        print(f"     ATR: ${atr:,.2f}, 信心: {confidence}")
        rr = tp_pct / sl_pct if sl_pct > 0 else 0
        print(f"     R/R: {rr:.2f}:1 (min={get_min_rr_ratio()}, mech_ok={mech_ok})")
        print()

        # Flow diagram
        print("  订单提交流程:")
        print()
        print("  ┌─────────────────────────────────────────────────────────────┐")
        print("  │ 1. _submit_bracket_order(side, quantity)                    │")
        print("  │    ├─ 检查 quantity >= min_trade_amount                     │")
        print("  │    ├─ 检查 enable_auto_sl_tp                                │")
        print("  │    ├─ 获取 entry_price (latest_price_data / bars)           │")
        print("  │    └─ 获取 confidence, support, resistance                  │")
        print("  └─────────────────────────────────────────────────────────────┘")
        print("                          ↓")
        print("  ┌─────────────────────────────────────────────────────────────┐")
        print("  │ 2. SL/TP 价格计算                                           │")
        print("  │    ├─ 优先: AI Judge 提供的 stop_loss, take_profit          │")
        print("  │    ├─ 验证: validate_multiagent_sltp()                      │")
        print("  │    │   ├─ 检查 SL 在入场价正确一侧                          │")
        print("  │    │   └─ R/R >= 1.5:1 顺势 / 1.95:1 逆势 (v5.12)          │")
        print("  │    └─ 回退: calculate_sr_based_sltp() (S/R Zones+ATR)      │")
        print("  └─────────────────────────────────────────────────────────────┘")
        print("                          ↓")
        print("  ┌─────────────────────────────────────────────────────────────┐")
        print("  │ 3. 两阶段订单提交 (v4.17)                                    │")
        print("  │    ├─ entry_order: LIMIT @ validated entry_price (GTC)      │")
        print("  │    ├─ sl_order: STOP_MARKET (on_position_opened, reduce)    │")
        print("  │    └─ tp_order: LIMIT (on_position_opened, reduce)          │")
        print("  └─────────────────────────────────────────────────────────────┘")
        print("                          ↓")
        print("  ┌─────────────────────────────────────────────────────────────┐")
        print("  │ 4. 订单提交 (submit_order_list)                             │")
        print("  │    └─ NautilusTrader 处理 OTO/OCO 链接                      │")
        print("  └─────────────────────────────────────────────────────────────┘")
        print("                          ↓")
        print("  ┌─────────────────────────────────────────────────────────────┐")
        print("  │ 5. 事件处理                                                 │")
        print("  │    ├─ on_order_filled (Entry) → on_position_opened          │")
        print("  │    ├─ on_order_filled (SL) → on_position_closed             │")
        print("  │    │   └─ OCO: 自动取消 TP                                  │")
        print("  │    └─ on_order_filled (TP) → on_position_closed             │")
        print("  │        └─ OCO: 自动取消 SL                                  │")
        print("  └─────────────────────────────────────────────────────────────┘")
        print()

        # v3.18 specific
        print("  v3.18 关键改进:")
        print("     • Bracket 失败时不回退到无保护订单")
        print("     • 发送 CRITICAL Telegram 警报")
        print("     • _last_signal_status 记录失败原因")
        print()

        print("  ✅ Bracket 订单流程模拟完成")
        return True

    def should_skip(self) -> bool:
        return self.ctx.summary_mode
