#!/usr/bin/env python3
"""
端到端交易评估全链路实测
E2E Trade Evaluation Pipeline Test

完整诊断: 评级生成 → 存储 → 加载 → 格式化 → Agent 消费 → Web 展示

在服务器上运行:
  cd /home/linuxuser/nautilus_AlgVex
  source venv/bin/activate
  sudo systemctl stop nautilus-trader
  python3 scripts/e2e_trade_pipeline_test.py --auto         # 全部测试
  python3 scripts/e2e_trade_pipeline_test.py --auto --skip-trade   # 跳过交易 (省钱)
  python3 scripts/e2e_trade_pipeline_test.py --auto --skip-web     # 跳过 Web API
  python3 scripts/e2e_trade_pipeline_test.py --auto --cleanup      # 测试后清理 E2E 记录
  sudo systemctl start nautilus-trader

流程 (6 Phase):
  Phase 0: 环境预检 (Python版本, 服务状态, 持仓检查, 目录)
  Phase 1: Binance Futures 最小单开/平仓 (可 --skip-trade 跳过)
  Phase 2: evaluate_trade() → record_outcome() → 写入 trading_memory.json
  Phase 3: 评级生成全覆盖 — 7 个等级 (A+/A/B/C/D/D-/F) 合成验证 + 边缘用例
  Phase 4: 存储→加载→格式化一致性 — round-trip + evaluation_failed + 500 cap
  Phase 5: Agent 消费验证 — _get_past_memories() 格式化 + _score_memory() + 全 Grade 标签
  Phase 6: Web API 全链路 — TestClient 进程内测试 + Service 直测 + 脱敏验证 (可 --skip-web)
  Cleanup: 清理 E2E 测试记录 (--cleanup)
"""

import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

# ─── 项目路径 ───
PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

# ─── 颜色 ───
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

results = []


def check(name, passed, detail=""):
    status = f"{GREEN}PASS{RESET}" if passed else f"{RED}FAIL{RESET}"
    results.append({"name": name, "passed": passed})
    print(f"  [{status}] {name}")
    if detail:
        for line in str(detail).split("\n"):
            print(f"         {line}")


def info(name, detail=""):
    print(f"  [{BLUE}INFO{RESET}] {name}")
    if detail:
        for line in str(detail).split("\n"):
            print(f"         {line}")


def warn(name, detail=""):
    print(f"  [{YELLOW}WARN{RESET}] {name}")
    if detail:
        for line in str(detail).split("\n"):
            print(f"         {line}")


def section(title):
    print(f"\n{BOLD}{CYAN}{'='*60}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'='*60}{RESET}")


def run_cmd(cmd, **kwargs):
    """Run subprocess with sane defaults."""
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    kwargs.setdefault("timeout", 30)
    return subprocess.run(cmd, **kwargs)


def load_env():
    """加载 ~/.env.algvex"""
    env_file = Path.home() / ".env.algvex"
    if not env_file.exists():
        print(f"{RED}ERROR: ~/.env.algvex 不存在{RESET}")
        sys.exit(1)
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ[key.strip()] = val.strip()
    info("环境变量已加载", str(env_file))


# ══════════════════════════════════════════════════════════════
#  PHASE 0: 环境预检
# ══════════════════════════════════════════════════════════════
def phase0_prechecks():
    section("PHASE 0: 环境预检")
    all_ok = True

    # 1. Python 版本
    v = sys.version_info
    py_ok = v.major == 3 and v.minor >= 12
    check("Python 版本 >= 3.12", py_ok, f"当前: {v.major}.{v.minor}.{v.micro}")
    if not py_ok:
        all_ok = False

    # 2. nautilus-trader 服务已停止
    try:
        r = run_cmd(["systemctl", "is-active", "nautilus-trader"], timeout=5)
        svc_stopped = r.stdout.strip() != "active"
        check("nautilus-trader 已停止", svc_stopped,
              f"状态: {r.stdout.strip()}" if not svc_stopped else "")
        if not svc_stopped:
            warn("Bot 运行中可能干扰测试!", "请先: sudo systemctl stop nautilus-trader")
            all_ok = False
    except Exception:
        info("systemctl 检查跳过 (非 systemd 环境)")

    # 3. data/ 目录
    data_dir = PROJECT_ROOT / "data"
    if not data_dir.exists():
        data_dir.mkdir(parents=True, exist_ok=True)
        info("已创建 data/ 目录")
    check("data/ 目录存在", data_dir.exists())

    # 4. 关键文件
    for name, path in [
        ("main_live.py", PROJECT_ROOT / "main_live.py"),
        ("strategy/trading_logic.py", PROJECT_ROOT / "strategy" / "trading_logic.py"),
        ("agents/multi_agent_analyzer.py", PROJECT_ROOT / "agents" / "multi_agent_analyzer.py"),
        ("configs/base.yaml", PROJECT_ROOT / "configs" / "base.yaml"),
    ]:
        check(f"文件存在: {name}", path.exists())
        if not path.exists():
            all_ok = False

    # 5. ~/.env.algvex
    env_file = Path.home() / ".env.algvex"
    check("~/.env.algvex 存在", env_file.exists())
    if env_file.exists():
        with open(env_file) as f:
            content = f.read()
        has_binance = "BINANCE_API_KEY=" in content and "BINANCE_API_SECRET=" in content
        has_deepseek = "DEEPSEEK_API_KEY=" in content
        check("Binance API 密钥配置", has_binance)
        check("DeepSeek API 密钥配置", has_deepseek)
        if not has_binance:
            all_ok = False
    else:
        all_ok = False

    # 6. Binance SDK
    try:
        from binance.um_futures import UMFutures  # noqa: F401
        check("binance SDK 可导入", True)
    except ImportError:
        check("binance SDK 可导入", False, "pip install binance-futures-connector")
        all_ok = False

    # 7. 检查 Binance 残留持仓
    try:
        api_key = os.getenv('BINANCE_API_KEY')
        api_secret = os.getenv('BINANCE_API_SECRET')
        if api_key and api_secret:
            from binance.um_futures import UMFutures
            client = UMFutures(key=api_key, secret=api_secret)
            positions = client.get_position_risk(symbol="BTCUSDT")
            open_pos = [p for p in positions if float(p.get('positionAmt', 0)) != 0]
            check("Binance 无残留 BTCUSDT 持仓", len(open_pos) == 0,
                  f"发现 {len(open_pos)} 个持仓!" if open_pos else "")
            if open_pos:
                for p in open_pos:
                    warn(f"残留持仓: {p.get('positionAmt')} BTC, "
                         f"未实现盈亏: {p.get('unRealizedProfit')}")
                all_ok = False
    except Exception as e:
        warn(f"持仓检查失败: {e}")

    return all_ok


