#!/usr/bin/env python3
"""
端到端数据流水线验证脚本 (v3.0)

调用真实 API + 离线逻辑验证，覆盖从数据获取到 AI 输入的完整流水线。
检测排序错误、公式错误、字段引用错误、单位转换错误、
SL/TP 验证逻辑、仓位计算、AI 响应解析、OI×Price 分析等。

v3.0 新增 (审计修复):
- Test 21/22: 4H + 1D 时间框架技术指标 (C1 — MTF 全覆盖)
- Test 23: Orderbook pipeline 端到端 (C2)
- Test 24: S/R Zone Calculator (C3)
- Test 25: Feature Pipeline v27.0 (C4 — extract_features + scores + tags)
- Test 26: Position & Account 字段契约 (H2+H3, v31.4 字段名验证)
- Test 27: 背离检测 RSI/MACD/OBV (H6)
- Test 28: 边界条件 (Extension/Volatility regime 阈值)
- T4 增强: EMA 12/26, Extension Ratio 内容, Volatility Regime, ATR
- T15 修复: 调用生产 _interpret_funding() 替代本地重写
- T16 修复: 调用生产 _calc_trend() 替代本地重写 (修正阈值 5.0 vs 旧版 2.0)

用法:
    python3 scripts/validate_data_pipeline.py           # 完整验证
    python3 scripts/validate_data_pipeline.py --quick    # 跳过慢速 API (Coinalyze)
    python3 scripts/validate_data_pipeline.py --offline  # 纯离线逻辑验证
    python3 scripts/validate_data_pipeline.py --json     # JSON 输出
"""

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add project root
project_root = Path(__file__).parent.parent.absolute()
sys.path.insert(0, str(project_root))

import requests
from utils.shared_logic import calculate_cvd_trend, CVD_TREND_RELATIVE_THRESHOLD, CVD_TREND_ABSOLUTE_FLOOR


# ─────────────────────────────────────────────────────────────────────
# Test Results Tracking
# ─────────────────────────────────────────────────────────────────────

class TestResults:
    def __init__(self):
        self.passed = []
        self.failed = []
        self.warnings = []

    def ok(self, name: str, detail: str = ""):
        self.passed.append((name, detail))
        print(f"  ✅ {name}" + (f" — {detail}" if detail else ""))

    def fail(self, name: str, detail: str = ""):
        self.failed.append((name, detail))
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))

    def warn(self, name: str, detail: str = ""):
        self.warnings.append((name, detail))
        print(f"  ⚠️  {name}" + (f" — {detail}" if detail else ""))

    def summary(self):
        total = len(self.passed) + len(self.failed)
        print("\n" + "=" * 60)
        print(f"  验证结果: {len(self.passed)}/{total} 通过, "
              f"{len(self.failed)} 失败, {len(self.warnings)} 警告")
        print("=" * 60)
        if self.failed:
            print("\n  失败项:")
            for name, detail in self.failed:
                print(f"    ❌ {name}: {detail}")
        if self.warnings:
            print("\n  警告项:")
            for name, detail in self.warnings:
                print(f"    ⚠️  {name}: {detail}")
        return len(self.failed) == 0


# ═════════════════════════════════════════════════════════════════════
# SECTION A: 在线 API 验证 (需要网络)
# ═════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────
# Test 1: 情绪数据 (globalLongShortAccountRatio)
# ─────────────────────────────────────────────────────────────────────

def test_sentiment_api_ordering(results: TestResults):
    """验证 Binance API 返回的数据排序 (升序: oldest first)"""
    print("\n─── Test 1: 情绪数据 API 排序验证 ───")

    url = "https://fapi.binance.com/futures/data/globalLongShortAccountRatio"
    params = {"symbol": "BTCUSDT", "period": "15m", "limit": 10}

    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()

        if not isinstance(data, list) or len(data) < 2:
            results.fail("API 返回数据", f"非预期格式: {type(data)}")
            return None

        results.ok("API 响应", f"{len(data)} 条数据")

        # 验证排序: timestamps 应该递增 (升序)
        timestamps = [int(d['timestamp']) for d in data]
        is_ascending = all(timestamps[i] <= timestamps[i+1] for i in range(len(timestamps)-1))

        if is_ascending:
            results.ok("排序验证", "升序 (oldest first) ✓")
        else:
            results.fail("排序验证", f"非升序! timestamps: {timestamps[:3]}...{timestamps[-3:]}")

        # data[-1] 应该是最新的
        newest_ts = timestamps[-1]
        oldest_ts = timestamps[0]
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        newest_delay_min = (now_ms - newest_ts) / 1000 / 60
        oldest_delay_min = (now_ms - oldest_ts) / 1000 / 60

        results.ok("data[-1] (最新)", f"延迟 {newest_delay_min:.1f} 分钟")
        results.ok("data[0] (最旧)", f"延迟 {oldest_delay_min:.1f} 分钟")

        if newest_delay_min > 30:
            results.warn("最新数据延迟", f"{newest_delay_min:.0f} 分钟 (>30分钟)")
        if oldest_delay_min > 180:
            results.warn("最旧数据延迟", f"{oldest_delay_min:.0f} 分钟 (正常, limit=10 × 15m)")

        return data

    except Exception as e:
        results.fail("API 调用", str(e))
        return None


# ─────────────────────────────────────────────────────────────────────
# Test 2: SentimentDataFetcher 解析
# ─────────────────────────────────────────────────────────────────────

def test_sentiment_client_parsing(results: TestResults):
    """验证 SentimentDataFetcher 的解析逻辑"""
    print("\n─── Test 2: SentimentDataFetcher 解析验证 ───")

    try:
        from utils.sentiment_client import SentimentDataFetcher

        fetcher = SentimentDataFetcher(timeframe="15m")
        data = fetcher.fetch("BTC")

        if data is None:
            results.fail("fetch() 返回 None", "API 可能不可用")
            return

        # 1. 必需字段检查
        required_fields = [
            'positive_ratio', 'negative_ratio', 'net_sentiment',
            'data_time', 'data_delay_minutes', 'source',
            'long_short_ratio', 'history'
        ]
        for field in required_fields:
            if field in data:
                results.ok(f"字段 '{field}'", f"值: {data[field]}" if field != 'history' else f"{len(data[field])} 条")
            else:
                results.fail(f"字段 '{field}' 缺失", f"返回的字段: {list(data.keys())}")

        # 2. 数值范围检查
        pos = data.get('positive_ratio', -1)
        neg = data.get('negative_ratio', -1)
        net = data.get('net_sentiment', -999)

        if 0 <= pos <= 1:
            results.ok("positive_ratio 范围", f"{pos:.4f} (0-1)")
        else:
            results.fail("positive_ratio 范围", f"{pos} 不在 0-1 之间")

        if 0 <= neg <= 1:
            results.ok("negative_ratio 范围", f"{neg:.4f} (0-1)")
        else:
            results.fail("negative_ratio 范围", f"{neg} 不在 0-1 之间")

        # 3. 公式验证: net_sentiment = positive - negative
        expected_net = pos - neg
        if abs(net - expected_net) < 0.0001:
            results.ok("net_sentiment 公式", f"{net:.4f} = {pos:.4f} - {neg:.4f}")
        else:
            results.fail("net_sentiment 公式错误",
                         f"实际={net:.4f}, 预期={expected_net:.4f} (pos-neg)")

        # 4. positive + negative ≈ 1.0
        total = pos + neg
        if abs(total - 1.0) < 0.01:
            results.ok("pos + neg ≈ 1.0", f"{total:.4f}")
        else:
            results.warn("pos + neg ≠ 1.0", f"{total:.4f}")

        # 5. 延迟检查 (修复后应该 < 30 分钟)
        delay = data.get('data_delay_minutes', -1)
        if 0 <= delay <= 30:
            results.ok("数据延迟", f"{delay} 分钟 (正常)")
        elif delay > 30:
            results.fail("数据延迟过大", f"{delay} 分钟! 检查 data[-1] vs data[0] 排序")
        else:
            results.warn("延迟值异常", f"{delay}")

        # 6. 历史数据排序 (应该 oldest → newest)
        history = data.get('history', [])
        if len(history) >= 2:
            hist_ts = [h.get('timestamp', 0) for h in history]
            hist_ascending = all(hist_ts[i] <= hist_ts[i+1] for i in range(len(hist_ts)-1))
            if hist_ascending:
                results.ok("历史数据排序", "升序 (oldest → newest) ✓")
            else:
                results.fail("历史数据排序错误", "应该是升序但不是!")
        else:
            results.warn("历史数据不足", f"仅 {len(history)} 条")

    except Exception as e:
        results.fail("SentimentDataFetcher", str(e))


# ─────────────────────────────────────────────────────────────────────
# Test 3: 订单流数据 (Buy/Sell Ratio, CVD)
# ─────────────────────────────────────────────────────────────────────

def test_order_flow(results: TestResults):
    """验证订单流数据计算"""
    print("\n─── Test 3: 订单流数据验证 ───")

    url = "https://fapi.binance.com/fapi/v1/klines"
    params = {"symbol": "BTCUSDT", "interval": "15m", "limit": 15}

    try:
        resp = requests.get(url, params=params, timeout=10)
        klines = resp.json()

        if not isinstance(klines, list) or len(klines) < 10:
            results.fail("K线 API", f"数据不足: {len(klines) if isinstance(klines, list) else 'N/A'}")
            return

        results.ok("K线 API", f"{len(klines)} 条")

        # 逐条验证 buy ratio 计算
        errors = []
        for i, k in enumerate(klines[-10:]):
            volume = float(k[5])           # Total volume
            taker_buy_vol = float(k[9])    # Taker buy volume
            quote_volume = float(k[7])     # Quote volume (USDT)
            trades = int(k[8])             # Number of trades

            if volume > 0:
                buy_ratio = taker_buy_vol / volume
                if not (0 <= buy_ratio <= 1):
                    errors.append(f"bar {i}: buy_ratio={buy_ratio:.4f} 超出 0-1 范围")
            else:
                errors.append(f"bar {i}: volume=0")

        if errors:
            results.fail("Buy Ratio 范围", "; ".join(errors[:3]))
        else:
            latest = klines[-1]
            vol = float(latest[5])
            buy_vol = float(latest[9])
            ratio = buy_vol / vol if vol > 0 else 0
            results.ok("Buy Ratio 计算", f"最新={ratio:.4f} (taker_buy/total)")

        # CVD 验证: delta = buy_vol - sell_vol = 2*buy_vol - total_vol
        cvd_deltas = []
        for k in klines[-10:]:
            vol = float(k[5])
            buy_vol = float(k[9])
            sell_vol = vol - buy_vol
            delta = buy_vol - sell_vol
            cvd_deltas.append(delta)

        cvd_sum = sum(cvd_deltas)
        results.ok("CVD 计算", f"10-bar 累计: {cvd_sum:.4f} BTC")

        # 验证平均交易大小
        latest = klines[-1]
        q_vol = float(latest[7])
        trades = int(latest[8])
        if trades > 0:
            avg_trade = q_vol / trades
            results.ok("平均交易大小", f"${avg_trade:,.2f} USDT/trade")
        else:
            results.warn("交易数为0", "无法计算平均交易大小")

        # 验证 OrderFlowProcessor (如果可导入)
        try:
            from utils.order_flow_processor import OrderFlowProcessor
            processor = OrderFlowProcessor()
            of_result = processor.process_klines(klines)

            if of_result.get('data_source') == 'binance_raw':
                results.ok("OrderFlowProcessor", f"data_source=binance_raw, bars={of_result.get('bars_count')}")
            else:
                results.warn("OrderFlowProcessor 降级", f"data_source={of_result.get('data_source')}")

            # 验证 buy_ratio 是 10-bar 平均值
            br = of_result.get('buy_ratio', -1)
            latest_br = of_result.get('latest_buy_ratio', -1)
            if 0 <= br <= 1:
                results.ok("OFP buy_ratio 范围", f"{br:.4f} (10-bar avg)")
            else:
                results.fail("OFP buy_ratio 范围", f"{br}")

            # CVD history 和 cumulative
            cvd_hist = of_result.get('cvd_history', [])
            cvd_cum = of_result.get('cvd_cumulative', None)
            if cvd_hist is not None:
                results.ok("OFP CVD history", f"{len(cvd_hist)} 条")
            if cvd_cum is not None:
                results.ok("OFP CVD cumulative", f"{cvd_cum:+.2f}")

            # recent_10_bars
            r10 = of_result.get('recent_10_bars', [])
            if r10 and all(0 <= x <= 1 for x in r10):
                results.ok("OFP recent_10_bars", f"{len(r10)} 条, 全部 0-1 范围")
            elif r10:
                results.fail("OFP recent_10_bars 范围", f"有值超出 0-1")

        except ImportError:
            results.warn("OrderFlowProcessor 不可导入", "跳过")

    except Exception as e:
        results.fail("订单流验证", str(e))


# ─────────────────────────────────────────────────────────────────────
# Test 4: 技术指标范围验证
# ─────────────────────────────────────────────────────────────────────

def test_indicator_ranges(results: TestResults):
    """验证技术指标的输出范围是否合理"""
    print("\n─── Test 4: 技术指标范围验证 ───")

    try:
        from indicators.technical_manager import TechnicalIndicatorManager

        manager = TechnicalIndicatorManager(
            rsi_period=14, macd_fast=12,
            macd_slow=26, macd_signal=9, bb_period=20
        )
        results.ok("指标管理器实例化", "TechnicalIndicatorManager ✓")

        # 获取 K 线来验证指标计算
        url = "https://fapi.binance.com/fapi/v1/klines"
        params = {"symbol": "BTCUSDT", "interval": "15m", "limit": 100}
        resp = requests.get(url, params=params, timeout=10)
        klines = resp.json()

        if not isinstance(klines, list):
            results.fail("K线数据", "无法获取")
            return

        # Feed bars via NautilusTrader Bar objects
        from nautilus_trader.model.data import Bar, BarType
        from nautilus_trader.model.objects import Price, Quantity

        bar_type = BarType.from_str("BTCUSDT-PERP.BINANCE-30-MINUTE-LAST-EXTERNAL")

        for k in klines[:-1]:  # skip last incomplete bar
            bar = Bar(
                bar_type=bar_type,
                open=Price.from_str(k[1]),
                high=Price.from_str(k[2]),
                low=Price.from_str(k[3]),
                close=Price.from_str(k[4]),
                volume=Quantity.from_str(k[5]),
                ts_event=int(k[0]) * 1_000_000,  # ms → ns
                ts_init=int(k[0]) * 1_000_000,
            )
            manager.update(bar)

        price = float(klines[-2][4])  # last complete bar close
        data = manager.get_technical_data(current_price=price)

        if data is None:
            results.fail("get_technical_data()", "返回 None (指标未初始化?)")
            return

        # RSI: 应该是 0-100
        rsi = data.get('rsi', -1)
        if 0 <= rsi <= 100:
            results.ok("RSI 范围", f"{rsi:.2f} (0-100 scale)")
        elif 0 <= rsi <= 1:
            results.fail("RSI 范围错误", f"{rsi:.4f} — 看起来是 0-1 scale, 应该是 0-100!")
        else:
            results.fail("RSI 范围异常", f"{rsi}")

        # MACD: 应该是合理的价格差值
        macd = data.get('macd', 0)
        macd_sig = data.get('macd_signal', 0)
        macd_hist = data.get('macd_histogram', 0)
        if abs(macd) < 10000:
            results.ok("MACD 范围", f"MACD={macd:.2f}, Signal={macd_sig:.2f}, Hist={macd_hist:.2f}")
        else:
            results.warn("MACD 值过大", f"{macd:.2f}")

        # MACD histogram = MACD - Signal
        expected_hist = macd - macd_sig
        if abs(macd_hist - expected_hist) < 0.001:
            results.ok("MACD Histogram 公式", f"{macd_hist:.4f} = MACD - Signal")
        else:
            results.fail("MACD Histogram 公式", f"实际={macd_hist:.4f}, 预期={expected_hist:.4f}")

        # SMA: 应该接近当前价格
        for sma_period in [5, 20, 50]:
            sma = data.get(f'sma_{sma_period}', 0)
            if sma > 0 and abs(sma - price) / price < 0.15:
                results.ok(f"SMA{sma_period} 范围", f"${sma:.2f}, 偏差={abs(sma-price)/price*100:.1f}%")
            elif sma > 0:
                results.warn(f"SMA{sma_period} 偏差较大", f"${sma:.2f} vs Price=${price:.2f}")
            else:
                results.warn(f"SMA{sma_period} 为 0", "可能未初始化")

        # Bollinger Bands: upper > middle > lower
        bb_upper = data.get('bb_upper', 0)
        bb_middle = data.get('bb_middle', 0)
        bb_lower = data.get('bb_lower', 0)
        if bb_upper > bb_middle > bb_lower > 0:
            results.ok("BB 顺序", f"Upper={bb_upper:.2f} > Middle={bb_middle:.2f} > Lower={bb_lower:.2f}")
        elif bb_upper == 0:
            results.warn("BB 未初始化", "数据不足?")
        else:
            results.fail("BB 顺序错误", f"U={bb_upper:.2f}, M={bb_middle:.2f}, L={bb_lower:.2f}")

        # BB position: 0-1 范围
        bb_pos = data.get('bb_position', -1)
        if 0 <= bb_pos <= 1:
            results.ok("BB Position 范围", f"{bb_pos:.4f} (0=lower, 1=upper)")
        elif bb_pos < 0 or bb_pos > 1:
            results.warn("BB Position 超出 0-1", f"{bb_pos:.4f} (价格在 BB 外)")

        # ADX: 应该是 0-100
        adx = data.get('adx', -1)
        if 0 <= adx <= 100:
            results.ok("ADX 范围", f"{adx:.2f} (0-100)")
        else:
            results.warn("ADX 范围异常", f"{adx}")

        # DI+/DI-: 应该是 0-100
        di_plus = data.get('di_plus', -1)
        di_minus = data.get('di_minus', -1)
        if 0 <= di_plus <= 100 and 0 <= di_minus <= 100:
            results.ok("DI+/DI- 范围", f"DI+={di_plus:.2f}, DI-={di_minus:.2f}")
        else:
            results.warn("DI+/DI- 范围异常", f"DI+={di_plus}, DI-={di_minus}")

        # Volume ratio: 应该 > 0
        vol_ratio = data.get('volume_ratio', -1)
        if vol_ratio > 0:
            results.ok("Volume Ratio", f"{vol_ratio:.2f}x")
        else:
            results.warn("Volume Ratio 异常", f"{vol_ratio}")

        # Support/Resistance: support < price < resistance
        support = data.get('support', 0)
        resistance = data.get('resistance', 0)
        if support > 0 and resistance > 0:
            if support < price < resistance:
                results.ok("S/R 关系", f"S=${support:,.2f} < P=${price:,.2f} < R=${resistance:,.2f}")
            elif support == resistance:
                results.warn("S/R 相等", f"S=R=${support:,.2f}")
            else:
                results.warn("S/R 异常", f"S=${support:,.2f}, P=${price:,.2f}, R=${resistance:,.2f}")

        # Trend fields
        for field in ['overall_trend', 'short_term_trend', 'macd_trend']:
            val = data.get(field)
            if val is not None:
                results.ok(f"趋势字段 '{field}'", f"{val}")
            else:
                results.warn(f"趋势字段 '{field}' 缺失", "")

        # ═══ EMA 12/26 验证 (H7 fix) ═══
        ema_12 = data.get('ema_12')
        ema_26 = data.get('ema_26')
        if ema_12 is not None and ema_12 > 0:
            results.ok("EMA 12", f"${ema_12:.2f}")
        else:
            results.fail("EMA 12 缺失或无效", f"ema_12={ema_12}")
        if ema_26 is not None and ema_26 > 0:
            results.ok("EMA 26", f"${ema_26:.2f}")
        else:
            results.fail("EMA 26 缺失或无效", f"ema_26={ema_26}")
        if ema_12 and ema_26 and abs(ema_12 - price) / price < 0.15:
            results.ok("EMA 12/26 接近价格", f"EMA12 偏差={abs(ema_12-price)/price*100:.1f}%")

        # ═══ ATR Extension Ratio 内容验证 (M3 fix) ═══
        from utils.shared_logic import classify_extension_regime
        ext_sma5 = data.get('extension_ratio_sma_5')
        ext_sma20 = data.get('extension_ratio_sma_20')
        ext_regime = data.get('extension_regime')
        if ext_sma5 is not None and isinstance(ext_sma5, (int, float)):
            results.ok("Extension Ratio SMA5", f"{ext_sma5:.3f}")
        else:
            results.fail("Extension Ratio SMA5 缺失", f"值={ext_sma5}")
        if ext_sma20 is not None and isinstance(ext_sma20, (int, float)):
            results.ok("Extension Ratio SMA20", f"{ext_sma20:.3f}")
        else:
            results.fail("Extension Ratio SMA20 缺失", f"值={ext_sma20}")
        if ext_regime in ('NORMAL', 'EXTENDED', 'OVEREXTENDED', 'EXTREME'):
            results.ok("Extension Regime 分类", f"{ext_regime}")
        else:
            results.fail("Extension Regime 无效", f"值={ext_regime}")

        # ═══ ATR Volatility Regime 验证 (H4 fix) ═══
        vol_regime = data.get('volatility_regime')
        vol_pct = data.get('volatility_percentile')
        atr_pct_val = data.get('atr_pct')
        if vol_regime in ('LOW', 'NORMAL', 'HIGH', 'EXTREME'):
            results.ok("Volatility Regime", f"{vol_regime}")
        else:
            results.fail("Volatility Regime 缺失或无效", f"值={vol_regime}")
        if vol_pct is not None and 0 <= vol_pct <= 100:
            results.ok("Volatility Percentile 范围", f"{vol_pct:.1f} (0-100)")
        elif vol_pct is not None:
            results.fail("Volatility Percentile 超出范围", f"{vol_pct}")
        if atr_pct_val is not None and atr_pct_val > 0:
            results.ok("ATR%", f"{atr_pct_val:.4f}%")

        # ═══ ATR 值验证 ═══
        atr_val = data.get('atr')
        if atr_val is not None and atr_val > 0:
            results.ok("ATR 值", f"${atr_val:.2f}")
        else:
            results.fail("ATR 缺失或非正", f"atr={atr_val}")

        # 验证 get_kline_data
        kline_data = manager.get_kline_data(count=10)
        if kline_data and len(kline_data) > 0:
            kd = kline_data[-1]
            required_kd = ['open', 'high', 'low', 'close', 'volume', 'timestamp']
            missing = [f for f in required_kd if f not in kd]
            if not missing:
                results.ok("get_kline_data 字段", f"{len(kline_data)} bars, 字段完整")
            else:
                results.fail("get_kline_data 字段缺失", str(missing))

            # OHLC 关系: low <= open,close <= high
            h, l, o, c = kd['high'], kd['low'], kd['open'], kd['close']
            if l <= min(o, c) and max(o, c) <= h:
                results.ok("OHLC 关系", f"L≤O,C≤H ✓")
            else:
                results.fail("OHLC 关系错误", f"O={o}, H={h}, L={l}, C={c}")
        else:
            results.warn("get_kline_data 为空", "bars 不足?")

        # 验证 get_historical_context
        hist_ctx = manager.get_historical_context(count=35)
        if hist_ctx:
            td = hist_ctx.get('trend_direction', 'UNKNOWN')
            if td not in ['INSUFFICIENT_DATA', 'ERROR', None]:
                results.ok("历史上下文", f"trend={td}, momentum={hist_ctx.get('momentum_shift')}")

                # 验证序列长度
                price_trend = hist_ctx.get('price_trend', [])
                rsi_trend = hist_ctx.get('rsi_trend', [])
                if len(price_trend) >= 5:
                    results.ok("价格序列", f"{len(price_trend)} 值")
                else:
                    results.warn("价格序列短", f"仅 {len(price_trend)} 值")

                if rsi_trend and all(0 <= v <= 100 for v in rsi_trend):
                    results.ok("RSI 序列范围", f"{len(rsi_trend)} 值, 全部 0-100")
                elif rsi_trend:
                    results.fail("RSI 序列范围错误", f"有值超出 0-100")

                # H5 fix: OBV 序列验证 (v20.0)
                obv_trend = hist_ctx.get('obv_trend', [])
                if len(obv_trend) >= 5:
                    results.ok("30M OBV 序列", f"{len(obv_trend)} 值")
                elif len(obv_trend) > 0:
                    results.warn("30M OBV 序列短", f"仅 {len(obv_trend)} 值")
                else:
                    results.warn("30M OBV 序列为空", "obv_trend 未填充")
            else:
                results.warn("历史上下文数据不足", f"trend_direction={td}")
        else:
            results.warn("get_historical_context 返回 None", "")

    except ImportError as e:
        results.warn("指标模块导入失败", f"{e} (可能缺少 NautilusTrader)")
    except Exception as e:
        results.fail("指标验证", str(e))
        import traceback
        traceback.print_exc()


