"""
Configuration Checker Module

Validates critical configurations that could prevent trading.
"""

import os
import re
import sys
from pathlib import Path
from typing import List, Tuple

import yaml

from .base import DiagnosticContext, DiagnosticStep, mask_sensitive


class CriticalConfigChecker(DiagnosticStep):
    """
    Check critical configurations that could prevent orders from executing.

    Validates:
    - main_live.py: load_all and reconciliation settings
    - ai_strategy.py: SL/TP field names
    - trading_logic.py: get_min_sl_distance_pct(), get_min_rr_ratio()
    - patches: Binance enum patches
    """

    name = "关键配置检查"

    def run(self) -> bool:
        print("-" * 70)
        issues, warnings = self._check_critical_config()

        if issues:
            print()
            print("  🚨 发现严重问题 (可能导致不能下单):")
            print()
            for issue in issues:
                for line in issue.split('\n'):
                    print(f"  {line}")
                print()
            self.ctx.errors.extend([i.split('\n')[0] for i in issues])

        if warnings:
            print("  ⚠️ 警告:")
            for warning in warnings:
                for line in warning.split('\n'):
                    print(f"     {line}")
            print()
            self.ctx.warnings.extend([w.split('\n')[0] for w in warnings])

        if not issues and not warnings:
            print("  ✅ load_all=True")
            print("  ✅ reconciliation=True")
            print("  ✅ SL/TP 字段名正确")
            print("  ✅ 所有关键配置检查通过")

        if issues:
            print("  " + "=" * 66)
            print("  ⛔ 发现严重配置问题! 请先修复上述问题再运行实盘交易。")
            print("  " + "=" * 66)
            print()
            # In non-interactive mode, just warn and continue
            if sys.stdin.isatty():
                response = input("  是否继续诊断? (y/N): ")
                if response.lower() != 'y':
                    print("  退出诊断。")
                    return False

        return len(issues) == 0

    def _check_critical_config(self) -> Tuple[List[str], List[str]]:
        """
        Check critical configuration settings.

        Returns:
            (issues, warnings): Lists of issues and warnings
        """
        issues = []
        warnings = []
        project_root = self.ctx.project_root

        # Check 1: main_live.py load_all and reconciliation
        self._check_main_live(project_root, issues, warnings)

        # Check 2: ai_strategy.py SL/TP field names
        self._check_strategy_fields(project_root, issues, warnings)

        # Check 3: trading_logic.py MIN_SL_DISTANCE_PCT
        self._check_trading_logic(project_root, issues, warnings)

        # Check 4: patches
        self._check_patches(project_root, issues, warnings)

        return issues, warnings

    def _check_main_live(
        self,
        project_root: Path,
        issues: List[str],
        warnings: List[str]
    ) -> None:
        """Check main_live.py configuration."""
        main_live_path = project_root / "main_live.py"

        if not main_live_path.exists():
            issues.append("❌ main_live.py 文件不存在!")
            return

        with open(main_live_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Check load_all
        load_all_matches = re.findall(r'load_all\s*=\s*(True|False)', content)
        if not load_all_matches:
            warnings.append("main_live.py: 未找到 load_all 配置")
        elif 'False' in load_all_matches:
            issues.append(
                "❌ main_live.py: load_all=False\n"
                "   → 可能导致 instrument 初始化不完整，订单无法执行\n"
                "   → 修复: 改为 load_all=True"
            )

        # Check reconciliation (supports two formats)
        reconciliation_hardcoded = re.findall(
            r'reconciliation\s*=\s*(True|False)', content
        )
        reconciliation_configmanager = re.search(
            r"config_manager\.get\s*\(\s*['\"]execution['\"].*['\"]reconciliation['\"].*default\s*=\s*(True|False)",
            content
        )

        if reconciliation_configmanager:
            if reconciliation_configmanager.group(1) == 'False':
                issues.append(
                    "❌ main_live.py: reconciliation default=False\n"
                    "   → 仓位不同步，可能导致订单管理异常\n"
                    "   → 修复: 改为 default=True"
                )
        elif reconciliation_hardcoded:
            if 'False' in reconciliation_hardcoded:
                issues.append(
                    "❌ main_live.py: reconciliation=False\n"
                    "   → 仓位不同步，可能导致订单管理异常\n"
                    "   → 修复: 改为 reconciliation=True"
                )
        else:
            warnings.append("main_live.py: 未找到 reconciliation 配置")

    def _check_strategy_fields(
        self,
        project_root: Path,
        issues: List[str],
        warnings: List[str]
    ) -> None:
        """Check strategy SL/TP field names (all mixin files)."""
        # v24.0: Read all strategy files (main + 5 mixins)
        content = ""
        for _sf in ["ai_strategy.py", "event_handlers.py", "order_execution.py",
                     "position_manager.py", "safety_manager.py", "telegram_commands.py"]:
            _sp = project_root / "strategy" / _sf
            if _sp.exists():
                content += _sp.read_text(encoding='utf-8') + "\n"

        if not content:
            warnings.append("strategy/ files not found")
            return

        # Check for incorrect field names
        if "stop_loss_multi" in content:
            issues.append(
                "❌ ai_strategy.py: 使用了 'stop_loss_multi' 字段名\n"
                "   → MultiAgent 返回的字段名是 'stop_loss'\n"
                "   → 这会导致 SL 值永远为 None\n"
                "   → 修复: 改为 .get('stop_loss')"
            )

        if "take_profit_multi" in content:
            issues.append(
                "❌ ai_strategy.py: 使用了 'take_profit_multi' 字段名\n"
                "   → MultiAgent 返回的字段名是 'take_profit'\n"
                "   → 这会导致 TP 值永远为 None\n"
                "   → 修复: 改为 .get('take_profit')"
            )

        # Check correct field names exist
        if not re.search(r"\.get\(['\"]stop_loss['\"]\)", content):
            warnings.append("ai_strategy.py: 未找到 .get('stop_loss') 调用")
        if not re.search(r"\.get\(['\"]take_profit['\"]\)", content):
            warnings.append("ai_strategy.py: 未找到 .get('take_profit') 调用")

    def _check_trading_logic(
        self,
        project_root: Path,
        issues: List[str],
        warnings: List[str]
    ) -> None:
        """Check trading_logic.py SL/TP validation functions and R/R gate."""
        trading_logic_path = project_root / "strategy" / "trading_logic.py"

        if not trading_logic_path.exists():
            return

        with open(trading_logic_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Check SL distance function exists
        if 'def get_min_sl_distance_pct' not in content:
            warnings.append(
                "trading_logic.py: 未找到 get_min_sl_distance_pct() 函数\n"
                "   → SL 距离验证可能不生效"
            )

        # Check R/R hard gate function exists
        if 'def get_min_rr_ratio' not in content:
            issues.append(
                "trading_logic.py: 未找到 get_min_rr_ratio() 函数\n"
                "   → R/R 硬性门槛未实现，AI 可能返回极低 R/R 的 SL/TP"
            )

        # Check validate_multiagent_sltp contains R/R check
        if 'def validate_multiagent_sltp' in content:
            # Find the function body (v5.12: expanded to 8000 chars due to
            # zone-anchored bypass + counter-trend escalation code)
            func_start = content.index('def validate_multiagent_sltp')
            func_body = content[func_start:func_start + 8000]
            if 'rr_ratio' not in func_body or 'get_min_rr_ratio' not in func_body:
                issues.append(
                    "trading_logic.py: validate_multiagent_sltp() 缺少 R/R 硬性门槛\n"
                    "   → AI 返回低 R/R (如 0.1:1) 时无法拦截"
                )

        # Check multi_agent_analyzer.py imports
        analyzer_path = project_root / "agents" / "multi_agent_analyzer.py"
        if analyzer_path.exists():
            with open(analyzer_path, 'r', encoding='utf-8') as f:
                analyzer_content = f.read()

            # v18.3: get_default_sl_pct removed from multi_agent_analyzer.py —
            # SL validation is handled downstream by calculate_mechanical_sltp()
            # in ai_strategy.py. No SL import needed here anymore.
            has_sl_removed_note = "get_default_sl_pct removed" in analyzer_content
            has_import = "from strategy.trading_logic import" in analyzer_content
            has_getter = (
                "get_min_sl_distance_pct" in analyzer_content
                or "get_default_sl_pct" in analyzer_content
            )

            if not has_sl_removed_note and not (has_import and has_getter):
                warnings.append(
                    "multi_agent_analyzer.py: 未从 trading_logic 导入 SL 相关函数\n"
                    "   → 应导入 get_default_sl_pct() 或 get_min_sl_distance_pct()"
                )

    def _check_patches(
        self,
        project_root: Path,
        issues: List[str],
        warnings: List[str]
    ) -> None:
        """Check if patches are properly set up."""
        binance_enums = project_root / "patches" / "binance_enums.py"
        if not binance_enums.exists():
            warnings.append(
                "patches/binance_enums.py 不存在 - 可能缺少枚举兼容性补丁"
            )


class MTFConfigChecker(DiagnosticStep):
    """
    Check Multi-Timeframe configuration.

    Validates:
    - MTF enabled status
    - Trend/Decision/Execution layer configuration
    - Order Flow configuration
    - Initialization settings
    """

    name = "MTF 多时间框架配置检查"

    def run(self) -> bool:
        print("-" * 70)

        # Load environment variables early (for API key checks)
        from dotenv import load_dotenv
        env_permanent = Path.home() / ".env.algvex"
        if env_permanent.exists():
            load_dotenv(env_permanent)
        else:
            load_dotenv()

        try:
            mtf_config_path = self.ctx.project_root / "configs" / "base.yaml"

            if not mtf_config_path.exists():
                self.ctx.add_warning("configs/base.yaml 不存在，跳过 MTF 检查")
                return True

            with open(mtf_config_path, 'r', encoding='utf-8') as f:
                self.ctx.base_config = yaml.safe_load(f)

            # Load thresholds from config
            self.ctx.load_thresholds_from_config()

            mtf_config = self.ctx.base_config.get('multi_timeframe', {})
            mtf_enabled = mtf_config.get('enabled', False)

            if mtf_enabled:
                self._display_mtf_config(mtf_config)
                self._check_mtf_manager()
            else:
                print("  ℹ️ MTF 多时间框架: 未启用")
                print("     → 如需启用，编辑 configs/base.yaml:")
                print("       multi_timeframe:")
                print("         enabled: true")

            # Check Order Flow config
            self._check_order_flow_config()

            # v6.0: Check Position Management config
            self._check_v60_position_management_config()

            return True

        except Exception as e:
            self.ctx.add_warning(f"MTF 配置检查失败: {e}")
            return True  # Non-critical, continue

    def _display_mtf_config(self, mtf_config: dict) -> None:
        """Display MTF configuration details."""
        print("  ✅ MTF 多时间框架: 已启用")

        # Trend layer (1D)
        trend_layer = mtf_config.get('trend_layer', {})
        trend_tf = trend_layer.get('timeframe', 'N/A')
        trend_sma = trend_layer.get('sma_period', 200)
        print(f"     趋势层 (Trend): {trend_tf} (SMA_{trend_sma})")
        if 'require_above_sma' in trend_layer:
            print(f"       require_above_sma: {trend_layer['require_above_sma']}")
        if 'require_macd_positive' in trend_layer:
            print(f"       require_macd_positive: {trend_layer['require_macd_positive']}")

        # Decision layer (4H)
        decision_layer = mtf_config.get('decision_layer', {})
        decision_tf = decision_layer.get('timeframe', 'N/A')
        print(f"     决策层 (Decision): {decision_tf}")
        if 'debate_rounds' in decision_layer:
            print(f"       debate_rounds: {decision_layer['debate_rounds']}")
        if 'include_trend_context' in decision_layer:
            print(f"       include_trend_context: {decision_layer['include_trend_context']}")

        # Execution layer (30M bar subscription, v18.2)
        execution_layer = mtf_config.get('execution_layer', {})
        execution_tf = execution_layer.get('default_timeframe', 'N/A')
        print(f"     执行层 (Execution): {execution_tf}")
        if 'rsi_entry_min' in execution_layer:
            print(f"       RSI 入场范围: {execution_layer.get('rsi_entry_min', 30)}-{execution_layer.get('rsi_entry_max', 70)}")

        # Initialization config
        init_cfg = mtf_config.get('initialization', {})
        if init_cfg:
            print("  ✅ MTF 初始化配置存在")
            print(f"     trend_min_bars: {init_cfg.get('trend_min_bars', 'N/A')}")
            print(f"     decision_min_bars: {init_cfg.get('decision_min_bars', 'N/A')}")
            print(f"     execution_min_bars: {init_cfg.get('execution_min_bars', 'N/A')}")
        else:
            print("  ⚠️ MTF initialization 配置段不存在")
            print("     → 将使用默认值 (220/60/40 bars)")

    def _check_mtf_manager(self) -> None:
        """Check MultiTimeframeManager module."""
        mtf_manager_path = self.ctx.project_root / "indicators" / "multi_timeframe_manager.py"

        if mtf_manager_path.exists():
            print("  ✅ MultiTimeframeManager 模块存在")
            try:
                from indicators.multi_timeframe_manager import MultiTimeframeManager
                print("  ✅ MultiTimeframeManager 导入成功")
                print("     v3.3: 三层数据收集 (1D/4H/30M)，决策逻辑由 AI 控制")
            except ImportError as e:
                self.ctx.add_warning(f"MultiTimeframeManager 导入失败: {e}")
        else:
            self.ctx.add_error("MultiTimeframeManager 模块不存在!")
            print("     → 预期路径: indicators/multi_timeframe_manager.py")

    def _check_order_flow_config(self) -> None:
        """Check Order Flow configuration."""
        order_flow = self.ctx.base_config.get('order_flow', {})
        order_flow_enabled = order_flow.get('enabled', False)

        print()
        if order_flow_enabled:
            print("  ✅ Order Flow: 已启用")
            binance_of = order_flow.get('binance', {})
            coinalyze = order_flow.get('coinalyze', {})
            print(f"     Binance enabled: {binance_of.get('enabled', False)}")
            print(f"     Coinalyze enabled: {coinalyze.get('enabled', False)}")

            # Check Coinalyze API key
            coinalyze_api_key = coinalyze.get('api_key') or os.getenv('COINALYZE_API_KEY')
            if coinalyze.get('enabled') and not coinalyze_api_key:
                print("     ⚠️ Coinalyze 已启用但缺少 API key")
            elif coinalyze.get('enabled') and coinalyze_api_key:
                print(f"     ✅ Coinalyze API key: {mask_sensitive(coinalyze_api_key)}")
        else:
            print("  ℹ️ Order Flow: 未启用")


    def _check_v60_position_management_config(self) -> None:
        """v6.0: Check cooldown/pyramiding configuration."""
        risk_config = self.ctx.base_config.get('risk', {})

        print()
        print("  📋 v6.0 仓位管理配置:")

        # Cooldown
        cooldown = risk_config.get('cooldown', {})
        cd_enabled = cooldown.get('enabled', False)
        print(f"     冷静期 (Cooldown): {'✅ 已启用' if cd_enabled else '❌ 未启用'}")
        if cd_enabled:
            print(f"       per_stoploss_candles: {cooldown.get('per_stoploss_candles', 'N/A')}")
            print(f"       noise/reversal/volatility: "
                  f"{cooldown.get('noise_stop_candles', 'N/A')}/"
                  f"{cooldown.get('reversal_stop_candles', 'N/A')}/"
                  f"{cooldown.get('volatility_stop_candles', 'N/A')}")

        # Pyramiding
        pyramiding = risk_config.get('pyramiding', {})
        py_enabled = pyramiding.get('enabled', False)
        print(f"     金字塔加仓 (Pyramiding): {'✅ 已启用' if py_enabled else '❌ 未启用'}")
        if py_enabled:
            print(f"       layer_sizes: {pyramiding.get('layer_sizes', 'N/A')}")
            print(f"       min_confidence: {pyramiding.get('min_confidence', 'N/A')}")
            print(f"       counter_trend_allowed: {pyramiding.get('counter_trend_allowed', 'N/A')}")


class StrategyConfigLoader(DiagnosticStep):
    """
    Load strategy configuration from main_live.py.

    Uses the same initialization flow as the production system.
    """

    name = "从 main_live.py 加载真实配置"

    def run(self) -> bool:
        try:
            # Add project root to path
            if str(self.ctx.project_root) not in sys.path:
                sys.path.insert(0, str(self.ctx.project_root))

            # Apply patches (same as main_live.py)
            from patches.binance_enums import apply_all_patches
            apply_all_patches()

            # Load environment variables (same as main_live.py)
            from dotenv import load_dotenv
            env_permanent = Path.home() / ".env.algvex"
            env_local = self.ctx.project_root / ".env"

            if env_permanent.exists():
                load_dotenv(env_permanent)
            elif env_local.exists():
                load_dotenv(env_local)
            else:
                load_dotenv()

            # Load strategy config
            from main_live import get_strategy_config
            from utils.config_manager import ConfigManager

            config_manager = ConfigManager(env=self.ctx.env)
            config_manager.load()

            self.ctx.strategy_config = get_strategy_config(config_manager)

            # Display config
            cfg = self.ctx.strategy_config
            if not self.ctx.summary_mode:
                print(f"  instrument_id: {cfg.instrument_id}")
                print(f"  bar_type: {cfg.bar_type}")
                print(f"  equity: ${cfg.equity}")
                print(f"  leverage: {cfg.leverage}x (配置值，实际将从 Binance 同步)")
                print(f"  min_confidence_to_trade: {cfg.min_confidence_to_trade}")
                timer_sec = cfg.timer_interval_sec
                timer_min = timer_sec / 60
                print(f"  timer_interval_sec: {timer_sec}s ({timer_min:.1f}分钟)")
                print()

                # v4.8: Position sizing configuration
                print("  📊 v4.8 仓位计算配置:")
                method = getattr(cfg, 'position_sizing_method', 'ai_controlled')
                print(f"     method: {method}")
                print(f"     max_position_ratio: {getattr(cfg, 'max_position_ratio', 0.30)*100:.0f}%")
                print(f"     cumulative: {getattr(cfg, 'position_sizing_cumulative', True)}")
                print(f"     信心映射:")
                print(f"       HIGH: {getattr(cfg, 'position_sizing_high_pct', 80)}%")
                print(f"       MEDIUM: {getattr(cfg, 'position_sizing_medium_pct', 50)}%")
                print(f"       LOW: {getattr(cfg, 'position_sizing_low_pct', 30)}%")
                print()

                print("  📊 技术指标配置:")
                print(f"     sma_periods: {list(cfg.sma_periods)}")
                print(f"     rsi_period: {cfg.rsi_period}")
                print(f"     macd_fast/slow: {cfg.macd_fast}/{cfg.macd_slow}")
                print(f"     strategy_mode: {getattr(cfg, 'strategy_mode', 'mechanical')}")
                print()

                # v6.0: Position Management config
                print("  📊 v6.0 仓位管理配置:")
                print(f"     cooldown_enabled: {getattr(cfg, 'cooldown_enabled', 'N/A')}")
                print(f"     pyramiding_enabled: {getattr(cfg, 'pyramiding_enabled', 'N/A')}")
                print()

                # v15.0: Extracted config values (was hardcoded)
                print("  📊 v15.0 配置化参数 (原硬编码值):")
                print(f"     emergency_sl_base_pct: {getattr(cfg, 'emergency_sl_base_pct', 'N/A')}")
                print(f"     emergency_sl_atr_multiplier: {getattr(cfg, 'emergency_sl_atr_multiplier', 'N/A')}")
                print(f"     emergency_sl_cooldown_seconds: {getattr(cfg, 'emergency_sl_cooldown_seconds', 'N/A')}s")
                print(f"     emergency_sl_max_consecutive: {getattr(cfg, 'emergency_sl_max_consecutive', 'N/A')}")
                print(f"     sr_zones_cache_ttl_seconds: {getattr(cfg, 'sr_zones_cache_ttl_seconds', 'N/A')}s")
                print(f"     price_cache_ttl_seconds: {getattr(cfg, 'price_cache_ttl_seconds', 'N/A')}s")
                print(f"     reversal_timeout_seconds: {getattr(cfg, 'reversal_timeout_seconds', 'N/A')}s")
                print(f"     max_leverage_limit: {getattr(cfg, 'max_leverage_limit', 'N/A')}")
                print()

                print("  ✅ 配置加载成功 (与实盘完全一致)")
                print()
                print(f"  ⏰ 注意: 实盘每 {timer_min:.0f} 分钟分析一次")
                print("     如果刚启动服务，需等待第一个周期触发")

            # Parse symbol and interval
            from .base import parse_bar_interval, extract_symbol
            self.ctx.symbol = extract_symbol(cfg.instrument_id)
            self.ctx.interval = parse_bar_interval(str(cfg.bar_type))

            return True

        except Exception as e:
            self.ctx.add_error(f"配置加载失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def should_skip(self) -> bool:
        # Never skip - all other steps depend on strategy_config
        return False