# ══════════════════════════════════════════════════════════════
#  PHASE 1: Binance 实际下单 + 平仓
# ══════════════════════════════════════════════════════════════
def phase1_binance_trade():
    section("PHASE 1/6: Binance 实际交易 (最小单)")

    from binance.um_futures import UMFutures

    api_key = os.getenv('BINANCE_API_KEY')
    api_secret = os.getenv('BINANCE_API_SECRET')
    client = UMFutures(key=api_key, secret=api_secret)

    # 获取当前价格
    ticker = client.ticker_price("BTCUSDT")
    current_price = float(ticker['price'])
    info("当前 BTC 价格", f"${current_price:,.2f}")

    # 最小下单量: BTCUSDT 最小 0.002 BTC
    qty = "0.002"
    info("下单数量", f"{qty} BTC (最小单, 约 ${current_price * 0.002:,.2f})")

    # Step 1: 开多 (LONG)
    entry_time = datetime.now(timezone.utc)
    try:
        open_order = client.new_order(
            symbol="BTCUSDT",
            side="BUY",
            type="MARKET",
            quantity=qty,
        )
        entry_order_id = open_order.get('orderId')
        check("LONG 开仓下单", True, f"orderId={entry_order_id}")
        info("开仓响应", json.dumps(open_order, indent=2)[:300])
    except Exception as e:
        check("LONG 开仓下单", False, str(e))
        return None

    # 等待成交
    time.sleep(1)

    # 验证订单已成交
    try:
        order_status = client.query_order(symbol="BTCUSDT", orderId=entry_order_id)
        filled = order_status.get('status') == 'FILLED'
        exec_qty = order_status.get('executedQty', '0')
        check("开仓订单已成交", filled,
              f"status={order_status.get('status')}, executedQty={exec_qty}")
        if not filled:
            warn("订单未成交，尝试取消并退出")
            try:
                client.cancel_order(symbol="BTCUSDT", orderId=entry_order_id)
            except Exception:
                pass
            return None
    except Exception as e:
        warn(f"查询订单状态失败: {e}, 继续尝试获取成交价")

    # 获取成交价 (加权平均)
    try:
        entry_trades = client.get_account_trades(symbol="BTCUSDT", orderId=entry_order_id)
        if entry_trades:
            # 加权平均价 (处理部分成交)
            total_qty = sum(float(t['qty']) for t in entry_trades)
            total_val = sum(float(t['qty']) * float(t['price']) for t in entry_trades)
            entry_price = total_val / total_qty if total_qty > 0 else current_price
            check("获取入场价", True,
                  f"${entry_price:,.2f} ({len(entry_trades)} 笔成交)")
        else:
            entry_price = current_price
            warn("无成交记录，使用 ticker 价格")
    except Exception:
        entry_price = current_price
        warn("获取入场价失败，使用 ticker 价格")

    # Step 2: 立即平仓 (SELL)
    try:
        close_order = client.new_order(
            symbol="BTCUSDT",
            side="SELL",
            type="MARKET",
            quantity=qty,
            reduceOnly="true",
        )
        close_order_id = close_order.get('orderId')
        check("平仓下单", True, f"orderId={close_order_id}")
    except Exception as e:
        check("平仓下单", False, str(e))
        warn("!! 手动平仓: 登录 Binance Futures 关闭仓位 !!")
        return None

    exit_time = datetime.now(timezone.utc)
    time.sleep(1)

    # 验证平仓已成交
    try:
        close_status = client.query_order(symbol="BTCUSDT", orderId=close_order_id)
        close_filled = close_status.get('status') == 'FILLED'
        check("平仓订单已成交", close_filled,
              f"status={close_status.get('status')}")
        if not close_filled:
            warn("!! 平仓未成交! 手动检查 Binance Futures !!")
    except Exception as e:
        warn(f"查询平仓状态失败: {e}")

    # 获取平仓成交价 (加权平均)
    try:
        exit_trades = client.get_account_trades(symbol="BTCUSDT", orderId=close_order_id)
        if exit_trades:
            total_qty = sum(float(t['qty']) for t in exit_trades)
            total_val = sum(float(t['qty']) * float(t['price']) for t in exit_trades)
            exit_price = total_val / total_qty if total_qty > 0 else current_price
            check("获取出场价", True,
                  f"${exit_price:,.2f} ({len(exit_trades)} 笔成交)")
        else:
            exit_price = current_price
            warn("无成交记录，使用 ticker 价格")
    except Exception:
        exit_price = current_price
        warn("获取出场价失败，使用 ticker 价格")

    # 验证无残留持仓
    try:
        positions = client.get_position_risk(symbol="BTCUSDT")
        open_pos = [p for p in positions if float(p.get('positionAmt', 0)) != 0]
        check("平仓后无残留持仓", len(open_pos) == 0,
              f"残留: {open_pos[0].get('positionAmt')} BTC" if open_pos else "")
    except Exception as e:
        warn(f"持仓验证失败: {e}")

    # 计算实际 P&L
    pnl_pct = round((exit_price - entry_price) / entry_price * 100, 4)
    pnl_usdt = round((exit_price - entry_price) * float(qty), 4)
    info("交易结果", f"入场=${entry_price:,.2f}, 出场=${exit_price:,.2f}, "
         f"P&L={pnl_pct:+.4f}% (${pnl_usdt:+.4f})")

    return {
        "entry_price": entry_price,
        "exit_price": exit_price,
        "pnl_pct": pnl_pct,
        "pnl_usdt": pnl_usdt,
        "direction": "LONG",
        "quantity": float(qty),
        "entry_time": entry_time.isoformat(),
        "exit_time": exit_time.isoformat(),
        "entry_order_id": entry_order_id,
        "close_order_id": close_order_id,
    }


# ══════════════════════════════════════════════════════════════
#  PHASE 2: evaluate_trade() + record_outcome()
# ══════════════════════════════════════════════════════════════
def phase2_evaluate_and_record(trade_data):
    section("PHASE 2/6: evaluate_trade() → record_outcome()")

    if not trade_data:
        warn("无交易数据，跳过")
        return None

    # Step 1: evaluate_trade
    try:
        from strategy.trading_logic import evaluate_trade
        check("evaluate_trade 导入成功", True)
    except Exception as e:
        check("evaluate_trade 导入成功", False, str(e))
        return None

    # 模拟 SL/TP (测试单无真实 SL/TP)
    entry = trade_data["entry_price"]
    sl_distance = entry * 0.01   # 1% SL
    tp_distance = entry * 0.02   # 2% TP (R/R = 2:1)
    planned_sl = round(entry - sl_distance, 2)
    planned_tp = round(entry + tp_distance, 2)
    info("模拟 SL/TP (测试用)", f"SL=${planned_sl:,.2f}, TP=${planned_tp:,.2f}, R/R=2:1")

    try:
        evaluation = evaluate_trade(
            entry_price=trade_data["entry_price"],
            exit_price=trade_data["exit_price"],
            planned_sl=planned_sl,
            planned_tp=planned_tp,
            direction=trade_data["direction"],
            pnl_pct=trade_data["pnl_pct"],
            confidence="MEDIUM",
            position_size_pct=1.0,
            entry_timestamp=trade_data["entry_time"],
            exit_timestamp=trade_data["exit_time"],
        )
        check("evaluate_trade() 执行成功", True)
        info("评级结果", json.dumps(evaluation, indent=2))
    except Exception as e:
        check("evaluate_trade() 执行成功", False, traceback.format_exc())
        return None

    # 验证评级结构
    required = [
        "grade", "direction_correct", "entry_price", "exit_price",
        "planned_sl", "planned_tp", "planned_rr", "actual_rr",
        "execution_quality", "exit_type", "confidence",
        "position_size_pct", "hold_duration_min"
    ]
    missing = [k for k in required if k not in evaluation]
    check("评级包含全部 13 字段", len(missing) == 0,
          f"缺少: {missing}" if missing else f"grade={evaluation['grade']}, "
          f"actual_rr={evaluation.get('actual_rr')}, exit_type={evaluation.get('exit_type')}")

    # Step 2: record_outcome
    import logging
    memory_file = str(PROJECT_ROOT / "data" / "trading_memory.json")
    conditions = (
        f"E2E_TEST: price=${trade_data['entry_price']:,.2f}, "
        f"entry_order={trade_data['entry_order_id']}, "
        f"close_order={trade_data['close_order_id']}"
    )

    try:
        from agents.multi_agent_analyzer import MultiAgentAnalyzer

        analyzer = MultiAgentAnalyzer.__new__(MultiAgentAnalyzer)
        analyzer.logger = logging.getLogger("e2e_test")
        analyzer.memory_file = memory_file

        # 加载已有记忆
        if os.path.exists(memory_file):
            with open(memory_file) as f:
                analyzer.decision_memory = json.load(f)
        else:
            analyzer.decision_memory = []

        info("已有记忆条数", f"{len(analyzer.decision_memory)}")

        analyzer.record_outcome(
            decision=trade_data["direction"],
            pnl=trade_data["pnl_pct"],
            conditions=conditions,
            evaluation=evaluation,
        )
        check("record_outcome() 执行成功", True)
    except ImportError:
        # MultiAgentAnalyzer 无法导入 (缺 openai 等)，手动写入
        warn("MultiAgentAnalyzer 导入失败，使用内联写入")
        if os.path.exists(memory_file):
            with open(memory_file) as f:
                memories = json.load(f)
        else:
            memories = []

        grade = evaluation.get('grade', '')
        actual_rr = evaluation.get('actual_rr', 0)
        exit_type = evaluation.get('exit_type', '')
        lesson_map = {
            'A+': f"Grade A+: Strong win (R/R {actual_rr:.1f}:1) - repeat this pattern",
            'A': f"Grade A: Strong win (R/R {actual_rr:.1f}:1) - repeat this pattern",
            'B': f"Grade B: Acceptable profit (R/R {actual_rr:.1f}:1)",
            'C': f"Grade C: Small profit but low R/R ({actual_rr:.1f}:1) - tighten entry",
            'D': f"Grade D: Controlled loss via {exit_type} - discipline maintained",
            'D-': "Grade D-: Loss without SL data - discipline unknown, ensure SL/TP capture",
            'F': "Grade F: Uncontrolled loss - review SL placement",
        }
        lesson = lesson_map.get(grade, f"E2E test trade - grade {grade}")

        memories.append({
            "decision": trade_data["direction"],
            "pnl": round(trade_data["pnl_pct"], 2),
            "conditions": conditions,
            "lesson": lesson,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "evaluation": evaluation,
        })

        # Cap at 500
        if len(memories) > 500:
            memories = memories[-500:]

        os.makedirs(os.path.dirname(memory_file), exist_ok=True)
        with open(memory_file, 'w') as f:
            json.dump(memories, f, indent=2)
        check("record_outcome() 内联写入成功", True)
    except Exception:
        check("record_outcome() 执行成功", False, traceback.format_exc())
        return None

    # Step 3: 验证文件已写入
    memory_path = Path(memory_file)
    check("trading_memory.json 存在", memory_path.exists(),
          f"大小: {memory_path.stat().st_size} bytes" if memory_path.exists() else "")

    if memory_path.exists():
        with open(memory_path) as f:
            data = json.load(f)

        # 验证 JSON 是 list
        check("trading_memory.json 格式正确 (list)", isinstance(data, list),
              f"实际类型: {type(data).__name__}")

        with_eval = [m for m in data if m.get("evaluation")]
        check("含 evaluation 的记录", len(with_eval) > 0,
              f"{len(with_eval)}/{len(data)} 条")

        # 验证最新条目是我们的
        latest = data[-1]
        is_our_trade = "E2E_TEST" in latest.get("conditions", "")
        check("最新记录是我们的测试交易", is_our_trade,
              f"条件: {latest.get('conditions', '')[:100]}")

        # 验证 evaluation 结构完整
        if latest.get("evaluation"):
            eval_fields = set(latest["evaluation"].keys())
            expected = set(required)
            check("evaluation 字段完整", expected.issubset(eval_fields),
                  f"缺少: {expected - eval_fields}" if not expected.issubset(eval_fields) else "")

        info("最新记录完整内容", json.dumps(latest, indent=2, ensure_ascii=False)[:500])

    return evaluation


