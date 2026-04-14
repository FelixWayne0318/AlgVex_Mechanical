#!/usr/bin/env python3
"""
Trailing Stop Diagnostic Tool v1.0
追踪止损诊断工具

Analyzes recent trading logs to determine:
1. How many positions were closed by trailing stop vs fixed SL vs TP
2. Whether trailing stop callback rate is reasonable for current volatility
3. Whether trailing stop is activating too early / too tight
4. Compares actual close prices with theoretical fixed-SL outcomes

Usage:
    python3 scripts/diagnose_trailing_stop.py              # Default 7 days
    python3 scripts/diagnose_trailing_stop.py --days 14    # Custom range
    python3 scripts/diagnose_trailing_stop.py --hours 48   # Hours mode
"""

import argparse
import json
import os
import re
import sys
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
MEMORY_FILE = DATA_DIR / "trading_memory.json"
LAYER_FILE = DATA_DIR / "layer_orders.json"


# ---------------------------------------------------------------------------
# 1) Parse trading_memory.json for close reasons
# ---------------------------------------------------------------------------
def analyze_memory(days: int) -> list[dict]:
    """Parse trading_memory.json for recent trades."""
    if not MEMORY_FILE.exists():
        print(f"  ⚠️ {MEMORY_FILE} not found")
        return []

    with open(MEMORY_FILE) as f:
        memories = json.load(f)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    recent = []

    for m in memories:
        ts_str = m.get("timestamp", "")
        try:
            if "T" in ts_str:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            else:
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=timezone.utc
                )
        except (ValueError, TypeError):
            continue

        if ts < cutoff:
            continue

        recent.append(m)

    return recent


def categorize_trades(trades: list[dict]) -> dict:
    """Categorize trades by close reason."""
    categories = defaultdict(list)

    for t in trades:
        reason = t.get("close_reason", "")
        outcome = t.get("outcome", "")
        pnl = t.get("pnl_pct", t.get("pnl_percentage", 0)) or 0
        grade = t.get("grade", "?")
        side = t.get("side", "?")
        entry = t.get("entry_price", 0)
        exit_p = t.get("exit_price", 0)
        sl = t.get("sl_price", 0)
        tp = t.get("tp_price", 0)
        confidence = t.get("confidence", "?")
        ts = t.get("timestamp", "?")

        info = {
            "timestamp": ts,
            "side": side,
            "entry": entry,
            "exit": exit_p,
            "sl": sl,
            "tp": tp,
            "pnl_pct": pnl,
            "grade": grade,
            "confidence": confidence,
            "outcome": outcome,
            "close_reason": reason,
        }

        # Categorize
        reason_lower = reason.lower() if reason else ""
        if "trailing" in reason_lower or "追踪" in reason_lower:
            categories["TRAILING_STOP"].append(info)
        elif "stop_loss" in reason_lower or "止损" in reason_lower or "sl" in reason_lower:
            categories["STOP_LOSS"].append(info)
        elif "take_profit" in reason_lower or "止盈" in reason_lower or "tp" in reason_lower:
            categories["TAKE_PROFIT"].append(info)
        elif "manual" in reason_lower or "手动" in reason_lower:
            categories["MANUAL"].append(info)
        elif "time_barrier" in reason_lower or "时间" in reason_lower:
            categories["TIME_BARRIER"].append(info)
        else:
            categories["OTHER"].append(info)

    return dict(categories)