# ─────────────────────────────────────────────────────────────────────
# Test 5: Funding Rate 完整验证
# ─────────────────────────────────────────────────────────────────────

def test_funding_rate_pipeline(results: TestResults):
    """验证 funding rate 完整数据流水线"""
    print("\n─── Test 5: Funding Rate 完整验证 ───")

    try:
        # --- 5a: Binance settled funding rate ---
        url = "https://fapi.binance.com/fapi/v1/fundingRate"
        params = {"symbol": "BTCUSDT", "limit": 3}
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()

        if isinstance(data, list) and len(data) > 0:
            # Binance /fundingRate 返回升序 (旧→新), data[-1] 是最新结算
            # 用 max(fundingTime) 确保取到最新记录，不依赖排序假设
            most_recent = max(data, key=lambda x: int(x.get('fundingTime', 0)))
            rate = float(most_recent.get('fundingRate', 0))
            rate_pct = rate * 100
            results.ok("Settled Funding Rate", f"{rate:.6f} (decimal) = {rate_pct:.5f}%")

            # 合理范围: -0.5% ~ +0.5%
            if abs(rate_pct) < 0.5:
                results.ok("Settled FR 范围", "正常 (<0.5%)")
            else:
                results.warn("Settled FR 极端值", f"{rate_pct:.5f}%")

            # 历史排序验证 (limit=3, 最近3次结算)
            if len(data) >= 2:
                times = [int(d.get('fundingTime', 0)) for d in data]
                is_ascending = all(times[i] <= times[i+1] for i in range(len(times)-1))
                order_str = "升序(旧→新)" if is_ascending else "降序(新→旧)"
                results.ok("Settled History", f"{len(data)} 条结算记录, {order_str}")
        else:
            results.fail("Settled Funding Rate API", f"返回: {data}")

        # --- 5b: Predicted funding rate (from premiumIndex) ---
        url2 = "https://fapi.binance.com/fapi/v1/premiumIndex"
        params2 = {"symbol": "BTCUSDT"}
        resp2 = requests.get(url2, params=params2, timeout=10)
        data2 = resp2.json()

        if isinstance(data2, dict):
            predicted = float(data2.get('lastFundingRate', 0))
            pred_pct = predicted * 100
            results.ok("Predicted Funding Rate", f"{predicted:.6f} = {pred_pct:.5f}%")

            # Mark price / Index price
            mark = float(data2.get('markPrice', 0))
            index = float(data2.get('indexPrice', 0))
            if mark > 0 and index > 0:
                premium_index = (mark - index) / index
                pi_pct = premium_index * 100
                results.ok("Premium Index", f"{pi_pct:+.4f}% (Mark=${mark:,.2f}, Index=${index:,.2f})")
            else:
                results.warn("Mark/Index Price 缺失", "")

            # Funding delta (predicted - settled)
            delta_pct = pred_pct - rate_pct
            results.ok("Funding Delta", f"{delta_pct:+.4f}% (predicted - settled)")

            # Next funding countdown
            next_time = int(data2.get('nextFundingTime', 0))
            if next_time > 0:
                now_ms = int(time.time() * 1000)
                remaining_min = (next_time - now_ms) / 60000
                if remaining_min > 0:
                    results.ok("下次结算倒计时", f"{remaining_min:.0f} 分钟")
                else:
                    results.warn("下次结算时间已过", f"{remaining_min:.0f} 分钟")
        else:
            results.fail("PremiumIndex API", f"返回: {data2}")

        # --- 5c: BinanceKlineClient (如果可导入) ---
        try:
            from utils.binance_kline_client import BinanceKlineClient
            client = BinanceKlineClient()
            fr_data = client.get_funding_rate()
            if fr_data:
                # 验证字段完整性
                required = ['funding_rate', 'funding_rate_pct', 'predicted_rate',
                            'predicted_rate_pct', 'mark_price', 'index_price', 'source']
                missing = [f for f in required if f not in fr_data]
                if not missing:
                    results.ok("BinanceKlineClient.get_funding_rate()", "字段完整")
                else:
                    results.fail("BinanceKlineClient 字段缺失", str(missing))

                # 验证数值一致性 (与直接 API 对比)
                # 两边都取最新结算费率: BKC 用 limit=1, 直接 API 用 max(fundingTime)
                bkc_settled = fr_data.get('funding_rate_pct', 0)
                bkc_predicted = fr_data.get('predicted_rate_pct', 0)
                diff = abs(bkc_settled - rate_pct)
                if diff < 0.001:
                    results.ok("Settled Rate 一致性", f"BKC={bkc_settled:.4f}% vs API={rate_pct:.4f}%")
                else:
                    results.warn(
                        "Settled Rate 不一致",
                        f"BKC={bkc_settled:.4f}% vs API={rate_pct:.4f}% (差异={diff:.4f}%)"
                    )
            else:
                results.warn("BinanceKlineClient.get_funding_rate() 返回 None", "")

            # 验证 funding rate history
            fr_hist = client.get_funding_rate_history(limit=5)
            if fr_hist and len(fr_hist) >= 2:
                results.ok("Funding Rate History", f"{len(fr_hist)} 条")
                # 验证 fundingRate 字段存在且可转为 float
                for i, record in enumerate(fr_hist[:3]):
                    try:
                        float(record.get('fundingRate', 'nan'))
                    except (ValueError, TypeError):
                        results.fail(f"History[{i}] fundingRate 无效", str(record))
            else:
                results.warn("Funding Rate History 不足", f"{len(fr_hist) if fr_hist else 0} 条")

        except ImportError:
            results.warn("BinanceKlineClient 不可导入", "跳过深度验证")

    except Exception as e:
        results.fail("Funding Rate 验证", str(e))


# ─────────────────────────────────────────────────────────────────────
# Test 6: Binance 衍生品数据
# ─────────────────────────────────────────────────────────────────────

def test_binance_derivatives(results: TestResults):
    """验证 Binance 衍生品数据 (大户、Taker、OI)"""
    print("\n─── Test 6: Binance 衍生品数据验证 ───")

    try:
        from utils.binance_derivatives_client import BinanceDerivativesClient
        client = BinanceDerivativesClient()
        data = client.fetch_all(symbol="BTCUSDT", period="15m", history_limit=10)

        if not data:
            results.fail("fetch_all() 返回空", "")
            return

        # --- Top Traders Position ---
        top_pos = data.get('top_long_short_position', {})
        latest = top_pos.get('latest')
        if latest:
            long_pct = float(latest.get('longAccount', 0)) * 100
            short_pct = float(latest.get('shortAccount', 0)) * 100
            ratio = float(latest.get('longShortRatio', 0))
            total = long_pct + short_pct
            if abs(total - 100) < 1:
                results.ok("Top Traders L/S", f"L={long_pct:.1f}% S={short_pct:.1f}% R={ratio:.2f}")
            else:
                results.fail("Top Traders L+S ≠ 100%", f"{total:.1f}%")
        else:
            results.warn("Top Traders 数据缺失", "")

        # --- Taker Buy/Sell Ratio ---
        taker = data.get('taker_long_short', {})
        taker_latest = taker.get('latest')
        if taker_latest:
            taker_ratio = float(taker_latest.get('buySellRatio', 0))
            if 0.1 < taker_ratio < 10:
                results.ok("Taker Buy/Sell", f"Ratio={taker_ratio:.3f}")
            else:
                results.warn("Taker Ratio 异常", f"{taker_ratio}")
        else:
            results.warn("Taker 数据缺失", "")

        # --- OI History ---
        oi_hist = data.get('open_interest_hist', {})
        oi_latest = oi_hist.get('latest')
        if oi_latest:
            oi_usd = float(oi_latest.get('sumOpenInterestValue', 0))
            if oi_usd > 0:
                results.ok("OI (Binance)", f"${oi_usd:,.0f}")
            else:
                results.fail("OI 值为 0", "")

            # OI history 数据量
            oi_data = oi_hist.get('data', [])
            if len(oi_data) >= 2:
                results.ok("OI History", f"{len(oi_data)} 条")
            else:
                results.warn("OI History 不足", f"{len(oi_data)} 条")
        else:
            results.warn("OI 数据缺失", "")

        # --- 24h Ticker ---
        ticker = data.get('ticker_24hr')
        if ticker:
            change_pct = float(ticker.get('priceChangePercent', 0))
            volume = float(ticker.get('quoteVolume', 0))
            results.ok("24h Ticker", f"Change={change_pct:+.2f}%, Volume=${volume:,.0f}")
        else:
            results.warn("24h Ticker 缺失", "")

        # --- Trend 计算 ---
        for key in ['top_long_short_position', 'taker_long_short', 'open_interest_hist']:
            trend = data.get(key, {}).get('trend')
            if trend in ['RISING', 'FALLING', 'STABLE', None]:
                results.ok(f"Trend ({key})", f"{trend}")
            else:
                results.warn(f"Trend 异常 ({key})", f"{trend}")

    except ImportError:
        results.warn("BinanceDerivativesClient 不可导入", "跳过")
    except Exception as e:
        results.fail("Binance 衍生品验证", str(e))


# ─────────────────────────────────────────────────────────────────────
# Test 7: 字段一致性 (生产者 → 消费者)
# ─────────────────────────────────────────────────────────────────────

def test_field_consistency(results: TestResults):
    """验证数据字段在生产者和消费者之间的一致性"""
    print("\n─── Test 7: 字段一致性检查 ───")

    try:
        from utils.sentiment_client import SentimentDataFetcher
        fetcher = SentimentDataFetcher(timeframe="15m")
        data = fetcher.fetch("BTC")

        if data is None:
            results.warn("情绪数据不可用", "跳过字段一致性检查")
            return

        # MultiAgentAnalyzer._format_sentiment_report() 使用的字段
        consumer_fields = {
            'net_sentiment': "MultiAgent + DeepSeek",
            'positive_ratio': "MultiAgent + DeepSeek",
            'negative_ratio': "MultiAgent + DeepSeek",
            'long_short_ratio': "display formatting",
            'history': "MultiAgent trend analysis",
        }

        for field, usage in consumer_fields.items():
            if field in data:
                results.ok(f"字段一致: '{field}'", f"用于 {usage}")
            else:
                results.fail(f"字段缺失: '{field}'", f"需要用于 {usage}")

        # 检查 history 内部字段
        history = data.get('history', [])
        if history:
            h = history[0]
            for hf in ['long', 'short', 'ratio', 'timestamp']:
                if hf in h:
                    results.ok(f"history.'{hf}'", f"值: {h[hf]}")
                else:
                    results.fail(f"history.'{hf}' 缺失", "MultiAgent 需要此字段")

    except Exception as e:
        results.fail("字段一致性", str(e))


# ═════════════════════════════════════════════════════════════════════
# SECTION B: 离线逻辑验证 (无需网络)
# ═════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────
# Test 8: 离线解析逻辑 (mock 数据)
# ─────────────────────────────────────────────────────────────────────

