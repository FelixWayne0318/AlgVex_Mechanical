# AlgVex - NautilusTrader 加密货币交易系统

## 项目概述
基于 NautilusTrader 框架的加密货币交易系统。**当前生产模式: Mechanical (v46.0)**，使用 3 维预判评分 (Structure/Divergence/Order Flow) + 纯规则决策，零 AI API 调用。AI 多代理辩论模式已在 Phase 3 清理中移除。

### 双系统架构 (Mechanical + SRP)

系统包含两个**完全独立**的交易策略，均基于 NautilusTrader，互不干扰：

```
nautilus-trader.service (Mechanical 量化交易)   nautilus-srp.service (SRP 策略)
├── main_live.py --strategy ai --mode mechanical ├── main_live.py --strategy srp
├── strategy/ (多代理 mixin 架构)                ├── srp_strategy/srp_strategy.py
├── agents/ (mechanical_decide + scoring)        ├── docs/SRP_strategy_v5.pine (SSoT)
├── API: BINANCE_API_KEY                         ├── API: SRP_BINANCE_API_KEY
├── 交易所: 币安账户 A                           ├── 交易所: 币安账户 B
└── 日志: journalctl -u nautilus-trader          └── 日志: journalctl -u nautilus-srp
```

| 维度 | Mechanical 量化交易 | SRP 策略 |
|------|-----------|---------|
| **策略类型** | 3 维预判评分 + 规则决策 (零 AI) | VWMA+RSI-MFI 通道 + DCA |
| **服务名** | `nautilus-trader` | `nautilus-srp` |
| **启动命令** | `main_live.py --strategy ai --mode mechanical --env production` | `main_live.py --strategy srp --env production` |
| **API Key** | `BINANCE_API_KEY` | `SRP_BINANCE_API_KEY` |
| **配置** | `configs/base.yaml` 主体 | `configs/base.yaml` → `srp:` 段落 |
| **策略代码** | `strategy/*.py` (5 mixin) + `agents/mechanical_decide.py` | `srp_strategy/srp_strategy.py` (单文件) |
| **Pine 源码** | 无 | `docs/SRP_strategy_v5.pine` (SSoT) |
| **Parity 工具** | 无 | `scripts/pine_tv_comparator.py` |

**隔离保证**:
- 不同 API key → 不同交易所账户 → 仓位互不影响
- 不同 systemd 进程 → 各自独立运行
- `main_live.py` 中 AI 走 `get_strategy_config()` (L108)，SRP 走 `get_srp_strategy_config()` (L407)，代码路径完全分离

### SRP 策略 (v5.0) 文件说明

| 文件 | 行数 | 用途 |
|------|------|------|
| `docs/SRP_strategy_v5.pine` | 351 | Pine Script 源码 (TradingView)，策略逻辑的 SSoT |
| `srp_strategy/srp_strategy.py` | 600 | NautilusTrader 实现，100% Pine v5.0 parity |
| `scripts/backtest_srp_v5_exact.py` | ~400 | Python 回测引擎 (Pine 精确复制) |
| `scripts/pine_tv_comparator.py` | ~470 | Pine broker 模拟器 + parity 对比工具 |
| `scripts/optimize_srp_params.py` | ~270 | 三阶段参数优化器 |
| `scripts/walk_forward_srp.py` | ~370 | Walk-Forward 过拟合验证 (SQN 评分) |
| `nautilus-srp.service` | 32 | systemd 服务文件 |
| `configs/base.yaml` → `srp:` | ~40 | SRP 策略参数配置 |

### SRP 策略修改规范

1. **Pine 是 SSoT**: 修改策略逻辑时先改 `docs/SRP_strategy_v5.pine`
2. **同步 NT 版本**: 将 Pine 改动同步到 `srp_strategy/srp_strategy.py`
3. **验证 Parity**: 运行 `python3 scripts/pine_tv_comparator.py --days 456` 确认 PERFECT PARITY
4. **参数优化**: `python3 scripts/optimize_srp_params.py --days 456 --capital 1500`
5. **过拟合检测**: `python3 scripts/walk_forward_srp.py --days 456 --capital 1500`

### 代码库规模

| 模块 | 行数 | 文件数 | 核心职责 |
|------|------|--------|---------|
| strategy/ | ~14,000 | 7 | 策略主体 (mixin 架构) |
| agents/ | ~4,500 | 5 | 机械决策系统 (v46.0) |
| utils/ | ~14,000 | 30 | 工具/客户端/数据聚合/风控 |
| scripts/ | ~35,000 | 26 | 诊断/回归/校准/回测/压力测试 |
| indicators/ | 1,420 | 3 | 技术指标计算 |
| tests/ | ~6,500 | 22 | 单元/集成/回归测试 |
| web/backend/ | 5,515 | 24 | FastAPI Web 管理 |
| patches/ | 481 | 3 | NT 兼容性补丁 |
| **总计 Python** | **~80,000** | **~125** | Phase 3 清理后 |

## 输出语言规范 (Output Language)

**默认**: 中英文混合输出。中文为主体语言，技术术语、代码相关内容保留英文。

**适用范围**: 本规范适用于**所有**面向用户的输出，包括但不限于：
- Claude 对话回复
- Telegram 消息 (交易信号、心跳、诊断摘要、命令响应等)
- Web 界面文本
- 脚本终端输出 (诊断工具、校准工具等)
- 日报/周报

| 场景 | 语言 | 示例 |
|------|------|------|
| 日常对话、解释、总结 | 中文 | "这个 bug 的根因是..." |
| 技术术语 | 英文原文 | R/R ratio, SL/TP, WebSocket, JWT, CRUD |
| 代码标识符 | 英文原文 | `calculate_mechanical_sltp()`, `on_timer` |
| 文件名/路径 | 英文原文 | `strategy/ai_strategy.py` |
| 命令行指令 | 英文原文 | `git pull origin main` |
| 代码注释 | 英文 | 代码中的注释保持英文 |
| Commit message | 英文 | Git 提交信息用英文 |
| CLAUDE.md 文档 | 中英混合 | 同本文档风格 |
| Telegram 消息标签 | 中文+英文 | "信号: 观望 (HOLD)", "检查: 110/110 通过" |
| Telegram 方向显示 | 中文 (via `side_to_cn()`) | 开多/开空/平多/平空/多仓/空仓 |
| 诊断摘要 | 中英混合 | "实时诊断 Realtime Diagnosis" |

## 关键信息

| 项目 | 值 |
|------|-----|
| **入口文件** | `main_live.py` (不是 main.py!) |
| **服务器 IP** | 139.180.157.152 |
| **用户名** | linuxuser |
| **安装路径** | /home/linuxuser/nautilus_AlgVex |
| **服务名** | nautilus-trader |
| **分支** | main |
| **Python** | 3.12+ (必须) |
| **NautilusTrader** | 1.224.0 |
| **配置文件** | ~/.env.algvex (永久存储) |
| **记忆文件** | data/trading_memory.json |

## 奥卡姆剃刀原则 (Occam's Razor)

**核心**: 如无必要，勿增实体。代码库只保留一套当前生效的系统，不保留"万一以后用到"的废弃代码。

| 规则 | 说明 |
|------|------|
| **一套系统** | 每个功能只有一种实现路径。不保留旧版 fallback、废弃分支、"备用方案" |
| **删除 > 注释** | 废弃代码直接删除，不注释保留。Git 历史可追溯 |
| **配置最小化** | 不保留 `enabled: false` 的废弃功能配置块。当前不用 = 删除 |
| **文档跟随代码** | 描述已删除功能的文档同步删除。设计文档在实现完成后归档或删除 |
| **无预防性抽象** | 不为假设的未来需求创建接口/抽象。三行重复代码优于一个过早抽象 |
| **单一真相源** | 同一逻辑不在多处重复。如需共享，提取为函数；否则只保留一处 |