# ══════════════════════════════════════════════════════════════
#  PHASE 3: 评级生成全覆盖 — 7 个等级合成验证 + 边缘用例
# ══════════════════════════════════════════════════════════════

# Synthetic trade scenarios for each grade
SYNTHETIC_TRADES = {
    'A+': dict(
        entry_price=100000.0, exit_price=103000.0,
        planned_sl=99000.0, planned_tp=103000.0,
        direction='LONG', pnl_pct=3.0, confidence='HIGH',
        desc='大幅盈利 R/R≈3:1',
    ),
    'A': dict(
        entry_price=100000.0, exit_price=101800.0,
        planned_sl=99000.0, planned_tp=102000.0,
        direction='LONG', pnl_pct=1.8, confidence='HIGH',
        desc='强盈利 R/R≈1.8:1',
    ),
    'B': dict(
        entry_price=100000.0, exit_price=101200.0,
        planned_sl=99000.0, planned_tp=102000.0,
        direction='LONG', pnl_pct=1.2, confidence='MEDIUM',
        desc='可接受盈利 R/R≈1.2:1',
    ),
    'C': dict(
        entry_price=100000.0, exit_price=100500.0,
        planned_sl=99000.0, planned_tp=102000.0,
        direction='LONG', pnl_pct=0.5, confidence='MEDIUM',
        desc='小盈利 R/R<1:1',
    ),
    'D': dict(
        entry_price=100000.0, exit_price=99200.0,
        planned_sl=99000.0, planned_tp=102000.0,
        direction='LONG', pnl_pct=-0.8, confidence='MEDIUM',
        desc='受控亏损 (在 SL 范围内)',
    ),
    'D-': dict(
        entry_price=100000.0, exit_price=96000.0,
        planned_sl=None, planned_tp=None,
        direction='LONG', pnl_pct=-4.0, confidence='LOW',
        desc='大亏损但无 SL 数据 (纪律未知)',
    ),
    'F': dict(
        entry_price=100000.0, exit_price=97000.0,
        planned_sl=99000.0, planned_tp=102000.0,
        direction='LONG', pnl_pct=-3.0, confidence='LOW',
        desc='失控亏损 (超出 SL 120%)',
    ),
}

EVAL_REQUIRED_FIELDS = [
    "grade", "direction_correct", "entry_price", "exit_price",
    "planned_sl", "planned_tp", "planned_rr", "actual_rr",
    "execution_quality", "exit_type", "confidence",
    "position_size_pct", "hold_duration_min",
]


def phase3_grade_generation_coverage():
    """Phase 3: 用合成数据测试全部 7 个等级 + 边缘用例"""
    section("PHASE 3/6: 评级生成全覆盖验证")

    try:
        from strategy.trading_logic import evaluate_trade
        check("evaluate_trade 导入成功", True)
    except Exception as e:
        check("evaluate_trade 导入成功", False, str(e))
        return {}

    grade_results = {}  # grade -> evaluation dict
    all_ok = True

    # ── 3a: 每个等级的合成测试 ──
    info("--- 3a: 7 个等级合成测试 ---")
    for expected_grade, params in SYNTHETIC_TRADES.items():
        desc = params.pop('desc', '')
        try:
            ev = evaluate_trade(
                entry_price=params['entry_price'],
                exit_price=params['exit_price'],
                planned_sl=params['planned_sl'],
                planned_tp=params['planned_tp'],
                direction=params['direction'],
                pnl_pct=params['pnl_pct'],
                confidence=params.get('confidence', 'MEDIUM'),
                position_size_pct=5.0,
                entry_timestamp="2026-01-01T00:00:00",
                exit_timestamp="2026-01-01T01:30:00",
            )
            actual_grade = ev.get('grade', '?')
            passed = actual_grade == expected_grade
            check(
                f"Grade {expected_grade}: {desc}",
                passed,
                f"期望={expected_grade}, 实际={actual_grade}, "
                f"R/R={ev.get('actual_rr')}, exit={ev.get('exit_type')}"
            )
            if not passed:
                all_ok = False

            # 验证 13 字段完整
            missing = [f for f in EVAL_REQUIRED_FIELDS if f not in ev]
            if missing:
                check(f"Grade {expected_grade} 字段完整", False, f"缺少: {missing}")
                all_ok = False

            grade_results[expected_grade] = ev
        except Exception as e:
            check(f"Grade {expected_grade}: evaluate_trade() 异常", False, str(e))
            all_ok = False
        finally:
            params['desc'] = desc  # restore

    # ── 3b: 边缘用例 ──
    info("--- 3b: 边缘用例 ---")

    # Edge 1: planned_sl=0 应被清理为 None
    try:
        ev = evaluate_trade(
            entry_price=100000, exit_price=99000,
            planned_sl=0, planned_tp=102000,
            direction='LONG', pnl_pct=-1.0,
        )
        check("Edge: planned_sl=0 → 清理为 None",
              ev.get('planned_sl') is None,
              f"planned_sl={ev.get('planned_sl')}")
    except Exception as e:
        check("Edge: planned_sl=0 处理", False, str(e))

    # Edge 2: planned_sl 为负数
    try:
        ev = evaluate_trade(
            entry_price=100000, exit_price=99000,
            planned_sl=-5000, planned_tp=102000,
            direction='LONG', pnl_pct=-1.0,
        )
        check("Edge: planned_sl 负数 → 清理为 None",
              ev.get('planned_sl') is None,
              f"planned_sl={ev.get('planned_sl')}")
    except Exception as e:
        check("Edge: planned_sl 负数处理", False, str(e))

    # Edge 3: entry_price=0
    try:
        ev = evaluate_trade(
            entry_price=0, exit_price=100000,
            planned_sl=99000, planned_tp=102000,
            direction='LONG', pnl_pct=0.5,
        )
        check("Edge: entry_price=0 不崩溃", True,
              f"grade={ev.get('grade')}")
    except Exception as e:
        check("Edge: entry_price=0 处理", False, str(e))

    # Edge 4: SHORT 方向
    try:
        ev = evaluate_trade(
            entry_price=100000, exit_price=98000,
            planned_sl=101000, planned_tp=97000,
            direction='SHORT', pnl_pct=2.0,
        )
        check("Edge: SHORT 方向盈利评级",
              ev.get('grade') in ('A+', 'A', 'B'),
              f"grade={ev.get('grade')}, R/R={ev.get('actual_rr')}")
    except Exception as e:
        check("Edge: SHORT 方向处理", False, str(e))

    # Edge 5: 小亏损无 SL → D (not D-)
    try:
        ev = evaluate_trade(
            entry_price=100000, exit_price=99500,
            planned_sl=None, planned_tp=None,
            direction='LONG', pnl_pct=-0.5,
        )
        check("Edge: 小亏损(-0.5%) 无SL → Grade D",
              ev.get('grade') == 'D',
              f"grade={ev.get('grade')} (< 2% 亏损应判 D 非 D-)")
    except Exception as e:
        check("Edge: 小亏损无SL", False, str(e))

    # Edge 6: hold_duration 计算
    try:
        ev = evaluate_trade(
            entry_price=100000, exit_price=101500,
            planned_sl=99000, planned_tp=102000,
            direction='LONG', pnl_pct=1.5,
            entry_timestamp="2026-01-01T10:00:00",
            exit_timestamp="2026-01-01T12:30:00",
        )
        check("Edge: hold_duration 计算 (150 min)",
              ev.get('hold_duration_min') == 150,
              f"hold_duration_min={ev.get('hold_duration_min')}")
    except Exception as e:
        check("Edge: hold_duration 计算", False, str(e))

    # ── 3c: direction_correct 验证 ──
    info("--- 3c: direction_correct 验证 ---")
    for grade, ev in grade_results.items():
        if grade in ('A+', 'A', 'B', 'C'):
            ok = ev.get('direction_correct') is True
            check(f"Grade {grade}: direction_correct=True", ok)
        elif grade in ('D', 'D-', 'F'):
            ok = ev.get('direction_correct') is False
            check(f"Grade {grade}: direction_correct=False", ok)

    if all_ok:
        info("全部 7 个等级 + 边缘用例通过 ✓")

    return grade_results