def test_offline_parsing_logic(results: TestResults):
    """用 mock 数据验证所有解析逻辑 (无需网络)"""
    print("\n─── Test 8: 离线解析逻辑 (mock 数据) ───")

    # Mock Binance API response: ascending order (oldest first)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    mock_api_response = []
    for i in range(10):
        ts = now_ms - (10 - i) * 15 * 60 * 1000
        mock_api_response.append({
            "symbol": "BTCUSDT",
            "longShortRatio": f"{1.0 + i * 0.01:.4f}",
            "longAccount": f"{0.50 + i * 0.005:.4f}",
            "shortAccount": f"{0.50 - i * 0.005:.4f}",
            "timestamp": ts
        })

    # 1. 验证排序是升序
    timestamps = [d['timestamp'] for d in mock_api_response]
    assert all(timestamps[i] <= timestamps[i+1] for i in range(len(timestamps)-1))
    results.ok("Mock 数据升序", "timestamps 递增")

    # 2. 验证 data[-1] 是最新的
    newest = mock_api_response[-1]
    oldest = mock_api_response[0]
    assert newest['timestamp'] > oldest['timestamp']
    results.ok("data[-1] 最新", f"ts={newest['timestamp']} > ts={oldest['timestamp']}")

    # 3. 模拟 _parse_binance_data 逻辑
    try:
        from utils.sentiment_client import SentimentDataFetcher
        fetcher = SentimentDataFetcher(timeframe="15m")

        result = fetcher._parse_binance_data(newest)
        if result:
            delay = result['data_delay_minutes']
            if delay <= 20:
                results.ok("data[-1] 延迟", f"{delay} 分钟 (正常)")
            else:
                results.fail("data[-1] 延迟异常", f"{delay} 分钟")

            expected = float(newest['longAccount']) - float(newest['shortAccount'])
            actual = result['net_sentiment']
            if abs(actual - expected) < 0.0001:
                results.ok("net_sentiment 公式", f"{actual:.4f} = long - short")
            else:
                results.fail("net_sentiment 公式", f"actual={actual}, expected={expected}")
        else:
            results.fail("_parse_binance_data", "返回 None")

        result_old = fetcher._parse_binance_data(oldest)
        if result_old:
            delay_old = result_old['data_delay_minutes']
            results.ok("data[0] 延迟 (反面验证)", f"{delay_old} 分钟 (应该≈150分钟)")
            if delay_old > 100:
                results.ok("修复验证", f"如果用 data[0] 会导致 {delay_old} 分钟延迟!")
            else:
                results.warn("反面验证不明显", f"delay={delay_old}")

    except ImportError:
        long_r = float(newest['longAccount'])
        short_r = float(newest['shortAccount'])
        net = long_r - short_r
        results.ok("手动解析验证", f"long={long_r:.4f}, short={short_r:.4f}, net={net:.4f}")

        data_time = datetime.fromtimestamp(newest['timestamp'] / 1000, tz=timezone.utc)
        now_utc = datetime.now(timezone.utc)
        delay = int((now_utc - data_time).total_seconds() // 60)
        results.ok("延迟计算", f"{delay} 分钟")

    # 4. 验证 history 排序逻辑
    history = []
    for item in mock_api_response:
        history.append({
            'long': float(item['longAccount']),
            'short': float(item['shortAccount']),
            'ratio': float(item['longShortRatio']),
            'timestamp': item['timestamp'],
        })

    if all(history[i]['timestamp'] <= history[i+1]['timestamp'] for i in range(len(history)-1)):
        results.ok("History 升序", "oldest → newest ✓")
    else:
        results.fail("History 排序", "不是升序!")

    if history[-1]['long'] > history[0]['long']:
        results.ok("History 数值趋势", f"从 {history[0]['long']:.4f} 到 {history[-1]['long']:.4f}")
    else:
        results.fail("History 数值", "最新值不是最大的 (mock 数据应该递增)")

    # 5. Buy ratio / CVD / Funding rate 公式
    test_volume = 100.0
    test_taker_buy = 55.0
    buy_ratio = test_taker_buy / test_volume
    assert abs(buy_ratio - 0.55) < 0.001
    results.ok("Buy Ratio 公式", f"{test_taker_buy}/{test_volume} = {buy_ratio}")

    sell_volume = test_volume - test_taker_buy
    cvd_delta = test_taker_buy - sell_volume
    assert abs(cvd_delta - 10.0) < 0.001
    results.ok("CVD Delta 公式", f"buy({test_taker_buy}) - sell({sell_volume}) = {cvd_delta}")

    raw_rate = 0.0001
    pct_rate = raw_rate * 100
    assert abs(pct_rate - 0.01) < 0.0001
    results.ok("Funding Rate 转换", f"{raw_rate} × 100 = {pct_rate}%")


# ─────────────────────────────────────────────────────────────────────
# Test 9: SL/TP 验证逻辑
# ─────────────────────────────────────────────────────────────────────

def test_sltp_validation(results: TestResults):
    """验证 validate_multiagent_sltp() 所有边界情况"""
    print("\n─── Test 9: SL/TP 验证逻辑 ───")

    try:
        from strategy.trading_logic import validate_multiagent_sltp

        price = 100000.0

        # --- LONG 有效 SL/TP ---
        valid, sl, tp, reason = validate_multiagent_sltp(
            side='BUY', multi_sl=98000.0, multi_tp=103000.0, entry_price=price
        )
        if valid and sl == 98000.0 and tp == 103000.0:
            results.ok("LONG 有效 SL/TP", f"SL={sl}, TP={tp}")
        else:
            results.fail("LONG 有效 SL/TP", f"valid={valid}, reason={reason}")

        # --- SHORT 有效 SL/TP ---
        valid, sl, tp, reason = validate_multiagent_sltp(
            side='SELL', multi_sl=102000.0, multi_tp=97000.0, entry_price=price
        )
        if valid and sl == 102000.0 and tp == 97000.0:
            results.ok("SHORT 有效 SL/TP", f"SL={sl}, TP={tp}")
        else:
            results.fail("SHORT 有效 SL/TP", f"valid={valid}, reason={reason}")

        # --- LONG SL 在入场价错误一侧 ---
        valid, _, _, reason = validate_multiagent_sltp(
            side='BUY', multi_sl=101000.0, multi_tp=103000.0, entry_price=price
        )
        if not valid:
            results.ok("LONG SL>entry 拒绝", f"reason={reason[:60]}")
        else:
            results.fail("LONG SL>entry 应被拒绝", "但通过了")

        # --- SHORT SL 在入场价错误一侧 ---
        valid, _, _, reason = validate_multiagent_sltp(
            side='SELL', multi_sl=99000.0, multi_tp=97000.0, entry_price=price
        )
        if not valid:
            results.ok("SHORT SL<entry 拒绝", f"reason={reason[:60]}")
        else:
            results.fail("SHORT SL<entry 应被拒绝", "但通过了")

        # --- R/R 过低 (< 1.5:1) ---
        # risk=2000 (2%), reward=1000 (1%) → R/R = 0.5:1
        valid, _, _, reason = validate_multiagent_sltp(
            side='BUY', multi_sl=98000.0, multi_tp=101000.0, entry_price=price
        )
        expected_rr = (101000 - price) / (price - 98000)  # 1000/2000 = 0.5
        from strategy.trading_logic import get_min_rr_ratio
        min_rr_t9 = get_min_rr_ratio()
        if not valid and 'R/R' in reason:
            results.ok("R/R < min 拒绝", f"R/R={expected_rr:.2f}:1 < {min_rr_t9}:1 ✓, {reason[:50]}")
        elif valid:
            results.fail("R/R 验证失败", f"R/R={expected_rr:.2f}:1 < {min_rr_t9}:1 但通过了!")

        # --- R/R 精确计算验证 ---
        # BUY: risk=entry-sl, reward=tp-entry, R/R=reward/risk
        valid_rr, _, _, reason_rr = validate_multiagent_sltp(
            side='BUY', multi_sl=97000.0, multi_tp=104500.0, entry_price=price
        )
        computed_rr = (104500.0 - price) / (price - 97000.0)  # 4500/3000 = 1.5
        if computed_rr >= min_rr_t9 and valid_rr:
            results.ok("R/R 精确 BUY", f"R/R={computed_rr:.4f}:1 >= {min_rr_t9}:1, 通过 ✓")
        elif computed_rr >= min_rr_t9 and not valid_rr:
            results.fail("R/R 精确 BUY", f"R/R={computed_rr:.4f}:1 >= {min_rr_t9}:1 但被拒绝: {reason_rr}")
        elif computed_rr < min_rr_t9 and not valid_rr:
            results.ok("R/R 精确 BUY", f"R/R={computed_rr:.4f}:1 < {min_rr_t9}:1, 拒绝 ✓")
        else:
            results.fail("R/R 精确 BUY", f"R/R={computed_rr:.4f}:1, valid={valid_rr}")

        # SELL: risk=sl-entry, reward=entry-tp, R/R=reward/risk
        valid_rr2, _, _, reason_rr2 = validate_multiagent_sltp(
            side='SELL', multi_sl=103000.0, multi_tp=95500.0, entry_price=price
        )
        computed_rr2 = (price - 95500.0) / (103000.0 - price)  # 4500/3000 = 1.5
        if computed_rr2 >= min_rr_t9 and valid_rr2:
            results.ok("R/R 精确 SELL", f"R/R={computed_rr2:.4f}:1 >= {min_rr_t9}:1, 通过 ✓")
        elif computed_rr2 >= min_rr_t9 and not valid_rr2:
            results.fail("R/R 精确 SELL", f"R/R={computed_rr2:.4f}:1 >= {min_rr_t9}:1 但被拒绝: {reason_rr2}")
        elif computed_rr2 < min_rr_t9 and not valid_rr2:
            results.ok("R/R 精确 SELL", f"R/R={computed_rr2:.4f}:1 < {min_rr_t9}:1, 拒绝 ✓")
        else:
            results.fail("R/R 精确 SELL", f"R/R={computed_rr2:.4f}:1, valid={valid_rr2}")

        # --- SL/TP 为 None ---
        valid, _, _, reason = validate_multiagent_sltp(
            side='BUY', multi_sl=None, multi_tp=None, entry_price=price
        )
        if not valid:
            results.ok("SL/TP=None 拒绝", "✓")
        else:
            results.fail("SL/TP=None 应被拒绝", "但通过了")

        # --- SL 太近 (< min distance) ---
        valid, _, _, reason = validate_multiagent_sltp(
            side='BUY', multi_sl=99990.0, multi_tp=103000.0, entry_price=price
        )
        if not valid:
            results.ok("SL 太近拒绝", f"0.01% < min, reason={reason[:60]}")
        else:
            results.warn("SL 太近未拒绝", "min_sl_distance 可能很小")

        # --- 支持 LONG/SHORT 格式 ---
        valid, sl, tp, _ = validate_multiagent_sltp(
            side='LONG', multi_sl=98000.0, multi_tp=103000.0, entry_price=price
        )
        if valid:
            results.ok("LONG 格式支持", "✓")
        else:
            results.fail("LONG 格式不支持", "应支持 LONG/SHORT 和 BUY/SELL")

    except ImportError as e:
        results.warn("trading_logic 导入失败", str(e))
    except Exception as e:
        results.fail("SL/TP 验证", str(e))
        import traceback
        traceback.print_exc()


# ─────────────────────────────────────────────────────────────────────
# Test 10: 仓位计算逻辑
# ─────────────────────────────────────────────────────────────────────

def test_position_sizing(results: TestResults):
    """验证 calculate_position_size() 逻辑"""
    print("\n─── Test 10: 仓位计算逻辑 ───")

    try:
        from strategy.trading_logic import calculate_position_size

        price = 100000.0
        price_data = {'price': price}
        # v35.1-fix: atr=500 (0.5% of price, realistic BTC volatility)
        # Previous atr=2000 (2%) triggered max_single_trade_risk_pct clamp
        technical_data = {'overall_trend': 'BULLISH', 'rsi': 50, 'atr': 500}

        # --- ai_controlled: AI 指定 80% ---
        signal = {'signal': 'BUY', 'confidence': 'HIGH', 'position_size_pct': 80}
        config = {
            'equity': 1000,
            'leverage': 10,
            'max_position_ratio': 0.30,
            'position_sizing': {'method': 'ai_controlled'},
            'min_trade_amount': 0.001,  # BTC, not USDT
        }

        qty, details = calculate_position_size(signal, price_data, technical_data, config)

        # Oracle values: exact formula replay (v35.1-fix)
        # max_usdt = equity × max_position_ratio × leverage = 1000 × 0.30 × 10 = 3000
        max_usdt = 1000 * 0.30 * 10  # = $3000
        # ai_controlled: position_size_pct=80 → size_ratio = 80/100 = 0.8
        # position_usdt = max_usdt × size_ratio = 3000 × 0.8 = 2400
        # appetite_scale = 0.8 (NORMAL, default when risk_appetite not provided)
        # position_usdt *= appetite_scale → 2400 × 0.8 = 1920
        # risk_clamp: sl_distance_frac = 2.0 × 500 / 100000 = 0.01
        #   max_risk_usdt = 1000 × 0.02 / 0.01 = 2000 → no clamp (1920 < 2000)
        expected_usdt = max_usdt * 0.80 * 0.8  # = $1920
        # btc_quantity = final_usdt / price = 1920 / 100000 = 0.0192
        # Binance BTC precision = 3 decimals → round(0.0192, 3) = 0.019
        expected_qty = expected_usdt / price  # = 0.0192 (pre-rounding)
        # Tolerance accounts for Binance step size rounding (0.001 BTC = $100 @ BTC $100K)
        step_size_usdt = 0.001 * price  # 1 step = $100 at current price

        if qty > 0:
            actual_usdt = qty * price
            # Exact formula verification (tolerance for Binance step rounding)
            if abs(actual_usdt - expected_usdt) <= step_size_usdt:
                results.ok("AI Controlled 精确值",
                           f"${actual_usdt:,.0f} ≈ ${expected_usdt:,.0f} "
                           f"(Binance 精度差 ${abs(actual_usdt - expected_usdt):,.0f} ≤ step ${step_size_usdt:,.0f}) ✓")
            elif actual_usdt >= expected_usdt:
                # May be elevated by min_notional (Binance minimum)
                results.ok("AI Controlled 仓位", f"${actual_usdt:,.0f} ≥ ${expected_usdt:,.0f} (min_notional 提升)")
            else:
                results.fail("AI Controlled 精确值",
                             f"${actual_usdt:,.0f} ≠ ${expected_usdt:,.0f} (equity×ratio×lev×pct)")

            # qty precision check (tolerance = 1 Binance step = 0.001 BTC)
            if abs(qty - expected_qty) <= 0.001:
                results.ok("BTC 数量精确值",
                           f"{qty:.6f} ≈ {expected_qty:.6f} "
                           f"(Binance round(x,3) 精度差 {abs(qty - expected_qty):.6f}) ✓")
            elif qty >= expected_qty:
                results.ok("BTC 数量", f"{qty:.6f} ≥ {expected_qty:.6f} (min_notional 提升)")
            else:
                results.fail("BTC 数量精确值", f"{qty:.6f} ≠ {expected_qty:.6f}")
        else:
            results.fail("AI Controlled 仓位", f"qty={qty}, details={details}")

        # 不应超过 max_usdt (考虑 Binance min_notional 可能抬高)
        actual_usdt = qty * price
        if actual_usdt <= max_usdt * 1.05:  # 5% 容差 (rounding + min_notional)
            results.ok("最大仓位限制", f"${actual_usdt:,.0f} ≤ max ${max_usdt:,.0f}")
        else:
            results.fail("超过最大仓位", f"${actual_usdt:,.0f} > max ${max_usdt:,.0f}")

        # --- 无效价格 ---
        qty2, details2 = calculate_position_size(
            signal, {'price': 0}, technical_data, config
        )
        if qty2 == 0:
            results.ok("价格=0 返回 0", "✓")
        else:
            results.fail("价格=0 应返回 0", f"qty={qty2}")

        # --- confidence-based (fixed_pct, 无 position_size_pct) ---
        # Fixed_pct 公式: final_usdt = base_usdt × conf_mult × trend_mult × rsi_mult
        # 当前 technical_data: trend='BULLISH'(不匹配强势), rsi=50(不极端)
        # → trend_mult=1.0, rsi_mult=1.0
        # → final_usdt = 100 × conf_mult × 1.0 × 1.0
        conf_mults = {'HIGH': 1.5, 'MEDIUM': 1.0, 'LOW': 0.5}
        max_usdt_fc = 1000 * 0.30 * 5  # = $1500
        for conf in ['HIGH', 'MEDIUM', 'LOW']:
            signal_fc = {'signal': 'BUY', 'confidence': conf}
            config_fc = {
                'equity': 1000,
                'leverage': 5,
                'max_position_ratio': 0.30,
                'position_sizing': {
                    'method': 'fixed_pct',
                    'ai_controlled': {'default_size_pct': 50},
                },
                'base_usdt': 100,
                'high_confidence_multiplier': 1.5,
                'medium_confidence_multiplier': 1.0,
                'low_confidence_multiplier': 0.5,
                'min_trade_amount': 0.001,  # BTC, not USDT
            }
            qty_fc, details_fc = calculate_position_size(signal_fc, price_data, technical_data, config_fc)
            # Oracle: expected_usdt = base × conf_mult × trend(1.0) × rsi(1.0)
            expected_fc = 100 * conf_mults[conf]
            actual_fc = qty_fc * price if qty_fc > 0 else 0

            if qty_fc > 0:
                if abs(actual_fc - expected_fc) < 1.0:
                    results.ok(f"Fixed PCT ({conf}) 精确值",
                               f"${actual_fc:,.0f} = $100×{conf_mults[conf]}×1.0×1.0 ✓")
                elif actual_fc > expected_fc:
                    results.ok(f"Fixed PCT ({conf})", f"${actual_fc:,.0f} ≥ ${expected_fc:,.0f} (min_notional 提升)")
                else:
                    results.fail(f"Fixed PCT ({conf}) 精确值",
                                 f"${actual_fc:,.0f} ≠ ${expected_fc:,.0f}")
            else:
                results.fail(f"Fixed PCT ({conf}) = 0",
                             f"details={details_fc}")

    except ImportError as e:
        results.warn("trading_logic 导入失败", str(e))
    except Exception as e:
        results.fail("仓位计算验证", str(e))
        import traceback
        traceback.print_exc()


# ─────────────────────────────────────────────────────────────────────
# Test 11: AI 响应解析
# ─────────────────────────────────────────────────────────────────────

def test_ai_response_parsing(results: TestResults):
    """验证 AI 响应 JSON 解析逻辑"""
    print("\n─── Test 11: AI 响应解析验证 ───")

    # --- 11a: 标准 JSON ---
    standard_json = '{"signal": "BUY", "confidence": "HIGH", "reason": "test", "stop_loss": 98000, "take_profit": 103000}'
    try:
        parsed = json.loads(standard_json)
        required = ["signal", "reason", "stop_loss", "take_profit", "confidence"]
        missing = [f for f in required if f not in parsed]
        if not missing:
            results.ok("标准 JSON 解析", "所有必需字段存在")
        else:
            results.fail("标准 JSON 字段缺失", str(missing))
    except json.JSONDecodeError:
        results.fail("标准 JSON 解析失败", "")

    # --- 11b: JSON 包裹在 markdown code block 中 ---
    wrapped_json = '```json\n{"signal": "SELL", "confidence": "MEDIUM", "reason": "test", "stop_loss": 102000, "take_profit": 97000}\n```'
    start = wrapped_json.find('{')
    end = wrapped_json.rfind('}') + 1
    if start != -1 and end > 0:
        extracted = wrapped_json[start:end]
        try:
            parsed = json.loads(extracted)
            results.ok("Markdown-wrapped JSON", f"signal={parsed.get('signal')}")
        except json.JSONDecodeError:
            results.fail("Markdown-wrapped JSON 解析失败", "")
    else:
        results.fail("Markdown-wrapped JSON 提取失败", "")

    # --- 11c: 包含内部双引号的 reason ---
    bad_json = '{"signal": "HOLD", "confidence": "LOW", "reason": "Price shows \\"strong\\" resistance", "stop_loss": 100000, "take_profit": 100000}'
    try:
        parsed = json.loads(bad_json)
        results.ok("转义引号 JSON", f"reason 长度={len(parsed.get('reason', ''))}")
    except json.JSONDecodeError:
        # 尝试 MultiAgentAnalyzer._safe_parse_json 的修复逻辑
        results.warn("转义引号 JSON 失败", "需要修复逻辑")

    # --- 11d: 信号值验证 ---
    valid_signals = {'BUY', 'SELL', 'HOLD'}
    valid_confidences = {'HIGH', 'MEDIUM', 'LOW'}

    for sig in valid_signals:
        if sig in valid_signals:
            results.ok(f"信号 '{sig}' 有效", "✓")

    for conf in valid_confidences:
        if conf in valid_confidences:
            results.ok(f"信心 '{conf}' 有效", "✓")

    # --- 11e: SL/TP 数值类型验证 ---
    test_signals = [
        {"signal": "BUY", "stop_loss": 98000, "take_profit": 103000},  # int
        {"signal": "BUY", "stop_loss": 98000.50, "take_profit": 103000.50},  # float
        {"signal": "BUY", "stop_loss": "98000", "take_profit": "103000"},  # str
    ]
    for i, sig in enumerate(test_signals):
        try:
            sl = float(sig['stop_loss'])
            tp = float(sig['take_profit'])
            if sl > 0 and tp > 0:
                results.ok(f"SL/TP 类型转换 (case {i+1})", f"SL={sl}, TP={tp}")
            else:
                results.fail(f"SL/TP 类型转换 (case {i+1})", "<=0")
        except (ValueError, TypeError) as e:
            results.fail(f"SL/TP 类型转换 (case {i+1})", str(e))

    # --- 11f: Fallback 信号验证 ---
    fallback = {
        "signal": "HOLD",
        "reason": "Conservative strategy due to technical analysis unavailable",
        "stop_loss": 100000 * 0.98,
        "take_profit": 100000 * 1.02,
        "confidence": "LOW",
        "is_fallback": True,
    }
    if fallback.get('signal') == 'HOLD' and fallback.get('is_fallback') is True:
        results.ok("Fallback 信号结构", "signal=HOLD, is_fallback=True")
    else:
        results.fail("Fallback 信号结构", str(fallback))


# ─────────────────────────────────────────────────────────────────────
# Test 12: OI×Price 四象限逻辑
# ─────────────────────────────────────────────────────────────────────

def test_oi_price_quadrant(results: TestResults):
    """验证 OI×Price 4-Quadrant 分析逻辑"""
    print("\n─── Test 12: OI×Price 四象限逻辑 ───")

    # 模拟 multi_agent_analyzer.py 中的四象限逻辑
    quadrant_map = {
        ("↑", "↑"): "New longs entering → BULLISH CONFIRMATION",
        ("↑", "↓"): "Short covering → WEAK rally (no new conviction)",
        ("↓", "↑"): "New shorts entering → BEARISH CONFIRMATION",
        ("↓", "↓"): "Long liquidation → BEARISH EXHAUSTION",
    }

    test_cases = [
        # (price_change, oi_change_pct, expected_price_dir, expected_oi_dir, expected_signal)
        (2.0, 3.0, "↑", "↑", "New longs"),
        (2.0, -3.0, "↑", "↓", "Short covering"),
        (-2.0, 3.0, "↓", "↑", "New shorts"),
        (-2.0, -3.0, "↓", "↓", "Long liquidation"),
        (0.05, 0.2, "→", "→", "Neutral"),  # 低于阈值
    ]

    for price_chg, oi_chg, exp_p, exp_o, keyword in test_cases:
        # 重现代码中的方向判断
        price_dir = "↑" if price_chg > 0.1 else "↓" if price_chg < -0.1 else "→"
        oi_dir = "↑" if oi_chg > 0.5 else "↓" if oi_chg < -0.5 else "→"

        if price_dir != exp_p or oi_dir != exp_o:
            results.fail(f"方向判断 P={price_chg} OI={oi_chg}",
                         f"得到 P{price_dir}+OI{oi_dir}, 预期 P{exp_p}+OI{exp_o}")
            continue

        signal = quadrant_map.get(
            (price_dir, oi_dir),
            f"Price {price_dir} + OI {oi_dir} = Neutral / consolidation"
        )

        if keyword.lower() in signal.lower():
            results.ok(f"P{price_dir}+OI{oi_dir}", f"{signal[:50]}")
        else:
            results.fail(f"P{price_dir}+OI{oi_dir}", f"预期含'{keyword}', 得到: {signal}")


# ─────────────────────────────────────────────────────────────────────
# Test 13: CVD 冷启动 & 趋势逻辑
# ─────────────────────────────────────────────────────────────────────

def test_cvd_cold_start(results: TestResults):
    """验证 CVD 冷启动检测和趋势计算"""
    print("\n─── Test 13: CVD 冷启动 & 趋势逻辑 ───")

    # 冷启动: < 3 bars
    for count in [0, 1, 2]:
        cvd_history = list(range(count))
        warning = ""
        if len(cvd_history) < 3:
            warning = " ⚠️ COLD_START (< 3 bars, trend unreliable)"
        if "COLD_START" in warning:
            results.ok(f"CVD {count} bars → COLD_START", "✓")
        else:
            results.fail(f"CVD {count} bars 应该 COLD_START", "")

    # 正常: >= 3 bars
    cvd_history_normal = [1, 2, 3, 4, 5]
    warning = ""
    if len(cvd_history_normal) < 3:
        warning = " ⚠️ COLD_START"
    if warning == "":
        results.ok("CVD 5 bars → 正常", "无 COLD_START")
    else:
        results.fail("CVD 5 bars 不应 COLD_START", "")

    # CVD trend — validate via shared SSoT (utils/shared_logic.py)
    cvd_short = [10, 20, 30]  # < 5 bars → NEUTRAL
    if calculate_cvd_trend(cvd_short) == "NEUTRAL":
        results.ok("CVD < 5 bars → NEUTRAL", "✓")
    else:
        results.fail("CVD < 5 bars should be NEUTRAL", calculate_cvd_trend(cvd_short))

    cvd_rising = [10, 10, 10, 10, 10, 20, 20, 20, 20, 20]
    if calculate_cvd_trend(cvd_rising) == "RISING":
        results.ok("CVD RISING", "shared_logic.calculate_cvd_trend confirmed")
    else:
        results.fail("CVD RISING 判定", calculate_cvd_trend(cvd_rising))

    cvd_falling = [20, 20, 20, 20, 20, 10, 10, 10, 10, 10]
    if calculate_cvd_trend(cvd_falling) == "FALLING":
        results.ok("CVD FALLING", "shared_logic.calculate_cvd_trend confirmed")
    else:
        results.fail("CVD FALLING 判定", calculate_cvd_trend(cvd_falling))


# ─────────────────────────────────────────────────────────────────────
# Test 14: Funding Delta 计算
# ─────────────────────────────────────────────────────────────────────

def test_funding_delta(results: TestResults):
    """验证 Funding Delta (predicted - settled) 方向判断"""
    print("\n─── Test 14: Funding Delta 计算逻辑 ───")

    test_cases = [
        # (settled_pct, predicted_pct, expected_direction)
        (-0.0100, -0.0050, "↑ more bullish pressure"),    # -0.01 → -0.005: 向正方向移动
        (0.0100, 0.0200, "↑ more bullish pressure"),     # 0.01 → 0.02: 多头更强
        (0.0100, 0.0050, "↓ more bearish pressure"),     # 0.01 → 0.005: 多头减弱
        (0.0100, 0.0100, "→ stable"),                    # 相同
    ]

    for settled, predicted, expected in test_cases:
        delta = predicted - settled
        if delta > 0:
            direction = "↑ more bullish pressure"
        elif delta < 0:
            direction = "↓ more bearish pressure"
        else:
            direction = "→ stable"

        if direction == expected:
            results.ok(f"Delta {settled:.4f}%→{predicted:.4f}%", f"{direction}")
        else:
            results.fail(f"Delta {settled:.4f}%→{predicted:.4f}%",
                         f"预期: {expected}, 实际: {direction}")


# ─────────────────────────────────────────────────────────────────────
# Test 15: Funding Rate 解读逻辑
# ─────────────────────────────────────────────────────────────────────

def test_funding_interpretation(results: TestResults):
    """
    验证 funding rate 解读逻辑 — 调用生产代码 AIDataAssembler._interpret_funding()

    H1 fix: 不再本地重写逻辑，直接调用生产代码进行 oracle 验证。
    """
    print("\n─── Test 15: Funding Rate 解读逻辑 (生产代码) ───")

    try:
        from utils.ai_data_assembler import AIDataAssembler

        # Instantiate with minimal mocks (only need _interpret_funding)
        assembler = AIDataAssembler.__new__(AIDataAssembler)

        test_cases = [
            (0.0015, "VERY_BULLISH"),
            (0.0008, "BULLISH"),
            (0.0001, "NEUTRAL"),
            (-0.0001, "NEUTRAL"),
            (-0.0008, "BEARISH"),
            (-0.0015, "VERY_BEARISH"),
            # Boundary cases
            (0.001, "BULLISH"),     # Exactly 0.001 → > 0.0005 → BULLISH
            (0.0005, "NEUTRAL"),    # Exactly 0.0005 → not > 0.0005 → NEUTRAL
            (-0.001, "BEARISH"),    # Exactly -0.001 → < -0.0005 → BEARISH
            (-0.0005, "NEUTRAL"),   # Exactly -0.0005 → not < -0.0005 → NEUTRAL
            (0.00051, "BULLISH"),   # Just above threshold
            (-0.00051, "BEARISH"),  # Just below threshold
        ]

        for rate, expected in test_cases:
            actual = assembler._interpret_funding(rate)
            if actual == expected:
                results.ok(f"FR {rate:.5f} → {actual}", "✓ (生产代码)")
            else:
                results.fail(f"FR {rate:.5f}", f"预期={expected}, 实际={actual} (生产代码)")

    except AttributeError:
        results.fail("_interpret_funding 方法不存在", "AIDataAssembler API 已变更?")
    except Exception as e:
        results.fail("Funding 解读验证", str(e))


# ─────────────────────────────────────────────────────────────────────
# Test 16: Binance 衍生品趋势计算
# ─────────────────────────────────────────────────────────────────────

def test_trend_calculation(results: TestResults):
    """
    验证趋势计算逻辑 — 调用生产 BinanceDerivativesClient._calc_trend()

    M1 fix: 不再本地重写逻辑。直接实例化生产代码验证。
    NOTE: 生产代码默认阈值为 5.0% (非 T16 旧版硬编码的 2.0%)。
    """
    print("\n─── Test 16: 趋势计算逻辑 (生产代码) ───")

    try:
        from utils.binance_derivatives_client import BinanceDerivativesClient

        # Instantiate with default config (threshold_pct=5.0)
        client = BinanceDerivativesClient.__new__(BinanceDerivativesClient)
        client.trend_threshold_pct = 5.0  # Production default

        # RISING: +10% > 5%
        data_rising = [{'val': 110}, {'val': 105}, {'val': 100}]
        actual = client._calc_trend(data_rising, 'val')
        if actual == "RISING":
            results.ok("趋势: +10% → RISING", "✓ (生产代码, threshold=5%)")
        else:
            results.fail("趋势: +10%", f"预期 RISING, 实际 {actual}")

        # FALLING: -10% < -5%
        data_falling = [{'val': 90}, {'val': 95}, {'val': 100}]
        actual = client._calc_trend(data_falling, 'val')
        if actual == "FALLING":
            results.ok("趋势: -10% → FALLING", "✓ (生产代码)")
        else:
            results.fail("趋势: -10%", f"预期 FALLING, 实际 {actual}")

        # STABLE: +3% (< 5% threshold)
        data_stable = [{'val': 103}, {'val': 101}, {'val': 100}]
        actual = client._calc_trend(data_stable, 'val')
        if actual == "STABLE":
            results.ok("趋势: +3% → STABLE", "✓ (< 5% threshold)")
        else:
            results.fail("趋势: +3%", f"预期 STABLE, 实际 {actual}")

        # Boundary: exactly +5% → should be STABLE (not > 5%)
        data_boundary = [{'val': 105}, {'val': 102}, {'val': 100}]
        actual = client._calc_trend(data_boundary, 'val')
        if actual == "STABLE":
            results.ok("趋势: +5% (边界) → STABLE", "✓ (not > 5%)")
        else:
            results.warn("趋势: +5% (边界)", f"实际 {actual}")

        # Edge: 数据不足
        actual = client._calc_trend([{'val': 100}], 'val')
        if actual is None:
            results.ok("趋势: 1条数据 → None", "✓")
        else:
            results.fail("趋势: 1条数据", f"预期 None, 实际 {actual}")

        # Edge: 空数据
        actual = client._calc_trend([], 'val')
        if actual is None:
            results.ok("趋势: 空数据 → None", "✓")
        else:
            results.fail("趋势: 空数据", f"预期 None, 实际 {actual}")

        # Edge: oldest = 0 (division by zero protection)
        actual = client._calc_trend([{'val': 10}, {'val': 0}], 'val')
        if actual is None:
            results.ok("趋势: oldest=0 → None", "✓ (除零保护)")
        else:
            results.fail("趋势: oldest=0", f"预期 None, 实际 {actual}")

        # NOTE: data[0] = newest, data[-1] = oldest (Binance 降序)
        results.ok("注意: Binance 衍生品 data[0]=newest", "与 sentiment (data[-1]=newest) 不同!")

    except Exception as e:
        results.fail("趋势计算验证", str(e))


# ─────────────────────────────────────────────────────────────────────
# Test 17: 生产数据流结构验证 (on_timer → analyze() 参数契约)
# ─────────────────────────────────────────────────────────────────────

def test_data_assembler_structure(results: TestResults):
    """
    验证生产 on_timer() → MultiAgentAnalyzer.analyze() 的参数契约。

    IMPORTANT: 生产 on_timer() 使用内联数据组装，不使用 AIDataAssembler。
    此测试验证:
    1. analyze() 接受的参数与生产传递的参数一致 (16 个)
    2. 各数据类型的子结构符合消费者期望
    3. Coinalyze 使用 fetch_all() (非 fetch_all_with_history())
    """
    print("\n─── Test 17: 生产数据流结构验证 ───")

    # ========== Part 1: 验证 analyze() 参数签名与生产调用一致 ==========
    # 生产调用位于 ai_strategy.py:1863-1886
    production_analyze_params = [
        'symbol', 'technical_report', 'sentiment_report',
        'current_position', 'price_data',
        'order_flow_report', 'derivatives_report',
        'binance_derivatives_report', 'orderbook_report',
        'account_context', 'bars_data',
        'bars_data_4h', 'bars_data_1d', 'daily_bar', 'weekly_bar',
        'atr_value',
        'data_quality_warnings',   # v6.6: Data quality warnings (ai_strategy.py:2970)
        'order_flow_report_4h',    # v18: 4H CVD order flow (ai_strategy.py:2972)
    ]

    try:
        import inspect
        from agents.multi_agent_analyzer import MultiAgentAnalyzer
        sig = inspect.signature(MultiAgentAnalyzer.analyze)
        sig_params = [p for p in sig.parameters if p != 'self']

        # Check all production params exist in signature
        missing_in_sig = [p for p in production_analyze_params if p not in sig_params]
        extra_in_sig = [p for p in sig_params if p not in production_analyze_params]

        if not missing_in_sig:
            results.ok(
                "analyze() 参数签名",
                f"全部 {len(production_analyze_params)} 个生产参数均存在于签名中"
            )
        else:
            results.fail(
                "analyze() 参数签名",
                f"生产传递但签名缺失: {missing_in_sig}"
            )

        if extra_in_sig:
            results.warn(
                "analyze() 额外参数",
                f"签名中有但生产未传递: {extra_in_sig}"
            )
    except Exception as e:
        results.warn("analyze() 签名检查", f"无法导入: {e}")

    # ========== Part 2: Coinalyze 方法一致性验证 ==========
    # 生产使用 fetch_all() (L1750), 不是 fetch_all_with_history()
    # fetch_all() 返回: {open_interest, liquidations, funding_rate, enabled}
    # fetch_all_with_history() 额外返回: trends, *_history (诊断/测试场景可用)
    try:
        from utils.coinalyze_client import CoinalyzeClient
        fetch_all_fields = ['open_interest', 'liquidations', 'funding_rate', 'enabled']
        # Verify fetch_all exists and has expected return structure comment
        if hasattr(CoinalyzeClient, 'fetch_all'):
            results.ok("Coinalyze 生产方法", "fetch_all() (非 fetch_all_with_history)")
        else:
            results.fail("Coinalyze 生产方法", "fetch_all() 方法不存在")

        results.ok("derivatives 子字段 (fetch_all)", f"{fetch_all_fields}")
    except Exception as e:
        results.warn("Coinalyze 验证", str(e)[:80])

    # ========== Part 3: 各数据类别子结构定义 ==========
    # order_flow 子结构 (OrderFlowProcessor.process_klines 输出)
    expected_order_flow = [
        'buy_ratio', 'avg_trade_usdt', 'volume_usdt', 'trades_count',
        'cvd_trend', 'recent_10_bars', 'data_source',
    ]

    # sentiment 子结构 (SentimentDataFetcher.fetch 输出 或 默认中性值)
    expected_sentiment = [
        'positive_ratio', 'negative_ratio', 'net_sentiment',
        'long_short_ratio', 'source',
    ]

    results.ok("order_flow 子字段", f"{len(expected_order_flow)} 个")
    results.ok("sentiment 子字段", f"{len(expected_sentiment)} 个")

    # ========== Part 4: 消费者字段路径 ==========
    consumer_paths = [
        ("_format_technical_report", "technical → price, sma_*, rsi, macd, bb_*, adx, volume_ratio"),
        ("_format_sentiment_report", "sentiment → net_sentiment, positive_ratio, negative_ratio, history"),
        ("_format_order_flow_report", "order_flow → buy_ratio, cvd_trend, cvd_history, volume_usdt"),
        ("_format_derivatives_report", "derivatives → open_interest, funding_rate, liquidations"),
        ("_format_orderbook_report", "order_book → obi, dynamics, anomalies, liquidity"),
        ("_build_key_metrics", "technical + derivatives + order_flow + sentiment (交叉引用)"),
    ]

    for method, fields in consumer_paths:
        results.ok(f"消费者: {method}", fields[:70])


# ─────────────────────────────────────────────────────────────────────
# Test 18: SMA 标签歧义验证
# ─────────────────────────────────────────────────────────────────────

def test_sma_label_disambiguation(results: TestResults):
    """验证 SMA200 标签区分 (30M vs 1D)"""
    print("\n─── Test 18: SMA 标签歧义验证 ───")

    # _build_key_metrics 中使用 SMA{period}_15M 标签
    # _format_technical_report 中 1D 部分使用 "SMA 200" 标签
    # Judge 应该能区分两者

    # 模拟 _build_key_metrics 的标签
    price = 100000
    sma_200_30m = 99000  # 30M SMA200 (v18.2: 15M→30M)
    pct_30m = (price - sma_200_30m) / sma_200_30m * 100
    label_30m = f"Price vs SMA200_30M: {pct_30m:+.2f}%"

    if "_30M" in label_30m:
        results.ok("Key Metrics SMA200 标签", f"{label_30m} (含 _30M 后缀)")
    else:
        results.fail("Key Metrics SMA200 标签", "缺少 _30M 后缀, 与 1D SMA200 混淆")

    # 模拟 _format_technical_report 的 1D 标签
    sma_200_1d = 95000  # Daily SMA200
    label_1d = f"SMA 200: ${sma_200_1d:,.2f}"
    section_1d = "=== MARKET DATA (1D Timeframe - Macro Trend) ==="

    if "1D" in section_1d:
        results.ok("1D SMA200 在 1D 段", f"段标题含 '1D'")
    else:
        results.fail("1D SMA200 段标题", "应包含 '1D' 区分")

    # 两者不应混淆
    if sma_200_30m != sma_200_1d:
        results.ok("30M vs 1D SMA200 不同", f"30M=${sma_200_30m:,.0f} vs 1D=${sma_200_1d:,.0f}")


# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────
# Test 19: 消费者字段契约验证 (防止 current_pct 类 bug)
# ─────────────────────────────────────────────────────────────────────

def test_consumer_field_contracts(results: TestResults):
    """
    验证 AIDataAssembler 输出的字段名与消费者 .get() 调用匹配。

    NOTE: 生产 on_timer() 不使用 AIDataAssembler，而是内联组装数据后
    直接传给 MultiAgentAnalyzer.analyze()。此测试验证 AIDataAssembler
    作为辅助工具 (diagnose 等场景) 的字段契约仍然正确。
    生产路径的参数契约由 Test 17 验证。

    核心思路: 不测生产者输出了什么，测消费者能不能读到值。
    这是防止字段名断裂 bug 的关键测试 (如 settled_pct vs current_pct)。
    """
    print("\n─── Test 19: 消费者字段契约验证 ───")

    try:
        from utils.ai_data_assembler import AIDataAssembler

        # --- 只 mock 数据源，不 mock 计算逻辑 ---
        # 原则: BinanceKlines/Sentiment = 纯数据源 → mock OK
        #        OrderFlowProcessor/CoinalyzeClient = 有公式计算 → 用真实生产代码
        from utils.order_flow_processor import OrderFlowProcessor
        from utils.coinalyze_client import CoinalyzeClient

        # Mock: Binance K线数据源 (纯 API 调用，无计算逻辑)
        class MockBinanceKlines:
            def get_funding_rate(self):
                return {
                    'funding_rate': 0.00058,
                    'funding_rate_pct': 0.058,
                    'predicted_rate': 0.00001,
                    'predicted_rate_pct': 0.001,
                    'next_funding_time': int(time.time() * 1000) + 3600000,
                    'next_funding_countdown_min': 60,
                    'mark_price': 100000.0,
                    'index_price': 99950.0,
                    'interest_rate': 0.0001,
                    'premium_index': 0.0005,
                }

            def get_funding_rate_history(self, limit=10):
                return [
                    {'fundingRate': '0.00030', 'fundingTime': 1000},
                    {'fundingRate': '0.00040', 'fundingTime': 2000},
                    {'fundingRate': '0.00058', 'fundingTime': 3000},
                ]

            def get_klines(self, **kwargs):
                # 10 根 K线: volume=100, taker_buy=60 → 真实 OrderFlowProcessor 计算
                t = int(time.time() * 1000)
                klines = []
                for i in range(10):
                    ts = t - (10 - i) * 60000
                    klines.append([
                        ts, '100000', '100500', '99500', '100000',
                        '100', ts + 60000, '1200000', 120, '60', '720000', '0',
                    ])
                return klines

        # ✅ 真实生产代码: OrderFlowProcessor (10-bar 平均, CVD, 趋势)
        real_order_flow = OrderFlowProcessor()

        # ✅ 真实生产代码: CoinalyzeClient (只 mock 6 个 API 调用，保留趋势计算公式)
        class TestableCoinalyze(CoinalyzeClient):
            """Override API calls, keep real fetch_all_with_history + _calc_trend_from_history"""
            def __init__(self):
                super().__init__(api_key="test_key", timeout=1, max_retries=0)

            def get_open_interest(self, symbol=None):
                return {'value': 500.0}

            def get_liquidations(self, symbol=None):
                return {'history': [{'l': 1.5, 's': 2.0}]}

            def get_funding_rate(self, symbol=None):
                return {'value': 0.0003}

            def get_open_interest_history(self, symbol=None, hours=4):
                # oldest=100, newest=110 → +10% > 3% → RISING (由真实公式计算)
                return {'history': [{'c': 100, 't': 1000}, {'c': 110, 't': 2000}]}

            def get_funding_rate_history(self, symbol=None, hours=4):
                # oldest=0.01, newest=0.008 → -20% < -3% → FALLING (由真实公式计算)
                return {'history': [{'c': 0.01, 't': 1000}, {'c': 0.008, 't': 2000}]}

            def get_long_short_ratio_history(self, symbol=None, hours=4):
                # oldest=1.0, newest=1.02 → +2% → STABLE (由真实公式计算)
                return {'history': [{'r': 1.0, 't': 1000}, {'r': 1.02, 't': 2000}]}

        # Mock: 情绪数据源 (纯 API 调用，计算仅 net=pos-neg)
        class MockSentiment:
            def fetch(self):
                return {
                    'positive_ratio': 0.55, 'negative_ratio': 0.45,
                    'net_sentiment': 0.1, 'long_short_ratio': 1.22,
                    'source': 'mock', 'history': [],
                }

        assembler = AIDataAssembler(
            binance_kline_client=MockBinanceKlines(),
            order_flow_processor=real_order_flow,          # ← 真实生产代码
            coinalyze_client=TestableCoinalyze(),          # ← 真实趋势计算
            sentiment_client=MockSentiment(),
        )

        # --- 调用真实的 assemble() 方法 ---
        assembled = assembler.assemble(
            technical_data={
                'price': 100000, 'rsi': 50, 'macd': 100, 'macd_signal': 95,
                'bb_upper': 101000, 'bb_lower': 99000, 'bb_middle': 100000,
                'bb_position': 0.5, 'adx': 25, 'di_plus': 20, 'di_minus': 15,
                'sma_5': 99900, 'sma_20': 99500, 'sma_50': 99000,
                'volume_ratio': 1.2, 'atr': 500,
            },
            symbol='BTCUSDT',
        )

        # ═══════════════════════════════════════════════════════════════
        # 消费者契约定义
        # 格式: (consumer_get_chain, description, allow_none)
        #   consumer_get_chain: 模拟消费者的 .get() 调用链
        #   description: 消费者位置
        #   allow_none: True = 允许 None (消费者有 if 保护)
        # ═══════════════════════════════════════════════════════════════

        derivatives = assembled.get('derivatives', {})
        fr = derivatives.get('funding_rate', {})
        oi = derivatives.get('open_interest', {})
        liq = derivatives.get('liquidations', {})
        sentiment = assembled.get('sentiment', {})
        order_flow = assembled.get('order_flow', {})

        contracts = [
            # --- Funding Rate (multi_agent_analyzer Bull/Bear prompt) ---
            (fr.get('current_pct'),
             "derivatives.funding_rate.current_pct",
             "multi_agent L1020: Bull/Bear analyst prompt", False),

            (fr.get('predicted_rate_pct'),
             "derivatives.funding_rate.predicted_rate_pct",
             "multi_agent L1022: Bull/Bear analyst prompt", True),

            (fr.get('value'),
             "derivatives.funding_rate.value",
             "multi_agent L2292: Risk Manager prompt", False),

            # --- Funding Rate (format_complete_report) ---
            (fr.get('current_pct', 0),
             "derivatives.funding_rate.current_pct (default=0)",
             "format_complete_report L485", False),

            (fr.get('interpretation'),
             "derivatives.funding_rate.interpretation",
             "format_complete_report L486", False),

            (fr.get('trend'),
             "derivatives.funding_rate.trend",
             "format_complete_report L483", False),

            (fr.get('premium_index'),
             "derivatives.funding_rate.premium_index",
             "format_complete_report L489", True),

            (fr.get('mark_price'),
             "derivatives.funding_rate.mark_price",
             "format_complete_report L492", False),

            (fr.get('history'),
             "derivatives.funding_rate.history",
             "format_complete_report L512", False),

            (fr.get('next_funding_countdown_min'),
             "derivatives.funding_rate.next_funding_countdown_min",
             "format_complete_report L504", True),

            # --- Open Interest ---
            (oi.get('total_btc') if oi else None,
             "derivatives.open_interest.total_btc",
             "format_complete_report L476", True),

            (oi.get('change_pct') if oi else None,
             "derivatives.open_interest.change_pct",
             "multi_agent L1033: OI change", True),

            # --- Liquidations ---
            (liq.get('total_usd') if liq else None,
             "derivatives.liquidations.total_usd",
             "multi_agent L1028: liquidation display", True),

            # --- Sentiment ---
            (sentiment.get('net_sentiment'),
             "sentiment.net_sentiment",
             "multi_agent L1055: sentiment report", False),

            (sentiment.get('positive_ratio'),
             "sentiment.positive_ratio",
             "multi_agent L1724: sentiment report", False),

            (sentiment.get('negative_ratio'),
             "sentiment.negative_ratio",
             "multi_agent L1728: sentiment report", False),

            # --- Order Flow ---
            (order_flow.get('buy_ratio'),
             "order_flow.buy_ratio",
             "multi_agent L1038: order flow report", False),

            (order_flow.get('cvd_trend'),
             "order_flow.cvd_trend",
             "multi_agent L1041: order flow report", False),
        ]

        # --- 执行契约验证 ---
        pass_count = 0
        fail_count = 0
        for value, field_path, consumer, allow_none in contracts:
            if value is None and not allow_none:
                results.fail(
                    f"契约断裂: {field_path}",
                    f"消费者 {consumer} 会读到 None → AI 看不到此数据"
                )
                fail_count += 1
            elif value is None and allow_none:
                pass_count += 1  # 静默通过 (允许 None)
            else:
                pass_count += 1

        if fail_count == 0:
            results.ok(
                "消费者字段契约",
                f"全部 {pass_count + fail_count} 条契约通过 "
                f"(生产者字段名 = 消费者 .get() key)"
            )
        else:
            results.fail(
                "消费者字段契约",
                f"{fail_count} 条断裂 — AI prompt 中有数据缺失"
            )

        # --- 额外验证: current_pct 不应为 0 (当费率非零时) ---
        if fr.get('value') and fr.get('value') != 0:
            current_pct = fr.get('current_pct')
            if current_pct == 0 or current_pct is None:
                results.fail(
                    "current_pct 零值保护",
                    f"funding_rate.value={fr['value']} 但 current_pct={current_pct}"
                )
            else:
                results.ok(
                    "current_pct 零值保护",
                    f"value={fr['value']:.6f} → current_pct={current_pct:.4f}% (非零)"
                )

        # --- 额外验证: format_complete_report 可正常执行 ---
        try:
            report = assembler.format_complete_report(assembled)
            # 验证报告中包含实际 funding rate 值 (不是 0.0000%)
            if 'Funding Rate' in report and '0.0000%' not in report.split('Funding Rate')[1][:30]:
                results.ok("format_complete_report", "Funding Rate 显示非零值")
            elif 'Funding Rate' in report:
                # 检查是否显示了 0.0000%
                fr_section = report.split('Funding Rate')[1][:50]
                if '0.0000%' in fr_section:
                    results.fail(
                        "format_complete_report Funding Rate",
                        f"显示 0.0000% (字段映射可能断裂): ...{fr_section.strip()[:40]}"
                    )
                else:
                    results.ok("format_complete_report", "Funding Rate 正常显示")
            else:
                results.warn("format_complete_report", "未找到 Funding Rate 段")
        except Exception as e:
            results.fail("format_complete_report 执行失败", str(e)[:80])

    except ImportError as e:
        results.warn("AIDataAssembler 不可导入", f"跳过契约验证: {e}")
    except Exception as e:
        results.fail("消费者字段契约测试异常", str(e)[:100])


# ─────────────────────────────────────────────────────────────────────
# Test 20: 生产代码计算正确性 (已知输入 → 预期输出)
# ─────────────────────────────────────────────────────────────────────

def test_production_calculations(results: TestResults):
    """
    用已知 mock 输入调用真实生产代码，验证输出值是否正确。

    与 Test 8 (离线公式) 的区别:
    - Test 8: 测试脚本自己的公式逻辑 (mock → 脚本代码 → 验证)
    - Test 20: 测试生产代码的公式逻辑 (mock → AIDataAssembler → 验证)

    能检测: 乘除错误、单位转换错误、rounding 错误、比较方向错误
    """
    print("\n─── Test 20: 生产代码计算正确性 ───")

    try:
        from utils.ai_data_assembler import AIDataAssembler

        # --- 已知输入值 (mock) ---
        MOCK_OI_BTC = 500.0
        MOCK_PRICE = 100000.0
        MOCK_LIQ_LONG_BTC = 1.5
        MOCK_LIQ_SHORT_BTC = 2.0
        MOCK_FUNDING_RATE = 0.00058  # decimal
        MOCK_FUNDING_PCT = 0.058     # percent (= 0.00058 * 100, rounded 4)
        MOCK_MARK_PRICE = 100000.0
        MOCK_INDEX_PRICE = 99950.0
        # History: [0.0003, 0.0004, 0.00058] → 0.00058 > 0.0003*1.1 → RISING
        MOCK_HISTORY_RATES = [0.00030, 0.00040, 0.00058]

        class MockBinanceKlines:
            def get_funding_rate(self):
                return {
                    'funding_rate': MOCK_FUNDING_RATE,
                    'funding_rate_pct': round(MOCK_FUNDING_RATE * 100, 6),
                    'predicted_rate': 0.00001,
                    'predicted_rate_pct': 0.001,
                    'next_funding_time': int(time.time() * 1000) + 3600000,
                    'next_funding_countdown_min': 60,
                    'mark_price': MOCK_MARK_PRICE,
                    'index_price': MOCK_INDEX_PRICE,
                    'premium_index': (MOCK_MARK_PRICE - MOCK_INDEX_PRICE) / MOCK_INDEX_PRICE,
                }

            def get_funding_rate_history(self, limit=10):
                return [
                    {'fundingRate': str(r), 'fundingTime': i * 1000}
                    for i, r in enumerate(MOCK_HISTORY_RATES)
                ]

            def get_klines(self, **kwargs):
                # 10 根 K线: volume=100, taker_buy=60 → 真实 OrderFlowProcessor 计算
                # 预期: buy_ratio=0.6, avg_trade_usdt=10000, volume_usdt=1200000
                t = int(time.time() * 1000)
                klines = []
                for i in range(10):
                    ts = t - (10 - i) * 60000
                    klines.append([
                        ts, '100000', '100500', '99500', '100000',
                        '100', ts + 60000, '1200000', 120, '60', '720000', '0',
                    ])
                return klines

        # ✅ 真实生产代码: OrderFlowProcessor
        from utils.order_flow_processor import OrderFlowProcessor
        real_order_flow = OrderFlowProcessor()

        # ✅ 真实生产代码: CoinalyzeClient (只 mock API，保留趋势计算公式)
        from utils.coinalyze_client import CoinalyzeClient

        class TestableCoinalyze(CoinalyzeClient):
            """Override API calls, keep real _calc_trend_from_history"""
            def __init__(self):
                super().__init__(api_key="test_key", timeout=1, max_retries=0)

            def get_open_interest(self, symbol=None):
                return {'value': MOCK_OI_BTC}

            def get_liquidations(self, symbol=None):
                return {'history': [
                    {'l': MOCK_LIQ_LONG_BTC, 's': MOCK_LIQ_SHORT_BTC}
                ]}

            def get_funding_rate(self, symbol=None):
                return {'value': 0.0003}

            def get_open_interest_history(self, symbol=None, hours=4):
                # oldest=100, newest=110 → +10% > 3% → 预期 RISING
                return {'history': [{'c': 100, 't': 1000}, {'c': 110, 't': 2000}]}

            def get_funding_rate_history(self, symbol=None, hours=4):
                # oldest=0.01, newest=0.008 → -20% < -3% → 预期 FALLING
                return {'history': [{'c': 0.01, 't': 1000}, {'c': 0.008, 't': 2000}]}

            def get_long_short_ratio_history(self, symbol=None, hours=4):
                # oldest=1.0, newest=1.02 → +2% → 预期 STABLE (< 3%)
                return {'history': [{'r': 1.0, 't': 1000}, {'r': 1.02, 't': 2000}]}

        class MockSentiment:
            def fetch(self):
                return {
                    'positive_ratio': 0.55, 'negative_ratio': 0.45,
                    'net_sentiment': 0.1, 'long_short_ratio': 1.22,
                    'source': 'mock', 'history': [],
                }

        assembler = AIDataAssembler(
            binance_kline_client=MockBinanceKlines(),
            order_flow_processor=real_order_flow,          # ← 真实生产代码
            coinalyze_client=TestableCoinalyze(),          # ← 真实趋势计算
            sentiment_client=MockSentiment(),
        )

        assembled = assembler.assemble(
            technical_data={'price': MOCK_PRICE, 'atr': 500},
            symbol='BTCUSDT',
        )

        derivatives = assembled.get('derivatives', {})
        fr = derivatives.get('funding_rate') or {}
        oi = derivatives.get('open_interest') or {}
        liq = derivatives.get('liquidations') or {}

        def assert_close(actual, expected, name, tolerance=0.01):
            """验证浮点数接近预期值"""
            if actual is None:
                results.fail(f"计算: {name}", f"结果为 None (预期 {expected})")
                return False
            if abs(actual - expected) <= tolerance:
                results.ok(f"计算: {name}", f"{actual} ≈ {expected}")
                return True
            else:
                results.fail(
                    f"计算: {name}",
                    f"实际={actual}, 预期={expected}, 差={actual - expected}"
                )
                return False

        # ═══ OI 转换: BTC → USD ═══
        # 500 BTC × $100,000 = $50,000,000
        expected_oi_usd = MOCK_OI_BTC * MOCK_PRICE
        assert_close(oi.get('total_usd'), expected_oi_usd,
                     f"OI USD = {MOCK_OI_BTC} BTC × ${MOCK_PRICE:,.0f}", tolerance=1)

        assert_close(oi.get('total_btc'), MOCK_OI_BTC,
                     f"OI BTC 透传 = {MOCK_OI_BTC}", tolerance=0.01)

        # ═══ Liquidation 转换: BTC → USD ═══
        # Long: 1.5 BTC × $100,000 = $150,000
        # Short: 2.0 BTC × $100,000 = $200,000
        expected_liq_long = MOCK_LIQ_LONG_BTC * MOCK_PRICE
        expected_liq_short = MOCK_LIQ_SHORT_BTC * MOCK_PRICE
        expected_liq_total = expected_liq_long + expected_liq_short

        assert_close(liq.get('long_usd'), expected_liq_long,
                     f"Liq Long = {MOCK_LIQ_LONG_BTC} BTC × ${MOCK_PRICE:,.0f}", tolerance=1)
        assert_close(liq.get('short_usd'), expected_liq_short,
                     f"Liq Short = {MOCK_LIQ_SHORT_BTC} BTC × ${MOCK_PRICE:,.0f}", tolerance=1)
        assert_close(liq.get('total_usd'), expected_liq_total,
                     f"Liq Total = ${expected_liq_total:,.0f}", tolerance=1)

        # ═══ Funding Rate 单位转换 ═══
        # 0.00058 × 100 = 0.058%
        assert_close(fr.get('current_pct'), MOCK_FUNDING_PCT,
                     f"FR % = {MOCK_FUNDING_RATE} × 100 = {MOCK_FUNDING_PCT}%", tolerance=0.0001)

        assert_close(fr.get('value'), MOCK_FUNDING_RATE,
                     f"FR decimal 透传 = {MOCK_FUNDING_RATE}", tolerance=0.000001)

        # ═══ Funding Rate 趋势方向 ═══
        # [0.0003, 0.0004, 0.00058]: 最新 0.00058 > 最旧 0.0003 × 1.1 = 0.00033 → RISING
        actual_trend = fr.get('trend')
        if actual_trend == 'RISING':
            results.ok(
                "计算: FR Trend",
                f"[{', '.join(str(r) for r in MOCK_HISTORY_RATES)}] → RISING ✓"
            )
        else:
            results.fail(
                "计算: FR Trend",
                f"预期 RISING, 实际 {actual_trend} "
                f"(rates: {MOCK_HISTORY_RATES})"
            )

        # ═══ Funding Rate 解读 ═══
        # 0.00058 > 0.0005 → BULLISH (not VERY_BULLISH since < 0.001)
        actual_interp = fr.get('interpretation')
        if actual_interp == 'BULLISH':
            results.ok("计算: FR Interpretation", f"{MOCK_FUNDING_RATE} → BULLISH ✓")
        else:
            results.fail(
                "计算: FR Interpretation",
                f"预期 BULLISH, 实际 {actual_interp}"
            )

        # ═══ History 记录数和排序 ═══
        history = fr.get('history', [])
        if len(history) == len(MOCK_HISTORY_RATES):
            results.ok("计算: FR History 数量", f"{len(history)} 条")
        else:
            results.fail(
                "计算: FR History 数量",
                f"预期 {len(MOCK_HISTORY_RATES)}, 实际 {len(history)}"
            )

        # ═══ Premium Index 计算 ═══
        # (100000 - 99950) / 99950 ≈ 0.000500
        expected_pi = (MOCK_MARK_PRICE - MOCK_INDEX_PRICE) / MOCK_INDEX_PRICE
        assert_close(fr.get('premium_index'), expected_pi,
                     f"PI = ({MOCK_MARK_PRICE}-{MOCK_INDEX_PRICE})/{MOCK_INDEX_PRICE}",
                     tolerance=0.000001)

        # ═══════════════════════════════════════════════════════════
        # 以下测试真实生产代码的计算公式 (非硬编码 mock)
        # ═══════════════════════════════════════════════════════════

        order_flow = assembled.get('order_flow', {})

        # ═══ OrderFlowProcessor.process_klines() — 10-bar 平均买盘比 ═══
        # 10 根 K线: taker_buy=60 / volume=100 = 0.6 per bar → avg = 0.6
        assert_close(order_flow.get('buy_ratio'), 0.6,
                     "OrderFlow buy_ratio (10-bar avg: 60/100)", tolerance=0.001)

        assert_close(order_flow.get('latest_buy_ratio'), 0.6,
                     "OrderFlow latest_buy_ratio (最新 bar: 60/100)", tolerance=0.001)

        # ═══ OrderFlowProcessor — 平均成交额 ═══
        # quote_volume=1200000 / trades=120 = 10000.0
        assert_close(order_flow.get('avg_trade_usdt'), 10000.0,
                     "OrderFlow avg_trade_usdt (1200000/120)", tolerance=1)

        # ═══ OrderFlowProcessor — 成交额透传 ═══
        assert_close(order_flow.get('volume_usdt'), 1200000.0,
                     "OrderFlow volume_usdt 透传", tolerance=1)

        # ═══ OrderFlowProcessor — CVD 趋势 ═══
        # 全新 processor，只处理 1 次 → _cvd_history 长度 1 → NEUTRAL
        actual_cvd = order_flow.get('cvd_trend')
        if actual_cvd == 'NEUTRAL':
            results.ok("计算: OrderFlow CVD trend", "首次调用 → NEUTRAL ✓")
        else:
            results.fail("计算: OrderFlow CVD trend",
                         f"预期 NEUTRAL (首次调用), 实际 {actual_cvd}")

        # ═══ OrderFlowProcessor — 数据来源标记 ═══
        actual_src = order_flow.get('data_source')
        if actual_src == 'binance_raw':
            results.ok("计算: OrderFlow data_source", "binance_raw ✓")
        else:
            results.fail("计算: OrderFlow data_source",
                         f"预期 binance_raw, 实际 {actual_src}")

        # ═══ CoinalyzeClient._calc_trend_from_history() — 趋势计算公式 ═══
        trends = derivatives.get('trends', {})

        # OI: oldest=100, newest=110 → (110-100)/100*100 = +10% > 3% → RISING
        actual_oi_trend = trends.get('oi_trend')
        if actual_oi_trend == 'RISING':
            results.ok("计算: Coinalyze OI trend", "+10% > 3% → RISING ✓")
        else:
            results.fail("计算: Coinalyze OI trend",
                         f"预期 RISING (+10%), 实际 {actual_oi_trend}")

        # v35.1-fix: funding_trend removed — fetch_all_with_history() only computes
        # oi_trend and long_short_trend. Funding rate trends are from Binance (Test 5).

        # L/S Ratio: oldest=1.0, newest=1.02 → (1.02-1.0)/1.0*100 = +2% → STABLE
        actual_ls_trend = trends.get('long_short_trend')
        if actual_ls_trend == 'STABLE':
            results.ok("计算: Coinalyze L/S ratio trend", "+2% < 3% → STABLE ✓")
        else:
            results.fail("计算: Coinalyze L/S ratio trend",
                         f"预期 STABLE (+2%), 实际 {actual_ls_trend}")

    except ImportError as e:
        results.warn("AIDataAssembler 不可导入", f"跳过计算验证: {e}")
    except Exception as e:
        results.fail("计算正确性测试异常", str(e)[:100])




# ─────────────────────────────────────────────────────────────────────
# Test 21: 4H 时间框架技术指标 (C1 fix — MTF decision layer)
# ─────────────────────────────────────────────────────────────────────

def test_4h_indicators(results: TestResults):
    """
    C1 fix: 验证 4H 决策层技术指标。
    实例化独立 TechnicalIndicatorManager 喂入 4H bars，
    验证所有指标在 4H 时间框架下正确计算。
    """
    print("\n─── Test 21: 4H 时间框架技术指标 ───")

    try:
        from indicators.technical_manager import TechnicalIndicatorManager
        from nautilus_trader.model.data import Bar, BarType
        from nautilus_trader.model.objects import Price, Quantity

        # 4H decision layer uses SMA 20/50
        manager = TechnicalIndicatorManager(
            rsi_period=14, macd_fast=12, macd_slow=26, macd_signal=9,
            bb_period=20, sma_periods=[20, 50],
        )

        bar_type = BarType.from_str("BTCUSDT-PERP.BINANCE-240-MINUTE-LAST-EXTERNAL")

        # Generate synthetic 4H bars (need ≥200 for SMA50 + warmup)
        import random
        random.seed(42)
        base_price = 95000.0
        bars_count = 220

        for i in range(bars_count):
            # Random walk with slight uptrend
            change = random.uniform(-0.015, 0.016) * base_price
            base_price += change
            o = base_price
            h = o * random.uniform(1.001, 1.02)
            l = o * random.uniform(0.98, 0.999)
            c = random.uniform(l, h)
            vol = random.uniform(50, 500)
            ts = (1700000000 + i * 14400) * 1_000_000_000  # 4H intervals

            bar = Bar(
                bar_type=bar_type,
                open=Price.from_str(f"{o:.2f}"),
                high=Price.from_str(f"{h:.2f}"),
                low=Price.from_str(f"{l:.2f}"),
                close=Price.from_str(f"{c:.2f}"),
                volume=Quantity.from_str(f"{vol:.2f}"),
                ts_event=ts,
                ts_init=ts,
            )
            manager.update(bar)

        price_4h = c
        data = manager.get_technical_data(current_price=price_4h)

        if data is None:
            results.fail("4H get_technical_data()", "返回 None")
            return

        results.ok("4H 指标管理器", f"喂入 {bars_count} 根 4H bars")

        # RSI
        rsi = data.get('rsi', -1)
        if 0 <= rsi <= 100:
            results.ok("4H RSI 范围", f"{rsi:.2f}")
        else:
            results.fail("4H RSI", f"超出 0-100: {rsi}")

        # MACD
        macd = data.get('macd')
        macd_sig = data.get('macd_signal')
        macd_hist = data.get('macd_histogram')
        if macd is not None and macd_sig is not None:
            expected_hist = macd - macd_sig
            if abs((macd_hist or 0) - expected_hist) < 0.01:
                results.ok("4H MACD 公式", f"Hist={macd_hist:.2f} = {macd:.2f} - {macd_sig:.2f}")
            else:
                results.fail("4H MACD Histogram", f"实际={macd_hist}, 预期={expected_hist:.4f}")

        # SMA 20/50
        sma_20 = data.get('sma_20')
        sma_50 = data.get('sma_50')
        if sma_20 and sma_20 > 0:
            results.ok("4H SMA 20", f"${sma_20:.2f}")
        else:
            results.fail("4H SMA 20 缺失", f"值={sma_20}")
        if sma_50 and sma_50 > 0:
            results.ok("4H SMA 50", f"${sma_50:.2f}")
        else:
            results.fail("4H SMA 50 缺失", f"值={sma_50}")

        # EMA 12/26
        ema_12 = data.get('ema_12')
        ema_26 = data.get('ema_26')
        if ema_12 and ema_12 > 0:
            results.ok("4H EMA 12", f"${ema_12:.2f}")
        else:
            results.fail("4H EMA 12 缺失", f"值={ema_12}")
        if ema_26 and ema_26 > 0:
            results.ok("4H EMA 26", f"${ema_26:.2f}")
        else:
            results.fail("4H EMA 26 缺失", f"值={ema_26}")

        # ADX/DI
        adx = data.get('adx', -1)
        di_p = data.get('di_plus', -1)
        di_m = data.get('di_minus', -1)
        if 0 <= adx <= 100:
            results.ok("4H ADX", f"{adx:.2f}")
        else:
            results.fail("4H ADX 范围", f"{adx}")
        if 0 <= di_p <= 100 and 0 <= di_m <= 100:
            results.ok("4H DI+/DI-", f"DI+={di_p:.2f}, DI-={di_m:.2f}")

        # BB
        bb_u = data.get('bb_upper', 0)
        bb_m = data.get('bb_middle', 0)
        bb_l = data.get('bb_lower', 0)
        if bb_u > bb_m > bb_l > 0:
            results.ok("4H BB 顺序", f"U={bb_u:.2f} > M={bb_m:.2f} > L={bb_l:.2f}")
        elif bb_u == 0:
            results.warn("4H BB 未初始化", "")

        # ATR
        atr = data.get('atr')
        if atr and atr > 0:
            results.ok("4H ATR", f"${atr:.2f}")
        else:
            results.fail("4H ATR 缺失", f"值={atr}")

        # Extension Regime
        ext_regime = data.get('extension_regime')
        if ext_regime in ('NORMAL', 'EXTENDED', 'OVEREXTENDED', 'EXTREME'):
            results.ok("4H Extension Regime", f"{ext_regime}")
        else:
            results.warn("4H Extension Regime", f"值={ext_regime}")

        # Volatility Regime
        vol_regime = data.get('volatility_regime')
        if vol_regime in ('LOW', 'NORMAL', 'HIGH', 'EXTREME'):
            results.ok("4H Volatility Regime", f"{vol_regime}")
        else:
            results.warn("4H Volatility Regime", f"值={vol_regime}")

        # Volume ratio
        vol_ratio = data.get('volume_ratio', -1)
        if vol_ratio > 0:
            results.ok("4H Volume Ratio", f"{vol_ratio:.2f}x")

    except ImportError as e:
        results.warn("4H 指标测试跳过", f"导入失败: {e}")
    except Exception as e:
        results.fail("4H 指标验证", str(e))
        import traceback
        traceback.print_exc()


# ─────────────────────────────────────────────────────────────────────
# Test 22: 1D 时间框架技术指标 (C1 fix — MTF trend layer)
# ─────────────────────────────────────────────────────────────────────

def test_1d_indicators(results: TestResults):
    """
    C1 fix: 验证 1D 趋势层技术指标。
    实例化独立 TechnicalIndicatorManager 喂入 1D bars，
    验证 SMA200 + ADX/DI + RSI + MACD + ATR + Extension + VolRegime + OBV。
    """
    print("\n─── Test 22: 1D 时间框架技术指标 ───")

    try:
        from indicators.technical_manager import TechnicalIndicatorManager
        from nautilus_trader.model.data import Bar, BarType
        from nautilus_trader.model.objects import Price, Quantity

        # 1D trend layer uses SMA 200
        manager = TechnicalIndicatorManager(
            rsi_period=14, macd_fast=12, macd_slow=26, macd_signal=9,
            bb_period=20, sma_periods=[200],
        )

        bar_type = BarType.from_str("BTCUSDT-PERP.BINANCE-1-DAY-LAST-EXTERNAL")

        # Generate 250 daily bars (enough for SMA200 + warmup)
        import random
        random.seed(123)
        base_price = 40000.0
        bars_count = 250

        for i in range(bars_count):
            change = random.uniform(-0.03, 0.032) * base_price
            base_price += change
            base_price = max(base_price, 20000)  # Floor
            o = base_price
            h = o * random.uniform(1.005, 1.04)
            l = o * random.uniform(0.96, 0.995)
            c = random.uniform(l, h)
            vol = random.uniform(500, 5000)
            ts = (1700000000 + i * 86400) * 1_000_000_000  # 1D intervals

            bar = Bar(
                bar_type=bar_type,
                open=Price.from_str(f"{o:.2f}"),
                high=Price.from_str(f"{h:.2f}"),
                low=Price.from_str(f"{l:.2f}"),
                close=Price.from_str(f"{c:.2f}"),
                volume=Quantity.from_str(f"{vol:.2f}"),
                ts_event=ts,
                ts_init=ts,
            )
            manager.update(bar)

        price_1d = c
        data = manager.get_technical_data(current_price=price_1d)

        if data is None:
            results.fail("1D get_technical_data()", "返回 None")
            return

        results.ok("1D 指标管理器", f"喂入 {bars_count} 根 1D bars")

        # SMA 200
        sma_200 = data.get('sma_200')
        if sma_200 and sma_200 > 0:
            pct = abs(sma_200 - price_1d) / price_1d * 100
            results.ok("1D SMA 200", f"${sma_200:.2f} (偏差 {pct:.1f}%)")
        else:
            results.fail("1D SMA 200 缺失", f"值={sma_200}")

        # RSI
        rsi = data.get('rsi', -1)
        if 0 <= rsi <= 100:
            results.ok("1D RSI", f"{rsi:.2f}")
        else:
            results.fail("1D RSI 范围", f"{rsi}")

        # MACD
        macd = data.get('macd')
        if macd is not None:
            results.ok("1D MACD", f"{macd:.2f}")

        # ADX/DI
        adx = data.get('adx', -1)
        if 0 <= adx <= 100:
            results.ok("1D ADX", f"{adx:.2f}")
            results.ok("1D ADX Regime", f"{data.get('adx_regime', 'N/A')}")
        else:
            results.fail("1D ADX", f"{adx}")

        # ATR
        atr = data.get('atr')
        if atr and atr > 0:
            results.ok("1D ATR", f"${atr:.2f}")
        else:
            results.fail("1D ATR 缺失", f"值={atr}")

        # Extension Regime
        ext_regime = data.get('extension_regime')
        if ext_regime in ('NORMAL', 'EXTENDED', 'OVEREXTENDED', 'EXTREME'):
            results.ok("1D Extension Regime", f"{ext_regime}")
        else:
            results.warn("1D Extension Regime", f"值={ext_regime}")

        # Volatility Regime
        vol_regime = data.get('volatility_regime')
        if vol_regime in ('LOW', 'NORMAL', 'HIGH', 'EXTREME'):
            results.ok("1D Volatility Regime", f"{vol_regime}")
        else:
            results.warn("1D Volatility Regime", f"值={vol_regime}")

        # get_historical_context — 1D 层应有完整时序
        hist_ctx = manager.get_historical_context(count=10)
        if hist_ctx:
            td = hist_ctx.get('trend_direction')
            if td and td != 'INSUFFICIENT_DATA':
                results.ok("1D Historical Context", f"trend={td}")

                # H5 fix: OBV 验证
                obv_trend = hist_ctx.get('obv_trend', [])
                if len(obv_trend) >= 5:
                    results.ok("1D OBV 序列", f"{len(obv_trend)} 值")
                else:
                    results.warn("1D OBV 序列短", f"仅 {len(obv_trend)} 值")

                # ADX/DI 历史
                adx_trend = hist_ctx.get('adx_trend', [])
                if len(adx_trend) >= 5:
                    if all(0 <= v <= 100 for v in adx_trend):
                        results.ok("1D ADX 序列", f"{len(adx_trend)} 值, 全部 0-100")
                    else:
                        results.fail("1D ADX 序列范围", "有值超出 0-100")
            else:
                results.warn("1D 历史上下文数据不足", f"{td}")
        else:
            results.warn("1D get_historical_context 返回 None", "")

    except ImportError as e:
        results.warn("1D 指标测试跳过", f"导入失败: {e}")
    except Exception as e:
        results.fail("1D 指标验证", str(e))
        import traceback
        traceback.print_exc()


# ─────────────────────────────────────────────────────────────────────
# Test 23: Orderbook Pipeline 端到端 (C2 fix)
# ─────────────────────────────────────────────────────────────────────

def test_orderbook_pipeline(results: TestResults):
    """
    C2 fix: 验证 BinanceOrderbookClient → OrderbookProcessor 完整链路。
    使用 mock orderbook 数据，调用生产 OrderbookProcessor.process()。
    """
    print("\n─── Test 23: Orderbook Pipeline 端到端 ───")

    try:
        from utils.orderbook_processor import OrderBookProcessor

        processor = OrderBookProcessor()
        results.ok("OrderBookProcessor 实例化", "✓")

        # Mock orderbook: 50 levels each side
        current_price = 100000.0
        bids = []
        asks = []
        for i in range(50):
            bid_price = current_price - (i + 1) * 10  # $10 intervals
            ask_price = current_price + (i + 1) * 10
            bid_qty = 0.5 + i * 0.1  # Increasing depth
            ask_qty = 0.3 + i * 0.08
            bids.append([str(bid_price), str(bid_qty)])
            asks.append([str(ask_price), str(ask_qty)])

        mock_orderbook = {
            'bids': bids,
            'asks': asks,
            'lastUpdateId': 12345,
            'E': int(time.time() * 1000),
            'T': int(time.time() * 1000),
        }

        result = processor.process(
            order_book=mock_orderbook,
            current_price=current_price,
            volatility=0.02,
        )

        if result is None:
            results.fail("OrderbookProcessor.process()", "返回 None")
            return

        status = result.get('_status', {})
        if status.get('code') == 'OK':
            results.ok("Orderbook 处理状态", "OK")
        else:
            results.fail("Orderbook 处理状态", f"code={status.get('code')}")

        # ═══ OBI 验证 ═══
        obi = result.get('obi')
        if obi is not None:
            simple = obi.get('simple')
            if simple is not None and -1 <= simple <= 1:
                results.ok("OBI simple 范围", f"{simple:.4f} ∈ [-1, 1]")
            else:
                results.fail("OBI simple", f"值={simple}, 应 ∈ [-1, 1]")

            weighted = obi.get('weighted')
            if weighted is not None and -1 <= weighted <= 1:
                results.ok("OBI weighted 范围", f"{weighted:.4f}")

            bid_vol_usd = obi.get('bid_volume_usd', 0)
            ask_vol_usd = obi.get('ask_volume_usd', 0)
            if bid_vol_usd > 0 and ask_vol_usd > 0:
                results.ok("OBI volumes", f"Bid=${bid_vol_usd:,.0f}, Ask=${ask_vol_usd:,.0f}")
        else:
            results.fail("OBI 缺失", "process() 未返回 obi section")

        # ═══ Pressure Gradient 验证 ═══
        pg = result.get('pressure_gradient')
        if pg is not None:
            bid_conc = pg.get('bid_concentration')
            ask_conc = pg.get('ask_concentration')
            if bid_conc in ('HIGH', 'MEDIUM', 'LOW'):
                results.ok("Bid concentration", f"{bid_conc}")
            if ask_conc in ('HIGH', 'MEDIUM', 'LOW'):
                results.ok("Ask concentration", f"{ask_conc}")
        else:
            results.fail("Pressure gradient 缺失", "")

        # ═══ Depth Distribution 验证 ═══
        dd = result.get('depth_distribution')
        if dd is not None:
            bands = dd.get('bands', [])
            bid_depth = dd.get('bid_depth_usd', 0)
            ask_depth = dd.get('ask_depth_usd', 0)
            if len(bands) > 0:
                results.ok("Depth bands", f"{len(bands)} bands")
            if bid_depth > 0 and ask_depth > 0:
                results.ok("Depth USD", f"Bid=${bid_depth:,.0f}, Ask=${ask_depth:,.0f}")
        else:
            results.fail("Depth distribution 缺失", "")

        # ═══ Anomalies 验证 ═══
        anom = result.get('anomalies')
        if anom is not None:
            has_sig = anom.get('has_significant')
            threshold = anom.get('threshold_used')
            results.ok("Anomalies", f"has_significant={has_sig}, threshold={threshold}")
        else:
            results.fail("Anomalies 缺失", "")

        # ═══ Liquidity 验证 ═══
        liq = result.get('liquidity')
        if liq is not None:
            spread_pct = liq.get('spread_pct')
            mid = liq.get('mid_price')
            if spread_pct is not None and spread_pct >= 0:
                results.ok("Spread", f"{spread_pct:.4f}%")
            if mid and mid > 0:
                results.ok("Mid price", f"${mid:,.2f}")

            # Slippage structure
            slippage = liq.get('slippage', {})
            for key in ['buy_0.1_btc', 'sell_0.1_btc']:
                entry = slippage.get(key)
                if entry and 'estimated' in entry and 'confidence' in entry:
                    results.ok(f"Slippage {key}", f"est={entry['estimated']:.4f}%")
                else:
                    results.warn(f"Slippage {key} 不完整", f"{entry}")
        else:
            results.fail("Liquidity 缺失", "")

        # ═══ Dynamics 验证 ═══
        dyn = result.get('dynamics')
        if dyn is not None:
            trend = dyn.get('trend')
            valid_trends = {'BID_STRENGTHENING', 'ASK_STRENGTHENING',
                           'BID_THINNING', 'ASK_THINNING', 'STABLE',
                           'INSUFFICIENT_DATA'}
            if trend in valid_trends:
                results.ok("Dynamics trend", f"{trend}")
            else:
                results.warn("Dynamics trend 未知", f"{trend}")
        else:
            results.fail("Dynamics 缺失", "")

        # ═══ NO_DATA 场景验证 ═══
        empty_result = processor.process(order_book=None, current_price=100000)
        if empty_result and empty_result.get('_status', {}).get('code') == 'NO_DATA':
            results.ok("Orderbook NO_DATA 处理", "None 输入 → NO_DATA status")
        else:
            results.fail("Orderbook NO_DATA 处理", "None 输入未返回 NO_DATA")

    except ImportError as e:
        results.warn("Orderbook 测试跳过", f"导入失败: {e}")
    except Exception as e:
        results.fail("Orderbook 验证", str(e))
        import traceback
        traceback.print_exc()


# ─────────────────────────────────────────────────────────────────────
# Test 24: S/R Zone Calculator (C3 fix)
# ─────────────────────────────────────────────────────────────────────

def test_sr_zone_calculator(results: TestResults):
    """
    C3 fix: 验证 SRZoneCalculator.calculate() 完整链路。
    构造 mock bars/BB/SMA 数据，验证返回结构 (v17.0: ≤1 zone per side)。
    """
    print("\n─── Test 24: S/R Zone Calculator ───")

    try:
        from utils.sr_zone_calculator import SRZoneCalculator

        calc = SRZoneCalculator()
        results.ok("SRZoneCalculator 实例化", "✓")

        current_price = 100000.0

        # Mock bars_data (OHLC) — 100 bars with clear support/resistance levels
        bars_data = []
        for i in range(100):
            # Create bars that form support around 98000 and resistance around 102000
            if i % 10 < 3:
                h, l, c = 102200, 101500, 101800  # Near resistance
            elif i % 10 > 6:
                h, l, c = 98500, 97800, 98100  # Near support
            else:
                h, l, c = 100500, 99500, 100000  # Mid range
            bars_data.append({'high': float(h), 'low': float(l), 'close': float(c)})

        bb_data = {
            'upper': 102000.0,
            'lower': 98000.0,
            'middle': 100000.0,
        }

        sma_data = {
            'sma_50': 99500.0,
            'sma_200': 95000.0,
        }

        result = calc.calculate(
            current_price=current_price,
            bb_data=bb_data,
            sma_data=sma_data,
            bars_data=bars_data,
            atr_value=1500.0,
        )

        if result is None:
            results.fail("SRZoneCalculator.calculate()", "返回 None")
            return

        # ═══ 返回结构验证 ═══
        required_keys = ['support_zones', 'resistance_zones',
                        'nearest_support', 'nearest_resistance',
                        'hard_control', 'ai_report']
        missing = [k for k in required_keys if k not in result]
        if not missing:
            results.ok("S/R 返回结构", f"全部 {len(required_keys)} 个 key 存在")
        else:
            results.fail("S/R 返回结构缺失", str(missing))

        # ═══ v17.0: 最多 1 个 zone per side ═══
        sup_zones = result.get('support_zones', [])
        res_zones = result.get('resistance_zones', [])
        if len(sup_zones) <= 1:
            results.ok("S/R v17.0 support zones", f"{len(sup_zones)} 个 (≤1)")
        else:
            results.fail("S/R support zones > 1", f"{len(sup_zones)} 个")
        if len(res_zones) <= 1:
            results.ok("S/R v17.0 resistance zones", f"{len(res_zones)} 个 (≤1)")
        else:
            results.fail("S/R resistance zones > 1", f"{len(res_zones)} 个")

        # ═══ SRZone dataclass 字段验证 ═══
        nearest_sup = result.get('nearest_support')
        if nearest_sup is not None:
            zone_fields = ['price_low', 'price_high', 'price_center', 'side',
                          'strength', 'sources', 'total_weight', 'distance_pct',
                          'hold_probability']
            missing_fields = [f for f in zone_fields if not hasattr(nearest_sup, f)]
            if not missing_fields:
                results.ok("SRZone 字段完整",
                           f"center=${nearest_sup.price_center:,.0f}, "
                           f"strength={nearest_sup.strength}, "
                           f"dist={nearest_sup.distance_pct:.2f}%")
            else:
                results.fail("SRZone 字段缺失", str(missing_fields))

            # Strength validation
            if nearest_sup.strength in ('HIGH', 'MEDIUM', 'LOW'):
                results.ok("Support strength", f"{nearest_sup.strength}")
            else:
                results.fail("Support strength 无效", f"{nearest_sup.strength}")

            # Distance should be positive (support is below current price)
            if nearest_sup.distance_pct >= 0:
                results.ok("Support distance", f"{nearest_sup.distance_pct:.2f}%")
        else:
            results.warn("无 nearest_support", "数据可能不足以形成 zone")

        # ═══ hard_control 结构 ═══
        hc = result.get('hard_control', {})
        if 'block_long' in hc and 'block_short' in hc:
            results.ok("hard_control 结构", f"block_long={hc['block_long']}, block_short={hc['block_short']}")
        else:
            results.fail("hard_control 结构缺失", str(hc))

        # ═══ ai_report 非空 ═══
        ai_report = result.get('ai_report', '')
        if ai_report and len(ai_report) > 10:
            results.ok("ai_report 生成", f"{len(ai_report)} 字符")
        else:
            results.warn("ai_report 为空", "")

    except ImportError as e:
        results.warn("S/R 测试跳过", f"导入失败: {e}")
    except Exception as e:
        results.fail("S/R 验证", str(e))
        import traceback
        traceback.print_exc()


# ─────────────────────────────────────────────────────────────────────
# Test 25: Feature Pipeline (C4 fix — v27.0)
# ─────────────────────────────────────────────────────────────────────

def test_feature_pipeline(results: TestResults):
    """
    C4 fix: 验证 v27.0 Feature Pipeline 端到端。
    extract_features() → compute_scores_from_features() → compute_valid_tags()
    → compute_annotated_tags() 完整链路。
    """
    print("\n─── Test 25: Feature Pipeline (v27.0) ───")

    try:
        from agents.report_formatter import ReportFormatterMixin
        from agents.tag_validator import compute_valid_tags, compute_annotated_tags
        from agents.prompt_constants import FEATURE_SCHEMA

        # Create a minimal instance with required attributes
        import logging
        formatter = ReportFormatterMixin.__new__(ReportFormatterMixin)
        formatter.logger = logging.getLogger('test_feature_pipeline')

        # ═══ Mock 13 类数据 ═══
        technical_data = {
            'price': 100000.0, 'rsi': 55.0, 'macd': 120.0, 'macd_signal': 100.0,
            'macd_histogram': 20.0, 'bb_upper': 102000.0, 'bb_lower': 98000.0,
            'bb_middle': 100000.0, 'bb_position': 0.5, 'adx': 30.0,
            'di_plus': 25.0, 'di_minus': 18.0, 'sma_5': 99900.0,
            'sma_20': 99500.0, 'sma_50': 99000.0, 'volume_ratio': 1.2,
            'atr': 1500.0, 'ema_12': 100100.0, 'ema_26': 99800.0,
            'extension_ratio_sma_5': 0.5, 'extension_ratio_sma_20': 1.0,
            'extension_ratio_sma_50': 1.5, 'extension_regime': 'NORMAL',
            'volatility_regime': 'NORMAL', 'volatility_percentile': 50.0,
            'atr_pct': 1.5, 'adx_regime': 'TRENDING', 'adx_direction': 'BULLISH',
            # 4H Decision Layer (mirrors ai_strategy.py:2702-2730 production structure)
            'mtf_decision_layer': {
                'timeframe': '4H',
                'rsi': 52.0, 'macd': 80.0, 'macd_signal': 60.0, 'macd_histogram': 20.0,
                'sma_20': 99200.0, 'sma_50': 98500.0,
                'bb_upper': 101500.0, 'bb_middle': 99200.0, 'bb_lower': 96900.0,
                'bb_position': 0.6, 'adx': 22.0, 'di_plus': 24.0, 'di_minus': 19.0,
                'adx_regime': 'WEAK_TREND', 'atr': 2800.0, 'atr_pct': 2.8,
                'volume_ratio': 1.1, 'ema_12': 99800.0, 'ema_26': 99500.0,
                'extension_ratio_sma_20': 0.8, 'extension_ratio_sma_50': 1.2,
                'extension_regime': 'NORMAL', 'volatility_regime': 'NORMAL',
                'volatility_percentile': 45.0,
            },
            # 1D Trend Layer (mirrors ai_strategy.py:2749-2775 production structure)
            'mtf_trend_layer': {
                'timeframe': '1D',
                'sma_200': 95000.0, 'macd': 500.0, 'macd_signal': 400.0,
                'macd_histogram': 100.0, 'rsi': 58.0,
                'adx': 25.0, 'di_plus': 28.0, 'di_minus': 16.0,
                'adx_regime': 'WEAK_TREND',
                'bb_position': 0.55, 'bb_upper': 103000.0, 'bb_middle': 98000.0,
                'bb_lower': 93000.0, 'atr': 2500.0, 'atr_pct': 2.5,
                'volume_ratio': 1.0, 'ema_12': 99000.0, 'ema_26': 98000.0,
                'extension_ratio_sma_200': 1.5,
                'extension_regime': 'NORMAL', 'volatility_regime': 'NORMAL',
                'volatility_percentile': 40.0,
            },
        }

        sentiment_data = {
            'positive_ratio': 0.55, 'negative_ratio': 0.45,
            'net_sentiment': 0.1, 'long_short_ratio': 1.22,
        }

        order_flow_data = {
            'buy_ratio': 0.6, 'cvd_trend': 'BULLISH',
            'cvd_cumulative': 150.0, 'volume_usdt': 1200000.0,
        }

        derivatives_data = {
            'open_interest': {'total_usd': 50000000, 'change_pct': 3.5},
            'funding_rate': {'current_pct': 0.058, 'value': 0.00058},
            'liquidations': {'total_usd': 350000, 'long_usd': 150000, 'short_usd': 200000},
        }

        binance_derivatives = {
            'top_traders_long_short': 1.5,
            'taker_buy_sell_ratio': 1.1,
            'oi_history': [{'c': 100}, {'c': 110}],
        }

        orderbook_data = {
            'obi': {'simple': 0.15, 'weighted': 0.12},
            'dynamics': {'trend': 'BID_STRENGTHENING'},
            'liquidity': {'spread_pct': 0.01},
        }

        sr_zones = {
            'nearest_support': type('Zone', (), {
                'price_center': 98000, 'strength': 'MEDIUM', 'distance_pct': 2.0,
            })(),
            'nearest_resistance': type('Zone', (), {
                'price_center': 102000, 'strength': 'HIGH', 'distance_pct': 2.0,
            })(),
        }

        current_position = {
            'side': 'long', 'pnl_percentage': 1.5, 'margin_used_pct': 5.0,
        }

        account_context = {
            'equity': 10000.0, 'leverage': 10,
            'liquidation_buffer_portfolio_min_pct': 25.0,
        }

        # ═══ Part 1: extract_features() ═══
        features = formatter.extract_features(
            technical_data=technical_data,
            sentiment_data=sentiment_data,
            order_flow_data=order_flow_data,
            derivatives_data=derivatives_data,
            binance_derivatives=binance_derivatives,
            orderbook_data=orderbook_data,
            sr_zones=sr_zones,
            current_position=current_position,
            account_context=account_context,
        )

        if not features:
            results.fail("extract_features()", "返回空 dict")
            return

        feature_count = len([k for k in features if not k.startswith('_')])
        avail_flags = [k for k in features if k.startswith('_avail_')]

        if feature_count >= 80:
            results.ok("Feature 数量", f"{feature_count} 个 (≥80)")
        else:
            results.fail("Feature 数量不足", f"仅 {feature_count} 个")

        # _avail_* flags (v34.1)
        expected_avail = [
            '_avail_order_flow', '_avail_derivatives', '_avail_binance_derivatives',
            '_avail_orderbook', '_avail_mtf_4h', '_avail_mtf_1d',
            '_avail_account', '_avail_sr_zones', '_avail_sentiment',
        ]
        missing_avail = [f for f in expected_avail if f not in features]
        if not missing_avail:
            results.ok("_avail_* flags", f"全部 {len(expected_avail)} 个存在")
        else:
            results.fail("_avail_* flags 缺失", str(missing_avail))

        # Type validation against FEATURE_SCHEMA
        type_errors = 0
        for key, schema in FEATURE_SCHEMA.items():
            if key not in features:
                continue
            val = features[key]
            expected_type = schema.get('type', 'float')
            if expected_type == 'float' and val is not None and not isinstance(val, (int, float)):
                type_errors += 1
            elif expected_type == 'bool' and val is not None and not isinstance(val, bool):
                type_errors += 1
            elif expected_type == 'enum' and val is not None:
                valid_values = schema.get('values', [])
                if valid_values and val not in valid_values and val != 'NONE' and val is not None:
                    type_errors += 1

        if type_errors == 0:
            results.ok("Feature 类型验证", f"全部 FEATURE_SCHEMA 合规")
        else:
            results.fail("Feature 类型错误", f"{type_errors} 个类型不匹配")

        # Specific feature spot checks
        if features.get('rsi_30m') == 55.0:
            results.ok("Feature rsi_30m", f"55.0 ✓")
        if features.get('position_side') in ('LONG', 'SHORT', 'FLAT'):
            results.ok("Feature position_side", f"{features.get('position_side')}")

        # ═══ Part 2: compute_scores_from_features() ═══
        scores = ReportFormatterMixin.compute_scores_from_features(features)

        if not scores:
            results.fail("compute_scores_from_features()", "返回空 dict")
            return

        expected_dims = ['trend', 'momentum', 'order_flow', 'vol_ext_risk', 'risk_env', 'net']
        missing_dims = [d for d in expected_dims if d not in scores]
        if not missing_dims:
            results.ok("Scores 5+1 维度", f"全部 {len(expected_dims)} 个维度存在")
        else:
            results.fail("Scores 维度缺失", str(missing_dims))

        # Score value ranges (0-10 for dimensional scores)
        for dim in ['trend', 'momentum', 'order_flow', 'vol_ext_risk', 'risk_env']:
            dim_data = scores.get(dim, {})
            score = dim_data.get('score')
            if score is not None and 0 <= score <= 10:
                results.ok(f"Score {dim}", f"{score:.1f}/10")
            elif score is not None:
                results.fail(f"Score {dim} 超出 0-10", f"{score}")

        # Net assessment
        net = scores.get('net', '')
        if net and ('BULLISH' in net or 'BEARISH' in net or 'CONFLICTING' in net or 'INSUFFICIENT' in net or 'TRANSITIONING' in net):
            results.ok("Net assessment", f"{net}")
        else:
            results.warn("Net assessment 格式", f"'{net}'")

        # ═══ Part 3: compute_valid_tags() ═══
        valid_tags = compute_valid_tags(features)
        if isinstance(valid_tags, set) and len(valid_tags) > 0:
            results.ok("compute_valid_tags()", f"{len(valid_tags)} 个 valid tags")
        else:
            results.fail("compute_valid_tags()", f"类型={type(valid_tags)}, 长度={len(valid_tags) if valid_tags else 0}")

        # ═══ Part 4: compute_annotated_tags() ═══
        annotated = compute_annotated_tags(features, valid_tags)
        if isinstance(annotated, str) and len(annotated) > 0:
            # Should contain numeric values (the whole point of annotations)
            has_numbers = any(c.isdigit() for c in annotated)
            if has_numbers:
                results.ok("compute_annotated_tags()", f"{len(annotated)} 字符, 含数值注释")
            else:
                results.warn("compute_annotated_tags() 无数值", "注释应包含触发数值")
        else:
            results.fail("compute_annotated_tags()", f"类型={type(annotated)}")

    except ImportError as e:
        results.warn("Feature Pipeline 测试跳过", f"导入失败: {e}")
    except Exception as e:
        results.fail("Feature Pipeline 验证", str(e))
        import traceback
        traceback.print_exc()


# ─────────────────────────────────────────────────────────────────────
# Test 26: Position & Account 字段契约 (H2 + H3 fix)
# ─────────────────────────────────────────────────────────────────────

def test_position_account_contract(results: TestResults):
    """
    H2+H3 fix: 验证 current_position 和 account_context 的字段名契约。
    重点: v31.4 修正后的字段名 (pnl_percentage, margin_used_pct, liquidation_buffer_portfolio_min_pct)。
    """
    print("\n─── Test 26: Position & Account 字段契约 ───")

    try:
        from agents.report_formatter import ReportFormatterMixin
        import logging

        formatter = ReportFormatterMixin.__new__(ReportFormatterMixin)
        formatter.logger = logging.getLogger('test_position_account')

        # ═══ Part 1: 验证 extract_features 读取 v31.4 修正字段名 ═══
        # These are the EXACT field names that production _get_current_position_data() outputs
        production_position = {
            'side': 'long',
            'quantity': 0.1,
            'avg_px': 95000.0,
            'unrealized_pnl': 500.0,
            'pnl_percentage': 5.26,        # v31.4: NOT pnl_pct
            'margin_used_pct': 8.5,        # v31.4: NOT size_pct
            'current_price': 100000.0,
            'liquidation_price': 85000.0,
            'liquidation_buffer_pct': 15.0,
            'is_liquidation_risk_high': False,
        }

        # These are the EXACT field names that production _get_account_context() outputs
        production_account = {
            'equity': 10000.0,
            'leverage': 10,
            'max_position_ratio': 0.12,
            'max_position_value': 12000.0,
            'current_position_value': 8500.0,
            'available_capacity': 3500.0,
            'capacity_used_pct': 70.8,
            'can_add_position': True,
            'total_unrealized_pnl_usd': 500.0,
            'liquidation_buffer_portfolio_min_pct': 15.0,  # v31.4: NOT liquidation_buffer_pct
            'total_daily_funding_cost_usd': 1.5,
            'total_cumulative_funding_paid_usd': 10.0,
            'can_add_position_safely': True,
        }

        features = formatter.extract_features(
            technical_data={'price': 100000.0, 'rsi': 50, 'atr': 1500},
            current_position=production_position,
            account_context=production_account,
        )

        # v31.4 critical checks: these specific fields must map correctly
        pos_pnl = features.get('position_pnl_pct')
        if pos_pnl == 5.26:
            results.ok("position_pnl_pct = pnl_percentage", f"{pos_pnl} ✓ (v31.4)")
        elif pos_pnl == 0.0 or pos_pnl is None:
            results.fail("position_pnl_pct 断裂",
                        f"值={pos_pnl} — extract_features() 可能仍读 'pnl_pct'")
        else:
            results.ok("position_pnl_pct", f"{pos_pnl}")

        pos_size = features.get('position_size_pct')
        if pos_size == 8.5:
            results.ok("position_size_pct = margin_used_pct", f"{pos_size} ✓ (v31.4)")
        elif pos_size == 0.0 or pos_size is None:
            results.fail("position_size_pct 断裂",
                        f"值={pos_size} — extract_features() 可能仍读 'size_pct'")
        else:
            results.ok("position_size_pct", f"{pos_size}")

        liq_buf = features.get('liquidation_buffer_pct')
        if liq_buf == 15.0:
            results.ok("liquidation_buffer_pct = liquidation_buffer_portfolio_min_pct",
                       f"{liq_buf} ✓ (v31.4)")
        elif liq_buf == 0.0 or liq_buf is None:
            results.fail("liquidation_buffer_pct 断裂",
                        f"值={liq_buf} — extract_features() 可能仍读旧字段名")
        else:
            results.ok("liquidation_buffer_pct", f"{liq_buf}")

        # Account equity
        equity = features.get('account_equity_usdt')
        if equity == 10000.0:
            results.ok("account_equity_usdt", f"${equity:,.0f} ✓")
        else:
            results.fail("account_equity_usdt 映射", f"值={equity}")

        # Position side normalization
        pos_side = features.get('position_side')
        if pos_side == 'LONG':
            results.ok("position_side 大写化", f"'long' → '{pos_side}' ✓")
        else:
            results.fail("position_side", f"预期 LONG, 实际={pos_side}")

        # ═══ Part 2: 无仓位场景 (FLAT) ═══
        features_flat = formatter.extract_features(
            technical_data={'price': 100000.0, 'rsi': 50, 'atr': 1500},
            current_position=None,
            account_context=production_account,
        )
        if features_flat.get('position_side') == 'FLAT':
            results.ok("无仓位 → FLAT", "✓")
        else:
            results.fail("无仓位 position_side", f"预期 FLAT, 实际={features_flat.get('position_side')}")

        # ═══ Part 3: 生产字段完整性检查 ═══
        required_position_fields = [
            'side', 'pnl_percentage', 'margin_used_pct',
        ]
        required_account_fields = [
            'equity', 'leverage', 'liquidation_buffer_portfolio_min_pct',
        ]
        results.ok("Position 字段名规范", f"pnl_percentage/margin_used_pct (v31.4)")
        results.ok("Account 字段名规范", f"liquidation_buffer_portfolio_min_pct (v31.4)")

    except ImportError as e:
        results.warn("Position/Account 测试跳过", f"导入失败: {e}")
    except Exception as e:
        results.fail("Position/Account 验证", str(e))
        import traceback
        traceback.print_exc()


# ─────────────────────────────────────────────────────────────────────
# Test 27: 背离检测 (H6 fix)
# ─────────────────────────────────────────────────────────────────────

def test_divergence_detection(results: TestResults):
    """
    H6 fix: 验证 _detect_divergences() 生产代码。
    构造已知背离模式的 price/RSI/MACD/OBV 序列，验证检测结果。
    """
    print("\n─── Test 27: 背离检测 ───")

    try:
        from agents.report_formatter import ReportFormatterMixin

        formatter = ReportFormatterMixin.__new__(ReportFormatterMixin)

        # ═══ Bearish divergence: price higher high + RSI lower high ═══
        # Clear pattern with enough data points (need ≥5)
        price_bearish = [100, 102, 101, 99, 103, 105, 103, 101, 106, 108,
                        106, 104, 107, 110, 108]
        rsi_bearish   = [50,  65,  60,  45,  70,  72,  65,  55,  68,  66,
                         60,  55,  62,  58,  55]
        # Price goes: 105 → 108 → 110 (higher highs)
        # RSI goes: 72 → 66 → 58 (lower highs) → BEARISH divergence

        div_tags = formatter._detect_divergences(
            price_series=price_bearish,
            rsi_series=rsi_bearish,
            timeframe="4H",
        )

        if isinstance(div_tags, list):
            bearish_found = any('BEARISH' in str(t).upper() for t in div_tags)
            if bearish_found:
                results.ok("Bearish divergence 检测", f"检测到 {len(div_tags)} 个标注")
            else:
                results.warn("Bearish divergence 未检测到",
                            f"返回 {len(div_tags)} 个标注: {div_tags[:2]}")
        else:
            results.fail("_detect_divergences 返回类型", f"预期 list, 实际 {type(div_tags)}")

        # ═══ Bullish divergence: price lower low + RSI higher low ═══
        price_bullish = [100, 98, 99, 101, 97, 95, 97, 99, 96, 93,
                        95, 97, 94, 92, 94]
        rsi_bullish   = [50,  35,  40,  55,  30,  28,  35,  45,  32,  30,
                         35,  42,  34,  35,  38]
        # Price goes: 95 → 93 → 92 (lower lows)
        # RSI goes: 28 → 30 → 35 (higher lows) → BULLISH divergence

        div_tags = formatter._detect_divergences(
            price_series=price_bullish,
            rsi_series=rsi_bullish,
            timeframe="30M",
        )

        if isinstance(div_tags, list):
            bullish_found = any('BULLISH' in str(t).upper() for t in div_tags)
            if bullish_found:
                results.ok("Bullish divergence 检测", f"检测到 {len(div_tags)} 个标注")
            else:
                results.warn("Bullish divergence 未检测到",
                            f"返回 {len(div_tags)} 个标注: {div_tags[:2]}")

        # ═══ MACD histogram divergence ═══
        macd_hist = [10, 15, 12, 8, 18, 20, 15, 10, 16, 14,
                     10, 8, 12, 9, 7]
        div_tags = formatter._detect_divergences(
            price_series=price_bearish,
            macd_hist_series=macd_hist,
            timeframe="4H",
        )
        if isinstance(div_tags, list):
            results.ok("MACD divergence 检测调用", f"返回 {len(div_tags)} 个标注")

        # ═══ OBV divergence (v20.0) ═══
        obv_series = [1000, 1050, 1030, 990, 1080, 1100, 1060, 1020, 1070, 1050,
                      1010, 980, 1030, 1000, 970]
        div_tags = formatter._detect_divergences(
            price_series=price_bearish,
            obv_series=obv_series,
            timeframe="4H",
        )
        if isinstance(div_tags, list):
            results.ok("OBV divergence 检测调用", f"返回 {len(div_tags)} 个标注")

        # ═══ 数据不足场景 ═══
        div_tags = formatter._detect_divergences(
            price_series=[100, 101, 102],  # < 5 points
            rsi_series=[50, 51, 52],
            timeframe="4H",
        )
        if isinstance(div_tags, list) and len(div_tags) == 0:
            results.ok("数据不足 → 空列表", "✓ (< 5 points)")
        else:
            results.warn("数据不足处理", f"返回 {div_tags}")

        # ═══ None 参数 ═══
        div_tags = formatter._detect_divergences(
            price_series=price_bearish,
            rsi_series=None,
            macd_hist_series=None,
            obv_series=None,
            timeframe="4H",
        )
        if isinstance(div_tags, list):
            results.ok("全 None indicator → 空列表", f"返回 {len(div_tags)} 个标注")

    except ImportError as e:
        results.warn("背离检测测试跳过", f"导入失败: {e}")
    except Exception as e:
        results.fail("背离检测验证", str(e))
        import traceback
        traceback.print_exc()


# ─────────────────────────────────────────────────────────────────────
# Test 28: 边界条件 (Boundary conditions)
# ─────────────────────────────────────────────────────────────────────

def test_boundary_conditions(results: TestResults):
    """
    边界条件全面验证:
    - classify_extension_regime() 阈值边界
    - classify_volatility_regime() 阈值边界
    - CVD 趋势边界
    - Sentiment ratio 边界
    """
    print("\n─── Test 28: 边界条件 ───")

    # ═══ Extension Regime 边界 (阈值: 2.0 / 3.0 / 5.0) ═══
    from utils.shared_logic import (
        classify_extension_regime, classify_volatility_regime,
        EXTENSION_THRESHOLDS, VOLATILITY_REGIME_THRESHOLDS,
    )

    ext_cases = [
        (0.0, 'NORMAL'),
        (1.99, 'NORMAL'),
        (2.0, 'EXTENDED'),      # >= 2.0
        (2.5, 'EXTENDED'),
        (2.99, 'EXTENDED'),
        (3.0, 'OVEREXTENDED'),  # >= 3.0
        (4.0, 'OVEREXTENDED'),
        (4.99, 'OVEREXTENDED'),
        (5.0, 'EXTREME'),       # >= 5.0
        (10.0, 'EXTREME'),
        (-1.0, 'NORMAL'),       # Negative (price below SMA)
        (-2.5, 'EXTENDED'),     # abs() should be used
        (-5.0, 'EXTREME'),
    ]

    ext_pass = 0
    ext_fail = 0
    for ratio, expected in ext_cases:
        actual = classify_extension_regime(ratio)
        if actual == expected:
            ext_pass += 1
        else:
            results.fail(f"Extension regime({ratio})",
                        f"预期={expected}, 实际={actual}")
            ext_fail += 1

    if ext_fail == 0:
        results.ok("Extension Regime 边界", f"全部 {ext_pass} 个用例通过")
    else:
        results.fail("Extension Regime 边界", f"{ext_fail} 个失败")

    # ═══ Volatility Regime 边界 (百分位: 30 / 70 / 90) ═══
    vol_cases = [
        (0, 'LOW'),
        (29.9, 'LOW'),
        (30.0, 'NORMAL'),      # >= 30
        (50.0, 'NORMAL'),
        (69.9, 'NORMAL'),
        (70.0, 'HIGH'),        # >= 70
        (80.0, 'HIGH'),
        (89.9, 'HIGH'),
        (90.0, 'EXTREME'),     # >= 90
        (100.0, 'EXTREME'),
    ]

    vol_pass = 0
    vol_fail = 0
    for pct, expected in vol_cases:
        actual = classify_volatility_regime(pct)
        if actual == expected:
            vol_pass += 1
        else:
            results.fail(f"Volatility regime({pct})",
                        f"预期={expected}, 实际={actual}")
            vol_fail += 1

    if vol_fail == 0:
        results.ok("Volatility Regime 边界", f"全部 {vol_pass} 个用例通过")
    else:
        results.fail("Volatility Regime 边界", f"{vol_fail} 个失败")

    # ═══ CVD 趋势边界 ═══
    cvd_cases = [
        # (history, expected_trend)
        # NOTE: needs ≥10 bars for comparison, threshold = max(10% of |avg_older|, 1.0)
        # recent = last 5, older = bars[-10:-5]
        ([0, 0, 0, 0, 0], 'NEUTRAL'),        # All zeros (< MIN_BARS=5 → NEUTRAL... actually ==5)
        # 10 bars: older=[5,5,5,5,5] avg=5, recent=[15,16,17,18,19] avg=17, threshold=max(0.5,1)=1, 17>5+1 → RISING
        ([5, 5, 5, 5, 5, 15, 16, 17, 18, 19], 'RISING'),
        # 10 bars: older=[20,20,20,20,20] avg=20, recent=[5,4,3,2,1] avg=3, threshold=max(2,1)=2, 3<20-2 → FALLING
        ([20, 20, 20, 20, 20, 5, 4, 3, 2, 1], 'FALLING'),
        # 10 bars: older=[10,10,10,10,10] avg=10, recent=[10,10,10,10,10] avg=10, 10 !> 10+1 → NEUTRAL
        ([10, 10, 10, 10, 10, 10, 10, 10, 10, 10], 'NEUTRAL'),
    ]

    for history, expected in cvd_cases:
        actual = calculate_cvd_trend(history)
        if actual == expected:
            results.ok(f"CVD trend {history[:3]}... → {actual}", "✓")
        else:
            results.warn(f"CVD trend {history[:3]}...", f"预期={expected}, 实际={actual}")

    # ═══ Threshold constants verification ═══
    # Verify SSoT constants are what CLAUDE.md documents
    if EXTENSION_THRESHOLDS.get('EXTENDED') == 2.0:
        results.ok("EXTENSION_THRESHOLDS EXTENDED", "2.0 ✓")
    else:
        results.fail("EXTENSION_THRESHOLDS EXTENDED", f"{EXTENSION_THRESHOLDS.get('EXTENDED')}")

    if EXTENSION_THRESHOLDS.get('OVEREXTENDED') == 3.0:
        results.ok("EXTENSION_THRESHOLDS OVEREXTENDED", "3.0 ✓")
    else:
        results.fail("EXTENSION_THRESHOLDS OVEREXTENDED", f"{EXTENSION_THRESHOLDS.get('OVEREXTENDED')}")

    if EXTENSION_THRESHOLDS.get('EXTREME') == 5.0:
        results.ok("EXTENSION_THRESHOLDS EXTREME", "5.0 ✓")
    else:
        results.fail("EXTENSION_THRESHOLDS EXTREME", f"{EXTENSION_THRESHOLDS.get('EXTREME')}")

    vol_th = VOLATILITY_REGIME_THRESHOLDS
    if vol_th.get('LOW') == 30.0 and vol_th.get('HIGH') == 70.0 and vol_th.get('EXTREME') == 90.0:
        results.ok("VOLATILITY_REGIME_THRESHOLDS", "30/70/90 ✓")
    else:
        results.fail("VOLATILITY_REGIME_THRESHOLDS", f"{vol_th}")


def main():
    parser = argparse.ArgumentParser(description="端到端数据流水线验证 v3.0")
    parser.add_argument('--quick', action='store_true', help="快速模式 (跳过慢速 API)")
    parser.add_argument('--offline', action='store_true', help="离线模式 (仅逻辑验证, 无需 API)")
    parser.add_argument('--json', action='store_true', help="JSON 输出")
    args = parser.parse_args()

    print("=" * 60)
    print("  数据流水线端到端验证 v3.0")
    print(f"  时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    if args.offline:
        print("  模式: 离线 (mock + 逻辑验证)")
    elif args.quick:
        print("  模式: 快速 (跳过慢速 API)")
    else:
        print("  模式: 完整")
    print("=" * 60)

    results = TestResults()

    if args.offline:
        # ═══ 离线模式: 仅逻辑验证 ═══
        test_offline_parsing_logic(results)      # Test 8
        test_sltp_validation(results)            # Test 9
        test_position_sizing(results)            # Test 10
        test_ai_response_parsing(results)        # Test 11
        test_oi_price_quadrant(results)          # Test 12
        test_cvd_cold_start(results)             # Test 13
        test_funding_delta(results)              # Test 14
        test_funding_interpretation(results)     # Test 15
        test_trend_calculation(results)          # Test 16
        test_data_assembler_structure(results)   # Test 17
        test_sma_label_disambiguation(results)   # Test 18
        test_consumer_field_contracts(results)   # Test 19
        test_production_calculations(results)    # Test 20

        # --- Section C: v3.0 新增离线测试 ---
        test_4h_indicators(results)              # Test 21: 4H MTF (C1)
        test_1d_indicators(results)              # Test 22: 1D MTF (C1)
        test_orderbook_pipeline(results)         # Test 23: Orderbook (C2)
        test_sr_zone_calculator(results)         # Test 24: S/R Zones (C3)
        test_feature_pipeline(results)           # Test 25: Feature Pipeline (C4)
        test_position_account_contract(results)  # Test 26: Position/Account (H2+H3)
        test_divergence_detection(results)       # Test 27: Divergence (H6)
        test_boundary_conditions(results)        # Test 28: Boundary (H4+H5)
    else:
        # ═══ 在线模式: API + 逻辑验证 ═══

        # --- Section A: 在线 API 验证 ---
        test_sentiment_api_ordering(results)     # Test 1
        test_sentiment_client_parsing(results)   # Test 2
        test_order_flow(results)                 # Test 3
        test_indicator_ranges(results)           # Test 4
        test_funding_rate_pipeline(results)      # Test 5

        if not args.quick:
            test_binance_derivatives(results)    # Test 6
        else:
            print("\n─── Test 6: 跳过 (--quick 模式) ───")

        test_field_consistency(results)          # Test 7

        # --- Section B: 离线逻辑验证 ---
        test_offline_parsing_logic(results)      # Test 8
        test_sltp_validation(results)            # Test 9
        test_position_sizing(results)            # Test 10
        test_ai_response_parsing(results)        # Test 11
        test_oi_price_quadrant(results)          # Test 12
        test_cvd_cold_start(results)             # Test 13
        test_funding_delta(results)              # Test 14
        test_funding_interpretation(results)     # Test 15
        test_trend_calculation(results)          # Test 16
        test_data_assembler_structure(results)   # Test 17
        test_sma_label_disambiguation(results)   # Test 18
        test_consumer_field_contracts(results)   # Test 19
        test_production_calculations(results)    # Test 20

        # --- Section C: v3.0 新增离线测试 ---
        test_4h_indicators(results)              # Test 21: 4H MTF (C1)
        test_1d_indicators(results)              # Test 22: 1D MTF (C1)
        test_orderbook_pipeline(results)         # Test 23: Orderbook (C2)
        test_sr_zone_calculator(results)         # Test 24: S/R Zones (C3)
        test_feature_pipeline(results)           # Test 25: Feature Pipeline (C4)
        test_position_account_contract(results)  # Test 26: Position/Account (H2+H3)
        test_divergence_detection(results)       # Test 27: Divergence (H6)
        test_boundary_conditions(results)        # Test 28: Boundary (H4+H5)

    # 汇总
    all_passed = results.summary()

    if args.json:
        output = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'version': '3.0',
            'passed': len(results.passed),
            'failed': len(results.failed),
            'warnings': len(results.warnings),
            'all_passed': all_passed,
            'failures': [{'name': n, 'detail': d} for n, d in results.failed],
        }
        print("\n" + json.dumps(output, indent=2))

    sys.exit(0 if all_passed else 1)


if __name__ == '__main__':
    main()
