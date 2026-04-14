"""
Diagnostics Module for AlgVex
================================

A modular diagnostic system for real-time trading signal analysis.
Refactored from the monolithic diagnose_realtime.py (v11.16) into
a clean, maintainable architecture.

v15.0 更新:
- code_integrity.py: P1.40 硬编码值提取验证 + P1.41 静默异常覆盖 + P1.42 测试套件基础设施
- math_verification.py: M6 Emergency SL 配置化 + M12 配置链验证 (base.yaml → StrategyConfig → self.xxx)
- config_checker.py: StrategyConfigLoader 显示 v15.0 配置化参数 (8 个原硬编码值)

v14.0 更新:
- code_integrity.py: P1.37 双频道 Telegram 路由 + P1.39 config.get() 安全检查
- architecture_verify.py: v14.0 双频道 Telegram + v7.3 SL 交叉验证运行时检查
- mtf_components.py: TelegramChecker 增加通知频道 API 连通性 + broadcast 参数验证

v13.1 更新:
- code_integrity.py: P1.36 extended — emergency SL on close/partial-close failure

v12.0 更新:
- code_integrity.py: P1.34-P1.35 Per-Agent Reflection Memory + 重启补回
- architecture_verify.py: v12.0 反思记忆运行时验证

v7.3 更新:
- code_integrity.py: P1.38 重启 SL 交叉验证 (Tier 2 交易所查询)

v7.2 更新:
- code_integrity.py: P1.6/P1.21/P1.24/P1.25 per-layer 独立 SL/TP

v7.0 更新:
- code_integrity.py: P1.30 SSoT AIDataAssembler
- architecture_verify.py: AIDataAssembler 运行时验证

v6.3 更新:
- math_verification.py: M11f-h ATR-primary gate 诊断守卫
- code_integrity.py: P1.29 AI prompt ATR 语言检查

v6.2 更新:
- code_integrity.py: P1.26 LIMIT入场 + P1.27 过期安全 + P1.28 线程安全
- position_check.py: NakedPositionScanner 裸仓检测

v6.0 更新:
- code_integrity.py: P1.14-P1.17 cooldown/pyramiding/eval integrity
- order_flow_simulation.py: 14 场景模拟
- config_checker.py: cooldown/pyramiding 配置验证

Module Structure:
- base.py: Core classes, utilities, and shared functionality
- code_integrity.py: v15.0 static code analysis (P1.0-P1.42)
- math_verification.py: v15.0 math verification (M1-M12)
- config_checker.py: Configuration validation (含 v6.0 position management)
- market_data.py: Market data fetching from Binance
- indicator_test.py: Technical indicator testing
- position_check.py: Position and account checking
- ai_decision.py: AI decision analysis (MultiAgent)
- mtf_components.py: Multi-timeframe component testing + v14.0 dual-channel Telegram
- lifecycle_test.py: Post-trade lifecycle and MTF routing tests
- order_flow_simulation.py: v6.0 order flow simulation (14 scenarios)
- architecture_verify.py: TradingAgents architecture verification + v14.0/v7.3 runtime checks
- summary.py: Results summary and export
- service_health.py: Service health and API checks

Usage:
    from scripts.diagnostics import DiagnosticRunner
    runner = DiagnosticRunner(env='production')
    runner.run_all()

"""

from .base import (
    DiagnosticContext,
    DiagnosticStep,
    DiagnosticRunner,
    MockBar,
    TeeOutput,
    ensure_venv,
    fetch_binance_klines,
    create_bar_from_kline,
    safe_float,
    print_wrapped,
    print_section,
    print_box,
)
__all__ = [
    "DiagnosticContext",
    "DiagnosticStep",
    "DiagnosticRunner",
    "MockBar",
    "TeeOutput",
    "ensure_venv",
    "fetch_binance_klines",
    "create_bar_from_kline",
    "safe_float",
    "print_wrapped",
    "print_section",
    "print_box",
]
