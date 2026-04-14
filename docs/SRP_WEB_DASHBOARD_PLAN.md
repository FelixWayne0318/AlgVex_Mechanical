# SRP Web Dashboard 更新方案 v2

## 核心原则
**尽量调用 NautilusTrader 原生能力，自己写代码越少越好。**

## 已有 NT 能力盘点

| 能力 | 文件 | NT 功能 | 状态 |
|------|------|---------|------|
| **回测引擎** | `scripts/backtest_srp_engine.py` (1012行) | `BacktestEngine` + 模拟 Venue + 真实订单撮合 | ⚠️ 需适配新 Config |
| **仓位报告** | NT 内置 | `engine.trader.generate_positions_report()` | ✅ 可直接用 |
| **订单报告** | NT 内置 | `engine.trader.generate_order_fills_report()` | ✅ 可直接用 |
| **权益曲线** | 已实现 | `_compute_per_bar_equity()` 逐 bar 权益 | ✅ 可直接用 |
| **Funding Rate** | 已实现 | `fetch_funding_rates()` + 成本计算 | ✅ 可直接用 |
| **Sharpe Ratio** | 已实现 | 从权益曲线计算年化 Sharpe | ✅ 可直接用 |
| **True MDD** | 已实现 | 逐 bar max drawdown (含未实现盈亏) | ✅ 可直接用 |
| **参数对比** | 已实现 | `run_compare()` 多参数集并行对比 | ✅ 可直接用 |
| **实盘策略** | `srp_strategy/srp_strategy.py` | NT Strategy 子类，100% Pine parity | ✅ |
| **状态持久化** | NT 策略内 | `data/srp_state.json` | ✅ |
| **K 线获取** | 已实现 | `fetch_klines()` Binance REST | ✅ |
| **数据转换** | 已实现 | `klines_to_dataframe()` + `BarDataWrangler` | ✅ |
| **Pine 对比** | `scripts/pine_tv_comparator.py` | 自写 PineBroker (非 NT) | 保留 |

**结论**: NT 已覆盖回测所需的 90% 功能。只需修复 Config 兼容性即可全部复用。

## 需要做的改动

### Step 0: 修复 backtest_srp_engine.py 适配新 Config

**唯一改动**: `run_backtest()` 里传给 `SRPStrategyConfig` 的参数列表
- 删除: `equity`, `leverage` (已从 Config 移除)
- 更新: 默认值对齐 v5.0 (srp_pct=1.0, dca_min_change_pct=3.0 等)
- 预计改动: ~10 行

### Step 1: 后端 Service 重写

**文件**: `web/backend/services/srp_service.py` (~200 行)

```python
class SRPService:

    def get_parameters(self):
        """读 configs/base.yaml → srp: 段落"""
        # 已有，更新字段即可

    def get_state(self):
        """读 data/srp_state.json (NT 策略写入)"""
        # 更新为 v5.0 字段 + 计算 tp_target, unrealized_pnl

    def get_service_status(self):
        """调用 systemctl is-active nautilus-srp"""
        # 新增

    def run_backtest(self, days=456, capital=1500):
        """调用 backtest_srp_engine.py (NT BacktestEngine)"""
        # subprocess 调用，返回 NT 原生报告:
        #   positions_report, order_fills_report, equity_curve,
        #   funding_rate, sharpe, true_mdd, profit_factor 等
        # 全部来自 NT，不自己算

    def run_parity_check(self, days=456):
        """调用 pine_tv_comparator.py"""
        # subprocess 调用，返回 parity 结果

    def get_backtest_result(self):
        """读最近一次回测结果 JSON"""

    def get_parity_result(self):
        """读最近一次 parity 结果 JSON"""

    def get_walkforward_result(self):
        """读最近一次 walk-forward 结果 JSON (CLI 运行)"""
```

### Step 2: 后端 API 路由

**文件**: `web/backend/api/routes/srp.py`

```
# Public (只读，无风险)
GET  /api/public/srp/parameters          — 策略参数
GET  /api/public/srp/state               — 仓位状态 (v5.0 字段 + tp_target)
GET  /api/public/srp/service-status      — NT 服务运行状态
GET  /api/public/srp/backtest            — 最近回测结果 (NT 报告)
GET  /api/public/srp/parity              — 最近 parity 结果
GET  /api/public/srp/walkforward         — 最近 walk-forward 结果

# Admin (需认证，防 DoS)
POST /api/admin/srp/backtest/run         — 运行 NT 回测 (~30s)
POST /api/admin/srp/parity/run           — 运行 Parity 对比 (~30s)
```

Walk-Forward 不提供 Web API (耗时 ~10 分钟，CLI 运行后读结果)。

### Step 3: backtest_srp_engine.py 添加 JSON 输出