# ---------------------------------------------------------------------------
# 2) Parse logs for trailing stop details
# ---------------------------------------------------------------------------
def parse_logs_for_trailing(hours: int) -> dict:
    """Parse journalctl logs for trailing stop events."""
    result = {
        "activations": [],
        "callbacks": [],
        "fills": [],
        "submissions": [],
        "atr_values": [],
        "errors": [],
    }

    try:
        cmd = [
            "journalctl", "-u", "nautilus-trader",
            f"--since={hours} hours ago",
            "--no-pager", "--no-hostname", "-o", "cat",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        lines = proc.stdout.splitlines() if proc.returncode == 0 else []
    except Exception as e:
        print(f"  ⚠️ journalctl failed: {e}")
        print("  ℹ️ Trying log files instead...")
        lines = _read_log_files(hours)

    for line in lines:
        # Trailing stop submission
        if "trailing" in line.lower() and ("submit" in line.lower() or "callback" in line.lower()):
            # Extract callback rate
            bps_match = re.search(r'(\d+)\s*bps', line)
            pct_match = re.search(r'callback\s+(\d+\.?\d*)%', line)
            activation_match = re.search(r'activation.*?(\d+\.?\d+)', line)

            entry = {"raw": line.strip()}
            if bps_match:
                entry["callback_bps"] = int(bps_match.group(1))
            if pct_match:
                entry["callback_pct"] = float(pct_match.group(1))
            if activation_match:
                entry["activation_price"] = float(activation_match.group(1))
            result["submissions"].append(entry)

        # Trailing stop filled
        if "trailing" in line.lower() and ("filled" in line.lower() or "triggered" in line.lower()):
            price_match = re.search(r'\$?([\d,]+\.?\d*)', line)
            entry = {"raw": line.strip()}
            if price_match:
                entry["fill_price"] = float(price_match.group(1).replace(",", ""))
            result["fills"].append(entry)

        # 追踪止损
        if "追踪止损" in line:
            price_match = re.search(r'\$?([\d,]+\.?\d*)', line)
            entry = {"raw": line.strip()}
            if price_match:
                entry["fill_price"] = float(price_match.group(1).replace(",", ""))
            result["fills"].append(entry)

        # ATR values
        atr_match = re.search(r'ATR[:\s=]+\$?([\d.]+)', line, re.IGNORECASE)
        if atr_match and "extension" not in line.lower():
            try:
                atr_val = float(atr_match.group(1))
                if 10 < atr_val < 50000:  # Reasonable ATR range
                    result["atr_values"].append(atr_val)
            except ValueError:
                pass

        # Errors
        if "trailing" in line.lower() and ("error" in line.lower() or "fail" in line.lower()):
            result["errors"].append(line.strip())

    return result


def _read_log_files(hours: int) -> list[str]:
    """Fallback: read log files directly."""
    lines = []
    log_dirs = [
        Path("/var/log/journal"),
        PROJECT_ROOT / "logs",
        Path("/tmp"),
    ]
    for d in log_dirs:
        if d.exists():
            for f in sorted(d.glob("*.log")):
                try:
                    with open(f) as fh:
                        lines.extend(fh.readlines())
                except Exception:
                    pass
    return lines


# ---------------------------------------------------------------------------
# 3) Analyze current trailing stop parameters
# ---------------------------------------------------------------------------
def analyze_parameters() -> dict:
    """Analyze current trailing stop parameters from config and code."""
    params = {
        "activation_r": 1.1,
        "atr_multiplier": 1.5,
        "min_bps": 10,
        "max_bps": 1000,
    }

    # Read from order_execution.py
    oe_file = PROJECT_ROOT / "strategy" / "order_execution.py"
    if oe_file.exists():
        content = oe_file.read_text()
        m = re.search(r'_TRAILING_ATR_MULTIPLIER\s*=\s*([\d.]+)', content)
        if m:
            params["atr_multiplier"] = float(m.group(1))
        m = re.search(r'_TRAILING_MIN_BPS\s*=\s*(\d+)', content)
        if m:
            params["min_bps"] = int(m.group(1))
        m = re.search(r'_TRAILING_MAX_BPS\s*=\s*(\d+)', content)
        if m:
            params["max_bps"] = int(m.group(1))

    # Read activation R from position_manager.py
    pm_file = PROJECT_ROOT / "strategy" / "position_manager.py"
    if pm_file.exists():
        content = pm_file.read_text()
        m = re.search(r'_TRAILING_ACTIVATION_R\s*=\s*([\d.]+)', content)
        if m:
            params["activation_r"] = float(m.group(1))

    # Read SL/TP params from base.yaml
    yaml_file = PROJECT_ROOT / "configs" / "base.yaml"
    if yaml_file.exists():
        content = yaml_file.read_text()
        # SL ATR multipliers
        sl_section = re.search(
            r'sl_atr_multiplier:.*?(?=\n\S|\Z)',
            content, re.DOTALL
        )
        if sl_section:
            params["sl_config_raw"] = sl_section.group(0).strip()

        tp_section = re.search(
            r'tp_rr_target:.*?(?=\n\S|\Z)',
            content, re.DOTALL
        )
        if tp_section:
            params["tp_config_raw"] = tp_section.group(0).strip()

    return params


# ---------------------------------------------------------------------------
# 4) Simulate: what if trailing stop was wider / didn't exist?
# ---------------------------------------------------------------------------
def simulate_alternatives(trades: list[dict], current_atr_multiplier: float):
    """Compare trailing stop outcomes vs hypothetical alternatives."""
    trailing_trades = [
        t for t in trades
        if "trailing" in (t.get("close_reason", "") or "").lower()
        or "追踪" in (t.get("close_reason", "") or "").lower()
    ]

    if not trailing_trades:
        return None

    results = {
        "trailing_count": len(trailing_trades),
        "trailing_avg_pnl": 0,
        "could_have_hit_tp": 0,
        "worse_than_sl": 0,
        "details": [],
    }

    total_pnl = 0
    for t in trailing_trades:
        entry = t.get("entry", 0) or 0
        exit_p = t.get("exit", 0) or 0
        sl = t.get("sl", 0) or 0
        tp = t.get("tp", 0) or 0
        side = t.get("side", "").upper()
        pnl = t.get("pnl_pct", 0) or 0
        total_pnl += pnl

        detail = {
            "timestamp": t.get("timestamp"),
            "side": side,
            "entry": entry,
            "exit": exit_p,
            "pnl_pct": pnl,
        }

        if entry and tp and exit_p:
            if side in ("LONG", "BUY"):
                # Did price go higher than trailing exit before hitting SL?
                detail["exit_vs_tp"] = f"exit=${exit_p:,.2f} vs tp=${tp:,.2f}"
                if tp > exit_p:
                    # Trailing stopped us out before TP
                    tp_distance_pct = ((tp - exit_p) / entry) * 100
                    detail["missed_tp_by_pct"] = round(tp_distance_pct, 2)
                    results["could_have_hit_tp"] += 1
            elif side in ("SHORT", "SELL"):
                detail["exit_vs_tp"] = f"exit=${exit_p:,.2f} vs tp=${tp:,.2f}"
                if tp < exit_p:
                    tp_distance_pct = ((exit_p - tp) / entry) * 100
                    detail["missed_tp_by_pct"] = round(tp_distance_pct, 2)
                    results["could_have_hit_tp"] += 1

        # Was trailing worse than just hitting SL?
        if pnl < 0:
            results["worse_than_sl"] += 1
            detail["note"] = "⚠️ Trailing stop resulted in LOSS (worse than fixed SL would be)"

        results["details"].append(detail)

    results["trailing_avg_pnl"] = round(total_pnl / len(trailing_trades), 3) if trailing_trades else 0
    return results


# ---------------------------------------------------------------------------
# 5) Calculate theoretical callback rates for current market
# ---------------------------------------------------------------------------
def calculate_theoretical_rates(params: dict, current_price: float = None):
    """Calculate what the trailing callback would be at various ATR levels."""
    if not current_price:
        # Try to get from Binance
        try:
            import urllib.request
            url = "https://fapi.binance.com/fapi/v1/ticker/price?symbol=BTCUSDT"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read())
                current_price = float(data["price"])
        except Exception:
            current_price = 85000  # Fallback

    mult = params.get("atr_multiplier", 1.5)
    min_bps = params.get("min_bps", 10)
    max_bps = params.get("max_bps", 1000)

    print(f"\n{'='*70}")
    print(f"📊 Trailing Stop Callback Rate 模拟 (当前价格: ${current_price:,.2f})")
    print(f"{'='*70}")
    print(f"  公式: callback_bps = (ATR × {mult} / price) × 10000")
    print(f"  钳制范围: [{min_bps}, {max_bps}] bps ({min_bps/100:.1f}% ~ {max_bps/100:.1f}%)")
    print()

    # v43.0: Trailing now uses 4H ATR (same as SL/TP)
    print(f"  ✅ v43.0: trailing stop 使用 4H ATR (与 SL/TP 统一)")
    print(f"     ATR multiplier: 0.6 (4H 尺度)")
    print(f"     激活阈值: 1.5R")
    print()

    # Simulate different ATR levels
    print(f"  {'ATR ($)':>10} │ {'Trailing ($)':>12} │ {'Callback':>10} │ {'BPS':>6} │ {'vs SL 4H':>12} │ 评估")
    print(f"  {'─'*10}─┼─{'─'*12}─┼─{'─'*10}─┼─{'─'*6}─┼─{'─'*12}─┼─{'─'*20}")

    # Typical BTC ATR ranges (30M)
    atr_levels = [200, 300, 400, 500, 600, 800, 1000, 1500, 2000]

    for atr in atr_levels:
        trailing_dist = atr * mult
        raw_bps = int((trailing_dist / current_price) * 10000)
        clamped_bps = max(min_bps, min(max_bps, raw_bps))
        actual_dist = current_price * clamped_bps / 10000
        callback_pct = clamped_bps / 100

        # Compare with 4H SL (4H ATR is typically 3-5x 30M ATR)
        # SL multiplier for 4H: HIGH=0.8, MED=1.0
        estimated_4h_atr = atr * 4  # rough estimate
        sl_distance_med = estimated_4h_atr * 1.0  # MEDIUM conf SL
        ratio = actual_dist / sl_distance_med if sl_distance_med else 0

        if callback_pct < 0.3:
            assessment = "❌ 过紧! 噪音频繁触发"
        elif callback_pct < 0.5:
            assessment = "⚠️ 偏紧, 易被洗出"
        elif callback_pct < 1.0:
            assessment = "⚠️ 中等, 快速行情可能触发"
        elif callback_pct < 2.0:
            assessment = "✅ 合理"
        elif callback_pct < 4.0:
            assessment = "✅ 宽松, 保护利润"
        else:
            assessment = "ℹ️ 很宽, 可能保护不足"

        clamped_note = " (clamped)" if raw_bps != clamped_bps else ""
        print(
            f"  ${atr:>8,.0f} │ ${actual_dist:>10,.2f} │ "
            f"{callback_pct:>8.2f}% │ {clamped_bps:>5}{clamped_note:>1} │ "
            f"{ratio:>10.1%} of SL │ {assessment}"
        )

    return current_price