**检查清单** (每次修改后自问):
1. 这段代码当前是否被生产路径调用？否 → 删除
2. 这个配置项当前是否影响系统行为？否 → 删除
3. 这个文档描述的是当前系统还是历史系统？历史 → 删除
4. 这个 fallback 路径在正常运行中是否可能触发？否 → 删除

## 零截断原则

- Telegram 超长消息: `_split_message()` 自动按 4096 字符硬限制分片发送，零丢弃
- Web API: 后端返回完整数据，前端自行处理显示长度
- 代码审查: `[:N]` 字符串切片必须说明理由。数组索引 (`list[:5]`) 和协议硬限制 (Telegram 4096) 除外

## 代码修改规范 (必读)

在修改任何代码之前，**必须**按以下顺序调研：

1. **官方文档** - NautilusTrader、python-telegram-bot 等框架的官方文档
2. **社区/GitHub Issues** - 查看是否有相关问题和解决方案
3. **原始仓库** - 对比 [Patrick-code-Bot/nautilus_AItrader](https://github.com/Patrick-code-Bot/nautilus_AItrader) 的实现
4. **提出方案** - 基于以上调研，结合当前系统问题，提出合理修改方案

**禁止**：
- 凭猜测直接修改代码
- 未经调研就"优化"或"改进"代码
- 忽略原始仓库的已验证实现
- 不了解框架线程模型就修改异步/多线程代码

**修改后必须运行**：
```bash
python3 scripts/smart_commit_analyzer.py
# 预期: 所有规则验证通过

python3 scripts/check_logic_sync.py
# 预期: All logic clones in sync
```

## SSoT 依赖表 (Single Source of Truth)

修改下列 SSoT 文件时，**必须**检查所有依赖方是否需要同步更新。`check_logic_sync.py` 会自动验证，但仍需人工确认语义一致性。

### 共享逻辑模块: `utils/shared_logic.py`

| 函数/常量 | 用途 | 导入方 |
|-----------|------|--------|
| `calculate_cvd_trend()` | CVD 趋势分类 | `utils/order_flow_processor.py`, `scripts/verify_indicators.py`, `scripts/validate_data_pipeline.py` |
| `classify_extension_regime()` | ATR Extension 级别分类 | `indicators/technical_manager.py`, `scripts/verify_extension_ratio.py` |
| `classify_volatility_regime()` | ATR Volatility Regime 分类 | `indicators/technical_manager.py`, `scripts/diagnostics/math_verification.py` |
| `VOLATILITY_REGIME_THRESHOLDS` | 30/70/90 百分位阈值常量 | 同上 |
| `EXTENSION_THRESHOLDS` | 2.0/3.0/5.0 阈值常量 | 同上 |
| `CVD_TREND_*` 常量 | CVD 计算参数 | 同上 |

### 其他 SSoT 文件及其依赖方

| SSoT 文件 | 关键逻辑 | 依赖方 (改源 → 必须检查) |
|-----------|---------|--------------------------|
| `strategy/trading_logic.py` | `calculate_mechanical_sltp()` | `utils/backtest_math.py` (standalone mirror) |
| `strategy/trading_logic.py` | `evaluate_trade()` (grade A+~F) | `web/backend/services/trade_evaluation_service.py` |
| `utils/backtest_math.py` | ATR Wilder's + SL/TP + SMA/BB | `scripts/calibrate_hold_probability.py`, `scripts/validate_production_sr.py`, `scripts/verify_indicators.py` |
| `utils/telegram_bot.py` | `side_to_cn()` | `telegram_command_handler.py` (x2 inline), strategy mixin files: `telegram_commands.py`, `position_manager.py`, `safety_manager.py` |
| `strategy/ai_strategy.py` + 5 mixin files | `_layer_orders` / `_next_layer_idx` | 7 处 `.clear()` 必须伴随 `_next_layer_idx = 0` (paired 检查覆盖全部 4 个策略文件) |
| `indicators/technical_manager.py` | SMA/EMA/RSI/ATR 计算 | `scripts/verify_indicators.py` (reference impl), `utils/backtest_math.py` (standalone ATR) |
| `agents/prompt_constants.py` | `FEATURE_SCHEMA` + `REASON_TAGS` | `tag_validator.py`, `report_formatter.py` |

### 自动检测工具

```bash
# 逻辑同步检查 (修改 SSoT 文件后必须运行)
python3 scripts/check_logic_sync.py          # 14 项检查
python3 scripts/check_logic_sync.py --verbose # 显示所有通过项

# Hook 自动警告 (已配置)
# .claude/hooks/warn-ssot-edit.sh — 编辑 SSoT 文件时自动提醒
```

### 新增逻辑副本检查清单

如果你发现一段逻辑需要在多处使用：
1. **优先**: 提取到 `utils/shared_logic.py`，其他位置 import
2. **次选**: 如果不能 import (如纯 Python 脚本)，在 `check_logic_sync.py` 的 `SYNC_REGISTRY` 中注册
3. **禁止**: 直接 copy-paste 且不注册同步检查

## 策略 Mixin 架构

`AITradingStrategy(Strategy)` 继承 NautilusTrader `Strategy`，通过 5 个 Mixin 组织代码：

```python
AITradingStrategy(Strategy)
    ├─ TelegramCommandsMixin   # /status, /close, /modify_sl 等 30+ 命令 (MRO 优先)
    ├─ EventHandlersMixin      # on_order_filled/rejected/canceled, on_position_opened/closed/changed
    ├─ OrderExecutionMixin     # _execute_trade, _open_new_position, _submit_bracket_order, trailing stops
    ├─ PositionManagerMixin    # _create_layer, time_barrier, cooldown, reflection, LIFO reduction
    └─ SafetyManagerMixin      # _submit_emergency_sl, _emergency_market_close, _tier2_recovery
```

### 核心生命周期

| 方法 | 触发 | 职责 |
|------|------|------|
| `on_start()` | 策略启动 | 恢复仓位状态、Tier 2 recovery、backfill trailing |
| `on_timer()` | 每 20 分钟 | 主决策循环 (数据聚合 → 预判评分 → 规则决策 → 执行) |
| `on_bar()` | 每 Bar 完成 | 路由到 MTF manager (1D/4H/30M) |
| `on_trade_tick()` | 每笔成交 | 实时价格监控 + 1.5% surge trigger |
| `on_position_closed()` | 仓位关闭 | 评估 (grade)、记忆、强制分析 |
| `on_stop()` | 策略停止 | 保留 SL/TP (不 cancel_all_orders) |

### 层级订单系统 (v7.2)

```
_layer_orders: Dict[str, Dict]    # layer_id → {entry_price, sl_price, tp_price, quantity, sl_order_id, tp_order_id, ...}
_order_to_layer: Dict[str, str]   # order_id → layer_id (反查)
```

- 每层独立 SL/TP，加仓不影响已有层
- LIFO 减仓 (最新层先平)
- `data/layer_orders.json` 持久化重启恢复
- Tier 2 startup: 交叉验证每层 SL 是否在交易所存活

## Mechanical Anticipatory 决策架构 (v46.0)

### 决策流程 (零 AI 调用, <1 秒延迟)

```
on_timer (20分钟)
  ↓
内联数据聚合 (13 类数据)
  ↓
extract_features() → 141 个 typed features
  ↓
compute_anticipatory_scores() — 3 维预判评分
  ├─ Structure: Extension EXTREME/OVEREXTENDED + S/R proximity
  ├─ Divergence: RSI/MACD/OBV 4H+30M 背离检测
  ├─ Order Flow: CVD-Price cross + OI+Price + 清算 + FR + Top Traders
  ├─ Regime 检测 (TRENDING/RANGING/MEAN_REVERSION/VOLATILE/DEFAULT)
  ├─ 动态权重提升 + BB Squeeze 放大 + Confluence damping
  └─ 输出: net_raw (-1.0~+1.0) + 3 维 scores + regime + trend_context
  ↓
mechanical_decide() — 纯规则决策 (~40 行核心)
  ├─ net_raw → regime threshold bands → signal + confidence
  ├─ 后置修正: 趋势末期 cap / 逆势降级 / 方向锁定
  └─ 仓位定价: confidence × trend_context × risk_env
  ↓
calculate_mechanical_sltp() → ATR × confidence 构造性保证 R/R >= 1.5:1
  ↓
22 个 Gate Checks → 订单执行
```

### 三层时间框架 (MTF)

| 层级 | 时间框架 | 职责 |
|------|---------|------|
| 趋势层 | 1D | SMA_200 + MACD，Risk-On/Off 过滤 |
| 决策层 | 4H | 预判评分 + 规则决策 |
| 执行层 | 30M | RSI 入场时机 + S/R 止损止盈 |

### 13 类数据覆盖

| # | 数据 | 必需 | 来源 |
|---|------|------|------|
| 1 | technical_data (30M) | Y | IndicatorManager (含 ATR Extension Ratio) |
| 2 | sentiment_data | Y | Binance 多空比 |
| 3 | price_data | Y | Binance ticker |
| 4 | order_flow_report | | BinanceKlineClient |
| 5 | derivatives_report (Coinalyze) | | CoinalyzeClient |
| 6 | binance_derivatives (Top Traders) | | BinanceDerivativesClient |
| 7 | orderbook_report | | BinanceOrderbookClient |
| 8 | mtf_decision_layer (4H) | | 技术指标 |
| 9 | mtf_trend_layer (1D) | | 技术指标 |
| 10 | current_position | | Binance |
| 11 | account_context | Y | Binance |
| 12 | historical_context | | 内部计算 |
| 13 | sr_zones_data | | S/R 计算器 |

### 记忆系统 (v46.0 mechanical)

**文件**: `data/trading_memory.json` (最多 500 条)

**数据流**:
```
on_position_closed → evaluate_trade() → record_outcome() → trading_memory.json
                                                                 ↓
                                                      模板 lesson (无 LLM)
                                                                 ↓
                                                Web API / Telegram 报告
```

v46.0 mechanical 模式使用 `_generate_lesson()` 模板生成 lesson (grade + PnL)，不调用 LLM 反思。

### Feature Pipeline (v46.0 mechanical)

```
13 类原始数据
  ↓
extract_features() → 141 typed features (FEATURE_SCHEMA)
  ↓
compute_anticipatory_scores() → 3 维预判评分 + net_raw
  ├─ structure:    Extension EXTREME/OVEREXTENDED + S/R proximity
  ├─ divergence:   RSI/MACD/OBV 4H+30M 背离
  ├─ order_flow:   CVD-Price cross + OI + 清算 + FR + Top Traders
  ├─ risk_env:     volatility regime + 综合风险评分
  └─ net_raw:      加权合成 (-1.0~+1.0) + confluence damping
  ↓
mechanical_decide() → signal + confidence + size_pct
  ↓
Feature snapshot 保存 → data/feature_snapshots/ (每周期)
```

### 交易评估框架

每笔交易平仓后自动评估 (`trading_logic.py:evaluate_trade()`):

| 等级 | 盈利交易 | 亏损交易 |
|------|---------|---------|
| A+ | R/R >= 2.5 | -- |
| A | R/R >= 1.5 | -- |
| B | R/R >= 1.0 | -- |
| C | R/R < 1.0 (小盈利) | -- |
| D | -- | 亏损 <= 计划 SL x 1.2 (有纪律) |
| F | -- | 亏损 > 计划 SL x 1.2 (失控) |

**Web 集成**: `TradeEvaluationService` 读取同一文件，提供:
- 公开 API: `/api/public/trade-evaluation/summary`, `/api/public/trade-evaluation/recent`
- 管理 API: `/api/admin/trade-evaluation/full`, `/api/admin/trade-evaluation/export`

### 核心架构决策 (仍生效)

| 版本 | 决策 | 说明 |
|------|------|------|
| v3.17 | R/R 驱动入场 | `calculate_mechanical_sltp()` 构造性保证 R/R >= 1.5:1 (ATR x confidence multiplier) |
| v3.18 | 订单流程安全 | 反转两阶段提交、Bracket 失败不回退 |
| v4.13 | 分步订单提交 | entry → on_position_opened → SL + TP 单独提交 (NT 1.222.0+) |
| v4.17 | LIMIT 入场 | LIMIT @ validated entry_price 取代 MARKET，R/R 永不低于验证值 |
| v5.12 | 逆势 R/R 提升 | 逆势交易 R/R >= 1.69:1 (x1.3)，补偿较低胜率 |
| v6.1 | Emergency SL 市价兜底 | SL 提交失败 → `_emergency_market_close()` 市价 reduce_only 平仓 |
| v6.6 | TP position-linked | `order_factory.limit_if_touched()` (TAKE_PROFIT)，仓位平仓后币安自动取消 |
| v6.7 | 9 项逻辑审计修复 | TP 重启恢复、数据质量门控、入场 FR 检查、过期仓位检测等 |
| v7.0 | 外部数据统一 (SSoT) | `AIDataAssembler.fetch_external_data()` 统一数据聚合 |
| v7.1 | 仓位上限钳制 | `min(position_usdt, max_usdt)` 保护 + emergency 重试+升级 + ConfigManager R/R 验证 |
| v7.2 | 每层独立 SL/TP | `_layer_orders` 追踪每层，LIFO 减仓，`data/layer_orders.json` 持久化 |
| v7.3 | 重启 SL 交叉验证 | Tier 2 恢复后验证每层 SL 存活，`on_stop()` 保留所有 SL/TP |
| v11.0 | S/R 纯信息化 | S/R zones 仅作为上下文信息，不机械锚定 SL/TP |
| v13.1 | 平仓失败 → Emergency SL | `_cmd_close`/`_cmd_partial_close` 平仓失败立即 `_submit_emergency_sl()` |
| v14.0 | Telegram 双频道 | 控制机器人(私聊) + 通知频道(订阅者)，每条消息只发一个地方 |
| v16.0 | S/R Hold Probability 校准 | `calibrate_hold_probability.py` 每周 cron 自动校准 |
| v17.0 | S/R 简化为 1+1 | 输出仅保留 nearest 1 support + 1 resistance |
| v17.1 | 清算缓冲双层保护 | `_execute_trade()` 硬地板: buffer<5% 阻止加仓 |
| v18.2 | 执行层 15M→30M | 减少噪音。Ghost position 3 次 -2022 后强制清除。Price Surge Trigger |
| v18.3 | Post-Close 积极分析期 | `_force_analysis_cycles_remaining = 2` 强制额外 2 轮分析 |
| v19.1 | ATR Extension Ratio | `(Price-SMA)/ATR` 4 级 regime (NORMAL/EXTENDED/OVEREXTENDED/EXTREME)，领域知识常量 |
| v19.1 | RSI/MACD/OBV 背离预计算 | `_detect_divergences()` 预计算，4H 主要 + 30M 辅助 |
| v19.1 | CVD-Price 交叉分析 | ACCUMULATION/DISTRIBUTION/CONFIRMED/ABSORPTION 自动标注 |
| v19.1.1 | Extension 趋势感知 | ADX>40 强趋势中 OVEREXTENDED 降权，仅 EXTREME 保留完整警告 |
| v20.0 | ATR Volatility Regime | ATR% 百分位分级 (LOW/NORMAL/HIGH/EXTREME)，共享逻辑在 `shared_logic.py` |
| v20.0 | OBV 背离检测 | EMA(20) 平滑后 OBV，与 CVD 互补 (宏观 vs 微观) |
| v21.0 | FR Consecutive Block Counter | >= 3 次同方向 FR 阻止 → 降级 HOLD，打破死循环 |
| v21.0 | 1D Historical Context | 10-bar 1D 时序数据注入，用于趋势衰竭检测 |
| v36.0 | Feature Pipeline Parity | `extract_features()` 多处数据源修复 + tag annotation 全覆盖 |
| v36.2 | Three-State ADX Direction | `adx_direction_1d` 三态 BULLISH/BEARISH/NEUTRAL |
| v36.3 | Ghost/TP/Orphan Guards | Ghost flag 清零修复、TP retry、orphan 清理双重防护 |
| v36.4 | In-Session TP Recovery | `_check_tp_coverage()` 每个 on_timer 周期自动恢复缺失 TP |
| v37.1 | SL/TP 参数 Plan II | SL: HIGH=1.8/MED=2.2 (4H ATR)，TP: HIGH=2.0/MED=1.8 |
| v38.1 | LOW Confidence 放行 | 30% 小仓位放行 LOW 信号积累数据 |
| v38.2 | MACD Histogram Enum Fix | EXPANDING/CONTRACTING/FLAT 匹配 `_classify_abs_trend()` 输出 |
| v39.0 | 4H ATR SL/TP | 优先 4H ATR (30M fallback)，multiplier 4H 尺度。ATR+multiplier 耦合，回滚必须原子 |
| v39.0 | 趋势衰竭反转检测 | 5 条件组合 >= 3 触发，trend_score 减 3。market_regime 用 max(1D,4H) ADX |
| v40.0 | 指标分类加权 | 信息密度加权 (CVD-Price 2.0 vs buy_ratio 0.5)，TRANSITIONING regime 2-cycle hysteresis |
| v40.0 | 背离独立处理 | 背离移出 momentum 投票，作为 trend_score 修正因子 |
| v42.1 | Close Reason 4-way | TRAILING_STOP_MARKET/STOP_MARKET/LIMIT_IF_TOUCHED/其他 |
| v42.1 | Reduce Guard | 部分减仓 resubmit 失败时恢复原值 + emergency SL 兜底 |
| v43.0 | Trailing Stop 4H ATR | trailing 迁移至 4H ATR，multiplier 0.6，activation 1.5R。ATR+multiplier 耦合 |
| v44.0 | TP R/R 统一 1.5 | 全级别 tp_rr_target=1.5，逆势仍 1.69 |
| v45.0 | Structure + Order Flow 预判 | Extension EXTREME 作方向性均值回归信号，OI/清算/FR/Top Traders 升级为方向性投票 |
| v46.0 | Mechanical 模式 | 删除全部 AI 组件，3 维预判 + 纯规则决策，零 API 调用，100% 确定性 |
| v47.0 | Structure 维度再平衡 | 1D EXTREME weight 3.0→1.5，4H Extension 新增投票，MEAN_REVERSION 跳过 boost |

### 技术指标一览

| # | 指标 | 周期 | 用途 | 首次版本 |
|---|------|------|------|---------|
| 1 | SMA | 5, 20, 50, 200 | 趋势跟踪、交叉检测 | v1.0 |
| 2 | EMA | 12, 26 | MACD 参考 | v1.0 |
| 3 | RSI (Wilder's) | 14 | 超买超卖、背离检测 | v1.0 |
| 4 | MACD | 12/26/9 | 趋势+动量背离 | v1.0 |
| 5 | Bollinger Bands | 20, 2s | Squeeze 检测、价格极端 | v1.0 |
| 6 | ADX / +DI / -DI | 14 | 趋势强度 (RANGING/WEAK/STRONG/VERY_STRONG) | v14.2 |
| 7 | ATR (Wilder's) | 14 | 波动率、SL/TP 距离 | v6.5 |
| 8 | Volume MA | 20 | 成交量比率 | v1.0 |
| 9 | ATR Extension Ratio | Per SMA | `(Price-SMA)/ATR` 价格偏离度 | v19.1 |
| 10 | ATR Volatility Regime | 90-bar lookback | ATR% 百分位 (LOW/NORMAL/HIGH/EXTREME) | v20.0 |
| 11 | OBV | Running sum + EMA(20) | 宏观成交量积累/派发 | v20.0 |

### 仓位计算方法

Mechanical mode 使用 DCA `base_order_pct` sizing (参见 `anticipatory.dca` config in `configs/base.yaml`)。

| 方法 | 说明 | 公式 |
|------|------|------|
| `atr_based` | 纯 ATR | `dollar_risk / (ATR x mult / price)` |
| `fixed_pct` | 固定百分比 (legacy) | `base_usdt x conf_mult x trend_mult x rsi_mult` |

### Trailing Stop (v24.0-v43.0)

Binance 原生 `TRAILING_STOP_MARKET` (服务端追踪止损)：
- **激活**: 入场 + 1.5R 利润时激活
- **回调率**: `0.6 x 4H_ATR / entry_price`，钳制到 [10, 1000] bps (0.1%-10%)
- **v24.2**: 已有仓位层的 trailing 自动回补 (`_backfill_trailing_for_existing_layers`)
- 与固定 SL 并存，trailing 保护利润，固定 SL 保护本金
- ATR source (4H) 与 multiplier (0.6) 耦合设计 — 回滚必须原子 revert

## 配置管理

### 分层架构

```
Layer 1: 代码常量 (业务规则，不可配置)
Layer 2: configs/base.yaml (所有业务参数)
Layer 3: configs/{env}.yaml (环境覆盖: production/development/backtest)
Layer 4: ~/.env.algvex (仅 API keys 等敏感信息)
```

| 数据类型 | 正确来源 | 错误做法 |
|---------|---------|---------|
| **敏感信息** (API keys) | `~/.env.algvex` | 写在代码或 YAML 中 |
| **业务参数** (止损比例等) | `configs/*.yaml` | 环境变量或代码硬编码 |
| **环境差异** (日志级别等) | `configs/{env}.yaml` | 在代码中 if/else 判断 |

### ConfigManager 使用

```python
from utils.config_manager import ConfigManager
config = ConfigManager(env='production')
config.load()
```

### 命令行环境切换

```bash
python3 main_live.py --env production    # 生产 (20分钟, INFO)
python3 main_live.py --env development   # 开发 (1分钟, DEBUG)
python3 main_live.py --env backtest      # 回测 (无Telegram)
python3 main_live.py --env development --dry-run  # 验证配置
```

### 环境变量 (~/.env.algvex)

```bash
# ===== 仅敏感信息 =====
BINANCE_API_KEY=xxx
BINANCE_API_SECRET=xxx
TELEGRAM_BOT_TOKEN=xxx
TELEGRAM_CHAT_ID=xxx
# v14.0: 通知频道机器人 (独立 bot，可选。交易信号+业绩专用)
TELEGRAM_NOTIFICATION_BOT_TOKEN=xxx   # 通知频道机器人 token
TELEGRAM_NOTIFICATION_CHAT_ID=xxx     # 通知频道 chat_id
COINALYZE_API_KEY=xxx          # 可选，无则自动降级
# 禁止放业务参数 (EQUITY, LEVERAGE 等应在 configs/*.yaml)
```

### 关键策略参数 (configs/base.yaml)

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `strategy_mode` | mechanical | 策略模式: mechanical (v46.0 预判评分) |
| `max_position_ratio` | 0.12 | 最大仓位比例 (10x杠杆下控制总仓位) |
| `min_confidence_to_trade` | LOW | 最低信心 |
| `trading_logic.min_rr_ratio` | 1.3 | R/R 硬性门槛 (tp_rr_target 统一为 1.5，min_rr 保持 1.3 作为绝对底线) |
| `trading_logic.counter_trend_rr_multiplier` | 1.3 | 逆势 R/R 倍数 (1.3x1.3=1.69) |
| `anticipatory.regime_config` | Optuna 产出 | 5 Regime 维度权重 + 信号阈值 (TRENDING/RANGING/MEAN_REVERSION/VOLATILE/DEFAULT) |
| `timer_interval_sec` | 1800 (base) / 1200 (production) | 分析间隔 (秒)，生产环境 20 分钟 |
| `pyramiding.min_confidence` | MEDIUM | 加仓最低信心 (已有浮盈仓位风险可控) |
| `pyramiding.min_profit_atr` | 0.5 | 加仓最低浮盈 (0.5x ATR) |

完整参数列表参见 `configs/base.yaml`。

## 服务器操作铁律 (每次必须遵守)

**给 AI 助手的强制要求**：向用户提供服务器命令时，必须满足以下三点，缺一不可：

### 1. 每条命令都必须先 cd
```bash
# 错误 (会报 "not a git repository" 或 "No such file or directory")
git pull origin main

# 正确 (始终以 cd 开头)
cd /home/linuxuser/nautilus_AlgVex && git pull origin main
```

### 2. checkout 后必须 pull，再运行脚本
```bash
cd /home/linuxuser/nautilus_AlgVex && \
  git fetch origin <branch> && \
  git checkout <branch> && \
  git pull origin <branch> && \
  source venv/bin/activate && \
  python3 scripts/xxx.py
```

### 3. 提供给用户的命令必须是完整一行可直接粘贴的
```bash
cd /home/linuxuser/nautilus_AlgVex && git pull origin claude/<branch> && source venv/bin/activate && python3 scripts/xxx.py [args]
```

> **根本原因**：用户每次 SSH 登录后默认在 `/home/linuxuser`，不在项目目录。

---

## 常用命令

```bash
# 全面诊断
python3 scripts/diagnose.py              # 运行全部检查
python3 scripts/diagnose.py --quick      # 快速检查
python3 scripts/diagnose.py --update --restart  # 更新+重启

# 实时诊断 (调用真实 API)
python3 scripts/diagnose_realtime.py
python3 scripts/diagnose_realtime.py --summary   # 仅关键结果
python3 scripts/diagnose_realtime.py --export --push  # 导出+推送

# 回归检测 (代码修改后必须运行)
python3 scripts/smart_commit_analyzer.py

# Mechanical 模式诊断 (v46.0)
python3 scripts/diagnose_mechanical.py              # 10 阶段全链路 E2E 诊断
python3 scripts/diagnose_mechanical.py --quick      # 快速离线检查 (跳过 API)

# Mechanical 校准 (v46.0)
python3 scripts/generate_cold_start_snapshots.py --days 90 --clear  # 冷启动快照生成
python3 scripts/calibrate_anticipatory.py           # Optuna 预判信号校准

# S/R Hold Probability 校准 (v16.0)
python3 scripts/calibrate_hold_probability.py                  # 交互式
python3 scripts/calibrate_hold_probability.py --auto-calibrate # Cron 模式
python3 scripts/calibrate_hold_probability.py --dry-run        # 预览不保存

# 交易频率诊断
python3 scripts/diagnose_trade_frequency.py
python3 scripts/diagnose_trade_frequency.py --hours 48

# 回测
python3 scripts/backtest_from_logs.py                   # 生产级多层仓位回测 (v3.0)
python3 scripts/backtest_from_logs.py --days 30         # 自定义天数

# 仓位管理压力测试 (需先停止生产服务)
sudo systemctl stop nautilus-trader
python3 scripts/stress_test_position_management.py
sudo systemctl start nautilus-trader

# 服务器操作
sudo systemctl restart nautilus-trader
sudo journalctl -u nautilus-trader -f --no-hostname
```

### 服务器代码同步 (一行命令)

```bash
cd /home/linuxuser/nautilus_AlgVex && sudo systemctl stop nautilus-trader && git fetch origin main && git reset --hard origin/main && find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null && echo "=== 最近提交 ===" && git log --oneline -5 && source venv/bin/activate && python3 scripts/diagnose_realtime.py
```

## 部署/升级

```bash
# 一键清空重装
curl -fsSL https://raw.githubusercontent.com/FelixWayne0318/AlgVex/main/reinstall.sh | bash

# 普通升级
cd /home/linuxuser/nautilus_AlgVex && git pull origin main && chmod +x setup.sh && ./setup.sh

# systemd 服务
sudo cp nautilus-trader.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable nautilus-trader && sudo systemctl restart nautilus-trader
```

## Backtest & Stress Test Suite

### 回测工具链

| 脚本 | 用途 | 输出 |
|------|------|------|
| `backtest_from_logs.py` | 生产级多层仓位回测 (pyramiding + trailing stop 仿真, v3.0) | `data/backtest_from_logs_result.json` |
| `stress_test_position_management.py` | 仓位管理异常场景压力测试 (8 大类 30+ 子场景) | Console pass/fail report |

### backtest_from_logs.py v3.0 Production Simulator

生产级仿真器，完全镜像 live 系统行为：
- **多层仓位**: 同方向信号叠加层 (最多 7 层)，每层独立 SL/TP
- **Trailing Stop**: 1.5R 利润激活，0.6x 4H ATR callback，钳制 [10, 1000] bps (v43.0)
- **LIFO 减仓**: 最新层先平
- **费用仿真**: 0.075% x 2 (round-trip) + 0.03% SL slippage
- **Production Gates**: cooldown (40min) + CB (3 SL→4h, 2 SL→0.5x) + dedup + market change + daily loss (3%) + DD breaker (10%→REDUCED, 15%→HALT)

### 压力测试覆盖 (stress_test_position_management.py)

| 类别 | 场景数 | 覆盖内容 |
|------|--------|---------|
| A. Layer State Machine | 5+ | 层级创建/删除/持久化一致性 |
| B. Emergency Escalation | 5+ | SL 失败 → Emergency SL → Market Close |
| C. Ghost & Orphan | 4+ | 幽灵仓位检测 + 孤立订单清理竞态 |
| D. Restart Recovery | 3+ | Tier 1/2/3 重启恢复 |
| E. Risk Controller | 4+ | 熔断器状态转换 (ACTIVE→REDUCED→HALTED→COOLDOWN) |
| F. Manual Operations | 3+ | 用户在币安手动平仓/取消 SL |
| G. Network & API Errors | 4+ | Binance API 超时/拒绝/-2021/-2022 |
| H. Position Sizing Edge | 3+ | 极端仓位计算 (零余额/极小仓位/溢出) |

### 验证链 (推荐执行顺序)

```
1. backtest_from_logs.py                — 全仿真 (多层 + gates + trailing)
   ↓
2. stress_test_position_management.py   — 异常场景压力测试
```

## Telegram 显示术语规范

用户端 Telegram 消息**禁止**出现原始英文 LONG/SHORT/BUY/SELL，必须使用中国期货行业标准术语。

### 标准术语表

| 场景 | 标准中文 | 禁止使用 | `side_to_cn()` action |
|------|---------|-------------|----------------------|
| 开仓方向 | **开多** / **开空** | 做多、买入、LONG、BUY | `'open'` |
| 平仓方向 | **平多** / **平空** | 卖出、SELL、CLOSE LONG | `'close'` |
| 持仓状态 | **多仓** / **空仓** | LONG、SHORT、多头、空头 | `'position'` |
| 简短标签 | **多** / **空** | LONG、SHORT | `'side'` |

### 唯一入口: `TelegramBot.side_to_cn()`

```python
# utils/telegram_bot.py — 所有方向显示必须通过此方法
TelegramBot.side_to_cn(side, action)
# side:   'LONG' | 'SHORT' | 'BUY' | 'SELL'
# action: 'open' | 'close' | 'position' | 'side'
```

### 新增显示代码检查清单

修改或新增 Telegram 消息时，必须确认:
1. 方向显示是否通过 `side_to_cn()` 或遵循上表术语？
2. 是否有 `f"...{side}..."` 直接拼接原始英文 side 到用户消息？→ 改用 `side_to_cn()`
3. 区分"开仓 vs 平仓"时，是否正确判断？(用 `reduce_only` 或 `realizedPnl != 0`)

## 常见错误避免

### 基础规范
- 使用 `python` → **始终 `python3`**
- 使用 `main.py` → `main_live.py`
- 忘记 `AUTO_CONFIRM=true` → 会卡在确认提示
- Python 3.11 或更低 → 必须 3.12+ (NT 1.224.0 要求)
- 从后台线程访问 `indicator_manager` → 使用 `_cached_current_price` (Rust 不可跨线程)
- `nautilus_trader.core.nautilus_pyo3` 指标 → `nautilus_trader.indicators` (Cython 版本)
- `__init__.py` 自动导入 → 直接导入模块 (避免循环导入)
- `sentiment_data['key']` → `sentiment_data.get('key', default)` (防 KeyError)
- 环境变量存业务参数 → 业务参数只在 `configs/*.yaml`
- 服务器命令不带 cd → 始终先 `cd /home/linuxuser/nautilus_AlgVex`
- git checkout 后忘记 git pull → checkout 完必须 pull 再运行

### 订单执行
- `order_factory.bracket()` + `submit_order_list()` → 分步提交 (v4.13)
- TP 用 `order_factory.limit()` → `order_factory.limit_if_touched()` (TAKE_PROFIT, position-linked) (v6.6)
- Bracket 失败回退无保护单 → CRITICAL 告警 + HOLD (v3.18)
- 反转交易直接平仓后开仓 → `_pending_reversal` 两阶段提交 (v3.18)
- 非 STOP_MARKET 平仓单一律判为 TAKE_PROFIT → 4-way dispatch (v42.1)

### 层级订单
- 加仓影响已有层 SL/TP → 每层独立 SL/TP，加仓创建新层 (v7.2)
- 重启恢复信任 JSON 不验证交易所 → Tier 2 交叉验证 SL 是否存活 (v7.3)
- Emergency SL 不创建 layer 条目 → `_submit_emergency_sl` 调用 `_create_layer()` 持久化 (v7.3)
- `on_stop()` fallback `cancel_all_orders()` → 保留所有订单，SL/TP 保护优先 (v7.3)
- Telegram close 取消 SL 后平仓失败 → 立即 `_submit_emergency_sl()` 防裸仓 (v13.1)
- `_reduce_position()` 部分减仓时 layer quantity 在 resubmit 前更新 → resubmit 失败时恢复+emergency SL (v42.1)

### SL/TP/Trailing
- R/R 仅 prompt 要求 → `calculate_mechanical_sltp()` 构造性保证 R/R >= 1.5:1
- 逆势交易用同样 R/R 门槛 → 逆势自动提升至 1.69:1 (v5.12)
- Funding Rate 精度 4 位 → 5 位小数 `:.5f` / `round(..., 6)` (匹配 Binance)
- SL/TP 用 30M ATR → v39.0: 4H ATR 为主 (30M fallback)，ATR+multiplier 耦合回滚
- Trailing stop 用 30M ATR → v43.0: 4H ATR，multiplier 0.6，activation 1.5R，耦合回滚
- Trailing stop 回调率超出 Binance 范围 → 钳制到 [10, 1000] bps (v24.0)
- 重启后已有层没有 trailing → `_backfill_trailing_for_existing_layers()` 自动补回 (v24.2)
- TP 提交失败无重试 → 2 次尝试 + `_check_tp_coverage()` on_timer 自动恢复 (v36.3-v36.4)

### Ghost/Orphan
- Ghost position 一次检测就清除 → 双确认 (等 2 个周期防 API 抖动) (v24.1)
- Ghost `_ghost_first_seen` 永久残留 → `on_position_closed()` + `_clear_position_state()` 清零 (v36.3)
- `_cleanup_orphaned_orders()` 误删刚提交的 SL/TP → 时间窗口 guard (<120s) + layer 匹配 guard (v36.3)
- Ghost position loop 无限重试 → 3 次 -2022 rejection 后强制 `_clear_position_state()` (v18.2)

### Telegram 显示
- 显示原始 LONG/SHORT/BUY/SELL → `side_to_cn()` 转换为 开多/开空/平多/平空/多仓/空仓
- 显示 "多头/空头" → 持仓状态统一用 "多仓/空仓"
- CVD 信号显示原始 ACCUMULATION → 吸筹/派发/吸收/确认
- 心跳信号标签显示 HOLD/LONG/SHORT → 观望/开多/开空

### 技术指标/Feature
- Extension ratio 阈值放 base.yaml → 领域知识常量 (2/3/5 ATR)，不经 YAML 配置 (v19.1)
- Volatility Regime 阈值放 base.yaml → 领域知识常量 (30/70/90 百分位) (v20.0)
- 原始 OBV 做背离检测 → EMA(20) 平滑后降噪 (v20.0)
- OBV 单独作为交易信号 → 需 RSI/MACD/CVD confluence 确认 (v20.0)
- FR block counter 平仓时 reset → 平仓不 reset (FR 压力是市场条件非仓位状态)，仅成功开仓后 reset (v21.0)
- `adx_direction_1d` DI+ == DI- 产生虚假 BEARISH → 三态 BULLISH/BEARISH/NEUTRAL (v36.2)
- `compute_scores_from_features()` MACD histogram 用 RISING/FALLING → EXPANDING/CONTRACTING/FLAT (v38.2)
- 1D Extension EXTREME weight 过高永远触发 → v47.0 降为 1.5，4H Extension 新增投票可对冲

### 配置/校准
- 校准参数放 base.yaml → 代码常量 (LOOKBACK_BARS 等在脚本中)
- 手动编辑 `data/calibration/latest.json` → 运行 `calibrate_hold_probability.py` 或 `/calibrate`
- 遍历 `support_zones[1:]` → v17.0 后列表最多 1 元素，只用 `nearest_support`/`nearest_resistance`
- 执行层用 15M → v18.2 迁移至 30M
- 平仓后立即进入 skip-gate → `_force_analysis_cycles_remaining = 2` 强制额外 2 轮分析 (v18.3)

### Mechanical 模式特有
- FR 硬门控阻止 mechanical 入场 → `_is_mechanical` 跳过硬 return (v46.0)
- Confluence 显示 AI 5 层 key → 按 `_strategy_mode` 选择 mechanical 3 维度 (v46.0)
- 单信号满分 score=10 → Confluence damping 限制单信号最多 ~3 分 (v46.0)
- `sr_zones` 未传入 `mechanical_analyze()` → 已修复，S/R proximity 维度恢复 (v46.0)

## 文件结构

```
/home/user/AlgVex/
├── main_live.py              # 入口文件 (774 行)
├── setup.sh / reinstall.sh   # 部署脚本
├── requirements.txt          # Python 依赖 (NT 1.224.0, empyrical-reloaded 等)
├── nautilus-trader.service    # systemd 服务
│
├── strategy/                 # 策略模块 (mixin 架构, ~14,000 行)
│   ├── ai_strategy.py        # 主策略入口 + 核心循环 (~5,500 行)
│   ├── event_handlers.py     # 事件回调 mixin (on_order_*, on_position_*) + ghost/orphan guards (~2,100 行)
│   ├── order_execution.py    # 订单执行 mixin (_execute_trade, trailing stop) (~1,500 行)
│   ├── position_manager.py   # 仓位管理 mixin (层级订单, 加仓/减仓) (~1,800 行)
│   ├── safety_manager.py     # 安全管理 mixin (emergency SL, 孤立检测, TP 恢复) (~1,000 行)
│   ├── telegram_commands.py  # Telegram 命令 mixin (/close, /modify_sl 等) (~2,400 行)
│   └── trading_logic.py      # 交易逻辑 + evaluate_trade() 评估 (SSoT) (~1,400 行)
│
├── agents/                   # 机械决策系统 (~4,500 行)
│   ├── multi_agent_analyzer.py # mechanical_analyze() + record_outcome() + snapshot save (~370 行)
│   ├── mechanical_decide.py  # mechanical_decide() + 方向锁定 + 持久化 (223 行)
│   ├── prompt_constants.py   # FEATURE_SCHEMA + REASON_TAGS + _get_multiplier
│   ├── report_formatter.py   # extract_features() + compute_anticipatory_scores() + 3 维评分 (~2,000 行)
│   └── tag_validator.py      # REASON_TAGS 验证 + compute_valid_tags/annotated_tags (~1,100 行)
│
├── indicators/               # 技术指标 (1,420 行)
│   ├── technical_manager.py  # Cython 指标 + ATR Extension/Volatility Regime (~1,100 行)
│   └── multi_timeframe_manager.py # 三层 MTF 管理 (1D/4H/30M) (~300 行)
│
├── utils/                    # 工具模块 (~14,000 行)
│   ├── config_manager.py     # 统一配置管理器 (30+ 验证规则)
│   ├── ai_data_assembler.py  # 13 类数据聚合 (SSoT, v7.0)
│   ├── telegram_bot.py       # Telegram 双频道通知 (v14.0)
│   ├── telegram_command_handler.py # Telegram 命令 + PIN 验证 (v3.0)
│   ├── telegram_queue.py     # SQLite 持久化消息队列
│   ├── binance_kline_client.py       # K线 + 订单流 + CVD + FR
│   ├── binance_derivatives_client.py # Top Traders 多空比
│   ├── binance_orderbook_client.py   # 订单簿深度
│   ├── binance_account.py    # 账户工具 (HMAC 签名 + 时间同步)
│   ├── coinalyze_client.py   # OI + Liquidations
│   ├── sentiment_client.py   # Binance 全球多空比
│   ├── sr_zone_calculator.py # S/R 区域计算 (v17.0: 1+1)
│   ├── sr_pivot_calculator.py # Floor Trader Pivot Points
│   ├── sr_swing_detector.py  # Williams Fractal + 成交量加权
│   ├── sr_volume_profile.py  # VPOC + Value Area
│   ├── sr_types.py           # S/R 数据类型定义
│   ├── order_flow_processor.py  # 订单流处理 (CVD, taker buy ratio)
│   ├── orderbook_processor.py   # 订单簿处理 (OBI, 滑点, 动态)
│   ├── risk_controller.py    # 风险熔断器 (drawdown/daily loss/consecutive loss + HMM regime)
│   ├── calibration_loader.py # 校准数据加载 (mtime 缓存)
│   ├── backtest_math.py      # 回测共享数学 (ATR Wilder's, SL/TP, SMA/BB)
│   ├── shared_logic.py       # SSoT 共享逻辑常量 (Extension/Volatility/CVD)
│   ├── audit_logger.py       # 审计日志 (SHA256 hash chain)
│   ├── http_retry.py         # 共享 HTTP 重试装饰器
│   ├── metrics_exporter.py   # Prometheus 指标导出
│   ├── kelly_sizer.py        # Fractional Kelly 仓位计算
│   ├── fear_greed_client.py  # Fear & Greed Index 客户端
│   ├── regime_detector.py    # HMM 4-State 市场 regime 检测
│   └── data_validator.py     # Pandera 数据验证
│
├── configs/                  # 配置 (分层架构)
│   ├── base.yaml             # 基础配置 (所有参数, SSoT)
│   ├── production.yaml       # 生产环境覆盖 (timer=1200s, INFO)
│   ├── development.yaml      # 开发环境覆盖 (1m timeframe, DEBUG)
│   └── backtest.yaml         # 回测环境覆盖 (无 Telegram)
│
├── scripts/                  # 脚本工具 (~35,000 行)
│   ├── diagnostics/          # 诊断模块
│   │   ├── base.py           # 诊断基类 + 上下文
│   │   ├── code_integrity.py # 114 项静态分析 (P1.0-P1.113)
│   │   ├── architecture_verify.py # 20+ 架构合规检查
│   │   ├── order_flow_simulation.py # 15 场景订单流程模拟 (含 trailing stop)
│   │   ├── config_checker.py # 配置验证
│   │   ├── indicator_test.py # 指标计算验证
│   │   ├── math_verification.py # 16 项数学公式验证 (M1-M16)
│   │   ├── market_data.py    # 市场数据获取
│   │   ├── mtf_components.py # MTF + Telegram + 错误恢复
│   │   ├── position_check.py # 仓位 + 记忆系统 + 裸仓扫描
│   │   ├── service_health.py # systemd 服务状态
│   │   ├── lifecycle_test.py # 交易生命周期测试
│   │   └── summary.py        # 数据流总结 + JSON 导出
│   ├── diagnose.py           # 离线诊断 (13 检查)
│   ├── diagnose_realtime.py  # 实时 API 诊断 (12 阶段)
│   ├── diagnose_trade_frequency.py # 交易频率 + SL/TP 效能诊断
│   ├── diagnose_feature_pipeline.py # Feature Pipeline 诊断
│   ├── diagnose_trailing_stop.py # Trailing Stop 诊断 (4H ATR parity)
│   ├── diagnose_mechanical.py # Mechanical 模式 10 阶段 E2E 诊断
│   ├── smart_commit_analyzer.py # 自进化回归检测
│   ├── check_logic_sync.py   # SSoT 逻辑同步检查 (14 检查项)
│   ├── calibrate_hold_probability.py # S/R Hold Probability 自动校准
│   ├── calibrate_anticipatory.py # Optuna 预判信号校准
│   ├── generate_cold_start_snapshots.py # 冷启动快照生成 (Binance 公开 API)
│   ├── verify_extension_ratio.py # ATR Extension Ratio 验证
│   ├── verify_indicators.py  # 技术指标计算验证
│   ├── validate_data_pipeline.py # 13 类数据管线验证
│   ├── validate_production_sr.py # 生产 S/R v17.0 验证
│   ├── backtest_from_logs.py # 生产级日志信号回测 (v3.0)
│   ├── backtest_sr_zones.py  # S/R 区域历史回测
│   ├── stress_test_position_management.py # 仓位管理压力测试
│   ├── e2e_trade_pipeline_test.py # 端到端交易管线测试
│   └── analyze_dependencies.py # 代码依赖分析
│
├── data/                     # 数据目录 (运行时生成)
│   ├── trading_memory.json   # 交易记忆 (最多 500 条)
│   ├── layer_orders.json     # 每层 SL/TP 持久化 (v7.2)
│   ├── calibration/          # S/R 校准数据 (v16.0)
│   │   ├── latest.json       # 当前校准因子
│   │   └── history/          # 历史校准存档 (最多 12 份)
│   ├── feature_snapshots/    # Feature snapshot (deterministic replay)
│   └── backtest_from_logs_result.json # 生产级回测结果
│
├── web/                      # Web 管理界面 (5,515 行后端 + ~35 前端组件)
│   ├── backend/              # FastAPI
│   │   ├── main.py           # FastAPI 入口
│   │   ├── core/             # config.py, database.py
│   │   ├── models/           # settings.py
│   │   ├── api/routes/       # public, admin, auth, trading, performance, websocket, mechanical, srp
│   │   └── services/         # trade_evaluation, performance, trading, config, mechanical, notification, srp
│   ├── frontend/             # Next.js 14 + React 18 + TypeScript + Tailwind CSS
│   │   ├── pages/            # index, dashboard, mechanical, performance, srp, copy, about, admin
│   │   ├── components/       # admin, charts, trading, trade-evaluation, mechanical, layout, ui
│   │   ├── hooks/            # useMechanical.ts, useTradeEvaluation.ts
│   │   └── lib/              # utils.ts, i18n.ts (EN+ZH)
│   └── deploy/               # Caddyfile, systemd services, redeploy.sh, setup.sh
│
├── patches/                  # 兼容性补丁 (必须在 NT 导入前加载)
│   ├── binance_enums.py      # 未知枚举处理 (_missing_ hook)
│   └── binance_positions.py  # 非 ASCII 持仓过滤
│
├── tests/                    # 测试 (~6,500 行)
│   ├── conftest.py           # pytest fixtures
│   ├── test_config_manager.py # ConfigManager
│   ├── test_trading_logic.py # SL/TP 计算 + 仓位大小
│   ├── test_feature_schema.py # 141 features 类型验证
│   ├── test_v19_1_verification.py # extension/divergence/CVD/OBV
│   ├── test_replay_determinism.py # 确定性重放
│   ├── test_bracket_order.py # OCO 订单流
│   ├── test_telegram.py / test_telegram_commands.py # Telegram 通知+命令
│   ├── test_orderbook.py     # OBI + 订单簿
│   ├── test_sl_fix.py / test_rounding_fix.py # SL + 精度修复
│   ├── test_binance_patch.py # 枚举补丁
│   ├── test_command_listener.py # 命令监听测试
│   ├── test_implementation_plan.py # 功能实现验证
│   └── manual_order_test.py  # 手动订单测试
├── docs/                     # 文档
└── .github/workflows/        # CI/CD (commit-analysis, codeql, claude)
```

## Web 管理界面架构

### 后端 (FastAPI)

**认证**: Google OAuth 2.0 → JWT → admin 白名单 (`ADMIN_EMAILS`)
**数据库**: SQLite (async via aiosqlite) — SocialLink, CopyTradingLink, SiteSettings

| 路由组 | 认证 | 端点数 | 核心功能 |
|--------|------|--------|---------|
| `/api/public/*` | No | 27 | 性能摘要、信号历史、交易评估、系统状态、feature snapshots、交易记忆、regime |
| `/api/admin/*` | Yes | 22 | 策略配置、服务控制、Telegram 配置、文件上传 |
| `/api/auth/*` | Mixed | 4 | Google OAuth login/callback/me/logout |
| `/api/trading/*` | Mixed | 12 | Binance 实时数据 (ticker/klines/orderbook/positions) |
| `/api/performance/*` | No | 12 | 盈亏曲线、通知管理、信号统计 |
| `/api/ws/*` | Mixed | 6 | WebSocket 实时流 (ticker 1s, account 5s, positions 3s) |

### 前端 (Next.js 14)

**页面**: index, dashboard, mechanical, performance, srp, copy, about, admin, regime
**i18n**: EN + ZH (via `lib/i18n.ts`)
**实时数据**: WebSocket 订阅 + SWR 数据获取

## Web 前端设计规范 (DipSway 风格)

### 导航栏设计

导航栏采用 **DipSway 风格**：透明背景 + 独立浮动组件组。

### Web 部署 (修改网站后必须执行)

```bash
# 一键重新部署 (推荐)
cd /home/linuxuser/nautilus_AlgVex && bash web/deploy/redeploy.sh

# 指定分支
cd /home/linuxuser/nautilus_AlgVex && bash web/deploy/redeploy.sh --branch claude/xxx

# 已经 pull 过了，跳过拉代码
cd /home/linuxuser/nautilus_AlgVex && bash web/deploy/redeploy.sh --skip-pull
```

**关键**: 每次修改必须 `npm run build`，先停服务再重建再启动，三个服务 (frontend/backend/caddy) 都重启。

### 服务管理 (统一使用 systemd)

```bash
sudo systemctl status algvex-backend algvex-frontend caddy
sudo journalctl -u algvex-frontend -n 30
```

**服务文件**: `web/deploy/algvex-backend.service`, `web/deploy/algvex-frontend.service`
**首次安装**: `cd /home/linuxuser/nautilus_AlgVex/web/deploy && chmod +x setup.sh && ./setup.sh`

## Telegram 双频道消息归属 (v14.0)

每条消息只发一个地方，零重复。`broadcast=True` → 仅通知频道，`broadcast=False` → 仅私聊。

| 消息类型 | 私聊 (控制面板) | 通知频道 (订阅者) | 说明 |
|---------|:--------------:|:----------------:|------|
| 系统启动/关闭 | Y | | 运维信息 |
| 心跳监控 | Y | | 20分钟/次 |
| **开仓/平仓/加减仓** | | Y | 核心交易信号 |
| **日报/周报** | | Y | 业绩展示 |
| 错误/告警/SL调整 | Y | | 调试/风控 |
| 命令响应/订单拒绝 | Y | | 运维 |

## Telegram 命令 (v3.0)

**快捷菜单** (/ 自动补全): `/menu` (推荐入口), `/s` 状态, `/p` 持仓, `/b` 余额, `/a` 技术面, `/fa` 触发分析, `/profit` 盈亏, `/close` 平仓, `/help`

**查询命令** (无需 PIN): `/status`, `/position`, `/balance`, `/analyze`, `/orders`, `/history`, `/risk`, `/daily`, `/weekly`, `/config`, `/version` (`/v`), `/logs` (`/l`), `/profit`

**控制命令** (需 PIN): `/pause`, `/resume`, `/close`, `/force_analysis`, `/partial_close 50` (`/pc`), `/set_leverage 10`, `/toggle trailing`, `/set min_confidence HIGH`, `/restart` (`/update`), `/calibrate`, `/modify_sl`, `/modify_tp`, `/reload_config`

## GitHub Actions

| 工作流 | 触发 | 功能 |
|--------|------|------|
| Commit Analysis | push/PR to main | 回归检测 + AI 分析 + 依赖分析 |
| CodeQL Analysis | push/PR + 每周一 | 安全漏洞 + 代码质量 |
| Claude Code | issue/PR | Claude Code Action |

## 外部 API 依赖

| API | 模块 | 用途 | 认证 |
|-----|------|------|------|
| Binance Futures (fapi) | `binance_kline_client.py` | K线、FR、价格 | 无 (公开) |
| Binance Futures (fapi) | `binance_account.py` | 账户、仓位、订单 | HMAC-SHA256 |
| Binance Futures (fapi) | `binance_derivatives_client.py` | Top Traders L/S、OI | 无 (公开) |
| Binance Futures (fapi) | `binance_orderbook_client.py` | 订单簿深度 | 无 (公开) |
| Binance Futures (fapi) | `sentiment_client.py` | 全球 L/S 比 | 无 (公开) |
| Coinalyze | `coinalyze_client.py` | OI + Liquidations + L/S | API Key (可选) |
| Telegram | `telegram_bot.py` | 通知 + 命令 | Bot Token |

## 数据持久化

| 文件 | 用途 | 大小限制 |
|------|------|---------|
| `data/trading_memory.json` | 交易记忆 + 评估 | 最多 500 条 |
| `data/layer_orders.json` | 每层 SL/TP 持久化 (重启恢复) | 按活跃层数 |
| `data/calibration/latest.json` | S/R Hold Probability 校准因子 | 单文件 |
| `data/calibration/history/` | 校准历史存档 | 最多 12 份 |
| `data/feature_snapshots/` | Feature snapshot (deterministic replay) | 按周期 |
| `data/telegram_queue.db` | Telegram 消息持久化队列 | 7 天保留 |
| `data/backtest_from_logs_result.json` | 生产级回测结果 | 按运行覆盖 |
| `data/trade_analysis_export.json` | 信号分析导出 (回测输入) | 按运行覆盖 |
| `logs/audit/` | Telegram 命令审计日志 (SHA256 hash chain) | 按日轮转 |

## 联系方式

- GitHub: FelixWayne0318
- 仓库: https://github.com/FelixWayne0318/AlgVex
