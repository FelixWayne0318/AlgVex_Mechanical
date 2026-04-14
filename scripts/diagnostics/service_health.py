# scripts/diagnostics/service_health.py
"""
服务健康检查模块

诊断项:
- [A] 服务运行状态检查 (systemd, memory, logs)
- [B] API 健康检查 (响应时间, 错误率)
- [C] 交易暂停状态检查
- [D] 历史信号追踪
"""

import os
import time
import subprocess
from typing import Dict, Any, Optional
from datetime import datetime, timedelta

from .base import DiagnosticStep


class ServiceHealthCheck(DiagnosticStep):
    """
    [新增 A] 服务运行状态检查

    检查项:
    - systemd 服务状态
    - 进程内存使用
    - 最近日志错误计数
    - 上次重启时间
    """

    name = "服务运行状态检查"
    step_number = "0"  # 放在最前面

    def run(self) -> bool:
        print()
        print("  📊 Systemd 服务状态:")

        # Check if running on server (has systemctl)
        status = "unknown"
        try:
            # Get service status
            result = subprocess.run(
                ["systemctl", "is-active", "nautilus-trader"],
                capture_output=True,
                text=True,
                timeout=5
            )
            status = result.stdout.strip()

            if status == "active":
                print("     ✅ nautilus-trader: 运行中")

                # Get uptime
                result = subprocess.run(
                    ["systemctl", "show", "nautilus-trader", "--property=ActiveEnterTimestamp"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                timestamp_line = result.stdout.strip()
                if "=" in timestamp_line:
                    timestamp_str = timestamp_line.split("=")[1]
                    print(f"     启动时间: {timestamp_str}")

            elif status == "inactive":
                print("     ⚠️ nautilus-trader: 未运行")
            else:
                print(f"     ❓ nautilus-trader: {status}")

        except FileNotFoundError:
            print("     ℹ️ systemctl 不可用 (可能在开发环境)")
        except subprocess.TimeoutExpired:
            print("     ⚠️ systemctl 超时")
        except Exception as e:
            print(f"     ⚠️ 检查失败: {e}")

        # Check recent log errors
        print()
        print("  📋 最近日志错误统计:")
        try:
            # Count errors in last 10 minutes
            result = subprocess.run(
                ["journalctl", "-u", "nautilus-trader", "--since", "10 min ago", "--no-pager"],
                capture_output=True,
                text=True,
                timeout=10
            )
            log_content = result.stdout
            log_lines = log_content.split('\n')

            error_count = log_content.lower().count("error")
            warning_count = log_content.lower().count("warning")
            panic_count = log_content.lower().count("panic")

            if panic_count > 0:
                print(f"     🔴 PANIC: {panic_count} (严重!)")
                # v6.3: 提取并显示 PANIC 具体内容
                print()
                print("     📋 PANIC 详细信息:")
                for i, line in enumerate(log_lines):
                    if "panic" in line.lower():
                        # 打印 panic 行及后续 3 行 (通常包含 stack trace)
                        print(f"     │ {line.strip()}")
                        for j in range(1, 4):
                            if i + j < len(log_lines) and log_lines[i + j].strip():
                                print(f"     │ {log_lines[i + j].strip()}")
                        print(f"     └─")
                print()
                # v6.3: PANIC 原因分析 — 根据已知模式提供诊断
                panic_text = log_content.lower()
                if "interpreter" in panic_text and "not initialized" in panic_text:
                    print("     💡 原因: PyO3 interpreter lifecycle PANIC")
                    print("        tokio-runtime-worker 在 Python 关闭后尝试调用 Python APIs")
                    print("        → v6.5 已修复: os._exit() 跳过 CPython finalization")
                    print("        → 如仍出现, 检查 main_live.py 是否已更新到 v6.5")
                    print("        → 命令: sudo journalctl -u nautilus-trader -n 50 --no-pager")
                elif "non-ascii" in panic_text or "unicode" in panic_text or "decode" in panic_text:
                    print("     💡 可能原因: 非 ASCII 符号 (如 币安人生USDT)")
                    print("        → 检查 patches/binance_positions.py 是否正确应用")
                elif "enum" in panic_text or "unknown variant" in panic_text or "validation" in panic_text:
                    print("     💡 可能原因: Binance 新增的未知枚举值")
                    print("        → 检查 patches/binance_enums.py 是否在 NT 导入前加载")
                elif "thread" in panic_text and ("send" in panic_text or "sync" in panic_text):
                    print("     💡 可能原因: Rust 指标跨线程访问")
                    print("        → 检查 Telegram 线程是否访问了 indicator_manager")
                elif "instrument" in panic_text or "cache" in panic_text:
                    print("     💡 可能原因: 合约加载失败 (Instrument cache)")
                    print("        → 检查 Binance API 连接和合约名称")
                elif "bar" in panic_text and ("type" in panic_text or "parse" in panic_text):
                    print("     💡 可能原因: BarType 解析失败")
                    print("        → 检查 configs/base.yaml 中的 bar_spec 格式")
                else:
                    print("     💡 未知 PANIC 类型 — 请检查上方日志细节")
                print()
            if error_count > 0:
                print(f"     🔴 ERROR: {error_count}")
                # v6.3: 显示最近 3 条 ERROR 行
                error_lines = [l for l in log_lines if "error" in l.lower()]
                for el in error_lines[-3:]:
                    print(f"     │ {el.strip()[:120]}")
            else:
                print(f"     ✅ ERROR: 0")
            if warning_count > 0:
                print(f"     🟡 WARNING: {warning_count}")
            else:
                print(f"     ✅ WARNING: 0")

        except FileNotFoundError:
            print("     ℹ️ journalctl 不可用")
        except subprocess.TimeoutExpired:
            print("     ⚠️ journalctl 超时")
        except Exception as e:
            print(f"     ⚠️ 日志检查失败: {e}")

        # v6.3: 如果服务 failed，查看更长时间范围内的 PANIC/崩溃原因
        if status in ("failed", "inactive"):
            print()
            print("  🔍 崩溃原因分析 (最近 1 小时):")
            try:
                result = subprocess.run(
                    ["journalctl", "-u", "nautilus-trader", "--since", "1 hour ago",
                     "--no-pager", "-n", "200"],
                    capture_output=True, text=True, timeout=10
                )
                crash_log = result.stdout
                if crash_log.strip():
                    crash_lines = crash_log.split('\n')
                    # 找到最后一次 panic/fatal/crash 附近的上下文
                    critical_indices = []
                    for idx, line in enumerate(crash_lines):
                        ll = line.lower()
                        if any(k in ll for k in ["panic", "fatal", "segfault",
                                                   "killed", "oom", "core dump",
                                                   "thread.*crash", "aborted"]):
                            critical_indices.append(idx)

                    if critical_indices:
                        for ci in critical_indices[-2:]:  # 最近 2 次崩溃
                            start = max(0, ci - 2)
                            end = min(len(crash_lines), ci + 5)
                            for li in range(start, end):
                                marker = ">>>" if li == ci else "   "
                                print(f"     {marker} {crash_lines[li].strip()[:140]}")
                            print(f"     {'─' * 50}")
                    else:
                        # 没有明确的 panic, 显示最后几行 (往往包含退出原因)
                        print("     (未找到明确 PANIC, 显示服务最后输出):")
                        for line in crash_lines[-8:]:
                            if line.strip():
                                print(f"     │ {line.strip()[:140]}")

                    # v6.3: 检查是否因 OOM 被系统杀死
                    if "killed" in crash_log.lower() or "oom" in crash_log.lower():
                        print()
                        print("     ⚠️ 可能被系统 OOM Killer 终止!")
                        print("        → 检查: sudo dmesg | grep -i 'out of memory'")
                        print("        → 建议: 增加服务器内存或减少其他进程")
                else:
                    print("     ℹ️ 该时间段内无日志")
            except Exception as e:
                print(f"     ⚠️ 崩溃分析失败: {e}")

        # Check memory usage (if possible)
        print()
        print("  💾 进程资源使用:")
        try:
            result = subprocess.run(
                ["ps", "aux"],
                capture_output=True,
                text=True,
                timeout=5
            )
            for line in result.stdout.split('\n'):
                if 'main_live.py' in line or 'nautilus' in line.lower():
                    parts = line.split()
                    if len(parts) >= 6:
                        cpu = parts[2]
                        mem = parts[3]
                        print(f"     CPU: {cpu}%, MEM: {mem}%")
                        break
            else:
                print("     ℹ️ 未找到运行中的进程")
        except Exception as e:
            print(f"     ⚠️ 资源检查失败: {e}")

        return True


class APIHealthCheck(DiagnosticStep):
    """
    [新增 B] API 健康检查

    检查项:
    - Binance API 响应时间
    - DeepSeek API 响应时间
    - Coinalyze API 响应时间
    """

    name = "API 健康检查 (响应时间)"
    step_number = "0.5"

    def run(self) -> bool:
        import requests

        print()
        print("  🌐 API 响应时间测试:")

        apis = [
            ("Binance Futures", "https://fapi.binance.com/fapi/v1/ping", 2),
            ("Binance Spot", "https://api.binance.com/api/v3/ping", 2),
        ]

        for name, url, timeout in apis:
            try:
                start = time.time()
                resp = requests.get(url, timeout=timeout)
                elapsed = (time.time() - start) * 1000

                if resp.status_code == 200:
                    status = "✅" if elapsed < 500 else "🟡" if elapsed < 1000 else "🔴"
                    print(f"     {status} {name}: {elapsed:.0f}ms")
                else:
                    print(f"     🔴 {name}: HTTP {resp.status_code}")
            except requests.Timeout:
                print(f"     🔴 {name}: 超时 (>{timeout}s)")
            except Exception as e:
                print(f"     🔴 {name}: {str(e)[:50]}")

        # Test DeepSeek API (just connectivity, not actual call)
        try:
            start = time.time()
            resp = requests.get(
                "https://api.deepseek.com",
                timeout=3,
                headers={"User-Agent": "AlgVex-diagnostic"}
            )
            elapsed = (time.time() - start) * 1000
            # Any response means network is reachable
            print(f"     ✅ DeepSeek API: {elapsed:.0f}ms (连通性)")
        except requests.Timeout:
            print(f"     🔴 DeepSeek API: 超时")
        except Exception as e:
            print(f"     🟡 DeepSeek API: {str(e)[:40]}")

        # Test Coinalyze (if API key exists)
        # v2.4.9: Fix - Coinalyze has no /ping endpoint, use /open-interest instead
        coinalyze_key = os.getenv('COINALYZE_API_KEY')
        if coinalyze_key:
            try:
                start = time.time()
                resp = requests.get(
                    "https://api.coinalyze.net/v1/open-interest",
                    timeout=3,
                    params={"symbols": "BTCUSDT_PERP.A"},
                    headers={"api_key": coinalyze_key}
                )
                elapsed = (time.time() - start) * 1000
                if resp.status_code == 200:
                    print(f"     ✅ Coinalyze API: {elapsed:.0f}ms")
                elif resp.status_code == 401:
                    print(f"     🔴 Coinalyze API: Invalid API key (401)")
                elif resp.status_code == 429:
                    print(f"     🟡 Coinalyze API: Rate limited (429)")
                else:
                    print(f"     🟡 Coinalyze API: HTTP {resp.status_code}")
            except Exception as e:
                print(f"     🟡 Coinalyze API: {str(e)[:40]}")
        else:
            print(f"     ℹ️ Coinalyze API: 未配置 key")

        return True


class TradingStateCheck(DiagnosticStep):
    """
    [新增 C] 交易暂停状态检查

    检查项:
    - is_trading_paused 状态
    - _timer_lock 状态 (如果可检测)
    """

    name = "交易状态检查"
    step_number = "9.5"  # 在持仓检查后

    def run(self) -> bool:
        print()
        print("  🔒 交易控制状态:")

        # Production uses in-memory self.is_trading_paused (set via Telegram /pause).
        # No file-based pause mechanism exists — display relevant config instead.
        print("     ℹ️ 暂停状态: 由运行中策略实例 is_trading_paused 控制 (Telegram /pause)")
        print("        诊断脚本无法读取运行时内存状态")

        # Check min_confidence setting (actually affects trading decisions)
        min_conf = getattr(self.ctx.strategy_config, 'min_confidence_to_trade', 'MEDIUM')
        print(f"     最低信心要求: {min_conf}")

        # Check cooldown/pyramiding config (affects trade frequency)
        cooldown_enabled = getattr(self.ctx.strategy_config, 'cooldown_enabled', False)
        pyramiding_enabled = getattr(self.ctx.strategy_config, 'pyramiding_enabled', False)
        print(f"     冷静期: {'✅ 启用' if cooldown_enabled else '❌ 未启用'}")
        print(f"     金字塔加仓: {'✅ 启用' if pyramiding_enabled else '❌ 未启用'}")

        return True


class SignalHistoryCheck(DiagnosticStep):
    """
    [新增 D] 历史信号追踪

    检查项:
    - 最近信号记录
    - 信号执行结果
    """

    name = "历史信号追踪"
    step_number = "15.5"  # 在诊断总结后

    def run(self) -> bool:
        print()
        print("  📜 最近信号记录:")

        # Check signal history file
        signal_history_file = "/home/linuxuser/nautilus_AlgVex/logs/signal_history.json"

        if os.path.exists(signal_history_file):
            try:
                import json
                with open(signal_history_file, 'r') as f:
                    history = json.load(f)

                if isinstance(history, list) and len(history) > 0:
                    recent = history[-5:]  # Last 5 signals
                    print(f"     总记录: {len(history)} 条")
                    print()
                    for i, sig in enumerate(reversed(recent), 1):
                        ts = sig.get('timestamp', 'N/A')
                        signal = sig.get('signal', 'N/A')
                        conf = sig.get('confidence', 'N/A')
                        executed = sig.get('executed', 'N/A')
                        reason = sig.get('reason', '')

                        status = "✅" if executed else "❌"
                        print(f"     [{i}] {ts[:19] if len(ts) > 19 else ts}")
                        print(f"         Signal: {signal} ({conf}) {status}")
                        if reason and not executed:
                            print(f"         原因: {reason[:50]}")
                else:
                    print("     ℹ️ 无信号记录")
            except Exception as e:
                print(f"     ⚠️ 读取失败: {e}")
        else:
            print("     ℹ️ 信号历史文件不存在")
            print("     → 这是正常的，实盘运行后会自动创建")

        # Also check position snapshots
        snapshots_dir = "/home/linuxuser/nautilus_AlgVex/data/position_snapshots"
        if os.path.exists(snapshots_dir):
            try:
                files = sorted(os.listdir(snapshots_dir))[-5:]
                if files:
                    print()
                    print("  📊 最近持仓快照:")
                    for f in files:
                        print(f"     - {f}")
            except Exception as e:
                print(f"     ⚠️ 快照目录读取失败: {e}")

        return True