# ══════════════════════════════════════════════════════════════
#  PHASE 4: 存储→加载→格式化一致性验证
# ══════════════════════════════════════════════════════════════
def phase4_storage_load_consistency(grade_results):
    """Phase 4: 验证 record_outcome 存储、加载 round-trip、evaluation_failed"""
    section("PHASE 4/6: 存储→加载→格式化一致性")

    import tempfile
    import logging

    tmp_dir = tempfile.mkdtemp(prefix="e2e_mem_")
    tmp_memory = os.path.join(tmp_dir, "test_memory.json")

    try:
        from agents.multi_agent_analyzer import MultiAgentAnalyzer
        analyzer = MultiAgentAnalyzer.__new__(MultiAgentAnalyzer)
        analyzer.logger = logging.getLogger("e2e_storage_test")
        analyzer.memory_file = tmp_memory
        analyzer.decision_memory = []
        has_analyzer = True
        check("MultiAgentAnalyzer 实例化 (临时文件)", True)
    except ImportError as e:
        warn(f"MultiAgentAnalyzer 导入失败 ({e}), 使用内联逻辑")
        has_analyzer = False

    # ── 4a: 写入全部 7 个等级的合成记录 ──
    info("--- 4a: 写入 7 个等级到临时文件 ---")
    for grade, params in SYNTHETIC_TRADES.items():
        ev = grade_results.get(grade)
        if not ev:
            warn(f"Grade {grade} 无评级结果, 跳过")
            continue

        conditions = (
            f"E2E_PHASE4: grade={grade}, "
            f"price=${params['entry_price']:,.0f}, RSI=55, MACD=bullish, BB=60%, "
            f"conf={params.get('confidence', 'MEDIUM')}, sentiment=neutral"
        )

        if has_analyzer:
            analyzer.record_outcome(
                decision=params['direction'],
                pnl=params['pnl_pct'],
                conditions=conditions,
                evaluation=ev,
            )
        else:
            # Inline write
            if not os.path.exists(tmp_memory):
                memories = []
            else:
                with open(tmp_memory) as f:
                    memories = json.load(f)
            memories.append({
                "decision": params['direction'],
                "pnl": round(params['pnl_pct'], 2),
                "conditions": conditions,
                "lesson": f"E2E test grade {grade}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "evaluation": ev,
            })
            with open(tmp_memory, 'w') as f:
                json.dump(memories, f, indent=2)

    check("临时文件已创建", os.path.exists(tmp_memory))

    # ── 4b: 加载 round-trip 验证 ──
    info("--- 4b: round-trip 一致性 ---")
    with open(tmp_memory) as f:
        loaded = json.load(f)

    check("加载记录数 == 写入数", len(loaded) == len(grade_results),
          f"loaded={len(loaded)}, expected={len(grade_results)}")

    rt_ok = True
    for entry in loaded:
        ev = entry.get('evaluation', {})
        stored_grade = ev.get('grade', '?')
        # 验证 grade 是已知等级
        if stored_grade not in ('A+', 'A', 'B', 'C', 'D', 'D-', 'F'):
            check(f"Round-trip: 未知 grade '{stored_grade}'", False)
            rt_ok = False
            continue
        # 验证 13 字段完整
        missing = [f for f in EVAL_REQUIRED_FIELDS if f not in ev]
        if missing:
            check(f"Round-trip Grade {stored_grade}: 字段完整", False,
                  f"缺少: {missing}")
            rt_ok = False
        # 验证 grade 与原始一致
        orig = grade_results.get(stored_grade, {})
        if orig and orig.get('actual_rr') != ev.get('actual_rr'):
            check(f"Round-trip Grade {stored_grade}: actual_rr 一致", False,
                  f"orig={orig.get('actual_rr')}, loaded={ev.get('actual_rr')}")
            rt_ok = False
    check("Round-trip 全部字段一致", rt_ok)

    # ── 4c: lesson 自动生成验证 ──
    info("--- 4c: lesson 自动生成验证 ---")
    lesson_ok = True
    for entry in loaded:
        lesson = entry.get('lesson', '')
        ev_grade = entry.get('evaluation', {}).get('grade', '')
        if ev_grade and not lesson:
            check(f"Grade {ev_grade}: lesson 不应为空", False)
            lesson_ok = False
        elif ev_grade and f"Grade {ev_grade}" not in lesson:
            check(f"Grade {ev_grade}: lesson 应包含 'Grade {ev_grade}'", False,
                  f"实际: {lesson[:80]}")
            lesson_ok = False
    check("所有记录 lesson 包含 Grade 标签", lesson_ok)

    # ── 4d: evaluation_failed 字段测试 ──
    info("--- 4d: evaluation_failed 字段测试 ---")
    if has_analyzer:
        pre_count = len(analyzer.decision_memory)
        analyzer.record_outcome(
            decision="LONG", pnl=-1.5,
            conditions="E2E_PHASE4: eval_failed test",
            eval_error_reason="timeout: DeepSeek API 30s",
        )
        latest = analyzer.decision_memory[-1]
        check("eval_error_reason → evaluation_failed 字段存在",
              'evaluation_failed' in latest,
              f"keys={list(latest.keys())}")
        check("evaluation_failed 值正确",
              latest.get('evaluation_failed') == "timeout: DeepSeek API 30s",
              f"值={latest.get('evaluation_failed')}")
        check("evaluation_failed 记录无 evaluation 字段",
              'evaluation' not in latest)
        # 移除这条测试记录
        analyzer.decision_memory.pop()
    else:
        info("跳过 evaluation_failed 测试 (无 MultiAgentAnalyzer)")

    # ── 4e: 500 条上限验证 ──
    info("--- 4e: 500 条上限验证 ---")
    if has_analyzer:
        # 保存原始数据
        orig_mem = analyzer.decision_memory[:]
        # 场景 1: 从 500 条开始, record_outcome 后应仍为 500
        analyzer.decision_memory = [
            {"decision": "LONG", "pnl": 0.1, "conditions": f"cap_test_{i}",
             "lesson": "test", "timestamp": "2026-01-01T00:00:00"}
            for i in range(500)
        ]
        analyzer.record_outcome(decision="LONG", pnl=0.5, conditions="cap_test_at_limit")
        check("500 cap: 从 500 条添加后仍 ≤ 500",
              len(analyzer.decision_memory) <= 500,
              f"当前数量={len(analyzer.decision_memory)}")
        # 场景 2: 从 502 条开始, pop(0) 每次只移除 1 条 → 502
        analyzer.decision_memory = [
            {"decision": "LONG", "pnl": 0.1, "conditions": f"cap_test_{i}",
             "lesson": "test", "timestamp": "2026-01-01T00:00:00"}
            for i in range(502)
        ]
        analyzer.record_outcome(decision="LONG", pnl=0.5, conditions="cap_test_overflow")
        check("500 cap: 从 502 条添加后 pop(0) → 502",
              len(analyzer.decision_memory) == 502,
              f"当前数量={len(analyzer.decision_memory)} (pop 每次仅移 1 条)")
        # 恢复
        analyzer.decision_memory = orig_mem
        analyzer._save_memory()
    else:
        info("跳过 500 cap 测试 (无 MultiAgentAnalyzer)")

    # ── 清理临时文件 ──
    try:
        shutil.rmtree(tmp_dir)
    except Exception:
        pass

    return tmp_memory


