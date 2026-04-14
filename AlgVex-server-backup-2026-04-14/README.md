# AlgVex - Algorithmic Crypto Trading System

## Dual-Strategy Automated Trading (Prism + SRP)

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![NautilusTrader](https://img.shields.io/badge/NautilusTrader-1.224.0-green.svg)](https://nautilustrader.io/)
[![License](https://img.shields.io/badge/license-Educational-orange.svg)](LICENSE)

**双策略算法交易系统：Prism 3 维预判评分 + SRP 均值回归。基于 NautilusTrader 框架，BTC/USDT 永续合约自动交易。零 AI API 调用，<1 秒决策延迟。**

---

## 快速部署

> **入口文件是 `main_live.py`，不是 `main.py`**

| 项目 | 值 |
|------|-----|
| 入口文件 | `main_live.py` |
| 服务器路径 | `/home/linuxuser/nautilus_AlgVex` |
| 服务名 | `nautilus-trader` (Prism) / `nautilus-srp` (SRP) |
| 分支 | `main` |
| 网站 | https://algvex.com |

```bash
# 常用命令
sudo systemctl restart nautilus-trader          # 重启 Prism
sudo systemctl restart nautilus-srp             # 重启 SRP
sudo journalctl -u nautilus-trader -f           # Prism 日志
sudo journalctl -u nautilus-srp -f              # SRP 日志
cd /home/linuxuser/nautilus_AlgVex && git pull origin main  # 更新代码
python3 scripts/diagnose_mechanical.py          # Prism 诊断
```

---

## 双策略架构

两个**完全独立**的策略，不同 API key、不同账户、互不干扰：

```
nautilus-trader.service (Prism 策略)      nautilus-srp.service (SRP 策略)
├── main_live.py --mode mechanical        ├── main_live.py --strategy srp
├── 3 维预判评分 + net_raw 阈值决策       ├── VWMA + RSI-MFI 通道 + DCA
├── 141 features → Structure/Div/Flow     ├── Pine Script v5.0 parity
├── TP=4% / SL=5% / DCA 4 层             ├── TP=2.5% / SL=6% / DCA
└── API: BINANCE_API_KEY                  └── API: SRP_BINANCE_API_KEY
```

### Prism 策略（3 维预判评分）

从 13 类数据源提取 141 个 typed features，计算 3 个独立维度：

| 维度 | 数据源 | 输出 |
|------|--------|------|
| **Structure** | Extension Ratio + S/R proximity | 均值回归方向 |
| **Divergence** | RSI/MACD/OBV 4H+30M 背离 | 动量衰竭信号 |
| **Order Flow** | CVD-Price cross, OI, FR, 清算, Top Traders | 微观资金流向 |

**决策阈值**（`net_raw` = 3 维加权合成 -1.0~+1.0）：
- `|net_raw| >= 0.45` → HIGH confidence
- `|net_raw| >= 0.35` → MEDIUM confidence
- `|net_raw| >= 0.20 + zone>=1` → LOW confidence（zone 确认）

### SRP 策略（均值回归）

基于 TradingView Pine Script 的 VWMA + RSI-MFI 通道策略：
- 入场：价格触及 VWMA 下轨 + RSI-MFI < 55
- DCA：价格继续下跌时分层加仓
- 出场：TP=2.5% from avg / SL=6% from avg
- 468 天回测 93.2% 胜率

---

## 核心特性

### 仓位管理
- **DCA 分层入场**：几何 1.5x 放大，最多 4 层（1 base + 3 DCA）
- **固定 SL/TP**：TP=4% / SL=5%（R/R=0.8:1，breakeven WR=55.6%）
- **Trailing Stop**：币安原生 TRAILING_STOP_MARKET，利润 1.5R 激活
- **每层独立保护**：每层独立 SL/TP 订单，LIFO 减仓

### 风险控制
- **风控熔断器**：Drawdown / Daily Loss / Consecutive SL 三维保护
- **Direction Lock**：同方向连续 2 次 SL → 锁定该方向
- **Emergency SL**：SL 提交失败 → 市价兜底 → 3 次重试
- **清算缓冲**：buffer < 5% 自动阻止开仓

### Telegram 双频道
- **私聊控制**：/status, /close, /modify_sl, /pause 等 30+ 命令
- **通知频道**：交易信号、平仓结果、日报/周报（订阅者专用）

### Web 管理界面
- 网站：https://algvex.com
- Prism 评分实时展示（3 维 gauge + net_raw 图表）
- 业绩分析 + 权益曲线
- SRP 策略状态 + 回测结果
- 管理后台：配置编辑、服务控制、系统诊断

---

## 技术栈

| 组件 | 技术 | 版本 |
|------|------|------|
| 交易框架 | NautilusTrader | 1.224.0 |
| 语言 | Python | 3.12+ |
| 交易所 | Binance Futures | REST + WebSocket |
| Web 前端 | Next.js + TypeScript + Tailwind | 14 |
| Web 后端 | FastAPI | 0.115+ |
| 反向代理 | Caddy | auto HTTPS |
| 数据库 | SQLite (web) | aiosqlite |
| 通知 | Telegram Bot API | python-telegram-bot |

---

## 安装

### 前置条件
- Python 3.12+
- Binance Futures 账户 + API Key
- Linux 服务器（推荐 Ubuntu 22.04）

### 一键安装
```bash
curl -fsSL https://raw.githubusercontent.com/FelixWayne0318/AlgVex/main/reinstall.sh | bash
```

### 手动安装
```bash
git clone https://github.com/FelixWayne0318/AlgVex.git
cd AlgVex
chmod +x setup.sh && ./setup.sh
```

### 环境变量（`~/.env.algvex`）
```bash
# 必需
BINANCE_API_KEY=xxx
BINANCE_API_SECRET=xxx
TELEGRAM_BOT_TOKEN=xxx
TELEGRAM_CHAT_ID=xxx

# SRP 策略（独立账户）
SRP_BINANCE_API_KEY=xxx
SRP_BINANCE_API_SECRET=xxx

# 可选
COINALYZE_API_KEY=xxx          # OI + Liquidations 数据
TELEGRAM_NOTIFICATION_BOT_TOKEN=xxx   # 通知频道
TELEGRAM_NOTIFICATION_CHAT_ID=xxx
```

---

## 配置

所有业务参数在 `configs/base.yaml`，分层架构：

```
Layer 1: 代码常量（业务规则，不可配置）
Layer 2: configs/base.yaml（所有业务参数 SSoT）
Layer 3: configs/{env}.yaml（环境覆盖）
Layer 4: ~/.env.algvex（仅 API keys）
```

### 关键参数
| 参数 | 默认值 | 说明 |
|------|--------|------|
| `anticipatory.zone_entry.high_threshold` | 0.45 | HIGH confidence 阈值 |
| `anticipatory.zone_entry.med_threshold` | 0.35 | MEDIUM confidence 阈值 |
| `anticipatory.zone_entry.low_threshold` | 0.20 | LOW confidence 阈值（需 zone 确认） |
| `anticipatory.dca.tp_pct` | 0.04 | TP 4% |
| `anticipatory.dca.sl_pct` | 0.05 | SL 5% |
| `anticipatory.dca.max_real_layers` | 4 | 最多 4 层 DCA |
| `anticipatory.dca.spacing_pct` | 0.03 | DCA 间距 3% |

---

## 运行

```bash
# Prism 策略（生产）
python3 main_live.py --strategy ai --mode mechanical --env production

# SRP 策略（生产）
python3 main_live.py --strategy srp --env production

# 开发模式（1 分钟间隔，DEBUG 日志）
python3 main_live.py --env development --dry-run
```

### systemd 服务
```bash
sudo systemctl enable nautilus-trader nautilus-srp
sudo systemctl start nautilus-trader nautilus-srp
```

---

## 诊断工具

```bash
# Prism 全链路诊断（10 阶段）
python3 scripts/diagnose_mechanical.py

# 回归检测（代码修改后必须运行）
python3 scripts/smart_commit_analyzer.py

# SSoT 逻辑同步检查
python3 scripts/check_logic_sync.py

# 快速诊断
python3 scripts/diagnose.py --quick
```

---

## 代码结构

```
AlgVex/
├── main_live.py              # 入口文件
├── strategy/                 # 策略模块（mixin 架构）
│   ├── ai_strategy.py        # 主策略 + 核心循环
│   ├── trading_logic.py      # SL/TP 计算 + 评估
│   ├── order_execution.py    # 订单执行 + DCA sizing
│   ├── event_handlers.py     # 事件回调
│   ├── position_manager.py   # 层级订单 + 加仓/减仓
│   ├── safety_manager.py     # Emergency SL + 安全保障
│   └── telegram_commands.py  # Telegram 命令
├── agents/                   # Prism 决策引擎
│   ├── mechanical_decide.py  # net_raw 阈值决策
│   ├── report_formatter.py   # compute_anticipatory_scores()
│   ├── multi_agent_analyzer.py # mechanical_analyze() 入口
│   ├── prompt_constants.py   # FEATURE_SCHEMA + REASON_TAGS
│   └── tag_validator.py      # compute_valid_tags()
├── srp_strategy/             # SRP 策略
│   └── srp_strategy.py       # VWMA + RSI-MFI（Pine parity）
├── indicators/               # 技术指标
├── utils/                    # 工具模块（32 文件）
├── configs/                  # 分层配置
├── scripts/                  # 诊断/回测/校准工具（49 脚本）
├── tests/                    # 测试
├── web/                      # Web 管理界面
│   ├── backend/              # FastAPI
│   ├── frontend/             # Next.js
│   └── deploy/               # Caddy + systemd
└── data/                     # 运行时数据
    ├── trading_memory.json
    ├── layer_orders.json
    └── feature_snapshots/
```

---

## 免责声明

本系统仅供教育和研究目的。加密货币交易涉及重大风险，过往业绩不保证未来收益。使用者需自行承担交易风险。

---

## 联系方式

- GitHub: [FelixWayne0318](https://github.com/FelixWayne0318)
- 网站: [algvex.com](https://algvex.com)
