"""
Position Check Module

Checks Binance account positions and balance.

v4.8 Updates:
- Get leverage from Binance API instead of hardcoded value
- Display cumulative position info for add-on scenarios

v4.8.1 Updates:
- Complete position fields to match production _get_current_position_data() (25 fields)
- Fix field name consistency (pnl_pct → pnl_percentage)
- Complete account_context fields to match production _get_account_context() (13 fields)
- Fix field names (max_usdt → max_position_value, remaining_capacity → available_capacity)

v6.2 Updates:
- NakedPositionScanner: cross-checks Binance open position vs open orders for SL protection

v19.1 Audit Updates:
- SL/TP recovery from Binance open orders via get_sl_tp_from_orders() (matches production Tier 3)
- R/R ratio calculated from recovered SL/TP prices
- Position value uses markPrice instead of lastPrice (matches production _get_current_position_data)
"""

from typing import Dict, Optional

from .base import DiagnosticContext, DiagnosticStep


class PositionChecker(DiagnosticStep):
    """
    Check current Binance positions.

    Uses BinanceAccountFetcher to get real position data.
    """

    name = "检查 Binance 真实持仓"

    def run(self) -> bool:
        print("-" * 70)

        try:
            from utils.binance_account import BinanceAccountFetcher

            account_fetcher = BinanceAccountFetcher()
            positions = account_fetcher.get_positions(symbol=self.ctx.symbol)

            # v4.8: Get real leverage from Binance
            binance_leverage = account_fetcher.get_leverage(self.ctx.symbol)
            self.ctx.binance_leverage = binance_leverage
            print(f"  📊 杠杆倍数 (from Binance): {binance_leverage}x")

            if positions:
                pos = positions[0]
                pos_amt = float(pos.get('positionAmt', 0))
                entry_price = float(pos.get('entryPrice', 0))
                unrealized_pnl = float(pos.get('unRealizedProfit', 0))

                if pos_amt != 0:
                    # v6.1: Pass raw Binance fields for accurate liquidation/mark price
                    binance_liq_price = float(pos.get('liquidationPrice', 0))
                    binance_mark_price = float(pos.get('markPrice', 0))
                    self._process_position(
                        pos_amt, entry_price, unrealized_pnl, binance_leverage,
                        binance_liq_price=binance_liq_price,
                        binance_mark_price=binance_mark_price,
                    )
                else:
                    print("  ✅ 无持仓")
            else:
                print("  ✅ 无持仓")

            # Get account balance
            self._get_account_balance(account_fetcher, binance_leverage)

            return True

        except Exception as e:
            self.ctx.add_warning(f"持仓检查失败: {e}")
            print("  → 继续假设无持仓")
            return True  # Non-critical

    def _process_position(
        self,
        pos_amt: float,
        entry_price: float,
        unrealized_pnl: float,
        leverage: int = 10,
        binance_liq_price: float = 0,
        binance_mark_price: float = 0,
    ) -> None:
        """
        Process and display position data.

        v4.8.1: Complete all 25 fields to match production _get_current_position_data()
        v6.1: Use Binance liquidationPrice/markPrice instead of formula approximation
        """
        side = 'long' if pos_amt > 0 else 'short'
        quantity = abs(pos_amt)
        avg_px = entry_price
        current_price = self.ctx.current_price

        # Calculate PnL if API returns 0 but we have prices
        if unrealized_pnl == 0 and entry_price > 0 and current_price > 0:
            if side == 'long':
                unrealized_pnl = (current_price - entry_price) * quantity
            else:
                unrealized_pnl = (entry_price - current_price) * quantity

        # === Tier 1: pnl_percentage (名称与生产代码一致) ===
        pnl_percentage = 0.0
        if avg_px > 0 and current_price:
            if side == 'long':
                pnl_percentage = ((current_price - avg_px) / avg_px) * 100
            else:
                pnl_percentage = ((avg_px - current_price) / avg_px) * 100

        # === Tier 1: duration_minutes, entry_timestamp ===
        # 诊断脚本无法获取入场时间，标记为 None
        duration_minutes = 0  # Match production Binance path default (ai_strategy.py:4024)
        entry_timestamp = None

        # === Tier 1: sl_price, tp_price, risk_reward_ratio ===
        # v19.1 audit: Query Binance open orders to recover SL/TP (matches production Tier 3 lookup)
        sl_price = None
        tp_price = None
        risk_reward_ratio = None
        try:
            from utils.binance_account import BinanceAccountFetcher
            sltp_fetcher = BinanceAccountFetcher()
            sltp_result = sltp_fetcher.get_sl_tp_from_orders(self.ctx.symbol, side)
            sl_price = sltp_result.get('sl_price')
            tp_price = sltp_result.get('tp_price')
            if sl_price and tp_price and avg_px > 0:
                if side == 'long':
                    risk = abs(avg_px - sl_price)
                    reward = abs(tp_price - avg_px)
                else:
                    risk = abs(sl_price - avg_px)
                    reward = abs(avg_px - tp_price)
                if risk > 0:
                    risk_reward_ratio = round(reward / risk, 2)
        except Exception:
            pass  # Non-critical: fall back to None

        # === Tier 2: peak_pnl_pct, worst_pnl_pct ===
        # 诊断脚本无法获取历史极值，使用当前 PnL 作为估计
        peak_pnl_pct = round(pnl_percentage, 2) if pnl_percentage > 0 else 0.0
        worst_pnl_pct = round(pnl_percentage, 2) if pnl_percentage < 0 else 0.0

        # === Tier 2: entry_confidence ===
        # 诊断脚本无法获取上次信号，标记为 None
        entry_confidence = None

        # === Tier 2: margin_used_pct ===
        # v19.1 audit: Use markPrice for position value (matches production _get_current_position_data)
        margin_used_pct = None
        equity = getattr(self.ctx, 'account_balance', {}).get('total_balance', 0)
        ref_price_for_value = binance_mark_price if binance_mark_price > 0 else current_price
        position_value = quantity * ref_price_for_value if ref_price_for_value else 0
        if equity and equity > 0 and current_price:
            margin_used_pct = round((position_value / equity) * 100, 2)

        # === v4.7: Liquidation Risk Fields (CRITICAL) ===
        # v6.1: Use Binance liquidationPrice directly (matches production ai_strategy.py:2813-2833)
        # Falls back to formula approximation only if Binance doesn't provide it
        liquidation_price = None
        liquidation_buffer_pct = None
        is_liquidation_risk_high = False
        # Use Binance markPrice for buffer calculation (more accurate than last trade price)
        ref_price = binance_mark_price if binance_mark_price > 0 else current_price

        if binance_liq_price and binance_liq_price > 0:
            # Priority: Binance API liquidationPrice (ground truth)
            liquidation_price = binance_liq_price
            if ref_price and ref_price > 0:
                if side == 'long':
                    liquidation_buffer_pct = ((ref_price - liquidation_price) / ref_price) * 100
                else:
                    liquidation_buffer_pct = ((liquidation_price - ref_price) / ref_price) * 100
        elif avg_px > 0 and leverage > 0:
            # Fallback: formula approximation
            maintenance_margin_ratio = 0.004  # Binance standard
            if side == 'long':
                liquidation_price = avg_px * (1 - 1/leverage + maintenance_margin_ratio)
                if ref_price and liquidation_price > 0:
                    liquidation_buffer_pct = ((ref_price - liquidation_price) / ref_price) * 100
            else:
                liquidation_price = avg_px * (1 + 1/leverage - maintenance_margin_ratio)
                if ref_price and liquidation_price > 0:
                    liquidation_buffer_pct = ((liquidation_price - ref_price) / ref_price) * 100

        if liquidation_buffer_pct is not None:
            liquidation_buffer_pct = round(max(0, liquidation_buffer_pct), 2)
            is_liquidation_risk_high = liquidation_buffer_pct < 10

        # === v4.7: Funding Rate Fields (CRITICAL) ===
        # 从 Binance funding rate 数据获取 (如果可用)
        funding_rate_current = None
        funding_rate_cumulative_usd = None
        effective_pnl_after_funding = None
        daily_funding_cost_usd = None

        if self.ctx.binance_funding_rate:
            fr_data = self.ctx.binance_funding_rate
            funding_rate_current = fr_data.get('funding_rate', 0)

            if funding_rate_current and position_value > 0:
                # Daily funding cost = position_value * |rate| * 3 settlements/day
                daily_funding_cost_usd = round(position_value * abs(funding_rate_current) * 3, 2)

                # 无法计算累计 funding (需要持仓时间)
                # funding_rate_cumulative_usd 和 effective_pnl_after_funding 保持 None

        # === v4.7: Drawdown Attribution Fields (RECOMMENDED) ===
        max_drawdown_pct = None
        max_drawdown_duration_bars = None
        consecutive_lower_lows = None  # v19.1 audit: Match production default (None, not 0)

        # 如果有 peak 和 current PnL，计算 drawdown
        if peak_pnl_pct is not None and pnl_percentage is not None:
            if peak_pnl_pct > pnl_percentage:
                max_drawdown_pct = round(peak_pnl_pct - pnl_percentage, 2)
            else:
                max_drawdown_pct = 0.0

        # v4.8.1: Complete 25 fields matching production _get_current_position_data()
        self.ctx.current_position = {
            # === Basic (4 fields) ===
            'side': side,
            'quantity': quantity,
            'avg_px': avg_px,
            'unrealized_pnl': unrealized_pnl,
            # === Tier 1 (6 fields) ===
            'pnl_percentage': round(pnl_percentage, 2),  # 名称修正: pnl_pct → pnl_percentage
            'duration_minutes': duration_minutes,
            'entry_timestamp': entry_timestamp,
            'sl_price': sl_price,
            'tp_price': tp_price,
            'risk_reward_ratio': risk_reward_ratio,
            # === Tier 2 (5 fields) ===
            'peak_pnl_pct': peak_pnl_pct,
            'worst_pnl_pct': worst_pnl_pct,
            'entry_confidence': entry_confidence,
            'margin_used_pct': margin_used_pct,
            'current_price': float(current_price) if current_price else None,
            # === v4.7: Liquidation Risk (3 fields) ===
            'liquidation_price': round(liquidation_price, 2) if liquidation_price else None,
            'liquidation_buffer_pct': liquidation_buffer_pct,
            'is_liquidation_risk_high': is_liquidation_risk_high,
            # === v4.7: Funding Rate (4 fields) ===
            'funding_rate_current': funding_rate_current,
            'funding_rate_cumulative_usd': funding_rate_cumulative_usd,
            'effective_pnl_after_funding': effective_pnl_after_funding,
            'daily_funding_cost_usd': daily_funding_cost_usd,
            # === v4.7: Drawdown Attribution (3 fields) ===
            'max_drawdown_pct': max_drawdown_pct,
            'max_drawdown_duration_bars': max_drawdown_duration_bars,
            'consecutive_lower_lows': consecutive_lower_lows,
            # Diagnostic-only: position value for capacity calculation
            # Production computes this in _get_account_context() from Binance API directly
            'position_value_usdt': position_value,
        }

        print(f"  ⚠️ 检测到现有持仓!")
        print(f"     方向: {side.upper()}")
        bc = self.ctx.base_currency
        print(f"     数量: ${position_value:,.0f} ({quantity:.4f} {bc})")
        print(f"     持仓价值: ${position_value:,.2f}")
        print(f"     入场价: ${avg_px:,.2f}")
        print(f"     未实现盈亏: ${unrealized_pnl:,.2f}")
        print(f"     盈亏比例: {pnl_percentage:+.2f}%")

        # v19.1 audit: Display recovered SL/TP/RR
        if sl_price is not None:
            print(f"     止损价 (SL): ${sl_price:,.2f}")
        if tp_price is not None:
            print(f"     止盈价 (TP): ${tp_price:,.2f}")
        if risk_reward_ratio is not None:
            print(f"     风险回报比 (R/R): {risk_reward_ratio:.2f}:1")
        if sl_price is None and tp_price is None:
            print(f"     SL/TP: 未检测到挂单保护")

        # v4.5 Tier 2: margin_used_pct
        if margin_used_pct is not None:
            print(f"     保证金占用: {margin_used_pct:.1f}%")

        # v4.7: Display liquidation risk
        if liquidation_price is not None:
            risk_emoji = "🔴" if is_liquidation_risk_high else "🟢"
            print(f"     爆仓价: ${liquidation_price:,.2f}")
            print(f"     爆仓距离: {risk_emoji} {liquidation_buffer_pct:.1f}%")
            if is_liquidation_risk_high:
                print(f"     ⚠️ 警告: 爆仓风险高 (<10%)")

        # v5.1: Display funding rate impact (settled rate)
        if funding_rate_current is not None:
            fr_pct = funding_rate_current * 100
            print(f"     已结算费率: {fr_pct:+.5f}%")
            if daily_funding_cost_usd:
                print(f"     日资金费用: ${daily_funding_cost_usd:,.2f}")

    def _get_account_balance(self, account_fetcher, leverage: int = 10) -> None:
        """
        Get and display account balance.

        v4.8.1: Complete all 13 fields to match production _get_account_context()
        Field names fixed to match what _format_account() expects:
        - max_usdt → max_position_value
        - remaining_capacity → available_capacity
        - Added: max_position_ratio, capacity_used_pct
        """
        print()
        print("  📊 账户资金详情:")

        try:
            balance_data = account_fetcher.get_balance()
            self.ctx.account_balance = balance_data

            total_balance = balance_data.get('total_balance', 0)
            available_balance = balance_data.get('available_balance', 0)
            account_unrealized_pnl = balance_data.get('unrealized_pnl', 0)

            used_margin = total_balance - available_balance
            margin_ratio = (
                (available_balance / total_balance * 100)
                if total_balance > 0 else 0
            )

            print(f"     总余额:       ${total_balance:,.2f}")
            print(f"     可用余额:     ${available_balance:,.2f}")
            print(f"     已用保证金:   ${used_margin:,.2f}")
            print(f"     保证金率:     {margin_ratio:.1f}%")
            print(f"     总未实现PnL:  ${account_unrealized_pnl:,.2f}")

            # v6.1 fix: Read max_position_ratio from config (matches production ai_strategy.py:2774)
            max_position_ratio = getattr(self.ctx.strategy_config, 'max_position_ratio', 0.12) if self.ctx.strategy_config else 0.12
            equity = total_balance
            max_position_value = equity * max_position_ratio * leverage

            print()
            print(f"  📊 v4.8 仓位计算参数:")
            print(f"     equity: ${equity:,.2f}")
            print(f"     leverage: {leverage}x")
            print(f"     max_position_ratio: {max_position_ratio*100:.0f}%")
            print(f"     max_position_value: ${max_position_value:,.2f}")

            # v4.8: Calculate capacity metrics
            current_position_value = 0
            if self.ctx.current_position:
                current_position_value = self.ctx.current_position.get('position_value_usdt', 0)

            available_capacity = max(0, max_position_value - current_position_value)
            capacity_used_pct = 0.0
            if max_position_value > 0:
                capacity_used_pct = (current_position_value / max_position_value) * 100

            # Determine if can add position (at least 10% capacity remaining)
            can_add_position = capacity_used_pct < 90

            # Get liquidation buffer from position if available
            liq_buffer_min = None
            if self.ctx.current_position:
                liq_buffer_min = self.ctx.current_position.get('liquidation_buffer_pct')

            # v4.7: Safer check - also consider liquidation buffer
            can_add_position_safely = can_add_position and (
                liq_buffer_min is None or liq_buffer_min > 15
            )

            # v4.7: Calculate funding costs from position data
            total_daily_funding_cost_usd = None
            total_cumulative_funding_paid_usd = None

            if self.ctx.current_position:
                daily_cost = self.ctx.current_position.get('daily_funding_cost_usd')
                if daily_cost is not None:
                    total_daily_funding_cost_usd = daily_cost
                cumulative = self.ctx.current_position.get('funding_rate_cumulative_usd')
                if cumulative is not None:
                    total_cumulative_funding_paid_usd = cumulative

            # v4.8.1: Complete 13 fields matching production _get_account_context()
            # Field names fixed to match what _format_account() expects
            self.ctx.account_context = {
                # === Core fields (8 fields) ===
                'equity': round(equity, 2),
                'leverage': leverage,
                'max_position_ratio': max_position_ratio,  # v4.8.1: Added
                'max_position_value': round(max_position_value, 2),  # 名称修正: max_usdt → max_position_value
                'current_position_value': round(current_position_value, 2),
                'available_capacity': round(available_capacity, 2),  # 名称修正: remaining_capacity → available_capacity
                'capacity_used_pct': round(capacity_used_pct, 1),  # v4.8.1: Added
                'can_add_position': can_add_position,
                # === v4.7: Portfolio-Level Risk Fields (5 fields) ===
                'total_unrealized_pnl_usd': round(account_unrealized_pnl, 2),
                'liquidation_buffer_portfolio_min_pct': round(liq_buffer_min, 2) if liq_buffer_min is not None else None,
                'total_daily_funding_cost_usd': round(total_daily_funding_cost_usd, 2) if total_daily_funding_cost_usd is not None else None,
                'total_cumulative_funding_paid_usd': round(total_cumulative_funding_paid_usd, 2) if total_cumulative_funding_paid_usd is not None else None,
                'can_add_position_safely': can_add_position_safely,
            }

            # v4.8: Display cumulative mode capacity
            if self.ctx.current_position:
                print()
                print(f"  📊 v4.8 累加模式状态:")
                print(f"     当前持仓价值: ${current_position_value:,.2f}")
                print(f"     剩余可加仓: ${available_capacity:,.2f}")
                print(f"     已用容量: {capacity_used_pct:.1f}%")
                if available_capacity <= 0:
                    print(f"     ⚠️ 已达 max_position_value 上限，无法加仓")

            # v4.7: Display portfolio risk
            print()
            print("  ⚠️ 组合风险:")
            print(f"     容量使用率: {capacity_used_pct:.1f}%")
            if liq_buffer_min is not None:
                risk_emoji = "🔴" if liq_buffer_min < 10 else "🟡" if liq_buffer_min < 15 else "🟢"
                print(f"     最小爆仓距离: {risk_emoji} {liq_buffer_min:.1f}%")
            if total_daily_funding_cost_usd:
                print(f"     日资金费用: ${total_daily_funding_cost_usd:,.2f}")
            safety_emoji = "✅" if can_add_position_safely else "⚠️"
            safety_text = "可安全加仓" if can_add_position_safely else "加仓需谨慎"
            print(f"     加仓建议: {safety_emoji} {safety_text}")

        except Exception as e:
            self.ctx.add_warning(f"无法获取账户余额: {e}")


