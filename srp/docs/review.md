# SRP v6.0 → v6.1 评审报告

> **评审标准**: 不改变策略方案，仅发现 BUG / 逻辑错误 / 功能缺失 / 迁移准备不足
> **评审范围**: `docs/SRP_strategy_v6.pine` (583 行 → v6.1: ~690 行)
> **状态**: ✅ 全部修复已合入 v6.1

---

## 〇、验证结果总结

| 报告 ID | 结论 | 原因 | v6.1 处理 |
|---------|------|------|-----------|
| BUG-1 pyramiding | **✅ 真实 BUG** | `pyramiding=5` 但 `maxval=10`，虚拟 DCA 永远不触发 | `pyramiding=11` |
| BUG-2 v_count | **✅ 真实 BUG** | 真实 DCA 也累加 v_count，虚拟次数被压缩 | 删除真实 DCA 中的 `v_count += 1` |
| BUG-3 RSI src | **❌ 误报** | RSI 用 `close` 是 Wilder's RSI 标准做法；tooltip 已写明只影响 VWMA+MFI | 加 `[DOC-2]` 注释，更新 tooltip |
| BUG-4 rsi_mfi | **⚠️ 继承设计** | 原版 Felix v5 就是 `rsi + mf/2`，改公式 = 改策略信号 | 加 `[DOC-1]` 注释说明实际范围 0~150 |
| BUG-5 plotshape | **✅ 真实 BUG** | 标记条件缺少 `last_exit_bar` 检查，止损同 bar 显示假入场 | 加入 `bar_index != last_exit_bar` |
| BUG-6 v_last_px | **❌ 非 BUG** | 方向检查 `close < v_last_px` 已防止误触发 | 无需修改 |

---

## 〇-B、第二轮审计结果 (35 项逐项检查)

> 审计提示词: `docs/SRP_v6_review_prompt.md`，覆盖 A~G 七大类 35 项检查。

### 新发现 BUG (v6.1 第二轮修复)

| ID | 严重 | 问题 | 修复 |
|---|---|---|---|
| **B4** | MEDIUM | `vdca_max` maxval=50，`2.5^50 ≈ 8.9×10^19` 溢出 float64 | maxval 降为 20 |
| **B5** | MEDIUM | `_calc_required_capital()` 对"仓位倍增"显示约 1/3 真实值 | 区分 DCA 模式计算 |
| **D3** | LOW | `sonum > 1` 阻止 DCA次数=0 时虚拟 DCA 工作 | 改为 `sonum >= 1` |
| **F4** | MEDIUM | JSON alert 缺少 `pos_size`/`avg_price`/`pnl` | 补充字段 |

### 审计通过项 (31/35)

A1~A7 全部通过 (Pine 引擎行为), B1~B3 通过 (数值精度), C1~C5 全部通过 (做空逻辑),
D1~D2/D4~D6 通过 (状态机), E1~E5 通过 (UI), F1~F3/F5 通过或仅需文档, G1~G3 通过 (代码质量)。

### 已知继承行为 (非 BUG，不修改)

- **B3**: `calcChangeFromLastDeal` 用 `close` 做分母，`calcNextSOprice` 用 `avg_price` — 基准不一致但双条件更保守
- **E2**: 仅真实 DCA 时 `v_avg ≈ position_avg_price`，虚拟均价线可能闪烁 — 纯视觉
- **G1**: 状态重置代码重复 5 处 (~8 行 × 5) — Pine v5 函数不能修改 `var` 全局变量

---

## 一、BUG 与逻辑错误 (已修复)

### BUG-1: `pyramiding` 硬编码与 DCA 次数输入不匹配 ❌ CRITICAL

**位置**: L18 vs L53

```pine
// L18: 策略声明
pyramiding = 5    // TradingView 硬限制: 同方向最多 5 笔

// L53: 用户输入
int sonum = input.int(4, ..., maxval=10) + 1  // 用户可设到 11
```

**问题**: 用户将 DCA 次数设为 5~10 时，`sonum` = 6~11，但 `pyramiding = 5` 限制最多 5 笔入场 (含首笔)。第 5 笔之后的 DCA 会被 TradingView 静默丢弃，用户看不到任何错误提示，但策略逻辑认为"还可以加仓"，虚拟 DCA 也不会触发 (因为 `strategy.opentrades < sonum` 仍为 true)。

