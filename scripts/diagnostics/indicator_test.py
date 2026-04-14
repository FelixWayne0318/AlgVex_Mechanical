"""
Indicator Test Module

Tests technical indicator initialization and calculation.
"""

from decimal import Decimal
from typing import Dict, Optional

from .base import (
    DiagnosticContext,
    DiagnosticStep,
    MockBar,
    fetch_binance_klines,
    create_bar_from_kline,
)


class IndicatorInitializer(DiagnosticStep):
    """
    Initialize TechnicalIndicatorManager with production config.

    Uses the same initialization as ai_strategy.py __init__.
    """

    name = "初始化 TechnicalIndicatorManager"

    def run(self) -> bool:
        try:
            from indicators.technical_manager import TechnicalIndicatorManager

            cfg = self.ctx.strategy_config

            # Use same parameters as ai_strategy.py (match production L536-545)
            self.ctx.indicator_manager = TechnicalIndicatorManager(
                sma_periods=list(cfg.sma_periods),
                ema_periods=[cfg.macd_fast, cfg.macd_slow],
                rsi_period=cfg.rsi_period,
                macd_fast=cfg.macd_fast,
                macd_slow=cfg.macd_slow,
                macd_signal=getattr(cfg, 'macd_signal', 9),
                bb_period=cfg.bb_period,
                bb_std=cfg.bb_std,
                volume_ma_period=getattr(cfg, 'volume_ma_period', 20),
                support_resistance_lookback=getattr(cfg, 'support_resistance_lookback', 20),
            )

            if not self.ctx.summary_mode:
                print(f"  sma_periods: {list(cfg.sma_periods)}")
                print(f"  ema_periods: [{cfg.macd_fast}, {cfg.macd_slow}]")
                print(f"  rsi_period: {cfg.rsi_period}")
                print(f"  macd: {cfg.macd_fast}/{cfg.macd_slow}")
                print(f"  bb_period: {cfg.bb_period}")
                print("  ✅ TechnicalIndicatorManager 初始化成功")

            # Feed K-line data
            for kline in self.ctx.klines_raw:
                bar = MockBar(
                    float(kline[1]),  # open
                    float(kline[2]),  # high
                    float(kline[3]),  # low
                    float(kline[4]),  # close
                    float(kline[5]),  # volume
                    int(kline[0])     # timestamp
                )
                self.ctx.indicator_manager.update(bar)

            # Check initialization
            if self.ctx.indicator_manager.is_initialized():
                print(f"  ✅ 指标已初始化 ({len(self.ctx.klines_raw)} 根K线)")
            else:
                self.ctx.add_warning("指标未完全初始化，可能数据不足")

            return True

        except Exception as e:
            self.ctx.add_error(f"TechnicalIndicatorManager 失败: {e}")
            import traceback
            traceback.print_exc()
            return False


