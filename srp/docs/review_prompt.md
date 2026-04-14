# SRP v6 Pine Script 评审提示词

> 你是一位管理超过 $50M AUM 的量化基金 CTO，精通 Pine Script v5、TradingView 策略引擎内部机制、DCA 网格策略、加密货币衍生品交易。你需要对 `SRP_strategy_v6.pine` 进行逐行级别的技术评审。

## 约束条件

1. **不改变策略方案** — VWMA+RSI-MFI 通道 + DCA + 虚拟 DCA + 周期切换 + 硬止损 的核心逻辑不得修改
2. **找出所有错误或 BUG** — 包括但不限于逻辑错误、边界条件、Pine Script 引擎行为差异、数值精度问题
3. **完善功能** — 在不改变策略方案的前提下，补全缺失的防护、监控、用户体验
4. **为迁移做准备** — Web 端 (NautilusTrader Python)、App 端 (Webhook + API) 的数据接口和状态管理

## 评审清单 (必须逐项执行)

### A. Pine Script 引擎行为审计

- [ ] A1. `strategy.entry()` 的 `qty` 参数语义：当 `default_qty_type=strategy.cash` 时，`qty` 传入的是**币数量**还是**美元金额**？验证所有 `strategy.entry()` 调用的 qty 传入方式是否正确。
- [ ] A2. `strategy.close()` 在同一 bar 内的行为：`strategy.position_size` 是否立即更新？`strategy.opentrades` 呢？验证 `last_exit_bar` 防护是否充分。
- [ ] A3. `strategy.opentrades` vs `strategy.closedtrades`：在 `strategy.close()` 执行后、同 bar 内，这两个值的状态。
- [ ] A4. `strategy.equity` 的计算时机：在 `strategy.entry()` 执行前还是后？`deal_base := strategy.equity * base_pct` 使用的是哪个时刻的 equity？
- [ ] A5. `var` 变量在函数内的赋值行为：Pine v5 中 `_make_pnl_label()` 等函数是否能读取全局 `var` 变量？是否存在作用域陷阱？
- [ ] A6. `barstate.isconfirmed` 与 `calc_on_every_tick` (默认 false) 的交互：表格是否只在 bar 关闭时更新？是否遗漏 realtime bar？
- [ ] A7. `alert()` 的 `freq=alert.freq_once_per_bar_close`：在同一 bar 内多次调用 `alert()` 时，只触发第一次还是最后一次？对 JSON alert 有何影响？

### B. 数学与数值精度审计

- [ ] B1. `calcNextSOprice()` 中的 `math.round(... / syminfo.mintick) * syminfo.mintick` 的 tick 对齐：对极端价格 (BTC > $100,000) 和极小 mintick (如 0.01) 的组合，是否存在浮点溢出？
- [ ] B2. `rsi_mfi = math.abs(rsi + mf / 2)` 的值域：当 `below=55` 和 `above=95` 时，做多信号需要 rsi_mfi < 55，做空需要 > 95。考虑到实际范围 0~150，阈值设置是否合理？做空的 `above=95` 在 150 满分中意味着什么？
- [ ] B3. `calcChangeFromLastDeal()` 使用 `close` 作为分母：`(close - last_deal) / close * 100`。当 close 极端偏离 last_deal 时，百分比计算是否正确？与 `SOconditions` 中 `calcNextSOprice` (使用 avg_price 作为基准) 是否存在基准不一致？
- [ ] B4. 虚拟 DCA 的 `v_qty * safety` 增长：经过 10 次虚拟 DCA (safety=1.5)，`v_qty` 增长到原始的 `1.5^10 = 57.7` 倍。`v_avg` 是否仍然有意义？止盈目标是否合理？
- [ ] B5. `_calc_required_capital()` 是否正确反映实际最大资金需求？公式是否考虑了 "仓位倍增" 模式的指数增长？

### C. 做空逻辑完整性审计

