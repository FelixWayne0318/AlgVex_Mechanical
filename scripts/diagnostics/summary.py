"""
Summary Module (v6.0)

Generates comprehensive diagnostic summaries and analysis.
Includes v6.0 machine-readable JSON output with position management metrics.
"""

import json
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .base import (
    DiagnosticContext,
    DiagnosticStep,
    print_box,
    print_wrapped,
    safe_float,
)


class DataFlowSummary(DiagnosticStep):
    """
    Generate complete data flow summary.

    Outputs all collected data values in a structured format.
    """

    name = "完整数据流汇总"

    def run(self) -> bool:
        print("-" * 70)
        print()

        self._print_technical_data()
        self._print_sentiment_data()
        self._print_order_flow_data()
        self._print_derivatives_data()
        self._print_position_data()
        self._print_ai_decision()
        self._print_mtf_status()

        print()
        print("  ✅ 完整数据流汇总完成")
        return True

    def _print_technical_data(self) -> None:
        """Print technical indicator data."""
        print_box("技术指标数据")
        print()
        td = self.ctx.technical_data

        print(f"  价格数据:")
        print(f"    当前价格: ${self.ctx.current_price:,.2f}")
        print(f"    24H 最高: ${self.ctx.price_data.get('high', 0):,.2f}")
        print(f"    24H 最低: ${self.ctx.price_data.get('low', 0):,.2f}")
        print(f"    价格变化: {self.ctx.price_data.get('price_change', 0):.2f}%")
        print()

        print(f"  移动平均线:")
        print(f"    SMA_5:  ${td.get('sma_5', 0):,.2f}")
        print(f"    SMA_20: ${td.get('sma_20', 0):,.2f}")
        print(f"    SMA_50: ${td.get('sma_50', 0):,.2f}")
        print(f"    EMA_12: ${td.get('ema_12', 0):,.2f}")
        print(f"    EMA_26: ${td.get('ema_26', 0):,.2f}")
        print()

        print(f"  震荡指标:")
        print(f"    RSI:           {td.get('rsi', 0):.2f}")
        print(f"    MACD:          {td.get('macd', 0):.4f}")
        print(f"    MACD Signal:   {td.get('macd_signal', 0):.4f}")
        print(f"    MACD Histogram:{td.get('macd_histogram', 0):.4f}")
        print()

        print(f"  布林带:")
        print(f"    BB Upper: ${td.get('bb_upper', 0):,.2f}")
        print(f"    BB Middle: ${td.get('bb_middle', 0):,.2f}")
        print(f"    BB Lower: ${td.get('bb_lower', 0):,.2f}")
        print()

        print(f"  支撑/阻力:")
        print(f"    支撑位: ${td.get('support', 0):,.2f}")
        print(f"    阻力位: ${td.get('resistance', 0):,.2f}")
        print()
        print(f"  趋势判断: {td.get('overall_trend', 'N/A')}")

    def _print_sentiment_data(self) -> None:
        """Print sentiment data."""
        print()
        print_box("情绪数据")
        print()
        sd = self.ctx.sentiment_data

        print(f"  Binance 多空比:")
        print(f"    Long/Short Ratio: {sd.get('long_short_ratio', 0):.4f}")
        print(f"    Long Account %:   {sd.get('positive_ratio', 0)*100:.2f}%")
        print(f"    Short Account %:  {sd.get('negative_ratio', 0)*100:.2f}%")
        print(f"    Net Sentiment:    {sd.get('net_sentiment', 0):.4f}")
        print(f"    数据来源: {sd.get('source', 'N/A')}")

    def _print_order_flow_data(self) -> None:
        """Print order flow data."""
        if not self.ctx.order_flow_report:
            return

        print()
        print_box("订单流数据")
        print()
        of = self.ctx.order_flow_report

        bars_count = of.get('bars_count', 10)
        print(f"  Binance Taker 数据 [采样窗口: {bars_count} bars]:")
        print(f"    Buy Ratio:      {of.get('buy_ratio', 0):.4f} ({of.get('buy_ratio', 0)*100:.2f}%)")
        print(f"    CVD Trend:      {of.get('cvd_trend', 'N/A')}")
        print(f"    Avg Trade Size: ${of.get('avg_trade_usdt', 0):,.2f}")
        print(f"    Volume (USDT):  ${of.get('volume_usdt', 0):,.0f}")
        print(f"    数据来源: {of.get('data_source', 'N/A')}")

        # v18 Item 16: 4H CVD order flow
        of_4h = getattr(self.ctx, 'order_flow_report_4h', None)
        if of_4h:
            print()
            print(f"  4H CVD 订单流 (v18 Item 16):")
            print(f"    Buy Ratio:      {of_4h.get('buy_ratio', 0):.4f}")
            print(f"    CVD Trend:      {of_4h.get('cvd_trend', 'N/A')}")
            print(f"    Volume (USDT):  ${of_4h.get('volume_usdt', 0):,.0f}")

    def _print_derivatives_data(self) -> None:
        """Print derivatives data."""
        if not self.ctx.derivatives_report and not self.ctx.binance_funding_rate:
            return

        print()
        print_box("衍生品数据")
        print()
        dr = self.ctx.derivatives_report or {}

        oi_data = dr.get('open_interest', {})
        liq_data = dr.get('liquidations', {})

        print(f"  Open Interest (Coinalyze):")
        if oi_data:
            bc = self.ctx.base_currency
            oi_val = float(oi_data.get('value', 0) or 0)
            oi_usd = oi_data.get('total_usd', 0)
            if not oi_usd and oi_val > 0:
                oi_usd = oi_val * (self.ctx.current_price or 0)
            print(f"    OI:          ${oi_usd:,.0f} ({oi_val:,.2f} {bc})")
            print(f"    OI Change:   {oi_data.get('change_pct', 'N/A')}")
        else:
            print(f"    (数据不可用)")

        # v5.1: Binance funding rate (settled + predicted)
        print()
        print(f"  Funding Rate (Binance):")
        if self.ctx.binance_funding_rate:
            fr = self.ctx.binance_funding_rate
            settled_pct = fr.get('funding_rate_pct', 0)
            predicted_pct = fr.get('predicted_rate_pct', 0)
            print(f"    Settled:     {settled_pct:.5f}%")
            print(f"    Predicted:   {predicted_pct:.5f}%")
            print(f"    Source:      binance_direct")
        else:
            print(f"    (数据不可用)")

        print()
        print(f"  Liquidations (1h):")
        if liq_data:
            history = liq_data.get('history', [])
            if history:
                latest = history[-1]
                long_btc = float(latest.get('l', 0))
                short_btc = float(latest.get('s', 0))
                long_usd = long_btc * self.ctx.current_price
                short_usd = short_btc * self.ctx.current_price
                bc = self.ctx.base_currency
                print(f"    Long:   ${long_usd:,.0f} ({long_btc:.4f} {bc})")
                print(f"    Short:  ${short_usd:,.0f} ({short_btc:.4f} {bc})")
            else:
                print(f"    (无爆仓记录)")
        else:
            print(f"    (数据不可用)")

    def _print_position_data(self) -> None:
        """
        Print current position data.

        v4.8.1: Updated to use correct field names and display all v4.5/v4.7 fields
        """
        print()
        print_box("当前持仓 & v4.8 仓位状态")
        print()

        # v4.8.1: Use correct field names (max_position_value, available_capacity)
        leverage = self.ctx.binance_leverage
        ctx = self.ctx.account_context or {}
        equity = ctx.get('equity', 0)
        max_position_value = ctx.get('max_position_value', 0)

        print(f"  v4.8 仓位参数:")
        print(f"    杠杆 (Binance): {leverage}x")
        print(f"    资金 (equity):  ${equity:,.2f}")
        print(f"    max_position_value: ${max_position_value:,.2f}")

        if self.ctx.current_position:
            pos = self.ctx.current_position
            position_value = pos.get('position_value_usdt', 0)
            available_capacity = ctx.get('available_capacity', max(0, max_position_value - position_value))

            print()
            print(f"  持仓状态: 有持仓")
            # === Basic (4 fields) ===
            print(f"    方向:     {pos.get('side', 'N/A').upper()}")
            bc = self.ctx.base_currency
            qty = pos.get('quantity', 0)
            notional = float(qty) * float(pos.get('avg_px', 0))
            print(f"    数量:     ${notional:,.0f} ({float(qty):.6f} {bc})")
            print(f"    持仓价值: ${position_value:,.2f}")
            print(f"    入场价:   ${pos.get('avg_px', 0):,.2f}")
            print(f"    未实现PnL: ${pos.get('unrealized_pnl', 0):,.2f}")
            # v4.8.1: Use correct field name pnl_percentage
            print(f"    盈亏比例: {pos.get('pnl_percentage', 0):+.2f}%")

            # === v4.5 Tier 1 fields ===
            print()
            print(f"  v4.5 Tier 1 数据:")
            duration = pos.get('duration_minutes')
            if duration is not None:
                hours = duration // 60
                mins = duration % 60
                print(f"    持仓时长: {hours}h {mins}m")
            else:
                print(f"    持仓时长: (诊断脚本不可用)")

            sl_price = pos.get('sl_price')
            tp_price = pos.get('tp_price')
            rr_ratio = pos.get('risk_reward_ratio')
            if sl_price:
                print(f"    止损价:   ${sl_price:,.2f}")
            if tp_price:
                print(f"    止盈价:   ${tp_price:,.2f}")
            if rr_ratio:
                print(f"    风险收益比: 1:{rr_ratio:.2f}")

            # === v4.5 Tier 2 fields ===
            print()
            print(f"  v4.5 Tier 2 数据:")
            peak_pnl = pos.get('peak_pnl_pct')
            worst_pnl = pos.get('worst_pnl_pct')
            entry_conf = pos.get('entry_confidence')
            margin_pct = pos.get('margin_used_pct')

            if peak_pnl is not None:
                print(f"    峰值盈亏: {peak_pnl:+.2f}%")
            if worst_pnl is not None:
                print(f"    最差盈亏: {worst_pnl:+.2f}%")
            if entry_conf:
                print(f"    入场信心: {entry_conf}")
            if margin_pct is not None:
                print(f"    保证金占用: {margin_pct:.1f}%")

            # === v4.7 Liquidation Risk ===
            print()
            print(f"  v4.7 爆仓风险:")
            liq_price = pos.get('liquidation_price')
            liq_buffer = pos.get('liquidation_buffer_pct')
            is_risk_high = pos.get('is_liquidation_risk_high', False)

            if liq_price:
                print(f"    爆仓价:   ${liq_price:,.2f}")
            if liq_buffer is not None:
                risk_emoji = "🔴" if is_risk_high else "🟢"
                print(f"    爆仓距离: {risk_emoji} {liq_buffer:.1f}%")
                if is_risk_high:
                    print(f"    ⚠️ 警告: 爆仓风险高 (<10%)")

            # === v5.1 Funding Rate ===
            print()
            print(f"  资金费率影响:")
            fr_current = pos.get('funding_rate_current')
            daily_cost = pos.get('daily_funding_cost_usd')
            cumulative = pos.get('funding_rate_cumulative_usd')
            effective_pnl = pos.get('effective_pnl_after_funding')

            if fr_current is not None:
                print(f"    已结算费率: {fr_current*100:+.4f}%")
            if daily_cost is not None:
                print(f"    日资金费用: ${daily_cost:,.2f}")
            if cumulative is not None:
                print(f"    累计资金费: ${cumulative:,.2f}")
            if effective_pnl is not None:
                print(f"    扣费后PnL: ${effective_pnl:,.2f}")

            # === v4.7 Drawdown ===
            print()
            print(f"  v4.7 回撤分析:")
            max_dd = pos.get('max_drawdown_pct')
            dd_bars = pos.get('max_drawdown_duration_bars')
            lower_lows = pos.get('consecutive_lower_lows', 0)

            if max_dd is not None:
                print(f"    最大回撤: {max_dd:.2f}%")
            if dd_bars is not None:
                print(f"    回撤持续: {dd_bars} bars")
            print(f"    连续新低: {lower_lows} bars")

            # === v4.8 累加模式 ===
            print()
            print(f"  v4.8 累加模式:")
            capacity_pct = ctx.get('capacity_used_pct', 0)
            if max_position_value > 0 and capacity_pct == 0:
                capacity_pct = (position_value / max_position_value * 100)
            print(f"    容量使用率: {capacity_pct:.1f}%")
            print(f"    可用容量: ${available_capacity:,.2f}")
            if available_capacity <= 0:
                print(f"    ⚠️ 已达上限，无法加仓")
        else:
            print()
            print(f"  持仓状态: 无持仓 (FLAT)")
            print(f"  v4.8 累加模式: 可开首仓")

    def _print_ai_decision(self) -> None:
        """Print AI decision results."""
        print()
        print_box("AI 决策结果")
        print()
        sd = self.ctx.signal_data

        print(f"  原始信号: {sd.get('signal', 'N/A')}")
        print(f"  最终信号: {self.ctx.final_signal}")
        print(f"  信心等级: {sd.get('confidence', 'N/A')}")
        print(f"  风险等级: {sd.get('risk_level', 'N/A')}")

        judge = sd.get('judge_decision', {})
        print(f"  胜出方:   {judge.get('winning_side', 'N/A')}")
        print()

        # SL/TP
        signal = sd.get('signal', 'HOLD')
        sltp_note = " (仅供参考，HOLD 不使用)" if signal == 'HOLD' else ""

        sl = safe_float(sd.get('stop_loss'))
        tp = safe_float(sd.get('take_profit'))
        if sl:
            print(f"  AI 止损: ${sl:,.2f}{sltp_note}")
        else:
            print(f"  AI 止损: N/A")
        if tp:
            print(f"  AI 止盈: ${tp:,.2f}{sltp_note}")
        else:
            print(f"  AI 止盈: N/A")

        print()
        print(f"  关键理由:")
        key_reasons = judge.get('key_reasons', [])
        for i, reason in enumerate(key_reasons[:3], 1):
            print(f"    {i}. {reason[:70]}...")

        risks = judge.get('acknowledged_risks', [])
        if risks:
            print()
            print(f"  确认风险:")
            for i, risk in enumerate(risks[:2], 1):
                print(f"    {i}. {risk[:70]}...")

        # v3.27: Invalidation field (nof1 alignment)
        invalidation = sd.get('invalidation', '')
        if invalidation:
            print()
            print(f"  ⛔ 失效条件: {invalidation[:100]}{'...' if len(invalidation) > 100 else ''}")

        print()
        reason = sd.get('reason', 'N/A')
        print(f"  决策理由: {reason[:100]}...")

    def _print_mtf_status(self) -> None:
        """Print MTF filter status."""
        print()
        print_box("MTF 过滤状态")
        print()

        print(f"  架构: TradingAgents - Pure Knowledge Prompts + R/R 驱动入场")
        print(f"  入场标准: R/R >= 2.0:1 由 calculate_mechanical_sltp() 构造性保证 (ATR × confidence)")
        print(f"  AI 决策: 纯知识描述 prompts (无 MUST/NEVER/ALWAYS 指令)")
        print(f"  输出格式: 包含 invalidation 字段 (nof1 对齐)")
        print()

        sd = self.ctx.signal_data
        print(f"  AI 决策: {sd.get('signal')} (Confidence: {sd.get('confidence')})")
        judge = sd.get('judge_decision', {})
        print(f"  Winning Side: {judge.get('winning_side', 'N/A')}")

    def should_skip(self) -> bool:
        return self.ctx.summary_mode