class TechnicalDataFetcher(DiagnosticStep):
    """
    Fetch technical indicator data.

    Same as on_timer technical data retrieval.
    """

    name = "获取技术数据 (模拟 on_timer 流程)"

    def run(self) -> bool:
        try:
            technical_data = self.ctx.indicator_manager.get_technical_data(
                self.ctx.current_price
            )

            # Add 'price' key (required by multi_agent_analyzer._format_technical_report)
            technical_data['price'] = self.ctx.current_price
            self.ctx.technical_data = technical_data

            # Display key indicators
            sma_keys = [k for k in technical_data.keys() if k.startswith('sma_')]
            for key in sorted(sma_keys):
                print(f"  {key.upper()}: ${technical_data[key]:,.2f}")

            ema_keys = [k for k in technical_data.keys() if k.startswith('ema_')]
            for key in sorted(ema_keys):
                print(f"  {key.upper()}: ${technical_data[key]:,.2f}")

            print(f"  RSI: {technical_data.get('rsi', 0):.2f}")
            print(f"  MACD: {technical_data.get('macd', 0):.4f}")
            print(f"  MACD Signal: {technical_data.get('macd_signal', 0):.4f}")
            print(f"  MACD Histogram: {technical_data.get('macd_histogram', 0):.4f}")
            print(f"  BB Upper: ${technical_data.get('bb_upper', 0):,.2f}")
            print(f"  BB Lower: ${technical_data.get('bb_lower', 0):,.2f}")

            # v19.1: ATR Extension Ratio validation
            ext_regime = technical_data.get('extension_regime', 'N/A')
            ext_sma20 = technical_data.get('extension_ratio_sma_20', None)
            if ext_sma20 is not None:
                print(f"  Extension Ratio SMA20: {ext_sma20:+.2f} ATR ({ext_regime})")
                # Validate regime classification matches threshold rules
                abs_ext = abs(ext_sma20)
                expected_regime = 'NORMAL'
                if abs_ext >= 5.0:
                    expected_regime = 'EXTREME'
                elif abs_ext >= 3.0:
                    expected_regime = 'OVEREXTENDED'
                elif abs_ext >= 2.0:
                    expected_regime = 'EXTENDED'
                if ext_regime == expected_regime:
                    print(f"  ✅ Extension regime classification correct: {ext_regime}")
                elif ext_regime == 'INSUFFICIENT_DATA':
                    print(f"  ⚠️ Extension regime: INSUFFICIENT_DATA (ATR or SMA not initialized)")
                else:
                    print(f"  ⚠️ Extension regime mismatch: got {ext_regime}, expected {expected_regime}")
                    self.ctx.add_warning(f"Extension regime mismatch: {ext_regime} vs {expected_regime}")
            else:
                print(f"  ⚠️ extension_ratio_sma_20 not found in technical_data")
                self.ctx.add_warning("v19.1 extension_ratio_sma_20 missing from technical data")

            # v20.0: ATR Volatility Regime validation
            vol_regime = technical_data.get('volatility_regime', 'N/A')
            vol_pct = technical_data.get('volatility_percentile', None)
            atr_pct = technical_data.get('atr_pct', None)
            if vol_pct is not None and atr_pct is not None:
                print(f"  Volatility Regime: {vol_regime} (ATR%={atr_pct:.4f}%, {vol_pct:.1f}th percentile)")
                # Validate regime classification matches percentile thresholds
                if vol_regime == 'INSUFFICIENT_DATA':
                    print(f"  ⚠️ Volatility regime: INSUFFICIENT_DATA (not enough bars for percentile)")
                else:
                    expected_vol = 'NORMAL'
                    if vol_pct >= 90.0:
                        expected_vol = 'EXTREME'
                    elif vol_pct >= 70.0:
                        expected_vol = 'HIGH'
                    elif vol_pct >= 30.0:
                        expected_vol = 'NORMAL'
                    else:
                        expected_vol = 'LOW'
                    if vol_regime == expected_vol:
                        print(f"  ✅ Volatility regime classification correct: {vol_regime}")
                    else:
                        print(f"  ⚠️ Volatility regime mismatch: got {vol_regime}, expected {expected_vol}")
                        self.ctx.add_warning(f"Volatility regime mismatch: {vol_regime} vs {expected_vol}")
            else:
                print(f"  ⚠️ volatility_percentile/atr_pct not found in technical_data")
                self.ctx.add_warning("v20.0 volatility_percentile/atr_pct missing from technical data")

            # Diagnostic-only data (not sent to AI)
            print(f"  [诊断用] Support: ${technical_data.get('support', 0):,.2f}")
            print(f"  [诊断用] Resistance: ${technical_data.get('resistance', 0):,.2f}")
            print(f"  [诊断用] Overall Trend: {technical_data.get('overall_trend', 'N/A')}")
            print("  ✅ 技术数据获取成功")
            print("  📝 v3.8+: AI 接收原始指标 + S/R Zone v2.0 (动态计算)，不接收预计算的 trend 标签")

            # Load MTF data
            self._load_mtf_data()

            return True

        except Exception as e:
            self.ctx.add_error(f"技术数据获取失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _load_mtf_data(self) -> None:
        """Load multi-timeframe data (4H and 1D)."""
        try:
            from indicators.technical_manager import TechnicalIndicatorManager

            # 4H Decision Layer
            # fetch_binance_klines already strips the last incomplete bar (v6.5)
            klines_4h = fetch_binance_klines(self.ctx.symbol, "4h", 60)
            if klines_4h and len(klines_4h) >= 50:
                # Match production multi_timeframe_manager.py decision_manager init
                cfg = self.ctx.strategy_config
                indicator_manager_4h = TechnicalIndicatorManager(
                    sma_periods=[20, 50],
                    ema_periods=[cfg.macd_fast, cfg.macd_slow],
                    rsi_period=cfg.rsi_period,
                    macd_fast=cfg.macd_fast,
                    macd_slow=cfg.macd_slow,
                    macd_signal=getattr(cfg, 'macd_signal', 9),
                    bb_period=cfg.bb_period,
                    bb_std=cfg.bb_std,
                    volume_ma_period=getattr(cfg, 'volume_ma_period', 20),
                    support_resistance_lookback=getattr(cfg, 'support_resistance_lookback', 20),
                )
                for kline in klines_4h:
                    bar_4h = create_bar_from_kline(kline, "4H")
                    indicator_manager_4h.update(bar_4h)

                decision_layer_data = indicator_manager_4h.get_technical_data(
                    self.ctx.current_price
                )
                self.ctx.technical_data['mtf_decision_layer'] = {
                    'timeframe': '4H',
                    'rsi': decision_layer_data.get('rsi', 50),
                    'macd': decision_layer_data.get('macd', 0),
                    'macd_signal': decision_layer_data.get('macd_signal', 0),
                    'sma_20': decision_layer_data.get('sma_20', 0),
                    'sma_50': decision_layer_data.get('sma_50', 0),
                    'bb_upper': decision_layer_data.get('bb_upper', 0),
                    'bb_middle': decision_layer_data.get('bb_middle', 0),
                    'bb_lower': decision_layer_data.get('bb_lower', 0),
                    'bb_position': decision_layer_data.get('bb_position', 0.5),
                    # v5.6: Add ADX/DI to 4H decision layer (match production ai_strategy.py)
                    'adx': decision_layer_data.get('adx', 0),
                    'di_plus': decision_layer_data.get('di_plus', 0),
                    'di_minus': decision_layer_data.get('di_minus', 0),
                    'adx_regime': decision_layer_data.get('adx_regime', 'UNKNOWN'),
                    # v18 audit: Match production ai_strategy.py A1 pass-through
                    'atr': decision_layer_data.get('atr', 0),
                    'volume_ratio': decision_layer_data.get('volume_ratio', 1.0),
                    'macd_histogram': decision_layer_data.get('macd_histogram', 0),
                    # v29.2: Full pass-through (match production ai_strategy.py)
                    'atr_pct': decision_layer_data.get('atr_pct', 0),
                    'extension_ratio_sma_20': decision_layer_data.get('extension_ratio_sma_20', 0),
                    'extension_ratio_sma_50': decision_layer_data.get('extension_ratio_sma_50', 0),
                    'extension_regime': decision_layer_data.get('extension_regime', 'NORMAL'),
                    'volatility_regime': decision_layer_data.get('volatility_regime', 'NORMAL'),
                    'volatility_percentile': decision_layer_data.get('volatility_percentile', 50),
                    'ema_12': decision_layer_data.get('ema_12', 0),
                    'ema_26': decision_layer_data.get('ema_26', 0),
                }
                # v18 Item 7: Add 4H historical context (16-bar time series)
                # indicator_manager_4h has been fed 60 bars, so history is available
                try:
                    hist_4h = indicator_manager_4h.get_historical_context(count=16)
                    if hist_4h and hist_4h.get('trend_direction') not in ['INSUFFICIENT_DATA', 'ERROR', None]:
                        self.ctx.technical_data['mtf_decision_layer']['historical_context'] = hist_4h
                        n_rsi = len(hist_4h.get('rsi_trend', []))
                        print(f"  ✅ 4H historical context: {n_rsi} bars (RSI/MACD/ADX/DI time series)")
                    else:
                        td_val = hist_4h.get('trend_direction') if hist_4h else 'None'
                        print(f"  ⚠️ 4H historical context: {td_val} (need more bars)")
                except Exception as hc_err:
                    print(f"  ⚠️ 4H historical context 获取失败: {hc_err}")

                mtf_4h = self.ctx.technical_data['mtf_decision_layer']
                rsi_4h = mtf_4h['rsi']
                adx_4h = mtf_4h['adx']
                print(f"  ✅ 4H 决策层数据加载: RSI={rsi_4h:.1f}, ADX={adx_4h:.1f} ({mtf_4h['adx_regime']})")

                # v4.0: Store raw 4H bars for S/R swing detection + volume profile
                # v15.1: No close_time — match production (NautilusTrader bars lack it)
                # v15.3: Slice to [-50:] to match production decision_mgr.recent_bars[-50:]
                all_bars_4h = [
                    {'high': float(k[2]), 'low': float(k[3]),
                     'close': float(k[4]), 'open': float(k[1]),
                     'volume': float(k[5])}
                    for k in klines_4h
                ]
                self.ctx.bars_data_4h = all_bars_4h[-50:]
            else:
                print("  ⚠️ 4H K线数据不足，跳过决策层")

            # 1D Trend Layer
            # fetch_binance_klines already strips the last incomplete bar (v6.5)
            klines_1d = fetch_binance_klines(self.ctx.symbol, "1d", 220)
            if klines_1d and len(klines_1d) >= 200:
                # Match production multi_timeframe_manager.py trend_manager init
                indicator_manager_1d = TechnicalIndicatorManager(
                    sma_periods=[200],
                    ema_periods=[cfg.macd_fast, cfg.macd_slow],
                    rsi_period=cfg.rsi_period,
                    macd_fast=cfg.macd_fast,
                    macd_slow=cfg.macd_slow,
                    macd_signal=getattr(cfg, 'macd_signal', 9),
                    bb_period=cfg.bb_period,
                    bb_std=cfg.bb_std,
                    volume_ma_period=getattr(cfg, 'volume_ma_period', 20),
                    support_resistance_lookback=getattr(cfg, 'support_resistance_lookback', 20),
                )
                for kline in klines_1d:
                    bar_1d = create_bar_from_kline(kline, "1D")
                    indicator_manager_1d.update(bar_1d)

                trend_layer_data = indicator_manager_1d.get_technical_data(
                    self.ctx.current_price
                )
                self.ctx.technical_data['mtf_trend_layer'] = {
                    'timeframe': '1D',
                    'sma_200': trend_layer_data.get('sma_200', 0),
                    'macd': trend_layer_data.get('macd', 0),
                    'macd_signal': trend_layer_data.get('macd_signal', 0),
                    # v3.25: 1D RSI + ADX for macro analysis (match production)
                    'rsi': trend_layer_data.get('rsi', 0),
                    'adx': trend_layer_data.get('adx', 0),
                    'di_plus': trend_layer_data.get('di_plus', 0),
                    'di_minus': trend_layer_data.get('di_minus', 0),
                    'adx_regime': trend_layer_data.get('adx_regime', 'UNKNOWN'),
                    # v18 Item 21: 1D BB/ATR pass-through (match production)
                    'bb_position': trend_layer_data.get('bb_position', 0.5),
                    'atr': trend_layer_data.get('atr', 0),
                    'macd_histogram': trend_layer_data.get('macd_histogram', 0),
                    'volume_ratio': trend_layer_data.get('volume_ratio', 1.0),
                    'bb_upper': trend_layer_data.get('bb_upper', 0),
                    'bb_lower': trend_layer_data.get('bb_lower', 0),
                    'bb_middle': trend_layer_data.get('bb_middle', 0),
                    # v29.2: Full pass-through (match production ai_strategy.py)
                    'atr_pct': trend_layer_data.get('atr_pct', 0),
                    'extension_ratio_sma_200': trend_layer_data.get('extension_ratio_sma_200', 0),
                    'extension_regime': trend_layer_data.get('extension_regime', 'NORMAL'),
                    'volatility_regime': trend_layer_data.get('volatility_regime', 'NORMAL'),
                    'volatility_percentile': trend_layer_data.get('volatility_percentile', 50),
                    'ema_12': trend_layer_data.get('ema_12', 0),
                    'ema_26': trend_layer_data.get('ema_26', 0),
                }
                # v21.0: Add 1D historical context (10-bar time series, matches production)
                try:
                    hist_1d = indicator_manager_1d.get_historical_context(count=10)
                    if hist_1d and hist_1d.get('trend_direction') not in ['INSUFFICIENT_DATA', 'ERROR', None]:
                        self.ctx.technical_data['mtf_trend_layer']['historical_context'] = hist_1d
                except Exception as e:
                    pass  # 1D historical context is best-effort

                mtf_1d = self.ctx.technical_data['mtf_trend_layer']
                sma_200 = mtf_1d['sma_200']
                rsi_1d = mtf_1d['rsi']
                adx_1d = mtf_1d['adx']
                hist_1d_bars = len(mtf_1d.get('historical_context', {}).get('adx_trend', []))
                print(f"  ✅ 1D 趋势层数据加载: SMA_200=${sma_200:,.2f}, RSI={rsi_1d:.1f}, ADX={adx_1d:.1f} ({mtf_1d['adx_regime']}), 历史序列: {hist_1d_bars} bars")

                # v4.0: Store raw 1D bars for S/R swing detection + pivot calculation
                # v15.1: No close_time — match production (NautilusTrader bars lack it)
                # v15.3: Slice to [-120:] to match production trend_mgr.recent_bars[-120:]
                bars_1d_all = [
                    {'high': float(k[2]), 'low': float(k[3]),
                     'close': float(k[4]), 'open': float(k[1]),
                     'volume': float(k[5])}
                    for k in klines_1d
                ]
                bars_1d_dicts = bars_1d_all[-120:]
                self.ctx.bars_data_1d = bars_1d_dicts
                if bars_1d_dicts:
                    self.ctx.daily_bar = bars_1d_dicts[-1]
                    try:
                        from utils.sr_pivot_calculator import aggregate_weekly_bar
                        self.ctx.weekly_bar = aggregate_weekly_bar(bars_1d_dicts)
                    except Exception:
                        pass
            else:
                count = len(klines_1d) if klines_1d else 0
                print(f"  ⚠️ 1D K线数据不足 ({count}/200)，跳过趋势层")

        except Exception as e:
            self.ctx.add_warning(f"MTF 多时间框架数据获取失败: {e}")
