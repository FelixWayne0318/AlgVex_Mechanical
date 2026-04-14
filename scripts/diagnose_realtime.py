#!/usr/bin/env python3
"""
实盘信号诊断工具 (v2.0 Phase 1)

100% 还原实盘 on_timer() → AIDataAssembler → MultiAgentAnalyzer.analyze() 全流程。
融合 v7.2 完整订单流诊断 (代码完整性 + 数学验证 + 15 场景模拟)。

v2.0 Phase 1 诊断更新 (升级方案组件验证):
  - Phase 1.5 新增 9 个诊断步骤:
    1. UpgradeV2DependencyCheck: 9 个新依赖安装验证
    2. UpgradeV2ModuleCheck: 7 个新模块导入验证
    3. UpgradeV2BugFixCheck: 8 项 bug 修复验证 (volume_profile/backtest_math/pivot/
       calibration_loader/audit_logger/pearson_r/_score_memory/sentiment_client)
    4. UpgradeV2TenacityCheck: 3 个 modified + 2 个 preserved REST client retry 状态
    5. UpgradeV2PanderaCheck: Pandera 3 层验证 (sentiment/features/cross-field)
    6. UpgradeV2HMMCheck: HMM 4-state 训练/预测/ADX fallback/v44 兼容
    7. UpgradeV2FearGreedCheck: Fear & Greed API 实时连通性
    8. UpgradeV2InstructorCheck: Instructor Pydantic schema + registry
    9. UpgradeV2ConfigCheck: base.yaml 新增段落 + Phase 0 baseline + DSPy dataset
  - v2.0 Parity Fix: HMM regime detection runs BEFORE analyze() (mirrors ai_strategy.py:2952-2989)
    hmm_regime_result 作为参数传入 analyze()，确保 AI agents 接收 regime context
  - v2.0 Parity Fix: Kelly sizing 传入 HMM regime (mirrors order_execution.py:430-447)
  - v2.0 Parity Fix: VaR/CVaR 使用 set_regime() 显示 regime-adaptive thresholds

v42.0 诊断更新 (ET Exhaustion Mechanism):
  - analyze() 调用新增 skip_entry_timing=False 参数 (v42.0 Tier 2 跳过 ET)
  - SignalProcessor Gate 2 显示 ET Exhaustion Tier 1/2 flags (若 signal_data 中存在)
  - 新增 [S3] stateful gate: ET Exhaustion 跨周期计数器 (Tier 1 ≥5 / Tier 2 ≥8)
  - code_integrity.py P1.122-P1.123: ET Exhaustion 回归守卫
  - 信号处理完整 pipeline: 7 gates + 3 stateful notes (新增 ET Exhaustion)

v32.1 诊断更新 (Risk Manager Conditional Skip):
  - Risk Manager API call skipped when Judge outputs HOLD/CLOSE/REDUCE (no position to size)
  - Non-actionable signals use safe passthrough defaults (no AI call)
  - Mirrors Entry Timing Agent skip pattern (Phase 2.5, v23.0)
  - RiskManagerStandaloneTest: forced invocation to catch bugs hidden by HOLD periods
  - P1.113: regression guard for conditional skip pattern in code_integrity.py

v31.4 诊断更新 (Feature Extraction Production Parity):
  - extract_features() field name 修复: 4 处 key 名不匹配导致 feature 永远为 0.0
    1. v31.4: 30M base indicator_manager uses ema_periods=[12,26] (from MACD config), features are ema_12_30m/ema_26_30m
    2. position_pnl_pct: cp.get('pnl_pct') → cp.get('pnl_percentage') (match _get_current_position_data)
    3. position_size_pct: cp.get('size_pct') → cp.get('margin_used_pct') (match _get_current_position_data)
    4. liquidation_buffer_pct: ac.get('liquidation_buffer_pct') → ac.get('liquidation_buffer_portfolio_min_pct')
  - FEATURE_SCHEMA 同步更新 source 注释
  - diagnose_quality.py / diagnose_quality_scoring.py ema 引用同步
  - ai_decision.py: 新增 v31.4 Feature Parity Verification (30M EMA keys + position/account field mapping)

v31.3 诊断更新 (AI Quality Audit 完整显示):
  - ai_decision.py _display_quality_audit(): 新增 v26.0+ citation/value/zone 错误显示
    实盘 AIQualityAuditor.audit() 返回 citation_errors/value_errors/zone_errors，
    诊断之前只显示 coverage/flags，漏掉了最关键的 accuracy verification 结果
  - ai_decision.py _display_quality_audit(): 新增 v29.4 neutral_acknowledged/unconfirmed_neutral
    显示，对齐 QualityReport.to_dict() 输出的全部字段

v30.6 诊断更新 (Production Parity Hardening):
  - indicator_test.py: 30M/4H/1D TechnicalIndicatorManager 新增 macd_signal 参数
    与实盘 multi_timeframe_manager.py 显式传递 macd_signal=default_macd_signal 一致
  - ai_decision.py: MultiAgentAnalyzer 构造器移除多余 memory_file 参数
    与实盘 ai_strategy.py:664-672 构造器参数完全一致 (生产使用默认值)
  - ai_decision.py: 新增 Constructor parity check 打印，验证 model/temperature/
    debate_rounds/memory_file 与实盘配置一致

v27.0 诊断更新 (Feature-Driven Architecture):
  - MultiAgentAnalyzer: 显示 extract_features() 状态 + structured vs text 路径选择
  - _display_results: 显示 decisive_reasons, evidence/risk_flags REASON_TAGS, conviction
  - Call trace: schema_version, feature_version, prompt_hash per call
  - Prompt 结构验证: 检测 feature-driven JSON prompts vs text prompts
  - Debate transcript: 显示 structured Bull/Bear output (tags + conviction)
  - Feature snapshot 验证: data/feature_snapshots/ 持久化检查
  - Risk Manager: 显示 risk_factors (REASON_TAGS)
  - Entry Timing: 显示 decisive_reasons (REASON_TAGS)

v28.0 诊断更新 (Entry Timing Standalone Test):
  - EntryTimingStandaloneTest: 独立强制测试 Entry Timing Agent (Phase 2.5)
    无论 Judge 输出 LONG/SHORT/HOLD，都额外构造 mock Judge decision (SHORT, HIGH)
    执行真实 AI 调用，验证 7 项: timing_verdict, timing_quality, adjusted_confidence
    (只降不升), counter_trend_risk, alignment, decisive_reasons (REASON_TAGS), reason
    防止 HOLD 长期不触发 ET 导致潜在 bug 隐藏。+1 API call (~3K tokens)

v24.0 诊断更新 (Trailing Stop TRAILING_STOP_MARKET):
  - Phase 1 CodeIntegrity: P1.100-P1.107 新增 8 项 v24.0 trailing stop 回归守卫
    P1.100: v24.0 _submit_trailing_stop (TRAILING_STOP_MARKET + BPS offset)
    P1.101: v24.0/v43.0 trailing activation (1.5R threshold + 4H ATR + min risk guard)
    P1.102: v24.0 event_handler TRAILING_STOP_MARKET recognition + close reason
    P1.103: v24.0 /modify_sl trailing flag reset (cancel TRAILING + reset flag)
    P1.104: v24.0 TRAILING_STOP_MARKET paired with STOP_MARKET across all checks
    P1.105: v24.0 startup recovery uses OrderType enum (no string matching)
    P1.106: v24.0 reconcile layer quantities triggers order resubmission
    P1.107: v24.0 emergency close cancels reduce_only orders first
  - Phase 1 CodeIntegrity: P1.108-P1.110 补充回归守卫
    P1.108: v21.0 FR Consecutive Block Counter (init/increment/threshold/reset)
    P1.109: v18.3 Post-close forced analysis cycles (set=2, consume)
    P1.110: v24.0 _resubmit_tp_for_layer (TP recovery on cancel/expire)
  - Phase 8 架构验证: v24.0 trailing stop 运行时验证 (12 项: submit method, on_timer call,
    safety pattern, activation R, BPS limits, event handler, string matching, modify_sl,
    reconciliation, emergency close, heartbeat, min risk guard)
  - Phase 10 订单流程模拟: 场景 16 追踪止损激活 (v24.0 TRAILING_STOP_MARKET)
    + 场景 14 紧急平仓更新 (v24.0 先取消 reduce_only 订单)
  - Phase 11 数学验证: M16 Trailing Stop boundary math (fee buffer, BPS limits, min risk guard, 4 项)

v23.0 诊断更新 (Entry Timing Agent + gate ordering alignment):
  - Phase 2.5 Entry Timing Agent 替代 v22.2 的 Alignment Gate + Entry Quality POOR + 30M Cap
  - Phase 7 SignalProcessor: 7 gates + 2 stateful notes, 顺序与生产环境完全一致
    [S1] Signal fingerprint dedup (stateful)
    [1]  Risk Controller (circuit breaker + position multiplier)
    [2]  Entry Timing Agent (reads Phase 2.5 results)
    [3]  Signal age check (>600s → HOLD)
    [4]  Legacy normalization (BUY→LONG, SELL→SHORT)
    [S2] FR consecutive block exhaustion (stateful)
    [5]  Confidence filter (min_confidence_to_trade)
    [6]  Liquidation buffer hard floor (<5% blocks add-on)
    [7]  FR entry check (paying FR > 0.09% blocks entry)

v20.0 诊断更新 (ATR Volatility Regime + OBV Divergence Detection):
  - Phase 1 CodeIntegrity: P1.85-P1.90 新增 6 项 v20.0 回归守卫
    P1.85: v20.0 ATR Volatility Regime calculation (_calculate_atr_regime + SSoT classify)
    P1.86: v20.0 Volatility Regime fields in get_technical_data (volatility_regime/percentile/atr_pct)
    P1.87: v20.0 OBV tracking (_update_obv + _obv_values + obv_trend in historical_context)
    P1.88: v20.0 OBV divergence in _detect_divergences (_ema_smooth + obv_series + custom format)
    P1.89: v20.0 Volatility Regime + OBV in AI prompts + SIGNAL_CONFIDENCE_MATRIX
    P1.90: v20.0 Volatility Regime orthogonal to calculate_mechanical_sltp (pure RISK/CONTEXT)
  - Phase 4 TechnicalDataFetcher: Volatility Regime field validation + percentile classification check
  - Phase 6 AIInputDataValidator: Volatility Regime fields displayed (vol_regime, percentile, atr_pct)
  - Phase 8 架构验证: v20.0 运行时验证 (8 项: atr_regime calc, fields, OBV tracking,
    OBV divergence, _ema_smooth, report display, SIGNAL_CONFIDENCE_MATRIX, orthogonality)
  - Phase 11 数学验证: M15 Volatility Regime boundary math (percentile thresholds, 12 edge cases)

v19.1 诊断更新 (ATR Extension Ratio + Divergence + CVD-Price):
  - Phase 1 CodeIntegrity: P1.74-P1.81 新增 8 项 v19.1 回归守卫
    P1.74: v19.1 ATR Extension Ratio calculation (_calculate_extension_ratios)
    P1.75: v19.1 Extension Ratio fields in get_technical_data (extension_ratio_sma_* + extension_regime)
    P1.76: v19.1 Extension Ratio integrated in all 5 AI agent prompts (Bull/Bear/Judge/EntryTiming/Risk)
    P1.77: v19.1 RSI/MACD divergence pre-computation (_detect_divergences)
    P1.78: v19.1 CVD-Price cross-analysis (ACCUMULATION/DISTRIBUTION/CONFIRMED)
    P1.79: v19.1 SIGNAL_CONFIDENCE_MATRIX Ext Ratio rows (overextended + extreme)
    P1.80: v19.1 Extension Ratio orthogonal to calculate_mechanical_sltp (pure RISK)
    P1.81: v19.1.1 Trend-aware extension modulation (ADX>40 de-emphasis)
  - Phase 4 TechnicalDataFetcher: Extension Ratio field validation + regime classification check
  - Phase 6 AIInputDataValidator: Extension Ratio fields displayed (ext_ratio_sma_*, extension_regime)
  - Phase 8 架构验证: v19.1 运行时验证 (7 项: extension calc, fields, divergence, CVD-Price,
    SIGNAL_CONFIDENCE_MATRIX, trend-aware, orthogonality)
  - Phase 11 数学验证: M14 Extension Ratio boundary math (regime thresholds, edge cases)

v19.0 诊断更新 (Confidence Authority + Signal Deadlock Fixes):
  - Phase 1 CodeIntegrity: P1.68-P1.73 新增 6 项 v19.0 回归守卫
    P1.68: v19.0 LogAdapter self._log (not self.log) — AttributeError crash fix
    P1.69: v19.0 Confidence authority separation (Judge→confidence, RM→risk_appetite)
    P1.70: v19.0 Fingerprint cleared in on_position_closed AND _cancel_pending_entry_order
    P1.71: v19.0 Fingerprint stored only when executed=True (prevents deadlock)
    P1.72: v19.0 Alignment gate receives ai_technical_data (with MTF layers)
    P1.73: v19.0 Memory selection once (_select_memories + preselected per-role)
  - Phase 8 架构验证: v19.0 运行时验证 (6 项: LogAdapter fix, confidence authority,
    risk_appetite validation, fingerprint clearing, memory optimization)
  - Phase 8 AI 决策输出验证: v19.0 字段更新 (risk_appetite 新增, risk_level/invalidation
    /debate_summary 改为可选)

v18.2 诊断更新 (Execution Layer + Bug Fixes):
  - Phase 1 CodeIntegrity: P1.63-P1.67 新增 5 项 v18.2 回归守卫
    P1.63: v18.2 Price Surge Trigger (on_trade_tick + _SURGE_THRESHOLD_PCT + 5min cooldown)
    P1.64: v18.2 Ghost Position Cleanup (3× -2022 rejection counter + _clear_position_state)
    P1.65: v23.0 Entry Timing Agent (取代 v18.2 Alignment Gate Weight Regime)
    P1.66: /modify_sl & /modify_tp layer tracking fix (_layer_orders update after modify)
    P1.67: /partial_close emergency SL re-protection (cancelled SL → _submit_emergency_sl)
  - Phase 8 架构验证: v18.2 运行时验证 (7 项: price surge trigger, ghost position
    cleanup, Entry Timing Agent (v23.0), signal reliability tiers, /modify_sl layer
    tracking, /partial_close emergency SL, strong-trend role conditioning)

v18.1 诊断更新 (v18 Batch 2/3 features):
  - Phase 1 CodeIntegrity: P1.57-P1.62 新增 6 项 v18 回归守卫
    P1.57: v18 Item 15 — 15M→30M migration (interval + prompt + historical_context)
    P1.58: v18 Item 16 — 4H CVD order flow (fetch + pass-through + MA param)
    P1.59: v23.0 Entry Timing Agent prompt structure (取代 v18 alignment gate)
    P1.60: v23.0 Entry Timing Agent confidence downgrade (取代 v18 entry quality)
    P1.61: v18 Item 20 — Direction compliance audit (method + counter + ADX skip)
    P1.62: v18 Item 22 — Per-agent data partitioning (direction_report + ADX<25 bypass)
  - Phase 6 AIInputDataValidator: fetch_external_data interval 15m→30m
  - Phase 6 AIInputDataValidator: order_flow_report_4h 加入数据完整性检查
  - Phase 6 AIInputDataValidator: historical_context count 35→20 (匹配 v18 Item 10)
  - Phase 7 MultiAgentAnalyzer: analyze() 新增 order_flow_report_4h 参数 (18 参数)
  - Phase 8 架构验证: v18 Batch 2/3 运行时验证 (9 项: alignment gate, direction
    compliance, data partitioning, 4H CVD, 4H historical context, 1D BB/ATR 等)
  - base.py DiagnosticContext: 新增 order_flow_report_4h 字段

v18.0.1 诊断更新:
  - Phase 5 MemorySystemChecker: v18.0 Extended Reflection 文件健康检查
    (data/extended_reflections.json 存在性/格式/条目/时效)
  - Phase 5 MemorySystemChecker: v18.0 Recency scoring 验证 (per-cycle cache)
  - Phase 8 架构验证: v18.0 Reflection Reform 7 项运行时检查 (已在 v18.0 实现)
  - P1.54-P1.56: v18.0 Reflection System Reform 回归守卫 (已在 v18.0 实现)

v18.0 诊断-实盘一致性审计:
  - P1.50-P1.52: v18.0 emergency retry timer, sentiment degradation, drawdown hysteresis
  - P1.53: v17.0 S/R 1+1 simplification regression guard
  - P1.54-P1.56: v18.0 Reflection System Reform (extended reflection + recency + trigger)
  - M13: Drawdown hysteresis runtime simulation (5 scenarios)

v16.0 诊断-实盘一致性审计:
  - P1.47-P1.48: v16.0 S/R Hold Probability 校准系统诊断覆盖
  - trading_logic.py: emergency_sl 加入 _get_trading_logic_config() (M6 诊断可读取)
  - math_verification.py: M12f 补齐 emergency_sl_max_consecutive (8 fields)
  - math_verification.py: M11a 阈值 1.5 → 2.0 (与 M11f 一致)
  - sentiment_client.py: 修复 self.logger 未初始化 bug

v15.5 诊断-实盘仓位计算一致性修复:
  - OrderSimulator: 内联简化计算替换为调用生产 calculate_position_size()
  - 补齐 appetite_scale 缩放 (NORMAL=0.8, CONSERVATIVE=0.5)
  - 补齐 max_single_trade_risk_pct 单笔风控钳制 (默认 2% equity)
  - PositionCalculator: 各信心级别显示也通过 calculate_position_size() 计算

v15.4 诊断-实盘 AI 输入数据一致性修复:
  - bars_data_4h: 从传全部 60 条修正为 [-50:] (匹配实盘 decision_mgr.recent_bars[-50:])
  - bars_data_1d: 从传全部 220 条修正为 [-120:] (匹配实盘 trend_mgr.recent_bars[-120:])
  - 注: 仍获取完整 K 线用于指标预热 (SMA 等)，仅 bars_data 传参切片

v15.3 Chandelier/Trailing Stop 全面清除:
  - 删除所有 Chandelier Trailing Stop 残留引用 (代码/配置/文档/诊断)
  - 场景数更正: 17 → 14 (删除 Chandelier 相关的 3 个场景)
  - M5 Dynamic Threshold 测试已移除 (仅 Chandelier 使用)
  - P1.4/P1.8 描述修正: 标记已删除或更新为 Time Barrier
  - 删除 _simulate_sl_replacement_retry 孤立 stub

v15.2 代码审查回归守卫:
  - P1.43: counter_trend_rr_multiplier 必须加载到 _TRADING_LOGIC_CONFIG 运行时缓存
  - P1.44: 紧急 SL/市价平仓/时间屏障 Telegram 消息必须用 side_to_cn() (禁止原始 LONG/SHORT)
  - P1.45: sentiment_client 必须用 .get() 安全访问 (禁止 bracket [] 直接访问)
  - P1.46: Funding rate 格式必须用 :.5f (5 位小数，匹配 Binance)
  - position_check.py: 已结算费率显示精度 .4f → .5f

v15.1 诊断-实盘一致性修复:
  - D9 [HIGH]: 移除 validate_multiagent_sltp() 幻影调用，改为与实盘一致的
    calculate_mechanical_sltp() + pct_fallback 路径
  - D4 [MEDIUM]: AI 分析前处理 pending reflections (匹配实盘 _process_pending_reflections)
  - D5 [MEDIUM]: 30M K线从 100 增至 250，确保 SMA_200 准确
  - D1 [LOW]: BinanceDerivativesClient 不传 config 参数 (匹配实盘 default threshold)
  - D3 [LOW]: OrderBookProcessor 补齐 price_band_pct (匹配实盘)
  - D8 [LOW]: 4H/1D bars 去除 close_time 字段 (匹配实盘 NautilusTrader bars)

v15.0 新增:
  - P1.40: v15.0 硬编码值提取验证 (8 个 magic number → config chain)
  - P1.41: v15.0 静默异常覆盖 (no bare except:pass without logging)
  - P1.42: v15.0 测试套件基础设施 (pytest.ini + conftest.py + 49 tests)
  - M6 升级: Emergency SL 读取 config 值 (不再硬编码 2%)
  - M12: v15.0 配置链验证 (base.yaml → ConfigManager → StrategyConfig → self.xxx)
  - Phase 2: StrategyConfigLoader 显示 v15.0 配置化参数

v14.0 新增:
  - P1.37: v14.0 双频道 Telegram 路由 (broadcast 参数 + 通知频道独立 bot)
  - P1.39: Config 访问安全 (禁止 self.config.get() 调用 StrategyConfig Struct)
  - Phase 8: 架构验证增加 v14.0 双频道 Telegram 运行时验证
  - Phase 9: TelegramChecker 增加通知频道 API 连通性 + broadcast 参数验证

v13.1 新增:
  - P1.36: v13.1 Phantom guard + emergency SL on close/partial-close failure

v12.0 新增:
  - P1.34: v12.0 Per-Agent Reflection Memory infrastructure (生成/更新/队列/角色注入)
  - P1.35: v12.0.1 Reflection backfill on restart (重启补回缺失反思)
  - Phase 5: MemorySystemChecker 增强 — 反思补回候选检测 + Insight 格式一致性
  - Phase 8: 架构验证增加 v12.0 反思运行时验证 (方法/参数/prompt/截断/配置)

v7.3 新增:
  - P1.38: v7.3 重启 SL 交叉验证 (Tier 2 交易所查询)
  - Phase 8: 架构验证增加 v7.3 重启 SL 交叉验证运行时检查

v7.2 新增:
  - P1.6:  v7.2 Add path: _create_layer + R/R validation (per-layer independent SL/TP)
  - P1.21: v7.2 Per-layer SL/TP persistence (_persist_layer_orders + _load_layer_orders)
  - P1.25: v7.2 State clearing covers _layer_orders + _order_to_layer
  - Phase 8: v7.2 architecture checks (_layer_orders, _order_to_layer, _create_layer, etc.)
  - Phase 10: Scenarios 2,3,7,8,16 updated for per-layer architecture

v7.0 新增:
  - P1.30: v7.0 SSoT — on_timer uses AIDataAssembler.fetch_external_data()
  - Phase 6 使用 AIDataAssembler.fetch_external_data() 获取外部数据 (与生产一致)
  - Phase 8 增加 v7.0 AIDataAssembler 运行时验证 (结构/回退/生产集成)

v6.3 新增:
  - M11f-h: ATR-primary min SL distance 诊断守卫 (2.0 ATR gate)
  - P1.29: AI prompt ATR 语言检查 (无固定百分比锚定)

v6.2 新增:
  - P1.26: LIMIT 入场检查 (不用 MARKET)
  - P1.27: LIMIT 过期/取消安全性 (无裸仓)
  - P1.28: 线程安全检查 (indicator_manager 不在后台线程)
  - NakedPositionScanner: 持仓 vs 挂单 SL 交叉验证

AI 决策流程 (顺序执行，每次分析周期):
  Phase 0: Reflection (0~1 AI call, 仅平仓后)
  Round 1: Bull Analyst → Bear Analyst  (2 API calls)
  Round 2: Bull Analyst → Bear Analyst  (2 API calls)
  Judge (Portfolio Manager) Decision    (1 API call)
  Entry Timing Agent (Phase 2.5)        (0~1 API call, 仅 LONG/SHORT)
  Risk Manager Evaluation               (0~1 API call, 仅 LONG/SHORT, v32.1)
  ─────────────────────────────────────
  合计: 5~7+1 次 DeepSeek API 顺序调用 (debate_rounds=2 时)

诊断阶段:
  Phase 0:  服务健康检查 + API 响应
  Phase 1:  v24.0 代码完整性检查 (静态分析, P1.0-P1.110, 含 v24.0 trailing stop 回归守卫)
  Phase 2:  配置验证 (含 v6.0 cooldown/pyramiding)
  Phase 3:  市场数据采集 (K线 + 情绪)
  Phase 4:  技术指标计算
  Phase 5:  持仓 + 裸仓检测 + v5.10+ 记忆系统验证 + v12.0 反思 + v18.0 Extended Reflection
  Phase 6:  AI 输入数据验证 (14 类, 含 v18 4H CVD, 外部数据通过 AIDataAssembler SSoT)
  Phase 7:  AI 决策 (7 次顺序 API 调用, 含 Entry Timing Agent) + 6 gates + 2 stateful notes
  Phase 8:  架构完整性验证 (v7.2 per-layer + DataAssembler + v12.0 反思 + v14.0 Telegram + v7.3 SL + v18.2 + v19.1 + v24.0 Trailing Stop)
  Phase 9:  MTF + Telegram (含 v14.0 双频道) + v6.2 错误恢复 (实际调用)
  Phase 10: v24.0 订单流程模拟 (15 场景, per-layer/LIFO/Time Barrier/Trailing Stop)
  Phase 11: v24.0 数学验证 (R/R, SL方向, Emergency SL, 配置链, Extension Ratio, Volatility Regime, Trailing Stop)
  Phase 12: 汇总 + 深度分析 + JSON 输出
"""