class DeepAnalysis(DiagnosticStep):
    """
    Deep analysis of signal conditions.

    Provides detailed reasoning for the current signal.
    """

    name = "深入分析: 信号产生条件"

    def run(self) -> bool:
        print()
        print("=" * 70)
        print("  📋 深入分析: 信号产生条件")
        print("=" * 70)
        print()

        self._analyze_technical_indicators()
        self._analyze_trend()
        self._analyze_sentiment()
        self._analyze_judge_decision()
        # v2.4.6: 移除 _analyze_trigger_conditions() - 误导性内容，暗示存在硬编码规则
        self._provide_recommendations()

        return True

    def _analyze_technical_indicators(self) -> None:
        """Analyze technical indicator thresholds."""
        print("[分析1] 技术指标阈值检查")
        print("-" * 50)

        td = self.ctx.technical_data
        cfg = self.ctx.strategy_config

        rsi = td.get('rsi', 50)
        rsi_upper = getattr(cfg, 'rsi_extreme_threshold_upper', 70)
        rsi_lower = getattr(cfg, 'rsi_extreme_threshold_lower', 30)

        print(f"  RSI: {rsi:.2f}")
        print(f"    参考阈值: 超卖<{rsi_lower}, 超买>{rsi_upper}")

        if rsi > rsi_upper:
            print(f"    → 🔴 超买区 (>{rsi_upper})")
        elif rsi < rsi_lower:
            print(f"    → 🟢 超卖区 (<{rsi_lower})")
        else:
            print(f"    → ⚪ 中性区间 ({rsi_lower}-{rsi_upper})")

        macd = td.get('macd', 0)
        macd_signal = td.get('macd_signal', 0)
        macd_hist = td.get('macd_histogram', 0)

        print()
        print(f"  MACD: {macd:.4f}")
        print(f"  MACD Signal: {macd_signal:.4f}")
        if macd > macd_signal:
            print("    → 🟢 MACD 在信号线上方")
        else:
            print("    → 🔴 MACD 在信号线下方")

        if macd_hist > 0:
            print(f"    → 🟢 柱状图为正 (+{macd_hist:.4f})")
        else:
            print(f"    → 🔴 柱状图为负 ({macd_hist:.4f})")

        # SMA analysis
        print()
        sma_5 = td.get('sma_5', 0)
        sma_20 = td.get('sma_20', 0)
        sma_50 = td.get('sma_50', 0)
        price = self.ctx.current_price

        print(f"  SMA_5: ${sma_5:,.2f}")
        print(f"  SMA_20: ${sma_20:,.2f}")
        print(f"  SMA_50: ${sma_50:,.2f}")
        print(f"  当前价格: ${price:,.2f}")

        if price > sma_5 > sma_20 > sma_50:
            print("    → 🟢 完美多头排列 (价格 > SMA5 > SMA20 > SMA50)")
        elif price < sma_5 < sma_20 < sma_50:
            print("    → 🔴 完美空头排列 (价格 < SMA5 < SMA20 < SMA50)")
        else:
            print("    → ⚪ 无明确趋势排列")

        # Bollinger Bands
        print()
        bb_upper = td.get('bb_upper', 0)
        bb_lower = td.get('bb_lower', 0)
        bb_width = bb_upper - bb_lower if bb_upper and bb_lower else 0
        bb_position = ((price - bb_lower) / bb_width * 100) if bb_width > 0 else 50

        print(f"  BB Upper: ${bb_upper:,.2f}")
        print(f"  BB Lower: ${bb_lower:,.2f}")
        print(f"  价格在带内位置: {bb_position:.1f}%")

        if bb_position > self.ctx.bb_overbought_threshold:
            print(f"    → 🔴 接近上轨 (>{self.ctx.bb_overbought_threshold}%, 可能超买)")
        elif bb_position < self.ctx.bb_oversold_threshold:
            print(f"    → 🟢 接近下轨 (<{self.ctx.bb_oversold_threshold}%, 可能超卖)")
        else:
            print("    → ⚪ 带内中间区域")

    def _analyze_trend(self) -> None:
        """Analyze trend strength."""
        print()
        print("[分析2] 趋势强度分析")
        print("-" * 50)

        td = self.ctx.technical_data
        trend = td.get('overall_trend', 'N/A')
        print(f"  整体趋势判断: {trend}")

        bars = self.ctx.indicator_manager.recent_bars if self.ctx.indicator_manager else []

        if len(bars) >= 10:
            price_10_bars_ago = float(bars[-10].close)
            price_change = ((self.ctx.current_price - price_10_bars_ago) / price_10_bars_ago) * 100
            print(f"  近10根K线变化: {price_change:+.2f}%")
        else:
            print(f"  近10根K线变化: N/A (K线数量不足)")

        if len(bars) >= 20:
            price_20_bars_ago = float(bars[-20].close)
            price_change = ((self.ctx.current_price - price_20_bars_ago) / price_20_bars_ago) * 100
            print(f"  近20根K线变化: {price_change:+.2f}%")

    def _analyze_sentiment(self) -> None:
        """Analyze market sentiment."""
        print()
        print("[分析3] 市场情绪分析")
        print("-" * 50)

        ls_ratio = self.ctx.sentiment_data.get('long_short_ratio', 1.0)
        print(f"  多空比: {ls_ratio:.4f}")

        if ls_ratio > self.ctx.ls_ratio_extreme_bullish:
            print(f"    → 🔴 极度看多 (>{self.ctx.ls_ratio_extreme_bullish}, 逆向指标: 可能下跌)")
        elif ls_ratio > self.ctx.ls_ratio_bullish:
            print(f"    → 🟡 偏多 (>{self.ctx.ls_ratio_bullish}, 市场乐观)")
        elif ls_ratio < self.ctx.ls_ratio_extreme_bearish:
            print(f"    → 🔴 极度看空 (<{self.ctx.ls_ratio_extreme_bearish}, 逆向指标: 可能上涨)")
        elif ls_ratio < self.ctx.ls_ratio_bearish:
            print(f"    → 🟡 偏空 (<{self.ctx.ls_ratio_bearish}, 市场悲观)")
        else:
            print("    → ⚪ 多空平衡")

    def _analyze_judge_decision(self) -> None:
        """Analyze Judge decision reasoning."""
        print()
        print("[分析4] Judge 决策原因分析 (TradingAgents)")
        print("-" * 50)

        sd = self.ctx.signal_data
        print(f"  ⚖️ Judge 最终决策: {sd.get('signal', 'N/A')}")
        print()

        judge = sd.get('judge_decision', {})
        if judge:
            print(f"  Winning Side: {judge.get('winning_side', 'N/A')}")

            key_reasons = judge.get('key_reasons', [])
            if key_reasons:
                print(f"  Key Reasons:")
                for reason in key_reasons[:3]:
                    print(f"    • {reason}")

            risks = judge.get('acknowledged_risks', [])
            if risks:
                print(f"  Acknowledged Risks:")
                for risk in risks[:2]:
                    print(f"    • {risk}")

        print()
        print(f"  📋 Judge 完整理由:")
        reason = sd.get('reason', 'N/A')
        print_wrapped(reason)

        # v3.27: Show invalidation condition
        invalidation = sd.get('invalidation', '')
        if invalidation:
            print()
            print(f"  ⛔ 失效条件 (Invalidation):")
            print_wrapped(invalidation)

        # Show debate summary if available
        debate_summary = sd.get('debate_summary')
        if debate_summary:
            print()
            print("  🗣️ 辩论摘要:")
            print_wrapped(debate_summary[:200] + "..." if len(debate_summary) > 200 else debate_summary)

    # v2.4.6: 移除 _analyze_trigger_conditions() 方法
    # 原因: 显示 "ANY 2 of these is sufficient" 等硬编码规则，与 TradingAgents v3.x
    # 的 AI 自主决策架构冲突，容易造成误解。实际交易由 MultiAgent 自主决策。

    def _provide_recommendations(self) -> None:
        """Provide recommendations based on AI decision."""
        print()
        print("[分析5] AI 决策说明")
        print("-" * 50)

        sd = self.ctx.signal_data
        signal = sd.get('signal', 'N/A')
        confidence = sd.get('confidence', 'N/A')
        reason = sd.get('reason', '')

        if signal == 'HOLD':
            print("  📌 AI 决策: HOLD")
            print(f"     信心: {confidence}")
            print()
            print("  ℹ️ HOLD 决策由 7 次 LLM 辩论得出 (Bull/Bear 各 2 轮 + Judge + Entry Timing + Risk)")
            print("     具体原因见上方 [分析4] Judge 决策原因分析")
        else:
            print(f"  📌 AI 决策: {signal} ({confidence})")
            if reason:
                print(f"     理由: {reason[:100]}...")

        print()
        print("=" * 70)
        print("  深入分析完成")
        print("=" * 70)

    def should_skip(self) -> bool:
        return self.ctx.summary_mode