class MemorySystemChecker(DiagnosticStep):
    """
    Check AI learning memory system (v5.10 + v12.0 + v18.0).

    Validates:
    - Memory file loading, saving, and format
    - v5.9 evaluation fields (grade, actual_rr, etc.)
    - v5.10 similarity-based memory retrieval (_build_current_conditions)
    - v12.0 Per-Agent Reflection Memory (reflection fields, role annotations, config)
    - v18.0 Extended Reflections (data/extended_reflections.json health)
    - v18.0 Recency scoring (per-cycle cache, constants)
    """

    name = "v18.0 记忆系统健康检查 (全 Agent 记忆 + 反思 + Extended Reflection + Recency)"

    def run(self) -> bool:
        print("-" * 70)

        try:
            import json
            from pathlib import Path

            memory_file = "data/trading_memory.json"
            memory_path = self.ctx.project_root / memory_file

            print(f"  📂 记忆文件路径: {memory_path}")

            if memory_path.exists():
                self._check_memory_file(memory_path)
            else:
                print(f"  ⚠️ 记忆文件不存在 (系统刚启动)")
                print(f"     → 首次交易后将自动创建")

            # Check MultiAgentAnalyzer memory system
            self._check_analyzer_memory()

            # v18.0: Check extended reflections file
            self._check_extended_reflections()

            # v18.0: Check recency scoring infrastructure
            self._check_recency_scoring()

            print()
            print("  ✅ v18.0 记忆系统健康检查完成")
            return True

        except Exception as e:
            self.ctx.add_warning(f"记忆系统检查失败: {e}")
            return True  # Non-critical

    def _check_memory_file(self, memory_path) -> None:
        """Check memory file content."""
        import json

        print(f"  ✅ 记忆文件存在")

        with open(memory_path, 'r', encoding='utf-8') as f:
            memories = json.load(f)

        print(f"  📊 记忆条目数量: {len(memories)}")

        if memories:
            successes = [m for m in memories if m.get('pnl', 0) > 0]
            failures = [m for m in memories if m.get('pnl', 0) <= 0]

            print(f"     ✅ 成功交易: {len(successes)} 条")
            print(f"     ❌ 失败交易: {len(failures)} 条")

            # Show recent 3 memories
            print()
            print("  📝 最近 3 条记忆:")
            for mem in memories[-3:]:
                decision = mem.get('decision', 'N/A')
                pnl = mem.get('pnl', 0)
                conditions = str(mem.get('conditions', 'N/A') or 'N/A')[:50]
                timestamp = str(mem.get('timestamp', 'N/A') or 'N/A')[:19]
                grade = ''
                evaluation = mem.get('evaluation', {})
                if evaluation and isinstance(evaluation, dict):
                    grade = f" [Grade {evaluation.get('grade', '?')}]"
                emoji = '✅' if pnl > 0 else '❌'
                print(f"     {emoji} [{timestamp}] {decision} → {pnl:+.2f}%{grade}")
                print(f"        Conditions: {conditions}...")

            # Validate basic format
            print()
            print("  🔍 记忆基础格式验证:")
            required_fields = ['decision', 'pnl', 'conditions', 'lesson', 'timestamp']
            latest = memories[-1] if memories else {}
            for field in required_fields:
                has_field = field in latest
                status = '✅ 存在' if has_field else '❌ 缺失'
                print(f"     {status}: {field}")

            # v5.9: Validate evaluation fields
            self._check_evaluation_fields(memories)

            # v5.9: Grade distribution
            self._check_grade_distribution(memories)

            # v12.0: Reflection Memory checks
            self._check_reflection_memory(memories)
        else:
            print("  ℹ️ 记忆为空 (系统刚启动，尚无交易记录)")

    def _check_evaluation_fields(self, memories) -> None:
        """v5.9: Validate evaluation fields in memory entries."""
        print()
        print("  🔍 v5.9 评估字段验证 (13 字段):")

        eval_fields = [
            'grade', 'direction_correct', 'entry_price', 'exit_price',
            'planned_sl', 'planned_tp', 'planned_rr', 'actual_rr',
            'execution_quality', 'exit_type', 'confidence',
            'position_size_pct', 'hold_duration_min',
        ]

        # Check entries with evaluations
        entries_with_eval = [m for m in memories if m.get('evaluation') and isinstance(m.get('evaluation'), dict)]
        total = len(memories)
        evaluated = len(entries_with_eval)

        print(f"     评估覆盖率: {evaluated}/{total} ({evaluated/total*100:.0f}% 已评估)" if total > 0 else "     评估覆盖率: 0/0")

        if entries_with_eval:
            latest_eval = entries_with_eval[-1].get('evaluation', {})
            present = 0
            for field in eval_fields:
                has = field in latest_eval
                if has:
                    present += 1
                status = '✅' if has else '❌'
                print(f"     {status} evaluation.{field}: {'present' if has else 'MISSING'}")
            print(f"     字段完整性: {present}/{len(eval_fields)}")
        else:
            print("     ⚠️ 无评估数据 (尚无平仓后的交易评估)")

    def _check_grade_distribution(self, memories) -> None:
        """v5.9: Show grade distribution."""
        entries_with_eval = [m for m in memories if m.get('evaluation') and isinstance(m.get('evaluation'), dict)]
        if not entries_with_eval:
            return

        print()
        print("  📊 v5.9 成绩分布:")
        grades = {}
        for m in entries_with_eval:
            grade = m.get('evaluation', {}).get('grade', '?')
            grades[grade] = grades.get(grade, 0) + 1

        for grade in ['A+', 'A', 'B', 'C', 'D', 'F']:
            count = grades.get(grade, 0)
            if count > 0:
                bar = '#' * count
                print(f"     {grade:>2}: {bar} ({count})")

        # Direction accuracy
        correct = sum(1 for m in entries_with_eval
                     if m.get('evaluation', {}).get('direction_correct', False))
        if entries_with_eval:
            accuracy = correct / len(entries_with_eval) * 100
            print(f"     方向准确率: {accuracy:.0f}% ({correct}/{len(entries_with_eval)})")

    def _check_reflection_memory(self, memories) -> None:
        """
        v12.0: Check Per-Agent Reflection Memory health.

        Validates:
        - reflection field presence and quality
        - winning_side field presence
        - entry_judge_summary field presence
        - original_lesson preservation
        - Reflection always enabled (hardcoded)
        """
        print()
        print("  🔍 v12.0 Per-Agent 反思记忆检查:")

        total = len(memories)
        if total == 0:
            print("     ℹ️ 无记忆条目，跳过反思检查")
            return

        # Count entries with v12.0 fields
        with_reflection = [m for m in memories if m.get('reflection')]
        with_winning_side = [m for m in memories if m.get('winning_side')]
        with_judge_summary = [m for m in memories if m.get('entry_judge_summary')]
        with_original_lesson = [m for m in memories if m.get('original_lesson')]

        pct_refl = len(with_reflection) / total * 100 if total > 0 else 0
        pct_ws = len(with_winning_side) / total * 100 if total > 0 else 0

        print(f"     📊 反思覆盖率: {len(with_reflection)}/{total} ({pct_refl:.0f}%)")
        print(f"     📊 winning_side 覆盖率: {len(with_winning_side)}/{total} ({pct_ws:.0f}%)")
        print(f"     📊 entry_judge_summary: {len(with_judge_summary)}/{total}")
        print(f"     📊 original_lesson (模板保留): {len(with_original_lesson)}/{total}")

        # Show latest reflection example
        if with_reflection:
            latest_refl = with_reflection[-1]
            refl_text = latest_refl.get('reflection', '')[:100]
            refl_ts = str(latest_refl.get('timestamp', ''))[:19]
            refl_len = len(latest_refl.get('reflection', ''))
            print(f"\n     📝 最新反思示例 [{refl_ts}] ({refl_len}字):")
            print(f"        \"{refl_text}...\"")

            # Validate reflection quality (basic heuristics)
            if refl_len < 20:
                print(f"        ⚠️ 反思过短 ({refl_len}字 < 20字最低标准)")
                self.ctx.add_warning("反思记忆: 最新反思过短")
        else:
            print(f"\n     ℹ️ 尚无 LLM 反思 (首笔反思将在下次平仓后的 on_timer 生成)")

        # Check for winning_side consistency with decision
        if with_winning_side:
            inconsistent = 0
            for m in with_winning_side:
                ws = m.get('winning_side', '').upper()
                decision = m.get('decision', '').upper()
                # BULL → should lead to LONG, BEAR → SHORT
                if ws == 'BULL' and decision in ('SHORT', 'SELL'):
                    inconsistent += 1
                elif ws == 'BEAR' and decision in ('LONG', 'BUY'):
                    inconsistent += 1
            if inconsistent > 0:
                print(f"     ⚠️ winning_side 与 decision 不一致: {inconsistent} 条")
                print(f"        (可能来自 Risk Manager 否决或反转)")
            else:
                print(f"     ✅ winning_side 与 decision 一致性: 通过")

        # Check config
        try:
            # v12.0: Reflection is always enabled (hardcoded in strategy, not YAML-driven)
            print(f"\n     ⚙️ 配置状态:")
            print(f"        reflection.enabled: ✅ true (代码硬编码)")
            print(f"        max_reflection_chars: 150")
            print(f"        temperature: 0.3")
        except Exception as e:
            print(f"     ⚠️ 配置检查失败: {e}")

        # Check code: generate_reflection method exists
        if self.ctx.multi_agent is not None:
            has_gen = hasattr(self.ctx.multi_agent, 'generate_reflection')
            has_upd = hasattr(self.ctx.multi_agent, 'update_last_memory_reflection')
            print(f"\n     🧠 方法检查:")
            print(f"        generate_reflection: {'✅ 存在' if has_gen else '❌ 缺失'}")
            print(f"        update_last_memory_reflection: {'✅ 存在' if has_upd else '❌ 缺失'}")
            if not has_gen or not has_upd:
                self.ctx.add_warning("v12.0 反思方法缺失 — 请检查 agents/multi_agent_analyzer.py")

        # v12.0.1: Check backfill readiness (entries with evaluation but no reflection)
        entries_with_eval = [m for m in memories if m.get('evaluation')]
        backfill_candidates = [m for m in entries_with_eval if not m.get('reflection')]
        if backfill_candidates:
            print(f"\n     🔄 重启补回候选: {len(backfill_candidates)} 条 (有 evaluation 无 reflection)")
            print(f"        下次 on_timer 将自动补回最近 3 条")
        elif entries_with_eval:
            print(f"\n     ✅ 所有含 evaluation 的条目均已有 reflection")

        # v12.0: Verify reflection in both success and failure output
        # Read all agent files (core + 3 auxiliary after mixin split)
        _agent_files = ["multi_agent_analyzer.py", "prompt_constants.py",
                        "report_formatter.py", "mechanical_decide.py"]
        src = ""
        for _af in _agent_files:
            _ap = self.ctx.project_root / "agents" / _af
            if _ap.exists():
                src += _ap.read_text() + "\n"
        if src:
            # v12.0 uses _extract_role_reflection which outputs via:
            #   str(parsed[k])[:80] (structured JSON) and str(reflection)[:80] (plain text)
            insight_count = src.count("Insight:") + src.count("lesson:")
            if insight_count >= 2:
                print(f"     ✅ 成功/失败交易均显示 Insight 反思")
            else:
                print(f"     ⚠️ Insight 显示不完整 (期望 ≥2 处, 实际 {insight_count})")
                self.ctx.add_warning("v12.0: 失败交易可能缺少 Insight 字段")

    def _check_extended_reflections(self) -> None:
        """
        v18.0: Check extended reflections file health.

        Validates:
        - data/extended_reflections.json existence and format
        - Entry count and FIFO cap (max 100)
        - Latest entry fields (timestamp, trade_count, win_rate, insight)
        - Insight length (≤200 chars)
        """
        import json

        print()
        print("  🔍 v18.0 Extended Reflections 检查:")

        ext_refl_file = "data/extended_reflections.json"
        ext_path = self.ctx.project_root / ext_refl_file

        if not ext_path.exists():
            print(f"     ℹ️ 文件不存在: {ext_refl_file}")
            print(f"        → 将在 5 笔交易平仓后自动生成 (EXTENDED_REFLECTION_INTERVAL=5)")
            return

        try:
            with open(ext_path, 'r', encoding='utf-8') as f:
                ext_refl = json.load(f)
        except Exception as e:
            print(f"     ❌ 文件读取失败: {e}")
            self.ctx.add_warning(f"v18.0: Extended reflections 文件损坏: {e}")
            return

        if not isinstance(ext_refl, list):
            print(f"     ❌ 格式错误: 期望 list, 实际 {type(ext_refl).__name__}")
            self.ctx.add_warning("v18.0: Extended reflections 格式错误 (非 list)")
            return

        print(f"     📊 条目数量: {len(ext_refl)} (上限 100)")

        if not ext_refl:
            print(f"     ℹ️ 文件存在但为空")
            return

        # Validate latest entry
        latest = ext_refl[-1]
        required_fields = ['timestamp', 'trade_count', 'win_rate', 'insight']
        missing = [f for f in required_fields if f not in latest]
        if missing:
            print(f"     ❌ 最新条目缺少字段: {missing}")
            self.ctx.add_warning(f"v18.0: Extended reflection 缺失字段: {missing}")
        else:
            ts = str(latest.get('timestamp', ''))[:19]
            trade_count = latest.get('trade_count', 0)
            win_rate = latest.get('win_rate', 0)
            avg_rr = latest.get('avg_rr', 0)
            insight = latest.get('insight', '')
            insight_len = len(insight)

            print(f"     📝 最新条目 [{ts}]:")
            print(f"        trade_count: {trade_count}")
            print(f"        win_rate: {win_rate*100:.0f}%")
            print(f"        avg_rr: {avg_rr:.1f}:1")
            # v30.2: 200 is prompt guidance only, not enforced (zero-truncation policy)
            print(f"        insight: {insight_len} 字 (prompt 指引 ≤200)")

            # Quality checks
            issues = []
            if trade_count < 1:
                issues.append(f"trade_count={trade_count} (期望 ≥1)")
            if not (0 <= win_rate <= 1.0):
                issues.append(f"win_rate={win_rate} 超出 [0, 1] 范围")
            # v30.2: 200 is prompt guidance, not hard limit.
            # Only warn if AI completely ignores the constraint (>500 chars).
            if insight_len > 500:
                issues.append(f"insight {insight_len} 字远超 prompt 指引 (≤200)")
            if insight_len < 10:
                issues.append(f"insight 过短 ({insight_len} 字)")

            if issues:
                for issue in issues:
                    print(f"        ⚠️ {issue}")
                self.ctx.add_warning(f"v18.0: Extended reflection 质量问题: {'; '.join(issues)}")
            else:
                print(f"        ✅ 格式和质量检查通过")

            # Show insight preview
            if insight:
                preview = insight[:100]
                print(f"        \"{preview}{'...' if len(insight) > 100 else ''}\"")

        # FIFO cap check
        if len(ext_refl) > 100:
            print(f"     ⚠️ 条目数 {len(ext_refl)} 超出 FIFO 上限 100")
            self.ctx.add_warning(f"v18.0: Extended reflections 超出 FIFO 上限 ({len(ext_refl)} > 100)")

    def _check_recency_scoring(self) -> None:
        """
        v18.0: Check recency scoring infrastructure.

        Validates:
        - RECENCY_WEIGHT and RECENCY_HALF_LIFE_DAYS constants importable
        - Values within reasonable ranges
        - _ext_reflections_cache attribute exists on multi_agent
        """
        print()
        print("  🔍 v18.0 Recency Scoring 检查:")

        # Check constants
        try:
            from agents.prompt_constants import (
                RECENCY_WEIGHT,
                RECENCY_HALF_LIFE_DAYS,
                EXTENDED_REFLECTION_INTERVAL,
                EXTENDED_REFLECTIONS_MAX_COUNT,
            )
            print(f"     ✅ RECENCY_WEIGHT: {RECENCY_WEIGHT}")
            print(f"     ✅ RECENCY_HALF_LIFE_DAYS: {RECENCY_HALF_LIFE_DAYS}")
            print(f"     ✅ EXTENDED_REFLECTION_INTERVAL: {EXTENDED_REFLECTION_INTERVAL}")
            print(f"     ✅ EXTENDED_REFLECTIONS_MAX_COUNT: {EXTENDED_REFLECTIONS_MAX_COUNT}")

            # Range validation
            issues = []
            if not (0.5 <= RECENCY_WEIGHT <= 3.0):
                issues.append(f"RECENCY_WEIGHT={RECENCY_WEIGHT} 超出合理范围 [0.5, 3.0]")
            if not (7 <= RECENCY_HALF_LIFE_DAYS <= 60):
                issues.append(f"RECENCY_HALF_LIFE_DAYS={RECENCY_HALF_LIFE_DAYS} 超出合理范围 [7, 60]")
            if not (3 <= EXTENDED_REFLECTION_INTERVAL <= 20):
                issues.append(f"EXTENDED_REFLECTION_INTERVAL={EXTENDED_REFLECTION_INTERVAL} 超出合理范围 [3, 20]")

            if issues:
                for issue in issues:
                    print(f"     ⚠️ {issue}")
                    self.ctx.add_warning(f"v18.0: {issue}")
            else:
                print(f"     ✅ 所有常量在合理范围内")
        except ImportError as e:
            print(f"     ❌ 无法导入 v18.0 常量: {e}")
            self.ctx.add_warning(f"v18.0: 无法导入 recency/reflection 常量: {e}")

        # Check per-cycle cache attribute on multi_agent
        if self.ctx.multi_agent is not None:
            has_cache = hasattr(self.ctx.multi_agent, '_ext_reflections_cache')
            print(f"     {'✅' if has_cache else '❌'} _ext_reflections_cache 属性: {'存在' if has_cache else '缺失'}")
            if not has_cache:
                self.ctx.add_warning("v18.0: multi_agent 缺少 _ext_reflections_cache 属性 (F3 缓存)")

    def _check_analyzer_memory(self) -> None:
        """Check MultiAgentAnalyzer memory system (v5.10)."""
        print()
        print("  🧠 MultiAgentAnalyzer 记忆系统状态:")

        # Initialize multi_agent if needed (just for memory check, no API call)
        if self.ctx.multi_agent is None:
            try:
                from agents.multi_agent_analyzer import MultiAgentAnalyzer as MAAnalyzer
                import os

                # v46.0: mechanical mode, no AI params needed
                self.ctx.multi_agent = MAAnalyzer()
            except Exception as e:
                print(f"     ⚠️ multi_agent 初始化失败: {e}")

        if self.ctx.multi_agent is not None:
            mem_count = len(getattr(self.ctx.multi_agent, 'decision_memory', []))
            mem_file = getattr(self.ctx.multi_agent, 'memory_file', 'N/A')
            print(f"     → 已加载记忆: {mem_count} 条")
            print(f"     → 记忆文件: {mem_file}")

            # v5.10+: Test _get_past_memories with current_conditions
            # (AnalysisContext refactor: uses MemoryConditions.from_feature_dict())
            if hasattr(self.ctx.multi_agent, '_get_past_memories'):
                current_conditions = None
                try:
                    # v49.0: analysis_context deleted, skip MemoryConditions
                    print(f"     → MemoryConditions: skipped (mechanical mode)")
                except Exception as e:
                    print(f"     → MemoryConditions: ⚠️ {e}")

                # Call with current_conditions (v5.10 similarity mode)
                try:
                    past_memories = self.ctx.multi_agent._get_past_memories(current_conditions)
                except TypeError:
                    # Fallback for older versions without current_conditions param
                    past_memories = self.ctx.multi_agent._get_past_memories()

                if past_memories:
                    print(f"     → 传给 AI 的记忆摘要: {len(past_memories)} 字符")
                    # Check for similarity scores in output (v5.10)
                    has_similarity = "sim=" in past_memories
                    if has_similarity:
                        print(f"     → v5.10 相似度检索: ✅ 已激活 (sim= 分数可见)")
                    elif mem_count >= 20:
                        print(f"     → v5.10 相似度检索: ⚠️ 记忆 >= 20 条但未见 sim= 分数")
                    else:
                        print(f"     → v5.10 相似度检索: ℹ️ 记忆 < 20 条，使用最近模式")
                    preview = past_memories[:200].replace('\n', ' ')
                    print(f"     → 预览: {preview}...")

                    # v6.0: Memory injection format validation (GAP #6/#7 fix)
                    self._validate_memory_format(past_memories, mem_count)
                else:
                    print(f"     → 传给 AI 的记忆摘要: (空 - 无历史交易)")

                # v12.0: Test per-agent role-annotated _get_past_memories
                self._check_per_agent_role_annotations(current_conditions)

            # v5.10: Check _score_memory method exists
            has_score = hasattr(self.ctx.multi_agent, '_score_memory')
            print(f"     → v5.10 _score_memory 方法: {'✅ 存在' if has_score else '❌ 缺失'}")
        else:
            print(f"     ⚠️ multi_agent 未初始化 (缺少 DEEPSEEK_API_KEY?)")

    def _check_per_agent_role_annotations(self, current_conditions) -> None:
        """
        v12.0: Test that _get_past_memories() produces role-specific annotations
        for bull, bear, judge, and risk agents.
        """
        print()
        print("     🔍 v12.0 Per-Agent 角色标注测试:")

        import inspect
        # Check if _get_past_memories accepts agent_role parameter
        sig = inspect.signature(self.ctx.multi_agent._get_past_memories)
        has_agent_role_param = 'agent_role' in sig.parameters
        print(f"       agent_role 参数: {'✅ 存在' if has_agent_role_param else '❌ 缺失'}")

        if not has_agent_role_param:
            self.ctx.add_warning("v12.0: _get_past_memories 缺少 agent_role 参数")
            return

        roles = ['bull', 'bear', 'judge', 'entry_timing', 'risk']
        role_results = {}

        for role in roles:
            try:
                role_memories = self.ctx.multi_agent._get_past_memories(
                    current_conditions, agent_role=role,
                )
                role_results[role] = role_memories
                length = len(role_memories) if role_memories else 0
                print(f"       {role:>13}: {length} 字符", end="")

                # Check for role-specific markers
                if role_memories:
                    if role == 'bull':
                        has_marker = '🎯' in role_memories or '⚠️' in role_memories or '📝' in role_memories
                    elif role == 'bear':
                        has_marker = '🎯' in role_memories or '⚠️' in role_memories or '📝' in role_memories
                    elif role == 'judge':
                        has_marker = '✅' in role_memories or '❌' in role_memories
                    elif role == 'entry_timing':
                        has_marker = '⏱️' in role_memories or 'MAE' in role_memories or 'MFE' in role_memories
                    elif role == 'risk':
                        has_marker = '📊' in role_memories or 'MAE' in role_memories or 'SL=' in role_memories
                    else:
                        has_marker = False

                    if has_marker:
                        print(f" ✅ 角色标注可见")
                    else:
                        print(f" ⚠️ 未见角色标注 (可能无足够数据)")
                else:
                    print(f" ℹ️ 空")
            except Exception as e:
                print(f"       {role:>5}: ❌ 错误 - {e}")
                role_results[role] = None

        # Check that different roles produce different content
        non_empty = {r: v for r, v in role_results.items() if v}
        if len(non_empty) >= 2:
            values = list(non_empty.values())
            all_same = all(v == values[0] for v in values[1:])
            if all_same:
                print(f"       ⚠️ 所有角色返回相同内容 (角色标注可能未生效)")
                self.ctx.add_warning("v12.0: 所有角色记忆内容相同，角色标注可能未生效")
            else:
                print(f"       ✅ 角色差异化: 不同角色返回不同内容")

    def _validate_memory_format(self, past_memories: str, mem_count: int) -> None:
        """
        v6.0: Validate memory injection format quality (GAP #6/#7 fix).

        Checks that injected memory text contains proper structure:
        - Trade entries with date, direction, PnL
        - Lesson/reflection text
        - Similarity scores (if v5.10 active)
        """
        print()
        print("     🔍 v6.0 记忆注入格式验证:")

        issues = []

        # Check for basic structure markers
        import re
        has_trade_entries = bool(re.search(r'(Trade|交易|#\d|✅|❌|SUCCESSFUL|FAILED)', past_memories))
        has_pnl = bool(re.search(r'(PnL|pnl|profit|loss|盈|亏|[+-]\d+\.?\d*%)', past_memories, re.IGNORECASE))
        has_direction = bool(re.search(r'(LONG|SHORT|BUY|SELL|做多|做空)', past_memories, re.IGNORECASE))
        has_lesson = bool(re.search(r'(lesson|Lesson|Insight|反思|教训|经验)', past_memories, re.IGNORECASE))

        checks = [
            ("交易条目标识", has_trade_entries),
            ("盈亏数据 (PnL)", has_pnl),
            ("方向信息 (LONG/SHORT)", has_direction),
            ("经验教训 (Lesson)", has_lesson),
        ]

        for name, passed in checks:
            status = "✅" if passed else "⚠️"
            print(f"       {status} {name}")
            if not passed:
                issues.append(name)

        # Check for empty or suspiciously short content
        if len(past_memories) < 50 and mem_count > 0:
            issues.append(f"记忆内容过短 ({len(past_memories)} chars, {mem_count} entries)")
            print(f"       ⚠️ 内容过短: {len(past_memories)} 字符 ({mem_count} 条记录)")

        if issues:
            self.ctx.add_warning(f"记忆格式问题: {', '.join(issues)}")
        else:
            print(f"       ✅ 记忆注入格式完整")

    def should_skip(self) -> bool:
        return self.ctx.summary_mode