# ══════════════════════════════════════════════════════════════
#  PHASE 5: Agent 消费验证 (增强版)
# ══════════════════════════════════════════════════════════════
def phase5_agent_consumption(grade_results):
    """Phase 5: 验证 _get_past_memories 格式化 + _score_memory + 全 Grade 标签"""
    section("PHASE 5/6: Agent 记忆消费验证 (增强版)")

    import tempfile
    import logging

    # ── 5a: 用临时文件构建包含全部 7 个等级的记忆池 ──
    info("--- 5a: 构建全等级临时记忆池 ---")
    tmp_dir = tempfile.mkdtemp(prefix="e2e_agent_")
    tmp_memory = os.path.join(tmp_dir, "agent_test_memory.json")

    try:
        from agents.multi_agent_analyzer import MultiAgentAnalyzer
    except ImportError as e:
        warn(f"MultiAgentAnalyzer 导入失败 ({e})")
        warn("在服务器 venv 中应可正常运行，此处跳过")
        return

    analyzer = MultiAgentAnalyzer.__new__(MultiAgentAnalyzer)
    analyzer.logger = logging.getLogger("e2e_agent_test")
    analyzer.memory_file = tmp_memory
    analyzer.decision_memory = []

    # 写入合成记录 (盈利 + 亏损)
    for grade, params in SYNTHETIC_TRADES.items():
        ev = grade_results.get(grade)
        if not ev:
            continue
        conditions = (
            f"price=${params['entry_price']:,.0f}, RSI=55, MACD=bullish, BB=60%, "
            f"conf={params.get('confidence', 'MEDIUM')}, sentiment=neutral"
        )
        analyzer.record_outcome(
            decision=params['direction'],
            pnl=params['pnl_pct'],
            conditions=conditions,
            evaluation=ev,
        )
    check("临时记忆池", True, f"{len(analyzer.decision_memory)} 条记录")

    # ── 5b: _get_past_memories() 基础验证 ──
    info("--- 5b: _get_past_memories() 格式化验证 ---")
    try:
        memories_text = analyzer._get_past_memories()
        has_content = len(memories_text) > 0
        check("_get_past_memories() 返回内容", has_content,
              f"长度={len(memories_text)} 字符")

        if has_content:
            has_success = "SUCCESSFUL" in memories_text or "\u2705" in memories_text
            has_failed = "FAILED" in memories_text or "\u274c" in memories_text
            has_quality = "TRADE QUALITY" in memories_text

            check("包含 SUCCESSFUL TRADES 段", has_success)
            check("包含 FAILED TRADES 段", has_failed)
            check("包含 TRADE QUALITY 段", has_quality)
        else:
            warn("_get_past_memories() 返回空字符串")
    except Exception:
        check("_get_past_memories() 基础调用", False, traceback.format_exc())
        memories_text = ""

    # ── 5c: 全部 7 个 Grade 标签验证 ──
    info("--- 5c: Grade 标签验证 ---")
    all_grades_present = True
    for grade in ['A+', 'A', 'B', 'C', 'D', 'D-', 'F']:
        tag = f"[{grade}]"
        present = tag in memories_text
        if not present:
            # D- 可能在 FAILED 段
            all_grades_present = False
        check(f"Grade 标签 {tag} 出现在记忆文本", present,
              "PASS" if present else "未找到 — Agent 无法看到此等级")

    # ── 5d: TRADE QUALITY 统计验证 ──
    info("--- 5d: TRADE QUALITY 统计验证 ---")
    if "TRADE QUALITY" in memories_text:
        # 解析 grade summary (e.g., "A+:1 A:1 B:1 C:1 D:1 D-:1 F:1")
        import re
        quality_line = [l for l in memories_text.split('\n') if 'TRADE QUALITY' in l]
        if quality_line:
            line = quality_line[0]
            # Extract grade:count pairs
            grade_counts = re.findall(r'([A-F][+\-]?):(\d+)', line)
            found_grades = {g for g, c in grade_counts}
            check("TRADE QUALITY 含多个等级",
                  len(found_grades) >= 3,
                  f"找到: {dict(grade_counts)}")

            # Direction accuracy
            acc_match = re.search(r'Direction accuracy:\s*(\d+)%', line)
            if acc_match:
                accuracy = int(acc_match.group(1))
                # 7 trades: A+, A, B, C 盈利 (4), D, D-, F 亏损 (3) → ~57%
                check("Direction accuracy 合理 (约 57%)",
                      30 <= accuracy <= 80,
                      f"accuracy={accuracy}%")
            else:
                check("Direction accuracy 存在", False, f"行: {line[:100]}")
    else:
        warn("无 TRADE QUALITY 段，跳过统计验证")

    # ── 5e: _score_memory() 验证 ──
    info("--- 5e: _score_memory() 验证 ---")

    # 空 current_conditions → 返回 0
    try:
        test_mem = analyzer.decision_memory[0] if analyzer.decision_memory else {}
        if test_mem:
            score_empty = analyzer._score_memory(test_mem, {})
            check("_score_memory(empty conditions) → 0.0",
                  score_empty == 0.0,
                  f"score={score_empty}")

            score_none = analyzer._score_memory(test_mem, None)
            check("_score_memory(None conditions) → 0.0",
                  score_none == 0.0,
                  f"score={score_none}")

            # 匹配条件应返回较高分数
            matching_cond = {
                'direction': 'LONG', 'rsi': 55, 'macd': 'bullish',
                'bb': 60, 'sentiment': 'neutral', 'conf': 'HIGH',
            }
            score_match = analyzer._score_memory(test_mem, matching_cond)
            check("_score_memory(matching) > 0",
                  score_match > 0,
                  f"score={score_match:.2f}")
    except Exception as e:
        check("_score_memory() 测试", False, str(e))

    # ── 5f: 也验证真实 trading_memory.json (如果存在) ──
    info("--- 5f: 真实记忆文件验证 ---")
    real_memory = str(PROJECT_ROOT / "data" / "trading_memory.json")
    if os.path.exists(real_memory):
        real_analyzer = MultiAgentAnalyzer.__new__(MultiAgentAnalyzer)
        real_analyzer.logger = logging.getLogger("e2e_real_mem")
        real_analyzer.memory_file = real_memory
        real_analyzer.decision_memory = real_analyzer._load_memory()

        with open(real_memory) as f:
            file_data = json.load(f)
        check("真实文件: 内存 == 文件记录数",
              len(real_analyzer.decision_memory) == len(file_data),
              f"内存={len(real_analyzer.decision_memory)}, 文件={len(file_data)}")

        if real_analyzer.decision_memory:
            real_text = real_analyzer._get_past_memories()
            check("真实文件: _get_past_memories() 有内容",
                  len(real_text) > 0,
                  f"长度={len(real_text)}")
    else:
        info("真实记忆文件不存在，跳过")

    # 打印 Agent 实际接收的文本
    if memories_text:
        print(f"\n  {BOLD}--- 记忆文本 (Agent 实际接收, 合成池) ---{RESET}")
        for line in memories_text.split('\n'):
            print(f"  {line}")
        print(f"  {BOLD}--- 记忆文本结束 ---{RESET}")

    # 清理
    try:
        shutil.rmtree(tmp_dir)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════
#  PHASE 6: Web API 端点验证 (增强版)
# ══════════════════════════════════════════════════════════════
def _ensure_backend_venv():
    """Ensure web/backend/venv exists and has dependencies installed."""
    venv_dir = PROJECT_ROOT / "web" / "backend" / "venv"
    venv_python = venv_dir / "bin" / "python3"
    req_file = PROJECT_ROOT / "web" / "backend" / "requirements.txt"

    if not req_file.exists():
        check("web/backend/requirements.txt 存在", False)
        return False

    # Step 1: Check if venv exists
    if not venv_python.exists():
        info("后端 venv 不存在", f"{venv_python}")
        info("创建 venv", "python3 -m venv ...")
        try:
            r = run_cmd(["python3", "-m", "venv", str(venv_dir)], timeout=60)
            if r.returncode != 0:
                check("创建后端 venv", False, r.stderr[:300])
                return False
            check("创建后端 venv", True)
        except Exception as e:
            check("创建后端 venv", False, str(e))
            return False
    else:
        check("后端 venv 存在", True, str(venv_python))

    # Step 2: Check key packages
    test_import = run_cmd([
        str(venv_python), "-c",
        "import fastapi, uvicorn, pydantic_settings; print('OK')"
    ], timeout=15)

    if test_import.returncode != 0 or "OK" not in test_import.stdout:
        info("依赖缺失，安装中...", test_import.stderr[:200] if test_import.stderr else "")
        pip = str(venv_dir / "bin" / "pip")
        try:
            r = run_cmd([pip, "install", "-r", str(req_file)], timeout=300)
            if r.returncode != 0:
                check("安装后端依赖", False, r.stderr[-500:])
                return False
            check("安装后端依赖", True)
        except subprocess.TimeoutExpired:
            check("安装后端依赖", False, "超时 (300s)")
            return False
        except Exception as e:
            check("安装后端依赖", False, str(e))
            return False
    else:
        check("后端核心依赖就绪", True, "fastapi, uvicorn, pydantic_settings")

    # Step 3: Verify empyrical-reloaded (not old empyrical)
    emp_check = run_cmd([
        str(venv_python), "-c",
        "import empyrical; print(empyrical.__version__)"
    ], timeout=15)

    if emp_check.returncode != 0:
        warn(f"empyrical 导入失败: {emp_check.stderr[:150]}")
        info("修复: 卸载旧版 + 安装 empyrical-reloaded ...")
        pip = str(venv_dir / "bin" / "pip")
        run_cmd([pip, "uninstall", "-y", "empyrical", "pandas-datareader"], timeout=30)
        r = run_cmd([pip, "install", "empyrical-reloaded>=0.5.12,<1.0"], timeout=120)
        if r.returncode != 0:
            check("empyrical-reloaded 安装", False, r.stderr[:200])
            return False
        check("empyrical-reloaded 安装", True)
    else:
        check("empyrical 可用", True, f"版本: {emp_check.stdout.strip()}")

    # Step 4: Verify backend can import main module
    backend_dir = PROJECT_ROOT / "web" / "backend"
    import_check = run_cmd([
        str(venv_python), "-c",
        "import sys; sys.path.insert(0, '.'); from core.config import settings; print('OK:', settings.ALGVEX_PATH)"
    ], timeout=15, cwd=str(backend_dir))

    if import_check.returncode != 0:
        warn(f"后端 main 模块导入失败:")
        warn(import_check.stderr[:500])
        # Don't return False - might still work via systemd
    else:
        check("后端核心模块可导入", True, import_check.stdout.strip())

    return True