class MachineReadableSummary(DiagnosticStep):
    """
    v6.0 机器可读 JSON 输出

    Generates a structured JSON summary of all diagnostic results,
    including v6.0 position management and memory system status.
    """

    name = "v6.0 机器可读 JSON 输出"

    def run(self) -> bool:
        print()
        print("=" * 70)
        print("  Machine-readable (复制以下内容给 Claude):")
        print("=" * 70)

        results = []

        # Code integrity results
        ci_results = getattr(self.ctx, 'code_integrity_results', [])
        for r in ci_results:
            results.append({
                "id": r["id"],
                "status": "pass" if r["pass"] else "fail",
                "desc": r["desc"],
                "actual": r.get("actual", ""),
            })

        # Math verification results
        mv_results = getattr(self.ctx, 'math_verification_results', [])
        for r in mv_results:
            results.append({
                "id": r["id"],
                "status": "pass" if r["pass"] else "fail",
                "desc": r["desc"],
                "actual": r.get("actual", ""),
            })

        # Phase results from runner
        for check_id, check_pass, desc in getattr(self.ctx, 'step_results', []):
            results.append({
                "id": check_id,
                "status": "pass" if check_pass else "fail",
                "desc": desc,
                "actual": "",
            })

        passed = sum(1 for r in results if r["status"] == "pass")
        failed = sum(1 for r in results if r["status"] == "fail")
        total = len(results)

        # Add high-level counts from errors/warnings
        errors_count = len(self.ctx.errors)
        warnings_count = len(self.ctx.warnings)

        # v5.10: Memory system status
        memory_status = {}
        if self.ctx.multi_agent:
            mem_count = len(getattr(self.ctx.multi_agent, 'decision_memory', []))
            has_similarity = hasattr(self.ctx.multi_agent, '_score_memory')
            memory_status = {
                "entries": mem_count,
                "similarity_mode": has_similarity and mem_count >= 20,
                "all_agents_receive_memory": True,  # Verified by P1.12
            }

        summary = {
            "version": "v6.0",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total": total,
            "passed": passed,
            "failed": failed,
            "errors": errors_count,
            "warnings": warnings_count,
            "signal": self.ctx.signal_data.get('signal', 'N/A'),
            "confidence": self.ctx.signal_data.get('confidence', 'N/A'),
            "price": self.ctx.current_price,
            "memory_system": memory_status,
            "results": results[:50],  # Limit for readability
        }

        print(json.dumps(summary, indent=2, ensure_ascii=False))
        print()
        return True

    def should_skip(self) -> bool:
        return self.ctx.summary_mode
