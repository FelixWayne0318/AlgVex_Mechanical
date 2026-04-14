#!/usr/bin/env python3
"""
diagnose_trade_frequency.py — Trade frequency & SL/TP effectiveness diagnostic (v2.0)

Analyzes the last 24-48h of nautilus-trader logs to determine:
1. Why the bot is opening very few (or zero) trades
2. SL/TP distance effectiveness and win rate analysis
3. R/R realization vs plan

Verdict: DESIGN issue / CODE issue / MARKET issue

Usage:
    cd /home/linuxuser/nautilus_AlgVex && python3 scripts/diagnose_trade_frequency.py
    cd /home/linuxuser/nautilus_AlgVex && python3 scripts/diagnose_trade_frequency.py --hours 48
"""

import subprocess
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta


def get_logs(hours: int = 24) -> list[str]:
    """Fetch journalctl logs for nautilus-trader service."""
    cmd = [
        "journalctl", "-u", "nautilus-trader",
        f"--since={hours} hours ago",
        "--no-pager", "--no-hostname", "-o", "short-iso"
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        lines = result.stdout.strip().split('\n')
        return [l for l in lines if l.strip()]
    except Exception as e:
        print(f"  ❌ journalctl failed: {e}")
        print("  Trying fallback: reading from log files...")
        return []


def analyze_logs(lines: list[str], hours: int) -> dict:
    """Parse logs and extract trade frequency + SL/TP effectiveness metrics."""
    stats = {
        # Timer cycles
        'timer_fired': 0,
        'timer_locked': 0,  # Previous on_timer still running

        # Pre-analysis gates
        'cooldown_skips': 0,
        'cooldown_types': Counter(),
        'gate_skips': 0,
        'gate_reasons': Counter(),
        'watchdog_forces': 0,
        'surge_triggers': 0,

        # AI analysis
        'ai_analyses_run': 0,
        'ai_errors': 0,

        # Signal distribution
        'signals': Counter(),
        'confidences': Counter(),

        # Blocking after signal
        'dedup_skips': 0,
        'risk_breaker_blocks': 0,
        'risk_breaker_reasons': Counter(),
        'et_rejects': 0,
        'fr_blocks': 0,
        'fr_exhaustion': 0,
        'position_size_zero': 0,
        'liq_buffer_blocks': 0,
        'paused_skips': 0,
        'signal_age_expired': 0,

        # Execution
        'trades_opened': 0,
        'trades_closed': 0,
        'trade_details': [],

        # Position state
        'positions_opened': 0,
        'positions_closed': 0,
        'sl_hits': 0,
        'tp_hits': 0,
        'emergency_sl': 0,

        # Indicators not ready
        'indicators_not_ready': 0,
        'mtf_not_ready': 0,

        # Heartbeat count (proxy for uptime)
        'heartbeats': 0,

        # Hold sources
        'hold_sources': Counter(),

        # Time barrier
        'time_barrier_closes': 0,

        # Raw log lines for key events
        'key_events': [],

        # v2.0: SL/TP effectiveness metrics
        'sltp_entries': [],       # [{entry, sl, tp, rr_planned, confidence, method, side}]
        'trade_evaluations': [],  # [{grade, rr_planned, rr_actual, exit_type}]
        'exit_types': Counter(),  # SL/TP/TIME_BARRIER/MANUAL/TRAILING
        'trailing_activations': 0,
        'tp_resubmits': 0,
        'tp_missing': 0,
    }

    for line in lines:
        # ===== Timer cycles =====
        if 'on_timer' in line and ('cycle' in line.lower() or 'started' in line.lower() or '⏱️' in line):
            stats['timer_fired'] += 1

        if 'Previous on_timer still running' in line:
            stats['timer_locked'] += 1
            stats['timer_fired'] += 1  # It was fired, just skipped

        # ===== Pre-analysis gates =====
        if '止损冷静期' in line or 'cooldown' in line.lower():
            if 'skip' in line.lower() or '冷静期' in line:
                stats['cooldown_skips'] += 1
                # Extract cooldown type
                m = re.search(r'冷静期\s*\((\w+)\)', line)
                if m:
                    stats['cooldown_types'][m.group(1)] += 1

        if '市场未变化' in line or 'Market unchanged' in line:
            stats['gate_skips'] += 1
            # Extract reason
            m = re.search(r'Market unchanged \(([^)]+)\)|市场未变化 \(([^)]+)\)', line)
            if m:
                reason = m.group(1) or m.group(2)
                stats['gate_reasons'][reason] += 1

        if 'Watchdog' in line and 'forcing' in line:
            stats['watchdog_forces'] += 1

        if 'Surge bypass' in line or '⚡' in line:
            stats['surge_triggers'] += 1

        # ===== AI analysis =====
        if 'Judge Decision' in line or '🎯' in line:
            stats['ai_analyses_run'] += 1
            # Extract signal
            m = re.search(r'Judge Decision:\s*(\w+)', line)
            if m:
                sig = m.group(1)
                stats['signals'][sig] += 1
            # Extract confidence
            m = re.search(r'Confidence:\s*(\w+)', line)
            if m:
                stats['confidences'][m.group(1)] += 1

        if 'Multi-Agent analysis failed' in line:
            stats['ai_errors'] += 1

        # ===== Post-signal blocks =====
        if 'Duplicate signal' in line or '重复信号' in line:
            stats['dedup_skips'] += 1

        if 'Risk Controller blocked' in line or '风控熔断' in line:
            stats['risk_breaker_blocks'] += 1
            m = re.search(r'blocked trade:\s*(.+?)(?:\s*\||$)', line)
            if not m:
                m = re.search(r'风控熔断阻止交易:\s*(.+?)(?:\s*\||$)', line)
            if m:
                stats['risk_breaker_reasons'][m.group(1).strip()] += 1

        # Match actual ET REJECT events only, not counter/warning/exhaustion log lines
        # Actual: "🚫 Entry Timing REJECT:" or "Entry Timing: 多 被拦截"
        # Exclude: "consecutive REJECTs", "REJECTED N×", "overriding REJECT"
        if ('Entry Timing' in line
                and ('REJECT' in line or '拦截' in line)
                and 'consecutive' not in line
                and 'overriding' not in line
                and 'Exhaustion' not in line):
            stats['et_rejects'] += 1
            stats['key_events'].append(line.strip()[-200:])

        if 'FR' in line and ('block' in line.lower() or '阻止' in line):
            stats['fr_blocks'] += 1

        if 'FR exhaustion' in line or 'fr_consecutive_blocks' in line or '降级为 HOLD' in line:
            if 'FR' in line:
                stats['fr_exhaustion'] += 1

        if 'position size is 0' in line or 'Calculated position size is 0' in line:
            stats['position_size_zero'] += 1

        if 'liquidation buffer' in line.lower() and ('block' in line.lower() or '<' in line):
            stats['liq_buffer_blocks'] += 1

        if 'Trading is paused' in line:
            stats['paused_skips'] += 1

        if 'Signal too old' in line or 'signal_age' in line:
            stats['signal_age_expired'] += 1

        # ===== Execution =====
        if ('开多' in line or '开空' in line) and ('action_taken' in line or '执行' in line or 'MARKET' in line or 'LIMIT' in line):
            stats['trades_opened'] += 1
            stats['key_events'].append(line.strip()[-200:])

        if ('平多' in line or '平空' in line) and ('action_taken' in line or '执行' in line):
            stats['trades_closed'] += 1

        # Position events
        if 'on_position_opened' in line or 'Position opened' in line:
            stats['positions_opened'] += 1

        if 'on_position_closed' in line or 'Position closed' in line:
            stats['positions_closed'] += 1

        # SL/TP hits
        if 'STOP_MARKET' in line and 'filled' in line.lower():
            stats['sl_hits'] += 1
            stats['exit_types']['SL'] += 1
        if 'TAKE_PROFIT' in line and 'filled' in line.lower():
            stats['tp_hits'] += 1
            stats['exit_types']['TP'] += 1
        if 'TRAILING_STOP' in line and 'filled' in line.lower():
            stats['exit_types']['TRAILING'] += 1
        if 'emergency' in line.lower() and 'sl' in line.lower():
            stats['emergency_sl'] += 1

        # Heartbeat (proxy for service uptime)
        if '💓' in line or 'heartbeat' in line.lower() or '心跳' in line:
            stats['heartbeats'] += 1

        # Not initialized
        if 'Indicators not yet initialized' in line:
            stats['indicators_not_ready'] += 1
        if 'not.*initialized' in line.lower() and 'mtf' in line.lower():
            stats['mtf_not_ready'] += 1

        # Hold sources from heartbeat
        m = re.search(r'hold_source["\']?\s*[:=]\s*["\']?(\w+)', line)
        if m:
            stats['hold_sources'][m.group(1)] += 1

        # Time barrier
        if 'Time barrier' in line or 'time_barrier' in line:
            stats['time_barrier_closes'] += 1
            stats['exit_types']['TIME_BARRIER'] += 1

        # Confidence below minimum
        if 'below minimum' in line.lower() or 'confidence.*LOW' in line:
            if 'skip' in line.lower() or 'below' in line.lower():
                stats['signals'].setdefault('_conf_too_low', 0)

        # Signal: HOLD explicit
        if 'Signal: HOLD' in line or '📊 Signal: HOLD' in line:
            pass  # Already counted in signals Counter

        # ===== v2.0: SL/TP effectiveness extraction =====

        # Extract SL/TP planned values from mechanical calculation log
        # Pattern: "SL/TP validated (v11.0 mechanical): Price=$X SL=$Y TP=$Z R/R=N:1 [method]"
        m = re.search(
            r'SL/TP validated.*Price=\$([\d,\.]+)\s+'
            r'SL=\$([\d,\.]+)\s+TP=\$([\d,\.]+)\s+'
            r'R/R=([\d\.]+):1\s+\[([^\]]+)\]',
            line
        )
        if m:
            entry_px = float(m.group(1).replace(',', ''))
            sl_px = float(m.group(2).replace(',', ''))
            tp_px = float(m.group(3).replace(',', ''))
            rr_planned = float(m.group(4))
            method = m.group(5)
            # Determine side from SL position relative to entry
            side = 'LONG' if sl_px < entry_px else 'SHORT'
            sl_dist_pct = abs(entry_px - sl_px) / entry_px * 100
            tp_dist_pct = abs(tp_px - entry_px) / entry_px * 100
            stats['sltp_entries'].append({
                'entry': entry_px,
                'sl': sl_px,
                'tp': tp_px,
                'rr_planned': rr_planned,
                'method': method,
                'side': side,
                'sl_dist_pct': sl_dist_pct,
                'tp_dist_pct': tp_dist_pct,
            })

        # Extract SL/TP from bracket submission log
        # Pattern: "SL: $X (STOP_MARKET → Algo API)\n   TP: $X (TAKE_PROFIT → Algo API)"
        m = re.search(r'SL:\s+\$([\d,\.]+).*TP:\s+\$([\d,\.]+)', line)
        if m and 'Submitting Layer' in line:
            pass  # Already captured from validated log line above

        # Extract trade evaluation
        # Pattern: "Trade evaluation: Grade=B | R/R planned=2.0 actual=1.2 | Exit=sl_hit"
        m = re.search(
            r'Trade evaluation:\s+Grade=(\S+)\s*\|\s*'
            r'R/R planned=([\d\.]+)\s+actual=([\d\.\-]+)\s*\|\s*'
            r'Exit=(\S+)',
            line
        )
        if m:
            stats['trade_evaluations'].append({
                'grade': m.group(1),
                'rr_planned': float(m.group(2)),
                'rr_actual': float(m.group(3)),
                'exit_type': m.group(4),
            })

        # Trailing stop activation
        if 'Trailing SL submitted' in line or 'trailing.*activat' in line.lower():
            stats['trailing_activations'] += 1

        # TP resubmit events
        if '止盈单已恢复' in line or 'TP resubmitted' in line:
            stats['tp_resubmits'] += 1

        # TP missing warnings
        if 'TP order failed' in line or 'no TP' in line.lower():
            stats['tp_missing'] += 1

    return stats


def print_report(stats: dict, hours: int):
    """Print human-readable diagnostic report."""
    print()
    print("=" * 70)
    print(f"  📊 AlgVex 交易频率 + SL/TP 效能诊断 (最近 {hours} 小时)")
    print("=" * 70)

    # ===== 1. Service uptime =====
    print(f"\n{'─' * 70}")
    print("  1️⃣  服务运行状态")
    print(f"{'─' * 70}")
    expected_heartbeats = (hours * 60) // 20  # Every 20 min
    hb = stats['heartbeats']
    uptime_pct = min(100, (hb / max(expected_heartbeats, 1)) * 100)
    print(f"  心跳次数: {hb} (预期 ~{expected_heartbeats})")
    print(f"  估算运行率: {uptime_pct:.0f}%")
    if hb == 0:
        print("  ⚠️  没有检测到心跳! 服务可能未运行或日志时间范围不对")
    if stats['indicators_not_ready'] > 0:
        print(f"  ⚠️  指标未就绪: {stats['indicators_not_ready']} 次")
    if stats['mtf_not_ready'] > 0:
        print(f"  ⚠️  MTF 未就绪: {stats['mtf_not_ready']} 次")
    if stats['timer_locked'] > 0:
        print(f"  ⚠️  Timer 锁定 (上一个 on_timer 还在跑): {stats['timer_locked']} 次")

    # ===== 2. Decision funnel =====
    print(f"\n{'─' * 70}")
    print("  2️⃣  决策漏斗 (从 Timer 到开仓)")
    print(f"{'─' * 70}")

    total_cycles = max(stats['ai_analyses_run'] + stats['cooldown_skips'] + stats['gate_skips'] + stats['watchdog_forces'], 1)
    # Better estimate from heartbeats
    if stats['heartbeats'] > total_cycles:
        total_cycles = stats['heartbeats']

    ai_ran = stats['ai_analyses_run']
    # Signals that were LONG or SHORT
    actionable = stats['signals'].get('LONG', 0) + stats['signals'].get('SHORT', 0)
    holds = stats['signals'].get('HOLD', 0)
    closes = stats['signals'].get('CLOSE', 0) + stats['signals'].get('REDUCE', 0)
    opened = stats['trades_opened']

    print(f"""
  Timer 周期 (估算):           ~{total_cycles}
    ├─ 冷静期跳过:             {stats['cooldown_skips']:>4}  ({stats['cooldown_skips']/max(total_cycles,1)*100:>5.1f}%)
    ├─ 市场未变化跳过:         {stats['gate_skips']:>4}  ({stats['gate_skips']/max(total_cycles,1)*100:>5.1f}%)
    ├─ Watchdog 强制分析:      {stats['watchdog_forces']:>4}
    ├─ Surge 触发分析:         {stats['surge_triggers']:>4}
    └─ AI 分析实际执行:        {ai_ran:>4}  ({ai_ran/max(total_cycles,1)*100:>5.1f}%)
        ├─ 信号 HOLD:          {holds:>4}  ({holds/max(ai_ran,1)*100:>5.1f}%)
        ├─ 信号 CLOSE/REDUCE:  {closes:>4}
        ├─ 信号 LONG/SHORT:    {actionable:>4}  ({actionable/max(ai_ran,1)*100:>5.1f}%)
        │   ├─ 重复信号跳过:   {stats['dedup_skips']:>4}
        │   ├─ 风控熔断阻止:   {stats['risk_breaker_blocks']:>4}
        │   ├─ ET 拦截 (REJECT):{stats['et_rejects']:>4}
        │   ├─ FR 阻止:        {stats['fr_blocks']:>4}
        │   ├─ FR 耗尽降级:    {stats['fr_exhaustion']:>4}
        │   ├─ 仓位=0 跳过:    {stats['position_size_zero']:>4}
        │   ├─ 清算缓冲阻止:   {stats['liq_buffer_blocks']:>4}
        │   └─ 暂停状态:       {stats['paused_skips']:>4}
        └─ ✅ 实际开仓:        {opened:>4}""")

    # ===== 3. Signal breakdown =====
    print(f"\n{'─' * 70}")
    print("  3️⃣  AI 信号分布")
    print(f"{'─' * 70}")
    if stats['signals']:
        for sig, count in stats['signals'].most_common():
            pct = count / max(ai_ran, 1) * 100
            bar = '█' * int(pct / 2)
            print(f"  {sig:<10} {count:>4} ({pct:>5.1f}%) {bar}")
    else:
        print("  (无信号数据)")

    print()
    if stats['confidences']:
        print("  Confidence 分布:")
        for conf, count in stats['confidences'].most_common():
            print(f"    {conf:<10} {count:>4}")

    # ===== 4. Skip reasons detail =====
    print(f"\n{'─' * 70}")
    print("  4️⃣  跳过/阻止原因详情")
    print(f"{'─' * 70}")

    if stats['cooldown_types']:
        print("  冷静期类型:")
        for ct, count in stats['cooldown_types'].most_common():
            print(f"    {ct}: {count} 次")

    if stats['gate_reasons']:
        print("  市场未变化原因:")
        for gr, count in stats['gate_reasons'].most_common():
            print(f"    {gr}: {count} 次")

    if stats['risk_breaker_reasons']:
        print("  风控熔断原因:")
        for rr, count in stats['risk_breaker_reasons'].most_common():
            print(f"    {rr}: {count} 次")

    if stats['hold_sources']:
        print("  HOLD 来源分布:")
        for hs, count in stats['hold_sources'].most_common():
            label = {
                'cooldown': '冷静期',
                'gate_skip': '市场未变化',
                'dedup': '重复信号',
                'risk_breaker': '风控熔断',
                'et_reject': 'ET 拦截',
                'explicit_judge': 'Judge 判定 HOLD',
            }.get(hs, hs)
            print(f"    {label} ({hs}): {count} 次")

    # ===== 5. Position events =====
    print(f"\n{'─' * 70}")
    print("  5️⃣  仓位事件")
    print(f"{'─' * 70}")
    print(f"  开仓: {stats['positions_opened']}")
    print(f"  平仓: {stats['positions_closed']}")
    print(f"  SL 触发: {stats['sl_hits']}")
    print(f"  TP 触发: {stats['tp_hits']}")
    print(f"  Emergency SL: {stats['emergency_sl']}")
    print(f"  Time Barrier 平仓: {stats['time_barrier_closes']}")
    print(f"  Trailing 激活: {stats['trailing_activations']}")

    # ===== 6. SL/TP Effectiveness Analysis (v2.0) =====
    print(f"\n{'─' * 70}")
    print("  6️⃣  SL/TP 效能分析 (v2.0)")
    print(f"{'─' * 70}")

    entries = stats['sltp_entries']
    evals = stats['trade_evaluations']

    if entries:
        sl_dists = [e['sl_dist_pct'] for e in entries]
        tp_dists = [e['tp_dist_pct'] for e in entries]
        rr_plans = [e['rr_planned'] for e in entries]

        avg_sl_dist = sum(sl_dists) / len(sl_dists)
        avg_tp_dist = sum(tp_dists) / len(tp_dists)
        avg_rr_plan = sum(rr_plans) / len(rr_plans)
        min_sl_dist = min(sl_dists)
        max_sl_dist = max(sl_dists)
        min_tp_dist = min(tp_dists)
        max_tp_dist = max(tp_dists)

        print(f"\n  📐 SL/TP 距离统计 ({len(entries)} 笔入场):")
        print(f"  {'':>4}{'平均':>10}{'最小':>10}{'最大':>10}")
        print(f"  {'SL':>4}{avg_sl_dist:>9.2f}%{min_sl_dist:>9.2f}%{max_sl_dist:>9.2f}%")
        print(f"  {'TP':>4}{avg_tp_dist:>9.2f}%{min_tp_dist:>9.2f}%{max_tp_dist:>9.2f}%")
        print(f"  {'R/R':>4}{avg_rr_plan:>9.2f}:1")

        # TP distance reachability assessment
        print(f"\n  📊 止盈可达性评估:")
        if avg_tp_dist > 3.0:
            print(f"  ⚠️  TP 平均距离 {avg_tp_dist:.2f}% — 偏大!")
            print(f"      BTC 20分钟(一个周期)波动通常 0.3-0.8%")
            print(f"      TP 距离约 {avg_tp_dist/0.5:.0f} 个周期才能到达 (假设单向 0.5%/周期)")
            print(f"      建议: 降低 R/R target 或收紧 SL 以缩短 TP 距离")
        elif avg_tp_dist > 2.0:
            print(f"  🟡 TP 平均距离 {avg_tp_dist:.2f}% — 中等偏大")
            print(f"      需要价格持续单向移动 {avg_tp_dist:.1f}% 才能到达 TP")
        else:
            print(f"  ✅ TP 平均距离 {avg_tp_dist:.2f}% — 合理范围")

        # SL tightness assessment
        print(f"\n  📊 止损宽度评估:")
        if avg_sl_dist < 0.8:
            print(f"  ⚠️  SL 平均距离 {avg_sl_dist:.2f}% — 可能过紧")
            print(f"      容易被正常价格波动 (噪音) 触发")
        elif avg_sl_dist > 2.5:
            print(f"  🟡 SL 平均距离 {avg_sl_dist:.2f}% — 偏宽")
            print(f"      单笔亏损较大，但不容易被噪音触发")
        else:
            print(f"  ✅ SL 平均距离 {avg_sl_dist:.2f}% — 合理范围")

        # Per-entry detail
        if len(entries) <= 10:
            print(f"\n  📝 各笔入场 SL/TP 详情:")
            for i, e in enumerate(entries, 1):
                print(f"    [{i}] {e['side']} @ ${e['entry']:,.0f} | "
                      f"SL=${e['sl']:,.0f} ({e['sl_dist_pct']:.2f}%) | "
                      f"TP=${e['tp']:,.0f} ({e['tp_dist_pct']:.2f}%) | "
                      f"R/R={e['rr_planned']:.1f}:1")
    else:
        print("  (无 SL/TP 计算日志，可能没有开仓)")

    # Trade evaluation analysis
    if evals:
        print(f"\n  📊 交易评估 ({len(evals)} 笔平仓):")
        grades = Counter(e['grade'] for e in evals)
        for g in ['A+', 'A', 'B', 'C', 'D', 'F']:
            if g in grades:
                print(f"    Grade {g}: {grades[g]} 笔")

        rr_actuals = [e['rr_actual'] for e in evals]
        rr_plans = [e['rr_planned'] for e in evals]
        avg_rr_actual = sum(rr_actuals) / len(rr_actuals) if rr_actuals else 0
        avg_rr_plan = sum(rr_plans) / len(rr_plans) if rr_plans else 0
        rr_realization = avg_rr_actual / avg_rr_plan * 100 if avg_rr_plan > 0 else 0

        print(f"\n    R/R 计划: {avg_rr_plan:.2f}:1 → 实际: {avg_rr_actual:.2f}:1")
        print(f"    R/R 实现率: {rr_realization:.0f}%")

        if rr_realization < 50:
            print(f"    ⚠️  R/R 实现率 <50%! 多数交易在到达 TP 前就退出了")
            print(f"        说明 TP 设置过远，价格到不了就回头触发 SL")

        # Exit type distribution
        exit_types = Counter(e['exit_type'] for e in evals)
        if exit_types:
            print(f"\n    退出方式分布:")
            for et, cnt in exit_types.most_common():
                print(f"      {et}: {cnt} ({cnt/len(evals)*100:.0f}%)")
    else:
        print(f"\n  (无交易评估数据)")

    # SL/TP hit rate
    total_exits = stats['sl_hits'] + stats['tp_hits'] + stats['exit_types'].get('TRAILING', 0)
    if total_exits > 0:
        tp_rate = stats['tp_hits'] / total_exits * 100
        sl_rate = stats['sl_hits'] / total_exits * 100
        trailing_rate = stats['exit_types'].get('TRAILING', 0) / total_exits * 100

        print(f"\n  📊 SL/TP 触发率 (基于 {total_exits} 次自动退出):")
        print(f"    SL 触发: {stats['sl_hits']} ({sl_rate:.0f}%)")
        print(f"    TP 触发: {stats['tp_hits']} ({tp_rate:.0f}%)")
        if stats['exit_types'].get('TRAILING', 0) > 0:
            print(f"    Trailing: {stats['exit_types']['TRAILING']} ({trailing_rate:.0f}%)")

        if tp_rate < 30 and total_exits >= 3:
            print(f"    ⚠️  TP 命中率 <30%! 这是核心问题:")
            print(f"        → TP 距离太远，价格到不了")
            print(f"        → 建议降低 tp_rr_target (当前 HIGH=2.5, MEDIUM=2.0)")
            print(f"        → 或收紧 SL (降低 sl_atr_multiplier)")
        elif tp_rate < 50 and total_exits >= 5:
            print(f"    🟡 TP 命中率 <50%，胜率偏低")
            print(f"        数学期望: 若 R/R=2.0，胜率需 >33% 才有正期望")
            print(f"        当前胜率 {tp_rate:.0f}%，{'✅ 正期望' if tp_rate > 33 else '❌ 负期望'}")

    # TP coverage issues
    if stats['tp_missing'] > 0 or stats['tp_resubmits'] > 0:
        print(f"\n  ⚠️  TP 覆盖问题:")
        if stats['tp_missing'] > 0:
            print(f"    TP 提交失败: {stats['tp_missing']} 次")
        if stats['tp_resubmits'] > 0:
            print(f"    TP 恢复 (resubmit): {stats['tp_resubmits']} 次")

    # ===== 7. Key events timeline =====
    if stats['key_events']:
        print(f"\n{'─' * 70}")
        print("  7️⃣  关键事件 (最近 10 条)")
        print(f"{'─' * 70}")
        for ev in stats['key_events'][-10:]:
            print(f"  {ev[:120]}")

    # ===== 8. VERDICT =====
    print(f"\n{'=' * 70}")
    print("  🔍 诊断结论")
    print(f"{'=' * 70}")

    verdicts = []
    severity = []

    # --- Check: Service not running ---
    if hb == 0:
        verdicts.append(("CODE/OPS", "服务未运行或日志为空，无法诊断"))
        severity.append(3)

    # --- Check: Too many cooldown skips ---
    cooldown_ratio = stats['cooldown_skips'] / max(total_cycles, 1)
    if cooldown_ratio > 0.3:
        verdicts.append((
            "DESIGN",
            f"冷静期占比过高 ({cooldown_ratio*100:.0f}%)。"
            f"频繁止损 → 长时间冷却。"
            f"检查 SL 是否太紧 (ATR 倍数)，或降低 cooldown candles"
        ))
        severity.append(2)

    # --- Check: Market not moving ---
    gate_ratio = stats['gate_skips'] / max(total_cycles, 1)
    if gate_ratio > 0.5:
        verdicts.append((
            "MARKET",
            f"市场未变化跳过占比 {gate_ratio*100:.0f}%。"
            f"BTC 波动率低，价格在 0.2% 内震荡，ATR 变化 <15%。"
            f"这是正常的市场行为，非代码问题"
        ))
        severity.append(1)

    # --- Check: AI running but all HOLD ---
    if ai_ran > 0 and actionable == 0:
        verdicts.append((
            "MARKET/DESIGN",
            f"AI 执行了 {ai_ran} 次分析，但 0 次产生 LONG/SHORT 信号。"
            f"全部为 HOLD ({holds}) / CLOSE ({closes})。"
            f"可能是: (1) 市场震荡无方向 (2) AI 过于保守 (3) 数据质量问题"
        ))
        severity.append(2)

    # --- Check: HOLD ratio too high ---
    if ai_ran > 5 and holds / max(ai_ran, 1) > 0.85:
        verdicts.append((
            "DESIGN/MARKET",
            f"HOLD 比例过高: {holds}/{ai_ran} = {holds/ai_ran*100:.0f}%。"
            f"正常 HOLD 比例约 60-70%。>85% 说明 AI 过于保守或市场无方向"
        ))
        severity.append(2)

    # --- Check: ET rejects too many ---
    if actionable > 0 and stats['et_rejects'] / max(actionable, 1) > 0.5:
        verdicts.append((
            "DESIGN",
            f"Entry Timing Agent 拦截了 {stats['et_rejects']}/{actionable} "
            f"({stats['et_rejects']/actionable*100:.0f}%) 的可操作信号。"
            f"ET 可能过于严格。检查 ADX/MTF 对齐条件"
        ))
        severity.append(2)

    # --- Check: FR blocking ---
    if stats['fr_blocks'] > 3 or stats['fr_exhaustion'] > 0:
        verdicts.append((
            "MARKET",
            f"Funding Rate 阻止了 {stats['fr_blocks']} 次信号"
            f"{', FR 耗尽降级 ' + str(stats['fr_exhaustion']) + ' 次' if stats['fr_exhaustion'] else ''}。"
            f"FR 异常说明市场有极端情绪，交易成本高"
        ))
        severity.append(1)

    # --- Check: Risk controller blocking ---
    if stats['risk_breaker_blocks'] > 0:
        verdicts.append((
            "DESIGN/OPS",
            f"风控熔断阻止了 {stats['risk_breaker_blocks']} 次交易。"
            f"原因: {dict(stats['risk_breaker_reasons'])}。"
            f"检查 drawdown 是否接近 15% halt 阈值"
        ))
        severity.append(2)

    # --- Check: Dedup too aggressive ---
    if stats['dedup_skips'] > 5 and stats['dedup_skips'] / max(actionable + stats['dedup_skips'], 1) > 0.3:
        verdicts.append((
            "DESIGN",
            f"重复信号跳过 {stats['dedup_skips']} 次。"
            f"AI 反复产生相同信号但被去重。"
            f"去重机制可能过于激进 (fingerprint 包含 confidence + risk_appetite)"
        ))
        severity.append(1)

    # --- Check: Position size = 0 ---
    if stats['position_size_zero'] > 0:
        verdicts.append((
            "CODE/CONFIG",
            f"计算仓位大小为 0 发生了 {stats['position_size_zero']} 次。"
            f"检查: equity sync / max_position_ratio / min notional $100"
        ))
        severity.append(2)

    # --- Check: AI errors ---
    if stats['ai_errors'] > 0:
        verdicts.append((
            "CODE",
            f"AI 分析失败 {stats['ai_errors']} 次。"
            f"检查 DeepSeek API key / 余额 / 网络"
        ))
        severity.append(3)

    # --- Check: Paused ---
    if stats['paused_skips'] > 0:
        verdicts.append((
            "OPS",
            f"交易处于暂停状态 ({stats['paused_skips']} 次检测到)。"
            f"使用 /resume 恢复"
        ))
        severity.append(3)

    # --- v2.0: Check: TP unreachable ---
    if entries and len(entries) >= 2:
        avg_tp = sum(e['tp_dist_pct'] for e in entries) / len(entries)
        if avg_tp > 3.0:
            verdicts.append((
                "DESIGN",
                f"TP 平均距离 {avg_tp:.1f}% — 过远难以到达。"
                f"当前 R/R target (HIGH=2.5, MEDIUM=2.0) 导致 TP 需价格大幅单向移动。"
                f"建议: 降低 tp_rr_target 或收紧 sl_atr_multiplier"
            ))
            severity.append(2)

    # --- v2.0: Check: Low TP hit rate ---
    if total_exits >= 3:
        tp_hit_rate = stats['tp_hits'] / total_exits
        if tp_hit_rate < 0.3:
            verdicts.append((
                "DESIGN",
                f"TP 命中率仅 {tp_hit_rate*100:.0f}% (SL 命中远多于 TP)。"
                f"核心问题: TP 设置过远，大多数交易在到达 TP 前触发 SL。"
                f"考虑: 降低 R/R target 到 1.5:1，提高胜率换取更低单笔利润"
            ))
            severity.append(2)

    # --- v2.0: Check: R/R realization poor ---
    if evals and len(evals) >= 2:
        rr_actuals = [e['rr_actual'] for e in evals]
        rr_plans = [e['rr_planned'] for e in evals]
        avg_actual = sum(rr_actuals) / len(rr_actuals)
        avg_plan = sum(rr_plans) / len(rr_plans)
        realization = avg_actual / avg_plan * 100 if avg_plan > 0 else 0
        if realization < 40:
            verdicts.append((
                "DESIGN",
                f"R/R 实现率仅 {realization:.0f}% (计划 {avg_plan:.1f}:1 → 实际 {avg_actual:.1f}:1)。"
                f"多数交易在远未到达 TP 时就退出了。TP 目标不现实"
            ))
            severity.append(2)

    # --- Overall health ---
    if not verdicts:
        if opened > 0:
            verdicts.append((
                "NORMAL",
                f"最近 {hours}h 开仓 {opened} 次，系统运行正常。"
                f"BTC 交易频率通常 0-3 次/天"
            ))
            severity.append(0)
        else:
            verdicts.append((
                "MARKET",
                f"所有系统正常运行，无异常阻止。"
                f"最可能是市场处于震荡/无方向状态，AI 正确选择观望"
            ))
            severity.append(1)

    # Print verdicts sorted by severity
    for i, (vtype, msg) in enumerate(sorted(zip(severity, verdicts), reverse=True)):
        sev, (vtype2, msg2) = vtype, verdicts[i]
        icon = {0: '✅', 1: '🟡', 2: '🟠', 3: '🔴'}[min(sev, 3)]
        print(f"\n  {icon} [{vtype2}] {msg2}")

    # ===== 9. Recommendations =====
    print(f"\n{'─' * 70}")
    print("  💡 建议操作")
    print(f"{'─' * 70}")

    if stats['cooldown_skips'] > total_cycles * 0.3:
        print("  1. 检查 SL 是否过紧: 查看最近 SL 触发后价格是否反向 → 如果是 noise stop，")
        print("     考虑放宽 ATR 倍数或降低 cooldown_per_stoploss_candles")

    if gate_ratio > 0.5:
        print("  1. 市场低波动，正常现象。可选:")
        print("     - 降低 _PRICE_CHANGE_THRESHOLD (当前 0.2%) — 已从 0.3% 调整")
        print("     - 降低 _ATR_CHANGE_THRESHOLD (当前 15%) 到 10%")
        print("     ⚠️ 但这会增加 AI API 调用成本 (无效分析更多)")

    if ai_ran > 5 and holds / max(ai_ran, 1) > 0.85:
        print("  1. 检查 AI prompt 是否过于保守")
        print("  2. 运行 /layer3 查看 HOLD 反事实分析 — 如果大量 HOLD 是 'wrong'，说明 AI 错过了机会")
        print("  3. 查看 feature snapshot: 市场数据是否正常输入到 AI")

    if stats['et_rejects'] > 2:
        print("  1. Entry Timing Agent 拦截过多，检查:")
        print("     - 是否 ADX>40 强趋势中所有逆势信号都被拦截 (设计如此)")
        print("     - /layer3 检查 REJECT accuracy — 如果 REJECT 正确率低，可能需要调整")

    if stats['risk_breaker_blocks'] > 0:
        print("  1. 运行 /risk 查看当前风控状态")
        print("  2. 如果 drawdown >10%，系统处于 REDUCED 状态 (仓位减半)")
        print("  3. 如果 drawdown >15%，系统 HALTED (完全停止交易)")

    if ai_ran == 0 and stats['cooldown_skips'] == 0 and stats['gate_skips'] == 0:
        print("  1. 服务可能未运行或刚启动。运行:")
        print("     sudo systemctl status nautilus-trader")
        print("     sudo journalctl -u nautilus-trader -n 50 --no-hostname")

    # v2.0: SL/TP specific recommendations
    if entries:
        avg_tp = sum(e['tp_dist_pct'] for e in entries) / len(entries)
        avg_sl = sum(e['sl_dist_pct'] for e in entries) / len(entries)
        if avg_tp > 2.5:
            print()
            print("  ━━━ SL/TP 优化建议 ━━━")
            print(f"  当前: SL 距离 {avg_sl:.2f}% | TP 距离 {avg_tp:.2f}%")
            print(f"  问题: TP 距离是 SL 的 {avg_tp/avg_sl:.1f} 倍，价格需大幅单向移动")
            print()
            print("  当前生产参数 (v39.0 — 4H ATR 基准):")
            print("    atr_source: 4H (primary), 30M (fallback)")
            print("    sl_atr_multiplier: HIGH=0.8, MEDIUM=1.0, LOW=1.0 (×4H ATR)")
            print("    tp_rr_target: HIGH=2.0, MEDIUM=1.8, LOW=1.8")
            print("    sl_atr_multiplier_floor: 0.5")
            print("    min_confidence: LOW (v38.1, 30% 小仓位积累数据)")

    if total_exits >= 3:
        tp_hit_rate = stats['tp_hits'] / total_exits
        if tp_hit_rate < 0.3:
            print()
            print("  ⚠️  关键发现: TP 命中率过低 → 建议采用 '小赚多次' 策略:")
            print("  1. 降低 tp_rr_target (1.5~1.8) — TP 更容易到达")
            print("  2. 降低 min_confidence_to_trade 到 MEDIUM (已是)")
            print("  3. 市场变化门槛已调至 0.2% (v39.0)")
            print("  4. 降低 cooldown candles (per_stoploss: 2→1, reversal_stop: 6→3)")

    print()

    # ===== 10. Quick summary =====
    print(f"{'=' * 70}")
    total_blocks = (stats['cooldown_skips'] + stats['gate_skips'] + stats['dedup_skips'] +
                    stats['risk_breaker_blocks'] + stats['et_rejects'] + stats['fr_blocks'] +
                    stats['position_size_zero'])
    print(f"  📋 汇总: {hours}h 内 ~{total_cycles} 个周期, "
          f"{ai_ran} 次 AI 分析, {actionable} 个方向信号, "
          f"{opened} 次开仓, {total_blocks} 次阻止")
    if total_exits > 0:
        print(f"  📊 退出: SL={stats['sl_hits']} TP={stats['tp_hits']} "
              f"Trailing={stats['exit_types'].get('TRAILING', 0)} "
              f"TimerBarrier={stats['time_barrier_closes']}")
        print(f"  📊 TP 命中率: {stats['tp_hits']}/{total_exits} = {stats['tp_hits']/total_exits*100:.0f}%")
    print(f"{'=' * 70}")
    print()


def main():
    hours = 24
    if len(sys.argv) > 1:
        if sys.argv[1] == '--hours' and len(sys.argv) > 2:
            hours = int(sys.argv[2])
        elif sys.argv[1].isdigit():
            hours = int(sys.argv[1])
        elif sys.argv[1] in ('-h', '--help'):
            print(__doc__)
            return

    print(f"\n⏳ 正在获取最近 {hours} 小时的日志...")
    lines = get_logs(hours)

    if not lines or len(lines) < 3:
        print(f"  ⚠️  日志为空或行数太少 ({len(lines)} 行)")
        print("  检查: sudo systemctl status nautilus-trader")
        print("  或尝试: sudo journalctl -u nautilus-trader -n 100 --no-hostname")
        return

    print(f"  ✅ 获取到 {len(lines)} 行日志")

    stats = analyze_logs(lines, hours)
    print_report(stats, hours)


if __name__ == '__main__':
    main()