# ---------------------------------------------------------------------------
# 6) Check layer_orders.json for current trailing state
# ---------------------------------------------------------------------------
def check_current_layers():
    """Check current layer orders for trailing stop status."""
    if not LAYER_FILE.exists():
        print("\n  ℹ️ 无活跃层级 (layer_orders.json 不存在)")
        return

    with open(LAYER_FILE) as f:
        layers = json.load(f)

    if not layers:
        print("\n  ℹ️ 无活跃层级 (空)")
        return

    print(f"\n{'='*70}")
    print(f"📋 当前层级 Trailing Stop 状态")
    print(f"{'='*70}")

    for layer_id, layer in layers.items():
        entry = layer.get("entry_price", 0)
        sl = layer.get("sl_price", 0)
        tp = layer.get("tp_price", 0)
        trailing_id = layer.get("trailing_order_id", "")
        trailing_active = layer.get("trailing_active", False)
        side = layer.get("side", "?")

        has_trailing = bool(trailing_id)
        risk = abs(entry - sl) if entry and sl else 0
        activation_price = 0
        if risk > 0 and entry:
            if side.upper() in ("LONG", "BUY"):
                activation_price = entry + (risk * 1.1)
            else:
                activation_price = entry - (risk * 1.1)

        status = "✅ 已提交" if has_trailing else "❌ 未提交"

        print(f"\n  Layer: {layer_id}")
        print(f"    Side: {side} | Entry: ${entry:,.2f} | SL: ${sl:,.2f} | TP: ${tp:,.2f}")
        print(f"    Risk (1R): ${risk:,.2f}")
        print(f"    Trailing: {status} (ID: {trailing_id or 'N/A'})")
        if activation_price:
            print(f"    Activation Price: ${activation_price:,.2f} (1.1R profit)")


