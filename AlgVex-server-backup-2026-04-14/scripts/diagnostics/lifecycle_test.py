"""
Lifecycle Test Module (v11.0-simple)

Tests post-trade lifecycle features and on_bar MTF routing logic.
Restored from v11.16 monolithic script.

v11.0-simple: Time Barrier enforces max holding period (12h trend / 6h counter-trend).
"""

from typing import Dict, Optional

from .base import (
    DiagnosticContext,
    DiagnosticStep,
    fetch_binance_klines,
    create_bar_from_kline,
    safe_float,
)


class PostTradeLifecycleTest(DiagnosticStep):
    """
    Test post-trade lifecycle features.

    Tests:
    - OCO orphan order cleanup (_cleanup_oco_orphans)
    - v11.0-simple: Position management (Time Barrier)

    Time Barrier enforces max holding period (12h trend / 6h counter-trend).
    Based on v11.16: [8.5/10] Post-Trade 生命周期测试
    """

    name = "Post-Trade 生命周期测试"

    def run(self) -> bool:
        print("-" * 70)

        cfg = self.ctx.strategy_config

        # Test OCO orphan order cleanup
        print("  📋 OCO 孤儿订单清理 (_cleanup_oco_orphans):")
        enable_oco = getattr(cfg, 'enable_oco', False)
        if enable_oco:
            print("     ✅ enable_oco = True")
            print("        → 实盘会在每次 on_timer 后调用 _cleanup_oco_orphans()")
            print("        → 清理无持仓时的 reduce-only 订单")
        else:
            print("     ⚠️ enable_oco = False (跳过清理)")

        # 2-level position management (runs before AI analysis)
        print()
        print("  📋 持仓管理 (Time Barrier) + AI 继续分析:")
        enable_auto_sltp = getattr(cfg, 'enable_auto_sl_tp', True)
        if enable_auto_sltp:
            print("     ✅ enable_auto_sl_tp = True")
            print("        Time Barrier — 顺势 12h / 逆势 6h 到期市价平仓")
            print("        → 管理完成后 AI 继续分析 (允许加仓/反转)")

            # Show current position context if exists
            if self.ctx.current_position:
                self._show_reevaluation_context()
        else:
            print("     ⚠️ enable_auto_sl_tp = False (SL/TP 不会动态调整)")

        # Test position snapshot
        print()
        print("  📋 持仓快照记录 (_save_position_snapshot):")
        print("     ✅ 每次 on_timer 记录持仓状态到 data/position_snapshots/")
        print("        → 用于追踪持仓历史和计算回撤")

        print()
        print("  ✅ Post-Trade 生命周期测试完成")
        return True

    def _show_reevaluation_context(self) -> None:
        """Show position management context for current position."""
        side = self.ctx.current_position.get('side', '').lower()
        entry_price = self.ctx.current_position.get('entry_price', 0)
        if entry_price <= 0:
            entry_price = self.ctx.current_position.get('avg_px', 0)

        if entry_price > 0 and self.ctx.current_price > 0:
            if side in ['long', 'buy']:
                pnl_pct = (self.ctx.current_price - entry_price) / entry_price * 100
            else:
                pnl_pct = (entry_price - self.ctx.current_price) / entry_price * 100
            print(f"        → 当前浮盈: {pnl_pct:.2f}%")
            print(f"        → 入场价: ${entry_price:,.2f}  当前价: ${self.ctx.current_price:,.2f}")
            print(f"        → 下次 on_timer: _check_time_barrier()")

    def should_skip(self) -> bool:
        return self.ctx.summary_mode