import argparse
import sys
from pathlib import Path

# Add project root to path for imports
project_root = Path(__file__).parent.parent.absolute()
sys.path.insert(0, str(project_root))

# Import venv helper first (before other imports)
from scripts.diagnostics.base import ensure_venv

# Ensure running in venv
ensure_venv()

# Now import the diagnostic framework
from scripts.diagnostics import DiagnosticRunner

# Import all diagnostic steps
from scripts.diagnostics.code_integrity import (
    CodeIntegrityChecker,
)
from scripts.diagnostics.config_checker import (
    CriticalConfigChecker,
    MTFConfigChecker,
    StrategyConfigLoader,
)
from scripts.diagnostics.market_data import (
    MarketDataFetcher,
    SentimentDataFetcher,
    PriceDataBuilder,
)
from scripts.diagnostics.indicator_test import (
    IndicatorInitializer,
    TechnicalDataFetcher,
)
from scripts.diagnostics.position_check import (
    PositionChecker,
    MemorySystemChecker,
    NakedPositionScanner,
)
from scripts.diagnostics.ai_decision import (
    AIInputDataValidator,
    MultiAgentAnalyzer,
    EntryTimingStandaloneTest,
    RiskManagerStandaloneTest,
    SignalProcessor,
    OrderSimulator,
    PositionCalculator,
)
from scripts.diagnostics.mtf_components import (
    MTFComponentTester,
    TelegramChecker,
    ErrorRecoveryChecker,
)
from scripts.diagnostics.lifecycle_test import (
    PostTradeLifecycleTest,
    OnBarMTFRoutingTest,
)
from scripts.diagnostics.architecture_verify import (
    TradingAgentsArchitectureVerifier,
    DiagnosticSummaryBox,
)
from scripts.diagnostics.summary import (
    DataFlowSummary,
    DeepAnalysis,
    MachineReadableSummary,
)
from scripts.diagnostics.service_health import (
    ServiceHealthCheck,
    APIHealthCheck,
    TradingStateCheck,
    SignalHistoryCheck,
)
from scripts.diagnostics.order_flow_simulation import (
    OrderFlowSimulator,
    ReversalStateSimulator,
    BracketOrderFlowSimulator,
)
from scripts.diagnostics.math_verification import (
    MathVerificationChecker,
)
# v49.0: upgrade_v2_verify deleted (AI-era diagnostics removed)