**修复**:
```pine
// 方案 A: pyramiding 设为 maxval+1 = 11
pyramiding = 11

// 方案 B: 限制 maxval = pyramiding - 1 = 4
int sonum = input.int(4, ..., maxval=4) + 1
```
推荐方案 A — 给用户最大灵活性。

---

### BUG-2: `v_count` 被真实 DCA 和虚拟 DCA 共同累加 ❌ HIGH

**位置**: L398 (入场设 0) → L416 (真实 DCA 累加) → L353 (虚拟 DCA 判断)

```pine
// L398: 新开仓
v_count := 0

// L416: 真实 DCA — 也累加 v_count!
v_count += 1

// L353: 虚拟 DCA 触发条件
... v_count < vdca_max ...
```

**问题**: 假设 `sonum=5` (4 次真实 DCA)，`vdca_max=10`。4 次真实 DCA 后 `v_count=4`，虚拟 DCA 只能再触发 6 次 (10-4)，而非用户预期的 10 次。

**修复**: 将真实 DCA 的 `v_count` 累加改为独立计数，或在虚拟 DCA 判断中减去真实 DCA 数量：
```pine
// 方案: 真实 DCA 不累加 v_count，仅同步虚拟状态
// L412-416 改为:
v_cost    := v_cost + dca_qty * close
v_qty     := v_qty + dca_qty
v_avg     := v_cost / v_qty
v_last_px := close
// 删除: v_count += 1
```

---

### BUG-3: RSI 数据源硬编码为 `close`，与 MFI/VWMA 的 `src` 不一致 ⚠️ MEDIUM

**位置**: L142 vs L145

```pine
// L142: RSI — 硬编码 close
float up   = ta.rma(math.max(ta.change(close), 0), rsi_len)
float down = ta.rma(-math.min(ta.change(close), 0), rsi_len)

// L145: MFI — 使用用户选择的 src
float mf = ta.mfi(src, mfi_len)

// L138: VWMA — 使用用户选择的 src
float core = ta.vwma(src, Length)
```

**问题**: 用户选择 "OHLC4" 或 "HL2" 数据源时，VWMA 和 MFI 使用新数据源，但 RSI 仍用 `close`。这导致 `rsi_mfi` 混合了不同数据源，用户更改数据源后效果不如预期。

**修复**:
```pine
float up   = ta.rma(math.max(ta.change(src), 0), rsi_len)
float down = ta.rma(-math.min(ta.change(src), 0), rsi_len)
```

---

### BUG-4: `rsi_mfi` 运算符优先级 — `mf` 权重仅为 RSI 的 1/2 ⚠️ MEDIUM

**位置**: L146

```pine
float rsi_mfi = math.abs(rsi + mf / 2)
// 实际计算: abs(rsi + (mf / 2))
// RSI 范围 0-100, mf/2 范围 0-50 → rsi_mfi 范围 0-150
```

**问题**:
1. MFI 被除以 2 后再加 RSI，MFI 对信号的贡献仅为 RSI 的 ~1/2，加权不均衡
2. `math.abs()` 是冗余的 — RSI ∈ [0,100] 且 MFI ∈ [0,100]，两者之和永远 ≥ 0
3. 原版 v5 也有此问题 (继承 bug)

**说明**: 此行为延续原版设计。如果是刻意为之，建议加注释说明；如果是优先级错误，应改为:
```pine
float rsi_mfi = (rsi + mf) / 2  // 等权重平均, 范围 0-100
// 此时 below/above 阈值也需要同步调整 (当前 below=55, above=95)
```

> **注意**: 修改此公式会改变策略行为，需重新回测。如保持原版逻辑不变，至少加注释标注实际行为。

---

### BUG-5: 入场标记 (`plotshape`) 条件与实际入场条件不一致 ⚠️ LOW

**位置**: L471-477 vs L379-401