- [ ] C1. 所有含 `strategy.position_size` 的判断：做空时 `position_size < 0`，每处用法是否正确处理了正负号？
- [ ] C2. PnL 标签中的盈亏计算：做空盈利 = `avg_price - close`。验证 `_make_pnl_label()` 中的 `pnl_val` 和 `pnl_pct` 公式。
- [ ] C3. 硬止损 `dd` 计算：做空时 `dd = (close - avg_price) / avg_price`。如果 close 大幅上涨，dd 是否正确反映亏损百分比？
- [ ] C4. 触轨信号 `exit_band_signal`：做多时用 `sig_short` 平仓，做空时用 `sig_long` 平仓。逻辑是否正确？
- [ ] C5. DCA 条件 `SOconditions`：做空加仓需要 `close > next_so` (价格上涨才加仓做空)。逻辑是否正确？应该是价格继续下跌还是反弹才加仓？

### D. 状态机与边界条件审计

- [ ] D1. `deal_dir == 0` 时的行为：在空仓且 `deal_dir=0` 时，`exit_band_signal = sig_long`。如果此时恰好 `sig_long=true`，是否会导致问题？
- [ ] D2. 入场同 bar 被平仓：Pine 引擎是否允许在同一 bar 内 entry 后立即 close？如果允许，v_avg/v_cost 是否正确？
- [ ] D3. `vdca_enabled=true` 但 `sonum=1`（0 次 DCA + 1 首笔）：条件 `sonum > 1` 跳过虚拟 DCA。是否应改为 `sonum >= 1`？即使没有真实 DCA，虚拟 DCA 是否仍应工作？
- [ ] D4. `peak_equity` 初始值 0：首笔交易前 `peak_equity=0`，第一次 `strategy.equity > 0` 时才更新。在 `initial_capital=1500` 时，`max_drawdown_pct` 的首次计算是否正确？
- [ ] D5. 周期切换发生在持仓中间：从牛市切换到熊市时，做多仓位被强制平仓。如果同 bar 内又触发做空信号，是否会立即开仓？`last_exit_bar` 是否阻止？
- [ ] D6. `strategy.entry()` 同 ID 重复调用：如果同 bar 内 `entry_signal=true` 且 `SOconditions=true`，是否可能同时触发首笔入场和 DCA？

### E. 可视化与 UI 审计

- [ ] E1. `tp_fill_ref` 在 `v_avg == 0` 时回退到 `strategy.position_avg_price`：这个 fallback 是否在所有情况下都正确？
- [ ] E2. `show_v_avg` 条件中 `v_avg < strategy.position_avg_price`（做多）：如果只做了真实 DCA 没有虚拟 DCA，`v_avg == strategy.position_avg_price`（近似），是否会频繁闪烁？
- [ ] E3. 信息面板的 `table.new()` 只在 `na(tbl)` 时创建：如果用户在实时图表中更改面板位置，是否需要重建表格？
- [ ] E4. 盈亏标签的 `bar_index + 1` 定位：在图表最后一根 bar 时，`bar_index + 1` 是否超出范围？
- [ ] E5. `_statusOpen()` 中 `deal_dir==0` 但 `strategy.opentrades > 0` 的异常状态：理论上不应发生，但如果状态不同步呢？

### F. 迁移准备度审计

- [ ] F1. JSON alert 的 `detail` 字段：如果 detail 包含双引号 `"` 或换行符 `\n`，JSON 是否破损？
- [ ] F2. Data Window 导出：`v_avg` 在空仓时为 0，`tp_target` 为 na。外部消费者能否区分"无数据"和"值为 0"？
- [ ] F3. `_build_alert()` 中 `deal_dir` 在平仓重置后的值：EXIT_* alert 发送时 `deal_dir` 是否已被重置为 0？如果是，JSON 中 `dir` 会显示 "FLAT" 而非实际方向。
- [ ] F4. Alert 的 `position_size` / `avg_price` 信息缺失：Webhook 消费者需要知道当前仓位大小和均价来验证同步。
- [ ] F5. 是否缺少 strategy.closedtrades 的最后交易 PnL 数据，用于外部追踪？

### G. 代码质量与可维护性

- [ ] G1. 状态重置代码重复 5 次（~8 行 × 5）：Pine v5 是否有更好的模式？
- [ ] G2. 魔法数字：`high * 0.04`（标签偏移）、`/ 1000`（ms→s 转换）等是否应抽取为常量？
- [ ] G3. `winrate` 计算 `strategy.wintrades / total_t * 100`：Pine Script 的整数除法问题。`wintrades` 和 `losstrades` 是 int，相除是否截断为 0？