def _ensure_backend_env():
    """Ensure web/backend/.env exists (needed for SECRET_KEY validation)."""
    env_file = PROJECT_ROOT / "web" / "backend" / ".env"
    env_example = PROJECT_ROOT / "web" / "backend" / ".env.example"

    if env_file.exists():
        check("web/backend/.env 存在", True)
        return True

    # Create minimal .env from example or scratch
    warn("web/backend/.env 不存在，创建最小配置...")
    import secrets
    secret_key = secrets.token_hex(32)

    content = f"SECRET_KEY={secret_key}\nDEBUG=false\n"

    if env_example.exists():
        # Copy example and set SECRET_KEY
        with open(env_example) as f:
            content = f.read()
        content = content.replace(
            "your-secret-key-change-in-production", secret_key
        )

    with open(env_file, 'w') as f:
        f.write(content)

    check("web/backend/.env 已创建", True, f"SECRET_KEY={secret_key[:8]}...")
    return True


def _start_backend_direct():
    """Start uvicorn directly with explicit ALGVEX_PATH.

    Returns the Popen process if healthy, None on failure.
    """
    venv_python = PROJECT_ROOT / "web" / "backend" / "venv" / "bin" / "python3"
    backend_dir = PROJECT_ROOT / "web" / "backend"

    if not venv_python.exists():
        check("后端 venv python", False, str(venv_python))
        return None

    # Kill anything on port 8000
    run_cmd(["fuser", "-k", "8000/tcp"], timeout=5)
    time.sleep(1)

    # Build environment: inherit parent env + override key variables.
    # Previous minimal env (only PATH/HOME/ALGVEX_PATH) caused failures
    # because the backend needs other vars (LANG, USER, etc.) for proper
    # file I/O and logging. Override ALGVEX_PATH to ensure correctness.
    env = os.environ.copy()
    env["ALGVEX_PATH"] = str(PROJECT_ROOT)
    env["PYTHONUNBUFFERED"] = "1"
    info("ALGVEX_PATH (显式设置)", str(PROJECT_ROOT))

    proc = subprocess.Popen(
        [str(venv_python), "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", "8000"],
        cwd=str(backend_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for health check
    info("等待后端就绪", "最多 15 秒...")
    for attempt in range(15):
        time.sleep(1)

        # Check if process died
        if proc.poll() is not None:
            stdout = proc.stdout.read().decode(errors="replace")[-1000:]
            stderr = proc.stderr.read().decode(errors="replace")[-1000:]
            check("后端进程存活", False,
                  f"退出码={proc.returncode}\nstdout: {stdout}\nstderr: {stderr}")
            return None

        try:
            probe = run_cmd(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                 "http://127.0.0.1:8000/api/health"],
                timeout=3
            )
            if probe.stdout.strip() == "200":
                check("Web 后端就绪", True, f"第 {attempt + 1} 秒 (PID={proc.pid})")
                return proc
        except Exception:
            pass

    # Timed out — collect logs
    stdout = proc.stdout.read1(4096).decode(errors="replace") if hasattr(proc.stdout, 'read1') else ""
    stderr = proc.stderr.read1(4096).decode(errors="replace") if hasattr(proc.stderr, 'read1') else ""
    check("Web 后端就绪", False,
          f"15 秒内未响应\nstdout: {stdout[-500:]}\nstderr: {stderr[-500:]}")
    proc.terminate()
    return None


def _write_probe_script(probe_path, algvex_path):
    """Write the comprehensive probe script to a temp file.

    This tests the FULL API data flow in-process (no uvicorn, no curl, no port).
    Uses FastAPI TestClient to exercise the exact same code path as production.
    """
    probe_path.write_text(f'''#!/usr/bin/env python3
"""E2E Phase 6 probe: in-process API data flow test."""
import sys, json, os, traceback
sys.path.insert(0, '.')
os.environ['ALGVEX_PATH'] = {repr(str(algvex_path))}

# ── Part 1: Direct service test ──
try:
    from services.trade_evaluation_service import TradeEvaluationService
    from core.config import settings

    svc = TradeEvaluationService()
    print(f'SVC_PATH={{svc.memory_file}}')
    print(f'SVC_EXISTS={{svc.memory_file.exists()}}')

    memories = svc._load_memory()
    print(f'SVC_LOADED={{len(memories)}}')

    summary = svc.get_evaluation_summary(days=None)
    print(f'SVC_TOTAL={{summary["total_evaluated"]}}')
    print(f'SVC_GRADES={{json.dumps(summary.get("grade_distribution", {{}}))}}')
    print(f'SVC_SCORE={{summary["avg_grade_score"]}}')
    print(f'SVC_EXIT={{json.dumps(summary.get("exit_type_distribution", {{}}))}}')
    print(f'SVC_CONF={{json.dumps(summary.get("confidence_accuracy", {{}}))}}')

    recent = svc.get_recent_trades(limit=5, include_details=False)
    print(f'SVC_RECENT={{len(recent)}}')
    if recent:
        print(f'SVC_RECENT_KEYS={{json.dumps(sorted(recent[0].keys()))}}')

    recent_admin = svc.get_recent_trades(limit=3, include_details=True)
    print(f'SVC_ADMIN={{len(recent_admin)}}')
    if recent_admin:
        print(f'SVC_ADMIN_KEYS={{json.dumps(sorted(recent_admin[0].keys()))}}')

except Exception as e:
    print(f'SVC_ERROR={{e}}')
    traceback.print_exc()

# ── Part 2: FastAPI TestClient (in-process HTTP) ──
try:
    from starlette.testclient import TestClient
    from main import app

    client = TestClient(app)

    # Health
    r = client.get('/api/health')
    print(f'TC_HEALTH_STATUS={{r.status_code}}')

    # Summary (days=0 = all time)
    r = client.get('/api/public/trade-evaluation/summary', params={{"days": 0}})
    print(f'TC_SUMMARY_STATUS={{r.status_code}}')
    d = r.json()
    print(f'TC_SUMMARY_TOTAL={{d.get("total_evaluated", -1)}}')
    print(f'TC_SUMMARY_GRADES={{json.dumps(d.get("grade_distribution", {{}}))}}')
    print(f'TC_SUMMARY_SCORE={{d.get("avg_grade_score", -1)}}')
    print(f'TC_SUMMARY_EXIT={{json.dumps(d.get("exit_type_distribution", {{}}))}}')
    print(f'TC_SUMMARY_CONF={{json.dumps(d.get("confidence_accuracy", {{}}))}}')

    # Recent (public)
    r = client.get('/api/public/trade-evaluation/recent', params={{"limit": 5}})
    print(f'TC_RECENT_STATUS={{r.status_code}}')
    recent_data = r.json()
    print(f'TC_RECENT_COUNT={{len(recent_data)}}')
    if recent_data:
        print(f'TC_RECENT_KEYS={{json.dumps(sorted(recent_data[0].keys()))}}')

    # Admin (expect 401/403 without auth — that's OK)
    r = client.get('/api/admin/trade-evaluation/full', params={{"limit": 3}})
    print(f'TC_ADMIN_STATUS={{r.status_code}}')
    if r.status_code == 200:
        admin_data = r.json()
        if isinstance(admin_data, list) and admin_data:
            print(f'TC_ADMIN_KEYS={{json.dumps(sorted(admin_data[0].keys()))}}')
    else:
        print(f'TC_ADMIN_DETAIL={{r.json().get("detail", "N/A")}}')

except ImportError as e:
    print(f'TC_SKIP={{e}}')
except Exception as e:
    print(f'TC_ERROR={{e}}')
    traceback.print_exc()
''')


def phase6_web_api():
    section("PHASE 6/6: Web API 全链路验证 (TestClient 进程内测试)")

    # Step 1: Ensure .env exists
    if not _ensure_backend_env():
        warn("后端 .env 配置失败，跳过 API 测试")
        return

    # Step 2: Ensure venv + dependencies
    if not _ensure_backend_venv():
        warn("后端环境未就绪，跳过 API 测试")
        return

    # Step 2b: Diagnose web/backend/.env
    info("--- .env 诊断 ---")
    backend_env_file = PROJECT_ROOT / "web" / "backend" / ".env"
    if backend_env_file.exists():
        with open(backend_env_file) as f:
            env_lines = f.readlines()
        for line in env_lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            key = line.split('=', 1)[0] if '=' in line else line
            if key in ('ALGVEX_PATH', 'DEBUG', 'DATABASE_URL'):
                info(f".env: {line}")
            elif 'SECRET' in key or 'KEY' in key or 'TOKEN' in key:
                info(f".env: {key}=<REDACTED>")
            else:
                info(f".env: {line}")
    else:
        info(".env 不存在 (将使用 auto-detect)")

    # Step 2c: Ensure httpx (needed for TestClient)
    venv_python = PROJECT_ROOT / "web" / "backend" / "venv" / "bin" / "python3"
    backend_dir = PROJECT_ROOT / "web" / "backend"
    pip_exe = PROJECT_ROOT / "web" / "backend" / "venv" / "bin" / "pip"

    httpx_check = run_cmd(
        [str(venv_python), "-c", "import httpx; print('OK')"],
        timeout=10, cwd=str(backend_dir)
    )
    if httpx_check.returncode != 0 or "OK" not in (httpx_check.stdout or ""):
        info("安装 httpx (TestClient 依赖)...")
        run_cmd([str(pip_exe), "install", "httpx"], timeout=60)

    # ── Step 3: 写入探测脚本并执行 (核心: 进程内完整 API 测试) ──
    # Uses a temp .py file — avoids all -c escaping / truncation issues.
    # Tests: Service direct + FastAPI TestClient (same code path as production)
    info("--- 写入并执行探测脚本 ---")
    probe_script_path = backend_dir / "_e2e_probe.py"
    _write_probe_script(probe_script_path, PROJECT_ROOT)

    env_for_probe = os.environ.copy()
    env_for_probe["ALGVEX_PATH"] = str(PROJECT_ROOT)
    env_for_probe["PYTHONUNBUFFERED"] = "1"

    probe_result = run_cmd(
        [str(venv_python), str(probe_script_path)],
        timeout=30, cwd=str(backend_dir), env=env_for_probe
    )

    # Always clean up temp script
    try:
        probe_script_path.unlink()
    except OSError:
        pass

    if probe_result.returncode != 0:
        check("探测脚本执行", False,
              f"exit={probe_result.returncode}\n"
              f"stdout: {(probe_result.stdout or '')[:500]}\n"
              f"stderr: {(probe_result.stderr or '')[:500]}")
        return

    # ── Step 4: 解析结果 ──
    output = probe_result.stdout or ""
    stderr_output = probe_result.stderr or ""

    # Print all output lines for visibility
    for line in output.strip().split('\n'):
        info(f"探测: {line}")
    if stderr_output.strip():
        for line in stderr_output.strip().split('\n')[-10:]:
            info(f"探测 stderr: {line}")

    # Parse structured output
    def _get(prefix):
        for line in output.split('\n'):
            if line.startswith(prefix):
                return line[len(prefix):]
        return None

    # ── Step 5: 验证直接服务结果 ──
    info("--- 直接服务 (Part 1) ---")

    svc_error = _get("SVC_ERROR=")
    if svc_error:
        check("Service 导入/执行", False, f"错误: {svc_error}")
    else:
        svc_path = _get("SVC_PATH=") or "?"
        svc_exists = _get("SVC_EXISTS=") == "True"
        svc_loaded = int(_get("SVC_LOADED=") or "0")
        svc_total = int(_get("SVC_TOTAL=") or "0")
        svc_score = float(_get("SVC_SCORE=") or "0")
        svc_grades = json.loads(_get("SVC_GRADES=") or "{}")
        svc_exit = json.loads(_get("SVC_EXIT=") or "{}")
        svc_conf = json.loads(_get("SVC_CONF=") or "{}")
        svc_recent = int(_get("SVC_RECENT=") or "0")
        svc_recent_keys = json.loads(_get("SVC_RECENT_KEYS=") or "[]")
        svc_admin = int(_get("SVC_ADMIN=") or "0")
        svc_admin_keys = json.loads(_get("SVC_ADMIN_KEYS=") or "[]")

        check("Service: memory_file 存在", svc_exists, svc_path)
        check("Service: _load_memory() 有数据", svc_loaded > 0,
              f"loaded={svc_loaded}")
        check("Service: total_evaluated > 0", svc_total > 0,
              f"total={svc_total}, grades={svc_grades}")
        check("Service: avg_grade_score > 0", svc_score > 0,
              f"score={svc_score}")
        check("Service: grade_distribution 非空", len(svc_grades) > 0,
              f"{svc_grades}")
        check("Service: exit_type_distribution 非空", len(svc_exit) > 0,
              f"{svc_exit}")

        # confidence_accuracy: no empty keys
        bad_conf_keys = [k for k in svc_conf if not k or k.strip() == '']
        check("Service: confidence_accuracy 无空 key",
              len(bad_conf_keys) == 0,
              f"keys={list(svc_conf.keys())}")

        check("Service: get_recent_trades (public) 有数据",
              svc_recent > 0, f"{svc_recent} 条")

        # Public view sanitization
        if svc_recent_keys:
            public_expected = {'grade', 'planned_rr', 'actual_rr', 'execution_quality',
                               'exit_type', 'confidence', 'hold_duration_min',
                               'direction_correct', 'timestamp'}
            sensitive_fields = {'pnl', 'conditions', 'lesson', 'entry_price', 'exit_price'}
            has_all = public_expected.issubset(set(svc_recent_keys))
            check("Service: public 含预期字段", has_all,
                  f"实际: {svc_recent_keys}")
            leaked = sensitive_fields.intersection(set(svc_recent_keys))
            check("Service: public 不泄露敏感字段",
                  len(leaked) == 0,
                  f"泄露: {leaked}" if leaked else "")

        # Admin view
        if svc_admin_keys:
            check("Service: admin 含 pnl", 'pnl' in svc_admin_keys,
                  f"keys={svc_admin_keys}")
            check("Service: admin 含 conditions",
                  'conditions' in svc_admin_keys)

    # ── Step 6: 验证 TestClient API 结果 ──
    info("--- TestClient API (Part 2) ---")

    tc_skip = _get("TC_SKIP=")
    tc_error = _get("TC_ERROR=")

    if tc_skip:
        warn(f"TestClient 跳过: {tc_skip}")
        info("可能需要: pip install httpx")
    elif tc_error:
        check("TestClient API 执行", False, f"错误: {tc_error}")
    else:
        tc_health = int(_get("TC_HEALTH_STATUS=") or "0")
        tc_summary_status = int(_get("TC_SUMMARY_STATUS=") or "0")
        tc_summary_total = int(_get("TC_SUMMARY_TOTAL=") or "-1")
        tc_summary_score = float(_get("TC_SUMMARY_SCORE=") or "-1")
        tc_summary_grades = json.loads(_get("TC_SUMMARY_GRADES=") or "{}")
        tc_summary_exit = json.loads(_get("TC_SUMMARY_EXIT=") or "{}")
        tc_summary_conf = json.loads(_get("TC_SUMMARY_CONF=") or "{}")
        tc_recent_status = int(_get("TC_RECENT_STATUS=") or "0")
        tc_recent_count = int(_get("TC_RECENT_COUNT=") or "0")
        tc_recent_keys = json.loads(_get("TC_RECENT_KEYS=") or "[]")
        tc_admin_status = int(_get("TC_ADMIN_STATUS=") or "0")
        tc_admin_keys = json.loads(_get("TC_ADMIN_KEYS=") or "[]")

        check("API: /api/health → 200", tc_health == 200,
              f"status={tc_health}")
        check("API: /api/public/.../summary → 200", tc_summary_status == 200,
              f"status={tc_summary_status}")
        check("API: summary total_evaluated > 0", tc_summary_total > 0,
              f"total={tc_summary_total}, grades={tc_summary_grades}")
        check("API: summary avg_grade_score > 0", tc_summary_score > 0,
              f"score={tc_summary_score}")
        check("API: summary grade_distribution 非空",
              len(tc_summary_grades) > 0, f"{tc_summary_grades}")
        check("API: summary exit_type_distribution 非空",
              len(tc_summary_exit) > 0, f"{tc_summary_exit}")

        # confidence_accuracy: no empty keys
        bad_tc_conf = [k for k in tc_summary_conf if not k or k.strip() == '']
        check("API: confidence_accuracy 无空 key",
              len(bad_tc_conf) == 0,
              f"keys={list(tc_summary_conf.keys())}")

        check("API: /api/public/.../recent → 200", tc_recent_status == 200,
              f"status={tc_recent_status}")
        check("API: recent 有数据", tc_recent_count > 0,
              f"{tc_recent_count} 条")

        # Public view sanitization via TestClient
        if tc_recent_keys:
            public_expected = {'grade', 'planned_rr', 'actual_rr', 'execution_quality',
                               'exit_type', 'confidence', 'hold_duration_min',
                               'direction_correct', 'timestamp'}
            sensitive_fields = {'pnl', 'conditions', 'lesson', 'entry_price', 'exit_price'}
            has_all = public_expected.issubset(set(tc_recent_keys))
            check("API: public 含预期字段", has_all,
                  f"实际: {tc_recent_keys}")
            leaked = sensitive_fields.intersection(set(tc_recent_keys))
            check("API: public 不泄露敏感字段",
                  len(leaked) == 0,
                  f"泄露: {leaked}" if leaked else "")

        # Admin endpoint (401 is expected without auth)
        if tc_admin_status == 200 and tc_admin_keys:
            check("API: admin 含 pnl", 'pnl' in tc_admin_keys,
                  f"keys={tc_admin_keys}")
            check("API: admin 含 conditions", 'conditions' in tc_admin_keys)
        elif tc_admin_status in (401, 403):
            admin_detail = _get("TC_ADMIN_DETAIL=") or "N/A"
            info("API: admin 需要认证 (预期行为)", admin_detail)
        else:
            info("API: admin 状态", f"status={tc_admin_status}")


# ══════════════════════════════════════════════════════════════
#  Cleanup: 清理 E2E 测试记录
# ══════════════════════════════════════════════════════════════
def cleanup_e2e_records():
    """Remove E2E_TEST entries from trading_memory.json."""
    section("CLEANUP: 清理 E2E 测试记录")

    memory_file = PROJECT_ROOT / "data" / "trading_memory.json"
    if not memory_file.exists():
        info("trading_memory.json 不存在，无需清理")
        return

    with open(memory_file) as f:
        data = json.load(f)

    original_count = len(data)
    cleaned = [m for m in data if "E2E_TEST" not in m.get("conditions", "")]
    removed = original_count - len(cleaned)

    if removed > 0:
        with open(memory_file, 'w') as f:
            json.dump(cleaned, f, indent=2)
        check("清理 E2E 测试记录", True, f"删除 {removed} 条, 保留 {len(cleaned)} 条")
    else:
        info("无 E2E 测试记录需要清理")


# ══════════════════════════════════════════════════════════════
#  主函数
# ══════════════════════════════════════════════════════════════
def main():
    print(f"\n{BOLD}{'#'*60}{RESET}")
    print(f"{BOLD}  端到端交易评估全链路实测 (E2E Pipeline Test){RESET}")
    print(f"{BOLD}  生成 → 存储 → 加载 → 格式化 → Agent 消费 → Web 展示{RESET}")
    print(f"{BOLD}  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{RESET}")
    print(f"{BOLD}{'#'*60}{RESET}")

    # 解析参数
    skip_trade = '--skip-trade' in sys.argv
    skip_web = '--skip-web' in sys.argv
    do_cleanup = '--cleanup' in sys.argv
    auto_confirm = '--auto' in sys.argv or os.getenv('AUTO_CONFIRM', '').lower() == 'true'

    # 安全确认
    if not skip_trade:
        print(f"\n{YELLOW}{BOLD}注意:{RESET}")
        print(f"  1. 此脚本将在 Binance Futures 开一个 {BOLD}0.002 BTC{RESET} 的 LONG 并立即平仓")
        print(f"  2. 预计损失: 仅点差+手续费 (约 $0.05-0.20)")
        print(f"  3. 请确保 Bot 已停止: sudo systemctl stop nautilus-trader")
        print(f"  4. 确保 ~/.env.algvex 中有正确的 API 密钥")
    else:
        print(f"\n{YELLOW}{BOLD}--skip-trade 模式: 跳过 Binance 交易，使用已有数据{RESET}")

    if skip_web:
        print(f"  {YELLOW}--skip-web 模式: 跳过 Web API 测试{RESET}")
    if do_cleanup:
        print(f"  {YELLOW}--cleanup 模式: 测试后清理 E2E 记录{RESET}")

    print()

    if auto_confirm:
        info("自动确认模式 (--auto / AUTO_CONFIRM=true)")
    else:
        confirm = input(f"{BOLD}输入 'yes' 开始测试: {RESET}").strip().lower()
        if confirm != 'yes':
            print("已取消。")
            sys.exit(0)

    # 加载环境变量
    load_env()

    # ══ Phase 0: 环境预检 ══
    prechecks_ok = phase0_prechecks()
    if not prechecks_ok and not skip_trade:
        warn("预检发现问题，交易阶段可能失败")

    # ══ Phase 1: Binance 实际交易 ══
    trade_data = None
    evaluation = None

    if skip_trade:
        section("PHASE 1/6: Binance 实际交易 (跳过)")
        info("--skip-trade 模式，使用已有 trading_memory.json")
        memory_file = PROJECT_ROOT / "data" / "trading_memory.json"
        if memory_file.exists():
            with open(memory_file) as f:
                data = json.load(f)
            if data:
                latest = data[-1]
                info("使用最新记录", latest.get("conditions", "")[:100])
                evaluation = latest.get("evaluation")
            else:
                warn("trading_memory.json 为空")
        else:
            warn("trading_memory.json 不存在, Phase 2 也将跳过")
    else:
        trade_data = phase1_binance_trade()

    # ══ Phase 2: evaluate + record (真实交易) ══
    if skip_trade:
        section("PHASE 2/6: evaluate + record (跳过)")
        info("--skip-trade 模式，跳过")
    else:
        evaluation = phase2_evaluate_and_record(trade_data)

    # ══ Phase 3: 评级生成全覆盖 (合成数据，始终运行) ══
    grade_results = phase3_grade_generation_coverage()

    # ══ Phase 4: 存储→加载→格式化一致性 ══
    if grade_results:
        phase4_storage_load_consistency(grade_results)
    else:
        section("PHASE 4/6: 存储→加载→格式化一致性 (跳过)")
        warn("Phase 3 未产生 grade_results, 跳过")

    # ══ Phase 5: Agent 消费验证 ══
    if grade_results:
        phase5_agent_consumption(grade_results)
    else:
        section("PHASE 5/6: Agent 消费验证 (跳过)")
        warn("Phase 3 未产生 grade_results, 跳过")

    # ══ Phase 6: Web API ══
    if skip_web:
        section("PHASE 6/6: Web API 端点验证 (跳过)")
        info("--skip-web 模式，跳过")
    else:
        phase6_web_api()

    # Cleanup
    if do_cleanup:
        cleanup_e2e_records()

    # ─── 汇总 ───
    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}  E2E 测试汇总 (6 Phase){RESET}")
    print(f"{BOLD}{'='*60}{RESET}")

    passed = sum(1 for r in results if r["passed"] is True)
    failed = sum(1 for r in results if r["passed"] is False)
    total = passed + failed

    print(f"  {GREEN}通过: {passed}{RESET}")
    print(f"  {RED}失败: {failed}{RESET}")
    print(f"  总计: {total}")

    if failed > 0:
        print(f"\n  {RED}{BOLD}失败项:{RESET}")
        for r in results:
            if r["passed"] is False:
                print(f"    {RED}\u2717 {r['name']}{RESET}")

    if trade_data:
        print(f"\n  {BOLD}交易摘要:{RESET}")
        print(f"    入场: ${trade_data['entry_price']:,.2f}")
        print(f"    出场: ${trade_data['exit_price']:,.2f}")
        print(f"    P&L:  {trade_data['pnl_pct']:+.4f}% (${trade_data['pnl_usdt']:+.4f})")
        if evaluation:
            print(f"    评级: {evaluation.get('grade', '?')}")
            print(f"    R/R:  {evaluation.get('actual_rr', 0)}")

    # 管线覆盖摘要
    pipeline_stages = {
        "生成 (Generation)": bool(grade_results),
        "存储 (Storage)": bool(grade_results),
        "加载 (Loading)": bool(grade_results),
        "格式化 (Formatting)": bool(grade_results),
        "Agent 消费": bool(grade_results),
        "Service 数据流": not skip_web,
        "API 路由 (TestClient)": not skip_web,
    }
    print(f"\n  {BOLD}管线覆盖:{RESET}")
    for stage, covered in pipeline_stages.items():
        icon = f"{GREEN}\u2713{RESET}" if covered else f"{YELLOW}\u2014{RESET}"
        print(f"    {icon} {stage}")

    if failed == 0:
        print(f"\n  {GREEN}{BOLD}\u2705 全链路端到端测试通过!{RESET}")
        modes = []
        if skip_trade:
            modes.append("skip-trade")
        if skip_web:
            modes.append("skip-web")
        if modes:
            print(f"  {YELLOW}(跳过: {', '.join(modes)}){RESET}")
        else:
            print(f"  {GREEN}生成 \u2192 存储 \u2192 加载 \u2192 格式化 \u2192 Agent \u2192 Web{RESET}\n")
    else:
        print(f"\n  {RED}{BOLD}\u274c 发现 {failed} 个问题，请检查上方失败项{RESET}\n")

    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