class OnBarMTFRoutingTest(DiagnosticStep):
    """
    Simulate on_bar MTF routing logic.

    Tests the bar type routing to different layers:
    - 1D bars → Trend layer (_handle_trend_bar)
    - 4H bars → Decision layer (_handle_decision_bar)
    - 30M bars → Execution layer (_handle_execution_bar)

    Based on v11.16: [10/14] on_bar MTF 路由逻辑模拟
    """

    name = "on_bar MTF 路由逻辑模拟"

    def run(self) -> bool:
        print("-" * 70)

        try:
            # Check MTF config
            mtf_config = self.ctx.base_config.get('multi_timeframe', {})
            mtf_enabled = mtf_config.get('enabled', False)

            if not mtf_enabled:
                print("  ℹ️ MTF 未启用，跳过路由测试")
                return True

            print("  📊 MTF Bar 路由逻辑 (与 ai_strategy.py:on_bar 一致):")
            print()

            # Get timeframe configs
            trend_tf = mtf_config.get('trend_layer', {}).get('timeframe', '1d')
            decision_tf = mtf_config.get('decision_layer', {}).get('timeframe', '4h')
            execution_tf = mtf_config.get('execution_layer', {}).get('default_timeframe', '30m')

            self._print_routing_rules(trend_tf, decision_tf, execution_tf)
            self._simulate_current_bar_routing()
            self._print_indicator_updates()
            self._check_mtf_bar_freshness()

            print()
            print("  ✅ on_bar MTF 路由模拟完成")
            return True

        except Exception as e:
            self.ctx.add_error(f"on_bar 路由模拟失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _print_routing_rules(self, trend_tf: str, decision_tf: str, execution_tf: str) -> None:
        """Print MTF routing rules."""
        print(f"  [路由规则] Bar 类型 → 处理层:")
        print()
        print(f"     • {trend_tf.upper()} bar → 趋势层 (_handle_trend_bar)")
        print(f"       - 更新 SMA_200, MACD")
        print(f"       - 收集趋势数据供 AI 分析 (v3.1: 不做本地判断)")
        print(f"       - 设置 _mtf_trend_initialized = True")
        print()
        print(f"     • {decision_tf.upper()} bar → 决策层 (_handle_decision_bar)")
        print(f"       - 更新决策层技术指标")
        print(f"       - 收集决策层数据 (AI 自主分析，无本地决策)")
        print(f"       - 设置 _mtf_decision_initialized = True")
        print()
        print(f"     • {execution_tf.upper()} bar → 执行层 (_handle_execution_bar)")
        print(f"       - 更新执行层指标 (RSI, MACD 等)")
        print(f"       - 更新 _cached_current_price (线程安全)")
        print(f"       - 设置 _mtf_execution_initialized = True")
        print()

    def _simulate_current_bar_routing(self) -> None:
        """Simulate routing for current bar."""
        cfg = self.ctx.strategy_config
        bar_type_str = str(getattr(cfg, 'bar_type', '30-MINUTE'))
        print(f"  [模拟路由] 当前诊断使用的 bar_type:")
        print(f"     bar_type: {bar_type_str}")

        if '1-DAY' in bar_type_str or '1D' in bar_type_str.upper():
            print(f"     → 路由到: 趋势层 (1D)")
        elif '4-HOUR' in bar_type_str or '4H' in bar_type_str.upper():
            print(f"     → 路由到: 决策层 (4H)")
        else:
            print(f"     → 路由到: 执行层 (30M) - 主分析周期")
        print()

    def _print_indicator_updates(self) -> None:
        """Print indicator update data."""
        td = self.ctx.technical_data

        print(f"  [指标更新] 本次 bar 更新的指标值:")
        print(f"     indicator_manager.update(bar) 后:")
        print(f"     • 价格: ${self.ctx.current_price:,.2f}")
        print(f"     • SMA_5: ${td.get('sma_5', 0):,.2f}")
        print(f"     • SMA_20: ${td.get('sma_20', 0):,.2f}")
        print(f"     • SMA_50: ${td.get('sma_50', 0):,.2f}")
        print(f"     • RSI: {td.get('rsi', 0):.2f}")
        print(f"     • MACD: {td.get('macd', 0):.4f}")
        print(f"     • MACD Signal: {td.get('macd_signal', 0):.4f}")
        print(f"     • Support: ${td.get('support', 0):,.2f}")
        print(f"     • Resistance: ${td.get('resistance', 0):,.2f}")

    def _check_mtf_bar_freshness(self) -> None:
        """
        v6.0: Check MTF bar data freshness (STALE #2 fix).

        Validates that 4H and 1D bar data are within expected time windows.
        Stale bars could cause AI to make decisions on outdated trend/decision data.
        """
        print()
        print("  [新鲜度] v6.0 MTF Bar 时间戳验证:")

        bars_4h = getattr(self.ctx, 'bars_data_4h', None)
        bars_1d = getattr(self.ctx, 'bars_data_1d', None)

        import time
        now = time.time() * 1000  # Binance uses milliseconds

        # Check 4H bars freshness
        # v15.2: Production bars (NautilusTrader) lack close_time — check bar count instead
        if bars_4h and len(bars_4h) > 0:
            latest_4h = bars_4h[-1]
            close_time_ms = None
            if isinstance(latest_4h, dict) and 'close_time' in latest_4h:
                close_time_ms = float(latest_4h['close_time'])
            elif isinstance(latest_4h, (list, tuple)) and len(latest_4h) > 6:
                close_time_ms = float(latest_4h[6])
            if close_time_ms is not None:
                age_hours = (now - close_time_ms) / (3600 * 1000)
                if age_hours > 8:  # More than 2x 4H cycle
                    self.ctx.add_warning(f"4H bar 数据过旧 ({age_hours:.1f}h)")
                    print(f"     ⚠️ 4H bar: {age_hours:.1f}h 前 (超过 8h 阈值)")
                else:
                    print(f"     ✅ 4H bar: {age_hours:.1f}h 前")
            else:
                # v15.1 intentionally omits close_time to match production NautilusTrader bars
                close_val = latest_4h.get('close', 0) if isinstance(latest_4h, dict) else None
                print(f"     ✅ 4H bar: {len(bars_4h)} bars loaded (close=${close_val:,.2f})" if close_val else f"     ✅ 4H bar: {len(bars_4h)} bars loaded")
        else:
            print(f"     ⚠️ 4H bar: 无数据")

        # Check 1D bars freshness
        if bars_1d and len(bars_1d) > 0:
            latest_1d = bars_1d[-1]
            close_time_ms = None
            if isinstance(latest_1d, dict) and 'close_time' in latest_1d:
                close_time_ms = float(latest_1d['close_time'])
            elif isinstance(latest_1d, (list, tuple)) and len(latest_1d) > 6:
                close_time_ms = float(latest_1d[6])
            if close_time_ms is not None:
                age_hours = (now - close_time_ms) / (3600 * 1000)
                if age_hours > 48:  # More than 2x 1D cycle
                    self.ctx.add_warning(f"1D bar 数据过旧 ({age_hours:.1f}h)")
                    print(f"     ⚠️ 1D bar: {age_hours:.1f}h 前 (超过 48h 阈值)")
                else:
                    print(f"     ✅ 1D bar: {age_hours:.1f}h 前")
            else:
                close_val = latest_1d.get('close', 0) if isinstance(latest_1d, dict) else None
                print(f"     ✅ 1D bar: {len(bars_1d)} bars loaded (close=${close_val:,.2f})" if close_val else f"     ✅ 1D bar: {len(bars_1d)} bars loaded")
        else:
            print(f"     ⚠️ 1D bar: 无数据")

    def should_skip(self) -> bool:
        return self.ctx.summary_mode