```pine
// L471: 标记条件 — 缺少 last_exit_bar 检查和 deal_dir 检查
bool base_entry_cond = entry_signal and strategy.opentrades == 0 and in_date_range
bool dca_entry_cond  = entry_signal and deal_dir == trade_dir and SOconditions(deal_dir) and ...

// L379: 实际入场条件 — 有 last_exit_bar 检查
if bar_index != last_exit_bar and entry_signal and strategy.opentrades == 0 and in_date_range
```

**问题**: 在止损/周期切换同 bar，标记仍会显示，但实际不会入场。视觉误导。

**修复**: 加入 `bar_index != last_exit_bar` 条件：
```pine
bool base_entry_cond = bar_index != last_exit_bar and entry_signal and strategy.opentrades == 0 and in_date_range
```

---

### BUG-6: 虚拟 DCA 第一次触发时 `v_last_px` 可能过时 ⚠️ LOW

**位置**: L203-208 + L415

```pine
// 真实 DCA 最后一次设置 v_last_px (L415):
v_last_px := close

// 虚拟 DCA 检查 (L207):
float chg = math.abs((close - v_last_px) / v_last_px) * 100
```

**问题**: 如果真实 DCA 全部用完后，价格先回升再继续下跌，`v_last_px` 记录的是最后一次真实 DCA 的价格。在价格回升期间，`chg` 可能满足条件但方向错误 (做多时价格回升，`close > v_last_px` 但条件要求 `close < v_last_px`)。实际上 L208 的方向检查 (`dir == 1 ? ... close < v_last_px`) 已覆盖此情况，所以不会误触发。

**结论**: 逻辑正确，但建议加注释说明方向检查的重要性。

---

## 二、功能完善建议 (不改变策略方案)

### FEAT-1: 最大回撤追踪与显示

**现状**: 信息面板无最大回撤指标，用户无法评估策略风险特征。

```pine
// 新增状态变量
var float peak_equity = 0.0
var float max_drawdown_pct = 0.0

// 每 bar 更新
if strategy.equity > peak_equity
    peak_equity := strategy.equity
float current_dd = (peak_equity - strategy.equity) / peak_equity * 100
if current_dd > max_drawdown_pct
    max_drawdown_pct := current_dd

// 信息面板新增一行
_tbl_row(tbl, 13, "最大回撤", str.tostring(math.round(max_drawdown_pct, 2)) + "%", C_BEAR)
```
> 需要将 table.new 的行数从 13 增加到 14+。

---

### FEAT-2: 连续亏损计数与保护

**现状**: 无连续亏损追踪，策略无法在连续爆亏时自我保护。

```pine
var int consecutive_losses = 0
var int max_consecutive_losses = 0

// 在平仓逻辑后检测
if just_closed
    if strategy.closedtrades.profit(strategy.closedtrades - 1) < 0
        consecutive_losses += 1
        if consecutive_losses > max_consecutive_losses
            max_consecutive_losses := consecutive_losses
    else
        consecutive_losses := 0
```
可配合仓位缩减使用 (连续亏 N 次后减半仓位)。

---

### FEAT-3: 每笔交易持仓时间统计

**现状**: 只在 PNL 标签中显示持仓时间，无统计汇总。

```pine
var float total_hold_seconds = 0.0
var int closed_trade_count = 0

// 平仓时累加
if just_closed
    total_hold_seconds += (time - deal_start_time) / 1000
    closed_trade_count += 1

// 面板显示平均持仓时间
float avg_hold = closed_trade_count > 0 ? total_hold_seconds / closed_trade_count : 0
_tbl_row(tbl, N, "平均持仓", _time_str(avg_hold), c_val)
```

---

### FEAT-4: RSI-MFI 值实时显示

**现状**: `rsi_mfi` 是核心信号驱动指标，但面板不显示当前值和阈值距离。

```pine
string rsi_text = str.tostring(math.round(rsi_mfi, 1)) + "  [" + str.tostring(below) + " / " + str.tostring(above) + "]"
color rsi_clr = rsi_mfi < below ? C_ENTRY : rsi_mfi > above ? C_BEAR : c_val
_tbl_row(tbl, N, "RSI-MFI", rsi_text, rsi_clr)
```

---

### FEAT-5: 平仓类型统计

**现状**: `last_exit_type` 仅记录最后一次平仓类型，无历史统计。