**改动**: 添加 `--json` 和 `--output` 参数
```bash
python3 scripts/backtest_srp_engine.py --days 456 --capital 1500 --json --output data/srp_backtest_result.json
```

输出 NT 原生报告全部字段:
```json
{
  "label": "Pine (Default)",
  "total_pnl": 321.30,
  "funding_cost": 12.45,
  "adjusted_pnl": 308.85,
  "adjusted_return_pct": 20.59,
  "trade_count": 158,
  "wins": 148,
  "losses": 10,
  "win_rate_pct": 93.67,
  "true_mdd_pct": 5.42,
  "max_unrealized_dd_pct": 28.96,
  "sharpe_ratio": 1.85,
  "profit_factor": 1.85,
  "gross_profit": 802.76,
  "gross_loss": 434.17,
  "buy_hold_return_pct": -29.23,
  "bar_count": 21888,
  "elapsed_sec": 28.5,
  "equity_curve": [1500, 1502, ...]
}
```

### Step 4: 前端页面更新

**文件**: `web/frontend/pages/srp.tsx` (~400 行)

#### 4.1 服务状态横幅
🟢 Running / 🔴 Stopped / ⚠️ Failed

#### 4.2 参数卡片 — v5.0 默认值
从 `/api/public/srp/parameters` 读取

#### 4.3 仓位状态卡片
从 `/api/public/srp/state` 读取，显示:
- 方向 | 均价 | 数量 | 浮动 PnL%
- DCA 层数 | Virtual Avg | TP Target
- DCA 入场记录表格

#### 4.4 回测结果卡片 — NT 原生报告
从 `/api/public/srp/backtest` 或 `/api/admin/srp/backtest/run` 读取

**核心指标** (大字):
- Net PnL | Adjusted Return% | Win Rate% | Profit Factor

**NT 独有指标** (表格):
- True MDD% (逐 bar 权益回撤)
- Max Unrealized DD% (浮亏最大回撤)
- Sharpe Ratio (年化)
- Funding Rate Cost
- Buy & Hold 对比

**退出分布**: TP / Band / SL / EOD
**天数选择**: 30 / 90 / 180 / 365 / 456

#### 4.5 Parity 对比卡片
从 `/api/public/srp/parity` 读取
- 每个 metric ✅/❌
- 判定: ✅ PERFECT PARITY

#### 4.6 Walk-Forward 结果卡片 (只读)
从 `/api/public/srp/walkforward` 读取
- SQN (In/Out) | WFE% | Verdict
- 提示: "服务器运行 python3 scripts/walk_forward_srp.py 更新"

#### 4.7 策略说明 — v5.0 逻辑
中英文，匹配 Pine v5.0 (含 DCA 双条件、Virtual DCA、三重退出)

## NT 功能复用率

| 功能 | 来源 | 自写代码 |
|------|------|---------|
| 订单撮合 | NT BacktestEngine | 0 行 |
| 仓位跟踪 | NT positions_report | 0 行 |
| 权益曲线 | _compute_per_bar_equity (已有) | 0 行 |
| Funding Rate | fetch_funding_rates (已有) | 0 行 |
| Sharpe/MDD | extract_results (已有) | 0 行 |
| K 线获取 | fetch_klines (已有) | 0 行 |
| 数据转换 | BarDataWrangler (NT 内置) | 0 行 |
| 策略逻辑 | SRPStrategy (NT Strategy) | 0 行 |
| Config 修复 | run_backtest() 参数 | ~10 行 |
| JSON 输出 | main() 添加 --json | ~20 行 |
| 后端 Service | srp_service.py | ~200 行 (调用+格式化) |
| API 路由 | srp.py | ~50 行 |
| 前端页面 | srp.tsx | ~400 行 |

**NT 复用率: 所有回测计算 100% 来自 NT，自写代码只做"调用+展示"。**

## 实施顺序

| 步骤 | 文件 | 改动量 | 说明 |
|------|------|--------|------|
| 0 | `scripts/backtest_srp_engine.py` | ~30 行 | 适配新 Config + --json 输出 |
| 1 | `web/backend/services/srp_service.py` | ~200 行 | 重写 service |
| 2 | `web/backend/api/routes/srp.py` | ~50 行 | 更新路由 |
| 3 | `web/frontend/pages/srp.tsx` | ~400 行 | 全面更新 |
| 4 | 部署 | `bash web/deploy/redeploy.sh` | 测试 |

## 验收标准

1. 回测数据 100% 来自 NT BacktestEngine (非自写 broker)
2. 含 NT 独有指标: True MDD, Sharpe, Funding Cost, Equity Curve
3. 参数卡片显示 v5.0 默认值
4. 仓位状态含 Virtual DCA + TP Target + 浮动 PnL
5. Parity 验证可一键运行
6. Walk-Forward 结果可查看
7. 服务状态实时显示
8. 计算型 API 需 admin 认证
9. 中英文切换正常