# ---------------------------------------------------------------------------
# 7) Key finding: 30M ATR vs 4H ATR mismatch
# ---------------------------------------------------------------------------
def analyze_atr_mismatch():
    """Highlight the potential 30M vs 4H ATR mismatch issue."""
    print(f"\n{'='*70}")
    print(f"🔍 关键分析: Trailing Stop ATR 源 vs SL/TP ATR 源")
    print(f"{'='*70}")
    print()
    print("  v39.0 将 SL/TP 的 ATR 源从 30M 迁移到 4H:")
    print("    SL = 4H_ATR × multiplier (HIGH=0.8, MED=1.0, LOW=1.0)")
    print()
    print("  ✅ v43.0: Trailing Stop 已迁移至 4H ATR:")
    print("    Callback = 4H_ATR × 0.6 / price")
    print("    激活门槛 = 1.5R (从 1.1R 提升)")
    print()
    print("  Trailing 距离与 SL 距离比例:")
    print("    → SL 距离 ≈ 4H_ATR × 1.0")
    print("    → Trailing 距离 ≈ 4H_ATR × 0.6")
    print("    → Trailing callback ≈ SL 的 60% (合理比例)")
    print()
    print("  💡 设计意图:")
    print("    1. 价格到达 1.5R 利润后 trailing 激活 (足够覆盖费用)")
    print("    2. Trailing 回调容忍度 = SL 的 ~60%，允许正常波动")
    print("    3. 统一 ATR 源 (4H) 消除 SL/Trailing 尺度不匹配问题")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Trailing Stop Diagnostic Tool")
    parser.add_argument("--days", type=int, default=7, help="Days to analyze (default: 7)")
    parser.add_argument("--hours", type=int, default=0, help="Hours to analyze (overrides --days)")
    args = parser.parse_args()

    if args.hours > 0:
        days = max(1, args.hours // 24)
        hours = args.hours
    else:
        days = args.days
        hours = days * 24

    print(f"{'='*70}")
    print(f"🔍 Trailing Stop 诊断报告")
    print(f"   分析范围: 最近 {days} 天 ({hours} 小时)")
    print(f"   时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")

    # --- Section 1: Trading Memory Analysis ---
    print(f"\n{'='*70}")
    print(f"📊 Section 1: 平仓原因统计 (trading_memory.json)")
    print(f"{'='*70}")

    trades = analyze_memory(days)
    if not trades:
        print("  ⚠️ 无最近交易记录")
    else:
        print(f"  最近 {days} 天共 {len(trades)} 笔交易\n")
        categories = categorize_trades(trades)

        total = len(trades)
        win_count = sum(1 for t in trades if (t.get("pnl_pct", t.get("pnl_percentage", 0)) or 0) > 0)
        loss_count = total - win_count

        print(f"  总览: {total} 笔 | 盈利 {win_count} | 亏损 {loss_count} | 胜率 {win_count/total*100:.1f}%\n")

        for cat, cat_trades in sorted(categories.items()):
            cat_pnls = [t["pnl_pct"] for t in cat_trades]
            avg_pnl = sum(cat_pnls) / len(cat_pnls) if cat_pnls else 0
            wins = sum(1 for p in cat_pnls if p > 0)
            losses = sum(1 for p in cat_pnls if p <= 0)

            icon = {
                "TRAILING_STOP": "🔄",
                "STOP_LOSS": "🛑",
                "TAKE_PROFIT": "🎯",
                "MANUAL": "👤",
                "TIME_BARRIER": "⏰",
            }.get(cat, "❓")

            print(f"  {icon} {cat}: {len(cat_trades)} 笔 ({len(cat_trades)/total*100:.1f}%)")
            print(f"     盈利 {wins} / 亏损 {losses} | 均 PnL: {avg_pnl:+.3f}%")

            # Show details for trailing stops
            if cat == "TRAILING_STOP":
                for t in cat_trades:
                    side_cn = "多" if t["side"].upper() in ("LONG", "BUY") else "空"
                    pnl_icon = "✅" if t["pnl_pct"] > 0 else "❌"
                    print(
                        f"     {pnl_icon} {t['timestamp'][:16]} | {side_cn} | "
                        f"入 ${t['entry']:,.2f} → 出 ${t['exit']:,.2f} | "
                        f"PnL {t['pnl_pct']:+.3f}% | {t['grade']}"
                    )
                    if t["sl"] and t["entry"]:
                        sl_dist_pct = abs(t["entry"] - t["sl"]) / t["entry"] * 100
                        print(f"        SL 距离: {sl_dist_pct:.2f}% | TP: ${t['tp']:,.2f}")

            # Show details for fixed SL
            if cat == "STOP_LOSS":
                for t in cat_trades:
                    side_cn = "多" if t["side"].upper() in ("LONG", "BUY") else "空"
                    print(
                        f"     ❌ {t['timestamp'][:16]} | {side_cn} | "
                        f"入 ${t['entry']:,.2f} → 出 ${t['exit']:,.2f} | "
                        f"PnL {t['pnl_pct']:+.3f}% | {t['grade']}"
                    )
            print()

        # Simulation
        sim = simulate_alternatives(trades, 1.5)
        if sim:
            print(f"  --- Trailing Stop 深度分析 ---")
            print(f"  Trailing 平仓数: {sim['trailing_count']}")
            print(f"  Trailing 平均 PnL: {sim['trailing_avg_pnl']:+.3f}%")
            print(f"  亏损的 Trailing 平仓: {sim['worse_than_sl']} 笔")
            print(f"  可能错过 TP 的: {sim['could_have_hit_tp']} 笔")
            if sim["worse_than_sl"] > 0:
                print(f"  ⚠️ {sim['worse_than_sl']} 笔 trailing stop 导致亏损!")
                print(f"     这表明 trailing 在利润不足时就被触发")

    # --- Section 2: Current Parameters ---
    print(f"\n{'='*70}")
    print(f"⚙️ Section 2: 当前参数")
    print(f"{'='*70}")

    params = analyze_parameters()
    print(f"  Trailing 激活门槛: {params['activation_r']}R (利润达到 {params['activation_r']}× 风险距离)")
    print(f"  Trailing ATR 乘数: {params['atr_multiplier']}× (4H ATR, v43.0)")
    print(f"  Callback 钳制范围: [{params['min_bps']}, {params['max_bps']}] bps "
          f"({params['min_bps']/100:.1f}% ~ {params['max_bps']/100:.1f}%)")

    if "sl_config_raw" in params:
        print(f"\n  SL 配置 (4H ATR, v39.0):")
        for line in params["sl_config_raw"].split("\n"):
            print(f"    {line.strip()}")
    if "tp_config_raw" in params:
        print(f"\n  TP 配置:")
        for line in params["tp_config_raw"].split("\n"):
            print(f"    {line.strip()}")

    # --- Section 3: Callback Rate Simulation ---
    current_price = calculate_theoretical_rates(params)

    # --- Section 4: ATR Mismatch Analysis ---
    analyze_atr_mismatch()

    # --- Section 5: Log Analysis ---
    print(f"\n{'='*70}")
    print(f"📜 Section 5: 日志分析 (最近 {hours}h)")
    print(f"{'='*70}")

    log_data = parse_logs_for_trailing(hours)
    print(f"  Trailing 提交事件: {len(log_data['submissions'])}")
    print(f"  Trailing 成交事件: {len(log_data['fills'])}")
    print(f"  错误事件: {len(log_data['errors'])}")

    if log_data["submissions"]:
        print(f"\n  最近 Trailing 提交:")
        for s in log_data["submissions"][-5:]:
            bps = s.get("callback_bps", "?")
            pct = s.get("callback_pct", "?")
            print(f"    • {bps} bps ({pct}%) — {s['raw'][:120]}")

    if log_data["fills"]:
        print(f"\n  最近 Trailing 成交:")
        for f_entry in log_data["fills"][-5:]:
            print(f"    • {f_entry['raw'][:120]}")

    if log_data["errors"]:
        print(f"\n  ⚠️ Trailing 错误:")
        for e in log_data["errors"][-3:]:
            print(f"    • {e[:120]}")

    if log_data["atr_values"]:
        atr_vals = log_data["atr_values"]
        avg_atr = sum(atr_vals) / len(atr_vals)
        min_atr = min(atr_vals)
        max_atr = max(atr_vals)
        print(f"\n  ATR 统计 (从日志): 均值=${avg_atr:,.2f} | 最小=${min_atr:,.2f} | 最大=${max_atr:,.2f}")

        # Calculate actual callback at average ATR
        if current_price:
            avg_callback_bps = int((avg_atr * params["atr_multiplier"] / current_price) * 10000)
            avg_callback_bps = max(params["min_bps"], min(params["max_bps"], avg_callback_bps))
            print(f"  → 当前均值 ATR 的 callback: {avg_callback_bps} bps ({avg_callback_bps/100:.2f}%)")

    # --- Section 6: Current Layers ---
    check_current_layers()

    # --- Section 7: Summary ---
    print(f"\n{'='*70}")
    print(f"📋 诊断总结")
    print(f"{'='*70}")

    issues = []

    # Check if trailing stops are causing most losses
    if trades:
        cats = categorize_trades(trades)
        trailing = cats.get("TRAILING_STOP", [])
        sl_trades = cats.get("STOP_LOSS", [])

        if trailing:
            trailing_losses = [t for t in trailing if t["pnl_pct"] <= 0]
            if len(trailing_losses) > len(trailing) * 0.5:
                issues.append(
                    f"❌ {len(trailing_losses)}/{len(trailing)} trailing stop 平仓亏损 "
                    f"(>50% 是亏损的!)"
                )

            trailing_avg = sum(t["pnl_pct"] for t in trailing) / len(trailing)
            if trailing_avg < 0:
                issues.append(
                    f"❌ Trailing stop 平均 PnL 为负 ({trailing_avg:+.3f}%)"
                )

        if not trailing and sl_trades:
            issues.append(
                f"ℹ️ 无 trailing stop 平仓记录, {len(sl_trades)} 笔固定 SL 平仓"
            )

    issues.append(
        "✅ v43.0: Trailing 已迁移至 4H ATR (与 SL/TP 统一)\n"
        "     → Trailing callback ≈ SL 距离的 ~60% (ATR×0.6 / ATR×1.0)"
    )

    for i, issue in enumerate(issues, 1):
        print(f"  {i}. {issue}")

    print(f"\n  🔧 推荐操作:")
    print(f"     1. 对比上面的 trailing stop 平仓记录，看是否大量亏损")
    print(f"     2. v43.0 已将 trailing ATR 源迁移至 4H (multiplier=0.6, activation=1.5R)")
    print(f"     3. 如需调整，修改 _TRAILING_ATR_MULTIPLIER (当前 0.6) 或 _TRAILING_ACTIVATION_R (当前 1.5)")
    print(f"     4. 运行 /layer3 查看 confidence calibration 数据")

    print(f"\n{'='*70}")
    print(f"✅ 诊断完成")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