```pine
var int exit_tp_count = 0
var int exit_band_count = 0
var int exit_sl_count = 0
var int exit_cycle_count = 0

// 各平仓处累加相应计数器
// 面板显示:
string exit_stats = "盈" + str.tostring(exit_tp_count) + " 轨" + str.tostring(exit_band_count) + " 损" + str.tostring(exit_sl_count) + " 切" + str.tostring(exit_cycle_count)
_tbl_row(tbl, N, "平仓分布", exit_stats, c_val)
```

---

### FEAT-6: 信号强度可视化

**现状**: 入场/出场标记仅有方向，无信号强度信息。

```pine
// rsi_mfi 偏离阈值的程度 = 信号强度
float sig_strength = trade_dir == 1 ? (below - rsi_mfi) / below : (rsi_mfi - above) / (150 - above)
// 可将强度映射到标记大小或颜色透明度
```

---

## 三、Web / App 迁移准备

### MIGRATE-1: Alert 消息结构化 (JSON Webhook) ❌ 必须

**现状**: Alert 消息为纯文本 (`"新开仓 {{ticker}}"`), 无法被程序解析。

**Web/App 需要**: 结构化 JSON，包含所有决策字段，供后端 API 消费。

```pine
// 替换现有 alert 调用:
_build_alert(action, detail) =>
    '{"v":"6.0"' +
     ',"action":"' + action + '"' +
     ',"ticker":"' + syminfo.ticker + '"' +
     ',"price":' + str.tostring(close) +
     ',"dir":"' + (deal_dir == 1 ? "LONG" : "SHORT") + '"' +
     ',"deal":' + str.tostring(dealcount) +
     ',"dca":' + str.tostring(socounter) +
     ',"vdca":' + str.tostring(v_count) +
     ',"v_avg":' + str.tostring(math.round(v_avg, 2)) +
     ',"tp":' + str.tostring(math.round(tp_target, 2)) +
     ',"equity":' + str.tostring(math.round(strategy.equity, 2)) +
     ',"rsi_mfi":' + str.tostring(math.round(rsi_mfi, 1)) +
     ',"cycle":"' + (in_bear ? "BEAR" : "BULL") + '"' +
     ',"detail":"' + detail + '"' +
     '}'

// 使用:
alert(_build_alert("ENTRY", "开仓 #" + str.tostring(dealcount)), freq=alert.freq_once_per_bar_close)
alert(_build_alert("DCA", "加仓 #" + str.tostring(socounter)), freq=alert.freq_once_per_bar_close)
alert(_build_alert("EXIT_TP", "止盈 v" + str.tostring(v_count)), freq=alert.freq_once_per_bar_close)
alert(_build_alert("EXIT_SL", "止损"), freq=alert.freq_once_per_bar_close)
alert(_build_alert("EXIT_BAND", "触轨"), freq=alert.freq_once_per_bar_close)
alert(_build_alert("EXIT_CYCLE", "周期切换"), freq=alert.freq_once_per_bar_close)
```

---

### MIGRATE-2: 参数导出 — 策略状态 Data Window

**现状**: 关键内部状态只在面板显示，外部程序无法获取。

```pine
// 通过 plot(display=display.data_window) 导出内部状态
plot(rsi_mfi,       "RSI-MFI",      display=display.data_window)
plot(v_avg,         "虚拟均价",     display=display.data_window)
plot(v_count,       "虚拟DCA次数",  display=display.data_window)
plot(tp_target,     "止盈目标",     display=display.data_window)
plot(trade_dir,     "交易方向",     display=display.data_window)  // 1=多, -1=空
plot(socounter,     "DCA次数",      display=display.data_window)
plot(deal_dir,      "持仓方向",     display=display.data_window)
plot(max_drawdown_pct, "最大回撤%", display=display.data_window)
```
这些值可通过 TradingView API 或截图 OCR 获取，作为迁移的中间过渡。

---

### MIGRATE-3: 信号 / 执行 / 可视化 三层分离

**现状**: 信号生成、订单执行、可视化代码混在一起。

**迁移建议**: 在代码注释中明确标注三层边界 (当前 v6 已用 `====` 分隔，结构良好)，后续迁移时:

| Pine 层 | Web/App 对应 | 迁移难度 |
|---------|-------------|---------|
| 指标计算 (L132-149) | Python `indicators/` 模块 | 低 — 纯数学 |
| 周期判断 (L120-130) | 配置文件 / 数据库 | 低 — 日期查表 |
| 入场/出场逻辑 (L244-416) | Python `srp_strategy.py` | 中 — 需对接交易所 API |
| 虚拟 DCA (L350-359) | Python 内存状态 | 低 — 纯计算 |
| 可视化 (L420-487) | React 图表组件 | 高 — 需 TradingView Charting Library |
| 信息面板 (L490-583) | React Dashboard | 低 — 数据绑定 |

---

### MIGRATE-4: 周期日期外部化

**现状**: 历史周期日期硬编码在 L125-128。

**问题**: Web/App 中需要动态更新周期 (不能修改 Pine 源码)。

**建议**: 在 `configs/base.yaml` 的 `srp:` 段落中维护周期日期表:
```yaml
srp:
  cycle_dates:
    - { top: "2013-11-30", bottom: "2015-01-14" }
    - { top: "2017-12-17", bottom: "2018-12-15" }
    - { top: "2021-11-10", bottom: "2022-11-21" }
    - { top: "2025-10-06", bottom: "2026-10-06" }  # 可配置
```
Pine 脚本中保持硬编码 (TradingView 限制)，但 Python 实现从配置读取。

---

### MIGRATE-5: 复利仓位计算的精确复现

**位置**: L384

```pine
deal_base := strategy.equity * base_pct
```

**迁移注意**: `strategy.equity` = 初始资金 + 已实现盈亏 + 未实现盈亏。Web/App 实现时需确保:
1. 使用交易所账户的"可用保证金"而非"总权益"
2. 扣除已占用保证金
3. 考虑杠杆倍数对可用资金的影响

---

## 四、代码质量与健壮性

### QUALITY-1: 平仓逻辑代码重复 (DRY 违反)

**位置**: L246-347 四处平仓逻辑，每处都重复:
```pine
v_qty := 0.0
v_cost := 0.0
v_avg := 0.0
v_last_px := 0.0
v_count := 0
socounter := 0
deal_dir := 0
last_exit_bar := bar_index
```

**建议**: 提取为函数:
```pine
_reset_deal_state() =>
    v_qty     := 0.0
    v_cost    := 0.0
    v_avg     := 0.0
    v_last_px := 0.0
    v_count   := 0
    socounter := 0
    deal_dir  := 0
    last_exit_bar := bar_index
```
> 注意: Pine Script 的函数不能修改 `var` 变量 (作用域限制)。需要用不同方式处理 — 例如设置一个 `var bool should_reset = false` flag，在逻辑末尾统一重置。或者接受重复以保证可读性。

---

### QUALITY-2: 魔法数字

| 位置 | 值 | 含义 | 建议 |
|------|---|------|------|
| L236 | `0.04` | PNL 标签偏移量 | 提取为常量 `LBL_OFFSET = 0.04` |
| L146 | `/2` | MFI 权重 | 加注释或提取为常量 |
| L530 | `low, high` | 渐变范围 | 文档说明 |

---

## 五、评审总结

| 类别 | 数量 | 严重等级 |
|------|------|---------|
| **CRITICAL BUG** | 1 | pyramiding 与 DCA 次数不匹配 |
| **HIGH BUG** | 1 | v_count 被双重累加 |
| **MEDIUM BUG** | 2 | RSI 数据源不一致 + rsi_mfi 权重 |
| **LOW BUG** | 2 | 标记条件不一致 + 注释建议 |
| **功能完善** | 6 | 回撤/连亏/持仓时间/RSI显示/平仓统计/信号强度 |
| **迁移准备** | 5 | JSON Alert/数据导出/三层分离/周期外部化/复利精确复现 |

### 优先级排序

1. **立即修复**: BUG-1 (pyramiding) + BUG-2 (v_count)
2. **尽快修复**: BUG-3 (RSI src) + MIGRATE-1 (JSON Alert)
3. **下一版本**: FEAT-1~6 功能完善 + MIGRATE-2~5
4. **可选**: BUG-4 (rsi_mfi 权重 — 需回测确认) + QUALITY 改进