def main():
    """Main entry point for the diagnostic tool."""
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description='实盘信号诊断工具 v27.0 (TradingAgents + Feature-Driven + Per-Layer + Reflection + Config-Driven + Trailing Stop)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 scripts/diagnose_realtime.py              # Full diagnosis
  python3 scripts/diagnose_realtime.py --summary    # Quick summary only
  python3 scripts/diagnose_realtime.py --export     # Export to logs/
  python3 scripts/diagnose_realtime.py --push       # Export and push to GitHub
  python3 scripts/diagnose_realtime.py --telegram   # Export + send summary to Telegram

  # Background run (disconnect SSH without interruption):
  nohup python3 scripts/diagnose_realtime.py --export --push --telegram > /dev/null 2>&1 &
        """
    )
    parser.add_argument(
        '--summary',
        action='store_true',
        help='仅显示关键结果，跳过详细分析'
    )
    parser.add_argument(
        '--export',
        action='store_true',
        help='导出诊断结果到文件 (logs/diagnosis_YYYYMMDD_HHMMSS.txt)'
    )
    parser.add_argument(
        '--push',
        action='store_true',
        help='导出并推送到 GitHub (默认推到 main 分支)'
    )
    parser.add_argument(
        '--push-branch',
        default='main',
        help='推送目标分支 (default: main)'
    )
    parser.add_argument(
        '--telegram',
        action='store_true',
        help='诊断完成后发送摘要到 Telegram 私聊 (配合 nohup 实现断开 SSH 不中断)'
    )
    parser.add_argument(
        '--env',
        default='production',
        choices=['production', 'development', 'backtest'],
        help='运行环境 (default: production)'
    )
    args = parser.parse_args()

    # --push implies --export; --telegram implies --export
    export_mode = args.export or args.push or args.telegram

    # Create diagnostic runner
    runner = DiagnosticRunner(
        env=args.env,
        summary_mode=args.summary,
        export_mode=export_mode,
        push_to_github=args.push,
        push_branch=args.push_branch,
        send_telegram=args.telegram,
    )

    # ── Phase 0: Service Health ──
    runner.add_step(ServiceHealthCheck)         # systemd/memory/logs
    runner.add_step(APIHealthCheck)             # API 响应时间

    # ── Phase 1: v24.0 Code Integrity (静态分析, P1.0-P1.110) ──
    runner.add_step(CodeIntegrityChecker)       # v24.0 代码完整性检查 (含 P1.100-P1.110: trailing stop + 补充回归守卫)

    # ── Phase 1.5: v2.0 Upgrade Plan Phase 1 Verification ──
    # v49.0: UpgradeV2 checks removed (upgrade_v2_verify.py deleted)

    # ── Phase 2: Configuration ──
    runner.add_step(CriticalConfigChecker)      # 关键配置
    runner.add_step(MTFConfigChecker)           # MTF 配置
    runner.add_step(StrategyConfigLoader)       # 策略配置加载

    # ── Phase 3: Market Data (mirrors on_timer) ──
    runner.add_step(MarketDataFetcher)          # K线数据
    runner.add_step(SentimentDataFetcher)       # 情绪数据

    # ── Phase 4: Technical Indicators ──
    runner.add_step(IndicatorInitializer)       # 指标管理器初始化
    runner.add_step(TechnicalDataFetcher)       # 技术指标数据
    runner.add_step(PriceDataBuilder)           # 价格数据构建

    # ── Phase 5: Position & Account + v5.10+ Memory + v12.0 Reflection + v18.0 Extended Reflection + v6.2 Naked Position ──
    runner.add_step(PositionChecker)            # Binance 持仓
    runner.add_step(NakedPositionScanner)       # v6.2 裸仓检测 (持仓 vs 挂单 SL)
    runner.add_step(MemorySystemChecker)        # v5.10+ 记忆系统 + v12.0 反思 + v18.0 Extended Reflection
    runner.add_step(TradingStateCheck)          # 交易暂停状态

    # ── Phase 6: AI Input Validation (13 categories, external via AIDataAssembler SSoT) ──
    runner.add_step(AIInputDataValidator)       # 验证传给 AI 的 13 类数据 (含 v18 4H CVD, 外部通过 AIDataAssembler)

    # ── Phase 7: AI Decision (5~7 sequential DeepSeek calls, v32.1) ──
    # Bull R1 → Bear R1 → Bull R2 → Bear R2 → Judge → Entry Timing (0~1) → Risk Manager (0~1)
    runner.add_step(MultiAgentAnalyzer)         # 运行完整 AI 分析 (含 v23.0 Entry Timing + v32.1 Risk skip)
    runner.add_step(EntryTimingStandaloneTest)  # Entry Timing 独立验证 (强制测试, 1 extra API call)
    runner.add_step(RiskManagerStandaloneTest)  # Risk Manager 独立验证 (强制测试, 1 extra API call)
    runner.add_step(SignalProcessor)            # 完整实盘 pipeline (Risk Controller + Entry Timing + Signal Age)

    # ── Phase 8: Architecture Verification (v7.2 per-layer + DataAssembler + v12.0 Reflection + v14.0 Telegram + v7.3 SL + v18.2 + v19.1 + v24.0 Trailing) ──
    runner.add_step(TradingAgentsArchitectureVerifier)  # 数据完整性 + v12.0 反思 + v14.0 双频道 + v7.3 SL + v18.2 + v19.1 + v24.0 Trailing Stop
    runner.add_step(DiagnosticSummaryBox)       # 诊断总结

    # ── Phase 9: MTF + Telegram (含 v14.0 双频道) + Error Recovery ──
    runner.add_step(MTFComponentTester)         # MTF 组件
    runner.add_step(TelegramChecker)            # Telegram 配置
    runner.add_step(ErrorRecoveryChecker)       # 错误恢复机制

    # ── Phase 10: Order Flow Simulation (15 scenarios, v7.2 per-layer + v24.0 trailing) ──
    runner.add_step(PostTradeLifecycleTest)     # OCO + S/R reevaluation
    runner.add_step(OnBarMTFRoutingTest)        # on_bar MTF 路由
    runner.add_step(OrderSimulator)             # Bracket 订单模拟
    runner.add_step(PositionCalculator)         # 仓位计算
    runner.add_step(OrderFlowSimulator)         # 完整订单流程 (15 场景, 含 v24.0 Trailing Stop)
    runner.add_step(ReversalStateSimulator)     # 反转状态机
    runner.add_step(BracketOrderFlowSimulator)  # Bracket 订单流程

    # ── Phase 11: v24.0 Math Verification (R/R, SL, Threshold, Counter-Trend, Extension Ratio, Trailing Stop) ──
    runner.add_step(MathVerificationChecker)    # v24.0 数学验证 (含 M14 Extension Ratio + M15 Volatility Regime + M16 Trailing Stop)

    # ── Phase 12: Summary + JSON Output ──
    runner.add_step(DataFlowSummary)            # 数据流汇总
    runner.add_step(DeepAnalysis)               # 深度分析
    runner.add_step(SignalHistoryCheck)          # 历史信号追踪
    runner.add_step(MachineReadableSummary)      # v6.0 机器可读 JSON 输出

    # Run all diagnostic steps
    success = runner.run_all()

    # Export results if requested
    if export_mode:
        runner.export_results()

    # Return exit code
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