class NakedPositionScanner(DiagnosticStep):
    """
    v6.2: Cross-check Binance position vs open orders for SL protection.

    A 'naked position' is one with no stop-loss order — the highest risk
    scenario. This scanner:
    1. Gets open position from Binance
    2. Gets all open orders for the symbol
    3. Checks if at least one STOP_MARKET / STOP order exists on the correct side
    4. Alerts if position has no protective SL order
    """

    name = "v6.2 裸仓检测 (持仓 vs 挂单 SL 交叉验证)"

    def run(self) -> bool:
        print("-" * 70)

        # Only run if we detected a position in Phase 5
        if not self.ctx.current_position:
            print("  ✅ 无持仓 — 无需裸仓检测")
            return True

        pos_side = self.ctx.current_position.get('side', '')
        pos_qty = self.ctx.current_position.get('quantity', 0)
        if not pos_side or pos_qty == 0:
            print("  ✅ 无有效持仓 — 无需裸仓检测")
            return True

        print(f"  📊 检测到持仓: {pos_side.upper()} {pos_qty}")
        print()

        try:
            import os
            import hmac
            import hashlib
            import time
            import requests

            api_key = os.getenv('BINANCE_API_KEY', '')
            api_secret = os.getenv('BINANCE_API_SECRET', '')

            if not api_key or not api_secret:
                print("  ⚠️ 无 API key — 跳过裸仓检测")
                return True

            # Fetch open orders from Binance Futures (regular + Algo API)
            base_url = "https://fapi.binance.com"
            headers = {"X-MBX-APIKEY": api_key}

            # 1) Regular orders: /fapi/v1/openOrders
            timestamp = int(time.time() * 1000)
            params = f"symbol={self.ctx.symbol}&timestamp={timestamp}"
            signature = hmac.new(api_secret.encode(), params.encode(), hashlib.sha256).hexdigest()
            url = f"{base_url}/fapi/v1/openOrders?{params}&signature={signature}"

            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code != 200:
                print(f"  ⚠️ Binance API 错误 ({resp.status_code}) — 跳过裸仓检测")
                return True

            open_orders = resp.json()

            # 2) Algo orders: /fapi/v1/openAlgoOrders (SL/TP via Algo API)
            algo_orders = []
            try:
                timestamp2 = int(time.time() * 1000)
                params2 = f"symbol={self.ctx.symbol}&timestamp={timestamp2}"
                sig2 = hmac.new(api_secret.encode(), params2.encode(), hashlib.sha256).hexdigest()
                url2 = f"{base_url}/fapi/v1/openAlgoOrders?{params2}&signature={sig2}"
                resp2 = requests.get(url2, headers=headers, timeout=10)
                if resp2.status_code == 200:
                    algo_data = resp2.json()
                    algo_orders = algo_data.get('orders', algo_data) if isinstance(algo_data, dict) else algo_data
                    if not isinstance(algo_orders, list):
                        algo_orders = []
            except Exception:
                pass  # Algo endpoint optional — don't block diagnosis

            # Categorize orders
            sl_orders = []
            tp_orders = []
            entry_orders = []

            # Regular orders
            for order in open_orders:
                order_type = order.get('type', '')
                order_side = order.get('side', '').upper()
                reduce_only = order.get('reduceOnly', False)

                if order_type in ('STOP_MARKET', 'STOP') and reduce_only:
                    sl_orders.append(order)
                elif order_type in ('TAKE_PROFIT_MARKET', 'TAKE_PROFIT', 'LIMIT') and reduce_only:
                    tp_orders.append(order)
                elif not reduce_only:
                    entry_orders.append(order)

            # Algo orders (different field names: orderType, triggerPrice)
            # Binance Algo API: algoType="CONDITIONAL" for all conditional orders,
            # orderType="STOP_MARKET"/"TAKE_PROFIT" etc. is the actual order type
            for algo in algo_orders:
                order_type = algo.get('orderType', algo.get('algoType', ''))
                algo_status = algo.get('algoStatus', algo.get('status', ''))
                if algo_status not in ('', 'WORKING', 'NEW'):
                    continue
                if order_type in ('STOP_MARKET', 'STOP'):
                    sl_orders.append(algo)
                elif order_type in ('TAKE_PROFIT_MARKET', 'TAKE_PROFIT'):
                    tp_orders.append(algo)

            print(f"  📋 挂单统计:")
            print(f"     SL 订单 (STOP + reduce_only): {len(sl_orders)}")
            print(f"     TP 订单 (TP/LIMIT + reduce_only): {len(tp_orders)}")
            print(f"     入场订单 (非 reduce_only): {len(entry_orders)}")
            print()

            # Check for naked position (no SL)
            has_sl = len(sl_orders) > 0
            has_tp = len(tp_orders) > 0

            if has_sl:
                for sl in sl_orders:
                    # Handle both regular (stopPrice/origQty) and Algo (triggerPrice/quantity)
                    stop_price = sl.get('stopPrice', sl.get('triggerPrice', 'N/A'))
                    sl_qty = sl.get('origQty', sl.get('quantity', 'N/A'))
                    source = 'algo' if 'algoId' in sl or 'algoType' in sl else 'regular'
                    print(f"  ✅ SL 保护单: stop_price=${float(stop_price):,.2f}, qty={sl_qty} ({source})")
            else:
                print(f"  🔴 ⚠️ 裸仓警告! 持仓 {pos_side.upper()} 无 SL 保护单!")
                print(f"     → 如果价格反向运行，没有止损保护")
                print(f"     → 可能原因: SL 提交失败 / 被意外取消 / 系统重启后未恢复")
                total_orders = len(open_orders) + len(algo_orders)
                self.ctx.add_error(
                    f"裸仓检测: {pos_side.upper()} 持仓 qty={pos_qty} 无 SL 保护单 "
                    f"(共 {total_orders} 挂单 [regular={len(open_orders)}, algo={len(algo_orders)}], SL=0)"
                )

            if has_tp:
                for tp in tp_orders:
                    tp_price_val = tp.get('price', tp.get('stopPrice', tp.get('triggerPrice', 'N/A')))
                    tp_qty = tp.get('origQty', tp.get('quantity', 'N/A'))
                    source = 'algo' if 'algoId' in tp or 'algoType' in tp else 'regular'
                    print(f"  ✅ TP 保护单: price=${float(tp_price_val):,.2f}, qty={tp_qty} ({source})")

            # Check SL quantity vs position quantity (v7.2 per-layer aware)
            # v7.2: Each layer has its own SL order. Multiple SL orders are normal
            # when there are multiple layers (pyramiding). The key check is:
            # 1. At least one SL order covers the position quantity
            # 2. Total SL >= position (over-protection is safe due to reduce_only)
            if has_sl:
                total_sl_qty = sum(float(sl.get('origQty', sl.get('quantity', 0))) for sl in sl_orders)
                n_sl = len(sl_orders)
                if pos_qty > 0 and abs(total_sl_qty - pos_qty) / pos_qty < 0.02:
                    print(f"  ✅ SL 数量匹配: SL qty={total_sl_qty:.4f} ≈ 持仓 qty={pos_qty:.4f}")
                elif total_sl_qty >= pos_qty:
                    # v7.2: Multiple layers → total SL > position is normal.
                    # reduce_only ensures no over-sell risk. Orphaned SL orders
                    # from closed layers are harmless (auto-rejected when filled
                    # against zero remaining position).
                    print(f"  ✅ SL 覆盖充足: {n_sl} 个 SL 单 (总量={total_sl_qty:.4f}) "
                          f"≥ 持仓 qty={pos_qty:.4f} (v7.2 per-layer)")
                    if total_sl_qty > pos_qty * 2:
                        print(f"  ℹ️ 注意: SL 总量是持仓的 {total_sl_qty/pos_qty:.1f}× "
                              f"(可能有已关闭层的孤儿 SL，reduce_only 保护无风险)")
                else:
                    # total SL < position → under-protected
                    print()
                    print(f"  ⚠️ SL 保护不足: SL qty={total_sl_qty:.4f} < 持仓 qty={pos_qty:.4f}")
                    self.ctx.add_warning(
                        f"SL 保护不足: SL={total_sl_qty:.4f} < 持仓={pos_qty:.4f}"
                    )

            return has_sl

        except Exception as e:
            self.ctx.add_warning(f"裸仓检测异常: {e}")
            print(f"  ⚠️ 裸仓检测异常: {e}")
            return True  # Non-critical
